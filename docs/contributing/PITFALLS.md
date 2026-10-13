# Common pitfalls when extending SlopStopper

A short list of the most-hit gotchas. Each one has bitten somebody
before; the rule is "do the obvious thing the obvious way."

## Quality checks

- **Adding a quality check workflow but forgetting to add a matching
  Task target.** CI and local diverge, so contributors can't reproduce
  the failure. Every workflow that runs a check must invoke
  `task ss:<category>:<action>`; the same target must exist in
  [`Taskfile.ss.yml`](../../Taskfile.ss.yml).
- **Putting analysis output anywhere other than under
  `.ss/reports/<category>/`.** It won't be `.gitignore`d and could
  pollute the consumer's repo when they install SlopStopper.
- **Invoking a task without the `ss:` prefix.** A bare `hygiene:complexity`
  gives "Task not found"; the target is `task ss:hygiene:complexity`, because
  all SlopStopper tasks live under the `ss` namespace via the root
  `Taskfile.yml`'s `includes:` block. Use `task ss:hygiene:complexity`.
- **Letting a Snyk check pass when Snyk never ran.** No token, a disabled
  product, a crashed CLI: each writes no readable report, and the only
  honest exit code is 2. `_snyk.incomplete_detail` renders that; never
  map "no results" to "no findings" in a new Snyk-backed check.
- **Gating on Snyk's `--severity-threshold` instead of the report.** The
  verdict lives in the check (`fail_on`), so the report always carries
  every finding and local, pre-push and CI agree. Passing the threshold
  flag to the CLI hides the findings below it from the report.

## Workflows

- **Creating a new workflow file without the `ss-` prefix.** It won't
  get grouped with the rest in the Actions UI, and consumers risk a
  clash when they install. Always name new workflows
  `ss-<category>-<action>.yml`.
- **Interpolating a URL or a token into `run:`.** `${{ inputs.url }}`
  inside a shell line is a command-injection surface; `cli/tests` and the
  reviewer will both reject it. Put the value under `env:` and read it as
  `"$URL"` in the script. Secrets (`SNYK_TOKEN`) are env-only everywhere,
  including the CLI, which never reads a token from `.slopstopper.yml`.
- **A `permissions:` block broader than the steps need, or none at all.**
  No block means the repository default, which may be write-all. Declare
  `contents: read` and add `pull-requests: write` / `issues: write` only on
  the workflows that comment.
- **No `timeout-minutes`.** A hung scanner then holds a runner for six
  hours and blocks the merge queue. Every job carries one.
- **Pinning an action to a tag.** `cli/tests/test_action_pins.py` fails
  it; use the commit SHA with the version in a trailing comment, and let
  Dependabot move it.
- **Forgetting the `workflow_run` lists.** A new check workflow's exact
  `name:` goes in both `ss-pr-summary.yml` and
  `ss-workflow-failure-issue.yml`; `workflow_run` cannot glob, so an
  omitted name silently never re-renders the summary or raises the issue.

## External resources and headers

- **Adding any external resource to a page.** CSP blocks it silently in
  production; test in DevTools first. If the resource is genuinely
  required, open a per-path exception in the file `headers.source`
  names and document it in
  [`docs/security/CSP_EXCEPTIONS.md`](../security/CSP_EXCEPTIONS.md),
  because the `ss:hygiene:csp-exceptions` check fails the build if they
  drift apart.
- **Adding a tool that reaches a new host.** An enterprise runner has an
  egress allowlist. Add the host to the table in
  [`docs/security/SNYK.md`](../security/SNYK.md) in the same PR, or the
  check will exit 2 on every locked-down runner with no obvious cause.

## Docs

- **Naming a file, task or workflow that doesn't exist.**
  `ss:hygiene:docs-accuracy` scans `docs/`, the root entry files and both
  skills for backtick-quoted paths, `task ss:*` targets and
  `.github/workflows/*.yml` references. Add the target before the doc that
  names it, or in the same PR.
- **Adding a doc without a route.** `ss:hygiene:docs-structure` fails on
  it. Every new doc gets a trigger-first "read" row in its category README
  (or the map), and the entry files stay under budget.
