"""Tests for the helpers shared across checks: _http, _report, _playwright.

These used to be copied into each check module (eight SSRF guards, seven
fetches, nine timestamps in three formats). Now there is one of each,
and this is where its behaviour is pinned.
"""

from __future__ import annotations

import io
import urllib.error
from pathlib import Path

import pytest

from slopstopper.checks import _http, _playwright, _report, _tools


# ── _http ────────────────────────────────────────────────────────


@pytest.mark.parametrize("url", ["file:///etc/passwd", "ftp://example.com/x", "gopher://x", "javascript:alert(1)"])
def test_require_safe_url_refuses_non_http(url):
    with pytest.raises(ValueError, match="API health check refuses scheme"):
        _http.require_safe_url(url, "API health check")


@pytest.mark.parametrize("url", ["http://example.com", "HTTPS://example.com/x"])
def test_require_safe_url_allows_http_and_https(url):
    _http.require_safe_url(url)


def test_open_url_guards_before_opening(monkeypatch):
    """The guard must run before anything is opened, or it guards nothing."""
    called = []
    monkeypatch.setattr(_http, "_send", lambda req, timeout, label: called.append(req))
    with pytest.raises(ValueError):
        _http.open_url("file:///etc/passwd", "ua")
    assert called == []


class _FakeResponse:
    def __init__(self, status=200, headers=None, body=b""):
        self.status = status
        self.headers = headers or {}
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_open_url_sends_user_agent_method_and_extra_headers(monkeypatch):
    seen = {}

    def fake_urlopen(req, timeout, label):
        seen["url"] = req.full_url
        seen["method"] = req.get_method()
        seen["headers"] = dict(req.header_items())
        seen["timeout"] = timeout
        return _FakeResponse()

    monkeypatch.setattr(_http, "_send", fake_urlopen)
    with _http.open_url("https://x.example/p", "SlopStopper-Test/1.0", method="HEAD",
                        headers={"Origin": "https://app.example"}, timeout=7):
        pass
    assert seen["url"] == "https://x.example/p"
    assert seen["method"] == "HEAD"
    assert seen["headers"]["User-agent"] == "SlopStopper-Test/1.0"
    assert seen["headers"]["Origin"] == "https://app.example"
    assert seen["timeout"] == 7


def test_fetch_text_returns_status_type_and_decoded_body(monkeypatch):
    monkeypatch.setattr(
        _http, "_send",
        lambda req, timeout, label: _FakeResponse(200, {"Content-Type": "text/html"}, "héllo".encode()),
    )
    assert _http.fetch_text("https://x.example", "ua") == (200, "text/html", "héllo")


def test_head_ok_maps_statuses_and_errors(monkeypatch):
    monkeypatch.setattr(_http, "_send", lambda req, timeout, label: _FakeResponse(204))
    assert _http.head_ok("https://x.example", "ua") == (True, "HTTP 204")

    monkeypatch.setattr(_http, "_send", lambda req, timeout, label: _FakeResponse(404))
    assert _http.head_ok("https://x.example", "ua") == (False, "HTTP 404")

    def raise_http(req, timeout, label):
        raise urllib.error.HTTPError("https://x", 500, "boom", {}, io.BytesIO())

    monkeypatch.setattr(_http, "_send", raise_http)
    assert _http.head_ok("https://x.example", "ua") == (False, "HTTP 500")

    def raise_url(req, timeout, label):
        raise urllib.error.URLError("refused")

    monkeypatch.setattr(_http, "_send", raise_url)
    ok, detail = _http.head_ok("https://x.example", "ua")
    assert ok is False and detail.startswith("URLError")

    # A refused scheme is a detail, not an exception, for a HEAD probe.
    assert _http.head_ok("file:///etc/passwd", "ua")[0] is False


@pytest.mark.parametrize(
    "url,base_path,path,expected",
    [
        ("https://api.example.com", "", "/health", "https://api.example.com/health"),
        ("https://api.example.com/", "", "/health", "https://api.example.com/health"),
        ("https://api.example.com", "/api/v1", "/health", "https://api.example.com/api/v1/health"),
        ("https://api.example.com", "/api/v1/", "/health", "https://api.example.com/api/v1/health"),
        ("https://api.example.com", "", "health", "https://api.example.com/health"),
        ("https://api.example.com", None, "health", "https://api.example.com/health"),
    ],
)
def test_join_url(url, base_path, path, expected):
    assert _http.join_url(url, base_path, path) == expected


# ── _report ──────────────────────────────────────────────────────


def test_generated_at_is_one_format():
    import re

    assert re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} UTC", _report.generated_at())


def test_render_findings_emits_the_bullet_syntax_comment_py_reads():
    """comment.extract_failures reads `^\\s*-\\s*❌\\s*(.+?)$`, so keep the shape."""
    import re

    lines = _report.render_findings("Issues", _report.FAIL_ICON, ["a broke", "b broke"])
    assert lines == ["**Issues:**", "- ❌ a broke", "- ❌ b broke", ""]
    pattern = re.compile(r"^\s*-\s*❌\s*(.+?)$")
    assert [pattern.match(l).group(1) for l in lines if pattern.match(l)] == ["a broke", "b broke"]


def test_render_findings_is_empty_when_nothing_to_render():
    assert _report.render_findings("Issues", "❌", []) == []


def test_render_skip_shape():
    assert _report.render_skip("no paths configured.", ["Set them:", "```yaml", "x: y", "```"]) == [
        "**Overall:** ⏭️ SKIPPED: no paths configured.",
        "",
        "Set them:",
        "```yaml",
        "x: y",
        "```",
        "",
    ]


def test_write_reports_writes_json_and_rendered_markdown(tmp_path):
    report_dir = tmp_path / "r"
    _report.write_reports(
        report_dir / "x.json", report_dir / "x.md",
        {"status": "pass", "n": 1}, lambda r: f"# Report {r['status']}\n",
    )
    assert (report_dir / "x.md").read_text() == "# Report pass\n"
    assert '"n": 1' in (report_dir / "x.json").read_text()


def test_gha_run_url(monkeypatch):
    for var in ("GITHUB_SERVER_URL", "GITHUB_REPOSITORY", "GITHUB_RUN_ID"):
        monkeypatch.delenv(var, raising=False)
    assert _report.gha_run_url() is None
    monkeypatch.setenv("GITHUB_SERVER_URL", "https://github.com")
    monkeypatch.setenv("GITHUB_REPOSITORY", "acme/site")
    monkeypatch.setenv("GITHUB_RUN_ID", "42")
    assert _report.gha_run_url() == "https://github.com/acme/site/actions/runs/42"


# ── _playwright ──────────────────────────────────────────────────


def test_npx_available_uses_which(monkeypatch):
    monkeypatch.setattr(_tools.shutil, "which", lambda _: "/usr/bin/npx")
    assert _tools.npx_available() is True
    monkeypatch.setattr(_tools.shutil, "which", lambda _: None)
    assert _tools.npx_available() is False


def test_build_cmd_names_the_config_spec_and_reporter():
    cmd = _playwright.build_cmd(Path("/cfg/playwright.config.js"), Path("/cfg/tests/smoke.spec.ts"), ci_mode=True)
    assert cmd[:3] == ["npx", "playwright", "test"]
    assert "--config=/cfg/playwright.config.js" in cmd
    assert "/cfg/tests/smoke.spec.ts" in cmd
    assert "--reporter=list,html,json" in cmd  # json: see _contract.playwright_ran
    assert "--reporter=list,json" in _playwright.build_cmd(Path("c"), Path("s"), ci_mode=False)


def test_write_summary_pass_and_fail(tmp_path, monkeypatch):
    monkeypatch.setattr(_report, "gha_run_url", lambda: "https://gh/run/1")
    md = tmp_path / "r" / "x.md"
    _playwright.write_summary(tmp_path / "r", md, "## Smoke", 0, "https://x", "it broke")
    text = md.read_text()
    assert "✅ PASSED" in text and "it broke" not in text
    _playwright.write_summary(tmp_path / "r", md, "## Smoke", 1, "https://x", "it broke")
    text = md.read_text()
    assert "❌ FAILED" in text and "it broke" in text and "https://gh/run/1" in text


# ── redirects are guarded too ────────────────────────────────────


@pytest.mark.parametrize("target", ["ftp://127.0.0.1/secret", "file:///etc/passwd"])
def test_a_redirect_off_http_is_refused_as_an_http_error(redirecting_server, target):
    """urllib follows `302 Location: ftp://…` by default; open_url must not.
    Refused the way urllib refuses `file://`, so both schemes behave alike
    and every caller's HTTPError handling reports it as a status."""
    url = redirecting_server(target)
    with pytest.raises(urllib.error.HTTPError) as exc:
        _http.open_url(url, "ua", timeout=5, label="Test check")
    assert exc.value.code == 302
    exc.value.close()


def test_head_ok_reports_a_refused_redirect_as_its_status(redirecting_server):
    ok, detail = _http.head_ok(redirecting_server("ftp://127.0.0.1/secret"), "ua", timeout=5)
    assert (ok, detail) == (False, "HTTP 302")


def test_the_opener_is_built_once_per_label():
    assert _http._opener("A check") is _http._opener("A check")
    assert _http._opener("A check") is not _http._opener("Another check")


@pytest.mark.parametrize(
    "url,expected",
    [("https://x.example", True), ("HTTP://x.example", True), ("ftp://x", False),
     ("data:image/png;base64,AA", False), ("/relative", False)],
)
def test_is_http_url(url, expected):
    assert _http.is_http_url(url) is expected


def test_generated_at_precisions():
    import re as _re

    assert _re.fullmatch(r"\d{4}-\d\d-\d\d \d\d:\d\d:\d\d UTC", _report.generated_at())
    assert _re.fullmatch(r"\d{4}-\d\d-\d\d \d\d:\d\d UTC", _report.generated_at(precision="minutes"))


# ── _playwright.run_check: the flow the four Playwright checks share ──

_FAKE_CHECK = _playwright.Check(
    name="fake",
    spec_name="smoke",  # a spec that exists in the bundled package data
    url_env="FAKE_TEST_URL",
    url_fallbacks=("SMOKE_TEST_URL",),
    title="## Fake Results",
    failure_hint="it broke",
    banner="Faking against: {url}",
    banner_icon="🧪",
    verb="fake",
)


def _run_fake(args, monkeypatch, *, npx=True, rc=0, report=True):
    """Drive run_check with a stubbed Playwright that exits `rc`.

    `report=False` leaves no JSON report behind, which is what a suite
    that never started looks like.
    """
    import subprocess

    captured: dict = {}

    def fake_run(cmd, env, check):
        captured["cmd"] = cmd
        captured["env"] = env
        if rc == 1 and report:
            from tests._fakes import playwright_failed
            return playwright_failed(cmd, env, check)
        return subprocess.CompletedProcess(cmd, rc)

    monkeypatch.setattr(_tools, "npx_available", lambda: npx)
    monkeypatch.setattr(subprocess, "run", fake_run)
    code = _playwright.run_check(
        _FAKE_CHECK, args, build_env=lambda url, ci: _playwright.base_env(_FAKE_CHECK, url, ci),
    )
    return code, captured


def test_parse_args_shape_and_prog_name(capsys):
    parsed = _playwright.parse_args(_FAKE_CHECK, ["http://localhost:8080", "--ci"])
    assert parsed.url_positional == "http://localhost:8080"
    assert parsed.url is None
    assert parsed.ci is True
    with pytest.raises(SystemExit):
        _playwright.parse_args(_FAKE_CHECK, ["--help"])
    assert "slopstopper run reliability:fake" in capsys.readouterr().out


def test_resolve_url_order_is_flag_then_own_env_then_fallbacks(monkeypatch):
    monkeypatch.setenv("FAKE_TEST_URL", "https://own")
    monkeypatch.setenv("SMOKE_TEST_URL", "https://fallback")
    assert _playwright.resolve_url(_FAKE_CHECK, "https://flag") == "https://flag"
    assert _playwright.resolve_url(_FAKE_CHECK, None) == "https://own"
    monkeypatch.delenv("FAKE_TEST_URL")
    assert _playwright.resolve_url(_FAKE_CHECK, None) == "https://fallback"
    monkeypatch.delenv("SMOKE_TEST_URL")
    assert _playwright.resolve_url(_FAKE_CHECK, None) is None


def test_base_env_sets_json_path_url_and_ci_only_in_ci_mode(isolated_cwd, monkeypatch):
    monkeypatch.delenv("CI", raising=False)
    env = _playwright.base_env(_FAKE_CHECK, "https://x", ci_mode=False)
    assert env["FAKE_TEST_URL"] == "https://x"
    assert env["PLAYWRIGHT_JSON_OUTPUT_NAME"].endswith("smoke-results.json")
    assert "CI" not in env
    assert _playwright.base_env(_FAKE_CHECK, "https://x", ci_mode=True)["CI"] == "true"


def test_run_check_returns_two_when_npx_missing(monkeypatch, isolated_cwd, capsys):
    code, captured = _run_fake(["--url", "https://x"], monkeypatch, npx=False)
    assert code == 2
    assert "cmd" not in captured
    assert "npx is not available" in capsys.readouterr().out


def test_run_check_returns_two_without_a_url_and_names_the_check(monkeypatch, isolated_cwd, capsys):
    monkeypatch.delenv("FAKE_TEST_URL", raising=False)
    monkeypatch.delenv("SMOKE_TEST_URL", raising=False)
    code, _ = _run_fake([], monkeypatch)
    assert code == 2
    out = capsys.readouterr().out
    assert "fake target URL is required" in out
    assert "FAKE_TEST_URL=https://your-site slopstopper run reliability:fake" in out


def test_run_check_returns_two_when_the_spec_is_missing(monkeypatch, isolated_cwd, capsys):
    monkeypatch.setattr(_playwright.templates, "playwright_spec", lambda name: Path("/nowhere/x.spec.ts"))
    code, captured = _run_fake(["--url", "https://x"], monkeypatch)
    assert code == 2
    assert "cmd" not in captured, "Playwright must not run without a spec"
    assert "fake spec not found" in capsys.readouterr().out
    assert not (isolated_cwd / ".ss" / ".run").exists(), "nothing is staged for a missing spec"


def test_run_check_prints_the_banner_with_its_icon_before_anything_else(monkeypatch, isolated_cwd, capsys):
    _run_fake(["https://x"], monkeypatch)
    out = capsys.readouterr().out
    assert "🧪 Faking against: https://x" in out
    plain = _playwright.Check(**{**_FAKE_CHECK.__dict__, "banner_icon": None})
    monkeypatch.setattr(_tools, "npx_available", lambda: True)
    _playwright.run_check(plain, ["https://x"], build_env=lambda url, ci: _playwright.base_env(plain, url, ci))
    out = capsys.readouterr().out
    assert "Faking against: https://x" in out and "🧪" not in out


def test_run_check_runs_playwright_writes_the_summary_and_maps_exit_codes(monkeypatch, isolated_cwd):
    code, captured = _run_fake(["https://x", "--ci"], monkeypatch, rc=0)
    assert code == 0
    assert captured["cmd"][:3] == ["npx", "playwright", "test"]
    assert "--reporter=list,html,json" in captured["cmd"]
    assert captured["env"]["FAKE_TEST_URL"] == "https://x"
    assert captured["env"]["CI"] == "true"
    assert "--config=.ss/.run/playwright.config.js" in captured["cmd"]
    assert ".ss/.run/tests/smoke.spec.ts" in captured["cmd"]
    assert not (isolated_cwd / ".ss" / "playwright.config.js").exists(), "a run must not eject"
    assert not (isolated_cwd / ".ss" / "tests").exists(), "a run must not eject"
    body = _FAKE_CHECK.report_md.read_text()
    assert body.startswith("## Fake Results") and "PASSED" in body

    code, _ = _run_fake(["https://x"], monkeypatch, rc=1)
    assert code == 1, "a suite that ran and failed is a verdict"
    body = _FAKE_CHECK.report_md.read_text()
    assert "FAILED" in body and "it broke" in body

    code, _ = _run_fake(["https://x"], monkeypatch, rc=2)
    assert code == 2, "any other Playwright exit is could-not-run"


def test_run_check_treats_exit_one_without_a_report_as_not_run(monkeypatch, isolated_cwd):
    code, _ = _run_fake(["https://x"], monkeypatch, rc=1, report=False)
    assert code == 2
