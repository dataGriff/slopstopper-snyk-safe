"""Static Application Security Testing (Snyk Code wrapper).

Subprocess-invokes `snyk code test` over the repository and reads the
SARIF it writes. SARIF rather than Snyk's proprietary JSON because it is
the format GitHub code scanning ingests: the same file the check gates on
can be uploaded to the Security tab, so one scan feeds both the PR comment
and the organisation's dashboard.

The verdict lives here, not in the workflow. Snyk Code reports each
finding at a SARIF level (`error`, `warning`, `note`), which this module
ranks as high, medium and low; `security.sast.fail_on` names the lowest
severity that fails the check. Snyk Code has no separate critical level in
SARIF, so `fail_on: critical` and `fail_on: high` gate the same findings.
A level this module doesn't recognise ranks as critical, because an
unknown label must not quietly fall below the gate.

What leaves the runner: Snyk Code uploads the source files it analyses to
Snyk's servers for the duration of the scan (see docs/security/SNYK.md
for the data-flow statement). It runs under the organisation named by
`SNYK_ORG` / `security.snyk.org`, authenticated by `SNYK_TOKEN`.

A scan that produced no readable report (Snyk could not authenticate, the
organisation has Snyk Code switched off, the CLI crashed) is "could not
run", not "no findings": the check exits 2 rather than passing a scan
that never happened. A repository with no files Snyk Code supports is a
pass with a note in the report, not a verdict on code that was never
scanned.

Configuration (.slopstopper.yml, optional):

    security:
      sast:
        fail_on: high      # critical | high (default) | medium | low | none
      snyk:
        org: my-org-slug   # or SNYK_ORG

Exit codes:
  0: no findings at or above `security.sast.fail_on`, or nothing to scan
  1: one or more findings at or above `security.sast.fail_on`
  2: snyk is not installed, could not authenticate, exited with an error,
      wrote no readable SARIF, or arguments were passed (this check takes
      none)
"""

from __future__ import annotations

from pathlib import Path

from slopstopper import output
from slopstopper.checks import _report, _snyk
from slopstopper.checks._contract import reject_extra_args, scan_incomplete

REPORT_DIR = Path(".ss/reports/sast")
REPORT_SARIF = REPORT_DIR / "sast-report.sarif"
REPORT_MD = REPORT_DIR / "sast-report.md"
TITLE = "SAST Analysis Report"

# Consumed by `slopstopper emit security:sast --target {pr-comment,issue}`.
META = {
    "report_path": str(REPORT_MD),
    "comment_discriminator": "SAST Analysis",
    "issue_title": "⚠️ SAST Issues in Main Branch",
    "issue_labels": ["sast", "security"],
    "issue_followup": "🔔 SAST findings at or above `security.sast.fail_on` detected again in commit",
}

FAIL_ON_KEY = "security.sast.fail_on"

# SARIF levels → the severity scale every Snyk check gates on.
LEVEL_SEVERITY = {"error": "high", "warning": "medium", "note": "low"}


def _snyk_available() -> bool:
    return _snyk.available()


def _run_snyk() -> tuple[int, list[str]]:
    return _snyk.run(
        ["code", "test", *_snyk.org_args(), f"--sarif-file-output={REPORT_SARIF}", "."],
        REPORT_DIR,
        REPORT_SARIF,
    )


def _read_results() -> list[dict] | None:
    """The SARIF results, or None when there is no usable report."""
    data = _snyk.read_json(REPORT_SARIF)
    if not isinstance(data, dict) or not isinstance(data.get("runs"), list):
        return None
    results: list[dict] = []
    for run in data["runs"]:
        if not isinstance(run, dict) or not isinstance(run.get("results"), list):
            return None
        results += [r for r in run["results"] if isinstance(r, dict)]
    return results


def severity_of(result: dict) -> str:
    """A result's severity on the critical/high/medium/low scale.

    Snyk puts the Snyk Code severity in `properties.snykSeverity` or the
    rule's tags on newer CLIs; the SARIF level is always there. The
    explicit severity wins when present, so a critical stays critical.
    """
    props = result.get("properties") or {}
    explicit = str(props.get("snykSeverity") or props.get("severity") or "").lower()
    if explicit in _snyk.SEVERITY_RANK:
        return explicit
    level = str(result.get("level") or "").lower()
    return LEVEL_SEVERITY.get(level, level or "unknown")


def _location(result: dict) -> str:
    try:
        physical = result["locations"][0]["physicalLocation"]
        uri = physical["artifactLocation"]["uri"]
        line = (physical.get("region") or {}).get("startLine", "?")
        return f"`{uri}:{line}`"
    except (KeyError, IndexError, TypeError):
        return "`unknown`"


def _format_row(result: dict) -> str:
    rule = result.get("ruleId", "unknown")
    message = _snyk.truncate((result.get("message") or {}).get("text", ""))
    return f"| {rule} | {severity_of(result)} | {_location(result)} | {message} |"


def _format_section(results: list[dict], title: str, icon: str) -> str:
    if not results:
        return ""
    out = f"## {icon} {title}\n\n| Rule | Severity | Location | Message |\n|------|----------|----------|---------|\n"
    for result in results:
        out += _format_row(result) + "\n"
    return out + "\n"


def _build_md_report(results: list[dict], gate: str, nothing_to_scan: bool = False) -> str:
    md = f"# {TITLE}\n\n**Generated**: {_report.generated_at()}\n\n"
    if nothing_to_scan:
        return md + _snyk.nothing_supported_md("Snyk Code", "source files")
    blocking, rest = _snyk.partition(results, gate, severity_of)
    md += "## Summary\n\n"
    if not results:
        md += "## ✅ SAST Status\n\nNo findings detected.\n\n"
    else:
        counts = _snyk.count_by_severity(results, severity_of)
        md += "| Metric | Count |\n|--------|-------|\n"
        md += f"| Total findings | {len(results)} |\n"
        for sev in ("critical", "high", "medium", "low"):
            md += f"| {sev.capitalize()} | {counts[sev]} |\n"
        md += "\n"
        md += _format_section(blocking, f"Blocking findings (at or above `fail_on: {gate}`)", "🔴")
        md += _format_section(rest, "Findings below the gate", "⚠️")
    md += "## Guidelines\n\n"
    if gate == "none":
        md += "- **Blocking**: nothing, because `security.sast.fail_on: none` reports findings without failing\n"
    else:
        md += f"- **Blocking**: findings at or above `fail_on: {gate}` fail the check. Fix them, or ignore one narrowly in `.snyk` with a reason and an expiry\n"
        md += "- **Below the gate**: reported for review; they don't fail the check\n"
    md += "- Run `task ss:security:sast` locally to reproduce findings (needs `SNYK_TOKEN`)\n\n"
    md += "## More Information\n\n"
    md += "- Generated by [Snyk Code](https://docs.snyk.io/scan-with-snyk/snyk-code)\n"
    md += "- Reports location: `.ss/reports/sast/`\n"
    md += "  - `sast-report.md` (this file)\n"
    md += "  - `sast-report.sarif` (machine-readable; upload it to GitHub code scanning)\n"
    return md


def run(args: list[str] | None = None) -> int:
    if args:
        return reject_extra_args("security:sast", args)
    if not _snyk_available():
        output.error(_snyk.INSTALL_HELP)
        return 2

    output.running("Running SAST analysis with Snyk Code…")
    rc, stderr_tail = _run_snyk()
    gate = _snyk.fail_on(FAIL_ON_KEY)

    if _snyk.found_nothing(rc):
        REPORT_MD.write_text(_build_md_report([], gate, nothing_to_scan=True))
        output.warn("Snyk Code found no supported source files; nothing to scan")
        output.footer(REPORT_DIR, [REPORT_MD.name])
        return 0

    results = _read_results()
    if results is None or not _snyk.completed(rc):
        return scan_incomplete(
            REPORT_DIR, REPORT_MD, TITLE,
            _snyk.incomplete_detail("Snyk Code", rc, results is not None, stderr_tail, REPORT_SARIF),
        )

    REPORT_MD.write_text(_build_md_report(results, gate))
    blocking = _snyk.blocking(results, gate, severity_of)
    if results:
        counts = _snyk.count_by_severity(results, severity_of)
        output.warn(
            f"Found {len(results)} finding(s): "
            + ", ".join(f"{counts[s]} {s}" for s in ("critical", "high", "medium", "low"))
        )
        if blocking:
            output.error(f"{len(blocking)} finding(s) at or above `fail_on: {gate}`, so the check fails")
        else:
            output.info(f"none at or above `fail_on: {gate}`, so the check does not fail")
    else:
        output.success("No findings detected")
    output.footer(REPORT_DIR, [REPORT_MD.name, REPORT_SARIF.name])
    return 1 if blocking else 0
