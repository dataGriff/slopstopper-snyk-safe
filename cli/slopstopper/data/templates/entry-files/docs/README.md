# Docs index

The fallback routing table (a README so it renders in place when you
browse `docs/`). `AGENTS.md` carries what most tasks need and routes the
common cases directly; come here when your task is not covered there.
Read only the doc whose trigger matches; each row says when.

## Routes

| When you are…                     | Do this                                                     |
| --------------------------------- | ----------------------------------------------------------- |
| doing <some kind of task>         | Read `<topic>.md` first for <what it holds> (make it a link once the doc exists) |

Every doc under `docs/` needs a row here (or in a README above it) in the
form **trigger → "read" → file → what it holds**. The
`ss:hygiene:docs-structure` check fails on a doc nothing routes to and on
a row that only says "see".
