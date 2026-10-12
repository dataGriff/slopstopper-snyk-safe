#!/usr/bin/env bash
# install-skill.sh: install/refresh the SlopStopper Claude Code skills
# into the target repo.
#
# The companion to install.sh: install.sh installs the whole quality suite
# into a repo; this script just covers the skill subset, so you can refresh
# just the playbooks without touching workflows, the Taskfile or the CLI.
#
# Skills are installed at *project level*, into the adopter repo's
# ./.claude/skills/ directory, so every contributor that clones the repo
# gets them automatically (Claude Code auto-discovers project-level skills
# the same way it does user-level ones). Commit the resulting files
# alongside the workflows.
#
# It copies from a local checkout only. Nothing is downloaded: run it from
# the same tagged checkout the suite was installed from, from inside the
# target repo:
#
#   git clone --branch v0.1.0 https://github.com/dataGriff/slopstopper-snyk-safe.git
#   bash slopstopper-snyk-safe/install-skill.sh
#
# Or, explicit target directory:
#   bash /path/to/slopstopper-snyk-safe/install-skill.sh /path/to/repo
#
# What lands:
#   <target>/.claude/skills/slopstopper-install/   # install + refresh (SKILL.md + references/)
#   <target>/.claude/skills/slopstopper-triage/    # diagnose a failing check
#
# Migration:
#   - If a previous version installed a single `install-slopstopper` skill,
#     that obsolete directory is removed from the target repo.
#   - The `slopstopper-update` skill has been folded into `slopstopper-install`
#     (which now covers both first-install and refresh). Any stale
#     `slopstopper-update/` directory in the target is removed.
#   - This script no longer writes to ~/.claude/skills/. If a previous
#     version installed copies there, they'll shadow the project-level ones
#     when Claude Code merges skill paths. Remove them manually with:
#       rm -rf ~/.claude/skills/slopstopper-install \
#              ~/.claude/skills/slopstopper-update \
#              ~/.claude/skills/slopstopper-triage
#
# What this affects:
#   Nothing outside <target>/.claude/skills/slopstopper-*/. Re-running
#   replaces each skill directory only if the source content differs,
#   so it is safe to refresh on a schedule.

set -euo pipefail

# Resolve the directory that contains this script. Run from a checkout, that
# is the skill source; install.sh passes its own checkout explicitly as
# SLOPSTOPPER_REPO_RAW=file:///path/to/checkout (with SLOPSTOPPER_SKILLS_QUIET=1
# to drop the banner and closing guidance). Only file:// sources are
# accepted: this script never fetches over the network.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd)" || true
REPO_RAW="${SLOPSTOPPER_REPO_RAW:-}"
if [ -z "${REPO_RAW}" ] && [ -n "${SCRIPT_DIR}" ] && [ -d "${SCRIPT_DIR}/.claude/skills" ]; then
  REPO_RAW="file://${SCRIPT_DIR}"
fi
SKILLS=(
  "slopstopper-install"
  "slopstopper-triage"
)
OBSOLETE_SKILLS=(
  "install-slopstopper"
  "slopstopper-update"
)

# ── argument parsing ─────────────────────────────────────────────────────────

TARGET_DIR="${1:-$(pwd)}"

# ── helpers ──────────────────────────────────────────────────────────────────

info()    { echo "  ℹ  $*"; }
success() { echo "  ✅ $*"; }
warn()    { echo "  ⚠️  $*"; }
error()   { echo "  ❌ $*" >&2; exit 1; }

sep() { echo "────────────────────────────────────────────────────────────"; }

# ── preflight ────────────────────────────────────────────────────────────────

case "${REPO_RAW}" in
  file://*) ;;
  "")
    echo "  ❌ install-skill.sh must run from a checkout of the slopstopper repository: no skill source found next to it." >&2
    echo "     Nothing is downloaded at run time. From inside your repo:" >&2
    echo "" >&2
    echo "       git clone --branch v0.1.0 https://github.com/dataGriff/slopstopper-snyk-safe.git" >&2
    echo "       bash slopstopper-snyk-safe/install-skill.sh" >&2
    echo "" >&2
    echo "     (or set SLOPSTOPPER_REPO_RAW=file:///path/to/checkout)" >&2
    exit 1
    ;;
  *)
    error "SLOPSTOPPER_REPO_RAW must be a local checkout (file:///path/to/checkout); this script never fetches skills over the network. Got: ${REPO_RAW}"
    ;;
esac

SOURCE_ROOT="${REPO_RAW#file://}"
[ -d "${SOURCE_ROOT}/.claude/skills" ] || error "No .claude/skills/ under ${SOURCE_ROOT}. Point SLOPSTOPPER_REPO_RAW at a slopstopper checkout."

if [ ! -d "${TARGET_DIR}" ]; then
  error "Target directory does not exist: ${TARGET_DIR}"
fi

if [ -z "${SLOPSTOPPER_SKILLS_QUIET:-}" ]; then
  sep
  echo "  🧠  SlopStopper: installing the Claude Code skills (project level)"
  echo "  Source : ${SOURCE_ROOT}/.claude/skills/<skill>/  (SKILL.md + references/)"
  echo "  Target : ${TARGET_DIR}/.claude/skills/<skill>/"
  sep
fi

# ── install each skill ──────────────────────────────────────────────────────

SKILLS_SRC="${SOURCE_ROOT}/.claude/skills"
SKILLS_DST="${TARGET_DIR}/.claude/skills"
STAGE_ROOT="$(mktemp -d)"
trap 'rm -rf "${STAGE_ROOT}"' EXIT

fetch() {
  cp "$1" "$2"
}

# A skill is a directory: SKILL.md plus the references/*.md files it links
# (the long tables live there and are read on demand, so the skill costs one
# page of context until a step needs detail). Copy SKILL.md, validate it,
# then copy every references/<name>.md it mentions. Only linked files, so
# a stray draft or editor file in the source tree never ships.
#
# The result is staged under a directory made with mkdir (so it carries the
# umask's mode, not mktemp's 0700), copied next to the destination, and only
# then moved into place: a failed copy leaves the installed skill
# untouched. Every step checks its own status, because callers run this on
# the left of `||`, where `set -e` does not apply.
install_skill_dir() {
  local skill="$1"
  local dest_dir="${SKILLS_DST}/${skill}"
  local stage="${STAGE_ROOT}/${skill}"
  local ref

  mkdir -p "${stage}/references" || return 1
  if ! fetch "${SKILLS_SRC}/${skill}/SKILL.md" "${stage}/SKILL.md" 2>/dev/null; then
    warn "${skill}: SKILL.md not found under ${SKILLS_SRC}/${skill}/"
    return 1
  fi
  if ! head -n 1 "${stage}/SKILL.md" | grep -q "^---$"; then
    warn "${skill}: SKILL.md does not look like a Claude Code skill (no frontmatter)"
    return 1
  fi
  for ref in $(grep -o 'references/[A-Za-z0-9_.-]*\.md' "${stage}/SKILL.md" | sort -u); do
    if ! fetch "${SKILLS_SRC}/${skill}/${ref}" "${stage}/${ref}" 2>/dev/null; then
      warn "${skill}: linked ${ref} not found under ${SKILLS_SRC}/${skill}/"
      return 1
    fi
  done
  rmdir "${stage}/references" 2>/dev/null || true  # a skill with no references

  if [ -d "${dest_dir}" ] && diff -rq "${stage}" "${dest_dir}" >/dev/null 2>&1; then
    info "${skill}: already up to date"
    return 0
  fi
  local had_it=false
  [ -d "${dest_dir}" ] && had_it=true
  local incoming="${dest_dir}.incoming.$$" outgoing="${dest_dir}.outgoing.$$"
  mkdir -p "${SKILLS_DST}" || return 1
  if ! cp -R "${stage}" "${incoming}"; then
    rm -rf "${incoming}"
    warn "${skill}: could not copy into ${SKILLS_DST}, so the installed copy was left as it was"
    return 1
  fi
  if [ "${had_it}" = true ]; then
    mv "${dest_dir}" "${outgoing}" || { rm -rf "${incoming}"; return 1; }
  fi
  if ! mv "${incoming}" "${dest_dir}"; then
    [ "${had_it}" = true ] && mv "${outgoing}" "${dest_dir}"
    rm -rf "${incoming}"
    return 1
  fi
  rm -rf "${outgoing}"
  if [ "${had_it}" = true ]; then success "${skill}: refreshed"; else success "${skill}: installed"; fi
}

FAILED_SKILLS=()
for skill in "${SKILLS[@]}"; do
  install_skill_dir "${skill}" || FAILED_SKILLS+=("${skill}")
done

# ── clean up obsolete skill directories ──────────────────────────────────────

# Adopters who ran an earlier version of this script have obsolete skills
# at superseded paths. Remove them so their <target>/.claude/skills/ only
# contains the current set. Safe because we never delete anything we didn't
# put there ourselves:
#   - `install-slopstopper` was the single-skill name; replaced by the
#     `slopstopper-install` trio member.
#   - `slopstopper-update` was a separate refresh skill; its content has
#     been folded into `slopstopper-install` (covers both verbs).
for obsolete in "${OBSOLETE_SKILLS[@]}"; do
  obsolete_dir="${TARGET_DIR}/.claude/skills/${obsolete}"
  if [ -d "${obsolete_dir}" ]; then
    rm -rf "${obsolete_dir}"
    info "Removed obsolete skill: ${obsolete}"
  fi
done

# Heads-up: if the adopter ran an old version of install-skill.sh on this
# machine, they likely have stale copies of the same skills at user level
# (~/.claude/skills/slopstopper-*). Those will shadow the project-level
# copies when Claude Code merges skill paths. We don't delete user-level
# state on someone's behalf, but we point at it so they can clean up
# explicitly.
if [ -d "${HOME}/.claude/skills/slopstopper-install" ] \
  || [ -d "${HOME}/.claude/skills/slopstopper-update" ] \
  || [ -d "${HOME}/.claude/skills/slopstopper-triage" ]; then
  warn "Stale user-level skills detected at ~/.claude/skills/slopstopper-*."
  info "They'll shadow the project-level copies you just installed. Clean up with:"
  info "  rm -rf ~/.claude/skills/slopstopper-install \\"
  info "         ~/.claude/skills/slopstopper-update \\"
  info "         ~/.claude/skills/slopstopper-triage"
fi

# ── post-install guidance ────────────────────────────────────────────────────

if [ "${#FAILED_SKILLS[@]}" -gt 0 ]; then
  echo "  ❌ Not installed: ${FAILED_SKILLS[*]} (see the warnings above). Any installed copy was left as it was." >&2
  exit 1
fi

[ -n "${SLOPSTOPPER_SKILLS_QUIET:-}" ] && exit 0

sep
echo ""
echo "  🎉 Skills installed at project level!"
echo ""
echo "  Claude Code auto-discovers the skills under .claude/skills/ for any"
echo "  contributor working in this repo. Commit them alongside the workflows."
echo ""
echo "  Which skill triggers depends on what the prompt asks:"
echo ""
echo "    • \"install / refresh / upgrade slopstopper\" → slopstopper-install"
echo "    • \"fix this failing slopstopper check\"      → slopstopper-triage"
echo ""
echo "  To install the full SlopStopper suite into a fresh repo, from the"
echo "  same checkout:"
echo "    cd <your-repo>"
echo "    bash ${SOURCE_ROOT}/install.sh"
echo ""
echo "  Re-running this script from a newer tagged checkout refreshes the"
echo "  skills to that version."
echo ""
sep
