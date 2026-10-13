# Docs index

The fallback routing table (a README so it renders in place when you
browse `docs/`). `AGENTS.md` carries what most tasks need
and routes the common cases directly; come here when your task is not
covered there. Read only the doc whose trigger matches. Each row says
when, and each category README routes onward to its own detail.

## Routes

| When you are…                                                                 | Do this                                                                                   |
| ----------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------- |
| adopting slopstopper in another repo, or asking what the suite installs and needs | Read [runbooks/INSTALL.md](runbooks/INSTALL.md) first for prerequisites, the tagged-checkout install, what lands, what each check needs, the Snyk settings, and how to configure and update |
| changing anything that ships to adopters (a check, workflow, skill, config key, template) | Read [contributing/CHANGE_MAP.md](contributing/CHANGE_MAP.md) before you start. It lists every surface each kind of change must touch |
| contributing code: commands, exit codes, commit conventions, pitfalls          | Read [contributing/README.md](contributing/README.md) first                               |
| asking how the repo is laid out, why mise + Task, profiles, or PR feedback      | Read [architecture/README.md](architecture/README.md) for the layout, C4, toolchain, and routes to profiles and PR feedback |
| fixing a hygiene check (complexity, docs-*, entry-files, CSP drift, OpenAPI)   | Read [hygiene/README.md](hygiene/README.md) for what each check enforces, its knobs and how to read its report |
| fixing a security check (SAST, dependencies, IaC, SBOM, secrets, headers, CSP), or setting up Snyk for CI | Read [security/README.md](security/README.md) for the shared Snyk contract and the routes to each scanner's guide |
| fixing a reliability check (smoke, accessibility, CWV, SEO, links, llms.txt, robots, sitemap, API health/latency) | Read [reliability/README.md](reliability/README.md) for the env-var contract and the routes to each audit |
| cutting a release, moving an adopter's CLI pin, or installing the Claude Code skills | Read [runbooks/README.md](runbooks/README.md) for the operational procedures                 |
| recording or revisiting a significant decision, including why this fork exists | Read [decisions/README.md](decisions/README.md) for the log, the fork decision and the note template |
| asking how to get help or escalate                                             | Read [support/README.md](support/README.md)                                                |

## The model

`AGENTS.md` first: the entry file carries the rules, commands and layout
most tasks need within ~2k tokens (it is prompt-cached, so inlining is
cheap; a routing hop costs a tool turn on every task that takes it). The
overflow lives here, one concern per doc, behind an explicit route of
the form **trigger → "read" → file → what it holds**. A doc nothing
routes to is invisible; a "see also" is skipped. Two checks keep it honest.
[`ss:hygiene:entry-files`](hygiene/README.md) budgets the entry files and
this map and insists on the route form; [`ss:hygiene:docs-structure`](hygiene/README.md)
fails on an unrouted doc, a soft route here, a route deeper than
AGENTS.md → map → directory README → doc, or a topic doc past 300 lines.
