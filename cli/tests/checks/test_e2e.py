"""Tests for the reliability:e2e check (Playwright wrapper).

The flow itself is `_playwright.run_check`, covered in test_shared_helpers;
these pin what e2e contributes: its Check, the env its spec reads
(start paths and the link cap, with validation), and the emit strings.
"""

from __future__ import annotations

import os
import subprocess

from slopstopper.checks import _tools, e2e
from tests._fakes import playwright_failed


def test_check_describes_the_e2e_spec():
    assert e2e.CHECK.spec_name == "e2e"
    assert e2e.CHECK.url_env == "E2E_TEST_URL"
    assert e2e.CHECK.url_fallbacks == ()
    assert e2e.CHECK.report_md.name == "e2e-report.md"


def test_build_env_threads_url_and_config_defaults(isolated_cwd, monkeypatch):
    monkeypatch.delenv("E2E_PAGES", raising=False)
    monkeypatch.delenv("E2E_MAX_LINKS", raising=False)
    caller_ci = os.environ.get("CI")
    env = e2e._build_env("https://example.com", ci_mode=False)
    assert env["E2E_TEST_URL"] == "https://example.com"
    assert env["E2E_PAGES"] == "/"
    assert env["E2E_MAX_LINKS"] == "25"
    assert env.get("CI") == caller_ci


def test_build_env_reads_config_keys(write_config, monkeypatch):
    monkeypatch.delenv("E2E_PAGES", raising=False)
    monkeypatch.delenv("E2E_MAX_LINKS", raising=False)
    write_config("pages:\n  e2e: /,/pricing\ne2e:\n  max_links: 5\n")
    env = e2e._build_env("https://example.com", ci_mode=False)
    assert env["E2E_PAGES"] == "/,/pricing"
    assert env["E2E_MAX_LINKS"] == "5"


def test_build_env_falls_back_when_max_links_is_not_a_positive_int(write_config, monkeypatch, capsys):
    monkeypatch.delenv("E2E_MAX_LINKS", raising=False)
    for raw in ("e2e:\n  max_links: all\n", "e2e:\n  max_links:\n", "e2e:\n  max_links: 0\n"):
        write_config(raw)
        env = e2e._build_env("https://example.com", ci_mode=False)
        assert env["E2E_MAX_LINKS"] == "25", raw
    assert "not a positive integer" in capsys.readouterr().out


def test_build_env_respects_caller_env_vars(monkeypatch):
    monkeypatch.setenv("E2E_PAGES", "/preset")
    monkeypatch.setenv("E2E_MAX_LINKS", "3")
    env = e2e._build_env("https://example.com", ci_mode=False)
    assert env["E2E_PAGES"] == "/preset"
    assert env["E2E_MAX_LINKS"] == "3"


def test_build_env_sets_ci_when_ci_mode():
    env = e2e._build_env("https://example.com", ci_mode=True)
    assert env["CI"] == "true"


# ── run(): the shared flow, driven through this check ────────────


def _stub_playwright(monkeypatch, rc=0):
    captured: dict = {}

    def fake_run(cmd, env, check):
        captured["cmd"] = cmd
        captured["env"] = env
        if rc == 1:
            return playwright_failed(cmd, env, check)
        return subprocess.CompletedProcess(cmd, rc)

    monkeypatch.setattr(_tools, "npx_available", lambda: True)
    monkeypatch.setattr(subprocess, "run", fake_run)
    return captured


def test_run_returns_two_when_npx_missing(monkeypatch, isolated_cwd, capsys):
    monkeypatch.setattr(_tools, "npx_available", lambda: False)
    assert e2e.run() == 2
    assert "npx is not available" in capsys.readouterr().out


def test_run_returns_two_when_url_missing(monkeypatch, isolated_cwd, capsys):
    monkeypatch.setattr(_tools, "npx_available", lambda: True)
    monkeypatch.delenv("E2E_TEST_URL", raising=False)
    assert e2e.run([]) == 2
    assert "e2e target URL is required" in capsys.readouterr().out


def test_run_invokes_playwright_with_the_e2e_spec_and_env(monkeypatch, isolated_cwd):
    captured = _stub_playwright(monkeypatch)
    assert e2e.run(["--url", "https://example.com"]) == 0
    assert captured["cmd"][:2] == ["npx", "playwright"]
    assert "--reporter=list,json" in captured["cmd"]
    assert any("e2e.spec.ts" in arg for arg in captured["cmd"])
    assert captured["env"]["E2E_TEST_URL"] == "https://example.com"
    assert captured["env"]["E2E_MAX_LINKS"] == "25"


def test_run_ci_mode_threads_html_reporter_and_ci_env(monkeypatch, isolated_cwd):
    captured = _stub_playwright(monkeypatch)
    assert e2e.run(["https://example.com", "--ci"]) == 0
    assert "--reporter=list,html,json" in captured["cmd"]
    assert captured["env"]["CI"] == "true"


def test_run_propagates_playwright_failure_and_writes_the_report(monkeypatch, isolated_cwd):
    _stub_playwright(monkeypatch, rc=1)
    assert e2e.run(["--url", "https://example.com"]) == 1
    body = e2e.CHECK.report_md.read_text()
    assert body.startswith("## 🧭 E2E Journey Results")
    assert "FAILED" in body and "playwright-report" in body


def test_run_returns_two_when_playwright_never_ran(monkeypatch, isolated_cwd):
    """Exit 1 with no JSON report means the suite did not reach a verdict."""
    monkeypatch.setattr(_tools, "npx_available", lambda: True)
    monkeypatch.setattr(subprocess, "run", lambda cmd, env, check: subprocess.CompletedProcess(cmd, 1))
    assert e2e.run(["--url", "https://example.com"]) == 2


def test_run_writes_report_on_pass(monkeypatch, isolated_cwd):
    _stub_playwright(monkeypatch)
    assert e2e.run(["--url", "https://example.com"]) == 0
    body = e2e.CHECK.report_md.read_text()
    assert "PASSED" in body and "https://example.com" in body


def test_meta_carries_the_emit_contract():
    assert e2e.META["report_path"] == str(e2e.CHECK.report_md)
    assert e2e.META["comment_discriminator"] == "## 🧭 E2E Journey Results"
    assert "e2e-failure" in e2e.META["issue_labels"]
    assert "reliability" in e2e.META["issue_labels"]
