"""Tests for the security:iac check (Snyk IaC wrapper)."""

from __future__ import annotations

import json

from slopstopper.checks import _snyk, iac


def _issue(id_: str, severity: str, line: int = 12, **extra) -> dict:
    return {
        "id": id_, "title": f"{id_} title", "severity": severity, "lineNumber": line,
        "path": ["resource", "aws_s3_bucket[logs]"], "isIgnored": False,
        "iacDescription": {"issue": "x", "impact": "y", "resolve": "Set `acl = \"private\"`"},
        **extra,
    }


HIGH = _issue("SNYK-CC-TF-45", "high")
MEDIUM = _issue("SNYK-CC-TF-7", "medium", 30)
IGNORED = _issue("SNYK-CC-TF-99", "critical", 1, isIgnored=True)


def _file(target: str, *issues: dict, **extra) -> dict:
    return {"targetFile": target, "ok": not issues, "infrastructureAsCodeIssues": list(issues), **extra}


def _stub_snyk(monkeypatch, rc: int = 0, payload=None, stderr=None):
    def fake_run():
        iac.REPORT_DIR.mkdir(parents=True, exist_ok=True)
        if payload is not None:
            iac.REPORT_JSON.write_text(json.dumps(payload))
        return rc, stderr or []

    monkeypatch.setattr(iac, "_snyk_available", lambda: True)
    monkeypatch.setattr(iac, "_run_snyk", fake_run)


def test_findings_skip_issues_ignored_by_the_policy_file():
    found = iac._findings([_file("main.tf", HIGH, IGNORED), _file("k8s/deploy.yaml", MEDIUM)])
    assert [(f["id"], f["manifest"]) for f in found] == [("SNYK-CC-TF-45", "main.tf"), ("SNYK-CC-TF-7", "k8s/deploy.yaml")]


def test_unparsed_files_are_named_in_the_report():
    files = [_file("main.tf", HIGH), {"targetFile": "broken.tf", "error": "Failed to parse Terraform file", "infrastructureAsCodeIssues": []}]
    md = iac._build_md_report(files, "high")
    assert "could not parse 1 file(s)" in md
    assert "`broken.tf`: Failed to parse Terraform file" in md


def test_format_row_shows_location_and_resolution():
    row = iac._format_row({**HIGH, "manifest": "main.tf"})
    assert "`main.tf:12`" in row and 'acl = "private"' in row and "high" in row


def test_read_files_is_none_for_a_payload_without_issue_lists(isolated_cwd):
    iac.REPORT_DIR.mkdir(parents=True)
    iac.REPORT_JSON.write_text(json.dumps([{"targetFile": "a.tf", "infrastructureAsCodeIssues": "nope"}]))
    assert iac._read_files() is None


def test_run_returns_two_when_snyk_is_missing(monkeypatch, isolated_cwd, capsys):
    monkeypatch.setattr(iac, "_snyk_available", lambda: False)
    assert iac.run() == 2
    assert "snyk is not installed" in capsys.readouterr().out


def test_run_rejects_arguments(isolated_cwd, capsys):
    assert iac.run(["main.tf"]) == 2
    assert "takes no arguments" in capsys.readouterr().out


def test_run_clean(monkeypatch, isolated_cwd, capsys):
    _stub_snyk(monkeypatch, 0, [_file("main.tf"), _file("k8s/deploy.yaml")])
    assert iac.run() == 0
    assert "No misconfigurations in 2 file(s)" in capsys.readouterr().out


def test_a_high_finding_fails(monkeypatch, isolated_cwd, capsys):
    _stub_snyk(monkeypatch, 1, [_file("main.tf", HIGH, MEDIUM)])
    assert iac.run() == 1
    out = capsys.readouterr().out
    assert "Found 2 finding(s) in 1 file(s)" in out
    assert "1 finding(s) at or above `fail_on: high`" in out
    md = iac.REPORT_MD.read_text()
    assert "SNYK-CC-TF-45" in md and "SNYK-CC-TF-7" in md


def test_medium_alone_passes_by_default_and_fails_at_fail_on_medium(monkeypatch, write_config):
    _stub_snyk(monkeypatch, 1, [_file("main.tf", MEDIUM)])
    assert iac.run() == 0
    write_config("security:\n  iac:\n    fail_on: medium\n")
    assert iac.run() == 1


def test_no_iac_files_is_a_pass(monkeypatch, isolated_cwd, capsys):
    _stub_snyk(monkeypatch, 3, None, ["No IaC files found"])
    assert iac.run() == 0
    assert "nothing to scan" in capsys.readouterr().out


def test_a_failed_scan_is_could_not_run(monkeypatch, isolated_cwd):
    _stub_snyk(monkeypatch, 2, None, ["Authentication error"])
    assert iac.run() == 2
    assert "Scan did not complete" in iac.REPORT_MD.read_text()


def test_run_snyk_builds_the_iac_command(monkeypatch, isolated_cwd):
    seen = {}

    def fake(args, report_dir, report_json):
        seen["args"] = args
        return 0, []

    monkeypatch.delenv("SNYK_ORG", raising=False)
    monkeypatch.setattr(_snyk, "run", fake)
    iac._run_snyk()
    assert seen["args"] == ["iac", "test", f"--json-file-output={iac.REPORT_JSON}", "."]
