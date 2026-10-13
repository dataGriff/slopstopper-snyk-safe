"""The shell inside `.github/actions/ss-resolve-url`, run for real.

Twelve workflows depend on this one event → URL table, and its failure
modes are silent in Actions: a URL that isn't validated, a schedule that
goes red instead of skipping, a broken CLI read as "unset". So the `run:`
block is extracted from action.yml and executed under the same shell
Actions uses (`bash -e -o pipefail`), with a stub `slopstopper` on PATH.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from tests.helpers import needs, yaml_block

REPO_ROOT = Path(__file__).resolve().parents[2]
ACTION = REPO_ROOT / ".github" / "actions" / "ss-resolve-url" / "action.yml"

pytestmark = needs("bash")


STUB = """#!/bin/sh
# `slopstopper config get <key>` → $STUB_<KEY>; STUB_FAIL makes it crash.
[ -n "$STUB_FAIL" ] && { echo "boom: config unreadable" >&2; exit 3; }
case "$3" in
  urls.preview)    printf '%s\\n' "$STUB_PREVIEW" ;;
  urls.production) printf '%s\\n' "$STUB_PRODUCTION" ;;
esac
"""


def resolve(tmp_path, event, mode="local", url="", deployment="", fallback="local",
            preview="", production="", cli_fails=False, site=True):
    """Run the resolver in `tmp_path`. `site` lays down a `public/` root, the
    bundled server's auto-detected static root; without it the repo has
    nothing to serve and mode local skips."""
    repo = tmp_path / "repo"
    repo.mkdir(exist_ok=True)
    if site:
        (repo / "public").mkdir(exist_ok=True)
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    stub = bindir / "slopstopper"
    stub.write_text(STUB)
    stub.chmod(0o755)
    out = tmp_path / "out"
    out.write_text("")
    script = tmp_path / "resolve.sh"
    script.write_text(yaml_block(ACTION, "run"))
    env = {
        **os.environ,
        "PATH": f"{bindir}{os.pathsep}{os.environ['PATH']}",
        "GITHUB_OUTPUT": str(out),
        "EVENT_NAME": event, "MODE": mode, "INPUT_URL": url,
        "DEPLOYMENT_URL": deployment, "DISPATCH_FALLBACK": fallback, "CHECK": "Test check",
        "STUB_PREVIEW": preview, "STUB_PRODUCTION": production,
        "STUB_FAIL": "1" if cli_fails else "",
    }
    proc = subprocess.run(["bash", "-e", "-o", "pipefail", str(script)], env=env,
                          cwd=repo, capture_output=True, text=True)
    outputs = dict(line.split("=", 1) for line in out.read_text().splitlines() if "=" in line)
    return proc.returncode, outputs, proc.stdout + proc.stderr


PROD = "https://example.com"
PREVIEW = "https://preview.example.com"
LOCAL = "http://localhost:8080"


# ── mode skip: the API checks ────────────────────────────────────

@pytest.mark.parametrize(
    "event,kwargs,expected",
    [
        ("pull_request", {"preview": PREVIEW}, {"url": PREVIEW, "skip": "false", "prod": "false"}),
        ("pull_request", {}, {"url": "", "skip": "true"}),
        ("push", {"production": PROD}, {"url": PROD, "skip": "false", "prod": "true"}),
        ("push", {}, {"skip": "true"}),
        # A scheduled run with nothing configured skips; it never goes red.
        ("schedule", {}, {"skip": "true"}),
        ("schedule", {"production": PROD}, {"url": PROD, "prod": "true"}),
        # A blank manual run skips; it does not quietly probe production.
        ("workflow_dispatch", {"production": PROD}, {"skip": "true"}),
        ("workflow_dispatch", {"url": PREVIEW}, {"url": PREVIEW, "prod": "false"}),
        # A preview deploy audits the preview with the dev config; only the
        # production URL gets the production one.
        ("deployment_status", {"deployment": PREVIEW, "production": PROD}, {"url": PREVIEW, "prod": "false"}),
        ("deployment_status", {"deployment": PROD, "production": PROD}, {"url": PROD, "prod": "true"}),
        ("deployment_status", {}, {"skip": "true"}),
    ],
)
def test_skip_mode(tmp_path, event, kwargs, expected):
    rc, outputs, log = resolve(tmp_path, event, mode="skip", **kwargs)
    assert rc == 0, log
    for key, value in expected.items():
        assert outputs[key] == value, (key, outputs, log)
    if expected.get("skip") == "true":
        assert "::notice::" in log and "Test check" in log


# ── mode local: the browser checks ───────────────────────────────

@pytest.mark.parametrize(
    "event,kwargs,expected",
    [
        ("pull_request", {}, {"url": LOCAL, "use_local": "true"}),
        ("push", {"production": PROD}, {"url": LOCAL, "use_local": "true"}),
        ("workflow_dispatch", {}, {"url": LOCAL, "use_local": "true"}),
        ("workflow_dispatch", {"url": PREVIEW}, {"url": PREVIEW, "use_local": "false", "prod": "false"}),
        ("deployment_status", {"deployment": PREVIEW, "production": PROD}, {"url": PREVIEW, "prod": "false"}),
        ("deployment_status", {"deployment": PROD + "/", "production": PROD}, {"url": PROD + "/", "prod": "true"}),
        ("schedule", {"production": PROD}, {"url": PROD, "prod": "true", "use_local": "false"}),
        # CWV: a blank dispatch audits production, with the production config
        # like any other run against urls.production.
        ("workflow_dispatch", {"fallback": "production", "production": PROD},
         {"url": PROD, "prod": "true", "use_local": "false"}),
    ],
)
def test_local_mode(tmp_path, event, kwargs, expected):
    rc, outputs, log = resolve(tmp_path, event, mode="local", **kwargs)
    assert rc == 0, log
    for key, value in expected.items():
        assert outputs[key] == value, (key, outputs, log)


@pytest.mark.parametrize("event", ["pull_request", "push", "workflow_dispatch"])
def test_local_mode_skips_when_there_is_nothing_to_serve(tmp_path, event):
    """A library or an API has no static root and no build script, so the
    browser check skips with a notice rather than serving an empty server."""
    rc, outputs, log = resolve(tmp_path, event, mode="local", site=False)
    assert rc == 0, log
    assert outputs["skip"] == "true" and outputs["use_local"] == "false" and outputs["url"] == ""
    assert "::notice::Nothing to serve for Test check" in log


@pytest.mark.parametrize("root", ["dist/client", "dist", "build", "out", "public", "app"])
def test_every_auto_detected_static_root_counts_as_a_site(tmp_path, root):
    (tmp_path / "repo" / root).mkdir(parents=True)
    rc, outputs, log = resolve(tmp_path, "pull_request", mode="local", site=False)
    assert rc == 0 and outputs["use_local"] == "true", log


def test_a_serve_root_counts_as_a_site(tmp_path, monkeypatch):
    (tmp_path / "repo" / "site-out").mkdir(parents=True)
    monkeypatch.setenv("SERVE_ROOT", "site-out")
    rc, outputs, log = resolve(tmp_path, "pull_request", mode="local", site=False)
    assert rc == 0 and outputs["use_local"] == "true", log


def test_a_serve_root_that_does_not_exist_is_not_a_site(tmp_path, monkeypatch):
    monkeypatch.setenv("SERVE_ROOT", "missing")
    rc, outputs, log = resolve(tmp_path, "pull_request", mode="local", site=False)
    assert rc == 0 and outputs["skip"] == "true", log


@needs("node")
def test_a_build_script_counts_as_a_site(tmp_path):
    (tmp_path / "repo").mkdir(exist_ok=True)
    (tmp_path / "repo" / "package.json").write_text('{"scripts": {"build": "vite build"}}')
    rc, outputs, log = resolve(tmp_path, "pull_request", mode="local", site=False)
    assert rc == 0 and outputs["use_local"] == "true", log


@pytest.mark.parametrize(
    "event,kwargs,message",
    [
        ("schedule", {}, "Scheduled run requires urls.production"),
        ("workflow_dispatch", {"fallback": "production"}, "No url input given"),
        ("deployment_status", {}, "Invalid URL format"),
    ],
)
def test_local_mode_errors(tmp_path, event, kwargs, message):
    rc, outputs, log = resolve(tmp_path, event, mode="local", **kwargs)
    assert rc != 0 and message in log
    assert "url" not in outputs


# ── failures that must end the step ──────────────────────────────

@pytest.mark.parametrize(
    "mode,event,kwargs",
    [
        ("local", "workflow_dispatch", {"url": "ftp://x"}),
        ("local", "workflow_dispatch", {"url": "javascript:alert(1)"}),
        ("local", "schedule", {"production": "ftp://x"}),
        ("local", "deployment_status", {"deployment": "not a url"}),
        ("skip", "pull_request", {"preview": "file:///etc/passwd"}),
        ("skip", "workflow_dispatch", {"url": "ftp://x"}),
    ],
)
def test_an_invalid_url_fails_the_step(tmp_path, mode, event, kwargs):
    rc, outputs, log = resolve(tmp_path, event, mode=mode, **kwargs)
    assert rc != 0, (outputs, log)
    assert "Invalid URL format" in log
    assert "url" not in outputs, "no URL may be emitted for the check to run against"


@pytest.mark.parametrize("mode,event,kwargs", [
    ("skip", "pull_request", {}), ("skip", "schedule", {}), ("local", "schedule", {}),
    # The is-it-production lookup for a dispatched url runs inside `$(…)`,
    # where bash drops `set -e` unless told otherwise.
    ("skip", "workflow_dispatch", {"url": PREVIEW}), ("local", "workflow_dispatch", {"url": PREVIEW}),
])
def test_a_broken_cli_is_not_an_unset_key(tmp_path, mode, event, kwargs):
    rc, outputs, log = resolve(tmp_path, event, mode=mode, cli_fails=True, **kwargs)
    assert rc != 0
    assert "config get" in log and "failed" in log
    assert outputs == {}


# ── a deployment whose URL is its log (#416) ─────────────────────
#
# GitHub Pages sets deployment_status.target_url to the Actions job and the
# site to environment_url. The workflows pass `environment_url ||
# target_url`; a provider that only fills target_url with a log link must
# still never have that link audited as the site.

PAGES_LOG = "https://github.com/hungovercoders/terminal-of-terror/actions/runs/38064650455/job/114249869256"


@pytest.mark.parametrize("mode", ["skip", "local"])
def test_a_log_link_is_replaced_by_urls_production(tmp_path, mode):
    rc, outputs, log = resolve(tmp_path, "deployment_status", mode=mode, deployment=PAGES_LOG, production=PROD)
    assert rc == 0, log
    assert outputs["url"] == PROD and outputs["prod"] == "true"
    assert "GitHub Actions log" in log


def test_a_log_link_with_no_production_skips_an_api_check(tmp_path):
    rc, outputs, log = resolve(tmp_path, "deployment_status", mode="skip", deployment=PAGES_LOG)
    assert rc == 0, log
    assert outputs["skip"] == "true" and outputs["url"] == ""


def test_a_log_link_with_no_production_builds_locally_for_a_browser_check(tmp_path):
    rc, outputs, log = resolve(tmp_path, "deployment_status", mode="local", deployment=PAGES_LOG)
    assert rc == 0, log
    assert outputs["url"] == LOCAL and outputs["use_local"] == "true"


WORKFLOWS = REPO_ROOT / ".github" / "workflows"


@pytest.mark.parametrize(
    "workflow",
    sorted(p.name for p in WORKFLOWS.glob("ss-*.yml") if "deployment_status:" in p.read_text()),
)
def test_deployment_workflows_pass_environment_url_and_only_on_success(workflow):
    text = (WORKFLOWS / workflow).read_text()
    assert ("deployment-url: ${{ github.event.deployment_status.environment_url"
            " || github.event.deployment_status.target_url }}") in text
    assert "github.event.deployment_status.state == 'success'" in text


# ── a dispatch of the live site is production (#425) ─────────────
#
# A deploy pipeline that dispatches a check with the URL it just deployed
# passes urls.production for the default branch, which should get the
# production treatment (CWV's stricter config) as a deployment does.

@pytest.mark.parametrize("mode", ["skip", "local"])
@pytest.mark.parametrize("url,prod", [(PROD, "true"), (PROD + "/", "true"), (PREVIEW, "false")])
def test_a_dispatch_is_production_only_when_its_url_is_urls_production(tmp_path, mode, url, prod):
    rc, outputs, log = resolve(tmp_path, "workflow_dispatch", mode=mode, url=url, production=PROD)
    assert rc == 0, log
    assert outputs["url"] == url and outputs["prod"] == prod
