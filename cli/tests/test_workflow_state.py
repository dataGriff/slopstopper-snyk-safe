"""Tests for slopstopper.workflow_state (ss-* workflows GitHub disabled)."""

from __future__ import annotations

import json
import subprocess

from slopstopper import workflow_state


class _Result:
    def __init__(self, returncode=0, stdout=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = ""


def _gh_returns(monkeypatch, result):
    monkeypatch.setattr(workflow_state.shutil, "which", lambda _: "/usr/bin/gh")
    monkeypatch.setattr(workflow_state.subprocess, "run", lambda *a, **k: result)


def test_none_when_gh_is_missing(monkeypatch):
    monkeypatch.setattr(workflow_state.shutil, "which", lambda _: None)
    assert workflow_state.disabled_ss_workflows() is None


def test_none_when_gh_fails(monkeypatch):
    """Unauthenticated or not a GitHub repo: gh exits non-zero."""
    _gh_returns(monkeypatch, _Result(returncode=1))
    assert workflow_state.disabled_ss_workflows() is None


def test_none_when_gh_times_out(monkeypatch):
    def boom(*a, **k):
        raise subprocess.TimeoutExpired(cmd="gh", timeout=30)

    monkeypatch.setattr(workflow_state.shutil, "which", lambda _: "/usr/bin/gh")
    monkeypatch.setattr(workflow_state.subprocess, "run", boom)
    assert workflow_state.disabled_ss_workflows() is None


def test_none_on_unparseable_output(monkeypatch):
    _gh_returns(monkeypatch, _Result(stdout="not json"))
    assert workflow_state.disabled_ss_workflows() is None


def test_keeps_only_disabled_ss_workflows(monkeypatch):
    rows = [
        {"id": 1, "name": "Smoke", "path": ".github/workflows/ss-reliability-smoke-tests.yml",
         "state": "disabled_inactivity"},
        {"id": 2, "name": "SAST", "path": ".github/workflows/ss-security-sast-check.yml",
         "state": "active"},
        {"id": 3, "name": "Deploy", "path": ".github/workflows/deploy.yml",
         "state": "disabled_inactivity"},
        {"id": 4, "name": "IaC", "path": ".github/workflows/ss-security-iac-check.yml",
         "state": "disabled_manually"},
    ]
    _gh_returns(monkeypatch, _Result(stdout=json.dumps(rows)))
    assert [r["id"] for r in workflow_state.disabled_ss_workflows()] == [1, 4]


def test_enable_command_uses_the_workflow_id():
    assert workflow_state.enable_command({"id": 292375157}) == "gh workflow enable 292375157"
