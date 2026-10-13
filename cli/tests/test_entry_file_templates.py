"""The entry-file scaffolds install.sh seeds must pass the checks they exist for.

A greenfield install copies `data/templates/entry-files/*` into the repo
root; the first `task ss:hygiene:test` on that repo must be green, or the
installer has shipped a cliff. These tests seed the scaffolds into a tmp
repo and run `hygiene:entry-files` and `hygiene:docs-structure` on them.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from slopstopper.checks import docs_structure, entry_files

TEMPLATES = Path(__file__).resolve().parents[1] / "slopstopper" / "data" / "templates" / "entry-files"
SCAFFOLDS = ("README.md", "AGENTS.md", "CLAUDE.md", "docs/README.md")


def _seed(root: Path) -> None:
    for rel in SCAFFOLDS:
        dst = root / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(TEMPLATES / rel, dst)


def test_the_scaffold_set_is_exactly_the_four_files():
    found = sorted(p.relative_to(TEMPLATES).as_posix() for p in TEMPLATES.rglob("*") if p.is_file())
    assert found == sorted(SCAFFOLDS), "install.sh seeds these four by name, so keep the set in step"


def test_scaffolds_pass_entry_files(isolated_cwd, capsys):
    _seed(isolated_cwd)
    assert entry_files.run() == 0, capsys.readouterr().out


def test_scaffolds_pass_docs_structure(isolated_cwd, capsys):
    _seed(isolated_cwd)
    assert docs_structure.run() == 0, capsys.readouterr().out


def test_claude_scaffold_is_the_pure_include():
    assert (TEMPLATES / "CLAUDE.md").read_text().strip() == entry_files.CLAUDE_INCLUDE
