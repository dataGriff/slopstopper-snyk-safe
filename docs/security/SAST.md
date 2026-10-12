# Static Application Security Testing (SAST)

`security:sast` runs **Snyk Code** over your own source code to find security bugs: injection, insecure deserialisation, path traversal, hardcoded credentials, weak cryptography and the rest of Snyk's rule set. This guide explains how to run it, what blocks, how to accept a finding, and where the SARIF goes.

## Quick Start

### Run Analysis Locally

```bash
export SNYK_TOKEN=…        # a Snyk token; CI uses the SNYK_TOKEN secret
task ss:security:sast
```

The `snyk` CLI comes from the `mise.toml` pin (`npm:snyk`), so `mise install` puts the same version on your PATH that CI runs. `SNYK_ORG` (or `security.snyk.org`) selects the organisation; `SNYK_API` selects a regional tenant. [SNYK.md](SNYK.md) covers all three.

### What You Get Automatically

- ✅ PR comments with the findings, highest severity first
- ✅ Merge blocking on findings at or above `security.sast.fail_on` (default: high)
- ✅ GitHub issues on `main` when blocking findings land (a scan that couldn't run fails the job but opens no issue)
- ✅ A SARIF file (`.ss/reports/sast/sast-report.sarif`) uploaded as a workflow artifact beside the markdown report

### Common Issues

| Problem | Solution |
|---------|----------|
| Exit 2 with `could not authenticate` or `missing SNYK_TOKEN` | The token isn't in the environment. Locally, export `SNYK_TOKEN`; in CI, add the repository secret. The check never passes a scan that didn't happen |
| Exit 2 with `Snyk Code is not enabled` | Snyk Code is switched off for the organisation. Enable it under the org's settings (Snyk Code → Enable) or scan under an org that has it (`SNYK_ORG`) |
| Exit 2 and the org slug looks wrong | `SNYK_ORG` must be the org **slug** (or id), not its display name; `snyk config get org` and the org settings page show it |
| Want stricter or looser blocking? | `security.sast.fail_on` in `.slopstopper.yml` (see below) |
| A finding is a false positive or accepted risk | Ignore it in the `.snyk` policy with a reason and an expiry (see below), never by lowering `fail_on` |
| Don't want SAST? | Delete `.github/workflows/ss-security-sast-check.yml` or list it under `workflows.disabled` |

### Severity Reference

Snyk Code writes SARIF, which has three levels. The check ranks them so one `fail_on` scale works across the Snyk checks:

| SARIF level | Ranked as | Default status | Action |
|-------------|-----------|----------------|--------|
| `error` | high | 🔴 Blocking | Must be fixed (or ignored in `.snyk` with a reason) before merge |
| `warning` | medium | ⚠️ Non-blocking | Review recommended |
| `note` | low | ℹ️ Informational | Awareness only |

Snyk Code has no separate critical level in SARIF, so `fail_on: critical` and `fail_on: high` gate the same findings. A level the check doesn't recognise ranks as critical, because an unknown label must not quietly fall below the gate.

---

## Overview

What SAST checks: **your own source code** for security vulnerabilities, using Snyk Code's semantic analysis (dataflow across files, not just pattern matching).

The SAST workflow:

- ✅ Runs on every PR to `main` and push to `main`
- ✅ Runs `snyk code test` with `--sarif-file-output`, under the org named by `SNYK_ORG` / `security.snyk.org`
- ✅ Posts findings as a PR comment
- ✅ Creates a GitHub issue when blocking findings land on `main`
- ✅ Fails PRs with blocking findings, and fails closed (exit 2) when Snyk produced no readable SARIF or exited with an error

**What leaves the runner.** Snyk Code uploads the source files it analyses to Snyk's servers for the duration of the scan; results are stored against the organisation. If that is a concern for a given repository, the data-flow statement and the regional-tenant option are in [SNYK.md](SNYK.md).

## Files Involved

| File | Purpose |
|------|---------|
| `.github/workflows/ss-security-sast-check.yml` | GitHub Actions workflow |
| `Taskfile.ss.yml` (`security:sast` task) | Local task runner shim → `slopstopper run security:sast` |
| `cli/slopstopper/checks/sast.py` | Check implementation (subprocess-invokes `snyk code test`, reads the SARIF, renders the MD report) |
| `cli/slopstopper/checks/_snyk.py` | What the Snyk checks share: CLI lookup, environment, severity scale, `fail_on` gate |
| `.snyk` | Snyk's policy file: ignores with reason and expiry (optional, repo root) |
| `.gitignore` | Excludes `.ss/reports/sast/` |

## Key Configuration Points

### Failure threshold

```yaml
# .slopstopper.yml
security:
  sast:
    fail_on: high      # critical | high (default) | medium | low | none
  snyk:
    org: my-org-slug   # or the SNYK_ORG variable, which wins when both are set
```

`none` reports without ever failing, which is a reasonable first week on a repo with a backlog: read the reports, fix or ignore what is there, then move the gate to `high`. The check's exit code is the verdict; the workflow never re-counts findings.

### Ignoring a finding: the `.snyk` policy

Snyk reads a `.snyk` file at the repo root. An ignore names the issue id, the path it applies to, a reason and an expiry, so an accepted risk is visible in review and comes back for re-evaluation:

```yaml
# .snyk
version: v1.25.0
ignore:
  'snyk:lic:...':          # the issue id from the report
    - '*':
        reason: Sample credential in a test fixture; never valid
        expires: 2027-01-01T00:00:00.000Z
```

The id and path are in the markdown report. Scope ignores to a path rather than `*` where you can. The same file carries ignores for the dependency and IaC checks, so one review covers every accepted risk in the repo. Prefer a narrow ignore with a reason over moving `fail_on`, because the gate stays honest for the next finding.

### The SARIF and GitHub code scanning

The check always writes SARIF, because it is the format GitHub code scanning ingests: the same file the check gates on can feed the organisation's Security tab. Uploading is optional and off by default (it needs `security-events: write`, which the shipped workflow does not request). To turn it on, add a `github/codeql-action/upload-sarif` step after the scan in your copy of the workflow, pinned to a commit SHA like every other action, with `sarif_file: .ss/reports/sast/sast-report.sarif` and the extra permission. Code scanning must be enabled on the repository (GitHub Advanced Security on private repos).

## Reports

`.ss/reports/sast/sast-report.{md,sarif}`: the markdown report (human-readable; the PR comment is built from it) and the SARIF (machine-readable; the file the check gated on). Both are uploaded as the `sast-reports` workflow artifact.

## Disable SAST

```bash
# Option A: config-driven (survives a re-run of install.sh)
#   workflows.disabled: [ss-security-sast-check.yml] in .slopstopper.yml, then re-run install.sh
# Option B: delete the workflow
rm .github/workflows/ss-security-sast-check.yml
```
