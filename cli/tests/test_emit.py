"""Tests for slopstopper.emit: PR comment + issue emission via gh CLI."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from slopstopper import emit


SAMPLE_META = {
    "report_path": "report.md",
    "comment_discriminator": "📚 Test Report",
    "issue_title": "📚 Test Thresholds Exceeded",
    "issue_labels": ["test-label", "maintenance"],
    "issue_followup": "🔔 Test thresholds exceeded again in commit",
}


# ── helpers ──────────────────────────────────────────────────────


def test_gh_available_via_which(monkeypatch):
    monkeypatch.setattr(emit.shutil, "which", lambda _: "/usr/bin/gh")
    assert emit._gh_available() is True
    monkeypatch.setattr(emit.shutil, "which", lambda _: None)
    assert emit._gh_available() is False


def test_repo_slug_reads_env(monkeypatch):
    monkeypatch.setenv("GITHUB_REPOSITORY", "owner/repo")
    assert emit._repo_slug() == "owner/repo"


def test_repo_slug_none_when_unset(monkeypatch):
    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
    assert emit._repo_slug() is None


def test_pr_number_from_event_reads_payload(monkeypatch, tmp_path):
    event = tmp_path / "event.json"
    event.write_text(json.dumps({"pull_request": {"number": 42}}))
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event))
    assert emit._pr_number_from_event() == 42


def test_pr_number_from_event_none_for_push(monkeypatch, tmp_path):
    event = tmp_path / "event.json"
    event.write_text(json.dumps({"ref": "refs/heads/main"}))
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event))
    assert emit._pr_number_from_event() is None


def test_pr_number_from_event_none_when_path_unset(monkeypatch):
    monkeypatch.delenv("GITHUB_EVENT_PATH", raising=False)
    assert emit._pr_number_from_event() is None


def test_pr_number_from_event_handles_malformed_json(monkeypatch, tmp_path):
    event = tmp_path / "event.json"
    event.write_text("not json")
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event))
    assert emit._pr_number_from_event() is None


# ── PR comment ───────────────────────────────────────────────────


def test_find_existing_pr_comment_matches_bot_discriminator():
    comments = [
        {"id": 1, "user": {"type": "User"}, "body": "📚 Test Report (human-typed)"},
        {"id": 2, "user": {"type": "Bot"}, "body": "Some other bot comment"},
        {"id": 3, "user": {"type": "Bot"}, "body": "📚 Test Report\nfull body…"},
    ]
    assert emit._find_existing_pr_comment(comments, "📚 Test Report") == 3


def test_find_existing_pr_comment_returns_none_when_no_match():
    comments = [{"id": 1, "user": {"type": "Bot"}, "body": "different content"}]
    assert emit._find_existing_pr_comment(comments, "missing") is None


def test_emit_pr_comment_fails_when_gh_missing(monkeypatch, isolated_cwd, capsys):
    monkeypatch.setattr(emit, "_gh_available", lambda: False)
    rc = emit.emit_pr_comment(Path("report.md"), "discrim")
    assert rc == 1
    assert "gh CLI is not available" in capsys.readouterr().err


def test_emit_pr_comment_fails_when_report_missing(monkeypatch, isolated_cwd, capsys):
    """A missing report is still an error on the posting path.

    The check now runs *after* PR resolution, because `--on-pass=delete`
    has to be able to clean up a stale comment when the report is gone.
    """
    monkeypatch.setattr(emit, "_gh_available", lambda: True)
    monkeypatch.setattr(emit, "_repo_slug", lambda: "owner/repo")
    monkeypatch.setattr(emit, "_pr_number_from_event", lambda: 42)
    rc = emit.emit_pr_comment(Path("nonexistent.md"), "discrim", check_name="hygiene:docs-size")
    assert rc == 1
    assert "Report not found" in capsys.readouterr().err


def test_emit_pr_comment_fails_when_no_pr_number(monkeypatch, isolated_cwd, capsys):
    Path("report.md").write_text("body")
    monkeypatch.setattr(emit, "_gh_available", lambda: True)
    monkeypatch.setattr(emit, "_repo_slug", lambda: "owner/repo")
    monkeypatch.setattr(emit, "_pr_number_from_event", lambda: None)
    rc = emit.emit_pr_comment(Path("report.md"), "discrim")
    assert rc == 1
    assert "No PR detected" in capsys.readouterr().err


def test_emit_pr_comment_creates_new_when_no_existing(monkeypatch, isolated_cwd, capsys):
    Path("report.md").write_text("body")
    monkeypatch.setattr(emit, "_gh_available", lambda: True)
    monkeypatch.setattr(emit, "_repo_slug", lambda: "owner/repo")
    monkeypatch.setattr(emit, "_pr_number_from_event", lambda: 99)
    monkeypatch.setattr(emit, "_list_pr_comments", lambda r, p: [])

    captured: dict = {}

    def fake_gh(*args, capture=False):
        captured["args"] = args
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(emit, "_gh", fake_gh)
    rc = emit.emit_pr_comment(Path("report.md"), "discrim")
    assert rc == 0
    assert "pr" in captured["args"] and "comment" in captured["args"]
    out = capsys.readouterr().out
    assert "Creating new PR comment on #99" in out


def test_emit_pr_comment_updates_existing(monkeypatch, isolated_cwd, capsys):
    Path("report.md").write_text("body")
    monkeypatch.setattr(emit, "_gh_available", lambda: True)
    monkeypatch.setattr(emit, "_repo_slug", lambda: "owner/repo")
    monkeypatch.setattr(emit, "_pr_number_from_event", lambda: 99)
    monkeypatch.setattr(
        emit, "_list_pr_comments",
        lambda r, p: [{"id": 555, "user": {"type": "Bot"}, "body": "📚 Test Report\nold body"}],
    )

    captured: dict = {}

    def fake_gh(*args, capture=False):
        captured["args"] = args
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(emit, "_gh", fake_gh)
    rc = emit.emit_pr_comment(Path("report.md"), "📚 Test Report")
    assert rc == 0
    assert "PATCH" in captured["args"]
    assert "repos/owner/repo/issues/comments/555" in captured["args"]
    out = capsys.readouterr().out
    assert "Updating existing PR comment 555" in out


# ── issue ────────────────────────────────────────────────────────


def test_find_existing_issue_returns_first_number(monkeypatch):
    def fake_gh(*args, capture=False):
        return subprocess.CompletedProcess(
            args, 0,
            stdout=json.dumps([{"number": 7}, {"number": 8}]),
            stderr="",
        )

    monkeypatch.setattr(emit, "_gh", fake_gh)
    assert emit._find_existing_issue(["test-label"]) == 7


def test_find_existing_issue_returns_none_on_empty(monkeypatch):
    monkeypatch.setattr(
        emit, "_gh",
        lambda *a, capture=False: subprocess.CompletedProcess(a, 0, stdout="[]", stderr=""),
    )
    assert emit._find_existing_issue(["test-label"]) is None


def test_find_existing_issue_returns_none_on_gh_failure(monkeypatch):
    monkeypatch.setattr(
        emit, "_gh",
        lambda *a, capture=False: subprocess.CompletedProcess(a, 1, stdout="", stderr="error"),
    )
    assert emit._find_existing_issue(["test-label"]) is None


def test_emit_issue_creates_new(monkeypatch, isolated_cwd, capsys):
    Path("report.md").write_text("body")
    monkeypatch.setattr(emit, "_gh_available", lambda: True)
    monkeypatch.setattr(emit, "_find_existing_issue", lambda labels, check_name=None: None)
    monkeypatch.setenv("GITHUB_SHA", "abc123")

    captured: dict = {}

    def fake_gh(*args, capture=False):
        captured.setdefault("calls", []).append(args)
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(emit, "_gh", fake_gh)
    rc = emit.emit_issue(
        Path("report.md"),
        "Test Title",
        ["test-label"],
        "🔔 followup",
    )
    assert rc == 0
    call = next(c for c in captured["calls"] if c[:2] == ("issue", "create"))
    assert "--title" in call and "Test Title" in call
    # Body file should contain the report content + sha footer
    body_file = next(arg for arg in call if "issue-body" in str(arg))
    body_text = Path(body_file).read_text()
    assert "body" in body_text
    assert "abc123" in body_text


def test_emit_issue_updates_existing_and_comments(monkeypatch, isolated_cwd, capsys):
    Path("report.md").write_text("body")
    monkeypatch.setattr(emit, "_gh_available", lambda: True)
    monkeypatch.setattr(emit, "_find_existing_issue", lambda labels, check_name=None: 42)
    monkeypatch.setenv("GITHUB_SHA", "deadbeef")

    captured: dict = {}

    def fake_gh(*args, capture=False):
        captured.setdefault("calls", []).append(args)
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(emit, "_gh", fake_gh)
    rc = emit.emit_issue(
        Path("report.md"),
        "Test Title",
        ["test-label"],
        "🔔 followup",
    )
    assert rc == 0
    edit_call = captured["calls"][0]
    assert "edit" in edit_call and "42" in edit_call
    comment_call = captured["calls"][1]
    assert "comment" in comment_call and "42" in comment_call
    assert any("deadbeef" in str(a) for a in comment_call)


def test_emit_issue_fails_when_gh_missing(monkeypatch, isolated_cwd, capsys):
    monkeypatch.setattr(emit, "_gh_available", lambda: False)
    rc = emit.emit_issue(Path("nope.md"), "T", ["L"], "F")
    assert rc == 1
    assert "gh CLI is not available" in capsys.readouterr().err


def test_emit_issue_prepends_slopstopper_label(monkeypatch, isolated_cwd):
    Path("report.md").write_text("body")
    monkeypatch.setattr(emit, "_gh_available", lambda: True)
    captured: dict = {}

    def fake_find(labels, check_name=None):
        captured["find_labels"] = list(labels)
        return None

    def fake_create(title, body_path, labels):
        captured["create_labels"] = list(labels)
        return 0

    monkeypatch.setattr(emit, "_find_existing_issue", fake_find)
    monkeypatch.setattr(emit, "_create_issue", fake_create)
    rc = emit.emit_issue(Path("report.md"), "T", ["sast", "security"], "F")
    assert rc == 0
    assert captured["find_labels"] == ["slopstopper", "sast", "security"]
    assert captured["create_labels"] == ["slopstopper", "sast", "security"]


def test_emit_issue_does_not_duplicate_slopstopper_label(monkeypatch, isolated_cwd):
    Path("report.md").write_text("body")
    monkeypatch.setattr(emit, "_gh_available", lambda: True)
    captured: dict = {}

    def fake_create(title, body_path, labels):
        captured["create_labels"] = list(labels)
        return 0

    monkeypatch.setattr(emit, "_find_existing_issue", lambda labels, check_name=None: None)
    monkeypatch.setattr(emit, "_create_issue", fake_create)
    rc = emit.emit_issue(Path("report.md"), "T", ["slopstopper", "sast"], "F")
    assert rc == 0
    assert captured["create_labels"].count("slopstopper") == 1


def test_emit_issue_writes_marker_when_check_name_provided(monkeypatch, isolated_cwd):
    Path("report.md").write_text("body")
    monkeypatch.setattr(emit, "_gh_available", lambda: True)
    monkeypatch.setattr(emit, "_find_existing_issue", lambda labels, check_name=None: None)
    monkeypatch.setenv("GITHUB_SHA", "abc123")

    captured: dict = {}

    def fake_create(title, body_path, labels):
        captured["body"] = Path(body_path).read_text()
        return 0

    monkeypatch.setattr(emit, "_create_issue", fake_create)
    rc = emit.emit_issue(
        Path("report.md"),
        "T",
        ["test-label"],
        "F",
        check_name="hygiene:docs-size",
    )
    assert rc == 0
    assert "<!-- slopstopper:check=hygiene:docs-size -->" in captured["body"]
    assert "*Commit: abc123*" in captured["body"]


def test_emit_issue_omits_marker_when_check_name_none(monkeypatch, isolated_cwd):
    Path("report.md").write_text("body")
    monkeypatch.setattr(emit, "_gh_available", lambda: True)
    monkeypatch.setattr(emit, "_find_existing_issue", lambda labels, check_name=None: None)
    monkeypatch.setenv("GITHUB_SHA", "abc123")

    captured: dict = {}

    def fake_create(title, body_path, labels):
        captured["body"] = Path(body_path).read_text()
        return 0

    monkeypatch.setattr(emit, "_create_issue", fake_create)
    rc = emit.emit_issue(Path("report.md"), "T", ["test-label"], "F")
    assert rc == 0
    assert "slopstopper:check" not in captured["body"]


def test_emit_issue_marker_idempotent_on_update(monkeypatch, isolated_cwd):
    """Re-emitting on a re-failure regenerates the footer fresh from the report,
    so the marker appears exactly once and is never duplicated."""
    Path("report.md").write_text("body content")
    monkeypatch.setattr(emit, "_gh_available", lambda: True)
    monkeypatch.setattr(emit, "_find_existing_issue", lambda labels, check_name=None: 42)
    monkeypatch.setenv("GITHUB_SHA", "abc123")

    captured: dict = {}

    def fake_update(number, body_path):
        captured.setdefault("bodies", []).append(Path(body_path).read_text())
        return 0

    monkeypatch.setattr(emit, "_update_issue_body", fake_update)
    monkeypatch.setattr(emit, "_comment_issue", lambda n, b: 0)

    # Emit twice. The second call must not produce a body with two markers.
    for _ in range(2):
        emit.emit_issue(
            Path("report.md"),
            "T",
            ["test-label"],
            "F",
            check_name="hygiene:docs-size",
        )

    for body in captured["bodies"]:
        assert body.count("<!-- slopstopper:check=hygiene:docs-size -->") == 1


# ── issue close ──────────────────────────────────────────────────


def test_close_issue_comments_and_closes_when_match(monkeypatch, capsys):
    monkeypatch.setattr(emit, "_gh_available", lambda: True)
    monkeypatch.setattr(emit, "_find_existing_issue", lambda labels, check_name=None: 77)

    captured: dict = {}

    def fake_gh(*args, capture=False):
        captured.setdefault("calls", []).append(args)
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(emit, "_gh", fake_gh)
    rc = emit._close_issue(["test-label"], "✅ now passing")
    assert rc == 0
    comment_call = captured["calls"][0]
    assert "comment" in comment_call and "77" in comment_call
    assert "✅ now passing" in comment_call
    close_call = captured["calls"][1]
    assert "close" in close_call and "77" in close_call
    assert "Closing issue #77" in capsys.readouterr().out


def test_close_issue_noops_when_no_match(monkeypatch, capsys):
    monkeypatch.setattr(emit, "_gh_available", lambda: True)
    monkeypatch.setattr(emit, "_find_existing_issue", lambda labels, check_name=None: None)

    def fake_gh(*args, capture=False):
        raise AssertionError(f"_gh should not be called when no open issue, got {args}")

    monkeypatch.setattr(emit, "_gh", fake_gh)
    rc = emit._close_issue(["test-label"], "✅ now passing")
    assert rc == 0
    assert "No open issue to close" in capsys.readouterr().out


def test_close_issue_fails_when_gh_missing(monkeypatch, capsys):
    monkeypatch.setattr(emit, "_gh_available", lambda: False)
    rc = emit._close_issue(["test-label"], "✅ now passing")
    assert rc == 1
    assert "gh CLI is not available" in capsys.readouterr().err


def test_close_issue_searches_with_augmented_labels(monkeypatch):
    """The close path must search with the same slopstopper-augmented labels
    that emit_issue used to create the issue. Otherwise a post-PR-2 issue
    would never be closed."""
    monkeypatch.setattr(emit, "_gh_available", lambda: True)
    captured: dict = {}

    def fake_find(labels, check_name=None):
        captured["find_labels"] = list(labels)
        return None

    monkeypatch.setattr(emit, "_find_existing_issue", fake_find)
    emit._close_issue(["sast", "security"], "✅ now passing")
    assert captured["find_labels"] == ["slopstopper", "sast", "security"]


# ── public dispatcher ────────────────────────────────────────────


def test_emit_routes_pr_comment(monkeypatch):
    called: dict = {}

    def fake_pr(report_path, discrim, **kwargs):
        called["pr"] = (report_path, discrim, kwargs)
        return 0

    monkeypatch.setattr(emit, "emit_pr_comment", fake_pr)
    rc = emit.emit("pr-comment", SAMPLE_META, check_name="hygiene:docs-size", status="pass")
    assert rc == 0
    assert called["pr"][1] == "📚 Test Report"
    # status and check_name have to reach the emitter: they pick the verdict
    # line and the comment marker respectively.
    assert called["pr"][2]["status"] == "pass"
    assert called["pr"][2]["check_name"] == "hygiene:docs-size"


def test_emit_routes_issue(monkeypatch):
    called: dict = {}

    def fake_issue(report_path, title, labels, followup, *, check_name=None):
        called["issue"] = (report_path, title, labels, followup, check_name)
        return 0

    monkeypatch.setattr(emit, "emit_issue", fake_issue)
    rc = emit.emit("issue", SAMPLE_META)
    assert rc == 0
    assert called["issue"][1] == "📚 Test Thresholds Exceeded"
    assert called["issue"][2] == ["test-label", "maintenance"]


def test_emit_rejects_unknown_target(capsys):
    rc = emit.emit("slack", SAMPLE_META)
    assert rc == 2
    assert "Unknown emit target" in capsys.readouterr().err


def test_emit_routes_on_pass_close_to_close_issue(monkeypatch):
    called: dict = {}

    def fake_close(labels, close_comment, check_name=None):
        called["close"] = (labels, close_comment)
        return 0

    monkeypatch.setattr(emit, "_close_issue", fake_close)
    rc = emit.emit("issue", SAMPLE_META, on_pass="close")
    assert rc == 0
    assert called["close"][0] == ["test-label", "maintenance"]
    # SAMPLE_META has no issue_close_comment → defaults to the module default.
    assert called["close"][1] == emit._DEFAULT_CLOSE_COMMENT


def test_emit_close_uses_meta_close_comment_when_present(monkeypatch):
    meta_with_close = {**SAMPLE_META, "issue_close_comment": "✅ custom-close"}
    called: dict = {}

    def fake_close(labels, close_comment, check_name=None):
        called["close"] = (labels, close_comment)
        return 0

    monkeypatch.setattr(emit, "_close_issue", fake_close)
    rc = emit.emit("issue", meta_with_close, on_pass="close")
    assert rc == 0
    assert called["close"][1] == "✅ custom-close"


def test_emit_without_on_pass_still_opens_issue(monkeypatch):
    """on_pass=None must not divert from the normal open-or-update path."""
    called: dict = {}

    def fake_issue(report_path, title, labels, followup, *, check_name=None):
        called["issue"] = True
        called["check_name"] = check_name
        return 0

    monkeypatch.setattr(emit, "emit_issue", fake_issue)
    rc = emit.emit("issue", SAMPLE_META)
    assert rc == 0
    assert called.get("issue") is True


def test_emit_threads_check_name_to_emit_issue(monkeypatch):
    called: dict = {}

    def fake_issue(report_path, title, labels, followup, *, check_name=None):
        called["check_name"] = check_name
        return 0

    monkeypatch.setattr(emit, "emit_issue", fake_issue)
    rc = emit.emit("issue", SAMPLE_META, check_name="hygiene:docs-size")
    assert rc == 0
    assert called["check_name"] == "hygiene:docs-size"


# ── META completeness ────────────────────────────────────────────


def test_every_check_has_meta():
    """Every check in REGISTRY must define a META dict, or be explicitly exempt.

    Prevents silent regressions where a new check lands without emit support
    and is only discovered when an adopter runs `slopstopper emit <check>`.
    """
    import importlib

    from slopstopper.checks import REGISTRY

    # Checks with no emit support by design. Empty as of PR 3 (every shipped
    # check has at least PR-comment-only META). Add a check name here only
    # if there's a documented architectural reason it cannot emit.
    EMIT_EXEMPT: set[str] = set()

    missing: list[str] = []
    for check_name in REGISTRY:
        if check_name in EMIT_EXEMPT:
            continue
        parts = check_name.split(":")[1:]
        module_name = "slopstopper.checks." + "_".join(parts).replace("-", "_")
        module = importlib.import_module(module_name)
        if getattr(module, "META", None) is None:
            missing.append(check_name)

    assert missing == [], (
        f"Checks without META: {missing}. Add a META dict to each check "
        "module (see the slopstopper.emit module docstring for the schema), "
        "or add the check name to EMIT_EXEMPT in this test with a documented "
        "reason."
    )


# ── compact PR comments + delete-on-pass ─────────────────────────


def _pr_env(monkeypatch, comments=None):
    """Stub the gh boundary and PR context; record every gh call."""
    calls = []

    def fake_gh(*args, capture=False):
        calls.append(args)
        import subprocess as sp

        return sp.CompletedProcess(list(args), 0, stdout="", stderr="")

    monkeypatch.setattr(emit, "_gh_available", lambda: True)
    monkeypatch.setattr(emit, "_repo_slug", lambda: "owner/repo")
    monkeypatch.setattr(emit, "_pr_number_from_event", lambda: 42)
    monkeypatch.setattr(emit, "_list_pr_comments", lambda repo, pr: comments or [])
    monkeypatch.setattr(emit, "_gh", fake_gh)
    return calls


def _bot_comment(cid, body):
    return {"id": cid, "user": {"type": "Bot"}, "body": body}


def test_pr_comment_body_is_compact_not_the_whole_report(monkeypatch, isolated_cwd):
    calls = _pr_env(monkeypatch)
    report = isolated_cwd / "r.md"
    report.write_text("# Big Report\n\n" + "\n".join(f"line {i}" for i in range(200)))

    assert emit.emit_pr_comment(report, "Big Report", check_name="reliability:seo", status="fail") == 0

    body = (isolated_cwd / "r.comment.md").read_text()
    assert body.startswith("### ❌ SEO")
    assert "<details>" in body
    # posted via `gh pr comment --body-file <the compact body>`, not the report
    assert any("pr" in c and "comment" in c for c in calls)


def test_pass_with_on_pass_delete_removes_the_comment(monkeypatch, isolated_cwd):
    marker = "<!-- slopstopper:check=reliability:seo -->"
    calls = _pr_env(monkeypatch, comments=[_bot_comment(7, f"### ❌ SEO\n{marker}")])
    report = isolated_cwd / "r.md"
    report.write_text("# R\n")

    rc = emit.emit_pr_comment(
        report, "SEO", check_name="reliability:seo", status="pass", on_pass="delete"
    )
    assert rc == 0
    deletes = [c for c in calls if "DELETE" in c]
    assert deletes, f"expected a DELETE call, got {calls}"
    assert "repos/owner/repo/issues/comments/7" in deletes[0]


def test_pass_with_on_pass_delete_is_a_noop_when_nothing_posted(monkeypatch, isolated_cwd, capsys):
    calls = _pr_env(monkeypatch, comments=[])
    report = isolated_cwd / "r.md"
    report.write_text("# R\n")

    rc = emit.emit_pr_comment(
        report, "SEO", check_name="reliability:seo", status="pass", on_pass="delete"
    )
    assert rc == 0
    assert not calls, "nothing to delete means no gh calls at all"
    assert "no prior comment" in capsys.readouterr().out


def test_warn_is_not_treated_as_a_pass_for_deletion(monkeypatch, isolated_cwd):
    """An advisory check with findings must keep its comment."""
    calls = _pr_env(monkeypatch, comments=[_bot_comment(4, "<!-- slopstopper:check=hygiene:docs-size -->")])
    report = isolated_cwd / "r.md"
    report.write_text("# Docs\n\n⚠️  27 files\n")

    rc = emit.emit_pr_comment(
        report, "Docs", check_name="hygiene:docs-size", status="warn", on_pass="delete"
    )
    assert rc == 0
    assert not any("DELETE" in c for c in calls)
    assert (isolated_cwd / "r.comment.md").read_text().startswith("### ⚠️ Docs Size")


def test_pass_without_on_pass_still_posts_a_one_line_comment(monkeypatch, isolated_cwd):
    _pr_env(monkeypatch)
    report = isolated_cwd / "r.md"
    report.write_text("# R\n")

    assert emit.emit_pr_comment(report, "SEO", check_name="reliability:seo", status="pass") == 0
    body = (isolated_cwd / "r.comment.md").read_text()
    assert body.startswith("### ✅ SEO: passed")


def test_delete_on_pass_does_not_need_the_report_to_exist(monkeypatch, isolated_cwd):
    """The report is gone when the check short-circuited; the stale comment
    should still be cleaned up rather than erroring."""
    calls = _pr_env(monkeypatch, comments=[_bot_comment(3, "<!-- slopstopper:check=reliability:seo -->")])
    rc = emit.emit_pr_comment(
        isolated_cwd / "missing.md",
        "SEO",
        check_name="reliability:seo",
        status="pass",
        on_pass="delete",
    )
    assert rc == 0
    assert any("DELETE" in c for c in calls)


# ── comment lookup ───────────────────────────────────────────────


def test_find_comment_prefers_the_marker_over_the_discriminator():
    comments = [
        _bot_comment(1, "🔎 SEO mentioned in passing"),
        _bot_comment(2, "<!-- slopstopper:check=reliability:seo -->"),
    ]
    found = emit._find_existing_pr_comment(
        comments, "🔎 SEO", "<!-- slopstopper:check=reliability:seo -->"
    )
    assert found == 2


def test_find_comment_falls_back_to_discriminator_for_pre_marker_comments():
    """Comments posted by an older CLI have no marker and must still be
    updated in place rather than duplicated."""
    comments = [_bot_comment(5, "# 🔎 SEO / Social-Share Metatag Report\n…")]
    found = emit._find_existing_pr_comment(comments, "🔎 SEO", "<!-- slopstopper:check=x -->")
    assert found == 5


def test_find_comment_ignores_human_comments():
    comments = [{"id": 9, "user": {"type": "User"}, "body": "<!-- slopstopper:check=x -->"}]
    assert emit._find_existing_pr_comment(comments, "nope", "<!-- slopstopper:check=x -->") is None


# ── head sha / run url ───────────────────────────────────────────


def test_head_sha_prefers_the_pr_head_over_the_merge_commit(monkeypatch, tmp_path):
    event = tmp_path / "event.json"
    event.write_text(json.dumps({"pull_request": {"number": 1, "head": {"sha": "head123"}}}))
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event))
    monkeypatch.setenv("GITHUB_SHA", "merge999")
    assert emit._head_sha() == "head123"


def test_head_sha_reads_workflow_run_payload(monkeypatch, tmp_path):
    event = tmp_path / "event.json"
    event.write_text(json.dumps({"workflow_run": {"head_sha": "run123", "pull_requests": []}}))
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event))
    monkeypatch.delenv("GITHUB_SHA", raising=False)
    assert emit._head_sha() == "run123"


def test_head_sha_falls_back_to_env(monkeypatch, tmp_path):
    event = tmp_path / "event.json"
    event.write_text(json.dumps({}))
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event))
    monkeypatch.setenv("GITHUB_SHA", "push123")
    assert emit._head_sha() == "push123"


def test_run_url_needs_the_full_actions_context(monkeypatch):
    monkeypatch.setenv("GITHUB_SERVER_URL", "https://github.com")
    monkeypatch.setenv("GITHUB_REPOSITORY", "o/r")
    monkeypatch.setenv("GITHUB_RUN_ID", "77")
    assert emit._run_url() == "https://github.com/o/r/actions/runs/77"
    monkeypatch.delenv("GITHUB_RUN_ID")
    assert emit._run_url() is None


# ── aggregate summary ────────────────────────────────────────────


def test_summary_context_reads_the_pr_from_a_workflow_run(monkeypatch, tmp_path):
    event = tmp_path / "event.json"
    event.write_text(
        json.dumps({"workflow_run": {"head_sha": "abc", "pull_requests": [{"number": 31}]}})
    )
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event))
    monkeypatch.setenv("GITHUB_REPOSITORY", "owner/repo")
    assert emit._summary_pr_context() == ("owner/repo", 31, "abc")


def test_summary_exits_zero_when_the_event_has_no_pr(monkeypatch, tmp_path, capsys):
    event = tmp_path / "event.json"
    event.write_text(json.dumps({"workflow_run": {"head_sha": "abc", "pull_requests": []}}))
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event))
    monkeypatch.setenv("GITHUB_REPOSITORY", "owner/repo")
    monkeypatch.setattr(emit, "_gh_available", lambda: True)
    assert emit.emit_pr_summary() == 0
    assert "No pull request" in capsys.readouterr().err


def test_summary_upserts_one_comment(monkeypatch, isolated_cwd, tmp_path):
    event = tmp_path / "event.json"
    event.write_text(
        json.dumps({"workflow_run": {"head_sha": "abc1234", "pull_requests": [{"number": 8}]}})
    )
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event))
    monkeypatch.setenv("GITHUB_REPOSITORY", "owner/repo")
    calls = _pr_env(monkeypatch, comments=[])
    monkeypatch.setattr(emit, "_pr_number_from_event", lambda: None)
    monkeypatch.setattr(
        emit,
        "_list_workflow_runs",
        lambda repo, sha: [
            {
                "path": ".github/workflows/ss-security-sast-check.yml",
                "conclusion": "failure",
                "status": "completed",
                "created_at": "2026-09-17T12:00:00Z",
                "html_url": "https://x/1",
            }
        ],
    )
    assert emit.emit_pr_summary() == 0
    body = Path(".ss/reports/pr-summary.md").read_text()
    assert body.startswith("## ❌ SlopStopper: 1 of 1 check failed")
    assert "**SAST**" in body
    assert any("pr" in c and "comment" in c for c in calls)


# ── exit 2 never becomes an issue ────────────────────────────────


def _fail_if_called(*a, **kw):
    raise AssertionError("gh must not be touched for a run that could not run")


@pytest.mark.parametrize("on_pass", [None, "close"])
def test_a_run_that_could_not_run_neither_opens_nor_closes_an_issue(
    monkeypatch, isolated_cwd, capsys, on_pass
):
    monkeypatch.setattr(emit, "_gh", _fail_if_called)
    monkeypatch.setattr(emit, "_gh_available", lambda: True)
    emit.record_exit("security:iac", 2)
    meta = {
        "report_path": "report.md",
        "issue_title": "t",
        "issue_labels": ["x"],
        "issue_followup": "f",
    }
    assert emit.emit("issue", meta, check_name="security:iac", on_pass=on_pass) == 0
    assert "could not run (exit 2)" in capsys.readouterr().out


def test_record_and_read_back_the_last_exit(isolated_cwd):
    assert emit.last_exit("security:dependencies") is None
    emit.record_exit("security:dependencies", 1)
    assert emit.last_exit("security:dependencies") == 1


def test_run_records_the_exit_code_for_emit(isolated_cwd, monkeypatch):
    from slopstopper import cli

    monkeypatch.setitem(cli.REGISTRY, "hygiene:test-two", lambda _args: 2)
    assert cli.main(["run", "hygiene:test-two"]) == 2
    assert emit.last_exit("hygiene:test-two") == 2


# ── labels the repo doesn't have (#417) ──────────────────────────


class _FakeRepo:
    """A `gh` stand-in holding a repo's labels; `issue create` fails on a missing one, as gh does."""

    def __init__(self, labels=(), label_create_works=True):
        self.labels = set(labels)
        self.label_create_works = label_create_works
        self.created: list[list[str]] = []
        self.issues: dict[int, dict] = {}

    def __call__(self, *args, capture=False):
        import subprocess
        if args[:2] == ("label", "list"):
            return subprocess.CompletedProcess(args, 0, json.dumps([{"name": n} for n in self.labels]), "")
        if args[:2] == ("label", "create"):
            if self.label_create_works:
                self.labels.add(args[2])
                return subprocess.CompletedProcess(args, 0, "", "")
            return subprocess.CompletedProcess(args, 1, "", "HTTP 403")
        if args[:2] == ("issue", "create"):
            wanted = [args[i + 1] for i, a in enumerate(args) if a == "--label"]
            missing = [w for w in wanted if w not in self.labels]
            if missing:
                return subprocess.CompletedProcess(args, 1, "", f"could not add label: '{missing[0]}' not found")
            self.created.append(wanted)
            body = Path(args[args.index("--body-file") + 1]).read_text()
            self.issues[len(self.issues) + 1] = {"labels": set(wanted), "body": body, "state": "open"}
            return subprocess.CompletedProcess(args, 0, f"https://github.com/o/r/issues/{len(self.issues)}\n", "")
        if args[:2] == ("issue", "list"):
            wanted = {args[i + 1] for i, a in enumerate(args) if a == "--label"}
            rows = [{"number": n, "body": i["body"]} for n, i in self.issues.items()
                    if i["state"] == "open" and wanted <= i["labels"]]
            return subprocess.CompletedProcess(args, 0, json.dumps(rows), "")
        if args[:2] == ("issue", "edit"):
            self.issues[int(args[2])]["body"] = Path(args[args.index("--body-file") + 1]).read_text()
            return subprocess.CompletedProcess(args, 0, "", "")
        if args[:2] == ("issue", "comment"):
            return subprocess.CompletedProcess(args, 0, "", "")
        if args[:2] == ("issue", "close"):
            self.issues[int(args[2])]["state"] = "closed"
            return subprocess.CompletedProcess(args, 0, "", "")
        raise AssertionError(args)


def test_create_issue_creates_missing_labels_first(monkeypatch, isolated_cwd):
    repo = _FakeRepo(labels={"security"})
    monkeypatch.setattr(emit, "_gh", repo)
    Path("body.md").write_text("x")
    assert emit._create_issue("T", Path("body.md"), ["slopstopper", "sast", "security"]) == 0
    assert repo.created == [["slopstopper", "sast", "security"]]
    assert {"slopstopper", "sast"} <= repo.labels


def test_create_issue_falls_back_to_no_labels_when_a_label_cant_be_made(monkeypatch, isolated_cwd, capsys):
    repo = _FakeRepo(label_create_works=False)
    monkeypatch.setattr(emit, "_gh", repo)
    Path("body.md").write_text("x")
    assert emit._create_issue("T", Path("body.md"), ["slopstopper"]) == 0
    assert repo.created == [[]]
    assert "creating the issue without them" in capsys.readouterr().err


def test_emit_issue_marks_the_workflow_that_raised_it(monkeypatch, isolated_cwd):
    """ss-workflow-failure-issue.yml matches this marker to the failing workflow (#412)."""
    Path("report.md").write_text("body")
    monkeypatch.setattr(emit, "_gh_available", lambda: True)
    monkeypatch.setattr(emit, "_find_existing_issue", lambda labels, check_name=None: None)
    monkeypatch.setenv("GITHUB_WORKFLOW_REF", "o/r/.github/workflows/ss-reliability-smoke-tests.yml@refs/heads/main")
    captured: dict = {}

    def fake_create(title, body_path, labels):
        captured["body"] = Path(body_path).read_text()
        return 0

    monkeypatch.setattr(emit, "_create_issue", fake_create)
    assert emit.emit_issue(Path("report.md"), "T", ["x"], "F", check_name="reliability:smoke") == 0
    assert "<!-- slopstopper:workflow=ss-reliability-smoke-tests.yml -->" in captured["body"]


def test_an_unlabelled_issue_is_still_found_updated_and_closed(monkeypatch, isolated_cwd):
    """Dedup can't rely on labels after the no-labels fallback, so the body
    marker finds the issue instead: one issue per check, closed on recovery."""
    repo = _FakeRepo(label_create_works=False)
    monkeypatch.setattr(emit, "_gh", repo)
    monkeypatch.setattr(emit, "_gh_available", lambda: True)
    monkeypatch.setenv("GITHUB_SHA", "abc123")
    Path("report.md").write_text("first failure")
    assert emit.emit_issue(Path("report.md"), "T", ["smoke"], "F", check_name="reliability:smoke") == 0
    Path("report.md").write_text("second failure")
    assert emit.emit_issue(Path("report.md"), "T", ["smoke"], "F", check_name="reliability:smoke") == 0
    assert list(repo.issues) == [1] and "second failure" in repo.issues[1]["body"]
    assert emit._close_issue(["smoke"], "fixed", check_name="reliability:smoke") == 0
    assert repo.issues[1]["state"] == "closed"
