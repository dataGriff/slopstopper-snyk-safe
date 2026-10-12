"""Tests for the reliability:llms-txt check."""

from __future__ import annotations

import urllib.error

import pytest

from slopstopper.checks import _http, llms_txt


VALID_BODY = (
    "# SlopStopper\n"
    "\n"
    "> A suite that stops the slop.\n"
    "\n"
    "## Site\n"
    "- [Home](https://slopstopper.dev/): landing\n"
    "- [Features](https://slopstopper.dev/features.html): the checks\n"
)


# ── arg / config plumbing ────────────────────────────────────────


def test_parse_args_defaults():
    parsed = llms_txt._parse_args(None)
    assert parsed.url is None
    assert parsed.path is None
    assert parsed.check_links is False
    assert parsed.require_summary is False


def test_parse_args_explicit():
    parsed = llms_txt._parse_args(
        ["--url", "https://example.com", "--path", "/l.txt", "--check-links", "--require-summary"]
    )
    assert parsed.url == "https://example.com"
    assert parsed.path == "/l.txt"
    assert parsed.check_links is True
    assert parsed.require_summary is True


def test_resolve_url_prefers_flag(monkeypatch):
    monkeypatch.setenv("LLMS_TXT_TEST_URL", "https://from-env")
    assert llms_txt._resolve_url("https://from-flag") == "https://from-flag"


def test_resolve_url_falls_back_to_env(monkeypatch):
    monkeypatch.setenv("LLMS_TXT_TEST_URL", "https://from-env")
    assert llms_txt._resolve_url(None) == "https://from-env"


def test_resolve_path_defaults(monkeypatch):
    monkeypatch.delenv("LLMS_TXT_PATH", raising=False)
    assert llms_txt._resolve_path(None) == "/llms.txt"


def test_resolve_path_env_override(monkeypatch):
    monkeypatch.setenv("LLMS_TXT_PATH", "/custom.txt")
    assert llms_txt._resolve_path(None) == "/custom.txt"


# ── safety ───────────────────────────────────────────────────────


def test_require_safe_url_accepts_http():
    _http.require_safe_url("http://example.com", llms_txt._LABEL)
    _http.require_safe_url("https://example.com", llms_txt._LABEL)


def test_require_safe_url_rejects_file_scheme():
    with pytest.raises(ValueError, match="refuses scheme 'file'"):
        _http.require_safe_url("file:///etc/passwd", llms_txt._LABEL)


# ── content parsing ──────────────────────────────────────────────


def test_first_content_line_skips_blanks():
    assert llms_txt._first_content_line("\n\n# Title\nrest") == "# Title"


def test_has_summary_true():
    assert llms_txt._has_summary(VALID_BODY) is True


def test_has_summary_false_when_heading_follows_h1():
    assert llms_txt._has_summary("# Title\n\n## Section\n- [x](https://e.com)") is False


def test_extract_links_finds_markdown_links():
    links = llms_txt._extract_links(VALID_BODY)
    assert "https://slopstopper.dev/" in links
    assert len(links) == 2


# ── audit integration (mocked HTTP) ──────────────────────────────


def test_audit_pass_on_valid_body(monkeypatch):
    monkeypatch.setattr(llms_txt, "_fetch", lambda url: (200, "text/plain", VALID_BODY))
    result = llms_txt._audit("https://example.com", "/llms.txt", False, False)
    assert result["status"] == "pass"
    assert result["issues"] == []
    assert result["link_count"] == 2


def test_audit_fails_when_missing_h1(monkeypatch):
    monkeypatch.setattr(
        llms_txt, "_fetch", lambda url: (200, "text/plain", "no heading\n- [x](https://e.com)")
    )
    result = llms_txt._audit("https://example.com", "/llms.txt", False, False)
    assert result["status"] == "fail"
    assert any("Missing H1" in i for i in result["issues"])


def test_audit_fails_when_no_links(monkeypatch):
    monkeypatch.setattr(llms_txt, "_fetch", lambda url: (200, "text/plain", "# Title\n\n> summary\n"))
    result = llms_txt._audit("https://example.com", "/llms.txt", False, False)
    assert result["status"] == "fail"
    assert any("No markdown links" in i for i in result["issues"])


def test_audit_fails_when_empty(monkeypatch):
    monkeypatch.setattr(llms_txt, "_fetch", lambda url: (200, "text/plain", "   "))
    result = llms_txt._audit("https://example.com", "/llms.txt", False, False)
    assert result["status"] == "fail"
    assert any("empty" in i for i in result["issues"])


def test_audit_fails_when_unreachable(monkeypatch):
    def boom(url):
        raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)

    monkeypatch.setattr(llms_txt, "_fetch", boom)
    result = llms_txt._audit("https://example.com", "/llms.txt", False, False)
    assert result["status"] == "fail"
    assert result["link_count"] == 0
    assert any("not reachable" in i for i in result["issues"])


def test_audit_content_type_note(monkeypatch):
    monkeypatch.setattr(llms_txt, "_fetch", lambda url: (200, "text/html", VALID_BODY))
    result = llms_txt._audit("https://example.com", "/llms.txt", False, False)
    assert result["status"] == "pass"
    assert any("Content-Type" in n for n in result["notes"])


def test_audit_summary_advisory_by_default(monkeypatch):
    body = "# Title\n\n## Section\n- [x](https://e.com)"
    monkeypatch.setattr(llms_txt, "_fetch", lambda url: (200, "text/plain", body))
    result = llms_txt._audit("https://example.com", "/llms.txt", False, require_summary=False)
    assert result["status"] == "pass"
    assert any("summary" in n for n in result["notes"])


def test_audit_summary_hard_fail_when_required(monkeypatch):
    body = "# Title\n\n## Section\n- [x](https://e.com)"
    monkeypatch.setattr(llms_txt, "_fetch", lambda url: (200, "text/plain", body))
    result = llms_txt._audit("https://example.com", "/llms.txt", False, require_summary=True)
    assert result["status"] == "fail"
    assert any("summary" in i for i in result["issues"])


def test_audit_check_links_records_reachability(monkeypatch):
    monkeypatch.setattr(llms_txt, "_fetch", lambda url: (200, "text/plain", VALID_BODY))
    monkeypatch.setattr(llms_txt, "_head_ok", lambda url: (True, "HTTP 200"))
    result = llms_txt._audit("https://example.com", "/llms.txt", check_links=True, require_summary=False)
    assert result["status"] == "pass"
    assert len(result["link_checks"]) == 2


def test_audit_check_links_notes_dead_link(monkeypatch):
    monkeypatch.setattr(llms_txt, "_fetch", lambda url: (200, "text/plain", VALID_BODY))
    monkeypatch.setattr(llms_txt, "_head_ok", lambda url: (False, "HTTP 404"))
    result = llms_txt._audit("https://example.com", "/llms.txt", check_links=True, require_summary=False)
    # dead links are advisory by default, so it still passes
    assert result["status"] == "pass"
    assert any("not reachable" in n for n in result["notes"])


# ── report builders ──────────────────────────────────────────────


def test_build_markdown_report_pass():
    result = {
        "url": "https://example.com/llms.txt",
        "status": "pass",
        "issues": [],
        "notes": [],
        "link_count": 2,
        "link_checks": [],
    }
    md = llms_txt._build_markdown_report(result)
    assert "✅ PASS" in md
    assert "https://example.com/llms.txt" in md


def test_build_markdown_report_fail():
    result = {
        "url": "https://example.com/llms.txt",
        "status": "fail",
        "issues": ["Missing H1 title (first line should be `# <name>`)"],
        "notes": [],
        "link_count": 0,
        "link_checks": [],
    }
    md = llms_txt._build_markdown_report(result)
    assert "❌ FAIL" in md
    assert "Missing H1" in md


# ── end-to-end run() ─────────────────────────────────────────────


def test_run_returns_two_when_url_missing(monkeypatch, isolated_cwd, capsys):
    monkeypatch.delenv("LLMS_TXT_TEST_URL", raising=False)
    rc = llms_txt.run([])
    assert rc == 2
    assert "llms.txt target URL is required" in capsys.readouterr().out


def test_run_returns_zero_on_valid(monkeypatch, isolated_cwd, capsys):
    monkeypatch.setattr(llms_txt, "_fetch", lambda url: (200, "text/plain", VALID_BODY))
    rc = llms_txt.run(["--url", "https://example.com"])
    assert rc == 0
    assert "well-formed" in capsys.readouterr().out
    assert llms_txt.REPORT_DIR.joinpath("llms-txt-report.md").exists()
    assert llms_txt.REPORT_DIR.joinpath("llms-txt-report.json").exists()


def test_run_returns_one_on_failure(monkeypatch, isolated_cwd, capsys):
    monkeypatch.setattr(llms_txt, "_fetch", lambda url: (200, "text/plain", "nothing here"))
    rc = llms_txt.run(["--url", "https://example.com"])
    assert rc == 1
    assert "Failures detected" in capsys.readouterr().out


def test_run_rejects_unsafe_url_scheme(monkeypatch, isolated_cwd, capsys):
    rc = llms_txt.run(["--url", "file:///etc/passwd"])
    assert rc == 2
    assert "refuses scheme 'file'" in capsys.readouterr().out
