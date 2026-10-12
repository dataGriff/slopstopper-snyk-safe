"""What the four Playwright-backed checks share.

smoke, e2e, accessibility and broken-links each drive a bundled Playwright
spec against a URL and write a short pass/fail summary pointing at
Playwright's own HTML report. Only the spec name, the env vars the spec
reads and the wording differ, so the plumbing lives here once:
`run_check` is the whole flow, and each check module contributes a
`Check` description plus its `_build_env` (the config keys its spec reads).
That pair is a module's entire production surface.

The staging step is the non-obvious part: the bundled config and specs
live inside the installed slopstopper-cli package (mise's tool install,
per mise.toml), where Playwright cannot resolve `node_modules`. Each run
copies the resolved config and spec into the self-ignoring `.ss/.run/`
in the adopter's CWD, next to `node_modules`. `templates.stage_playwright`
documents it.

Exit codes (the contract every one of these checks keeps):
  0: playwright tests passed
  1: playwright tests ran and failed (report still written)
  2: npx (Node.js) not available, the URL is missing, the spec could
      not be found, or Playwright exited with any other non-zero code
      (the suite didn't run to a verdict). Playwright exiting 1 without
      running the tests (a config error, no tests found, the browser
      failing to launch) is 2 as well
"""

from __future__ import annotations

import argparse
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from slopstopper import output, templates
from slopstopper.checks import _report, _tools
from slopstopper.checks._contract import playwright_ran, runner_exit

REPORT_DIR = Path(".ss/reports/reliability")


@dataclass(frozen=True)
class Check:
    """What distinguishes one Playwright-backed check from the next."""

    name: str
    """Short name used in messages and argparse: `smoke`, `broken-links`."""
    spec_name: str
    """Basename of the bundled spec: `tests/<spec_name>.spec.ts`."""
    url_env: str
    """The env var the spec reads its base URL from; `run_check` sets it."""
    url_fallbacks: tuple[str, ...]
    """Env vars consulted after `url_env` when no URL is passed (smoke's, usually)."""
    title: str
    """Markdown heading of the summary; also the PR-comment discriminator."""
    failure_hint: str
    """One sentence under the heading when the run fails."""
    banner: str
    """Status line printed before Playwright starts; `{url}` is substituted."""
    banner_icon: Optional[str] = None
    """Emoji for the banner; None uses the plain `running` style."""
    verb: str = "audit"
    """What the check does to the URL, for argparse help: `smoke-test`, `walk`."""

    @property
    def playwright_json(self) -> Path:
        # Playwright's JSON reporter output says whether the tests ran at all
        # (see `_contract.playwright_ran`), which its exit code alone can't say.
        return REPORT_DIR / f"{self.spec_name}-results.json"

    @property
    def report_md(self) -> Path:
        return REPORT_DIR / f"{self.spec_name}-report.md"

    @property
    def check_id(self) -> str:
        return f"reliability:{self.name}"


def parse_args(check: Check, args: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog=f"slopstopper run {check.check_id}", add_help=False)
    p.add_argument(
        "url_positional",
        nargs="?",
        default=None,
        help=f"Site URL to {check.verb} (alternative to --url; e.g. http://localhost:8080)",
    )
    p.add_argument("--url", default=None, help=f"Site URL to {check.verb} (else ${check.url_env})")
    p.add_argument("--ci", action="store_true", help="CI mode: html reporter, CI=true")
    p.add_argument("--help", "-h", action="help")
    return p.parse_args(args or [])


def resolve_url(check: Check, parsed_url: str | None) -> str | None:
    """The flag, else the check's own env var, else its fallbacks in order."""
    if parsed_url:
        return parsed_url
    for name in (check.url_env, *check.url_fallbacks):
        value = os.environ.get(name)
        if value:
            return value
    return None


def base_env(check: Check, url: str, ci_mode: bool) -> dict[str, str]:
    """The caller's environment plus what every spec needs: where to write
    the JSON report, the base URL, and CI=true in CI mode. A check's own
    `_build_env` layers its config keys on top with `setdefault`, so the
    caller's env always wins over `.slopstopper.yml`."""
    env = dict(os.environ)
    env["PLAYWRIGHT_JSON_OUTPUT_NAME"] = str(Path.cwd() / check.playwright_json)
    env[check.url_env] = url
    if ci_mode:
        env["CI"] = "true"
    return env


def build_cmd(config: Path, spec: Path, ci_mode: bool) -> list[str]:
    # `json` feeds `_contract.playwright_ran`: Playwright exits 1 both when
    # tests failed and when they never ran, and only the report can tell.
    reporter = "list,html,json" if ci_mode else "list,json"
    return [
        "npx", "playwright", "test",
        f"--config={config}",
        str(spec),
        f"--reporter={reporter}",
    ]


def write_summary(
    report_dir: Path,
    md_path: Path,
    title: str,
    exit_code: int,
    url: str,
    failure_hint: str,
) -> None:
    """A minimal markdown summary consumable by `slopstopper emit`.

    Playwright's HTML report at `playwright-report/` is the source of
    truth for failure detail; this just summarises pass/fail, points at
    it, and links the workflow run when there is one.
    """
    report_dir.mkdir(parents=True, exist_ok=True)
    status = "✅ PASSED" if exit_code == 0 else "❌ FAILED"
    lines = [title, "", f"**Status:** {status}", f"**Target:** `{url}`"]
    if exit_code != 0:
        lines += ["", failure_hint]
        run_url = _report.gha_run_url()  # looked up at call time, so patching _report works
        if run_url:
            lines += ["", f"[View the workflow run]({run_url})"]
    md_path.write_text("\n".join(lines) + "\n")


def run_check(
    check: Check,
    args: list[str] | None,
    *,
    build_env: Callable[[str, bool], dict[str, str]],
) -> int:
    """The whole flow of a Playwright-backed check, from args to exit code.

    `build_env` is the one thing that varies: the config keys each spec
    reads, layered over `base_env`.
    """
    if not _tools.npx_available():
        output.error("npx is not available. Install Node.js to run Playwright tests.")
        return 2

    parsed = parse_args(check, args)
    url = resolve_url(check, parsed.url_positional or parsed.url)
    if not url:
        output.error(f"{check.name} target URL is required")
        output._emit("Usage:")
        output._emit(f"  slopstopper run {check.check_id} -- --url https://your-site.example.com")
        output._emit(f"  {check.url_env}=https://your-site slopstopper run {check.check_id}")
        return 2

    message = check.banner.format(url=url)
    if check.banner_icon:
        output.status(check.banner_icon, message)
    else:
        output.running(message)
    spec = templates.playwright_spec(check.spec_name)
    if not spec.exists():
        output.error(f"{check.name} spec not found at {spec}")
        output._emit("   The spec is bundled inside slopstopper-cli; reinstall to repair.")
        return 2

    config, staged_spec = templates.stage_playwright(check.spec_name)
    env = build_env(url, parsed.ci)
    cmd = build_cmd(config, staged_spec, parsed.ci)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    check.playwright_json.unlink(missing_ok=True)
    result = subprocess.run(cmd, env=env, check=False)
    write_summary(REPORT_DIR, check.report_md, check.title, result.returncode, url, check.failure_hint)
    # Playwright's own exit code is kept in the report. It exits 1 both when
    # tests failed (a verdict on the site) and when they never ran (bad
    # config, no browser): the JSON report tells the two apart.
    return runner_exit(result.returncode, ran=playwright_ran(check.playwright_json))
