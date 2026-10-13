"""Guards for the documentation layout rules that prose alone kept losing.

- This repo's own docs are the canonical AGENTS.md-first example, so the
  two docs checks must pass on the repository itself, not only on the
  scaffolds (``test_entry_file_templates.py``). A regression here is what
  an adopter would see after copying the pattern.
- The map is ``docs/README.md`` and nothing else: a ``docs/index.md`` is
  the pre-0.15 shape and is reported as legacy.
- A category README is a map, not a dumping ground: exactly one H1, with
  per-check detail extracted into sibling files. That every sibling is
  routed from a README is ``ss:hygiene:docs-structure``'s rule, which
  resolves link paths, so it is not repeated here.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from slopstopper import config
from slopstopper.checks import docs_structure, entry_files

REPO_ROOT = Path(__file__).resolve().parents[2]
DOCS = REPO_ROOT / "docs"
CATEGORIES = sorted(p.name for p in DOCS.iterdir() if p.is_dir())

_H1 = re.compile(r"^# ", re.M)
_FENCE = re.compile(r"^```.*?^```", re.M | re.S)

# decisions/README.md is the log AND the note template; the template's own
# H1 is what `task decisions:new` copies out, and decisions:validate checks
# for it. Its three H1s (title, log, template) are the format, not a dumping
# ground.
_EXTRA_H1_ALLOWED = {"decisions": 3}


@pytest.fixture
def repo_cwd(monkeypatch):
    """Run a check from the repo root, as `task ss:hygiene:*` does.

    Reports land under the gitignored `.ss/reports/`, exactly as a local run
    leaves them.
    """
    monkeypatch.chdir(REPO_ROOT)
    config.reload()
    yield REPO_ROOT
    config.reload()


def test_the_repo_passes_its_own_entry_files_check(repo_cwd, capsys):
    assert entry_files.run() == 0, capsys.readouterr().out


def test_the_repo_passes_its_own_docs_structure_check(repo_cwd, capsys):
    assert docs_structure.run() == 0, capsys.readouterr().out


def test_the_map_is_docs_readme_and_there_is_no_legacy_index():
    assert (DOCS / "README.md").is_file()
    assert not (DOCS / "index.md").exists(), "docs/index.md is the pre-0.15 map; docs/README.md is the map"


@pytest.mark.parametrize("category", CATEGORIES)
def test_category_readme_is_a_map_with_one_h1(category):
    readme = DOCS / category / "README.md"
    text = _FENCE.sub("", readme.read_text(encoding="utf-8"))  # `# comment` in bash blocks is not a heading
    h1s = len(_H1.findall(text))
    allowed = _EXTRA_H1_ALLOWED.get(category, 1)
    assert h1s, f"{readme.relative_to(REPO_ROOT)} has no H1, but a category README opens with its title"
    assert h1s <= allowed, (
        f"{readme.relative_to(REPO_ROOT)} has {h1s} H1s, but per-check detail "
        f"belongs in a sibling docs/{category}/<CHECK>.md that the README routes"
    )
