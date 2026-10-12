"""Tests for the hygiene:openapi drift check.

The behaviour worth pinning: a YAML spec skips rather than failing (the
CLI has no YAML parser and saying so is more useful than going red), the
committed-vs-served comparison is on operation sets rather than document
equality, and parameterised paths are never probed, because a 404 from
`/users/{id}` could mean a missing route or merely a missing record, and
reporting both as failures would train people to ignore the check.
"""

from __future__ import annotations

import json

import pytest

from slopstopper.checks import _http, openapi


def _spec(paths: dict) -> dict:
    return {"openapi": "3.0.0", "info": {"title": "demo", "version": "1"}, "paths": paths}


SPEC = _spec(
    {
        "/health": {"get": {"responses": {"200": {"description": "ok"}}}},
        "/v1/items": {"get": {}, "post": {}},
        "/users/{id}": {"get": {}},
    }
)


def _opts(**overrides) -> dict:
    base = {"spec": "openapi.json", "served_spec": "", "probe_paths": True, "ignore_paths": []}
    base.update(overrides)
    return base


# ── arg / config plumbing ────────────────────────────────────────


def test_parse_args_defaults():
    parsed = openapi._parse_args(None)
    assert parsed.url is None
    assert parsed.spec is None
    assert parsed.served_spec is None
    assert parsed.no_probe is False
    assert parsed.ignore == []


def test_parse_args_explicit():
    parsed = openapi._parse_args(
        [
            "--url", "https://api.example.com",
            "--spec", "docs/openapi.json",
            "--served-spec", "https://api.example.com/openapi.json",
            "--no-probe",
            "--ignore", "/internal/*",
        ]
    )
    assert parsed.spec == "docs/openapi.json"
    assert parsed.served_spec == "https://api.example.com/openapi.json"
    assert parsed.no_probe is True
    assert parsed.ignore == ["/internal/*"]


def test_options_come_from_config(write_config):
    write_config(
        "api:\n"
        "  openapi:\n"
        "    spec: openapi.json\n"
        "    served_spec: https://api.example.com/openapi.json\n"
        "    ignore_paths: [/internal/*]\n"
    )
    opts = openapi._resolve_options(openapi._parse_args(None))
    assert opts["spec"] == "openapi.json"
    assert opts["served_spec"] == "https://api.example.com/openapi.json"
    assert opts["ignore_paths"] == ["/internal/*"]


def test_spec_key_comes_from_config(write_config):
    write_config("api:\n  openapi:\n    spec: openapi.json\n")
    assert openapi._resolve_options(openapi._parse_args(None))["spec"] == "openapi.json"


def test_flag_beats_config(write_config):
    write_config("api:\n  openapi:\n    spec: from-config.json\n")
    opts = openapi._resolve_options(openapi._parse_args(["--spec", "from-flag.json"]))
    assert opts["spec"] == "from-flag.json"


def test_no_probe_flag_disables_probing(write_config):
    write_config("api:\n  openapi:\n    spec: openapi.json\n")
    assert openapi._resolve_options(openapi._parse_args(["--no-probe"]))["probe_paths"] is False


# ── graceful skips ───────────────────────────────────────────────


def test_run_skips_with_exit_zero_when_unconfigured(write_config, capsys):
    write_config("urls:\n  production: https://api.example.com\n")
    assert openapi.run(["https://api.example.com"]) == 0
    assert "No api.openapi.spec configured" in capsys.readouterr().out


@pytest.mark.parametrize("spec", ["openapi.yaml", "openapi.yml", "docs/API.YAML"])
def test_a_yaml_spec_skips_rather_than_failing(write_config, capsys, spec):
    """No YAML parser ships with the CLI. Saying so beats going red."""
    write_config(f"api:\n  openapi:\n    spec: {spec}\n")
    assert openapi.run(["https://api.example.com"]) == 0
    out = capsys.readouterr().out
    assert "JSON only" in out
    assert "/openapi.json" in out


def test_a_missing_spec_file_is_could_not_run(write_config, capsys):
    """Nothing to compare is exit 2, as it is for DAST with the same config."""
    write_config("api:\n  openapi:\n    spec: nowhere.json\n")
    assert openapi.run(["https://api.example.com"]) == 2
    assert "spec file not found" in capsys.readouterr().out


def test_malformed_json_fails(write_config, isolated_cwd, capsys):
    (isolated_cwd / "openapi.json").write_text("{not json")
    write_config("api:\n  openapi:\n    spec: openapi.json\n")
    assert openapi.run([]) == 1
    assert "not valid JSON" in capsys.readouterr().out


# ── spec shape ───────────────────────────────────────────────────


def test_a_spec_with_no_version_key_fails():
    issues = openapi._validate_shape({"paths": {"/h": {"get": {}}}}, "the spec")
    assert any("version key" in i for i in issues)


def test_a_spec_with_no_paths_fails():
    issues = openapi._validate_shape({"openapi": "3.0.0", "paths": {}}, "the spec")
    assert any("no `paths`" in i for i in issues)


def test_a_swagger_2_spec_is_accepted():
    assert openapi._validate_shape({"swagger": "2.0", "paths": {"/h": {}}}, "the spec") == []


# ── operation sets ───────────────────────────────────────────────


def test_operations_are_method_plus_path():
    assert openapi._operations(SPEC, []) == {
        "GET /health",
        "GET /v1/items",
        "POST /v1/items",
        "GET /users/{id}",
    }


def test_non_operation_keys_are_not_operations():
    """`parameters` and `summary` live under a path item and aren't methods."""
    document = _spec({"/h": {"get": {}, "parameters": [], "summary": "x"}})
    assert openapi._operations(document, []) == {"GET /h"}


def test_ignore_paths_drops_operations():
    assert openapi._operations(SPEC, ["/users/*"]) == {
        "GET /health",
        "GET /v1/items",
        "POST /v1/items",
    }


# ── committed vs served ──────────────────────────────────────────


def _stub_served(monkeypatch, document):
    monkeypatch.setattr(
        openapi, "_load_spec_text", lambda spec: (json.dumps(document), None)
    )


def test_a_matching_served_spec_passes(monkeypatch):
    _stub_served(monkeypatch, SPEC)
    monkeypatch.setattr(openapi, "_probe_status", lambda url: 200)
    result = openapi._audit(
        "https://api.example.com",
        SPEC,
        _opts(served_spec="https://api.example.com/openapi.json"),
    )
    assert result["status"] == "pass"


def test_operation_comparison_ignores_ordering(monkeypatch):
    """Operations compare as sets, not sequences, so a reordered spec is not drift."""
    reordered = _spec(
        {
            "/users/{id}": {"get": {}},
            "/v1/items": {"post": {}, "get": {}},
            "/health": {"get": {}},
        }
    )
    _stub_served(monkeypatch, reordered)
    monkeypatch.setattr(openapi, "_probe_status", lambda url: 200)
    result = openapi._audit(
        "https://api.example.com",
        SPEC,
        _opts(served_spec="https://api.example.com/openapi.json"),
    )
    assert result["status"] == "pass"


def test_an_operation_served_but_not_committed_is_drift(monkeypatch):
    served = _spec(dict(SPEC["paths"], **{"/v1/new-thing": {"get": {}}}))
    _stub_served(monkeypatch, served)
    monkeypatch.setattr(openapi, "_probe_status", lambda url: 200)
    result = openapi._audit(
        "https://api.example.com",
        SPEC,
        _opts(served_spec="https://api.example.com/openapi.json"),
    )
    assert result["status"] == "fail"
    assert result["drift"]["missing_from_committed"] == ["GET /v1/new-thing"]
    assert any("committed artifact is stale" in i for i in result["issues"])


def test_an_operation_committed_but_not_served_is_drift(monkeypatch):
    served = _spec({k: v for k, v in SPEC["paths"].items() if k != "/health"})
    _stub_served(monkeypatch, served)
    monkeypatch.setattr(openapi, "_probe_status", lambda url: 200)
    result = openapi._audit(
        "https://api.example.com",
        SPEC,
        _opts(served_spec="https://api.example.com/openapi.json"),
    )
    assert result["status"] == "fail"
    assert result["drift"]["missing_from_served"] == ["GET /health"]


def test_ignore_paths_applies_to_the_comparison(monkeypatch):
    served = _spec(dict(SPEC["paths"], **{"/internal/debug": {"get": {}}}))
    _stub_served(monkeypatch, served)
    monkeypatch.setattr(openapi, "_probe_status", lambda url: 200)
    result = openapi._audit(
        "https://api.example.com",
        SPEC,
        _opts(served_spec="https://api.example.com/openapi.json", ignore_paths=["/internal/*"]),
    )
    assert result["status"] == "pass"


def test_an_unreadable_served_spec_fails(monkeypatch):
    monkeypatch.setattr(openapi, "_load_spec_text", lambda spec: (None, "could not fetch it"))
    monkeypatch.setattr(openapi, "_probe_status", lambda url: 200)
    result = openapi._audit(
        "https://api.example.com",
        SPEC,
        _opts(served_spec="https://api.example.com/openapi.json"),
    )
    assert result["status"] == "fail"
    assert any("served spec unreadable" in i for i in result["issues"])


def test_no_served_spec_is_noted_as_the_missing_signal(monkeypatch):
    monkeypatch.setattr(openapi, "_probe_status", lambda url: 200)
    result = openapi._audit("https://api.example.com", SPEC, _opts())
    assert result["status"] == "pass"
    assert any("served_spec" in n for n in result["notes"])


# ── probing ──────────────────────────────────────────────────────


def test_parameterised_paths_are_not_probed():
    probeable, skipped = openapi._parameterless_paths(SPEC, [])
    assert probeable == ["/health", "/v1/items"]
    assert skipped == ["/users/{id}"]


def test_skipped_parameterised_paths_are_noted(monkeypatch):
    monkeypatch.setattr(openapi, "_probe_status", lambda url: 200)
    result = openapi._audit("https://api.example.com", SPEC, _opts())
    assert result["skipped_paths"] == ["/users/{id}"]
    assert any("take parameters and were not probed" in n for n in result["notes"])


def test_a_404_on_a_documented_path_fails(monkeypatch):
    monkeypatch.setattr(
        openapi, "_probe_status", lambda url: 404 if url.endswith("/health") else 200
    )
    result = openapi._audit("https://api.example.com", SPEC, _opts())
    assert result["status"] == "fail"
    assert any("the route is gone" in i for i in result["issues"])


def test_a_405_is_not_a_failure(monkeypatch):
    """A 405 proves the route exists, because the check only sends GET."""
    monkeypatch.setattr(openapi, "_probe_status", lambda url: 405)
    result = openapi._audit("https://api.example.com", SPEC, _opts())
    assert result["status"] == "pass"


def test_a_500_on_a_documented_path_fails(monkeypatch):
    monkeypatch.setattr(openapi, "_probe_status", lambda url: 503)
    result = openapi._audit("https://api.example.com", SPEC, _opts())
    assert result["status"] == "fail"
    assert any("HTTP 503" in i for i in result["issues"])


def test_probing_is_skipped_without_a_url():
    result = openapi._audit(None, SPEC, _opts())
    assert result["probes"] == []
    assert any("no URL supplied" in n for n in result["notes"])


def test_no_probe_skips_the_probes(monkeypatch):
    monkeypatch.setattr(openapi, "_probe_status", lambda url: 404)
    result = openapi._audit("https://api.example.com", SPEC, _opts(probe_paths=False))
    assert result["probes"] == []
    assert result["status"] == "pass"


def test_ignore_paths_applies_to_probing():
    probeable, _ = openapi._parameterless_paths(SPEC, ["/v1/*"])
    assert probeable == ["/health"]


# ── safety ───────────────────────────────────────────────────────


@pytest.mark.parametrize("url", ["file:///etc/passwd", "ftp://example.com/x", "gopher://x"])
def test_refuses_non_http_schemes(url):
    with pytest.raises(ValueError, match="refuses scheme"):
        _http.require_safe_url(url, openapi._LABEL)


@pytest.mark.parametrize("url", ["http://api.example.com", "https://api.example.com"])
def test_allows_http_and_https(url):
    _http.require_safe_url(url, openapi._LABEL)


# ── report ───────────────────────────────────────────────────────


def test_report_renders_the_skip_state():
    md = openapi._build_markdown_report(
        {"status": "skipped", "skip_reason": "no spec", "skip_guidance": ["set it"]}
    )
    assert "SKIPPED" in md
    assert "no spec" in md


def test_report_lists_drift_and_the_probe_table(monkeypatch):
    served = _spec(dict(SPEC["paths"], **{"/v1/new-thing": {"get": {}}}))
    _stub_served(monkeypatch, served)
    monkeypatch.setattr(openapi, "_probe_status", lambda url: 200)
    result = openapi._audit(
        "https://api.example.com",
        SPEC,
        _opts(served_spec="https://api.example.com/openapi.json"),
    )
    md = openapi._build_markdown_report(result)
    assert "❌ FAIL" in md
    assert "GET /v1/new-thing" in md
    assert "Probed paths" in md
    assert "How to Fix" in md


def test_meta_is_declared_for_emit():
    assert openapi.META["report_path"].endswith("openapi-report.md")
    assert openapi.META["comment_discriminator"]


def test_an_unsafe_target_url_is_could_not_run(write_config, isolated_cwd, capsys):
    (isolated_cwd / "openapi.json").write_text('{"openapi": "3.0.0", "paths": {}}')
    write_config("api:\n  openapi:\n    spec: openapi.json\n")
    assert openapi.run(["file:///etc/passwd"]) == 2
    assert "refuses scheme" in capsys.readouterr().out
