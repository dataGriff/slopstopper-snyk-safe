"""Tests for the reliability:sitemap completeness + drift check."""

from __future__ import annotations

import urllib.error

import pytest

from slopstopper.checks import _http, sitemap


BASE = "http://t"

HTML_INDEX = '<a href="/a.html">A</a> <a href="/b.html">B</a> <a href="https://ext.example/x">ext</a>'
HTML_A = '<a href="/">home</a> <a href="/b.html">B</a>'
HTML_B = '<a href="/a.html">A</a>'

URLSET = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
    "<url><loc>https://prod.example/</loc></url>"
    "<url><loc>https://prod.example/a.html</loc></url>"
    "<url><loc>https://prod.example/b.html</loc></url>"
    "</urlset>"
)

SITEMAP_INDEX = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
    "<sitemap><loc>https://prod.example/sitemap-0.xml</loc></sitemap>"
    "</sitemapindex>"
)

LLMS = "# Site\n> summary\n- [Home](https://prod.example/)\n- [A](/a.html)\n- [B](/b.html)\n"


class FakeNet:
    """Keyed fake for _fetch/_head_ok. Unknown URLs 404."""

    def __init__(self, pages: dict[str, tuple[int, str, str]], heads: dict[str, tuple[bool, str]] | None = None):
        self.pages = pages
        self.heads = heads or {}

    def fetch(self, url: str) -> tuple[int, str, str]:
        if url in self.pages:
            return self.pages[url]
        raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)

    def head_ok(self, url: str) -> tuple[bool, str]:
        return self.heads.get(url, (False, "HTTP 404"))

    def install(self, monkeypatch):
        monkeypatch.setattr(sitemap, "_fetch", self.fetch)
        monkeypatch.setattr(sitemap, "_head_ok", self.head_ok)
        return self


def _site(sitemap_body: str = URLSET, llms_body: str = LLMS, extra: dict | None = None) -> dict:
    pages = {
        "http://t/": (200, "text/html; charset=utf-8", HTML_INDEX),
        "http://t/a.html": (200, "text/html", HTML_A),
        "http://t/b.html": (200, "text/html", HTML_B),
        "http://t/sitemap.xml": (200, "application/xml", sitemap_body),
        "http://t/llms.txt": (200, "text/plain", llms_body),
    }
    if extra:
        pages.update(extra)
    return pages


def _audit(monkeypatch, pages, heads=None, **kw):
    FakeNet(pages, heads).install(monkeypatch)
    defaults = dict(
        sitemap_path="/sitemap.xml",
        llms_path="/llms.txt",
        max_pages=200,
        ignore_paths=[],
        allow_orphans=True,
        require_llms_complete=False,
    )
    defaults.update(kw)
    return sitemap._audit(BASE, **defaults)


# ── arg / config plumbing ────────────────────────────────────────


def test_parse_args_defaults():
    parsed = sitemap._parse_args(None)
    assert parsed.url is None
    assert parsed.path is None
    assert parsed.max_pages is None
    assert parsed.strict_orphans is False
    assert parsed.require_llms_complete is False


def test_parse_args_explicit():
    parsed = sitemap._parse_args(
        ["--url", "https://e.com", "--path", "/s.xml", "--llms-path", "/l.txt",
         "--max-pages", "5", "--ignore", "/api/*", "--strict-orphans", "--require-llms-complete"]
    )
    assert parsed.url == "https://e.com"
    assert parsed.path == "/s.xml"
    assert parsed.llms_path == "/l.txt"
    assert parsed.max_pages == 5
    assert parsed.ignore == ["/api/*"]
    assert parsed.strict_orphans is True
    assert parsed.require_llms_complete is True


def test_resolve_url_prefers_flag(monkeypatch):
    monkeypatch.setenv("SITEMAP_TEST_URL", "https://from-env")
    assert sitemap._resolve_url("https://from-flag") == "https://from-flag"


def test_resolve_url_falls_back_to_env(monkeypatch):
    monkeypatch.setenv("SITEMAP_TEST_URL", "https://from-env")
    assert sitemap._resolve_url(None) == "https://from-env"


def test_resolve_path_defaults(monkeypatch, isolated_cwd):
    monkeypatch.delenv("SITEMAP_PATH", raising=False)
    assert sitemap._resolve_path(None) == "/sitemap.xml"


def test_resolve_path_env_override(monkeypatch):
    monkeypatch.setenv("SITEMAP_PATH", "/custom.xml")
    assert sitemap._resolve_path(None) == "/custom.xml"


# ── safety ───────────────────────────────────────────────────────


def test_require_safe_url_accepts_http():
    _http.require_safe_url("http://example.com", sitemap._LABEL)
    _http.require_safe_url("https://example.com", sitemap._LABEL)


def test_require_safe_url_rejects_file_scheme():
    with pytest.raises(ValueError, match="refuses scheme 'file'"):
        _http.require_safe_url("file:///etc/passwd", sitemap._LABEL)


# ── path normalisation ───────────────────────────────────────────


def test_normalise_index_html_to_root():
    assert sitemap._normalise_path("/index.html") == "/"


def test_normalise_nested_index_html():
    assert sitemap._normalise_path("/blog/index.html") == "/blog"


def test_normalise_strips_trailing_slash_but_keeps_root():
    assert sitemap._normalise_path("/about/") == "/about"
    assert sitemap._normalise_path("/") == "/"
    assert sitemap._normalise_path("") == "/"


def test_ignored_matches_glob():
    assert sitemap._ignored("/api/users", ["/api/*"]) is True
    assert sitemap._ignored("/about", ["/api/*"]) is False


def test_rebase_puts_prod_loc_on_test_origin():
    assert sitemap._rebase("http://t", "https://prod.example/a.html") == "http://t/a.html"


# ── crawler ──────────────────────────────────────────────────────


def test_crawl_follows_internal_links_only(monkeypatch):
    FakeNet(_site()).install(monkeypatch)
    crawled, capped = sitemap._crawl(BASE, 200, [])
    assert crawled == {"/", "/a.html", "/b.html"}
    assert capped is False


def test_crawl_skips_non_html(monkeypatch):
    pages = _site()
    pages["http://t/data.json"] = (200, "application/json", "{}")
    pages["http://t/"] = (200, "text/html", HTML_INDEX + '<a href="/data.json">d</a>')
    FakeNet(pages).install(monkeypatch)
    crawled, _ = sitemap._crawl(BASE, 200, [])
    assert "/data.json" not in crawled


def test_crawl_respects_ignore_paths(monkeypatch):
    pages = _site()
    pages["http://t/"] = (200, "text/html", HTML_INDEX + '<a href="/api/secret.html">s</a>')
    pages["http://t/api/secret.html"] = (200, "text/html", "x")
    FakeNet(pages).install(monkeypatch)
    crawled, _ = sitemap._crawl(BASE, 200, ["/api/*"])
    assert "/api/secret.html" not in crawled


def test_crawl_caps_at_max_pages(monkeypatch):
    FakeNet(_site()).install(monkeypatch)
    crawled, capped = sitemap._crawl(BASE, 2, [])
    assert capped is True
    assert len(crawled) == 2


# ── sitemap collection ───────────────────────────────────────────


def test_collect_urlset(monkeypatch):
    FakeNet(_site()).install(monkeypatch)
    paths, err = sitemap._collect_sitemap(BASE, "http://t/sitemap.xml", set())
    assert err is None
    assert paths == {"/", "/a.html", "/b.html"}


def test_collect_sitemap_index_recurses(monkeypatch):
    pages = _site(sitemap_body=SITEMAP_INDEX)
    pages["http://t/sitemap-0.xml"] = (200, "application/xml", URLSET)
    FakeNet(pages).install(monkeypatch)
    paths, err = sitemap._collect_sitemap(BASE, "http://t/sitemap.xml", set())
    assert err is None
    assert paths == {"/", "/a.html", "/b.html"}


def test_collect_sitemap_invalid_xml(monkeypatch):
    pages = _site(sitemap_body="<not-xml")
    FakeNet(pages).install(monkeypatch)
    _paths, err = sitemap._collect_sitemap(BASE, "http://t/sitemap.xml", set())
    assert err is not None and "invalid sitemap XML" in err


def test_collect_sitemap_unreachable(monkeypatch):
    FakeNet(_site()).install(monkeypatch)
    _paths, err = sitemap._collect_sitemap(BASE, "http://t/missing.xml", set())
    assert err is not None and "not reachable" in err


# ── audit: flat vs nested pass equally ───────────────────────────


def test_audit_pass_flat_sitemap(monkeypatch):
    result = _audit(monkeypatch, _site())
    assert result["status"] == "pass"
    assert result["missing_pages"] == []
    assert result["dead_entries"] == []
    assert result["llms_gaps"] == []


def test_audit_pass_nested_sitemap_index(monkeypatch):
    pages = _site(sitemap_body=SITEMAP_INDEX)
    pages["http://t/sitemap-0.xml"] = (200, "application/xml", URLSET)
    result = _audit(monkeypatch, pages)
    assert result["status"] == "pass"
    assert result["missing_pages"] == []


# ── audit: hard-fails ────────────────────────────────────────────


def test_audit_missing_page_hard_fails(monkeypatch):
    # sitemap omits /b.html though it is reachable
    sm = URLSET.replace("<url><loc>https://prod.example/b.html</loc></url>", "")
    result = _audit(monkeypatch, _site(sitemap_body=sm))
    assert result["status"] == "fail"
    assert result["missing_pages"] == ["/b.html"]
    assert any("missing from sitemap" in i for i in result["issues"])


def test_audit_dead_entry_hard_fails(monkeypatch):
    # sitemap lists /gone.html which is not crawled and 404s on HEAD
    sm = URLSET.replace(
        "</urlset>", "<url><loc>https://prod.example/gone.html</loc></url></urlset>"
    )
    heads = {"http://t/gone.html": (False, "HTTP 404")}
    result = _audit(monkeypatch, _site(sitemap_body=sm), heads=heads)
    assert result["status"] == "fail"
    assert result["dead_entries"] == [{"path": "/gone.html", "detail": "HTTP 404"}]
    assert any("Stale sitemap entry" in i for i in result["issues"])


# ── audit: orphan advisory vs strict ─────────────────────────────


def test_audit_orphan_is_advisory_by_default(monkeypatch):
    # /extra.html in sitemap, reachable (HEAD 200) but not internally linked
    sm = URLSET.replace(
        "</urlset>", "<url><loc>https://prod.example/extra.html</loc></url></urlset>"
    )
    heads = {"http://t/extra.html": (True, "HTTP 200")}
    result = _audit(monkeypatch, _site(sitemap_body=sm), heads=heads)
    assert result["status"] == "pass"
    assert result["orphan_entries"] == ["/extra.html"]
    assert any("orphan" in n for n in result["notes"])


def test_audit_orphan_hard_fails_when_strict(monkeypatch):
    sm = URLSET.replace(
        "</urlset>", "<url><loc>https://prod.example/extra.html</loc></url></urlset>"
    )
    heads = {"http://t/extra.html": (True, "HTTP 200")}
    result = _audit(monkeypatch, _site(sitemap_body=sm), heads=heads, allow_orphans=False)
    assert result["status"] == "fail"
    assert any("orphan" in i for i in result["issues"])


# ── audit: llms cross-check ──────────────────────────────────────


def test_audit_llms_gap_is_advisory_by_default(monkeypatch):
    # llms.txt omits /b.html
    llms = "# Site\n- [Home](https://prod.example/)\n- [A](/a.html)\n"
    result = _audit(monkeypatch, _site(llms_body=llms))
    assert result["status"] == "pass"
    assert result["llms_gaps"] == ["/b.html"]
    assert any("not listed in llms.txt" in n for n in result["notes"])


def test_audit_llms_gap_hard_fails_when_required(monkeypatch):
    llms = "# Site\n- [Home](https://prod.example/)\n- [A](/a.html)\n"
    result = _audit(monkeypatch, _site(llms_body=llms), require_llms_complete=True)
    assert result["status"] == "fail"
    assert any("not listed in llms.txt" in i for i in result["issues"])


def test_audit_missing_llms_is_note_only(monkeypatch):
    pages = _site()
    del pages["http://t/llms.txt"]
    result = _audit(monkeypatch, pages)
    assert result["status"] == "pass"
    assert any("skipping llms cross-check" in n for n in result["notes"])


def test_audit_llms_absolute_urls_match_by_path(monkeypatch):
    # llms uses absolute prod URLs; must not be dropped as "cross-origin"
    result = _audit(monkeypatch, _site())
    assert result["llms_gaps"] == []


# ── audit: sitemap unreachable / cap ─────────────────────────────


def test_audit_unreachable_sitemap_hard_fails(monkeypatch):
    pages = _site()
    del pages["http://t/sitemap.xml"]
    result = _audit(monkeypatch, pages)
    assert result["status"] == "fail"
    assert any("not reachable" in i for i in result["issues"])


def test_audit_cap_adds_note(monkeypatch):
    result = _audit(monkeypatch, _site(), max_pages=1)
    assert any("capped at max_pages" in n for n in result["notes"])


# ── report builders ──────────────────────────────────────────────


def _pass_result() -> dict:
    return {
        "url": BASE,
        "sitemap_url": "http://t/sitemap.xml",
        "status": "pass",
        "issues": [],
        "notes": [],
        "crawled_count": 3,
        "sitemap_count": 3,
        "missing_pages": [],
        "dead_entries": [],
        "orphan_entries": [],
        "llms_gaps": [],
    }


def test_build_markdown_report_pass():
    md = sitemap._build_markdown_report(_pass_result())
    assert "✅ PASS" in md
    assert "generate the file, not hand-edit it" in md


def test_build_markdown_report_fail_lists_issues():
    result = _pass_result()
    result.update(status="fail", issues=["Reachable page missing from sitemap.xml: /b.html"])
    md = sitemap._build_markdown_report(result)
    assert "❌ FAIL" in md
    assert "/b.html" in md


# ── end-to-end run() ─────────────────────────────────────────────


def test_run_returns_two_when_url_missing(monkeypatch, isolated_cwd, capsys):
    monkeypatch.delenv("SITEMAP_TEST_URL", raising=False)
    rc = sitemap.run([])
    assert rc == 2
    assert "sitemap target URL is required" in capsys.readouterr().out


def test_run_returns_zero_on_valid(monkeypatch, isolated_cwd, capsys):
    FakeNet(_site(), {"http://t/llms.txt": (True, "HTTP 200")}).install(monkeypatch)
    rc = sitemap.run(["--url", BASE])
    assert rc == 0
    out = capsys.readouterr().out
    assert "complete and free of dead entries" in out
    assert sitemap.REPORT_DIR.joinpath("sitemap-report.md").exists()
    assert sitemap.REPORT_DIR.joinpath("sitemap-report.json").exists()


def test_run_returns_one_on_missing_page(monkeypatch, isolated_cwd, capsys):
    sm = URLSET.replace("<url><loc>https://prod.example/b.html</loc></url>", "")
    FakeNet(_site(sitemap_body=sm)).install(monkeypatch)
    rc = sitemap.run(["--url", BASE])
    assert rc == 1
    assert "Failures detected" in capsys.readouterr().out


def test_run_rejects_unsafe_url_scheme(monkeypatch, isolated_cwd, capsys):
    rc = sitemap.run(["--url", "file:///etc/passwd"])
    assert rc == 2
    assert "refuses scheme 'file'" in capsys.readouterr().out
