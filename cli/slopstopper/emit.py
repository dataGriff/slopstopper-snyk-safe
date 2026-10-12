"""GitHub PR-comment and issue emission for checks.

Centralises the find-or-create logic that lives, today, as duplicated
`actions/github-script@v7` blocks in every `ss-*-check.yml` workflow.
The plan called this out as the load-bearing simplification. Once the
emit logic lives here, workflows shrink from ~50 lines of YAML +
embedded JS to a single shell line:

  - run: slopstopper run hygiene:docs-size
  - run: slopstopper emit hygiene:docs-size --target pr-comment
    if: github.event_name == 'pull_request'
  - run: slopstopper emit hygiene:docs-size --target issue
    if: github.event_name == 'push' && steps.check.outcome == 'failure'

Subprocess-invokes the `gh` CLI, which is already on every GitHub Actions
runner and already authed via $GITHUB_TOKEN. Same licensing-boundary
pattern as the other external tools (gitleaks, snyk):
slopstopper-cli wheel ships zero gh code.

Each check that wants to emit declares a META dict beside its `run()`:

    META = {
        "report_path": ".ss/reports/.../*.md",
        "comment_discriminator": "📚 ...",   # substring uniquely on the bot's PR comment
        "issue_title": "📚 ... Exceeds Thresholds",
        "issue_labels": ["...", "maintenance"],
        "issue_followup": "🔔 Thresholds exceeded again in commit",
        "issue_close_comment": "✅ ... now within thresholds. Closing automatically.",  # optional; used by `emit --on-pass=close`
    }

The brand label `slopstopper` is auto-prepended to `issue_labels` so every
emitted issue can be discovered with `gh issue list --label slopstopper`.
`gh issue create --label` fails on a label the repo doesn't have, so any
missing label is created first; if one still can't be applied, the issue
is created without labels and a warning says so, because a lost failure
report is worse than an unlabelled one. The body marker then stands in
for the labels when the issue is looked up again.
Bodies also gain a hidden HTML-comment marker `<!-- slopstopper:check=<name> -->`
in the footer for machine-readable dedup and, in Actions, a
`<!-- slopstopper:workflow=<file>.yml -->` marker naming the workflow that
raised it. `ss-workflow-failure-issue.yml` skips its generic failure issue
only when an open issue carries the failing workflow's own marker, so one
check's issue never silences another check's failure.

PR-comment update path: `gh api PATCH .../issues/comments/{id}`, because
`gh` doesn't have a direct edit-comment command.
Issue path: `gh issue list | gh issue {create,edit,comment,close}`.

Note: issue bodies are bot-managed. `_update_issue_body` overwrites the
whole body on every re-emit, so the marker stays stable but hand-edited
maintainer notes in the body get wiped. Track notes as issue comments
instead.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from slopstopper import comment as comment_mod


def _gh_available() -> bool:
    return shutil.which("gh") is not None


def _repo_slug() -> str | None:
    """GitHub-Actions-supplied $GITHUB_REPOSITORY = owner/name."""
    return os.environ.get("GITHUB_REPOSITORY")


def _pr_number_from_event() -> int | None:
    """Read the PR number from the workflow event payload.

    Works for `pull_request` triggers. Returns None on push / workflow_dispatch.
    """
    path = os.environ.get("GITHUB_EVENT_PATH")
    if not path:
        return None
    try:
        event = json.loads(Path(path).read_text())
    except (OSError, json.JSONDecodeError):
        return None
    pr = event.get("pull_request")
    if isinstance(pr, dict) and isinstance(pr.get("number"), int):
        return pr["number"]
    return None


def _event_payload() -> dict:
    """The workflow event payload, or {} when unreadable."""
    path = os.environ.get("GITHUB_EVENT_PATH")
    if not path:
        return {}
    try:
        payload = json.loads(Path(path).read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _head_sha() -> str | None:
    """The PR head commit, not the synthetic merge commit.

    On a `pull_request` event $GITHUB_SHA is the merge commit, which is
    not a SHA a reviewer can find in the branch, so prefer the payload's
    head sha and fall back to the env var for push/workflow_run.
    """
    event = _event_payload()
    pr = event.get("pull_request")
    if isinstance(pr, dict):
        head = pr.get("head")
        if isinstance(head, dict) and head.get("sha"):
            return str(head["sha"])
    run = event.get("workflow_run")
    if isinstance(run, dict) and run.get("head_sha"):
        return str(run["head_sha"])
    return os.environ.get("GITHUB_SHA")


def _run_url() -> str | None:
    """Link to the run that produced this comment, when in Actions."""
    server = os.environ.get("GITHUB_SERVER_URL")
    repo = _repo_slug()
    run_id = os.environ.get("GITHUB_RUN_ID")
    if not (server and repo and run_id):
        return None
    return f"{server}/{repo}/actions/runs/{run_id}"


def _gh(*args: str, capture: bool = False) -> subprocess.CompletedProcess[str]:
    """Run `gh` with stdout/stderr passthrough (or captured)."""
    cmd = ["gh", *args]
    if capture:
        return subprocess.run(cmd, check=False, capture_output=True, text=True)
    return subprocess.run(cmd, check=False)


# ── PR comment emission ──────────────────────────────────────────


def _list_pr_comments(repo: str, pr: int) -> list[dict]:
    result = _gh("api", f"repos/{repo}/issues/{pr}/comments", capture=True)
    if result.returncode != 0:
        return []
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else []


def _find_existing_pr_comment(
    comments: list[dict], discriminator: str, marker: str | None = None
) -> int | None:
    """Id of the bot comment for this check, or None.

    Matches the hidden marker first, because it is exact and stays stable
    even if the report's headings change. It then falls back to the
    discriminator substring so comments posted before markers existed are
    still found and updated rather than duplicated.
    """
    for needle in ([marker] if marker else []) + [discriminator]:
        for entry in comments:
            if not isinstance(entry, dict):
                continue
            user = entry.get("user") or {}
            if user.get("type") != "Bot":
                continue
            if needle and needle in (entry.get("body") or ""):
                cid = entry.get("id")
                if isinstance(cid, int):
                    return cid
    return None


def _update_pr_comment(repo: str, comment_id: int, body_path: Path) -> int:
    """gh api PATCH .../issues/comments/<id> -F body=@<path>."""
    result = _gh(
        "api", "-X", "PATCH",
        f"repos/{repo}/issues/comments/{comment_id}",
        "-F", f"body=@{body_path}",
        capture=True,
    )
    return result.returncode


def _create_pr_comment(pr: int, body_path: Path) -> int:
    result = _gh("pr", "comment", str(pr), "--body-file", str(body_path))
    return result.returncode


def _delete_pr_comment(repo: str, comment_id: int) -> int:
    result = _gh(
        "api", "-X", "DELETE", f"repos/{repo}/issues/comments/{comment_id}", capture=True
    )
    return result.returncode


def _pr_context() -> tuple[str, int] | None:
    """(repo, pr_number), or None with a reason printed."""
    repo = _repo_slug()
    pr = _pr_number_from_event()
    if not repo:
        print("❌ $GITHUB_REPOSITORY is not set", file=sys.stderr)
        return None
    if pr is None:
        print("❌ No PR detected from the event payload, so skipping the PR comment", file=sys.stderr)
        return None
    return repo, pr


def _resolve_pass_action(repo: str, pr: int, discriminator: str, marker: str) -> int:
    """--on-pass=delete: drop the stale comment, leave nothing behind."""
    existing = _find_existing_pr_comment(_list_pr_comments(repo, pr), discriminator, marker)
    if existing is None:
        print("✅ Check passed and there is no prior comment to clean up, so nothing to post")
        return 0
    print(f"🧹 Check passed, so deleting stale PR comment {existing}")
    return _delete_pr_comment(repo, existing)


def emit_pr_comment(
    report_path: Path,
    discriminator: str,
    *,
    check_name: str | None = None,
    status: str = "fail",
    on_pass: str | None = None,
) -> int:
    """Upsert the compact PR comment for one check.

    `comment.build_body` renders the body as a verdict line, the failing
    items and the full report folded away, rather than as the whole
    report verbatim. `status` comes from the workflow (the
    check's own step outcome); with `on_pass='delete'` a passing check
    removes its comment instead of posting one, so a green PR ends up
    carrying only the aggregate summary.
    """
    if not _gh_available():
        print("❌ gh CLI is not available, and --target pr-comment needs it", file=sys.stderr)
        return 1

    context = _pr_context()
    if context is None:
        return 1
    repo, pr = context
    marker = comment_mod.CHECK_MARKER.format(name=check_name or "")

    if status == "pass" and on_pass == "delete":
        return _resolve_pass_action(repo, pr, discriminator, marker)

    if not report_path.exists():
        print(f"❌ Report not found at {report_path}", file=sys.stderr)
        return 1

    body = comment_mod.build_body(
        check_name or "",
        report_path.read_text(),
        status=status,
        head_sha=_head_sha(),
        run_url=_run_url(),
    )
    body_path = report_path.with_suffix(".comment.md")
    body_path.write_text(body)

    existing = _find_existing_pr_comment(_list_pr_comments(repo, pr), discriminator, marker)
    if existing is not None:
        print(f"↻ Updating existing PR comment {existing}")
        return _update_pr_comment(repo, existing, body_path)
    print(f"+ Creating new PR comment on #{pr}")
    return _create_pr_comment(pr, body_path)


# ── aggregate PR summary ─────────────────────────────────────────


def _summary_pr_context() -> tuple[str, int, str | None] | None:
    """(repo, pr, head_sha) for the summary, across event types.

    A `workflow_run` payload carries the PR list and head sha of the run
    that just finished; a `pull_request` payload carries them directly.
    """
    repo = _repo_slug()
    if not repo:
        print("❌ $GITHUB_REPOSITORY is not set", file=sys.stderr)
        return None

    pr = _pr_number_from_event()
    event = _event_payload()
    run = event.get("workflow_run")
    if pr is None and isinstance(run, dict):
        prs = run.get("pull_requests")
        if isinstance(prs, list) and prs and isinstance(prs[0], dict):
            number = prs[0].get("number")
            if isinstance(number, int):
                pr = number
    if pr is None:
        print(
            "ℹ️  No pull request associated with this event, so nothing to summarise",
            file=sys.stderr,
        )
        return None
    return repo, pr, _head_sha()


def _list_workflow_runs(repo: str, head_sha: str) -> list[dict]:
    """Every workflow run recorded against a commit."""
    result = _gh(
        "api",
        "--paginate",
        f"repos/{repo}/actions/runs?head_sha={head_sha}&per_page=100",
        "--jq",
        ".workflow_runs[]",
        capture=True,
    )
    if result.returncode != 0:
        print(f"❌ Could not list workflow runs for {head_sha}", file=sys.stderr)
        return []
    runs = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(entry, dict):
            runs.append(entry)
    return runs


def emit_pr_summary() -> int:
    """Upsert the single aggregate status comment for the PR.

    Reads the workflow runs GitHub already recorded for the head commit,
    so it needs no coordination with the individual check workflows and
    cannot race them into a half-written comment.
    """
    if not _gh_available():
        print("❌ gh CLI is not available, and the PR summary needs it", file=sys.stderr)
        return 1

    context = _summary_pr_context()
    if context is None:
        return 0  # not a PR: nothing to do, and not an error
    repo, pr, head_sha = context
    if not head_sha:
        print("❌ Could not resolve the head commit for this PR", file=sys.stderr)
        return 1

    runs = _list_workflow_runs(repo, head_sha)
    body = comment_mod.build_summary(runs, head_sha=head_sha)
    body_path = Path(".ss/reports/pr-summary.md")
    body_path.parent.mkdir(parents=True, exist_ok=True)
    body_path.write_text(body)

    existing = _find_existing_pr_comment(
        _list_pr_comments(repo, pr), comment_mod.SUMMARY_MARKER
    )
    if existing is not None:
        print(f"↻ Updating PR summary comment {existing}")
        return _update_pr_comment(repo, existing, body_path)
    print(f"+ Creating PR summary comment on #{pr}")
    return _create_pr_comment(pr, body_path)


# ── Issue emission ───────────────────────────────────────────────


_BRAND_LABEL = "slopstopper"


def _augment_labels(labels: list[str]) -> list[str]:
    """Prepend the slopstopper brand label if not already present.

    Idempotent, so it is safe to call repeatedly. Single insertion point so every
    open/close path uses the same label set for dedup.
    """
    if _BRAND_LABEL in labels:
        return labels
    return [_BRAND_LABEL, *labels]


def _list_open_issues(*args: str) -> list[dict]:
    result = _gh("issue", "list", "--state", "open", *args, capture=True)
    if result.returncode != 0:
        return []
    try:
        data = json.loads(result.stdout or "[]")
    except json.JSONDecodeError:
        return []
    return [item for item in data if isinstance(item, dict)] if isinstance(data, list) else []


def _find_existing_issue(labels: list[str], check_name: str | None = None) -> int | None:
    """The open issue this check owns: the first with every label, else the
    first whose body carries the check's marker.

    The marker pass is what finds an issue created without labels (see
    `_create_issue`), so a repo that can't take labels still gets one
    issue per check, updated and closed like any other.
    """
    label_args: list[str] = []
    for lbl in labels:
        label_args += ["--label", lbl]
    for item in _list_open_issues(*label_args, "--json", "number"):
        if isinstance(item.get("number"), int):
            return item["number"]
    if not check_name:
        return None
    marker = f"<!-- slopstopper:check={check_name} -->"
    for item in _list_open_issues("--limit", "100", "--json", "number,body"):
        if isinstance(item.get("number"), int) and marker in (item.get("body") or ""):
            return item["number"]
    return None


_LABEL_COLOR = "5319e7"


def _ensure_labels(labels: list[str]) -> None:
    """Create each label the repo doesn't have yet. Existing labels are left as they are."""
    result = _gh("label", "list", "--limit", "1000", "--json", "name", capture=True)
    if result.returncode != 0:
        return
    try:
        existing = {str(entry.get("name", "")).lower() for entry in json.loads(result.stdout or "[]")}
    except (json.JSONDecodeError, AttributeError):
        return
    for lbl in labels:
        if lbl.lower() not in existing:
            print(f"+ Creating label {lbl!r}")
            _gh(
                "label", "create", lbl,
                "--color", _LABEL_COLOR,
                "--description", "Raised by a slopstopper check",
                capture=True,
            )


def _create_issue(title: str, body_path: Path, labels: list[str]) -> int:
    _ensure_labels(labels)
    cmd: list[str] = ["issue", "create", "--title", title, "--body-file", str(body_path)]
    labelled = list(cmd)
    for lbl in labels:
        labelled += ["--label", lbl]
    result = _gh(*labelled, capture=True)
    if result.returncode != 0 and labels and "label" in (result.stderr or "").lower():
        print(
            f"⚠️  Could not apply labels {labels} ({(result.stderr or '').strip()}); "
            "creating the issue without them",
            file=sys.stderr,
        )
        result = _gh(*cmd, capture=True)
    if result.stdout:
        print(result.stdout, end="")
    if result.returncode != 0 and result.stderr:
        print(result.stderr, end="", file=sys.stderr)
    return result.returncode


def _update_issue_body(number: int, body_path: Path) -> int:
    return _gh("issue", "edit", str(number), "--body-file", str(body_path)).returncode


def _comment_issue(number: int, body: str) -> int:
    return _gh("issue", "comment", str(number), "--body", body).returncode


WORKFLOW_MARKER = "<!-- slopstopper:workflow={file} -->"


def _workflow_file() -> str:
    """The running workflow's file name (`ss-reliability-smoke-tests.yml`), from
    GITHUB_WORKFLOW_REF (`owner/repo/.github/workflows/<file>@<ref>`); "" outside Actions."""
    ref = os.environ.get("GITHUB_WORKFLOW_REF", "")
    return ref.split("@", 1)[0].rsplit("/", 1)[-1] if ref else ""


def _commit_sha() -> str:
    return os.environ.get("GITHUB_SHA", "")


def emit_issue(
    report_path: Path,
    title: str,
    labels: list[str],
    followup: str,
    *,
    check_name: str | None = None,
) -> int:
    """Create a new issue with the report, or update the existing one and post a follow-up comment.

    The body is the report content with a `Commit: <sha>` footer and, when
    `check_name` is provided, a hidden `<!-- slopstopper:check=... -->`
    marker. Labels are augmented with the `slopstopper` brand label.
    """
    if not _gh_available():
        print("❌ gh CLI is not available, and --target issue needs it", file=sys.stderr)
        return 1
    if not report_path.exists():
        print(f"❌ Report not found at {report_path}", file=sys.stderr)
        return 1

    labels = _augment_labels(labels)
    sha = _commit_sha()
    body = report_path.read_text()

    footer_lines: list[str] = []
    if sha:
        footer_lines.append(f"*Commit: {sha}*")
    if check_name:
        footer_lines.append(f"<!-- slopstopper:check={check_name} -->")
    workflow = _workflow_file()
    if workflow:
        footer_lines.append(WORKFLOW_MARKER.format(file=workflow))
    footer = ("\n\n---\n" + "\n".join(footer_lines) + "\n") if footer_lines else "\n"
    body_with_footer = body + footer

    body_path = report_path.with_suffix(".issue-body.md")
    body_path.write_text(body_with_footer)

    existing = _find_existing_issue(labels, check_name)
    if existing is not None:
        print(f"↻ Updating existing issue #{existing}")
        rc = _update_issue_body(existing, body_path)
        if rc == 0 and sha:
            _comment_issue(existing, f"{followup} {sha}")
        return rc
    print("+ Creating new issue")
    return _create_issue(title, body_path, labels)


# ── issue close ──────────────────────────────────────────────────


_DEFAULT_CLOSE_COMMENT = "✅ Check is now passing on `main`. Closing automatically."


def _close_issue(labels: list[str], close_comment: str, check_name: str | None = None) -> int:
    """Comment + close the open issue this check owns. No-op if none exists.

    Used by `emit --on-pass=close` as the post-success twin of
    `emit_issue`'s post-failure open path. Same lookup (labels, then the
    body marker) via `_find_existing_issue`, so a check only ever closes
    the issue it would have re-opened.
    """
    if not _gh_available():
        print("❌ gh CLI is not available, and --on-pass=close needs it", file=sys.stderr)
        return 1
    labels = _augment_labels(labels)
    existing = _find_existing_issue(labels, check_name)
    if existing is None:
        print("· No open issue to close")
        return 0
    print(f"× Closing issue #{existing}")
    rc = _comment_issue(existing, close_comment)
    if rc != 0:
        return rc
    return _gh("issue", "close", str(existing)).returncode


# ── the last run's exit code ─────────────────────────────────────
#
# `slopstopper run` records each check's exit code here, and `emit
# --target issue` reads it back. Exit 2 means "could not run", which is
# never a finding, so it must neither open a tracking issue nor close one
# as clean. Recording it in the CLI puts that rule in one place instead of in
# every workflow's `if:` (the run and emit steps share a job workspace).

LAST_EXIT_DIR = Path(".ss/reports/.last-exit")


def _last_exit_path(check_name: str) -> Path:
    return LAST_EXIT_DIR / check_name.replace(":", "-")


def record_exit(check_name: str, rc: int) -> None:
    """Remember `check_name`'s exit code for a later `emit`. Best effort."""
    try:
        LAST_EXIT_DIR.mkdir(parents=True, exist_ok=True)
        _last_exit_path(check_name).write_text(f"{rc}\n")
    except OSError:
        pass


def last_exit(check_name: str) -> int | None:
    """The exit code `slopstopper run` recorded for `check_name`, if any."""
    try:
        return int(_last_exit_path(check_name).read_text().strip())
    except (OSError, ValueError):
        return None


# ── public dispatcher ────────────────────────────────────────────


def emit(
    target: str,
    meta: dict,
    *,
    check_name: str | None = None,
    on_pass: str | None = None,
    status: str = "fail",
) -> int:
    """Route --target {pr-comment, issue} to the corresponding emitter.

    meta is the check's META dict (see module docstring). check_name is
    the `category:name` identifier used in the issue body's brand marker.
    on_pass='close' (only valid with target='issue') flips the issue
    branch from open/update to comment-and-close. It is the post-success
    twin of the post-failure open path.
    """
    report_path = Path(meta["report_path"])
    if target == "pr-comment":
        return emit_pr_comment(
            report_path,
            meta["comment_discriminator"],
            check_name=check_name,
            status=status,
            on_pass=on_pass,
        )
    if target == "issue":
        if check_name and last_exit(check_name) == 2:
            print(
                f"· {check_name} could not run (exit 2). That is not a finding, so no issue "
                "is opened, updated or closed. See the run step's output."
            )
            return 0
        if on_pass == "close":
            return _close_issue(
                meta["issue_labels"],
                meta.get("issue_close_comment", _DEFAULT_CLOSE_COMMENT),
                check_name=check_name,
            )
        return emit_issue(
            report_path,
            meta["issue_title"],
            meta["issue_labels"],
            meta["issue_followup"],
            check_name=check_name,
        )
    print(f"❌ Unknown emit target: {target!r}", file=sys.stderr)
    return 2
