# Steps 6–7: When the fix is broader than one check, and when to delete the check

> Part of the `slopstopper-triage` skill. `SKILL.md` says when to read this; it is not loaded until then.

## Step 6: When the fix is broader than one check

If multiple checks fail with the same root cause, batch-fix once and re-run all affected checks instead of fixing each in isolation. Common patterns:

- **Setting `SNYK_TOKEN` (and `SNYK_ORG` / `SNYK_API`) in the repo settings** → turns four exit-2 jobs (`sast`, `dependencies`, `iac`, `sbom`) into real verdicts in one edit. Until the secret exists every Snyk check is red for the same reason; don't triage them one by one.
- **Node version pin bump** → fixes Core Web Vitals + Accessibility + SEO + Playwright + Smoke in one edit.
- **Adding `public/_headers` with a security-headers baseline** → fixes the smoke spec's CORP header check and the `api-headers` HSTS / `nosniff` findings on a static host.
- **Setting up the AGENTS.md-first docs layout** (`AGENTS.md` under ~2k tokens with explicit "before you X, read Y" routes and a catch-all to `docs/README.md`; `CLAUDE.md` exactly `@AGENTS.md`; every doc under `docs/` routed) → fixes docs-structure + docs-size + docs-accuracy + entry-files together. The `entry-files` report ships a paste-ready fix per rule; the `docs-structure` report names every unrouted doc and the README that should route it. `slopstopper-install` Step 5 has the decide-first recipe (routes only / build / fan out) and the pre-0.15 migration.
- **Moving the `slopstopper-cli` pin** → if a default tuning was widened upstream, multiple checks go green at once. The CLI is pinned as a git tag under `cli:` in `.slopstopper.yml`; re-run `install.sh` from the new tag's checkout, bump `cli.ref`, install the same tag locally, commit so CI matches, then re-run the aggregates. Don't `pip install` a newer CLI by hand, because that drifts off the committed pin without updating CI.
- **Moving the `snyk` pin in `mise.toml`** → a scanner bump can change verdicts on the same commit (new rules, a new advisory). It is a reviewable PR on purpose; `mise install` after the bump puts the new version on PATH locally.

The aggregates make this fast: `task ss:hygiene:test` re-runs every hygiene check; `task ss:security:scan` re-runs every security check. Both are just sequenced `slopstopper run …` calls under the hood, so adopters who skip the Taskfile can chain the CLI invocations directly.

## Step 7: When to delete the check instead of fixing it

**First ask whether it's a whole class, not one check.** If a repo is an API or a library, the browser-and-SEO checks don't belong there as a group, which is what `profile:` is for (`slopstopper profile list`). It is the maintained answer because the profile survives a re-run, is visible in review, and reverses cleanly if the repo grows a web surface later. Reach for deletion only for a check that a profile doesn't cover.

A small number of checks guard a pattern a given target may not have. Delete them, or list them under `workflows.disabled` in `.slopstopper.yml` (the config-driven form is easier to audit and survives a clone-and-rebuild). Either way `.ss/.workflows-installed` remembers the deletion, so re-running `install.sh` won't bring them back.

Deletable on legitimate grounds:

- **`ss-hygiene-csp-exceptions-check.yml`** guards the headers file `headers.source` names. (Already dropped by `profile: library`; and harmless under any profile while `headers.source` is null.)
- **`ss-hygiene-docs-accuracy-check.yml` + `docs-size-check.yml` + `docs-structure-check.yml` + `entry-files-check.yml`**, only if the target genuinely won't keep an `AGENTS.md` and routed docs. If you're keeping docs at all, set up the layout instead (see `slopstopper-install` Step 5). On a repo whose entry file already fits, that is a few route lines.
- **`ss-security-sast-check.yml`**, only when Snyk Code's source upload is unacceptable for this repository (a data-residency rule) or Snyk Code cannot be enabled on the org. The other three Snyk checks send only the dependency graph (IaC sends nothing), so keep them; say plainly that dropping SAST leaves no static code analysis.
- **`ss-security-iac-check.yml`**, never for "the repo has no IaC": it passes with a note in that case. Only if the org has no IaC entitlement.
- **`ss-security-sbom-check.yml`**, only for a repo with genuinely no dependencies, and even then `security.sbom.allow_empty: true` is the better lever because the artifact keeps being produced once a manifest appears.
- **The four Snyk workflows together**, only when there is no Snyk organisation at all and nobody will create one. Say that the security layer is then secrets-only, and point at `docs/security/SNYK.md` in the slopstopper repo for the day one exists.
- **The four API workflows** (`ss-reliability-api-health-check.yml`, `ss-reliability-api-latency-check.yml`, `ss-security-api-headers-check.yml`, `ss-hygiene-openapi-check.yml`), only if the repo serves no API at all. They're already inert unconfigured, so deleting them is tidiness rather than necessity; `profile: library` drops all four as part of a coherent set, which is the better lever.

Don't delete a check just because it's failing. The bar is *"this check has nothing meaningful to guard on this target"*, not *"this check is inconvenient"*.
