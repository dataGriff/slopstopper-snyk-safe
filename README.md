# SlopStopper (Snyk edition)

**An enterprise-ready quality suite for every PR: Snyk-backed security scanning plus hygiene and reliability checks, installed into any repo from a tagged checkout.** The CLI is `slopstopper-cli`; humans, agents and CI invoke every check through one `task ss:*` surface. Forked from [hungovercoders/slopstopper](https://github.com/hungovercoders/slopstopper) (MIT).

## Install

From an exact tag, never from a pipe. Clone the release you reviewed, then run the installer inside the repo that should get the suite:

```bash
git clone --branch v0.1.0 https://github.com/dataGriff/slopstopper-snyk-safe.git
cd <your-repo>
bash ../slopstopper-snyk-safe/install.sh     # add --profile api or --profile library
```

It is idempotent (re-run to refresh) and lands the `ss-*.yml` workflows, `task ss:*` shims, the mise pin, a `.slopstopper.yml` starter and two Claude Code skills; CI installs the CLI from the tag pinned under `cli:` there.

CI needs the **`SNYK_TOKEN`** secret (a Snyk service-account token; without it every Snyk check exits 2) and optionally the **`SNYK_ORG`** (organisation slug) and **`SNYK_API`** (regional tenant) variables.

## Docs

- Adopting it: prerequisites, what lands, what each check needs, configure, update → [`docs/runbooks/INSTALL.md`](./docs/runbooks/INSTALL.md)
- Enterprise rollout: service account, regional tenants, data flow, egress allowlist, branch protection → [`docs/security/SNYK.md`](./docs/security/SNYK.md)
- Working in this repo: [`AGENTS.md`](./AGENTS.md) carries the conventions; [`docs/README.md`](./docs/README.md) routes to everything else
- Claude Code: `install.sh` lands the `slopstopper-install` and `slopstopper-triage` skills in `<repo>/.claude/skills/`

## Dogfooded here

This repo hosts the CLI (under [`cli/`](./cli)) and runs the suite it ships under the `library` profile. The badges are its own CI on every PR and push to `main`.

### 🔒 Security

[![SAST](https://github.com/dataGriff/slopstopper-snyk-safe/actions/workflows/ss-security-sast-check.yml/badge.svg?branch=main)](https://github.com/dataGriff/slopstopper-snyk-safe/actions/workflows/ss-security-sast-check.yml)
[![Dependencies](https://github.com/dataGriff/slopstopper-snyk-safe/actions/workflows/ss-security-dependencies-check.yml/badge.svg?branch=main)](https://github.com/dataGriff/slopstopper-snyk-safe/actions/workflows/ss-security-dependencies-check.yml)
[![IaC](https://github.com/dataGriff/slopstopper-snyk-safe/actions/workflows/ss-security-iac-check.yml/badge.svg?branch=main)](https://github.com/dataGriff/slopstopper-snyk-safe/actions/workflows/ss-security-iac-check.yml)
[![SBOM](https://github.com/dataGriff/slopstopper-snyk-safe/actions/workflows/ss-security-sbom-check.yml/badge.svg?branch=main)](https://github.com/dataGriff/slopstopper-snyk-safe/actions/workflows/ss-security-sbom-check.yml)
[![Secrets](https://github.com/dataGriff/slopstopper-snyk-safe/actions/workflows/ss-security-secrets-check.yml/badge.svg?branch=main)](https://github.com/dataGriff/slopstopper-snyk-safe/actions/workflows/ss-security-secrets-check.yml)
[![API Headers](https://github.com/dataGriff/slopstopper-snyk-safe/actions/workflows/ss-security-api-headers-check.yml/badge.svg?branch=main)](https://github.com/dataGriff/slopstopper-snyk-safe/actions/workflows/ss-security-api-headers-check.yml)

### 🧹 Hygiene

[![Complexity](https://github.com/dataGriff/slopstopper-snyk-safe/actions/workflows/ss-hygiene-complexity-check.yml/badge.svg?branch=main)](https://github.com/dataGriff/slopstopper-snyk-safe/actions/workflows/ss-hygiene-complexity-check.yml)
[![Docs Accuracy](https://github.com/dataGriff/slopstopper-snyk-safe/actions/workflows/ss-hygiene-docs-accuracy-check.yml/badge.svg?branch=main)](https://github.com/dataGriff/slopstopper-snyk-safe/actions/workflows/ss-hygiene-docs-accuracy-check.yml)
[![Docs Size](https://github.com/dataGriff/slopstopper-snyk-safe/actions/workflows/ss-hygiene-docs-size-check.yml/badge.svg?branch=main)](https://github.com/dataGriff/slopstopper-snyk-safe/actions/workflows/ss-hygiene-docs-size-check.yml)
[![Docs Structure](https://github.com/dataGriff/slopstopper-snyk-safe/actions/workflows/ss-hygiene-docs-structure-check.yml/badge.svg?branch=main)](https://github.com/dataGriff/slopstopper-snyk-safe/actions/workflows/ss-hygiene-docs-structure-check.yml)
[![Entry Files](https://github.com/dataGriff/slopstopper-snyk-safe/actions/workflows/ss-hygiene-entry-files-check.yml/badge.svg?branch=main)](https://github.com/dataGriff/slopstopper-snyk-safe/actions/workflows/ss-hygiene-entry-files-check.yml)
[![CSP Exceptions](https://github.com/dataGriff/slopstopper-snyk-safe/actions/workflows/ss-hygiene-csp-exceptions-check.yml/badge.svg?branch=main)](https://github.com/dataGriff/slopstopper-snyk-safe/actions/workflows/ss-hygiene-csp-exceptions-check.yml)
[![OpenAPI Drift](https://github.com/dataGriff/slopstopper-snyk-safe/actions/workflows/ss-hygiene-openapi-check.yml/badge.svg?branch=main)](https://github.com/dataGriff/slopstopper-snyk-safe/actions/workflows/ss-hygiene-openapi-check.yml)
[![Auto Label PRs](https://github.com/dataGriff/slopstopper-snyk-safe/actions/workflows/ss-hygiene-auto-label-pr.yml/badge.svg?branch=main)](https://github.com/dataGriff/slopstopper-snyk-safe/actions/workflows/ss-hygiene-auto-label-pr.yml)

### ✅ Reliability

[![Smoke Tests](https://github.com/dataGriff/slopstopper-snyk-safe/actions/workflows/ss-reliability-smoke-tests.yml/badge.svg?branch=main)](https://github.com/dataGriff/slopstopper-snyk-safe/actions/workflows/ss-reliability-smoke-tests.yml)
[![E2E](https://github.com/dataGriff/slopstopper-snyk-safe/actions/workflows/ss-reliability-e2e-check.yml/badge.svg?branch=main)](https://github.com/dataGriff/slopstopper-snyk-safe/actions/workflows/ss-reliability-e2e-check.yml)
[![Accessibility](https://github.com/dataGriff/slopstopper-snyk-safe/actions/workflows/ss-reliability-accessibility-check.yml/badge.svg?branch=main)](https://github.com/dataGriff/slopstopper-snyk-safe/actions/workflows/ss-reliability-accessibility-check.yml)
[![Core Web Vitals](https://github.com/dataGriff/slopstopper-snyk-safe/actions/workflows/ss-reliability-core-web-vitals.yml/badge.svg?branch=main)](https://github.com/dataGriff/slopstopper-snyk-safe/actions/workflows/ss-reliability-core-web-vitals.yml)
[![SEO Metatags](https://github.com/dataGriff/slopstopper-snyk-safe/actions/workflows/ss-reliability-seo-check.yml/badge.svg?branch=main)](https://github.com/dataGriff/slopstopper-snyk-safe/actions/workflows/ss-reliability-seo-check.yml)
[![Broken Links](https://github.com/dataGriff/slopstopper-snyk-safe/actions/workflows/ss-reliability-broken-links-check.yml/badge.svg?branch=main)](https://github.com/dataGriff/slopstopper-snyk-safe/actions/workflows/ss-reliability-broken-links-check.yml)
[![llms.txt](https://github.com/dataGriff/slopstopper-snyk-safe/actions/workflows/ss-reliability-llms-txt-check.yml/badge.svg?branch=main)](https://github.com/dataGriff/slopstopper-snyk-safe/actions/workflows/ss-reliability-llms-txt-check.yml)
[![robots.txt](https://github.com/dataGriff/slopstopper-snyk-safe/actions/workflows/ss-reliability-robots-txt-check.yml/badge.svg?branch=main)](https://github.com/dataGriff/slopstopper-snyk-safe/actions/workflows/ss-reliability-robots-txt-check.yml)
[![sitemap](https://github.com/dataGriff/slopstopper-snyk-safe/actions/workflows/ss-reliability-sitemap-check.yml/badge.svg?branch=main)](https://github.com/dataGriff/slopstopper-snyk-safe/actions/workflows/ss-reliability-sitemap-check.yml)
[![API Health](https://github.com/dataGriff/slopstopper-snyk-safe/actions/workflows/ss-reliability-api-health-check.yml/badge.svg?branch=main)](https://github.com/dataGriff/slopstopper-snyk-safe/actions/workflows/ss-reliability-api-health-check.yml)
[![API Latency](https://github.com/dataGriff/slopstopper-snyk-safe/actions/workflows/ss-reliability-api-latency-check.yml/badge.svg?branch=main)](https://github.com/dataGriff/slopstopper-snyk-safe/actions/workflows/ss-reliability-api-latency-check.yml)

### 🤖 Operational

[![Failure Alerts](https://github.com/dataGriff/slopstopper-snyk-safe/actions/workflows/ss-workflow-failure-issue.yml/badge.svg?branch=main)](https://github.com/dataGriff/slopstopper-snyk-safe/actions/workflows/ss-workflow-failure-issue.yml)
[![CI · slopstopper-cli](https://github.com/dataGriff/slopstopper-snyk-safe/actions/workflows/ci-cli.yml/badge.svg?branch=main)](https://github.com/dataGriff/slopstopper-snyk-safe/actions/workflows/ci-cli.yml)

## License and attribution

MIT; the full text is in [LICENSE](./LICENSE), which retains the upstream notice (forked at v0.21.0). Tool credits and licences are in [`ATTRIBUTIONS.md`](./ATTRIBUTIONS.md).
