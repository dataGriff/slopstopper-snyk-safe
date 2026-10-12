"""Tests for the security:dependencies check (Snyk Open Source wrapper)."""

from __future__ import annotations

import json

from slopstopper.checks import _snyk, dependencies


def _vuln(id_: str, severity: str, package: str = "lodash", version: str = "4.17.15", **extra) -> dict:
    return {
        "id": id_, "title": f"Prototype Pollution in {package}", "severity": severity,
        "packageName": package, "version": version, "from": ["app@1.0.0", f"{package}@{version}"],
        "fixedIn": ["4.17.21"], "isUpgradable": True, "isPatchable": False, **extra,
    }


CRITICAL = _vuln("SNYK-JS-LODASH-1", "critical")
HIGH = _vuln("SNYK-JS-MINIMIST-2", "high", "minimist", "1.2.0", fixedIn=[])
MEDIUM = _vuln("SNYK-JS-QS-3", "medium", "qs", "6.5.0")
LICENCE = {**_vuln("snyk:lic:npm:gpl-thing:GPL-3.0", "high", "gpl-thing", "1.0.0"), "type": "license", "title": "GPL-3.0 license"}


def _project(target: str, *vulns: dict) -> dict:
    return {"ok": not vulns, "vulnerabilities": list(vulns), "targetFile": target, "projectName": "app", "packageManager": "npm"}


def _stub_snyk(monkeypatch, rc: int = 0, payload=None, stderr=None):
    def fake_run():
        dependencies.REPORT_DIR.mkdir(parents=True, exist_ok=True)
        if payload is not None:
            dependencies.REPORT_JSON.write_text(json.dumps(payload))
        return rc, stderr or []

    monkeypatch.setattr(dependencies, "_snyk_available", lambda: True)
    monkeypatch.setattr(dependencies, "_run_snyk", fake_run)


# ── collecting findings ──────────────────────────────────────────


def test_findings_tag_each_issue_with_its_manifest():
    found = dependencies._findings([_project("package.json", HIGH), _project("api/requirements.txt", MEDIUM)])
    assert [(f["id"], f["manifest"]) for f in found] == [
        ("SNYK-JS-MINIMIST-2", "package.json"), ("SNYK-JS-QS-3", "api/requirements.txt"),
    ]


def test_findings_collapse_the_same_issue_reached_by_two_paths():
    twice = _project("package.json", HIGH, {**HIGH, "from": ["app@1.0.0", "other@2", "minimist@1.2.0"]})
    assert len(dependencies._findings([twice])) == 1


def test_the_same_issue_in_two_manifests_is_two_rows():
    assert len(dependencies._findings([_project("a/package.json", HIGH), _project("b/package.json", HIGH)])) == 2


def test_fix_describes_what_to_do():
    assert dependencies._fix(CRITICAL) == "upgrade to 4.17.21"
    assert dependencies._fix({**HIGH, "isUpgradable": True}) == "upgrade available"
    assert dependencies._fix({**HIGH, "isUpgradable": False, "isPatchable": True}) == "patch available"
    assert dependencies._fix({**HIGH, "isUpgradable": False}) == "no fix yet"


def test_read_projects_is_none_when_a_project_has_no_vulnerability_list(isolated_cwd):
    dependencies.REPORT_DIR.mkdir(parents=True)
    dependencies.REPORT_JSON.write_text(json.dumps([_project("package.json"), {"ok": False, "error": "could not resolve"}]))
    assert dependencies._read_projects() is None


def test_read_projects_accepts_a_single_object(isolated_cwd):
    dependencies.REPORT_DIR.mkdir(parents=True)
    dependencies.REPORT_JSON.write_text(json.dumps(_project("package.json")))
    assert dependencies._read_projects() == [_project("package.json")]


# ── report ───────────────────────────────────────────────────────


def test_report_separates_blocking_below_gate_and_licence_rows():
    md = dependencies._build_md_report([_project("package.json", CRITICAL, MEDIUM, LICENCE)], "high")
    assert "Blocking vulnerabilities" in md and "SNYK-JS-LODASH-1" in md
    assert "Vulnerabilities below the gate" in md and "SNYK-JS-QS-3" in md
    assert "Licence policy issues" in md and "GPL-3.0 license" in md
    assert "| Licence issues | 1 |" in md


def test_report_is_clean_without_findings():
    md = dependencies._build_md_report([_project("package.json")], "high")
    assert "No vulnerabilities or licence issues detected" in md
    assert "`package.json`" in md


# ── run ──────────────────────────────────────────────────────────


def test_run_returns_two_when_snyk_is_missing(monkeypatch, isolated_cwd, capsys):
    monkeypatch.setattr(dependencies, "_snyk_available", lambda: False)
    assert dependencies.run() == 2
    assert "snyk is not installed" in capsys.readouterr().out


def test_run_rejects_arguments(isolated_cwd, capsys):
    assert dependencies.run(["--dev"]) == 2
    assert "takes no arguments" in capsys.readouterr().out


def test_run_clean(monkeypatch, isolated_cwd, capsys):
    _stub_snyk(monkeypatch, 0, [_project("package.json"), _project("requirements.txt")])
    assert dependencies.run() == 0
    assert "No vulnerabilities or licence issues in 2 manifest(s)" in capsys.readouterr().out


def test_critical_or_high_fails_by_default(monkeypatch, isolated_cwd, capsys):
    _stub_snyk(monkeypatch, 1, [_project("package.json", CRITICAL, MEDIUM)])
    assert dependencies.run() == 1
    out = capsys.readouterr().out
    assert "Found 2 finding(s) across 1 manifest(s)" in out
    assert "1 finding(s) at or above `fail_on: high`" in out


def test_medium_alone_passes_by_default(monkeypatch, isolated_cwd, capsys):
    _stub_snyk(monkeypatch, 1, [_project("package.json", MEDIUM)])
    assert dependencies.run() == 0
    assert "none at or above `fail_on: high`" in capsys.readouterr().out


def test_a_licence_issue_gates_like_a_vulnerability(monkeypatch, isolated_cwd):
    _stub_snyk(monkeypatch, 1, [_project("package.json", LICENCE)])
    assert dependencies.run() == 1


def test_fail_on_low_blocks_everything(monkeypatch, write_config):
    write_config("security:\n  dependencies:\n    fail_on: low\n")
    _stub_snyk(monkeypatch, 1, [_project("package.json", MEDIUM)])
    assert dependencies.run() == 1


def test_no_manifests_is_a_pass_with_a_note(monkeypatch, isolated_cwd, capsys):
    _stub_snyk(monkeypatch, 3, None, ["Could not detect supported target files"])
    assert dependencies.run() == 0
    assert "nothing to scan" in capsys.readouterr().out
    assert "Nothing to scan" in dependencies.REPORT_MD.read_text()


def test_a_failed_scan_is_could_not_run(monkeypatch, isolated_cwd):
    _stub_snyk(monkeypatch, 2, None, ["Authentication error (SNYK-0005)"])
    assert dependencies.run() == 2
    md = dependencies.REPORT_MD.read_text()
    assert "Scan did not complete" in md and "SNYK_TOKEN" in md


def test_a_report_written_with_a_fatal_exit_is_not_a_pass(monkeypatch, isolated_cwd):
    _stub_snyk(monkeypatch, 2, [_project("package.json")])
    assert dependencies.run() == 2


def test_run_snyk_builds_the_command_and_honours_include_dev(monkeypatch, write_config):
    seen = {}

    def fake(args, report_dir, report_json):
        seen["args"] = args
        return 0, []

    monkeypatch.delenv("SNYK_ORG", raising=False)
    monkeypatch.setattr(_snyk, "run", fake)
    write_config("security:\n  dependencies:\n    include_dev: true\n  snyk:\n    org: acme\n")
    dependencies._run_snyk()
    assert seen["args"] == [
        "test", "--all-projects", "--org=acme", f"--json-file-output={dependencies.REPORT_JSON}", "--dev",
    ]
