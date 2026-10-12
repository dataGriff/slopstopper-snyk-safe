"""Cloudflare _headers text format adapter.

Parses the native Cloudflare _headers file (also used by Netlify) into
the same {for, csp} shape as the JSON adapter. Format:

    /path-pattern
      Header-Name: value
      Another-Header: value

    /other-pattern
      ...

Path patterns are at column 0; header lines are indented; lines starting
with `#` are comments. A blank line followed by a new path at column 0
starts a new block.
"""

from __future__ import annotations

from pathlib import Path


def parse(path: Path) -> list[dict]:
    """Return [{for: str, csp: str | None}, ...] for each rule block."""
    rules: list[dict] = []
    current_path: str | None = None
    current_headers: dict[str, str] = {}

    def flush() -> None:
        nonlocal current_path, current_headers
        if current_path is not None:
            csp = current_headers.get("Content-Security-Policy")
            rules.append({
                "for": current_path,
                "csp": csp if isinstance(csp, str) else None,
            })
        current_path = None
        current_headers = {}

    for raw in path.read_text().splitlines():
        line = raw.rstrip()
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if not line.startswith((" ", "\t")):
            flush()
            current_path = stripped
            continue
        if current_path is None or ":" not in stripped:
            continue
        name, _, value = stripped.partition(":")
        current_headers[name.strip()] = value.strip()

    flush()
    return rules
