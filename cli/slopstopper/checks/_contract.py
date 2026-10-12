"""The exit-code contract's shared helpers (see `slopstopper.checks`).

  0: the repo passed (or the check skipped gracefully)
  1: the repo failed the check: a verdict about the code or the site
  2: the check could not run: bad input, a missing tool, a scan that
      produced nothing readable. Never a verdict, so never a tracking issue

Every mapping from "something went wrong" to an exit code lives here, so
a change to the contract is one edit rather than one per check.
"""

from __future__ import annotations

import json
from pathlib import Path

from slopstopper import output
from slopstopper.checks import _http

EXIT_PASS = 0
EXIT_FAIL = 1
EXIT_CANNOT_RUN = 2
# Kept for the checks that predate the name: an argument a check can't
# honour is a misconfiguration, and misconfiguration is "could not run".
EXIT_MISCONFIGURED = EXIT_CANNOT_RUN


def reject_extra_args(check_name: str, args: list[str]) -> int:
    """Refuse arguments a check has no parser for. Always returns 2.

    Nine checks are configured entirely through `.slopstopper.yml` and
    have no flags. They used to drop whatever the CLI forwarded, so
    `slopstopper run hygiene:complexity -- --max-ccn 5` ran with the
    config default and reported nothing, so the user believed a setting
    was in effect that never was.
    """
    output.error(
        f"{check_name} takes no arguments (got: {' '.join(args)}). "
        "It is configured through .slopstopper.yml. See the check's docstring "
        "or `slopstopper checks describe` for the keys it reads."
    )
    return EXIT_MISCONFIGURED


def refuse_unsafe_url(url: str, label: str) -> int | None:
    """Check a target URL before any request: 2 if its scheme isn't
    http/https, else None. A `file://` or `ftp://` target is a bad input,
    not a site failure. `label` names the check in the message."""
    try:
        _http.require_safe_url(url, label)
    except ValueError as e:
        output.error(str(e))
        return EXIT_CANNOT_RUN
    return None


def runner_exit(returncode: int, *, ran: bool = True) -> int:
    """Map a test runner's exit code (Playwright, lhci) onto the contract.

    0 is a pass. 1 is a verdict only when the runner actually ran its
    tests or audits (`ran`), because both tools also exit 1 when the
    browser fails to launch or collection aborts, and that is "could not
    run".
    Anything else (a missing binary, a crash, a signal) is 2.
    """
    if returncode == 0:
        return EXIT_PASS
    if returncode == 1 and ran:
        return EXIT_FAIL
    return EXIT_CANNOT_RUN


# Playwright fails every test with this when the browser never started,
# which is an environment fault, not a verdict on the site.
_BROWSER_LAUNCH_ERROR = "browserType.launch"


def _failure_messages(suite: dict) -> list[str]:
    """Error messages of every non-passing test result under a JSON-report suite."""
    messages: list[str] = []
    for spec in suite.get("specs") or []:
        for test in spec.get("tests") or []:
            for result in test.get("results") or []:
                if result.get("status") in ("passed", "skipped"):
                    continue
                error = result.get("error") or {}
                messages.append(str(error.get("message") or ""))
    for child in suite.get("suites") or []:
        messages += _failure_messages(child)
    return messages


def playwright_ran(json_report: Path) -> bool:
    """Whether Playwright's JSON report shows the tests ran to a verdict.

    False when there is no report, a global error (a bad config or no
    tests found, which also make Playwright exit 1), nothing executed, or
    every failure is the browser failing to launch.
    """
    try:
        data = json.loads(json_report.read_text())
    except (OSError, ValueError):
        return False
    if not isinstance(data, dict) or data.get("errors"):
        return False
    stats = data.get("stats") or {}
    if sum(int(stats.get(k) or 0) for k in ("expected", "unexpected", "flaky")) == 0:
        return False
    failures = [m for suite in data.get("suites") or [] for m in _failure_messages(suite)]
    return not failures or not all(_BROWSER_LAUNCH_ERROR in m for m in failures)


def scan_incomplete(report_dir: Path, report_md: Path, title: str, detail: str) -> int:
    """Record a scan that produced nothing usable, and return 2.

    The markdown report says so explicitly, so the artifact and the PR
    comment read "did not complete" rather than a clean pass.
    """
    report_dir.mkdir(parents=True, exist_ok=True)
    report_md.write_text(f"# {title}\n\n## ❌ Scan did not complete\n\n{detail}\n")
    output.error(f"{title}: the scan did not complete, so it counts as not run rather than clean")
    output.footer(report_dir, [report_md.name])
    return EXIT_CANNOT_RUN


def stderr_tail_md(tool: str, stderr_tail: list[str]) -> str:
    """The stderr block of an incomplete-scan report: what the tool last said."""
    if not stderr_tail:
        return f"{tool} printed nothing to stderr."
    body = "\n".join(stderr_tail)
    return f"The last {len(stderr_tail)} line(s) {tool} printed to stderr:\n\n```text\n{body}\n```"
