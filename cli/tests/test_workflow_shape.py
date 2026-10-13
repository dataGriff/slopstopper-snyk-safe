"""Guard: the ss-* workflows share their plumbing through the composite actions.

Before `.github/actions/ss-setup` and `ss-resolve-url` existed, the same
three toolchain steps were copied into 24 workflows and the same
`validate_url()` shell function into 12, with four mutually incompatible
`if:` dialects on the emit step growing around them. Copies drift, and that
is how two workflows ended up never deleting their PR comment.

These tests keep the plumbing in one place: no ss-* workflow may inline
what the actions provide, every local `uses:` must resolve, and every
`steps.<id>.outputs` reference must name a step that exists (a renamed
step id fails silently in Actions, where the expression just evaluates empty).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS_DIR = REPO_ROOT / ".github/workflows"
ACTIONS_DIR = REPO_ROOT / ".github/actions"

# Not built on the composite actions: they aren't checks and don't need the
# CLI toolchain.
EXEMPT = {
    "ss-hygiene-auto-label-pr.yml",
    "ss-workflow-failure-issue.yml",
}

WORKFLOWS = sorted(p.name for p in WORKFLOWS_DIR.glob("ss-*.yml"))
BUILT_ON_ACTIONS = [w for w in WORKFLOWS if w not in EXEMPT]


def _text(name: str) -> str:
    return (WORKFLOWS_DIR / name).read_text(encoding="utf-8")


def test_the_composite_actions_exist():
    for action in ("ss-setup", "ss-resolve-url"):
        assert (ACTIONS_DIR / action / "action.yml").is_file(), action


def test_the_fixture_finds_workflows():
    assert len(BUILT_ON_ACTIONS) >= 15


@pytest.mark.parametrize("workflow", BUILT_ON_ACTIONS)
def test_workflow_sets_up_through_the_composite_action(workflow):
    text = _text(workflow)
    assert "uses: ./.github/actions/ss-setup" in text, f"{workflow} does not use ss-setup"
    for inlined in ("jdx/mise-action", "actions/setup-python", "pip install -e ./cli"):
        assert inlined not in text, (
            f"{workflow} inlines `{inlined}`, but that step lives in .github/actions/ss-setup"
        )


@pytest.mark.parametrize("workflow", BUILT_ON_ACTIONS)
def test_workflow_does_not_inline_url_resolution(workflow):
    assert "validate_url()" not in _text(workflow), (
        f"{workflow} defines validate_url(), but URL resolution lives in .github/actions/ss-resolve-url"
    )


@pytest.mark.parametrize("workflow", BUILT_ON_ACTIONS)
def test_ss_setup_follows_checkout(workflow):
    """A later `pip install <tool>` or `npm ci` relies on the Python and
    node that ss-setup puts on PATH, so it has to come first."""
    names = re.findall(r"^      - name: (.+)$", _text(workflow), re.M)
    assert names[0].startswith("Checkout"), workflow
    assert names[1] == "Set up slopstopper", f"{workflow}: second step is {names[1]!r}"


@pytest.mark.parametrize("workflow", WORKFLOWS)
def test_every_local_action_reference_resolves(workflow):
    for ref in re.findall(r"uses: \./\.github/actions/([A-Za-z0-9_-]+)", _text(workflow)):
        assert (ACTIONS_DIR / ref / "action.yml").is_file(), f"{workflow} uses missing action {ref}"


@pytest.mark.parametrize("workflow", WORKFLOWS)
def test_every_step_output_reference_names_a_real_step(workflow):
    text = _text(workflow)
    ids = set(re.findall(r"^\s+id:\s*(\S+)\s*$", text, re.M))
    refs = set(re.findall(r"steps\.([A-Za-z0-9_-]+)\.", text))
    assert refs <= ids, f"{workflow} references steps that don't exist: {sorted(refs - ids)}"


def test_resolve_url_declares_the_outputs_the_workflows_read():
    action = (ACTIONS_DIR / "ss-resolve-url" / "action.yml").read_text(encoding="utf-8")
    # Only the `outputs:` block: an input name read as an output evaluates
    # empty just as silently as a typo would.
    outputs_block = re.search(r"^outputs:\n((?:  .*\n|\n)+)", action, re.M)
    assert outputs_block, "ss-resolve-url declares no outputs"
    declared = set(re.findall(r"^  ([a-z_]+):\n    description", outputs_block.group(1), re.M))
    assert declared == {"url", "skip", "use_local", "prod"}, declared
    used: set[str] = set()
    for workflow in BUILT_ON_ACTIONS:
        text = _text(workflow)
        if "ss-resolve-url" not in text:
            continue
        step_id = re.search(r"id: (\S+)\n(?:.*\n){0,5}?\s+uses: \./\.github/actions/ss-resolve-url", text)
        assert step_id, workflow
        used |= set(re.findall(rf"steps\.{re.escape(step_id.group(1))}\.outputs\.([a-z_]+)", text))
    assert used <= declared, f"workflows read outputs ss-resolve-url doesn't declare: {sorted(used - declared)}"


@pytest.mark.parametrize("workflow", BUILT_ON_ACTIONS)
def test_local_build_steps_follow_the_resolved_url(workflow):
    """The event → local-build table lives in ss-resolve-url. A workflow that
    gates its build/serve steps on `github.event_name` instead keeps a second
    copy, which drifts: the serve step stops matching the URL being audited."""
    text = _text(workflow)
    if "ss-resolve-url" not in text:
        return
    hand_rolled = re.findall(
        r"- name: ((?:Build|Start|Stop)[^\n]*)\n\s+if: [^\n]*github\.event_name == 'pull_request' \|\| github\.event_name == 'push'",
        text,
    )
    assert not hand_rolled, f"{workflow}: gate {hand_rolled} on steps.<id>.outputs.use_local"


def _playwright_install_steps(text: str) -> list[str]:
    """Each step block (from `- name:` to the next step) that installs Playwright browsers."""
    steps = re.split(r"(?m)^(?=\s+- name: )", text)
    return [step for step in steps if "playwright install" in step]


@pytest.mark.parametrize(
    "workflow", sorted(p.name for p in WORKFLOWS_DIR.glob("*.yml") if "playwright install" in p.read_text(encoding="utf-8"))
)
def test_playwright_install_is_time_boxed(workflow):
    # A stalled browser download once held two PR jobs for hours, because
    # nothing bounded the step short of GitHub's six-hour job limit (#386).
    steps = _playwright_install_steps(_text(workflow))
    assert steps, f"{workflow}: mentions `playwright install` but no step runs it"
    for step in steps:
        assert re.search(r"(?m)^\s+timeout-minutes:\s*\d+", step), (
            f"{workflow}: the Playwright install step has no `timeout-minutes`"
        )
        assert "timeout " in step, (
            f"{workflow}: the Playwright install step does not bound each attempt with `timeout`"
        )


def _issue_step_conditions(text: str) -> list[str]:
    """The `if:` text of every step that runs `slopstopper emit … --target issue`."""
    steps = re.split(r"\n      - ", text)
    return [step.split("if:", 1)[1].split("run:", 1)[0] for step in steps
            if "--target issue" in step and "if:" in step]


# One rule for every check that raises its own issue, dispatched or not: a
# branch dispatch never opens the main issue, and a dispatch on main
# opens or closes it like a push does.
@pytest.mark.parametrize("workflow", [w for w in WORKFLOWS if re.search(r"run: slopstopper emit \S+ --target issue", _text(w))])
def test_per_check_issues_come_only_from_non_pr_runs_on_main(workflow):
    conditions = _issue_step_conditions(_text(workflow))
    assert conditions, workflow
    for cond in conditions:
        assert "github.event_name != 'pull_request'" in cond and "github.ref == 'refs/heads/main'" in cond, (workflow, cond)


def test_the_csp_build_step_reads_both_header_keys_without_swallowing_a_cli_failure():
    """`format: auto` picks the meta adapter for a built HTML root, which only
    exists after a build, so the step must look at the source too; and a
    `$(…)` inside `[ ]` would hide a broken CLI."""
    text = _text("ss-hygiene-csp-exceptions-check.yml")
    step = text[text.index("- name: Build the site"):text.index("- name: Run CSP exceptions check")]
    assert 'format="$(slopstopper config get headers.format)"' in step
    assert 'source="$(slopstopper config get headers.source)"' in step
    assert '[ "$(slopstopper' not in step


# ── enterprise hardening ─────────────────────────────────────────
#
# A `${{ … }}` expression inside `run:` is pasted into the shell script
# before it runs, so a value an outsider can shape (a dispatch input, a
# branch name, a step output derived from one) is a command-injection
# vector. The value goes through `env:` instead, where the shell sees it
# as data. The emit lines' `steps.<id>.outcome == 'success' && 'pass' ||
# 'fail'` expressions can only ever produce `pass` or `fail`, so they are
# left alone; `with:` inputs of a local action are not shell.

INJECTABLE = re.compile(
    r"\$\{\{\s*(?:steps\.[A-Za-z0-9_-]+\.outputs\.url|github\.event\.inputs\.|inputs\."
    r"|github\.head_ref|github\.event\.pull_request\.(?:title|body|head\.ref)"
    r"|github\.event\.(?:issue|comment|review)\.(?:title|body))"
)


def _run_blocks(text: str) -> list[str]:
    """The text of every `run:` value: a one-liner, or a block scalar up to the next key at the step's indent."""
    blocks = []
    lines = text.splitlines()
    for i, line in enumerate(lines):
        m = re.match(r"^(\s+)run:\s*(.*)$", line)
        if not m:
            continue
        indent, rest = m.groups()
        if rest.strip() in ("|", "|-", ">", ">-"):
            body = []
            for nxt in lines[i + 1:]:
                if nxt.strip() and len(nxt) - len(nxt.lstrip()) <= len(indent):
                    break
                body.append(nxt)
            blocks.append("\n".join(body))
        else:
            blocks.append(rest)
    return blocks


@pytest.mark.parametrize("workflow", WORKFLOWS)
def test_no_run_line_interpolates_untrusted_input(workflow):
    offending = [b for b in _run_blocks(_text(workflow)) if INJECTABLE.search(b)]
    assert not offending, (
        f"{workflow} pastes an untrusted value into a shell with ${{{{ … }}}}; "
        f"pass it through `env:` and read it as \"$VAR\": {offending}"
    )


def test_the_injection_pattern_catches_the_shapes_it_is_for():
    assert INJECTABLE.search("task ss:x -- ${{ steps.url.outputs.url }}")
    assert INJECTABLE.search("echo ${{ github.event.inputs.url }}")
    assert INJECTABLE.search("git checkout ${{ github.head_ref }}")
    assert not INJECTABLE.search("--status ${{ steps.audit.outcome == 'success' && 'pass' || 'fail' }}")
    assert not INJECTABLE.search('task ss:x -- "$TARGET_URL"')


@pytest.mark.parametrize("workflow", [w for w in WORKFLOWS if re.search(r"steps\.[A-Za-z0-9_-]+\.outputs\.url", _text(w))])
def test_the_audited_url_reaches_the_task_through_env(workflow):
    """Every URL-driven check hands the resolved URL to its task as `"$TARGET_URL"`
    (or another env var), never as expression text."""
    text = _text(workflow)
    runs = [b for b in _run_blocks(text) if re.search(r"task ss:[a-z0-9:-]+ -- ", b)]
    assert runs, workflow
    for run in runs:
        assert re.search(r'task ss:[a-z0-9:-]+ -- "\$[A-Z_]+"', run), (workflow, run)


@pytest.mark.parametrize("workflow", WORKFLOWS)
def test_every_workflow_declares_top_level_permissions(workflow):
    """GITHUB_TOKEN defaults to whatever the repository setting says, which on
    an older repository is read-write everything. A top-level block makes
    the grant explicit and least-privilege in the file a reviewer reads."""
    assert re.search(r"^permissions:\n(?:  [a-z-]+: (?:read|write|none)\n)+", _text(workflow), re.M), (
        f"{workflow} has no top-level `permissions:` block"
    )


def _jobs(text: str) -> dict[str, str]:
    """{job id: the job's header text up to its `steps:`}."""
    jobs_at = text.index("\njobs:\n") + len("\njobs:\n")
    body = text[jobs_at:]
    out = {}
    for m in re.finditer(r"^  ([A-Za-z0-9_-]+):\n((?:    .*\n|\n)+?)(?=^  [A-Za-z0-9_-]+:|\Z)", body, re.M | re.S):
        out[m.group(1)] = m.group(2).split("    steps:", 1)[0]
    return out


@pytest.mark.parametrize("workflow", WORKFLOWS)
def test_every_job_has_a_timeout(workflow):
    """Without one, a hung scanner or a stalled download holds a runner for
    GitHub's six-hour default, and on a concurrency group blocks the next run."""
    jobs = _jobs(_text(workflow))
    assert jobs, f"{workflow}: no jobs found"
    for job, header in jobs.items():
        m = re.search(r"^    timeout-minutes:\s*(\d+)\s*$", header, re.M)
        assert m, f"{workflow}: job {job!r} has no job-level `timeout-minutes`"
        assert 1 <= int(m.group(1)) <= 60, (workflow, job, m.group(1))


@pytest.mark.parametrize("workflow", [w for w in WORKFLOWS if w not in {"ss-pr-summary.yml", "ss-workflow-failure-issue.yml"}])
def test_check_workflows_cancel_superseded_runs(workflow):
    """A newer push to the same ref makes the older run's verdict moot; the
    group is per workflow so one check never cancels another."""
    text = _text(workflow)
    assert "concurrency:\n  group: ${{ github.workflow }}-${{ github.ref }}\n  cancel-in-progress: true\n" in text, (
        f"{workflow} has no per-ref concurrency group"
    )


def test_the_two_reacting_workflows_keep_their_own_concurrency_semantics():
    """The summary must never cancel a render (the last one has every
    conclusion) and the failure tracker runs per failed workflow."""
    assert "cancel-in-progress: false" in _text("ss-pr-summary.yml")
    assert "cancel-in-progress: true" not in _text("ss-workflow-failure-issue.yml")


# ── no scanner is downloaded by a workflow ───────────────────────
#
# Every external tool the checks shell out to (snyk, gitleaks, lizard via
# the CLI) comes from a pin: mise.toml or the CLI's own dependencies,
# installed by ss-setup. A `pip install <tool>` or `curl … | tar` inside a
# workflow floats to whatever is current and is exactly what an
# enterprise reviewer is asked to rule out.

FLOATING_INSTALL = re.compile(
    r"pip install (?!-e \./cli)(?!\"\$spec\")|curl [^\n]*\|\s*(?:tar|sh|bash)\b|wget [^\n]*\|\s*(?:tar|sh|bash|gpg)\b|apt-get install"
)


@pytest.mark.parametrize("workflow", WORKFLOWS)
def test_no_workflow_installs_a_tool_outside_the_pins(workflow):
    hits = [b for b in _run_blocks(_text(workflow)) if FLOATING_INSTALL.search(b)]
    assert not hits, f"{workflow} installs a tool at a floating version: {hits}"


# ── the Snyk workflows carry what the checks read ─────────────────

SNYK_WORKFLOWS = [
    "ss-security-sast-check.yml",
    "ss-security-dependencies-check.yml",
    "ss-security-iac-check.yml",
    "ss-security-sbom-check.yml",
]


@pytest.mark.parametrize("workflow", SNYK_WORKFLOWS)
def test_snyk_run_steps_get_the_token_and_org_from_secrets_and_vars(workflow):
    text = _text(workflow)
    for step in [s for s in re.split(r"(?m)^(?=      - name: )", text) if re.search(r"task -x ss:security:|task ss:security:monitor", s)]:
        assert "SNYK_TOKEN: ${{ secrets.SNYK_TOKEN }}" in step, (workflow, step[:60])
        assert "SNYK_ORG: ${{ vars.SNYK_ORG }}" in step, (workflow, step[:60])
        assert "SNYK_API: ${{ vars.SNYK_API }}" in step, (workflow, step[:60])
    assert "SNYK_TOKEN" in text.split("\non:\n", 1)[0], f"{workflow}: the header comment must say it needs SNYK_TOKEN"


def test_the_sast_workflow_uploads_the_sarif():
    text = _text("ss-security-sast-check.yml")
    assert ".ss/reports/sast/sast-report.sarif" in text and ".ss/reports/sast/sast-report.md" in text
    assert "upload-sarif" in text, "say how an org with GHAS gets the SARIF into code scanning"
    assert "uses: github/codeql-action" not in text, "not added by default: no new third-party action"


def test_the_sbom_is_kept_as_a_compliance_artifact():
    text = _text("ss-security-sbom-check.yml")
    assert ".ss/reports/sbom/" in text
    assert re.search(r"retention-days: 90", text)


def test_snyk_monitor_runs_only_for_main_and_after_the_emit_steps():
    text = _text("ss-security-dependencies-check.yml")
    at = text.index("- name: Register the project with Snyk (main only)")
    assert at > text.rindex("slopstopper emit security:dependencies")
    step = text[at:]
    assert "github.event_name != 'pull_request' && github.ref == 'refs/heads/main'" in step
    assert "run: task ss:security:monitor" in step
