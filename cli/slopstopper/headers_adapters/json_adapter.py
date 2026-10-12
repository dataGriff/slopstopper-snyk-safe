"""JSON header-source adapter.

Parses `[{"for": "/path", "values": {"Header-Name": "value"}}, ...]`,
the JSON shape a Cloudflare Worker's headers file uses (worker/headers.json). A Cloudflare
Worker reads the same file and applies headers per request.
"""

from __future__ import annotations

import json
from pathlib import Path


def parse(path: Path) -> list[dict]:
    """Return [{for: str, csp: str | None}, ...] for each entry in the JSON array."""
    raw = json.loads(path.read_text())
    if not isinstance(raw, list):
        return []
    rules: list[dict] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        for_path = entry.get("for")
        values = entry.get("values") or {}
        if not isinstance(for_path, str) or not isinstance(values, dict):
            continue
        csp = values.get("Content-Security-Policy")
        rules.append({"for": for_path, "csp": csp if isinstance(csp, str) else None})
    return rules
