"""Tests for the headers adapter registry and built-in adapters."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from slopstopper import headers_adapters
from slopstopper.headers_adapters import cloudflare_adapter, json_adapter


# ── JSON adapter ──────────────────────────────────────────────────


def test_json_adapter_parses_basic_shape(tmp_path):
    payload = [
        {"for": "/foo", "values": {"Content-Security-Policy": "default-src 'self'"}},
        {"for": "/bar", "values": {"Cache-Control": "no-cache"}},
    ]
    path = tmp_path / "headers.json"
    path.write_text(json.dumps(payload))
    rules = json_adapter.parse(path)
    assert rules == [
        {"for": "/foo", "csp": "default-src 'self'"},
        {"for": "/bar", "csp": None},
    ]


def test_json_adapter_returns_empty_for_non_list(tmp_path):
    path = tmp_path / "headers.json"
    path.write_text(json.dumps({"oops": "object"}))
    assert json_adapter.parse(path) == []


def test_json_adapter_skips_malformed_entries(tmp_path):
    payload = [
        {"for": "/good", "values": {"Content-Security-Policy": "x"}},
        "string-entry",
        {"missing": "for"},
        {"for": 123, "values": {}},
        {"for": "/values-not-dict", "values": "x"},
    ]
    path = tmp_path / "headers.json"
    path.write_text(json.dumps(payload))
    rules = json_adapter.parse(path)
    assert rules == [{"for": "/good", "csp": "x"}]


# ── Cloudflare _headers adapter ────────────────────────────────────


def test_cloudflare_adapter_parses_two_blocks(tmp_path):
    path = tmp_path / "_headers"
    path.write_text(
        "/foo\n"
        "  Content-Security-Policy: default-src 'self'\n"
        "  Cache-Control: max-age=60\n"
        "\n"
        "/bar\n"
        "  Cache-Control: no-cache\n"
    )
    rules = cloudflare_adapter.parse(path)
    assert rules == [
        {"for": "/foo", "csp": "default-src 'self'"},
        {"for": "/bar", "csp": None},
    ]


def test_cloudflare_adapter_ignores_comments_and_blanks(tmp_path):
    path = tmp_path / "_headers"
    path.write_text(
        "# top comment\n"
        "\n"
        "/x\n"
        "  # indented comment lines are skipped\n"
        "  Content-Security-Policy: x\n"
    )
    assert cloudflare_adapter.parse(path) == [{"for": "/x", "csp": "x"}]


def test_cloudflare_adapter_handles_tab_indent(tmp_path):
    path = tmp_path / "_headers"
    path.write_text("/x\n\tContent-Security-Policy: tabbed\n")
    assert cloudflare_adapter.parse(path) == [{"for": "/x", "csp": "tabbed"}]


# ── Registry / dispatcher ─────────────────────────────────────────


def test_detect_format_json_for_json_extension(tmp_path):
    assert headers_adapters.detect_format(tmp_path / "x.json") == "json"


def test_detect_format_cloudflare_for_anything_else(tmp_path):
    assert headers_adapters.detect_format(tmp_path / "_headers") == "cloudflare-text"
    assert headers_adapters.detect_format(tmp_path / "x.toml") == "cloudflare-text"


def test_parse_auto_dispatches_by_extension(tmp_path):
    path = tmp_path / "h.json"
    path.write_text(json.dumps([{"for": "/", "values": {}}]))
    rules = headers_adapters.parse(path, "auto")
    assert rules == [{"for": "/", "csp": None}]


def test_parse_unknown_format_raises_key_error(tmp_path):
    with pytest.raises(KeyError):
        headers_adapters.parse(tmp_path / "x", "bogus-format")


# ── meta adapter: the CSP lives in each built page (#422) ────────

from slopstopper.headers_adapters import meta_adapter  # noqa: E402
from tests.helpers import meta_page  # noqa: E402

STRICT = "default-src 'self'; script-src 'self'"
GISCUS = "default-src 'self'; script-src 'self' https://giscus.app; frame-src https://giscus.app"


def test_meta_adapter_reports_the_shared_policy_as_the_baseline_and_each_relaxation(tmp_path):
    meta_page(tmp_path, "index.html", STRICT)
    meta_page(tmp_path, "about.html", STRICT)
    meta_page(tmp_path, "blog/index.html", STRICT)
    meta_page(tmp_path, "discuss/index.html", GISCUS)
    assert meta_adapter.parse(tmp_path) == [
        {"for": "/*", "csp": STRICT},
        {"for": "/discuss/", "csp": GISCUS},
    ]


def test_meta_adapter_reports_a_page_with_no_policy(tmp_path):
    meta_page(tmp_path, "index.html", STRICT)
    meta_page(tmp_path, "googleabc.html", None)
    assert {"for": "/googleabc.html", "csp": None, "missing": True} in meta_adapter.parse(tmp_path)


def test_meta_adapter_does_not_count_a_stricter_page_as_a_relaxation(tmp_path):
    meta_page(tmp_path, "index.html", GISCUS)
    meta_page(tmp_path, "a.html", GISCUS)
    meta_page(tmp_path, "strict.html", STRICT)
    assert meta_adapter.parse(tmp_path) == [{"for": "/*", "csp": GISCUS}]


def test_a_directory_source_is_detected_as_meta(tmp_path):
    assert headers_adapters.detect_format(tmp_path) == "meta"


def test_meta_adapter_reports_an_empty_policy_as_missing(tmp_path):
    """`content=""` enforces nothing, so it must read as no policy, not as a stricter one."""
    meta_page(tmp_path, "index.html", STRICT)
    meta_page(tmp_path, "blank.html", "")
    assert {"for": "/blank.html", "csp": None, "missing": True} in meta_adapter.parse(tmp_path)
