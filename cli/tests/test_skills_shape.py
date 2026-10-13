"""Guard: the shipped skills stay short, and their references stay linked.

The repo caps its own agent entry files at 1,500 words because "agent
files crowd the context window of every agent conversation". The two
skills it ships into adopter repos had reached 10,061 and 7,128 words,
6.7× and 4.8× that cap, loaded whole on every trigger. They are now a
short SKILL.md that maps the playbook, plus references/*.md read only
when a step needs them. This keeps it that way.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
SKILLS_DIR = REPO_ROOT / ".claude/skills"
SKILLS = sorted(p.name for p in SKILLS_DIR.iterdir() if (p / "SKILL.md").is_file())

# The same standard the repo applies to README.md / AGENTS.md / CLAUDE.md.
MAX_SKILL_WORDS = 1500


def _skill_md(skill: str) -> str:
    return (SKILLS_DIR / skill / "SKILL.md").read_text(encoding="utf-8")


def _references(skill: str) -> list[Path]:
    return sorted((SKILLS_DIR / skill / "references").glob("*.md"))


def test_the_two_shipped_skills_are_present():
    assert SKILLS == ["slopstopper-install", "slopstopper-triage"]


@pytest.mark.parametrize("skill", SKILLS)
def test_skill_md_has_frontmatter(skill):
    text = _skill_md(skill)
    assert text.startswith("---\n")
    assert re.search(r"^name: " + re.escape(skill) + r"$", text, re.M)
    assert re.search(r"^description: .{40,}", text, re.M)


@pytest.mark.parametrize("skill", SKILLS)
def test_skill_md_is_a_map_not_a_manual(skill):
    words = len(_skill_md(skill).split())
    assert words <= MAX_SKILL_WORDS, (
        f"{skill}/SKILL.md is {words} words; the cap is {MAX_SKILL_WORDS}. Move detail into "
        "references/<name>.md and link it from the step that needs it."
    )


@pytest.mark.parametrize("skill", SKILLS)
def test_every_reference_is_linked_from_skill_md_and_exists(skill):
    text = _skill_md(skill)
    mentioned = set(re.findall(r"references/([A-Za-z0-9_.-]+\.md)", text))
    on_disk = {p.name for p in _references(skill)}
    assert mentioned == on_disk, (
        f"{skill}: SKILL.md mentions {sorted(mentioned - on_disk)} that don't exist; "
        f"references on disk not mentioned: {sorted(on_disk - mentioned)}"
    )
    assert on_disk, f"{skill} has no references/, and the split is the point"


@pytest.mark.parametrize("skill", SKILLS)
def test_every_reference_says_what_it_is(skill):
    for ref in _references(skill):
        text = ref.read_text(encoding="utf-8")
        assert text.startswith("# "), f"{ref.name} has no H1"
        assert f"`{skill}` skill" in text, f"{ref.name} does not say which skill it belongs to"


@pytest.mark.parametrize("skill", SKILLS)
def test_references_carry_the_content_not_skill_md(skill):
    """The references are where the long tables live."""
    ref_words = sum(len(p.read_text(encoding="utf-8").split()) for p in _references(skill))
    assert ref_words > len(_skill_md(skill).split())
