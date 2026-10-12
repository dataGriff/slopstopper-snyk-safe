"""Command dispatcher for the slopstopper CLI.

The slopstopper CLI is the single source of truth for every check in
the security / hygiene / reliability suite. Every workflow body, every
local `task ss:*` shim, and every CI step ultimately calls into one of
the subcommands below.

Subcommands:
  run [--json] <category>:<check>   Run a check, write reports under .ss/reports/.
                                    --json prints one object (exit code, status,
                                    the ❌ failures, the files written) on stdout
                                    and sends the check's own output to stderr.
  emit <category>:<check> --target  Post the report to a PR comment or main-
        {pr-comment, issue}         branch tracking issue (find-or-update).
        [--status {pass,fail}]      PR comments are compact: a verdict line, the
        [--on-pass {close,delete}]  failing items, full report folded away.
  summary --target pr-comment       Upsert ONE comment covering every check on
                                    the PR, from the workflow runs GitHub
                                    recorded for the head commit.
  discover <check> --event <e>      Resolve pages.<check> via sitemap /
                                    changed-pages / explicit list, print
                                    comma-separated paths.
  config get <key> [<default>]      Read a value from .slopstopper.yml.
        [--json]                    Scalars print as-is; lists comma-joined;
                                    null/missing prints the default. --json
                                    prints the value as JSON (null when unset).
  templates list | path <n> | eject <n>
                                    Inspect / resolve / customise bundled
                                    Playwright specs, config, lighthouserc,
                                    server.js. Eject copies the bundled file
                                    into .ss/<name> for adopter editing.
  serve                             Run the bundled static server (with
                                    optional worker/headers.json auto-detect).
                                    execvp's into node so backgrounded
                                    `slopstopper serve &` gives bash the
                                    right PID for kill.
  checks list [--category <cat>] [--json]
                                    Walk the registry and show every check's
                                    name + one-line description. Useful for
                                    discoverability before running.
  doctor [--json]                   Verify install state: which external
                                    tools are present and run (node, gh,
                                    snyk, gitleaks), and which ss-*
                                    workflows GitHub disabled.
  profile list | show | expand <n> | disabled | detect   [--json]
                                    Inspect project-shape profiles. Each is a
                                    `profile:` preset that switches off the
                                    checks a UI / API / library repo has no
                                    use for. `show` reports the active one,
                                    `detect` suggests one from repo contents.

`run`, `doctor`, `profile`, `config get` and `checks list` take `--json`
and then print one JSON value and nothing else on stdout, so a skill, a
workflow or a script can read it without parsing the decorated text. The
shapes are a public contract, documented next to the exit codes in
docs/contributing/README.md.

Future: init, inspect.
"""

from __future__ import annotations

import argparse
import contextlib
import importlib
import json as _json
import os
import shutil
import subprocess
import sys
import traceback
from pathlib import Path

from slopstopper import (
    __version__,
    badges as badges_mod,
    comment,
    config,
    discovery,
    emit as emit_mod,
    output,
    profiles,
    templates,
    workflow_state,
)
from slopstopper.checks import REGISTRY
from slopstopper.checks._contract import EXIT_CANNOT_RUN, EXIT_FAIL, EXIT_PASS


DOCS_URL = "https://github.com/dataGriff/slopstopper-snyk-safe"


# ── bare-invocation banner ───────────────────────────────────────


BANNER = f"""\
slopstopper {__version__}  ·  security, hygiene and reliability checks on every PR

Usage:  slopstopper <command> [options]

Commands:
  run        Run a quality check (security, hygiene, reliability)
  emit       Post a check's report to a PR or main-branch issue
  summary    Upsert the single aggregate check-status comment on a PR
  discover   Resolve pages.<check> for a reliability check
  config     Read .slopstopper.yml values
  templates  Inspect / eject bundled templates
  serve      Run the bundled static server
  checks     List available checks
  doctor     Verify install + required tools
  profile    Inspect the repo's project-shape profile (ui / api / library)

Quick start:
  slopstopper checks list             # see what's available
  slopstopper doctor                  # verify your install
  slopstopper run hygiene:docs-size   # run a check

Docs: {DOCS_URL}
"""


# ── main parser ──────────────────────────────────────────────────


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="slopstopper",
        description=(
            "A Python CLI plus GitHub Actions workflows that run security, hygiene\n"
            "and reliability checks on every PR. One command to install into any repo.\n\n"
            "Every check writes a report under .ss/reports/<category>/ and exits\n"
            "non-zero on failure. PR-comment / issue posting is decoupled via\n"
            "`slopstopper emit`. Templates (Playwright specs, lighthouserc,\n"
            "server.js) ship inside the wheel; adopters customise by ejecting\n"
            "a file into .ss/<name>.\n"
        ),
        epilog=(
            "Quick start:\n"
            "  slopstopper checks list             # see what's available\n"
            "  slopstopper doctor                  # verify your install\n"
            "  slopstopper run hygiene:docs-size   # run a check\n\n"
            f"Docs: {DOCS_URL}"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"slopstopper {__version__}",
    )
    parser.add_argument(
        "--quiet", "-q",
        action="store_true",
        help="Suppress decorative output (running/section/footer). Reports still write to disk.",
    )

    sub = parser.add_subparsers(dest="command")

    _add_run(sub)
    _add_emit(sub)
    _add_summary(sub)
    _add_discover(sub)
    _add_config(sub)
    _add_templates(sub)
    _add_badges(sub)
    _add_serve(sub)
    _add_checks(sub)
    _add_doctor(sub)
    _add_profile(sub)

    return parser


def _add_run(sub) -> None:
    p = sub.add_parser(
        "run",
        help="Run a quality check by name",
        description=(
            "Run a check by its `<category>:<name>` key (see `slopstopper checks list`).\n"
            "Writes the report under .ss/reports/<category>/ and exits non-zero on failure.\n"
        ),
        epilog=(
            "Examples:\n"
            "  slopstopper run hygiene:docs-size\n"
            "  slopstopper run security:secrets\n"
            "  slopstopper run reliability:cwv -- --url https://example.com --prod\n"
            "  slopstopper run security:api-headers -- https://api.example.com\n"
            "  slopstopper run --json hygiene:entry-files\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--json",
        action="store_true",
        help=(
            "Print one JSON object with the verdict (exit_code, status, failures, "
            "reports) on stdout; the check's own output goes to stderr"
        ),
    )
    p.add_argument(
        "check",
        help="Check name in 'category:name' form (e.g. hygiene:docs-size)",
    )
    # Extra args after the check name flow through to the check's own
    # parser. Only the URL-driven checks use this (e.g. security:api-headers
    # takes a URL); the static ones reject any.
    p.add_argument(
        "check_args",
        nargs=argparse.REMAINDER,
        help="Extra args forwarded to the check (e.g. -- <url> for a URL-driven check)",
    )


def _add_emit(sub) -> None:
    p = sub.add_parser(
        "emit",
        help="Emit a previously-run check's report to GitHub",
        description=(
            "Post the check's report (already written by `slopstopper run …`) to\n"
            "either a PR comment (find-or-update by discriminator) or a main-branch\n"
            "tracking issue (create-or-update). Subprocess-invokes the `gh` CLI.\n"
        ),
        epilog=(
            "Examples:\n"
            "  slopstopper emit hygiene:docs-size --target pr-comment\n"
            "  slopstopper emit security:sast --target issue\n"
            "  slopstopper emit security:sast --target issue --on-pass=close\n"
            "  slopstopper emit reliability:seo --target pr-comment --status fail\n"
            "  slopstopper emit reliability:seo --target pr-comment --status pass --on-pass=delete\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "check",
        help="Check name in 'category:name' form whose report to emit",
    )
    p.add_argument(
        "--target",
        required=True,
        choices=["pr-comment", "issue"],
        help="Where to send the report: pr-comment | issue",
    )
    p.add_argument(
        "--status",
        choices=["pass", "fail", "warn"],
        default="fail",
        help=(
            "The check's own outcome, as the workflow saw it (default: fail). "
            "'warn' is for the advisory checks that report findings while exiting 0. "
            "Drives the comment's verdict line instead of parsing the report. "
            "Pass `--status ${{ steps.<id>.outcome == 'success' && 'pass' || 'fail' }}`"
        ),
    )
    p.add_argument(
        "--on-pass",
        choices=["close", "delete"],
        default=None,
        help=(
            "Post-pass behaviour. 'close' (--target issue) comments + closes any open "
            "issue matching the check's labels. 'delete' (--target pr-comment) removes "
            "the check's rolling comment, so a green PR carries only the summary"
        ),
    )


def _add_summary(sub) -> None:
    p = sub.add_parser(
        "summary",
        help="Upsert the single aggregate SlopStopper status comment on a PR",
        description=(
            "Render one status comment covering every slopstopper check on the\n"
            "pull request, from the workflow runs GitHub already recorded for the\n"
            "head commit. Needs no coordination with the check workflows, so it\n"
            "cannot race them: a green PR gets a three-line comment, a red one\n"
            "leads with a table of just the failures.\n\n"
            "Intended to run from `ss-pr-summary.yml` on `workflow_run: completed`.\n"
            "Exits 0 when the event has no associated pull request.\n"
        ),
        epilog="Example:\n  slopstopper summary --target pr-comment\n",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--target",
        required=True,
        choices=["pr-comment"],
        help="Where to send the summary (only pr-comment today)",
    )


def _add_discover(sub) -> None:
    p = sub.add_parser(
        "discover",
        help="Resolve pages.<check> for a reliability check",
        description=(
            "Resolve the page list a reliability check should audit, using the\n"
            "resolution order: explicit list → sitemap.xml → changed pages (vs\n"
            "main). The event drives which mode `.slopstopper.yml` picks.\n"
            "Prints comma-separated paths to stdout; exit 2 if no path resolved.\n"
        ),
        epilog=(
            "Examples:\n"
            "  slopstopper discover smoke --event pr\n"
            "  slopstopper discover broken-links --event main\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "check",
        choices=discovery.CHECKS + ("broken-links",),
        help="Reliability check whose pages to resolve",
    )
    p.add_argument(
        "--event",
        choices=discovery.EVENTS,
        default="local",
        help="CI event that drives discovery mode (default: local)",
    )


def _add_config(sub) -> None:
    p = sub.add_parser(
        "config",
        help="Read values from .slopstopper.yml",
        description="Inspect values from .slopstopper.yml.\n",
    )
    config_sub = p.add_subparsers(dest="config_action", required=True)
    get = config_sub.add_parser(
        "get",
        help="Print the value at a dot-path key, or the default if missing",
        description=(
            "Read a dot-path key from .slopstopper.yml. Scalars print as-is;\n"
            "lists print comma-joined; null/missing keys print the default\n"
            "(or empty string).\n"
        ),
        epilog=(
            "Examples:\n"
            "  slopstopper config get smoke.og_image_path /og-image.png\n"
            "  slopstopper config get workflows.disabled\n"
            "  slopstopper config get urls.production\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    get.add_argument("key", help="Dot-path into .slopstopper.yml (e.g. workflows.disabled)")
    get.add_argument(
        "default",
        nargs="?",
        default=None,
        help="Fallback printed when the key is missing or null (default: empty string)",
    )
    get.add_argument(
        "--json",
        action="store_true",
        help="Print the value as JSON (a list stays a list; unset prints null, or the default)",
    )


def _add_templates(sub) -> None:
    p = sub.add_parser(
        "templates",
        help="Inspect / eject bundled templates",
        description=(
            "Bundled templates (Playwright specs, lighthouserc dev/prod, server.js,\n"
            "playwright.config.js) live inside the slopstopper-cli wheel. To\n"
            "customise a template, eject it. `eject` copies it into `.ss/<name>`\n"
            "and the CLI's resolver prefers the override.\n"
        ),
    )
    sub2 = p.add_subparsers(dest="templates_action", required=True)
    sub2.add_parser(
        "list",
        help="List bundled templates and whether each is ejected to .ss/",
        description="List every bundled template, marking which ones are already ejected.\n",
        epilog="Example:\n  slopstopper templates list\n",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    pa = sub2.add_parser(
        "path",
        help="Print the resolved path (override-or-bundled) for a template",
        description=(
            "Print the absolute path the CLI's templates resolver would use:\n"
            ".ss/<name> if ejected, else the wheel's package-data copy.\n"
        ),
        epilog="Example:\n  slopstopper templates path lighthouserc.json\n",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    pa.add_argument("name", help="Template name (see `slopstopper templates list`)")
    ej = sub2.add_parser(
        "eject",
        help="Copy the bundled template into .ss/<name> for customisation",
        description=(
            "Copy the bundled file into `.ss/<name>` so you can edit it. The\n"
            "CLI's templates resolver picks up the override on the next run.\n"
            "No-op if `.ss/<name>` already exists (customisations are preserved).\n"
        ),
        epilog="Example:\n  slopstopper templates eject lighthouserc.json\n",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ej.add_argument("name", help="Template name (see `slopstopper templates list`)")


def _add_badges(sub) -> None:
    p = sub.add_parser(
        "badges",
        help="Print a markdown status-badges block for the installed workflows",
        description=(
            "Generate a markdown badges block for the ss-*.yml workflows in\n"
            ".github/workflows/, grouped by loop (Security / Hygiene /\n"
            "Reliability / Operational). Paste the output into your README.\n"
            "OWNER/REPO is detected from $GITHUB_REPOSITORY or `git remote`.\n"
        ),
        epilog=(
            "Examples:\n"
            "  slopstopper badges                       # preview to stdout\n"
            "  slopstopper badges > badges.md           # write to a file\n"
            "  slopstopper badges --no-advert           # skip the slopstopper badge\n"
            "  slopstopper badges --owner foo --repo bar\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--owner",
        default=None,
        help="GitHub owner (auto-detected from git remote if omitted)",
    )
    p.add_argument(
        "--repo",
        default=None,
        help="GitHub repo (auto-detected from git remote if omitted)",
    )
    p.add_argument(
        "--no-advert",
        action="store_true",
        help="Skip the 'powered by slopstopper' shields.io badge",
    )


def _add_serve(sub) -> None:
    sub.add_parser(
        "serve",
        help="Run the bundled static server",
        description=(
            "Run the bundled static server. Auto-detects SERVE_ROOT\n"
            "(the first match of dist/client, dist, build, out, public, app) and\n"
            "optionally applies headers from worker/headers.json if present.\n\n"
            "Replaces the slopstopper process via execvp(node), so a backgrounded\n"
            "`slopstopper serve &` gives bash the right PID via `$!` for later kill.\n"
        ),
        epilog=(
            "Environment:\n"
            "  PORT                    default 8080\n"
            "  SERVE_ROOT              explicit build-output dir\n"
            "  SS_SERVER_HEADERS       explicit JSON headers file\n\n"
            "Examples:\n"
            "  slopstopper serve\n"
            "  PORT=8000 SERVE_ROOT=dist/client slopstopper serve\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )


def _add_checks(sub) -> None:
    p = sub.add_parser(
        "checks",
        help="List available checks",
        description="Inspect what checks the installed CLI knows about.\n",
    )
    sub2 = p.add_subparsers(dest="checks_action", required=True)
    ls = sub2.add_parser(
        "list",
        help="List every check with its category + one-line description",
        description=(
            "Walk the REGISTRY and print each check's `<category>:<name>` key plus\n"
            "a one-line summary (first line of the check module's docstring).\n"
        ),
        epilog=(
            "Examples:\n"
            "  slopstopper checks list\n"
            "  slopstopper checks list --category security\n"
            "  slopstopper checks list --json\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ls.add_argument(
        "--category",
        choices=["security", "hygiene", "reliability"],
        help="Only show checks in this category",
    )
    ls.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON instead of the formatted table",
    )


def _add_doctor(sub) -> None:
    p = sub.add_parser(
        "doctor",
        help="Verify install + required external tools",
        description=(
            "Check that the external tools each slopstopper check needs are on\n"
            "PATH and run (`<tool> --version` exits 0): node (Playwright/\n"
            "Lighthouse/server), gh (emit), snyk (sast, dependencies, iac,\n"
            "sbom), gitleaks (secrets). A tool that resolves but\n"
            "fails to run (a stale mise shim, say) counts as missing.\n\n"
            "Then ask GitHub (via gh) which ss-* workflows it has disabled.\n"
            "GitHub switches off scheduled workflows after 60 days without\n"
            "repo activity, and a disabled workflow runs on no trigger, PRs\n"
            "included. Skipped with a note when gh can't reach the repo.\n\n"
            "Exit 0 if everything required is installed and running (a tool\n"
            "whose check this repo doesn't carry doesn't count). Exit 1 if a\n"
            "required-and-enabled tool is missing or broken, or an ss-*\n"
            "workflow is disabled for inactivity.\n"
        ),
        epilog="Example:\n  slopstopper doctor\n",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--json",
        action="store_true",
        help="Print one JSON object (ok, exit_code, tools, workflows) instead of the report",
    )


def _add_profile(sub) -> None:
    p = sub.add_parser(
        "profile",
        help="Inspect the repo's project-shape profile (ui / api / library)",
        description=(
            "A profile is a named preset for `workflows.disabled`: it switches\n"
            "off the checks a repo of that shape has no use for. `profile: api`\n"
            "drops the nine browser-and-SEO reliability checks, which assume\n"
            "HTML and a public web surface and go red rather than no-op on an\n"
            "API. Set it in .slopstopper.yml; `workflows.enabled` takes an\n"
            "individual check back out of the profile's set.\n"
        ),
    )
    sub2 = p.add_subparsers(dest="profile_action", required=True)
    actions = []
    actions.append(sub2.add_parser(
        "list",
        help="List every profile and the workflows it disables",
        description="Print each profile, what it's for, and which workflows it switches off.\n",
        epilog="Example:\n  slopstopper profile list\n",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    ))
    actions.append(sub2.add_parser(
        "show",
        help="Show the active profile and this repo's effective disabled set",
        description=(
            "Report the profile from .slopstopper.yml (or the default when unset)\n"
            "and the workflows this repo ends up not carrying once\n"
            "workflows.disabled / workflows.enabled are applied.\n"
        ),
        epilog="Example:\n  slopstopper profile show\n",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    ))
    ex = sub2.add_parser(
        "expand",
        help="Print the workflow filenames a named profile always disables (one per line)",
        description=(
            "Print a profile's unconditional workflow list, one filename per line.\n"
            "It leaves out the API checks `ui` holds back until the repo shows an\n"
            "API; `profile disabled` prints this repo's full effective set.\n"
            "Exits 2 on an unknown profile name.\n"
        ),
        epilog="Example:\n  slopstopper profile expand api\n",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ex.add_argument("name", help=f"Profile name ({' | '.join(profiles.names())})")
    actions.append(ex)
    actions.append(sub2.add_parser(
        "disabled",
        help="Print the workflows this repo doesn't carry (one per line)",
        description=(
            "Print this repo's effective disabled set, one filename per line: the\n"
            "profile's workflows (and its no-API holdbacks) minus workflows.enabled,\n"
            "plus workflows.disabled. The set install.sh leaves out; workflows read it\n"
            "to skip checks the repo has turned off.\n"
        ),
        epilog="Example:\n  slopstopper profile disabled\n",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    ))
    actions.append(sub2.add_parser(
        "detect",
        help="Suggest a profile from the repo's contents without changing anything",
        description=(
            "Sniff the working directory for web / API / library markers and print\n"
            "the profile that fits, with the evidence. Advisory only: it never\n"
            "edits .slopstopper.yml, because a wrong silent pick (checks quietly\n"
            "off) is worse than the default superset (checks visibly red).\n"
        ),
        epilog="Example:\n  slopstopper profile detect\n",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    ))
    for action in actions:
        action.add_argument(
            "--json",
            action="store_true",
            help="Print the result as one JSON value instead of the text",
        )


# ── main entry point ─────────────────────────────────────────────


# Top-level subcommand dispatch table. Each entry takes the parsed
# argparse Namespace and returns an exit code. Keeping the routing
# data-driven keeps main()'s cyclomatic complexity flat as the CLI
# gains subcommands.
_DISPATCHERS = {
    "run":       lambda a: _dispatch_run(a.check, a.check_args, as_json=a.json),
    "emit":      lambda a: _dispatch_emit(a.check, a.target, on_pass=a.on_pass, status=a.status),
    "summary":   lambda a: emit_mod.emit_pr_summary(),
    "discover":  lambda a: _dispatch_discover(a.check, a.event),
    "config":    lambda a: _dispatch_config_get(a.key, a.default, as_json=a.json),
    "templates": lambda a: _dispatch_templates(a.templates_action, getattr(a, "name", None)),
    "badges":    lambda a: _dispatch_badges(a.owner, a.repo, a.no_advert),
    "serve":     lambda a: _dispatch_serve(),
    "checks":    lambda a: _dispatch_checks_list(a.category, a.json),
    "doctor":    lambda a: _dispatch_doctor(as_json=a.json),
    "profile":   lambda a: _dispatch_profile(a.profile_action, getattr(a, "name", None), as_json=a.json),
}


def main(argv: list[str] | None = None) -> int:
    """Parse argv and dispatch.

    Bare `slopstopper` (no subcommand) prints the banner and exits 0.
    It is not an error, just a friendly landing page for first-time users.
    """
    parser = _build_parser()

    # Bare invocation → friendly banner. Argparse's `subparsers(required=True)`
    # would otherwise error out with a noisy stderr; we pre-check argv to
    # short-circuit before parsing.
    effective_argv = argv if argv is not None else sys.argv[1:]
    if not effective_argv:
        print(BANNER, end="")
        return 0

    args = parser.parse_args(argv)
    output.set_quiet(bool(getattr(args, "quiet", False)))

    dispatcher = _DISPATCHERS.get(args.command)
    if dispatcher is None:
        parser.error(f"unknown command: {args.command}")
        return 2
    return dispatcher(args)


# ── dispatchers ──────────────────────────────────────────────────


_STATUS = {EXIT_PASS: "pass", EXIT_FAIL: "fail", EXIT_CANNOT_RUN: "cannot-run"}


def _check_module_name(check_name: str) -> str:
    """`reliability:api-health` → `slopstopper.checks.api_health`:
    drop the category, join the rest with underscores."""
    parts = check_name.split(":")[1:]
    return "slopstopper.checks." + "_".join(parts).replace("-", "_")


def _check_meta(check_name: str) -> dict | None:
    """The check's META dict, or None when it has none or is not a module
    (a test's fake registered straight into REGISTRY)."""
    try:
        module = importlib.import_module(_check_module_name(check_name))
    except ImportError:
        return None
    meta = getattr(module, "META", None)
    return meta if isinstance(meta, dict) else None


@contextlib.contextmanager
def _stdout_to_stderr():
    """Route everything the check prints, subprocesses included, to stderr,
    so stdout carries the JSON verdict and nothing else. The file
    descriptor is swapped as well as sys.stdout because the Playwright,
    Lighthouse and scanner wrappers inherit fd 1 rather than the Python
    object."""
    sys.stdout.flush()
    saved = os.dup(1)
    try:
        os.dup2(2, 1)
        with contextlib.redirect_stdout(sys.stderr):
            yield
    finally:
        sys.stderr.flush()
        os.dup2(saved, 1)
        os.close(saved)


def _run_check(check_name: str, check_args: list[str]) -> int:
    try:
        return REGISTRY[check_name](check_args)
    except Exception:  # noqa: BLE001
        # Any crash means "could not run". Python's default for an uncaught
        # exception is exit 1, which the contract (and every workflow gate)
        # reads as "the repo failed the check". A crash in the check is not
        # a verdict on the repo.
        traceback.print_exc()
        print(
            f"❌ {check_name} crashed, so it exits 2 (could not run). "
            "That is not a verdict on the repo.",
            file=sys.stderr,
        )
        return EXIT_CANNOT_RUN


def _report_path(check_name: str) -> Path | None:
    meta = _check_meta(check_name)
    if meta and meta.get("report_path"):
        return Path(meta["report_path"])
    return None


def _report_dir_state(report: Path | None) -> dict[Path, int]:
    """Every file under the report directory with its mtime, so the verdict
    can tell what this run wrote from what an earlier run left behind."""
    if report is None or not report.parent.is_dir():
        return {}
    return {
        path: path.stat().st_mtime_ns
        for path in report.parent.iterdir()
        if path.is_file()
    }


def _run_verdict(check_name: str, rc: int, before: dict[Path, int]) -> dict:
    """The `run --json` object: the exit code and its name, the `- ❌`
    failures from the report (what the PR comment lists), and the files
    the check wrote this run under its report directory. A report an
    earlier run left behind is neither: a check that could not run
    (exit 2 before writing) reports no failures, not last time's."""
    failures: list[str] = []
    report = _report_path(check_name)
    written = sorted(
        path for path, mtime in _report_dir_state(report).items()
        if before.get(path) != mtime
    )
    if report is not None and report in written:
        failures = comment.extract_failures(report.read_text(encoding="utf-8"))
    reports = [str(path) for path in written]
    return {
        "check": check_name,
        "exit_code": rc,
        "status": _STATUS.get(rc, "cannot-run"),
        "failures": failures,
        "reports": reports,
    }


def _dispatch_run(check_name: str, check_args: list[str], as_json: bool = False) -> int:
    # `run <check> --json` reads the flag as a check argument (REMAINDER
    # takes everything after the name); honour it there too.
    if check_args and check_args[0] == "--json":
        as_json, check_args = True, check_args[1:]
        # argparse strips a leading `--` only when it is the first remainder.
        if check_args and check_args[0] == "--":
            check_args = check_args[1:]
    if check_name not in REGISTRY:
        print(f"❌ unknown check: {check_name}", file=sys.stderr)
        print(f"   known checks: {', '.join(sorted(REGISTRY))}", file=sys.stderr)
        if as_json:
            print(_json.dumps({
                "check": check_name,
                "exit_code": EXIT_CANNOT_RUN,
                "status": "cannot-run",
                "error": "unknown check",
                "failures": [],
                "reports": [],
            }, indent=2))
        return EXIT_CANNOT_RUN
    if as_json:
        before = _report_dir_state(_report_path(check_name))
        with _stdout_to_stderr():
            rc = _run_check(check_name, check_args)
    else:
        rc = _run_check(check_name, check_args)
    # Read back by `emit --target issue`, which never files exit 2 as a finding.
    emit_mod.record_exit(check_name, rc)
    if as_json:
        print(_json.dumps(_run_verdict(check_name, rc, before), indent=2))
    return rc


def _dispatch_config_get(key: str, default: str | None, as_json: bool = False) -> int:
    """Print the value at `key`, matching the bash load_config.py shape.

    Scalars print as-is. Lists print comma-joined. Missing / null
    values print the `default` argument (an empty string when none was
    given). With --json the value prints as JSON, so a list stays a
    list; an unset key prints the default, or null when none was given,
    and an explicit empty default is still an empty string.
    """
    if as_json:
        value = config.get(key, None)
        if value is None:
            value = default
        print(_json.dumps(value))
        return 0
    if default is None:
        default = ""
    value = config.get(key, default)
    if value is None:
        print("")
    elif isinstance(value, list):
        print(",".join(str(v) for v in value))
    else:
        print(value)
    return 0


def _dispatch_discover(check_name: str, event: str) -> int:
    """Resolve pages for `check_name` under `event` and print comma-joined paths.

    Mirrors the bash `.ss/scripts/discover-pages.py` exit codes:
      0: at least one path resolved (printed to stdout)
      1: internal error (logged to stderr)
      2: no path resolved (nothing printed)
    """
    normalised = check_name.replace("-", "_")
    try:
        paths = discovery.discover(normalised, event)
    except Exception as e:  # noqa: BLE001
        print(f"❌ internal discovery error: {e}", file=sys.stderr)
        return 1
    if not paths:
        return 2
    print(",".join(paths))
    return 0


def _dispatch_badges(owner: str | None, repo: str | None, no_advert: bool) -> int:
    """Print the README badges block for the installed ss-*.yml workflows."""
    try:
        block = badges_mod.generate(owner=owner, repo=repo, no_advert=no_advert)
    except ValueError as e:
        print(f"❌ {e}", file=sys.stderr)
        return 2
    print(block, end="")
    return 0


def _dispatch_serve() -> int:
    """Replace the current process with `node <bundled or ejected server.js>`.

    Using execvp (not subprocess) means a backgrounded `slopstopper serve
    &` in bash gets the right PID via `$!`. Bash sees node, so
    `kill $SERVER_PID` actually stops the listener.
    """
    if not shutil.which("node"):
        print("❌ node is not available. Install Node.js to run the local server.", file=sys.stderr)
        return 1
    server_path = templates.template_path(templates.SERVER_JS_NAME)
    if not server_path.exists():
        print(f"❌ Bundled server.js not found at {server_path}", file=sys.stderr)
        return 1
    os.execvp("node", ["node", str(server_path)])
    return 1  # unreachable, because execvp doesn't return on success


def _dispatch_templates(action: str, name: str | None) -> int:
    """Route the `templates {list, path, eject}` subcommands."""
    if action == "list":
        for n in templates.list_templates():
            mark = "(ejected → .ss/)" if templates.is_ejected(n) else "(bundled)"
            print(f"{n}  {mark}")
        return 0
    if action == "path":
        try:
            print(templates.template_path(name))  # type: ignore[arg-type]
        except KeyError:
            print(f"❌ unknown template: {name}", file=sys.stderr)
            print(
                f"   known templates: {', '.join(templates.list_templates())}", file=sys.stderr
            )
            return 2
        return 0
    if action == "eject":
        try:
            dest, was_new = templates.eject(name)  # type: ignore[arg-type]
        except KeyError:
            print(f"❌ unknown template: {name}", file=sys.stderr)
            print(
                f"   known templates: {', '.join(templates.list_templates())}", file=sys.stderr
            )
            return 2
        if was_new:
            print(f"✅ ejected to {dest}")
        else:
            print(f"ℹ️  {dest} already exists, so it was left in place and keeps overriding the bundle")
        return 0
    print(f"❌ unknown templates action: {action}", file=sys.stderr)
    return 2


def _dispatch_emit(
    check_name: str, target: str, *, on_pass: str | None = None, status: str = "fail"
) -> int:
    """Look up the check's META dict (from its module) and emit."""
    if check_name not in REGISTRY:
        print(f"❌ unknown check: {check_name}", file=sys.stderr)
        return 2
    if on_pass == "close" and target != "issue":
        print(
            f"❌ --on-pass=close is only valid with --target issue (got --target {target})",
            file=sys.stderr,
        )
        return 2
    if on_pass == "delete" and target != "pr-comment":
        print(
            f"❌ --on-pass=delete is only valid with --target pr-comment (got --target {target})",
            file=sys.stderr,
        )
        return 2
    module_name = _check_module_name(check_name)
    try:
        module = importlib.import_module(module_name)
    except ImportError as e:
        print(f"❌ cannot import {module_name}: {e}", file=sys.stderr)
        return 2
    meta = getattr(module, "META", None)
    if not meta:
        print(
            f"❌ {check_name} has no META, so emit is not wired up for this check yet",
            file=sys.stderr,
        )
        return 2
    return emit_mod.emit(target, meta, check_name=check_name, on_pass=on_pass, status=status)


# ── checks list ──────────────────────────────────────────────────


def _check_summary(check_name: str) -> str:
    """First non-empty line of the check module's docstring, or ''."""
    # Drop the category, join remaining segments with underscores so multi-
    # segment names like `reliability:api-health` resolve to the
    # `api_health` module.
    parts = check_name.split(":")[1:]
    module_name = "slopstopper.checks." + "_".join(parts).replace("-", "_")
    try:
        module = importlib.import_module(module_name)
    except ImportError:
        return ""
    doc = module.__doc__ or ""
    for line in doc.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped
    return ""


def _dispatch_checks_list(category: str | None, as_json: bool) -> int:
    keys = sorted(REGISTRY)
    if category is not None:
        keys = [k for k in keys if k.split(":")[0] == category]

    # A check whose workflow this repo's profile switches off is still
    # listed, because it's in the registry and `slopstopper run` will run
    # it locally. It is marked so the list reflects what CI does.
    disabled = _disabled_workflows()
    entries = [
        {
            "name": k,
            "summary": _check_summary(k),
            "workflow": profiles.workflow_for(k),
            "disabled": profiles.check_is_disabled(k, disabled),
        }
        for k in keys
    ]

    if as_json:
        print(_json.dumps(entries, indent=2))
        return 0

    if not entries:
        suffix = f" in category {category!r}" if category else ""
        print(f"No checks registered{suffix}.")
        return 0

    width = max(len(e["name"]) for e in entries)
    for e in entries:
        # The emoji is two columns wide, so the bare marker is three spaces.
        marker = " 💤" if e["disabled"] else "   "
        print(f" {marker} {e['name']:<{width}}  {e['summary']}")

    if any(e["disabled"] for e in entries):
        print("")
        print(
            f"  💤 = no workflow in this repo (profile: {profiles.active_name()} "
            "/ workflows.disabled). It still runs locally via `slopstopper run`."
        )
    return 0


# ── doctor ───────────────────────────────────────────────────────


# Each entry: tool name → (check-name-that-needs-it, install hint).
# A "check-name-that-needs-it" of None means the tool is needed by the
# CLI itself (gh for emit, node for several reliability checks).
#
# Note on lizard: it used to appear here as an external binary, which
# was misleading. `brew install lizard` ships lz4's lizard (a different
# tool), and the real Python lizard had to be `pipx inject`ed into
# the slopstopper-cli venv before hygiene:complexity worked. lizard
# is now a runtime dependency of slopstopper-cli (see pyproject.toml),
# so it's always in the venv that `python -m lizard` resolves against.
# Doctor no longer needs to check for it.
_DOCTOR_TOOLS: list[tuple[str, str | None, str]] = [
    ("node", None, "Install Node.js (https://nodejs.org/), which Playwright, Lighthouse and the server need"),
    ("gh", None, "Install GitHub CLI (https://cli.github.com/), which `slopstopper emit` needs"),
    ("snyk", "security:dependencies", "mise install (npm:snyk is pinned in mise.toml)  or  npm install -g snyk; then set SNYK_TOKEN"),
    ("gitleaks", "security:secrets", "mise install (gitleaks is pinned in mise.toml)  or  brew install gitleaks"),
]


def _probe_tool(tool: str) -> tuple[bool, str]:
    """Run `<tool> --version`. Returns (runs, first line of output).

    A tool that resolves on PATH but can't run (a stale mise shim, a
    broken venv) returns False with the first line it printed, so doctor
    reports it rather than passing a binary every check will trip over.
    """
    try:
        result = subprocess.run(
            [tool, "--version"], capture_output=True, text=True, check=False, timeout=15
        )
    except subprocess.TimeoutExpired:
        return False, "`--version` timed out"
    except OSError as exc:
        return False, str(exc)
    runs = result.returncode == 0
    lines = [ln.strip() for ln in (result.stdout or result.stderr or "").splitlines() if ln.strip()]
    if lines:
        return runs, lines[0]
    return runs, "" if runs else f"`--version` exited {result.returncode}"


def _disabled_workflows() -> set[str]:
    """Workflow filenames this repo doesn't carry.

    The `profile:` preset unioned with `workflows.disabled` and minus
    `workflows.enabled`. See slopstopper.profiles.
    """
    return profiles.effective_disabled()


def _check_is_disabled(check_name: str, disabled: set[str]) -> bool:
    """True if the workflow that fronts `check_name` is in the disabled set."""
    return profiles.check_is_disabled(check_name, disabled)


def _doctor_tool_rows(disabled: set[str]) -> list[dict]:
    """One row per external tool: `status` is ok, missing, broken (on PATH
    but `--version` fails) or skipped (unusable, but only a check this
    repo doesn't carry needs it)."""
    rows = []
    for tool, needed_by, hint in _DOCTOR_TOOLS:
        path = shutil.which(tool)
        row = {
            "tool": tool,
            "needed_by": needed_by,
            "path": path,
            "version": None,
            "status": "ok",
            "problem": None,
            "hint": hint,
        }
        if path:
            runs, detail = _probe_tool(tool)
            if runs:
                row["version"] = detail or None
                rows.append(row)
                continue
            row["problem"] = f"found at {path} but does not run: {detail}"
            row["status"] = "broken"
        else:
            row["problem"] = "not installed"
            row["status"] = "missing"
        if needed_by and _check_is_disabled(needed_by, disabled):
            row["status"] = "skipped"
        rows.append(row)
    return rows


def _doctor_tools(rows: list[dict]) -> int:
    """Report each external tool; return how many required ones are unusable."""
    unusable = 0
    for row in rows:
        tool, status = row["tool"], row["status"]
        if status == "ok":
            extra = f" ({row['version']})" if row["version"] else ""
            output.success(f"{tool:<10} found at {row['path']}{extra}")
            continue
        needed_by, problem = row["needed_by"], row["problem"]
        if status == "skipped":
            output.info(
                f"{tool:<10} {problem}, but only {needed_by} needs it and this "
                f"repo doesn't carry that check (profile: {profiles.active_name()} / "
                f"workflows.disabled), so skipping"
            )
            continue

        scope = needed_by if needed_by else "the CLI itself"
        output.error(f"{tool:<10} {problem}, and {scope} needs it")
        if status == "broken":
            output._emit("             A mise shim that no longer resolves: run `mise reshim`,")
            output._emit("             or reinstall the tool. Otherwise:")
        output._emit(f"             {row['hint']}")
        unusable += 1
    return unusable


def _doctor_workflow_rows() -> list[dict] | None:
    """The ss-* workflows GitHub has disabled, or None when GitHub can't
    be asked (no gh, unauthenticated, not a GitHub repo)."""
    rows = workflow_state.disabled_ss_workflows()
    if rows is None:
        return None
    return [
        {
            "name": row.get("name") or row.get("path"),
            "state": row.get("state"),
            "inactive": row.get("state") == workflow_state.INACTIVITY,
            "fix": workflow_state.enable_command(row),
        }
        for row in rows
    ]


def _doctor_workflows(rows: list[dict] | None) -> int:
    """Report ss-* workflows GitHub has disabled; return how many are
    disabled for inactivity. A manually disabled one is reported but
    doesn't fail doctor, since someone chose it."""
    if rows is None:
        output.info(
            "workflows  skipped: gh is missing, unauthenticated, or this isn't a "
            "GitHub repo, so GitHub can't be asked which ss-* workflows it disabled"
        )
        return 0
    if not rows:
        output.success("workflows  every installed ss-* workflow is enabled on GitHub")
        return 0
    inactive = 0
    for row in rows:
        name, state, enable = row["name"], row["state"], row["fix"]
        if row["inactive"]:
            inactive += 1
            output.error(f"workflows  {name}: {state}. It runs on no trigger. Fix: {enable}")
        else:
            output.warn(f"workflows  {name}: {state}. Re-enable with: {enable}")
    if inactive:
        output._emit(
            "             GitHub disables scheduled workflows after 60 days without "
            "repo activity; re-enable each one above."
        )
    return inactive


def _dispatch_doctor(as_json: bool = False) -> int:
    """Verify external tools and workflow state; exit 1 on any blocking problem."""
    tools = _doctor_tool_rows(_disabled_workflows())
    workflows = _doctor_workflow_rows()

    if as_json:
        unusable = sum(1 for row in tools if row["status"] in ("missing", "broken"))
        inactive = sum(1 for row in workflows or [] if row["inactive"])
        rc = EXIT_PASS if unusable == 0 and inactive == 0 else EXIT_FAIL
        print(_json.dumps({
            "ok": rc == EXIT_PASS,
            "exit_code": rc,
            "tools": tools,
            "workflows": {"checked": workflows is not None, "disabled": workflows or []},
        }, indent=2))
        return rc

    output.status("🩺", "slopstopper doctor: checking external tools and workflows")
    output.separator()

    unusable = _doctor_tools(tools)
    inactive = _doctor_workflows(workflows)

    output.separator()
    if unusable == 0 and inactive == 0:
        output.success("All required tools available.")
        return 0
    if unusable:
        output.error(f"{unusable} required tool(s) missing or broken. See the hints above.")
    if inactive:
        output.error(f"{inactive} ss-* workflow(s) disabled by GitHub for inactivity.")
    return 1


# ── profile ──────────────────────────────────────────────────────


def _profile_rows() -> list[dict]:
    default = profiles.default_name()
    rows = []
    for name in profiles.names():
        entry = profiles.describe(name) or {}
        rows.append({
            "name": name,
            "default": name == default,
            "summary": entry.get("summary", ""),
            "detail": entry.get("detail") or None,
            "disables": list(entry.get("disables", [])),
            "disables_without_api": list(entry.get("disables_without_api", [])),
        })
    return rows


def _print_profile_list(as_json: bool = False) -> int:
    """Every profile, what it's for, and the workflows it switches off."""
    rows = _profile_rows()
    if as_json:
        print(_json.dumps(rows, indent=2))
        return 0
    for row in rows:
        suffix = "  (default)" if row["default"] else ""
        output._emit(f"  {row['name']}{suffix}")
        output._emit(f"      {row['summary']}")
        if row["detail"]:
            output._emit(f"      {row['detail']}")
        disables, held = row["disables"], row["disables_without_api"]
        if not disables and not held:
            output._emit("      disables: nothing, so every check applies")
        if disables:
            output._emit(f"      disables {len(disables)} workflow(s):")
            for workflow in disables:
                output._emit(f"        · {workflow}")
        if held:
            output._emit(f"      holds back {len(held)} workflow(s) until the repo shows an API:")
            for workflow in held:
                output._emit(f"        · {workflow}")
        output._emit("")
    output._emit("  Set one in .slopstopper.yml:  profile: api")
    output._emit("  Take an individual check back: workflows.enabled: [ss-reliability-seo-check.yml]")
    return 0


def _profile_show_data() -> dict:
    """The active profile, where it came from, and this repo's effective
    disabled set with the source of each entry."""
    name = profiles.active_name()
    entry = profiles.describe(name) or {}
    configured = str(config.get("profile") or "").strip()
    if not configured:
        origin = "default, because `profile:` is unset"
    elif profiles.describe(configured):
        origin = "from .slopstopper.yml"
    else:
        origin = f"default, because {configured!r} in .slopstopper.yml is not a known profile"

    disabled = sorted(profiles.effective_disabled())
    from_profile = profiles.profile_disables()
    no_api = set(profiles.without_api(name)) & from_profile
    rows = []
    for workflow in disabled:
        if workflow in no_api:
            source = "profile, no API detected"
        elif workflow in from_profile:
            source = "profile"
        else:
            source = "workflows.disabled"
        rows.append({"workflow": workflow, "source": source})
    return {
        "profile": name,
        "origin": origin,
        "configured": configured or None,
        "warning": profiles.validate() or None,
        "summary": entry.get("summary", ""),
        "disabled": rows,
        "re_enabled": sorted(from_profile - set(disabled)),
    }


def _print_profile_show(as_json: bool = False) -> int:
    """The active profile plus this repo's effective disabled set."""
    data = _profile_show_data()
    if as_json:
        print(_json.dumps(data, indent=2))
        return 0
    if data["warning"]:
        output.warn(data["warning"])

    output._emit(f"  Profile: {data['profile']}  ({data['origin']})")
    output._emit(f"      {data['summary']}")
    output._emit("")

    if not data["disabled"]:
        output._emit("  Disabled workflows: none. This repo carries the full suite.")
        return 0

    output._emit(f"  Disabled workflows ({len(data['disabled'])}):")
    for row in data["disabled"]:
        output._emit(f"    · {row['workflow']:<42} ({row['source']})")

    if data["re_enabled"]:
        output._emit("")
        output._emit("  Re-enabled by workflows.enabled despite the profile:")
        for workflow in data["re_enabled"]:
            output._emit(f"    · {workflow}")
    return 0


def _print_profile_expand(name: str | None, as_json: bool = False) -> int:
    """The profile's unconditional list, one per line. install.sh reads this
    repo's effective set via `profile disabled` instead."""
    workflows = profiles.expand(name or "")
    if workflows is None:
        message = f"unknown profile {name!r}. Expected one of: {', '.join(profiles.names())}"
        if as_json:
            print(_json.dumps({"error": message}))
        else:
            output.error(message)
        return 2
    if as_json:
        print(_json.dumps({"profile": name, "workflows": list(workflows)}, indent=2))
        return 0
    for workflow in workflows:
        print(workflow)
    return 0


def _print_profile_detect(as_json: bool = False) -> int:
    """Suggest a profile from repo contents. Advisory: changes nothing."""
    suggested, reason = profiles.detect()
    active = profiles.active_name()
    if as_json:
        print(_json.dumps({
            "suggested": suggested,
            "reason": reason,
            "active": active,
            "matches": suggested == active,
        }, indent=2))
        return 0
    output._emit(f"  Suggested profile: {suggested}  ({reason})")
    if suggested == active:
        output._emit(f"  Active profile:    {active} (already a match, nothing to do).")
        return 0
    output._emit(f"  Active profile:    {active}")
    output._emit("")
    output._emit("  To adopt the suggestion, in .slopstopper.yml:")
    output._emit(f"      profile: {suggested}")
    output._emit("  then re-run install.sh so the workflow set follows.")
    return 0


def _print_profile_disabled(as_json: bool = False) -> int:
    disabled = sorted(profiles.effective_disabled())
    if as_json:
        print(_json.dumps({"disabled": disabled}, indent=2))
        return 0
    for workflow in disabled:
        print(workflow)
    return 0


_PROFILE_ACTIONS = {
    "list": lambda name, as_json: _print_profile_list(as_json),
    "show": lambda name, as_json: _print_profile_show(as_json),
    "expand": _print_profile_expand,
    "disabled": lambda name, as_json: _print_profile_disabled(as_json),
    "detect": lambda name, as_json: _print_profile_detect(as_json),
}


def _dispatch_profile(action: str, name: str | None, as_json: bool = False) -> int:
    handler = _PROFILE_ACTIONS.get(action)
    if handler is None:
        output.error(f"unknown profile action: {action}")
        return 2
    return handler(name, as_json)


