# Contributing

This document defines the default way to contribute to this project.

## Prerequisites

- Install [mise](https://mise.jdx.dev) and run `mise install` at the repo root: it installs `task`, `node`, the `snyk` CLI and `gitleaks` at the versions `mise.toml` pins
- Verify installation: `task --version`
- For the Snyk checks, export `SNYK_TOKEN` (and `SNYK_ORG` / `SNYK_API` if your org needs them); [`docs/security/SNYK.md`](../security/SNYK.md) explains all three

## Canonical interface: Taskfile (the `ss` namespace)

**Agents and contributors must use `task ss:<name>` instead of raw commands**
wherever possible. SlopStopper's task definitions live in
[`Taskfile.ss.yml`](../../Taskfile.ss.yml); the root
[`Taskfile.yml`](../../Taskfile.yml) is a thin integration layer that imports
them under the `ss` namespace via `includes:`. This keeps SlopStopper's
tasks isolated from anything a consumer has in their own root Taskfile.

The Taskfile is the single source of truth for build, test, lint and scan
operations so developers, AI agents and CI all run the same thing, with
no drift and no version skew.

Run `task --list` for the full set. The most-used ones:

| Task | What it does |
| ---- | ------------ |
| `task contributing:setup` | `npm install`, the CLI as an editable install in `cli/.venv` (the one every contributor task and pytest use), Playwright's Chromium, and the pre-push hook |
| `task contributing:test` | The CLI's pytest suite under `cli/tests/` (what `ci-cli.yml` runs) |
| `task contributing:lint` | markdownlint over `docs/` (advisory, not a CI gate) |
| `task ss:hygiene:complexity` | Cyclomatic complexity check (Lizard) |
| `task ss:hygiene:entry-files` | Enforce <2k token budget on entry files |
| `task ss:hygiene:docs-accuracy` | Catch broken links + stale task/workflow refs |
| `task ss:security:sast` | Static security scan (Snyk Code) |
| `task ss:security:dependencies` | Dependency vulnerability + licence scan (Snyk Open Source) |
| `task ss:security:iac` | Infrastructure-as-code scan (Snyk IaC) |
| `task ss:security:sbom` | Software bill of materials (Snyk SBOM) |
| `task ss:security:secrets` | Secrets detection (Gitleaks) |
| `task ss:security:scan` | All of the above security checks in sequence |
| `task ss:reliability:smoke` | Smoke tests against a URL |
| `task ss:reliability:accessibility` | axe-core WCAG 2.1 AA audit |
| `task ss:reliability:cwv` | Lighthouse CI / Core Web Vitals |

For CI-style output (retries, HTML reports, fail-on-flake), pass
`--ci` through the Task `--` separator the same way the workflows do,
for example `task ss:reliability:accessibility -- --ci`. The same Task command
runs in both loops, and the flag toggles the run shape.

## Exit codes

Every check returns the same three codes, and CI gates on them directly,
so there is no per-check post-processing step deciding the verdict.

| Code | Meaning |
|---|---|
| `0` | Ran; nothing to fail on. Includes a graceful skip, because an unconfigured check is not a failing check |
| `1` | Ran; the repo failed it. Findings over a threshold, drift, a budget exceeded |
| `2` | Could not run. Missing tool, missing token, missing input, unreadable report, an argument the check has no parser for |

The full contract, and why it matters, is in the `slopstopper.checks`
package docstring; `cli/tests/test_exit_code_contract.py` checks every
check's docstring against what its code can actually return. The Snyk
checks lean on the third code: a scan that produced no readable report is
a 2, never a pass.

## Machine-readable output

The commands below take `--json` and then print one JSON value and
nothing else on stdout, so a skill, a workflow or a script reads it
instead of parsing the decorated text. The others (`discover`,
`templates`, `badges`) already print a single
plain value a shell can use. These shapes are a contract like the exit
codes: add a key freely, never rename or remove one without a note in the
CLI README. `cli/tests/test_cli.py` pins them.

| Command | Prints |
|---|---|
| `run --json <check>` | `{check, exit_code, status, failures, reports}`. `status` names the exit code (`pass`, `fail`, `cannot-run`); `failures` is the report's `- ❌` bullets, the same list the PR comment shows; `reports` is every file the check wrote this run under its report directory. A report an earlier run left behind counts as neither, so a check that could not run reports no failures. The check's own output goes to stderr |
| `doctor --json` | `{ok, exit_code, tools, workflows}`. Each tool row has `tool`, `needed_by`, `path`, `version`, `status` (`ok`, `missing`, `broken`, `skipped`), `problem` and `hint`; `workflows.checked` is false when GitHub could not be asked, else `workflows.disabled` lists `{name, state, inactive, fix}` |
| `profile list --json` | a list of `{name, default, summary, detail, disables, disables_without_api}` |
| `profile show --json` | `{profile, origin, configured, warning, summary, disabled: [{workflow, source}], re_enabled}` |
| `profile detect --json` | `{suggested, reason, active, matches}` |
| `profile expand <n> --json` | `{profile, workflows}`, or `{error}` with exit 2 |
| `profile disabled --json` | `{disabled}` |
| `config get <key> [<default>] --json` | the value as JSON: a list stays a list, an unset key prints the default (an explicit `""` included), or `null` when none was given |
| `checks list --json` | a list of `{name, summary, workflow, disabled}` |

## Quick verification checklist

Run these before opening a PR. Each one mirrors the equivalent CI check
exactly:

```bash
task contributing:test                # CLI pytest suite (ci-cli.yml)
task ss:hygiene:test                  # Full hygiene suite (also the pre-push hook)
task ss:security:scan                 # Snyk Code, dependencies, IaC, SBOM, secrets (needs SNYK_TOKEN)
```

Or call the CLI directly if you'd rather skip the `task` shim layer
(`slopstopper run security:sast`, etc.), since
the shims are thin and call the same code path either way.

## The CLI test suite (pytest)

The product is the Python package under [`cli/`](../../cli/); its tests
live in `cli/tests/` and are the first thing to run after any change to a
check, the installer, or a workflow (several tests, such as `cli/tests/test_workflow_triggers.py`,
read the workflow files and the skills as fixtures, so a YAML edit
can fail a Python test).

```bash
task contributing:test                       # whole suite
task contributing:test -- -k docs_size       # filter (any pytest args after --)
task -t cli/Taskfile.yml test                # same thing, without the alias
```

The target creates and reuses a project-local venv at `cli/.venv/`
(gitignored) with the `test` extra installed, so it doesn't fight system
Python under PEP 668. CI runs the identical target from
[`ci-cli.yml`](../../.github/workflows/ci-cli.yml).

## Workflow

- Create a focused branch for each change.
- Keep pull requests small and reviewable.
- Link changes to relevant decisions or issues when applicable.

## Pre-Merge Checks

- Verify tests and checks pass.
- Confirm documentation is updated when behavior or structure changes.
- Ensure no accidental scope creep is included.

## Actions are pinned to commits

Every third-party action in the workflows and composite actions is pinned to a commit as `uses: owner/repo@<sha> # vN`, never to a tag. A tag can be moved; a workflow that holds a Snyk token and writes to PRs and issues should not run whatever a moved tag points at. Dependabot (`.github/dependabot.yml`) opens a weekly PR when a new release lands and updates both the SHA and the version comment, so pinning costs nothing day to day. `cli/tests/test_action_pins.py` fails on any floating ref. The scanner versions follow the same rule in `mise.toml`: exact, moved by a reviewable PR.

## Workflows are least-privilege

Each `ss-*.yml` declares only the permissions its steps need (`contents: read` plus `pull-requests: write` / `issues: write` where it comments), carries a `timeout-minutes`, and passes every URL and token to the shell through `env:` rather than `${{ }}` interpolation inside `run:`. A new workflow keeps all three; `docs/contributing/PITFALLS.md` has the details.

## Commit conventions

[Conventional Commits](https://www.conventionalcommits.org/):
`<type>(<scope>): <description>` where type is one of `feat`, `fix`,
`docs`, `style`, `test`, `chore`, `refactor`. Examples:

- `feat(sbom): accept spdx2.3+json`
- `fix(install): record the tag in cli.ref`
- `docs(agents): refresh the Snyk route`

Releases are hand-cut annotated tags; the prefixes decide the bump
([`docs/runbooks/RELEASE.md`](../runbooks/RELEASE.md)).

## Coding Conventions

- Prefer clarity over cleverness.
- Keep changes minimal and localized.
- Follow existing project style and naming patterns.

## Routes

| When you are…                                                              | Do this                                                                             |
| -------------------------------------------------------------------------- | ----------------------------------------------------------------------------------- |
| changing anything that ships to adopters (a check, workflow, skill, config key, template) | Read [CHANGE_MAP.md](CHANGE_MAP.md) before you start. It lists every surface each kind of change must touch |
| about to add a workflow, task, report path or external resource            | Read [PITFALLS.md](PITFALLS.md) first for workflow naming, the task namespace, workflow hardening and CSP |
