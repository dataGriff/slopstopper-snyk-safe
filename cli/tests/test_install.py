"""Integration tests for install.sh.

install.sh is bash, not Python, but it's CLI-adjacent (the entry point
adopters run) and these tests exercise install.sh in a subprocess
against a throwaway target dir so the deletion-tracking, CLI-pin and
security-headers behaviour doesn't regress.

Each test resolves install.sh from the repo root (parent of cli/) and
runs it as `bash install.sh <target>`: the script runs from this checkout
(templates/ + install.sh + cli/ live next to it) against a different
directory, which is the only mode it supports. Nothing is cloned or
downloaded.

SKIP_CLI_INSTALL=1 makes install.sh skip the pipx install and the
pipx/mise preflight requirement, so these assert on the bash-side
behaviour (the `cli:` pin written into .slopstopper.yml, the mise.toml
pins, workflow tracking, headers seeding) deterministically and offline.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import stat
import subprocess
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
INSTALL_SH = REPO_ROOT / "install.sh"
INSTALL_SKILL_SH = REPO_ROOT / "install-skill.sh"

# The pins install.sh writes into an adopter's mise.toml, and the source
# it records for the CLI. Hardcoded rather than read from install.sh, so a
# change there has to be made deliberately in both places (and in this
# repo's own mise.toml, which the toolchain test below compares).
SNYK_VERSION = "1.1308.0"
GITLEAKS_VERSION = "8.30.1"
DEFAULT_CLI_SOURCE = "https://github.com/dataGriff/slopstopper-snyk-safe.git"

# Every workflow install.sh ships, by layer. The registration test checks
# each check's workflow is in here; this list pins the whole set so a
# removed-upstream workflow can't creep back in.
EXPECTED_GENERIC_WORKFLOWS = {
    "ss-hygiene-complexity-check.yml",
    "ss-hygiene-csp-exceptions-check.yml",
    "ss-hygiene-docs-accuracy-check.yml",
    "ss-hygiene-docs-size-check.yml",
    "ss-hygiene-docs-structure-check.yml",
    "ss-hygiene-entry-files-check.yml",
    "ss-hygiene-auto-label-pr.yml",
    "ss-security-sast-check.yml",
    "ss-security-secrets-check.yml",
    "ss-security-dependencies-check.yml",
    "ss-security-iac-check.yml",
    "ss-security-sbom-check.yml",
    "ss-workflow-failure-issue.yml",
    "ss-pr-summary.yml",
    "ss-reliability-smoke-tests.yml",
    "ss-reliability-e2e-check.yml",
    "ss-reliability-accessibility-check.yml",
    "ss-reliability-broken-links-check.yml",
    "ss-reliability-core-web-vitals.yml",
    "ss-reliability-seo-check.yml",
    "ss-reliability-llms-txt-check.yml",
    "ss-reliability-robots-txt-check.yml",
    "ss-reliability-sitemap-check.yml",
    "ss-reliability-api-health-check.yml",
    "ss-reliability-api-latency-check.yml",
    "ss-security-api-headers-check.yml",
    "ss-hygiene-openapi-check.yml",
}

# Artefacts an earlier slopstopper release shipped that this suite does
# not. A re-run removes them from an adopter repo.
OBSOLETE_WORKFLOWS = {
    "ss-security-dast-check.yml",
    "ss-security-vulnerability-all-check.yml",
    "ss-security-vulnerability-new-check.yml",
    "ss-cloudflare-deployed.yml",
    "ss-hygiene-doc-updater.md",
    "ss-hygiene-doc-updater.lock.yml",
}


def _run_install(
    target: Path,
    *,
    args: list[str] | None = None,
    env_extra: dict[str, str] | None = None,
    script: Path = INSTALL_SH,
) -> subprocess.CompletedProcess[str]:
    """Run install.sh against the given target dir. Returns the completed process.

    Always sets SKIP_CLI_INSTALL=1 so tests don't run pipx on every
    invocation. These tests exercise the bash-side behaviour (workflow
    tracking, headers seeding, the cli pin, mise.toml pins), not the CLI
    install itself. Extra flags (e.g. ["--cli-ref", "v1"]) go before the
    target via `args`.
    """
    env = os.environ.copy()
    env["SKIP_CLI_INSTALL"] = "1"
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        ["bash", str(script), *(args or []), str(target)],
        capture_output=True,
        text=True,
        env=env,
        cwd=REPO_ROOT,
    )


def _mise_tool(target: Path, key: str) -> str | None:
    """Return the version pinned for `key` in the target's mise.toml, or None."""
    cfg = target / "mise.toml"
    if not cfg.exists():
        return None
    for line in cfg.read_text().splitlines():
        m = re.match(r'^\s*"?' + re.escape(key) + r'"?\s*=\s*"([^"]*)"', line)
        if m:
            return m.group(1)
    return None


def _cli_pin(target: Path) -> tuple[str | None, str | None]:
    """The (source, ref) the `cli:` block in .slopstopper.yml names, parsed
    with the CLI's own loader (the same one ss-setup's reader relies on)."""
    from slopstopper import config

    tree = config._load_yaml_subset(target / ".slopstopper.yml")
    block = tree.get("cli") or {}
    source = block.get("source") if isinstance(block, dict) else None
    ref = block.get("ref") if isinstance(block, dict) else None
    return (source or None, ref or None)


def _checkout_ref() -> str:
    """What install.sh should record for this checkout: the tag HEAD is
    exactly on, else the full SHA."""
    tag = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "describe", "--tags", "--exact-match", "HEAD"],
        capture_output=True, text=True,
    )
    if tag.returncode == 0 and tag.stdout.strip():
        return tag.stdout.strip()
    return subprocess.run(
        ["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()


def _make_minimal_target(tmp_path: Path) -> Path:
    """Create the minimum scaffolding install.sh expects: a git repo with public/."""
    target = tmp_path / "adopter"
    target.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=target, check=True)
    (target / "public").mkdir()
    return target


def _source_copy(tmp_path: Path, *, git: bool, tag: str | None = None) -> Path:
    """A copy of this checkout (minus .git, venvs and build output) that a
    test can run install.sh from: as an unpacked archive (git=False) or as
    its own git repo, optionally with HEAD on `tag`."""
    src = tmp_path / "src"
    skip = {".git", "node_modules", ".venv", "__pycache__", ".ss", ".pytest_cache", "build", "dist"}
    shutil.copytree(
        REPO_ROOT, src,
        ignore=lambda _d, names: [n for n in names if n in skip or n.endswith(".egg-info")],
    )
    if git:
        env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@x", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@x"}
        subprocess.run(["git", "init", "-q"], cwd=src, check=True)
        subprocess.run(["git", "add", "-A"], cwd=src, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "snapshot"], cwd=src, check=True, env=env)
        if tag:
            subprocess.run(["git", "tag", tag], cwd=src, check=True)
    return src


# ── the CLI pin: cli.source + cli.ref in .slopstopper.yml ────────


def test_cli_ref_defaults_to_the_checkouts_ref(tmp_path):
    """The workflows, shims and templates come from this tree, so the CLI is
    pinned to the same ref: the tag when HEAD is on one, else the SHA."""
    target = _make_minimal_target(tmp_path)
    result = _run_install(target, args=["--no-hooks", "--no-skills"])
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
    assert _cli_pin(target) == (DEFAULT_CLI_SOURCE, _checkout_ref())


def test_cli_ref_is_the_tag_when_the_checkout_is_on_one(tmp_path):
    src = _source_copy(tmp_path, git=True, tag="v9.9.9")
    target = _make_minimal_target(tmp_path)
    result = _run_install(target, args=["--no-hooks", "--no-skills"], script=src / "install.sh")
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
    assert _cli_pin(target)[1] == "v9.9.9"


def test_cli_ref_is_the_sha_when_the_checkout_is_not_tagged(tmp_path):
    src = _source_copy(tmp_path, git=True)
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=src, capture_output=True, text=True, check=True).stdout.strip()
    target = _make_minimal_target(tmp_path)
    result = _run_install(target, args=["--no-hooks", "--no-skills"], script=src / "install.sh")
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
    assert _cli_pin(target)[1] == head
    assert len(head) == 40


def test_cli_ref_flag_overrides_the_checkouts_ref(tmp_path):
    target = _make_minimal_target(tmp_path)
    result = _run_install(target, args=["--cli-ref", "v1.2.3", "--no-hooks", "--no-skills"])
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
    assert _cli_pin(target) == (DEFAULT_CLI_SOURCE, "v1.2.3")
    assert "slopstopper-cli @ git+" + DEFAULT_CLI_SOURCE + "@v1.2.3#subdirectory=cli" in result.stdout


def test_cli_ref_env_var_is_equivalent_to_the_flag(tmp_path):
    target = _make_minimal_target(tmp_path)
    result = _run_install(target, args=["--no-hooks", "--no-skills"], env_extra={"SLOPSTOPPER_CLI_REF": "v4.5.6"})
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
    assert _cli_pin(target)[1] == "v4.5.6"


def test_cli_source_flag_points_at_a_mirror(tmp_path):
    """An enterprise mirrors the repo internally; the pin names the mirror
    so CI never reaches github.com."""
    target = _make_minimal_target(tmp_path)
    mirror = "https://git.corp.example/mirrors/slopstopper-snyk-safe.git"
    result = _run_install(target, args=["--cli-source", mirror, "--cli-ref", "v0.1.0", "--no-hooks", "--no-skills"])
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
    assert _cli_pin(target) == (mirror, "v0.1.0")
    assert f"slopstopper-cli @ git+{mirror}@v0.1.0#subdirectory=cli" in result.stdout


def test_cli_ref_requires_argument(tmp_path):
    """--cli-ref with no value is a hard error, not a silent no-op."""
    result = subprocess.run(
        ["bash", str(INSTALL_SH), "--cli-ref"], capture_output=True, text=True,
        env={**os.environ, "SKIP_CLI_INSTALL": "1"}, cwd=REPO_ROOT,
    )
    assert result.returncode != 0
    assert "--cli-ref requires a git ref" in (result.stdout + result.stderr)


def test_cli_ref_that_would_break_the_pip_spec_is_refused(tmp_path):
    target = _make_minimal_target(tmp_path)
    result = _run_install(target, args=["--cli-ref", "v1 && rm -rf /", "--no-hooks", "--no-skills"])
    assert result.returncode != 0
    assert "not a usable git ref" in result.stdout + result.stderr
    assert not (target / ".github/workflows").exists(), "should fail before installing"


def test_the_cli_block_is_appended_to_a_config_that_predates_it(tmp_path):
    """An adopter upgrading from the PyPI-era suite has no `cli:` block."""
    target = _make_minimal_target(tmp_path)
    (target / ".slopstopper.yml").write_text("urls:\n  production: https://example.com\n")
    result = _run_install(target, args=["--cli-ref", "v1.0.0", "--no-hooks", "--no-skills"])
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
    body = (target / ".slopstopper.yml").read_text()
    assert "https://example.com" in body, "existing config must be preserved"
    assert _cli_pin(target) == (DEFAULT_CLI_SOURCE, "v1.0.0")
    assert "cli pin v1.0.0 added" in result.stdout


def test_an_existing_cli_block_is_updated_in_place(tmp_path):
    """Comments and other keys in the block stay; only source and ref move."""
    target = _make_minimal_target(tmp_path)
    (target / ".slopstopper.yml").write_text(
        "profile: ui\n"
        "# the pin\n"
        "cli:\n"
        "  # mirrored internally\n"
        "  source: https://git.corp.example/slopstopper.git\n"
        "  ref: v0.0.1\n"
        "workflows:\n"
        "  disabled: []\n"
    )
    result = _run_install(target, args=["--cli-ref", "v0.0.2", "--no-hooks", "--no-skills"])
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
    body = (target / ".slopstopper.yml").read_text()
    assert body.count("cli:") == 1
    assert "  # mirrored internally\n" in body
    assert "  ref: v0.0.2\n" in body
    assert f"  source: {DEFAULT_CLI_SOURCE}\n" in body
    assert "workflows:\n  disabled: []\n" in body


def test_a_rerun_from_the_same_ref_leaves_the_config_byte_for_byte(tmp_path):
    """The config is adopter-owned: a re-run from the same ref rewrites nothing."""
    target = _make_minimal_target(tmp_path)
    cfg = target / ".slopstopper.yml"
    assert _run_install(target, args=["--no-hooks", "--no-skills"]).returncode == 0
    cfg.write_text(cfg.read_text() + "# a note the adopter added\n")
    before = cfg.read_text()
    result = _run_install(target, args=["--no-hooks", "--no-skills"])
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
    assert cfg.read_text() == before
    assert "cli pin already" in result.stdout


def test_legacy_cli_version_key_is_stripped(tmp_path):
    """The pre-git-pin cli_version key (and its comment block) goes; other
    keys stay; the pin is the checkout's ref, not the old number."""
    target = _make_minimal_target(tmp_path)
    cfg = target / ".slopstopper.yml"
    cfg.write_text(
        "smoke:\n"
        "  og_image_path: /og-image.png\n"
        "# ── slopstopper-cli version pin ──\n"
        "# blurb\n"
        "cli_version: '0.5.0'\n"
        "headers:\n"
        "  source: public/_headers\n"
    )
    result = _run_install(target, args=["--no-hooks", "--no-skills"])
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
    text = cfg.read_text()
    assert "cli_version" not in text
    assert "slopstopper-cli version pin ──\n# blurb" not in text
    assert "og_image_path: /og-image.png" in text
    assert "source: public/_headers" in text
    assert _cli_pin(target)[1] == _checkout_ref()


def test_a_source_without_git_needs_an_explicit_ref(tmp_path):
    """Installed from an unpacked archive there is no ref to read, and a pin
    is never guessed."""
    src = _source_copy(tmp_path, git=False)
    target = _make_minimal_target(tmp_path)
    result = _run_install(target, args=["--no-hooks", "--no-skills"], script=src / "install.sh")
    assert result.returncode != 0
    assert "could not determine the slopstopper-cli ref" in result.stdout + result.stderr
    assert "--cli-ref" in result.stdout + result.stderr
    assert not (target / ".github/workflows").exists()


def test_a_source_without_git_removes_a_stale_marker(tmp_path):
    """Installed from an unpacked archive (with the ref given), there is no
    commit to record. A marker left from the previous install would name a
    commit these files didn't come from."""
    src = _source_copy(tmp_path, git=False)
    target = _make_minimal_target(tmp_path)
    (target / ".ss").mkdir()
    (target / ".ss" / ".installed-from").write_text("0" * 40 + "\n")
    result = _run_install(target, args=["--cli-ref", "v0.1.0", "--no-hooks", "--no-skills"], script=src / "install.sh")
    assert result.returncode == 0, result.stdout + result.stderr
    assert not (target / ".ss" / ".installed-from").exists()
    assert _cli_pin(target)[1] == "v0.1.0"


def test_install_records_the_source_commit(tmp_path):
    """A refresh diffs upstream from this commit, as the install skill's
    "Spot newly-shipped knobs" step describes."""
    target = _make_minimal_target(tmp_path)
    assert _run_install(target, args=["--no-hooks", "--no-skills"]).returncode == 0
    head = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()
    assert (target / ".ss" / ".installed-from").read_text() == head + "\n"


# ── mise.toml toolchain pins ─────────────────────────────────────


def test_toolchain_is_pinned_in_mise_toml(tmp_path):
    """task, node, snyk and gitleaks land as exact pins, and nothing points
    at a package index for the CLI."""
    target = _make_minimal_target(tmp_path)
    result = _run_install(target, args=["--no-hooks", "--no-skills"])
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
    text = (target / "mise.toml").read_text()
    assert _mise_tool(target, "task") == "3"
    assert _mise_tool(target, "node") == "20"
    assert _mise_tool(target, "npm:snyk") == SNYK_VERSION
    assert _mise_tool(target, "gitleaks") == GITLEAKS_VERSION
    assert "pipx:slopstopper-cli" not in text
    assert "pypi" not in text.lower()


def test_adopter_pins_match_this_repos_own_mise_toml():
    """This repo runs the same scanners it ships; the two pin sets stay in
    step so a bump lands in both."""
    own = (REPO_ROOT / "mise.toml").read_text()
    for key, version in (("npm:snyk", SNYK_VERSION), ("gitleaks", GITLEAKS_VERSION)):
        assert re.search(r'^\s*"?' + re.escape(key) + r'"?\s*=\s*"' + re.escape(version) + '"', own, re.M), (
            f"{key} = {version} is what install.sh writes for adopters, but this repo's mise.toml pins something else"
        )


def test_a_legacy_pipx_cli_pin_is_removed(tmp_path):
    """The upstream suite pinned the CLI as a pipx package in mise.toml. Left
    in place it would pull an unreviewed copy from a package index."""
    target = _make_minimal_target(tmp_path)
    (target / "mise.toml").write_text('[tools]\n"pipx:slopstopper-cli" = "0.21.0"\n"node" = "18"\n')
    result = _run_install(target, args=["--no-hooks", "--no-skills"])
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
    text = (target / "mise.toml").read_text()
    assert "pipx:slopstopper-cli" not in text
    assert "removed the legacy pipx:slopstopper-cli pin" in result.stdout
    assert _mise_tool(target, "node") == "18"
    assert _mise_tool(target, "npm:snyk") == SNYK_VERSION


def test_existing_mise_tools_are_preserved(tmp_path):
    """Writing the pins must not disturb other tools in an existing mise.toml."""
    target = _make_minimal_target(tmp_path)
    (target / "mise.toml").write_text('[tools]\n"node" = "20"\n"go" = "1.22"\n')
    result = _run_install(target, args=["--no-hooks", "--no-skills"])
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
    text = (target / "mise.toml").read_text()
    assert '"go" = "1.22"' in text
    assert '"node" = "20"' in text
    assert text.count("npm:snyk") == 1


def test_pins_are_idempotent_on_rerun(tmp_path):
    target = _make_minimal_target(tmp_path)
    assert _run_install(target, args=["--no-hooks", "--no-skills"]).returncode == 0
    before = (target / "mise.toml").read_text()
    assert _run_install(target, args=["--no-hooks", "--no-skills"]).returncode == 0
    assert (target / "mise.toml").read_text() == before


def test_existing_node_pin_is_not_overridden(tmp_path):
    """If the adopter already pins node in mise.toml, install leaves it alone."""
    target = _make_minimal_target(tmp_path)
    (target / "mise.toml").write_text('[tools]\n"node" = "18"\n')
    result = _run_install(target, args=["--no-hooks", "--no-skills"])
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
    assert _mise_tool(target, "node") == "18"


def test_node_version_file_suppresses_mise_node_seed(tmp_path):
    """An existing .node-version means the adopter manages node themselves,
    so install must not also seed a node pin into mise.toml."""
    target = _make_minimal_target(tmp_path)
    (target / ".node-version").write_text("18\n")
    result = _run_install(target, args=["--no-hooks", "--no-skills"])
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
    assert _mise_tool(target, "node") is None
    assert _mise_tool(target, "npm:snyk") == SNYK_VERSION


def test_legacy_node_version_is_stripped(tmp_path):
    """A dead node_version in .slopstopper.yml (and its comment block) is removed
    on refresh. Nothing ever read it, and node lives in mise.toml."""
    target = _make_minimal_target(tmp_path)
    cfg = target / ".slopstopper.yml"
    cfg.write_text(
        "# ── Node version pin ──\n"
        "# blurb\n"
        "node_version: '22'\n"
        "headers:\n"
        "  source: public/_headers\n"
    )
    result = _run_install(target, args=["--no-hooks", "--no-skills"])
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
    text = cfg.read_text()
    assert "node_version" not in text
    assert "# ── Node version pin" not in text
    assert "source: public/_headers" in text
    assert _mise_tool(target, "node") == "20"


# ── preflight and the no-network contract ────────────────────────


def test_a_real_install_requires_pipx_and_mise(tmp_path):
    """Without the test seam, the CLI is installed by pipx and the scanners
    by mise, so both are named up front rather than failing mid-flight."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for tool in ("git", "python3", "bash"):
        found = shutil.which(tool)
        assert found, tool
        (bin_dir / tool).symlink_to(found)
    target = _make_minimal_target(tmp_path)
    result = subprocess.run(
        ["bash", str(INSTALL_SH), str(target)],
        capture_output=True, text=True, cwd=REPO_ROOT,
        env={"PATH": str(bin_dir), "HOME": os.environ.get("HOME", str(tmp_path))},
    )
    assert result.returncode != 0
    assert "pipx" in result.stderr and "mise" in result.stderr
    assert not (target / ".github/workflows").exists()


def test_install_sh_refuses_to_run_outside_a_checkout(tmp_path):
    """A copied-out install.sh has nothing to install from and never clones."""
    lone = tmp_path / "lone"
    lone.mkdir()
    shutil.copy(INSTALL_SH, lone / "install.sh")
    target = _make_minimal_target(tmp_path)
    result = _run_install(target, script=lone / "install.sh")
    assert result.returncode != 0
    assert "must run from a checkout" in result.stderr
    assert "git clone --branch" in result.stderr
    assert not (target / ".github/workflows").exists()


def test_install_sh_refuses_its_own_directory_as_the_target(tmp_path):
    src = _source_copy(tmp_path, git=True)
    result = _run_install(src, script=src / "install.sh")
    assert result.returncode != 0
    assert "must run from a checkout" in result.stderr


@pytest.mark.parametrize("script", [INSTALL_SH, INSTALL_SKILL_SH])
def test_installers_fetch_nothing_at_run_time(script):
    """No pipe-to-bash form, no raw-content URL, no package index, no clone:
    the only network call is pipx installing the pinned git ref."""
    text = script.read_text()
    for needle in ("raw.githubusercontent.com", "hungovercoders", "pypi", "PyPI", "curl ", "git clone --depth", "| bash", "| sh"):
        assert needle not in text, f"{script.name} still carries {needle!r}"
    assert "--upgrade-cli" not in text and "--cli-version" not in text and "FORCE_LATEST" not in text


def test_generic_workflows_is_exactly_the_shipped_set():
    text = INSTALL_SH.read_text()
    block = text[text.index("GENERIC_WORKFLOWS=(") : text.index("\n)\n", text.index("GENERIC_WORKFLOWS=("))]
    assert set(re.findall(r'"(ss-[^"]+)"', block)) == EXPECTED_GENERIC_WORKFLOWS
    on_disk = {p.name for p in (REPO_ROOT / ".github/workflows").glob("ss-*.yml")}
    assert EXPECTED_GENERIC_WORKFLOWS <= on_disk, sorted(EXPECTED_GENERIC_WORKFLOWS - on_disk)


# ── obsolete artefacts from the upstream suite ───────────────────


def test_a_rerun_removes_the_workflows_this_suite_no_longer_ships(tmp_path):
    """An adopter refreshing from the upstream suite carries ZAP, Trivy, the
    Cloudflare trigger and the doc-updater. Nothing here runs them, so
    they go, and the adopter's own workflows stay."""
    target = _make_minimal_target(tmp_path)
    workflows = target / ".github/workflows"
    workflows.mkdir(parents=True)
    for name in OBSOLETE_WORKFLOWS:
        (workflows / name).write_text("# from the upstream suite\n")
    (workflows / "deploy.yml").write_text("# the adopter's own\n")
    (target / ".zap").mkdir()
    (target / ".zap/rules.tsv").write_text("10096\tIGNORE\n")

    result = _run_install(target, args=["--no-hooks", "--no-skills"])
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
    for name in OBSOLETE_WORKFLOWS:
        assert not (workflows / name).exists(), name
        assert f"removed obsolete workflow .github/workflows/{name}" in result.stdout
    assert (workflows / "deploy.yml").exists()
    # The adopter's ZAP rules are theirs: left in place, flagged as unused.
    assert (target / ".zap/rules.tsv").read_text() == "10096\tIGNORE\n"
    assert ".zap/rules.tsv is not read by this suite any more" in result.stdout
    marker = (target / ".ss/.workflows-installed").read_text().split()
    assert not (set(marker) & OBSOLETE_WORKFLOWS)


def test_no_zap_rules_are_seeded(tmp_path):
    target = _make_minimal_target(tmp_path)
    assert _run_install(target, args=["--no-hooks", "--no-skills"]).returncode == 0
    assert not (target / ".zap").exists()
    assert not (REPO_ROOT / "templates" / "zap-rules.tsv.example").exists()


# ── #100 deletion-tracking false positive on stale marker ────────


def test_stale_marker_with_zero_workflows_on_disk_is_ignored(tmp_path):
    """A marker listing every workflow with no workflows on disk is stale.

    Reproduces the failure mode: prior branch left
    `.ss/.workflows-installed` behind, the install thinks the user
    deleted every workflow and silently installs zero.
    """
    target = _make_minimal_target(tmp_path)
    # An API spec, so the ui profile keeps its four API checks and every
    # workflow is expected back.
    (target / "openapi.yaml").write_text("openapi: 3.0.0\n")

    (target / ".ss").mkdir()
    workflow_names = sorted(EXPECTED_GENERIC_WORKFLOWS)
    (target / ".ss/.workflows-installed").write_text("\n".join(workflow_names) + "\n")

    result = _run_install(target)
    assert result.returncode == 0, f"install.sh failed:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"

    installed = sorted(p.name for p in (target / ".github/workflows").glob("ss-*.yml"))
    assert installed == workflow_names, f"{installed}\nstdout:\n{result.stdout}"
    # Should also surface the warning so silent fixes aren't silent.
    assert "stale" in result.stdout.lower() or "stale" in result.stderr.lower(), (
        f"Expected 'stale' warning in output:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )


def test_genuine_user_deletion_is_still_respected(tmp_path):
    """If some workflows are on disk and others are in the marker but missing,
    respect the deletion, because that's a real user choice."""
    target = _make_minimal_target(tmp_path)

    first = _run_install(target)
    assert first.returncode == 0, f"first install failed:\n{first.stdout}\n{first.stderr}"

    deleted = "ss-security-secrets-check.yml"
    deleted_path = target / ".github/workflows" / deleted
    assert deleted_path.exists(), "test setup: expected the workflow to be present"
    deleted_path.unlink()

    second = _run_install(target)
    assert second.returncode == 0, f"second install failed:\n{second.stdout}\n{second.stderr}"
    assert not deleted_path.exists(), "user-deleted workflow should NOT be re-added"
    assert "stale" not in second.stdout.lower(), (
        "stale-marker warning should NOT fire when other workflows are on disk"
    )


# ── #102 security-headers idempotent append ──────────────────────


def test_seeds_headers_block_when_public_headers_missing(tmp_path):
    """A fresh adopter with no public/_headers gets it seeded with the slopstopper block."""
    target = _make_minimal_target(tmp_path)

    result = _run_install(target)
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"

    headers = (target / "public/_headers").read_text()
    assert "# slopstopper security headers begin" in headers
    assert "# slopstopper security headers end" in headers


def test_appends_headers_block_to_existing_cache_only_headers(tmp_path):
    """When public/_headers holds only cache rules, slopstopper appends without losing them."""
    target = _make_minimal_target(tmp_path)
    existing = (
        "/*.html\n"
        "  Cache-Control: public, max-age=0, must-revalidate\n"
        "\n"
        "/assets/*\n"
        "  Cache-Control: public, max-age=31536000, immutable\n"
    )
    (target / "public/_headers").write_text(existing)

    result = _run_install(target)
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"

    headers = (target / "public/_headers").read_text()
    assert existing in headers, "adopter's existing cache rules must be preserved"
    assert "# slopstopper security headers begin" in headers
    assert "# slopstopper security headers end" in headers


def test_headers_append_is_idempotent(tmp_path):
    """Second install should not duplicate the headers block."""
    target = _make_minimal_target(tmp_path)
    existing = "/*.html\n  Cache-Control: public, max-age=0\n"
    (target / "public/_headers").write_text(existing)

    first = _run_install(target)
    assert first.returncode == 0
    first_content = (target / "public/_headers").read_text()
    assert first_content.count("# slopstopper security headers begin") == 1

    second = _run_install(target)
    assert second.returncode == 0
    second_content = (target / "public/_headers").read_text()
    assert second_content.count("# slopstopper security headers begin") == 1
    assert second_content == first_content, "second install must not modify the headers file"


def test_leaves_a_configured_headers_file_alone(tmp_path):
    """A _headers that already sets a security header is the adopter's policy; no block is appended (#453)."""
    target = _make_minimal_target(tmp_path)
    existing = (
        "/*\n"
        "  Content-Security-Policy: default-src 'self'\n"
        "  Strict-Transport-Security: max-age=63072000\n"
        "  Cache-Control: public, max-age=0, must-revalidate\n"
    )
    (target / "public/_headers").write_text(existing)

    result = _run_install(target)
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
    assert (target / "public/_headers").read_text() == existing
    assert "already sets security headers" in result.stdout


def test_a_commented_security_header_does_not_count_as_configured(tmp_path):
    """Only an uncommented header line marks the file as configured."""
    target = _make_minimal_target(tmp_path)
    existing = "/*\n  # Content-Security-Policy: default-src 'self'\n  Cache-Control: public, max-age=0\n"
    (target / "public/_headers").write_text(existing)

    result = _run_install(target)
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
    assert "# slopstopper security headers begin" in (target / "public/_headers").read_text()


# ── pre-push hygiene hook ────────────────────────────────────────


def _hooks_path(target: Path) -> str | None:
    """Return the target repo's local core.hooksPath, or None if unset."""
    result = subprocess.run(
        ["git", "config", "--local", "--get", "core.hooksPath"],
        cwd=target,
        capture_output=True,
        text=True,
    )
    value = result.stdout.strip()
    return value or None


def test_installs_pre_push_hook_and_wires_hookspath(tmp_path):
    """On a fresh adopter with no existing hook setup, the hook file lands,
    is executable, and core.hooksPath is wired to .githooks."""
    target = _make_minimal_target(tmp_path)

    result = _run_install(target)
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"

    hook = target / ".githooks/pre-push"
    assert hook.exists(), "pre-push hook file should be installed"
    assert os.access(hook, os.X_OK), "pre-push hook must be executable"
    assert "ss:hygiene:test" in hook.read_text(), "hook must run the hygiene aggregate"
    assert _hooks_path(target) == ".githooks", "core.hooksPath should be wired to .githooks"


def test_no_hooks_flag_skips_hook(tmp_path):
    """--no-hooks writes neither the hook file nor the hooksPath config."""
    target = _make_minimal_target(tmp_path)

    result = _run_install(target, args=["--no-hooks"])
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"

    assert not (target / ".githooks/pre-push").exists(), "--no-hooks must not write the hook"
    assert _hooks_path(target) is None, "--no-hooks must not touch core.hooksPath"


def test_no_hooks_env_var_skips_hook(tmp_path):
    """SLOPSTOPPER_NO_HOOKS=1 is the env-var equivalent of --no-hooks."""
    target = _make_minimal_target(tmp_path)

    result = _run_install(target, env_extra={"SLOPSTOPPER_NO_HOOKS": "1"})
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"

    assert not (target / ".githooks/pre-push").exists()
    assert _hooks_path(target) is None


def test_existing_hook_manager_preserves_hookspath(tmp_path):
    """An adopter already running pre-commit gets the hook file dropped in,
    but their core.hooksPath is left untouched (never hijack an existing setup)."""
    target = _make_minimal_target(tmp_path)
    (target / ".pre-commit-config.yaml").write_text("repos: []\n")

    result = _run_install(target)
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"

    assert (target / ".githooks/pre-push").exists(), "hook file should still be written for opt-in"
    assert _hooks_path(target) is None, "existing hook manager: core.hooksPath must be left alone"


def test_custom_hookspath_is_not_overridden(tmp_path):
    """A repo that already points core.hooksPath elsewhere keeps its value."""
    target = _make_minimal_target(tmp_path)
    subprocess.run(
        ["git", "config", "core.hooksPath", ".my-hooks"], cwd=target, check=True
    )

    result = _run_install(target)
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"

    assert _hooks_path(target) == ".my-hooks", "custom hooksPath must be preserved"


# ── project-shape profiles ───────────────────────────────────────


def _installed(target: Path) -> set[str]:
    return {p.name for p in (target / ".github/workflows").glob("ss-*.yml")}


def _profile_key(target: Path) -> str | None:
    for line in (target / ".slopstopper.yml").read_text().splitlines():
        if line.startswith("profile:"):
            return line.split(":", 1)[1].strip()
    return None


# The nine workflows the `api` profile drops. Hardcoded
# rather than read from profiles.json, so a change to the mapping has to
# be made deliberately in both places.
API_DROPPED = {
    "ss-reliability-smoke-tests.yml",
    "ss-reliability-e2e-check.yml",
    "ss-reliability-accessibility-check.yml",
    "ss-reliability-broken-links-check.yml",
    "ss-reliability-core-web-vitals.yml",
    "ss-reliability-seo-check.yml",
    "ss-reliability-llms-txt-check.yml",
    "ss-reliability-robots-txt-check.yml",
    "ss-reliability-sitemap-check.yml",
}

# The static layer every profile keeps: the Snyk checks and secrets apply
# to code of any shape.
STATIC_SECURITY = {
    "ss-security-sast-check.yml",
    "ss-security-secrets-check.yml",
    "ss-security-dependencies-check.yml",
    "ss-security-iac-check.yml",
    "ss-security-sbom-check.yml",
}


def test_profile_flag_writes_the_key_and_drops_those_workflows(tmp_path):
    target = _make_minimal_target(tmp_path)
    result = _run_install(target, args=["--profile", "api"])
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"

    assert _profile_key(target) == "api"
    installed = _installed(target)
    assert not (installed & API_DROPPED), f"api profile installed browser checks: {installed & API_DROPPED}"
    assert STATIC_SECURITY <= installed


def test_profile_env_var_is_equivalent_to_the_flag(tmp_path):
    target = _make_minimal_target(tmp_path)
    result = _run_install(target, env_extra={"SLOPSTOPPER_PROFILE": "api"})
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
    assert _profile_key(target) == "api"
    assert not (_installed(target) & API_DROPPED)


# Two of the API checks. `library` drops them (nothing served at all); `ui`
# and `api` both keep them, where they stay inert until api.* is configured.
API_WORKFLOWS = {
    "ss-reliability-api-health-check.yml",
    "ss-security-api-headers-check.yml",
}


def test_ui_profile_leaves_out_api_checks_when_there_is_no_api(tmp_path):
    """A static site shouldn't run four jobs that check nothing."""
    target = _make_minimal_target(tmp_path)
    result = _run_install(target)
    assert result.returncode == 0
    assert not (_installed(target) & API_WORKFLOWS)
    assert "No API found" in result.stdout
    assert "ss-reliability-smoke-tests.yml" in _installed(target)


@pytest.mark.parametrize(
    "marker,body",
    [("openapi.yaml", "openapi: 3.0.0\n"), ("package.json", '{"dependencies": {"express": "^4"}}\n')],
)
def test_ui_profile_keeps_api_checks_when_the_repo_has_an_api(tmp_path, marker, body):
    target = _make_minimal_target(tmp_path)
    (target / marker).write_text(body)
    result = _run_install(target)
    assert result.returncode == 0
    assert API_WORKFLOWS <= _installed(target)
    assert "No API found" not in result.stdout


def test_configuring_api_and_rerunning_adds_the_api_checks(tmp_path):
    target = _make_minimal_target(tmp_path)
    assert _run_install(target).returncode == 0
    assert not (_installed(target) & API_WORKFLOWS)
    cfg = target / ".slopstopper.yml"
    cfg.write_text(cfg.read_text() + "\napi:\n  health:\n    path: /healthz\n")
    assert _run_install(target).returncode == 0
    assert API_WORKFLOWS <= _installed(target)


def test_api_checks_ship_under_the_api_profile(tmp_path):
    target = _make_minimal_target(tmp_path)
    assert _run_install(target, args=["--profile", "api"]).returncode == 0
    installed = _installed(target)
    assert API_WORKFLOWS <= installed
    assert not (installed & API_DROPPED)


def test_library_profile_keeps_the_static_layer_only(tmp_path):
    """A library serves nothing, so there is no endpoint to probe; the Snyk
    and secrets checks still apply to its code."""
    target = _make_minimal_target(tmp_path)
    assert _run_install(target, args=["--profile", "library"]).returncode == 0
    installed = _installed(target)
    assert not (installed & API_WORKFLOWS)
    assert not (installed & API_DROPPED)
    assert STATIC_SECURITY <= installed


def test_profile_dropped_workflows_stay_out_of_the_marker(tmp_path):
    """Otherwise the deletion-respect rule would suppress them forever.

    The marker means "we installed this". A profile-skipped workflow was
    never installed, so recording it would make a later profile change
    look like a user deletion and never bring the workflow back.
    """
    target = _make_minimal_target(tmp_path)
    assert _run_install(target, args=["--profile", "api"]).returncode == 0
    marker = (target / ".ss/.workflows-installed").read_text().split()
    assert not (set(marker) & API_DROPPED)


def test_switching_profile_removes_and_restores_workflows(tmp_path):
    """ui → api removes the nine; api → ui brings them back."""
    target = _make_minimal_target(tmp_path)
    assert _run_install(target).returncode == 0
    full = _installed(target)
    assert API_DROPPED <= full, "sanity: a default install carries the browser checks"

    assert _run_install(target, args=["--profile", "api"]).returncode == 0
    assert not (_installed(target) & API_DROPPED)

    assert _run_install(target, args=["--profile", "ui"]).returncode == 0
    assert _installed(target) == full


def test_plain_rerun_honours_the_stored_profile(tmp_path):
    """The config is the source of truth, not the flag, so a bare re-run
    must not resurrect the workflows the stored profile drops."""
    target = _make_minimal_target(tmp_path)
    assert _run_install(target, args=["--profile", "api"]).returncode == 0

    result = _run_install(target)
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
    assert _profile_key(target) == "api", "a bare re-run must not rewrite the key"
    assert not (_installed(target) & API_DROPPED)


def test_workflows_enabled_keeps_one_check_the_profile_drops(tmp_path):
    target = _make_minimal_target(tmp_path)
    assert _run_install(target, args=["--profile", "api"]).returncode == 0

    config = target / ".slopstopper.yml"
    config.write_text(
        config.read_text().replace(
            "  enabled: []", "  enabled: [ss-reliability-seo-check.yml]"
        )
    )
    result = _run_install(target)
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"

    installed = _installed(target)
    assert "ss-reliability-seo-check.yml" in installed
    assert "ss-reliability-core-web-vitals.yml" not in installed


def test_profile_key_is_appended_to_a_config_that_predates_it(tmp_path):
    """An adopter upgrading from a pre-profile release has no such key."""
    target = _make_minimal_target(tmp_path)
    (target / ".slopstopper.yml").write_text("urls:\n  production: https://example.com\n")

    result = _run_install(target, args=["--profile", "library"])
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"

    body = (target / ".slopstopper.yml").read_text()
    assert _profile_key(target) == "library"
    assert "https://example.com" in body, "existing config must be preserved"


def test_unknown_profile_name_fails_before_touching_the_target(tmp_path):
    target = _make_minimal_target(tmp_path)
    result = _run_install(target, args=["--profile", "rest"])
    assert result.returncode != 0
    assert "Unknown --profile" in result.stdout + result.stderr
    assert not (target / ".github/workflows").exists(), "should fail before installing"


def test_no_profile_flag_leaves_an_existing_install_unchanged(tmp_path):
    """The key arriving in the schema must not change an install's shape.

    An unset `profile:` resolves to `ui`, which drops nothing.
    """
    target = _make_minimal_target(tmp_path)
    (target / ".slopstopper.yml").write_text("urls:\n  production:\n")

    result = _run_install(target)
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
    assert _profile_key(target) is None, "no flag → no key written"
    assert API_DROPPED <= _installed(target)


# When the profiles module cannot be resolved at all (no python3, or an
# unreadable cli/ tree, simulated here by breaking the interpreter), the
# installer must not quietly hand a repo the full `ui` workflow set it
# explicitly opted out of. PYTHONHOME pointing nowhere makes every
# `python3 -c …` in install.sh fail the way a missing interpreter would.
_BROKEN_PYTHON = {"PYTHONHOME": "/nonexistent-python-home"}


def test_unresolvable_profile_aborts_when_the_config_opted_out(tmp_path):
    target = _make_minimal_target(tmp_path)
    (target / ".slopstopper.yml").write_text("profile: api\n")
    result = _run_install(target, env_extra=_BROKEN_PYTHON)
    assert result.returncode != 0
    assert "profile 'api'" in result.stdout + result.stderr
    assert not _installed(target), "must abort before any workflow lands"


def test_unresolvable_profile_warns_and_installs_everything_for_the_default(tmp_path):
    target = _make_minimal_target(tmp_path)
    result = _run_install(target, env_extra=_BROKEN_PYTHON)
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
    assert "defaulting to 'ui'" in result.stdout + result.stderr
    assert "ss-reliability-smoke-tests.yml" in _installed(target)


# ── --no-task rewrites every Task invocation ─────────────────────


def test_no_task_leaves_no_task_invocation_in_any_workflow(tmp_path):
    """`task -x ss:…` (exit-code passthrough) must be rewritten as well as
    `task ss:…`, or --no-task installs still need Task."""
    target = _make_minimal_target(tmp_path)
    result = _run_install(target, args=["--no-task", "--no-hooks", "--no-skills"])
    assert result.returncode == 0, result.stderr
    workflows = target / ".github" / "workflows"
    leftovers = {
        p.name: line.strip()
        for p in workflows.glob("ss-*.yml")
        for line in p.read_text().splitlines()
        if "task ss:" in line or "task -x ss:" in line
    }
    assert not leftovers, leftovers
    sast = (workflows / "ss-security-sast-check.yml").read_text()
    assert "slopstopper run security:sast" in sast


# ── composite actions ────────────────────────────────────────────
#
# Every ss-* workflow starts with `uses: ./.github/actions/ss-setup`, so
# the actions must land next to the workflows or every check fails on
# its first step. They are plumbing, not checks: copied under every
# profile, refreshed wholesale, never tracked in the marker.


@pytest.mark.parametrize("profile", ["ui", "api", "library"])
def test_composite_actions_land_under_every_profile(tmp_path, profile):
    target = _make_minimal_target(tmp_path)
    result = _run_install(target, args=["--profile", profile, "--no-hooks", "--no-skills"])
    assert result.returncode == 0, result.stderr
    for action in ("ss-setup", "ss-resolve-url"):
        assert (target / ".github/actions" / action / "action.yml").is_file(), action
    marker = (target / ".ss/.workflows-installed").read_text()
    assert "ss-setup" not in marker


def test_composite_actions_are_refreshed_not_respected_as_deletions(tmp_path):
    """Deleting a workflow is a choice the installer respects; deleting the
    plumbing every workflow needs is not, so it comes back."""
    target = _make_minimal_target(tmp_path)
    assert _run_install(target, args=["--no-hooks", "--no-skills"]).returncode == 0
    action = target / ".github/actions/ss-setup/action.yml"
    action.write_text("# hand-edited\n")
    assert _run_install(target, args=["--no-hooks", "--no-skills"]).returncode == 0
    assert "hand-edited" not in action.read_text()
    assert "using: composite" in action.read_text()


# ── skills install as directories ────────────────────────────────


def test_skills_install_from_the_local_checkout_with_their_references(tmp_path):
    """Run from a checkout, install.sh copies each skill directory without
    the network, and every references/*.md the SKILL.md links comes with it."""
    target = _make_minimal_target(tmp_path)
    result = _run_install(target, args=["--no-hooks"])
    assert result.returncode == 0, result.stderr
    for skill in ("slopstopper-install", "slopstopper-triage"):
        skill_md = target / ".claude/skills" / skill / "SKILL.md"
        assert skill_md.is_file(), skill
        text = skill_md.read_text()
        assert text.startswith("---\n")
        for ref in set(re.findall(r"references/([A-Za-z0-9_.-]+\.md)", text)):
            assert (target / ".claude/skills" / skill / "references" / ref).is_file(), f"{skill}/{ref}"
    assert "installed" in result.stdout


def test_skills_are_refreshed_as_a_whole_directory(tmp_path):
    target = _make_minimal_target(tmp_path)
    assert _run_install(target, args=["--no-hooks"]).returncode == 0
    stray = target / ".claude/skills/slopstopper-install/references/stale.md"
    stray.write_text("# left over from an older skill\n")
    second = _run_install(target, args=["--no-hooks"])
    assert second.returncode == 0, second.stderr
    assert not stray.exists(), "a refresh must replace the directory, not merge into it"
    assert "refreshed" in second.stdout


def test_install_skill_sh_copies_skill_md_and_every_linked_reference(tmp_path):
    """The standalone script copies SKILL.md, then each references/*.md the
    map links, from the checkout it is run from: no SLOPSTOPPER_REPO_RAW,
    no network."""
    target = tmp_path / "adopter"
    target.mkdir()
    env = {k: v for k, v in os.environ.items() if k != "SLOPSTOPPER_REPO_RAW"}
    result = subprocess.run(
        ["bash", str(INSTALL_SKILL_SH), str(target)], capture_output=True, text=True, env=env,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    for skill in ("slopstopper-install", "slopstopper-triage"):
        text = (target / ".claude/skills" / skill / "SKILL.md").read_text()
        refs = set(re.findall(r"references/([A-Za-z0-9_.-]+\.md)", text))
        assert refs, skill
        for ref in refs:
            assert (target / ".claude/skills" / skill / "references" / ref).is_file(), f"{skill}/{ref}"
    assert "curl" not in result.stdout


def test_install_skill_sh_outside_a_checkout_says_to_run_from_one(tmp_path):
    lone = tmp_path / "lone"
    lone.mkdir()
    shutil.copy(INSTALL_SKILL_SH, lone / "install-skill.sh")
    target = tmp_path / "adopter"
    target.mkdir()
    env = {k: v for k, v in os.environ.items() if k != "SLOPSTOPPER_REPO_RAW"}
    result = subprocess.run(
        ["bash", str(lone / "install-skill.sh"), str(target)], capture_output=True, text=True, env=env,
    )
    assert result.returncode != 0
    assert "must run from a checkout" in result.stderr
    assert "git clone --branch" in result.stderr
    assert not (target / ".claude").exists()


def test_install_skill_sh_refuses_a_remote_source(tmp_path):
    target = tmp_path / "adopter"
    target.mkdir()
    result = subprocess.run(
        ["bash", str(INSTALL_SKILL_SH), str(target)], capture_output=True, text=True,
        env={**os.environ, "SLOPSTOPPER_REPO_RAW": "https://raw.example.com/slopstopper/main"},
    )
    assert result.returncode != 0
    assert "never fetches skills over the network" in result.stderr
    assert not (target / ".claude").exists()


def _skill_source(tmp_path):
    """A file:// copy of this repo's skills that a test can break."""
    src = tmp_path / "src"
    shutil.copytree(REPO_ROOT / ".claude/skills", src / ".claude/skills")
    return src


def _run_install_skill(target, src, umask=None):
    cmd = f'bash "{INSTALL_SKILL_SH}" "{target}"'
    if umask is not None:
        cmd = f"umask {umask}; {cmd}"
    return subprocess.run(
        ["bash", "-c", cmd], capture_output=True, text=True,
        env={**os.environ, "SLOPSTOPPER_REPO_RAW": f"file://{src}"},
    )


def test_install_skill_sh_ships_only_the_files_the_map_links(tmp_path):
    """An editor swap file or an unlinked draft in the source tree never
    reaches an adopter, even though install.sh runs from a working checkout."""
    src = _skill_source(tmp_path)
    (src / ".claude/skills/slopstopper-install/references/wip.md").write_text("# draft\n")
    (src / ".claude/skills/slopstopper-install/.DS_Store").write_text("junk")
    target = tmp_path / "adopter"
    target.mkdir()
    result = _run_install_skill(target, src)
    assert result.returncode == 0, result.stdout + result.stderr
    installed = target / ".claude/skills/slopstopper-install"
    assert not (installed / "references/wip.md").exists()
    assert not (installed / ".DS_Store").exists()


def test_a_failed_skill_leaves_the_installed_copy_and_fails_loudly(tmp_path):
    src = _skill_source(tmp_path)
    target = tmp_path / "adopter"
    target.mkdir()
    assert _run_install_skill(target, src).returncode == 0
    installed = target / ".claude/skills/slopstopper-install"
    before = (installed / "SKILL.md").read_text()

    # Upstream changes the map but one linked reference is missing.
    skill_src = src / ".claude/skills/slopstopper-install"
    (skill_src / "SKILL.md").write_text(before + "\nnew upstream line\n")
    (skill_src / "references/verify.md").unlink()
    triage_md = src / ".claude/skills/slopstopper-triage/SKILL.md"
    triage_md.write_text(triage_md.read_text() + "\ntriage update\n")

    result = _run_install_skill(target, src)
    assert result.returncode == 1
    assert "slopstopper-install" in result.stdout + result.stderr
    assert (installed / "SKILL.md").read_text() == before, "a failed refresh must not touch the installed skill"
    assert (installed / "references/verify.md").is_file()
    # The other skill still refreshed, and nothing half-swapped is left beside them.
    assert "triage update" in (target / ".claude/skills/slopstopper-triage/SKILL.md").read_text()
    leftovers = [p.name for p in (target / ".claude/skills").iterdir() if "." in p.name]
    assert not leftovers, leftovers


def test_installed_skill_directories_follow_the_umask(tmp_path):
    """Staging in mktemp's 0700 directory used to leave the skills unreadable
    to anyone else sharing the checkout."""
    src = _skill_source(tmp_path)
    target = tmp_path / "adopter"
    target.mkdir()
    assert _run_install_skill(target, src, umask="022").returncode == 0
    for path in (
        target / ".claude/skills/slopstopper-install",
        target / ".claude/skills/slopstopper-install/references",
    ):
        assert stat.S_IMODE(path.stat().st_mode) == 0o755, path


# ── the config seed is a starter, not the schema ─────────────────
#
# install.sh used to copy the whole .slopstopper.yml.example verbatim as
# the adopter's config, when a typical repo touches about eight keys. The
# seed is templates/slopstopper.yml.starter; the example stays the
# schema reference, linked from the starter.

STARTER = REPO_ROOT / "templates" / "slopstopper.yml.starter"
EXAMPLE = REPO_ROOT / ".slopstopper.yml.example"


def _leaves(tree: dict, prefix: str = "") -> dict:
    """Dotted path -> value for every leaf of a parsed config."""
    out: dict = {}
    for k, v in tree.items():
        if isinstance(v, dict) and v:  # an empty mapping (`production:` with no value) is a leaf
            out |= _leaves(v, f"{prefix}{k}.")
        else:
            out[f"{prefix}{k}"] = v
    return out


def _keys(tree: dict) -> set[str]:
    """Every dotted key path, leaves and the mappings above them."""
    paths = set(_leaves(tree))
    return paths | {p.rsplit(".", n)[0] for p in paths for n in range(1, p.count(".") + 1)}


def test_the_starter_is_short_and_parses():
    from slopstopper import config

    assert STARTER.is_file()
    assert len(STARTER.read_text().splitlines()) <= 90, "the starter should stay under two screenfuls"
    tree = config._load_yaml_subset(STARTER)
    assert tree["profile"] == "ui"
    assert "urls" in tree and "pages" in tree and "workflows" in tree
    assert "security" in tree and "snyk" in tree["security"]


def test_every_starter_key_exists_in_the_schema_reference():
    """The starter is a subset of the schema, never a fork of it."""
    from slopstopper import config

    starter_keys = _keys(config._load_yaml_subset(STARTER))
    example_keys = _keys(config._load_yaml_subset(EXAMPLE))
    assert starter_keys <= example_keys, sorted(starter_keys - example_keys)


def test_the_starter_seeds_the_schemas_defaults():
    """A starter value that drifted from the schema default would pin every
    fresh install to a stale explicit value that overrides the new default."""
    from slopstopper import config

    starter = _leaves(config._load_yaml_subset(STARTER))
    example = _leaves(config._load_yaml_subset(EXAMPLE))
    drifted = {k: (v, example[k]) for k, v in starter.items() if k in example and example[k] != v}
    assert not drifted, drifted


def test_the_schema_reference_documents_the_snyk_keys_and_the_cli_pin():
    """The keys the four Snyk checks read, with their defaults, and the
    `cli:` block ss-setup reads."""
    from slopstopper import config
    from slopstopper.checks import _snyk, sbom

    example = _leaves(config._load_yaml_subset(EXAMPLE))
    for check in ("sast", "dependencies", "iac"):
        assert example[f"security.{check}.fail_on"] == _snyk.DEFAULT_FAIL_ON, check
    assert example["security.dependencies.include_dev"] is False
    assert example["security.sbom.format"] == sbom.DEFAULT_FORMAT
    assert example["security.sbom.allow_empty"] is False
    assert "security.snyk.org" in example
    assert example["cli.source"] == DEFAULT_CLI_SOURCE
    assert re.fullmatch(r"v\d+\.\d+\.\d+", example["cli.ref"])
    text = EXAMPLE.read_text()
    for stale in ("semgrep", "trivy", "zap", "dast", "pipx:slopstopper-cli", "doc-updater", "host_override"):
        assert stale not in text.lower(), stale


def test_first_install_seeds_the_starter_then_pins_the_cli(tmp_path):
    """The seeded config is the starter plus the one block install.sh owns."""
    target = _make_minimal_target(tmp_path)
    result = _run_install(target, args=["--no-hooks", "--no-skills"])
    assert result.returncode == 0, result.stderr
    seeded = (target / ".slopstopper.yml").read_text()
    assert seeded.startswith(STARTER.read_text())
    assert seeded != EXAMPLE.read_text()
    tail = seeded[len(STARTER.read_text()):]
    assert tail.lstrip().startswith("# ── slopstopper-cli pin")
    assert f"cli:\n  source: {DEFAULT_CLI_SOURCE}\n  ref: {_checkout_ref()}\n" in tail


def test_profile_flag_writes_into_the_seeded_starter(tmp_path):
    target = _make_minimal_target(tmp_path)
    result = _run_install(target, args=["--profile", "api", "--no-hooks", "--no-skills"])
    assert result.returncode == 0, result.stderr
    lines = (target / ".slopstopper.yml").read_text().splitlines()
    assert lines.count("profile: api") == 1
    assert "profile: ui" not in lines


def test_the_starter_describes_the_ui_api_holdback():
    starter = STARTER.read_text()
    assert "ship inert" not in starter
    assert "re-run install.sh" in starter and "format: meta" in starter


def test_the_starter_names_no_removed_tooling():
    starter = STARTER.read_text().lower()
    for stale in ("dast", "zap", "cloudflare worker", "hungovercoders", "pypi"):
        assert stale not in starter, stale


# ── legacy run-time copies under .ss/ (#389) ─────────────────────────────────

DATA_DIR = REPO_ROOT / "cli" / "slopstopper" / "data"


def test_unmodified_spec_copies_are_scrubbed_one_by_one(tmp_path):
    """CLI <= 0.17 auto-ejected only the specs a run used, so a partial set
    of byte-equal copies must go too; a customised spec stays an override."""
    target = _make_minimal_target(tmp_path)
    tests = target / ".ss" / "tests"
    tests.mkdir(parents=True)
    shutil.copy(DATA_DIR / "tests" / "smoke.spec.ts", tests / "smoke.spec.ts")
    shutil.copy(DATA_DIR / "playwright.config.js", target / ".ss" / "playwright.config.js")
    (tests / "e2e.spec.ts").write_text("// customised journeys\n")
    result = _run_install(target, args=["--no-hooks", "--no-skills"])
    assert result.returncode == 0, result.stderr
    assert not (tests / "smoke.spec.ts").exists()
    assert not (target / ".ss" / "playwright.config.js").exists()
    assert (tests / "e2e.spec.ts").read_text() == "// customised journeys\n"


def test_a_tests_dir_of_only_unmodified_copies_is_removed(tmp_path):
    target = _make_minimal_target(tmp_path)
    tests = target / ".ss" / "tests"
    tests.mkdir(parents=True)
    shutil.copy(DATA_DIR / "tests" / "accessibility.spec.ts", tests / "accessibility.spec.ts")
    result = _run_install(target, args=["--no-hooks", "--no-skills"])
    assert result.returncode == 0, result.stderr
    assert not tests.exists()


def test_gitignore_block_covers_the_playwright_run_dir(tmp_path):
    target = _make_minimal_target(tmp_path)
    assert _run_install(target, args=["--no-hooks", "--no-skills"]).returncode == 0
    assert ".ss/.run/" in (target / ".gitignore").read_text().splitlines()


# ── package.json seed, .gitignore block refresh, post-install copy ──


def test_a_repo_without_package_json_gets_a_minimal_one_of_its_own(tmp_path):
    target = _make_minimal_target(tmp_path)
    assert _run_install(target, args=["--no-hooks", "--no-skills"]).returncode == 0
    raw = (target / "package.json").read_text()
    pkg = json.loads(raw)
    assert pkg["name"] == "adopter"
    assert pkg["private"] is True
    assert "@playwright/test" in pkg["devDependencies"]
    assert "scripts" not in pkg
    assert "slopstopper" not in raw.lower() and "cloudflare" not in raw.lower()


def test_gitignore_block_ignores_node_modules(tmp_path):
    target = _make_minimal_target(tmp_path)
    assert _run_install(target, args=["--no-hooks", "--no-skills"]).returncode == 0
    assert "node_modules/" in (target / ".gitignore").read_text().splitlines()


OLD_GITIGNORE_BLOCK = (
    "# slopstopper begin\n"
    "**/.ss/reports/\n"
    "playwright-report/\n"
    "test-results/\n"
    ".lighthouseci/\n"
    "# slopstopper end\n"
)


def test_refresh_replaces_an_old_gitignore_block_and_keeps_adopter_lines(tmp_path):
    target = _make_minimal_target(tmp_path)
    (target / ".gitignore").write_text("dist/\n.env\n\n" + OLD_GITIGNORE_BLOCK + "\n# mine\ncoverage/\n")
    result = _run_install(target, args=["--no-hooks", "--no-skills"])
    assert result.returncode == 0, result.stderr
    assert "slopstopper block updated" in result.stdout
    text = (target / ".gitignore").read_text()
    template = (REPO_ROOT / "templates" / "gitignore.block").read_text()
    assert text == "dist/\n.env\n\n" + template + "\n# mine\ncoverage/\n"
    assert text.count("# slopstopper begin") == 1


def test_a_current_gitignore_block_is_left_byte_identical(tmp_path):
    target = _make_minimal_target(tmp_path)
    assert _run_install(target, args=["--no-hooks", "--no-skills"]).returncode == 0
    before = (target / ".gitignore").read_text()
    result = _run_install(target, args=["--no-hooks", "--no-skills"])
    assert "slopstopper block already current" in result.stdout
    assert (target / ".gitignore").read_text() == before


def test_a_gitignore_block_without_an_end_marker_is_left_alone(tmp_path):
    target = _make_minimal_target(tmp_path)
    hand_edited = "# slopstopper begin\n.lighthouseci/\nsecrets.txt\n"
    (target / ".gitignore").write_text(hand_edited)
    result = _run_install(target, args=["--no-hooks", "--no-skills"])
    assert result.returncode == 0, result.stderr
    assert "has no '# slopstopper end'" in result.stdout
    assert (target / ".gitignore").read_text() == hand_edited


def test_a_gitignore_that_mentions_the_begin_marker_without_the_line_is_left_alone(tmp_path):
    target = _make_minimal_target(tmp_path)
    hand_edited = "node_modules/\n# see the # slopstopper begin marker below, once added\n"
    (target / ".gitignore").write_text(hand_edited)
    result = _run_install(target, args=["--no-hooks", "--no-skills"])
    assert result.returncode == 0, result.stderr
    assert "no line that is exactly that marker" in result.stdout
    assert (target / ".gitignore").read_text() == hand_edited


def test_post_install_names_the_snyk_secret_and_the_next_steps(tmp_path):
    """The Snyk checks are inert until SNYK_TOKEN exists, so the banner says
    where it goes, what is optional, and the two commands that finish the
    local setup. Nothing about Cloudflare, Copilot or the doc-updater."""
    target = _make_minimal_target(tmp_path)
    result = _run_install(target, args=["--no-hooks", "--no-skills"])
    assert result.returncode == 0, result.stderr
    out = result.stdout
    assert "SNYK_TOKEN" in out and "service-account token" in out
    assert "SNYK_ORG" in out and "SNYK_API" in out
    assert "mise install" in out and "slopstopper doctor" in out
    assert "urls.production" in out
    assert f"slopstopper-cli pinned to {_checkout_ref()}" in out
    for stale in ("Cloudflare", "COPILOT_GITHUB_TOKEN", "doc-updater", "Doc Auto-Updater", "DAST", "PyPI", "curl", "mise use node@22"):
        assert stale not in out, stale
