# slopstopper-cli

A Python CLI plus GitHub Actions workflows that run Snyk-backed security scanning, secrets detection, hygiene and reliability checks on every PR. This package is the CLI; the enterprise fork of slopstopper that ships it lives at <https://github.com/dataGriff/slopstopper-snyk-safe>.

> **Not on PyPI.** The CLI is installed from a git tag of this repository. The CI workflows under `.github/workflows/ss-*.yml` start with `.github/actions/ss-setup`, which installs `slopstopper-cli @ git+<cli.source>@<cli.ref>#subdirectory=cli` from the `cli:` block in `.slopstopper.yml` (or the pip requirement in `SLOPSTOPPER_CLI_SPEC` for an internal index), then call `slopstopper run <category>:<check>`, the same code path you use locally.

## Install

Pinned, for a repo you'll keep (the installer writes the workflows, shims and config, and CI installs the CLI from the pinned tag):

```bash
git clone --branch v0.1.0 https://github.com/dataGriff/slopstopper-snyk-safe.git
cd <your-repo>
bash ../slopstopper-snyk-safe/install.sh
```

Standalone, to try the CLI from an exact tag:

```bash
pip install "slopstopper-cli @ git+https://github.com/dataGriff/slopstopper-snyk-safe.git@v0.1.0#subdirectory=cli"
slopstopper --version
slopstopper checks list
slopstopper doctor          # which external tools (snyk, gitleaks, node, gh) are missing
```

The Snyk checks need the `snyk` CLI (pinned in `mise.toml` as `npm:snyk`) and a `SNYK_TOKEN` in the environment; without the token they exit 2 (could not run). `docs/security/SNYK.md` in the repo covers the service account, `SNYK_ORG`, `SNYK_API` and what each product sends to Snyk.

Development (editable, from a clone of this repo):

```bash
pip install -e ./cli
slopstopper run hygiene:docs-size
```

## CLI surface

```text
slopstopper run [--json] <category>:<check>    # run a check, write reports under .ss/reports/
slopstopper emit <c>:<n> --target {pr-comment,issue}
slopstopper summary --target pr-comment        # one rolling PR comment covering every check
slopstopper discover <check> --event <e>       # resolve pages.<check> from sitemap / changed / list
slopstopper config get <key> [<default>] [--json]   # read .slopstopper.yml
slopstopper templates {list, path <n>, eject <n>}   # inspect / customise bundled templates
slopstopper serve                              # bundled static server for the local browser-check loop
slopstopper checks list [--category <c>] [--json]
slopstopper doctor [--json]                    # verify external tools are installed
slopstopper profile {list, show, expand <n>, disabled, detect} [--json]   # project-shape profile (ui / api / library)
slopstopper badges                             # the README badge block for the installed workflows
slopstopper --quiet …                          # suppress decorative output (CI logs)
```

On `run`, `doctor`, `profile`, `config get` and `checks list`, `--json` prints one JSON value and nothing else on stdout (`run` sends the check's own output to stderr), so a script or an agent reads a verdict without parsing the decorated text. The shapes are a contract, documented with the exit codes in [`docs/contributing/README.md`](https://github.com/dataGriff/slopstopper-snyk-safe/blob/main/docs/contributing/README.md).

`slopstopper` with no args prints a banner of the commands above plus a quick-start block.

## Run the tests

```bash
# From the repo root:
task -t cli/Taskfile.yml test

# Or from cli/:
task test

# Filter to a single test:
task -t cli/Taskfile.yml test -- -k docs_size
```

From the repo root `task contributing:test` is an alias for the same target. It is also what CI runs (see [`ci-cli.yml`](https://github.com/dataGriff/slopstopper-snyk-safe/blob/main/.github/workflows/ci-cli.yml)), so local and CI invocations stay in sync. The Taskfile creates and reuses a project-local venv at `cli/.venv/` (gitignored) so it doesn't conflict with system Python under PEP 668.

That workflow is slopstopper-internal. It is **not** part of the distributed `ss-*-check.yml` suite and is never seeded into adopter repos.

## Layout

- `slopstopper/cli.py` is the argparse dispatcher and prints the bare-invocation banner
- `slopstopper/output.py` holds the shared formatters (running / success / warn / error / footer / `--quiet`)
- `slopstopper/config.py` reads `.slopstopper.yml` (stdlib-only YAML subset)
- `slopstopper/templates.py` resolves bundled templates and backs the `templates {list, path, eject}` API
- `slopstopper/emit.py` wraps the `gh` CLI to post PR comments and main-branch issues
- `slopstopper/discovery.py` resolves the pages to audit for reliability checks
- `slopstopper/profiles.py` holds the project-shape profiles. It resolves which workflows a repo carries with `(profile.disables − workflows.enabled) ∪ workflows.disabled` (explicit `workflows.disabled` wins if the same workflow is listed in both), plus the check → workflow map. `install.sh` imports this module directly, so it is the only implementation
- `slopstopper/checks/` holds one module per check; the registry is in `__init__.py`. `_snyk.py` is what the four Snyk wrappers (`sast.py`, `dependencies.py`, `iac.py`, `sbom.py`) share: locating the CLI, the environment (`SNYK_TOKEN`, `SNYK_ORG`, `SNYK_API`, analytics off), the severity scale, the `fail_on` gate and reading the report
- `slopstopper/data/` holds the bundled Playwright specs, lighthouserc dev/prod, server.js and `profiles.json`

## Adding a check

A check is more than a module. It needs a workflow, a Task target, profile classification, a badge label, docs and a skills entry. Follow the single checklist in the **"New quality check"** row of [`docs/contributing/CHANGE_MAP.md`](https://github.com/dataGriff/slopstopper-snyk-safe/blob/main/docs/contributing/CHANGE_MAP.md) rather than a summary here. Inside this package, `slopstopper/checks/<name>.py` exposes `run(args) -> int` (0 pass, 1 findings, 2 could-not-run) and registers in `checks/__init__.py`'s `REGISTRY`. Its docstring's first line is what `slopstopper checks list` prints. It prints through `from slopstopper import output` so `--quiet` works, and declares a `META` dict if `emit.py` should post it.

## Skills for agents

The duo under [`.claude/skills/slopstopper-{install,triage}/`](https://github.com/dataGriff/slopstopper-snyk-safe/tree/main/.claude/skills) is the long-form playbook for Claude Code agents working with this CLI. Update them when you add or rename a check, env var, `task ss:*` target, or profile.

## Acknowledgements

The only third-party Python dependency of slopstopper-cli is [`lizard`](https://pypi.org/project/lizard/), the complexity engine behind `hygiene:complexity`, which must live in the same interpreter the CLI runs from. Every other tool (the Snyk CLI, Gitleaks, Playwright, axe-core, Lighthouse) is invoked via `subprocess` only. Full credit, licences and upstream links for every tool we drive, and the attribution to the upstream project this is forked from, live in [`ATTRIBUTIONS.md`](https://github.com/dataGriff/slopstopper-snyk-safe/blob/main/ATTRIBUTIONS.md).

## License

MIT; the full text is in [LICENSE](./LICENSE).
