"""Tests for the reliability:api-health check.

The behaviour worth pinning: the graceful skip when unconfigured (an
unconfigured check must not fail a PR), and the body assertions, which
are the reason this exists rather than pointing smoke at /health. A
`{"status": "degraded"}` body returned with HTTP 200 satisfies every
reachability probe and must fail here.
"""

from __future__ import annotations

import json
import urllib.error

import pytest

from slopstopper.checks import _http, api_health


HEALTHY = json.dumps({"status": "ok", "version": "1.2.3", "deps": {"db": "up"}})


def _opts(**overrides) -> dict:
    base = {
        "base_path": "",
        "path": "/health",
        "expect_status": 200,
        "require_json": True,
        "require_fields": [],
        "expect_fields": {},
        "max_response_ms": None,
    }
    base.update(overrides)
    return base


def _stub_fetch(monkeypatch, status=200, content_type="application/json", body=HEALTHY, ms=5.0):
    monkeypatch.setattr(api_health, "_fetch", lambda url: (status, content_type, body, ms))


# ── arg / config plumbing ────────────────────────────────────────


def test_parse_args_defaults():
    parsed = api_health._parse_args(None)
    assert parsed.url is None
    assert parsed.path is None
    assert parsed.expect_status is None
    assert parsed.allow_non_json is False
    assert parsed.require_field == []


def test_parse_args_explicit():
    parsed = api_health._parse_args(
        [
            "--url", "https://api.example.com",
            "--path", "/readyz",
            "--expect-status", "204",
            "--allow-non-json",
            "--require-field", "status",
            "--require-field", "deps.db",
            "--max-response-ms", "250",
        ]
    )
    assert parsed.url == "https://api.example.com"
    assert parsed.path == "/readyz"
    assert parsed.expect_status == 204
    assert parsed.allow_non_json is True
    assert parsed.require_field == ["status", "deps.db"]
    assert parsed.max_response_ms == 250


def test_options_come_from_config(write_config):
    write_config(
        "api:\n"
        "  base_path: /api/v1\n"
        "  health:\n"
        "    path: /healthz\n"
        "    expect_status: 204\n"
        "    require_fields: [status]\n"
        "    expect_fields:\n"
        "      status: ok\n"
    )
    opts = api_health._resolve_options(api_health._parse_args(None))
    assert opts["base_path"] == "/api/v1"
    assert opts["path"] == "/healthz"
    # The stdlib YAML subset yields strings; the check must coerce.
    assert opts["expect_status"] == 204
    assert opts["require_fields"] == ["status"]
    assert opts["expect_fields"] == {"status": "ok"}


def test_flag_beats_config(write_config):
    write_config("api:\n  health:\n    path: /from-config\n")
    opts = api_health._resolve_options(api_health._parse_args(["--path", "/from-flag"]))
    assert opts["path"] == "/from-flag"


def test_env_beats_config(write_config, monkeypatch):
    write_config("api:\n  health:\n    path: /from-config\n")
    monkeypatch.setenv("API_HEALTH_PATH", "/from-env")
    opts = api_health._resolve_options(api_health._parse_args(None))
    assert opts["path"] == "/from-env"


def test_malformed_expect_status_falls_back_to_default(write_config):
    write_config("api:\n  health:\n    path: /h\n    expect_status: not-a-number\n")
    opts = api_health._resolve_options(api_health._parse_args(None))
    assert opts["expect_status"] == api_health.DEFAULT_EXPECT_STATUS


# ── graceful skip ────────────────────────────────────────────────


def test_run_skips_with_exit_zero_when_unconfigured(write_config, capsys):
    """An unconfigured check is not a failing check. This is the same
    contract as hygiene:csp-exceptions with headers.source: null."""
    write_config("urls:\n  production: https://api.example.com\n")
    assert api_health.run(["https://api.example.com"]) == 0
    out = capsys.readouterr().out
    assert "No api.health.path configured" in out


def test_run_requires_a_url_once_configured(write_config, monkeypatch, capsys):
    write_config("api:\n  health:\n    path: /health\n")
    monkeypatch.delenv("API_HEALTH_TEST_URL", raising=False)
    assert api_health.run([]) == 2  # missing input, not a verdict
    assert "URL is required" in capsys.readouterr().out


# ── URL joining ──────────────────────────────────────────────────


@pytest.mark.parametrize(
    "url,base_path,path,expected",
    [
        ("https://api.example.com", "", "/health", "https://api.example.com/health"),
        ("https://api.example.com/", "", "/health", "https://api.example.com/health"),
        ("https://api.example.com", "/api/v1", "/health", "https://api.example.com/api/v1/health"),
        ("https://api.example.com", "/api/v1/", "/health", "https://api.example.com/api/v1/health"),
        ("https://api.example.com", "", "health", "https://api.example.com/health"),
    ],
)
def test_join(url, base_path, path, expected):
    assert api_health._join(url, base_path, path) == expected


# ── audit ────────────────────────────────────────────────────────


def test_audit_passes_on_a_healthy_endpoint(monkeypatch):
    _stub_fetch(monkeypatch)
    result = api_health._audit("https://api.example.com", _opts(require_fields=["status", "deps.db"]))
    assert result["status"] == "pass"
    assert result["issues"] == []


def test_audit_fails_on_unexpected_status(monkeypatch):
    _stub_fetch(monkeypatch, status=503, body=HEALTHY)
    result = api_health._audit("https://api.example.com", _opts())
    assert result["status"] == "fail"
    assert any("HTTP 503" in i for i in result["issues"])


def test_audit_fails_on_degraded_body_behind_http_200(monkeypatch):
    """The motivating case: a reachability probe passes, this must not."""
    _stub_fetch(monkeypatch, body=json.dumps({"status": "degraded"}))
    result = api_health._audit(
        "https://api.example.com", _opts(expect_fields={"status": "ok"})
    )
    assert result["status"] == "fail"
    assert any("`status` is `degraded`" in i for i in result["issues"])


def test_audit_fails_on_missing_required_field(monkeypatch):
    _stub_fetch(monkeypatch, body=json.dumps({"status": "ok"}))
    result = api_health._audit("https://api.example.com", _opts(require_fields=["version"]))
    assert result["status"] == "fail"
    assert any("`version` missing" in i for i in result["issues"])


def test_audit_resolves_dotted_fields(monkeypatch):
    _stub_fetch(monkeypatch)
    result = api_health._audit(
        "https://api.example.com", _opts(expect_fields={"deps.db": "up"})
    )
    assert result["status"] == "pass"


def test_audit_fails_on_non_json_content_type(monkeypatch):
    _stub_fetch(monkeypatch, content_type="text/html", body="<html>OK</html>")
    result = api_health._audit("https://api.example.com", _opts())
    assert result["status"] == "fail"
    assert any("content-type" in i for i in result["issues"])


def test_audit_accepts_vendor_json_content_types(monkeypatch):
    _stub_fetch(monkeypatch, content_type="application/health+json; charset=utf-8")
    result = api_health._audit("https://api.example.com", _opts())
    assert result["status"] == "pass"


def test_audit_allows_non_json_when_opted_out(monkeypatch):
    _stub_fetch(monkeypatch, content_type="text/plain", body="OK")
    result = api_health._audit("https://api.example.com", _opts(require_json=False))
    assert result["status"] == "pass"


def test_audit_fails_on_unparseable_json(monkeypatch):
    _stub_fetch(monkeypatch, body="{not json")
    result = api_health._audit("https://api.example.com", _opts())
    assert result["status"] == "fail"
    assert any("not valid JSON" in i for i in result["issues"])


def test_audit_fails_on_empty_body(monkeypatch):
    _stub_fetch(monkeypatch, body="   ")
    result = api_health._audit("https://api.example.com", _opts())
    assert result["status"] == "fail"
    assert any("empty" in i for i in result["issues"])


def test_audit_fails_when_unreachable(monkeypatch):
    def boom(url):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(api_health, "_fetch", boom)
    result = api_health._audit("https://api.example.com", _opts())
    assert result["status"] == "fail"
    assert result["http_status"] is None
    assert any("not reachable" in i for i in result["issues"])


def test_audit_enforces_a_response_budget_when_set(monkeypatch):
    _stub_fetch(monkeypatch, ms=900.0)
    result = api_health._audit("https://api.example.com", _opts(max_response_ms=250))
    assert result["status"] == "fail"
    assert any("over the 250ms budget" in i for i in result["issues"])


def test_audit_reports_timing_as_a_note_with_no_budget(monkeypatch):
    _stub_fetch(monkeypatch, ms=900.0)
    result = api_health._audit("https://api.example.com", _opts())
    assert result["status"] == "pass"
    assert any("no api.health.max_response_ms" in n for n in result["notes"])


def test_audit_notes_a_non_object_body_when_fields_unasserted(monkeypatch):
    _stub_fetch(monkeypatch, body=json.dumps(["ok"]))
    result = api_health._audit("https://api.example.com", _opts())
    assert result["status"] == "pass"
    assert any("not an object" in n for n in result["notes"])


# ── safety ───────────────────────────────────────────────────────


@pytest.mark.parametrize("url", ["file:///etc/passwd", "ftp://example.com/x", "gopher://x"])
def test_refuses_non_http_schemes(url):
    with pytest.raises(ValueError, match="refuses scheme"):
        _http.require_safe_url(url, api_health._LABEL)


@pytest.mark.parametrize("url", ["http://api.example.com", "https://api.example.com"])
def test_allows_http_and_https(url):
    _http.require_safe_url(url, api_health._LABEL)


# ── report ───────────────────────────────────────────────────────


def test_report_renders_the_skip_state():
    md = api_health._build_markdown_report({"status": "skipped"})
    assert "SKIPPED" in md
    assert "api.health.path" in md


def test_report_lists_issues_and_the_body_preview():
    md = api_health._build_markdown_report(
        {
            "url": "https://api.example.com/health",
            "status": "fail",
            "http_status": 503,
            "response_ms": 12.0,
            "issues": ["returned HTTP 503, expected 200"],
            "notes": [],
            "body_preview": '{"status": "down"}',
        }
    )
    assert "❌ FAIL" in md
    assert "returned HTTP 503" in md
    assert '{"status": "down"}' in md
    assert "How to Fix" in md


def test_meta_is_declared_for_emit():
    assert api_health.META["report_path"].endswith("api-health-report.md")
    assert api_health.META["comment_discriminator"]
