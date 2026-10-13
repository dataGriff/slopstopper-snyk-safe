"""SEO + social-share metatag audit (in-CLI implementation).

Lifted from .ss/scripts/check-seo-metatags.py during Phase 2 of the CLI
pivot. The bash-side script remains in place for adopters who haven't
upgraded; the CLI no longer subprocesses to it.

Fetches one or more pages and asserts the presence of the metadata that
social-share crawlers (Slack, Twitter/X, LinkedIn, Discord, Facebook)
and SEO indexers rely on. It complements `reliability:cwv`, because
Lighthouse's SEO category covers core SEO but does not flag missing
OpenGraph or Twitter tags.

CLI surface:
  slopstopper run reliability:seo -- --url URL [--pages /,/blog]
        [--no-require-og-image] [--no-verify-og-image]
        [--og-image-base http://localhost:8080]

Stdlib-only (urllib + HTMLParser). Writes
.ss/reports/seo/seo-metatags-report.{md,json}.

Configuration (.slopstopper.yml, all optional):

    pages:
      seo: /,/blog,/about
    reliability:
      coverage:
        pr: changed     # see slopstopper.discovery for the resolution order
        main: sitemap

Env-var equivalents the CLI also honours (precedence: flag > env > config > "/"):
  SEO_TEST_URL, SEO_PAGES, SEO_REQUIRE_OG_IMAGE, SEO_VERIFY_OG_IMAGE,
  SEO_OG_IMAGE_BASE.

See .slopstopper.yml.example for the canonical schema.

Exit codes:
  0: all pages passed
  1: failures detected
  2: the URL is missing, or its scheme is not http/https
"""

from __future__ import annotations

import argparse
import json
import os
import urllib.error
import urllib.parse
from html.parser import HTMLParser
from pathlib import Path
from typing import Optional

from slopstopper import discovery, output
from slopstopper.checks import _http, _report
from slopstopper.checks._contract import refuse_unsafe_url

REPORT_DIR = Path(".ss/reports/seo")
REPORT_MD = REPORT_DIR / "seo-metatags-report.md"
USER_AGENT = "SlopStopper-SEO-Check/1.0"

# Consumed by `slopstopper emit reliability:seo --target pr-comment`.
# Discriminator `🔎 SEO` matches both the pre-flip JS heading
# ("## 🔎 SEO Metatag Audit") and the post-flip report H1
# ("# 🔎 SEO / Social-Share Metatag Report"), so the same bot comment
# is reused after the workflow flip. No issue keys: the check's exit
# code fails the workflow on missing tags, no main-branch issue is
# created.
META = {
    "report_path": str(REPORT_MD),
    "comment_discriminator": "🔎 SEO",
}


# ── safety ───────────────────────────────────────────────────────


_LABEL = "SEO check"


# ── HTML parser: only walks the <head>, captures meta/title/link ─


class HeadParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.in_head = False
        self.in_title = False
        self.title: Optional[str] = None
        self.metas: list[dict[str, str]] = []
        self.links: list[dict[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        if tag == "head":
            self.in_head = True
            return
        if not self.in_head:
            return
        a = {k: v or "" for k, v in attrs}
        if tag == "title":
            self.in_title = True
        elif tag == "meta":
            self.metas.append(a)
        elif tag == "link":
            self.links.append(a)

    def handle_endtag(self, tag: str) -> None:
        if tag == "head":
            self.in_head = False
        elif tag == "title":
            self.in_title = False

    def handle_data(self, data: str) -> None:
        if self.in_title and self.title is None:
            self.title = data.strip()


def _fetch(url: str) -> tuple[int, str, str]:
    return _http.fetch_text(url, USER_AGENT, label=_LABEL)


def _head_ok(url: str) -> tuple[bool, str]:
    """Return (ok, detail). Validates og:image is reachable and is an image."""
    try:
        with _http.open_url(url, USER_AGENT, method="HEAD", label=_LABEL) as resp:
            ct = resp.headers.get("Content-Type", "")
            if resp.status != 200:
                return False, f"HTTP {resp.status}"
            if not ct.startswith("image/"):
                return False, f"Content-Type is {ct!r}, expected image/*"
            return True, ct
    except (urllib.error.URLError, TimeoutError, ValueError) as e:
        # ValueError: a data:/ftp: og:image, or a redirect off http(s). A
        # finding about the page, not a reason the whole check can't run.
        return False, f"{type(e).__name__}: {e}"


# ── tag collection + validation ──────────────────────────────────


def _get_meta(metas: list[dict[str, str]], key_attr: str, key_value: str) -> Optional[str]:
    for m in metas:
        if m.get(key_attr, "").lower() == key_value.lower():
            return m.get("content", "")
    return None


def _get_link(links: list[dict[str, str]], rel: str) -> Optional[str]:
    for link in links:
        if link.get("rel", "").lower() == rel.lower():
            return link.get("href", "")
    return None


def _value_or_empty(value: Optional[str]) -> str:
    return value if value else ""


def _collect_tags(parser: HeadParser) -> dict[str, str]:
    return {
        "title": _value_or_empty(parser.title),
        "description": _value_or_empty(_get_meta(parser.metas, "name", "description")),
        "canonical": _value_or_empty(_get_link(parser.links, "canonical")),
        "viewport": _value_or_empty(_get_meta(parser.metas, "name", "viewport")),
        "og:type": _value_or_empty(_get_meta(parser.metas, "property", "og:type")),
        "og:title": _value_or_empty(_get_meta(parser.metas, "property", "og:title")),
        "og:description": _value_or_empty(_get_meta(parser.metas, "property", "og:description")),
        "og:url": _value_or_empty(_get_meta(parser.metas, "property", "og:url")),
        "og:image": _value_or_empty(_get_meta(parser.metas, "property", "og:image")),
        "twitter:card": _value_or_empty(_get_meta(parser.metas, "name", "twitter:card")),
        "twitter:title": _value_or_empty(_get_meta(parser.metas, "name", "twitter:title")),
        "twitter:description": _value_or_empty(_get_meta(parser.metas, "name", "twitter:description")),
        "twitter:image": _value_or_empty(_get_meta(parser.metas, "name", "twitter:image")),
    }


def _validate_core_tags(tags: dict[str, str], issues: list[str], notes: list[str]) -> None:
    title = tags["title"]
    description = tags["description"]
    if not title:
        issues.append("Missing <title>")
    elif len(title) > 70:
        notes.append(f"<title> is {len(title)} chars, longer than the suggested 60")
    if not description:
        issues.append("Missing <meta name=\"description\">")
    elif len(description) > 160:
        notes.append(f"meta description is {len(description)} chars, longer than the suggested 160")
    if not tags["viewport"]:
        issues.append("Missing <meta name=\"viewport\">")
    if not tags["canonical"]:
        issues.append("Missing <link rel=\"canonical\">")


def _validate_open_graph_tags(tags: dict[str, str], issues: list[str], require_og_image: bool) -> None:
    for key in ("og:title", "og:description", "og:type", "og:url"):
        if not tags[key]:
            issues.append(f"Missing {key}")
    if require_og_image and not tags["og:image"]:
        issues.append("Missing og:image")


def _validate_twitter_tags(
    tags: dict[str, str], issues: list[str], notes: list[str], require_og_image: bool
) -> None:
    twitter_card = tags["twitter:card"]
    if not twitter_card:
        issues.append("Missing twitter:card")
    elif twitter_card not in {"summary", "summary_large_image", "app", "player"}:
        notes.append(
            f"twitter:card is {twitter_card!r}, expected one of summary|summary_large_image|app|player"
        )
    for key in ("twitter:title", "twitter:description"):
        if not tags[key]:
            issues.append(f"Missing {key}")
    if require_og_image and not tags["twitter:image"]:
        issues.append("Missing twitter:image")


def _rewrite_origin(url: str, new_base: str) -> str:
    src = urllib.parse.urlparse(url)
    new = urllib.parse.urlparse(new_base)
    return urllib.parse.urlunparse(
        (new.scheme, new.netloc, src.path, src.params, src.query, src.fragment)
    )


def _maybe_rewrite_og_image(og_image_abs: str, og_image_base: Optional[str]) -> tuple[str, bool]:
    if not og_image_base:
        return og_image_abs, False
    src_origin = urllib.parse.urlparse(og_image_abs).netloc
    override_origin = urllib.parse.urlparse(og_image_base).netloc
    if not src_origin or not override_origin or src_origin == override_origin:
        return og_image_abs, False
    return _rewrite_origin(og_image_abs, og_image_base), True


def _verify_og_image(
    tags: dict[str, str],
    page_url: str,
    og_image_base: Optional[str],
    issues: list[str],
) -> dict:
    og_image_abs = urllib.parse.urljoin(page_url, tags["og:image"])
    head_url, rewritten = _maybe_rewrite_og_image(og_image_abs, og_image_base)
    ok, detail = _head_ok(head_url)
    if not ok:
        via = f" (origin-rewritten from {og_image_abs})" if rewritten else ""
        issues.append(f"og:image not reachable ({head_url}){via}: {detail}")
    return {
        "url": head_url,
        "original_url": og_image_abs if rewritten else None,
        "ok": ok,
        "detail": detail,
    }


def _check_page(
    base: str,
    path: str,
    require_og_image: bool,
    verify_og_image: bool,
    og_image_base: Optional[str] = None,
) -> dict:
    page_url = urllib.parse.urljoin(base.rstrip("/") + "/", path.lstrip("/"))
    issues: list[str] = []
    notes: list[str] = []

    try:
        status, ctype, body = _fetch(page_url)
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as e:
        return {
            "url": page_url,
            "status": "error",
            "issues": [f"Failed to fetch: {type(e).__name__}: {e}"],
            "notes": [],
        }

    if status != 200:
        issues.append(f"HTTP status {status}")
    if "text/html" not in ctype.lower():
        notes.append(f"Content-Type is {ctype!r}, expected text/html")

    parser = HeadParser()
    parser.feed(body)
    tags = _collect_tags(parser)
    _validate_core_tags(tags, issues, notes)
    _validate_open_graph_tags(tags, issues, require_og_image)
    _validate_twitter_tags(tags, issues, notes, require_og_image)

    image_check: Optional[dict] = None
    if tags["og:image"] and verify_og_image:
        image_check = _verify_og_image(tags, page_url, og_image_base, issues)

    return {
        "url": page_url,
        "status": "fail" if issues else "pass",
        "issues": issues,
        "notes": notes,
        "tags": tags,
        "image_check": image_check,
    }


# ── markdown report builders ─────────────────────────────────────


def _append_issues(lines: list[str], issues: list[str]) -> None:
    lines.extend(_report.render_issues(issues))


def _append_notes(lines: list[str], notes: list[str]) -> None:
    lines.extend(_report.render_notes(notes))


def _append_tags(lines: list[str], tags: dict[str, str]) -> None:
    lines.append("<details><summary>Tags detected</summary>")
    lines.append("")
    lines.append("| Tag | Value |")
    lines.append("|---|---|")
    for k, v in tags.items():
        display = (v[:120] + "…") if v and len(v) > 120 else v
        lines.append(f"| `{k}` | {display or '_(empty)_'} |")
    lines.append("")
    lines.append("</details>")
    lines.append("")


def _append_image_check(lines: list[str], image_check: dict) -> None:
    icon = "✅" if image_check["ok"] else "❌"
    lines.append(f"**og:image fetch:** {icon} `{image_check['url']}` ({image_check['detail']})")
    if image_check.get("original_url"):
        lines.append(
            f"  _(origin rewritten from `{image_check['original_url']}` via `SEO_OG_IMAGE_BASE`)_"
        )
    lines.append("")


def _append_page_markdown(lines: list[str], result: dict) -> None:
    status_icon = {"pass": "✅", "fail": "❌", "error": "💥"}.get(result["status"], "•")
    lines.append(f"## {status_icon} {result['url']}")
    lines.append("")
    if result["issues"]:
        _append_issues(lines, result["issues"])
    if result.get("notes"):
        _append_notes(lines, result["notes"])
    if result.get("tags"):
        _append_tags(lines, result["tags"])
    if result.get("image_check"):
        _append_image_check(lines, result["image_check"])


def _build_markdown_report(results: list[dict], base: str, overall_pass: bool) -> str:
    lines: list[str] = []
    lines.append("# 🔎 SEO / Social-Share Metatag Report")
    lines.append("")
    lines.append(f"**Base URL:** {base}")
    lines.append("")
    lines.append(f"**Overall:** {'✅ PASS' if overall_pass else '❌ FAIL'}")
    lines.append("")
    for result in results:
        _append_page_markdown(lines, result)
    lines.append("---")
    lines.append("")
    lines.append("## How to Fix")
    lines.append("")
    lines.append(
        "- **Missing tag** → add it to the page's `<head>`. See [docs/reliability/SEO.md](../../../docs/reliability/SEO.md) for the full required set."
    )
    lines.append(
        "- **og:image not reachable** → confirm the URL is absolute, same-origin (or CORS-allowed for crawlers), and returns `image/*` content type."
    )
    lines.append("- **Title / description too long** → trim to keep search-result snippets readable.")
    lines.append("")
    return "\n".join(lines) + "\n"


def _write_reports(results: list[dict], base: str) -> None:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    overall_pass = all(r["status"] == "pass" for r in results)
    json_path = REPORT_DIR / "seo-metatags-report.json"
    json_path.write_text(
        json.dumps(
            {"base_url": base, "overall": "pass" if overall_pass else "fail", "pages": results},
            indent=2,
        )
        + "\n"
    )
    md_path = REPORT_DIR / "seo-metatags-report.md"
    md_path.write_text(_build_markdown_report(results, base, overall_pass))


def _print_results(results: list[dict]) -> None:
    for r in results:
        icon = {"pass": "✅", "fail": "❌", "error": "💥"}.get(r["status"], "•")
        suffix = "" if r["status"] == "pass" else f": {len(r['issues'])} issue(s)"
        output._emit(f"  {icon} {r['url']}{suffix}")
        for issue in r["issues"]:
            output._emit(f"      - {issue}")


# ── CLI entrypoint ───────────────────────────────────────────────


def _parse_args(args: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="slopstopper run reliability:seo", add_help=False)
    p.add_argument(
        "url_positional",
        nargs="?",
        default=None,
        help="Site URL to audit (alternative to --url; e.g. http://localhost:8080)",
    )
    p.add_argument("--url", default=None, help="Site URL to audit")
    p.add_argument("--pages", default=None, help="Comma-separated paths (default: /)")
    p.add_argument(
        "--no-require-og-image",
        action="store_true",
        help="Skip og:image presence check",
    )
    p.add_argument(
        "--no-verify-og-image",
        action="store_true",
        help="Skip HEAD-fetching og:image",
    )
    p.add_argument(
        "--og-image-base",
        default=None,
        help="Rewrite og:image origin to this base before HEAD",
    )
    p.add_argument("--help", "-h", action="help")
    return p.parse_args(args or [])


def _resolve_url(parsed_url: str | None) -> str | None:
    return parsed_url or os.environ.get("SEO_TEST_URL")


def _discover_pages() -> str | None:
    return discovery.pages_csv("seo")


def _resolve_pages(parsed_pages: str | None) -> list[str]:
    """Pick the pages list using flag → env → discovery → default order."""
    raw = parsed_pages or os.environ.get("SEO_PAGES")
    if raw is None:
        raw = _discover_pages()
    if not raw:
        return ["/"]
    pages = [p.strip() for p in raw.split(",") if p.strip()]
    return pages or ["/"]


def run(args: list[str] | None = None) -> int:
    parsed = _parse_args(args)
    url = _resolve_url(parsed.url_positional or parsed.url)
    if not url:
        output.error("SEO target URL is required")
        output._emit("Usage:")
        output._emit("  slopstopper run reliability:seo -- --url https://your-site.example.com")
        output._emit("  SEO_TEST_URL=https://your-site slopstopper run reliability:seo")
        return 2

    if (rc := refuse_unsafe_url(url, _LABEL)) is not None:
        return rc

    require_og_image = not parsed.no_require_og_image
    verify_og_image = not parsed.no_verify_og_image
    og_image_base = parsed.og_image_base or os.environ.get("SEO_OG_IMAGE_BASE", "").strip() or None
    pages = _resolve_pages(parsed.pages)

    output.status("🔎", f"SEO metatag audit against: {url}")
    output._emit(f"   Pages: {', '.join(pages)}")
    if og_image_base:
        output._emit(f"   og:image origin override → {og_image_base}")
    output.separator()

    try:
        results = [
            _check_page(url, p, require_og_image, verify_og_image, og_image_base) for p in pages
        ]
    except ValueError as e:
        # A bad input the up-front URL guard couldn't see (a configured path
        # that composes to an unusable URL): the check could not run.
        output.error(str(e))
        return 2

    _write_reports(results, url)
    _print_results(results)

    overall_pass = all(r["status"] == "pass" for r in results)
    output.separator()
    if overall_pass:
        output.success("All pages pass.")
        return 0
    output.error("Failures detected. See .ss/reports/seo/seo-metatags-report.md for full details.")
    return 1
