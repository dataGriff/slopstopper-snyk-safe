"""Tests for the security:api-headers check.

The assertions that matter are the CORS ones, and specifically which
findings are hard failures versus advisory notes. A check that fails on
advice becomes noise, and one that only advises on a credentialed
wildcard isn't doing its job.
"""

from __future__ import annotations

import urllib.error

import pytest

from slopstopper.checks import _http, api_headers


def _opts(**overrides) -> dict:
    base = {
        "base_path": "",
        "paths": ["/health"],
        "require_hsts": True,
        "require_nosniff": True,
        "allow_wildcard_cors": False,
        "allowed_origins": [],
    }
    base.update(overrides)
    return base


CLEAN_HTTPS = {
    "Content-Type": "application/json",
    "X-Content-Type-Options": "nosniff",
    "Strict-Transport-Security": "max-age=31536000",
}


def _stub(monkeypatch, by_origin: dict, status: int = 200):
    """Stub _fetch_headers with a {origin_or_None: headers} mapping."""

    def fake(url, origin):
        return status, by_origin.get(origin, by_origin.get(None, {}))

    monkeypatch.setattr(api_headers, "_fetch_headers", fake)


# ── arg / config plumbing ────────────────────────────────────────


def test_parse_args_defaults():
    parsed = api_headers._parse_args(None)
    assert parsed.path == []
    assert parsed.allowed_origin == []
    assert parsed.allow_wildcard_cors is False
    assert parsed.no_require_hsts is False


def test_parse_args_repeatable_paths_and_origins():
    parsed = api_headers._parse_args(
        [
            "--path", "/health",
            "--path", "/v1/items",
            "--allowed-origin", "https://app.example.com",
            "--allow-wildcard-cors",
            "--no-require-hsts",
        ]
    )
    assert parsed.path == ["/health", "/v1/items"]
    assert parsed.allowed_origin == ["https://app.example.com"]
    assert parsed.allow_wildcard_cors is True
    assert parsed.no_require_hsts is True


def test_options_come_from_config(write_config):
    write_config(
        "api:\n"
        "  base_path: /api\n"
        "  headers:\n"
        "    paths: [/health, /v1/items]\n"
        "    require_hsts: false\n"
        "    allow_wildcard_cors: true\n"
        "    allowed_origins: [https://app.example.com]\n"
    )
    opts = api_headers._resolve_options(api_headers._parse_args(None))
    assert opts["base_path"] == "/api"
    assert opts["paths"] == ["/health", "/v1/items"]
    assert opts["require_hsts"] is False
    assert opts["allow_wildcard_cors"] is True
    assert opts["allowed_origins"] == ["https://app.example.com"]


def test_env_paths_are_comma_separated(write_config, monkeypatch):
    write_config("api:\n  headers:\n    paths: [/from-config]\n")
    monkeypatch.setenv("API_HEADERS_PATHS", "/a, /b ,/c")
    opts = api_headers._resolve_options(api_headers._parse_args(None))
    assert opts["paths"] == ["/a", "/b", "/c"]


# ── graceful skip ────────────────────────────────────────────────


def test_run_skips_with_exit_zero_when_no_paths_configured(write_config, capsys):
    write_config("urls:\n  production: https://api.example.com\n")
    assert api_headers.run(["https://api.example.com"]) == 0
    assert "No api.headers.paths configured" in capsys.readouterr().out


def test_run_requires_a_url_once_configured(write_config, monkeypatch, capsys):
    write_config("api:\n  headers:\n    paths: [/health]\n")
    monkeypatch.delenv("API_HEADERS_TEST_URL", raising=False)
    assert api_headers.run([]) == 2  # missing input, not a verdict
    assert "URL is required" in capsys.readouterr().out


# ── header lookup ────────────────────────────────────────────────


def test_header_lookup_is_case_insensitive():
    headers = {"access-control-allow-origin": "*"}
    assert api_headers._header(headers, "Access-Control-Allow-Origin") == "*"
    assert api_headers._header(headers, "missing") == ""


# ── CORS ─────────────────────────────────────────────────────────


def test_wildcard_with_credentials_always_fails(monkeypatch):
    """Browsers reject the pair, so the intended policy was never enforced.
    Hard failure even when allow_wildcard_cors is set."""
    _stub(monkeypatch, {None: {
        **CLEAN_HTTPS,
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Credentials": "true",
    }})
    result = api_headers._audit_path(
        "https://api.example.com", "", "/health", _opts(allow_wildcard_cors=True)
    )
    assert result["status"] == "fail"
    assert any("Browsers reject this pair" in i for i in result["issues"])


def test_bare_wildcard_fails_by_default(monkeypatch):
    _stub(monkeypatch, {None: {**CLEAN_HTTPS, "Access-Control-Allow-Origin": "*"}})
    result = api_headers._audit_path("https://api.example.com", "", "/health", _opts())
    assert result["status"] == "fail"
    assert any("lets any site read" in i for i in result["issues"])


def test_bare_wildcard_is_permitted_when_opted_in(monkeypatch):
    _stub(monkeypatch, {None: {**CLEAN_HTTPS, "Access-Control-Allow-Origin": "*"}})
    result = api_headers._audit_path(
        "https://api.example.com", "", "/health", _opts(allow_wildcard_cors=True)
    )
    assert result["status"] == "pass"
    assert any("allowed by api.headers.allow_wildcard_cors" in n for n in result["notes"])


def test_reflected_origin_with_credentials_fails(monkeypatch):
    _stub(monkeypatch, {
        None: CLEAN_HTTPS,
        api_headers.PROBE_ORIGIN: {
            **CLEAN_HTTPS,
            "Access-Control-Allow-Origin": api_headers.PROBE_ORIGIN,
            "Access-Control-Allow-Credentials": "true",
            "Vary": "Origin",
        },
    })
    result = api_headers._audit_path("https://api.example.com", "", "/health", _opts())
    assert result["status"] == "fail"
    assert any("reflects any Origin" in i for i in result["issues"])


def test_reflected_origin_without_credentials_is_advisory(monkeypatch):
    _stub(monkeypatch, {
        None: CLEAN_HTTPS,
        api_headers.PROBE_ORIGIN: {
            **CLEAN_HTTPS,
            "Access-Control-Allow-Origin": api_headers.PROBE_ORIGIN,
            "Vary": "Origin",
        },
    })
    result = api_headers._audit_path("https://api.example.com", "", "/health", _opts())
    assert result["status"] == "pass"
    assert any("reflects any Origin" in n for n in result["notes"])


def test_missing_vary_origin_is_advisory_when_origin_is_echoed(monkeypatch):
    _stub(monkeypatch, {
        None: CLEAN_HTTPS,
        api_headers.PROBE_ORIGIN: {
            **CLEAN_HTTPS,
            "Access-Control-Allow-Origin": api_headers.PROBE_ORIGIN,
        },
    })
    result = api_headers._audit_path("https://api.example.com", "", "/health", _opts())
    assert any("`Vary: Origin` is missing" in n for n in result["notes"])


def test_configured_origin_that_is_no_longer_allowed_fails(monkeypatch):
    _stub(monkeypatch, {None: CLEAN_HTTPS})
    result = api_headers._audit_path(
        "https://api.example.com",
        "",
        "/health",
        _opts(allowed_origins=["https://app.example.com"]),
    )
    assert result["status"] == "fail"
    assert any("allowed_origins" in i for i in result["issues"])
    assert result["origin_checks"][0]["allowed"] is False


def test_configured_origin_that_is_allowed_passes(monkeypatch):
    origin = "https://app.example.com"
    _stub(monkeypatch, {
        None: CLEAN_HTTPS,
        origin: {**CLEAN_HTTPS, "Access-Control-Allow-Origin": origin},
    })
    result = api_headers._audit_path(
        "https://api.example.com", "", "/health", _opts(allowed_origins=[origin])
    )
    assert result["status"] == "pass"
    assert result["origin_checks"][0]["allowed"] is True


# ── transport + content ──────────────────────────────────────────


def test_missing_hsts_fails_on_https(monkeypatch):
    headers = {k: v for k, v in CLEAN_HTTPS.items() if k != "Strict-Transport-Security"}
    _stub(monkeypatch, {None: headers})
    result = api_headers._audit_path("https://api.example.com", "", "/health", _opts())
    assert result["status"] == "fail"
    assert any("Strict-Transport-Security" in i for i in result["issues"])


def test_hsts_is_not_asserted_on_http(monkeypatch):
    """HSTS has no effect over http, so demanding it there is noise."""
    headers = {k: v for k, v in CLEAN_HTTPS.items() if k != "Strict-Transport-Security"}
    _stub(monkeypatch, {None: headers})
    result = api_headers._audit_path("http://localhost:8080", "", "/health", _opts())
    assert result["status"] == "pass"
    assert any("no effect" in n for n in result["notes"])


def test_missing_nosniff_fails(monkeypatch):
    headers = {k: v for k, v in CLEAN_HTTPS.items() if k != "X-Content-Type-Options"}
    _stub(monkeypatch, {None: headers})
    result = api_headers._audit_path("https://api.example.com", "", "/health", _opts())
    assert result["status"] == "fail"
    assert any("nosniff" in i for i in result["issues"])


def test_nosniff_can_be_turned_off(monkeypatch):
    headers = {k: v for k, v in CLEAN_HTTPS.items() if k != "X-Content-Type-Options"}
    _stub(monkeypatch, {None: headers})
    result = api_headers._audit_path(
        "https://api.example.com", "", "/health", _opts(require_nosniff=False)
    )
    assert result["status"] == "pass"


def test_fingerprint_headers_are_advisory(monkeypatch):
    _stub(monkeypatch, {None: {**CLEAN_HTTPS, "X-Powered-By": "Express 4.18.2"}})
    result = api_headers._audit_path("https://api.example.com", "", "/health", _opts())
    assert result["status"] == "pass"
    assert any("X-Powered-By" in n for n in result["notes"])


def test_versioned_server_header_is_advisory(monkeypatch):
    _stub(monkeypatch, {None: {**CLEAN_HTTPS, "Server": "nginx/1.25.3"}})
    result = api_headers._audit_path("https://api.example.com", "", "/health", _opts())
    assert result["status"] == "pass"
    assert any("includes a version" in n for n in result["notes"])


def test_unversioned_server_header_is_not_flagged(monkeypatch):
    _stub(monkeypatch, {None: {**CLEAN_HTTPS, "Server": "cloudflare"}})
    result = api_headers._audit_path("https://api.example.com", "", "/health", _opts())
    assert not any("Server" in n for n in result["notes"])


def test_headers_are_audited_even_on_a_4xx(monkeypatch):
    """A 401 still has a CORS policy worth auditing."""
    _stub(monkeypatch, {None: {**CLEAN_HTTPS, "Access-Control-Allow-Origin": "*"}}, status=401)
    result = api_headers._audit_path("https://api.example.com", "", "/health", _opts())
    assert result["http_status"] == 401
    assert result["status"] == "fail"


def test_unreachable_endpoint_fails(monkeypatch):
    def boom(url, origin):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(api_headers, "_fetch_headers", boom)
    result = api_headers._audit_path("https://api.example.com", "", "/health", _opts())
    assert result["status"] == "fail"
    assert any("not reachable" in i for i in result["issues"])


# ── multi-path audit ─────────────────────────────────────────────


def test_audit_fails_overall_when_any_path_fails(monkeypatch):
    def fake(url, origin):
        if url.endswith("/leaky"):
            return 200, {**CLEAN_HTTPS, "Access-Control-Allow-Origin": "*"}
        return 200, CLEAN_HTTPS

    monkeypatch.setattr(api_headers, "_fetch_headers", fake)
    result = api_headers._audit(
        "https://api.example.com", _opts(paths=["/health", "/leaky"])
    )
    assert result["status"] == "fail"
    assert [p["status"] for p in result["paths"]] == ["pass", "fail"]


def test_base_path_is_applied_to_every_probe(monkeypatch):
    seen = []

    def fake(url, origin):
        seen.append(url)
        return 200, CLEAN_HTTPS

    monkeypatch.setattr(api_headers, "_fetch_headers", fake)
    api_headers._audit("https://api.example.com", _opts(base_path="/api/v1"))
    assert all(u.startswith("https://api.example.com/api/v1/health") for u in seen)


# ── safety ───────────────────────────────────────────────────────


@pytest.mark.parametrize("url", ["file:///etc/passwd", "ftp://example.com/x"])
def test_refuses_non_http_schemes(url):
    with pytest.raises(ValueError, match="refuses scheme"):
        _http.require_safe_url(url, api_headers._LABEL)


def test_probe_origin_uses_a_reserved_tld():
    """RFC 2606 reserves .invalid, so the probe can never hit a real host."""
    assert api_headers.PROBE_ORIGIN.endswith(".invalid")


# ── report ───────────────────────────────────────────────────────


def test_report_renders_the_skip_state():
    md = api_headers._build_markdown_report({"status": "skipped"})
    assert "SKIPPED" in md
    assert "api.headers.paths" in md


def test_report_lists_per_path_findings():
    md = api_headers._build_markdown_report(
        {
            "url": "https://api.example.com",
            "status": "fail",
            "probe_origin": api_headers.PROBE_ORIGIN,
            "paths": [
                {
                    "url": "https://api.example.com/health",
                    "status": "fail",
                    "http_status": 200,
                    "issues": ["`Access-Control-Allow-Origin: *` lets any site read this response."],
                    "notes": [],
                    "origin_checks": [
                        {"origin": "https://app.example.com", "allowed": False, "acao": ""}
                    ],
                }
            ],
        }
    )
    assert "❌ FAIL" in md
    assert "/health" in md
    assert "How to Fix" in md
    assert "Access-Control-Allow-Origin" in md


def test_meta_is_declared_for_emit():
    assert api_headers.META["report_path"].endswith("api-headers-report.md")
    assert api_headers.META["comment_discriminator"]
