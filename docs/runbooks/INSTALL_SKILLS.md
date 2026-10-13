# Install the SlopStopper Claude Code skills

The companion to [`install.sh`](../../install.sh). Where `install.sh` drops the whole quality suite into a repo, [`install-skill.sh`](../../install-skill.sh) targets just the Claude Code skill subset, which is useful for refreshing the playbooks without re-running the full installer.

Both scripts write skills at **project level**. They land in the adopter repo's `.claude/skills/` directory, so every contributor that clones the repo gets them automatically. Claude Code auto-discovers project-level skills the same way it does user-level ones.

After install, Claude Code auto-picks the right skill for any SlopStopper-related prompt:

| Skill | Triggered by prompts like… |
| ----- | -------------------------- |
| [`slopstopper-install`](../../.claude/skills/slopstopper-install/SKILL.md) | "add slopstopper to this repo", "install the slopstopper quality suite", "refresh slopstopper", "upgrade slopstopper", "pull in new slopstopper checks" |
| [`slopstopper-triage`](../../.claude/skills/slopstopper-triage/SKILL.md) | "fix this failing slopstopper check", "the complexity check is failing", "SAST exits 2 with no findings" |

`slopstopper-install` covers both first install and refresh. Its mode-detection branch checks for `.slopstopper.yml` + `.ss/.workflows-installed` and routes to the right subset of steps. There's no separate `slopstopper-update` skill; the previous version was folded into `slopstopper-install` because install.sh is idempotent and the two flows shared 80% of their steps.

## What you need

- **Claude Code**, from [claude.com/claude-code](https://claude.com/claude-code). Skills land at project level regardless of whether Claude Code is installed on the machine running the install; any contributor who has Claude Code will pick them up when they open the repo.
- **A tagged checkout of this repository**, because `install-skill.sh` copies each skill file from it. `install.sh` runs the same script against the checkout it is running from, so neither needs the network for this step.
- **Write access to the target repo**, since the scripts create `.claude/skills/` under the repo root.

## Install

Skills are installed as part of `install.sh` automatically, from the tagged checkout:

```bash
git clone --branch v0.1.0 https://github.com/dataGriff/slopstopper-snyk-safe.git
cd <your-repo>
bash ../slopstopper-snyk-safe/install.sh
```

Pass `--no-skills` (or set `SLOPSTOPPER_NO_SKILLS=1`) to skip the skill subset.

To refresh just the skills without re-running the whole installer, run the standalone script from the same checkout, inside the target repo:

```bash
cd <your-repo>
bash ../slopstopper-snyk-safe/install-skill.sh
```

Or, explicit target directory:

```bash
bash ../slopstopper-snyk-safe/install-skill.sh /path/to/repo
```

## What lands

Two directories, one per skill, written under the target repo root:

```
<repo>/.claude/skills/slopstopper-install/SKILL.md      # the playbook map, loaded on trigger
<repo>/.claude/skills/slopstopper-install/references/   # the long sections, read when a step needs them
<repo>/.claude/skills/slopstopper-triage/SKILL.md
<repo>/.claude/skills/slopstopper-triage/references/
```

Each skill's map file (SKILL.md) is short (about the ~2,000-token budget this repo puts on its own `AGENTS.md`) and links the `references/*.md` files that hold the full tables. Claude Code loads the map when the skill triggers and reads a reference only when the map points at it, so the skill costs one page of context until a step needs detail.

Nothing outside `<repo>/.claude/skills/slopstopper-*/` is touched. Per skill, the script copies the map, checks it looks like a Claude Code skill (frontmatter present), then copies every reference it links. Only linked files come across, so nothing else in the source tree ships. The result is staged next to the installed skill and then moved into place, so a failed copy leaves the installed copy as it was. A skill that fails is named at the end and the script exits non-zero; `install.sh` reports that as a warning and carries on.

`install-skill.sh` is the only implementation: `install.sh` runs it against the checkout `install.sh` itself is running from, so the skills always match the workflows and templates installed beside them, and the tag you checked out is the tag you get.

**Commit the resulting files** alongside the workflows that `install.sh` lands. The `.gitignore` block that `install.sh` appends already includes a carve-out for `.claude/skills/` so repos with a blanket `.claude/*` ignore don't silently shadow them.

## Why project level (and what happened to user level)

A previous version of this script wrote skills to `~/.claude/skills/slopstopper-*`. That made the skills available globally, but only on the installer's machine. Every other contributor cloning the repo would need to run the install themselves to get the playbooks. That was asymmetric with how the workflows landed (project-level, committed, shared on clone), and a frequent source of "the suite is set up but Claude doesn't know about it" confusion.

The new model treats skills like every other slopstopper artefact: shipped into the repo, committed, shared by every contributor on git clone. Two scripts write to one destination, so there is no more user/project drift.

## What happened to `slopstopper-update`

The previous skill set was a trio: `slopstopper-install`, `slopstopper-update`, `slopstopper-triage`. The install and update skills overlapped heavily, because install.sh is idempotent, the local-verify loop was identical and the "what just landed / what changed" inventory was the same surface. The genuinely-update-only content (re-applying customizations, diffing upstream for new knobs, spotting newly-shipped checks) has been folded into `slopstopper-install` as a "Refresh-only" section that the mode-detection branch routes to when `.slopstopper.yml` already exists. One skill now carries both flows, so nobody has to keep two near-identical playbooks in step.

The old `slopstopper-update` directory is auto-removed on every install or refresh (it's listed in `install-skill.sh`'s `OBSOLETE_SKILLS` array alongside the older single-skill `install-slopstopper` name).

## Migrating from the user-level install

If you previously ran `install-skill.sh` (older version) on this machine, you'll have stale copies at `~/.claude/skills/slopstopper-{install,update,triage}/SKILL.md`. Claude Code merges user-level and project-level skills, so the stale user-level copies will shadow the project-level ones until you remove them:

```bash
rm -rf ~/.claude/skills/slopstopper-install \
       ~/.claude/skills/slopstopper-update \
       ~/.claude/skills/slopstopper-triage
```

`install-skill.sh` (and so `install.sh`) detects this scenario and prints a one-line warning suggesting the cleanup. It doesn't execute the `rm`, because an installer should not silently delete another contributor's user-level state, but it tells you exactly which command to run.

## How the skills get used

Claude Code auto-discovers skills under `.claude/skills/` in the project root. Each skill's `description` field in the SKILL frontmatter is what Claude matches against incoming prompts:

- A prompt about installing or refreshing slopstopper → `slopstopper-install` (its mode-detection branch handles the rest).
- A "fix this failing check" prompt → `slopstopper-triage` (the diagnostic table).

Per-invocation context stays small because Claude only loads the one skill that matches. Each skill ends with a "When to hand off" pointer to the other so the agent can escalate without you having to remember the names.

## Refresh

Re-run either script any time. Each compares the staged directory against what's installed and only replaces it if content differs. It replaces the whole directory, so a reference file from an older skill layout does not linger.

## Uninstall

```bash
rm -rf .claude/skills/slopstopper-install .claude/skills/slopstopper-triage
```

Claude Code stops invoking the skills immediately on next prompt. No other state to clean up.

## When this runbook needs updating

- A skill is added to or removed from `install-skill.sh`'s `SKILLS` array → update the table at the top, the "What lands" file list, and the uninstall command.
- A skill is renamed → update all the above plus the migration sections.
- The migration cleanup mechanism changes → update the "What happened to slopstopper-update" and "Migrating from user-level" sections.
- The skills move back to user level (or a hybrid) → rewrite the "Why project level" section.

The change map (`docs/contributing/CHANGE_MAP.md`) cross-references this runbook for the relevant change classes.
