"""Tests for the reliability:accessibility check (Playwright wrapper).

The flow itself is `_playwright.run_check`, covered in test_shared_helpers;
these pin what the check contributes: its Check, the page list its spec
reads (via discovery), and the emit strings existing issues dedup on.
"""

from __future__ import annotations

import os
import subprocess

from slopstopper import discovery
from slopstopper.checks import _tools, accessibility
from tests._fakes import playwright_failed


def test_check_describes_the_spec_and_falls_back_to_the_smoke_url():
    assert accessibility.CHECK.spec_name == "accessibility"
    assert accessibility.CHECK.url_env == "ACCESSIBILITY_TEST_URL"
    assert accessibility.CHECK.url_fallbacks == ("SMOKE_TEST_URL",)
    assert accessibility.CHECK.banner_icon == "♿"


def test_build_env_threads_url(isolated_cwd, monkeypatch):
    monkeypatch.delenv("ACCESSIBILITY_PAGES", raising=False)
    monkeypatch.setattr(discovery, "discover", lambda check, event: [])
    env = accessibility._build_env("https://example.com", ci_mode=False)
    assert env["ACCESSIBILITY_TEST_URL"] == "https://example.com"
    assert "ACCESSIBILITY_PAGES" not in env, "nothing discovered leaves the spec's default in place"


def test_build_env_discovers_pages_when_unset(isolated_cwd, monkeypatch):
    monkeypatch.delenv("ACCESSIBILITY_PAGES", raising=False)
    monkeypatch.setattr(discovery, "discover", lambda check, event: ["/", "/about"] if check == "accessibility" else [])
    env = accessibility._build_env("https://example.com", ci_mode=False)
    assert env["ACCESSIBILITY_PAGES"] == "/,/about"


def test_build_env_defaults_pages_to_root_when_unconfigured(isolated_cwd, monkeypatch):
    monkeypatch.delenv("ACCESSIBILITY_PAGES", raising=False)
    env = accessibility._build_env("https://example.com", ci_mode=False)
    assert env["ACCESSIBILITY_PAGES"] == "/"


def test_build_env_preserves_caller_pages(monkeypatch):
    monkeypatch.setenv("ACCESSIBILITY_PAGES", "/preset")
    monkeypatch.setattr(discovery, "discover", lambda check, event: ["/should-not-use"])
    env = accessibility._build_env("https://example.com", ci_mode=False)
    assert env["ACCESSIBILITY_PAGES"] == "/preset"


def test_build_env_ci_flag(isolated_cwd, monkeypatch):
    monkeypatch.setattr(discovery, "discover", lambda check, event: [])
    caller_ci = os.environ.get("CI")
    assert accessibility._build_env("https://example.com", ci_mode=False).get("CI") == caller_ci
    assert accessibility._build_env("https://example.com", ci_mode=True)["CI"] == "true"


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
    assert accessibility.run() == 2
    assert "npx is not available" in capsys.readouterr().out


def test_run_returns_two_when_url_missing(monkeypatch, isolated_cwd, capsys):
    monkeypatch.setattr(_tools, "npx_available", lambda: True)
    monkeypatch.delenv("ACCESSIBILITY_TEST_URL", raising=False)
    monkeypatch.delenv("SMOKE_TEST_URL", raising=False)
    assert accessibility.run([]) == 2
    assert "accessibility target URL is required" in capsys.readouterr().out


def test_run_uses_the_smoke_url_when_its_own_is_unset(monkeypatch, isolated_cwd):
    captured = _stub_playwright(monkeypatch)
    monkeypatch.delenv("ACCESSIBILITY_TEST_URL", raising=False)
    monkeypatch.setenv("SMOKE_TEST_URL", "https://from-smoke")
    assert accessibility.run([]) == 0
    assert captured["env"]["ACCESSIBILITY_TEST_URL"] == "https://from-smoke"


def test_run_invokes_playwright_with_the_spec_and_env(monkeypatch, isolated_cwd):
    captured = _stub_playwright(monkeypatch)
    assert accessibility.run(["--url", "https://example.com"]) == 0
    assert captured["cmd"][:2] == ["npx", "playwright"]
    assert "--reporter=list,json" in captured["cmd"]
    assert any("accessibility.spec.ts" in arg for arg in captured["cmd"])
    assert captured["env"]["ACCESSIBILITY_TEST_URL"] == "https://example.com"


def test_run_ci_mode_threads_html_reporter(monkeypatch, isolated_cwd):
    captured = _stub_playwright(monkeypatch)
    assert accessibility.run(["https://example.com", "--ci"]) == 0
    assert "--reporter=list,html,json" in captured["cmd"]
    assert captured["env"]["CI"] == "true"


def test_run_propagates_playwright_failure_and_writes_the_report(monkeypatch, isolated_cwd):
    _stub_playwright(monkeypatch, rc=1)
    assert accessibility.run(["--url", "https://example.com"]) == 1
    body = accessibility.CHECK.report_md.read_text()
    assert body.startswith("## ♿ Accessibility Audit Results")
    assert "FAILED" in body and "playwright-report" in body


def test_run_writes_report_on_pass(monkeypatch, isolated_cwd):
    _stub_playwright(monkeypatch)
    assert accessibility.run(["--url", "https://example.com"]) == 0
    body = accessibility.CHECK.report_md.read_text()
    assert "PASSED" in body and "https://example.com" in body


def test_meta_matches_the_workflow_strings():
    """Title + label must match the strings existing open issues carry so
    they continue to dedup."""
    assert accessibility.META["report_path"] == str(accessibility.CHECK.report_md)
    assert accessibility.META["comment_discriminator"] == "## ♿ Accessibility Audit Results"
    assert accessibility.META["issue_title"] == "♿ Accessibility Violations Detected on Main Branch"
    assert "accessibility" in accessibility.META["issue_labels"]
    assert "reliability" in accessibility.META["issue_labels"]
