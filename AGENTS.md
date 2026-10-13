# SlopStopper agent guide

Open standard for agents, AI assistants and automation tools working in
this repo (conformant with [agents.md](https://agents.md)). `CLAUDE.md` is
`@AGENTS.md`. This file carries what most tasks need; the routes at the
end say when to open anything else.

## What this repo is

**`slopstopper-cli`** under `cli/` holds every check's logic. It is not
on PyPI: adopters pin an exact git tag of this repo under `cli:` in
`.slopstopper.yml` and pull the suite in with the `ss-*.yml` workflows,
`task ss:*` shims and `install.sh`, run from a tagged checkout. The
security layer is Snyk (Code, Open Source, IaC, SBOM) plus Gitleaks. A
change that affects adopters usually touches the CLI, a workflow, the
installer, the docs and both skills.

## Ground rules

1. **`task ss:<category>:<action>` is the canonical interface.** Humans,
   agents and CI all go through it; `slopstopper run …` is the
   implementation each shim calls. Run `task --list` before writing a
   one-off command.
2. **One definition of green.** `task ss:hygiene:test` (docs-size,
   docs-structure, docs-accuracy, entry-files, CSP drift, complexity) is
   what the pre-push hook and CI run. Run it before committing; run the
   CLI tests when `cli/` changes.
3. **Every check keeps the exit-code contract:** 0 ran clean, 1 ran and
   failed, 2 could not run. CI gates on it directly. A Snyk check with no
   `SNYK_TOKEN` is a 2, never a silent pass.
4. **Budgets are the feature.** When `entry-files` or `docs-structure`
   fails, move content deeper into `docs/` and route it; those budgets
   (tokens, lines, hops) never move. `docs-size`'s totals are advisory
   and sized to the repo. Change them only in a commit that says why.
5. **Conventional Commits** (`feat:`, `fix:`, `docs:`, `chore:` …).
   Releases are annotated git tags cut by hand (`docs/runbooks/RELEASE.md`
   below). Third-party actions are pinned to a commit SHA, never a tag.
6. **This repo IS the CLI.** `mise.toml` pins the scanners (`snyk`,
   `gitleaks`, `task`, `node`), never `slopstopper-cli`; workflows
   install `cli/` editable and run HEAD. Adopters pin a git tag.

## Commands

| Command                                  | Does                                                       |
| ---------------------------------------- | ---------------------------------------------------------- |
| `task --list`                            | every runnable task                                        |
| `task ss:hygiene:test`                   | the static hygiene suite (what the pre-push hook runs)     |
| `task ss:security:scan`                  | Snyk Code, dependencies, IaC, SBOM, secrets (needs `SNYK_TOKEN`) |
| `task ss:security:monitor`               | `snyk monitor`: register the dependency graph on main      |
| `task ss:reliability:<check> -- <url>`   | a browser check against a running site                     |
| `task contributing:test`                 | the pytest suite for `slopstopper-cli` (`-- -k <expr>` filters) |
| `task contributing:setup`                | npm deps, the CLI editable in `cli/.venv`, Chromium, the hook |
| `slopstopper checks list` / `doctor`     | what exists / which external tools are missing             |

## Layout

```
AGENTS.md            this file (CLAUDE.md is @AGENTS.md)
docs/                topic docs, one per concern; docs/README.md is the map
docs/security/SNYK.md the enterprise rollout page: service account, tenant, data flow, egress
cli/slopstopper/     the CLI: checks/<check>.py (_snyk.py is shared), data/, profiles.json
cli/tests/           pytest; test_check_registration.py enforces every surface a check needs
.github/workflows/   ss-*.yml, each `uses: ./.github/actions/ss-setup` then one task
Taskfile.ss.yml      the `task ss:*` shims  ·  Taskfile.yml includes them under `ss`
install.sh           adopter installer, run from a tagged checkout (seeds templates, pins)
.claude/skills/      slopstopper-install + slopstopper-triage, shipped to adopters
mise.toml            exact pins for task, node, snyk and gitleaks (local and CI read it)
.slopstopper.yml     this repo's config; .slopstopper.yml.example is the schema
```

## Conventions most changes touch

- **Naming follows the docs categories:** task `hygiene:complexity`,
  workflow `ss-hygiene-complexity-check.yml`, docs under `docs/hygiene/`.
- **Checks are stdlib-only Python**, subprocess-invoke their tool, write
  `.ss/reports/<check>/`, and carry a `Configuration` block in the module
  docstring for every `config.get("…")` key they read.
- **A new or renamed check is registered in ~11 places** (workflow,
  shim, installer, profiles, badge label, PR summary, docs, tests, labeler,
  both skills, README). The route below lists them; the registration
  test fails on any you miss.
- **Docs state the present; git holds the past.** No "previously we…"
  sections; the commit message carries the story.
- **Headers/CSP:** whatever `headers.source` names is the single source
  of truth; a CSP change must land in the exceptions doc too.
- **Workflows are least-privilege and SHA-pinned**, carry a timeout, and
  pass URLs and tokens through `env:`, never interpolated into shell.

## Routes: read before you act

| When you are…                                                              | Do this                                                                                   |
| -------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------- |
| changing anything that ships to adopters (a check, workflow, skill, config key, template) | Read [docs/contributing/CHANGE_MAP.md](./docs/contributing/CHANGE_MAP.md) before you start. It lists what each kind of change must touch |
| about to add a workflow, task, report path or external resource            | Read [docs/contributing/PITFALLS.md](./docs/contributing/PITFALLS.md) first. It lists what has bitten before |
| fixing a failing docs or hygiene check                                      | Read [docs/hygiene/README.md](./docs/hygiene/README.md). It gives each check's rules, knobs and report |
| setting up or rotating Snyk credentials, or rolling the suite out to an enterprise | Read [docs/security/SNYK.md](./docs/security/SNYK.md). It covers the service account, `SNYK_TOKEN` / `SNYK_ORG` / `SNYK_API`, data flow and egress |
| cutting a release or moving an adopter's `cli.ref` pin                      | Read [docs/runbooks/RELEASE.md](./docs/runbooks/RELEASE.md). It is the tag-and-bump procedure |
| installing or refreshing slopstopper in another repo                        | Read [.claude/skills/slopstopper-install/SKILL.md](./.claude/skills/slopstopper-install/SKILL.md), the install/refresh playbook |
| diagnosing a failing slopstopper check in any repo                          | Read [.claude/skills/slopstopper-triage/SKILL.md](./.claude/skills/slopstopper-triage/SKILL.md). It walks workflow → task → report → fix |
| doing any task not covered above                                            | Read [docs/README.md](./docs/README.md) before you start; do not guess a convention        |

`ss:hygiene:entry-files` keeps this file under ~2k tokens and every route
above explicit; `ss:hygiene:docs-structure` keeps every doc routed.
