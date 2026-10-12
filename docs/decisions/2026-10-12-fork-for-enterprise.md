# Fork for enterprise: Snyk replaces Semgrep, Trivy and ZAP; site and agentic workflows removed

## Metadata

- Date: 2026-10-12
- Status: accepted
- Owner: dataGriff
- Related area: security, architecture, release

## Decision Summary

Fork hungovercoders/slopstopper at v0.21.0 (MIT) into an enterprise-ready
suite: the four security scans run through the Snyk CLI under one
service-account token, the CLI ships as git tags that adopters pin rather
than a PyPI package, and everything an adopter does not install (the
reference site, its Cloudflare deploy, the demos, the agentic doc-updater,
release automation) is removed.

## Context

The upstream suite was built to be adopted by a public repository and
proven on a public site. An enterprise rollout has different constraints:
one scanner vendor with a support contract and an organisation-level
policy, a machine identity for CI rather than personal tokens, regional
data residency, an egress allowlist on the runner, no pipe-to-bash
installs, no unpinned package installs, and a reviewable answer to "which
commit of the tooling ran on this PR". Upstream's mix of Semgrep (LGPL,
registry-fetched rules), Trivy (a local database that goes stale) and ZAP
(a Docker container against a running site) met none of those cleanly,
and its site, deploy and agentic workflows were weight an adopter never
installs.

## Options Considered

- Option A: keep upstream as-is and document the enterprise settings around it. Rejected: three scanners, three credential models, local/CI drift in Trivy, and a curl-piped installer are each an individual exception a security review would raise.
- Option B: fork, replace the scanners with Snyk, drop what adopters never install, pin the CLI as a git tag. Chosen.
- Option C: contribute Snyk support upstream as an alternative backend. Possible later; it does not remove the site, the deploy or the release automation from the repository an enterprise has to review and mirror.

## Decision and Rationale

Option B. Snyk Code, Open Source, IaC and SBOM share one token, one
organisation, one API host (regional tenants supported) and one policy file
for accepted risks, so the enterprise setup is three settings and one
egress entry. The verdict stays in the CLI (one `fail_on` per check,
"no report" is exit 2), so the gate is reviewable in this repository
rather than in Snyk's flags. Pinning the CLI as a tag means CI runs a
commit someone reviewed, and a mirror or an internal wheel keeps the same
guarantee off the public internet. ZAP goes because it needs Docker and a
running site, which the enterprise profile (`library`) never has; its CSP
coupling is replaced by the plain exceptions doc.

## Impact

- Positive outcomes: one vendor and one credential for all security scanning; exact scanner pins in `mise.toml`; least-privilege, SHA-pinned, timed-out workflows with no shell interpolation; an SBOM on every commit; a repository that contains only what adopters install.
- Tradeoffs / risks: Snyk Code uploads source to Snyk (documented per product in `docs/security/SNYK.md`; disable that one workflow where it is unacceptable); a Snyk account is now a prerequisite; no DAST until a replacement that fits the enterprise shape is chosen; releases are manual.
- Follow-up actions: adopt in the first internal repositories under `profile: library` and `api`; decide on a DAST replacement if a deployed surface needs one; consider contributing the `_snyk.py` backend upstream.

## Review Trigger (Optional)

Revisit if Snyk's pricing or terms change, if an adopter needs DAST, or if
upstream grows a pluggable scanner backend that would let this fork
shrink to a configuration.
