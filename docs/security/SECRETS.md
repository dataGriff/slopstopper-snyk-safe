# Secrets Detection

`security:secrets` runs **Gitleaks** to identify hardcoded credentials, API keys, and other sensitive data in your source code and git history. Gitleaks is pinned to an exact version in `mise.toml`, so local runs and CI use the same scanner. This guide explains how to customise and use this feature.

## Quick Start

### Run Analysis Locally
```bash
task ss:security:secrets
```

### What You Get Automatically
- ✅ PR comments with secrets detection report
- ✅ Merge blocking if any secrets are detected
- ✅ GitHub issues on main branch if secrets land there

### Common Issues

| Problem | Solution |
|---------|----------|
| Workflow not triggering? | Check workflow is at `.github/workflows/ss-security-secrets-check.yml` |
| False positives? | Add a `.gitleaks.toml` allowlist (see below) |
| Don't want secrets checks? | Delete `.github/workflows/ss-security-secrets-check.yml` |

---

## Overview

What secrets detection checks: **hardcoded credentials** such as API keys, tokens, passwords, private keys and other sensitive values in source code files and git history.

The secrets workflow:
- ✅ Runs automatically on every PR to `main` and push to `main`
- ✅ Scans all files and full git history with Gitleaks
- ✅ Posts findings as PR comments
- ✅ Creates GitHub issues when secrets land on `main`
- ✅ Fails PRs whenever secrets are detected (always blocking)
- ✅ Never writes the credential to disk, because gitleaks runs with `--redact` and the check strips `Secret` / `Match` / `Line` / `Message` (a commit message can quote the value) plus author PII before anything reads the JSON; rule, file, line number and commit survive. A report that can't be parsed is scrubbed and fails the check rather than reading as "no findings". CI artifacts are downloadable by anyone with read access, so a check that contains a leak must not widen its audience

## Files Involved

| File | Purpose |
|------|---------|
| `.github/workflows/ss-security-secrets-check.yml` | GitHub Actions workflow |
| `Taskfile.ss.yml` (`secrets` task) | Local task runner shim → `slopstopper run security:secrets` |
| `cli/slopstopper/checks/secrets.py` | Check implementation (subprocess-invokes Gitleaks, renders MD report) |
| `.gitignore` | Excludes `.ss/reports/secrets/` |

## Suppressing False Positives

Create a `.gitleaks.toml` in the project root to allowlist known false positives:

```toml
[allowlist]
description = "Project allowlist"

# Allowlist a specific file
paths = [
  '''tests/fixtures/.*'''
]

# Allowlist a specific pattern (e.g. a test key)
regexes = [
  '''AKIAIOSFODNN7EXAMPLE'''
]

# Allowlist a specific commit
commits = [
  "abc123def456"
]
```

## If Secrets Are Detected

Act immediately:

1. **Revoke** the exposed credential (rotate API keys, invalidate tokens)
2. **Remove** the secret from the codebase
3. **Clean git history** if the secret was committed:
   ```bash
   git filter-branch --force --index-filter \
     "git rm --cached --ignore-unmatch path/to/file" \
     --prune-empty --tag-name-filter cat -- --all
   ```
   Or use [BFG Repo Cleaner](https://rtyley.github.io/bfg-repo-cleaner/) for a simpler approach.
4. **Force-push** the cleaned history (coordinate with your team)

## Disable Secrets Checking

```bash
# Option A: delete the workflow
rm .github/workflows/ss-security-secrets-check.yml

# Option B: disable in GitHub UI → Actions → Secrets Detection → Disable workflow
```

## Running Locally

```bash
mise install                 # task + gitleaks at the versions mise.toml pins
task ss:security:secrets
```

Reports are saved to `.ss/reports/secrets/`.
