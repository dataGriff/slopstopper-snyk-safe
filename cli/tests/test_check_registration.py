"""Guard: every check in the registry is wired into every surface it needs.

AGENTS.md's "New quality check" row lists the places a check has to be
registered: workflow, Taskfile shim, installer, profile map, badge
label, both `workflow_run` trigger lists, docs, tests. It is a prose
checklist in a table cell, and nothing enforced it: when the two API
checks in #333 landed they touched 24 files against a 13-item list, and
`reliability:cwv` had shipped with a badge, a workflow and a marketing
bullet but not one line of documentation.

This test turns the checklist into assertions keyed off `REGISTRY`, so
a check that exists in the CLI but is missing from any surface fails
here with a message naming the surface. The two `workflow_run` lists
are covered by `test_workflow_triggers.py`.

Parsed with small line readers rather than a YAML library: the CLI
has no YAML parser (lizard is its only runtime dependency) and the test
suite holds the same line.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from slopstopper import badges, profiles
from slopstopper.checks import REGISTRY
from slopstopper.checks.docs_accuracy import _TASKFILE_TASK_RE

from tests.test_workflow_triggers import NON_CHECK_WORKFLOWS, _expected_workflows, _trigger_list


REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS_DIR = REPO_ROOT / ".github/workflows"
TASKFILE = REPO_ROOT / "Taskfile.ss.yml"
INSTALL_SH = REPO_ROOT / "install.sh"
DOCS_DIR = REPO_ROOT / "docs"
TESTS_DIR = Path(__file__).resolve().parent / "checks"

CHECKS = sorted(REGISTRY)

# Where each check is documented: a phrase that must appear in a heading
# (`#`, `##` or `###`) in some markdown file under docs/<category>/. The
# phrase is deliberately the human name, not the check key, because the
# docs are written for people; a new check adds a row here and the test
# fails until the heading exists.
DOC_HEADINGS: dict[str, str] = {
    "hygiene:complexity": "Code Complexity",
    "hygiene:csp-exceptions": "CSP Exceptions",
    "hygiene:docs-accuracy": "Documentation Accuracy",
    "hygiene:docs-size": "Documentation Size",
    "hygiene:docs-structure": "Documentation Structure",
    "hygiene:entry-files": "Entry-File Budget",
    "hygiene:openapi": "OpenAPI Drift",
    "reliability:accessibility": "Accessibility",
    "reliability:api-health": "API Health",
    "reliability:api-latency": "API Latency",
    "reliability:broken-links": "Broken Link",
    "reliability:cwv": "Core Web Vitals",
    "reliability:llms-txt": "llms.txt",
    "reliability:robots-txt": "robots.txt",
    "reliability:seo": "SEO",
    "reliability:sitemap": "sitemap",
    "reliability:smoke": "Smoke Tests",
    "reliability:e2e": "E2E",
    "security:api-headers": "API Headers",
    "security:dependencies": "Dependency Vulnerability",
    "security:iac": "Infrastructure as Code",
    "security:sast": "SAST",
    "security:sbom": "Software Bill of Materials",
    "security:secrets": "Secrets Detection",
}


def _category(check: str) -> str:
    return check.split(":", 1)[0]


def _module_name(check: str) -> str:
    return REGISTRY[check].__module__.rsplit(".", 1)[-1]


def _taskfile_targets() -> set[str]:
    # The same regex docs-accuracy uses, so "what is a Task target" has one definition.
    return set(_TASKFILE_TASK_RE.findall(TASKFILE.read_text(encoding="utf-8")))


def _generic_workflows() -> set[str]:
    text = INSTALL_SH.read_text(encoding="utf-8")
    block = text[text.index("GENERIC_WORKFLOWS=(") : text.index("\n)\n", text.index("GENERIC_WORKFLOWS=("))]
    return set(re.findall(r'"(ss-[^"]+)"', block))


def _headings_under(category: str) -> list[str]:
    out: list[str] = []
    for md in sorted((DOCS_DIR / category).glob("*.md")):
        out += re.findall(r"^#{1,3} +(.+)$", md.read_text(encoding="utf-8"), re.M)
    return out


# ── sanity ───────────────────────────────────────────────────────


def test_the_registry_is_not_empty():
    assert len(CHECKS) >= 20


def test_every_registry_key_has_a_docs_heading_row():
    """The mapping above must grow with the registry."""
    assert set(DOC_HEADINGS) == set(CHECKS), (
        f"add a DOC_HEADINGS row for: {sorted(set(CHECKS) - set(DOC_HEADINGS))}; "
        f"remove: {sorted(set(DOC_HEADINGS) - set(CHECKS))}"
    )


# ── one assertion per surface ────────────────────────────────────


@pytest.mark.parametrize("check", CHECKS)
def test_check_has_a_workflow_in_the_profile_map(check):
    workflow = profiles.workflow_for(check)
    assert workflow, f"{check} is not in profiles.CHECK_WORKFLOWS"
    assert (WORKFLOWS_DIR / workflow).is_file(), (
        f"{check} maps to {workflow}, which does not exist under .github/workflows/"
    )


@pytest.mark.parametrize("check", CHECKS)
def test_check_has_a_taskfile_shim(check):
    assert check in _taskfile_targets(), f"Taskfile.ss.yml has no `{check}:` target"


@pytest.mark.parametrize("check", CHECKS)
def test_check_workflow_is_installed_by_install_sh(check):
    workflow = profiles.workflow_for(check)
    assert workflow in _generic_workflows(), (
        f"{workflow} is not in install.sh's GENERIC_WORKFLOWS, so adopters never get {check}"
    )


@pytest.mark.parametrize("check", CHECKS)
def test_check_workflow_has_a_curated_badge_label(check):
    workflow = profiles.workflow_for(check)
    assert workflow in badges.WORKFLOW_DISPLAY, (
        f"{workflow} has no badges.WORKFLOW_DISPLAY entry; its README badge would fall back "
        "to a title-cased filename"
    )
    group, _label = badges.WORKFLOW_DISPLAY[workflow]
    assert group == _category(check), f"{workflow} is badged under {group!r}, not {_category(check)!r}"


LABELER = REPO_ROOT / ".github/labeler.yml"


def _labeler_globs(label: str) -> list[str]:
    """The quoted globs under one top-level label in .github/labeler.yml.

    Line reader, same as the rest of this file: a top-level key is a line
    with no indent ending in a colon; the block runs to the next one.
    """
    globs: list[str] = []
    inside = False
    for line in LABELER.read_text(encoding="utf-8").splitlines():
        if re.match(r"^[A-Za-z0-9_-]+:\s*$", line):
            inside = line.rstrip(":").strip() == label
            continue
        if inside:
            globs += re.findall(r"'([^']+)'", line)
    return globs


@pytest.mark.parametrize("check", CHECKS)
def test_check_module_is_in_labeler_yml(check):
    """A PR that touches only the check's module must get the category label.

    The workflow and docs globs in .github/labeler.yml match by category
    prefix; the module filenames carry no category, so they are listed
    by hand and nothing but this test notices a missing or renamed one.
    """
    path = f"cli/slopstopper/checks/{_module_name(check)}.py"
    assert path in _labeler_globs(_category(check)), (
        f"{check}: add '{path}' under the `{_category(check)}:` label in .github/labeler.yml"
    )


@pytest.mark.parametrize("check", CHECKS)
def test_check_has_a_test_module(check):
    module = _module_name(check)
    assert (TESTS_DIR / f"test_{module}.py").is_file(), (
        f"{check} ({module}.py) has no cli/tests/checks/test_{module}.py"
    )


@pytest.mark.parametrize("check", CHECKS)
def test_check_is_documented_under_its_category(check):
    phrase = DOC_HEADINGS[check].lower()
    headings = _headings_under(_category(check))
    assert any(phrase in h.lower() for h in headings), (
        f"{check} has no heading containing {DOC_HEADINGS[check]!r} in any "
        f"docs/{_category(check)}/*.md, because a check with a badge and a workflow but no "
        "documentation is exactly what this test exists to catch"
    )


# ── the one check count ──────────────────────────────────────────
#
# "How many checks" was typed by hand in at least six places and showed
# 27 / 26 / 24 / 22 / ~18 / 21 / 20 simultaneously. The definition is:
# a check is a workflow ss-pr-summary.yml summarises. Everything else
# (the summary itself, the failure tracker) is plumbing.
#
# When these numbers change, the prose that quotes them has to follow.
# PROSE_SITES below is that list, and the count test reads each file so a
# stale number fails here rather than surviving in a playbook.

EXPECTED_CHECKS = 25
# every ss-*.yml: the check workflows plus the summary and the failure tracker
EXPECTED_INSTALLED_WORKFLOWS = 27

# Files that quote the counts, and the numbers each may pair with the word
# "check(s)" / "workflow(s)". A number outside the set is drift.
# Every skill file is a site: the split into SKILL.md + references/ moved
# count-bearing prose into files nobody would think to list by hand.
_SKILL_COUNTS = {"checks": {25, 16, 11}, "workflows": {27}}
PROSE_SITES = {
    **{
        str(p.relative_to(REPO_ROOT)): _SKILL_COUNTS
        for p in sorted((REPO_ROOT / ".claude/skills").glob("slopstopper-*/**/*.md"))
    },
    # 23: the worked example reads "2 of 25 checks failed … The other 23 checks".
    "docs/architecture/PR_FEEDBACK.md": {"checks": {25, 23}, "workflows": {25}},
    ".slopstopper.yml": {"checks": {25}, "workflows": set()},
}
# "24 checks", "27 new `ss-*.yml` workflows", "~21 GitHub Actions workflows":
# a number, at most one qualifier, then the noun. Nothing looser, or
# "2 of 24 checks" reads as a stale 2.
_COUNT_RE = re.compile(
    r"~?(\d+)\+?\s+(?:new\s+|`ss-\*\.yml`\s+|GitHub Actions\s+|check\s+)?(checks?|workflows?)\b"
)


def _installed_workflows() -> set[str]:
    return {p.name for p in WORKFLOWS_DIR.glob("ss-*.yml")}


def test_the_check_count_is_the_one_quoted_in_the_docs():
    checks = _installed_workflows() - NON_CHECK_WORKFLOWS
    assert len(checks) == EXPECTED_CHECKS, (
        f"{len(checks)} check workflows on disk; update EXPECTED_CHECKS and PROSE_SITES"
    )
    # The definition, not just the number: a check is what ss-pr-summary.yml summarises.
    summarised = _expected_workflows(NON_CHECK_WORKFLOWS)  # {filename: workflow name}
    assert set(summarised) == checks
    assert set(_trigger_list(WORKFLOWS_DIR / "ss-pr-summary.yml")) == set(summarised.values())
    assert len(_installed_workflows()) == EXPECTED_INSTALLED_WORKFLOWS


def test_every_skill_file_is_a_prose_site():
    skill_sites = [s for s in PROSE_SITES if s.startswith(".claude/skills/")]
    assert len(skill_sites) >= 10, "the skills glob matched almost nothing. Did .claude/skills move?"


@pytest.mark.parametrize("site", sorted(PROSE_SITES))
def test_prose_quotes_the_current_counts(site):
    allowed = PROSE_SITES[site]
    text = (REPO_ROOT / site).read_text(encoding="utf-8")
    stale = [
        m.group(0)
        for m in _COUNT_RE.finditer(text)
        if int(m.group(1)) not in allowed["checks" if m.group(2).startswith("check") else "workflows"]
    ]
    assert not stale, f"{site} quotes a count that is not current: {stale}"


@pytest.mark.parametrize(
    "profile,expected",
    [("ui", 25), ("api", 16), ("library", 11)],
)
def test_the_per_profile_check_count(profile, expected):
    """The install skill's shape table quotes these three numbers."""
    dropped = set(profiles.expand(profile) or [])
    assert dropped <= _installed_workflows(), f"{profile} drops a workflow that doesn't exist"
    assert EXPECTED_CHECKS - len(dropped) == expected
