"""Tests for `checks/_snyk.py`, the plumbing the four Snyk checks share."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from slopstopper.checks import _snyk, _tools


# ── environment and org ──────────────────────────────────────────


def test_environment_disables_analytics_and_keeps_the_token(monkeypatch):
    monkeypatch.setenv("SNYK_TOKEN", "t0k3n")
    env = _snyk.environment()
    assert env["SNYK_DISABLE_ANALYTICS"] == "1"
    assert env["SNYK_TOKEN"] == "t0k3n"


def test_environment_drops_empty_snyk_variables(monkeypatch):
    """A workflow passes `${{ secrets.SNYK_TOKEN }}` unconditionally, so an
    unset secret arrives as an empty string that must not shadow the CLI's
    own config or defaults."""
    monkeypatch.setenv("SNYK_TOKEN", "")
    monkeypatch.setenv("SNYK_API", "  ")
    monkeypatch.setenv("SNYK_ORG", "acme")
    env = _snyk.environment()
    assert "SNYK_TOKEN" not in env and "SNYK_API" not in env
    assert env["SNYK_ORG"] == "acme"


def test_token_present_ignores_an_empty_value(monkeypatch):
    monkeypatch.setenv("SNYK_TOKEN", "")
    assert not _snyk.token_present()
    monkeypatch.setenv("SNYK_TOKEN", "t")
    assert _snyk.token_present()


def test_org_args_is_empty_when_nothing_is_configured(monkeypatch, isolated_cwd):
    monkeypatch.delenv("SNYK_ORG", raising=False)
    assert _snyk.org_args() == []


def test_org_args_reads_the_config(monkeypatch, write_config):
    monkeypatch.delenv("SNYK_ORG", raising=False)
    write_config("security:\n  snyk:\n    org: acme-platform\n")
    assert _snyk.org_args() == ["--org=acme-platform"]


def test_the_env_var_wins_over_the_config(monkeypatch, write_config):
    write_config("security:\n  snyk:\n    org: from-file\n")
    monkeypatch.setenv("SNYK_ORG", "from-env")
    assert _snyk.org_args() == ["--org=from-env"]


# ── severities and the gate ──────────────────────────────────────


def test_fail_on_reads_a_valid_value(write_config):
    write_config("security:\n  iac:\n    fail_on: medium\n")
    assert _snyk.fail_on("security.iac.fail_on") == "medium"


def test_fail_on_defaults_to_high(isolated_cwd):
    assert _snyk.fail_on("security.iac.fail_on") == "high"


def test_an_unknown_fail_on_warns_and_uses_the_default(write_config, capsys):
    write_config("security:\n  sast:\n    fail_on: severe\n")
    assert _snyk.fail_on("security.sast.fail_on") == "high"
    assert "is not one of critical, high, medium, low, none" in capsys.readouterr().out


def test_fail_on_false_is_an_invalid_value_not_the_default(write_config, capsys):
    """A bare `fail_on: false` must not silently collapse to the default."""
    write_config("security:\n  sast:\n    fail_on: false\n")
    assert _snyk.fail_on("security.sast.fail_on") == "high"
    assert "'false' is not one of" in capsys.readouterr().out


@pytest.mark.parametrize("severity,expected", [("critical", 3), ("HIGH", 2), ("medium", 1), ("low", 0)])
def test_rank_is_case_insensitive(severity, expected):
    assert _snyk.rank(severity) == expected


@pytest.mark.parametrize("severity", ["", None, "severe", "p0"])
def test_an_unknown_severity_ranks_as_critical(severity):
    """Fail closed: a label Snyk has never emitted must not fall below the gate."""
    assert _snyk.rank(severity) == _snyk.UNKNOWN_RANK


FINDINGS = [
    {"id": "c", "severity": "critical"},
    {"id": "h", "severity": "high"},
    {"id": "m", "severity": "medium"},
    {"id": "l", "severity": "low"},
    {"id": "u", "severity": "unheard-of"},
]


def test_blocking_keeps_findings_at_or_above_the_gate():
    assert [f["id"] for f in _snyk.blocking(FINDINGS, "high")] == ["c", "h", "u"]
    assert [f["id"] for f in _snyk.blocking(FINDINGS, "low")] == ["c", "h", "m", "l", "u"]


def test_blocking_is_empty_for_none():
    assert _snyk.blocking(FINDINGS, "none") == []


def test_blocking_takes_a_severity_reader():
    nested = [{"props": {"sev": "high"}}, {"props": {"sev": "low"}}]
    assert _snyk.blocking(nested, "high", lambda f: f["props"]["sev"]) == [nested[0]]


def test_count_by_severity_buckets_unknowns_as_critical():
    assert _snyk.count_by_severity(FINDINGS) == {"critical": 2, "high": 1, "medium": 1, "low": 1}


# ── exit codes and reports ───────────────────────────────────────


def test_completed_and_found_nothing_name_snyks_exit_codes():
    assert _snyk.completed(0) and _snyk.completed(1)
    assert not _snyk.completed(2) and not _snyk.completed(3)
    assert _snyk.found_nothing(3)
    assert not _snyk.found_nothing(0)


def test_read_json_is_none_when_missing_or_malformed(isolated_cwd):
    assert _snyk.read_json(Path("nope.json")) is None
    Path("bad.json").write_text("{not json")
    assert _snyk.read_json(Path("bad.json")) is None


def test_as_projects_wraps_a_single_object_and_passes_a_list():
    assert _snyk.as_projects({"ok": True}) == [{"ok": True}]
    assert _snyk.as_projects([{"a": 1}, {"b": 2}]) == [{"a": 1}, {"b": 2}]
    assert _snyk.as_projects("text") is None
    assert _snyk.as_projects([1, 2]) is None


def test_run_removes_the_previous_report_and_passes_the_environment(monkeypatch, isolated_cwd):
    report_dir = Path(".ss/reports/x")
    report = report_dir / "x.json"
    report_dir.mkdir(parents=True)
    report.write_text("{}")
    seen = {}

    def fake(cmd, tail, env=None, merge_stdout=False):
        seen["cmd"] = cmd
        seen["env"] = env
        seen["merge_stdout"] = merge_stdout
        return 0, []

    monkeypatch.setattr(_tools, "run_tailing_stderr", fake)
    assert _snyk.run(["code", "test"], report_dir, report) == (0, [])
    assert not report.exists(), "a scan that dies must not leave last run's report behind"
    assert seen["cmd"] == ["snyk", "code", "test"]
    assert seen["env"]["SNYK_DISABLE_ANALYTICS"] == "1"
    assert seen["merge_stdout"] is True, "Snyk reports its errors on stdout; the tail must carry them"


def test_run_tailing_stderr_can_fold_stdout_into_the_tail(isolated_cwd):
    rc, tail = _tools.run_tailing_stderr(
        [sys.executable, "-c", "import sys; print('on stdout'); print('on stderr', file=sys.stderr); sys.exit(3)"],
        5, merge_stdout=True,
    )
    assert rc == 3
    assert set(tail) == {"on stdout", "on stderr"}


def test_run_to_file_captures_stdout_and_tails_stderr(monkeypatch, isolated_cwd):
    import subprocess

    out_path = Path(".ss/reports/sbom/sbom.json")

    def fake_run(cmd, stdout, stderr, text, errors, env, check):
        stdout.write(json.dumps({"components": []}))
        return subprocess.CompletedProcess(cmd, 0, stdout=None, stderr="line a\n\nline b\n")

    monkeypatch.setattr(subprocess, "run", fake_run)
    rc, tail = _snyk.run_to_file(["sbom"], out_path.parent, out_path)
    assert rc == 0
    assert tail == ["line a", "line b"]
    assert json.loads(out_path.read_text()) == {"components": []}


def test_run_to_file_removes_an_empty_output(monkeypatch, isolated_cwd):
    import subprocess

    out_path = Path(".ss/reports/sbom/sbom.json")
    monkeypatch.setattr(
        subprocess, "run",
        lambda cmd, **k: subprocess.CompletedProcess(cmd, 2, stdout=None, stderr="boom\n"),
    )
    rc, tail = _snyk.run_to_file(["sbom"], out_path.parent, out_path)
    assert rc == 2 and tail == ["boom"]
    assert not out_path.exists()


def test_run_to_file_turns_a_failed_commands_stdout_into_the_tail(monkeypatch, isolated_cwd):
    """Snyk prints its error on stdout; a failed `snyk sbom` must not leave
    that text under the SBOM's filename, and the report must quote it."""
    import subprocess

    out_path = Path(".ss/reports/sbom/sbom.json")

    def fake_run(cmd, stdout, stderr, text, errors, env, check):
        stdout.write(" ERROR   Authentication error (SNYK-0005)\nStatus:  401 Unauthorized\n")
        return subprocess.CompletedProcess(cmd, 2, stdout=None, stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    rc, tail = _snyk.run_to_file(["sbom"], out_path.parent, out_path)
    assert rc == 2
    assert not out_path.exists()
    assert "Status:  401 Unauthorized" in tail


def test_run_to_file_reports_an_exec_failure(monkeypatch, isolated_cwd):
    import subprocess

    def boom(*a, **k):
        raise OSError("Exec format error")

    monkeypatch.setattr(subprocess, "run", boom)
    out_path = Path(".ss/reports/sbom/sbom.json")
    assert _snyk.run_to_file(["sbom"], out_path.parent, out_path) == (127, ["Exec format error"])


def test_incomplete_detail_says_when_no_token_reached_the_check(monkeypatch):
    """Snyk IaC fails on fetching org settings, not on an auth message, when
    there is no token; the report must still name the missing secret."""
    monkeypatch.delenv("SNYK_TOKEN", raising=False)
    detail = _snyk.incomplete_detail("Snyk IaC", 2, False, ["IAC failure (SNYK-CLI-0012)"], Path("r.json"))
    assert "No `SNYK_TOKEN` reached this check" in detail


def test_incomplete_detail_names_the_token_when_snyk_could_not_authenticate(monkeypatch):
    monkeypatch.setenv("SNYK_TOKEN", "present-but-rejected")
    detail = _snyk.incomplete_detail(
        "Snyk Code", 2, False, ["MissingApiTokenError: `snyk` requires an authenticated account."], Path("r.sarif")
    )
    assert "Snyk Code exited 2 and wrote no readable report" in detail
    assert "SNYK_TOKEN" in detail
    assert "MissingApiTokenError" in detail


def test_incomplete_detail_without_an_auth_error_still_points_at_doctor(monkeypatch):
    monkeypatch.setenv("SNYK_TOKEN", "present")
    detail = _snyk.incomplete_detail("Snyk IaC", 2, True, ["network timeout"], Path("r.json"))
    assert "reported a failure" in detail
    assert "SNYK_TOKEN" not in detail.split("network timeout")[0]
    assert "slopstopper doctor" in detail
    assert "SNYK_API" in detail


def test_truncate_collapses_whitespace_and_cuts_long_text():
    assert _snyk.truncate("a  b\n c") == "a b c"
    assert _snyk.truncate("x" * 100).endswith("...") and len(_snyk.truncate("x" * 100)) == 80
