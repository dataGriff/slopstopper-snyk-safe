"""Core Web Vitals audit (Lighthouse CI wrapper).

Implements the reliability:cwv flow:

  slopstopper run reliability:cwv -- --url https://your-site.example.com

  which is, under the covers:

  npx lhci autorun --collect.url="$CWV_URL" --config=<resolved lhci config>

Subprocess-invokes `npx lhci`. Lighthouse CI is Apache-2.0, and the
slopstopper-cli wheel ships zero Lighthouse code.

After lhci finishes, cwv.py reads the latest `.lighthouseci/lhr-*.json`,
extracts the four headline metrics (Performance, LCP, TBT, CLS) plus
FCP, and writes `.ss/reports/cwv/cwv-report.md` with the threshold
table. emit.py then builds the PR comment or issue from that report,
the same way it does for every other check.

Configuration: thresholds and the lhci config path live in the
`.ss/lighthouserc.json` override (or the package-data fallback under
`cli/slopstopper/data/lighthouserc.json`). The dev config skips the
`is-crawlable` audit: previews (workers.dev, Vercel, Netlify) answer
`X-Robots-Tag: noindex` by design, which would sink SEO to ~0.6. The
prod config keeps it, because noindex in production is a real bug. cwv.py's threshold-table
limits mirror that file so the rendered table matches what lhci
actually enforced.

  reliability:
    cwv:
      public_report: false   # true uploads the full Lighthouse report
                             # (screenshots, page content) to Google's
                             # temporary public storage and links it

The upload target is set on the lhci command line, so it overrides
whatever an ejected `.ss/lighthouserc.json` says. By default the HTML
reports stay local under `.ss/reports/cwv/lighthouse/`.

Exit codes:
  0: lhci passed all thresholds
  1: lhci audited the page and failed a threshold (report still written)
  2: npx (Node.js) not available, the URL is missing, the Lighthouse
      config does not exist, or lhci didn't run to a verdict: any exit
      other than 0/1, or an exit 1 with no Lighthouse result written this
      run (Chrome failed to launch, collection aborted)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

from slopstopper import config, output, templates
from slopstopper.checks import _tools
from slopstopper.checks._contract import runner_exit

REPORT_DIR = Path(".ss/reports/cwv")
REPORT_MD = REPORT_DIR / "cwv-report.md"
LHCI_DIR = Path(".lighthouseci")
HTML_DIR = REPORT_DIR / "lighthouse"

# The threshold table is kept in lockstep with `.ss/lighthouserc.json`'s
# `assertions` block so the rendered table reflects what lhci actually
# enforced. If you tune the lhci config, mirror the change here.
THRESHOLDS = {
    "performance": ("Performance score", "≥ 70", 70, "min"),
    "lcp":         ("LCP", "≤ 4 s",    4000, "max"),
    "tbt":         ("TBT", "≤ 600 ms",  600, "max"),
    "cls":         ("CLS", "≤ 0.25",  0.25, "max"),
}

# Consumed by `slopstopper emit reliability:cwv --target pr-comment|issue`.
# Discriminator `🚦 Core Web Vitals` matches the pre-flip github-script
# block's PR comment heading so the same bot comment is reused after
# the workflow flip.
META = {
    "report_path": str(REPORT_MD),
    "comment_discriminator": "🚦 Core Web Vitals",
    "issue_title": "❌ Core Web Vitals Below Threshold",
    "issue_labels": ["cwv-failure", "reliability"],
    "issue_followup": "🔔 Core Web Vitals failure recurred in commit",
    "issue_close_comment": "✅ Core Web Vitals are now passing all thresholds. Closing automatically.",
}


def _parse_args(args: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="slopstopper run reliability:cwv", add_help=False)
    p.add_argument(
        "url_positional",
        nargs="?",
        default=None,
        help="Site URL to audit (alternative to --url; e.g. http://localhost:8080)",
    )
    p.add_argument("--url", default=None, help="Site URL to audit (else $CWV_URL)")
    p.add_argument(
        "--prod",
        action="store_true",
        help="Use the stricter prod lighthouserc (default: dev). "
        "Workflows pass this on deployment_status / schedule events.",
    )
    p.add_argument(
        "--config",
        default=None,
        help="Explicit Lighthouse CI config path. Overrides --prod resolution. "
        "Default resolution: .ss/lighthouserc[.prod].json override, else package data.",
    )
    p.add_argument("--help", "-h", action="help")
    return p.parse_args(args or [])


_npx_available = _tools.npx_available


def _resolve_url(parsed_url: str | None) -> str | None:
    return parsed_url or os.environ.get("CWV_URL")


def _build_cmd(url: str, config_path: str, public_report: bool = False) -> list[str]:
    upload = (
        ["--upload.target=temporary-public-storage"]
        if public_report
        else ["--upload.target=filesystem", f"--upload.outputDir={HTML_DIR}"]
    )
    return [
        "npx", "lhci", "autorun",
        f"--collect.url={url}",
        f"--config={config_path}",
        *upload,
    ]


def _run_lhci(cmd: list[str]) -> tuple[int, str]:
    """Run lhci, tee its output to our stdout, and return (exit_code, captured_output)."""
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1
    )
    chunks: list[str] = []
    assert proc.stdout is not None
    for line in proc.stdout:
        sys.stdout.write(line)
        sys.stdout.flush()
        chunks.append(line)
    rc = proc.wait()
    return rc, "".join(chunks)


# ── lhci JSON parsing + report rendering ─────────────────────────


def _latest_lhr_json() -> Path | None:
    if not LHCI_DIR.exists():
        return None
    reports = sorted(LHCI_DIR.glob("lhr-*.json"))
    return reports[-1] if reports else None


def _extract_metrics(lhr: dict) -> dict[str, float | None]:
    """Pull the four headline metrics + FCP from a Lighthouse result JSON."""
    categories = lhr.get("categories") or {}
    audits = lhr.get("audits") or {}
    perf_cat = categories.get("performance") or {}

    def _audit_value(key: str) -> float | None:
        audit = audits.get(key) or {}
        value = audit.get("numericValue")
        return float(value) if isinstance(value, (int, float)) else None

    perf_score = perf_cat.get("score")
    return {
        "performance": round(perf_score * 100) if isinstance(perf_score, (int, float)) else None,
        "lcp": _audit_value("largest-contentful-paint"),
        "tbt": _audit_value("total-blocking-time"),
        "cls": _audit_value("cumulative-layout-shift"),
        "fcp": _audit_value("first-contentful-paint"),
    }


def _format_value(metric: str, value: float | None) -> str:
    if value is None:
        return "N/A"
    if metric == "performance":
        return f"{int(value)}/100"
    if metric == "cls":
        return f"{value:.3f}"
    return f"{int(round(value))} ms"


def _passes(metric: str, value: float | None) -> bool | None:
    if value is None:
        return None
    _, _, threshold, direction = THRESHOLDS[metric]
    return value >= threshold if direction == "min" else value <= threshold


def _icon(passed: bool | None) -> str:
    if passed is None:
        return "⚠️"
    return "✅" if passed else "❌"


def _extract_report_url(output: str) -> str | None:
    """lhci prints the storage URL to stdout. Capture the first one."""
    match = re.search(r"https://storage\.googleapis\.com/\S+", output)
    return match.group(0) if match else None


def _build_report_md(
    url: str,
    metrics: dict[str, float | None],
    report_url: str | None,
    overall_pass: bool,
) -> str:
    status = "✅ PASSED" if overall_pass else "❌ FAILED"
    lines: list[str] = [
        f"## 🚦 Core Web Vitals {status}",
        "",
        f"**URL audited:** {url}",
        "",
        "| Metric | Threshold | Status |",
        "|--------|-----------|--------|",
    ]
    for key, (label, threshold_str, _, _) in THRESHOLDS.items():
        value = metrics.get(key)
        lines.append(
            f"| {label} | {threshold_str} | {_icon(_passes(key, value))} {_format_value(key, value)} |"
        )
    lines.append("")
    if report_url:
        lines.append(f"[📊 Full Lighthouse Report]({report_url})")
    else:
        lines.append(
            f"📊 Full Lighthouse HTML reports: `{HTML_DIR}/` "
            "(in CI, the `lighthouse-reports` workflow artifact)."
        )
    lines.append("")
    lines.append("### How to run locally")
    lines.append("")
    lines.append("```bash")
    lines.append("slopstopper run reliability:cwv -- --url https://your-site.example.com")
    lines.append("```")
    lines.append("")
    return "\n".join(lines)


def _write_report(url: str, output: str, lhci_exit: int) -> None:
    """Parse the latest lhr-*.json + lhci stdout, and write the markdown report."""
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    lhr_path = _latest_lhr_json()
    if lhr_path is None:
        REPORT_MD.write_text(
            "## 🚦 Core Web Vitals ⚠️ NO REPORT\n\n"
            "Lighthouse CI did not produce a report JSON under `.lighthouseci/`. "
            "Check the lhci output above for the cause.\n"
        )
        return
    try:
        lhr = json.loads(lhr_path.read_text())
    except (OSError, json.JSONDecodeError) as e:
        REPORT_MD.write_text(
            f"## 🚦 Core Web Vitals ⚠️ PARSE ERROR\n\nCould not parse {lhr_path}: {e}\n"
        )
        return
    metrics = _extract_metrics(lhr)
    report_url = _extract_report_url(output)
    REPORT_MD.write_text(
        _build_report_md(url, metrics, report_url, overall_pass=lhci_exit == 0)
    )


# ── CLI entrypoint ───────────────────────────────────────────────


def run(args: list[str] | None = None) -> int:
    if not _npx_available():
        output.error("npx is not available. Install Node.js to run Lighthouse CI.")
        return 2

    parsed = _parse_args(args)
    url = _resolve_url(parsed.url_positional or parsed.url)
    if not url:
        output.error("CWV target URL is required")
        output._emit("Usage:")
        output._emit("  slopstopper run reliability:cwv -- --url https://your-site.example.com")
        output._emit("  CWV_URL=https://your-site slopstopper run reliability:cwv")
        return 2

    config_path = (
        Path(parsed.config) if parsed.config else templates.lighthouserc(prod=parsed.prod)
    )
    if not config_path.exists():
        output.error(f"Lighthouse CI config not found at {config_path}")
        return 2

    output.status("🚦", f"Running Core Web Vitals audit against: {url}")
    cmd = _build_cmd(url, str(config_path), config.get_bool("reliability.cwv.public_report"))
    started = time.time()
    rc, captured = _run_lhci(cmd)
    _write_report(url, captured, rc)
    output.footer(REPORT_DIR, [REPORT_MD.name])
    # lhci exits 1 both when an assertion failed (a verdict on the site) and
    # when Chrome never launched or collection aborted. Only the first
    # leaves a Lighthouse result written during this run.
    return runner_exit(rc, ran=_audited_since(started))


def _audited_since(started: float) -> bool:
    """True if lhci wrote a Lighthouse result during this run."""
    lhr = _latest_lhr_json()
    try:
        return lhr is not None and lhr.stat().st_mtime >= started - 1
    except OSError:
        return False
