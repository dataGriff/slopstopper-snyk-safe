# Hygiene

Overview of code and documentation hygiene and quality gates.

## Overview

Hygiene checks keep code within complexity bounds and documentation sized, routed and accurate enough for an AI context window.

## Checks

### Code Complexity Analysis
Analyzes code complexity using Lizard to identify overly complex functions and modules. Helps identify refactoring opportunities and maintain code health.

```bash
task ss:hygiene:complexity
```

The check **fails** when any function's cyclomatic complexity (CCN) exceeds `hygiene.complexity.max_ccn` (default `15`, which is Lizard's own warning line). The gate lives in the CLI, so `task ss:hygiene:complexity` returns the same pass/fail locally, in the pre-push hook and in CI. There is no separate CI-only threshold. To run stricter, set the knob in `.slopstopper.yml`:

```yaml
hygiene:
  complexity:
    max_ccn: 10   # McCabe's classic "review" bound
```

The gate measures production code. Test files by the common conventions (`tests/`, `test/`, `__tests__/`, `spec/`, `*_test.go`, `test_*.py`, `*.test.*`, `*.spec.*`, `*_spec.rb`, `*Test.java` …) and everything `.gitignore` ignores (build output such as `dist/`) are skipped, so a local run after a build sees what CI sees. Committed generated or vendored code goes in `hygiene.complexity.exclude` (globs, added to the built-in set).

When a function trips the ceiling and is genuinely well-factored (e.g. a flat dispatch or validation table), prefer raising `max_ccn` with a note over contorting the code, but treat that as a deliberate, documented decision rather than a way to silence noise.

### Excluding docs from the docs checks

`hygiene.docs.exclude` (repo-relative globs, default `docs/archive/**`) takes paths out of docs-size, docs-structure and docs-accuracy at once. Use it for content that lives under `docs/` but isn't agent or contributor documentation, such as a course rendered onto a site, rather than raising budgets or turning off routing for the real docs. Setting it replaces the default, so list the archive too if you keep one. Every report has an **Excluded** section naming what it left out.

### Documentation Size Monitoring
Monitors overall documentation size and checks against configured thresholds:

- **Total documentation size:** max 150 KB
- **Individual file sizes:** max 20 KB
- **Number of documentation files:** max 25

Advisory: it always exits 0 and the report carries the verdict. Under AGENTS.md-first only `AGENTS.md` is always loaded and every other doc is opt-in behind a route, so the per-file cap is the one that protects a reader; the totals are a growth signal, sized to the repo, and moved only in a commit that says why. Running it generates `.ss/reports/docs/docs-size-report.md`.

```bash
task ss:hygiene:docs-size
```

### Documentation Structure Validation

Validates the AGENTS.md-first model: every doc under `docs/` must be reachable by an **explicit route** from `AGENTS.md`, the map (`docs/README.md`) or a `README.md` above it. An explicit route is a line that names its trigger and says read (`Before you change CI, read docs/ci.md for what task ci runs`, or a row of a "When you are… | Do this" table). A doc nothing routes to is invisible to agents; a "see also" is skipped. The report lists every reachable doc with its hop count.

**Fails on:**

- `unrouted_doc` is a doc with no explicit route (the message says whether it is linked softly, or routed from a README that is itself unreachable)
- `soft_route` is a `.md` link in the map that does not say when to read it
- `route_too_deep` means more hops than `hygiene.docs_structure.max_route_depth` (default 3: `AGENTS.md` → map → directory README → doc)
- `doc_over_lines` is a topic doc past `hygiene.docs_structure.max_doc_lines` (default 300; the map is exempt). Split by concern and route each part
- `legacy_index` is a legacy index file beside the map. The map is a README so the repo UI renders it in place; fold it in and delete it
- `broken_route` is a route whose target does not exist

`hygiene.docs_structure.require_routed_docs: false` keeps only the other rules. The map path is shared with the entry-files check (`hygiene.entry_files.map_path`).

```bash
task ss:hygiene:docs-structure
```

### Documentation Accuracy
Scans markdown for stale or broken references: internal markdown links that don't resolve, `task <name>` references to non-existent Taskfile tasks, workflow filename references that don't match `.github/workflows/`, and stale source-file references. A backtick-quoted file resolves at the repo root, next to the doc, or anywhere in the repo by name or path suffix (for example `pack.json` or `scripts/render.sh`), counting files `git ls-files` lists, so prose can name files the way people say them. Scans all of `docs/` plus the repo-root entry files (`README.md`, `AGENTS.md`, `CLAUDE.md`, `CONTRIBUTING.md`) so drift in the most-loaded files is caught too.

Runs **weekly on a schedule** (Monday 07:00 UTC), on PRs/pushes that change docs or project structure, and can be triggered manually. When issues are found, a GitHub issue is automatically created or updated.

```bash
task ss:hygiene:docs-accuracy
```

By default it reads `docs/**/*.md` and the four root entry files. `hygiene.docs_accuracy.extra_paths` (repo-relative globs in `.slopstopper.yml`) brings more files into scope, with only the checks that are precise for a file describing an adopter's tree rather than this one: `task ss:…` and workflow references must exist (markdown), and every `github.com/<this repo>/blob|tree/<ref>/<path>` link must point at a path that exists (markdown and HTML). This repo scans `.claude/skills/**/*.md` this way, because every piece of skill drift the repo review found lived in a file the `docs/`-only scan never read.

### Entry-File Budget

Enforces the AGENTS.md-first entry files declared in [`docs/README.md`](../README.md#the-model). `AGENTS.md` is loaded into every agent conversation (it is prompt-cached, so inlining what most tasks need is cheap, while every routing hop costs a tool turn), but instruction-following degrades as rules pile up, so it stays under **~2,000 estimated tokens** (chars/4; roughly 40–60 rules) and routes the overflow. The check enforces:

- **Token budgets.** `AGENTS.md` ≤ `hygiene.entry_files.max_tokens` (2000), `README.md` ≤ `readme_max_tokens` (600, pipeline badges excluded), the map ≤ `map_max_tokens` (1000)
- **`CLAUDE.md` is exactly `@AGENTS.md`.** With one agent entry point there is nothing to drift (`require_claude_include`)
- **Every `.md` link in `AGENTS.md` is an explicit route** that gives the trigger, then "read", then the file (`require_explicit_routes`). One of them reaches the map, which `README.md` also links (`require_map_pointer`)
- **The map exists** at `hygiene.entry_files.map_path` (`docs/README.md`)

It prints the **cold-start cost** (always-loaded tokens, the fallback hop, the sum) on every run, so a creeping cost is visible before a budget trips. Fails the build on violation; the report carries a paste-ready fix for each one (the route line, the `@AGENTS.md` body, a map skeleton, the rename for a legacy index file). The fix for an over-budget file is to move the content that serves the fewest tasks into a `docs/` topic doc and route it, never to raise the budget.

```bash
task ss:hygiene:entry-files
```

The workflow `ss-hygiene-entry-files-check.yml` runs on PRs/pushes touching `README.md`, `AGENTS.md`, `CLAUDE.md` or `docs/README.md`.

### CSP Exceptions Drift Check

Validates that every per-path CSP relaxation in the headers file named by
`headers.source` in `.slopstopper.yml` has a matching documented entry in
[`docs/security/CSP_EXCEPTIONS.md`](../security/CSP_EXCEPTIONS.md), and vice
versa. Fails the build if either side has an entry the other lacks.

A site should ship a strict `default-src 'self'` CSP by default. When a page
genuinely needs a vetted third-party widget (a comments embed, an analytics
tag), the exception must be declared in both files and SRI-pinned. This check
keeps the two sources of truth in sync so CSP relaxations are never silent or
undocumented. With `headers.source: null` (this repo, and the installer's seed)
it skips with exit 0.

```bash
task ss:hygiene:csp-exceptions
```

The workflow `ss-hygiene-csp-exceptions-check.yml` runs on PRs/pushes touching
anything: the source is whatever `headers.source` names, and under `format: meta` the workflow builds the site first and reads every page's `<meta>` CSP.

### OpenAPI Drift

The API-shaped analogue of the documentation-accuracy check. That one catches a
doc referencing a file that moved; this one catches a spec that has stopped
describing the API it documents, which is documentation that quietly became
fiction on a different surface.

**The only hygiene check that needs a URL**, since drift means the spec measured
against the thing it documents. So it is the one hygiene check `library` drops,
and deliberately **not** part of `task ss:hygiene:test`, because that aggregate
is what the pre-push hook runs and is all-static, so adding it would make every
`git push` hit the network.

| Signal | What it means | Needs |
|---|---|---|
| Served but not committed | the committed spec is stale against what is deployed | `served_spec` |
| Committed but not served | a route was removed and the spec wasn't updated, or the deploy is behind | `served_spec` |
| Documented path returns 404 | the spec describes a route that isn't there | a URL |
| Documented path returns 5xx | the route exists but is broken | a URL |

The committed-vs-served comparison is the headline: it compares operation sets
(method + path), not whole documents, so a reordered spec or a different
`servers` block is not drift. The comparison needs no probing and no guessing,
and it raises no false positives.

Paths taking parameters (`/users/{id}`) are counted but never probed: a 404 from
one could mean "route missing" or merely "no such record", and reporting both as
failures would train people to ignore the check. A 405 proves the route exists
and passes; only `GET` is sent. Detecting *undocumented* routes is out of scope
because it needs framework-specific route introspection.

**JSON specs only.** `slopstopper-cli` ships no YAML parser (its only
third-party dependency is `lizard`, for `hygiene:complexity`). A YAML spec is a graceful skip with that guidance rather
than a failure, since most frameworks serve the JSON form at `/openapi.json`.

```yaml
# .slopstopper.yml
api:
  openapi:
    spec:                 # URL or repo-relative JSON file
    served_spec:          # URL the live API publishes its spec at
    probe_paths: true     # probe parameterless documented paths
    ignore_paths: []      # globs excluded from probing and comparison
```

With `api.openapi.spec` unset the check exits 0 with a note, because an
unconfigured check is not a failing check.

```bash
task ss:hygiene:openapi -- https://api.example.com
```

Report: `.ss/reports/openapi/openapi-report.{md,json}`. Workflow:
`ss-hygiene-openapi-check.yml`, resolving `urls.preview` on pull requests and
`urls.production` on main, schedules and deployments like the API
workflows. With no URL the committed-vs-served comparison still runs (it needs
only `served_spec`); just the probes are skipped.

## Quick Reference

Run all hygiene checks:
```bash
task ss:hygiene:test
```

Run individual checks:
```bash
task ss:hygiene:complexity        # Analyze code complexity
task ss:hygiene:docs-size         # Monitor overall documentation size
task ss:hygiene:entry-files       # Token budgets + explicit routes on AGENTS.md, README.md, CLAUDE.md, the map
task ss:hygiene:docs-structure    # Every doc under docs/ has an explicit route
task ss:hygiene:docs-accuracy     # Check for broken links and stale refs
task ss:hygiene:csp-exceptions    # Validate CSP exceptions are fully documented
```

`task ss:hygiene:test` runs everything above **except** `hygiene:openapi`, which
needs a live API, so run that one separately with a URL.

## When to Run

`task ss:hygiene:test` before every push (the pre-push hook runs it); CI runs each check on the paths it watches. The thresholds keep code refactorable (complexity), keep every doc readable in one sitting (per-doc size and lines), and keep a typical task to zero reads beyond `AGENTS.md` (entry-file budgets, explicit routes). When one trips, move content deeper and route it; the `.ss/reports/` file for the check names what to move.
