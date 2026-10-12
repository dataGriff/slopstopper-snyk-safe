"""CSP-Exceptions Drift Detector.

Ports .ss/scripts/check-csp-exceptions.py. Enforces that every per-path
CSP relaxation in the configured header source is documented in
`docs/security/CSP_EXCEPTIONS.md`, and vice versa.

Configuration (.slopstopper.yml):

    headers:
      source: worker/headers.json   # path to header file (set to null to skip)
      format: json                  # json | cloudflare-text | meta | auto

`format: meta` is for hosts that can't send headers (GitHub Pages, S3
without CloudFront): `source` is the built HTML root (`dist`), and each
page's `<meta http-equiv="Content-Security-Policy">` is read instead (see
headers_adapters/meta_adapter.py); `auto` picks it for a source with no
file extension. A page with no policy is a warning. The root only exists
after a build: missing or empty in CI it exits 2, and locally it skips
with a note, so the pre-push gate doesn't need a build.

See .slopstopper.yml.example for the canonical schema and supported
adapters.

Exit codes mirror the bash:
  0: source and doc agree, or no source is configured (a graceful skip)
  1: drift detected (details in report)
  2: required input files missing, unknown adapter format, the built
      HTML root missing in CI (format: meta), or arguments were passed
      (this check takes none)
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

from slopstopper import config, headers_adapters, output
from slopstopper.checks._contract import reject_extra_args

EXCEPTIONS_DOC = Path("docs/security/CSP_EXCEPTIONS.md")
REPORT_DIR = Path(".ss/reports/csp")
REPORT_JSON = REPORT_DIR / "csp-exceptions-report.json"
REPORT_MD = REPORT_DIR / "csp-exceptions-report.md"

# Consumed by `slopstopper emit hygiene:csp-exceptions --target pr-comment`.
# The discriminator substring `🔐 CSP Exceptions` appears in both legacy
# bot comments (pre-flip JS heading "🔐 CSP Exceptions Check") and the
# post-flip body (report H1 "🔐 CSP Exceptions Report"), so a single bot
# comment is reused across the migration. No issue keys: this check fails
# the workflow on drift via its own exit code, no main-branch issue is
# created.
META = {
    "report_path": str(REPORT_MD),
    "comment_discriminator": "🔐 CSP Exceptions",
}

REQUIRED_FIELDS = {
    "Origin allowed",
    "Directives added",
    "Loader SRI",
    "Why",
    "Approved by",
    "Data leaving site",
    "Refresh policy",
}

ORIGIN_RE = re.compile(r"https?://[^\s`'\"\)\],]+")
HEADING_RE = re.compile(r"^###\s+`?(/[^\s`]+)`?\s*$")
FIELD_RE = re.compile(r"^-\s*\*\*([^:*]+):\*\*\s*(.*)$")


def _resolve_source() -> tuple[Path | None, str | None, str | None]:
    """Return (path, format_name, skip_reason)."""
    source_str = config.get("headers.source")
    format_name = config.get("headers.format", "auto") or "auto"
    if not source_str:
        return None, None, "no headers.source configured in .slopstopper.yml"
    source_path = Path(str(source_str))
    if not source_path.exists():
        return source_path, format_name, f"configured headers source {source_path} does not exist"
    return source_path, format_name, None


def _meta_source(source_path: Path, format_name: str) -> bool:
    """Whether the source is read as built HTML: `meta`, or `auto` on a
    directory (a path with no file extension, until it exists)."""
    if format_name == "meta":
        return True
    if format_name != "auto":
        return False
    return source_path.is_dir() if source_path.exists() else not source_path.suffix


def _unbuilt(reason: str) -> int:
    """A <meta> source is the built HTML: could-not-run in CI, a skip locally."""
    if os.environ.get("GITHUB_ACTIONS") == "true":
        output.error(f"CSP exceptions check: {reason}. A <meta> CSP source is the built HTML, so build the site before this check runs.")
        return 2
    output.info(f"CSP exceptions check: {reason} (a <meta> CSP source is the built HTML; build the site to check it), so skipping.")
    return 0


# ── CSP_EXCEPTIONS.md parser ────────────────────────────────────────


def _new_entry() -> dict:
    return {"origins": set(), "sri": None, "fields_seen": set()}


def _apply_doc_field(entry: dict, field: str, value: str) -> None:
    entry["fields_seen"].add(field)
    if field in {"Origin allowed", "Directives added"}:
        entry["origins"].update(o.rstrip("/") for o in ORIGIN_RE.findall(value))
    elif field == "Loader SRI":
        entry["sri"] = value.split()[0] if value else None


def _flush_doc_entry(out: dict, state: dict) -> None:
    if state["current"] is not None and state["path"] is not None:
        out[state["path"]] = state["current"]
        state["current"] = None
        state["path"] = None


def _handle_doc_line(out: dict, state: dict, line: str) -> None:
    if line.startswith("## "):
        _flush_doc_entry(out, state)
        state["in_section"] = line.strip() == "## Exceptions"
        return
    if not state["in_section"]:
        return
    heading = HEADING_RE.match(line)
    if heading:
        _flush_doc_entry(out, state)
        path = heading.group(1)
        # /* is the site-wide CSP baseline, not a per-path exception. The
        # headers side filters it out too, so the doc parser does the same.
        if path == "/*":
            state["path"] = None
            state["current"] = None
            return
        state["path"] = path
        state["current"] = _new_entry()
        return
    if state["current"] is None:
        return
    field = FIELD_RE.match(line)
    if field:
        _apply_doc_field(state["current"], field.group(1).strip(), field.group(2).strip())


def _parse_exceptions_doc(doc_path: Path) -> dict[str, dict]:
    if not doc_path.exists():
        return {}
    out: dict[str, dict] = {}
    state: dict = {"in_section": False, "path": None, "current": None}
    for raw_line in doc_path.read_text().splitlines():
        _handle_doc_line(out, state, raw_line.rstrip())
    _flush_doc_entry(out, state)
    return out


# ── Comparison ──────────────────────────────────────────────────────


def _headers_exception_map(rules: list[dict]) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for rule in rules:
        path = rule["for"]
        if path == "/*" or rule["csp"] is None:
            continue
        external = headers_adapters.csp_origins(rule["csp"])
        if external:
            out[path] = external
    return out


def _missing_policy_issues(rules: list[dict]) -> list[dict]:
    return [
        {
            "severity": "warn",
            "path": rule["for"],
            "message": f"`{rule['for']}` has no Content-Security-Policy meta tag, so the browser enforces no policy on it",
        }
        for rule in rules
        if rule.get("missing")
    ]


def _sri_issues(path: str, sri: str) -> list[dict]:
    if "TODO" in sri.upper():
        return [{
            "severity": "warn",
            "path": path,
            "message": f"`{path}` Loader SRI is a placeholder ({sri}). Refresh it before this exception ships",
        }]
    if not sri:
        return [{
            "severity": "warn",
            "path": path,
            "message": f"`{path}` has no Loader SRI. That is acceptable if the third party does not support SRI, but document why in the entry",
        }]
    return []


def _issues_for_documented_path(path: str, required: set[str], entry: dict, source_label: str) -> list[dict]:
    issues: list[dict] = []
    missing_origins = required - entry["origins"]
    if missing_origins:
        issues.append({
            "severity": "error",
            "path": path,
            "message": f"`{path}` allows {', '.join(sorted(missing_origins))} in {source_label} but those origins are not listed in CSP_EXCEPTIONS.md",
        })
    missing_fields = REQUIRED_FIELDS - entry["fields_seen"]
    if missing_fields:
        issues.append({
            "severity": "error",
            "path": path,
            "message": f"`{path}` is missing required field(s) in CSP_EXCEPTIONS.md: {', '.join(sorted(missing_fields))}",
        })
    issues.extend(_sri_issues(path, entry["sri"] or ""))
    return issues


def _compare(rules: list[dict], doc_entries: dict[str, dict], source_label: str) -> list[dict]:
    issues: list[dict] = _missing_policy_issues(rules)
    headers_map = _headers_exception_map(rules)
    for path, required in headers_map.items():
        entry = doc_entries.get(path)
        if entry is None:
            issues.append({
                "severity": "error",
                "path": path,
                "message": f"`{path}` in {source_label} allows external origins ({', '.join(sorted(required))}) but has no entry in CSP_EXCEPTIONS.md",
            })
            continue
        issues.extend(_issues_for_documented_path(path, required, entry, source_label))
    for path in doc_entries:
        if path not in headers_map:
            issues.append({
                "severity": "error",
                "path": path,
                "message": f"`{path}` is documented in CSP_EXCEPTIONS.md but no matching CSP exception exists in {source_label}",
            })
    return issues


# ── Report writers ─────────────────────────────────────────────────


def _overall_status(issues: list[dict]) -> str:
    if any(i["severity"] == "error" for i in issues):
        return "❌ FAIL"
    if any(i["severity"] == "warn" for i in issues):
        return "⚠️ WARN"
    return "✅ PASS"


def _md_issue_section(title: str, severity: str, issues: list[dict]) -> list[str]:
    bucket = [i for i in issues if i["severity"] == severity]
    if not bucket:
        return []
    lines = [title, ""]
    lines.extend(f"- **{i['path']}**: {i['message']}" for i in bucket)
    lines.append("")
    return lines


def _md_fix_section(source_label: str) -> list[str]:
    return [
        "## How to Fix",
        "",
        "- **Missing doc entry** → add a `### \\`/path\\`` heading under `## Exceptions` in `docs/security/CSP_EXCEPTIONS.md` with all required fields.",
        f"- **Mismatched origins** → make `Origin allowed` / `Directives added` in the doc list every external origin in the corresponding `{source_label}` entry.",
        f"- **Stale doc entry** → remove the heading, or restore the matching entry in `{source_label}`.",
        "- **Placeholder SRI** → recompute with the procedure documented in `CSP_EXCEPTIONS.md` and update both the doc and the page that loads the third-party script.",
        "",
    ]


def _build_md_report(issues: list[dict], summary: dict) -> str:
    source_label = summary.get("source", "headers source")
    lines: list[str] = [
        "# 🔐 CSP Exceptions Report",
        "",
        f"**Status:** {_overall_status(issues)}",
        "",
        f"- Headers source: `{source_label}` (format: `{summary.get('format', 'unknown')}`)",
        f"- Documented exceptions in `docs/security/CSP_EXCEPTIONS.md`: {summary['doc_entries']}",
        f"- CSP exceptions in source (non-`/*`): {summary['headers_exceptions']}",
        "",
    ]
    if not issues:
        lines.extend([f"No drift detected. `{source_label}` and `CSP_EXCEPTIONS.md` agree.", ""])
    else:
        lines.extend(_md_issue_section("## ❌ Errors", "error", issues))
        lines.extend(_md_issue_section("## ⚠️ Warnings", "warn", issues))
    lines.extend(_md_fix_section(source_label))
    return "\n".join(lines) + "\n"


def _write_reports(issues: list[dict], summary: dict) -> None:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_JSON.write_text(json.dumps({"summary": summary, "issues": issues}, indent=2) + "\n")
    REPORT_MD.write_text(_build_md_report(issues, summary))


def _print_results(headers_count: int, doc_count: int, issues: list[dict], source_label: str) -> None:
    output.status("🔐", "CSP exceptions check")
    output._emit(f"   source: {source_label}")
    output._emit(f"   CSP exceptions in source: {headers_count}")
    output._emit(f"   documented in CSP_EXCEPTIONS.md: {doc_count}")
    output.separator()
    if not issues:
        output.success("No drift detected.")
        return
    for i in issues:
        icon = "❌" if i["severity"] == "error" else "⚠️ "
        output._emit(f"   {icon} {i['path']}: {i['message']}")
    output.separator()


def run(args: list[str] | None = None) -> int:
    if args:
        return reject_extra_args("hygiene:csp-exceptions", args)
    source_path, format_name, skip_reason = _resolve_source()
    if source_path is None:
        output.info(f"CSP exceptions check: {skip_reason}, so skipping.")
        return 0
    if skip_reason and _meta_source(source_path, format_name):
        return _unbuilt(skip_reason)
    if skip_reason:
        output.info(f"CSP exceptions check: {skip_reason}, so skipping.")
        return 0
    if format_name not in {*headers_adapters.ADAPTERS.keys(), "auto"}:
        output.error(
            f"Unknown headers.format '{format_name}' in .slopstopper.yml. "
            f"Known: {', '.join(sorted(headers_adapters.ADAPTERS.keys()))} or 'auto'."
        )
        return 2
    if not EXCEPTIONS_DOC.exists():
        output.error("docs/security/CSP_EXCEPTIONS.md not found")
        return 2

    rules = headers_adapters.parse(source_path, format_name)
    if not rules and _meta_source(source_path, format_name):
        return _unbuilt(f"{source_path} has no HTML pages")
    doc_entries = _parse_exceptions_doc(EXCEPTIONS_DOC)
    headers_count = len(_headers_exception_map(rules))
    issues = _compare(rules, doc_entries, str(source_path))

    _write_reports(issues, {
        "doc_entries": len(doc_entries),
        "headers_exceptions": headers_count,
        "source": str(source_path),
        "format": format_name,
    })
    _print_results(headers_count, len(doc_entries), issues, str(source_path))

    if any(i["severity"] == "error" for i in issues):
        output.error("Drift detected. See .ss/reports/csp/csp-exceptions-report.md for full details.")
        return 1
    if issues:
        output.warn("Warnings only, so passing.")
    return 0
