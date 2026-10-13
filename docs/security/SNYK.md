# Snyk: credentials, tenancy, data flow and enterprise rollout

Four checks run through the Snyk CLI: `security:sast` (Snyk Code), `security:dependencies` (Snyk Open Source), `security:iac` (Snyk IaC) and `security:sbom` (Snyk SBOM). They share one credential, one organisation and one API endpoint. This page is the rollout guide: set up once per organisation, then every adopter repo needs only the three settings below.

## The three settings

| Name | Where | Required | What it is |
| ---- | ----- | -------- | ---------- |
| `SNYK_TOKEN` | repository (or organisation) **secret** | yes | the API token of a Snyk **service account**. Env only; never in `.slopstopper.yml`. Without it every Snyk check exits 2 (could not run); nothing passes silently |
| `SNYK_ORG` | repository **variable**, or `security.snyk.org` in `.slopstopper.yml` | recommended | the Snyk organisation **slug** to scan under. The variable wins when both are set. Unset, the CLI scans under the token's default org, which is rarely the one you meant |
| `SNYK_API` | repository or organisation **variable** | only for a regional tenant | the API base URL, e.g. `https://api.eu.snyk.io` or `https://api.au.snyk.io`. Unset means `https://api.snyk.io` (US) |

The workflows read them with `env:` and pass them to the CLI as environment variables; nothing is interpolated into a shell command. Locally, export the same three and `task ss:security:scan` runs exactly what CI runs, with the same pinned CLI (`npm:snyk` in `mise.toml`).

## Create the service account and organisation

1. **Pick or create the organisation.** One Snyk organisation per team or per trust boundary is the usual shape; a group holds the organisations. Settings that matter here live at the organisation level: whether Snyk Code is enabled, the licence policy, the severity thresholds and any org-level ignores.
2. **Enable Snyk Code** on that organisation (Settings → Snyk Code → Enable). `security:sast` exits 2 with "Snyk Code is not enabled" until it is.
3. **Create a service account** (Settings → Service accounts) with the **Org Admin** role if the account should run `snyk monitor` (which creates projects), or a custom role with *test* and *monitor* permissions on the organisation. A personal token works for a trial and is wrong for CI: it dies with the person, carries their full permissions and is not auditable as a machine identity.
4. **Store the token** as the `SNYK_TOKEN` secret, at organisation level in GitHub if several repositories share the Snyk org, scoped to the repositories that need it. Store the org slug as `SNYK_ORG` the same way.
5. **Confirm** with a `workflow_dispatch` of `ss-security-dependencies-check.yml` on a repo with a manifest: a 0 or 1 means the token works; a 2 names what is missing.

## Regional tenants

Snyk runs separate tenants (US, EU, AU). A token belongs to one tenant, and the CLI must be told which with `SNYK_API`, or every call returns an authentication error that looks like a bad token. Set `SNYK_API` at the GitHub organisation level so no adopter forgets it, and make sure the egress allowlist below names the regional host. The web UI's URL tells you the tenant: `app.eu.snyk.io` means `https://api.eu.snyk.io`.

## What each product sends to Snyk

The checks are honest about data flow because an enterprise reviewer will ask. Per product:

| Check | Product | What leaves the runner | Stored by Snyk |
| ----- | ------- | ---------------------- | -------------- |
| `security:sast` | Snyk Code | the **source files** it analyses, uploaded for the duration of the scan | the results, against the organisation |
| `security:dependencies` | Snyk Open Source | the **dependency graph** (package names and versions), not source | the results; with `snyk monitor` on `main`, the graph itself as a project on the dashboard |
| `security:sbom` | Snyk SBOM | the **dependency graph**, from which Snyk assembles the document | nothing beyond the request |
| `security:iac` | Snyk IaC | **nothing**: the CLI downloads its rule bundle and evaluates the files locally. Results reach Snyk only with `--report`, which the check does not pass | nothing |

Analytics are disabled on every invocation (`SNYK_DISABLE_ANALYTICS=1`): the runner sends Snyk the scan and nothing else. If Snyk Code's source upload is not acceptable for a repository, disable `ss-security-sast-check.yml` for that repo under `workflows.disabled` and keep the other three, which never send source.

## Egress allowlist

A locked-down runner needs these hosts reachable over HTTPS (443):

| Host | Why |
| ---- | --- |
| `api.snyk.io`, or the regional `api.eu.snyk.io` / `api.au.snyk.io` | every Snyk call: authentication, the scans, `snyk monitor`, the IaC rule bundle |
| `downloads.snyk.io` and `static.snyk.io` | the Snyk CLI binary that the `snyk` npm wrapper fetches on first run; `static.snyk.io` also serves the IaC rules |
| `registry.npmjs.org` | the `snyk` npm package (`npm:snyk` in `mise.toml`) and the Playwright / Lighthouse devDependencies |
| `pypi.org` and `files.pythonhosted.org` | `pip install` of `slopstopper-cli`'s one dependency (`lizard`) and its build backend |
| `github.com` and `objects.githubusercontent.com` | the `cli.source` clone of this repository, the `gitleaks` release mise installs, and the `actions/*` the workflows use |
| `mise.jdx.dev` / `github.com` | the mise installer and its tool downloads (`jdx/mise-action`) |

A mirror or internal index replaces the public hosts: point `cli.source` at the internal mirror of this repository (or set `SLOPSTOPPER_CLI_SPEC` to a pip requirement your index serves), configure npm and pip for the internal registry in the runner image, and keep only the Snyk hosts public. Snyk Broker is the supported path when Snyk must not reach GitHub directly; it does not change what the CLI needs.

## Branch protection

The suite is only a gate if the branch requires it. On each adopter repository, under Settings → Branches → the `main` rule, make these required status checks:

- `SlopStopper · SAST Analysis`
- `SlopStopper · Dependency Vulnerability Scan`
- `SlopStopper · IaC Scan`
- `SlopStopper · SBOM`
- `SlopStopper · Secrets Detection`

Require them to pass before merging, and keep "Require branches to be up to date" on so a PR cannot merge against a `main` that has since gained a dependency. The hygiene and reliability checks can be required too; the five above are the enterprise minimum. Each job carries a `timeout-minutes`, so a hung scan fails rather than blocking the queue.

## Rotating the token

1. Create a new token on the same service account (Snyk service accounts hold one token; rotating regenerates it) or create a second service account for a zero-gap swap.
2. Update the `SNYK_TOKEN` secret in GitHub. An organisation-level secret rotates every repository at once.
3. Dispatch one Snyk workflow and confirm exit 0 or 1.
4. Revoke the old token (delete the old service account if you created a second).

Rotate on a schedule the organisation sets, on any suspected exposure, and whenever someone with access to the GitHub secret leaves. The token never appears in a report, a PR comment or an artifact: the checks pass it to the CLI through the environment and `--redact`-style scrubbing is unnecessary because the CLI never echoes it.

## What to do on a finding

| Check | Real finding | Accepted risk or false positive |
| ----- | ------------ | ------------------------------- |
| `security:sast` | fix the code; the SARIF names the file and line, and the Snyk UI explains the dataflow | an ignore in `.snyk` with a reason and an expiry ([SAST.md](SAST.md)) |
| `security:dependencies` | upgrade to the version the report's fix column names | `.snyk` ignore scoped to the dependency path ([VULNERABILITY.md](VULNERABILITY.md)) |
| `security:iac` | fix the resource definition | `.snyk` ignore scoped to the resource ([IAC.md](IAC.md)) |
| `security:sbom` | add the manifest or lock file Snyk needs | `security.sbom.allow_empty: true` for a repo with no dependencies ([SBOM.md](SBOM.md)) |
| `security:secrets` | revoke, scrub history, rotate ([SECRETS.md](SECRETS.md)) | a path-scoped allowlist in the Gitleaks config file (for example, a test-fixture path); [SECRETS.md](SECRETS.md) has the format |

Never move `fail_on` to make a PR green. The gate is one key per check so that a loosening is visible in review; an ignore in `.snyk` is the honest form, because it names the issue, the reason and the date it comes back.

## Exit codes, for the record

Every Snyk check keeps the suite's contract: 0 ran clean (or nothing to scan), 1 ran and found something at or above `fail_on`, 2 could not run (no token, wrong tenant, Snyk Code disabled, the CLI missing, no readable report). CI gates on the code directly; a 2 fails the job without opening a tracking issue, because it is not a verdict on the repository.
