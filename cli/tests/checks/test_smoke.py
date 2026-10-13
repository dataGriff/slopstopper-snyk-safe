"""Tests for the reliability:smoke check (Playwright wrapper).

The flow itself is `_playwright.run_check`, covered in test_shared_helpers;
these pin what smoke contributes: its Check, the env its spec reads, and
the emit strings existing issues dedup on.
"""

from __future__ import annotations

import os
import subprocess

from slopstopper.checks import _tools, smoke
from tests._fakes import playwright_failed


def test_check_describes_the_smoke_spec():
    assert smoke.CHECK.spec_name == "smoke"
    assert smoke.CHECK.url_env == "SMOKE_TEST_URL"
    assert smoke.CHECK.url_fallbacks == ()
    assert smoke.CHECK.report_md.name == "smoke-report.md"


def test_build_env_threads_url_and_config_defaults(isolated_cwd, monkeypatch):
    monkeypatch.delenv("SMOKE_OG_IMAGE_PATH", raising=False)
    monkeypatch.delenv("SMOKE_PAGES", raising=False)
    # GitHub Actions sets CI=true in the runner env. We're asserting that
    # _build_env doesn't ADD CI when ci_mode=False, not that CI is absent
    # from the caller's environment. Snapshot caller-CI before the call.
    caller_ci = os.environ.get("CI")
    env = smoke._build_env("https://example.com", ci_mode=False)
    assert env["SMOKE_TEST_URL"] == "https://example.com"
    assert env["SMOKE_OG_IMAGE_PATH"] == "/og-image.png"
    assert env["SMOKE_PAGES"] == "/"
    assert env.get("CI") == caller_ci


def test_build_env_reads_config_keys(write_config, monkeypatch):
    monkeypatch.delenv("SMOKE_OG_IMAGE_PATH", raising=False)
    monkeypatch.delenv("SMOKE_PAGES", raising=False)
    write_config(
        "smoke:\n  og_image_path: /static/og.png\npages:\n  smoke: /,/blog,/about\n"
    )
    env = smoke._build_env("https://example.com", ci_mode=False)
    assert env["SMOKE_OG_IMAGE_PATH"] == "/static/og.png"
    assert env["SMOKE_PAGES"] == "/,/blog,/about"


def test_build_env_respects_caller_env_vars(monkeypatch):
    monkeypatch.setenv("SMOKE_OG_IMAGE_PATH", "/preset.png")
    monkeypatch.setenv("SMOKE_PAGES", "/preset-page")
    env = smoke._build_env("https://example.com", ci_mode=False)
    # setdefault means caller's env wins over config defaults
    assert env["SMOKE_OG_IMAGE_PATH"] == "/preset.png"
    assert env["SMOKE_PAGES"] == "/preset-page"


def test_build_env_sets_ci_when_ci_mode():
    env = smoke._build_env("https://example.com", ci_mode=True)
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
    assert smoke.run() == 2
    assert "npx is not available" in capsys.readouterr().out


def test_run_returns_two_when_url_missing(monkeypatch, isolated_cwd, capsys):
    monkeypatch.setattr(_tools, "npx_available", lambda: True)
    monkeypatch.delenv("SMOKE_TEST_URL", raising=False)
    assert smoke.run([]) == 2
    assert "smoke target URL is required" in capsys.readouterr().out


def test_run_invokes_playwright_with_the_smoke_spec_and_env(monkeypatch, isolated_cwd):
    captured = _stub_playwright(monkeypatch)
    assert smoke.run(["--url", "https://example.com"]) == 0
    assert captured["cmd"][:2] == ["npx", "playwright"]
    assert "--reporter=list,json" in captured["cmd"]
    assert any("smoke.spec.ts" in arg for arg in captured["cmd"])
    assert captured["env"]["SMOKE_TEST_URL"] == "https://example.com"
    assert captured["env"]["SMOKE_PAGES"] == "/"


def test_run_ci_mode_threads_html_reporter_and_ci_env(monkeypatch, isolated_cwd):
    captured = _stub_playwright(monkeypatch)
    assert smoke.run(["https://example.com", "--ci"]) == 0
    assert "--reporter=list,html,json" in captured["cmd"]
    assert captured["env"]["CI"] == "true"


def test_run_propagates_playwright_failure_and_writes_the_report(monkeypatch, isolated_cwd):
    _stub_playwright(monkeypatch, rc=1)
    assert smoke.run(["--url", "https://example.com"]) == 1
    body = smoke.CHECK.report_md.read_text()
    assert body.startswith("## Smoke Test Results")
    assert "FAILED" in body and "playwright-report" in body


def test_run_writes_report_on_pass(monkeypatch, isolated_cwd):
    _stub_playwright(monkeypatch)
    assert smoke.run(["--url", "https://example.com"]) == 0
    body = smoke.CHECK.report_md.read_text()
    assert "PASSED" in body and "https://example.com" in body


def test_meta_matches_legacy_workflow_strings():
    """Title + label must match the legacy `gh issue create` strings so
    existing open issues continue to dedup after the workflow migrates."""
    assert smoke.META["report_path"] == str(smoke.CHECK.report_md)
    assert smoke.META["comment_discriminator"] == "## Smoke Test Results"
    assert smoke.META["issue_title"] == "❌ Smoke Tests Failing"
    assert "smoke-test-failure" in smoke.META["issue_labels"]
    assert "reliability" in smoke.META["issue_labels"]
