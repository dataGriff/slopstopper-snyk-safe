"""Tests for the reliability:robots-txt check."""

from __future__ import annotations

import urllib.error

import pytest

from slopstopper.checks import _http, robots_txt


VALID_BODY = (
    "User-agent: *\n"
    "Allow: /\n"
    "\n"
    "Sitemap: https://slopstopper.dev/sitemap.xml\n"
    "Llms: https://slopstopper.dev/llms.txt\n"
)

DISALLOW_ALL_BODY = "User-agent: *\nDisallow: /\n\nSitemap: https://e.com/sitemap.xml\n"


# ── arg / config plumbing ────────────────────────────────────────


def test_parse_args_defaults():
    parsed = robots_txt._parse_args(None)
    assert parsed.url is None
    assert parsed.path is None
    assert parsed.check_links is False
    assert parsed.allow_disallow_all is False


def test_parse_args_explicit():
    parsed = robots_txt._parse_args(
        ["--url", "https://example.com", "--path", "/r.txt", "--check-links",
         "--allow-disallow-all"]
    )
    assert parsed.url == "https://example.com"
    assert parsed.path == "/r.txt"
    assert parsed.check_links is True
    assert parsed.allow_disallow_all is True


def test_resolve_url_prefers_flag(monkeypatch):
    monkeypatch.setenv("ROBOTS_TXT_TEST_URL", "https://from-env")
    assert robots_txt._resolve_url("https://from-flag") == "https://from-flag"


def test_resolve_url_falls_back_to_env(monkeypatch):
    monkeypatch.setenv("ROBOTS_TXT_TEST_URL", "https://from-env")
    assert robots_txt._resolve_url(None) == "https://from-env"


def test_resolve_path_defaults(monkeypatch):
    monkeypatch.delenv("ROBOTS_TXT_PATH", raising=False)
    assert robots_txt._resolve_path(None) == "/robots.txt"


def test_resolve_path_env_override(monkeypatch):
    monkeypatch.setenv("ROBOTS_TXT_PATH", "/custom.txt")
    assert robots_txt._resolve_path(None) == "/custom.txt"


# ── safety ───────────────────────────────────────────────────────


def test_require_safe_url_accepts_http():
    _http.require_safe_url("http://example.com", robots_txt._LABEL)
    _http.require_safe_url("https://example.com", robots_txt._LABEL)


def test_require_safe_url_rejects_file_scheme():
    with pytest.raises(ValueError, match="refuses scheme 'file'"):
        _http.require_safe_url("file:///etc/passwd", robots_txt._LABEL)


# ── robots.txt parsing ───────────────────────────────────────────


def test_global_directive_extracts_sitemaps():
    sitemaps = robots_txt._global_directive(VALID_BODY, "Sitemap")
    assert sitemaps == ["https://slopstopper.dev/sitemap.xml"]


def test_global_directive_is_case_insensitive():
    assert robots_txt._global_directive("sitemap: https://e.com/s.xml", "Sitemap") == [
        "https://e.com/s.xml"
    ]


def test_directive_strips_comments():
    assert robots_txt._directive("Disallow: /admin  # secret") == ("disallow", "/admin")


def test_blocks_all_true_for_wildcard_disallow_root():
    assert robots_txt._blocks_all_crawlers(DISALLOW_ALL_BODY) is True


def test_blocks_all_false_for_scoped_disallow():
    assert robots_txt._blocks_all_crawlers("User-agent: *\nDisallow: /admin\n") is False


def test_blocks_all_false_when_disallow_root_targets_other_agent():
    body = "User-agent: BadBot\nDisallow: /\n\nUser-agent: *\nAllow: /\n"
    assert robots_txt._blocks_all_crawlers(body) is False


def test_blocks_all_true_when_star_shares_group_with_other_agent():
    body = "User-agent: BadBot\nUser-agent: *\nDisallow: /\n"
    assert robots_txt._blocks_all_crawlers(body) is True


# ── audit integration (mocked HTTP) ──────────────────────────────


def test_audit_pass_on_valid_body(monkeypatch):
    monkeypatch.setattr(robots_txt, "_fetch", lambda url: (200, "text/plain", VALID_BODY))
    result = robots_txt._audit("https://example.com", "/robots.txt", False, False)
    assert result["status"] == "pass"
    assert result["issues"] == []
    assert result["sitemap_count"] == 1
    assert any("`Llms:` is not a robots.txt directive" in n for n in result["notes"])


def test_audit_fails_on_blanket_disallow(monkeypatch):
    monkeypatch.setattr(robots_txt, "_fetch", lambda url: (200, "text/plain", DISALLOW_ALL_BODY))
    result = robots_txt._audit("https://example.com", "/robots.txt", False, False)
    assert result["status"] == "fail"
    assert any("de-indexes the entire site" in i for i in result["issues"])


def test_audit_allow_disallow_all_downgrades_to_note(monkeypatch):
    monkeypatch.setattr(robots_txt, "_fetch", lambda url: (200, "text/plain", DISALLOW_ALL_BODY))
    result = robots_txt._audit(
        "https://example.com", "/robots.txt", False, allow_disallow_all=True
    )
    assert result["status"] == "pass"
    assert any("allowed by allow_disallow_all" in n for n in result["notes"])


def test_audit_fails_when_no_sitemap(monkeypatch):
    monkeypatch.setattr(robots_txt, "_fetch", lambda url: (200, "text/plain", "User-agent: *\nAllow: /\n"))
    result = robots_txt._audit("https://example.com", "/robots.txt", False, False)
    assert result["status"] == "fail"
    assert any("No `Sitemap:`" in i for i in result["issues"])


def test_audit_fails_when_empty(monkeypatch):
    monkeypatch.setattr(robots_txt, "_fetch", lambda url: (200, "text/plain", "   "))
    result = robots_txt._audit("https://example.com", "/robots.txt", False, False)
    assert result["status"] == "fail"
    assert any("empty" in i for i in result["issues"])


def test_audit_fails_when_unreachable(monkeypatch):
    def boom(url):
        raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)

    monkeypatch.setattr(robots_txt, "_fetch", boom)
    result = robots_txt._audit("https://example.com", "/robots.txt", False, False)
    assert result["status"] == "fail"
    assert result["sitemap_count"] == 0
    assert any("not reachable" in i for i in result["issues"])


def test_audit_content_type_note(monkeypatch):
    monkeypatch.setattr(robots_txt, "_fetch", lambda url: (200, "text/html", VALID_BODY))
    result = robots_txt._audit("https://example.com", "/robots.txt", False, False)
    assert result["status"] == "pass"
    assert any("Content-Type" in n for n in result["notes"])


def test_a_body_without_llms_gets_no_llms_note(monkeypatch):
    body = "User-agent: *\nAllow: /\n\nSitemap: https://e.com/s.xml\n"
    monkeypatch.setattr(robots_txt, "_fetch", lambda url: (200, "text/plain", body))
    result = robots_txt._audit("https://example.com", "/robots.txt", False, allow_disallow_all=False)
    assert result["status"] == "pass"
    assert not any("Llms" in n for n in result["notes"])


def test_the_require_llms_knob_is_ignored_with_a_note(monkeypatch, write_config, capsys):
    """A retired knob never fails a run and never silently does nothing."""
    body = "User-agent: *\nAllow: /\n\nSitemap: https://e.com/s.xml\n"
    monkeypatch.setattr(robots_txt, "_fetch", lambda url: (200, "text/plain", body))
    write_config("reliability:\n  robots_txt:\n    require_llms: true\n")
    assert robots_txt.run(["--url", "https://example.com"]) == 0
    assert "require_llms is ignored" in capsys.readouterr().out


def test_audit_check_links_records_reachability(monkeypatch):
    monkeypatch.setattr(robots_txt, "_fetch", lambda url: (200, "text/plain", VALID_BODY))
    monkeypatch.setattr(robots_txt, "_head_ok", lambda url: (True, "HTTP 200"))
    result = robots_txt._audit("https://example.com", "/robots.txt", check_links=True, allow_disallow_all=False)
    assert result["status"] == "pass"
    assert len(result["link_checks"]) == 1


def test_audit_check_links_notes_dead_link(monkeypatch):
    monkeypatch.setattr(robots_txt, "_fetch", lambda url: (200, "text/plain", VALID_BODY))
    monkeypatch.setattr(robots_txt, "_head_ok", lambda url: (False, "HTTP 404"))
    result = robots_txt._audit("https://example.com", "/robots.txt", check_links=True, allow_disallow_all=False)
    # dead links are advisory, so it still passes
    assert result["status"] == "pass"
    assert any("not reachable" in n for n in result["notes"])


# ── report builders ──────────────────────────────────────────────


def test_build_markdown_report_pass():
    result = {
        "url": "https://example.com/robots.txt",
        "status": "pass",
        "issues": [],
        "notes": [],
        "sitemap_count": 1,
        "link_checks": [],
    }
    md = robots_txt._build_markdown_report(result)
    assert "✅ PASS" in md
    assert "https://example.com/robots.txt" in md


def test_build_markdown_report_fail():
    result = {
        "url": "https://example.com/robots.txt",
        "status": "fail",
        "issues": ["`User-agent: *` has a blanket `Disallow: /`, which de-indexes the entire site"],
        "notes": [],
        "sitemap_count": 0,
        "llms_count": 0,
        "link_checks": [],
    }
    md = robots_txt._build_markdown_report(result)
    assert "❌ FAIL" in md
    assert "de-indexes" in md


# ── end-to-end run() ─────────────────────────────────────────────


def test_run_returns_two_when_url_missing(monkeypatch, isolated_cwd, capsys):
    monkeypatch.delenv("ROBOTS_TXT_TEST_URL", raising=False)
    rc = robots_txt.run([])
    assert rc == 2
    assert "robots.txt target URL is required" in capsys.readouterr().out


def test_run_returns_zero_on_valid(monkeypatch, isolated_cwd, capsys):
    monkeypatch.setattr(robots_txt, "_fetch", lambda url: (200, "text/plain", VALID_BODY))
    rc = robots_txt.run(["--url", "https://example.com"])
    assert rc == 0
    assert "healthy" in capsys.readouterr().out
    assert robots_txt.REPORT_DIR.joinpath("robots-txt-report.md").exists()
    assert robots_txt.REPORT_DIR.joinpath("robots-txt-report.json").exists()


def test_run_returns_one_on_failure(monkeypatch, isolated_cwd, capsys):
    monkeypatch.setattr(robots_txt, "_fetch", lambda url: (200, "text/plain", DISALLOW_ALL_BODY))
    rc = robots_txt.run(["--url", "https://example.com"])
    assert rc == 1
    assert "Failures detected" in capsys.readouterr().out


def test_run_rejects_unsafe_url_scheme(monkeypatch, isolated_cwd, capsys):
    rc = robots_txt.run(["--url", "file:///etc/passwd"])
    assert rc == 2
    assert "refuses scheme 'file'" in capsys.readouterr().out
