"""Documentation structure validator. Every doc must have an explicit route.

Validates the AGENTS.md-first model declared in docs/README.md: `AGENTS.md`
carries what most tasks need and routes the rest; the docs map
(`docs/README.md`) is the fallback routing table; a directory README is
the map of a topic that split past its budget. A doc is *reachable* when
some routing file (`AGENTS.md`, the map, or a `README.md` above it under
`docs/`) links it on a line that names a trigger and says read. A doc
nothing routes to is invisible to agents, so it fails the build; so does a
soft link in the map, a route that runs more hops than an agent should
pay, and a topic doc too long to read in one sitting.

Configuration (.slopstopper.yml, optional):

    hygiene:
      entry_files:
        map_path: docs/README.md     # shared with hygiene:entry-files
      docs_structure:
        require_routed_docs: true    # every doc under docs/ has an explicit route
        max_route_depth: 3           # AGENTS.md → map → directory README → doc
        max_doc_lines: 300           # per topic doc (0 disables)

Docs matching `hygiene.docs.exclude` (default `docs/archive/**`, see
_docs.py) need no route and have no line budget; the report lists them.

The pre-0.15 `require_indexed_docs` knob is read as `require_routed_docs`
with a note. A `docs/index.md` beside the map is reported. The map is a
README so that the repo UI renders it in place.

Writes a JSON report (machine-readable, drives downstream tooling) and a
markdown report (human-readable).

Exit codes:
  0: clean
  1: violations
  2: docs/ or the map missing (no map to check against), or
      arguments were passed (this check takes none)
"""

from __future__ import annotations

import json
from collections import deque
from pathlib import Path

from slopstopper import config, output
from slopstopper.checks import _docs, _report, _routes
from slopstopper.checks._contract import reject_extra_args

DOCS_DIR = Path("docs")
AGENTS_FILE = Path("AGENTS.md")
LEGACY_INDEX = DOCS_DIR / "index.md"
DEFAULT_MAP_PATH = "docs/README.md"
DEFAULT_MAX_ROUTE_DEPTH = 3
DEFAULT_MAX_DOC_LINES = 300
REPORT_DIR = Path(".ss/reports/docs")
REPORT_JSON = REPORT_DIR / "docs-structure-report.json"
REPORT_MD = REPORT_DIR / "docs-structure-report.md"

# Consumed by `slopstopper emit hygiene:docs-structure --target {pr-comment,issue}`.
# Unchanged across the routing rewrite so the same bot comments/issues
# are matched.
META = {
    "report_path": str(REPORT_MD),
    "comment_discriminator": "📋 Documentation Structure",
    "issue_title": "📋 Documentation Structure Issues",
    "issue_labels": ["documentation-structure", "documentation"],
    "issue_followup": "🔔 Documentation structure issues detected again in commit",
}

ROUTE_FORM = _routes.ROUTE_TEMPLATE  # one copy, in _routes


# ── configuration ────────────────────────────────────────────────


def _settings() -> dict:
    legacy = config.get("hygiene.docs_structure.require_indexed_docs")
    if legacy is not None:
        output.warn(
            "hygiene.docs_structure.require_indexed_docs is now require_routed_docs "
            "(a doc must be *routed*, not merely linked), so it is read as that."
        )
    routed_default = config.get_bool("hygiene.docs_structure.require_indexed_docs", True)
    return {
        "map_path": config.get_str("hygiene.entry_files.map_path", DEFAULT_MAP_PATH),
        "require_routed_docs": config.get_bool("hygiene.docs_structure.require_routed_docs", routed_default),
        "max_route_depth": config.get_int("hygiene.docs_structure.max_route_depth", DEFAULT_MAX_ROUTE_DEPTH),
        "max_doc_lines": config.get_int("hygiene.docs_structure.max_doc_lines", DEFAULT_MAX_DOC_LINES),
    }


# ── the route graph ──────────────────────────────────────────────


def _rel(path: Path) -> str:
    try:
        return path.resolve().relative_to(Path.cwd().resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def _is_routing_file(path: Path, docs_dir: Path, map_file: Path) -> bool:
    """A README under docs/ may route the docs beside and below it."""
    if path == map_file.resolve():
        return True
    return path.name == "README.md" and docs_dir.resolve() in path.parents


def _may_route(source: Path, target: Path, docs_dir: Path, map_file: Path) -> bool:
    """AGENTS.md and the map route anything; a directory README only its own subtree."""
    if source == AGENTS_FILE.resolve() or source == map_file.resolve():
        return True
    return source.parent in target.parents


def _walk_routes(docs_dir: Path, map_file: Path) -> tuple[dict[Path, dict], list[dict]]:
    """BFS from AGENTS.md over explicit routes.

    Returns `(reached, broken)`: every doc reached with its depth and the
    file that routed it, plus routes whose target does not exist.
    """
    map_resolved = map_file.resolve()
    reached: dict[Path, dict] = {}
    broken: list[dict] = []
    queue: deque[tuple[Path, int]] = deque()
    if AGENTS_FILE.is_file():
        queue.append((AGENTS_FILE.resolve(), 0))
    # The map is depth 1 whether or not AGENTS.md routes it. That rule is
    # hygiene:entry-files' to report, once.
    reached[map_resolved] = {"depth": 1, "via": _rel(AGENTS_FILE)}
    queue.append((map_resolved, 1))
    while queue:
        source, depth = queue.popleft()
        explicit, _soft = _routes.route_table(source)
        for target in sorted(explicit):
            if not target.is_file():
                broken.append({"source": _rel(source), "target": _rel(target)})
                continue
            if not _may_route(source, target, docs_dir, map_file) or target in reached:
                continue
            reached[target] = {"depth": depth + 1, "via": _rel(source)}
            if _is_routing_file(target, docs_dir, map_file):
                queue.append((target, depth + 1))
    return reached, broken


def _mentions(docs_dir: Path, map_file: Path) -> tuple[dict[Path, Path], dict[Path, Path]]:
    """Where every routing file links each doc: `(explicit, soft)`, target → source.

    Used to explain an unrouted doc: linked softly (the usual near-miss),
    routed from a README outside its subtree, or routed from a README that
    is itself unreachable.
    """
    explicit: dict[Path, Path] = {}
    soft: dict[Path, Path] = {}
    sources = [p for p in [AGENTS_FILE, map_file] if p.is_file()]
    sources += [p for p in sorted(docs_dir.rglob("README.md")) if p.resolve() != map_file.resolve()]
    for source in sources:
        explicit_here, soft_here = _routes.route_table(source)
        for target in explicit_here:
            explicit.setdefault(target, source.resolve())
        for target in soft_here:
            soft.setdefault(target, source.resolve())
    return explicit, soft


# ── rules ────────────────────────────────────────────────────────


def _why_unrouted(
    resolved: Path, explicit: dict, soft: dict, reached: dict, docs_dir: Path, map_file: Path
) -> str:
    if resolved in explicit:
        source = explicit[resolved]
        if not _may_route(source, resolved, docs_dir, map_file):
            return (
                f"routed from {_rel(source)}, which is outside its subtree. A directory README "
                "routes only the docs beside and below it, so that line is a cross-reference"
            )
        if source not in reached:
            return f"routed from {_rel(source)}, but that file is not reachable itself (route it first)"
    if resolved in soft:
        return f"linked from {_rel(soft[resolved])} without a trigger or 'read'"
    return "not linked from AGENTS.md, the map, or a README above it"


def _route_home(doc: Path, docs_dir: Path, map_file: Path) -> str:
    """The README that should carry the route to `doc`: the nearest README
    above it (a directory README is routed by its parent's, not itself)."""
    directory = doc.parent.parent if doc.name == "README.md" else doc.parent
    while docs_dir.resolve() in directory.resolve().parents or directory.resolve() == docs_dir.resolve():
        readme = directory / "README.md"
        if readme.is_file() and readme.resolve() != doc.resolve():
            return _rel(readme)
        directory = directory.parent
    return _rel(map_file)


def _check_routed(
    docs: list[Path], reached: dict, mentions: tuple[dict, dict], settings: dict,
    docs_dir: Path, map_file: Path,
) -> list[dict]:
    if not settings["require_routed_docs"]:
        return []
    explicit, soft = mentions
    violations: list[dict] = []
    for doc in docs:
        resolved = doc.resolve()
        if resolved in reached:
            continue
        rel = _rel(doc)
        where = _route_home(doc, docs_dir, map_file)
        detail = _why_unrouted(resolved, explicit, soft, reached, docs_dir, map_file)
        violations.append({
            "type": "unrouted_doc",
            "path": rel,
            "message": f"Unrouted doc: {rel} is {detail}. Add a route to {where}: '{ROUTE_FORM}'",
        })
    return violations


def _check_map_soft_routes(map_file: Path) -> list[dict]:
    _explicit, soft = _routes.numbered_route_table(map_file)
    return [
        {
            "type": "soft_route",
            "path": _rel(map_file),
            "message": _routes.soft_route_message(_rel(map_file), lineno, _rel(doc), line),
        }
        for doc, (lineno, line) in sorted(soft.items(), key=lambda item: item[1][0])
    ]


def _check_depth(reached: dict, settings: dict) -> list[dict]:
    limit = settings["max_route_depth"]
    return [
        {
            "type": "route_too_deep",
            "path": _rel(doc),
            "message": (
                f"Route too deep: {_rel(doc)} is {info['depth']} hops from AGENTS.md "
                f"(limit {limit}, via {info['via']}). Route it from the map or AGENTS.md directly, "
                "or merge the README that only routes onward"
            ),
        }
        for doc, info in sorted(reached.items())
        if info["depth"] > limit
    ]


def _check_doc_lines(docs: list[Path], settings: dict) -> list[dict]:
    limit = settings["max_doc_lines"]
    if not limit:
        return []
    violations: list[dict] = []
    for doc in docs:
        n = _routes.count_lines(_routes.read_markdown(doc))
        if n > limit:
            violations.append({
                "type": "doc_over_lines",
                "path": _rel(doc),
                "message": (
                    f"Doc over budget: {_rel(doc)} is {n} lines (limit {limit}). "
                    "Split it into focused docs and route each one"
                ),
            })
    return violations


def _check_legacy_index(map_file: Path) -> list[dict]:
    if map_file.resolve() == LEGACY_INDEX.resolve() or not LEGACY_INDEX.is_file():
        return []
    return [{
        "type": "legacy_index",
        "path": _rel(LEGACY_INDEX),
        "message": (
            f"Legacy map: {_rel(LEGACY_INDEX)} exists beside {_rel(map_file)}. Fold it into "
            f"{_rel(map_file)} and delete it. The map is a README so that the repo UI renders it in place"
        ),
    }]


def _broken_violations(broken: list[dict]) -> list[dict]:
    return [
        {
            "type": "broken_route",
            "path": b["source"],
            "message": f"Broken route: {b['source']} routes to {b['target']}, which does not exist",
        }
        for b in broken
    ]


def _check_structure(docs_dir: Path, settings: dict) -> dict | None:
    if not docs_dir.exists():
        output.error("docs/ directory not found")
        return None
    map_file = Path(settings["map_path"])
    if not map_file.is_file():
        hint = f" ({_rel(LEGACY_INDEX)} exists, so rename it to {settings['map_path']})" if LEGACY_INDEX.is_file() else ""
        output.error(f"{settings['map_path']} not found{hint}")
        return None
    docs, excluded = _docs.split([p for p in sorted(docs_dir.rglob("*.md")) if p.resolve() != map_file.resolve()])
    reached, broken = _walk_routes(docs_dir, map_file)
    skip = {p.resolve() for p in excluded}
    reached = {doc: info for doc, info in reached.items() if doc.resolve() not in skip}
    violations = _check_routed(docs, reached, _mentions(docs_dir, map_file), settings, docs_dir, map_file)
    violations += _check_map_soft_routes(map_file)
    violations += _check_depth(reached, settings)
    violations += _check_doc_lines(docs, settings)
    violations += _check_legacy_index(map_file)
    violations += _broken_violations(broken)
    routes = [
        {"doc": _rel(doc), "depth": info["depth"], "via": info["via"]}
        for doc, info in sorted(reached.items(), key=lambda kv: (kv[1]["depth"], _rel(kv[0])))
    ]
    return {"violations": violations, "routes": routes, "doc_count": len(docs), "excluded": [_rel(p) for p in excluded]}


# ── report ───────────────────────────────────────────────────────

_SECTIONS = (
    ("unrouted_doc", "### Unrouted Docs", f"*Add a trigger-first row to the README above it (or the map): `{ROUTE_FORM}`*"),
    ("soft_route", "### Soft Routes in the Map", "*Reword as trigger → \"read\" → file → what it holds*"),
    ("route_too_deep", "### Routes Too Deep", "*Route from the map or AGENTS.md directly; merge a README that only routes onward*"),
    ("doc_over_lines", "### Docs Over the Line Budget", "*Split into focused docs, one concern each, and route each one*"),
    ("legacy_index", "### Legacy Index", "*Fold `docs/index.md` into the map README and delete it*"),
    ("broken_route", "### Broken Routes", "*Fix the path, or drop the route*"),
)


def _format_violations_content(violations: list[dict]) -> str:
    content = f"Found **{len(violations)}** violation(s):\n\n"
    by_type: dict[str, list[dict]] = {}
    for v in violations:
        by_type.setdefault(v.get("type", "unknown"), []).append(v)
    for vtype, header, note in _SECTIONS:
        if vtype not in by_type:
            continue
        content += header + "\n\n"
        for v in by_type[vtype]:
            content += f"- {v['message']}\n"
        content += f"\n{note}\n\n"
    return content


def _format_routes_section(routes: list[dict]) -> str:
    if not routes:
        return "## Routes\n\n_No doc is reachable from `AGENTS.md` yet._\n\n"
    lines = ["## Routes", "", "Every doc an agent can reach, and how many hops it costs:", "",
             "| Doc | Hops from AGENTS.md | Routed from |", "| --- | ------------------- | ----------- |"]
    lines += [f"| `{r['doc']}` | {r['depth']} | `{r['via']}` |" for r in routes]
    return "\n".join(lines) + "\n\n"


def _build_md_report(data: dict, generated_at: str) -> str:
    violations = data.get("violations", [])
    status_line = (
        "✅ Every doc has an explicit route"
        if data.get("valid", True)
        else "❌ Documentation structure violations found"
    )
    report = (
        f"# 📋 Documentation Structure Report\n\n"
        f"**Generated:** {generated_at}\n\n"
        f"## Status\n\n{status_line}\n\n"
        "## The model\n\n"
        "`AGENTS.md` carries what most tasks need and routes the rest. The docs map is the "
        "fallback routing table; a directory README is the map of a topic that split. A doc is "
        "reachable only through an explicit route, which is a line that names its trigger and says read. "
        "An unrouted doc is invisible to agents; a soft link is skipped.\n\n"
        "## Violations\n\n"
    )
    report += _format_violations_content(violations) if violations else "✅ No violations found\n\n"
    report += _format_routes_section(data.get("routes", []))
    report += _docs.report_section([Path(p) for p in data.get("excluded", [])])
    report += (
        "## How to Fix\n\n"
        f"1. **Unrouted doc:** add a row to the README above it, or to the map, in the form `{ROUTE_FORM}`. "
        "The trigger first, then \"read\", then the file as a markdown link.\n"
        "2. **Soft route:** the map links a doc without saying when to read it. Reword the row.\n"
        "3. **Route too deep:** route the doc from the map or `AGENTS.md` directly; a README that only "
        "routes onward is a turn spent on nothing.\n"
        "4. **Doc over the line budget:** split it by concern and route each part.\n"
        "5. **Legacy index:** fold `docs/index.md` into the map README and delete it.\n\n"
        "---\n\n*Report generated by Documentation Structure Check*\n"
    )
    return report


def run(args: list[str] | None = None) -> int:
    if args:
        return reject_extra_args("hygiene:docs-structure", args)
    output.running("Validating documentation structure…")
    settings = _settings()

    result = _check_structure(DOCS_DIR, settings)
    if result is None:
        return 2
    violations = result["violations"]

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    data = {
        "violations": violations,
        "valid": len(violations) == 0,
        "violation_count": len(violations),
        "doc_count": result["doc_count"],
        "routes": result["routes"],
        "map_path": settings["map_path"],
        "excluded": result["excluded"],
    }
    REPORT_JSON.write_text(json.dumps(data, indent=2))
    REPORT_MD.write_text(_build_md_report(data, _report.generated_at()))

    if violations:
        output.error(f"Found {len(violations)} structure violation(s)")
        for v in violations:
            output._emit(f"  - {v['message']}")
        output.footer(REPORT_DIR, [REPORT_MD.name])
        return 1
    excluded_note = f" ({len(result['excluded'])} excluded by hygiene.docs.exclude)" if result["excluded"] else ""
    output.success(f"Documentation structure is valid: all {result['doc_count']} docs are routed{excluded_note}")
    output.footer(REPORT_DIR, [REPORT_MD.name])
    return 0
