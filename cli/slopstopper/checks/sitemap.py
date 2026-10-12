"""sitemap.xml completeness + drift audit (in-CLI implementation).

Crawls a live site from `/` and cross-checks the served discovery files
against the pages it actually reaches. Where `reliability:llms-txt` and
`reliability:robots-txt` assert those files *exist and are well-formed*,
this check asserts they are *complete and current*. It is a black-box
assertion that the served `sitemap.xml` mirrors the reachable site,
**independent of how the file is produced** (hand-authored or generated).

The headline guarantee: **is `sitemap.xml` an accurate, current mirror of
the reachable site?** A reachable page absent from the sitemap is an
incompleteness bug; a `<loc>` that 404s is stale drift. Both hard-fail.
`llms.txt` is a *curated* map, so its page gaps are advisory by default.

Why the check still matters when a site generates these files at build
time: dynamic generation *prevents* drift, but the generator can still
have bugs (a collection left out of the iterator, an over-aggressive
`draft` filter). Prevention and independent detection are complementary,
so the remediation advice recommends generating the file from the route
inventory rather than hand-patching the missing `<loc>`.

Both a flat `<urlset>` and a nested `<sitemapindex>` → child sitemaps
(`sitemap-0.xml`, …) are supported transparently; neither shape is
flagged. A flat sitemap is spec-valid, because the index is only the *optional*
scale-out mechanism past the 50k-URL / 50MB limit.

CLI surface:
  slopstopper run reliability:sitemap -- --url URL [--path /sitemap.xml]
        [--llms-path /llms.txt] [--max-pages N] [--ignore GLOB ...]
        [--strict-orphans] [--require-llms-complete]

Stdlib-only (urllib + html.parser + ElementTree). Writes
.ss/reports/sitemap/sitemap-report.{md,json}.

Configuration (.slopstopper.yml, all optional):

    reliability:
      sitemap:
        max_pages: 200              # crawl bound; hitting it is logged, not silent
        ignore_paths: []             # globs excluded from crawl + diff
        allow_orphans: true          # sitemap entries not internally linked → advisory
        require_llms_complete: false # escalate llms.txt page gaps to hard-fail
        sitemap_path: /sitemap.xml
        llms_path: /llms.txt

Env-var equivalents the CLI also honours (precedence: flag > env > config):
  SITEMAP_TEST_URL   base URL to audit (required)
  SITEMAP_PATH       path to the sitemap (default: /sitemap.xml)

See .slopstopper.yml.example for the canonical schema.

Exit codes:
  0: sitemap present, complete and free of dead entries
  1: failures detected
  2: the URL is missing, or its scheme is not http/https
"""

from __future__ import annotations

import argparse
import fnmatch
import os
import urllib.error
import urllib.parse
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from pathlib import Path

from slopstopper import config, output
from slopstopper.checks import _http, _report
from slopstopper.checks._contract import refuse_unsafe_url
from slopstopper.checks.llms_txt import _extract_links
from slopstopper.discovery import SITEMAP_NS, _collect_from_urlset

REPORT_DIR = Path(".ss/reports/sitemap")
REPORT_JSON = REPORT_DIR / "sitemap-report.json"
REPORT_MD = REPORT_DIR / "sitemap-report.md"
USER_AGENT = "SlopStopper-Sitemap-Check/1.0"
DEFAULT_PATH = "/sitemap.xml"
DEFAULT_LLMS_PATH = "/llms.txt"
DEFAULT_MAX_PAGES = 200
HTML_CONTENT_TYPES = ("text/html", "application/xhtml+xml")

# Consumed by `slopstopper emit reliability:sitemap --target pr-comment`.
# No issue keys: the check's exit code fails the workflow, no
# main-branch issue is created.
META = {
    "report_path": str(REPORT_MD),
    "comment_discriminator": "🗺️ sitemap",
}


# ── safety ───────────────────────────────────────────────────────


_LABEL = "sitemap check"


def _fetch(url: str) -> tuple[int, str, str]:
    return _http.fetch_text(url, USER_AGENT, label=_LABEL)


def _head_ok(url: str) -> tuple[bool, str]:
    """Return (ok, detail) for a link target; ok means reachable and not 4xx/5xx."""
    return _http.head_ok(url, USER_AGENT, label=_LABEL)


# ── path helpers ─────────────────────────────────────────────────


def _origin(url: str) -> tuple[str, str]:
    p = urllib.parse.urlparse(url)
    return p.scheme.lower(), p.netloc.lower()


def _same_origin(base: str, url: str) -> bool:
    return _origin(base) == _origin(url)


def _normalise_path(path: str) -> str:
    """Canonicalise a path for set comparison.

    `/index.html` → `/`, a trailing `index.html` is dropped, and a trailing
    slash is stripped from everything but the root. Applied identically to
    crawled and sitemap paths so trailing-slash / index.html differences
    wash out of the diff.
    """
    if not path:
        return "/"
    if path.endswith("/index.html"):
        path = path[: -len("index.html")]
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/")
    return path or "/"


def _ignored(path: str, ignore_paths: list[str]) -> bool:
    return any(fnmatch.fnmatchcase(path, glob) for glob in ignore_paths)


def _rebase(base: str, loc: str) -> str:
    """Rebase a sitemap/llms `<loc>` onto the test origin.

    Sitemaps carry absolute production URLs even when served from a local
    build, so audit them against the origin under test. Never HEAD prod
    from a localhost run.
    """
    return urllib.parse.urljoin(base.rstrip("/") + "/", urllib.parse.urlparse(loc).path.lstrip("/"))


# ── crawl ────────────────────────────────────────────────────────


class _HrefCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.hrefs: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        for key, value in attrs:
            if key == "href" and value:
                self.hrefs.append(value)


def _extract_hrefs(body: str) -> list[str]:
    parser = _HrefCollector()
    parser.feed(body)
    return parser.hrefs


def _is_html(ctype: str) -> bool:
    return any(ct in ctype.lower() for ct in HTML_CONTENT_TYPES)


def _fetch_html(url: str) -> str | None:
    """Fetch a URL; return its body iff it is a reachable 200 HTML page."""
    try:
        status, ctype, body = _fetch(url)
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ValueError):
        return None
    if status != 200 or not _is_html(ctype):
        return None
    return body


def _next_links(base: str, url: str, body: str, visited: set[str], ignore_paths: list[str]) -> list[str]:
    """Same-origin, non-ignored, unseen `<a href>` targets on a page."""
    out: list[str] = []
    for href in _extract_hrefs(body):
        target = urllib.parse.urljoin(url, href)
        parsed = urllib.parse.urlparse(target)
        if not _http.is_http_url(target) or not _same_origin(base, target):
            continue
        npath = _normalise_path(parsed.path or "/")
        if npath in visited or _ignored(npath, ignore_paths):
            continue
        out.append(parsed._replace(query="", fragment="").geturl())
    return out


def _crawl(base: str, max_pages: int, ignore_paths: list[str]) -> tuple[set[str], bool]:
    """BFS from `/`, following same-origin `<a href>` links.

    Returns (set of normalised HTML page paths, whether max_pages was hit).
    Only server-rendered links are seen, so a JS-rendered SPA under-crawls.
    Point the check at built static output for those.
    """
    queue: list[str] = [urllib.parse.urljoin(base.rstrip("/") + "/", "")]
    visited: set[str] = set()
    crawled: set[str] = set()

    while queue:
        url = queue.pop(0)
        path = _normalise_path(urllib.parse.urlparse(url).path or "/")
        if path in visited:
            continue
        visited.add(path)

        body = _fetch_html(url)
        if body is None:
            continue

        crawled.add(path)
        if len(crawled) >= max_pages:
            return crawled, True

        queue.extend(_next_links(base, url, body, visited, ignore_paths))

    return crawled, False


# ── sitemap parsing ──────────────────────────────────────────────


def _collect_index(base: str, root: ET.Element, seen: set[str]) -> tuple[set[str], str | None]:
    """Recurse a `<sitemapindex>` into its child sitemaps over HTTP."""
    paths: set[str] = set()
    for sitemap_el in root.findall(f"{SITEMAP_NS}sitemap"):
        loc = sitemap_el.find(f"{SITEMAP_NS}loc")
        if loc is None or not loc.text:
            continue
        child_paths, err = _collect_sitemap(base, _rebase(base, loc.text.strip()), seen)
        if err:
            return set(), err
        paths |= child_paths
    return paths, None


def _parse_sitemap_xml(body: str) -> tuple[ET.Element | None, str | None]:
    """Parse sitemap bytes into a root element, or (None, error)."""
    try:
        # Encode so ElementTree accepts the `<?xml encoding=...?>` declaration.
        return ET.fromstring(body.encode("utf-8")), None  # nosec B314
    except ET.ParseError as e:
        return None, f"invalid sitemap XML: {e}"


def _collect_sitemap(base: str, sitemap_url: str, seen: set[str]) -> tuple[set[str], str | None]:
    """Fetch + parse a sitemap, recursing into index files over HTTP.

    Returns (set of normalised paths, error). The error is a string on an
    unreachable / invalid sitemap (a hard-fail), else None. Child sitemap
    URLs are rebased onto the test origin.
    """
    if sitemap_url in seen:
        return set(), None
    seen.add(sitemap_url)

    try:
        status, _ctype, body = _fetch(sitemap_url)
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ValueError) as e:
        return set(), f"sitemap not reachable: {type(e).__name__}: {e}"
    if status != 200:
        return set(), f"HTTP status {status} (expected 200)"

    root, parse_err = _parse_sitemap_xml(body)
    if parse_err:
        return set(), parse_err

    if root.tag == f"{SITEMAP_NS}sitemapindex":
        return _collect_index(base, root, seen)
    if root.tag == f"{SITEMAP_NS}urlset":
        return {_normalise_path(p) for p in _collect_from_urlset(root)}, None
    return set(), f"unexpected root element {root.tag!r} (expected urlset or sitemapindex)"


# ── llms.txt parsing ─────────────────────────────────────────────


def _llms_paths(base: str, llms_path: str) -> tuple[set[str] | None, str | None]:
    """Return (set of normalised link paths, error).

    A missing / unreachable llms.txt yields (None, note). The `llms-txt`
    check owns its existence, so here it's advisory only.

    Links are compared by path, not origin: llms.txt commonly points at its
    pages with absolute production URLs, so an origin filter would wrongly
    drop every internal link on a localhost run (and behave differently in
    CI vs prod). External links (github, other sites) simply won't share a
    path with a crawled page, so they don't create false coverage.
    """
    url = urllib.parse.urljoin(base.rstrip("/") + "/", llms_path.lstrip("/"))
    try:
        status, _ctype, body = _fetch(url)
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ValueError) as e:
        return None, f"llms.txt not reachable ({type(e).__name__}), so skipping llms cross-check"
    if status != 200:
        return None, f"llms.txt returned HTTP {status}, so skipping llms cross-check"

    paths: set[str] = set()
    for link in _extract_links(body):
        target = urllib.parse.urljoin(url, link)
        parsed = urllib.parse.urlparse(target)
        if not _http.is_http_url(target):
            continue
        paths.add(_normalise_path(parsed.path or "/"))
    return paths, None


# ── audit ────────────────────────────────────────────────────────


def _result(
    base: str,
    sitemap_url: str,
    crawled: set[str],
    sitemap_count: int,
    issues: list[str],
    notes: list[str],
    missing: list[str],
    dead: list[dict],
    orphans: list[str],
    llms_gaps: list[str],
) -> dict:
    return {
        "url": base,
        "sitemap_url": sitemap_url,
        "status": "fail" if issues else "pass",
        "issues": issues,
        "notes": notes,
        "crawled_count": len(crawled),
        "sitemap_count": sitemap_count,
        "missing_pages": missing,
        "dead_entries": dead,
        "orphan_entries": orphans,
        "llms_gaps": llms_gaps,
    }


def _diff_extra(
    base: str, extra: set[str], allow_orphans: bool, issues: list[str], notes: list[str]
) -> tuple[list[dict], list[str]]:
    """Classify sitemap entries not in the crawl → dead (fail) or orphan (advisory)."""
    dead: list[dict] = []
    orphans: list[str] = []
    for path in sorted(extra):
        ok, detail = _head_ok(urllib.parse.urljoin(base.rstrip("/") + "/", path.lstrip("/")))
        if not ok:
            dead.append({"path": path, "detail": detail})
            issues.append(f"Stale sitemap entry (unreachable {detail}): {path}")
        else:
            orphans.append(path)
            (notes if allow_orphans else issues).append(
                f"Sitemap entry not internally linked (orphan): {path}"
            )
    return dead, orphans


def _diff_llms(
    base: str,
    llms_path: str,
    crawled: set[str],
    require_llms_complete: bool,
    issues: list[str],
    notes: list[str],
) -> list[str]:
    """Advisory (unless required) diff of crawled pages against llms.txt links."""
    llms_paths, llms_err = _llms_paths(base, llms_path)
    if llms_err:
        notes.append(llms_err)
        return []
    if llms_paths is None:
        return []
    gaps = sorted(crawled - llms_paths)
    for path in gaps:
        (issues if require_llms_complete else notes).append(
            f"Reachable page not listed in llms.txt: {path}"
        )
    return gaps


def _audit(
    base: str,
    sitemap_path: str,
    llms_path: str,
    max_pages: int,
    ignore_paths: list[str],
    allow_orphans: bool,
    require_llms_complete: bool,
) -> dict:
    _http.require_safe_url(base, _LABEL)  # fail fast; the crawl/collect loops swallow scheme errors
    issues: list[str] = []
    notes: list[str] = []

    crawled, capped = _crawl(base, max_pages, ignore_paths)
    if capped:
        output.warn(f"Crawl hit max_pages={max_pages}; diff covers the first {len(crawled)} pages only")
        notes.append(f"Crawl capped at max_pages={max_pages}; pages beyond the cap were not checked")

    sitemap_url = urllib.parse.urljoin(base.rstrip("/") + "/", sitemap_path.lstrip("/"))
    sitemap_paths, sitemap_err = _collect_sitemap(base, sitemap_url, set())
    if sitemap_err:
        issues.append(sitemap_err)
        return _result(base, sitemap_url, crawled, 0, issues, notes, [], [], [], [])

    sitemap_paths = {p for p in sitemap_paths if not _ignored(p, ignore_paths)}

    # crawled − sitemap → reachable but unlisted → incompleteness (hard-fail)
    missing_pages = sorted(crawled - sitemap_paths)
    for path in missing_pages:
        issues.append(f"Reachable page missing from sitemap.xml: {path}")

    dead_entries, orphan_entries = _diff_extra(base, sitemap_paths - crawled, allow_orphans, issues, notes)
    llms_gaps = _diff_llms(base, llms_path, crawled, require_llms_complete, issues, notes)

    return _result(
        base, sitemap_url, crawled, len(sitemap_paths), issues, notes,
        missing_pages, dead_entries, orphan_entries, llms_gaps,
    )


# ── markdown report ──────────────────────────────────────────────


def _build_markdown_report(result: dict) -> str:
    lines: list[str] = []
    lines.append("# 🗺️ sitemap Report")
    lines.append("")
    lines.append(f"**Site:** {result['url']}")
    lines.append("")
    lines.append(f"**Sitemap:** {result['sitemap_url']}")
    lines.append("")
    lines.append(f"**Overall:** {'✅ PASS' if result['status'] == 'pass' else '❌ FAIL'}")
    lines.append("")
    lines.append(
        f"**Pages crawled:** {result['crawled_count']} · "
        f"**Sitemap entries:** {result['sitemap_count']}"
    )
    lines.append("")
    lines.extend(_report.render_issues(result["issues"]))
    lines.extend(_report.render_notes(result["notes"]))
    lines.append("---")
    lines.append("")
    lines.append("## How to Fix")
    lines.append("")
    lines.append(
        "- **The durable fix is to generate the file, not hand-edit it.** Missing and stale entries "
        "mean your sitemap has drifted from your routes. Rather than hand-patching each "
        "`<loc>`, generate `sitemap.xml` (and `llms.txt`) from your route inventory at "
        "build time, using a framework sitemap integration (e.g. `@astrojs/sitemap`) or a "
        "build-time endpoint that iterates your content. A generated file can't drift. "
        "See [docs/reliability/SITEMAP.md](../../../docs/reliability/SITEMAP.md)."
    )
    lines.append(
        "- **Reachable page missing from sitemap** → add it (or, better, regenerate it as "
        "described above). This is the incompleteness the check guards against."
    )
    lines.append(
        "- **Stale sitemap entry (unreachable)** → the `<loc>` 404s; remove the deleted "
        "page or fix its URL."
    )
    lines.append(
        "- **Orphan** → a real page that nothing links to. Add an internal link, or accept "
        "it (`reliability.sitemap.allow_orphans: true`, the default, keeps orphans advisory)."
    )
    lines.append("")
    return "\n".join(lines) + "\n"


def _write_reports(result: dict) -> None:
    _report.write_reports(REPORT_JSON, REPORT_MD, result, _build_markdown_report)


def _print_result(result: dict) -> None:
    icon = "✅" if result["status"] == "pass" else "❌"
    output._emit(f"  {icon} {result['url']} ({result['crawled_count']} pages crawled)")
    for issue in result["issues"]:
        output._emit(f"      - {issue}")


# ── CLI entrypoint ───────────────────────────────────────────────


def _parse_args(args: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="slopstopper run reliability:sitemap", add_help=False)
    p.add_argument("url_positional", nargs="?", default=None, help="Base URL to audit")
    p.add_argument("--url", default=None, help="Base URL to audit")
    p.add_argument("--path", default=None, help="Path to sitemap.xml (default: /sitemap.xml)")
    p.add_argument("--llms-path", default=None, help="Path to llms.txt (default: /llms.txt)")
    p.add_argument("--max-pages", type=int, default=None, help="Crawl bound (default: 200)")
    p.add_argument("--ignore", action="append", default=None, help="Glob to exclude (repeatable)")
    p.add_argument(
        "--strict-orphans",
        action="store_true",
        help="Fail on sitemap entries that nothing links to",
    )
    p.add_argument(
        "--require-llms-complete",
        action="store_true",
        help="Fail if a reachable page is missing from llms.txt",
    )
    p.add_argument("--help", "-h", action="help")
    return p.parse_args(args or [])


def _resolve_url(parsed_url: str | None) -> str | None:
    return parsed_url or os.environ.get("SITEMAP_TEST_URL")


def _resolve_path(parsed_path: str | None) -> str:
    return (
        parsed_path
        or os.environ.get("SITEMAP_PATH")
        or config.get("reliability.sitemap.sitemap_path")
        or DEFAULT_PATH
    )


def _resolve_opts(parsed: argparse.Namespace) -> dict:
    """Resolve every knob (flag > env > config) into `_audit` kwargs."""
    return {
        "sitemap_path": _resolve_path(parsed.path),
        "llms_path": parsed.llms_path or config.get("reliability.sitemap.llms_path") or DEFAULT_LLMS_PATH,
        "max_pages": parsed.max_pages or config.get_int("reliability.sitemap.max_pages", DEFAULT_MAX_PAGES),
        "ignore_paths": list(parsed.ignore or config.get("reliability.sitemap.ignore_paths", []) or []),
        "allow_orphans": not parsed.strict_orphans
        and config.get_bool("reliability.sitemap.allow_orphans", True),
        "require_llms_complete": parsed.require_llms_complete
        or config.get_bool("reliability.sitemap.require_llms_complete", False),
    }


def run(args: list[str] | None = None) -> int:
    parsed = _parse_args(args)
    url = _resolve_url(parsed.url_positional or parsed.url)
    if not url:
        output.error("sitemap target URL is required")
        output._emit("Usage:")
        output._emit("  slopstopper run reliability:sitemap -- --url https://your-site.example.com")
        output._emit("  SITEMAP_TEST_URL=https://your-site slopstopper run reliability:sitemap")
        return 2

    if (rc := refuse_unsafe_url(url, _LABEL)) is not None:
        return rc

    output.status("🗺️", f"sitemap completeness audit against: {url}")
    output.separator()

    try:
        result = _audit(url, **_resolve_opts(parsed))
    except ValueError as e:
        # A bad input the up-front URL guard couldn't see (a configured path
        # that composes to an unusable URL): the check could not run.
        output.error(str(e))
        return 2

    _write_reports(result)
    _print_result(result)

    output.separator()
    if result["status"] == "pass":
        output.success("sitemap.xml is complete and free of dead entries.")
        return 0
    output.error("Failures detected. See .ss/reports/sitemap/sitemap-report.md for full details.")
    return 1
