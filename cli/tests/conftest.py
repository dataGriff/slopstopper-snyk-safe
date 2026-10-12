"""Shared pytest fixtures for the slopstopper-cli test suite."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Iterator

import pytest

from slopstopper import config


@pytest.fixture
def isolated_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """chdir into a tmp_path and reset the config cache around the test.

    The config module reads `.slopstopper.yml` from CWD and caches the
    result at module level. Tests that lay out a fake repo or edit
    `.slopstopper.yml` need a clean slate both before and after.
    """
    monkeypatch.chdir(tmp_path)
    config.reload()
    yield tmp_path
    config.reload()


@pytest.fixture
def git_repo(isolated_cwd: Path) -> Path:
    """An isolated CWD that is an empty git checkout, for checks that ask git which files the repo owns."""
    subprocess.run(["git", "init", "-q"], check=True)
    return isolated_cwd


@pytest.fixture
def write_config(isolated_cwd: Path) -> callable:
    """Write a `.slopstopper.yml` into the isolated CWD and clear the config cache."""

    def _write(body: str) -> Path:
        path = isolated_cwd / ".slopstopper.yml"
        path.write_text(body)
        config.reload()
        return path

    return _write


@pytest.fixture
def docs_tree(isolated_cwd: Path) -> callable:
    """Build a `docs/` tree under the isolated CWD from a {relpath: content} mapping."""

    def _build(files: dict[str, str | bytes]) -> Path:
        docs = isolated_cwd / "docs"
        docs.mkdir(exist_ok=True)
        for relpath, content in files.items():
            target = docs / relpath
            target.parent.mkdir(parents=True, exist_ok=True)
            if isinstance(content, bytes):
                target.write_bytes(content)
            else:
                target.write_text(content)
        return docs

    return _build


@pytest.fixture
def redirecting_server():
    """`redirecting_server(target)` → base URL of a local server that
    answers every request with `302 Location: target`."""
    from tests._fakes import start_redirecting_server

    server = start_redirecting_server()

    def _url(target: str) -> str:
        server.target = target
        return f"http://127.0.0.1:{server.server_port}"

    yield _url
    server.shutdown()
    server.server_close()
