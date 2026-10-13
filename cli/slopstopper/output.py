"""Shared output formatters for the slopstopper CLI.

Every check's `run()` calls into these helpers instead of bare `print()`
so the user sees one consistent visual language across the suite:

  ┌─ running()      🔍 What's about to run
  ├─ section()      ━━━ A section break with a title
  ├─ status()       ✅ / ❌ / ⚠️ status lines per finding
  ├─ footer()       📁 Where the reports landed
  └─ separator()    ━━━ A bare horizontal rule

The `--quiet` global flag (top-level in `cli.py`) sets `QUIET = True`
here so the formatters become no-ops; checks still write reports to
disk, but CI logs stay clean.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Iterable

# Toggled by `cli.main()` when `--quiet` is passed. Module-global so
# `cli.py` can flip it once and every check picks it up via the
# formatters below, with no need to thread a parameter through every
# subprocess wrapper.
QUIET = False

# Width of the horizontal-rule separator. 60 chars fits an 80-col
# terminal with room for prefix indentation. Stay in lockstep across
# checks so section breaks look uniform.
SEPARATOR_WIDTH = 60


def set_quiet(quiet: bool) -> None:
    """Toggle decorative output suppression for the current process."""
    global QUIET
    QUIET = quiet


# ── colour ───────────────────────────────────────────────────────
#
# The suite's visual language is emoji plus plain text, so colour is an
# accent, never the signal. It is only ever written when a terminal will
# read it: CI logs, a `task` run piped to a file, a report and a demo
# recording all get the bare text, so `[0;34m` never leaks into them.

_SGR = {
    "red": "\033[0;31m",
    "green": "\033[0;32m",
    "yellow": "\033[0;33m",
    "blue": "\033[0;34m",
    "bold": "\033[1m",
}
_SGR_RESET = "\033[0m"


def colour_enabled(stream=None) -> bool:
    """Whether escape codes may be written to `stream` (default stdout).

    Follows the conventions terminals and CI agree on: `NO_COLOR` set to
    anything switches colour off, `FORCE_COLOR` set to anything switches
    it on, a `TERM` of `dumb` or a stream that is not a TTY gets plain
    text. Evaluated per call, because tests and `--quiet` swap streams.
    """
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("FORCE_COLOR"):
        return True
    if os.environ.get("TERM") == "dumb":
        return False
    target = stream if stream is not None else sys.stdout
    isatty = getattr(target, "isatty", None)
    return bool(isatty and isatty())


def colour(text: str, name: str, *, stream=None) -> str:
    """Wrap `text` in the SGR sequence for `name` when colour is enabled.

    `name` is one of red, green, yellow, blue, bold. Unknown names and a
    non-colour stream both return `text` untouched, so a caller never
    has to branch on the terminal itself.
    """
    code = _SGR.get(name)
    if code is None or not colour_enabled(stream):
        return text
    return f"{code}{text}{_SGR_RESET}"


def _emit(line: str = "", *, force: bool = False, stream=None) -> None:
    """Write a single line to stdout (or the override stream).

    `force=True` bypasses the quiet flag. Use it for genuine errors that
    should land even with `--quiet`.
    """
    if QUIET and not force:
        return
    target = stream if stream is not None else sys.stdout
    print(line, file=target)


def running(message: str) -> None:
    """Mark the start of a check or major step. `🔍 <message>`."""
    _emit(f"🔍 {message}")


def section(title: str, emoji: str = "") -> None:
    """Print a titled section break: separator, title, separator."""
    if QUIET:
        return
    _emit(separator_str())
    prefix = f"{emoji} " if emoji else ""
    _emit(f"{prefix}{title}")
    _emit(separator_str())


def status(emoji: str, message: str, *, force: bool = False) -> None:
    """Print a pass/fail/warn/info status line as `<emoji> <message>`.

    `force=True` keeps the line under `--quiet` (use for terminal errors).
    """
    _emit(f"{emoji} {message}", force=force)


def info(message: str) -> None:
    """Neutral informational line. `ℹ️ <message>`."""
    _emit(f"ℹ️  {message}")


def success(message: str) -> None:
    """Pass / green-path status. `✅ <message>`."""
    _emit(f"✅ {message}")


def warn(message: str) -> None:
    """Warning that doesn't fail the check. `⚠️ <message>`."""
    _emit(f"⚠️  {message}")


def error(message: str) -> None:
    """Print a hard failure. It is always emitted, even with `--quiet`.

    Stays on stdout (not stderr) to preserve compatibility with existing
    checks and tests that asserted on `capsys.readouterr().out`. The
    `force=True` keeps the line visible under `--quiet`.
    """
    _emit(f"❌ {message}", force=True)


def separator(width: int = SEPARATOR_WIDTH) -> None:
    """Emit a horizontal rule."""
    _emit(separator_str(width))


def separator_str(width: int = SEPARATOR_WIDTH) -> str:
    """Build the separator string without printing it (for embedding)."""
    return "━" * width


def footer(report_dir: Path, files: Iterable[str] = ()) -> None:
    """Standard footer naming the report dir + the files dropped in it.

    Always appears after a check completes so the user knows where to
    look. Honours `--quiet`.
    """
    if QUIET:
        return
    _emit()
    _emit(f"📁 Reports saved to: {report_dir}/")
    for f in files:
        _emit(f"   • {f}")


def blank() -> None:
    """Emit a blank line (honours `--quiet`)."""
    _emit()
