# Third-party attributions

This repository is a fork of [hungovercoders/slopstopper](https://github.com/hungovercoders/slopstopper)
at v0.21.0, published under the MIT licence; the upstream copyright notice
is retained in [`LICENSE`](./LICENSE). The fork replaces the original
security scanners with the Snyk CLI, removes the reference site and the
agentic workflows, and ships the CLI as git tags rather than through PyPI.

`slopstopper-cli` is MIT-licensed. Its only bundled runtime dependency is
**Lizard** (MIT), declared in `cli/pyproject.toml` so pip installs it into
the same environment, where its own package metadata carries its licence
notice. slopstopper-cli does not bundle or statically link any other
third-party code: every remaining tool below is invoked via `subprocess`
only; each stays under its own licence, owned and maintained by its
upstream project, and adopters install them separately (the pinned
versions are in `mise.toml`).

The same boundary applies at runtime: `slopstopper run <check>` shells out
to these tools and does not import them. Updating this file is part of the
checklist whenever a new check or external tool is added (see
[`docs/contributing/CHANGE_MAP.md`](./docs/contributing/CHANGE_MAP.md)).

## Tools invoked by slopstopper-cli

| Tool | Licence | Used by | Upstream |
|------|---------|---------|----------|
| Snyk CLI | Apache-2.0 (the CLI; using it requires a Snyk account and is subject to Snyk's terms of service) | `ss-security-sast` (Snyk Code), `ss-security-dependencies` (Snyk Open Source), `ss-security-iac` (Snyk IaC), `ss-security-sbom` (Snyk SBOM), `task ss:security:monitor` | <https://github.com/snyk/cli> |
| Gitleaks | MIT | `ss-security-secrets` | <https://github.com/gitleaks/gitleaks> |
| Lizard | MIT | `ss-hygiene-complexity` | <https://github.com/terryyin/lizard> |
| markdownlint-cli | MIT | `task contributing:lint` (repo-internal, advisory) | <https://github.com/igorshubovych/markdownlint-cli> |
| Playwright | Apache-2.0 | smoke, E2E, accessibility, broken-links | <https://github.com/microsoft/playwright> |
| axe-core | MPL-2.0 | accessibility audit (loaded into Playwright) | <https://github.com/dequelabs/axe-core> |
| Lighthouse CI | Apache-2.0 | Core Web Vitals | <https://github.com/GoogleChrome/lighthouse-ci> |
| GitHub CLI (`gh`) | MIT | `slopstopper emit` (PR comments, issues) | <https://github.com/cli/cli> |
| Node.js | MIT | runs the Snyk CLI (npm package), Playwright, Lighthouse, `slopstopper serve` | <https://github.com/nodejs/node> |
| mise | MIT | installs the pinned toolchain from `mise.toml`, locally and in CI | <https://github.com/jdx/mise> |
| Task | MIT | the `task ss:*` shim layer | <https://github.com/go-task/task> |

## GitHub Actions / build-time dependencies

Used across `.github/workflows/*.yml` and `.github/actions/*/action.yml`.
Every action is pinned to a commit SHA with the release in a trailing
comment; the versions below are those pins.

GitHub-maintained actions (all MIT):

| Action | Licence | Purpose |
|--------|---------|---------|
| `actions/checkout` v7.0.1 | MIT | check out the repo in workflows |
| `actions/setup-python` v7.0.0 | MIT | install Python in `ss-setup` and `ci-cli.yml` |
| `actions/upload-artifact` v7.0.1 | MIT | upload reports, the SARIF and the SBOM as artifacts |
| `actions/github-script` v9.0.0 | MIT | inline JS for the PR summary and failure tracker |
| `actions/labeler` v7.0.0 | MIT | auto-label PRs (`ss-hygiene-auto-label-pr`) |

Third-party actions:

| Action | Licence | Purpose |
|--------|---------|---------|
| `jdx/mise-action` v5.1.1 | MIT | install the pinned toolchain (task, node, snyk, gitleaks) from `mise.toml` |

## Verification

The authoritative current state lives in:

- subprocess call sites under [`cli/slopstopper/checks/`](./cli/slopstopper/checks/),
- the pins in [`mise.toml`](./mise.toml), and
- workflow definitions under [`.github/workflows/ss-*.yml`](./.github/workflows/).

The CI check `slopstopper run hygiene:docs-accuracy` catches drift between
this document and the workflow set.
