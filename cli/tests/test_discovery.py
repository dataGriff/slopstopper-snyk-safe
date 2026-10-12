"""Tests for the in-CLI discovery module (lifted from
.ss/scripts/discover-pages.py)."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

from slopstopper import discovery


SITEMAP_INDEX = """<?xml version="1.0" encoding="UTF-8"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap><loc>https://example.com/sitemap-0.xml</loc></sitemap>
</sitemapindex>
"""

SITEMAP_URLSET = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://example.com/</loc></url>
  <url><loc>https://example.com/blog/</loc></url>
  <url><loc>https://example.com/about/</loc></url>
</urlset>
"""


# ── env-var override ──────────────────────────────────────────────


def test_env_var_override_takes_precedence(isolated_cwd, monkeypatch, write_config):
    write_config("pages:\n  smoke: /from-config\n")
    monkeypatch.setenv("SMOKE_PAGES", "/from-env,/x")
    assert discovery.discover("smoke", "local") == ["/from-env", "/x"]


def test_env_var_empty_value_falls_through(isolated_cwd, monkeypatch, write_config):
    write_config("pages:\n  smoke: /from-config\n")
    monkeypatch.setenv("SMOKE_PAGES", "  ,  ")
    assert discovery.discover("smoke", "local") == ["/from-config"]


# ── pages.<check> ─────────────────────────────────────────────────


def test_pages_list_comma_separated_string(isolated_cwd, write_config):
    write_config("pages:\n  accessibility: /,/about\n")
    assert discovery.discover("accessibility", "local") == ["/", "/about"]


def test_pages_list_empty_falls_through_to_root(isolated_cwd, write_config):
    write_config("pages: {}\n")
    assert discovery.discover("accessibility", "local") == ["/"]


def test_no_config_defaults_to_root(isolated_cwd):
    assert discovery.discover("seo", "local") == ["/"]


# ── coverage modes ────────────────────────────────────────────────


def test_coverage_explicit_list_used_for_pr(isolated_cwd, write_config):
    write_config(
        "reliability:\n  coverage:\n    pr: /,/changed\n"
        "pages:\n  smoke: /not-used\n"
    )
    assert discovery.discover("smoke", "pr") == ["/", "/changed"]


def test_local_event_skips_coverage_modes(isolated_cwd, write_config):
    write_config(
        "reliability:\n  coverage:\n    pr: /coverage-only\n"
        "pages:\n  smoke: /from-pages\n"
    )
    # event=local must NOT consult reliability.coverage.*, only pages.*
    assert discovery.discover("smoke", "local") == ["/from-pages"]


def test_changed_mode_outside_pr_skipped(isolated_cwd, write_config):
    write_config(
        "reliability:\n  coverage:\n    main: changed\n"
        "pages:\n  smoke: /fallback\n"
    )
    assert discovery.discover("smoke", "main") == ["/fallback"]


# ── sitemap parsing ──────────────────────────────────────────────


def test_sitemap_mode_reads_urlset(isolated_cwd, write_config):
    sitemap = Path("dist/sitemap.xml")
    sitemap.parent.mkdir(parents=True, exist_ok=True)
    sitemap.write_text(SITEMAP_URLSET)
    write_config("reliability:\n  coverage:\n    main: sitemap\n")
    paths = discovery.discover("smoke", "main")
    assert "/" in paths
    assert "/blog/" in paths
    assert "/about/" in paths


def test_sitemap_index_recurses_into_children(isolated_cwd, write_config):
    Path("dist").mkdir(parents=True, exist_ok=True)
    Path("dist/sitemap-index.xml").write_text(SITEMAP_INDEX)
    Path("dist/sitemap-0.xml").write_text(SITEMAP_URLSET)
    write_config("reliability:\n  coverage:\n    main: sitemap\n")
    paths = discovery.discover("smoke", "main")
    assert "/blog/" in paths


def test_sitemap_missing_returns_empty(isolated_cwd, write_config):
    write_config("reliability:\n  coverage:\n    main: sitemap\n")
    # No sitemap files anywhere; coverage produces []. Falls through to
    # pages.<check>, then to "/".
    assert discovery.discover("smoke", "main") == ["/"]


def test_sitemap_strips_to_path_component():
    root = ET.fromstring(SITEMAP_URLSET)
    paths = discovery._collect_from_urlset(root)
    # Each <loc> URL is reduced to its path component
    assert paths == ["/", "/blog/", "/about/"]


# ── changed-pages mode ───────────────────────────────────────────


def test_map_file_to_urls_with_astro_blog_md():
    # Astro rules intentionally double-match blog content (specific + generic
    # collection rule), and upstream _dedupe collapses these. We just confirm
    # the URL is produced.
    urls = discovery._map_file_to_urls("src/content/blog/hello.md", discovery.ASTRO_RULES)
    assert "/blog/hello/" in urls


def test_map_file_to_urls_with_astro_page():
    # `index.astro` double-matches the specific and generic page rules in
    # the original bash flow; just confirm the homepage URL is produced.
    urls = discovery._map_file_to_urls("src/pages/index.astro", discovery.ASTRO_RULES)
    assert "/" in urls


def test_map_file_to_urls_with_next_pages_router():
    urls = discovery._map_file_to_urls("pages/about.tsx", discovery.NEXT_PAGES_RULES)
    assert urls == ["/about/"]


def test_map_file_to_urls_with_next_app_router():
    urls = discovery._map_file_to_urls("app/dashboard/page.tsx", discovery.NEXT_APP_RULES)
    assert urls == ["/dashboard/"]


def test_map_file_to_urls_unmapped_returns_empty():
    urls = discovery._map_file_to_urls("README.md", discovery.ASTRO_RULES)
    assert urls == []


def test_detect_framework_default_to_astro(isolated_cwd):
    # With no framework config files it defaults to the Astro rules.
    assert discovery._detect_framework_rules() == discovery.ASTRO_RULES


def test_detect_framework_next(isolated_cwd):
    Path("next.config.mjs").write_text("export default {}")
    assert discovery._detect_framework_rules() == (
        discovery.NEXT_PAGES_RULES + discovery.NEXT_APP_RULES
    )


def test_detect_framework_sveltekit(isolated_cwd):
    Path("svelte.config.js").write_text("export default {}")
    assert discovery._detect_framework_rules() == discovery.SVELTEKIT_RULES


def test_cross_cutting_change_glob_match(isolated_cwd, write_config):
    write_config(
        "reliability:\n"
        "  coverage:\n"
        "    cross_cutting_paths:\n"
        "      - 'src/layouts/*.astro'\n"
        "      - 'package.json'\n"
    )
    trigger = discovery._any_cross_cutting_change(["src/layouts/Base.astro"])
    assert trigger == "src/layouts/Base.astro"


def test_cross_cutting_no_match(isolated_cwd, write_config):
    write_config(
        "reliability:\n  coverage:\n    cross_cutting_paths: ['package.json']\n"
    )
    assert discovery._any_cross_cutting_change(["docs/CHANGELOG.md"]) is None


def test_expand_url_template_substitutes_named_groups():
    import re
    match = re.match(r"^(?P<slug>[^/]+)$", "hello")
    assert discovery._expand_url_template("/blog/$slug/", match) == "/blog/hello/"


def test_dedupe_preserves_order():
    assert discovery._dedupe(["/", "/a", "/", "/b", "/a"]) == ["/", "/a", "/b"]


# ── pages_csv: what the specs' <CHECK>_PAGES env var is built from ───


def test_pages_csv_joins_discovered_paths(monkeypatch, isolated_cwd):
    monkeypatch.setattr(discovery, "discover", lambda check, event: ["/", "/blog"])
    assert discovery.pages_csv("smoke") == "/,/blog"


def test_pages_csv_is_none_when_nothing_is_found_or_discovery_raises(monkeypatch, isolated_cwd):
    monkeypatch.setattr(discovery, "discover", lambda check, event: [])
    assert discovery.pages_csv("smoke") is None

    def boom(check, event):
        raise RuntimeError("boom")

    monkeypatch.setattr(discovery, "discover", boom)
    assert discovery.pages_csv("smoke") is None


def test_pages_csv_defaults_to_root_when_unconfigured(isolated_cwd):
    assert discovery.pages_csv("smoke") == "/"

