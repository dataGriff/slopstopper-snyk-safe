# Security Policy

## Supported versions

SlopStopper is released as git tags of this repository. Adopters pin an
exact tag under `cli:` in `.slopstopper.yml` and move the pin
deliberately. Only the latest tag receives security fixes; the fix for an
issue is a new tag, never a rewritten one.

| Version | Supported |
| ------- | --------- |
| The latest `vX.Y.Z` tag | ✅ |
| `main` | ✅ for reports; fixes ship as the next tag |
| Older tags | Move `cli.ref` to the latest tag and re-run `install.sh` |

## Reporting a vulnerability

**Please do not open a public issue for security bugs.**

Use GitHub's private vulnerability reporting on this repository
(Security → Advisories → Report a vulnerability, at
<https://github.com/dataGriff/slopstopper-snyk-safe/security/advisories/new>).
This routes the report to the maintainers and lets us coordinate
disclosure with you.

What to include:

- A clear description of the issue and the impact you can demonstrate
- Reproduction steps (a minimal example, ideally against a fork)
- The tag, commit SHA or workflow file affected
- Any suggested mitigation

## What to expect

- **Acknowledgement** within 5 working days.
- **Triage update** within 10 working days, confirming whether it's
  in scope, an estimated severity, and a fix timeline.
- **Coordinated disclosure** once a fix has shipped as a tag. We will
  credit you in the advisory unless you ask us not to.

## Scope

In scope:

- The **slopstopper-cli** Python package under `cli/slopstopper/`:
  every check (the Snyk wrappers in particular, since they handle a
  token and decide a verdict), the emit/discovery/templates/serve
  subcommands, and the bundled `data/` (Playwright specs, lighthouserc,
  server.js).
- The **GitHub Actions workflows** under `.github/workflows/ss-*.yml`
  and the composite actions under `.github/actions/` that adopter repos
  copy from: permissions, pins, how secrets and URLs reach the shell.
- The **adopter installer** (`install.sh`, `install-skill.sh`) and the
  `Taskfile.ss.yml` shim adopters get.
- Any docs or skill that, if followed, would lead an adopter into an
  insecure configuration.

Out of scope:

- Vulnerabilities in upstream tools (the Snyk CLI, Gitleaks, Lizard,
  axe-core, Lighthouse, Playwright). Please report those upstream.
- Findings produced by the suite running against adopter code. Those
  belong to the adopter.
- The Snyk service itself. What the suite sends to Snyk, and when, is
  described in [`docs/security/SNYK.md`](./docs/security/SNYK.md).

## Related docs

- [`docs/security/README.md`](./docs/security/README.md): an overview of
  what each security check does and how to extend it.
- [`docs/security/SNYK.md`](./docs/security/SNYK.md): the service
  account, token rotation, data flow and egress allowlist.
