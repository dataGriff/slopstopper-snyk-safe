"""Tests for the security:sast check (Snyk Code wrapper)."""

from __future__ import annotations

import json

import pytest

from slopstopper.checks import _snyk, sast


def _result(rule: str, level: str, uri: str = "src/app.py", line: int = 42, **props) -> dict:
    return {
        "ruleId": rule,
        "level": level,
        "message": {"text": f"{rule} triggered"},
        "locations": [{"physicalLocation": {"artifactLocation": {"uri": uri}, "region": {"startLine": line}}}],
        "properties": props,
    }


HIGH = _result("python/SqlInjection", "error")
MEDIUM = _result("python/HardcodedPassword", "warning", "src/config.py", 7)
LOW = _result("python/DebugEnabled", "note", "src/settings.py", 3)
CRITICAL = _result("python/CommandInjection", "error", "src/shell.py", 9, snykSeverity="critical")


def _sarif(*results: dict) -> dict:
    return {"version": "2.1.0", "runs": [{"tool": {"driver": {"name": "SnykCode"}}, "results": list(results)}]}


def _stub_snyk(monkeypatch, rc: int = 0, sarif: dict | None = None, stderr: list[str] | None = None):
    def fake_run():
        sast.REPORT_DIR.mkdir(parents=True, exist_ok=True)
        if sarif is not None:
            sast.REPORT_SARIF.write_text(json.dumps(sarif))
        return rc, stderr or []

    monkeypatch.setattr(sast, "_snyk_available", lambda: True)
    monkeypatch.setattr(sast, "_run_snyk", fake_run)


# ── severity mapping ─────────────────────────────────────────────


@pytest.mark.parametrize("result,expected", [(HIGH, "high"), (MEDIUM, "medium"), (LOW, "low"), (CRITICAL, "critical")])
def test_severity_of_maps_sarif_levels_and_honours_an_explicit_severity(result, expected):
    assert sast.severity_of(result) == expected


def test_an_unknown_level_does_not_fall_below_the_gate():
    odd = _result("x", "fatal")
    assert sast.severity_of(odd) == "fatal"
    assert _snyk.blocking([odd], "high", sast.severity_of) == [odd]


def test_format_row_carries_rule_severity_location_and_message():
    row = sast._format_row(HIGH)
    assert "python/SqlInjection" in row and "high" in row and "`src/app.py:42`" in row


def test_format_row_survives_a_result_without_a_location():
    assert "`unknown`" in sast._format_row({"ruleId": "r", "level": "error", "message": {"text": "m"}})


# ── reading the report ───────────────────────────────────────────


def test_read_results_is_none_without_a_report(isolated_cwd):
    assert sast._read_results() is None


def test_read_results_is_none_for_a_non_sarif_payload(isolated_cwd):
    sast.REPORT_DIR.mkdir(parents=True)
    sast.REPORT_SARIF.write_text(json.dumps({"vulnerabilities": []}))
    assert sast._read_results() is None


def test_read_results_flattens_every_run(isolated_cwd):
    sast.REPORT_DIR.mkdir(parents=True)
    sast.REPORT_SARIF.write_text(json.dumps({"runs": [{"results": [HIGH]}, {"results": [LOW]}]}))
    assert sast._read_results() == [HIGH, LOW]


# ── run ──────────────────────────────────────────────────────────


def test_run_returns_two_when_snyk_is_missing(monkeypatch, isolated_cwd, capsys):
    monkeypatch.setattr(sast, "_snyk_available", lambda: False)
    assert sast.run() == 2
    assert "snyk is not installed" in capsys.readouterr().out


def test_run_rejects_arguments(isolated_cwd, capsys):
    assert sast.run(["--foo"]) == 2
    assert "takes no arguments" in capsys.readouterr().out


def test_run_clean_when_no_results(monkeypatch, isolated_cwd, capsys):
    _stub_snyk(monkeypatch, 0, _sarif())
    assert sast.run() == 0
    assert "✅ No findings detected" in capsys.readouterr().out
    assert "No findings detected" in sast.REPORT_MD.read_text()


def test_a_high_finding_fails_and_the_report_lists_everything(monkeypatch, isolated_cwd, capsys):
    _stub_snyk(monkeypatch, 1, _sarif(HIGH, MEDIUM, LOW))
    assert sast.run() == 1
    out = capsys.readouterr().out
    assert "Found 3 finding(s)" in out
    assert "1 finding(s) at or above `fail_on: high`" in out
    md = sast.REPORT_MD.read_text()
    assert "python/SqlInjection" in md and "python/HardcodedPassword" in md and "python/DebugEnabled" in md
    assert "Blocking findings" in md and "Findings below the gate" in md


def test_medium_and_low_alone_do_not_fail_by_default(monkeypatch, isolated_cwd, capsys):
    _stub_snyk(monkeypatch, 1, _sarif(MEDIUM, LOW))
    assert sast.run() == 0
    assert "none at or above `fail_on: high`" in capsys.readouterr().out


def test_fail_on_medium_makes_a_medium_fail(monkeypatch, write_config):
    write_config("security:\n  sast:\n    fail_on: medium\n")
    _stub_snyk(monkeypatch, 1, _sarif(MEDIUM))
    assert sast.run() == 1


def test_fail_on_none_never_fails(monkeypatch, write_config):
    write_config("security:\n  sast:\n    fail_on: none\n")
    _stub_snyk(monkeypatch, 1, _sarif(CRITICAL))
    assert sast.run() == 0
    assert "reports findings without failing" in sast.REPORT_MD.read_text()


def test_no_supported_files_is_a_pass_with_a_note(monkeypatch, isolated_cwd, capsys):
    _stub_snyk(monkeypatch, 3, None, ["No supported files found"])
    assert sast.run() == 0
    assert "nothing to scan" in capsys.readouterr().out
    assert "Nothing to scan" in sast.REPORT_MD.read_text()


def test_a_failed_scan_is_could_not_run_not_clean(monkeypatch, isolated_cwd, capsys):
    _stub_snyk(monkeypatch, 2, None, ["MissingApiTokenError: `snyk` requires an authenticated account."])
    assert sast.run() == 2
    md = sast.REPORT_MD.read_text()
    assert "Scan did not complete" in md
    assert "SNYK_TOKEN" in md
    assert "MissingApiTokenError" in md


def test_a_report_with_a_fatal_exit_is_not_a_clean_pass(monkeypatch, isolated_cwd):
    """Snyk can write a SARIF and still exit 2 (a partial scan); that is not a pass."""
    _stub_snyk(monkeypatch, 2, _sarif())
    assert sast.run() == 2
    assert "reported a failure" in sast.REPORT_MD.read_text()


def test_a_malformed_report_is_could_not_run(monkeypatch, isolated_cwd):
    def fake_run():
        sast.REPORT_DIR.mkdir(parents=True, exist_ok=True)
        sast.REPORT_SARIF.write_text("{not sarif")
        return 0, []

    monkeypatch.setattr(sast, "_snyk_available", lambda: True)
    monkeypatch.setattr(sast, "_run_snyk", fake_run)
    assert sast.run() == 2


def test_run_snyk_builds_the_code_test_command(monkeypatch, isolated_cwd):
    seen = {}

    def fake(args, report_dir, report_json):
        seen["args"] = args
        return 0, []

    monkeypatch.setenv("SNYK_ORG", "acme")
    monkeypatch.setattr(_snyk, "run", fake)
    sast._run_snyk()
    assert seen["args"] == ["code", "test", "--org=acme", f"--sarif-file-output={sast.REPORT_SARIF}", "."]


def test_a_previous_runs_report_cannot_stand_in_for_this_one(monkeypatch, isolated_cwd):
    sast.REPORT_DIR.mkdir(parents=True)
    sast.REPORT_SARIF.write_text(json.dumps(_sarif()))
    monkeypatch.setattr(sast, "_snyk_available", lambda: True)
    monkeypatch.setattr(_snyk._tools, "run_tailing_stderr", lambda cmd, n, env=None, merge_stdout=False: (2, ["crashed"]))
    assert sast.run() == 2
    assert not sast.REPORT_SARIF.exists()
