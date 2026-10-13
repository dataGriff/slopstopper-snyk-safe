"""Dependency vulnerability and licence scanning (Snyk Open Source wrapper).

Subprocess-invokes `snyk test --all-projects` over the repository and
reads the JSON it writes: one result per manifest Snyk found (a lock
file, a `package.json`, a `requirements.txt`, a `go.mod`, …), each with
its vulnerabilities and, when the Snyk organisation has a licence policy,
its licence issues. Both kinds gate the same way: a licence issue carries
the severity the policy gave it.

The verdict lives here, not in the workflow. `security.dependencies.fail_on`
names the lowest severity that fails the check (default high: CRITICAL
and HIGH block, MEDIUM and LOW are reported). A severity this module
doesn't recognise ranks as critical, because an unknown label must not
quietly fall below the gate. Snyk's own `.snyk` policy file is honoured,
so an accepted risk is ignored there, with a reason and an expiry, rather
than by loosening the gate.

What leaves the runner: the dependency graph (package names and
versions), not source code. The scan runs under the organisation named
by `SNYK_ORG` / `security.snyk.org`, authenticated by `SNYK_TOKEN`.
`task ss:security:monitor` (the main-branch workflow step) sends the same
graph to `snyk monitor`, which is what keeps the project on Snyk's
dashboard between pushes.

A scan that produced no readable report (no token, a network failure, a
manifest Snyk could not resolve) is "could not run", not "no findings":
the check exits 2 rather than passing a scan that never happened. A
repository with no manifest Snyk supports passes with a note.

Configuration (.slopstopper.yml, optional):

    security:
      dependencies:
        fail_on: high        # critical | high (default) | medium | low | none
        include_dev: false   # true scans devDependencies too (`--dev`)
      snyk:
        org: my-org-slug     # or SNYK_ORG

Exit codes:
  0: no findings at or above `security.dependencies.fail_on`, or nothing to scan
  1: one or more findings at or above `security.dependencies.fail_on`
  2: snyk is not installed, could not authenticate, exited with an error,
      wrote no readable report, or arguments were passed (this check takes
      none)
"""

from __future__ import annotations

from pathlib import Path

from slopstopper import config, output
from slopstopper.checks import _report, _snyk
from slopstopper.checks._contract import reject_extra_args, scan_incomplete

REPORT_DIR = Path(".ss/reports/dependencies")
REPORT_JSON = REPORT_DIR / "dependencies-report.json"
REPORT_MD = REPORT_DIR / "dependencies-report.md"
TITLE = "Dependency Vulnerability Report"

# Consumed by `slopstopper emit security:dependencies --target {pr-comment,issue}`.
META = {
    "report_path": str(REPORT_MD),
    "comment_discriminator": "Dependency Vulnerability",
    "issue_title": "⚠️ Dependency Vulnerabilities in Main Branch",
    "issue_labels": ["dependencies", "security"],
    "issue_followup": "🔔 Dependency findings at or above `security.dependencies.fail_on` detected again in commit",
}

FAIL_ON_KEY = "security.dependencies.fail_on"


def _snyk_available() -> bool:
    return _snyk.available()


def _run_snyk() -> tuple[int, list[str]]:
    args = ["test", "--all-projects", *_snyk.org_args(), f"--json-file-output={REPORT_JSON}"]
    if config.get_bool("security.dependencies.include_dev", False):
        args.append("--dev")
    return _snyk.run(args, REPORT_DIR, REPORT_JSON)


def _read_projects() -> list[dict] | None:
    """Snyk's per-project results, or None when there is no usable report.

    A project entry without a `vulnerabilities` list is one Snyk could not
    scan (it carries an `error` instead), and the report must not read as
    clean, so the whole scan counts as not run.
    """
    projects = _snyk.as_projects(_snyk.read_json(REPORT_JSON))
    if projects is None:
        return None
    for project in projects:
        if not isinstance(project.get("vulnerabilities"), list):
            return None
    return projects


def _unscanned() -> list[str]:
    """One line per manifest Snyk could not scan, from the report it did
    write: the entry's `error` names the manifest and the cause, and that
    is what the could-not-run report must show."""
    projects = _snyk.as_projects(_snyk.read_json(REPORT_JSON)) or []
    lines = []
    for project in projects:
        if isinstance(project.get("vulnerabilities"), list):
            continue
        manifest = project.get("targetFile") or project.get("path") or project.get("displayTargetFile") or "?"
        reason = project.get("error") or project.get("message") or "no result for this manifest"
        lines.append(f"- `{manifest}`: {_snyk.truncate(str(reason), 200)}")
    return lines


def _findings(projects: list[dict]) -> list[dict]:
    """Every issue across every project, each tagged with its manifest.

    Snyk repeats a vulnerability once per dependency path; the report
    keeps one row per (issue, package, version, manifest) so a count of
    rows is a count of things to fix.
    """
    seen: set[tuple] = set()
    found: list[dict] = []
    for project in projects:
        manifest = project.get("targetFile") or project.get("displayTargetFile") or project.get("projectName") or "?"
        for issue in project["vulnerabilities"]:
            if not isinstance(issue, dict):
                continue
            key = (issue.get("id"), issue.get("packageName"), issue.get("version"), manifest)
            if key in seen:
                continue
            seen.add(key)
            found.append({**issue, "manifest": manifest})
    return found


def _is_licence(issue: dict) -> bool:
    return str(issue.get("type") or "").lower() == "license"


def _fix(issue: dict) -> str:
    fixed_in = issue.get("fixedIn") or []
    if isinstance(fixed_in, list) and fixed_in:
        return "upgrade to " + ", ".join(str(v) for v in fixed_in[:3])
    if issue.get("isUpgradable"):
        return "upgrade available"
    if issue.get("isPatchable"):
        return "patch available"
    return "no fix yet"


def _format_row(issue: dict) -> str:
    package = f"{issue.get('packageName', '?')}@{issue.get('version', '?')}"
    return (
        f"| {issue.get('id', '?')} | {issue.get('severity', 'unknown')} | {package} | "
        f"`{issue['manifest']}` | {_snyk.truncate(issue.get('title', ''), 60)} | {_fix(issue)} |"
    )


def _format_section(issues: list[dict], title: str, icon: str) -> str:
    if not issues:
        return ""
    out = f"## {icon} {title}\n\n"
    out += "| ID | Severity | Package | Manifest | Title | Fix |\n|----|----------|---------|----------|-------|-----|\n"
    for issue in issues:
        out += _format_row(issue) + "\n"
    return out + "\n"


def _guidelines(gate: str) -> str:
    md = "## Guidelines\n\n"
    if gate == "none":
        md += "- **Blocking**: nothing, because `security.dependencies.fail_on: none` reports findings without failing\n"
    else:
        md += f"- **Blocking**: findings at or above `fail_on: {gate}` fail the check. Upgrade the package, or record an accepted risk in `.snyk` with a reason and an expiry\n"
        md += "- **Below the gate**: reported for review; they don't fail the check\n"
    md += "- Run `task ss:security:dependencies` locally to reproduce findings (needs `SNYK_TOKEN`)\n\n"
    md += "## More Information\n\n"
    md += "- Generated by [Snyk Open Source](https://docs.snyk.io/scan-with-snyk/snyk-open-source)\n"
    md += "- Reports location: `.ss/reports/dependencies/`\n"
    md += "  - `dependencies-report.md` (this file)\n"
    md += "  - `dependencies-report.json` (machine-readable)\n"
    return md


def _findings_sections(findings: list[dict], gate: str) -> str:
    """The summary table and the three finding tables."""
    vulns = [f for f in findings if not _is_licence(f)]
    licences = [f for f in findings if _is_licence(f)]
    blocking_vulns, other_vulns = _snyk.partition(vulns, gate)
    counts = _snyk.count_by_severity(findings)
    md = "| Metric | Count |\n|--------|-------|\n"
    md += f"| Total findings | {len(findings)} |\n"
    for sev in ("critical", "high", "medium", "low"):
        md += f"| {sev.capitalize()} | {counts[sev]} |\n"
    md += f"| Licence issues | {len(licences)} |\n\n"
    md += _format_section(blocking_vulns, f"Blocking vulnerabilities (at or above `fail_on: {gate}`)", "🔴")
    md += _format_section(other_vulns, "Vulnerabilities below the gate", "⚠️")
    md += _format_section(licences, "Licence policy issues", "📜")
    return md


def _build_md_report(projects: list[dict], gate: str, nothing_to_scan: bool = False) -> str:
    md = f"# {TITLE}\n\n**Generated**: {_report.generated_at()}\n\n"
    if nothing_to_scan:
        return md + _snyk.nothing_supported_md("Snyk Open Source", "dependency manifests")
    findings = _findings(projects)
    md += "## Summary\n\n"
    md += f"Scanned {len(projects)} manifest(s): " + ", ".join(
        f"`{p.get('targetFile') or p.get('projectName') or '?'}`" for p in projects
    ) + "\n\n"
    if not findings:
        md += "## ✅ Dependency Status\n\nNo vulnerabilities or licence issues detected.\n\n"
    else:
        md += _findings_sections(findings, gate)
    return md + _guidelines(gate)


def run(args: list[str] | None = None) -> int:
    if args:
        return reject_extra_args("security:dependencies", args)
    if not _snyk_available():
        output.error(_snyk.INSTALL_HELP)
        return 2

    output.running("Running dependency scan with Snyk Open Source…")
    rc, stderr_tail = _run_snyk()
    gate = _snyk.fail_on(FAIL_ON_KEY)

    if _snyk.found_nothing(rc):
        REPORT_MD.write_text(_build_md_report([], gate, nothing_to_scan=True))
        output.warn("Snyk found no supported dependency manifests; nothing to scan")
        output.footer(REPORT_DIR, [REPORT_MD.name])
        return 0

    projects = _read_projects()
    if projects is None or not _snyk.completed(rc):
        unscanned = _unscanned()
        detail = _snyk.incomplete_detail(
            "Snyk Open Source", rc, projects is not None or bool(unscanned), stderr_tail, REPORT_JSON
        )
        if unscanned:
            detail += "\n\nSnyk could not scan these manifests:\n\n" + "\n".join(unscanned)
        return scan_incomplete(REPORT_DIR, REPORT_MD, TITLE, detail)

    REPORT_MD.write_text(_build_md_report(projects, gate))
    findings = _findings(projects)
    blocking = _snyk.blocking(findings, gate)
    if findings:
        counts = _snyk.count_by_severity(findings)
        output.warn(
            f"Found {len(findings)} finding(s) across {len(projects)} manifest(s): "
            + ", ".join(f"{counts[s]} {s}" for s in ("critical", "high", "medium", "low"))
        )
        if blocking:
            output.error(f"{len(blocking)} finding(s) at or above `fail_on: {gate}`, so the check fails")
        else:
            output.info(f"none at or above `fail_on: {gate}`, so the check does not fail")
    else:
        output.success(f"No vulnerabilities or licence issues in {len(projects)} manifest(s)")
    output.footer(REPORT_DIR, [REPORT_MD.name, REPORT_JSON.name])
    return 1 if blocking else 0
