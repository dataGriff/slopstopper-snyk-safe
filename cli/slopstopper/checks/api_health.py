"""API health/readiness endpoint audit (in-CLI implementation).

The API-shaped analogue of `reliability:smoke`. Smoke drives a browser
over HTML pages; this probes the one endpoint an API is expected to
expose for exactly this purpose and asserts the contract around it:
reachable, the documented status code, a JSON content-type, a body that
parses, and whatever fields the repo says that body must carry.

Why a dedicated check rather than pointing smoke at `/health`: smoke
asserts DOM-shaped things (a `<title>`, a stylesheet link, a shareable
og-image) that a JSON endpoint will never satisfy, and a health
endpoint has assertions of its own. `{"status": "degraded"}` returned
with HTTP 200 is a pass to a reachability probe and a failure to
anything that reads the body.

Skips gracefully (exit 0) when no endpoint is configured, the same
contract as `hygiene:csp-exceptions` with `headers.source: null`, so an
adopter's first PR is green before they've configured anything.

CLI surface:
  slopstopper run reliability:api-health -- --url URL [--path /health]
        [--expect-status 200] [--allow-non-json] [--require-field status]
        [--max-response-ms 1000]

Stdlib-only (urllib). Writes .ss/reports/api-health/api-health-report.{md,json}.

Configuration (.slopstopper.yml, all optional):

    api:
      base_path: ''             # prefix the API is served under, e.g. /api/v1
      health:
        path:                   # unset (default) → check skips gracefully
        expect_status: 200      # status code the endpoint must return
        require_json: true      # assert a JSON content-type and a parseable body
        require_fields: []      # dot-paths that must be present in the body
        expect_fields:          # dot-path → exact expected value
          # status: ok
        max_response_ms:        # unset → timing reported, never enforced

Env-var equivalents the CLI also honours (precedence: flag > env > config):
  API_HEALTH_TEST_URL   base URL to audit (required)
  API_HEALTH_PATH       path to the health endpoint

See .slopstopper.yml.example for the canonical schema.

Exit codes:
  0: endpoint healthy, or no endpoint configured (graceful skip)
  1: failures detected
  2: the URL is missing, or its scheme is not http/https
"""

from __future__ import annotations

import argparse
import json
import os
import time
import urllib.error
from pathlib import Path

from slopstopper import config, output
from slopstopper.checks import _http, _report
from slopstopper.checks._contract import refuse_unsafe_url

REPORT_DIR = Path(".ss/reports/api-health")
REPORT_MD = REPORT_DIR / "api-health-report.md"
REPORT_JSON = REPORT_DIR / "api-health-report.json"
USER_AGENT = "SlopStopper-ApiHealth-Check/1.0"
DEFAULT_EXPECT_STATUS = 200
JSON_CONTENT_TYPES = ("application/json", "application/health+json", "+json")

# Consumed by `slopstopper emit reliability:api-health --target pr-comment`.
# No issue keys: the exit code fails the workflow; no main-branch issue.
META = {
    "report_path": str(REPORT_MD),
    "comment_discriminator": "🩺 API Health",
}


# ── safety ───────────────────────────────────────────────────────


_LABEL = "API health check"


def _fetch(url: str) -> tuple[int, str, str, float]:
    """GET the URL. Returns (status, content_type, body, elapsed_ms).

    A 4xx/5xx is data, not an exception. The check reports the status it
    got against the status it expected, so HTTPError is unwrapped rather
    than propagated.
    """
    started = time.monotonic()
    try:
        with _http.open_url(url, USER_AGENT, label=_LABEL) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            elapsed = (time.monotonic() - started) * 1000
            return resp.status, resp.headers.get("Content-Type", ""), body, elapsed
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace") if e.fp else ""
        elapsed = (time.monotonic() - started) * 1000
        return e.code, e.headers.get("Content-Type", "") if e.headers else "", body, elapsed


# ── body assertions ──────────────────────────────────────────────


def _is_json_content_type(content_type: str) -> bool:
    ct = content_type.split(";")[0].strip().lower()
    return ct in JSON_CONTENT_TYPES[:2] or ct.endswith("+json")


def _resolve_field(body: object, dotted: str) -> tuple[bool, object]:
    """Walk a dot-path through nested dicts. Returns (found, value).

    List indices are not supported: a health body deep enough to need
    them is not the shape this check is for, and silently accepting
    `items.0.status` would imply support that isn't there.
    """
    current = body
    for part in dotted.split("."):
        if not isinstance(current, dict) or part not in current:
            return False, None
        current = current[part]
    return True, current


def _check_fields(
    parsed_body: object,
    require_fields: list[str],
    expect_fields: dict,
    issues: list[str],
) -> None:
    for dotted in require_fields:
        found, _ = _resolve_field(parsed_body, dotted)
        if not found:
            issues.append(f"required field `{dotted}` missing from the response body")

    for dotted, expected in (expect_fields or {}).items():
        found, actual = _resolve_field(parsed_body, dotted)
        if not found:
            issues.append(f"expected field `{dotted}` missing from the response body")
        elif str(actual) != str(expected):
            issues.append(f"field `{dotted}` is `{actual}`, expected `{expected}`")


def _check_body(
    body: str,
    content_type: str,
    opts: dict,
    issues: list[str],
    notes: list[str],
) -> object:
    """Validate the response body. Returns the parsed body, or None."""
    if not body.strip():
        issues.append("response body is empty")
        return None

    if not opts["require_json"]:
        return None

    if not _is_json_content_type(content_type):
        issues.append(
            f"content-type is `{content_type or '(none)'}`, expected a JSON type "
            "(pass --allow-non-json, or set api.health.require_json: false, if that's intended)"
        )

    try:
        parsed = json.loads(body)
    except ValueError as e:
        issues.append(f"response body is not valid JSON: {e}")
        return None

    if opts["require_fields"] or opts["expect_fields"]:
        _check_fields(parsed, opts["require_fields"], opts["expect_fields"], issues)
    elif not isinstance(parsed, dict):
        notes.append(
            f"response body is a JSON {type(parsed).__name__}, not an object, and "
            "api.health.require_fields / expect_fields can only assert on an object"
        )
    return parsed


# ── audit ────────────────────────────────────────────────────────


def _join(url: str, base_path: str, path: str) -> str:
    return _http.join_url(url, base_path, path)


def _audit(url: str, opts: dict) -> dict:
    target = _join(url, opts["base_path"], opts["path"])
    issues: list[str] = []
    notes: list[str] = []

    try:
        status, content_type, body, elapsed_ms = _fetch(target)
    except (urllib.error.URLError, TimeoutError) as e:
        return {
            "url": target,
            "status": "fail",
            "http_status": None,
            "response_ms": None,
            "issues": [f"endpoint not reachable: {type(e).__name__}: {e}"],
            "notes": [],
            "body_preview": "",
        }

    if status != opts["expect_status"]:
        issues.append(f"returned HTTP {status}, expected {opts['expect_status']}")

    _check_body(body, content_type, opts, issues, notes)

    budget = opts["max_response_ms"]
    timing = f"{elapsed_ms:.0f}ms"
    if budget:
        if elapsed_ms > budget:
            issues.append(f"responded in {timing}, over the {budget}ms budget")
    else:
        notes.append(f"responded in {timing} (no api.health.max_response_ms budget set)")

    return {
        "url": target,
        "status": "fail" if issues else "pass",
        "http_status": status,
        "response_ms": round(elapsed_ms, 1),
        "content_type": content_type,
        "issues": issues,
        "notes": notes,
        "body_preview": body[:500],
    }


# ── markdown report ──────────────────────────────────────────────


def _build_markdown_report(result: dict) -> str:
    lines: list[str] = []
    lines.append("# 🩺 API Health Report")
    lines.append("")
    if result["status"] == "skipped":
        lines.extend(
            _report.render_skip(
                "no `api.health.path` configured.",
                [
                    "Point this check at your health/readiness endpoint in `.slopstopper.yml`:",
                    "",
                    "```yaml",
                    "api:",
                    "  health:",
                    "    path: /health",
                    "    require_fields: [status]",
                    "```",
                ],
            )
        )
        return "\n".join(lines) + "\n"

    lines.append(f"**Endpoint:** {result['url']}")
    lines.append("")
    lines.append(f"**Overall:** {'✅ PASS' if result['status'] == 'pass' else '❌ FAIL'}")
    lines.append("")
    lines.append(f"**HTTP status:** {result['http_status'] if result['http_status'] else 'unreachable'}")
    lines.append("")
    if result["response_ms"] is not None:
        lines.append(f"**Response time:** {result['response_ms']}ms")
        lines.append("")
    lines.extend(_report.render_issues(result["issues"]))
    lines.extend(_report.render_notes(result["notes"]))
    if result["body_preview"]:
        lines.append("<details><summary>Response body (first 500 bytes)</summary>")
        lines.append("")
        lines.append("```")
        lines.append(result["body_preview"])
        lines.append("```")
        lines.append("")
        lines.append("</details>")
        lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## How to Fix")
    lines.append("")
    lines.append(
        "- **Not reachable** → the endpoint is down, or the URL is wrong. Check "
        "`urls.production` / `urls.preview` and `api.health.path` in `.slopstopper.yml`."
    )
    lines.append(
        "- **Wrong status** → a readiness endpoint should return the documented code "
        "when healthy. Set `api.health.expect_status` if yours deliberately differs."
    )
    lines.append(
        "- **Not JSON** → serve `Content-Type: application/json`, or set "
        "`api.health.require_json: false` if the endpoint is deliberately plain-text."
    )
    lines.append(
        "- **Missing / wrong field** → the body no longer matches the contract in "
        "`api.health.require_fields` / `expect_fields`. Fix the endpoint, or the config "
        "if the contract changed deliberately."
    )
    lines.append(
        "- See [docs/reliability/README.md](../../../docs/reliability/README.md) for the "
        "full env-var and config contract."
    )
    lines.append("")
    return "\n".join(lines) + "\n"


def _write_reports(result: dict) -> None:
    _report.write_reports(REPORT_JSON, REPORT_MD, result, _build_markdown_report)


def _print_result(result: dict) -> None:
    icon = "✅" if result["status"] == "pass" else "❌"
    output._emit(f"  {icon} {result['url']}  (HTTP {result['http_status']})")
    for issue in result["issues"]:
        output._emit(f"      - {issue}")
    for note in result["notes"]:
        output._emit(f"      ⚠️  {note}")


# ── CLI entrypoint ───────────────────────────────────────────────


def _parse_args(args: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="slopstopper run reliability:api-health", add_help=False)
    p.add_argument("url_positional", nargs="?", default=None, help="Base URL to audit")
    p.add_argument("--url", default=None, help="Base URL to audit")
    p.add_argument("--path", default=None, help="Path to the health endpoint (e.g. /health)")
    p.add_argument("--expect-status", type=int, default=None, help="Status code the endpoint must return")
    p.add_argument(
        "--allow-non-json",
        action="store_true",
        help="Skip the JSON content-type + parse assertions",
    )
    p.add_argument(
        "--require-field",
        action="append",
        default=[],
        dest="require_field",
        metavar="DOTTED",
        help="Dot-path that must be present in the body (repeatable)",
    )
    p.add_argument(
        "--max-response-ms",
        type=int,
        default=None,
        help="Fail if the endpoint responds slower than this",
    )
    p.add_argument("--help", "-h", action="help")
    return p.parse_args(args or [])


def _resolve_options(parsed: argparse.Namespace) -> dict:
    return {
        "base_path": config.get("api.base_path", "") or "",
        "path": parsed.path or os.environ.get("API_HEALTH_PATH") or config.get("api.health.path"),
        "expect_status": parsed.expect_status
        or config.get_int("api.health.expect_status", DEFAULT_EXPECT_STATUS),
        "require_json": not parsed.allow_non_json
        and config.get_bool("api.health.require_json", True),
        "require_fields": list(parsed.require_field or config.get("api.health.require_fields", []) or []),
        "expect_fields": config.get("api.health.expect_fields", {}) or {},
        "max_response_ms": parsed.max_response_ms or config.get_int("api.health.max_response_ms", None),
    }


def _skip(reason: str) -> int:
    """Skip gracefully, because an unconfigured check is not a failing check."""
    output.info(reason)
    _write_reports({"url": None, "status": "skipped", "issues": [], "notes": [reason]})
    output.success("Nothing to audit, so skipping (exit 0).")
    return 0


def run(args: list[str] | None = None) -> int:
    parsed = _parse_args(args)
    opts = _resolve_options(parsed)

    if not opts["path"]:
        return _skip(
            "No api.health.path configured in .slopstopper.yml, so there is nothing to probe. "
            "Set it to your health/readiness endpoint (e.g. /health) to enable this check."
        )

    url = parsed.url_positional or parsed.url or os.environ.get("API_HEALTH_TEST_URL")
    if not url:
        output.error("API health target URL is required")
        output._emit("Usage:")
        output._emit("  slopstopper run reliability:api-health -- --url https://api.example.com")
        output._emit("  API_HEALTH_TEST_URL=https://api.example.com slopstopper run reliability:api-health")
        return 2

    if (rc := refuse_unsafe_url(url, _LABEL)) is not None:
        return rc

    output.status("🩺", f"API health audit against: {_join(url, opts['base_path'], opts['path'])}")
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
        output.success("Health endpoint is reachable and matches its contract.")
        return 0
    output.error(
        "Failures detected. See .ss/reports/api-health/api-health-report.md for full details."
    )
    return 1
