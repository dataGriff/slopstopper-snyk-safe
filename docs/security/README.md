# Security

Security scanning and controls for this project: what each `security:*` check
looks at, how it blocks, and where to tune it. One file per check; this page
is the map, so read only the doc whose trigger matches. Four of the checks
run through the Snyk CLI and share one credential: set that up first.

## Routes

| When you are…                                                        | Do this                                                                 |
| -------------------------------------------------------------------- | ----------------------------------------------------------------------- |
| setting up Snyk for CI (service account, `SNYK_TOKEN`, `SNYK_ORG`, `SNYK_API`), rolling the suite out across an enterprise, allowlisting egress, or rotating the token | Read [SNYK.md](SNYK.md) first for the account model, what each product sends to Snyk, the hosts a runner needs, branch protection and rotation |
| tuning what SAST blocks, uploading the SARIF to code scanning, or ignoring a Snyk Code finding | Read [SAST.md](SAST.md) for the quick start, the severity mapping, `security.sast.fail_on`, the `.snyk` policy and the SARIF file |
| fixing a dependency or licence finding, keeping the project on Snyk's dashboard, or when local and CI disagree | Read [VULNERABILITY.md](VULNERABILITY.md) for `security:dependencies`, `fail_on`, `include_dev`, licence issues, `snyk monitor` and local/CI parity |
| fixing a Terraform, Kubernetes, CloudFormation, Helm or ARM misconfiguration | Read [IAC.md](IAC.md) for what Snyk IaC evaluates, the gate and `.snyk` ignores |
| producing or retaining the software bill of materials, or when `security:sbom` fails on an empty SBOM | Read [SBOM.md](SBOM.md) for the formats, `allow_empty` and keeping the artifact as a compliance record |
| handling a Gitleaks finding or allowlisting a false positive          | Read [SECRETS.md](SECRETS.md) for the quick start, the allowlist format and what to do the moment a real secret is detected |
| adding a third-party script, widget or any external resource to a page | Read [CSP_EXCEPTIONS.md](CSP_EXCEPTIONS.md) first for the strict-default + per-path exception pattern and the schema |
| auditing CORS, HSTS or `nosniff` on a JSON API                        | Read [API_HEADERS.md](API_HEADERS.md) for the findings, the hard-fail split, knobs and commands |

| Check | Tool | Blocks on |
| ----- | ---- | --------- |
| `security:sast` | Snyk Code | findings at or above `security.sast.fail_on` (default high) in your own code |
| `security:dependencies` | Snyk Open Source | vulnerabilities and licence issues at or above `security.dependencies.fail_on` (default high) |
| `security:iac` | Snyk IaC | misconfigurations at or above `security.iac.fail_on` (default high) |
| `security:sbom` | Snyk SBOM | no SBOM could be produced (an empty one fails unless `security.sbom.allow_empty`) |
| `security:secrets` | Gitleaks | any secret, in files or git history |
| `security:api-headers` | stdlib Python | credentialed CORS wildcards, missing HSTS / `nosniff` |
| `hygiene:csp-exceptions` | stdlib Python | drift between the `headers.source` file and the exceptions doc |

Every check follows the same shape: run it locally with the `task`
target named after it (`task ss:security:sast`,
`task ss:hygiene:csp-exceptions`), read the report its page names under
`.ss/reports/`, get a PR comment on pull requests and a GitHub issue when a
blocking finding lands on `main`. Disable one by listing its workflow under
`workflows.disabled` in `.slopstopper.yml` (or by choosing a
[profile](../architecture/PROFILES.md) that drops it). `task ss:security:scan`
runs the four Snyk checks and secrets in sequence; `task ss:security:monitor`
registers the dependency graph with Snyk (the dependencies workflow runs it on
`main`).

The four Snyk checks share one contract. They read `SNYK_TOKEN` from the
environment (never from the config file), the organisation from `SNYK_ORG`
or `security.snyk.org`, and the regional API from `SNYK_API`. Analytics are
off on every invocation. The verdict is computed from the report the CLI
wrote, so the gate is one `fail_on` key per check and the report always
carries every finding; a scan that produced no readable report (no token,
Snyk Code switched off for the organisation, a crash) is exit 2, could not
run, never a pass. Accepted risks go in Snyk's `.snyk` policy file with a
reason and an expiry, which every product honours, rather than in a looser
gate.

## Static Application Security Testing (SAST)

Snyk Code analyses **your own source code** for injection, insecure
deserialisation, hardcoded credentials, path traversal and the rest of its
rule set, and writes SARIF. High blocks; medium and low are reported;
`security.sast.fail_on` moves that line. The same SARIF can be uploaded to
GitHub code scanning. Source files are uploaded to Snyk for the duration of
the scan.

## Dependency Vulnerability Scanning

Snyk Open Source resolves every manifest it finds (lock files, `package.json`,
`requirements.txt`, `go.mod`, …) and reports known vulnerabilities and, when
the organisation has a licence policy, licence issues. High and critical
block. Only the dependency graph leaves the runner.

## Infrastructure as Code

Snyk IaC evaluates Terraform, CloudFormation, Kubernetes manifests, Helm
charts and ARM templates against rules the CLI downloads, locally, and
reports nothing to Snyk. A repo with no IaC passes with a note.

## Software Bill of Materials

Snyk SBOM writes a CycloneDX (or SPDX) document for the repository's
dependencies on every PR and on `main`, uploaded as a workflow artifact.
The check fails when no SBOM could be produced, because an empty SBOM on a
repo that ships software is a compliance gap.

## Secrets Detection

Gitleaks scans all files **and full git history** for credentials, tokens
and private keys. Always blocking; the check never writes the credential to
disk. Allowlist known false positives in the file SECRETS.md describes.

## CSP Exceptions

A strict `default-src 'self'` Content Security Policy on every path, and a
**scoped, documented, SRI-pinned per-path exception** whenever one page
genuinely needs a third party, rather than a weaker site-wide policy. The
[`ss:hygiene:csp-exceptions`](../../Taskfile.ss.yml) check fails on drift
between the headers file `headers.source` names and the exceptions doc
routed above. It is inert until `headers.source` is set.

## API Headers & CORS

On a JSON API, CORS rather than CSP decides who may read a response.
`security:api-headers` audits that policy, plus HSTS and `nosniff`, and
splits findings into hard failures and advisory notes.
