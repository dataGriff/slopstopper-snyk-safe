"""Page-discovery orchestrator for the reliability gates.

Lifted from .ss/scripts/discover-pages.py during Phase 2 of the CLI
pivot. The bash-side script remains in place for adopters who haven't
upgraded yet; the reliability check ports under cli/slopstopper/checks/
now import this module directly instead of subprocess-invoking it.

Resolves "which paths should I audit?" for a given check (accessibility,
SEO, broken-links, smoke, e2e) and CI event (pr, main, cron, local).

Resolution order (first hit wins):
    1. Env-var override: if <CHECK>_PAGES is set, pass it through.
    2. reliability.coverage.<event> in .slopstopper.yml:
         - "sitemap" → parse dist/client/sitemap-index.xml (or sibling
           fallbacks), return every <loc> URL's path.
         - "changed" (PR only) → git diff origin/main...HEAD, map each
           changed source file to its output URL via framework-specific
           rules (Astro / Next / SvelteKit auto-detected).
         - "<path,path>" → comma-separated explicit list.
    3. pages.<check> in .slopstopper.yml: the existing hand-list behaviour.
    4. Default → "/".

Stdlib only, to keep the no-deps invariant.
"""

from __future__ import annotations

import fnmatch
import os
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Optional, Sequence
from urllib.parse import urlparse

from slopstopper import config


CHECKS = ("smoke", "e2e", "accessibility", "seo", "broken_links")
EVENTS = ("pr", "main", "cron", "local")

ENV_VAR_BY_CHECK = {
    "smoke": "SMOKE_PAGES",
    "e2e": "E2E_PAGES",
    "accessibility": "ACCESSIBILITY_PAGES",
    "seo": "SEO_PAGES",
    "broken_links": "BROKEN_LINKS_PAGES",
}

SITEMAP_CANDIDATES = (
    "dist/client/sitemap-index.xml",
    "dist/sitemap-index.xml",
    "dist/sitemap.xml",
    "dist/sitemap-0.xml",
    "build/sitemap.xml",
    "out/sitemap.xml",
    "public/sitemap.xml",
)

SITEMAP_NS = "{http://www.sitemaps.org/schemas/sitemap/0.9}"


# ── Per-framework source-to-URL rules ─────────────────────────────────

ASTRO_RULES: tuple[tuple[str, str], ...] = (
    (r"^src/content/blog/(?P<slug>[^/]+)\.md$", "/blog/$slug/"),
    (r"^src/content/blog/(?P<slug>[^/]+)\.mdx$", "/blog/$slug/"),
    (r"^src/content/(?P<col>[^/]+)/(?P<slug>[^/]+)\.md$", "/$col/$slug/"),
    (r"^src/content/(?P<col>[^/]+)/(?P<slug>[^/]+)\.mdx$", "/$col/$slug/"),
    (r"^src/pages/index\.astro$", "/"),
    (r"^src/pages/(?P<page>[^/]+)/index\.astro$", "/$page/"),
    (r"^src/pages/(?P<page>[^/]+)\.astro$", "/$page/"),
)

NEXT_PAGES_RULES: tuple[tuple[str, str], ...] = (
    (r"^pages/index\.tsx?$", "/"),
    (r"^pages/index\.jsx?$", "/"),
    (r"^pages/(?P<page>[^/]+)/index\.tsx?$", "/$page/"),
    (r"^pages/(?P<page>[^/]+)/index\.jsx?$", "/$page/"),
    (r"^pages/(?P<page>[^/]+)\.tsx?$", "/$page/"),
    (r"^pages/(?P<page>[^/]+)\.jsx?$", "/$page/"),
)

NEXT_APP_RULES: tuple[tuple[str, str], ...] = (
    (r"^app/page\.tsx?$", "/"),
    (r"^app/page\.jsx?$", "/"),
    (r"^app/(?P<route>[^/]+)/page\.tsx?$", "/$route/"),
    (r"^app/(?P<route>[^/]+)/page\.jsx?$", "/$route/"),
)

SVELTEKIT_RULES: tuple[tuple[str, str], ...] = (
    (r"^src/routes/\+page\.svelte$", "/"),
    (r"^src/routes/(?P<route>[^/]+)/\+page\.svelte$", "/$route/"),
)


def _detect_framework_rules() -> tuple[tuple[str, str], ...]:
    if any(Path(f).exists() for f in ("astro.config.mjs", "astro.config.js", "astro.config.ts")):
        return ASTRO_RULES
    if any(Path(f).exists() for f in ("next.config.mjs", "next.config.js", "next.config.ts")):
        return NEXT_PAGES_RULES + NEXT_APP_RULES
    if any(Path(f).exists() for f in ("svelte.config.js", "svelte.config.mjs")):
        return SVELTEKIT_RULES
    return ASTRO_RULES


def log(msg: str) -> None:
    """Log to stderr so nothing pollutes the stdout payload the caller consumes."""
    print(msg, file=sys.stderr)


# ── Sitemap parsing ─────────────────────────────────────────────────


def _find_sitemap_path() -> Optional[Path]:
    for candidate in SITEMAP_CANDIDATES:
        path = Path(candidate)
        if path.exists():
            return path
    return None


def _resolve_child_sitemap(parent: Path, loc: str) -> Optional[Path]:
    candidate = parent.parent / Path(urlparse(loc).path).name
    if candidate.exists():
        return candidate
    return None


def _extract_paths_from_sitemap_xml(path: Path, seen: set[Path]) -> list[str]:
    """Read a sitemap XML file; recurse into sitemap-index files.

    The sitemap is local trusted build output produced by the adopter's
    own build process, so it is never adversary-controlled. Stdlib ElementTree's
    default parser since Python 3.7.1 does not resolve external entities,
    so XXE is moot. Defusedxml is avoided to keep the no-deps invariant.
    """
    if path in seen:
        return []
    seen.add(path)
    try:
        tree = ET.parse(path)  # nosec B314
    except ET.ParseError as e:
        log(f"⚠️  Failed to parse sitemap {path}: {e}")
        return []
    root = tree.getroot()
    if root.tag == f"{SITEMAP_NS}sitemapindex":
        return _collect_from_index(root, path, seen)
    if root.tag == f"{SITEMAP_NS}urlset":
        return _collect_from_urlset(root)
    return []


def _collect_from_index(root: ET.Element, path: Path, seen: set[Path]) -> list[str]:
    paths: list[str] = []
    for sitemap_el in root.findall(f"{SITEMAP_NS}sitemap"):
        loc = sitemap_el.find(f"{SITEMAP_NS}loc")
        if loc is None or not loc.text:
            continue
        child = _resolve_child_sitemap(path, loc.text.strip())
        if child is not None:
            paths.extend(_extract_paths_from_sitemap_xml(child, seen))
    return paths


def _collect_from_urlset(root: ET.Element) -> list[str]:
    paths: list[str] = []
    for url_el in root.findall(f"{SITEMAP_NS}url"):
        loc = url_el.find(f"{SITEMAP_NS}loc")
        if loc is None or not loc.text:
            continue
        parsed = urlparse(loc.text.strip())
        paths.append(parsed.path or "/")
    return paths


def _dedupe(paths: list[str]) -> list[str]:
    seen: set[str] = set()
    return [p for p in paths if not (p in seen or seen.add(p))]


def _from_sitemap() -> list[str]:
    path = _find_sitemap_path()
    if path is None:
        log(
            "❌ sitemap mode: no sitemap found at any of: "
            + ", ".join(SITEMAP_CANDIDATES)
        )
        log("   Run `npm run build` (or your build command) first.")
        return []
    log(f"📄 Sitemap discovered: {path}")
    unique = _dedupe(_extract_paths_from_sitemap_xml(path, set()))
    log(f"   → {len(unique)} unique paths")
    return unique


# ── Changed-pages mode ───────────────────────────────────────────────


def _git_diff_files(base_ref: str) -> Optional[list[str]]:
    try:
        result = subprocess.run(
            ["git", "diff", "--name-only", f"{base_ref}...HEAD"],
            capture_output=True,
            text=True,
            check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        log(f"⚠️  git diff failed: {e}")
        log(
            "   Hint: set `fetch-depth: 0` on actions/checkout for this workflow."
        )
        return None
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def _file_matches_any_glob(file_path: str, patterns: Sequence[str]) -> bool:
    return any(fnmatch.fnmatchcase(file_path, p) for p in patterns)


def _expand_url_template(template: str, match: re.Match) -> str:
    def replace(m: re.Match) -> str:
        name = m.group(1) or m.group(2)
        return match.groupdict().get(name, "") or ""

    return re.sub(r"\$\{([a-zA-Z_][a-zA-Z0-9_]*)\}|\$([a-zA-Z_][a-zA-Z0-9_]*)", replace, template)


def _map_file_to_urls(file_path: str, rules: Sequence[tuple[str, str]]) -> list[str]:
    urls: list[str] = []
    for pattern, template in rules:
        try:
            match = re.match(pattern, file_path)
        except re.error as e:
            log(f"⚠️  bad framework regex {pattern!r}: {e}")
            continue
        if match:
            urls.append(_expand_url_template(template, match))
    return urls


def _resolve_base_ref() -> str:
    base_ref = os.environ.get("GITHUB_BASE_REF") or "origin/main"
    if not base_ref.startswith("origin/") and "/" not in base_ref:
        base_ref = f"origin/{base_ref}"
    return base_ref


def _any_cross_cutting_change(files: list[str]) -> Optional[str]:
    cross_cutting = list(config.get("reliability.coverage.cross_cutting_paths", []) or [])
    for file_path in files:
        if _file_matches_any_glob(file_path, cross_cutting):
            return file_path
    return None


def _map_changed_files(files: list[str]) -> tuple[list[str], list[str]]:
    rules = _detect_framework_rules()
    urls: list[str] = []
    unmapped: list[str] = []
    for file_path in files:
        mapped = _map_file_to_urls(file_path, rules)
        if mapped:
            urls.extend(mapped)
        else:
            unmapped.append(file_path)
    return urls, unmapped


def _from_changed_pages() -> list[str]:
    files = _git_diff_files(_resolve_base_ref())
    if files is None:
        log("   → falling back to sitemap mode")
        return _from_sitemap()

    trigger = _any_cross_cutting_change(files)
    if trigger:
        log(f"🎯 Cross-cutting change detected: {trigger} matches cross_cutting_paths")
        log("   → escalating to sitemap sweep")
        return _from_sitemap()

    urls, unmapped = _map_changed_files(files)
    if unmapped:
        log(f"ℹ️  {len(unmapped)} changed file(s) had no source_to_url match (permissive)")

    unique = _dedupe(urls)
    if not unique:
        log("   → changed-pages produced empty set, falling through to sitemap")
        return _from_sitemap()
    log(f"   → {len(unique)} changed page(s) selected from {len(files)} file(s)")
    return unique


# ── Resolution orchestrator ───────────────────────────────────────────


def _from_env(check: str) -> Optional[list[str]]:
    env_name = ENV_VAR_BY_CHECK[check]
    raw = os.environ.get(env_name)
    if raw is None:
        return None
    paths = [p.strip() for p in raw.split(",") if p.strip()]
    if paths:
        log(f"🔁 {env_name} preset in environment ({len(paths)} path(s))")
        return paths
    return None


def _coverage_mode_str(event: str) -> Optional[str]:
    raw = config.get(f"reliability.coverage.{event}")
    if isinstance(raw, str):
        return raw.strip() or None
    if isinstance(raw, list):
        items = [str(p).strip() for p in raw if str(p).strip()]
        if items:
            return ",".join(items)
    return None


def _from_coverage_mode(event: str) -> Optional[list[str]]:
    mode = _coverage_mode_str(event)
    if mode is None:
        return None
    if mode == "sitemap":
        return _from_sitemap()
    if mode == "changed":
        if event != "pr":
            log(f"⚠️  changed mode only valid for --event=pr (got {event}); skipping")
            return None
        return _from_changed_pages()
    paths = [p.strip() for p in mode.split(",") if p.strip()]
    if paths:
        log(f"📋 explicit list from reliability.coverage.{event} ({len(paths)} path(s))")
        return paths
    return None


def _from_pages_list(check: str) -> Optional[list[str]]:
    raw = config.get(f"pages.{check}")
    if raw is None:
        return None
    if isinstance(raw, list):
        paths = [str(p).strip() for p in raw if str(p).strip()]
    else:
        paths = [p.strip() for p in str(raw).split(",") if p.strip()]
    if paths:
        log(f"📜 pages.{check} ({len(paths)} path(s))")
        return paths
    return None


def pages_csv(check: str, event: str = "local") -> Optional[str]:
    """`discover(...)` comma-joined for a spec's `<CHECK>_PAGES` env var.

    None when discovery raises or finds nothing, so the spec falls back
    to its built-in default rather than the check dying on a config error.
    """
    try:
        paths = discover(check, event)
    except Exception:
        return None
    return ",".join(paths) if paths else None


def discover(check: str, event: str = "local") -> list[str]:
    env_paths = _from_env(check)
    if env_paths:
        return env_paths
    if event in {"pr", "main", "cron"}:
        coverage_paths = _from_coverage_mode(event)
        if coverage_paths:
            return coverage_paths
    pages_paths = _from_pages_list(check)
    if pages_paths:
        return pages_paths
    log("ℹ️  no discovery hit; defaulting to /")
    return ["/"]
