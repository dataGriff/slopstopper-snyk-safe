"""The github-script inside `ss-workflow-failure-issue.yml`, run for real under node.

It decides whether a failure on main gets a generic tracking issue or is
already covered by the failing check's own issue. Getting that wrong is
silent: a check fails on main and nobody hears. So the script is
extracted from the workflow and run against a stubbed `github` client.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from tests.helpers import needs, yaml_block

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ss-workflow-failure-issue.yml"

pytestmark = needs("node")

RUN_URL = "https://github.com/o/r/actions/runs/1"

HARNESS = """
const issues = %(issues)s;
const calls = [];
const github = { rest: { issues: {
  listForRepo: async () => ({ data: issues }),
  create: async (args) => { calls.push(['create', args.title]); },
  createComment: async (args) => { calls.push(['comment', args.issue_number]); },
}}};
const context = {
  repo: { owner: 'o', repo: 'r' },
  serverUrl: 'https://github.com',
  payload: { workflow_run: %(run)s },
};
(async () => {
%(script)s
})().then(() => console.log(JSON.stringify(calls)));
"""


def _raise_issue_script() -> str:
    return yaml_block(WORKFLOW, "script", step="Create or update issue for failed workflow run")


def _fail(tmp_path, workflow_file: str, name: str, open_issues: list[dict]) -> list:
    run = {
        "name": name, "id": 1, "html_url": RUN_URL, "head_sha": "abcdef1234",
        "head_branch": "main", "path": f".github/workflows/{workflow_file}",
    }
    js = tmp_path / "harness.js"
    js.write_text(HARNESS % {"issues": json.dumps(open_issues), "run": json.dumps(run),
                             "script": _raise_issue_script()})
    out = subprocess.run(["node", str(js)], capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


def _check_issue(number: int, check: str, workflow_file: str) -> dict:
    return {
        "number": number, "title": "📊 Core Web Vitals", "labels": [{"name": "slopstopper"}],
        "body": f"report\n\n---\n<!-- slopstopper:check={check} -->\n"
                f"<!-- slopstopper:workflow={workflow_file} -->\n",
    }


CWV_ISSUE = _check_issue(7, "reliability:cwv", "ss-reliability-core-web-vitals.yml")


def test_another_checks_open_issue_does_not_silence_a_failure(tmp_path):
    calls = _fail(tmp_path, "ss-reliability-smoke-tests.yml", "SlopStopper · Reliability Smoke Tests", [CWV_ISSUE])
    assert calls == [["create", "❌ Workflow Failure: SlopStopper · Reliability Smoke Tests"]]


def test_a_failure_its_own_check_issue_covers_is_not_duplicated(tmp_path):
    calls = _fail(tmp_path, "ss-reliability-core-web-vitals.yml", "SlopStopper · Core Web Vitals", [CWV_ISSUE])
    assert calls == []


def test_a_workflow_with_no_issue_of_its_own_is_never_silenced(tmp_path):
    calls = _fail(tmp_path, "ss-hygiene-auto-label-pr.yml", "SlopStopper · Hygiene - Auto Label PRs",
                  [CWV_ISSUE])
    assert calls == [["create", "❌ Workflow Failure: SlopStopper · Hygiene - Auto Label PRs"]]


def test_an_issue_linking_this_run_still_covers_it(tmp_path):
    legacy = {"number": 3, "title": "x", "labels": [], "body": f"see {RUN_URL}"}
    assert _fail(tmp_path, "ss-reliability-smoke-tests.yml", "SlopStopper · Reliability Smoke Tests", [legacy]) == []


def test_a_repeat_failure_comments_on_its_existing_failure_issue(tmp_path):
    existing = {"number": 9, "title": "❌ Workflow Failure: SlopStopper · Reliability Smoke Tests",
                "labels": [{"name": "workflow-failure"}], "body": "earlier run"}
    calls = _fail(tmp_path, "ss-reliability-smoke-tests.yml", "SlopStopper · Reliability Smoke Tests",
                  [CWV_ISSUE, existing])
    assert calls == [["comment", 9]]
