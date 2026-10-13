# Contributing to SlopStopper

> 📌 **This guide is for contributors to the SlopStopper project itself.**
> If you've installed SlopStopper into your own repo and want to customise
> or extend it there, the main [`README.md`](./README.md) and
> [`docs/`](./docs/) cover that. Read on if you want to push a change up to
> this repository.

Thanks for considering a contribution. The full contributor guide lives at:

→ [`docs/contributing/README.md`](./docs/contributing/README.md)

## Short version

- Branch from `main` and keep changes focused
- Use [Conventional Commits](https://www.conventionalcommits.org/):
  `<type>(<scope>): <description>` (types: `feat`, `fix`, `docs`, `style`,
  `test`, `chore`, `refactor`). Releases are git tags cut by hand from
  `main`; see [`docs/runbooks/RELEASE.md`](./docs/runbooks/RELEASE.md)
- Run `task --list` to see check tasks before pushing
- The Taskfile is the single source of truth: `task <name>` runs the same
  thing locally that CI runs
- Open a pull request; the SlopStopper checks will report back

## Quick checks before pushing

```bash
task contributing:test                   # CLI pytest suite
task ss:hygiene:test                     # hygiene suite (the pre-push hook)
task ss:security:scan                    # Snyk Code, dependencies, IaC, SBOM, secrets (needs SNYK_TOKEN)
```

The fast hygiene subset (`task ss:hygiene:test`) also runs automatically as a
**pre-push hook** once you've run `task contributing:setup` (it points
`core.hooksPath` at [`.githooks/`](./.githooks)). That gate catches the cheap,
deterministic issues before they reach CI; the security scan needs a Snyk
token, so it stays out of the hook and runs by hand or in CI. Bypass a single
push with `git push --no-verify`.

## Agents

If you're an AI agent working on this repo, read
[`AGENTS.md`](./AGENTS.md) for the canonical conventions and constraints.

## Code of conduct, support, escalation

See [`docs/support/`](./docs/support/).
