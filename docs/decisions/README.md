# Decisions

This directory records significant decisions and their rationale for the project.

## How to Use

1. Add a row to the [Decisions Log](#decisions-log) below for each significant decision.
2. If extra context is needed, create a supporting note using the [Decision Template](#decision-template) as a guide.
3. Name supporting notes as `<date>-<slug>.md`.

## Tasks

```bash
task decisions:validate            # Validate the decisions log
task decisions:new SLUG=<name>     # Create a new decision note from template
```

---

# Decisions Log

Running log of significant decisions for this project.

## How to Use

- Add one row per significant decision.
- Keep entries concise and clear.
- Use supporting notes only when needed: `<date>-<slug>.md`.

## Decisions

| Date | Decision | Status | Rationale | Notes |
| ---- | -------- | ------ | --------- | ----- |
| TBD | Initialize decisions framework | accepted | Establish a lightweight, format-agnostic decision log as the source of truth for significant decisions. | none |
| 2026-10-12 | Fork for enterprise: Snyk replaces Semgrep/Trivy/ZAP; site and agentic workflows removed | accepted | One commercially supported scanner family with an org-level policy, a service-account token and regional tenancy; the CLI pinned as a git tag so every runner executes a reviewed commit; nothing in the repo that an adopter does not install. | [2026-10-12-fork-for-enterprise.md](2026-10-12-fork-for-enterprise.md) |

## Status Values

Use one of: `proposed`, `accepted`, `superseded`, `rejected`.

## Routes

| When you are…                                                        | Do this                                                                   |
| -------------------------------------------------------------------- | ------------------------------------------------------------------------- |
| asking why this fork exists, what it dropped from upstream, or whether to add a removed piece back | Read [2026-10-12-fork-for-enterprise.md](2026-10-12-fork-for-enterprise.md) for the options considered, the trade-offs and the review trigger |

---

# Decision Template

Use this template for significant decisions when additional context beyond the decisions log is needed.

## Metadata

- Date: YYYY-MM-DD
- Status: proposed | accepted | superseded | rejected
- Owner: Team or person
- Related area: (architecture, security, reliability, deployment, etc.)

## Decision Summary

One or two sentences describing the decision.

## Context

What problem, constraint, or trigger led to this decision?

## Options Considered

- Option A:
- Option B:
- Option C (optional):

## Decision and Rationale

What was chosen and why this option was selected.

## Impact

- Positive outcomes:
- Tradeoffs / risks:
- Follow-up actions:

## Review Trigger (Optional)

When should this decision be revisited?
