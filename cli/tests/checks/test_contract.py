"""The exit-code contract's shared helpers (`slopstopper.checks._contract`)."""

from __future__ import annotations

import json

import pytest

from slopstopper.checks import _contract
from tests._fakes import PLAYWRIGHT_FAILED_REPORT


def test_runner_exit_maps_onto_the_contract():
    assert _contract.runner_exit(0) == 0
    assert _contract.runner_exit(1) == 1
    assert _contract.runner_exit(1, ran=False) == 2  # exit 1, but nothing ran
    for code in (2, 127, 130, -9):
        assert _contract.runner_exit(code) == 2


def _report(tmp_path, data):
    path = tmp_path / "results.json"
    path.write_text(json.dumps(data))
    return path


def test_playwright_ran_on_a_real_failure(tmp_path):
    assert _contract.playwright_ran(_report(tmp_path, PLAYWRIGHT_FAILED_REPORT))


@pytest.mark.parametrize(
    "data",
    [
        {"errors": [{"message": "Error: No tests found"}], "stats": {}, "suites": []},
        {"errors": [], "stats": {"expected": 0, "unexpected": 0, "flaky": 0}, "suites": []},
        {
            "errors": [],
            "stats": {"expected": 0, "unexpected": 1, "flaky": 0},
            "suites": [{"specs": [{"tests": [{"results": [
                {"status": "failed", "error": {"message": "browserType.launch: Executable doesn't exist"}}
            ]}]}]}],
        },
        "not an object",
    ],
    ids=["global-error", "nothing-executed", "browser-never-launched", "not-an-object"],
)
def test_playwright_did_not_run(tmp_path, data):
    assert not _contract.playwright_ran(_report(tmp_path, data))


def test_playwright_did_not_run_without_a_report(tmp_path):
    assert not _contract.playwright_ran(tmp_path / "missing.json")


def test_refuse_unsafe_url(capsys):
    assert _contract.refuse_unsafe_url("https://example.com", "Test check") is None
    assert _contract.refuse_unsafe_url("file:///etc/passwd", "Test check") == 2
    assert "Test check refuses scheme 'file'" in capsys.readouterr().out


def test_scan_incomplete_says_so_and_returns_two(tmp_path, capsys):
    md = tmp_path / "r" / "report.md"
    assert _contract.scan_incomplete(md.parent, md, "Some Report", "why") == 2
    assert "Scan did not complete" in md.read_text()
    assert "did not complete" in capsys.readouterr().out
