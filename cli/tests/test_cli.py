"""Tests for the CLI dispatcher (cli.py)."""

from __future__ import annotations

import argparse
import subprocess
import sys

import pytest

from slopstopper import cli


def test_version_flag_prints_and_exits(capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(["--version"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "slopstopper" in out


def test_run_unknown_check_returns_2(capsys):
    rc = cli.main(["run", "no-such:check"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "unknown check" in err
    assert "known checks" in err


def test_bare_invocation_prints_banner_and_exits_zero(capsys):
    """No args → friendly banner + exit 0 (not an error)."""
    rc = cli.main([])
    assert rc == 0
    out = capsys.readouterr().out
    assert "slopstopper" in out
    assert "Commands:" in out
    assert "Quick start:" in out
    assert "run" in out
    assert "doctor" in out


# ── emit subcommand ──────────────────────────────────────────────


def test_emit_unknown_check_returns_2(capsys):
    rc = cli.main(["emit", "no-such:check", "--target", "pr-comment"])
    assert rc == 2
    assert "unknown check" in capsys.readouterr().err


def test_emit_no_meta_returns_2(monkeypatch, capsys):
    """When a check module is loaded but lacks META, dispatcher returns 2.

    Every shipped check has META as of PR 3, so this test patches one
    away to exercise the dispatcher's missing-META branch.
    """
    from slopstopper.checks import accessibility

    monkeypatch.setattr(accessibility, "META", None)
    rc = cli.main(["emit", "reliability:accessibility", "--target", "pr-comment"])
    assert rc == 2
    assert "has no META" in capsys.readouterr().err


def test_emit_routes_to_emit_module(monkeypatch):
    """When the check has META, the dispatcher hands off to emit.emit
    with the target, META dict, check_name, and on_pass kwargs."""
    called: dict = {}

    def fake_emit(target, meta, *, check_name=None, on_pass=None, status="fail"):
        called.update(
            target=target, meta=meta, check_name=check_name, on_pass=on_pass, status=status
        )
        return 0

    monkeypatch.setattr(cli.emit_mod, "emit", fake_emit)
    rc = cli.main(["emit", "hygiene:docs-size", "--target", "issue"])
    assert rc == 0
    assert called["target"] == "issue"
    assert called["check_name"] == "hygiene:docs-size"
    assert called["on_pass"] is None
    assert called["status"] == "fail", "default must not silently claim a pass"
    assert "comment_discriminator" in called["meta"]


def test_emit_threads_on_pass_close_through(monkeypatch):
    called: dict = {}

    def fake_emit(target, meta, *, check_name=None, on_pass=None, status="fail"):
        called["on_pass"] = on_pass
        return 0

    monkeypatch.setattr(cli.emit_mod, "emit", fake_emit)
    rc = cli.main(["emit", "hygiene:docs-size", "--target", "issue", "--on-pass=close"])
    assert rc == 0
    assert called["on_pass"] == "close"


def test_emit_threads_status_and_delete_through(monkeypatch):
    called: dict = {}

    def fake_emit(target, meta, *, check_name=None, on_pass=None, status="fail"):
        called.update(on_pass=on_pass, status=status)
        return 0

    monkeypatch.setattr(cli.emit_mod, "emit", fake_emit)
    rc = cli.main(
        [
            "emit", "hygiene:docs-size", "--target", "pr-comment",
            "--status", "pass", "--on-pass=delete",
        ]
    )
    assert rc == 0
    assert called == {"on_pass": "delete", "status": "pass"}


def test_emit_rejects_on_pass_close_with_pr_comment_target(capsys):
    rc = cli.main(["emit", "hygiene:docs-size", "--target", "pr-comment", "--on-pass=close"])
    assert rc == 2
    assert "--on-pass=close is only valid with --target issue" in capsys.readouterr().err


def test_emit_rejects_on_pass_delete_with_issue_target(capsys):
    rc = cli.main(["emit", "hygiene:docs-size", "--target", "issue", "--on-pass=delete"])
    assert rc == 2
    assert "--on-pass=delete is only valid with --target pr-comment" in capsys.readouterr().err


def test_summary_subcommand_routes_to_the_emitter(monkeypatch):
    called: dict = {}

    def fake_summary():
        called["ran"] = True
        return 0

    monkeypatch.setattr(cli.emit_mod, "emit_pr_summary", fake_summary)
    assert cli.main(["summary", "--target", "pr-comment"]) == 0
    assert called == {"ran": True}


def test_summary_requires_a_target(capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(["summary"])
    assert exc.value.code != 0


def test_emit_requires_target_flag(capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(["emit", "hygiene:docs-size"])
    assert exc.value.code != 0
    err = capsys.readouterr().err
    assert "required" in err or "--target" in err


# ── discover subcommand ───────────────────────────────────────────


def test_discover_prints_comma_joined_paths(monkeypatch, capsys):
    captured = {}

    def fake_discover(check, event):
        captured["check"] = check
        captured["event"] = event
        return ["/", "/blog", "/about"]

    monkeypatch.setattr(cli.discovery, "discover", fake_discover)
    rc = cli.main(["discover", "smoke", "--event", "pr"])
    assert rc == 0
    assert capsys.readouterr().out.strip() == "/,/blog,/about"
    assert captured == {"check": "smoke", "event": "pr"}


def test_discover_returns_2_when_no_paths(monkeypatch, capsys):
    monkeypatch.setattr(cli.discovery, "discover", lambda check, event: [])
    rc = cli.main(["discover", "broken_links", "--event", "main"])
    assert rc == 2
    assert capsys.readouterr().out == ""


def test_discover_accepts_hyphen_alias_for_broken_links(monkeypatch):
    """`broken-links` (hyphen, slopstopper-style) maps to the same
    underscore-form `broken_links` the discovery module expects."""
    captured = {}

    def fake_discover(check, event):
        captured["check"] = check
        return ["/"]

    monkeypatch.setattr(cli.discovery, "discover", fake_discover)
    rc = cli.main(["discover", "broken-links", "--event", "local"])
    assert rc == 0
    assert captured["check"] == "broken_links"


def test_discover_returns_1_on_internal_error(monkeypatch, capsys):
    def boom(check, event):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(cli.discovery, "discover", boom)
    rc = cli.main(["discover", "seo", "--event", "cron"])
    assert rc == 1
    assert "kaboom" in capsys.readouterr().err


def test_discover_rejects_unknown_check(capsys):
    with pytest.raises(SystemExit):
        cli.main(["discover", "nope", "--event", "local"])


def test_discover_default_event_is_local(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        cli.discovery,
        "discover",
        lambda check, event: captured.update({"event": event}) or ["/"],
    )
    rc = cli.main(["discover", "smoke"])
    assert rc == 0
    assert captured["event"] == "local"


# ── config get subcommand ─────────────────────────────────────────


def test_config_get_prints_scalar(monkeypatch, capsys):
    monkeypatch.setattr(cli.config, "get", lambda key, default: "https://example.com")
    rc = cli.main(["config", "get", "urls.production"])
    assert rc == 0
    assert capsys.readouterr().out.strip() == "https://example.com"


def test_config_get_prints_list_comma_joined(monkeypatch, capsys):
    monkeypatch.setattr(
        cli.config, "get", lambda key, default: ["ss-foo-check.yml", "ss-bar-check.yml"]
    )
    rc = cli.main(["config", "get", "workflows.disabled"])
    assert rc == 0
    assert capsys.readouterr().out.strip() == "ss-foo-check.yml,ss-bar-check.yml"


def test_config_get_uses_default_when_missing(monkeypatch, capsys):
    # config.get returns whatever default was passed; emulate the
    # "missing" path by returning the default directly.
    monkeypatch.setattr(cli.config, "get", lambda key, default: default)
    rc = cli.main(["config", "get", "no.such.key", "fallback"])
    assert rc == 0
    assert capsys.readouterr().out.strip() == "fallback"


def test_config_get_prints_empty_when_none(monkeypatch, capsys):
    monkeypatch.setattr(cli.config, "get", lambda key, default: None)
    rc = cli.main(["config", "get", "headers.source"])
    assert rc == 0
    assert capsys.readouterr().out == "\n"


# ── templates subcommand ──────────────────────────────────────────


def test_templates_list_prints_each_with_status(isolated_cwd, capsys):
    rc = cli.main(["templates", "list"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "playwright.config.js" in out
    assert "lighthouserc.json" in out
    assert "(bundled)" in out
    assert "(ejected" not in out


def test_templates_list_marks_ejected(isolated_cwd, capsys):
    override = isolated_cwd / ".ss" / "lighthouserc.json"
    override.parent.mkdir(parents=True, exist_ok=True)
    override.write_text("{}")
    rc = cli.main(["templates", "list"])
    assert rc == 0
    out = capsys.readouterr().out
    # The ejected entry carries the override marker; others remain bundled.
    assert "lighthouserc.json  (ejected" in out
    assert "playwright.config.js  (bundled)" in out


def test_templates_path_prints_resolved(isolated_cwd, capsys):
    rc = cli.main(["templates", "path", "lighthouserc.json"])
    assert rc == 0
    out = capsys.readouterr().out.strip()
    assert out.endswith("lighthouserc.json")


def test_templates_path_unknown_returns_2(isolated_cwd, capsys):
    rc = cli.main(["templates", "path", "not-a-template"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "unknown template" in err
    assert "known templates" in err


def test_templates_eject_creates_override(isolated_cwd, capsys):
    rc = cli.main(["templates", "eject", "lighthouserc.json"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "ejected" in out
    assert (isolated_cwd / ".ss" / "lighthouserc.json").exists()


def test_templates_eject_no_clobber(isolated_cwd, capsys):
    override = isolated_cwd / ".ss" / "lighthouserc.json"
    override.parent.mkdir(parents=True, exist_ok=True)
    override.write_text("{ \"keep_me\": true }")
    rc = cli.main(["templates", "eject", "lighthouserc.json"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "already exists" in out
    assert override.read_text() == "{ \"keep_me\": true }"


def test_templates_eject_unknown_returns_2(isolated_cwd, capsys):
    rc = cli.main(["templates", "eject", "not-a-template"])
    assert rc == 2
    assert "unknown template" in capsys.readouterr().err


# ── serve subcommand ──────────────────────────────────────────────


def test_serve_returns_1_when_node_missing(monkeypatch, isolated_cwd, capsys):
    monkeypatch.setattr(cli.shutil, "which", lambda _: None)
    rc = cli.main(["serve"])
    assert rc == 1
    assert "node is not available" in capsys.readouterr().err


def test_serve_execs_node_with_resolved_server_js(monkeypatch, isolated_cwd):
    captured: dict = {}

    def fake_execvp(file, args):
        captured["file"] = file
        captured["args"] = args
        # execvp doesn't return on success. To make the test path return,
        # we just don't raise. _dispatch_serve will fall through to its
        # "unreachable" return 1.

    monkeypatch.setattr(cli.shutil, "which", lambda _: "/usr/bin/node")
    monkeypatch.setattr(cli.os, "execvp", fake_execvp)

    cli.main(["serve"])
    assert captured["file"] == "node"
    assert captured["args"][0] == "node"
    # Should resolve to the bundled server.js (no .ss/ override in the
    # isolated cwd).
    assert captured["args"][1].endswith("server.js")


def test_serve_prefers_ss_override(monkeypatch, isolated_cwd):
    override = isolated_cwd / ".ss" / "server.js"
    override.parent.mkdir(parents=True, exist_ok=True)
    override.write_text("// custom")

    captured: dict = {}

    def fake_execvp(file, args):
        captured["args"] = args

    monkeypatch.setattr(cli.shutil, "which", lambda _: "/usr/bin/node")
    monkeypatch.setattr(cli.os, "execvp", fake_execvp)

    cli.main(["serve"])
    # Resolved path should be the override, not the bundled file.
    from pathlib import Path
    assert Path(captured["args"][1]) == Path(".ss/server.js")


def test_config_get_default_default_is_empty_string(monkeypatch, capsys):
    """Matches the bash load_config.py shim: missing key + no default = ''"""
    captured = {}
    monkeypatch.setattr(
        cli.config,
        "get",
        lambda key, default: captured.update({"default": default}) or default,
    )
    rc = cli.main(["config", "get", "no.such.key"])
    assert rc == 0
    assert captured["default"] == ""
    assert capsys.readouterr().out == "\n"


# ── checks list subcommand ────────────────────────────────────────


def test_checks_list_prints_every_registered_check(isolated_cwd, capsys):
    rc = cli.main(["checks", "list"])
    assert rc == 0
    out = capsys.readouterr().out
    # Spot-check a few known names from REGISTRY.
    assert "hygiene:docs-size" in out
    assert "security:secrets" in out
    assert "reliability:cwv" in out


def test_checks_list_filters_by_category(isolated_cwd, capsys):
    rc = cli.main(["checks", "list", "--category", "security"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "security:secrets" in out
    # Hygiene checks shouldn't appear under --category=security.
    assert "hygiene:docs-size" not in out


def test_checks_list_json_emits_machine_readable(isolated_cwd, capsys):
    import json
    rc = cli.main(["checks", "list", "--json"])
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert isinstance(data, list)
    names = {e["name"] for e in data}
    assert "hygiene:docs-size" in names
    assert all("summary" in e for e in data)


def test_checks_list_empty_category_says_so(isolated_cwd, capsys, monkeypatch):
    # Force the filtered list to be empty by patching the registry view.
    monkeypatch.setattr(cli, "REGISTRY", {})
    rc = cli.main(["checks", "list", "--category", "security"])
    assert rc == 0
    assert "No checks registered" in capsys.readouterr().out


# ── doctor subcommand ─────────────────────────────────────────────


def _no_github(monkeypatch, rows=None):
    """Keep doctor off the network: GitHub reports `rows` (None = unreachable)."""
    monkeypatch.setattr(cli.workflow_state, "disabled_ss_workflows", lambda: rows)


def test_doctor_passes_when_all_tools_present(isolated_cwd, capsys, monkeypatch):
    monkeypatch.setattr(cli.shutil, "which", lambda _: "/usr/bin/stub")
    monkeypatch.setattr(cli, "_probe_tool", lambda _: (True, "stub 1.0"))
    _no_github(monkeypatch)
    rc = cli.main(["doctor"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "All required tools available" in out
    # Each tool from _DOCTOR_TOOLS should be listed.
    for tool, _, _ in cli._DOCTOR_TOOLS:
        assert tool in out


def test_doctor_fails_when_required_tool_missing(isolated_cwd, capsys, monkeypatch):
    """Missing snyk with security:dependencies enabled → exit 1."""
    monkeypatch.setattr(cli.shutil, "which", lambda tool: None if tool == "snyk" else "/x")
    monkeypatch.setattr(cli, "_probe_tool", lambda _: (True, ""))
    _no_github(monkeypatch)
    monkeypatch.setattr(cli, "_disabled_workflows", lambda: set())
    rc = cli.main(["doctor"])
    assert rc == 1
    out = capsys.readouterr().out
    assert "snyk" in out
    assert "not installed" in out
    assert "required tool(s) missing" in out


def test_doctor_skips_disabled_check_tools(isolated_cwd, capsys, monkeypatch):
    """Missing gitleaks is fine if this repo doesn't carry security:secrets."""
    monkeypatch.setattr(
        cli.shutil, "which", lambda tool: None if tool == "gitleaks" else "/x"
    )
    monkeypatch.setattr(cli, "_probe_tool", lambda _: (True, ""))
    _no_github(monkeypatch)
    monkeypatch.setattr(
        cli, "_disabled_workflows", lambda: {"ss-security-secrets-check.yml"}
    )
    rc = cli.main(["doctor"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "gitleaks" in out
    assert "doesn't carry" in out
    assert "workflows.disabled" in out


def test_doctor_skips_tools_for_a_check_the_profile_drops(
    write_config, capsys, monkeypatch
):
    """A check listed under workflows.disabled drops its tool from doctor.

    Exercises the real resolution path rather than a patched
    `_disabled_workflows`, through the explicit check → workflow map.
    """
    write_config("profile: library\nworkflows:\n  disabled: [ss-security-secrets-check.yml]\n")
    monkeypatch.setattr(
        cli.shutil,
        "which",
        lambda tool: None if tool == "gitleaks" else "/x",
    )
    monkeypatch.setattr(cli, "_probe_tool", lambda _: (True, ""))
    _no_github(monkeypatch)
    rc = cli.main(["doctor"])
    assert rc == 0, capsys.readouterr().out
    out = capsys.readouterr().out
    assert "profile: library" in out


def test_doctor_fails_when_node_or_gh_missing(isolated_cwd, capsys, monkeypatch):
    """node and gh are needed by the CLI itself (no disabled-bypass)."""
    monkeypatch.setattr(cli.shutil, "which", lambda tool: None if tool == "node" else "/x")
    monkeypatch.setattr(cli, "_probe_tool", lambda _: (True, ""))
    _no_github(monkeypatch)
    rc = cli.main(["doctor"])
    assert rc == 1
    out = capsys.readouterr().out
    assert "node" in out
    assert "the CLI itself" in out


def test_doctor_fails_when_a_tool_resolves_but_does_not_run(isolated_cwd, capsys, monkeypatch):
    """A stale mise shim is on PATH but can't run: that's broken, not installed."""
    monkeypatch.setattr(cli.shutil, "which", lambda tool: f"/shims/{tool}")
    monkeypatch.setattr(
        cli, "_probe_tool",
        lambda tool: (False, "mise ERROR snyk is not a valid shim") if tool == "snyk" else (True, ""),
    )
    monkeypatch.setattr(cli, "_disabled_workflows", lambda: set())
    _no_github(monkeypatch)
    rc = cli.main(["doctor"])
    assert rc == 1
    out = capsys.readouterr().out
    assert "snyk       found at /shims/snyk but does not run" in out
    assert "not a valid shim" in out
    assert "mise reshim" in out
    assert "missing or broken" in out


def test_probe_tool_reports_a_nonzero_exit_as_not_running(monkeypatch):
    class Result:
        returncode = 1
        stdout = ""
        stderr = "mise ERROR snyk is not a valid shim\nmise ERROR Run with --verbose\n"

    monkeypatch.setattr(cli.subprocess, "run", lambda *a, **k: Result())
    assert cli._probe_tool("snyk") == (False, "mise ERROR snyk is not a valid shim")


def test_probe_tool_returns_the_version_line(monkeypatch):
    class Result:
        returncode = 0
        stdout = "1.90.0\nextra\n"
        stderr = ""

    monkeypatch.setattr(cli.subprocess, "run", lambda *a, **k: Result())
    assert cli._probe_tool("snyk") == (True, "1.90.0")


def test_doctor_fails_when_an_ss_workflow_is_disabled_for_inactivity(isolated_cwd, capsys, monkeypatch):
    monkeypatch.setattr(cli.shutil, "which", lambda _: "/x")
    monkeypatch.setattr(cli, "_probe_tool", lambda _: (True, ""))
    _no_github(monkeypatch, [{
        "id": 292375157, "name": "SlopStopper · Reliability Smoke Tests",
        "path": ".github/workflows/ss-reliability-smoke-tests.yml",
        "state": "disabled_inactivity",
    }])
    rc = cli.main(["doctor"])
    assert rc == 1
    out = capsys.readouterr().out
    assert "Reliability Smoke Tests: disabled_inactivity" in out
    assert "gh workflow enable 292375157" in out
    assert "60 days" in out


def test_doctor_warns_but_passes_on_a_manually_disabled_workflow(isolated_cwd, capsys, monkeypatch):
    monkeypatch.setattr(cli.shutil, "which", lambda _: "/x")
    monkeypatch.setattr(cli, "_probe_tool", lambda _: (True, ""))
    _no_github(monkeypatch, [{
        "id": 1, "name": "SlopStopper · SAST Analysis",
        "path": ".github/workflows/ss-security-sast-check.yml", "state": "disabled_manually",
    }])
    assert cli.main(["doctor"]) == 0
    assert "gh workflow enable 1" in capsys.readouterr().out


def test_doctor_skips_the_workflow_check_when_github_is_unreachable(isolated_cwd, capsys, monkeypatch):
    monkeypatch.setattr(cli.shutil, "which", lambda _: "/x")
    monkeypatch.setattr(cli, "_probe_tool", lambda _: (True, ""))
    _no_github(monkeypatch, None)
    assert cli.main(["doctor"]) == 0
    assert "GitHub can't be asked" in capsys.readouterr().out


# ── --quiet global flag ───────────────────────────────────────────


def test_quiet_flag_suppresses_check_output(isolated_cwd, capsys, monkeypatch):
    """--quiet flips output.QUIET so check decorations vanish."""
    from slopstopper import output as out_mod

    seen = {}

    def fake_run(_args):
        out_mod.running("would be hidden")
        out_mod.success("would be hidden")
        seen["quiet"] = out_mod.QUIET
        return 0

    monkeypatch.setitem(cli.REGISTRY, "hygiene:test-fake", fake_run)
    rc = cli.main(["--quiet", "run", "hygiene:test-fake"])
    assert rc == 0
    assert seen["quiet"] is True
    # No "would be hidden" lines on stdout.
    assert "would be hidden" not in capsys.readouterr().out


def test_quiet_flag_does_not_suppress_errors(isolated_cwd, capsys, monkeypatch):
    """--quiet still lets errors through (they're load-bearing)."""
    from slopstopper import output as out_mod

    def fake_run(_args):
        out_mod.error("critical error must show")
        return 1

    monkeypatch.setitem(cli.REGISTRY, "hygiene:test-fake-err", fake_run)
    rc = cli.main(["--quiet", "run", "hygiene:test-fake-err"])
    assert rc == 1
    assert "critical error must show" in capsys.readouterr().out


def test_quiet_default_is_off(isolated_cwd, capsys, monkeypatch):
    """Without --quiet, the QUIET flag stays False."""
    from slopstopper import output as out_mod

    seen = {}

    def fake_run(_args):
        seen["quiet"] = out_mod.QUIET
        return 0

    monkeypatch.setitem(cli.REGISTRY, "hygiene:test-fake-default", fake_run)
    cli.main(["run", "hygiene:test-fake-default"])
    assert seen["quiet"] is False



def test_a_crashing_check_exits_two_not_one(isolated_cwd, capsys, monkeypatch):
    """An uncaught exception defaults to exit 1, "the repo failed". A crash
    in the check is "could not run", so no tracking issue gets opened."""

    def fake_run(_args):
        raise AttributeError("'NoneType' object has no attribute 'get'")

    monkeypatch.setitem(cli.REGISTRY, "hygiene:test-fake-crash", fake_run)
    assert cli.main(["run", "hygiene:test-fake-crash"]) == 2
    err = capsys.readouterr().err
    assert "AttributeError" in err  # the traceback is still shown
    assert "could not run" in err


# ── badges subcommand ────────────────────────────────────────────


def test_badges_threads_flags_through(monkeypatch, capsys):
    """Smoke test: badges routes args.owner / args.repo / args.no_advert to
    badges.generate and prints the result."""
    called: dict = {}

    def fake_generate(owner=None, repo=None, no_advert=False):
        called["owner"] = owner
        called["repo"] = repo
        called["no_advert"] = no_advert
        return "## Pipeline status\n\n(stub)\n"

    monkeypatch.setattr(cli.badges_mod, "generate", fake_generate)
    rc = cli.main(["badges", "--owner", "acme", "--repo", "widget", "--no-advert"])
    assert rc == 0
    assert called == {"owner": "acme", "repo": "widget", "no_advert": True}
    assert "## Pipeline status" in capsys.readouterr().out


def test_badges_surfaces_generate_value_error(monkeypatch, capsys):
    def fake_generate(owner=None, repo=None, no_advert=False):
        raise ValueError("Could not detect owner/repo")

    monkeypatch.setattr(cli.badges_mod, "generate", fake_generate)
    rc = cli.main(["badges"])
    assert rc == 2
    assert "Could not detect owner/repo" in capsys.readouterr().err


# ── every subcommand parses ─────────────────────────────────────
#
# The parser is the source of the list, so a new subcommand is covered the
# moment it is added: `--help` at every level must exit 0 with a usage
# line, and every top-level command must have a dispatcher, or `main()`
# would parse it and then fall through to "unknown command".


def _command_paths(parser, prefix=()):
    """Every subcommand path the parser accepts, depth first."""
    paths = []
    for action in parser._actions:
        if not isinstance(action, argparse._SubParsersAction):
            continue
        for name, sub in action.choices.items():
            path = (*prefix, name)
            paths.append(path)
            paths.extend(_command_paths(sub, path))
    return paths


COMMAND_PATHS = _command_paths(cli._build_parser())


def test_the_parser_walk_finds_the_nested_commands():
    assert ("run",) in COMMAND_PATHS
    assert ("templates", "eject") in COMMAND_PATHS
    assert ("profile", "detect") in COMMAND_PATHS
    assert ("checks", "list") in COMMAND_PATHS


@pytest.mark.parametrize("path", COMMAND_PATHS, ids=" ".join)
def test_every_subcommand_help_exits_zero(path, capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main([*path, "--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert out.startswith("usage: slopstopper " + " ".join(path)), out.splitlines()[0]


def test_every_top_level_command_has_a_dispatcher():
    top = {path[0] for path in COMMAND_PATHS}
    assert top == set(cli._DISPATCHERS), (
        f"commands without a dispatcher: {sorted(top - set(cli._DISPATCHERS))}; "
        f"dispatchers without a command: {sorted(set(cli._DISPATCHERS) - top)}"
    )


# ── --json: the machine-readable contract ────────────────────────
#
# docs/contributing/README.md documents these shapes next to the exit
# codes. Adding a key is fine; renaming or dropping one breaks adopters'
# scripts, which is what these tests are for.


def test_run_json_prints_one_object_and_sends_the_check_output_to_stderr(isolated_cwd, capsys, monkeypatch):
    import json

    def fake_run(_args):
        print("chatter the check prints")
        return 0

    monkeypatch.setitem(cli.REGISTRY, "hygiene:test-fake-json", fake_run)
    assert cli.main(["run", "--json", "hygiene:test-fake-json"]) == 0
    captured = capsys.readouterr()
    data = json.loads(captured.out)
    # Required keys and values; an added key is allowed by the contract.
    assert data["check"] == "hygiene:test-fake-json"
    assert data["exit_code"] == 0
    assert data["status"] == "pass"
    assert data["failures"] == []
    assert data["reports"] == []
    assert "chatter" in captured.err


def test_run_json_flag_after_the_check_name_also_works(isolated_cwd, capsys, monkeypatch):
    import json

    monkeypatch.setitem(cli.REGISTRY, "hygiene:test-fake-json", lambda args: 1 if args == ["--url", "u"] else 2)
    assert cli.main(["run", "hygiene:test-fake-json", "--json", "--", "--url", "u"]) == 1
    data = json.loads(capsys.readouterr().out)
    assert data["status"] == "fail"
    assert data["exit_code"] == 1


def test_run_json_lists_the_report_failures_and_the_files_written(isolated_cwd, capsys, monkeypatch):
    import json
    from pathlib import Path

    report_dir = Path(".ss/reports/fake")

    def fake_run(_args):
        report_dir.mkdir(parents=True)
        (report_dir / "fake-report.md").write_text(
            "# Fake\n\n**Issues:**\n- ❌ README.md is over budget\n- ❌ docs/x.md is unrouted\n"
        )
        (report_dir / "fake-report.json").write_text("{}")
        return 1

    monkeypatch.setitem(cli.REGISTRY, "hygiene:test-fake-json", fake_run)
    monkeypatch.setattr(cli, "_check_meta", lambda name: {"report_path": str(report_dir / "fake-report.md")})
    assert cli.main(["run", "--json", "hygiene:test-fake-json"]) == 1
    data = json.loads(capsys.readouterr().out)
    assert data["status"] == "fail"
    assert data["failures"] == ["README.md is over budget", "docs/x.md is unrouted"]
    assert data["reports"] == [
        str(report_dir / "fake-report.json"),
        str(report_dir / "fake-report.md"),
    ]


def test_run_json_reports_a_crash_as_cannot_run(isolated_cwd, capsys, monkeypatch):
    import json

    def fake_run(_args):
        raise RuntimeError("boom")

    monkeypatch.setitem(cli.REGISTRY, "hygiene:test-fake-json", fake_run)
    assert cli.main(["run", "--json", "hygiene:test-fake-json"]) == 2
    captured = capsys.readouterr()
    assert json.loads(captured.out)["status"] == "cannot-run"
    assert "RuntimeError" in captured.err


def test_run_json_unknown_check_is_cannot_run_with_an_error(isolated_cwd, capsys):
    import json

    assert cli.main(["run", "--json", "hygiene:no-such-check"]) == 2
    data = json.loads(capsys.readouterr().out)
    assert data["status"] == "cannot-run"
    assert data["error"] == "unknown check"


def test_run_json_keeps_stdout_clean_of_subprocess_output(isolated_cwd, capfd, monkeypatch):
    """The scanner and browser wrappers inherit fd 1, not sys.stdout, so
    this captures at the file-descriptor level: a regression of the fd
    swap would put the child's line on stdout."""
    import json
    import subprocess

    def fake_run(_args):
        subprocess.run([sys.executable, "-c", "print('from a child process')"], check=False)
        return 0

    monkeypatch.setitem(cli.REGISTRY, "hygiene:test-fake-json", fake_run)
    assert cli.main(["run", "--json", "hygiene:test-fake-json"]) == 0
    out, err = capfd.readouterr()
    assert "from a child process" not in out
    assert "from a child process" in err
    json.loads(out)


def test_run_json_ignores_a_report_an_earlier_run_left_behind(isolated_cwd, capsys, monkeypatch):
    """A check that exits 2 before writing (a missing scanner, say) must
    not report last time's failures as this run's."""
    import json
    from pathlib import Path

    report_dir = Path(".ss/reports/fake")
    report_dir.mkdir(parents=True)
    (report_dir / "fake-report.md").write_text("**Issues:**\n- ❌ from last time\n")

    monkeypatch.setitem(cli.REGISTRY, "hygiene:test-fake-json", lambda args: 2)
    monkeypatch.setattr(cli, "_check_meta", lambda name: {"report_path": str(report_dir / "fake-report.md")})
    assert cli.main(["run", "--json", "hygiene:test-fake-json"]) == 2
    data = json.loads(capsys.readouterr().out)
    assert data["status"] == "cannot-run"
    assert data["failures"] == []
    assert data["reports"] == []


def test_doctor_json_when_everything_is_present(isolated_cwd, capsys, monkeypatch):
    import json

    monkeypatch.setattr(cli.shutil, "which", lambda _: "/usr/bin/stub")
    monkeypatch.setattr(cli, "_probe_tool", lambda _: (True, "stub 1.0"))
    _no_github(monkeypatch, rows=[])
    assert cli.main(["doctor", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["ok"] is True
    assert data["exit_code"] == 0
    assert [row["tool"] for row in data["tools"]] == [tool for tool, _, _ in cli._DOCTOR_TOOLS]
    assert all(row["status"] == "ok" and row["version"] == "stub 1.0" for row in data["tools"])
    assert data["workflows"] == {"checked": True, "disabled": []}


def test_doctor_json_names_the_missing_tool_and_its_hint(isolated_cwd, capsys, monkeypatch):
    import json

    monkeypatch.setattr(cli.shutil, "which", lambda tool: None if tool == "snyk" else "/x")
    monkeypatch.setattr(cli, "_probe_tool", lambda _: (True, ""))
    _no_github(monkeypatch)
    monkeypatch.setattr(cli, "_disabled_workflows", lambda: set())
    assert cli.main(["doctor", "--json"]) == 1
    data = json.loads(capsys.readouterr().out)
    assert data["ok"] is False
    snyk = next(row for row in data["tools"] if row["tool"] == "snyk")
    assert snyk["status"] == "missing"
    assert snyk["needed_by"] == "security:sast, security:dependencies, security:iac, security:sbom"
    assert snyk["problem"] == "not installed"
    assert "snyk" in snyk["hint"]
    assert data["workflows"]["checked"] is False


def test_doctor_json_marks_a_tool_only_a_dropped_check_needs_as_skipped(isolated_cwd, capsys, monkeypatch):
    import json

    monkeypatch.setattr(cli.shutil, "which", lambda tool: None if tool == "gitleaks" else "/x")
    monkeypatch.setattr(cli, "_probe_tool", lambda _: (True, ""))
    _no_github(monkeypatch)
    monkeypatch.setattr(cli, "_check_is_disabled", lambda name, disabled: name == "security:secrets")
    assert cli.main(["doctor", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    gitleaks = next(row for row in data["tools"] if row["tool"] == "gitleaks")
    assert gitleaks["status"] == "skipped"


def test_doctor_keeps_snyk_required_while_any_snyk_check_is_on(isolated_cwd, capsys, monkeypatch):
    """snyk serves four checks; disabling one of them does not make it optional."""
    import json

    monkeypatch.setattr(cli.shutil, "which", lambda tool: None if tool == "snyk" else "/x")
    monkeypatch.setattr(cli, "_probe_tool", lambda _: (True, ""))
    _no_github(monkeypatch)
    monkeypatch.setattr(cli, "_check_is_disabled", lambda name, disabled: name == "security:dependencies")
    assert cli.main(["doctor", "--json"]) == 1
    data = json.loads(capsys.readouterr().out)
    assert next(row for row in data["tools"] if row["tool"] == "snyk")["status"] == "missing"
    monkeypatch.setattr(cli, "_check_is_disabled", lambda name, disabled: name.startswith("security:"))
    assert cli.main(["doctor", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert next(row for row in data["tools"] if row["tool"] == "snyk")["status"] == "skipped"


def test_monitor_runs_snyk_with_the_shared_org_and_environment(write_config, monkeypatch, capsys):
    from slopstopper.checks import _snyk

    write_config("security:\n  snyk:\n    org: acme\n")
    monkeypatch.delenv("SNYK_ORG", raising=False)
    monkeypatch.setenv("SNYK_API", "")
    monkeypatch.setattr(_snyk, "available", lambda: True)
    seen = {}

    def fake_run(cmd, env, check):
        seen["cmd"], seen["env"] = cmd, env
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(cli.subprocess, "run", fake_run)
    assert cli.main(["monitor"]) == 0
    assert seen["cmd"] == ["snyk", "monitor", "--all-projects", "--org=acme"]
    assert "SNYK_API" not in seen["env"] and seen["env"]["SNYK_DISABLE_ANALYTICS"] == "1"
    assert "Snyk accepted the snapshot" in capsys.readouterr().out


def test_monitor_is_could_not_run_when_snyk_rejects_it(isolated_cwd, monkeypatch, capsys):
    from slopstopper.checks import _snyk

    monkeypatch.setattr(_snyk, "available", lambda: True)
    monkeypatch.setattr(cli.subprocess, "run", lambda cmd, env, check: subprocess.CompletedProcess(cmd, 2))
    assert cli.main(["monitor"]) == 2
    assert "dashboard was not updated" in capsys.readouterr().out


def test_monitor_is_could_not_run_without_snyk(isolated_cwd, monkeypatch, capsys):
    from slopstopper.checks import _snyk

    monkeypatch.setattr(_snyk, "available", lambda: False)
    assert cli.main(["monitor"]) == 2
    assert "snyk is not installed" in capsys.readouterr().out


def test_doctor_json_reports_an_inactive_workflow(isolated_cwd, capsys, monkeypatch):
    import json

    monkeypatch.setattr(cli.shutil, "which", lambda _: "/x")
    monkeypatch.setattr(cli, "_probe_tool", lambda _: (True, ""))
    _no_github(monkeypatch, rows=[{"name": "ss-x.yml", "state": cli.workflow_state.INACTIVITY, "id": "1"}])
    assert cli.main(["doctor", "--json"]) == 1
    data = json.loads(capsys.readouterr().out)
    (row,) = data["workflows"]["disabled"]
    assert row["name"] == "ss-x.yml"
    assert row["inactive"] is True
    assert row["fix"]


def test_config_get_json_keeps_a_list_a_list(monkeypatch, capsys):
    import json

    monkeypatch.setattr(cli.config, "get", lambda key, default=None: ["a.yml", "b.yml"])
    assert cli.main(["config", "get", "workflows.disabled", "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == ["a.yml", "b.yml"]


def test_config_get_json_unset_is_null_or_the_default(monkeypatch, capsys):
    import json

    monkeypatch.setattr(cli.config, "get", lambda key, default=None: None)
    assert cli.main(["config", "get", "urls.production", "--json"]) == 0
    assert json.loads(capsys.readouterr().out) is None
    assert cli.main(["config", "get", "urls.production", "http://x", "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == "http://x"
    # An explicit empty fallback is a supplied default, not an omitted one.
    assert cli.main(["config", "get", "urls.production", "", "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == ""
