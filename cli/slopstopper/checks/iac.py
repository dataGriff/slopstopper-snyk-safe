"""Infrastructure-as-code misconfiguration scanning (Snyk IaC wrapper).

Subprocess-invokes `snyk iac test` over the repository and reads the JSON
it writes: one result per IaC file Snyk found (Terraform, CloudFormation,
Kubernetes manifests, Helm charts, ARM templates, Dockerfiles when
enabled), each with its misconfigurations.

The verdict lives here, not in the workflow. `security.iac.fail_on` names
the lowest severity that fails the check (default high). A severity this
module doesn't recognise ranks as critical, because an unknown label must
not quietly fall below the gate. Snyk's `.snyk` policy file is honoured,
so an accepted finding is ignored there, with a reason and an expiry.

What leaves the runner: the IaC files are evaluated by the CLI against
rules it downloads; results are not reported to Snyk unless `--report`
is passed, which this check does not do. The scan runs under the
organisation named by `SNYK_ORG` / `security.snyk.org`, authenticated by
`SNYK_TOKEN`.

A scan that produced no readable report is "could not run", not "no
findings": the check exits 2 rather than passing a scan that never
happened. A repository with no IaC files passes with a note: most
application repositories have none, and an unconfigured check is not a
failing check.

Configuration (.slopstopper.yml, optional):

    security:
      iac:
        fail_on: high      # critical | high (default) | medium | low | none
      snyk:
        org: my-org-slug   # or SNYK_ORG

Exit codes:
  0: no findings at or above `security.iac.fail_on`, or no IaC files
  1: one or more findings at or above `security.iac.fail_on`
  2: snyk is not installed, could not authenticate, exited with an error,
      wrote no readable report, or arguments were passed (this check takes
      none)
"""

from __future__ import annotations

from pathlib import Path

from slopstopper import output
from slopstopper.checks import _report, _snyk
from slopstopper.checks._contract import reject_extra_args, scan_incomplete

REPORT_DIR = Path(".ss/reports/iac")
REPORT_JSON = REPORT_DIR / "iac-report.json"
REPORT_MD = REPORT_DIR / "iac-report.md"
TITLE = "Infrastructure as Code Report"

# Consumed by `slopstopper emit security:iac --target {pr-comment,issue}`.
META = {
    "report_path": str(REPORT_MD),
    "comment_discriminator": "Infrastructure as Code",
    "issue_title": "⚠️ IaC Misconfigurations in Main Branch",
    "issue_labels": ["iac", "security"],
    "issue_followup": "🔔 IaC findings at or above `security.iac.fail_on` detected again in commit",
}

FAIL_ON_KEY = "security.iac.fail_on"


def _snyk_available() -> bool:
    return _snyk.available()


def _run_snyk() -> tuple[int, list[str]]:
    return _snyk.run(
        ["iac", "test", *_snyk.org_args(), f"--json-file-output={REPORT_JSON}", "."],
        REPORT_DIR,
        REPORT_JSON,
    )


def _read_files() -> list[dict] | None:
    """Snyk's per-file results, or None when there is no usable report.

    A file Snyk could not parse comes back with an `error` and no issue
    list. It is kept, so the report names it, but counts as scanned with
    no findings only when `ok` is not false.
    """
    files = _snyk.as_projects(_snyk.read_json(REPORT_JSON))
    if files is None:
        return None
    for entry in files:
        if not isinstance(entry.get("infrastructureAsCodeIssues", []), list):
            return None
    return files


def _findings(files: list[dict]) -> list[dict]:
    found: list[dict] = []
    for entry in files:
        target = entry.get("targetFile") or entry.get("targetFilePath") or "?"
        for issue in entry.get("infrastructureAsCodeIssues") or []:
            if isinstance(issue, dict) and not issue.get("isIgnored"):
                found.append({**issue, "manifest": target})
    return found


def _unparsed(files: list[dict]) -> list[str]:
    return [
        f"`{entry.get('targetFile') or entry.get('path') or '?'}`: {_snyk.truncate(entry.get('error') or entry.get('message') or 'could not be parsed', 100)}"
        for entry in files
        if entry.get("error") and not entry.get("infrastructureAsCodeIssues")
    ]


def _format_row(issue: dict) -> str:
    line = issue.get("lineNumber", "?")
    resolve = (issue.get("iacDescription") or {}).get("resolve") or issue.get("resolve") or ""
    return (
        f"| {issue.get('id', '?')} | {issue.get('severity', 'unknown')} | "
        f"`{issue['manifest']}:{line}` | {_snyk.truncate(issue.get('title', ''), 60)} | "
        f"{_snyk.truncate(resolve, 80)} |"
    )


def _format_section(issues: list[dict], title: str, icon: str) -> str:
    if not issues:
        return ""
    out = f"## {icon} {title}\n\n"
    out += "| Rule | Severity | Location | Title | Resolve |\n|------|----------|----------|-------|---------|\n"
    for issue in issues:
        out += _format_row(issue) + "\n"
    return out + "\n"


def _build_md_report(files: list[dict], gate: str, nothing_to_scan: bool = False) -> str:
    md = f"# {TITLE}\n\n**Generated**: {_report.generated_at()}\n\n"
    if nothing_to_scan:
        return md + _snyk.nothing_supported_md("Snyk IaC", "infrastructure-as-code files")
    findings = _findings(files)
    blocking = _snyk.blocking(findings, gate)
    md += "## Summary\n\n"
    md += f"Scanned {len(files)} file(s).\n\n"
    unparsed = _unparsed(files)
    if unparsed:
        md += "> ⚠️ Snyk could not parse " + f"{len(unparsed)} file(s); results may be incomplete.\n\n"
        md += "".join(f"- {line}\n" for line in unparsed) + "\n"
    if not findings:
        md += "## ✅ IaC Status\n\nNo misconfigurations detected.\n\n"
    else:
        counts = _snyk.count_by_severity(findings)
        md += "| Metric | Count |\n|--------|-------|\n"
        md += f"| Total findings | {len(findings)} |\n"
        for sev in ("critical", "high", "medium", "low"):
            md += f"| {sev.capitalize()} | {counts[sev]} |\n"
        md += "\n"
        md += _format_section(blocking, f"Blocking findings (at or above `fail_on: {gate}`)", "🔴")
        md += _format_section([f for f in findings if f not in blocking], "Findings below the gate", "⚠️")
    md += "## Guidelines\n\n"
    if gate == "none":
        md += "- **Blocking**: nothing, because `security.iac.fail_on: none` reports findings without failing\n"
    else:
        md += f"- **Blocking**: findings at or above `fail_on: {gate}` fail the check. Fix the resource, or ignore one narrowly in `.snyk` with a reason and an expiry\n"
        md += "- **Below the gate**: reported for review; they don't fail the check\n"
    md += "- Run `task ss:security:iac` locally to reproduce findings (needs `SNYK_TOKEN`)\n\n"
    md += "## More Information\n\n"
    md += "- Generated by [Snyk IaC](https://docs.snyk.io/scan-with-snyk/snyk-iac)\n"
    md += "- Reports location: `.ss/reports/iac/`\n"
    md += "  - `iac-report.md` (this file)\n"
    md += "  - `iac-report.json` (machine-readable)\n"
    return md


def run(args: list[str] | None = None) -> int:
    if args:
        return reject_extra_args("security:iac", args)
    if not _snyk_available():
        output.error(_snyk.INSTALL_HELP)
        return 2

    output.running("Running infrastructure-as-code scan with Snyk IaC…")
    rc, stderr_tail = _run_snyk()
    gate = _snyk.fail_on(FAIL_ON_KEY)

    if _snyk.found_nothing(rc):
        REPORT_MD.write_text(_build_md_report([], gate, nothing_to_scan=True))
        output.info("No infrastructure-as-code files found; nothing to scan")
        output.footer(REPORT_DIR, [REPORT_MD.name])
        return 0

    files = _read_files()
    if files is None or not _snyk.completed(rc):
        return scan_incomplete(
            REPORT_DIR, REPORT_MD, TITLE,
            _snyk.incomplete_detail("Snyk IaC", rc, files is not None, stderr_tail, REPORT_JSON),
        )

    REPORT_MD.write_text(_build_md_report(files, gate))
    findings = _findings(files)
    blocking = _snyk.blocking(findings, gate)
    if findings:
        counts = _snyk.count_by_severity(findings)
        output.warn(
            f"Found {len(findings)} finding(s) in {len(files)} file(s): "
            + ", ".join(f"{counts[s]} {s}" for s in ("critical", "high", "medium", "low"))
        )
        if blocking:
            output.error(f"{len(blocking)} finding(s) at or above `fail_on: {gate}`, so the check fails")
        else:
            output.info(f"none at or above `fail_on: {gate}`, so the check does not fail")
    else:
        output.success(f"No misconfigurations in {len(files)} file(s)")
    output.footer(REPORT_DIR, [REPORT_MD.name, REPORT_JSON.name])
    return 1 if blocking else 0
