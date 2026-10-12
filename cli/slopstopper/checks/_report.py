"""Report scaffolding shared by every check.

Each check writes a markdown report that `slopstopper emit` turns into a
PR comment or an issue, and `comment.py` reads that markdown back with the
regex `^\\s*-\\s*❌\\s*(.+?)$` to list the failing items. That means the
PR bot's output silently depends on every check emitting the same bullet
syntax. Seventeen modules used to hand-roll it; this is where the
contract lives, so a new check gets it right by construction.

Also the one timestamp. Nine checks had `_generated_at` in three
different formats; a report should not depend on which module wrote it.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

# `- ❌ text` is what comment.extract_failures reads. Keep the icon and
# the single space or the PR comment silently loses its failure list.
FAIL_ICON = "❌"
NOTE_ICON = "⚠️ "


def generated_at(precision: str = "seconds") -> str:
    """The one timestamp format: UTC, suffixed. Reports use seconds; the
    rolling PR comment uses `precision="minutes"` because it is a
    freshness marker that re-renders on every check, where seconds are
    noise."""
    fmt = "%Y-%m-%d %H:%M:%S UTC" if precision == "seconds" else "%Y-%m-%d %H:%M UTC"
    return datetime.now(timezone.utc).strftime(fmt)


def render_findings(label: str, icon: str, findings: list[str]) -> list[str]:
    """`**Label:**` followed by one `- <icon> finding` bullet each; nothing if empty."""
    if not findings:
        return []
    lines = [f"**{label}:**"]
    lines.extend(f"- {icon} {finding}" for finding in findings)
    lines.append("")
    return lines


def render_issues(findings: list[str]) -> list[str]:
    """The failing items, as the bullets `comment.extract_failures` reads."""
    return render_findings("Issues", FAIL_ICON, findings)


def render_notes(findings: list[str]) -> list[str]:
    """Advisory items: shown, never parsed as failures."""
    return render_findings("Notes", NOTE_ICON, findings)


def render_skip(reason: str, guidance: list[str]) -> list[str]:
    """The graceful-skip block: an unconfigured check is not a failing check."""
    lines = [f"**Overall:** ⏭️ SKIPPED: {reason}", ""]
    lines.extend(guidance)
    lines.append("")
    return lines


def write_reports(
    json_path: Path,
    md_path: Path,
    result: dict,
    render_markdown: Callable[[dict], str],
) -> None:
    """Write the JSON result and the markdown rendered from it."""
    for directory in {json_path.parent, md_path.parent}:
        directory.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(result, indent=2) + "\n")
    md_path.write_text(render_markdown(result))


def gha_run_url() -> str | None:
    """Link to the current GitHub Actions run, or None outside Actions."""
    server = os.environ.get("GITHUB_SERVER_URL")
    repo = os.environ.get("GITHUB_REPOSITORY")
    run_id = os.environ.get("GITHUB_RUN_ID")
    if not (server and repo and run_id):
        return None
    return f"{server}/{repo}/actions/runs/{run_id}"
