"""API latency + payload budget audit (in-CLI implementation).

The API-shaped analogue of `reliability:cwv`. Core Web Vitals measures
what a browser experiences rendering a page; there is no rendering on a
JSON API, so the equivalent question is narrower and more answerable:
how long does the endpoint take to answer, and how much does it send
back.

Samples each configured path several times and reports the **median**
and the **slowest** sample, plus the response size.

Deliberately not "p95": five samples cannot support a 95th percentile,
and calling `max()` a percentile would dress up one noisy reading as
statistics. The median is what the budget gates on, because a single
slow sample from a shared CI runner moves the maximum and not the
middle. `slowest_ms` exists for anyone who wants an explicit tail
ceiling, opt-in.

Budgets are opt-in throughout: with none set the check reports timings
as notes and exits 0, exactly like `api.health.max_response_ms`. What is
*not* optional is reachability. An endpoint that errors or answers
non-2xx fails whether or not a budget is configured, because timing a
connection error is meaningless.

CLI surface:
  slopstopper run reliability:api-latency -- --url URL [--path /v1/items]
        [--samples 5] [--warmup 1] [--median-ms 400] [--slowest-ms 1500]
        [--max-bytes 200000]

Stdlib-only (urllib + statistics). Writes
.ss/reports/api-latency/api-latency-report.{md,json}.

Configuration (.slopstopper.yml, all optional):

    api:
      base_path: ''          # prefix the API is served under
      latency:
        paths: []            # endpoints to sample; empty → check skips
        samples: 5           # measured requests per path
        warmup: 1            # discarded first requests (connect, cold start)
        median_ms:           # unset → advisory
        slowest_ms:          # unset → advisory
        max_bytes:           # unset → advisory

Env-var equivalents the CLI also honours (precedence: flag > env > config):
  API_LATENCY_TEST_URL   base URL of the API (required)
  API_LATENCY_PATHS      comma-separated paths to sample

See .slopstopper.yml.example for the canonical schema.

Exit codes:
  0: every path reachable and within any configured budget, or no paths
      configured (graceful skip)
  1: a path was unreachable / answered non-2xx, or a configured budget
      was exceeded
  2: the URL is missing, or its scheme is not http/https
"""

from __future__ import annotations

import argparse
import os
import statistics
import time
import urllib.error
from pathlib import Path

from slopstopper import config, output
from slopstopper.checks import _http, _report
from slopstopper.checks._contract import refuse_unsafe_url

REPORT_DIR = Path(".ss/reports/api-latency")
REPORT_MD = REPORT_DIR / "api-latency-report.md"
REPORT_JSON = REPORT_DIR / "api-latency-report.json"
USER_AGENT = "SlopStopper-ApiLatency-Check/1.0"

DEFAULT_SAMPLES = 5
DEFAULT_WARMUP = 1

# Consumed by `slopstopper emit reliability:api-latency --target pr-comment`.
# No issue keys: the exit code fails the workflow; no main-branch issue.
META = {
    "report_path": str(REPORT_MD),
    "comment_discriminator": "⏱ API Latency",
}


# ── safety ───────────────────────────────────────────────────────


_LABEL = "API latency check"


def _fetch(url: str) -> tuple[int, int, float]:
    """GET the URL once. Returns (status, body_bytes, elapsed_ms).

    A 4xx/5xx is unwrapped rather than raised. The status is reported
    against the sample, because a timing taken from an error response is
    not a latency measurement worth keeping.
    """
    started = time.monotonic()
    try:
        with _http.open_url(url, USER_AGENT, label=_LABEL) as resp:
            body = resp.read()
            return resp.status, len(body), (time.monotonic() - started) * 1000
    except urllib.error.HTTPError as e:
        body = e.read() if e.fp else b""
        return e.code, len(body), (time.monotonic() - started) * 1000


# ── sampling ─────────────────────────────────────────────────────


def _join(url: str, base_path: str, path: str) -> str:
    return _http.join_url(url, base_path, path)


def _status_ok(status: int) -> bool:
    return 200 <= status < 400


def _sample_path(target: str, samples: int, warmup: int) -> dict:
    """Time `samples` requests after discarding `warmup` of them.

    The warmup requests are thrown away rather than averaged in: the
    first hit to an endpoint pays for DNS, TLS and whatever cold start
    the platform imposes, none of which is the steady-state latency a
    budget is about.
    """
    for _ in range(max(0, warmup)):
        try:
            _fetch(target)
        except (urllib.error.URLError, TimeoutError):
            # A failing warmup is reported by the measured run below.
            break

    timings: list[float] = []
    sizes: list[int] = []
    statuses: list[int] = []
    for _ in range(max(1, samples)):
        try:
            status, size, elapsed = _fetch(target)
        except (urllib.error.URLError, TimeoutError) as e:
            return {"error": f"{type(e).__name__}: {e}", "timings": timings}
        timings.append(elapsed)
        sizes.append(size)
        statuses.append(status)

    return {
        "error": None,
        "timings": timings,
        "median_ms": round(statistics.median(timings), 1),
        "slowest_ms": round(max(timings), 1),
        "fastest_ms": round(min(timings), 1),
        "bytes": max(sizes),
        "statuses": sorted(set(statuses)),
    }


# ── budgets ──────────────────────────────────────────────────────


def _check_budgets(sample: dict, opts: dict, issues: list[str], notes: list[str], target: str) -> None:
    """Compare a sampled path against whichever budgets are configured."""
    median_budget = opts["median_ms"]
    slowest_budget = opts["slowest_ms"]
    bytes_budget = opts["max_bytes"]

    if median_budget and sample["median_ms"] > median_budget:
        issues.append(
            f"`{target}` median {sample['median_ms']}ms is over the {median_budget}ms budget"
        )
    if slowest_budget and sample["slowest_ms"] > slowest_budget:
        issues.append(
            f"`{target}` slowest sample {sample['slowest_ms']}ms is over the "
            f"{slowest_budget}ms ceiling"
        )
    if bytes_budget and sample["bytes"] > bytes_budget:
        issues.append(
            f"`{target}` returned {sample['bytes']} bytes, over the {bytes_budget}-byte budget"
        )
    if not (median_budget or slowest_budget or bytes_budget):
        notes.append(
            f"`{target}` median {sample['median_ms']}ms, slowest {sample['slowest_ms']}ms, "
            f"{sample['bytes']} bytes (no budget set, so nothing is enforced)"
        )


def _audit_path(url: str, path: str, opts: dict) -> dict:
    target = _join(url, opts["base_path"], path)
    issues: list[str] = []
    notes: list[str] = []

    try:
        sample = _sample_path(target, opts["samples"], opts["warmup"])
    except ValueError as e:
        return {"url": target, "status": "fail", "issues": [str(e)], "notes": [], "sample": {}}

    if sample["error"]:
        return {
            "url": target,
            "status": "fail",
            "issues": [f"`{target}` not reachable: {sample['error']}"],
            "notes": [],
            "sample": {},
        }

    bad = [s for s in sample["statuses"] if not _status_ok(s)]
    if bad:
        # Reachability is the floor: a 500 that answers in 3ms is not fast.
        issues.append(
            f"`{target}` answered HTTP {', '.join(str(s) for s in bad)}. "
            "A timing from an error response isn't a latency measurement"
        )
    else:
        _check_budgets(sample, opts, issues, notes, target)

    return {
        "url": target,
        "status": "fail" if issues else "pass",
        "issues": issues,
        "notes": notes,
        "sample": sample,
    }


def _audit(url: str, opts: dict) -> dict:
    paths = [_audit_path(url, path, opts) for path in opts["paths"]]
    return {
        "url": url,
        "status": "fail" if any(p["status"] == "fail" for p in paths) else "pass",
        "samples": opts["samples"],
        "warmup": opts["warmup"],
        "paths": paths,
    }


# ── markdown report ──────────────────────────────────────────────


def _render_skip() -> list[str]:
    return _report.render_skip(
        'no `api.latency.paths` configured.',
        ['List the endpoints to sample in `.slopstopper.yml`:', '', '```yaml', 'api:', '  latency:', '    paths: [/health, /v1/items]', '```'],
    )


def _timing_table(paths: list[dict]) -> list[str]:
    measured = [p for p in paths if p["sample"]]
    if not measured:
        return []
    lines = ["| Endpoint | Median | Slowest | Fastest | Bytes |", "|---|---|---|---|---|"]
    for page in measured:
        s = page["sample"]
        icon = "✅" if page["status"] == "pass" else "❌"
        lines.append(
            f"| {icon} `{page['url']}` | {s['median_ms']}ms | {s['slowest_ms']}ms | "
            f"{s['fastest_ms']}ms | {s['bytes']} |"
        )
    lines.append("")
    return lines


def _build_markdown_report(result: dict) -> str:
    lines = ["# ⏱ API Latency Report", ""]
    if result["status"] == "skipped":
        lines.extend(_render_skip())
        return "\n".join(lines) + "\n"

    lines.append(f"**Base URL:** {result['url']}")
    lines.append("")
    lines.append(f"**Overall:** {'✅ PASS' if result['status'] == 'pass' else '❌ FAIL'}")
    lines.append("")
    lines.append(
        f"**Sampling:** {result['samples']} measured request(s) per endpoint "
        f"after {result['warmup']} discarded warmup request(s)"
    )
    lines.append("")
    lines.extend(_timing_table(result["paths"]))

    issues = [i for page in result["paths"] for i in page["issues"]]
    notes = [n for page in result["paths"] for n in page["notes"]]
    lines.extend(_report.render_issues(issues))
    lines.extend(_report.render_notes(notes))

    lines.append("---")
    lines.append("")
    lines.append("## How to Fix")
    lines.append("")
    lines.append(
        "- **Not reachable / non-2xx** → this fails with or without a budget. Check the "
        "URL and that the endpoint is deployed; a timing from an error response is not a "
        "latency measurement."
    )
    lines.append(
        "- **Over a budget** → the median is what `api.latency.median_ms` gates on, so a "
        "single slow sample won't trip it. Derive budgets from observed numbers rather "
        "than guessing, or the check becomes noise."
    )
    lines.append(
        "- **Payload too large** → `api.latency.max_bytes` usually catches an endpoint "
        "that stopped paginating or started embedding a relation it used to reference."
    )
    lines.append(
        "- See [docs/reliability/README.md](../../../docs/reliability/README.md) for the "
        "full config and env-var contract."
    )
    lines.append("")
    return "\n".join(lines) + "\n"


def _write_reports(result: dict) -> None:
    _report.write_reports(REPORT_JSON, REPORT_MD, result, _build_markdown_report)


def _print_result(result: dict) -> None:
    for page in result["paths"]:
        icon = "✅" if page["status"] == "pass" else "❌"
        sample = page["sample"]
        summary = (
            f"  median {sample['median_ms']}ms · slowest {sample['slowest_ms']}ms · "
            f"{sample['bytes']} bytes"
            if sample
            else ""
        )
        output._emit(f"  {icon} {page['url']}{summary}")
        for issue in page["issues"]:
            output._emit(f"      - {issue}")
        for note in page["notes"]:
            output._emit(f"      ⚠️  {note}")


# ── CLI entrypoint ───────────────────────────────────────────────


def _parse_args(args: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="slopstopper run reliability:api-latency", add_help=False)
    p.add_argument("url_positional", nargs="?", default=None, help="Base URL of the API")
    p.add_argument("--url", default=None, help="Base URL of the API")
    p.add_argument(
        "--path", action="append", default=[], dest="path", help="Endpoint to sample (repeatable)"
    )
    p.add_argument("--samples", type=int, default=None, help="Measured requests per endpoint")
    p.add_argument("--warmup", type=int, default=None, help="Discarded requests before measuring")
    p.add_argument("--median-ms", type=int, default=None, help="Fail if the median exceeds this")
    p.add_argument(
        "--slowest-ms", type=int, default=None, help="Fail if any sample exceeds this"
    )
    p.add_argument(
        "--max-bytes", type=int, default=None, help="Fail if the response exceeds this size"
    )
    p.add_argument("--help", "-h", action="help")
    return p.parse_args(args or [])


def _env_paths() -> list[str]:
    raw = os.environ.get("API_LATENCY_PATHS", "")
    return [part.strip() for part in raw.split(",") if part.strip()]


def _resolve_options(parsed: argparse.Namespace) -> dict:
    return {
        "base_path": config.get("api.base_path", "") or "",
        "paths": list(parsed.path or _env_paths() or config.get("api.latency.paths", []) or []),
        "samples": parsed.samples or config.get_int("api.latency.samples", DEFAULT_SAMPLES),
        "warmup": parsed.warmup if parsed.warmup is not None else config.get_int("api.latency.warmup", DEFAULT_WARMUP),
        "median_ms": parsed.median_ms or config.get_int("api.latency.median_ms", None),
        "slowest_ms": parsed.slowest_ms or config.get_int("api.latency.slowest_ms", None),
        "max_bytes": parsed.max_bytes or config.get_int("api.latency.max_bytes", None),
    }


def _skip(reason: str) -> int:
    """Skip gracefully, because an unconfigured check is not a failing check."""
    output.info(reason)
    _write_reports({"url": None, "status": "skipped", "paths": [], "samples": 0, "warmup": 0})
    output.success("Nothing to sample, so skipping (exit 0).")
    return 0


def run(args: list[str] | None = None) -> int:
    parsed = _parse_args(args)
    opts = _resolve_options(parsed)

    if not opts["paths"]:
        return _skip(
            "No api.latency.paths configured in .slopstopper.yml, so there is nothing to sample. "
            "List the endpoints whose response time you care about to enable this check."
        )

    url = parsed.url_positional or parsed.url or os.environ.get("API_LATENCY_TEST_URL")
    if not url:
        output.error("API latency target URL is required")
        output._emit("Usage:")
        output._emit("  slopstopper run reliability:api-latency -- --url https://api.example.com")
        output._emit(
            "  API_LATENCY_TEST_URL=https://api.example.com slopstopper run reliability:api-latency"
        )
        return 2

    if (rc := refuse_unsafe_url(url, _LABEL)) is not None:
        return rc

    output.status("⏱", f"API latency audit against: {url}")
    output._emit(
        f"   Paths: {', '.join(opts['paths'])} "
        f"({opts['samples']} sample(s) after {opts['warmup']} warmup)"
    )
    output.separator()

    result = _audit(url, opts)
    _write_reports(result)
    _print_result(result)

    output.separator()
    if result["status"] == "pass":
        output.success("Every endpoint is reachable and within budget.")
        return 0
    output.error(
        "Failures detected. See .ss/reports/api-latency/api-latency-report.md for full details."
    )
    return 1
