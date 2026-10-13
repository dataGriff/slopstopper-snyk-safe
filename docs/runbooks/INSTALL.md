# Adopting SlopStopper

Everything an adopter needs once: prerequisites, the install from a tagged
checkout, what `install.sh` writes, what each check needs from you, the
Snyk settings, how to run the suite, configure it and move the CLI pin.
The [README](../../README.md) holds the install commands; this is the rest.

## Prerequisites

The CLI is installed from a git tag of this repository (locally by
`install.sh`, in CI by `.github/actions/ss-setup`); each check
subprocess-invokes its own tool (`snyk`, `gitleaks`, `node`). `mise.toml`
pins every tool version, and `slopstopper doctor` reports what's missing
or won't run, plus which `ss-*` workflows GitHub has disabled for inactivity.

- **[mise](https://mise.jdx.dev)** is required. It installs the pinned
  `task`, `node`, `snyk` and `gitleaks`, activated per-directory (CI uses
  `jdx/mise-action` against the same file)
- **Python 3.11+** must be on PATH: the CLI is a Python package
- **git** and **bash** to clone the tagged checkout and run `install.sh`
- **A Snyk account** with a service-account token for the four Snyk
  checks ([SNYK.md](../security/SNYK.md) walks through creating it)

Per-check tools, all pinned in `mise.toml` (skip any check your profile drops):

| Tool | Needed by | Pinned as |
| ---- | --------- | --------- |
| `snyk` | `security:sast`, `security:dependencies`, `security:iac`, `security:sbom`, `task ss:security:monitor` | `"npm:snyk"` (exact version) |
| `gitleaks` | `security:secrets` | `"gitleaks"` (exact version) |
| `node` 20+ | the Snyk CLI's npm wrapper, the reliability checks (Playwright + Lighthouse), `slopstopper serve` | `"node"` |
| `task` | the `task ss:*` shim layer | `"task"` |
| `gh` | `slopstopper emit` (PR comments + issues from CI; present on GitHub runners) | not pinned |

## Install from a tagged checkout

Never pipe the installer from the network. Clone the exact release you
reviewed, then run its `install.sh` from inside the repository that
should get the suite:

```bash
git clone --branch v0.1.0 https://github.com/dataGriff/slopstopper-snyk-safe.git
cd <your-repo>
bash ../slopstopper-snyk-safe/install.sh                  # default: profile ui
bash ../slopstopper-snyk-safe/install.sh --profile api    # or library
```

The installer records the tag it ran from under `cli:` in the seeded
`.slopstopper.yml`, which is what CI installs:

```yaml
cli:
  source: https://github.com/dataGriff/slopstopper-snyk-safe.git
  ref: v0.1.0
```

`ss-setup` installs `slopstopper-cli @ git+<source>@<ref>#subdirectory=cli`.
An enterprise whose runners cannot reach github.com mirrors the repository
and points `source` at the mirror, or builds the wheel once (`pip wheel ./cli`),
publishes it to an internal index and sets the `SLOPSTOPPER_CLI_SPEC`
variable to a full pip requirement such as `slopstopper-cli==0.1.0`; the
override wins over the yml.

## What gets installed

Everything SlopStopper owns lives under the `ss` namespace so it can't clash with files you already have.

| Item | Description |
| ---- | ----------- |
| `.github/workflows/ss-*.yml` | Security, hygiene, reliability and operational workflows, SHA-pinned, least-privilege, with timeouts |
| `.github/actions/ss-*/` | Composite steps the workflows share (`ss-setup` installs the toolchain and the pinned CLI; `ss-resolve-url` turns an event into the URL to audit); replaced on refresh |
| `Taskfile.ss.yml` | Thin `task ss:*` shims that call the CLI, for the local dev loop |
| `Taskfile.yml` | Created if missing (else: prints the include block to paste in) |
| `.githooks/pre-push` | Pre-push hygiene gate (`--no-hooks` opts out; defers to husky/lefthook/pre-commit) |
| `mise.toml` | Toolchain pins (`task`, `node`, `snyk`, `gitleaks`), read locally and in CI |
| `.slopstopper.yml` | Config starter with the `cli:` pin, profile, URLs, headers and page lists (never overwritten) |
| `README.md`, `AGENTS.md`, `CLAUDE.md`, `docs/README.md` | AGENTS.md-first entry-file scaffolds, seeded only when absent |
| `.claude/skills/slopstopper-*/` | The `slopstopper-install` and `slopstopper-triage` Claude Code skills (`--no-skills` opts out) |
| `.ss/reports/` | The `.gitignore`d directory the CLI writes reports to |
| `package.json` | Created (or `devDependencies` merged into an existing file) |

Bundled Playwright specs, lighthouserc dev/prod, and the local static server live inside the CLI package. To customise one, `slopstopper templates eject <name>` copies it into `.ss/`.

## What you get

There are four feedback loops, all on every PR and push to `main`. The `ui` profile runs all of them; `api` and `library` drop the browser checks:

| Loop | What it does | Tools | Docs |
| ---- | ------------ | ----- | ---- |
| 🔒 **Security** | Snyk Code (SAST), dependency vulnerabilities and licences, IaC misconfigurations, an SBOM artifact, secrets detection, API header + CORS audit | Snyk CLI, Gitleaks, stdlib Python | [Security →](../security/README.md) |
| 🧹 **Hygiene** | Complexity caps, docs structure/accuracy/size and entry-file checks, OpenAPI drift, auto-labelled PRs | Lizard, stdlib Python | [Hygiene →](../hygiene/README.md) |
| ✅ **Reliability** | E2E + smoke tests, broken-link audits, accessibility (WCAG 2.1 AA), Core Web Vitals, SEO metatags, llms.txt, robots.txt, sitemap.xml, API health + latency | Playwright, axe-core, Lighthouse CI, stdlib Python | [Reliability →](../reliability/README.md) |
| 🤖 **Operational** | One rolling PR comment summarises every check; a failed workflow on `main` auto-raises an issue | GitHub Actions | [PR feedback →](../architecture/PR_FEEDBACK.md) |

## What each check needs

Three portability layers. Layer 1 runs on install; layers 2 and 3 need a little config:

| Layer | Checks | What you provide |
| ----- | ------ | ---------------- |
| **1. Static analysis** (any code) | Secrets, Complexity, Doc Structure/Accuracy/Size, Entry Files, Auto-label PRs, Workflow-failure tracker | Nothing. It works out of the box |
| **2. Snyk** (any code) | SAST, Dependencies, IaC, SBOM | The `SNYK_TOKEN` secret; `SNYK_ORG` and, for a regional tenant, `SNYK_API` as variables. Without the token these four exit 2 (could not run), not pass |
| **3. Deployed surface** (need a URL) | Smoke, E2E, Broken Links, Accessibility, Core Web Vitals, SEO Metatags, llms.txt, robots.txt, sitemap.xml, API Health/Latency/Headers, OpenAPI Drift | `urls.production` / `urls.preview` in `.slopstopper.yml` ([per-check env vars](../reliability/README.md) also work) |

To drop a check, delete its workflow or list it under `workflows.disabled`. Re-runs respect both. If the repo is not a website, `--profile api` (or `library`) installs only the applicable checks (see [Profiles](../architecture/PROFILES.md)).

## Same commands, both loops

`task ss:<category>:<check>` is the canonical interface. Humans, agents and CI all go through it, so the suite shares one invocation surface with the rest of your codebase. The shims call `slopstopper-cli` under the hood; pass `--no-task` to `install.sh` to skip Task and have workflows call the CLI directly.

```bash
task ss:hygiene:complexity                    # the canonical form
task ss:reliability:accessibility -- http://localhost:8080
task ss:security:sast                         # CI runs the same line (needs SNYK_TOKEN)
slopstopper run hygiene:complexity            # underlying CLI if you skip Task
```

## Configure

Most checks work out of the box. To wire up the full suite:

**[`.slopstopper.yml`](../../.slopstopper.yml.example)** at the repo root is the config file. `install.sh` seeds a starter (the `cli:` pin, `profile`, URLs, page lists, `headers.source: null`) so the first PR is green; the link is the full schema. Survives reinstalls.

**Repo secrets and variables** (under Settings → Secrets and variables → Actions):

- `SNYK_TOKEN` (secret): the Snyk service-account token. Required by the four Snyk checks.
- `SNYK_ORG` (variable): the Snyk organisation slug, or set `security.snyk.org` in `.slopstopper.yml`.
- `SNYK_API` (variable): only for a regional tenant, e.g. `https://api.eu.snyk.io`.
- `SLOPSTOPPER_CLI_SPEC` (variable): only when the CLI comes from an internal index rather than the `cli:` pin.

Setup for all of these, the data-flow statement and the egress allowlist: [SNYK.md](../security/SNYK.md).

**Thresholds** (the four `fail_on` gates, the complexity ceiling, doc size, entry-file token budgets and the rest) are keys in [`.slopstopper.yml.example`](../../.slopstopper.yml.example). Lighthouse budgets ship inside the CLI; override via `.ss/`.

**Docs layout.** The docs checks enforce AGENTS.md-first. `AGENTS.md` stays under ~2k tokens with explicit routes, `CLAUDE.md` is exactly `@AGENTS.md`, `docs/README.md` is the map and every doc is routed. A fresh install seeds the scaffolds; an existing repo gets paste-ready fixes in the `entry-files` report.

**Branch protection.** Make the five security workflows required status checks on `main` so the suite gates merges rather than commenting on them ([SNYK.md](../security/SNYK.md) lists the names).

## Update

Refresh workflows + shims from a newer tag (config + customisations survive). Check out the tag, re-run its installer, then move `cli.ref` to the same tag:

```bash
git -C ../slopstopper-snyk-safe fetch --tags
git -C ../slopstopper-snyk-safe checkout v0.2.0
bash ../slopstopper-snyk-safe/install.sh
```

See [UPGRADE_CLI.md](./UPGRADE_CLI.md) for the pin, and [INSTALL_SKILLS.md](./INSTALL_SKILLS.md) to refresh only the Claude Code skills.

## Releases and provenance

Releases are annotated git tags of this repository; the changelog section for each is in [`CHANGELOG.md`](../../CHANGELOG.md). Pinning `cli.ref` to a tag means the CLI you run is the commit that tag names, reviewable in this repository's history; a mirror or internal wheel keeps the same guarantee inside your network. The CLI has one third-party Python dependency, `lizard` (for `hygiene:complexity`); every other tool runs via `subprocess` at the version `mise.toml` pins. Credits and licences live in [`ATTRIBUTIONS.md`](../../ATTRIBUTIONS.md).
