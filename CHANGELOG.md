# Changelog

All notable changes to **slopstopper-cli** are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Releases are annotated git tags of this repository (`vX.Y.Z`); adopters pin one under `cli.ref` in `.slopstopper.yml`. To cut one, follow [`docs/runbooks/RELEASE.md`](./docs/runbooks/RELEASE.md): bump `version` in `cli/pyproject.toml` and `__version__` in `cli/slopstopper/__init__.py`, add the section here, tag, push the tag.

## 0.1.0 (2026-10-12)

The first release of the enterprise fork of [hungovercoders/slopstopper](https://github.com/hungovercoders/slopstopper) (forked at v0.21.0, MIT). The CLI, the `task ss:*` surface, the hygiene and reliability checks and the two Claude Code skills carry over; the security layer, the release mechanism and the repo's own footprint change.

### Security layer: Snyk

* `security:sast` now wraps **Snyk Code** (`snyk code test`). It writes SARIF (`.ss/reports/sast/sast-report.sarif`) alongside the markdown report, ranks SARIF `error` / `warning` / `note` as high / medium / low, and gates on `security.sast.fail_on` (default `high`). Replaces Semgrep.
* `security:dependencies` (workflow `ss-security-dependencies-check.yml`) wraps **Snyk Open Source** (`snyk test --all-projects`): vulnerabilities and licence issues per manifest, gated on `security.dependencies.fail_on` (default `high`), with `security.dependencies.include_dev` for devDependencies. Replaces Trivy's `security:vulnerability:all`.
* New `security:iac` wraps **Snyk IaC** (`snyk iac test`): Terraform, CloudFormation, Kubernetes, Helm and ARM misconfigurations, gated on `security.iac.fail_on`. A repo with no IaC passes with a note.
* New `security:sbom` wraps **Snyk SBOM** (`snyk sbom --all-projects`): a CycloneDX (or SPDX) document written to `.ss/reports/sbom/` and uploaded as a CI artifact on every PR and on `main`. `security.sbom.format` picks the format; `security.sbom.allow_empty` accepts a repo with no manifests.
* New `task ss:security:monitor` sends the dependency graph to `snyk monitor` so the project stays on Snyk's dashboard between pushes; the dependencies workflow runs it on `main`.
* Shared Snyk plumbing (`cli/slopstopper/checks/_snyk.py`): `SNYK_TOKEN` from the environment only, `SNYK_ORG` or `security.snyk.org`, `SNYK_API` for regional tenants, analytics disabled on every invocation, verdicts computed from the report rather than Snyk's own threshold flag, and "no readable report" is exit 2 (could not run), never a pass. The `.snyk` policy file is honoured for ignores.
* `security:secrets` keeps Gitleaks, now installed at the exact version pinned in `mise.toml` rather than downloaded by the workflow.
* `security:dast` (OWASP ZAP) is removed, along with its gate module, `.zap/rules.tsv` seeding and the CSP-exception coupling.

### Distribution and release

* The CLI is no longer published to PyPI. Adopters pin an exact git tag under `cli:` in `.slopstopper.yml` (`source` + `ref`); `.github/actions/ss-setup` installs `slopstopper-cli @ git+<source>@<ref>#subdirectory=cli`, or the pip requirement in `SLOPSTOPPER_CLI_SPEC` for an internal index or mirror.
* `install.sh` is run from a tagged checkout (`git clone --branch vX.Y.Z …`), never piped from `curl`.
* Releases are hand-cut annotated git tags; release-please, the PyPI publish workflow and build-provenance attestation are gone.
* The toolchain is pinned to exact versions in `mise.toml`: `task`, `node`, `npm:snyk` 1.1308.0 and `gitleaks` 8.30.1.

### Workflows

* Every `ss-*.yml` workflow is SHA-pinned, least-privilege, carries a job timeout, and passes URLs and tokens through `env:` rather than interpolating them into shell.
* Removed: the Cloudflare post-deploy trigger, the deploy workflow, the gh-aw doc-updater and the release workflows.

### Repository

* The reference site (`app/`), the Cloudflare Worker (`worker/`), the demos and the site-specific docs are removed; this repo runs under the `library` profile.
* Docs: `docs/security/SNYK.md` (enterprise rollout: service account, regional tenant, data flow per product, egress allowlist, branch protection, rotation), `docs/security/IAC.md`, `docs/security/SBOM.md`; `SAST.md` and `VULNERABILITY.md` rewritten for Snyk; `RELEASE.md`, `UPGRADE_CLI.md` and `INSTALL.md` rewritten for the tag-based install.
* Version reset to 0.1.0 for the fork.
