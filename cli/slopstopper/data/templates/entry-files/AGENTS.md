# Agent guide

Open standard for agents, AI assistants and automation tools working in
this repo (conformant with [agents.md](https://agents.md)). `CLAUDE.md`
is `@AGENTS.md`. This file carries what most tasks need; the routes at
the end say when to open anything else.

## Ground rules

1. **Reuse the task runner.** Run `task --list` before writing a script or
   one-off command; if a task already does it, use the task.
2. **Validate before committing.** `task ss:hygiene:test` runs the static
   slopstopper checks, the same ones CI runs.
3. **Budgets are the feature.** When a docs check fails, move content
   deeper (into `docs/`); never raise the budget.

## Commands

| Command                 | Does                                              |
| ----------------------- | ------------------------------------------------- |
| `task --list`           | every runnable task                               |
| `task ss:hygiene:test`  | docs-size, docs-structure, docs-accuracy, entry-files, complexity |
| `task ss:security:scan` | SAST, secrets, dependency scan                    |

## Layout

```
AGENTS.md        this file (CLAUDE.md is @AGENTS.md)
docs/            topic docs, routed from here and from docs/README.md
.slopstopper.yml slopstopper config: thresholds, URLs, profile
Taskfile.yml     every runnable command (`task --list`)
```

## Conventions

- Add the coding conventions, naming patterns and non-obvious rules most
  changes touch here. Keep each to a line; the reasoning goes in a doc.

## Routes: read before you act

| When you are…                        | Do this                                                              |
| ------------------------------------ | -------------------------------------------------------------------- |
| fixing a failing slopstopper check   | Read `.ss/reports/<check>/` first. The report names the finding and the fix |
| doing any task not covered above     | Read [docs/README.md](./docs/README.md) before you start; do not guess a convention |

The `ss:hygiene:entry-files` check keeps this file under its token budget
and every route above explicit, so don't remove either.
