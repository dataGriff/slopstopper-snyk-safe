"""Tests for PR-comment rendering (comment.py).

Two things carry the user-visible contract: a comment must never claim a
verdict the workflow didn't report, and a summary must never read as a
pass while checks are unfinished or a run is missing.
"""

from __future__ import annotations

import pytest

from slopstopper import comment


FAILING_REPORT = """# 🔎 SEO / Social-Share Metatag Report

**Overall:** ❌ FAIL

## ❌ https://example.com/pricing

**Issues:**
- ❌ og:image is missing
- ❌ canonical points at another domain

**Notes:**
- ⚠️  meta description is 173 chars
"""

PASSING_REPORT = """# 🔎 SEO / Social-Share Metatag Report

**Overall:** ✅ PASS

## ✅ https://example.com/
"""


def _run(filename, conclusion="success", status="completed", created="2026-09-17T12:00:00Z"):
    return {
        "path": f".github/workflows/{filename}",
        "conclusion": conclusion,
        "status": status,
        "created_at": created,
        "html_url": f"https://github.com/o/r/actions/runs/{filename}",
    }


# ── display names ────────────────────────────────────────────────


def test_display_name_comes_from_the_badge_label():
    """One naming source: badge, comment and summary agree."""
    assert comment.display_name("reliability:seo") == "SEO"
    assert comment.display_name("security:dependencies") == "Dependency CVEs"
    assert comment.display_name("reliability:cwv") == "Core Web Vitals"


def test_display_name_falls_back_to_the_check_key():
    assert comment.display_name("no-such:check") == "no-such:check"


# ── failure extraction ───────────────────────────────────────────


def test_extract_failures_takes_issue_bullets_only():
    failures = comment.extract_failures(FAILING_REPORT)
    assert failures == ["og:image is missing", "canonical points at another domain"]


def test_extract_failures_ignores_advisory_notes():
    assert comment.extract_failures("- ⚠️  just a note\n") == []


def test_extract_failures_handles_indented_bullets():
    assert comment.extract_failures("      - ❌ nested finding") == ["nested finding"]


def test_extract_failures_empty_for_a_report_without_the_convention():
    assert comment.extract_failures("| CCN | 17 |\nSome prose.") == []


# ── per-check body ───────────────────────────────────────────────


def test_pass_body_is_two_visible_lines():
    body = comment.build_body("reliability:seo", PASSING_REPORT, status="pass")
    visible = body.split("<details>")[0].strip().splitlines()
    assert visible == ["### ✅ SEO: passed"]


def test_fail_body_leads_with_the_count_and_lists_failures():
    body = comment.build_body("reliability:seo", FAILING_REPORT, status="fail")
    head = body.split("<details>")[0]
    assert head.startswith("### ❌ SEO: 2 issues")
    assert "- og:image is missing" in head


def test_fail_body_singular_for_one_issue():
    report = "# R\n- ❌ the one thing\n"
    body = comment.build_body("hygiene:complexity", report, status="fail")
    assert ": 1 issue" in body
    assert "1 issues" not in body


def test_fail_body_without_extractable_failures_still_says_failed():
    """Checks that don't use the bullet convention must not render as passing."""
    body = comment.build_body("security:sast", "# SAST\n\nSome prose report.", status="fail")
    assert body.split("<details>")[0].strip() == "### ❌ SAST: failed"


def test_warn_status_does_not_claim_a_failure():
    """docs-size exits 0 while reporting thresholds exceeded, so a ❌ here
    would contradict the ✅ its green job gets in the summary table."""
    body = comment.build_body("hygiene:docs-size", "# Docs\n\n⚠️  27 files exceeds 25\n", status="warn")
    head = body.split("<details>")[0].strip()
    assert head == "### ⚠️ Docs Size: has alerts"
    assert "❌" not in head
    assert "passed" not in head


def test_warn_counts_bullets_as_alerts():
    body = comment.build_body("hygiene:docs-size", "# D\n- ❌ a\n- ❌ b\n", status="warn")
    assert "⚠️ Docs Size: 2 alerts" in body


def test_long_failure_lists_are_truncated():
    report = "# R\n" + "\n".join(f"- ❌ finding {i}" for i in range(20))
    head = comment.build_body("reliability:seo", report, status="fail").split("<details>")[0]
    assert head.count("\n- ") == comment.MAX_VISIBLE_FAILURES + 1  # + the "and N more" line
    assert f"…and {20 - comment.MAX_VISIBLE_FAILURES} more" in head


def test_body_folds_the_whole_report():
    body = comment.build_body("reliability:seo", FAILING_REPORT, status="fail")
    assert "<details><summary>" in body
    assert "</details>" in body
    # Every report line survives, just demoted and folded.
    assert "canonical points at another domain" in body.split("<details>")[1]


def test_body_demotes_report_headings():
    """The report's H1 must not compete with the comment's own verdict line."""
    body = comment.build_body("reliability:seo", FAILING_REPORT, status="fail")
    folded = body.split("<details>")[1]
    assert "## 🔎 SEO / Social-Share Metatag Report" in folded
    assert "\n# 🔎" not in folded


def test_body_carries_the_marker_and_provenance():
    body = comment.build_body(
        "reliability:seo", PASSING_REPORT, status="pass",
        head_sha="abc1234def", run_url="https://github.com/o/r/actions/runs/9",
    )
    assert comment.CHECK_MARKER.format(name="reliability:seo") in body
    assert "head `abc1234`" in body
    assert "[run](https://github.com/o/r/actions/runs/9)" in body


def test_body_omits_the_run_link_when_not_in_actions():
    body = comment.build_body("reliability:seo", PASSING_REPORT, status="pass")
    assert "[run](" not in body
    assert "head `unknown`" in body


# ── aggregate summary ────────────────────────────────────────────


def test_summary_all_green_lists_grouped_names():
    runs = [_run("ss-security-sast-check.yml"), _run("ss-reliability-seo-check.yml")]
    out = comment.build_summary(runs, head_sha="abc1234def")
    assert out.startswith("## ✅ SlopStopper: all 2 checks passed")
    assert "🔒 Security: SAST" in out
    assert "✅ Reliability: SEO" in out
    assert comment.SUMMARY_MARKER in out


def test_summary_leads_with_failures():
    runs = [
        _run("ss-security-sast-check.yml"),
        _run("ss-reliability-seo-check.yml", conclusion="failure"),
        _run("ss-hygiene-complexity-check.yml", conclusion="failure"),
    ]
    out = comment.build_summary(runs, head_sha="abc1234def")
    assert out.startswith("## ❌ SlopStopper: 2 of 3 checks failed")
    head = out.split("<details>")[0]
    assert "**SEO**" in head and "**Complexity**" in head
    assert "SAST" not in head, "passing checks belong behind the fold"
    assert "The other 1 checks" in out


def test_summary_noun_agrees_with_the_total_not_the_failure_count():
    one = [_run("ss-reliability-seo-check.yml", conclusion="failure")]
    assert "1 of 1 check failed" in comment.build_summary(one)

    several = [
        _run("ss-reliability-seo-check.yml", conclusion="failure"),
        _run("ss-security-sast-check.yml"),
        _run("ss-hygiene-complexity-check.yml"),
    ]
    assert "1 of 3 checks failed" in comment.build_summary(several)


def test_summary_reports_in_progress_rather_than_claiming_a_pass():
    """A half-finished suite must not read as green."""
    runs = [
        _run("ss-security-sast-check.yml"),
        _run("ss-reliability-seo-check.yml", conclusion=None, status="in_progress"),
    ]
    out = comment.build_summary(runs)
    assert out.startswith("## ⏳ SlopStopper: 1 of 2 still running")
    assert "⏳ SEO" in out


def test_summary_failure_wins_over_still_running():
    runs = [
        _run("ss-security-sast-check.yml", conclusion="failure"),
        _run("ss-reliability-seo-check.yml", conclusion=None, status="in_progress"),
    ]
    assert comment.build_summary(runs).startswith("## ❌ SlopStopper")


def test_summary_skips_non_check_workflows():
    runs = [
        _run("ss-security-sast-check.yml"),
        _run("ss-release.yml"),
        _run("ss-pr-summary.yml"),
        _run("ss-workflow-failure-issue.yml"),
    ]
    out = comment.build_summary(runs)
    assert "all 1 checks passed" in out
    assert "Release" not in out


def test_summary_ignores_foreign_workflows():
    runs = [_run("ss-security-sast-check.yml"), _run("my-own-deploy.yml")]
    assert "all 1 checks passed" in comment.build_summary(runs)


def test_summary_keeps_only_the_newest_run_per_workflow():
    """A re-run supersedes the failure it replaced."""
    runs = [
        _run("ss-reliability-seo-check.yml", conclusion="failure", created="2026-09-17T10:00:00Z"),
        _run("ss-reliability-seo-check.yml", conclusion="success", created="2026-09-17T12:00:00Z"),
    ]
    out = comment.build_summary(runs)
    assert out.startswith("## ✅ SlopStopper: all 1 checks passed")


def test_summary_handles_no_runs():
    out = comment.build_summary([])
    assert "no check runs found" in out


@pytest.mark.parametrize(
    "conclusion,expected",
    [("skipped", "⏭️"), ("cancelled", "⚪"), ("neutral", "⚪"), ("timed_out", "❌")],
)
def test_summary_icons_per_conclusion(conclusion, expected):
    runs = [_run("ss-security-sast-check.yml", conclusion=conclusion)]
    assert expected in comment.build_summary(runs)


def test_skipped_runs_are_neither_failures_nor_passes():
    runs = [_run("ss-security-sast-check.yml", conclusion="skipped")]
    out = comment.build_summary(runs)
    assert "failed" not in out
    assert "passed" not in out
    assert "1 skipped or cancelled" in out


def test_a_mix_of_passes_and_skips_reports_both():
    runs = [
        _run("ss-security-sast-check.yml"),
        _run("ss-reliability-seo-check.yml", conclusion="skipped"),
    ]
    out = comment.build_summary(runs)
    assert "1 passed, 1 skipped or cancelled" in out
    assert "✅ SAST" in out and "⏭️ SEO" in out
