"""Tests for the hygiene:docs-structure check."""

from __future__ import annotations

import json
from pathlib import Path

from slopstopper.checks import docs_structure


def _route(target: str, holds: str = "detail") -> str:
    """A standalone explicit route (its own paragraph, so it never joins a table)."""
    return f"\nBefore you do the {Path(target).stem} thing, read [{target}]({target}) for {holds}.\n"


def test_rows_of_a_when_table_need_no_cue_word(isolated_cwd):
    _seed_agents()
    _seed_map("| fixing CI | Read [ci.md](ci.md) for what task ci runs |\n")
    _doc("ci.md")
    assert docs_structure.run() == 0


def _seed_map(rows: str = "") -> Path:
    docs = Path("docs")
    docs.mkdir(exist_ok=True)
    (docs / "README.md").write_text("# Docs index\n\n| When you are… | Do this |\n| - | - |\n" + rows)
    return docs


def _seed_agents(body: str = "") -> None:
    Path("AGENTS.md").write_text(
        "# Agents\n\n" + body +
        "\nFor any task not covered above, read [docs/README.md](docs/README.md) for the routing table.\n"
    )


def _doc(rel: str, body: str = "# Doc\n") -> Path:
    p = Path("docs") / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body)
    return p


def _violations() -> list[dict]:
    return json.loads(docs_structure.REPORT_JSON.read_text())["violations"]


def _types() -> list[str]:
    return sorted(v["type"] for v in _violations())


# ── settings ─────────────────────────────────────────────────────


def test_settings_defaults(isolated_cwd):
    s = docs_structure._settings()
    assert s["map_path"] == "docs/README.md"
    assert s["require_routed_docs"] is True
    assert s["max_route_depth"] == 3
    assert s["max_doc_lines"] == 300


def test_settings_share_the_map_path_with_entry_files(write_config):
    write_config("hygiene:\n  entry_files:\n    map_path: docs/MAP.md\n")
    assert docs_structure._settings()["map_path"] == "docs/MAP.md"


def test_legacy_require_indexed_docs_is_read_as_require_routed_docs(write_config, capsys):
    write_config("hygiene:\n  docs_structure:\n    require_indexed_docs: false\n")
    assert docs_structure._settings()["require_routed_docs"] is False
    assert "require_routed_docs" in capsys.readouterr().out


# ── the happy path ───────────────────────────────────────────────


def test_run_clean_flat_docs_routed_from_the_map(isolated_cwd, capsys):
    _seed_agents()
    _seed_map(_route("ci.md") + _route("tasks.md"))
    _doc("ci.md")
    _doc("tasks.md")
    assert docs_structure.run() == 0
    data = json.loads(docs_structure.REPORT_JSON.read_text())
    assert data["valid"] is True
    assert data["doc_count"] == 2
    assert {r["doc"]: r["depth"] for r in data["routes"]} == {
        "docs/README.md": 1, "docs/ci.md": 2, "docs/tasks.md": 2,
    }
    assert docs_structure.REPORT_MD.exists()


def test_a_doc_routed_directly_from_agents_is_one_hop(isolated_cwd):
    _seed_agents("Before you change CI, read [docs/ci.md](docs/ci.md) for what task ci runs.\n")
    _seed_map()
    _doc("ci.md")
    assert docs_structure.run() == 0
    routes = {r["doc"]: r for r in json.loads(docs_structure.REPORT_JSON.read_text())["routes"]}
    assert routes["docs/ci.md"] == {"doc": "docs/ci.md", "depth": 1, "via": "AGENTS.md"}


def test_a_directory_readme_routes_its_own_subtree(isolated_cwd):
    _seed_agents()
    _seed_map(_route("security/README.md", "the security checks"))
    _doc("security/README.md", "# Security\n\n" + _route("DAST.md"))
    _doc("security/DAST.md")
    assert docs_structure.run() == 0
    routes = {r["doc"]: r["depth"] for r in json.loads(docs_structure.REPORT_JSON.read_text())["routes"]}
    assert routes["docs/security/DAST.md"] == 3


def test_excluded_docs_need_no_route_or_line_budget(write_config):
    write_config("hygiene:\n  docs:\n    exclude: [docs/course/**]\n")
    _seed_agents()
    _seed_map()
    _doc("course/01-intro.md", "# Lesson\n" + "line\n" * 400)
    _doc("archive/old.md")
    assert docs_structure.run() == 1
    assert [v["type"] for v in _violations()] == ["unrouted_doc"]
    assert "archive/old.md" in _violations()[0]["message"]
    data = json.loads(docs_structure.REPORT_JSON.read_text())
    assert data["excluded"] == ["docs/course/01-intro.md"]
    assert "`docs/course/01-intro.md`" in docs_structure.REPORT_MD.read_text()


def test_the_archive_is_excluded_by_default(isolated_cwd):
    _seed_agents()
    _seed_map()
    _doc("archive/old.md")
    assert docs_structure.run() == 0


def test_run_works_without_an_agents_file(isolated_cwd):
    _seed_map(_route("ci.md"))
    _doc("ci.md")
    assert docs_structure.run() == 0


# ── unrouted docs ────────────────────────────────────────────────


def test_an_unlinked_doc_is_unrouted(isolated_cwd):
    _seed_agents()
    _seed_map()
    _doc("ORPHAN.md")
    assert docs_structure.run() == 1
    [v] = _violations()
    assert v["type"] == "unrouted_doc"
    assert v["path"] == "docs/ORPHAN.md"
    assert "not linked from AGENTS.md, the map, or a README above it" in v["message"]
    assert "Add a route to docs/README.md" in v["message"]


def test_a_softly_linked_doc_is_unrouted_and_says_so(isolated_cwd):
    _seed_agents()
    _seed_map(_route("hygiene/README.md"))
    _doc("hygiene/README.md", "# Hygiene\n\n## Contents\n\n- [DETAIL.md](DETAIL.md): more detail\n")
    _doc("hygiene/DETAIL.md")
    assert docs_structure.run() == 1
    [v] = _violations()
    assert v["type"] == "unrouted_doc"
    assert "linked from docs/hygiene/README.md without a trigger or 'read'" in v["message"]
    assert "Add a route to docs/hygiene/README.md" in v["message"]


def test_a_directory_readme_cannot_route_a_doc_outside_its_subtree(isolated_cwd):
    """A route in docs/hygiene/README.md to ../security/DAST.md is a cross-reference, not a route."""
    _seed_agents()
    _seed_map(_route("hygiene/README.md"))
    _doc("hygiene/README.md", "# Hygiene\n\n" + _route("../security/DAST.md"))
    _doc("security/DAST.md")
    assert docs_structure.run() == 1
    [v] = _violations()
    assert v["path"] == "docs/security/DAST.md"
    assert "routed from docs/hygiene/README.md, which is outside its subtree" in v["message"]
    assert "Add a route to docs/README.md" in v["message"]  # docs/security/ has no README here, so the map


def test_an_unrouted_directory_readme_leaves_its_docs_unrouted_too(isolated_cwd):
    _seed_agents()
    _seed_map()
    _doc("security/README.md", "# Security\n\n" + _route("IAC.md"))
    _doc("security/IAC.md")
    assert docs_structure.run() == 1
    assert [v["path"] for v in _violations()] == ["docs/security/IAC.md", "docs/security/README.md"]
    readme = _violations()[1]["message"]
    assert "Add a route to docs/README.md" in readme, "a directory README is routed by the map, not by itself"
    iac = _violations()[0]["message"]
    assert "routed from docs/security/README.md, but that file is not reachable itself" in iac


def test_require_routed_docs_false_turns_the_rule_off(write_config):
    write_config("hygiene:\n  docs_structure:\n    require_routed_docs: false\n")
    _seed_agents()
    _seed_map()
    _doc("ORPHAN.md")
    assert docs_structure.run() == 0


def test_link_forms_a_route_may_use(isolated_cwd):
    _seed_agents()
    _seed_map(
        '| a | Read [a](./A.md "Design notes") first |\n'
        "| b | When you need b, read [b](B.md#section) |\n"
    )
    _doc("A.md")
    _doc("B.md")
    assert docs_structure.run() == 0


# ── soft routes in the map, depth, line budget, legacy index, broken routes ──


def test_a_soft_link_in_the_map_is_a_violation_even_when_the_doc_is_routed_elsewhere(isolated_cwd):
    _seed_agents("Before you change CI, read [docs/ci.md](docs/ci.md) for what task ci runs.\n")
    _seed_map("See also [ci](ci.md).\n")
    _doc("ci.md")
    assert docs_structure.run() == 1
    assert _types() == ["soft_route"]
    assert 'docs/README.md line 5 → docs/ci.md: "See also' in _violations()[0]["message"]


def test_a_route_deeper_than_the_limit_fails(isolated_cwd):
    _seed_agents()
    _seed_map(_route("a/README.md"))
    _doc("a/README.md", "# a\n\n" + _route("b/README.md"))
    _doc("a/b/README.md", "# b\n\n" + _route("deep.md"))
    _doc("a/b/deep.md")
    assert docs_structure.run() == 1
    [v] = _violations()
    assert v["type"] == "route_too_deep"
    assert v["path"] == "docs/a/b/deep.md"
    assert "4 hops" in v["message"]


def test_max_route_depth_is_a_knob(write_config):
    write_config("hygiene:\n  docs_structure:\n    max_route_depth: 4\n")
    _seed_agents()
    _seed_map(_route("a/README.md"))
    _doc("a/README.md", "# a\n\n" + _route("b/README.md"))
    _doc("a/b/README.md", "# b\n\n" + _route("deep.md"))
    _doc("a/b/deep.md")
    assert docs_structure.run() == 0


def test_a_non_utf8_doc_is_counted_not_crashed(isolated_cwd):
    _seed_agents()
    _seed_map(_route("latin1.md"))
    Path("docs/latin1.md").write_bytes(b"# \xc4nderungen\n" + b"line\n" * 301)
    assert docs_structure.run() == 1
    assert _types() == ["doc_over_lines"]


def test_a_topic_doc_over_the_line_budget_fails(isolated_cwd):
    _seed_agents()
    _seed_map(_route("long.md"))
    _doc("long.md", "line\n" * 301)
    assert docs_structure.run() == 1
    [v] = _violations()
    assert v["type"] == "doc_over_lines"
    assert "301 lines (limit 300)" in v["message"]


def test_the_map_itself_is_exempt_from_the_line_budget(isolated_cwd):
    _seed_agents()
    _seed_map("| x | Read [x](x.md) when x |\n" * 400)
    _doc("x.md")
    assert docs_structure.run() == 0


def test_max_doc_lines_zero_disables_the_rule(write_config):
    write_config("hygiene:\n  docs_structure:\n    max_doc_lines: 0\n")
    _seed_agents()
    _seed_map(_route("long.md"))
    _doc("long.md", "line\n" * 500)
    assert docs_structure.run() == 0


def test_a_legacy_index_beside_the_map_is_reported(isolated_cwd):
    _seed_agents()
    _seed_map()
    Path("docs/index.md").write_text("# old map\n")
    assert docs_structure.run() == 1
    types = _types()
    assert "legacy_index" in types
    assert "unrouted_doc" in types  # it is also just a doc nothing routes to


def test_a_route_to_a_missing_file_is_broken(isolated_cwd):
    _seed_agents()
    _seed_map(_route("gone.md"))
    assert docs_structure.run() == 1
    [v] = _violations()
    assert v["type"] == "broken_route"
    assert "routes to docs/gone.md, which does not exist" in v["message"]


# ── exit 2 ───────────────────────────────────────────────────────


def test_run_returns_two_when_docs_dir_missing(isolated_cwd, capsys):
    assert docs_structure.run() == 2
    assert "docs/ directory not found" in capsys.readouterr().out


def test_run_returns_two_when_map_missing_and_names_a_legacy_index(isolated_cwd, capsys):
    Path("docs").mkdir()
    Path("docs/index.md").write_text("# old\n")
    assert docs_structure.run() == 2
    out = capsys.readouterr().out
    assert "docs/README.md not found" in out
    assert "rename it to docs/README.md" in out


# ── report rendering ─────────────────────────────────────────────


def test_format_violations_groups_by_type():
    out = docs_structure._format_violations_content([
        {"type": "unrouted_doc", "path": "docs/x.md", "message": "Unrouted doc: docs/x.md"},
        {"type": "doc_over_lines", "path": "docs/y.md", "message": "Doc over budget: docs/y.md"},
    ])
    assert "Found **2** violation(s)" in out
    assert "### Unrouted Docs" in out
    assert "### Docs Over the Line Budget" in out


def test_build_md_report_status_lines_and_routes_table():
    clean = {"violations": [], "valid": True, "violation_count": 0,
             "routes": [{"doc": "docs/ci.md", "depth": 2, "via": "docs/README.md"}]}
    md = docs_structure._build_md_report(clean, "2026-06-12 00:00:00 UTC")
    assert "✅ Every doc has an explicit route" in md
    assert "| `docs/ci.md` | 2 | `docs/README.md` |" in md
    bad = {"violations": [{"type": "unrouted_doc", "path": "docs/x.md", "message": "Unrouted doc: docs/x.md"}],
           "valid": False, "violation_count": 1, "routes": []}
    md = docs_structure._build_md_report(bad, "t")
    assert "❌ Documentation structure violations found" in md
    assert "Unrouted doc: docs/x.md" in md
    assert "No doc is reachable" in md
