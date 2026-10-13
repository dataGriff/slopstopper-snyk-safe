"""What the Snyk-backed checks share: locating the CLI, the environment it
runs with, the severity scale and the `fail_on` gate, and reading the
report it wrote.

Four checks shell out to the `snyk` CLI (Snyk Code for `security:sast`,
Snyk Open Source for `security:dependencies` and `security:sbom`, Snyk
IaC for `security:iac`). None imports Snyk: the CLI is a separate,
pinned binary (`npm:snyk` in mise.toml) so its licence and its updates
stay on the adopter's side, the same boundary every other tool keeps.

The verdict is computed here from the report, never from Snyk's own
`--severity-threshold`, so a report always carries every finding and the
gate is one config key per check. Snyk exits 0 for a clean scan, 1 for a
scan with findings, 2 for a failure and 3 when it found nothing it
supports; `completed()` and `found_nothing()` name those so a check never
hard-codes the numbers.

Analytics are off (`SNYK_DISABLE_ANALYTICS=1`) on every invocation: an
enterprise runner should send Snyk the scan and nothing else.

Configuration (.slopstopper.yml, optional; the env var wins when both are set):

    security:
      snyk:
        org: my-org-slug      # or SNYK_ORG; the Snyk organisation to scan under
        # SNYK_API (env only): the regional API, e.g. https://api.eu.snyk.io
        # SNYK_TOKEN (env only): never in the config file
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

from slopstopper import config, output
from slopstopper.checks import _tools

TOOL = "snyk"

# The CLI's exit codes for a scan that completed. 2 is a failure (auth,
# network, a crash) and 3 means no supported project or file was found.
COMPLETED = frozenset({0, 1})
EXIT_NOTHING_SUPPORTED = 3

SEVERITY_RANK = {"critical": 3, "high": 2, "medium": 1, "low": 0}
UNKNOWN_RANK = 3  # fail closed: a severity Snyk has never emitted blocks
FAIL_ON_CHOICES = ("critical", "high", "medium", "low", "none")
DEFAULT_FAIL_ON = "high"

# How much of Snyk's stderr an incomplete-scan report carries.
STDERR_TAIL_LINES = 20

INSTALL_HELP = (
    "snyk is not installed.\n"
    "Install the pinned CLI with:\n"
    "  mise install            # reads the npm:snyk pin in mise.toml\n"
    "  npm install -g snyk     # or by hand, any version\n"
    "More: https://docs.snyk.io/snyk-cli/install-or-update-the-snyk-cli"
)

# What Snyk prints when there is no usable token. Matched case-insensitively
# against its stderr so the report says "set SNYK_TOKEN" rather than only
# "exited 2".
_AUTH_MARKERS = ("authentication", "missingapitoken", "snyk auth", "api token", "unauthorized")


def available() -> bool:
    return shutil.which(TOOL) is not None


def environment() -> dict[str, str]:
    """The environment a scan runs with: the caller's, analytics off.

    An empty `SNYK_*` variable is dropped rather than passed through. A
    workflow sets `SNYK_TOKEN: ${{ secrets.SNYK_TOKEN }}` and the like
    unconditionally, so an unset secret or variable reaches the step as
    an empty string, and an empty `SNYK_API` or `SNYK_TOKEN` must not
    shadow the CLI's own defaults and config.
    """
    env = {k: v for k, v in os.environ.items() if not (k.startswith("SNYK_") and v.strip() == "")}
    env["SNYK_DISABLE_ANALYTICS"] = "1"
    return env


def token_present() -> bool:
    """Whether a token reached the environment. Snyk can also be
    authenticated through `snyk auth` config, so a missing token is a
    warning before the run, not a refusal."""
    return bool(os.environ.get("SNYK_TOKEN", "").strip())


def org_args() -> list[str]:
    """`--org=<slug>` when an organisation is configured, else nothing.

    `SNYK_ORG` wins over `security.snyk.org`: a workflow sets the env var
    from a repository variable, and that is the one the security team
    controls.
    """
    org = os.environ.get("SNYK_ORG", "").strip() or config.get_str("security.snyk.org", "").strip()
    return [f"--org={org}"] if org else []


def fail_on(key: str, default: str = DEFAULT_FAIL_ON) -> str:
    """The configured gate for `key`, one of FAIL_ON_CHOICES.

    `get_str`, not `get()`: a bare `fail_on: false` parses as a boolean,
    and must reach the invalid-value warning rather than silently
    collapsing to the default.
    """
    raw = config.get_str(key, default).strip().lower()
    if raw in FAIL_ON_CHOICES:
        return raw
    output.warn(
        f"{key}: {raw!r} is not one of {', '.join(FAIL_ON_CHOICES)}. Using {default!r} instead."
    )
    return default


def rank(severity: str | None) -> int:
    """A severity's rank. One Snyk has never emitted, or a missing one on a
    malformed finding, ranks as critical: fail closed, not open."""
    return SEVERITY_RANK.get(str(severity or "").lower(), UNKNOWN_RANK)


def blocking(findings: list[dict], gate: str, severity_of=None) -> list[dict]:
    """Findings at or above `gate`. `severity_of` reads a finding's
    severity; by default its `severity` key."""
    if gate == "none":
        return []
    read = severity_of or (lambda f: f.get("severity"))
    threshold = SEVERITY_RANK[gate]
    return [f for f in findings if rank(read(f)) >= threshold]


def count_by_severity(findings: list[dict], severity_of=None) -> dict[str, int]:
    read = severity_of or (lambda f: f.get("severity"))
    counts = {s: 0 for s in SEVERITY_RANK}
    for finding in findings:
        sev = str(read(finding) or "").lower()
        counts[sev if sev in counts else "critical"] += 1
    return counts


def run(args: list[str], report_dir: Path, report_json: Path) -> tuple[int, list[str]]:
    """Run `snyk <args>`; return its exit code and the last lines of stderr.

    The previous run's report is removed first: a scan that dies before
    writing must leave no report, not last run's. stderr streams through
    as the scan runs, and the tail is kept for the report.
    """
    report_dir.mkdir(parents=True, exist_ok=True)
    report_json.unlink(missing_ok=True)
    # Snyk prints its error catalogue entries (SNYK-0005 and friends) on
    # stdout, so both streams feed the tail or the report shows only the
    # binary wrapper's download chatter.
    return _tools.run_tailing_stderr([TOOL, *args], STDERR_TAIL_LINES, env=environment(), merge_stdout=True)


def run_to_file(args: list[str], report_dir: Path, out_path: Path) -> tuple[int, list[str]]:
    """Run `snyk <args>` with its stdout written to `out_path`; return the
    exit code and the last lines of its stderr.

    For the one command (`snyk sbom`) whose product is its stdout. A
    command that cannot start returns 127 with the OS error as its one
    line, and an empty file is removed so it never reads as a report.
    """
    import subprocess

    report_dir.mkdir(parents=True, exist_ok=True)
    out_path.unlink(missing_ok=True)
    try:
        with out_path.open("w") as out:
            proc = subprocess.run(
                [TOOL, *args], stdout=out, stderr=subprocess.PIPE, text=True,
                errors="replace", env=environment(), check=False,
            )
    except OSError as exc:
        out_path.unlink(missing_ok=True)
        return 127, [str(exc)]
    if out_path.exists() and out_path.stat().st_size == 0:
        out_path.unlink()
    lines = [ln.rstrip() for ln in (proc.stderr or "").splitlines() if ln.strip()]
    return proc.returncode, lines[-STDERR_TAIL_LINES:]


def completed(rc: int) -> bool:
    return rc in COMPLETED


def found_nothing(rc: int) -> bool:
    return rc == EXIT_NOTHING_SUPPORTED


def read_json(path: Path):
    """The JSON Snyk wrote, or None when there is no usable report.

    None means the scan could not run, and the caller exits 2. Returning
    an empty result here would turn a crashed scan into a clean pass.
    """
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def as_projects(data) -> list[dict] | None:
    """Snyk's per-project results as a list. `--all-projects` writes an
    array, a single project writes one object; both land here as a list
    of dicts. None for anything else."""
    if isinstance(data, dict):
        return [data]
    if isinstance(data, list) and all(isinstance(item, dict) for item in data):
        return data
    return None


def incomplete_detail(product: str, rc: int, has_data: bool, stderr_tail: list[str], report_json: Path) -> str:
    """The body of an incomplete-scan report: what happened and what Snyk said."""
    from slopstopper.checks._contract import stderr_tail_md

    what = "wrote no readable report" if not has_data else "reported a failure"
    detail = (
        f"{product} exited {rc} and {what} (`{report_json}`). "
        "The scan is treated as not run, not as clean."
    )
    hints = []
    joined = "\n".join(stderr_tail).lower()
    if not token_present():
        hints.append(
            "No `SNYK_TOKEN` reached this check. In CI, add it as a repository secret "
            "(a service-account token for the Snyk organisation); locally, export it or "
            "run `snyk auth`. Every Snyk product needs it, including the organisation "
            "settings Snyk IaC fetches before it scans."
        )
    elif any(marker in joined for marker in _AUTH_MARKERS):
        hints.append(
            "Snyk could not authenticate. In CI, set the `SNYK_TOKEN` repository "
            "secret (a service-account token for the Snyk organisation); locally, "
            "run `snyk auth` or export `SNYK_TOKEN`."
        )
    hints.append(
        "If it says the `snyk` command can't run, `slopstopper doctor` confirms it. "
        "A regional tenant needs `SNYK_API` (for example `https://api.eu.snyk.io`)."
    )
    return f"{detail}\n\n{stderr_tail_md(product, stderr_tail)}\n\n" + "\n\n".join(hints)


def nothing_supported_md(product: str, what: str) -> str:
    return (
        f"## ⏭️ Nothing to scan\n\n{product} found no {what} it supports in this repository, "
        "so there is nothing to report. The check passes; it is not a verdict on code "
        "that was never scanned.\n"
    )


def truncate(text: str, width: int = 80) -> str:
    text = " ".join(str(text or "").split())
    return (text[: width - 3] + "...") if len(text) > width else text
