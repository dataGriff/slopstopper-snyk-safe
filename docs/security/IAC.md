# Infrastructure as Code (IaC) Scanning

`security:iac` runs **Snyk IaC** over the repository's infrastructure code and reports misconfigurations: a public S3 bucket, a security group open to the world, a container running as root, a Kubernetes deployment with no resource limits, an unencrypted disk. This guide explains what it evaluates, how it blocks, and how to accept a finding.

## Quick Start

### Run Analysis Locally

```bash
export SNYK_TOKEN=…        # a Snyk token; CI uses the SNYK_TOKEN secret
task ss:security:iac
```

### What You Get Automatically

- ✅ PR comments with the misconfigurations, per file, highest severity first
- ✅ Merge blocking on findings at or above `security.iac.fail_on` (default: high)
- ✅ GitHub issues on `main` when blocking findings land
- ✅ A pass with a note on a repository that has no IaC at all, because an unconfigured check is not a failing check

### Common Issues

| Problem | Solution |
|---------|----------|
| Exit 2 with `could not authenticate` | `SNYK_TOKEN` is missing. The rules download needs it even though the evaluation is local |
| "No IaC files found" and exit 0 | Expected on an application repo. If the repo does have IaC Snyk missed, check the file extensions: Terraform `.tf`, CloudFormation `.yaml`/`.json` with `AWSTemplateFormatVersion` or `Resources`, Kubernetes manifests with `apiVersion`/`kind`, Helm charts, ARM templates |
| A finding is wrong for this deployment | Ignore it in `.snyk` with a reason and an expiry (below) |
| Don't want IaC scans? | List `ss-security-iac-check.yml` under `workflows.disabled`, or delete it |

### Severity Reference

| Severity | Status |
|----------|--------|
| CRITICAL / HIGH | 🔴 Blocking under the default `fail_on: high` |
| MEDIUM / LOW | ⚠️ Reported, non-blocking |

A severity the check doesn't recognise ranks as critical.

---

## Overview

What IaC scanning checks: **every infrastructure file Snyk recognises** (Terraform and Terraform plan output, CloudFormation, Kubernetes manifests, Helm charts, ARM templates; Dockerfiles when the org enables it), evaluated against Snyk's IaC rule set.

The IaC workflow:

- ✅ Runs on every PR to `main` and push to `main`
- ✅ Runs `snyk iac test` with `--json-file-output` over the repository root
- ✅ Posts the report as a PR comment and opens a `main` issue on blocking findings
- ✅ Fails closed (exit 2) when Snyk produced no readable report

**What leaves the runner.** The CLI downloads its rule bundle and evaluates the files **locally**. Results are not reported to Snyk unless `--report` is passed, and this check does not pass it, so IaC findings never appear on the Snyk dashboard and no file content leaves the runner. If you want them on the dashboard, run `snyk iac test --report` yourself; the check deliberately keeps the quieter default.

## Files Involved

| File | Purpose |
|------|---------|
| `.github/workflows/ss-security-iac-check.yml` | GitHub Actions workflow |
| `Taskfile.ss.yml` (`security:iac` task) | Local shim → `slopstopper run security:iac` |
| `cli/slopstopper/checks/iac.py` | Check implementation (subprocess-invokes `snyk iac test`, reads the JSON, renders the MD report) |
| `cli/slopstopper/checks/_snyk.py` | Shared Snyk plumbing |
| `.snyk` | Snyk's policy file: ignores with reason and expiry (optional, repo root) |

## Configuration

```yaml
# .slopstopper.yml
security:
  iac:
    fail_on: high      # critical | high (default) | medium | low | none
  snyk:
    org: my-org-slug   # or SNYK_ORG
```

## Ignoring a finding: the `.snyk` policy

```yaml
# .snyk
version: v1.25.0
ignore:
  SNYK-CC-TF-1:
    - 'infra/dev/*':
        reason: Dev-only bucket, public by design, holds published docs
        expires: 2027-01-01T00:00:00.000Z
```

The rule id (`SNYK-CC-…`) and the resource path are in the report. Scope the ignore to the resource or directory rather than `*`, give it a reason a reviewer can check, and let it expire so the decision is revisited.

## Reports

`.ss/reports/iac/iac-report.{md,json}`: the markdown report (human-readable) and Snyk's full JSON output, both uploaded as a workflow artifact.
