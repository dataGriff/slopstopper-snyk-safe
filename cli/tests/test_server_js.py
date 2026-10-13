"""Tests for the bundled `server.js` header parsers.

The server is JavaScript, not Python, so these tests invoke `node` in a
subprocess to load `server.js` as a module, then exercise its exported
parsers against fixtures. Skipped (rather than failed) when `node` is
not on PATH, since slopstopper-cli's Python tests must run cleanly in
node-less environments too (e.g. lint-only CI matrices).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from slopstopper import templates


SERVER_JS = templates.PACKAGE_DATA_DIR / "server.js"


def _run_node_assert(fixture_path: Path, parser_name: str) -> list[dict]:
    """Invoke `node -e '...'` to require server.js and run a parser.

    Returns the parsed rules as a Python list of dicts. Raises
    AssertionError (via the subprocess result) on failure.
    """
    script = (
        f"const s = require({json.dumps(str(SERVER_JS))});\n"
        f"const result = s.{parser_name}({json.dumps(str(fixture_path))});\n"
        f"process.stdout.write(JSON.stringify(result));\n"
    )
    proc = subprocess.run(
        ["node", "-e", script],
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(proc.stdout)


# Under CI node is always present (ss-setup / mise), so a missing node fails there rather than skipping.
if shutil.which("node") is None and not os.environ.get("CI"):
    pytest.skip("node not on PATH; server.js parser tests require node", allow_module_level=True)


# ── parseCloudflareHeaders ───────────────────────────────────────


def test_parses_simple_cloudflare_headers(tmp_path):
    fixture = tmp_path / "_headers"
    fixture.write_text(
        "/*\n"
        "  X-Frame-Options: DENY\n"
        "  Referrer-Policy: strict-origin-when-cross-origin\n"
        "\n"
        "/og-image.png\n"
        "  Cross-Origin-Resource-Policy: cross-origin\n"
    )
    rules = _run_node_assert(fixture, "parseCloudflareHeaders")
    assert rules == [
        {
            "for": "/*",
            "values": {
                "X-Frame-Options": "DENY",
                "Referrer-Policy": "strict-origin-when-cross-origin",
            },
        },
        {
            "for": "/og-image.png",
            "values": {"Cross-Origin-Resource-Policy": "cross-origin"},
        },
    ]


def test_skips_pure_comment_lines(tmp_path):
    fixture = tmp_path / "_headers"
    fixture.write_text(
        "# top-of-file comment\n"
        "/*\n"
        "  # this comment doesn't belong inside a values block but we tolerate it\n"
        "  X-Content-Type-Options: nosniff\n"
    )
    rules = _run_node_assert(fixture, "parseCloudflareHeaders")
    assert rules == [{"for": "/*", "values": {"X-Content-Type-Options": "nosniff"}}]


def test_skips_blocks_without_path_pattern(tmp_path):
    """A block whose first non-comment line isn't a path pattern is skipped."""
    fixture = tmp_path / "_headers"
    fixture.write_text(
        "# slopstopper security headers begin\n"
        "# (intentionally commented-out block; install ships it disabled by default)\n"
        "\n"
        "/*\n"
        "  X-Frame-Options: DENY\n"
    )
    rules = _run_node_assert(fixture, "parseCloudflareHeaders")
    # The "# slopstopper..." block has no path pattern; only the active /* block produces a rule.
    assert rules == [{"for": "/*", "values": {"X-Frame-Options": "DENY"}}]


def test_handles_multiple_blocks(tmp_path):
    fixture = tmp_path / "_headers"
    fixture.write_text(
        "/blog/*\n"
        "  Cache-Control: public, max-age=60\n"
        "\n"
        "/assets/*\n"
        "  Cache-Control: public, max-age=31536000, immutable\n"
        "\n"
        "/api/*\n"
        "  Cache-Control: no-store\n"
        "  X-Robots-Tag: noindex\n"
    )
    rules = _run_node_assert(fixture, "parseCloudflareHeaders")
    assert len(rules) == 3
    assert rules[0]["for"] == "/blog/*"
    assert rules[2]["values"]["X-Robots-Tag"] == "noindex"


# ── pickParser auto-detection via loadHeaderRules ────────────────


def test_pick_parser_uses_cloudflare_for_underscore_headers(tmp_path):
    fixture = tmp_path / "_headers"
    fixture.write_text("/*\n  X-Frame-Options: DENY\n")
    # pickParser returns a function reference, but JSON-serialising a fn
    # gives null. Instead call loadHeaderRules which threads pickParser
    # internally and returns the parsed result.
    script = (
        f"const s = require({json.dumps(str(SERVER_JS))});\n"
        f"process.stdout.write(JSON.stringify(s.loadHeaderRules({json.dumps(str(fixture))})));\n"
    )
    proc = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True)
    rules = json.loads(proc.stdout)
    assert rules == [{"for": "/*", "values": {"X-Frame-Options": "DENY"}}]


def test_pick_parser_uses_json_for_dot_json_extension(tmp_path):
    fixture = tmp_path / "headers.json"
    payload = [{"for": "/*", "values": {"X-Frame-Options": "DENY"}}]
    fixture.write_text(json.dumps(payload))
    script = (
        f"const s = require({json.dumps(str(SERVER_JS))});\n"
        f"process.stdout.write(JSON.stringify(s.loadHeaderRules({json.dumps(str(fixture))})));\n"
    )
    proc = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True)
    rules = json.loads(proc.stdout)
    assert rules == payload


# ── headersForPath integration ───────────────────────────────────


def test_headers_for_path_matches_glob_and_exact(tmp_path):
    """End-to-end: load _headers, then ask which headers apply to a path."""
    fixture = tmp_path / "_headers"
    fixture.write_text(
        "/*\n"
        "  X-Frame-Options: DENY\n"
        "\n"
        "/og-image.png\n"
        "  Cross-Origin-Resource-Policy: cross-origin\n"
    )
    script = (
        f"const s = require({json.dumps(str(SERVER_JS))});\n"
        f"const rules = s.loadHeaderRules({json.dumps(str(fixture))});\n"
        f"const result = {{\n"
        f"  root: s.headersForPath(rules, '/'),\n"
        f"  blog: s.headersForPath(rules, '/blog/post'),\n"
        f"  og: s.headersForPath(rules, '/og-image.png'),\n"
        f"}};\n"
        f"process.stdout.write(JSON.stringify(result));\n"
    )
    proc = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True)
    result = json.loads(proc.stdout)
    # /* matches everything → X-Frame-Options applies to every path
    assert result["root"] == {"X-Frame-Options": "DENY"}
    assert result["blog"] == {"X-Frame-Options": "DENY"}
    # The OG image gets both /* and its exact-match rule; later rules win for overlapping keys.
    assert result["og"]["X-Frame-Options"] == "DENY"
    assert result["og"]["Cross-Origin-Resource-Policy"] == "cross-origin"
