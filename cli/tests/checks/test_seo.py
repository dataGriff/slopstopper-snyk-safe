"""Tests for the reliability:seo check (in-CLI implementation, lifted
from .ss/scripts/check-seo-metatags.py)."""

from __future__ import annotations

import pytest

from slopstopper.checks import _http, seo


# ── arg / config plumbing ────────────────────────────────────────


def test_parse_args_defaults():
    parsed = seo._parse_args(None)
    assert parsed.url is None
    assert parsed.pages is None
    assert parsed.no_require_og_image is False
    assert parsed.no_verify_og_image is False
    assert parsed.og_image_base is None


def test_parse_args_explicit():
    parsed = seo._parse_args([
        "--url", "https://example.com",
        "--pages", "/,/blog",
        "--no-require-og-image",
        "--no-verify-og-image",
        "--og-image-base", "http://localhost:8080",
    ])
    assert parsed.url == "https://example.com"
    assert parsed.pages == "/,/blog"
    assert parsed.no_require_og_image is True
    assert parsed.no_verify_og_image is True
    assert parsed.og_image_base == "http://localhost:8080"


def test_resolve_url_prefers_flag(monkeypatch):
    monkeypatch.setenv("SEO_TEST_URL", "https://from-env")
    assert seo._resolve_url("https://from-flag") == "https://from-flag"


def test_resolve_url_falls_back_to_env(monkeypatch):
    monkeypatch.setenv("SEO_TEST_URL", "https://from-env")
    assert seo._resolve_url(None) == "https://from-env"


def test_resolve_url_none_when_neither_set(monkeypatch):
    monkeypatch.delenv("SEO_TEST_URL", raising=False)
    assert seo._resolve_url(None) is None


def test_discover_pages_defaults_to_root_when_unconfigured(isolated_cwd):
    assert seo._discover_pages() == "/"


def test_discover_pages_returns_joined_paths(monkeypatch, isolated_cwd):
    monkeypatch.setattr(seo.discovery, "discover", lambda check, event: ["/", "/blog"])
    assert seo._discover_pages() == "/,/blog"


def test_resolve_pages_prefers_flag(monkeypatch, isolated_cwd):
    monkeypatch.setenv("SEO_PAGES", "/should-not-use")
    monkeypatch.setattr(seo.discovery, "discover", lambda check, event: ["/from-discover"])
    assert seo._resolve_pages("/from-flag,/x") == ["/from-flag", "/x"]


def test_resolve_pages_falls_back_to_env(monkeypatch, isolated_cwd):
    monkeypatch.setenv("SEO_PAGES", "/from-env,/y")
    assert seo._resolve_pages(None) == ["/from-env", "/y"]


def test_resolve_pages_uses_discovery_when_no_flag_or_env(monkeypatch, isolated_cwd):
    monkeypatch.delenv("SEO_PAGES", raising=False)
    monkeypatch.setattr(seo.discovery, "discover", lambda check, event: ["/blog"])
    assert seo._resolve_pages(None) == ["/blog"]


def test_resolve_pages_defaults_to_root(monkeypatch, isolated_cwd):
    monkeypatch.delenv("SEO_PAGES", raising=False)
    monkeypatch.setattr(seo.discovery, "discover", lambda check, event: [])
    assert seo._resolve_pages(None) == ["/"]


# ── safety ───────────────────────────────────────────────────────


def test_require_safe_url_accepts_http():
    _http.require_safe_url("http://example.com", seo._LABEL)
    _http.require_safe_url("https://example.com", seo._LABEL)


def test_require_safe_url_rejects_file_scheme():
    with pytest.raises(ValueError, match="refuses scheme 'file'"):
        _http.require_safe_url("file:///etc/passwd", seo._LABEL)


def test_require_safe_url_rejects_ftp_scheme():
    with pytest.raises(ValueError, match="refuses scheme 'ftp'"):
        _http.require_safe_url("ftp://example.com", seo._LABEL)


# ── HTML parsing ─────────────────────────────────────────────────


def test_head_parser_captures_title_and_meta():
    parser = seo.HeadParser()
    parser.feed(
        '<html><head>'
        '<title>Hello</title>'
        '<meta name="description" content="A site">'
        '<meta property="og:title" content="Hello OG">'
        '<link rel="canonical" href="https://example.com/">'
        '</head><body>ignored</body></html>'
    )
    assert parser.title == "Hello"
    assert any(m.get("name") == "description" for m in parser.metas)
    assert any(link.get("rel") == "canonical" for link in parser.links)


def test_head_parser_ignores_body_content():
    parser = seo.HeadParser()
    parser.feed(
        '<html><head><title>X</title></head>'
        '<body><meta name="evil" content="injection"></body></html>'
    )
    assert not any(m.get("name") == "evil" for m in parser.metas)


def test_get_meta_returns_content():
    metas = [{"name": "description", "content": "ok"}]
    assert seo._get_meta(metas, "name", "description") == "ok"


def test_get_meta_returns_none_when_absent():
    assert seo._get_meta([], "name", "description") is None


def test_get_meta_is_case_insensitive():
    metas = [{"name": "Description", "content": "ok"}]
    assert seo._get_meta(metas, "name", "description") == "ok"


def test_get_link_returns_href():
    links = [{"rel": "canonical", "href": "https://example.com/"}]
    assert seo._get_link(links, "canonical") == "https://example.com/"


# ── validation ───────────────────────────────────────────────────


def _empty_tags() -> dict[str, str]:
    return {k: "" for k in [
        "title", "description", "canonical", "viewport",
        "og:type", "og:title", "og:description", "og:url", "og:image",
        "twitter:card", "twitter:title", "twitter:description", "twitter:image",
    ]}


def test_validate_core_tags_flags_missing():
    issues: list[str] = []
    notes: list[str] = []
    seo._validate_core_tags(_empty_tags(), issues, notes)
    assert "Missing <title>" in issues
    assert "Missing <meta name=\"description\">" in issues
    assert "Missing <meta name=\"viewport\">" in issues
    assert "Missing <link rel=\"canonical\">" in issues


def test_validate_core_tags_warns_long_title():
    tags = _empty_tags()
    tags["title"] = "x" * 100
    tags["description"] = "short"
    tags["viewport"] = "width=device-width"
    tags["canonical"] = "https://example.com/"
    issues: list[str] = []
    notes: list[str] = []
    seo._validate_core_tags(tags, issues, notes)
    assert issues == []
    assert any("title> is 100 chars" in n for n in notes)


def test_validate_open_graph_tags_requires_og_image_when_flagged():
    tags = _empty_tags()
    issues: list[str] = []
    seo._validate_open_graph_tags(tags, issues, require_og_image=True)
    assert "Missing og:image" in issues


def test_validate_open_graph_tags_skips_og_image_when_disabled():
    tags = _empty_tags()
    tags["og:title"] = "x"
    tags["og:description"] = "x"
    tags["og:type"] = "website"
    tags["og:url"] = "https://example.com/"
    issues: list[str] = []
    seo._validate_open_graph_tags(tags, issues, require_og_image=False)
    assert "Missing og:image" not in issues


def test_validate_twitter_tags_warns_unknown_card():
    tags = _empty_tags()
    tags["twitter:card"] = "weird"
    tags["twitter:title"] = "x"
    tags["twitter:description"] = "x"
    tags["twitter:image"] = "x"
    issues: list[str] = []
    notes: list[str] = []
    seo._validate_twitter_tags(tags, issues, notes, require_og_image=True)
    assert "Missing twitter:card" not in issues
    assert any("twitter:card is 'weird'" in n for n in notes)


# ── og:image origin rewriting ────────────────────────────────────


def test_rewrite_origin_preserves_path_query():
    out = seo._rewrite_origin(
        "https://prod.example.com/og.png?v=1",
        "http://localhost:8080",
    )
    assert out == "http://localhost:8080/og.png?v=1"


def test_maybe_rewrite_no_base_returns_original():
    url, rewritten = seo._maybe_rewrite_og_image("https://prod.example.com/og.png", None)
    assert url == "https://prod.example.com/og.png"
    assert rewritten is False


def test_maybe_rewrite_same_origin_returns_original():
    url, rewritten = seo._maybe_rewrite_og_image(
        "https://prod.example.com/og.png",
        "https://prod.example.com",
    )
    assert rewritten is False


def test_maybe_rewrite_different_origin_rewrites():
    url, rewritten = seo._maybe_rewrite_og_image(
        "https://prod.example.com/og.png",
        "http://localhost:8080",
    )
    assert url == "http://localhost:8080/og.png"
    assert rewritten is True


# ── check_page integration (mocked HTTP) ─────────────────────────


def _fake_fetch_ok(url):
    return (
        200,
        "text/html; charset=utf-8",
        '<html><head>'
        '<title>Hi</title>'
        '<meta name="description" content="A page">'
        '<meta name="viewport" content="width=device-width">'
        '<link rel="canonical" href="https://example.com/">'
        '<meta property="og:title" content="t">'
        '<meta property="og:description" content="d">'
        '<meta property="og:type" content="website">'
        '<meta property="og:url" content="https://example.com/">'
        '<meta property="og:image" content="https://example.com/og.png">'
        '<meta name="twitter:card" content="summary_large_image">'
        '<meta name="twitter:title" content="t">'
        '<meta name="twitter:description" content="d">'
        '<meta name="twitter:image" content="https://example.com/og.png">'
        '</head></html>',
    )


def test_check_page_returns_pass_when_all_tags_present(monkeypatch):
    monkeypatch.setattr(seo, "_fetch", _fake_fetch_ok)
    monkeypatch.setattr(seo, "_head_ok", lambda url: (True, "image/png"))
    result = seo._check_page(
        "https://example.com", "/", require_og_image=True, verify_og_image=True
    )
    assert result["status"] == "pass"
    assert result["issues"] == []


def test_check_page_returns_fail_on_missing_tags(monkeypatch):
    def fake_empty(url):
        return 200, "text/html", "<html><head></head></html>"

    monkeypatch.setattr(seo, "_fetch", fake_empty)
    monkeypatch.setattr(seo, "_head_ok", lambda url: (True, "image/png"))
    result = seo._check_page(
        "https://example.com", "/", require_og_image=True, verify_og_image=False
    )
    assert result["status"] == "fail"
    assert any("Missing <title>" in i for i in result["issues"])


def test_check_page_returns_error_on_fetch_failure(monkeypatch):
    import urllib.error

    def boom(url):
        raise urllib.error.URLError("nope")

    monkeypatch.setattr(seo, "_fetch", boom)
    result = seo._check_page(
        "https://example.com", "/", require_og_image=True, verify_og_image=False
    )
    assert result["status"] == "error"
    assert any("Failed to fetch" in i for i in result["issues"])


# ── report builders ──────────────────────────────────────────────


def test_build_markdown_report_clean():
    result = {
        "url": "https://example.com/",
        "status": "pass",
        "issues": [],
        "notes": [],
        "tags": _empty_tags(),
        "image_check": None,
    }
    md = seo._build_markdown_report([result], "https://example.com", overall_pass=True)
    assert "✅ PASS" in md
    assert "https://example.com/" in md


def test_build_markdown_report_with_failures():
    result = {
        "url": "https://example.com/",
        "status": "fail",
        "issues": ["Missing <title>"],
        "notes": [],
        "tags": _empty_tags(),
        "image_check": None,
    }
    md = seo._build_markdown_report([result], "https://example.com", overall_pass=False)
    assert "❌ FAIL" in md
    assert "Missing <title>" in md


# ── end-to-end run() ─────────────────────────────────────────────


def test_run_returns_two_when_url_missing(monkeypatch, isolated_cwd, capsys):
    monkeypatch.delenv("SEO_TEST_URL", raising=False)
    rc = seo.run([])
    assert rc == 2
    assert "SEO target URL is required" in capsys.readouterr().out


def test_run_returns_zero_when_all_pages_pass(monkeypatch, isolated_cwd, capsys):
    monkeypatch.setattr(seo, "_fetch", _fake_fetch_ok)
    monkeypatch.setattr(seo, "_head_ok", lambda url: (True, "image/png"))
    monkeypatch.setattr(seo, "_discover_pages", lambda: None)
    rc = seo.run(["--url", "https://example.com"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "All pages pass" in out
    assert seo.REPORT_DIR.joinpath("seo-metatags-report.md").exists()
    assert seo.REPORT_DIR.joinpath("seo-metatags-report.json").exists()


def test_run_returns_one_when_pages_fail(monkeypatch, isolated_cwd, capsys):
    def fake_empty(url):
        return 200, "text/html", "<html><head></head></html>"

    monkeypatch.setattr(seo, "_fetch", fake_empty)
    monkeypatch.setattr(seo, "_head_ok", lambda url: (True, "image/png"))
    monkeypatch.setattr(seo, "_discover_pages", lambda: None)
    rc = seo.run(["--url", "https://example.com"])
    assert rc == 1
    out = capsys.readouterr().out
    assert "Failures detected" in out


def test_run_rejects_unsafe_url_scheme(monkeypatch, isolated_cwd, capsys):
    monkeypatch.setattr(seo, "_discover_pages", lambda: None)
    rc = seo.run(["--url", "file:///etc/passwd"])
    assert rc == 2
    assert "refuses scheme 'file'" in capsys.readouterr().out


def test_run_threads_og_image_base(monkeypatch, isolated_cwd):
    captured: dict = {}

    def fake_head(url):
        captured.setdefault("urls", []).append(url)
        return True, "image/png"

    monkeypatch.setattr(seo, "_fetch", _fake_fetch_ok)
    monkeypatch.setattr(seo, "_head_ok", fake_head)
    monkeypatch.setattr(seo, "_discover_pages", lambda: None)
    rc = seo.run([
        "--url", "https://example.com",
        "--og-image-base", "http://localhost:8080",
    ])
    assert rc == 0
    # og:image was https://example.com/og.png; should have been rewritten
    assert any("localhost:8080" in u for u in captured["urls"])


@pytest.mark.parametrize("og_image", ["data:image/png;base64,AA", "ftp://cdn.example/og.png"])
def test_a_non_http_og_image_is_a_finding_not_a_crash(og_image):
    """The scheme guard raises ValueError; for an og:image that is "not
    reachable" and is reported on the page. It is not a reason the check can't run."""
    ok, detail = seo._head_ok(og_image)
    assert ok is False
    assert "refuses scheme" in detail
