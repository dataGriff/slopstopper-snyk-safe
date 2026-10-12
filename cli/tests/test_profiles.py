"""Tests for project-shape profiles (profiles.py) and their CLI surface.

The profile mechanism decides which workflows a repo carries, so the
cases that matter are the resolution rules: an unset key behaves like
`ui` (no existing install changes shape on upgrade), explicit config
beats the preset in both directions, and a typo falls back to the
default rather than silently disabling a different set.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from slopstopper import cli, config, profiles

REPO_ROOT = Path(__file__).resolve().parents[2]


DATA_PATH = Path(profiles.__file__).resolve().parent / "data" / "profiles.json"


@pytest.fixture(autouse=True)
def _fresh_profile_cache():
    """profiles.load() caches the JSON table at module level."""
    profiles.reload()
    yield
    profiles.reload()


# ── the data file ────────────────────────────────────────────────


def test_data_file_is_valid_json_with_a_default_that_exists():
    table = json.loads(DATA_PATH.read_text())
    assert table["default"] in table["profiles"]


def test_default_profile_disables_nothing():
    """`ui` is the fallback for an unset key, so it must be a no-op."""
    assert profiles.expand(profiles.default_name()) == []


def test_every_profile_disables_only_real_workflows():
    """A typo in profiles.json would silently fail to disable anything."""
    repo_root = Path(__file__).resolve().parents[2]
    on_disk = {p.name for p in (repo_root / ".github" / "workflows").glob("ss-*.yml")}
    for name in profiles.names():
        for workflow in profiles.expand(name) or []:
            assert workflow in on_disk, f"profile {name!r} names missing {workflow}"


def test_names_lists_the_default_first():
    assert profiles.names()[0] == profiles.default_name()


def test_expand_unknown_profile_returns_none():
    assert profiles.expand("no-such-profile") is None


def test_api_profile_drops_browser_checks_but_keeps_the_snyk_checks():
    """The Snyk checks scan source and manifests, so they belong on every
    shape of repo; only the browser-and-SEO checks go."""
    disables = profiles.expand("api")
    assert "ss-reliability-core-web-vitals.yml" in disables
    assert "ss-reliability-seo-check.yml" in disables
    assert "ss-security-sast-check.yml" not in disables
    assert "ss-security-dependencies-check.yml" not in disables
    assert "ss-security-iac-check.yml" not in disables
    assert "ss-security-sbom-check.yml" not in disables


def test_library_profile_is_a_superset_of_api():
    assert set(profiles.expand("api")) <= set(profiles.expand("library"))


# ── resolution rules ─────────────────────────────────────────────


def test_unset_profile_resolves_to_the_default(write_config):
    write_config("urls:\n  production:\n")
    assert profiles.active_name() == profiles.default_name()
    assert profiles.effective_disabled() == set(profiles.without_api("ui"))
    assert profiles.validate() is None


def test_unknown_profile_falls_back_to_default_and_warns(write_config):
    write_config("profile: rest-api\n")
    assert profiles.active_name() == profiles.default_name()
    assert profiles.effective_disabled() == set(profiles.without_api("ui"))
    message = profiles.validate()
    assert message is not None
    assert "rest-api" in message


def test_profile_expands_into_the_disabled_set(write_config):
    write_config("profile: api\n")
    assert profiles.effective_disabled() == set(profiles.expand("api"))


def test_workflows_disabled_adds_to_the_profile_set(write_config):
    write_config("profile: api\nworkflows:\n  disabled: [ss-hygiene-complexity-check.yml]\n")
    disabled = profiles.effective_disabled()
    assert "ss-hygiene-complexity-check.yml" in disabled
    assert "ss-reliability-seo-check.yml" in disabled


def test_workflows_enabled_takes_a_check_back_out(write_config):
    write_config("profile: api\nworkflows:\n  enabled: [ss-reliability-seo-check.yml]\n")
    disabled = profiles.effective_disabled()
    assert "ss-reliability-seo-check.yml" not in disabled
    assert "ss-reliability-sitemap-check.yml" in disabled


def test_enabled_accepts_names_without_the_yml_extension(write_config):
    write_config("profile: api\nworkflows:\n  enabled: [ss-reliability-seo-check]\n")
    assert "ss-reliability-seo-check.yml" not in profiles.effective_disabled()


def test_disabled_keeps_the_doc_updaters_md_source_name(write_config):
    """`.md` is a shipped workflow extension (the gh-aw source), so it must not become `.md.yml` (#452)."""
    write_config(
        "workflows:\n"
        "  disabled: [ss-hygiene-doc-updater.md, ss-hygiene-doc-updater.lock.yml, ss-hygiene-complexity-check]\n"
    )
    disabled = profiles.effective_disabled()
    assert "ss-hygiene-doc-updater.md" in disabled
    assert "ss-hygiene-doc-updater.lock.yml" in disabled
    assert "ss-hygiene-complexity-check.yml" in disabled
    assert not any(name.endswith(".md.yml") for name in disabled)


def test_disabled_keeps_any_name_that_already_has_an_extension(write_config):
    """The rule is "has an extension", not an allowlist: an adopter-added
    `custom.yaml` must stay `custom.yaml` so the installer's sweep can
    remove it (#452)."""
    write_config("workflows:\n  disabled: [custom.yaml, ss-x.yml, ss-y]\n")
    assert profiles.effective_disabled() >= {"custom.yaml", "ss-x.yml", "ss-y.yml"}
    assert not any(name.endswith(".yaml.yml") for name in profiles.effective_disabled())


def test_explicit_disable_wins_over_explicit_enable(write_config):
    write_config(
        "workflows:\n"
        "  disabled: [ss-reliability-seo-check.yml]\n"
        "  enabled: [ss-reliability-seo-check.yml]\n"
    )
    assert "ss-reliability-seo-check.yml" in profiles.effective_disabled()


def test_malformed_workflows_lists_are_ignored_not_fatal(write_config):
    write_config("profile: api\nworkflows:\n  enabled: not-a-list\n")
    assert profiles.effective_disabled() == set(profiles.expand("api"))


# ── check → workflow mapping ─────────────────────────────────────


def test_every_registered_check_maps_to_a_workflow_on_disk():
    """The four non-conventional filenames are why the map is explicit."""
    from slopstopper.checks import REGISTRY

    repo_root = Path(__file__).resolve().parents[2]
    on_disk = {p.name for p in (repo_root / ".github" / "workflows").glob("ss-*.yml")}
    for check in REGISTRY:
        workflow = profiles.workflow_for(check)
        assert workflow is not None, f"{check} has no workflow mapping"
        assert workflow in on_disk, f"{check} maps to missing {workflow}"


@pytest.mark.parametrize(
    "check,workflow",
    [
        ("reliability:cwv", "ss-reliability-core-web-vitals.yml"),
        ("reliability:smoke", "ss-reliability-smoke-tests.yml"),
        ("security:sbom", "ss-security-sbom-check.yml"),
        ("hygiene:csp-exceptions", "ss-hygiene-csp-exceptions-check.yml"),
    ],
)
def test_names_that_dont_follow_the_filename_convention(check, workflow):
    assert profiles.workflow_for(check) == workflow


def test_check_is_disabled_follows_the_profile(write_config):
    write_config("profile: api\n")
    assert profiles.check_is_disabled("reliability:cwv")
    assert not profiles.check_is_disabled("security:iac")
    assert not profiles.check_is_disabled("no-such:check")


# ── shape detection ──────────────────────────────────────────────


def test_detect_reads_a_web_repo_as_ui(isolated_cwd):
    (isolated_cwd / "public").mkdir()
    (isolated_cwd / "public" / "index.html").write_text("<html></html>")
    name, reason = profiles.detect(isolated_cwd)
    assert name == "ui"
    assert "index.html" in reason


def test_detect_reads_an_openapi_repo_as_api(isolated_cwd):
    (isolated_cwd / "openapi.yaml").write_text("openapi: 3.0.0\n")
    name, _ = profiles.detect(isolated_cwd)
    assert name == "api"


def test_detect_reads_a_server_framework_dependency_as_api(isolated_cwd):
    (isolated_cwd / "pyproject.toml").write_text('dependencies = ["fastapi>=0.110"]\n')
    name, reason = profiles.detect(isolated_cwd)
    assert name == "api"
    assert "fastapi" in reason


def test_detect_reads_a_plain_package_as_library(isolated_cwd):
    (isolated_cwd / "pyproject.toml").write_text('name = "widgets"\n')
    name, _ = profiles.detect(isolated_cwd)
    assert name == "library"


def test_detect_prefers_ui_when_signals_conflict(isolated_cwd):
    """The superset errs toward running a check rather than skipping it."""
    (isolated_cwd / "openapi.yaml").write_text("openapi: 3.0.0\n")
    (isolated_cwd / "index.html").write_text("<html></html>")
    name, _ = profiles.detect(isolated_cwd)
    assert name == "ui"


def test_detect_ignores_build_output(isolated_cwd):
    """A built index.html under dist/ is output, not evidence of shape."""
    (isolated_cwd / "dist").mkdir()
    (isolated_cwd / "dist" / "index.html").write_text("<html></html>")
    (isolated_cwd / "pyproject.toml").write_text('name = "widgets"\n')
    name, reason = profiles.detect(isolated_cwd)
    assert name == "library"
    assert "index.html" not in reason


def test_detect_falls_back_to_the_default_on_an_empty_repo(isolated_cwd):
    name, reason = profiles.detect(isolated_cwd)
    assert name == profiles.default_name()
    assert "no clear signal" in reason


# ── CLI surface ──────────────────────────────────────────────────


def test_profile_list_names_every_profile(capsys):
    assert cli.main(["profile", "list"]) == 0
    out = capsys.readouterr().out
    for name in profiles.names():
        assert name in out


def test_profile_expand_prints_one_filename_per_line(capsys):
    assert cli.main(["profile", "expand", "api"]) == 0
    lines = [line for line in capsys.readouterr().out.splitlines() if line.strip()]
    assert lines == profiles.expand("api")


def test_profile_expand_unknown_name_exits_2(capsys):
    assert cli.main(["profile", "expand", "nope"]) == 2
    captured = capsys.readouterr()
    assert "unknown profile" in captured.out + captured.err


def test_profile_show_reports_the_active_profile_and_its_source(write_config, capsys):
    write_config("profile: api\n")
    assert cli.main(["profile", "show"]) == 0
    out = capsys.readouterr().out
    assert "api" in out
    assert ".slopstopper.yml" in out
    assert "ss-reliability-seo-check.yml" in out


def test_profile_show_flags_an_unset_key_as_the_default(write_config, capsys):
    write_config("urls:\n  production:\n")
    assert cli.main(["profile", "show"]) == 0
    out = capsys.readouterr().out
    assert "unset" in out
    assert "no API detected" in out


def test_ui_without_api_holds_back_exactly_the_four_api_checks(write_config):
    write_config("profile: ui\n")
    assert profiles.api_signal() is None
    assert profiles.effective_disabled() == {
        "ss-reliability-api-health-check.yml",
        "ss-security-api-headers-check.yml",
        "ss-reliability-api-latency-check.yml",
        "ss-hygiene-openapi-check.yml",
    }


def test_a_configured_api_check_counts_as_an_api(write_config):
    write_config("api:\n  health:\n    path: /healthz\n")
    assert profiles.api_signal() == "api.health.path is set in .slopstopper.yml"
    assert profiles.effective_disabled() == set()


def test_the_schema_examples_empty_api_block_is_not_an_api(write_config):
    """Copying .slopstopper.yml.example verbatim must not switch the ui holdback off."""
    write_config((REPO_ROOT / ".slopstopper.yml.example").read_text())
    assert profiles.api_signal() is None
    assert profiles.effective_disabled() == set(profiles.without_api("ui"))


def test_profile_list_shows_what_ui_holds_back(isolated_cwd, capsys):
    assert cli.main(["profile", "list"]) == 0
    out = capsys.readouterr().out
    assert "holds back 4 workflow(s) until the repo shows an API" in out
    assert "nothing, so every check applies" not in out


def test_an_openapi_spec_counts_as_an_api(write_config, isolated_cwd):
    write_config("profile: ui\n")
    (isolated_cwd / "openapi.json").write_text("{}")
    assert profiles.api_signal() == "found openapi.json"
    assert profiles.effective_disabled() == set()


def test_workflows_enabled_takes_back_an_api_check_without_an_api(write_config):
    write_config("workflows:\n  enabled: [ss-reliability-api-health-check.yml]\n")
    assert "ss-reliability-api-health-check.yml" not in profiles.effective_disabled()


def test_profile_show_warns_on_an_unknown_name(write_config, capsys):
    write_config("profile: rest-api\n")
    assert cli.main(["profile", "show"]) == 0
    captured = capsys.readouterr()
    assert "unknown profile" in captured.out + captured.err


def test_profile_detect_is_advisory_and_writes_nothing(isolated_cwd, capsys):
    (isolated_cwd / "openapi.yaml").write_text("openapi: 3.0.0\n")
    config.reload()
    assert cli.main(["profile", "detect"]) == 0
    out = capsys.readouterr().out
    assert "api" in out
    assert not (isolated_cwd / ".slopstopper.yml").exists()


def test_checks_list_marks_checks_the_profile_drops(write_config, capsys):
    write_config("profile: api\n")
    assert cli.main(["checks", "list", "--category", "reliability"]) == 0
    out = capsys.readouterr().out
    assert "💤" in out
    assert "profile: api" in out


def test_checks_list_json_carries_the_workflow_and_disabled_flag(write_config, capsys):
    write_config("profile: api\n")
    assert cli.main(["checks", "list", "--json"]) == 0
    entries = {e["name"]: e for e in json.loads(capsys.readouterr().out)}
    assert entries["reliability:cwv"]["disabled"] is True
    assert entries["reliability:cwv"]["workflow"] == "ss-reliability-core-web-vitals.yml"
    assert entries["security:iac"]["disabled"] is False


def test_profile_disabled_prints_the_effective_set_one_per_line(write_config, capsys):
    """One line per workflow, so a shell loop can read the effective set."""
    write_config("profile: ui\nworkflows:\n  disabled: [ss-reliability-seo-check.yml]\n"
                 "  enabled: [ss-reliability-api-health-check.yml]\n")
    assert cli.main(["profile", "disabled"]) == 0
    assert capsys.readouterr().out.splitlines() == sorted(profiles.effective_disabled())
    assert "ss-reliability-seo-check.yml" in profiles.effective_disabled()
    assert "ss-reliability-api-health-check.yml" not in profiles.effective_disabled()


# ── --json ───────────────────────────────────────────────────────


def test_profile_list_json_has_one_row_per_profile(capsys):
    import json

    assert cli.main(["profile", "list", "--json"]) == 0
    rows = json.loads(capsys.readouterr().out)
    assert [row["name"] for row in rows] == profiles.names()
    assert sum(1 for row in rows if row["default"]) == 1
    api = next(row for row in rows if row["name"] == "api")
    assert "ss-reliability-smoke-tests.yml" in api["disables"]
    assert {"name", "default", "summary", "detail", "disables", "disables_without_api"} <= set(rows[0])


def test_profile_show_json_reports_the_profile_and_each_source(write_config, capsys):
    import json

    write_config("profile: api\nworkflows:\n  disabled: [ss-security-sast-check.yml]\n")
    assert cli.main(["profile", "show", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["profile"] == "api"
    assert data["configured"] == "api"
    assert data["origin"] == "from .slopstopper.yml"
    assert data["warning"] is None
    sources = {row["workflow"]: row["source"] for row in data["disabled"]}
    assert sources["ss-security-sast-check.yml"] == "workflows.disabled"
    assert sources["ss-reliability-smoke-tests.yml"] == "profile"
    assert data["re_enabled"] == []


def test_profile_detect_json(isolated_cwd, capsys):
    import json

    assert cli.main(["profile", "detect", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert {"suggested", "reason", "active", "matches"} <= set(data)
    assert data["matches"] == (data["suggested"] == data["active"])


def test_profile_expand_json_and_its_error(capsys):
    import json

    assert cli.main(["profile", "expand", "api", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["profile"] == "api"
    assert data["workflows"] == profiles.expand("api")
    assert cli.main(["profile", "expand", "nope", "--json"]) == 2
    assert "unknown profile" in json.loads(capsys.readouterr().out)["error"]


def test_profile_disabled_json(write_config, capsys):
    import json

    write_config("profile: library\n")
    assert cli.main(["profile", "disabled", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["disabled"] == sorted(profiles.effective_disabled())
