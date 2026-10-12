"""Tests for the templates resolver (Playwright + Lighthouse configs and
spec files: .ss/ override → package-data fallback)."""

from __future__ import annotations

from pathlib import Path

from slopstopper import templates


# ── package-data presence ────────────────────────────────────────


def test_package_data_dir_exists():
    assert templates.PACKAGE_DATA_DIR.exists()
    assert templates.PACKAGE_DATA_DIR.is_dir()


def test_bundled_playwright_config_exists():
    assert (templates.PACKAGE_DATA_DIR / templates.PLAYWRIGHT_CONFIG_NAME).exists()


def test_bundled_lighthouserc_exists():
    assert (templates.PACKAGE_DATA_DIR / templates.LIGHTHOUSERC_NAME).exists()


def test_bundled_lighthouserc_prod_exists():
    assert (templates.PACKAGE_DATA_DIR / templates.LIGHTHOUSERC_PROD_NAME).exists()


def test_bundled_spec_files_present():
    tests_dir = templates.PACKAGE_DATA_DIR / "tests"
    assert (tests_dir / "smoke.spec.ts").exists()
    assert (tests_dir / "accessibility.spec.ts").exists()
    assert (tests_dir / "broken-links.spec.ts").exists()


# ── fallback to package data ─────────────────────────────────────


def test_playwright_config_falls_back_to_package_data(isolated_cwd):
    assert templates.playwright_config() == (
        templates.PACKAGE_DATA_DIR / templates.PLAYWRIGHT_CONFIG_NAME
    )


def test_lighthouserc_falls_back_to_package_data(isolated_cwd):
    assert templates.lighthouserc() == (
        templates.PACKAGE_DATA_DIR / templates.LIGHTHOUSERC_NAME
    )


def test_lighthouserc_prod_falls_back_to_package_data(isolated_cwd):
    assert templates.lighthouserc(prod=True) == (
        templates.PACKAGE_DATA_DIR / templates.LIGHTHOUSERC_PROD_NAME
    )


def test_lighthouserc_prod_override_wins(isolated_cwd):
    override = isolated_cwd / ".ss" / "lighthouserc.prod.json"
    override.parent.mkdir(parents=True, exist_ok=True)
    override.write_text("{}")
    resolved = templates.lighthouserc(prod=True)
    assert resolved == Path(".ss/lighthouserc.prod.json")


def test_lighthouserc_dev_ignores_prod_override(isolated_cwd):
    """A prod override mustn't get picked up when prod=False."""
    override = isolated_cwd / ".ss" / "lighthouserc.prod.json"
    override.parent.mkdir(parents=True, exist_ok=True)
    override.write_text("{}")
    resolved = templates.lighthouserc(prod=False)
    assert resolved == (templates.PACKAGE_DATA_DIR / templates.LIGHTHOUSERC_NAME)


def test_playwright_spec_falls_back_to_package_data(isolated_cwd):
    assert templates.playwright_spec("smoke") == (
        templates.PACKAGE_DATA_DIR / "tests" / "smoke.spec.ts"
    )


# ── .ss/ override wins ───────────────────────────────────────────


def test_playwright_config_override_wins(isolated_cwd):
    override = isolated_cwd / ".ss" / "playwright.config.js"
    override.parent.mkdir(parents=True, exist_ok=True)
    override.write_text("// custom config")
    resolved = templates.playwright_config()
    assert resolved == Path(".ss/playwright.config.js")


def test_lighthouserc_override_wins(isolated_cwd):
    override = isolated_cwd / ".ss" / "lighthouserc.json"
    override.parent.mkdir(parents=True, exist_ok=True)
    override.write_text("{}")
    resolved = templates.lighthouserc()
    assert resolved == Path(".ss/lighthouserc.json")


def test_playwright_spec_override_wins(isolated_cwd):
    override = isolated_cwd / ".ss" / "tests" / "smoke.spec.ts"
    override.parent.mkdir(parents=True, exist_ok=True)
    override.write_text("// custom spec")
    resolved = templates.playwright_spec("smoke")
    assert resolved == Path(".ss/tests/smoke.spec.ts")


# ── inventory + eject ────────────────────────────────────────────


def test_list_templates_returns_known_names():
    names = templates.list_templates()
    assert "playwright.config.js" in names
    assert "lighthouserc.json" in names
    assert "lighthouserc.prod.json" in names
    assert "server.js" in names
    assert "tests/smoke.spec.ts" in names
    # Sorted for stable output.
    assert names == sorted(names)


def test_bundled_server_js_exists():
    assert (templates.PACKAGE_DATA_DIR / templates.SERVER_JS_NAME).exists()


def test_template_path_returns_bundled_when_not_ejected(isolated_cwd):
    p = templates.template_path("lighthouserc.json")
    assert p == templates.PACKAGE_DATA_DIR / "lighthouserc.json"


def test_template_path_returns_override_when_ejected(isolated_cwd):
    override = isolated_cwd / ".ss" / "lighthouserc.json"
    override.parent.mkdir(parents=True, exist_ok=True)
    override.write_text("{}")
    assert templates.template_path("lighthouserc.json") == Path(".ss/lighthouserc.json")


def test_template_path_raises_for_unknown_name():
    import pytest
    with pytest.raises(KeyError):
        templates.template_path("not-a-template")


def test_is_ejected_reflects_disk_state(isolated_cwd):
    assert templates.is_ejected("playwright.config.js") is False
    override = isolated_cwd / ".ss" / "playwright.config.js"
    override.parent.mkdir(parents=True, exist_ok=True)
    override.write_text("// custom")
    assert templates.is_ejected("playwright.config.js") is True


def test_eject_copies_bundled_into_dotss(isolated_cwd):
    dest, was_new = templates.eject("lighthouserc.json")
    assert was_new is True
    assert dest == Path(".ss/lighthouserc.json")
    assert dest.exists()
    # Content should match the bundled file byte-for-byte.
    bundled = (templates.PACKAGE_DATA_DIR / "lighthouserc.json").read_text()
    assert dest.read_text() == bundled


def test_eject_creates_nested_tests_dir(isolated_cwd):
    dest, was_new = templates.eject("tests/smoke.spec.ts")
    assert was_new is True
    assert dest == Path(".ss/tests/smoke.spec.ts")
    assert dest.exists()


def test_eject_leaves_existing_override_alone(isolated_cwd):
    override = isolated_cwd / ".ss" / "lighthouserc.json"
    override.parent.mkdir(parents=True, exist_ok=True)
    override.write_text("{ \"custom\": true }")
    dest, was_new = templates.eject("lighthouserc.json")
    assert was_new is False
    assert dest == Path(".ss/lighthouserc.json")
    # Content is unchanged, so the adopter's customisation is preserved.
    assert override.read_text() == "{ \"custom\": true }"


def test_eject_raises_for_unknown_name(isolated_cwd):
    import pytest
    with pytest.raises(KeyError):
        templates.eject("not-a-template")


def test_playwright_spec_override_only_for_named_check(isolated_cwd):
    # An override for `smoke` doesn't affect `accessibility`
    override = isolated_cwd / ".ss" / "tests" / "smoke.spec.ts"
    override.parent.mkdir(parents=True, exist_ok=True)
    override.write_text("// custom smoke spec")

    smoke_resolved = templates.playwright_spec("smoke")
    a11y_resolved = templates.playwright_spec("accessibility")

    assert smoke_resolved == Path(".ss/tests/smoke.spec.ts")
    assert a11y_resolved == (
        templates.PACKAGE_DATA_DIR / "tests" / "accessibility.spec.ts"
    )


# ── stage_playwright (run-time copies for the reliability checks) ─


def test_stage_playwright_copies_bundled_files_into_the_run_dir(isolated_cwd):
    config, spec = templates.stage_playwright("smoke")
    assert config == Path(".ss/.run/playwright.config.js")
    assert spec == Path(".ss/.run/tests/smoke.spec.ts")
    assert config.read_text() == (templates.PACKAGE_DATA_DIR / "playwright.config.js").read_text()
    assert spec.read_text() == (templates.PACKAGE_DATA_DIR / "tests" / "smoke.spec.ts").read_text()


def test_stage_playwright_never_writes_an_override(isolated_cwd):
    templates.stage_playwright("smoke")
    for name in templates.list_templates():
        assert not templates.is_ejected(name), f"{name} reads as ejected after a run"


def test_stage_playwright_run_dir_ignores_itself(isolated_cwd):
    templates.stage_playwright("smoke")
    assert (isolated_cwd / ".ss" / ".run" / ".gitignore").read_text() == "*\n"


def test_stage_playwright_copies_a_deliberate_override(isolated_cwd):
    override = isolated_cwd / ".ss" / "tests" / "smoke.spec.ts"
    override.parent.mkdir(parents=True)
    override.write_text("// customised spec")
    config_override = isolated_cwd / ".ss" / "playwright.config.js"
    config_override.write_text("// customised config")
    config, spec = templates.stage_playwright("smoke")
    assert spec.read_text() == "// customised spec"
    assert config.read_text() == "// customised config"


def test_stage_playwright_refreshes_a_stale_copy(isolated_cwd):
    stale = isolated_cwd / ".ss" / ".run" / "tests" / "smoke.spec.ts"
    stale.parent.mkdir(parents=True)
    stale.write_text("// from an older CLI")
    _, spec = templates.stage_playwright("smoke")
    assert spec.read_text() == (templates.PACKAGE_DATA_DIR / "tests" / "smoke.spec.ts").read_text()
