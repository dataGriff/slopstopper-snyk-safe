"""Tests for the security:secrets check (gitleaks wrapper)."""

from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from slopstopper.checks import secrets


SAMPLE_FINDINGS = [
    {
        "RuleID": "aws-access-key",
        "Description": "AWS access key found",
        "File": "src/leak.py",
        "StartLine": 12,
        "Commit": "abcdef1234567890",
    },
    {
        "ruleID": "github-token",
        "description": "GitHub token with very long descriptive text " + ("x" * 80),
        "file": "src/other.py",
        "startLine": 99,
        "commit": "",
    },
]


def test_format_finding_row_uses_capitalized_keys():
    row = secrets._format_finding_row(SAMPLE_FINDINGS[0])
    assert "aws-access-key" in row
    assert "src/leak.py:12" in row
    assert "abcdef12" in row  # short commit


def test_format_finding_row_handles_lowercase_keys_and_working_tree():
    row = secrets._format_finding_row(SAMPLE_FINDINGS[1])
    assert "github-token" in row
    assert "src/other.py:99" in row
    assert "working tree" in row


def test_format_finding_row_truncates_long_description():
    row = secrets._format_finding_row(SAMPLE_FINDINGS[1])
    # Truncation marker
    assert "..." in row


def test_build_md_report_clean():
    md = secrets._build_md_report([])
    assert "✅ Secrets Status" in md
    assert "No secrets detected" in md
    assert "| Rule |" not in md  # no findings table


def test_build_md_report_with_findings():
    md = secrets._build_md_report(SAMPLE_FINDINGS)
    assert "2 secret(s) detected" in md
    assert "| Rule | Location |" in md
    assert "aws-access-key" in md
    assert "github-token" in md


def test_read_findings_is_none_when_gitleaks_wrote_no_report(isolated_cwd):
    """No report is "the scan did not run", never "no findings"."""
    assert secrets._read_findings() is None


def test_read_findings_handles_null_payload(isolated_cwd):
    secrets.REPORT_DIR.mkdir(parents=True, exist_ok=True)
    secrets.REPORT_JSON.write_text("null")
    assert secrets._read_findings() == []


def test_read_findings_handles_empty_payload(isolated_cwd):
    secrets.REPORT_DIR.mkdir(parents=True, exist_ok=True)
    secrets.REPORT_JSON.write_text("")
    assert secrets._read_findings() == []


def test_read_findings_parses_list_payload(isolated_cwd):
    secrets.REPORT_DIR.mkdir(parents=True, exist_ok=True)
    secrets.REPORT_JSON.write_text(json.dumps(SAMPLE_FINDINGS))
    out = secrets._read_findings()
    assert len(out) == 2


def test_read_findings_treats_a_dict_payload_as_unreadable(isolated_cwd):
    secrets.REPORT_DIR.mkdir(parents=True)
    secrets.REPORT_JSON.write_text(json.dumps({"not": "a list"}))
    out = secrets._read_findings()
    assert [f["RuleID"] for f in out] == [secrets.UNREADABLE_RULE_ID]


def test_gitleaks_available_uses_shutil_which(monkeypatch):
    monkeypatch.setattr(secrets.shutil, "which", lambda _: "/usr/bin/gitleaks")
    assert secrets._gitleaks_available() is True

    monkeypatch.setattr(secrets.shutil, "which", lambda _: None)
    assert secrets._gitleaks_available() is False


def test_run_returns_two_when_gitleaks_missing(monkeypatch, isolated_cwd, capsys):
    """A missing tool is 'could not run', not 'the repo failed'."""
    monkeypatch.setattr(secrets, "_gitleaks_available", lambda: False)
    rc = secrets.run()
    assert rc == 2
    assert "gitleaks is not installed" in capsys.readouterr().out


def test_run_clean_path_when_no_findings(monkeypatch, isolated_cwd, capsys):
    def fake_gitleaks():
        secrets.REPORT_DIR.mkdir(parents=True, exist_ok=True)
        secrets.REPORT_JSON.write_text("null")

    monkeypatch.setattr(secrets, "_gitleaks_available", lambda: True)
    monkeypatch.setattr(secrets, "_run_gitleaks", fake_gitleaks)
    rc = secrets.run()
    assert rc == 0
    md = secrets.REPORT_MD.read_text()
    assert "No secrets detected" in md
    assert "✅ No secrets detected" in capsys.readouterr().out


def test_run_with_findings_returns_one(monkeypatch, isolated_cwd, capsys):
    """The check is the gate. It used to return 0 with the verdict living
    in a Python heredoc inside the workflow, where no test could reach it,
    so `slopstopper run security:secrets` exited 0 on a live credential."""
    def fake_gitleaks():
        secrets.REPORT_DIR.mkdir(parents=True, exist_ok=True)
        secrets.REPORT_JSON.write_text(json.dumps(SAMPLE_FINDINGS))

    monkeypatch.setattr(secrets, "_gitleaks_available", lambda: True)
    monkeypatch.setattr(secrets, "_run_gitleaks", fake_gitleaks)
    rc = secrets.run()
    assert rc == 1
    out = capsys.readouterr().out
    assert "Found 2 secret(s)" in out
    md = secrets.REPORT_MD.read_text()
    assert "aws-access-key" in md


# ── redaction ────────────────────────────────────────────────────
#
# Gitleaks' native JSON carries the credential itself (`Secret`) and the
# source text around it (`Match`, `Line`). Both reports are uploaded as
# CI artifacts that anyone with read access can download, so the JSON
# must be scrubbed on disk before anything else can pick it up.

# A well-formed fake GitHub PAT. The real gitleaks binary must flag it inside
# the temp repo the end-to-end test builds, so it has to look real; the
# `github-pat` rule is a plain regex that is stable across gitleaks 8.21–8.30
# (the AWS rule is not, because 8.30 stopped flagging a bare AKIA… id). GitHub's own
# push protection checksums PATs, so a random one is never mistaken for live.
# Defined exactly once, on a line gitleaks is told to skip, because the
# secrets workflow scans every branch's history and would otherwise fail
# every open PR on this fixture.
PLANTED_KEY = "ghp_ujOeHwdFcAefAZhnM6Jy8c1rihtYlKNxHddm"  # gitleaks:allow

RAW_FINDING = {
    "RuleID": "github-pat",
    "Description": "GitHub personal access token",
    "File": "config/prod.env",
    "StartLine": 12,
    "EndLine": 12,
    "Commit": "abcdef1234567890",
    "Fingerprint": "abcdef1234567890:config/prod.env:github-pat:12",
    "Secret": PLANTED_KEY,
    "Match": f"GITHUB_TOKEN={PLANTED_KEY}",
    "Line": f"export GITHUB_TOKEN={PLANTED_KEY}",
    "Entropy": 3.5,
}


def test_redact_finding_strips_credential_bearing_keys():
    redacted = secrets._redact_finding(
        {**RAW_FINDING, "Message": f"hotfix: set {PLANTED_KEY}", "Author": "Dev", "Email": "dev@x"}
    )
    for key in ("Secret", "Match", "Line", "Message", "Author", "Email"):
        assert key not in redacted, key
    assert PLANTED_KEY not in json.dumps(redacted)
    # Everything a human needs to find and fix it survives.
    assert redacted["RuleID"] == "github-pat"
    assert redacted["File"] == "config/prod.env"
    assert redacted["StartLine"] == 12
    assert redacted["Commit"] == "abcdef1234567890"
    assert redacted["Fingerprint"].startswith("abcdef12")


def test_redact_finding_is_case_insensitive():
    lower = {"ruleID": "x", "secret": "s", "match": "m", "line": "l", "file": "f"}
    redacted = secrets._redact_finding(lower)
    assert set(redacted) == {"ruleID", "file"}


def test_redact_findings_leaves_non_dict_entries_alone():
    assert secrets._redact_findings(["not a dict", RAW_FINDING])[0] == "not a dict"


def test_run_rewrites_the_json_on_disk_without_secret_values(monkeypatch, isolated_cwd):
    def fake_gitleaks():
        secrets.REPORT_DIR.mkdir(parents=True, exist_ok=True)
        secrets.REPORT_JSON.write_text(json.dumps([RAW_FINDING]))

    monkeypatch.setattr(secrets, "_gitleaks_available", lambda: True)
    monkeypatch.setattr(secrets, "_run_gitleaks", fake_gitleaks)
    assert secrets.run() == 1  # findings are the gate

    on_disk = secrets.REPORT_JSON.read_text()
    assert PLANTED_KEY not in on_disk
    assert '"Secret"' not in on_disk
    assert '"Match"' not in on_disk
    # The workflow gate counts list length, so the shape must survive.
    assert len(json.loads(on_disk)) == 1
    assert PLANTED_KEY not in secrets.REPORT_MD.read_text()


def test_run_scrubs_a_malformed_report_and_fails_closed(monkeypatch, isolated_cwd):
    """An unparseable report may hold a secret, and must not read as "none".

    The workflow gate counts list entries, so the scrubbed file carries one
    sentinel finding and the check exits 1. A truncated report that did
    contain findings can never turn a PR green.
    """

    def fake_gitleaks():
        secrets.REPORT_DIR.mkdir(parents=True, exist_ok=True)
        secrets.REPORT_JSON.write_text('[{"Secret": "' + PLANTED_KEY + '"')  # truncated

    monkeypatch.setattr(secrets, "_gitleaks_available", lambda: True)
    monkeypatch.setattr(secrets, "_run_gitleaks", fake_gitleaks)
    assert secrets.run() == 1  # possibly a secret: counted as one, never "clean"

    on_disk = secrets.REPORT_JSON.read_text()
    assert PLANTED_KEY not in on_disk
    findings = json.loads(on_disk)
    assert [f["RuleID"] for f in findings] == [secrets.UNREADABLE_RULE_ID]
    assert secrets.UNREADABLE_RULE_ID in secrets.REPORT_MD.read_text()


def test_run_scrubs_a_report_that_is_not_utf8_and_fails_closed(monkeypatch, isolated_cwd):
    """UnicodeDecodeError is a ValueError, not a JSONDecodeError, but it must
    still land in the scrub-and-fail path rather than a traceback that
    leaves the raw file behind for the artifact upload."""

    def fake_gitleaks():
        secrets.REPORT_DIR.mkdir(parents=True, exist_ok=True)
        secrets.REPORT_JSON.write_bytes(b'\xff\xfe[{"Secret": "' + PLANTED_KEY.encode() + b'"}]')

    monkeypatch.setattr(secrets, "_gitleaks_available", lambda: True)
    monkeypatch.setattr(secrets, "_run_gitleaks", fake_gitleaks)
    assert secrets.run() == 1
    assert PLANTED_KEY not in secrets.REPORT_JSON.read_text()


def test_run_removes_a_report_it_cannot_rewrite(monkeypatch, isolated_cwd):
    """If the scrub itself fails the raw file is unlinked and the error
    propagates. There is never a green exit over an unredacted report."""

    class Unwritable(type(secrets.REPORT_JSON)):
        def write_text(self, *a, **k):
            raise OSError("read-only filesystem")

    def fake_gitleaks():
        secrets.REPORT_DIR.mkdir(parents=True, exist_ok=True)
        secrets.REPORT_JSON.write_text(json.dumps([RAW_FINDING]))
        monkeypatch.setattr(secrets, "REPORT_JSON", Unwritable(secrets.REPORT_JSON))

    monkeypatch.setattr(secrets, "_gitleaks_available", lambda: True)
    monkeypatch.setattr(secrets, "_run_gitleaks", fake_gitleaks)
    with pytest.raises(OSError):
        secrets.run()
    assert not secrets.REPORT_JSON.exists()


def test_read_findings_is_the_only_read_site_and_returns_redacted(isolated_cwd):
    """Any future importer that reads findings through the module gets
    redacted dicts and finds the file already scrubbed."""
    secrets.REPORT_DIR.mkdir(parents=True)
    secrets.REPORT_JSON.write_text(json.dumps([RAW_FINDING]))
    out = secrets._read_findings()
    assert "Secret" not in out[0] and "Match" not in out[0]
    assert PLANTED_KEY not in secrets.REPORT_JSON.read_text()


def test_run_gitleaks_redacts_at_the_source(monkeypatch, isolated_cwd):
    """`--redact` makes gitleaks write REDACTED into the report file itself,
    so no unredacted bytes exist even if the check dies before the rewrite."""
    seen = {}
    monkeypatch.setattr(
        secrets.subprocess, "run", lambda argv, **kw: seen.setdefault("argv", argv)
    )
    secrets._run_gitleaks()
    assert "--redact" in seen["argv"]
    assert seen["argv"].index("--redact") < seen["argv"].index("--report-format=json")


def test_write_redacted_json_does_not_create_a_file_gitleaks_never_wrote(isolated_cwd):
    secrets._write_redacted_json([])
    assert not secrets.REPORT_JSON.exists()


@pytest.mark.skipif(
    shutil.which("gitleaks") is None or shutil.which("git") is None,
    reason="gitleaks and git must both be on PATH",
)
def test_real_gitleaks_output_is_redacted_end_to_end(isolated_cwd):
    """Plant a token, run the real binary, prove it never reaches disk."""
    planted = PLANTED_KEY
    # Hermetic: a contributor's global commit.gpgsign / defaultBranch must
    # not reach into the temp repo.
    git = [
        "git", "-c", "user.email=t@t", "-c", "user.name=t",
        "-c", "commit.gpgsign=false", "-c", "init.defaultBranch=main",
    ]
    subprocess.run([*git, "init", "-q", "."], check=True)
    (isolated_cwd / "config.env").write_text(f"GITHUB_TOKEN={planted}\n")
    subprocess.run([*git, "add", "-A"], check=True)
    subprocess.run([*git, "commit", "-q", "-m", "leak"], check=True)

    assert secrets.run() == 1  # the planted token is a finding

    findings = json.loads(secrets.REPORT_JSON.read_text())
    assert len(findings) >= 1, "gitleaks should have flagged the planted key"
    for finding in findings:
        for key in ("Secret", "Match", "Line", "Message", "Author", "Email"):
            assert key not in finding, key
    assert planted not in secrets.REPORT_JSON.read_text()
    assert planted not in secrets.REPORT_MD.read_text()
    # The redacted report still says where to look.
    assert findings[0]["File"] == "config.env"
    assert findings[0]["StartLine"] == 1


# ── a scan that never wrote a report ─────────────────────────────


def test_run_returns_two_when_gitleaks_writes_no_report(monkeypatch, isolated_cwd, capsys):
    monkeypatch.setattr(secrets, "_gitleaks_available", lambda: True)
    monkeypatch.setattr(secrets, "_run_gitleaks", lambda: None)
    assert secrets.run() == 2
    assert "did not complete" in capsys.readouterr().out
    assert "Scan did not complete" in secrets.REPORT_MD.read_text()


def test_a_previous_runs_report_cannot_stand_in_for_this_one(monkeypatch, isolated_cwd):
    """gitleaks dies before writing: last run's clean `[]` must not be read."""
    secrets.REPORT_DIR.mkdir(parents=True, exist_ok=True)
    secrets.REPORT_JSON.write_text("[]")
    monkeypatch.setattr(secrets, "_gitleaks_available", lambda: True)
    monkeypatch.setattr(secrets.subprocess, "run", lambda argv, **kw: None)  # writes nothing
    assert secrets.run() == 2
