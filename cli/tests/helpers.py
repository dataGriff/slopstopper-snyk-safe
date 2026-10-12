"""Helpers shared by test modules (fixtures live in conftest.py)."""

from __future__ import annotations

import os
import shutil
import textwrap
from pathlib import Path

import pytest


def yaml_block(path: Path, key: str, step: str | None = None) -> str:
    """The dedented body of the first `<key>: |` block in a workflow or
    action, optionally after the step named `step`. The shell and
    github-script tests run these blocks for real."""
    lines = Path(path).read_text().splitlines()
    start = 0
    if step is not None:
        start = next(i for i, l in enumerate(lines) if l.strip() == f"- name: {step}")
    at = next(i for i in range(start, len(lines)) if lines[i].strip() == f"{key}: |")
    indent = len(lines[at]) - len(lines[at].lstrip()) + 2
    body = []
    for line in lines[at + 1:]:
        if line.strip() and len(line) - len(line.lstrip()) < indent:
            break
        body.append(line)
    return textwrap.dedent("\n".join(body))


def meta_page(root: Path, rel: str, csp: str | None) -> Path:
    """A built page under `root`: no CSP tag for None, an empty one for ""."""
    meta = "" if csp is None else f'<meta http-equiv="Content-Security-Policy" content="{csp}">'
    target = root / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(f"<!doctype html><html><head>{meta}<title>x</title></head><body></body></html>")
    return target


def needs(*tools: str) -> pytest.MarkDecorator:
    """Skip when a tool is missing locally. Under CI the test runs anyway,
    so a workflow edit that drops the tool fails loudly instead of
    silently skipping."""
    missing = [t for t in tools if shutil.which(t) is None]
    return pytest.mark.skipif(bool(missing) and not os.environ.get("CI"), reason=f"needs {', '.join(missing)}")
