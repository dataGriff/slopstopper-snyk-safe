"""Tests for the reliability:cwv check (Lighthouse CI wrapper)."""

from __future__ import annotations

import json
from pathlib import Path

from slopstopper import templates
from slopstopper.checks import _tools, cwv


# ── helpers ──────────────────────────────────────────────────────


def test_parse_args_defaults():
    parsed = cwv._parse_args(None)
    assert parsed.url is None
    # config defaults to None because resolution happens in run() via templates
    assert parsed.config is None


def test_parse_args_explicit():
    parsed = cwv._parse_args(["--url", "https://example.com", "--config", "custom.json"])
    assert parsed.url == "https://example.com"
    assert parsed.config == "custom.json"
    assert parsed.prod is False


def test_parse_args_prod_flag():
    parsed = cwv._parse_args(["--url", "https://example.com", "--prod"])
    assert parsed.prod is True


def test_run_with_prod_uses_prod_lighthouserc(monkeypatch, isolated_cwd):
    import json as _json
    captured: dict = {}

    def fake_run_lhci(cmd):
        captured["cmd"] = cmd
        lhci = Path(".lighthouseci")
        lhci.mkdir()
        (lhci / "lhr-1.json").write_text(_json.dumps(_sample_lhr()))
        return 0, ""

    monkeypatch.setattr(cwv, "_npx_available", lambda: True)
    monkeypatch.setattr(cwv, "_run_lhci", fake_run_lhci)

    rc = cwv.run(["--url", "https://example.com", "--prod"])
    assert rc == 0
    config_arg = next(a for a in captured["cmd"] if a.startswith("--config="))
    assert "lighthouserc.prod.json" in config_arg


def test_run_without_prod_uses_dev_lighthouserc(monkeypatch, isolated_cwd):
    import json as _json
    captured: dict = {}

    def fake_run_lhci(cmd):
        captured["cmd"] = cmd
        lhci = Path(".lighthouseci")
        lhci.mkdir()
        (lhci / "lhr-1.json").write_text(_json.dumps(_sample_lhr()))
        return 0, ""

    monkeypatch.setattr(cwv, "_npx_available", lambda: True)
    monkeypatch.setattr(cwv, "_run_lhci", fake_run_lhci)

    rc = cwv.run(["--url", "https://example.com"])
    assert rc == 0
    config_arg = next(a for a in captured["cmd"] if a.startswith("--config="))
    # Dev config is plain `lighthouserc.json`, not `.prod.json`.
    assert config_arg.endswith("lighthouserc.json")


def test_resolve_url_prefers_flag(monkeypatch):
    monkeypatch.setenv("CWV_URL", "https://from-env")
    assert cwv._resolve_url("https://from-flag") == "https://from-flag"


def test_resolve_url_falls_back_to_env(monkeypatch):
    monkeypatch.setenv("CWV_URL", "https://from-env")
    assert cwv._resolve_url(None) == "https://from-env"


def test_resolve_url_none_when_neither_set(monkeypatch):
    monkeypatch.delenv("CWV_URL", raising=False)
    assert cwv._resolve_url(None) is None


def test_build_cmd_threads_url_and_config():
    cmd = cwv._build_cmd("https://example.com", "myconf.json")
    assert cmd[:3] == ["npx", "lhci", "autorun"]
    assert "--collect.url=https://example.com" in cmd
    assert "--config=myconf.json" in cmd


def test_build_cmd_keeps_reports_local_by_default():
    cmd = cwv._build_cmd("https://example.com", "myconf.json")
    assert "--upload.target=filesystem" in cmd
    assert f"--upload.outputDir={cwv.HTML_DIR}" in cmd
    assert not any("temporary-public-storage" in arg for arg in cmd)


def test_build_cmd_public_report_is_opt_in():
    cmd = cwv._build_cmd("https://example.com", "myconf.json", public_report=True)
    assert "--upload.target=temporary-public-storage" in cmd
    assert "--upload.target=filesystem" not in cmd


def test_bundled_lighthouserc_never_targets_public_storage():
    from slopstopper import templates

    for prod in (False, True):
        rc = json.loads(templates.lighthouserc(prod=prod).read_text())
        assert rc["ci"]["upload"]["target"] == "filesystem"


def test_run_reads_public_report_from_config(monkeypatch, isolated_cwd, write_config):
    write_config("reliability:\n  cwv:\n    public_report: true\n")
    captured: dict = {}

    def fake_run_lhci(cmd):
        captured["cmd"] = cmd
        return 0, ""

    monkeypatch.setattr(cwv, "_npx_available", lambda: True)
    monkeypatch.setattr(cwv, "_run_lhci", fake_run_lhci)
    cwv.run(["--url", "https://example.com"])
    assert "--upload.target=temporary-public-storage" in captured["cmd"]


# ── META contract ────────────────────────────────────────────────


def test_meta_has_required_keys_for_pr_comment_and_issue():
    assert cwv.META["report_path"].endswith("cwv-report.md")
    for key in ("comment_discriminator", "issue_title", "issue_labels", "issue_followup"):
        assert key in cwv.META
    # Bot reuses the existing pre-flip comment by matching this substring.
    assert "Core Web Vitals" in cwv.META["comment_discriminator"]


# ── lhci JSON parsing ────────────────────────────────────────────


def _sample_lhr() -> dict:
    return {
        "categories": {"performance": {"score": 0.88}},
        "audits": {
            "largest-contentful-paint": {"numericValue": 1234.5},
            "total-blocking-time": {"numericValue": 50},
            "cumulative-layout-shift": {"numericValue": 0.0123},
            "first-contentful-paint": {"numericValue": 600.4},
        },
    }


def test_extract_metrics_rounds_perf_and_passes_through_audits():
    m = cwv._extract_metrics(_sample_lhr())
    assert m["performance"] == 88
    assert m["lcp"] == 1234.5
    assert m["tbt"] == 50
    assert m["cls"] == 0.0123
    assert m["fcp"] == 600.4


def test_extract_metrics_handles_missing_fields():
    m = cwv._extract_metrics({})
    assert m == {"performance": None, "lcp": None, "tbt": None, "cls": None, "fcp": None}


def test_passes_threshold_logic():
    assert cwv._passes("performance", 70) is True
    assert cwv._passes("performance", 69) is False
    assert cwv._passes("lcp", 4000) is True
    assert cwv._passes("lcp", 4001) is False
    assert cwv._passes("tbt", 600) is True
    assert cwv._passes("tbt", 601) is False
    assert cwv._passes("cls", 0.25) is True
    assert cwv._passes("cls", 0.26) is False


def test_passes_returns_none_for_missing_metric():
    assert cwv._passes("lcp", None) is None


def test_format_value_per_metric():
    assert cwv._format_value("performance", 88) == "88/100"
    assert cwv._format_value("lcp", 1234.5) == "1234 ms"
    assert cwv._format_value("cls", 0.123) == "0.123"
    assert cwv._format_value("performance", None) == "N/A"


def test_icon_paths():
    assert cwv._icon(True) == "✅"
    assert cwv._icon(False) == "❌"
    assert cwv._icon(None) == "⚠️"


def test_extract_report_url_finds_storage_url():
    out = "lots of output\n  https://storage.googleapis.com/abc/def.html\n"
    assert cwv._extract_report_url(out) == "https://storage.googleapis.com/abc/def.html"


def test_extract_report_url_returns_none_when_absent():
    assert cwv._extract_report_url("nothing to see here") is None


# ── markdown rendering ───────────────────────────────────────────


def test_build_report_md_pass_path_includes_table_and_status():
    md = cwv._build_report_md(
        "https://example.com",
        {"performance": 88, "lcp": 1200, "tbt": 50, "cls": 0.01, "fcp": 600},
        report_url="https://storage.googleapis.com/x.html",
        overall_pass=True,
    )
    assert "PASSED" in md
    assert "https://example.com" in md
    assert "| Performance score | ≥ 70 |" in md
    assert "✅" in md
    assert "[📊 Full Lighthouse Report](https://storage.googleapis.com/x.html)" in md
    assert "slopstopper run reliability:cwv" in md


def test_build_report_md_fail_path_marks_failing_metrics():
    md = cwv._build_report_md(
        "https://example.com",
        {"performance": 40, "lcp": 9000, "tbt": 50, "cls": 0.01, "fcp": 600},
        report_url=None,
        overall_pass=False,
    )
    assert "FAILED" in md
    # Performance row should have a ❌; LCP row should have a ❌.
    assert "❌ 40/100" in md
    assert "❌ 9000 ms" in md
    # No storage URL => point at the local HTML reports instead.
    assert "Full Lighthouse Report" not in md
    assert f"`{cwv.HTML_DIR}/`" in md


def test_build_report_md_renders_warning_for_missing_metric():
    md = cwv._build_report_md(
        "https://example.com",
        {"performance": None, "lcp": 1200, "tbt": 50, "cls": 0.01, "fcp": None},
        report_url=None,
        overall_pass=True,
    )
    assert "⚠️ N/A" in md


# ── _write_report (happy + error paths) ─────────────────────────


def test_write_report_writes_markdown_from_lhr_json(monkeypatch, isolated_cwd):
    lhci = Path(".lighthouseci")
    lhci.mkdir()
    (lhci / "lhr-1.json").write_text(json.dumps(_sample_lhr()))

    cwv._write_report("https://example.com", "log line\n", lhci_exit=0)

    md = Path(cwv.REPORT_MD).read_text()
    assert "PASSED" in md
    assert "88/100" in md


def test_write_report_picks_latest_lhr(monkeypatch, isolated_cwd):
    lhci = Path(".lighthouseci")
    lhci.mkdir()
    (lhci / "lhr-1.json").write_text(json.dumps({"categories": {"performance": {"score": 0.5}}}))
    (lhci / "lhr-2.json").write_text(json.dumps(_sample_lhr()))  # score 0.88

    cwv._write_report("https://example.com", "", lhci_exit=0)
    assert "88/100" in Path(cwv.REPORT_MD).read_text()


def test_write_report_handles_missing_lhr_dir(monkeypatch, isolated_cwd):
    cwv._write_report("https://example.com", "", lhci_exit=1)
    text = Path(cwv.REPORT_MD).read_text()
    assert "NO REPORT" in text


def test_write_report_handles_unparseable_lhr(monkeypatch, isolated_cwd):
    lhci = Path(".lighthouseci")
    lhci.mkdir()
    (lhci / "lhr-1.json").write_text("not json")

    cwv._write_report("https://example.com", "", lhci_exit=1)
    assert "PARSE ERROR" in Path(cwv.REPORT_MD).read_text()


def test_write_report_marks_failed_on_nonzero_lhci_exit(monkeypatch, isolated_cwd):
    lhci = Path(".lighthouseci")
    lhci.mkdir()
    (lhci / "lhr-1.json").write_text(json.dumps(_sample_lhr()))

    cwv._write_report("https://example.com", "", lhci_exit=1)
    assert "FAILED" in Path(cwv.REPORT_MD).read_text()


# ── subprocess / runtime ─────────────────────────────────────────


def test_npx_available_via_which(monkeypatch):
    """`_npx_available` is the shared _tools.npx_available; patch where it looks."""
    monkeypatch.setattr(_tools.shutil, "which", lambda _: "/usr/bin/npx")
    assert cwv._npx_available() is True
    monkeypatch.setattr(_tools.shutil, "which", lambda _: None)
    assert cwv._npx_available() is False


def test_run_returns_two_when_npx_missing(monkeypatch, isolated_cwd, capsys):
    monkeypatch.setattr(cwv, "_npx_available", lambda: False)
    rc = cwv.run()
    assert rc == 2  # could not run
    assert "npx is not available" in capsys.readouterr().out


def test_run_returns_two_when_url_missing(monkeypatch, isolated_cwd, capsys):
    monkeypatch.setattr(cwv, "_npx_available", lambda: True)
    monkeypatch.delenv("CWV_URL", raising=False)
    rc = cwv.run([])
    assert rc == 2
    assert "CWV target URL is required" in capsys.readouterr().out


def test_run_returns_two_when_explicit_config_missing(monkeypatch, isolated_cwd, capsys):
    """An explicit --config that doesn't exist still errors out. The
    default (no --config flag) resolves via templates and always finds
    something."""
    monkeypatch.setattr(cwv, "_npx_available", lambda: True)
    rc = cwv.run(["--url", "https://example.com", "--config", "missing.json"])
    assert rc == 2  # could not run
    assert "Lighthouse CI config not found" in capsys.readouterr().out


def test_run_invokes_lhci_and_writes_report(monkeypatch, isolated_cwd):
    captured: dict = {}

    def fake_run_lhci(cmd):
        captured["cmd"] = cmd
        # Drop a lhr JSON like a real lhci would.
        lhci = Path(".lighthouseci")
        lhci.mkdir()
        (lhci / "lhr-1.json").write_text(json.dumps(_sample_lhr()))
        return 0, "lhci output https://storage.googleapis.com/x.html"

    monkeypatch.setattr(cwv, "_npx_available", lambda: True)
    monkeypatch.setattr(cwv, "_run_lhci", fake_run_lhci)

    rc = cwv.run(["--url", "https://example.com"])
    assert rc == 0
    assert captured["cmd"][:3] == ["npx", "lhci", "autorun"]
    assert "--collect.url=https://example.com" in captured["cmd"]
    md = Path(cwv.REPORT_MD).read_text()
    assert "PASSED" in md
    assert "88/100" in md
    assert "Full Lighthouse Report" in md


def test_run_propagates_lhci_failure_but_still_writes_report(monkeypatch, isolated_cwd):
    def fake_run_lhci(cmd):
        lhci = Path(".lighthouseci")
        lhci.mkdir()
        (lhci / "lhr-1.json").write_text(json.dumps(_sample_lhr()))
        return 1, ""

    monkeypatch.setattr(cwv, "_npx_available", lambda: True)
    monkeypatch.setattr(cwv, "_run_lhci", fake_run_lhci)

    rc = cwv.run(["--url", "https://example.com"])
    assert rc == 1
    md = Path(cwv.REPORT_MD).read_text()
    assert "FAILED" in md


def test_run_accepts_explicit_config(monkeypatch, isolated_cwd):
    custom = isolated_cwd / "custom-lhci.json"
    custom.write_text("{}")

    captured: dict = {}

    def fake_run_lhci(cmd):
        captured["cmd"] = cmd
        lhci = Path(".lighthouseci")
        lhci.mkdir()
        (lhci / "lhr-1.json").write_text(json.dumps(_sample_lhr()))
        return 0, ""

    monkeypatch.setattr(cwv, "_npx_available", lambda: True)
    monkeypatch.setattr(cwv, "_run_lhci", fake_run_lhci)

    rc = cwv.run(["--url", "https://example.com", "--config", str(custom)])
    assert rc == 0
    assert f"--config={custom}" in captured["cmd"]


def test_an_lhci_exit_one_without_a_fresh_result_is_could_not_run(monkeypatch, isolated_cwd):
    """Chrome failed to launch: lhci exits 1, but audited nothing this run.
    A result left over from an earlier run doesn't count."""
    import os

    lhci = Path(".lighthouseci")
    lhci.mkdir()
    stale = lhci / "lhr-1.json"
    stale.write_text(json.dumps(_sample_lhr()))
    os.utime(stale, (1_000_000, 1_000_000))

    monkeypatch.setattr(cwv, "_npx_available", lambda: True)
    monkeypatch.setattr(cwv, "_run_lhci", lambda cmd: (1, "Unable to launch Chrome"))
    assert cwv.run(["--url", "https://example.com"]) == 2


def test_the_dev_config_skips_is_crawlable_and_the_prod_config_keeps_it():
    """Previews send X-Robots-Tag: noindex by design, which fails Lighthouse's
    is-crawlable audit and sinks SEO to ~0.6; in production noindex is a bug."""
    dev = json.loads(templates.lighthouserc(prod=False).read_text())
    prod = json.loads(templates.lighthouserc(prod=True).read_text())
    assert dev["ci"]["collect"]["settings"]["skipAudits"] == ["is-crawlable"]
    assert "skipAudits" not in prod["ci"]["collect"].get("settings", {})
    assert prod["ci"]["assert"]["assertions"]["categories:seo"] == dev["ci"]["assert"]["assertions"]["categories:seo"]
