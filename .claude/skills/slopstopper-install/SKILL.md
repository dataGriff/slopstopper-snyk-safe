---
name: slopstopper-install
description: Install slopstopper into a repo for the first time OR refresh an existing slopstopper install. Use when a user asks to add slopstopper, install the slopstopper quality suite, refresh slopstopper, upgrade slopstopper, move the slopstopper-cli tag pin, pull in new slopstopper checks, set up Snyk for CI (SNYK_TOKEN, SNYK_ORG, SNYK_API), see what's new in slopstopper, or tailor which checks apply to a UI / API / library repo. Covers pre-flight, picking a project-shape profile, the tagged-checkout install command, idempotent re-run on existing installs, post-install URL and Snyk config, AGENTS.md-first docs layout setup, README badges, customizations that get wiped on refresh, new-knob discovery, and a local-first verification loop that closes every check before pushing. For per-check failure diagnosis use the slopstopper-triage skill.
---


# Install or refresh slopstopper

You're being asked to install slopstopper into a repo, or refresh an existing install. Both flows live here: the steps are mostly the same (`install.sh` is idempotent), with a refresh-only section for diffing upstream and re-applying customizations that get wiped on re-run.

This file is the map. Each step below is a short summary plus the reference file that holds the full playbook for it. **Read a reference only when you reach that step.** The references are long and this file is short, both on purpose, so the whole skill costs the context of one page until you need detail.

## Mode detection: first install or refresh

Check the target repo before doing anything else:

```bash
ls .slopstopper.yml .ss/.workflows-installed 2>/dev/null
```

- **Both files exist** → a **refresh**. Re-run `install.sh` from the new tag's checkout, then work `references/refresh.md` (diff upstream, re-apply customizations, move `cli.ref`, spot new knobs) and `references/verify.md` before pushing. Steps 1, 4–6 were done last time; skim them.
- **Neither exists** → a **first install**. Walk every step in order.
- **Only one exists** (rare) → partial state. Stop and ask the user what happened. Usually an install was interrupted or partly reverted.

## What slopstopper is, in three lines

A Python CLI plus GitHub Actions workflows that run Snyk-backed security scanning (Code, Open Source, IaC, SBOM), secrets detection, hygiene and reliability checks on every PR. The `slopstopper-cli` Python package owns every check's logic; it is not on PyPI, so each adopter repo pins an exact git tag of the slopstopper repository under `cli:` in `.slopstopper.yml` and CI installs it from there. Every workflow boils down to `slopstopper run <category>:<check>` (runs the check, writes `.ss/reports/`) and `slopstopper emit <category>:<check> --target {pr-comment,issue}` (posts the result). The install lands up to 27 workflows, pins the scanners via **mise** (`mise.toml`: `task`, `node`, `snyk`, `gitleaks`), merges devDeps into `package.json` (or creates a minimal one), seeds `.slopstopper.yml` with the `cli:` pin, and creates a `Taskfile.yml` if the target has none. Don't run it blind.

## The playbook

| Step | What it is | Read |
|---|---|---|
| **1. Pre-flight** | Decide the project-shape profile (`ui` / `api` / `library`) first, because the browser checks go red, not inert, on an API. Then twelve questions about the target: existing workflows, `docs/` shape, headers source, Node version, the Snyk org and tenant, private-repo CI minutes, dirty tree. | `references/preflight.md` |
| **2. Install** | The one command (`bash <checkout>/install.sh [--profile …]`, on a branch, from a `git clone --branch vX.Y.Z` of slopstopper), what it writes and what it leaves alone, `--no-task` / `--no-hooks` / `--no-skills`. | `references/install.md` |
| **3. What landed** | The file inventory to read back to the user: workflows per profile, `Taskfile.ss.yml`, the `cli:` pin, `mise.toml`, the pre-push hook, these skills, the composite actions. | `references/install.md` |
| **4. Configure** | `.slopstopper.yml`: profile, the `cli:` pin, `urls.production` / `urls.preview`, page lists, the four API checks (inert until `api.*` is set; under `ui` they install only once the repo shows an API, so re-run the installer after setting `api.*`), the `security.*` gates, hygiene thresholds, Node version. Plus the **repo secret and variables** the Snyk checks need: `SNYK_TOKEN`, `SNYK_ORG`, `SNYK_API`. | `references/configure.md` |
| **5. Docs layout (AGENTS.md first)** | If keeping the docs checks: `AGENTS.md` carries what most tasks need under ~2k tokens with explicit routes, `CLAUDE.md` is `@AGENTS.md`, `docs/README.md` is the map, every doc routed. Decide routes-only / build / fan-out first; paste-ready scaffolds and fixes. | `references/map-pattern.md` |
| **6. Badges** | `slopstopper badges` → the README block. | `references/configure.md` |
| **Refresh-only** | Diff installed workflows against the new tag, re-apply the customizations the installer wipes, move `cli.ref`, spot new knobs and new checks, clean up obsolete artefacts. | `references/refresh.md` |
| **7. Verify locally** | Pass A (static, seconds; the Snyk checks need `SNYK_TOKEN` exported) then Pass B (needs a URL). Get **every check green locally before pushing**, so CI confirms rather than discovers. Hand off to `slopstopper-triage` for any red check. | `references/verify.md` |
| **8. Push** | Own branch, own commit, watch the confirmation pass. | `references/verify.md` |

## Step 9: When NOT to install slopstopper

Say so plainly if:

- The target has no CI at all and the user only wants one check. Point them at `pip install "slopstopper-cli @ git+<source>@<tag>#subdirectory=cli"` and a single `slopstopper run …` instead.
- It's a one-file script where even `--profile library` (11 checks plus the summary and failure tracker) is overkill.
- There is **no Snyk organisation and nobody to create one**. The four Snyk checks exit 2 (could not run) without `SNYK_TOKEN`, which fails every PR. Either get the service account first (`docs/security/SNYK.md` in the slopstopper repo) or disable the four Snyk workflows under `workflows.disabled` and say the security layer is then secrets-only.
- It's a **private repo with a tight CI-minutes budget**. Actions minutes are free on public repos and billed on private ones; the dynamic checks (Playwright, Lighthouse) are the expensive ones. Call the cost out and let the user decide (Step 1.12 in `references/preflight.md`).

## Step 10: When to hand off

- A check fails during Step 7's local loop, or a previously-green check goes red on refresh → **`slopstopper-triage`** (workflow → local task → report → finding category → fix location), then come back.
- Anything that changes slopstopper itself (a renamed task, a new knob, a new workflow) has to be reflected in this skill or it silently rots. `references/maintaining.md` lists the triggers.

## Notes for the agent

- The install is reversible by deleting the slopstopper-added files. Commit it as its own commit so it's easy to revert.
- Re-running `install.sh` is safe because it tracks deletions in `.ss/.workflows-installed`, so workflows the user deliberately removed don't come back. The composite actions under `.github/actions/ss-*/` and this skill's files are refreshed wholesale; customise workflows, not those.
- Hardcoded URL edits to `ss-*.yml` are wiped on reinstall. Say so before the user makes them.
- `SNYK_TOKEN` is env-only. Never write it into `.slopstopper.yml`, a workflow file or a commit; the CLI only reads it from the environment, and the repo secret is where it lives in CI.
- Step 7 is the most important part of the install. Don't push until it's green locally, because the local loop is faster than CI by an order of magnitude.
- Surface findings, don't auto-fix everything. The user decides what's a real issue, a tuning task, or a deletion candidate.
