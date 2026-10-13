"""Which installed `ss-*` workflows GitHub has switched off.

GitHub disables any workflow with a `schedule:` trigger once a repo has had
no activity for 60 days (state `disabled_inactivity`). A disabled workflow
runs on no trigger, pull requests included, and never fails, so nothing
downstream notices: the check just disappears from the PR's checks list and
the scheduled production runs stop. `slopstopper doctor` and the install.sh
post-install banner both read this.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import PurePosixPath

INACTIVITY = "disabled_inactivity"


def disabled_ss_workflows() -> list[dict] | None:
    """The `ss-*` workflows GitHub reports as disabled, from the CWD's repo.

    Each row is gh's `{id, name, path, state}`. Returns None when GitHub
    can't be asked: gh missing, unauthenticated, or not a GitHub repo.
    """
    if shutil.which("gh") is None:
        return None
    try:
        result = subprocess.run(
            ["gh", "workflow", "list", "--all", "--limit", "500",
             "--json", "id,name,path,state"],
            capture_output=True, text=True, check=False, timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    try:
        rows = json.loads(result.stdout or "[]")
    except ValueError:
        return None
    if not isinstance(rows, list):
        return None
    return [
        row for row in rows
        if isinstance(row, dict)
        and PurePosixPath(str(row.get("path", ""))).name.startswith("ss-")
        and str(row.get("state", "")).startswith("disabled")
    ]


def enable_command(row: dict) -> str:
    return f"gh workflow enable {row.get('id')}"
