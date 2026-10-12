"""API response-header + CORS policy audit (in-CLI implementation).

The API-shaped analogue of `hygiene:csp-exceptions`. That check guards
the headers a browser reads on an HTML surface, with CSP at the centre.
A JSON API has a different header contract: CSP is largely irrelevant
(there is no document to restrict), while CORS becomes the control that
decides who can read the response. A permissive CORS policy on a
credentialed API is a cross-origin data leak, not a style violation.

What it probes, per configured path:

  CORS
    - `Access-Control-Allow-Origin: *` together with
      `Access-Control-Allow-Credentials: true`. Browsers reject this
      pair, so it's usually a misconfigured allowlist rather than a
      working policy. It also means the intended policy was never
      actually enforced. Always a hard failure.
    - A wildcard origin on its own. Fine for a genuinely public
      read-only API, so it fails only unless `allow_wildcard_cors`.
    - An arbitrary Origin reflected back. The check sends a probe origin
      that nothing should trust; if it comes back in
      `Access-Control-Allow-Origin` alongside credentials, any site can
      read authenticated responses. Hard failure with credentials,
      advisory without.
    - `Vary: Origin` missing when the origin is echoed, so a shared cache
      can serve one origin's allowance to another. Advisory.
    - When `allowed_origins` is configured, each is sent as an `Origin`
      and must be allowed. This catches an allowlist that silently
      stopped matching.

  Transport + content
    - `Strict-Transport-Security` on https origins (`require_hsts`).
      Skipped for http/localhost, where HSTS is meaningless.
    - `X-Content-Type-Options: nosniff` (`require_nosniff`).
    - Implementation-leaking headers (`X-Powered-By`,
      `X-AspNet-Version`, a versioned `Server`). Advisory: they hand an
      attacker a version to look up, but they are not the vulnerability.

Skips gracefully (exit 0) when no paths are configured, the same
contract as `hygiene:csp-exceptions` with `headers.source: null`.

CLI surface:
  slopstopper run security:api-headers -- --url URL [--path /health]
        [--allow-wildcard-cors] [--no-require-hsts] [--no-require-nosniff]
        [--allowed-origin https://app.example.com]

Stdlib-only (urllib). Writes .ss/reports/api-headers/api-headers-report.{md,json}.

Configuration (.slopstopper.yml, all optional):

    api:
      base_path: ''              # prefix the API is served under
      headers:
        paths: []                # endpoints to probe; empty → check skips
        require_hsts: true       # demand HSTS on https origins
        require_nosniff: true    # demand X-Content-Type-Options: nosniff
        allow_wildcard_cors: false   # permit `Access-Control-Allow-Origin: *`
        allowed_origins: []      # origins that must be allowed when sent

Env-var equivalents the CLI also honours (precedence: flag > env > config):
  API_HEADERS_TEST_URL   base URL to audit (required)
  API_HEADERS_PATHS      comma-separated paths to probe

See .slopstopper.yml.example for the canonical schema.

Exit codes:
  0: every probed path passes, or no paths configured (graceful skip)
  1: failures detected
  2: the URL is missing, or its scheme is not http/https
"""

from __future__ import annotations

import argparse
import os
import urllib.error
import urllib.parse
from pathlib import Path

from slopstopper import config, output
from slopstopper.checks import _http, _report
from slopstopper.checks._contract import refuse_unsafe_url

REPORT_DIR = Path(".ss/reports/api-headers")
REPORT_MD = REPORT_DIR / "api-headers-report.md"
REPORT_JSON = REPORT_DIR / "api-headers-report.json"
USER_AGENT = "SlopStopper-ApiHeaders-Check/1.0"

# An origin no API should ever trust. Sent to see whether the server
# reflects whatever Origin it is handed. `.invalid` is reserved by
# RFC 2606 precisely so it can never resolve to a real host.
PROBE_ORIGIN = "https://slopstopper-cors-probe.invalid"

# Headers that name the implementation and its version. Advisory, because
# they are a fingerprinting aid, not a vulnerability in themselves.
FINGERPRINT_HEADERS = ("X-Powered-By", "X-AspNet-Version", "X-AspNetMvc-Version")

# Consumed by `slopstopper emit security:api-headers --target pr-comment`.
META = {
    "report_path": str(REPORT_MD),
    "comment_discriminator": "🛡 API Headers",
}


# ── safety ───────────────────────────────────────────────────────


_LABEL = "API headers check"


def _fetch_headers(url: str, origin: str | None) -> tuple[int, dict]:
    """GET the URL with an optional Origin. Returns (status, headers).

    Response headers matter whatever the status code, so a 4xx/5xx is
    unwrapped rather than raised: an API that 401s still has a CORS
    policy worth auditing.
    """
    extra = {"Origin": origin} if origin else None
    try:
        with _http.open_url(url, USER_AGENT, headers=extra, label=_LABEL) as resp:
            return resp.status, dict(resp.headers.items())
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers.items()) if e.headers else {}


def _header(headers: dict, name: str) -> str:
    """Case-insensitive header lookup (HTTP header names aren't case-sensitive)."""
    lowered = name.lower()
    for key, value in headers.items():
        if key.lower() == lowered:
            return value
    return ""


# ── CORS assertions ──────────────────────────────────────────────


def _check_wildcard_cors(headers: dict, allow_wildcard: bool, issues: list[str], notes: list[str]) -> None:
    acao = _header(headers, "Access-Control-Allow-Origin").strip()
    credentials = _header(headers, "Access-Control-Allow-Credentials").strip().lower() == "true"
    if acao != "*":
        return
    if credentials:
        issues.append(
            "`Access-Control-Allow-Origin: *` is sent with "
            "`Access-Control-Allow-Credentials: true`. Browsers reject this pair, so the "
            "policy you intended is not being enforced. Echo a specific allowed origin instead"
        )
    elif not allow_wildcard:
        issues.append(
            "`Access-Control-Allow-Origin: *` lets any site read this response. "
            "Set api.headers.allow_wildcard_cors: true if the endpoint is deliberately public"
        )
    else:
        notes.append("wildcard CORS allowed by api.headers.allow_wildcard_cors")


def _check_reflected_origin(url: str, issues: list[str], notes: list[str]) -> dict:
    """Send an untrusted Origin and see whether it comes back allowed."""
    try:
        _, headers = _fetch_headers(url, PROBE_ORIGIN)
    except (urllib.error.URLError, TimeoutError):
        notes.append("origin-reflection probe could not be completed")
        return {}

    acao = _header(headers, "Access-Control-Allow-Origin").strip()
    credentials = _header(headers, "Access-Control-Allow-Credentials").strip().lower() == "true"
    if acao != PROBE_ORIGIN:
        return {"reflected": False}

    if credentials:
        issues.append(
            f"the server reflects any Origin it is sent (probed with `{PROBE_ORIGIN}`) and "
            "allows credentials, so any site can read authenticated responses from this endpoint. "
            "Validate Origin against an allowlist"
        )
    else:
        notes.append(
            f"the server reflects any Origin it is sent (probed with `{PROBE_ORIGIN}`). "
            "Without credentials this leaks less, but an allowlist is still the correct shape"
        )

    if not _header(headers, "Vary"):
        notes.append(
            "`Vary: Origin` is missing while the origin is echoed, so a shared cache can serve "
            "one origin's allowance to another"
        )
    return {"reflected": True, "credentials": credentials}


def _check_allowed_origins(
    url: str, allowed_origins: list[str], issues: list[str]
) -> list[dict]:
    """Each configured origin must actually be allowed when sent."""
    results = []
    for origin in allowed_origins:
        try:
            _, headers = _fetch_headers(url, origin)
        except (urllib.error.URLError, TimeoutError) as e:
            issues.append(f"could not probe allowed origin `{origin}`: {type(e).__name__}")
            continue
        acao = _header(headers, "Access-Control-Allow-Origin").strip()
        ok = acao in (origin, "*")
        if not ok:
            issues.append(
                f"origin `{origin}` is listed in api.headers.allowed_origins but the endpoint "
                f"answered `Access-Control-Allow-Origin: {acao or '(none)'}`"
            )
        results.append({"origin": origin, "allowed": ok, "acao": acao})
    return results


# ── transport + content assertions ───────────────────────────────


def _check_transport_headers(url: str, headers: dict, opts: dict, issues: list[str], notes: list[str]) -> None:
    is_https = urllib.parse.urlparse(url).scheme.lower() == "https"

    if opts["require_hsts"]:
        if not is_https:
            notes.append("HSTS not asserted because the target is http, where the header has no effect")
        elif not _header(headers, "Strict-Transport-Security"):
            issues.append(
                "`Strict-Transport-Security` is missing. Add it (e.g. "
                "`max-age=31536000; includeSubDomains`) or set api.headers.require_hsts: false"
            )

    if opts["require_nosniff"] and _header(headers, "X-Content-Type-Options").strip().lower() != "nosniff":
        issues.append(
            "`X-Content-Type-Options: nosniff` is missing. A JSON response that a browser "
            "sniffs as HTML is an XSS vector. Or set api.headers.require_nosniff: false"
        )

    for name in FINGERPRINT_HEADERS:
        value = _header(headers, name)
        if value:
            notes.append(f"`{name}: {value}` names your stack and version, so consider removing it")

    server = _header(headers, "Server")
    if server and any(ch.isdigit() for ch in server):
        notes.append(f"`Server: {server}` includes a version, so consider trimming it to the product name")


# ── audit ────────────────────────────────────────────────────────


def _join(url: str, base_path: str, path: str) -> str:
    return _http.join_url(url, base_path, path)


def _audit_path(url: str, base_path: str, path: str, opts: dict) -> dict:
    target = _join(url, base_path, path)
    issues: list[str] = []
    notes: list[str] = []

    try:
        status, headers = _fetch_headers(target, None)
    except (urllib.error.URLError, TimeoutError) as e:
        return {
            "url": target,
            "status": "fail",
            "http_status": None,
            "issues": [f"endpoint not reachable: {type(e).__name__}: {e}"],
            "notes": [],
            "origin_checks": [],
        }

    _check_wildcard_cors(headers, opts["allow_wildcard_cors"], issues, notes)
    _check_reflected_origin(target, issues, notes)
    origin_checks = _check_allowed_origins(target, opts["allowed_origins"], issues)
    _check_transport_headers(target, headers, opts, issues, notes)

    return {
        "url": target,
        "status": "fail" if issues else "pass",
        "http_status": status,
        "issues": issues,
        "notes": notes,
        "origin_checks": origin_checks,
    }


def _audit(url: str, opts: dict) -> dict:
    pages = [_audit_path(url, opts["base_path"], path, opts) for path in opts["paths"]]
    failed = [p for p in pages if p["status"] == "fail"]
    return {
        "url": url,
        "status": "fail" if failed else "pass",
        "probe_origin": PROBE_ORIGIN,
        "paths": pages,
    }


# ── markdown report ──────────────────────────────────────────────


def _render_skip() -> list[str]:
    return _report.render_skip(
        'no `api.headers.paths` configured.',
        ['List the endpoints to probe in `.slopstopper.yml`:', '', '```yaml', 'api:', '  headers:', '    paths: [/health, /v1/items]', '```'],
    )


def _render_origin_table(origin_checks: list[dict]) -> list[str]:
    if not origin_checks:
        return []
    lines = [
        "| Configured origin | Allowed | `Access-Control-Allow-Origin` |",
        "|---|---|---|",
    ]
    for oc in origin_checks:
        mark = "✅" if oc["allowed"] else "❌"
        lines.append(f"| `{oc['origin']}` | {mark} | `{oc['acao'] or '(none)'}` |")
    lines.append("")
    return lines


def _render_path(page: dict) -> list[str]:
    icon = "✅" if page["status"] == "pass" else "❌"
    lines = [
        f"## {icon} `{page['url']}`",
        "",
        f"HTTP {page['http_status'] if page['http_status'] else 'unreachable'}",
        "",
    ]
    lines.extend(_report.render_issues(page["issues"]))
    lines.extend(_report.render_notes(page["notes"]))
    lines.extend(_render_origin_table(page["origin_checks"]))
    if not page["issues"] and not page["notes"]:
        lines.extend(["No issues.", ""])
    return lines


def _build_markdown_report(result: dict) -> str:
    lines: list[str] = ["# 🛡 API Headers Report", ""]
    if result["status"] == "skipped":
        lines.extend(_render_skip())
        return "\n".join(lines) + "\n"

    lines.append(f"**Base URL:** {result['url']}")
    lines.append("")
    lines.append(f"**Overall:** {'✅ PASS' if result['status'] == 'pass' else '❌ FAIL'}")
    lines.append("")
    lines.append(f"**Origin-reflection probe:** `{result['probe_origin']}`")
    lines.append("")

    for page in result["paths"]:
        lines.extend(_render_path(page))

    lines.append("---")
    lines.append("")
    lines.append("## How to Fix")
    lines.append("")
    lines.append(
        "- **Wildcard + credentials** → browsers reject that pair, so your intended policy "
        "isn't being applied. Validate `Origin` against an allowlist and echo the matched value."
    )
    lines.append(
        "- **Reflected origin** → don't echo `Origin` back unchecked. Compare it to an "
        "allowlist first, and send `Vary: Origin` so caches don't cross the wires."
    )
    lines.append(
        "- **Missing HSTS / nosniff** → add them at the edge (the same place the HTML surface "
        "gets its headers). Both knobs can be turned off per-repo if they're handled elsewhere."
    )
    lines.append(
        "- See [docs/security/README.md](../../../docs/security/README.md) for the full "
        "header contract, and `hygiene:csp-exceptions` for the browser-surface equivalent."
    )
    lines.append("")
    return "\n".join(lines) + "\n"


def _write_reports(result: dict) -> None:
    _report.write_reports(REPORT_JSON, REPORT_MD, result, _build_markdown_report)


def _print_result(result: dict) -> None:
    for page in result["paths"]:
        icon = "✅" if page["status"] == "pass" else "❌"
        output._emit(f"  {icon} {page['url']}")
        for issue in page["issues"]:
            output._emit(f"      - {issue}")
        for note in page["notes"]:
            output._emit(f"      ⚠️  {note}")


# ── CLI entrypoint ───────────────────────────────────────────────


def _parse_args(args: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="slopstopper run security:api-headers", add_help=False)
    p.add_argument("url_positional", nargs="?", default=None, help="Base URL to audit")
    p.add_argument("--url", default=None, help="Base URL to audit")
    p.add_argument(
        "--path",
        action="append",
        default=[],
        dest="path",
        help="Endpoint path to probe (repeatable)",
    )
    p.add_argument(
        "--allowed-origin",
        action="append",
        default=[],
        dest="allowed_origin",
        help="Origin that must be allowed when sent (repeatable)",
    )
    p.add_argument(
        "--allow-wildcard-cors",
        action="store_true",
        help="Permit `Access-Control-Allow-Origin: *`",
    )
    p.add_argument("--no-require-hsts", action="store_true", help="Don't demand HSTS")
    p.add_argument("--no-require-nosniff", action="store_true", help="Don't demand nosniff")
    p.add_argument("--help", "-h", action="help")
    return p.parse_args(args or [])


def _env_paths() -> list[str]:
    raw = os.environ.get("API_HEADERS_PATHS", "")
    return [part.strip() for part in raw.split(",") if part.strip()]


def _resolve_options(parsed: argparse.Namespace) -> dict:
    return {
        "base_path": config.get("api.base_path", "") or "",
        "paths": list(parsed.path or _env_paths() or config.get("api.headers.paths", []) or []),
        "require_hsts": not parsed.no_require_hsts
        and config.get_bool("api.headers.require_hsts", True),
        "require_nosniff": not parsed.no_require_nosniff
        and config.get_bool("api.headers.require_nosniff", True),
        "allow_wildcard_cors": parsed.allow_wildcard_cors
        or config.get_bool("api.headers.allow_wildcard_cors", False),
        "allowed_origins": list(
            parsed.allowed_origin or config.get("api.headers.allowed_origins", []) or []
        ),
    }


def _skip(reason: str) -> int:
    """Skip gracefully, because an unconfigured check is not a failing check."""
    output.info(reason)
    _write_reports({"url": None, "status": "skipped", "paths": [], "probe_origin": PROBE_ORIGIN})
    output.success("Nothing to audit, so skipping (exit 0).")
    return 0


def run(args: list[str] | None = None) -> int:
    parsed = _parse_args(args)
    opts = _resolve_options(parsed)

    if not opts["paths"]:
        return _skip(
            "No api.headers.paths configured in .slopstopper.yml, so there is nothing to probe. "
            "List your API endpoints (e.g. [/health, /v1/items]) to enable this check."
        )

    url = parsed.url_positional or parsed.url or os.environ.get("API_HEADERS_TEST_URL")
    if not url:
        output.error("API headers target URL is required")
        output._emit("Usage:")
        output._emit("  slopstopper run security:api-headers -- --url https://api.example.com")
        output._emit("  API_HEADERS_TEST_URL=https://api.example.com slopstopper run security:api-headers")
        return 2

    if (rc := refuse_unsafe_url(url, _LABEL)) is not None:
        return rc

    output.status("🛡", f"API header + CORS audit against: {url}")
    output._emit(f"   Paths: {', '.join(opts['paths'])}")
    output.separator()

    try:
        result = _audit(url, opts)
    except ValueError as e:
        # A bad input the up-front URL guard couldn't see (a configured path
        # that composes to an unusable URL): the check could not run.
        output.error(str(e))
        return 2

    _write_reports(result)
    _print_result(result)

    output.separator()
    if result["status"] == "pass":
        output.success("Every probed endpoint passes the API header contract.")
        return 0
    output.error(
        "Failures detected. See .ss/reports/api-headers/api-headers-report.md for full details."
    )
    return 1
