# Refresh-only: diff upstream, re-apply customizations, spot new knobs

> Part of the `slopstopper-install` skill. `SKILL.md` says when to read this; it is not loaded until then.

## Refresh-only: diff upstream, re-apply customizations, spot new knobs

**Skip this section on a first install.** It's relevant only when `.slopstopper.yml` and `.ss/.workflows-installed` both existed in Step 1 (mode-detection), meaning the installer just ran in-place over an existing slopstopper install.

`install.sh` is idempotent but **not transactional**. Every workflow YAML in `GENERIC_WORKFLOWS`, `Taskfile.ss.yml`, `.github/actions/ss-*/` and the `# slopstopper begin/end` block in `.gitignore` is rewritten wholesale on every run, from whatever tag the checkout you ran it from is at. The `cli:` pin in `.slopstopper.yml` is **not** touched (a refresh never bumps it, as "Move the CLI pin" below explains), so after refreshing from a newer tag the workflows and the pinned CLI disagree until you move `cli.ref`. A few classes of customization get wiped and need re-applying; a few classes of upstream change need manual catch-up because the installer doesn't drag everything across. Walk this section before the local-verify loop in Step 7.

### Re-enable workflows GitHub switched off

A repo quiet enough to need a refresh is often one GitHub has stopped running. It disables any workflow with a `schedule:` trigger after 60 days without repo activity, and a disabled workflow runs on no trigger, pull requests included, so those checks silently vanish from the PR's list. The post-install banner lists any `ss-*` workflow in that state with its `gh workflow enable <id>` command, and `slopstopper doctor` reports the same. Re-enable them (ask the user first; it changes repo settings) before the Step 7 loop, or the confirmation pass on CI will be missing checks.

### Diff installed workflows against upstream

`install.sh` uses a hardcoded `GENERIC_WORKFLOWS` array, not a wildcard over slopstopper's `.github/workflows/ss-*.yml`. The two can drift when slopstopper ships a workflow that the installer hasn't been updated to include. Catch the gap against the tagged checkout you refreshed from:

```bash
comm -23 \
  <(ls ../slopstopper-snyk-safe/.github/workflows/ | grep '^ss-' | sort) \
  <(ls .github/workflows/ | grep '^ss-' | sort)
```

Any line in the output is an `ss-*.yml` workflow that exists in the checkout but isn't in your target. Subtract the profile's set first (`slopstopper profile expand <name>`). If any look relevant, copy them across directly (and customize the URLs and page paths like the rest). Flag the gap upstream as an `install.sh` fix.

### Re-apply customizations the installer wiped

The installer refreshes `Taskfile.ss.yml`, the slopstopper block in `.gitignore` (between its markers only), the `.ss/` overlay, `.ss/.installed-from`, `.githooks/pre-push`, the `.github/actions/ss-*/` composite actions and the `ss-*.yml` workflows wholesale. Anything hand-edited in those files is gone. `.slopstopper.yml`, though, is **never** overwritten by the installer, so the bulk of customization (the `cli:` pin, headers source/format, URLs, pages, og-image path, the `security.*` gates, disabled workflows, hygiene thresholds) survives every re-run. The Node version lives in `mise.toml` and is seeded only when absent, so a bump you made there survives too; the `snyk` and `gitleaks` pins there are refreshed to the checkout's versions, which is the point.

What still needs re-checking after a refresh:

- **Anything hand-edited inside `ss-*.yml` workflow files** beyond what `.slopstopper.yml` covers. Common case: extra workflow-level `permissions:` for a custom integration, a non-standard schedule, or workflow-level env vars beyond the documented URL/PAGES set. Diff against upstream to find them. Push bespoke wording into the check's META in `slopstopper-cli` upstream rather than hand-editing the YAML locally, because workflow edits don't survive `install.sh` re-runs.
- **Anything hand-edited inside `.ss/tests/*.spec.ts`, `.ss/playwright.config.js`, or `.ss/lighthouserc.json`.** These used to be seeded by `install.sh` but now live inside the `slopstopper-cli` package. The installer's byte-equality scrub removes unmodified copies (so the package's version wins via the templates resolver); customized copies survive in `.ss/` and continue to override.
- **Workflows that carry a custom `SNYK_*` setting.** The shipped workflows read `SNYK_TOKEN`, `SNYK_ORG` and `SNYK_API` from the repo's secrets and variables, so nothing in the YAML needs re-applying; but a workflow someone edited to hard-code an org or an API host is reset to the shipped form. Move such values into the repo variables (or `security.snyk.org`) rather than re-editing the YAML.
- **The Node version pin.** It lives in `mise.toml` (`[tools] node`), not `.slopstopper.yml`. `install.sh` seeds `node = "20"` only when none is declared, so a bump you made (`mise use node@22`) survives a refresh and there's nothing to re-sync, since mise locally and CI via `jdx/mise-action` read the same pin. If you mirror URLs as repo variables, re-push those after editing `.slopstopper.yml`.
- **The pre-push hook file** (`.githooks/pre-push`) is refreshed wholesale because it's slopstopper-owned, so hand-edits to it are lost. The `core.hooksPath` **wiring** is re-applied on every refresh **when safe**, meaning when hooksPath is unset or already `.githooks` and no other hook manager is present. It won't override a hooksPath the adopter pointed elsewhere, or a husky/lefthook/pre-commit setup. Note the flip side: an adopter who simply *unset* hooksPath will have it re-wired on the next refresh (empty is indistinguishable from never-set). To opt out durably, pass `--no-hooks` on refreshes or point hooksPath at their own dir.

- **`.github/labeler.yml`** if upstream's labeler template added categories you want (the installer never overwrites your existing file, so new categories don't land automatically).

### Move the CLI pin (intentional upgrades)

`slopstopper-cli` is pinned per-repo as a git tag under `cli:` in `.slopstopper.yml` (`source` + `ref`), and CI installs exactly that tag on every run (`.github/actions/ss-setup`: `slopstopper-cli @ git+<source>@<ref>#subdirectory=cli`). A refresh never bumps it. This is deliberate: a breaking release can't surprise the repo, and the bump PR is the review of the new CLI. To move the pin:

1. Check out the new tag in the slopstopper checkout and re-run its `install.sh` (done above), so workflows, shims and composite actions match the CLI they will call.
2. Edit `cli.ref` in `.slopstopper.yml` to the same tag (`v0.2.0`). Keep `source` as the repository or the enterprise mirror; a branch name in `ref` defeats the pin and `ss-setup` installs whatever that branch holds.
3. Install that tag locally (`pip install "slopstopper-cli @ git+<source>@<ref>#subdirectory=cli"` into the venv you use, or `pip install -e ../slopstopper-snyk-safe/cli`) and confirm `slopstopper --version`.
4. Skim the slopstopper `CHANGELOG.md` section for the tag for breaking changes, then run the Step 7 local-verify loop so the new version is green before you push the pin bump. Commit `.slopstopper.yml` together with the refreshed workflows.

An enterprise serving the CLI from an internal index sets the `SLOPSTOPPER_CLI_SPEC` repository variable (`slopstopper-cli==X.Y.Z`) instead, which wins over the yml; bump that variable rather than `cli.ref`.

**The shell you run the loop in may still have the old CLI.** A venv or a user-site install from the previous tag stays on `PATH` until you reinstall. Before the Step 7 loop, confirm `slopstopper --version` matches `cli.ref`; if it doesn't, reinstall from the tag. The scanners are separate: `mise install` after a refresh picks up the new `snyk` / `gitleaks` pins.

### Spot newly-shipped knobs in `.slopstopper.yml.example`

The `.slopstopper.yml.example` file in the slopstopper repo is the schema reference. Don't diff it against the repo's `.slopstopper.yml`: a config seeded from the starter deliberately carries only the keys the repo sets, so that diff lists every default as "missing". Diff the schema between the slopstopper commit the previous install came from and the one this refresh came from. The installer records it in `.ss/.installed-from`, the old value is still in `HEAD` until you commit the refresh, and the checkout you refreshed from has both commits:

```bash
old="$(git show HEAD:.ss/.installed-from 2>/dev/null)"   # the previous install's commit
new="$(cat .ss/.installed-from 2>/dev/null)"              # this refresh's commit
if [ -z "$old" ] || [ -z "$new" ]; then
  echo "no commit recorded for the previous install or this one; use the fallback below"
else
  git -C ../slopstopper-snyk-safe diff "$old" "$new" -- .slopstopper.yml.example
fi
```

Lines added on the right are knobs shipped since the last install. This commit is what the workflows, `Taskfile.ss.yml` and installer came from, so an empty diff (or `old` = `new`) really does mean no new knobs. A commit git doesn't know (a mirror that squashed, a local commit) makes the diff fail rather than lie; never read a failure as "everything is new".

**Fallback** (no marker on one side, or a commit the checkout doesn't have): read `git -C ../slopstopper-snyk-safe log -p -- .slopstopper.yml.example` back to `git log -1 --format=%cs -- Taskfile.ss.yml .github/workflows/`, the last commit in which a refresh changed slopstopper's files. A refresh that changed nothing leaves no commit, so that date can be earlier than the last refresh: some of what you read may already be in place, but nothing newer is missed.

A knob read by the CLI (rather than by a workflow or the installer) only takes effect once the tag pinned under `cli.ref` is a release that has it. If a new knob does nothing, check the changelog and move the pin. Most knobs ship with sensible defaults, so no action is required. The link at the top of the repo's `.slopstopper.yml` is the schema to copy blocks from.

Surfaces worth checking explicitly:

- **`profile:`**, the project-shape preset (`ui` / `api` / `library`). A config that predates the key has no `profile:` line, which resolves to `ui`. That is the full suite, exactly as before, so nothing changed under the repo. If the repo is an API or a library, this is the highest-value knob in the diff: setting it and re-running the installer removes the browser-and-SEO checks that were never going to pass. Run `slopstopper profile detect` and confirm with the user before switching. Check `workflows.disabled` at the same time, since entries that a profile now covers can be dropped from it.
- **`hygiene.docs_size.*` / `hygiene.entry_files.*` / `hygiene.docs_structure.*`**, per-check thresholds and rule toggles. Defaults are intentionally tight (150 KB / 25 files; 2000 / 600 / 1000 tokens; 300 lines per doc; route depth 3). **0.15 moved the docs checks to AGENTS.md-first**, and a refresh onto a 0.15+ CLI turns these red on a Map Pattern repo: `entry_files.max_words` is ignored (budgets are estimated tokens under `max_tokens`, `readme_max_tokens` and `map_max_tokens`), `CLAUDE.md` must be exactly `@AGENTS.md` (`require_claude_include`), every `.md` link in `AGENTS.md` must be a trigger-first "read" route (`require_explicit_routes`), the map is `docs/README.md` (`map_path`), `docs_structure.require_indexed_docs` is read as `require_routed_docs` (a doc must be *routed*, not merely linked), and `max_route_depth` / `max_doc_lines` are new. The fix is the Step 5 migration in `references/map-pattern.md`, not the knobs: turn a rule off only when it is genuinely wrong for the repo, with a `# why` comment. If the red comes from non-doc content under `docs/` (a course, rendered tutorials), list it in `hygiene.docs.exclude` (default `[docs/archive/**]`; setting it replaces the default), which takes it out of all three docs checks.
- **`hygiene.complexity.max_ccn`**, the CCN ceiling (default 15). The complexity gate lives in the CLI, so `task ss:hygiene:complexity` fails locally and in the pre-push hook exactly as it does in CI. If a function newly blocks a push and is genuinely well-factored, raise the ceiling here rather than contorting the code. **`hygiene.complexity.exclude`** (globs) skips committed generated or vendored code; test files by convention (`*_test.go`, `*.spec.*`, `tests/` …) and gitignored paths are skipped without it, so a Go repo's test functions don't count against the gate.
- **`hygiene.docs_accuracy.extra_paths`**, globs for files outside `docs/` the accuracy check should scan (task and workflow references in markdown, plus every `github.com/<this repo>/blob|tree/…` link in markdown or HTML, must resolve). It is off by default and worth setting for a repo with a marketing site that links into its own source tree, or that ships skills/instructions naming files, since those rot silently otherwise.
- **`security.snyk.org`** and the four gates **`security.sast.fail_on`**, **`security.dependencies.fail_on`** (+ `include_dev`), **`security.iac.fail_on`**, **`security.sbom.format`** / **`allow_empty`**. Each gate is the lowest severity that fails (`critical | high | medium | low | none`, default `high`); the check's exit code is the verdict and the report always carries every finding, so what you see locally is what CI gates on. Accepted risks go in the `.snyk` policy with a reason and expiry, not in the gate. A config that predates the fork has no `security.*` block at all, which resolves to the defaults.
- **`api.latency.*`**, endpoints to sample plus three opt-in budgets (`median_ms`, `slowest_ms`, `max_bytes`). Set `paths` and leave the budgets unset on first adoption: the check reports timings and enforces nothing until a budget exists, and budgets should be derived from observed numbers rather than guessed.
- **`api.openapi.served_spec` / `probe_paths` / `ignore_paths`**, alongside `spec`. `served_spec` is the one worth setting: it turns `hygiene:openapi` from a reachability prober into a committed-vs-deployed drift comparison, which is the signal with no false positives. Note `hygiene:openapi` reads **JSON specs only**, so if `spec` is YAML it skips with guidance.
- **`reliability.coverage.{pr,main,cron}`**, page-discovery modes. Adopters with a sitemap should opt in to `sitemap` on main and `changed` on PRs; otherwise reliability checks only audit `/` by default.
- **`reliability.cwv.public_report`** (default `false`). Reports stay in `.ss/reports/cwv/lighthouse/` and the workflow artifact. `true` uploads the full Lighthouse report, screenshots and page content included, to Google's temporary public storage: only for a public site whose team wants the shareable link, never for a private repo or an authenticated preview.
- **`reliability.coverage.cross_cutting_paths`**, escalation triggers for `changed` mode. If a PR-only audit skipped pages that should have been included, this is the lever.

If a previously-failing check started passing after a refresh without any code change, eyeball whether a default got loosened upstream, which the slopstopper changelog flags.

### Spot new checks and task targets

Two surfaces to scan after a refresh:

1. **The CLI's check registry.** `slopstopper --help` lists subcommands; the canonical list of check names is in `cli/slopstopper/checks/__init__.py`'s `REGISTRY` dict upstream. If a new check landed (e.g. `reliability:<new-check>`), it's runnable as `slopstopper run reliability:<new-check>` even before any workflow ships it.
2. **The Taskfile shims.** Re-running the installer refreshes `Taskfile.ss.yml`, which mirrors every CLI check as a `task ss:*` shim. Eyeball:

   ```bash
   task --list | grep '^\* ss:'
   ```

If anything looks unfamiliar, check `Taskfile.ss.yml` for the `desc:` and `summary:` (each shim documents the env vars it forwards). New checks are usually surfaced as part of a new workflow. If a new workflow landed in the diff above, you'll see the matching task here regardless, since `Taskfile.ss.yml` is refreshed wholesale.

If new workflows did land and you want the README badges refreshed to match, re-run `slopstopper badges` and replace the existing Pipeline status block in `README.md`.

### Clean up redundant artefacts

Adopter repos should hold exactly the **expected set** of slopstopper artefacts plus their own customisations and nothing more. Anything outside that set is noise: a former skill that's been folded into another, an old `ss-*-check.yml` that's been renamed upstream, a `.ss/` file that moved into the CLI wheel. Keep the working tree tidy on every refresh.

**What `install.sh` and `install-skill.sh` already clean up automatically:**

- `<repo>/.claude/skills/<name>/` directories listed in `OBSOLETE_SKILLS`, currently `install-slopstopper` (single-skill legacy) and `slopstopper-update` (folded into `slopstopper-install`). The list lives in `install-skill.sh`, which `install.sh` runs.
- `.ss/scripts/`, a pre-CLI artefact scrubbed wholesale on every install; `.zap/rules.tsv`, the policy file of the removed DAST check, if a pre-fork install seeded one.
- Byte-equal copies of `.ss/playwright.config.js`, `.ss/lighthouserc.json`, `.ss/lighthouserc.prod.json` and each `.ss/tests/*.spec.ts`, which moved into the slopstopper-cli package; byte-identical adopter copies are removed spec by spec (the package's version wins via the templates resolver). Customised copies survive. Runs stage them in the self-ignoring `.ss/.run/`, so a run never adds files to `.ss/`.
- Workflows the adopter explicitly disabled via `.slopstopper.yml` `workflows.disabled`, removed on every install.
- `.github/actions/<name>/` directories listed in `install.sh`'s `OBSOLETE_ACTIONS` (empty today). The shipped composite actions (`ss-setup`, `ss-resolve-url`) are replaced wholesale on every run.

**What you should sanity-check manually on refresh** (the installer can't auto-detect these without an explicit removal list):

```bash
# 0. Legacy docs map: a pre-0.15 install seeded docs/index.md; the map is docs/README.md now.
#    The installer warns but never renames adopter content, so do it in the refresh commit
#    (`git mv docs/index.md docs/README.md`, then rewrite its rows as routes; Step 5 covers it).
ls docs/index.md 2>/dev/null && echo 'legacy map: migrate (Step 5)'

# 1. Skills: anything in .claude/skills/slopstopper-* that isn't in the current expected set is stale
ls -d .claude/skills/slopstopper-*/ 2>/dev/null
# Expected currently: slopstopper-install, slopstopper-triage. Anything else → flag for the user.

# 2. Composite actions: anything in .github/actions/ss-* other than the shipped set is stale
ls -d .github/actions/ss-*/ 2>/dev/null
# Expected currently: ss-setup, ss-resolve-url. Anything else → flag for the user (and, if it
# was a slopstopper action that got renamed, add it to OBSOLETE_ACTIONS upstream).

# 3. Workflows: anything matching ss-*-check.yml that doesn't exist in the checkout is stale
diff \
  <(ls ../slopstopper-snyk-safe/.github/workflows/ | grep '^ss-' | sort) \
  <(ls .github/workflows/ | grep '^ss-' | sort)
# Lines prefixed with `>` are local-only ss-* files, either stale or a workflow the adopter
# deliberately customised away from upstream. A pre-fork install carries ss-security-dast-check.yml,
# ss-security-vulnerability-all-check.yml, ss-security-vulnerability-new-check.yml,
# ss-hygiene-doc-updater.lock.yml and ss-cloudflare-deployed.yml: all removed in the fork, so
# delete them (and the `.zap/` directory). Ask the user before deleting anything else.
```

The line to draw before deleting:

- **Slopstopper-shipped, no longer in the expected set** → delete. Examples: `slopstopper-update/SKILL.md`, the DAST / Trivy / dependency-review / doc-updater / Cloudflare workflows a pre-fork install carried, an `ss-old-check.yml` that was renamed in a recent slopstopper release.
- **Adopter-customised, intentionally divergent** → keep. Examples: an `ss-*.yml` the adopter forked under the same name with bespoke logic, a hand-edited `Taskfile.ss.yml` shim. `.ss/.workflows-installed` tracks adopter deletions but not adopter modifications, so when in doubt, ask the user.
- **Adopter-added content under `.claude/skills/` with a non-`slopstopper-` prefix** → not slopstopper's, never touch.

When a clean-up surfaces something the installer should've handled automatically, the fix is to add it to `OBSOLETE_SKILLS` (or the equivalent list for the artefact type) upstream (`OBSOLETE_SKILLS` lives in `install-skill.sh`; the workflow and action lists in `install.sh`). See the "Removing a slopstopper-shipped artefact" row of `docs/contributing/CHANGE_MAP.md`. The next adopter then gets the cleanup for free.
