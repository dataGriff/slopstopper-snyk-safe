"""Tests for the hygiene:entry-files check."""

from __future__ import annotations

import json
from pathlib import Path

from slopstopper.checks import _routes, entry_files


MAP_ROUTE = (
    "For any task not covered above, read [`docs/README.md`](./docs/README.md) "
    "which is the routing table for every doc in this repo.\n"
)
SOFT_MAP_LINK = "See [`docs/README.md`](./docs/README.md) for the docs.\n"


def _seed_entry_files(root: Path, contents: dict[str, str] | None = None) -> None:
    """Seed the trio with defaults that satisfy every rule."""
    defaults = {
        "README.md": f"# Project\n\nOne paragraph.\n\n{SOFT_MAP_LINK}",
        "AGENTS.md": f"# Agents\n\n- Run `task check` before committing.\n\n{MAP_ROUTE}",
        "CLAUDE.md": "@AGENTS.md\n",
    }
    for name in entry_files.ENTRY_FILES:
        (root / name).write_text((contents or {}).get(name, defaults[name]))


def _seed_map(root: Path, body: str = "# Docs index\n\n| When | Do |\n| - | - |\n") -> None:
    (root / "docs").mkdir(exist_ok=True)
    (root / "docs" / "README.md").write_text(body)


def _payload() -> dict:
    return json.loads(entry_files.REPORT_JSON.read_text())


# ── token estimate ───────────────────────────────────────────────


def test_estimate_tokens_is_chars_over_four():
    assert _routes.estimate_tokens("a" * 400) == 100


def test_estimate_tokens_excludes_badge_only_lines():
    badge = "[![CI](https://x/badge.svg)](https://x/actions)\n"
    prose = "Real words here.\n"
    assert _routes.estimate_tokens(badge * 10 + prose) == _routes.estimate_tokens(prose)


def test_estimate_tokens_keeps_a_badge_beside_prose():
    line = "Status: [![CI](https://x/badge.svg)](https://x/actions)\n"
    assert _routes.estimate_tokens(line) == len(line.rstrip("\n")) // 4


# ── route detection (shared helper) ─────────────────────────────


def test_route_table_separates_explicit_from_soft(isolated_cwd):
    p = isolated_cwd / "AGENTS.md"
    p.write_text(
        "Before you change CI, read [ci](docs/ci.md) for what task ci runs.\n\n"
        "See [tasks](docs/tasks.md) for more.\n\n"
        "| When you are… | Do this |\n| --- | --- |\n"
        "| adding a doc | Read [style](docs/style.md) first |\n"
    )
    explicit, soft = _routes.route_table(p)
    assert {d.name for d in explicit} == {"ci.md", "style.md"}
    assert {d.name for d in soft} == {"tasks.md"}


def test_route_table_joins_wrapped_lines_and_skips_fences(isolated_cwd):
    p = isolated_cwd / "AGENTS.md"
    p.write_text(
        "When you touch the worker,\nread [worker](docs/worker.md).\n\n"
        "```\nsee [nope](docs/nope.md)\n```\n"
    )
    explicit, soft = _routes.route_table(p)
    assert {d.name for d in explicit} == {"worker.md"}
    assert soft == {}


def test_route_table_a_soft_mention_beside_an_explicit_route_counts_as_routed(isolated_cwd):
    p = isolated_cwd / "AGENTS.md"
    p.write_text("See [ci](docs/ci.md).\n\nBefore you change CI, read [ci](docs/ci.md).\n")
    explicit, soft = _routes.route_table(p)
    assert len(explicit) == 1 and soft == {}


def test_a_routing_table_header_is_the_trigger_for_its_rows(isolated_cwd):
    """In a "When you are… | Do this" table the first cell is the trigger, so a
    row whose second cell says Read is an explicit route even without a cue word."""
    p = isolated_cwd / "AGENTS.md"
    p.write_text(
        "| When you are… | Do this |\n| --- | --- |\n"
        "| fixing a security check | Read [security](docs/security/README.md) |\n"
        "| cutting a release | See [release](docs/release.md) |\n\n"
        "| Check | Docs |\n| --- | --- |\n"
        "| SAST | Read [sast](docs/sast.md) |\n"
    )
    explicit, soft = _routes.route_table(p)
    assert {d.name for d in explicit} == {"README.md"}
    assert {d.name for d in soft} == {"release.md", "sast.md"}


def test_cues_must_come_in_order_trigger_read_link():
    assert _routes.is_explicit_route("Before you deploy, read [r](docs/r.md).")
    assert not _routes.is_explicit_route("Read [r](docs/r.md) when an incident happens.")
    assert not _routes.is_explicit_route("Read [r](docs/r.md) if you need details.")
    assert not _routes.is_explicit_route("Follow the [style guide](docs/style.md) unless told otherwise.")
    assert not _routes.is_explicit_route("| SAST | Read [sast](docs/sast.md) first |")


def test_a_cue_word_inside_the_link_target_does_not_count():
    assert not _routes.is_explicit_route("Read [w](docs/when.md).")
    assert not _routes.is_explicit_route('<a href="docs/before-read.md">x</a>')


def test_each_link_is_judged_at_its_own_position(isolated_cwd):
    p = isolated_cwd / "AGENTS.md"
    p.write_text("See [a](docs/a.md); then, before you ship, read [b](docs/b.md).\n")
    explicit, soft = _routes.route_table(p)
    assert {d.name for d in explicit} == {"b.md"}
    assert {d.name for d in soft} == {"a.md"}


def test_reference_style_and_html_links_are_links(isolated_cwd):
    p = isolated_cwd / "AGENTS.md"
    p.write_text(
        "Before deployment, read [the runbook][deploy].\n\n"
        "See [notes][]. \n\n"
        'When you edit CI, read <a href="docs/ci.md">ci</a>.\n\n'
        "[deploy]: docs/DEPLOY.md\n[notes]: docs/notes.md\n"
    )
    explicit, soft = _routes.route_table(p)
    assert {d.name for d in explicit} == {"DEPLOY.md", "ci.md"}
    assert {d.name for d in soft} == {"notes.md"}


def test_tilde_fences_are_skipped_too():
    assert _routes.logical_lines("Para one\n~~~\nsee [x](docs/x.md)\n~~~\nafter\n") == ["Para one", "after"]


def test_only_the_first_row_of_a_table_can_be_its_header():
    rows = _routes.logical_lines(
        "| Command | Does |\n| --- | --- |\n| `task first` | bootstrap |\n| `task docs` | Read [d](docs/d.md) |\n"
    )
    assert rows == ["| Command | Does |", "| `task first` | bootstrap |", "| `task docs` | Read [d](docs/d.md) |"]
    assert not _routes.is_explicit_route(rows[2])


def test_a_flag_row_is_not_a_header_separator():
    rows = _routes.logical_lines("| Flag | Does |\n| --- | --- |\n| --no-task | when you use it, read [ci](docs/ci.md) |\n")
    assert rows[-1].startswith("| --no-task |")
    assert _routes.is_explicit_route(rows[-1])


def test_a_pipeless_gfm_table_is_still_a_table(isolated_cwd):
    p = isolated_cwd / "AGENTS.md"
    p.write_text(
        "When you are… | Do this\n--- | ---\n"
        "fixing CI | Read [ci](docs/ci.md)\nother | see [z](docs/z.md)\n"
    )
    explicit, soft = _routes.route_table(p)
    assert {d.name for d in explicit} == {"ci.md"}
    assert {d.name for d in soft} == {"z.md"}


def test_cues_are_scoped_to_the_sentence_that_holds_the_link():
    para = (
        "AGENTS.md first: the file carries what most tasks need. "
        'The form is trigger → "read" → file. '
        "Two checks keep it honest: [entry-files](hygiene/README.md) budgets it."
    )
    assert not _routes.is_explicit_route(para)
    assert _routes.is_explicit_route("Agents skip soft links. Before you ship, read [x](docs/x.md).")


def test_html_comments_are_not_routes():
    assert _routes.logical_lines("<!-- Before you X, read [h](docs/h.md) -->\nlive text\n") == ["live text"]
    assert _routes.logical_lines("a <!-- multi\nline --> b\n") == ["a  b"]


def test_a_nested_bullet_continues_its_parent(isolated_cwd):
    p = isolated_cwd / "AGENTS.md"
    p.write_text("- When you change CI:\n  - read [ci](docs/ci.md)\n  - then run `task ci`\n")
    explicit, soft = _routes.route_table(p)
    assert {d.name for d in explicit} == {"ci.md"} and soft == {}


def test_non_utf8_docs_do_not_crash(isolated_cwd):
    p = isolated_cwd / "AGENTS.md"
    p.write_bytes(b"Before you ship, read [x](docs/x.md) \xe4nderungen\n")
    explicit, _soft = _routes.route_table(p)
    assert {d.name for d in explicit} == {"x.md"}


def test_soft_routes_are_labelled_by_path(isolated_cwd):
    (isolated_cwd / "docs" / "a").mkdir(parents=True)
    (isolated_cwd / "docs" / "b").mkdir()
    (isolated_cwd / "AGENTS.md").write_text("See [a](docs/a/README.md) and [b](docs/b/README.md).\n")
    labels = entry_files._soft_routes("AGENTS.md", entry_files._settings())
    assert [l.split(":")[0] for l in labels] == [
        "AGENTS.md line 1 → docs/a/README.md",
        "AGENTS.md line 1 → docs/b/README.md",
    ]


def test_soft_routes_name_the_source_line_and_the_target(isolated_cwd):
    (isolated_cwd / "docs").mkdir()
    (isolated_cwd / "AGENTS.md").write_text(
        "# Agents\n\n<!-- a\nmulti-line comment -->\nIntro text.\n\n"
        "- Read [map](docs/README.md) when stuck.\n"
        "- See [ci](docs/ci.md) and\n  [deploy](docs/deploy.md).\n"
    )
    labels = entry_files._soft_routes("AGENTS.md", entry_files._settings())
    assert labels == [
        'AGENTS.md line 7 → docs/README.md: "- Read [map](docs/README.md) when stuck."',
        'AGENTS.md line 8 → docs/ci.md: "- See [ci](docs/ci.md) and [deploy](docs/deploy.md)."',
        'AGENTS.md line 8 → docs/deploy.md: "- See [ci](docs/ci.md) and [deploy](docs/deploy.md)."',
    ]


def test_over_budget_advice_fits_the_file():
    assert "@AGENTS.md" in entry_files._over_budget_advice("CLAUDE.md", "docs/README.md")
    assert "routing table" in entry_files._over_budget_advice("docs/README.md", "docs/README.md")
    assert "orientation" in entry_files._over_budget_advice("README.md", "docs/README.md")
    assert "route it from `AGENTS.md`" in entry_files._over_budget_advice("AGENTS.md", "docs/README.md")


def test_for_situation_counts_as_trigger_first():
    assert _routes.is_explicit_route("For incidents, read [r](RUNBOOK.md).")
    assert not _routes.is_explicit_route("Read [r](RUNBOOK.md) for details.")


# ── settings ─────────────────────────────────────────────────────


def test_settings_defaults(isolated_cwd):
    s = entry_files._settings()
    assert s["max_tokens"] == entry_files.DEFAULT_MAX_TOKENS
    assert s["readme_max_tokens"] == entry_files.DEFAULT_README_MAX_TOKENS
    assert s["map_max_tokens"] == entry_files.DEFAULT_MAP_MAX_TOKENS
    assert s["map_path"] == "docs/README.md"
    assert s["require_map_pointer"] is True
    assert s["require_explicit_routes"] is True
    assert s["require_claude_include"] is True


def test_settings_overrides(write_config):
    write_config(
        "hygiene:\n  entry_files:\n    max_tokens: 800\n    readme_max_tokens: 300\n"
        "    map_max_tokens: 500\n    map_path: docs/MAP.md\n    require_explicit_routes: false\n"
        "    require_claude_include: false\n"
    )
    s = entry_files._settings()
    assert (s["max_tokens"], s["readme_max_tokens"], s["map_max_tokens"]) == (800, 300, 500)
    assert s["map_path"] == "docs/MAP.md"
    assert s["require_explicit_routes"] is False
    assert s["require_claude_include"] is False


def test_settings_garbage_falls_back(write_config):
    write_config("hygiene:\n  entry_files:\n    max_tokens: bananas\n")
    assert entry_files._settings()["max_tokens"] == entry_files.DEFAULT_MAX_TOKENS


def test_legacy_max_words_is_ignored_with_a_note(write_config, capsys):
    write_config("hygiene:\n  entry_files:\n    max_words: 10\n")
    s = entry_files._settings()
    assert s["max_tokens"] == entry_files.DEFAULT_MAX_TOKENS
    assert "max_words" in capsys.readouterr().out


# ── measurement ──────────────────────────────────────────────────


def test_measure_returns_none_for_missing_file(isolated_cwd):
    assert entry_files._measure("nope.md", entry_files._settings()) is None


def test_measure_reports_tokens_and_rules(isolated_cwd):
    _seed_entry_files(isolated_cwd)
    _seed_map(isolated_cwd)
    m = entry_files._measure("AGENTS.md", entry_files._settings())
    assert m["file"] == "AGENTS.md"
    assert m["budget"] == entry_files.DEFAULT_MAX_TOKENS
    assert m["tokens"] == _routes.estimate_tokens(Path("AGENTS.md").read_text())
    assert m["over_budget"] is False
    assert m["pointer_ok"] is True
    assert m["soft_routes"] == []


def test_measure_flags_over_budget(isolated_cwd, write_config):
    write_config("hygiene:\n  entry_files:\n    readme_max_tokens: 50\n    require_map_pointer: false\n")
    (isolated_cwd / "README.md").write_text("word " * 100)
    m = entry_files._measure("README.md", entry_files._settings())
    assert m["over_budget"] is True
    assert m["tokens"] == 125
    assert m["headroom"] == -75


def test_readme_uses_its_own_budget_and_the_map_its_own(isolated_cwd):
    s = entry_files._settings()
    assert entry_files._budget_for("README.md", s) == entry_files.DEFAULT_README_MAX_TOKENS
    assert entry_files._budget_for("AGENTS.md", s) == entry_files.DEFAULT_MAX_TOKENS
    assert entry_files._budget_for("docs/README.md", s) == entry_files.DEFAULT_MAP_MAX_TOKENS


def test_measure_all_includes_the_map_row(isolated_cwd):
    _seed_entry_files(isolated_cwd)
    _seed_map(isolated_cwd)
    measurements, missing = entry_files._measure_all(entry_files._settings())
    assert [m["file"] for m in measurements] == ["README.md", "AGENTS.md", "CLAUDE.md", "docs/README.md"]
    assert missing == []


def test_measure_all_partitions_present_and_missing(isolated_cwd):
    (isolated_cwd / "README.md").write_text("hello world\n")
    (isolated_cwd / "AGENTS.md").write_text("a b c d e\n")
    measurements, missing = entry_files._measure_all(entry_files._settings())
    assert [m["file"] for m in measurements] == ["README.md", "AGENTS.md"]
    assert missing == ["CLAUDE.md"]


# ── rules ────────────────────────────────────────────────────────


def test_claude_must_be_a_pure_include(isolated_cwd):
    (isolated_cwd / "CLAUDE.md").write_text("Defer to [AGENTS.md](./AGENTS.md).\n\n@AGENTS.md\n")
    assert entry_files._pointer_violation("CLAUDE.md", entry_files._settings()) == "claude_not_pure_include"
    (isolated_cwd / "CLAUDE.md").write_text("\n@AGENTS.md\n\n")
    assert entry_files._pointer_violation("CLAUDE.md", entry_files._settings()) is None


def test_claude_rule_can_be_turned_off(write_config):
    write_config("hygiene:\n  entry_files:\n    require_claude_include: false\n")
    Path("CLAUDE.md").write_text("Anything at all.\n")
    assert entry_files._pointer_violation("CLAUDE.md", entry_files._settings()) is None


def test_agents_needs_an_explicit_route_to_the_map(isolated_cwd):
    _seed_map(isolated_cwd)
    (isolated_cwd / "AGENTS.md").write_text(SOFT_MAP_LINK)
    assert entry_files._pointer_violation("AGENTS.md", entry_files._settings()) == "missing_map_pointer"
    (isolated_cwd / "AGENTS.md").write_text(MAP_ROUTE)
    assert entry_files._pointer_violation("AGENTS.md", entry_files._settings()) is None


def test_readme_accepts_a_plain_link_to_the_map(isolated_cwd):
    _seed_map(isolated_cwd)
    (isolated_cwd / "README.md").write_text(SOFT_MAP_LINK)
    assert entry_files._pointer_violation("README.md", entry_files._settings()) is None


def test_anchored_map_link_still_counts(isolated_cwd):
    _seed_map(isolated_cwd)
    (isolated_cwd / "README.md").write_text("[map](./docs/README.md#routes)\n")
    assert entry_files._pointer_violation("README.md", entry_files._settings()) is None


def test_soft_link_to_map_is_enough_when_explicit_routes_are_off(write_config):
    write_config("hygiene:\n  entry_files:\n    require_explicit_routes: false\n")
    _seed_map(Path("."))
    Path("AGENTS.md").write_text(SOFT_MAP_LINK)
    assert entry_files._pointer_violation("AGENTS.md", entry_files._settings()) is None


def test_soft_routes_are_listed_for_agents_only(isolated_cwd):
    (isolated_cwd / "docs").mkdir()
    (isolated_cwd / "docs" / "ci.md").write_text("# ci\n")
    body = "See [ci](docs/ci.md).\n"
    (isolated_cwd / "AGENTS.md").write_text(body)
    (isolated_cwd / "README.md").write_text(body)
    s = entry_files._settings()
    assert entry_files._soft_routes("AGENTS.md", s) == ['AGENTS.md line 1 → docs/ci.md: "See [ci](docs/ci.md)."']
    assert entry_files._soft_routes("README.md", s) == []


# ── reports ──────────────────────────────────────────────────────


def _m(**over) -> dict:
    base = {
        "file": "README.md", "tokens": 100, "lines": 10, "budget": 600,
        "over_budget": False, "headroom": 500,
        "pointer_ok": True, "pointer_violation": None, "soft_routes": [],
    }
    return {**base, **over}


def test_build_md_report_includes_rows_cold_start_and_status(isolated_cwd):
    s = entry_files._settings()
    md = entry_files._build_md_report(
        [_m(), _m(file="AGENTS.md", tokens=2100, budget=2000, over_budget=True, headroom=-100),
         _m(file="docs/README.md", tokens=300, budget=1000, headroom=700)],
        "2026-06-12 00:00:00 UTC", "❌", s, False,
    )
    assert "📏 Entry-File Budget + AGENTS.md-first Report" in md
    assert "| `AGENTS.md` | 2100 | 10 | 2000 | ❌ over | -100 |" in md
    assert "Cold-start cost" in md
    assert "| always loaded | `AGENTS.md` (via `CLAUDE.md`) | 2100" in md
    assert "| uncovered task | both | 2400 |" in md
    assert "is over its token budget (2100 > 2000)" in md


def test_build_md_report_emits_paste_ready_fixes(isolated_cwd):
    s = entry_files._settings()
    md = entry_files._build_md_report(
        [_m(file="AGENTS.md", pointer_ok=False, pointer_violation="missing_map_pointer",
            soft_routes=['ci.md: "See ci"']),
         _m(file="CLAUDE.md", pointer_ok=False, pointer_violation="claude_not_pure_include")],
        "t", "❌", s, True,
    )
    assert "missing an explicit route to the map" in md
    assert "For any task not covered above, read [`docs/README.md`](./docs/README.md)" in md
    assert "has soft routes" in md
    assert "is not a pure include" in md
    assert "```markdown\n@AGENTS.md\n```" in md
    assert "`docs/README.md` does not exist" in md
    assert "| When you are… | Do this |" in md


def test_missing_map_fix_names_a_legacy_index(isolated_cwd):
    (isolated_cwd / "docs").mkdir()
    (isolated_cwd / "docs" / "index.md").write_text("# old map\n")
    md = entry_files._fix_map_missing("docs/README.md")
    assert "git mv docs/index.md docs/README.md" in md


def test_build_json_report_round_trips(isolated_cwd):
    s = entry_files._settings()
    payload = json.loads(entry_files._build_json_report([_m()], "2026-06-12 00:00:00 UTC", s, False, True))
    assert payload["generated_at"] == "2026-06-12 00:00:00 UTC"
    assert payload["budget_tokens"] == 2000
    assert payload["readme_budget_tokens"] == 600
    assert payload["map_budget_tokens"] == 1000
    assert payload["map_path"] == "docs/README.md"
    assert payload["map_file_present"] is True
    assert payload["measurements"] == [_m()]
    assert payload["cold_start"] == {"always_loaded": 0, "fallback_hop": 0, "uncovered_task": 100 * 0}
    assert payload["clean"] is True
    assert payload["violation_count"] == 0


def test_build_json_report_counts_violations_once_per_file(isolated_cwd):
    s = entry_files._settings()
    payload = json.loads(entry_files._build_json_report(
        [_m(file="AGENTS.md", over_budget=True, pointer_ok=False, pointer_violation="missing_map_pointer",
            soft_routes=["x"]),
         _m(file="CLAUDE.md")],
        "t", s, True, False,
    ))
    # AGENTS counts once (budget OR rule) + missing map file = 2
    assert payload["violation_count"] == 2
    assert payload["clean"] is False


# ── run ──────────────────────────────────────────────────────────


def test_run_clean_returns_zero_and_writes_both_reports(isolated_cwd, capsys):
    _seed_entry_files(isolated_cwd)
    _seed_map(isolated_cwd)
    assert entry_files.run() == 0
    assert entry_files.REPORT_MD.exists()
    payload = _payload()
    assert payload["clean"] is True
    assert payload["violation_count"] == 0
    assert "cold start:" in capsys.readouterr().out


def test_run_returns_one_when_over_budget(isolated_cwd, write_config, capsys):
    write_config("hygiene:\n  entry_files:\n    readme_max_tokens: 2\n    require_map_pointer: false\n")
    _seed_entry_files(isolated_cwd, {"README.md": "one two three four five\n"})
    assert entry_files.run() == 1
    assert _payload()["clean"] is False


def test_run_returns_one_when_readme_lacks_the_map_link(isolated_cwd, capsys):
    _seed_entry_files(isolated_cwd, {"README.md": "no pointer here\n"})
    _seed_map(isolated_cwd)
    assert entry_files.run() == 1
    flags = {m["file"]: m["pointer_ok"] for m in _payload()["measurements"]}
    assert flags == {"README.md": False, "AGENTS.md": True, "CLAUDE.md": True, "docs/README.md": True}


def test_run_returns_one_on_a_soft_route_in_agents(isolated_cwd, capsys):
    _seed_entry_files(isolated_cwd, {"AGENTS.md": f"{MAP_ROUTE}\nSee [ci](docs/ci.md).\n"})
    _seed_map(isolated_cwd)
    (isolated_cwd / "docs" / "ci.md").write_text("# ci\n")
    assert entry_files.run() == 1
    agents = next(m for m in _payload()["measurements"] if m["file"] == "AGENTS.md")
    assert agents["pointer_ok"] is True
    assert agents["soft_routes"] == ['AGENTS.md line 3 → docs/ci.md: "See [ci](docs/ci.md)."']


def test_run_returns_one_when_claude_is_not_a_pure_include(isolated_cwd, capsys):
    _seed_entry_files(isolated_cwd, {"CLAUDE.md": "Some prose.\n\n@AGENTS.md\n"})
    _seed_map(isolated_cwd)
    assert entry_files.run() == 1
    flags = {m["file"]: m["pointer_violation"] for m in _payload()["measurements"]}
    assert flags["CLAUDE.md"] == "claude_not_pure_include"


def test_run_returns_one_when_map_file_missing(isolated_cwd, capsys):
    _seed_entry_files(isolated_cwd)
    assert entry_files.run() == 1
    assert _payload()["map_file_present"] is False


def test_run_returns_one_when_map_over_its_budget(isolated_cwd, write_config, capsys):
    write_config("hygiene:\n  entry_files:\n    map_max_tokens: 5\n")
    _seed_entry_files(isolated_cwd)
    _seed_map(isolated_cwd, "# Docs index\n\n" + "row " * 50)
    assert entry_files.run() == 1
    map_row = next(m for m in _payload()["measurements"] if m["file"] == "docs/README.md")
    assert map_row["over_budget"] is True


def test_run_respects_disabled_rules(isolated_cwd, write_config, capsys):
    write_config(
        "hygiene:\n  entry_files:\n    require_map_pointer: false\n"
        "    require_explicit_routes: false\n    require_claude_include: false\n"
    )
    _seed_entry_files(isolated_cwd, {
        "README.md": "no pointer\n", "AGENTS.md": "See [x](docs/x.md)\n", "CLAUDE.md": "no pointer\n",
    })
    assert entry_files.run() == 0
    assert _payload()["clean"] is True


def test_run_returns_two_when_entry_file_missing(isolated_cwd, capsys):
    (isolated_cwd / "README.md").write_text("present\n")
    assert entry_files.run() == 2
    assert "AGENTS.md" in capsys.readouterr().out
