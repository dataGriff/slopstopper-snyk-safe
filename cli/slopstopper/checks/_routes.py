"""Shared helpers for the AGENTS.md-first docs checks.

`hygiene:entry-files` and `hygiene:docs-structure` both read the same
things out of a markdown file: how many tokens it costs an agent to load,
which docs it links, and whether each link is a *route* an agent will
actually follow. One copy, so the two checks agree on what a route is.

A route is a logical line (a paragraph, bullet or table row outside
fenced code) that links a `.md` file with, in this order, a trigger, then
"read", then the link:

    Before you change CI, read docs/ci.md for what `task ci` runs.
    | When you are…  | Do this                                   |
    | adding a check | Read [hygiene/README.md](hygiene/README.md) |

"See docs/ci.md for details" is a soft link: agents treat it as optional
and skip it, then reinvent what the doc already settled. A line with the
trigger stated and the verb "read" gets followed. That measured
difference is the whole reason the checks insist on the form.
"""

from __future__ import annotations

import re
from pathlib import Path

# Tokens are estimated as chars/4. That is coarse, but stable,
# dependency-free and far closer to what a tokenizer charges for link-heavy markdown
# than a word count is (a 1,400-word AGENTS.md full of tables and links
# costs ~3,200 tokens, not ~2,000).
_BADGE_LINE_RE = re.compile(r"^\s*(?:\[?!\[[^\]]*\]\([^)]*\)\]?(?:\([^)]*\))?\s*)+$")
# The three ways markdown links a file: inline, reference-style, HTML.
_INLINE_LINK_RE = re.compile(r"\]\(\s*<?([^)\s>]+)>?(?:\s+(?:\"[^\"]*\"|'[^']*'))?\s*\)")
_REFERENCE_USE_RE = re.compile(r"\[([^\]]*)\]\[([^\]]*)\]")
_REFERENCE_DEF_RE = re.compile(r"^\s*\[([^\]]+)\]:\s*<?(\S+?)>?(?:\s+(?:\"[^\"]*\"|'[^']*'|\([^)]*\)))?\s*$")
_HREF_RE = re.compile(r"""href\s*=\s*["']([^"']+)["']""")
_BLOCK_START_RE = re.compile(r"^\s*(#|\||[-*+]\s|\d+[.)]\s|>)")
_FENCE_RE = re.compile(r"^\s*(`{3,}|~{3,})")
_PIPED_ROW_RE = re.compile(r"^\s*\|")
# A header separator is made of nothing but pipes, dashes, colons and spaces.
_TABLE_RULE_RE = re.compile(r"^\s*\|?(?:\s*:?-+:?\s*\|)*\s*:?-+:?\s*\|?\s*$")
_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.S)
_NESTED_ITEM_RE = re.compile(r"^\s{2,}(?:[-*+]|\d+[.)])\s")
_SENTENCE_END_RE = re.compile(r"[.!?](?=\s)")
_READ_CUE_RE = re.compile(r"\b(read|open|load|follow)\b", re.I)
# "For <situation>, read X" is trigger-first too; "read X for details" is not.
_TRIGGER_CUE_RE = re.compile(
    r"\b(before|when|if|whenever|unless|first|any task)\b|^\W*for\b", re.I
)
ROUTE_TEMPLATE = "Before you <do X>, read <doc> for <what it holds>"


def estimate_tokens(text: str) -> int:
    """Estimated token cost of `text`, badge-only lines excluded.

    A README's pipeline-status badges are images a reader never parses
    as prose; counting their URLs would charge the budget for a block
    `slopstopper badges` generates. Everything else counts.
    """
    kept = [line for line in text.splitlines() if not _BADGE_LINE_RE.match(line)]
    return len("\n".join(kept)) // 4


def count_lines(text: str) -> int:
    return len(text.splitlines())


def _first_cell(row: str) -> str:
    cells = [c.strip() for c in row.strip().strip("|").split("|")]
    return cells[0] if cells else ""


class _Joiner:
    """Accumulates source lines into logical lines (see `logical_lines`)."""

    def __init__(self) -> None:
        self.out: list[tuple[int, str]] = []
        self.current: str | None = None
        self.start = 0
        self.in_table = False
        self.table_prefix = ""

    def flush(self) -> None:
        if self.current is not None:
            self.out.append((self.start, self.current))
        self.current = None

    def blank(self) -> None:
        self.flush()
        self.in_table = False
        self.table_prefix = ""

    def table_row(self, stripped: str, lineno: int) -> None:
        self.flush()
        self.start = lineno
        if _TABLE_RULE_RE.match(stripped):
            return
        if not self.in_table:
            # The first row of a table is its header. When its first cell
            # names a trigger ("When you are…") the column *is* the
            # trigger, so every data row is read with it prefixed.
            self.in_table = True
            first = _first_cell(stripped)
            self.table_prefix = first + " " if _TRIGGER_CUE_RE.search(first) else ""
            self.current = stripped
            return
        self.current = self.table_prefix + stripped

    def block_start(self, stripped: str, lineno: int) -> None:
        self.flush()
        self.start = lineno
        self.current = stripped
        self.in_table = False
        self.table_prefix = ""

    def continuation(self, stripped: str) -> None:
        self.current += " " + stripped  # type: ignore[operator]


def _table_row_flags(lines: list[str]) -> list[bool]:
    """Which source lines are table rows.

    GFM allows a table without leading pipes (`a | b` / `--- | ---`), so a
    leading `|` is not required: a run of lines containing a pipe whose
    second line is the header rule is a table too.
    """
    flags = [bool(_PIPED_ROW_RE.match(line)) for line in lines]
    i = 0
    while i < len(lines) - 1:
        if "|" in lines[i] and _TABLE_RULE_RE.match(lines[i + 1]) and "|" in lines[i + 1]:
            j = i
            while j < len(lines) and "|" in lines[j] and lines[j].strip():
                flags[j] = True
                j += 1
            i = j
        else:
            i += 1
    return flags


def _strip_comments(text: str) -> tuple[list[str], list[int]]:
    """`text` without HTML comments, split into lines, plus the 1-based
    source line each kept line starts on (a multi-line comment shifts them)."""
    kept: list[str] = []
    removed: list[tuple[int, int]] = []  # (offset in the stripped text, newlines removed there)
    last = 0
    for m in _HTML_COMMENT_RE.finditer(text):
        kept.append(text[last:m.start()])
        removed.append((sum(map(len, kept)), m.group(0).count("\n")))
        last = m.end()
    kept.append(text[last:])
    lines = "".join(kept).splitlines(keepends=True)
    origin: list[int] = []
    offset = newlines = 0
    for line in lines:
        hidden = sum(n for at, n in removed if at < offset)
        origin.append(1 + newlines + hidden)
        offset += len(line)
        newlines += 1
    return [(line.splitlines() or [""])[0] for line in lines], origin


def numbered_logical_lines(text: str) -> list[tuple[int, str]]:
    """`logical_lines`, each paired with the 1-based source line it starts on."""
    joiner = _Joiner()
    fence: str | None = None
    lines, origin = _strip_comments(text)  # a commented-out route is not a route
    is_row = _table_row_flags(lines)
    for line, row, lineno in zip(lines, is_row, origin):
        stripped = line.strip()
        opener = _FENCE_RE.match(line)
        if opener and (fence is None or stripped.startswith(fence)):
            fence = None if fence else opener.group(1)[0] * 3
            joiner.blank()  # a fence is a block boundary either way
        elif fence:
            continue
        elif _REFERENCE_DEF_RE.match(line):
            continue  # a definition is a footnote, not a sentence
        elif not stripped:
            joiner.blank()
        elif row:
            joiner.table_row(stripped, lineno)
        elif joiner.current is not None and _NESTED_ITEM_RE.match(line):
            joiner.continuation(stripped)  # a sub-bullet continues its parent's sentence
        elif joiner.current is None or _BLOCK_START_RE.match(line):
            joiner.block_start(stripped, lineno)
        else:
            joiner.continuation(stripped)
    joiner.flush()
    return joiner.out


def logical_lines(text: str) -> list[str]:
    """Paragraphs, bullets and table rows as single strings.

    Wrapped prose is joined so a route split over two source lines still
    reads as one. Fenced code blocks (``` or ~~~) are skipped, because a
    link inside a code sample is an example, not a route. A data row of a
    table whose header's first cell is a trigger ("When you are…") comes
    back with that header prefixed, so the row reads as the trigger-first
    sentence it is.
    """
    return [line for _lineno, line in numbered_logical_lines(text)]


def read_markdown(path: Path) -> str:
    """Read a doc as UTF-8, substituting undecodable bytes rather than crashing:
    a Latin-1 stray in one file must not turn a finding into a traceback."""
    return path.read_text(encoding="utf-8", errors="replace")


def reference_definitions(text: str) -> dict[str, str]:
    """`[label]: target` definitions, keyed by lower-cased label."""
    refs: dict[str, str] = {}
    for line in _HTML_COMMENT_RE.sub("", text).splitlines():
        m = _REFERENCE_DEF_RE.match(line)
        if m:
            refs.setdefault(m.group(1).strip().lower(), m.group(2))
    return refs


def _is_doc_target(target: str) -> bool:
    bare = target.split("#", 1)[0].split("?", 1)[0]
    return bare.endswith(".md") and not target.startswith(("http://", "https://", "mailto:", "/"))


def link_spans(line: str, refs: dict[str, str] | None = None) -> list[tuple[int, str]]:
    """`(position, target)` for every `.md` link on one logical line.

    Inline `[t](x.md)`, reference `[t][ref]` / `[ref][]` resolved through
    `refs`, and HTML `href="x.md"`. URLs and absolute paths are not docs.
    """
    spans: list[tuple[int, str]] = []
    for m in _INLINE_LINK_RE.finditer(line):
        spans.append((m.start(), m.group(1)))
    for m in _HREF_RE.finditer(line):
        spans.append((m.start(), m.group(1)))
    for m in _REFERENCE_USE_RE.finditer(line):
        label = (m.group(2) or m.group(1)).strip().lower()
        target = (refs or {}).get(label)
        if target:
            spans.append((m.start(), target))
    return sorted((p, t.split("#", 1)[0].split("?", 1)[0]) for p, t in spans if _is_doc_target(t))


def doc_links(line: str, refs: dict[str, str] | None = None) -> list[str]:
    """Relative `.md` link targets on one logical line (URLs excluded)."""
    return [t for _p, t in link_spans(line, refs)]


def _mask_targets(line: str) -> str:
    """Blank out link targets so a cue word inside a path does not count."""
    def blank(m: re.Match) -> str:
        return m.group(0)[:2] + " " * (len(m.group(0)) - 3) + ")"
    masked = _INLINE_LINK_RE.sub(blank, line)
    return _HREF_RE.sub(lambda m: " " * len(m.group(0)), masked)


def _explicit_before(line: str, position: int) -> bool:
    """True when, within the sentence that holds the link, a read cue
    precedes `position` and a trigger precedes that cue.

    The window is one sentence, not the paragraph: a "see also" link in a
    paragraph that happened to say "if" and "read" three sentences earlier
    is still a soft link.
    """
    head = _mask_targets(line)[:position]
    ends = [m.end() for m in _SENTENCE_END_RE.finditer(head)]
    if ends:
        head = " " * ends[-1] + head[ends[-1]:]
    reads = [m.start() for m in _READ_CUE_RE.finditer(head)]
    if not reads:
        return False
    return any(t.start() < reads[-1] for t in _TRIGGER_CUE_RE.finditer(head))


def is_explicit_route(line: str, refs: dict[str, str] | None = None) -> bool:
    """True when the line has a trigger, then "read", then the link, in that order."""
    spans = link_spans(line, refs)
    if spans:
        return _explicit_before(line, spans[0][0])
    return _explicit_before(line, len(line))


def resolve(source: Path, target: str) -> Path | None:
    try:
        return (source.parent / target).resolve()
    except (OSError, ValueError):
        return None


def route_table(source: Path) -> tuple[dict[Path, str], dict[Path, str]]:
    """`numbered_route_table` without the line numbers."""
    explicit, soft = numbered_route_table(source)
    return (
        {doc: line for doc, (_n, line) in explicit.items()},
        {doc: line for doc, (_n, line) in soft.items()},
    )


def numbered_route_table(
    source: Path,
) -> tuple[dict[Path, tuple[int, str]], dict[Path, tuple[int, str]]]:
    """Split the `.md` links in `source` into explicit routes and soft links.

    Returns `(explicit, soft)`, each mapping the resolved target to the
    first logical line that mentions it, as `(source line number, line)`.
    Each link is judged on its own position: a trigger and a read cue must
    both come before it. A doc
    that is routed explicitly on one line and mentioned softly on another
    counts as routed; a doc with only soft mentions is unreachable in
    practice.
    """
    explicit: dict[Path, tuple[int, str]] = {}
    soft: dict[Path, tuple[int, str]] = {}
    try:
        text = read_markdown(source)
    except OSError:
        return explicit, soft
    refs = reference_definitions(text)
    for lineno, line in numbered_logical_lines(text):
        for position, target in link_spans(line, refs):
            resolved = resolve(source, target)
            if resolved is None:
                continue
            bucket = explicit if _explicit_before(line, position) else soft
            bucket.setdefault(resolved, (lineno, line))
    for doc in explicit:
        soft.pop(doc, None)
    return explicit, soft


def soft_route_label(source: str, lineno: int, target: str, line: str) -> str:
    """Where a soft link sits and what it links: `AGENTS.md line 5 → docs/ci.md: "…"`."""
    return f"{source} line {lineno} → {target}: \"{line[:70]}\""


def soft_route_message(source: str, lineno: int, target: str, line: str) -> str:
    return (
        f"{soft_route_label(source, lineno, target, line)} is a soft route. A route agents "
        f"follow names its trigger and says read: '{ROUTE_TEMPLATE}'."
    )


def route_snippet(target: str, holds: str) -> str:
    """A paste-ready explicit route to `target`."""
    return f"Before you <do X>, read [`{target}`](./{target}) for {holds}."
