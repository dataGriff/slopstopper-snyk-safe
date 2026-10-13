# Steps 2–3: Run the installer, then read what landed

> Part of the `slopstopper-install` skill. `SKILL.md` says when to read this; it is not loaded until then.

## Step 2: Run the installer

Before running anything, ensure you're on a clean branch:

```bash
git status                              # must show clean
git checkout -b chore/slopstopper-install   # or chore/slopstopper-refresh for a refresh
```

`install.sh` adds up to 27 workflow files, the two composite actions, a `Taskfile.ss.yml` and a `.slopstopper.yml` to the repo in one shot. On `main` that's an awkward 27+-file commit; on a dedicated branch the diff is reviewable and the rollback is `git checkout main && git branch -D <branch>`. If `git status` is dirty, stop and reconcile first, because the installer doesn't ask before writing into `ss-*.yml` or `Taskfile.ss.yml`. On a refresh, the wipe-and-replace behaviour will clobber anything sitting in the tracked files it rewrites, so commit or stash first.

**The installer runs from a tagged checkout of the slopstopper repository, never from a pipe.** Clone the exact tag the user has agreed on (the latest unless they say otherwise), then run its `install.sh` from inside the target repo:

```bash
git clone --branch v0.1.0 https://github.com/dataGriff/slopstopper-snyk-safe.git ../slopstopper-snyk-safe
bash ../slopstopper-snyk-safe/install.sh                # default Task mode, `ui` profile
bash ../slopstopper-snyk-safe/install.sh --profile api  # or the profile you agreed above
```

The checkout is the source of everything that lands (workflows, shims, composite actions, templates, these skills), and the tag it is at becomes the `cli:` pin the installer seeds into `.slopstopper.yml`, so the CLI CI installs is the same commit the workflows came from. An enterprise that mirrors the repository clones the mirror instead; the pin's `source` then names the mirror.

The installer is idempotent. Re-running it from a newer checkout pulls newer checks but respects deletions (tracked via `.ss/.workflows-installed`). It does **not** touch an existing `cli:` pin: `cli.ref` in `.slopstopper.yml` is what CI installs, and a plain re-run never bumps it, so a breaking release can't reach the repo until someone edits `cli.ref` (see "Move the CLI pin" in `references/refresh.md`). All slopstopper files live under the `ss` namespace, so your repo's existing files are not touched. If a pre-CLI install left a `.ss/scripts/` directory behind, the installer scrubs it on first run.

**`install.sh` also installs the SlopStopper Claude Code skills at project level**, under `<target>/.claude/skills/slopstopper-{install,triage}/` (each a `SKILL.md` map plus its `references/`), so every contributor that clones the repo picks them up automatically (Claude Code auto-discovers project-level skills). Commit the resulting files alongside the workflows. Pass `--no-skills` or set `SLOPSTOPPER_NO_SKILLS=1` to disable. To refresh just the skills later without re-running the whole installer, run `install-skill.sh` from the same checkout inside the repo. If you've previously run an older version of `install-skill.sh` that wrote to `~/.claude/skills/slopstopper-*`, the installer warns you so you can clean up the user-level copies before they shadow the project-level ones.

### What `install.sh` writes (and leaves alone)

Three categories of write, in order of "how much trust to extend on re-run":

**Always overwritten (slopstopper-owned, safe to clobber):**

- `Taskfile.ss.yml`, `.ss/.workflows-installed`, `.ss/.installed-from` (the slopstopper commit this install came from; the baseline for the refresh diff), `.github/actions/ss-*/`
- Workflows under `.github/workflows/ss-*.yml` that are in `GENERIC_WORKFLOWS` and not listed in `.slopstopper.yml` `workflows.disabled`
- `mise.toml`'s slopstopper pins: `task`, `node` (seeded `= "20"` only when the repo doesn't already declare one), `npm:snyk` and `gitleaks` at exact versions. The scanners are pinned on purpose: a floating scanner changes its findings between two runs of the same commit
- `<repo>/.claude/skills/slopstopper-install/` and `<repo>/.claude/skills/slopstopper-triage/`, each `SKILL.md` plus `references/`, replaced as a whole (at project level, with `--no-skills` to opt out)
- `.githooks/pre-push`, the pre-push hygiene gate (opt out with `--no-hooks`). The *file* is always refreshed; the `core.hooksPath` **wiring** is only set when safe (the next list defines it), so it's left alone if the adopter runs their own hook manager

**Seeded only if missing (adopter-owned, NEVER overwritten on re-run):**

- `.slopstopper.yml` (config; a first install seeds a short starter from `templates/slopstopper.yml.starter` with the `cli:` pin (`source` + `ref`, the tag the installer ran from), `profile`, URLs, page lists, og-image path, `headers.source` and `workflows`, and it links the full schema reference for every other key. Once it exists `install.sh` never touches it, so moving `cli.ref` is a hand edit)
- `.github/labeler.yml`, `.markdownlint.json`
- Root `Taskfile.yml`, only if absent; otherwise install.sh prints the `includes:` block to paste in
- `package.json`, only if absent (otherwise see below): a minimal manifest named after the repo, `private: true`, slopstopper's devDependencies and **no** `build` script. The browser-check workflows skip the build when there is none and serve the repo as-is, so add `scripts.build` if the site needs building first
- AGENTS.md-first entry files (`README.md`, `AGENTS.md`, `CLAUDE.md`, `docs/README.md`), seeded from `cli/slopstopper/data/templates/entry-files/` when absent and never overwritten. If they exist but break a rule (budget, soft route, `CLAUDE.md` not the pure include, no route to the map), the `ss:hygiene:entry-files` check fails with a paste-ready fix in its report, which you apply manually rather than re-seeding. A pre-0.15 `docs/index.md` is never touched; the installer warns and the check's report carries the rename

**Conservatively additive on shared files (adopter content preserved):**

- `package.json` devDeps merge: adds missing keys; existing keys are kept on version conflict (warning printed, not error).
- `.gitignore`: the `# slopstopper begin` / `# slopstopper end` block is slopstopper-owned. The first install appends it, and every re-run replaces what's between the markers with the current template, so new ignore lines (`.ss/.run/`, `node_modules/`) reach existing installs. Lines outside the markers are never edited. A begin marker with no end marker is left alone with a warning.
- `public/_headers` (only if `public/` exists): appends a commented-out security-headers baseline inside `# slopstopper security headers begin/end` markers. Same idempotent skip on re-run, and a file that already sets a security header (an uncommented `Content-Security-Policy`, `Strict-Transport-Security` or `X-Content-Type-Options` line) is left byte-identical.
- `core.hooksPath` (git config, not a file): set to `.githooks` **only** when the adopter has no custom `core.hooksPath` and no other hook manager (`.husky/`, `lefthook.yml`/`.yaml`, `.pre-commit-config.yaml`). If they already manage hooks, the installer drops `.githooks/pre-push` in but leaves their config alone and prints a one-line opt-in. `--no-hooks` / `SLOPSTOPPER_NO_HOOKS=1` skips the whole step.

**Everything else is left alone.** That covers source code, build outputs, custom workflows under `.github/workflows/` outside the `ss-*` namespace, generic configs (`.eslintrc`, `tsconfig.json`, etc.), and anything under `src/`, `infra/`, etc.

**One risk worth flagging:** inline customisations to `ss-*.yml` workflow body content (bespoke `gh issue create` blocks, extra steps, custom env vars beyond what `.slopstopper.yml` covers) get wiped on the next `install.sh` re-run because workflow files are wholesale-replaced. Push bespoke wording into the check's META in `slopstopper-cli` upstream rather than hand-editing the YAML locally. See the **Refresh-only** section below for how to diff and re-apply on refresh.

By default the installer ships **Task-driven workflows**, where every check runs via `task ss:<category>:<check>` so the suite shares one invocation surface with `task build`, `task deploy`, etc. The workflows install the toolchain via `jdx/mise-action`, which reads `mise.toml` and installs `task`, `node`, `snyk` and `gitleaks`, then install the CLI from the `cli:` pin, so adopters don't need any of them in their CI runners by hand. If the adopter explicitly doesn't want Task in their CI, install with `--no-task`:

```bash
bash ../slopstopper-snyk-safe/install.sh --no-task
```

`--no-task` post-processes each shipped workflow at install time to rewrite `task ss:<X>` lines to `slopstopper run <X>`, which is the same execution path without the Task layer. (The `jdx/mise-action` step stays; mise still installs the scanners, and `task` simply goes unused.) Default mode is the right choice for most adopters; `--no-task` is the escape hatch.

Full variants (all flags, optional explicit target dir):
```bash
bash ../slopstopper-snyk-safe/install.sh [TARGET_DIR]                   # default Task mode
bash ../slopstopper-snyk-safe/install.sh --no-task [TARGET_DIR]         # CLI-direct mode
bash ../slopstopper-snyk-safe/install.sh --no-hooks [TARGET_DIR]        # skip the pre-push hygiene hook
```

## Step 3: What just landed

Sanity-check the install dropped what you expect:

- `.slopstopper.yml` `cli:`, the pin CI installs the CLI from: `source` (the repository or mirror URL) and `ref` (the tag the installer ran from). `.github/actions/ss-setup` installs `slopstopper-cli @ git+<source>@<ref>#subdirectory=cli` on every run. Locally, install the same tag into a venv (`pip install "slopstopper-cli @ git+<source>@<ref>#subdirectory=cli"`) or `pip install -e ../slopstopper-snyk-safe/cli` from the checkout, and confirm `slopstopper --version` matches the tag.
- `mise.toml`, the per-repo toolchain pins (`task`, `node`, `npm:snyk`, `gitleaks`). Both `install.sh` (local) and the `ss-*.yml` workflows (`jdx/mise-action`) read it, so local and CI run the same scanner versions, node included, with no separate `setup-node` step. Commit it. `mise install` puts the pinned `snyk` and `gitleaks` on PATH.
- `Taskfile.ss.yml`, thin `task ss:*` shims that each call `slopstopper run <category>:<check>` under the covers. They suit adopters who already drive their dev loop with `task`.
- `Taskfile.yml`, created if missing (otherwise it needs the manual `includes:` block per Step 1.1).
- `.ss/.workflows-installed`, the manifest of what the installer put in place, which is how re-runs respect a deliberate deletion (commit this). With `.ss/.installed-from` below, the only files the installer writes into `.ss/`. The static server for the local loop is not a file in the repo, because it ships inside the CLI as `slopstopper serve` (eject with `slopstopper templates eject server.js` only if you need to customise it).
- `.ss/.installed-from`, the slopstopper commit this install came from; a refresh diffs upstream against it (commit this).
- `.github/workflows/ss-*.yml`, the curated installer set, minus whatever the profile drops (25 checks on `ui`, 16 on `api`, 11 on `library`), plus `ss-pr-summary.yml` and `ss-workflow-failure-issue.yml` under all three. Each workflow body is now ~8 lines: `uses: ./.github/actions/ss-setup` (Python + mise toolchain + the pinned CLI), `slopstopper run …`, `slopstopper emit … --target pr-comment|issue`. Every one is SHA-pinned, declares least-privilege `permissions:`, carries a `timeout-minutes`, and reads URLs and tokens through `env:`.
- The five security workflows and what they need: `ss-security-sast-check.yml` (Snyk Code), `ss-security-dependencies-check.yml` (Snyk Open Source; runs `task ss:security:monitor` on `main`), `ss-security-iac-check.yml` (Snyk IaC), `ss-security-sbom-check.yml` (Snyk SBOM, uploads the document as an artifact), `ss-security-secrets-check.yml` (Gitleaks). The four Snyk ones read the `SNYK_TOKEN` secret and the `SNYK_ORG` / `SNYK_API` variables; until the secret exists they exit 2 (could not run), which is Step 4's job.
- Every third-party action in those workflows and actions is pinned to a commit (`uses: actions/checkout@<sha> # v7`). slopstopper's own Dependabot keeps its copies current and a refresh copies the new pins in, so the adopter repo needs no Dependabot config for them, but pins only move when you refresh.
- `.github/actions/ss-setup/` and `.github/actions/ss-resolve-url/`, the composite steps those workflows call. They are local actions, so nothing is published or version-pinned and everything a workflow does is diffable in the adopter's own tree. `ss-setup` is where the `cli:` pin is read; `ss-resolve-url` is where the event → URL table lives (`urls.preview` on PRs for the API checks; the browser checks build and serve locally).
- `ss-pr-summary.yml`, which posts **one** rolling comment per PR summarising every check (`❌ SlopStopper: 2 of 25 checks failed`, failures in a table, the rest folded). The per-check workflows post compact comments (a verdict line, the failing items, the report folded away) and **delete their comment when they pass**, so a green PR carries only the summary. Tell the user this up front: the first green PR looking "empty" of bot comments is the intended behaviour, not a broken install.
- If the banner shows **⛔ GitHub has switched off N slopstopper workflow(s)**, GitHub disabled them for 60 days without repo activity and they run on no trigger, PRs included. Each line carries its `gh workflow enable <id>`; ask the user, then run them. `slopstopper doctor` re-checks later. The banner skips this silently when `gh` can't reach the repo.
- `.slopstopper.yml` `profile:`, the key the installer wrote (or left alone). Confirm the installed set matches it with `slopstopper profile show`: it prints the active profile, where it came from, and every workflow this repo is deliberately not carrying. Run it before the missing-workflow comparison below, because a workflow the profile dropped is *supposed* to be absent and will otherwise read as a gap.
- `package.json`, with devDeps merged.
- `.claude/skills/slopstopper-install/` + `.claude/skills/slopstopper-triage/` (each `SKILL.md` + `references/`), the project-level Claude Code playbooks. Claude Code auto-discovers them for any contributor in this repo. Commit them.
- `.githooks/pre-push`, the pre-push hygiene gate (runs `task ss:hygiene:test` before every push). Commit it. On a normal install the installer also sets `git config core.hooksPath .githooks`; confirm with `git config --get core.hooksPath`. If the adopter already runs husky/lefthook/pre-commit (or a custom hooksPath), the file lands but the wiring is skipped with an opt-in note (run `git config core.hooksPath .githooks`, or add `task ss:hygiene:test` to their own manager). `--no-hooks` skips it entirely.

**What's NOT there any more** (if you're updating from a pre-CLI install): `.ss/scripts/` (every Python/bash script lives in `slopstopper-cli`); and `.ss/playwright.config.js`, `.ss/lighthouserc.json`, `.ss/lighthouserc.prod.json`, `.ss/tests/` (now bundled in the CLI, so the installer scrubs unmodified byte-equal copies on re-run but leaves customized files alone since they'll override via the CLI's templates resolver).

### Customising a Playwright spec or lighthouserc

The CLI looks for `.ss/<filename>` first and falls back to the bundled copy. Use `slopstopper templates` to list, locate, and eject:

```bash
slopstopper templates list                       # see what's bundled + what's already ejected
slopstopper templates path lighthouserc.json     # print the resolved path (override-or-bundled)
slopstopper templates eject lighthouserc.json    # copy bundled → .ss/lighthouserc.json
$EDITOR .ss/lighthouserc.json
```

The CLI picks up the override on the next run. Caveat: customizations don't auto-merge with upstream changes, because you own the file once you eject. `slopstopper templates eject` won't overwrite an existing `.ss/<filename>`, and the installer's byte-equality scrub leaves customized files alone.

**Confirm the installed set matches the checkout.** `install.sh` uses a hardcoded `GENERIC_WORKFLOWS` array, not a wildcard over slopstopper's `.github/workflows/ss-*.yml`. The two can drift when slopstopper ships a workflow that the installer hasn't been updated to include. To catch this, compare against the tagged checkout you installed from:

```bash
# inside the target repo, after install
comm -23 \
  <(ls ../slopstopper-snyk-safe/.github/workflows/ | grep '^ss-' | sort) \
  <(ls .github/workflows/ | grep '^ss-' | sort)
```

Any line in the output is a workflow that exists in the checkout but didn't land. **Subtract the profile's set first.** `slopstopper profile expand <name>` lists the workflows this repo drops on purpose, and they'll all show up here otherwise. Of what remains, if any look relevant, copy them from the checkout into `.github/workflows/` directly (and customize their URLs and page paths like the rest). Flag the gap upstream as an `install.sh` fix too.

The installer's stdout summarises what's active vs what needs config. Read it and relay it to the user.
