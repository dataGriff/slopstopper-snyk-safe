"""Entry-file budget + AGENTS.md-first routing check.

Enforces the rules declared in docs/README.md ("AGENTS.md first"):

  1. `AGENTS.md` is the working layer: it carries what most tasks need
     and is loaded into every agent conversation, so it stays under a
     token budget (~2k estimated tokens, chars/4). Past that, agents
     start dropping rules; the overflow routes to `docs/`.
  2. `README.md` is human orientation plus a quick start, under its own
     (smaller) budget. Pipeline badges are not counted.
  3. `CLAUDE.md` is exactly `@AGENTS.md`, so there is one agent entry
     point and nothing to drift.
  4. Every `.md` link in `AGENTS.md` is an explicit route made of a
     trigger, then "read", then the file. One of them points at the docs
     map (`docs/README.md`), which must exist and stay under its own
     budget so the fallback hop stays cheap.
  5. `README.md` links the map, so humans find the docs too.

A cold-start report (always-loaded tokens, the fallback hop, the sum)
prints on every run, pass or fail: a cost that is visible gets managed.

Writes a markdown report and a JSON report under .ss/reports/entry-files/.
The markdown report includes a paste-ready snippet for each violation so
adopters can remediate in one copy/paste.

Configuration (.slopstopper.yml, optional):

    hygiene:
      entry_files:
        max_tokens: 2000             # AGENTS.md (and CLAUDE.md, if not a pure include)
        readme_max_tokens: 600       # README.md, badges excluded
        map_max_tokens: 1000         # the docs map
        map_path: docs/README.md     # the docs map (relative to repo root)
        require_map_pointer: true    # README.md + AGENTS.md must reach the map
        require_explicit_routes: true  # every .md link in AGENTS.md is a route
        require_claude_include: true   # CLAUDE.md is exactly `@AGENTS.md`

See .slopstopper.yml.example for the canonical schema. The pre-0.15
`max_words` knob is ignored with a note, because budgets are tokens now.

Exit codes:
  0: every entry file within budget AND every rule satisfied
  1: at least one budget OR rule violation
  2: required entry files missing, or arguments were passed (this
      check takes none)
"""

from __future__ import annotations

import json
from pathlib import Path

from slopstopper import config, output
from slopstopper.checks import _report, _routes
from slopstopper.checks._contract import reject_extra_args

ENTRY_FILES = ("README.md", "AGENTS.md", "CLAUDE.md")
README_FILE, AGENTS_FILE, CLAUDE_FILE = ENTRY_FILES
CLAUDE_INCLUDE = "@AGENTS.md"
LEGACY_MAP_PATH = "docs/index.md"

DEFAULT_MAX_TOKENS = 2000
DEFAULT_README_MAX_TOKENS = 600
DEFAULT_MAP_MAX_TOKENS = 1000
DEFAULT_MAP_PATH = "docs/README.md"

REPORT_DIR = Path(".ss/reports/entry-files")
REPORT_MD = REPORT_DIR / "entry-file-size-report.md"
REPORT_JSON = REPORT_DIR / "entry-file-size-report.json"

# Consumed by `slopstopper emit hygiene:entry-files --target pr-comment`.
# The discriminator is unchanged across the token-budget rewrite so the
# same bot comment is reused. No issue keys: the workflow gates main with
# the check's own exit code, no main-branch issue is created.
META = {
    "report_path": str(REPORT_MD),
    "comment_discriminator": "📏 Entry-File Budget Check",
}


# ── configuration ────────────────────────────────────────────────


def _settings() -> dict:
    if config.get("hygiene.entry_files.max_words") is not None:
        output.warn(
            "hygiene.entry_files.max_words is no longer read because budgets are "
            "estimated tokens now (hygiene.entry_files.max_tokens / readme_max_tokens)."
        )
    return {
        "max_tokens": config.get_int("hygiene.entry_files.max_tokens", DEFAULT_MAX_TOKENS),
        "readme_max_tokens": config.get_int(
            "hygiene.entry_files.readme_max_tokens", DEFAULT_README_MAX_TOKENS
        ),
        "map_max_tokens": config.get_int("hygiene.entry_files.map_max_tokens", DEFAULT_MAP_MAX_TOKENS),
        "map_path": config.get_str("hygiene.entry_files.map_path", DEFAULT_MAP_PATH),
        "require_map_pointer": config.get_bool("hygiene.entry_files.require_map_pointer", True),
        "require_explicit_routes": config.get_bool("hygiene.entry_files.require_explicit_routes", True),
        "require_claude_include": config.get_bool("hygiene.entry_files.require_claude_include", True),
    }


def _budget_for(name: str, settings: dict) -> int:
    if name == README_FILE:
        return settings["readme_max_tokens"]
    if name == settings["map_path"]:
        return settings["map_max_tokens"]
    return settings["max_tokens"]


# ── measurement ──────────────────────────────────────────────────


def _read(path: Path) -> str:
    try:
        return _routes.read_markdown(path)
    except OSError:
        return ""


def _is_pure_include(text: str) -> bool:
    return text.strip() == CLAUDE_INCLUDE


def _reaches_map(path: Path, map_file: Path, explicit_only: bool) -> bool:
    explicit, soft = _routes.route_table(path)
    target = map_file.resolve()
    if target in explicit:
        return True
    return (not explicit_only) and target in soft


def _pointer_violation(name: str, settings: dict) -> str | None:
    """The rule `name` breaks, or None when it is compliant."""
    path = Path(name)
    if name == CLAUDE_FILE:
        if settings["require_claude_include"] and not _is_pure_include(_read(path)):
            return "claude_not_pure_include"
        return None
    if not settings["require_map_pointer"]:
        return None
    explicit_only = name == AGENTS_FILE and settings["require_explicit_routes"]
    if _reaches_map(path, Path(settings["map_path"]), explicit_only):
        return None
    return "missing_map_pointer"


def _soft_routes(name: str, settings: dict) -> list[str]:
    """Soft `.md` links in AGENTS.md. Every link there must be a route."""
    if name != AGENTS_FILE or not settings["require_explicit_routes"]:
        return []
    _explicit, soft = _routes.numbered_route_table(Path(name))
    return [
        _routes.soft_route_label(name, lineno, _rel(doc), line)
        for doc, (lineno, line) in sorted(soft.items(), key=lambda item: item[1][0])
    ]


def _rel(path: Path) -> str:
    try:
        return path.relative_to(Path.cwd().resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def _measure(name: str, settings: dict) -> dict | None:
    """Measure one file against its budget and rules; None if it is absent."""
    path = Path(name)
    if not path.exists():
        return None
    text = _read(path)
    budget = _budget_for(name, settings)
    tokens = _routes.estimate_tokens(text)
    is_entry = name in ENTRY_FILES
    violation = _pointer_violation(name, settings) if is_entry else None
    soft = _soft_routes(name, settings) if is_entry else []
    return {
        "file": name,
        "tokens": tokens,
        "lines": _routes.count_lines(text),
        "budget": budget,
        "over_budget": tokens > budget,
        "headroom": budget - tokens,
        "pointer_ok": violation is None,
        "pointer_violation": violation,
        "soft_routes": soft,
    }


def _measure_all(settings: dict) -> tuple[list[dict], list[str]]:
    measurements: list[dict] = []
    missing: list[str] = []
    for name in ENTRY_FILES:
        m = _measure(name, settings)
        if m is None:
            missing.append(name)
        else:
            measurements.append(m)
    map_row = _measure(settings["map_path"], settings)
    if map_row is not None:
        measurements.append(map_row)
    return measurements, missing


def _file_violations(m: dict) -> int:
    return int(m["over_budget"] or not m["pointer_ok"] or bool(m["soft_routes"]))


def _violation_count(measurements: list[dict], map_file_missing: bool) -> int:
    return sum(_file_violations(m) for m in measurements) + int(map_file_missing)


# ── report ───────────────────────────────────────────────────────


def _status_line(clean: bool) -> str:
    if clean:
        return "✅ Every entry file is within budget, CLAUDE.md is a pure include, and every route is explicit."
    return "❌ An entry file is over its token budget, or breaks an AGENTS.md-first rule (see below)."


def _md_row(m: dict) -> str:
    size_status = "❌ over" if m["over_budget"] else "✅ ok"
    if m["file"] in ENTRY_FILES:
        rules_status = "✅ ok" if m["pointer_ok"] and not m["soft_routes"] else "❌ see below"
    else:
        rules_status = "n/a"
    return (
        f"| `{m['file']}` | {m['tokens']} | {m['lines']} | {m['budget']} | "
        f"{size_status} | {m['headroom']:+d} | {rules_status} |"
    )


def _map_pointer_snippet(map_path: str) -> str:
    return _routes.route_snippet(map_path, "the routing table for every doc in this repo").replace(
        "Before you <do X>", "For any task not covered above"
    )


def _map_file_snippet() -> str:
    return (
        "# Docs index\n\n"
        "The fallback routing table. `AGENTS.md` carries what most tasks need\n"
        "and routes the common cases directly; come here when your task is not\n"
        "covered there. Read only the doc whose trigger matches.\n\n"
        "| When you are… | Do this |\n"
        "| ------------- | ------- |\n"
        "| doing <some kind of task> | Read [<topic>.md](<topic>.md) first for <what it holds> |\n"
    )


def _over_budget_advice(name: str, map_path: str) -> str:
    if name == AGENTS_FILE:
        return (
            f"Move the content that serves the fewest tasks into a `docs/` topic doc and "
            f"route it from `AGENTS.md` with a trigger-first line "
            f"(`{_routes.route_snippet('docs/<topic>.md', 'what it holds')}`). "
            f"Keep the rules most tasks need inline, because a hop costs a tool turn on every task that takes it."
        )
    if name == CLAUDE_FILE:
        return f"Make it exactly `{CLAUDE_INCLUDE}` and move anything real into `AGENTS.md`."
    if name == map_path:
        return (
            "The map is a routing table, not a reference: keep one row per doc, trim each row to "
            "the trigger and what the doc holds, and push a topic that has grown several rows down "
            "into its own directory README that the map routes to once."
        )
    return (
        f"Keep it to orientation and a quick start; everything else lives under `docs/` "
        f"and is reachable from [`{map_path}`](./{map_path})."
    )


def _fix_over_budget(m: dict, map_path: str) -> str:
    where = _over_budget_advice(m["file"], map_path)
    return f"### `{m['file']}` is over its token budget ({m['tokens']} > {m['budget']})\n\n{where}\n"


def _fix_pointer(m: dict, map_path: str) -> str:
    if m["pointer_violation"] == "claude_not_pure_include":
        return (
            "### `CLAUDE.md` is not a pure include\n\n"
            "Replace the contents of `CLAUDE.md` with exactly:\n\n"
            f"```markdown\n{CLAUDE_INCLUDE}\n```\n\n"
            "Claude Code expands the directive, so agent guidance has one source. "
            "Any other content here is a second copy that drifts.\n"
        )
    verb = "an explicit route to" if m["file"] == AGENTS_FILE else "a link to"
    return (
        f"### `{m['file']}` is missing {verb} the map\n\n"
        f"Paste this into `{m['file']}` (the closing line of its routes section):\n\n"
        f"```markdown\n{_map_pointer_snippet(map_path)}\n```\n"
    )


def _fix_soft_routes(m: dict) -> str:
    lines = "\n".join(f"- {s}" for s in m["soft_routes"])
    return (
        f"### `{m['file']}` has soft routes\n\n"
        "Agents skip a doc link that does not say when to read it. Reword each line below "
        "as **trigger → \"read\" → file → what it holds**, or drop the link:\n\n"
        f"{lines}\n\n"
        "```markdown\n"
        f"{_routes.route_snippet('docs/<topic>.md', 'what it holds')}\n"
        "```\n"
    )


def _fix_map_missing(map_path: str) -> str:
    rename = ""
    if Path(LEGACY_MAP_PATH).exists() and map_path != LEGACY_MAP_PATH:
        rename = (
            f"`{LEGACY_MAP_PATH}` exists: rename it (`git mv {LEGACY_MAP_PATH} {map_path}`) "
            "and rewrite its rows as explicit routes. The repo UI renders a README in place "
            "when someone browses `docs/`, which an `index.md` never gets.\n\n"
        )
    return (
        f"### `{map_path}` does not exist\n\n"
        f"{rename}"
        f"AGENTS.md-first needs a docs map at `{map_path}` to act as the fallback routing table "
        "for tasks `AGENTS.md` does not anticipate. Create it with at minimum:\n\n"
        f"```markdown\n{_map_file_snippet()}```\n\n"
        "`task ss:hygiene:docs-structure` then checks that every doc under `docs/` is "
        "reachable from it by an explicit route.\n"
    )


def _build_fix_section(measurements: list[dict], map_path: str, map_file_missing: bool) -> str:
    sections: list[str] = []
    for m in measurements:
        if m["over_budget"]:
            sections.append(_fix_over_budget(m, map_path))
        if m["pointer_violation"]:
            sections.append(_fix_pointer(m, map_path))
        if m["soft_routes"]:
            sections.append(_fix_soft_routes(m))
    if map_file_missing:
        sections.append(_fix_map_missing(map_path))
    if not sections:
        return ""
    return "## How to fix violations\n\n" + "\n".join(sections)


def _cold_start(measurements: list[dict], map_path: str) -> dict:
    by_file = {m["file"]: m["tokens"] for m in measurements}
    always = by_file.get(AGENTS_FILE, 0)
    hop = by_file.get(map_path, 0)
    return {"always_loaded": always, "fallback_hop": hop, "uncovered_task": always + hop}


def _cold_start_md(cold: dict, map_path: str, max_tokens: int) -> str:
    return (
        "## Cold-start cost (estimated tokens, chars/4)\n\n"
        "| Layer | File | Tokens |\n"
        "| ----- | ---- | ------ |\n"
        f"| always loaded | `{AGENTS_FILE}` (via `{CLAUDE_FILE}`) | {cold['always_loaded']} (target ≤ {max_tokens}) |\n"
        f"| fallback hop | `{map_path}` | {cold['fallback_hop']} (uncovered tasks only) |\n"
        f"| uncovered task | both | {cold['uncovered_task']} |\n"
    )


def _build_md_report(
    measurements: list[dict],
    generated_at: str,
    status_line: str,
    settings: dict,
    map_file_missing: bool,
) -> str:
    map_path = settings["map_path"]
    rows = "\n".join(_md_row(m) for m in measurements)
    map_block = (
        f"## Map file\n\n❌ `{map_path}` not found. AGENTS.md-first needs a docs map.\n\n"
        if map_file_missing
        else ""
    )
    fix_section = _build_fix_section(measurements, map_path, map_file_missing)
    fix_block = f"\n{fix_section}\n" if fix_section else ""
    cold = _cold_start_md(_cold_start(measurements, map_path), map_path, settings["max_tokens"])
    return f"""# 📏 Entry-File Budget + AGENTS.md-first Report

**Generated:** {generated_at}

`AGENTS.md` is loaded into every agent conversation (`CLAUDE.md` is its
include), so it carries what most tasks need within a token budget and
routes the rest to `docs/` with explicit "before you X, read Y" lines.
`README.md` is human orientation under a smaller budget (badges excluded);
`{map_path}` is the fallback routing table. This check enforces the budgets,
the pure `CLAUDE.md` include, and the route form.

## Measurements

| File | Tokens | Lines | Budget | Size | Headroom | Rules |
| ---- | ------ | ----- | ------ | ---- | -------- | ----- |
{rows}

{cold}
{map_block}## Status

{status_line}
{fix_block}
---

*Generated by `task ss:hygiene:entry-files`.*
"""


def _build_json_report(
    measurements: list[dict],
    generated_at: str,
    settings: dict,
    map_file_missing: bool,
    clean: bool,
) -> str:
    payload = {
        "generated_at": generated_at,
        "budget_tokens": settings["max_tokens"],
        "readme_budget_tokens": settings["readme_max_tokens"],
        "map_budget_tokens": settings["map_max_tokens"],
        "map_path": settings["map_path"],
        "map_file_present": not map_file_missing,
        "require_map_pointer": settings["require_map_pointer"],
        "require_explicit_routes": settings["require_explicit_routes"],
        "require_claude_include": settings["require_claude_include"],
        "cold_start": _cold_start(measurements, settings["map_path"]),
        "measurements": measurements,
        "clean": clean,
        "violation_count": _violation_count(measurements, map_file_missing),
    }
    return json.dumps(payload, indent=2)


def _print_summary(measurements: list[dict], status_line: str, settings: dict, map_file_missing: bool) -> None:
    output.status("📏", "Entry-file budget + AGENTS.md-first check")
    output.separator()
    for m in measurements:
        size_marker = "❌" if m["over_budget"] else "✅"
        rules_marker = "❌" if (not m["pointer_ok"] or m["soft_routes"]) else "✅"
        output._emit(
            f"  {size_marker} {m['file']:<16} {m['tokens']:>5} tokens  "
            f"(budget {m['budget']}, headroom {m['headroom']:+d})  {rules_marker} rules"
        )
    if map_file_missing:
        output._emit(f"  ❌ {settings['map_path']} not found")
    cold = _cold_start(measurements, settings["map_path"])
    output.blank()
    output._emit(
        f"  cold start: {cold['always_loaded']} always loaded (AGENTS.md) + "
        f"{cold['fallback_hop']} fallback hop ({settings['map_path']}) = {cold['uncovered_task']}"
    )
    output.blank()
    output._emit(status_line)
    output.footer(REPORT_DIR, [REPORT_MD.name])


def run(args: list[str] | None = None) -> int:
    if args:
        return reject_extra_args("hygiene:entry-files", args)
    settings = _settings()
    map_file_missing = settings["require_map_pointer"] and not Path(settings["map_path"]).exists()

    measurements, missing = _measure_all(settings)
    if missing:
        output.error(f"Required entry files not found: {', '.join(missing)}")
        return 2

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    clean = _violation_count(measurements, map_file_missing) == 0
    generated_at = _report.generated_at()
    status_line = _status_line(clean)

    REPORT_JSON.write_text(_build_json_report(measurements, generated_at, settings, map_file_missing, clean))
    REPORT_MD.write_text(_build_md_report(measurements, generated_at, status_line, settings, map_file_missing))

    _print_summary(measurements, status_line, settings, map_file_missing)
    return 0 if clean else 1
