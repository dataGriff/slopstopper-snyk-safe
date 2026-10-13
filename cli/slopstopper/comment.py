"""PR-comment bodies: compact per-check verdicts and the aggregate summary.

Reports are written as standalone documents with an H1, an overall
verdict, per-item sections, a folded evidence table and a "How to Fix"
section. That is the right shape for a file you open deliberately, and the
wrong shape for nineteen of them stacked on a pull request: on an
all-passing run the suite used to post ~390 lines of markdown across 19
comment cards, so whether anything failed, the one thing a reviewer wants
to know, was the hardest thing to find.

This module renders what actually goes in a comment:

  build_body()     one check → a verdict line, the failing items, and the
                   full report folded away. Driven by the check's exit
                   status, which the workflow passes in, not by parsing
                   the report for a verdict.
  build_summary()  every check → one table, from the workflow runs GitHub
                   already recorded for the head SHA. A green PR gets a
                   single three-line comment; a red one leads with the
                   failures.

Naming is not re-declared here. A check's display label comes from
`profiles.CHECK_WORKFLOWS` (check → workflow file) chained through
`badges.WORKFLOW_DISPLAY` (workflow file → human label), so the label on
a PR comment, on a README badge and in the summary table are the same
string from the same place.
"""

from __future__ import annotations

import re

from slopstopper import badges, profiles
from slopstopper.checks import _report

# Hidden markers. The per-check marker makes comment lookup exact rather
# than "does the body happen to contain the report's H1 text", and the
# summary marker keys the single aggregate comment.
CHECK_MARKER = "<!-- slopstopper:check={name} -->"
SUMMARY_MARKER = "<!-- slopstopper:pr-summary -->"

# Failure bullets in a report body. The in-CLI checks render each issue as
# `- ❌ <text>`; checks that don't follow the convention simply contribute
# no bullets and the comment falls back to the verdict line plus the
# folded report.
_FAILURE_BULLET_RE = re.compile(r"^\s*-\s*❌\s*(.+?)\s*$")

# How many failing items to show before folding the rest away. Past this,
# the comment stops being a summary and the report is the better read.
MAX_VISIBLE_FAILURES = 8

GROUP_LABELS = {
    "security": "🔒 Security",
    "hygiene": "🧹 Hygiene",
    "reliability": "✅ Reliability",
    "operational": "🤖 Operational",
}
GROUP_ORDER = ["security", "hygiene", "reliability", "operational"]

# Workflows that are not checks: they do work *about* the repo rather than
# reporting on it, so a status row for them is noise in a review summary.
SUMMARY_EXCLUDED = {
    "ss-pr-summary.yml",
    "ss-release.yml",
    "ss-workflow-failure-issue.yml",
}

# GitHub conclusion → (icon, counts_as). `None` conclusion means the run
# is still going; that is reported rather than hidden, so a half-finished
# summary can't read as a pass.
_CONCLUSION_ICONS = {
    "success": "✅",
    "failure": "❌",
    "timed_out": "❌",
    "startup_failure": "❌",
    "action_required": "❌",
    "cancelled": "⚪",
    "skipped": "⏭️",
    "neutral": "⚪",
    "stale": "⚪",
}
_FAILED_CONCLUSIONS = {"failure", "timed_out", "startup_failure", "action_required"}


# ── shared helpers ───────────────────────────────────────────────


def display_name(check_name: str) -> str:
    """Human label for a check, via its workflow's badge label."""
    workflow = profiles.workflow_for(check_name)
    if workflow is None:
        return check_name
    return badges.group_workflow(workflow)[1]


def _short_sha(sha: str | None) -> str:
    return sha[:7] if sha else "unknown"


def _now() -> str:
    return _report.generated_at(precision="minutes")


# ── per-check comment body ───────────────────────────────────────


def extract_failures(report_md: str) -> list[str]:
    """The `- ❌ …` bullets from a report body, in order."""
    out = []
    for line in report_md.splitlines():
        match = _FAILURE_BULLET_RE.match(line)
        if match:
            out.append(match.group(1))
    return out


def _demote_headings(report_md: str) -> str:
    """Push every heading down a level so the folded report doesn't
    compete with the comment's own verdict heading."""
    return re.sub(r"(?m)^(#{1,5})(?= )", r"#\1", report_md)


def _failure_lines(failures: list[str]) -> list[str]:
    if not failures:
        return []
    visible = failures[:MAX_VISIBLE_FAILURES]
    lines = [f"- {item}" for item in visible]
    hidden = len(failures) - len(visible)
    if hidden > 0:
        lines.append(f"- …and {hidden} more (see the full report below)")
    lines.append("")
    return lines


def _headline(status: str, label: str, failures: list[str]) -> str:
    """The comment's first line.

    `warn` exists for the advisory checks: `hygiene:docs-size` reports
    thresholds exceeded while deliberately exiting 0, so a ❌ here would
    contradict the ✅ the summary table shows for its green job. ⚠️ says
    "look at this" without claiming the check failed.
    """
    if status == "pass":
        return f"### ✅ {label}: passed"
    icon, noun = ("⚠️", "alert") if status == "warn" else ("❌", "issue")
    count = len(failures)
    if count == 1:
        return f"### {icon} {label}: 1 {noun}"
    if count > 1:
        return f"### {icon} {label}: {count} {noun}s"
    return f"### {icon} {label}: {'has alerts' if status == 'warn' else 'failed'}"


def build_body(
    check_name: str,
    report_md: str,
    *,
    status: str,
    head_sha: str | None = None,
    run_url: str | None = None,
) -> str:
    """Render the PR comment for one check.

    `status` is the check's own exit status as the workflow saw it
    ('pass' / 'fail'). It is deliberately not inferred from the report
    text, which varies per check and, for the advisory checks, says
    "THRESHOLDS EXCEEDED" on a run that exits 0.
    """
    label = display_name(check_name)
    failures = extract_failures(report_md) if status != "pass" else []

    lines = [_headline(status, label, failures), ""]
    lines.extend(_failure_lines(failures))

    summary_label = "Full report" if status == "pass" else "Full report and evidence"

    lines.extend(
        [
            f"<details><summary>{summary_label}</summary>",
            "",
            _demote_headings(report_md).rstrip(),
            "",
            "</details>",
            "",
        ]
    )

    footer = f"<sub>slopstopper · `{check_name}` · head `{_short_sha(head_sha)}`"
    if run_url:
        footer += f" · [run]({run_url})"
    footer += "</sub>"
    lines.append(footer)
    lines.append(CHECK_MARKER.format(name=check_name))
    return "\n".join(lines) + "\n"


# ── aggregate summary comment ────────────────────────────────────


def _newest_run_per_workflow(runs: list[dict]) -> list[dict]:
    """The newest run per workflow file, so re-runs supersede older ones."""
    newest: dict[str, dict] = {}
    for run in runs:
        path = str(run.get("path") or "")
        filename = path.rsplit("/", 1)[-1]
        if not filename.startswith("ss-") or filename in SUMMARY_EXCLUDED:
            continue
        current = newest.get(filename)
        if current is None or str(run.get("created_at") or "") > str(current.get("created_at") or ""):
            newest[filename] = {**run, "_filename": filename}
    return list(newest.values())


def _classify(run: dict) -> tuple[str, str]:
    """(icon, bucket) for a run. bucket ∈ failed / passed / other / running."""
    if run.get("status") != "completed":
        return "⏳", "running"
    conclusion = str(run.get("conclusion") or "")
    icon = _CONCLUSION_ICONS.get(conclusion, "⚪")
    if conclusion in _FAILED_CONCLUSIONS:
        return icon, "failed"
    if conclusion == "success":
        return icon, "passed"
    return icon, "other"


def _group_rows(runs: list[dict]) -> dict[str, list[tuple[str, str, dict]]]:
    grouped: dict[str, list[tuple[str, str, dict]]] = {}
    for run in runs:
        group, label = badges.group_workflow(run["_filename"])
        icon, _bucket = _classify(run)
        grouped.setdefault(group, []).append((icon, label, run))
    for rows in grouped.values():
        rows.sort(key=lambda row: row[1].lower())
    return grouped


def _summary_heading(counts: dict[str, int], total: int) -> str:
    """The one line a reviewer reads. It must never overstate the result:
    a skipped or cancelled run is not a pass, and an unfinished suite is
    not a green one."""
    if counts["failed"]:
        # The noun agrees with the total, not the failure count:
        # "1 of 3 checks failed", "1 of 1 check failed".
        word = "check" if total == 1 else "checks"
        return f"## ❌ SlopStopper: {counts['failed']} of {total} {word} failed"
    if counts["running"]:
        return f"## ⏳ SlopStopper: {counts['running']} of {total} still running"
    if total == 0:
        return "## SlopStopper: no check runs found for this commit"
    if counts["other"] and not counts["passed"]:
        return f"## ⚪ SlopStopper: no checks ran ({counts['other']} skipped or cancelled)"
    if counts["other"]:
        return (
            f"## ✅ SlopStopper: {counts['passed']} passed, "
            f"{counts['other']} skipped or cancelled"
        )
    return f"## ✅ SlopStopper: all {total} checks passed"


def _failure_table(rows: list[tuple[str, str, dict]]) -> list[str]:
    lines = ["| | Check | |", "|---|---|---|"]
    for icon, label, run in rows:
        url = run.get("html_url") or ""
        link = f"[logs]({url})" if url else ""
        lines.append(f"| {icon} | **{label}** | {link} |")
    lines.append("")
    return lines


def _group_lines(grouped: dict[str, list[tuple[str, str, dict]]], with_icons: bool) -> list[str]:
    lines = []
    for group in GROUP_ORDER:
        rows = grouped.get(group)
        if not rows:
            continue
        if with_icons:
            items = " · ".join(f"{icon} {label}" for icon, label, _ in rows)
        else:
            items = " · ".join(label for _, label, _ in rows)
        lines.append(f"{GROUP_LABELS[group]}: {items}")
        lines.append("")
    return lines


def _render_failures_first(
    grouped: dict[str, list[tuple[str, str, dict]]], failed_files: list[str]
) -> list[str]:
    """Failing checks as a table up top; everything else folded below."""
    failing_rows = [
        row for rows in grouped.values() for row in rows if row[2]["_filename"] in failed_files
    ]
    failing_rows.sort(key=lambda row: row[1].lower())
    lines = _failure_table(failing_rows)

    rest = {
        group: [r for r in rows if r[2]["_filename"] not in failed_files]
        for group, rows in grouped.items()
    }
    rest = {group: rows for group, rows in rest.items() if rows}
    if not rest:
        return lines

    remaining = sum(len(rows) for rows in rest.values())
    lines.append(f"<details><summary>The other {remaining} checks</summary>")
    lines.append("")
    lines.extend(_group_lines(rest, with_icons=True))
    lines.append("</details>")
    lines.append("")
    return lines


def build_summary(runs: list[dict], *, head_sha: str | None = None) -> str:
    """Render the single aggregate status comment from workflow runs."""
    considered = _newest_run_per_workflow(runs)
    buckets = {run["_filename"]: _classify(run)[1] for run in considered}
    counts = {
        bucket: sum(1 for value in buckets.values() if value == bucket)
        for bucket in ("failed", "running", "other", "passed")
    }
    failed_files = [name for name, bucket in buckets.items() if bucket == "failed"]

    lines = [_summary_heading(counts, len(considered)), ""]
    grouped = _group_rows(considered)
    if failed_files:
        lines.extend(_render_failures_first(grouped, failed_files))
    else:
        # Icons go in as soon as anything is not a plain pass, so a skipped
        # or still-running check is visibly different from a green one.
        everything_passed = counts["passed"] == len(considered)
        lines.extend(_group_lines(grouped, with_icons=not everything_passed))

    lines.append(f"<sub>head `{_short_sha(head_sha)}` · updated {_now()}</sub>")
    lines.append(SUMMARY_MARKER)
    return "\n".join(lines) + "\n"
