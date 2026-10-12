"""Guard: every check workflow appears in both `workflow_run` trigger lists.

Two workflows react to the others finishing, and GitHub does not support
globs in `workflow_run.workflows`, so each name must be spelled out:

- `ss-pr-summary.yml` re-renders the single rolling PR comment. A
  workflow left out still runs and still posts its own comment, but the
  summary silently stops re-rendering after it, so the headline count
  goes stale without anything going red.
- `ss-workflow-failure-issue.yml` raises a tracking issue when a check
  fails on main. A workflow left out can fail on main and nobody hears,
  which breaks the product's "nothing fails silently" promise in its own repo.
  When this test was added the list was eleven checks stale and named a
  workflow that no longer existed.

The requirement is documented in AGENTS.md and both skills; nothing
enforced it until this test, and adding checks is exactly when it bites.

Parsed with a small line reader rather than a YAML library: the CLI
has no YAML parser (lizard is its only runtime dependency) and the test
suite holds the same line.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS_DIR = REPO_ROOT / ".github/workflows"

# Workflows that are not checks:
#   ss-pr-summary               the PR summary itself
#   ss-workflow-failure-issue   reacts to failures; not a check
NON_CHECK_WORKFLOWS = {
    "ss-pr-summary.yml",
    "ss-workflow-failure-issue.yml",
}

# Each trigger list and the workflows it is allowed to leave out. Both
# lists watch exactly the check workflows: nothing else runs on main and
# produces a verdict worth summarising or raising an issue for.
TRIGGER_LISTS = {
    "ss-pr-summary.yml": NON_CHECK_WORKFLOWS,
    "ss-workflow-failure-issue.yml": NON_CHECK_WORKFLOWS,
}


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


def _workflow_name(path: Path) -> str | None:
    """The workflow's top-level `name:`, unquoted."""
    for line in path.read_text(encoding="utf-8").splitlines():
        match = re.match(r"^name:\s*(.+)$", line)
        if match:
            return _unquote(match.group(1))
    return None


def _trigger_list(workflow: Path) -> list[str]:
    """The `workflow_run.workflows` entries from a reacting workflow."""
    lines = workflow.read_text(encoding="utf-8").splitlines()
    names: list[str] = []
    inside = False
    for line in lines:
        if re.match(r"^\s+workflows:\s*$", line):
            inside = True
            continue
        if not inside:
            continue
        if re.match(r"^\s+#", line):
            continue
        match = re.match(r"^\s+-\s+(.+)$", line)
        if match:
            names.append(_unquote(match.group(1)))
        elif line.strip():
            break
    return names


def _expected_workflows(excluded: set[str]) -> dict[str, str]:
    """{filename: workflow name} for every ss-*.yml a trigger list must carry."""
    out = {}
    for path in sorted(WORKFLOWS_DIR.glob("ss-*.yml")):
        if path.name in excluded:
            continue
        name = _workflow_name(path)
        assert name, f"{path.name} has no top-level `name:`"
        out[path.name] = name
    return out


LISTS = sorted(TRIGGER_LISTS)


def test_the_fixture_finds_workflows():
    """Sanity: a broken glob would make every assertion below vacuous."""
    assert len(_expected_workflows(NON_CHECK_WORKFLOWS)) >= 15
    for name in LISTS:
        assert len(_trigger_list(WORKFLOWS_DIR / name)) >= 15


@pytest.mark.parametrize("reacting", LISTS)
def test_every_expected_workflow_is_in_the_trigger_list(reacting):
    triggers = set(_trigger_list(WORKFLOWS_DIR / reacting))
    expected = _expected_workflows(TRIGGER_LISTS[reacting])
    missing = {f: n for f, n in expected.items() if n not in triggers}
    assert not missing, (
        f"These workflows are missing from {reacting}'s `workflow_run.workflows` "
        f"list, so it will never react to them: {missing}"
    )


@pytest.mark.parametrize("reacting", LISTS)
def test_the_trigger_list_names_no_workflow_that_does_not_exist(reacting):
    """A renamed workflow leaves a dead entry behind, which is just as silent."""
    known = {_workflow_name(p) for p in WORKFLOWS_DIR.glob("ss-*.yml")}
    unknown = [n for n in _trigger_list(WORKFLOWS_DIR / reacting) if n not in known]
    assert not unknown, (
        f"{reacting} triggers on workflows that no longer exist under these names: {unknown}"
    )


@pytest.mark.parametrize("reacting", LISTS)
def test_the_trigger_list_has_no_duplicates(reacting):
    triggers = _trigger_list(WORKFLOWS_DIR / reacting)
    assert len(triggers) == len(set(triggers))


@pytest.mark.parametrize(
    "filename",
    [
        "ss-reliability-api-latency-check.yml",
        "ss-hygiene-openapi-check.yml",
        "ss-security-iac-check.yml",
        "ss-security-sbom-check.yml",
        "ss-security-dependencies-check.yml",
    ],
)
def test_the_newest_checks_are_covered(filename):
    """Named explicitly so a deletion can't quietly shrink the guard."""
    name = _workflow_name(WORKFLOWS_DIR / filename)
    for reacting in LISTS:
        assert name in set(_trigger_list(WORKFLOWS_DIR / reacting)), reacting


@pytest.mark.parametrize("reacting", LISTS)
@pytest.mark.parametrize(
    "stale",
    [
        "SlopStopper · DAST Analysis",
        "SlopStopper · Dependency review",
        "SlopStopper · Hygiene - Documentation Updater",
        "SlopStopper · Cloudflare Deployed Checks",
    ],
)
def test_removed_workflows_are_not_listed(reacting, stale):
    """The Snyk fork dropped ZAP, dependency-review, the doc updater and the
    Cloudflare trigger. A dead entry is harmless to GitHub and misleading
    to a reader, so it stays out."""
    assert stale not in _trigger_list(WORKFLOWS_DIR / reacting)
