"""Tests for the reliability:api-latency check.

The behaviour worth pinning: warmup samples are discarded rather than
averaged in, budgets gate on the median rather than the maximum (so one
slow sample from a shared runner doesn't fail a PR), and reachability
fails with no budget configured at all, because a timing taken from a
500 is not a latency measurement.
"""

from __future__ import annotations

import urllib.error

import pytest

from slopstopper.checks import _http, api_latency


def _opts(**overrides) -> dict:
    base = {
        "base_path": "",
        "paths": ["/health"],
        "samples": 3,
        "warmup": 1,
        "median_ms": None,
        "slowest_ms": None,
        "max_bytes": None,
    }
    base.update(overrides)
    return base


def _stub_fetch(monkeypatch, timings, status=200, size=100):
    """Replace _fetch with one that walks `timings`, recording every call.

    Returns the call log so a test can assert on how many requests were
    made. The warmup contract is about request count, not just numbers.
    """
    calls: list[str] = []
    remaining = list(timings)

    def fake(url):
        calls.append(url)
        elapsed = remaining.pop(0) if remaining else timings[-1]
        return status, size, elapsed

    monkeypatch.setattr(api_latency, "_fetch", fake)
    return calls


# ── arg / config plumbing ────────────────────────────────────────


def test_parse_args_defaults():
    parsed = api_latency._parse_args(None)
    assert parsed.url is None
    assert parsed.path == []
    assert parsed.samples is None
    assert parsed.warmup is None
    assert parsed.median_ms is None


def test_parse_args_explicit():
    parsed = api_latency._parse_args(
        [
            "--url", "https://api.example.com",
            "--path", "/health",
            "--path", "/v1/items",
            "--samples", "9",
            "--warmup", "2",
            "--median-ms", "400",
            "--slowest-ms", "1500",
            "--max-bytes", "2048",
        ]
    )
    assert parsed.url == "https://api.example.com"
    assert parsed.path == ["/health", "/v1/items"]
    assert parsed.samples == 9
    assert parsed.warmup == 2
    assert parsed.median_ms == 400
    assert parsed.slowest_ms == 1500
    assert parsed.max_bytes == 2048


def test_options_come_from_config(write_config):
    write_config(
        "api:\n"
        "  base_path: /api/v1\n"
        "  latency:\n"
        "    paths: [/health, /v1/items]\n"
        "    samples: 7\n"
        "    warmup: 3\n"
        "    median_ms: 250\n"
    )
    opts = api_latency._resolve_options(api_latency._parse_args(None))
    assert opts["base_path"] == "/api/v1"
    assert opts["paths"] == ["/health", "/v1/items"]
    # The stdlib YAML subset yields strings; the check must coerce.
    assert opts["samples"] == 7
    assert opts["warmup"] == 3
    assert opts["median_ms"] == 250


def test_flag_beats_config(write_config):
    write_config("api:\n  latency:\n    paths: [/from-config]\n")
    opts = api_latency._resolve_options(api_latency._parse_args(["--path", "/from-flag"]))
    assert opts["paths"] == ["/from-flag"]


def test_env_beats_config(write_config, monkeypatch):
    write_config("api:\n  latency:\n    paths: [/from-config]\n")
    monkeypatch.setenv("API_LATENCY_PATHS", "/from-env, /also-env")
    opts = api_latency._resolve_options(api_latency._parse_args(None))
    assert opts["paths"] == ["/from-env", "/also-env"]


def test_malformed_samples_falls_back_to_default(write_config):
    write_config("api:\n  latency:\n    paths: [/h]\n    samples: lots\n")
    opts = api_latency._resolve_options(api_latency._parse_args(None))
    assert opts["samples"] == api_latency.DEFAULT_SAMPLES


def test_warmup_zero_from_a_flag_is_honoured(write_config):
    """`--warmup 0` is falsy, but it must not silently fall back to the default."""
    write_config("api:\n  latency:\n    paths: [/h]\n")
    opts = api_latency._resolve_options(api_latency._parse_args(["--warmup", "0"]))
    assert opts["warmup"] == 0


# ── graceful skip ────────────────────────────────────────────────


def test_run_skips_with_exit_zero_when_unconfigured(write_config, capsys):
    """An unconfigured check is not a failing check."""
    write_config("urls:\n  production: https://api.example.com\n")
    assert api_latency.run(["https://api.example.com"]) == 0
    assert "No api.latency.paths configured" in capsys.readouterr().out


def test_run_requires_a_url_once_configured(write_config, monkeypatch, capsys):
    write_config("api:\n  latency:\n    paths: [/health]\n")
    monkeypatch.delenv("API_LATENCY_TEST_URL", raising=False)
    assert api_latency.run([]) == 2  # missing input, not a verdict
    assert "URL is required" in capsys.readouterr().out


# ── sampling ─────────────────────────────────────────────────────


def test_warmup_requests_are_discarded_from_the_statistics(monkeypatch):
    """The warmup number is the cold start, so it must not reach the median."""
    calls = _stub_fetch(monkeypatch, [5000.0, 10.0, 10.0, 10.0])
    sample = api_latency._sample_path("https://api.example.com/health", samples=3, warmup=1)
    assert len(calls) == 4  # 1 warmup + 3 measured
    assert sample["median_ms"] == 10.0
    assert sample["slowest_ms"] == 10.0


def test_zero_warmup_measures_every_request(monkeypatch):
    calls = _stub_fetch(monkeypatch, [10.0, 20.0, 30.0])
    sample = api_latency._sample_path("https://api.example.com/health", samples=3, warmup=0)
    assert len(calls) == 3
    assert sample["median_ms"] == 20.0


def test_a_single_sample_still_works(monkeypatch):
    _stub_fetch(monkeypatch, [42.0])
    sample = api_latency._sample_path("https://api.example.com/health", samples=1, warmup=0)
    assert sample["median_ms"] == 42.0
    assert sample["slowest_ms"] == 42.0
    assert sample["fastest_ms"] == 42.0


def test_payload_size_is_measured_on_the_body(monkeypatch):
    _stub_fetch(monkeypatch, [10.0, 10.0], size=4096)
    sample = api_latency._sample_path("https://api.example.com/health", samples=2, warmup=0)
    assert sample["bytes"] == 4096


def test_a_failing_warmup_does_not_mask_a_failing_measurement(monkeypatch):
    def boom(url):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(api_latency, "_fetch", boom)
    sample = api_latency._sample_path("https://api.example.com/health", samples=3, warmup=1)
    assert sample["error"]
    assert "URLError" in sample["error"]


# ── budgets ──────────────────────────────────────────────────────


def test_one_slow_outlier_does_not_fail_a_median_budget(monkeypatch):
    """The motivating case: a shared CI runner hiccups on one request."""
    _stub_fetch(monkeypatch, [10.0, 10.0, 900.0])
    result = api_latency._audit(
        "https://api.example.com", _opts(samples=3, warmup=0, median_ms=100)
    )
    assert result["status"] == "pass"


def test_the_same_outlier_does_fail_a_slowest_budget(monkeypatch):
    """`slowest_ms` is the opt-in tail ceiling, which is noisier by construction."""
    _stub_fetch(monkeypatch, [10.0, 10.0, 900.0])
    result = api_latency._audit(
        "https://api.example.com", _opts(samples=3, warmup=0, slowest_ms=100)
    )
    assert result["status"] == "fail"
    assert any("slowest sample" in i for i in result["paths"][0]["issues"])


def test_a_consistently_slow_endpoint_fails_a_median_budget(monkeypatch):
    _stub_fetch(monkeypatch, [800.0, 900.0, 850.0])
    result = api_latency._audit(
        "https://api.example.com", _opts(samples=3, warmup=0, median_ms=100)
    )
    assert result["status"] == "fail"
    assert any("median" in i for i in result["paths"][0]["issues"])


def test_a_payload_budget_fails_an_oversized_response(monkeypatch):
    _stub_fetch(monkeypatch, [10.0, 10.0], size=500_000)
    result = api_latency._audit(
        "https://api.example.com", _opts(samples=2, warmup=0, max_bytes=200_000)
    )
    assert result["status"] == "fail"
    assert any("over the 200000-byte budget" in i for i in result["paths"][0]["issues"])


def test_timings_are_advisory_with_no_budget_set(monkeypatch):
    _stub_fetch(monkeypatch, [900.0, 900.0])
    result = api_latency._audit("https://api.example.com", _opts(samples=2, warmup=0))
    assert result["status"] == "pass"
    assert any("no budget set" in n for n in result["paths"][0]["notes"])


# ── reachability is not opt-in ───────────────────────────────────


def test_unreachable_fails_with_no_budgets_set(monkeypatch):
    def boom(url):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(api_latency, "_fetch", boom)
    result = api_latency._audit("https://api.example.com", _opts())
    assert result["status"] == "fail"
    assert any("not reachable" in i for i in result["paths"][0]["issues"])


def test_non_2xx_fails_with_no_budgets_set(monkeypatch):
    """A 500 that answers in 3ms is not fast."""
    _stub_fetch(monkeypatch, [3.0, 3.0], status=500)
    result = api_latency._audit("https://api.example.com", _opts(samples=2, warmup=0))
    assert result["status"] == "fail"
    assert any("HTTP 500" in i for i in result["paths"][0]["issues"])


def test_a_3xx_is_not_a_failure(monkeypatch):
    _stub_fetch(monkeypatch, [10.0, 10.0], status=301)
    result = api_latency._audit("https://api.example.com", _opts(samples=2, warmup=0))
    assert result["status"] == "pass"


def test_one_bad_path_fails_the_whole_audit(monkeypatch):
    statuses = {"https://api.example.com/health": 200, "https://api.example.com/boom": 500}

    def fake(url):
        return statuses[url], 100, 10.0

    monkeypatch.setattr(api_latency, "_fetch", fake)
    result = api_latency._audit(
        "https://api.example.com", _opts(paths=["/health", "/boom"], samples=1, warmup=0)
    )
    assert result["status"] == "fail"
    assert [p["status"] for p in result["paths"]] == ["pass", "fail"]


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
    assert api_latency._join(url, base_path, path) == expected


# ── safety ───────────────────────────────────────────────────────


@pytest.mark.parametrize("url", ["file:///etc/passwd", "ftp://example.com/x", "gopher://x"])
def test_refuses_non_http_schemes(url):
    with pytest.raises(ValueError, match="refuses scheme"):
        _http.require_safe_url(url, api_latency._LABEL)


@pytest.mark.parametrize("url", ["http://api.example.com", "https://api.example.com"])
def test_allows_http_and_https(url):
    _http.require_safe_url(url, api_latency._LABEL)


# ── report ───────────────────────────────────────────────────────


def test_report_renders_the_skip_state():
    md = api_latency._build_markdown_report({"status": "skipped"})
    assert "SKIPPED" in md
    assert "api.latency.paths" in md


def test_report_tabulates_timings_and_lists_issues(monkeypatch):
    _stub_fetch(monkeypatch, [800.0, 900.0, 850.0])
    result = api_latency._audit(
        "https://api.example.com", _opts(samples=3, warmup=0, median_ms=100)
    )
    md = api_latency._build_markdown_report(result)
    assert "❌ FAIL" in md
    assert "| Endpoint | Median | Slowest | Fastest | Bytes |" in md
    assert "over the 100ms budget" in md
    assert "How to Fix" in md


def test_meta_is_declared_for_emit():
    assert api_latency.META["report_path"].endswith("api-latency-report.md")
    assert api_latency.META["comment_discriminator"]
