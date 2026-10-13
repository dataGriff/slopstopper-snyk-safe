"""Which docs the docs checks scan, shared by docs-size, docs-structure and
docs-accuracy so one key moves all three.

Configuration (.slopstopper.yml, optional):

    hygiene:
      docs:
        exclude:              # repo-relative globs; `*` also crosses `/`
          - docs/archive/**   # the default; setting the key replaces it

For content that lives under docs/ but isn't agent or contributor
documentation, such as a course or a changelog rendered onto a site.
Every report lists what was left out, so an exclusion is never silent.
"""

from __future__ import annotations

import fnmatch
from pathlib import Path

from slopstopper import config, output

DEFAULT_EXCLUDE = ["docs/archive/**"]


def exclude_patterns() -> list[str]:
    raw = config.get("hygiene.docs.exclude", DEFAULT_EXCLUDE)
    patterns = [str(p).strip().removeprefix("./") for p in (raw if isinstance(raw, list) else [raw]) if str(p).strip()]
    usable = []
    for pattern in patterns:
        if pattern.startswith(("/", "~")) or ".." in Path(pattern).parts:
            output.warn(f"hygiene.docs.exclude entry {pattern!r} is not a repo-relative glob, so it is ignored")
            continue
        usable.append(pattern)
    return usable


def is_excluded(path: Path, patterns: list[str]) -> bool:
    rel = path.as_posix().removeprefix("./")
    return any(fnmatch.fnmatch(rel, pat) for pat in patterns)


def split(paths: list[Path]) -> tuple[list[Path], list[Path]]:
    """`(kept, excluded)` under the configured patterns, order preserved."""
    patterns = exclude_patterns()
    kept: list[Path] = []
    excluded: list[Path] = []
    for p in paths:
        (excluded if is_excluded(p, patterns) else kept).append(p)
    return kept, excluded


def report_section(excluded: list[Path]) -> str:
    """Markdown listing what `hygiene.docs.exclude` left out, or "" when nothing was."""
    if not excluded:
        return ""
    lines = [
        "## Excluded",
        "",
        f"{len(excluded)} doc(s) left out by `hygiene.docs.exclude` "
        f"({', '.join(f'`{p}`' for p in exclude_patterns())}):",
        "",
    ]
    lines += [f"- `{p.as_posix()}`" for p in excluded]
    return "\n".join(lines) + "\n\n"
