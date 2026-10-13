# Step 4: Categorise the finding

> Part of the `slopstopper-triage` skill. `SKILL.md` says when to read this; it is not loaded until then.

## Step 4: Categorise the finding

For each failing finding, decide which of the three buckets it falls into:

### Real finding: fix the code

The check is correctly flagging a problem. Examples:
- Secrets check finds an actual leaked API key → revoke immediately, scrub history (`git filter-repo` or BFG), then fix the leak source.
- SAST (Snyk Code) flags a SQL injection in a query builder → rewrite to use parameterised queries.
- The dependency scan flags a vulnerable version with a fix available → upgrade to the version the report's fix column names.
- IaC flags a storage bucket with public read → fix the resource definition.
- Accessibility flags a missing `alt` on a real image → add the alt text.
- Broken-links check flags an internal link that 404s → fix or remove the link.

There's no escape hatch here, so fix the code.

### False positive: suppress narrowly with `# why`

The check's heuristic is misfiring on this codebase. Use the check's documented suppression mechanism, never a global disable, and always carry a `# why` comment so the next reader knows why the exception is justified.

| Check | Suppression file / mechanism | Granularity |
|---|---|---|
| Secrets (gitleaks) | `.gitleaks.toml` `[allowlist]` block | Path or regex |
| SAST (Snyk Code) | `.snyk` policy: `ignore:` → the issue id → a path glob with `reason:` and `expires:` | Per-issue, path-scoped |
| Dependencies (Snyk Open Source) | `.snyk` policy, same shape, keyed on the Snyk issue id (`SNYK-JS-…`) with the dependency path (`snyk ignore --id=… --reason=… --expiry=…` writes it) | Per-issue, per-dependency path |
| IaC (Snyk IaC) | `.snyk` policy, keyed on the rule id (`SNYK-CC-…`) with the resource path | Per-rule, per-resource |
| SBOM | none: there is nothing to suppress. An empty SBOM on a repo with no dependencies is `security.sbom.allow_empty: true` (a knob, not a suppression) | – |
| CSP exceptions | `docs/security/CSP_EXCEPTIONS.md` `## Exceptions` → `### /path` heading (glob patterns supported: `/*`, `/blog/*`) | Per-path |
| Accessibility (axe-core) | `disabledRules` in the spec, or scope the spec to exclude the offending selector | Per-rule or per-element |
| Complexity (lizard) | Refactor the function, or raise `hygiene.complexity.max_ccn` in `.slopstopper.yml` (default 15) with a `# why` note. A flagged file that is generated or vendored → `hygiene.complexity.exclude` | Per-repo (the ceiling is global) |

Each suppression is a documented gate exception, not a way to quiet findings that should be fixed. The PR that adds it is the place reviewers catch it. The `.snyk` entries carry an `expires:` date on purpose, so an accepted risk comes back for re-evaluation; never write one without it. Org-level ignores in the Snyk UI also apply, but they are invisible in the repo, so prefer the file.

### Could not run (exit 2): fix the environment

The fourth bucket, and the first one to rule out on a Snyk check: no `SNYK_TOKEN`, a token from another tenant (`SNYK_API` unset or wrong), `SNYK_ORG` naming a display name instead of the slug, Snyk Code switched off for the org, the `snyk` binary missing from PATH (`mise install`). The check wrote no verdict, so there is nothing to suppress or tune. The report's stderr block and `slopstopper doctor` name the cause; `docs/security/SNYK.md` in the slopstopper repo is the setup guide.

### Threshold too tight: tune in `.slopstopper.yml`

The check is correct but the default threshold doesn't fit this repo. **First check `.slopstopper.yml`**, because many checks read their thresholds from there, falling back to a hardcoded default when unset. The example file ([`.slopstopper.yml.example`](https://github.com/dataGriff/slopstopper-snyk-safe/blob/main/.slopstopper.yml.example)) is the canonical schema reference.

Config-driven knobs (no file edit needed beyond `.slopstopper.yml`):

| Check | Keys | Defaults |
| --- | --- | --- |
| `hygiene:docs-size` | `hygiene.docs_size.max_total_size_kb` / `.max_file_size_kb` / `.max_files` | 150 / 20 / 25 |
| `hygiene:entry-files` | `hygiene.entry_files.max_tokens` / `.readme_max_tokens` / `.map_max_tokens` / `.map_path` / `.require_map_pointer` / `.require_explicit_routes` / `.require_claude_include` | 2000 / 600 / 1000 / `docs/README.md` / true / true / true |
| `hygiene:docs-accuracy` | `hygiene.docs_accuracy.extra_paths` | `[]`, so only `docs/` + the root entry files are scanned |
| `hygiene:docs-structure` | `hygiene.docs_structure.require_routed_docs` / `.max_route_depth` / `.max_doc_lines` | `true`, so every doc has an explicit route / 3 / 300 |
| all three docs checks | `hygiene.docs.exclude` (globs) | `[docs/archive/**]`; for non-doc content under `docs/` such as a course, rather than raising the caps |
| `reliability:smoke` | `pages.smoke`, `smoke.og_image_path` | `/`, `/og-image.png` |
| `reliability:e2e` | `pages.e2e`, `e2e.max_links` | `/`, `25` |
| `reliability:{accessibility,seo,broken_links}` | `pages.<check>`, `reliability.coverage.<event>` | `/`, hand-list mode |
| `hygiene:csp-exceptions` | `headers.source`, `headers.format` | unset (graceful skip) |
| `hygiene:complexity` | `hygiene.complexity.max_ccn` / `.exclude` | 15 / `[]` (tests and gitignored paths are skipped without it) |
| `security:sast` | `security.sast.fail_on` | `high` (critical and high gate the same findings: SARIF has no critical level) |
| `security:dependencies` | `security.dependencies.fail_on` / `.include_dev` | `high` / `false` |
| `security:iac` | `security.iac.fail_on` | `high` |
| `security:sbom` | `security.sbom.format` / `.allow_empty` | `cyclonedx1.5+json` / `false` |
| all four Snyk checks | `security.snyk.org` (or the `SNYK_ORG` variable, which wins) | unset, so the token's default org |
| `reliability:cwv` | `reliability.cwv.public_report` (report hosting, not a threshold; budgets live in `.ss/lighthouserc.json`) | `false`, so reports stay local |

Tunings that are NOT yet config-driven (require code/file edits):

- `.ss/lighthouserc.json` (or the bundled `cli/slopstopper/data/lighthouserc.json`) holds the Core Web Vitals thresholds. Edit the `.ss/` copy if you want adopter-side persistence; the CLI prefers it over the package-data fallback.

Tuning is a real decision. When you raise a threshold, leave a `# why` comment in `.slopstopper.yml` so the next maintainer doesn't roll it back. Don't tune to silence noise; tune to match a deliberate design decision. Moving a Snyk `fail_on` to make one PR green is the wrong lever every time: a `.snyk` ignore names the issue, the reason and the date it comes back, and keeps the gate honest for the next finding.
