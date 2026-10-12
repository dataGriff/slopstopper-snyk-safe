"""Read values from .slopstopper.yml.

Stdlib-only YAML subset parser, lifted from .ss/scripts/load_config.py
during the CLI pivot. Slopstopper deliberately avoids a PyYAML dependency
so the CLI's dependency surface stays minimal (lizard is the one runtime
dep, for the complexity check). The subset is enough for the
.slopstopper.yml shape: scalars, nested mappings, sequences of scalars,
inline lists, and `null`/empty values. Tab indentation, inline maps,
folded/literal block scalars, anchors and quoted keys are not supported,
and the parser warns on stderr when it sees one.

Values are never coerced: numbers and booleans come back as the strings
the file spelled. Use get_int / get_bool / get_str rather than wrapping
get() in int() or bool(), because `bool("false")` is True. A value that is set
but unusable (`max_ccn: fifteen`) falls back to the default AND warns on
stderr, for the same reason an unparsed line does: an override the
adopter thinks applied must never vanish silently.

If the file doesn't exist or a key is absent, get() returns the supplied
default. Errors during parsing are non-fatal. They fall back to defaults
and warn on stderr.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

CONFIG_PATH = Path(".slopstopper.yml")

_INDENT_RE = re.compile(r"^( *)(.*)$")
_KV_RE = re.compile(r"^([A-Za-z0-9_\-]+):\s*(.*)$")
_LIST_ITEM_RE = re.compile(r"^-\s+(.*)$")
_INLINE_LIST_RE = re.compile(r"^\[(.*)\]$")
_DOCUMENT_MARKERS = frozenset({"---", "..."})

_KEYWORDS = {"": None, "null": None, "~": None, "true": True, "false": False}


def _parse_inline_list(s: str) -> list:
    inner = _INLINE_LIST_RE.match(s).group(1).strip()
    if not inner:
        return []
    return [_parse_scalar(part) for part in inner.split(",")]


def _strip_quotes(s: str) -> str | None:
    if len(s) >= 2 and s[0] == s[-1] and s[0] in ("'", '"'):
        return s[1:-1]
    return None


def _parse_scalar(raw: str) -> object:
    s = raw.strip()
    keyword_value = _KEYWORDS.get(s.lower()) if s.lower() in _KEYWORDS else "MISS"
    if keyword_value != "MISS":
        return keyword_value
    unquoted = _strip_quotes(s)
    if unquoted is not None:
        return unquoted
    if _INLINE_LIST_RE.match(s):
        return _parse_inline_list(s)
    return s


# A quote opens a quoted scalar only at a scalar boundary: the start of the
# line, after whitespace, or after `[` / `,` inside an inline list. An
# apostrophe inside a plain scalar (`desc: don't panic`) is just a character.
_QUOTE_OPENERS = frozenset(" \t[,")


def _strip_comment(line: str) -> str:
    """Drop a trailing `# comment`, YAML-style.

    A `#` starts a comment only at the beginning of the line or after
    whitespace, which is the same rule real YAML applies. Without that rule an
    unquoted `url: https://example.com/#anchor` silently lost its
    fragment and became a different, valid-looking URL. Quoted scalars
    are skipped over, so `title: "a # b"` keeps its `#`.
    """
    if "#" not in line:
        return line
    quote: str | None = None  # the quote character that opened the current scalar
    for i, ch in enumerate(line):
        if quote is not None:
            if ch == quote:
                quote = None
            continue
        if ch in ("'", '"'):
            if i == 0 or line[i - 1] in _QUOTE_OPENERS:
                quote = ch
        elif ch == "#" and (i == 0 or line[i - 1].isspace()):
            return line[:i].rstrip()
    return line


def _pop_to_parent(stack: list, indent: int) -> object | None:
    while stack and stack[-1][0] >= indent:
        stack.pop()
    if not stack:
        return None
    return stack[-1][1]


def _handle_list_item(parent: object, item: re.Match, stack: list) -> None:
    """Append a parsed list item (an `_LIST_ITEM_RE` match) to the parent.

    If the parent is an empty dict opened by `key:` with no value, this is
    actually a block list. Retroactively convert it to a list under the
    grandparent, so the dict→list ambiguity inherent to YAML is resolved
    on the first list item encountered.
    """
    value = _parse_scalar(item.group(1))
    if isinstance(parent, list):
        parent.append(value)
        return
    if isinstance(parent, dict) and not parent and len(stack) >= 2:
        grandparent = stack[-2][1]
        if isinstance(grandparent, dict):
            for k, v in grandparent.items():
                if v is parent:
                    new_list: list = [value]
                    grandparent[k] = new_list
                    stack[-1] = (stack[-1][0], new_list)
                    return


def _handle_key_value(parent: object, indent: int, kv: re.Match, stack: list) -> None:
    """Store a `key: value` line (a `_KV_RE` match) under the parent mapping."""
    if not isinstance(parent, dict):
        return
    key, value_str = kv.group(1), kv.group(2)
    if value_str == "":
        new_container: dict = {}
        parent[key] = new_container
        stack.append((indent, new_container))
    else:
        parent[key] = _parse_scalar(value_str)


def _load_yaml_subset(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        raw = path.read_text()
    except OSError as e:
        print(f"⚠  slopstopper.config: could not read {path} ({e}), so using defaults", file=sys.stderr)
        return {}

    root: dict = {}
    stack: list[tuple[int, object]] = [(-1, root)]

    for lineno, raw_line in enumerate(raw.splitlines(), start=1):
        line = _strip_comment(raw_line)
        if not line.strip():
            continue
        match = _INDENT_RE.match(line)
        if not match:
            continue
        indent, body = len(match.group(1)), match.group(2)
        parent = _pop_to_parent(stack, indent)
        if parent is None:
            return root
        if indent == 0 and body in _DOCUMENT_MARKERS:
            # `---` / `...` are valid, common and carry nothing, so there
            # is nothing to warn about.
            continue
        item = _LIST_ITEM_RE.match(body)
        kv = None if item else _KV_RE.match(body)
        if item:
            _handle_list_item(parent, item, stack)
        elif kv:
            _handle_key_value(parent, indent, kv, stack)
        else:
            # Outside the subset (tab indentation, inline maps, folded
            # scalars, anchors, quoted keys…). Say so: a silently skipped
            # line means the check runs with a default the adopter thinks
            # they overrode, and there is nothing in the output to tell
            # them why.
            _warn_unparsed(path, lineno, raw_line)

    return _convert_empty_dicts_to_lists_if_needed(root, raw)


def _warn_unparsed(path: Path, lineno: int, raw_line: str) -> None:
    hint = ""
    if raw_line.startswith("\t"):
        hint = " (it uses tab indentation, so switch to spaces)"
    print(
        f"⚠  slopstopper.config: {path}:{lineno} is outside the supported YAML subset"
        f"{hint} and was ignored: {raw_line.strip()!r}",
        file=sys.stderr,
    )


def _convert_empty_dicts_to_lists_if_needed(node: object, raw_yaml: str) -> object:
    if isinstance(node, dict):
        for key, value in list(node.items()):
            if isinstance(value, dict) and not value:
                pattern = re.compile(rf"^\s*{re.escape(key)}:\s*\n\s+-\s+", re.MULTILINE)
                if pattern.search(raw_yaml):
                    node[key] = []
            else:
                _convert_empty_dicts_to_lists_if_needed(value, raw_yaml)
    return node


_CACHE: dict | None = None
_CACHE_PATH: Path | None = None


def _config() -> dict:
    global _CACHE, _CACHE_PATH
    if _CACHE is None or _CACHE_PATH != CONFIG_PATH:
        _CACHE = _load_yaml_subset(CONFIG_PATH)
        _CACHE_PATH = CONFIG_PATH
    return _CACHE


def get(path: str, default: object = None) -> object:
    """Return the value at a dot-path in .slopstopper.yml, or default."""
    node: object = _config()
    for segment in path.split("."):
        if not isinstance(node, dict) or segment not in node:
            return default
        node = node[segment]
    if node is None:
        return default
    if isinstance(node, dict) and not node:
        return default
    return node


# ── typed accessors ──────────────────────────────────────────────
#
# The subset parser never coerces: `max_ccn: 15` comes back as the string
# '15', and `"false"` (quoted) as the string 'false'. Every check used to
# hand-roll its own int() / bool() around get(), four of them with a
# byte-identical helper and two with a bare int() that raised a traceback
# on `max_ccn: fifteen`. These are the one copy.

_TRUE_WORDS = frozenset({"true", "yes", "1", "on"})
_FALSE_WORDS = frozenset({"false", "no", "0", "off"})


def _warn_unusable(path: str, raw: object, kind: str, default: object) -> None:
    print(
        f"⚠  slopstopper.config: {path} is set to {raw!r}, which is not {kind}, so "
        f"using the default ({default!r})",
        file=sys.stderr,
    )


_UNSET = object()


def get_int(path: str, default: int | None = None) -> int | None:
    """Integer at `path`, or `default` when unset.

    A value that is set but not an integer (`max_ccn: fifteen`, or a bare
    `true`) also yields `default`. It warns on stderr too, so the override
    the adopter wrote doesn't silently turn into the default.
    """
    raw = get(path, _UNSET)
    if raw is _UNSET:
        return default
    if not isinstance(raw, bool):
        try:
            return int(raw)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            pass
    _warn_unusable(path, raw, "an integer", default)
    return default


def get_bool(path: str, default: bool = False) -> bool:
    """Boolean at `path`, or `default` when unset.

    Accepts real booleans and the usual spellings as strings, so a quoted
    `"false"` is False rather than a non-empty (truthy) string. Anything
    else yields `default` and warns on stderr.
    """
    raw = get(path, _UNSET)
    if raw is _UNSET:
        return default
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, str):
        word = raw.strip().lower()
        if word in _TRUE_WORDS:
            return True
        if word in _FALSE_WORDS:
            return False
    _warn_unusable(path, raw, "a boolean", default)
    return default


def get_str(path: str, default: str = "") -> str:
    """String at `path`, or `default` when unset."""
    raw = get(path, default)
    return default if raw is None else str(raw)


def reload() -> None:
    """Force re-read of .slopstopper.yml (for tests)."""
    global _CACHE, _CACHE_PATH
    _CACHE = None
    _CACHE_PATH = None
