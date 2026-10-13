"""Headers source adapters.

The CSP-drift check (and anything else that needs a list of {path, csp}
rules) reads its source via one of these adapters. Adopters pick the
adapter in `.slopstopper.yml` `headers.format`.

Adapter contract:

    def parse(path: pathlib.Path) -> list[dict]:
        '''Return [{"for": str, "csp": str | None}, ...] for each rule.'''

Path is what the adopter named in `.slopstopper.yml` `headers.source`: a
headers file, or for `meta` the built HTML root. A rule may also carry
`missing: True` (a page with no policy); its `csp` is then None.
Adapters do not validate the path's existence; the caller checks first.

Built-in adapters live as submodules here. The plan-doc convention for
adopter-supplied custom adapters is to drop a Python module under
`.ss/overrides/headers_adapters/<name>.py` exporting `parse(path)`; that
loader is a future PR.
"""

from __future__ import annotations

from pathlib import Path

from slopstopper.headers_adapters import cloudflare_adapter, json_adapter, meta_adapter
from slopstopper.headers_adapters.csp import csp_origins

__all__ = ["ADAPTERS", "csp_origins", "detect_format", "parse"]


ADAPTERS = {
    "json": json_adapter.parse,
    "cloudflare-text": cloudflare_adapter.parse,
    "meta": meta_adapter.parse,
}


def detect_format(path: Path) -> str:
    """Best-effort format detection from a file path.

    Used when `.slopstopper.yml` says `format: auto`. A directory means
    the meta adapter (built HTML), extension `.json` the json adapter, and
    anything else (including no extension, like a bare `_headers` file)
    the cloudflare-text adapter.
    """
    if path.is_dir():
        return "meta"
    if path.suffix.lower() == ".json":
        return "json"
    return "cloudflare-text"


def parse(path: Path, format_name: str) -> list[dict]:
    """Dispatch to the named adapter.

    Raises KeyError if `format_name` is unknown. The caller should validate
    against ADAPTERS.keys() and report a friendly error.
    """
    if format_name == "auto":
        format_name = detect_format(path)
    adapter = ADAPTERS[format_name]
    return adapter(path)
