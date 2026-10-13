"""llms.txt AI-discoverability audit (in-CLI implementation).

Fetches `/llms.txt` from a live URL and asserts it is a well-formed
`llms.txt` map per the llmstxt.org convention: an H1 title, an optional
summary blockquote, and one or more markdown link sections that point
agents/LLMs at a site's key content. It complements `reliability:seo`,
which covers what human-facing crawlers read, while this covers the
surface an AI assistant reads.

`llms.txt` is an *unratified* convention, so the hard-fail bar is
deliberately low (reachable, non-empty, has an H1, has at least one
link). Spec niceties (summary blockquote, plain-text/markdown
content-type, link reachability) are advisory notes that only fail the
check when a config knob escalates them.

CLI surface:
  slopstopper run reliability:llms-txt -- --url URL [--path /llms.txt]
        [--check-links] [--require-summary]

Stdlib-only (urllib). Writes .ss/reports/llms-txt/llms-txt-report.{md,json}.

Configuration (.slopstopper.yml, all optional):

    reliability:
      llms_txt:
        check_links: false      # HEAD-verify every link inside llms.txt
        require_summary: false   # escalate a missing `> summary` to hard-fail

Env-var equivalents the CLI also honours (precedence: flag > env > config):
  LLMS_TXT_TEST_URL   base URL to audit (required)
  LLMS_TXT_PATH       path to the file (default: /llms.txt)

See .slopstopper.yml.example for the canonical schema.

Exit codes:
  0: llms.txt present and well-formed
  1: failures detected
  2: the URL is missing, or its scheme is not http/https
"""

from __future__ import annotations

import argparse
import os
import re
import urllib.error
import urllib.parse
from pathlib import Path

from slopstopper import config, output
from slopstopper.checks import _http, _report
from slopstopper.checks._contract import refuse_unsafe_url

REPORT_DIR = Path(".ss/reports/llms-txt")
REPORT_JSON = REPORT_DIR / "llms-txt-report.json"
REPORT_MD = REPORT_DIR / "llms-txt-report.md"
USER_AGENT = "SlopStopper-LlmsTxt-Check/1.0"
DEFAULT_PATH = "/llms.txt"
MARKDOWN_CONTENT_TYPES = ("text/plain", "text/markdown")

# Matches a markdown inline link: [label](target)
LINK_RE = re.compile(r"\[[^\]]+\]\(([^)]+)\)")

# Consumed by `slopstopper emit reliability:llms-txt --target pr-comment`.
# No issue keys: the check's exit code fails the workflow, no
# main-branch issue is created.
META = {
    "report_path": str(REPORT_MD),
    "comment_discriminator": "📄 llms.txt",
}


# ── safety ───────────────────────────────────────────────────────


_LABEL = "llms.txt check"


def _fetch(url: str) -> tuple[int, str, str]:
    return _http.fetch_text(url, USER_AGENT, label=_LABEL)


def _head_ok(url: str) -> tuple[bool, str]:
    """Return (ok, detail) for a link target; ok means reachable and not 4xx/5xx."""
    return _http.head_ok(url, USER_AGENT, label=_LABEL)


# ── content validation ───────────────────────────────────────────


def _first_content_line(body: str) -> str:
    for line in body.splitlines():
        if line.strip():
            return line.strip()
    return ""


def _has_summary(body: str) -> bool:
    """True if a `> summary` blockquote follows the H1 before any heading."""
    seen_h1 = False
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if not seen_h1:
            seen_h1 = stripped.startswith("# ")
            continue
        if stripped.startswith(">"):
            return True
        if stripped.startswith("#"):
            return False
    return False


def _extract_links(body: str) -> list[str]:
    return LINK_RE.findall(body)


def _check_links(links: list[str], base: str, notes: list[str], issues: list[str], hard: bool) -> list[dict]:
    results: list[dict] = []
    for link in links:
        target = urllib.parse.urljoin(base, link)
        if not _http.is_http_url(target):
            continue  # skip mailto:, relative anchors that don't resolve to http(s)
        ok, detail = _head_ok(target)
        results.append({"url": target, "ok": ok, "detail": detail})
        if not ok:
            msg = f"Link not reachable ({target}): {detail}"
            (issues if hard else notes).append(msg)
    return results


def _validate_body(
    status: int,
    ctype: str,
    body: str,
    require_summary: bool,
) -> tuple[list[str], list[str], list[str]]:
    """Return (issues, notes, links) for a fetched llms.txt body."""
    issues: list[str] = []
    notes: list[str] = []

    if status != 200:
        issues.append(f"HTTP status {status} (expected 200)")
    if not any(ct in ctype.lower() for ct in MARKDOWN_CONTENT_TYPES):
        notes.append(f"Content-Type is {ctype!r}, expected text/plain or text/markdown")
    if not body.strip():
        issues.append("llms.txt is empty")
    if not _first_content_line(body).startswith("# "):
        issues.append("Missing H1 title (first line should be `# <name>`)")

    links = _extract_links(body)
    if not links:
        issues.append("No markdown links found. llms.txt should link to key content")
    if not _has_summary(body):
        msg = "Missing `> summary` blockquote after the H1 (recommended by the spec)"
        (issues if require_summary else notes).append(msg)

    return issues, notes, links


def _audit(
    base: str,
    path: str,
    check_links: bool,
    require_summary: bool,
) -> dict:
    file_url = urllib.parse.urljoin(base.rstrip("/") + "/", path.lstrip("/"))

    try:
        status, ctype, body = _fetch(file_url)
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as e:
        return {
            "url": file_url,
            "status": "fail",
            "issues": [f"llms.txt not reachable: {type(e).__name__}: {e}"],
            "notes": [],
            "link_count": 0,
            "link_checks": [],
        }

    issues, notes, links = _validate_body(status, ctype, body, require_summary)

    link_checks: list[dict] = []
    if links and check_links:
        link_checks = _check_links(links, file_url, notes, issues, hard=False)

    return {
        "url": file_url,
        "status": "fail" if issues else "pass",
        "issues": issues,
        "notes": notes,
        "link_count": len(links),
        "link_checks": link_checks,
    }


# ── markdown report ──────────────────────────────────────────────


def _build_markdown_report(result: dict) -> str:
    lines: list[str] = []
    lines.append("# 📄 llms.txt Report")
    lines.append("")
    lines.append(f"**File:** {result['url']}")
    lines.append("")
    lines.append(f"**Overall:** {'✅ PASS' if result['status'] == 'pass' else '❌ FAIL'}")
    lines.append("")
    lines.append(f"**Links found:** {result['link_count']}")
    lines.append("")
    lines.extend(_report.render_issues(result["issues"]))
    lines.extend(_report.render_notes(result["notes"]))
    if result["link_checks"]:
        lines.append("<details><summary>Link checks</summary>")
        lines.append("")
        lines.append("| Link | Status |")
        lines.append("|---|---|")
        for lc in result["link_checks"]:
            icon = "✅" if lc["ok"] else "❌"
            lines.append(f"| `{lc['url']}` | {icon} {lc['detail']} |")
        lines.append("")
        lines.append("</details>")
        lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## How to Fix")
    lines.append("")
    lines.append(
        "- **Not reachable** → publish an `llms.txt` at your site root. See "
        "[docs/reliability/LLMS_TXT.md](../../../docs/reliability/LLMS_TXT.md)."
    )
    lines.append(
        "- **Missing H1 / links** → the file is markdown: start with `# <name>`, "
        "add a `> summary` line, then `## Section` headings with `- [label](url)` links."
    )
    lines.append("")
    return "\n".join(lines) + "\n"


def _write_reports(result: dict) -> None:
    _report.write_reports(REPORT_JSON, REPORT_MD, result, _build_markdown_report)


def _print_result(result: dict) -> None:
    icon = "✅" if result["status"] == "pass" else "❌"
    output._emit(f"  {icon} {result['url']}")
    for issue in result["issues"]:
        output._emit(f"      - {issue}")


# ── CLI entrypoint ───────────────────────────────────────────────


def _parse_args(args: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="slopstopper run reliability:llms-txt", add_help=False)
    p.add_argument("url_positional", nargs="?", default=None, help="Base URL to audit")
    p.add_argument("--url", default=None, help="Base URL to audit")
    p.add_argument("--path", default=None, help="Path to llms.txt (default: /llms.txt)")
    p.add_argument("--check-links", action="store_true", help="HEAD-verify links inside llms.txt")
    p.add_argument(
        "--require-summary",
        action="store_true",
        help="Fail if the `> summary` blockquote is missing",
    )
    p.add_argument("--help", "-h", action="help")
    return p.parse_args(args or [])


def _resolve_url(parsed_url: str | None) -> str | None:
    return parsed_url or os.environ.get("LLMS_TXT_TEST_URL")


def _resolve_path(parsed_path: str | None) -> str:
    return parsed_path or os.environ.get("LLMS_TXT_PATH") or DEFAULT_PATH


def run(args: list[str] | None = None) -> int:
    parsed = _parse_args(args)
    url = _resolve_url(parsed.url_positional or parsed.url)
    if not url:
        output.error("llms.txt target URL is required")
        output._emit("Usage:")
        output._emit("  slopstopper run reliability:llms-txt -- --url https://your-site.example.com")
        output._emit("  LLMS_TXT_TEST_URL=https://your-site slopstopper run reliability:llms-txt")
        return 2

    if (rc := refuse_unsafe_url(url, _LABEL)) is not None:
        return rc

    path = _resolve_path(parsed.path)
    check_links = parsed.check_links or config.get_bool("reliability.llms_txt.check_links", False)
    require_summary = parsed.require_summary or config.get_bool("reliability.llms_txt.require_summary", False)

    output.status("📄", f"llms.txt audit against: {url}{path}")
    output.separator()

    try:
        result = _audit(url, path, check_links, require_summary)
    except ValueError as e:
        # A bad input the up-front URL guard couldn't see (a configured path
        # that composes to an unusable URL): the check could not run.
        output.error(str(e))
        return 2

    _write_reports(result)
    _print_result(result)

    output.separator()
    if result["status"] == "pass":
        output.success("llms.txt is present and well-formed.")
        return 0
    output.error("Failures detected. See .ss/reports/llms-txt/llms-txt-report.md for full details.")
    return 1
