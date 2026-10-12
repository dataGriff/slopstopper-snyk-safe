"""`<meta>` CSP header-source adapter, for hosts that can't send headers.

GitHub Pages and S3 without CloudFront serve no custom response headers,
so the CSP lives in each page's
`<meta http-equiv="Content-Security-Policy" content="…">`. `path` is the
built HTML root (`headers.source: dist`); every `*.html` under it is a
page, served at its path relative to the root (`blog/index.html` →
`/blog/`).

The policy most pages share is the site baseline and comes back as
`/*`. A page whose policy allows an external origin the baseline
doesn't is a relaxation and comes back under its own path, as a per-path
block would in a headers file. A page with no CSP meta tag, or an empty one, comes back
with `csp: None, missing: True`, so the check can report it instead of
reading it as unrestricted.
"""

from __future__ import annotations

from collections import Counter
from html.parser import HTMLParser
from pathlib import Path

from slopstopper.headers_adapters.csp import csp_origins


class _CspMeta(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.csp: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "meta" or self.csp is not None:
            return
        values = {k.lower(): (v or "") for k, v in attrs}
        if values.get("http-equiv", "").lower() == "content-security-policy":
            self.csp = values.get("content", "").strip()


def page_csp(html: str) -> str | None:
    parser = _CspMeta()
    parser.feed(html)
    return parser.csp


def url_path(page: Path, root: Path) -> str:
    rel = page.relative_to(root).as_posix()
    if rel == "index.html":
        return "/"
    if rel.endswith("/index.html"):
        return "/" + rel.removesuffix("index.html")
    return "/" + rel


def parse(path: Path) -> list[dict]:
    """Return [{for, csp[, missing]}]: the `/*` baseline, each relaxation, each page without a policy."""
    pages = {
        url_path(p, path): page_csp(p.read_text(errors="replace"))
        for p in sorted(path.rglob("*.html"))
    }
    policies = [csp for csp in pages.values() if csp]
    if not policies:
        return [{"for": url, "csp": None, "missing": True} for url in pages]
    baseline = Counter(policies).most_common(1)[0][0]
    allowed = csp_origins(baseline)
    rules: list[dict] = [{"for": "/*", "csp": baseline}]
    for url, csp in pages.items():
        if not csp:
            rules.append({"for": url, "csp": None, "missing": True})
        elif csp_origins(csp) - allowed:
            rules.append({"for": url, "csp": csp})
    return rules
