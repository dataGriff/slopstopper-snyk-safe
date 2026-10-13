"""Tests for slopstopper.badges, the README badges block generator."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from slopstopper import badges


# ── owner/repo detection ──────────────────────────────────────────


def test_detect_owner_repo_uses_github_actions_env(monkeypatch):
    monkeypatch.setenv("GITHUB_REPOSITORY", "acme/widget")
    assert badges._detect_owner_repo() == ("acme", "widget")


def test_detect_owner_repo_falls_back_to_ssh_remote(monkeypatch):
    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)

    def fake_run(cmd, check, capture_output, text):
        return subprocess.CompletedProcess(
            cmd, 0, stdout="git@github.com:acme/widget.git\n", stderr=""
        )

    monkeypatch.setattr(badges.subprocess, "run", fake_run)
    assert badges._detect_owner_repo() == ("acme", "widget")


def test_detect_owner_repo_falls_back_to_https_remote(monkeypatch):
    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)

    def fake_run(cmd, check, capture_output, text):
        return subprocess.CompletedProcess(
            cmd, 0, stdout="https://github.com/acme/widget.git\n", stderr=""
        )

    monkeypatch.setattr(badges.subprocess, "run", fake_run)
    assert badges._detect_owner_repo() == ("acme", "widget")


def test_detect_owner_repo_handles_https_without_dot_git(monkeypatch):
    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)

    def fake_run(cmd, check, capture_output, text):
        return subprocess.CompletedProcess(
            cmd, 0, stdout="https://github.com/acme/widget\n", stderr=""
        )

    monkeypatch.setattr(badges.subprocess, "run", fake_run)
    assert badges._detect_owner_repo() == ("acme", "widget")


def test_detect_owner_repo_returns_none_when_git_fails(monkeypatch):
    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)

    def fake_run(cmd, check, capture_output, text):
        raise subprocess.CalledProcessError(128, cmd)

    monkeypatch.setattr(badges.subprocess, "run", fake_run)
    assert badges._detect_owner_repo() == (None, None)


def test_detect_owner_repo_returns_none_for_non_github_remote(monkeypatch):
    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)

    def fake_run(cmd, check, capture_output, text):
        return subprocess.CompletedProcess(
            cmd, 0, stdout="https://gitlab.com/acme/widget.git\n", stderr=""
        )

    monkeypatch.setattr(badges.subprocess, "run", fake_run)
    assert badges._detect_owner_repo() == (None, None)


# ── workflow listing + grouping ──────────────────────────────────


def test_list_installed_workflows_scans_directory(isolated_cwd):
    workflows = Path(".github/workflows")
    workflows.mkdir(parents=True)
    (workflows / "ss-security-sast-check.yml").write_text("name: SAST")
    (workflows / "ss-hygiene-complexity-check.yml").write_text("name: Complexity")
    (workflows / "ci.yml").write_text("name: Other")  # non-ss, ignored
    listed = badges._list_installed_workflows()
    assert listed == [
        "ss-hygiene-complexity-check.yml",
        "ss-security-sast-check.yml",
    ]


def test_list_installed_workflows_returns_empty_when_no_dir(isolated_cwd):
    assert badges._list_installed_workflows() == []


def test_group_workflow_uses_curated_display():
    assert badges.group_workflow("ss-security-sast-check.yml") == ("security", "SAST")
    assert badges.group_workflow("ss-security-dependencies-check.yml") == (
        "security",
        "Dependency CVEs",
    )
    assert badges.group_workflow("ss-security-sbom-check.yml") == ("security", "SBOM")
    assert badges.group_workflow("ss-workflow-failure-issue.yml") == (
        "operational",
        "Workflow Failures",
    )


def test_group_workflow_falls_back_to_filename_derivation():
    # An imaginary new workflow not yet in WORKFLOW_DISPLAY:
    group, display = badges.group_workflow("ss-security-future-thing-check.yml")
    assert group == "security"
    assert display == "Future Thing"


# ── full generate() ──────────────────────────────────────────────


def test_generate_with_explicit_owner_repo(isolated_cwd):
    workflows = Path(".github/workflows")
    workflows.mkdir(parents=True)
    (workflows / "ss-security-sast-check.yml").write_text("")
    (workflows / "ss-hygiene-complexity-check.yml").write_text("")
    block = badges.generate(owner="acme", repo="widget")
    assert "## Pipeline status" in block
    assert badges.ADVERT_BADGE in block
    assert "### 🔒 Security" in block
    assert "### 🧹 Hygiene" in block
    assert "[![SAST]" in block
    assert "[![Complexity]" in block
    assert "github.com/acme/widget/actions/workflows/ss-security-sast-check.yml" in block
    # Empty groups (reliability / operational) should NOT appear:
    assert "### ✅ Reliability" not in block
    assert "### 🤖 Operational" not in block


def test_generate_no_advert_skips_advert_badge(isolated_cwd):
    workflows = Path(".github/workflows")
    workflows.mkdir(parents=True)
    (workflows / "ss-security-sast-check.yml").write_text("")
    block = badges.generate(owner="acme", repo="widget", no_advert=True)
    assert badges.ADVERT_BADGE not in block
    assert "## Pipeline status" in block  # header still present


def test_generate_raises_when_owner_repo_undetectable(monkeypatch, isolated_cwd):
    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
    monkeypatch.setattr(
        badges, "_detect_owner_repo", lambda: (None, None)
    )
    with pytest.raises(ValueError, match="Could not detect owner/repo"):
        badges.generate()


def test_generate_raises_when_no_workflows_installed(isolated_cwd):
    Path(".github/workflows").mkdir(parents=True)
    # Empty directory
    with pytest.raises(ValueError, match="No slopstopper workflows installed"):
        badges.generate(owner="acme", repo="widget")


def test_generate_groups_workflows_in_canonical_order(isolated_cwd):
    workflows = Path(".github/workflows")
    workflows.mkdir(parents=True)
    # Create one workflow per group in non-canonical order:
    (workflows / "ss-workflow-failure-issue.yml").write_text("")
    (workflows / "ss-reliability-smoke-tests.yml").write_text("")
    (workflows / "ss-hygiene-complexity-check.yml").write_text("")
    (workflows / "ss-security-sast-check.yml").write_text("")
    block = badges.generate(owner="acme", repo="widget", no_advert=True)
    # Security must come before hygiene must come before reliability must come
    # before operational.
    security_pos = block.index("### 🔒 Security")
    hygiene_pos = block.index("### 🧹 Hygiene")
    reliability_pos = block.index("### ✅ Reliability")
    operational_pos = block.index("### 🤖 Operational")
    assert security_pos < hygiene_pos < reliability_pos < operational_pos
