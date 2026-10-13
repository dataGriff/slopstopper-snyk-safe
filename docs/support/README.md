# Support

How to get help with this project.

## Support Flow

Before raising a support request, please work through the following steps in order. Most questions can be resolved without opening an issue.

```mermaid
flowchart TD
    A([I need help]) --> B{Have you asked\nan AI assistant?}
    B -- No --> C[Ask your AI assistant\nabout the codebase]
    C --> D{Resolved?}
    D -- Yes --> Z([Done ✅])
    D -- No --> E{Is this specific\nto this repo?}
    B -- Yes --> E
    E -- Yes --> F[Open a Support Issue\nin this repository]
    F --> G{Resolved?}
    G -- Yes --> Z
    G -- No --> H{Cross-repo or\ncross-team?}
    E -- No --> H
    H -- Yes --> I[Start a GitHub Discussion]
    I --> Z
```

## Step 1. Ask AI First

Use an AI assistant to ask questions about the codebase before raising an issue. This is the fastest path to an answer and keeps the issue tracker focused on genuine bugs or gaps. The repo ships two Claude Code skills for exactly this: `slopstopper-install` for installing or refreshing the suite and `slopstopper-triage` for a failing check.

**Examples of things AI can help with:**

- "Why does `security:sast` exit 2 with no findings?"
- "Where is the configuration for X?"
- "What does this workflow file do?"
- "How do I move the `cli.ref` pin to a new tag?"

## Step 2. Raise a Support Issue (repo-specific problems)

If AI cannot resolve your question and the problem is **specific to this repository**, open a support issue using the template provided:

👉 [Open a Support Issue](https://github.com/dataGriff/slopstopper-snyk-safe/issues/new?template=support-request.yml)

Use this for:

- Unexpected behaviour within this repo
- Missing documentation that is clearly scoped to this project
- Configuration questions specific to this suite

## Step 3. Start a Discussion (cross-repo or cross-team)

If the problem spans multiple repositories, teams, or is architectural in nature, start a **GitHub Discussion** rather than an issue:

👉 [Start a Discussion](https://github.com/dataGriff/slopstopper-snyk-safe/discussions/new)

Use this for:

- Questions that touch multiple repos or teams
- Broad architectural or process questions
- RFCs and proposals that need community input

## Choosing the Right Channel

| Situation | Channel |
|-----------|---------|
| Quick question about the codebase | Ask an AI assistant |
| Bug or gap scoped to this repo | [Support Issue](https://github.com/dataGriff/slopstopper-snyk-safe/issues/new?template=support-request.yml) |
| Cross-repo or cross-team concern | [Discussion](https://github.com/dataGriff/slopstopper-snyk-safe/discussions/new) |
| Security vulnerability | Private vulnerability reporting, per [`SECURITY.md`](../../SECURITY.md); never a public issue |
| A Snyk credential or tenant question | [Snyk setup](../security/SNYK.md) |
