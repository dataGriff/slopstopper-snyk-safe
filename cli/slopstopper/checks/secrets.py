"""Secrets detection (Gitleaks wrapper).

Ports the bash security:secrets flow into one self-contained check:

  task secrets:check-tool   (installs gitleaks if missing)
  + gitleaks detect --source=. --redact --report-format=json --report-path=...
  + python3 .ss/scripts/generate-secrets-md.py

It subprocess-invokes `gitleaks`, following the same licensing-boundary
pattern as complexity (lizard). Adopters install gitleaks themselves (MIT). The
slopstopper-cli wheel ships zero gitleaks code.

The JSON report never holds a credential on disk. Gitleaks' native output
carries the captured value verbatim (`Secret`), the source text around it
(`Match`, `Line`) and the commit message (`Message`), which can quote the
same value. Both reports are uploaded as CI artifacts, which anyone with
read access to the repo can download for the retention window, so a
check whose job is to contain a leak would otherwise widen its audience.
Two layers, so no window exists in which an unredacted file sits on disk:

  1. gitleaks runs with `--redact`, so `Secret` / `Match` are written as
     "REDACTED" by the scanner itself, so there is nothing to clean up if
     the check is killed between the scan and the rewrite.
  2. `_read_findings` is the report's only read site: it strips the
     credential-bearing keys (plus `Author` / `Email`, PII with no use in
     a report) and rewrites the file before returning, so no caller in
     this module or any importer ever sees an unredacted finding.

A report that cannot be read or parsed is scrubbed and replaced by one
sentinel finding, and the check exits 1: gitleaks ran and wrote
something, which may have been a secret, so it is treated as one and a
tracking issue is opened rather than the run shrugged off. No report at
all means gitleaks never got as far as writing one (not a git repo, a bad
ref, killed): that is exit 2. The previous run's report is removed before
every scan, so it can never stand in for this one.

Exit codes:
  0: no secrets detected
  1: one or more secrets detected, or gitleaks' report could not be read
      (counted as a finding, as described above). The check itself is the gate
  2: gitleaks is not installed, arguments were passed (this check takes
      none), or gitleaks wrote no report, so the scan did not run
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from slopstopper import output
from slopstopper.checks import _report
from slopstopper.checks._contract import reject_extra_args, scan_incomplete

REPORT_DIR = Path(".ss/reports/secrets")
REPORT_JSON = REPORT_DIR / "secrets-report.json"
REPORT_MD = REPORT_DIR / "secrets-report.md"

# Consumed by `slopstopper emit security:secrets --target {pr-comment,issue}`.
# Discriminator substring `Secrets Detection` matches both the pre-flip JS
# heading ("## 🔑 Secrets Detection") and the post-flip report H1
# ("# Secrets Detection Report"), so the same bot comment is reused after
# the workflow flip. Issue title, labels, and follow-up string are
# byte-identical to the legacy block.
META = {
    "report_path": str(REPORT_MD),
    "comment_discriminator": "Secrets Detection",
    "issue_title": "⚠️ Secrets Detected in Main Branch",
    "issue_labels": ["secrets", "security"],
    "issue_followup": "🔔 Secrets detected again in commit",
}

_INSTALL_HELP = (
    "gitleaks is not installed.\n"
    "Install with:\n"
    "  brew install gitleaks                   # macOS\n"
    "  sudo apt-get install gitleaks           # Debian/Ubuntu (recent versions)\n"
    "Or pre-built binary from https://github.com/gitleaks/gitleaks/releases"
)


def _gitleaks_available() -> bool:
    return shutil.which("gitleaks") is not None


def _run_gitleaks() -> None:
    """Invoke `gitleaks detect --source=. ...`.

    gitleaks exits 1 both for findings and for a fatal error, so its exit
    code can't tell them apart; the report is the signal. The previous
    run's report is removed first, so "no report" really means this scan
    wrote none.
    """
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_JSON.unlink(missing_ok=True)
    subprocess.run(
        [
            "gitleaks", "detect",
            "--source=.",
            # Scrub `Secret` / `Match` in the report file itself, not just
            # the logs. This is the first of the two redaction layers.
            "--redact",
            "--report-format=json",
            f"--report-path={REPORT_JSON}",
        ],
        check=False,
    )


# Keys in gitleaks' native finding schema that carry the credential itself
# (`Secret`), the source text around it (`Match`, `Line`), the commit
# message (which can quote the value verbatim) and author PII. Matched
# case-insensitively because the report code tolerates both `RuleID` and
# `ruleID` spellings.
REDACTED_KEYS = frozenset({"secret", "match", "line", "message", "author", "email"})

# Written in place of a report slopstopper could not read or parse. It is
# counted as a finding (exit 1), and it carries nothing gitleaks captured.
UNREADABLE_RULE_ID = "slopstopper-unreadable-report"
UNREADABLE_REPORT_FINDING = {
    "RuleID": UNREADABLE_RULE_ID,
    "Description": (
        "gitleaks wrote a report slopstopper could not read or parse; it was "
        "scrubbed and counted as a finding so the check fails closed"
    ),
    "File": str(REPORT_JSON),
    "StartLine": 0,
    "Commit": "",
}


def _redact_finding(finding: dict) -> dict:
    """The finding with every credential-bearing key removed."""
    return {k: v for k, v in finding.items() if k.lower() not in REDACTED_KEYS}


def _redact_findings(findings: list[dict]) -> list[dict]:
    return [_redact_finding(f) if isinstance(f, dict) else f for f in findings]


def _report_is_unreadable(findings: list[dict]) -> bool:
    return any(
        isinstance(f, dict) and f.get("RuleID") == UNREADABLE_RULE_ID for f in findings
    )


def _read_findings() -> list[dict] | None:
    """The report's findings, redacted, with the report on disk rewritten.

    This is the only place the JSON is read, so every caller gets redacted
    findings and the file is scrubbed before this returns. A report that
    cannot be read (`OSError`) or parsed (`ValueError`, which covers both
    `JSONDecodeError` and `UnicodeDecodeError`) or is not a list becomes
    the single `UNREADABLE_REPORT_FINDING`: it is still counted as a
    finding, and its bytes never survive. None means gitleaks wrote no
    report at all, meaning the scan did not run.
    """
    if not REPORT_JSON.exists():
        return None
    try:
        content = REPORT_JSON.read_text().strip()
        data = json.loads(content) if content and content != "null" else []
        if not isinstance(data, list):
            raise ValueError("gitleaks report is not a JSON list")
        findings = _redact_findings(data)
    except (OSError, ValueError):
        findings = [dict(UNREADABLE_REPORT_FINDING)]
    _write_redacted_json(findings)
    return findings


def _write_redacted_json(findings: list[dict]) -> None:
    """Overwrite gitleaks' report with the redacted findings.

    Only ever called for a file gitleaks produced. If the rewrite itself
    fails, the file is removed instead: a report that cannot be scrubbed
    must not be left for the artifact upload. If even that fails, the
    error propagates, because the check must not report success over an
    unredacted file.
    """
    if not REPORT_JSON.exists():
        return
    try:
        REPORT_JSON.write_text(json.dumps(findings, indent=2) + "\n")
    except OSError:
        REPORT_JSON.unlink()
        raise


def _format_finding_row(finding: dict) -> str:
    rule_id = finding.get("RuleID", finding.get("ruleID", "unknown"))
    description = finding.get("Description", finding.get("description", ""))
    file_path = finding.get("File", finding.get("file", "unknown"))
    line = finding.get("StartLine", finding.get("startLine", "?"))
    commit = finding.get("Commit", finding.get("commit", ""))
    short_commit = commit[:8] if commit else "working tree"
    location = f"`{file_path}:{line}`"
    desc_truncated = (description[:57] + "...") if len(description) > 60 else description
    return f"| {rule_id} | {location} | {short_commit} | {desc_truncated} |"


def _build_md_report(findings: list[dict]) -> str:
    total = len(findings)
    md = "# Secrets Detection Report\n\n"
    md += f"**Generated**: {_report.generated_at()}\n\n"
    md += "## Summary\n\n"
    if total == 0:
        md += "## ✅ Secrets Status\n\nNo secrets detected.\n\n"
    else:
        md += f"> ⚠️ **{total} secret(s) detected.** Revoke and remove them immediately.\n\n"
        md += "| Rule | Location | Commit | Description |\n"
        md += "|------|----------|--------|-------------|\n"
        for finding in findings:
            md += _format_finding_row(finding) + "\n"
        md += "\n"
    md += "## Guidelines\n\n"
    md += "- **If secrets are found**: Revoke the credential immediately, then remove it from the codebase and git history\n"
    md += "- Use environment variables or a secrets manager instead of hardcoding credentials\n"
    md += "- Consider adding a `.gitleaks.toml` to tune rules for your project\n"
    md += "- Run `task secrets` locally before pushing to catch issues early\n\n"
    md += "## More Information\n\n"
    md += "- Generated by [Gitleaks](https://gitleaks.io/)\n"
    md += "- Reports location: `.ss/reports/secrets/`\n"
    md += "  - `secrets-report.md` (this file)\n"
    md += "  - `secrets-report.json` (machine-readable; secret values redacted)\n"
    return md


def run(args: list[str] | None = None) -> int:
    if args:
        return reject_extra_args("security:secrets", args)
    if not _gitleaks_available():
        output.error(_INSTALL_HELP)
        return 2

    output.status("🔑", "Running secrets detection…")
    _run_gitleaks()
    findings = _read_findings()
    if findings is None:
        return scan_incomplete(
            REPORT_DIR, REPORT_MD, "Secrets Detection Report",
            f"gitleaks wrote no report at `{REPORT_JSON}`. This usually means the directory is "
            "not a git repository, the clone is too shallow for the history it was asked "
            "to scan, or the process was killed. The scan is treated as not run, not as clean.",
        )
    REPORT_MD.write_text(_build_md_report(findings))

    if _report_is_unreadable(findings):
        output.error(
            "gitleaks' report could not be read or parsed, so it was scrubbed and "
            "counted as a finding. Re-run the scan to see what it holds"
        )
        output.footer(REPORT_DIR, [REPORT_MD.name])
        return 1
    if findings:
        output.warn(f"Found {len(findings)} secret(s). Revoke and remove them immediately")
    else:
        output.success("No secrets detected")
    output.footer(REPORT_DIR, [REPORT_MD.name])
    return 1 if findings else 0
