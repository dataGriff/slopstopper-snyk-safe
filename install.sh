#!/usr/bin/env bash
# install.sh: installs the SlopStopper tooling into a target repository.
#
# Run it from a checkout of this repository, pinned to the release you
# want, from inside the target repo:
#
#   git clone --branch v0.1.0 https://github.com/dataGriff/slopstopper-snyk-safe.git
#   bash slopstopper-snyk-safe/install.sh
#
# or name the target explicitly:
#
#   bash /path/to/slopstopper-snyk-safe/install.sh /path/to/your-repo
#
# Nothing is fetched at run time. Every file comes from the checkout the
# script runs from; the CLI is installed by pipx from the same repository
# and ref (recorded as `cli.source` / `cli.ref` in .slopstopper.yml, which
# the ss-setup composite action reads in CI); the scanners are exact pins
# in mise.toml that `mise install` resolves. There is no pipe-to-bash
# form, no "latest" lookup and no package index: the ref you clone is the ref you
# run, locally and in CI.
#
# Layout installed (all SlopStopper-owned files are under the `ss` namespace):
#
#   .slopstopper.yml            # seeded from templates/slopstopper.yml.starter
#                               #   on first run, never overwritten; the
#                               #   `cli:` block (source + ref) is written on
#                               #   every run from the checkout's ref
#   mise.toml                   # task, node, snyk and gitleaks pins ([tools]);
#                               #   `mise install` installs them
#   Taskfile.yml                # thin root; created on fresh installs, left
#                               #   alone if you already have one (instructions
#                               #   printed in that case)
#   Taskfile.ss.yml             # all SlopStopper task shims; always
#                               #   refreshed on re-run so updates flow through
#   .ss/.workflows-installed    # manifest of installed workflows (commit this)
#   .github/actions/ss-*/       # composite steps the workflows share (refreshed every run)
#   .ss/.installed-from         # slopstopper commit this install came from (commit this)
#   .ss/reports/                # SlopStopper-owned scan/report output dirs
#                               # (the Playwright specs and config,
#                               # lighthouserc dev/prod and server.js are
#                               # CLI-managed and live in the slopstopper-cli
#                               # package. To customise, `slopstopper templates
#                               # eject <name>` writes the file into .ss/ and
#                               # the CLI's templates resolver prefers it.)
#   .github/workflows/ss-*      # all SlopStopper workflows are ss- prefixed
#                               #   so they group together in the Actions UI
#                               #   and cannot clash with your existing workflows
#   .githooks/pre-push          # runs the fast hygiene checks before every push
#                               #   (core.hooksPath → .githooks; skipped if you
#                               #   already run husky/lefthook/pre-commit)
#
# Workflow set ships in two conceptual layers:
#   1. Static analysis: works on any code (Snyk Code SAST, Gitleaks secrets,
#      Snyk Open Source dependencies, Snyk IaC, Snyk SBOM, complexity, doc checks)
#   2. Web-app / API dynamic: needs a URL (Smoke, E2E, Accessibility, CWV, SEO,
#      Broken Links, llms.txt, robots.txt, sitemap, API health/latency/headers,
#      OpenAPI drift)
# Deploy is intentionally not a layer: this suite audits what you deploy and
# never deploys it. If you don't use one of the layers, delete its workflows
# from .github/workflows/ (or set a `profile:`). Re-running this installer
# respects that deletion (it tracks what it installed in .ss/.workflows-installed).

set -euo pipefail

# Where the CLI is installed from, unless --cli-source names a mirror. This
# must match the repository this script ships in: the ref recorded next to
# it is a tag or commit of this repository.
DEFAULT_CLI_SOURCE="https://github.com/dataGriff/slopstopper-snyk-safe.git"

# Toolchain pins written into the adopter's mise.toml. One copy here and one
# in this repo's own mise.toml, which the CLI tests keep in step; bump both
# in a commit that says why. Every tool is pinned so a runner never resolves
# "latest" on its own.
TASK_VERSION="3"
DEFAULT_NODE_VERSION="20"
SNYK_VERSION="1.1308.0"
GITLEAKS_VERSION="8.30.1"

# ── argument parsing ─────────────────────────────────────────────────────────
#
# Default mode ships Task-flavour workflows. Every check runs as
# `task ss:<name>`, which is the canonical interface for humans, agents and
# CI alike. Adopters who'd rather skip Task in CI pass `--no-task` (or set
# SLOPSTOPPER_NO_TASK=1) and the workflows are post-processed at install
# time to call slopstopper-cli directly. See docs/architecture/ for the
# design rationale.

USE_TASK=true
if [ -n "${SLOPSTOPPER_NO_TASK:-}" ]; then
  USE_TASK=false
fi

INSTALL_SKILLS=true
if [ -n "${SLOPSTOPPER_NO_SKILLS:-}" ]; then
  INSTALL_SKILLS=false
fi

INSTALL_HOOKS=true
if [ -n "${SLOPSTOPPER_NO_HOOKS:-}" ]; then
  INSTALL_HOOKS=false
fi

# Project-shape profile. A profile is a preset for `workflows.disabled`
# that switches off the checks a repo of that shape has no use for (an API
# has no Core Web Vitals; a library has no URL at all). The flag writes
# `profile:` into .slopstopper.yml so the choice survives re-runs and shows
# up in a diff, then the workflow set follows from the config, for the same
# reason as workflows.disabled below. Empty = leave the config alone
# (an unset `profile:` means `ui`, i.e. today's full suite).
PROFILE_FLAG="${SLOPSTOPPER_PROFILE:-}"

# slopstopper-cli pin. The CLI is installed from a git ref of this
# repository, never from a package index. The ref defaults to the ref this
# checkout is at (a tag when HEAD is tagged, else the full commit SHA), so
# the CLI always matches the workflows and shims copied from the same tree.
# --cli-ref overrides it; --cli-source points at an internal mirror.
CLI_REF_FLAG="${SLOPSTOPPER_CLI_REF:-}"
CLI_SOURCE="${SLOPSTOPPER_CLI_SOURCE:-$DEFAULT_CLI_SOURCE}"

POSITIONAL=()
while [ "$#" -gt 0 ]; do
  case "$1" in
    --no-task)
      USE_TASK=false
      shift
      ;;
    --no-skills)
      INSTALL_SKILLS=false
      shift
      ;;
    --no-hooks)
      INSTALL_HOOKS=false
      shift
      ;;
    --profile)
      PROFILE_FLAG="${2:-}"
      [ -n "$PROFILE_FLAG" ] || { echo "  ❌ --profile requires a name (e.g. --profile api)" >&2; exit 1; }
      shift 2
      ;;
    --profile=*)
      PROFILE_FLAG="${1#*=}"
      [ -n "$PROFILE_FLAG" ] || { echo "  ❌ --profile requires a name (e.g. --profile=api)" >&2; exit 1; }
      shift
      ;;
    --cli-ref)
      CLI_REF_FLAG="${2:-}"
      [ -n "$CLI_REF_FLAG" ] || { echo "  ❌ --cli-ref requires a git ref (e.g. --cli-ref v0.1.0)" >&2; exit 1; }
      shift 2
      ;;
    --cli-ref=*)
      CLI_REF_FLAG="${1#*=}"
      [ -n "$CLI_REF_FLAG" ] || { echo "  ❌ --cli-ref requires a git ref (e.g. --cli-ref=v0.1.0)" >&2; exit 1; }
      shift
      ;;
    --cli-source)
      CLI_SOURCE="${2:-}"
      [ -n "$CLI_SOURCE" ] || { echo "  ❌ --cli-source requires a git URL (e.g. --cli-source https://git.corp/mirrors/slopstopper-snyk-safe.git)" >&2; exit 1; }
      shift 2
      ;;
    --cli-source=*)
      CLI_SOURCE="${1#*=}"
      [ -n "$CLI_SOURCE" ] || { echo "  ❌ --cli-source requires a git URL" >&2; exit 1; }
      shift
      ;;
    --help|-h)
      cat <<'USAGE'
install.sh: install the SlopStopper quality suite into a target repo.

Run it from a checkout of the slopstopper repository, at the tag you want:

  git clone --branch v0.1.0 https://github.com/dataGriff/slopstopper-snyk-safe.git
  bash slopstopper-snyk-safe/install.sh [TARGET_DIR]   # TARGET_DIR defaults to the current directory

Usage:
  bash install.sh [TARGET_DIR]                   # Task-driven workflows (default)
  bash install.sh --no-task [TARGET_DIR]         # CLI-driven workflows (no Task install)
  bash install.sh --no-skills [TARGET_DIR]       # Skip installing Claude Code skills
  bash install.sh --no-hooks [TARGET_DIR]        # Skip installing the pre-push hygiene hook
  bash install.sh --profile api [TARGET_DIR]     # Install the check set for a repo of that shape
  bash install.sh --cli-ref v0.1.0 [TARGET_DIR]  # Pin the CLI to that tag or commit instead of this checkout's
  bash install.sh --cli-source <git-url> [TARGET_DIR]  # Install the CLI from an internal mirror of this repo

Env var equivalents:
  SLOPSTOPPER_NO_TASK=1 bash install.sh
  SLOPSTOPPER_NO_SKILLS=1 bash install.sh
  SLOPSTOPPER_NO_HOOKS=1 bash install.sh
  SLOPSTOPPER_PROFILE=api bash install.sh
  SLOPSTOPPER_CLI_REF=v0.1.0 bash install.sh
  SLOPSTOPPER_CLI_SOURCE=<git-url> bash install.sh
  SLOPSTOPPER_CLI_SPEC='slopstopper-cli==0.1.0' bash install.sh
      # a full pip requirement from an internal index; it replaces the git
      # spec for the local pipx install only (cli.source / cli.ref are still
      # written, and CI reads the same variable from its environment)

Profiles (--profile) tailor the suite to the repo's shape:

  ui       a site or app that serves HTML. Every check applies (default);
           the four API checks once the repo shows an API (a spec, a
           server framework, or an API check configured under api.*).
  api      JSON/gRPC endpoints with no browser surface. Drops the nine
           browser-and-SEO reliability checks, which assume HTML and a
           public web surface and would go red rather than no-op.
  library  a library, CLI or package with no deployed surface. Also drops
           CSP exceptions and the four API checks.

The flag writes `profile:` into .slopstopper.yml, so the choice survives
re-runs and is visible in review; the workflow set is derived from that key
on every run. Omitting the flag leaves an existing `profile:` untouched and
prints a suggestion when the key is unset. To keep one check the profile
drops, list it under `workflows.enabled` in .slopstopper.yml. See
`slopstopper profile list` for the full mapping.

Default mode installs workflows that invoke `task ss:<check>` so the suite
shares a single invocation surface with the rest of your codebase. `--no-task`
installs workflows that invoke `slopstopper run <check>` directly.

How the CLI is pinned: install.sh records the git ref of the checkout it
runs from (the tag, or the commit SHA when HEAD is not tagged) as `cli.ref`
in .slopstopper.yml, next to `cli.source`, and installs exactly that locally
with `pipx install --force "slopstopper-cli @ git+<source>@<ref>#subdirectory=cli"`.
CI installs the same spec from the same two keys. To move the pin, clone the
new tag and re-run, or pass --cli-ref. Nothing is ever resolved to "latest".
git, python3, pipx and mise are required (mise installs the pinned task,
node, snyk and gitleaks from mise.toml: run `mise install` after this).

The installer also writes the SlopStopper Claude Code skills into
<target>/.claude/skills/slopstopper-*/ so every contributor on the
repo benefits from them on git clone (Claude Code auto-discovers project-
level skills). Pass --no-skills to skip this step. To refresh skills later
without re-running the whole installer, use install-skill.sh from the
same checkout.

The installer also wires a pre-push git hook (.githooks/pre-push, via
core.hooksPath) that runs the fast static hygiene checks (task ss:hygiene:test)
before every push, so the first CI run confirms rather than discovers. It won't
touch an existing hook manager (husky / lefthook / pre-commit) or a custom
core.hooksPath. In that case it drops the hook file in and prints how to opt in.
Pass --no-hooks to skip. Bypass a single push with 'git push --no-verify'.
USAGE
      exit 0
      ;;
    *)
      POSITIONAL+=("$1")
      shift
      ;;
  esac
done
set -- "${POSITIONAL[@]:+${POSITIONAL[@]}}"

TARGET_DIR="${1:-$(pwd)}"

# ── helpers ──────────────────────────────────────────────────────────────────

info()    { echo "  ℹ  $*"; }
success() { echo "  ✅ $*"; }
warn()    { echo "  ⚠️  $*"; }
error()   { echo "  ❌ $*" >&2; exit 1; }

sep() { echo "────────────────────────────────────────────────────────────"; }

# Path to the target's mise config, which holds the toolchain pins. Prefer
# an existing file (so we never create a second config alongside an adopter's),
# else default to mise.toml. mise reads any of these names.
mise_config_path() {
  local f
  for f in mise.toml .mise.toml mise.local.toml .config/mise/config.toml; do
    [ -f "$TARGET_DIR/$f" ] && { echo "$TARGET_DIR/$f"; return 0; }
  done
  echo "$TARGET_DIR/mise.toml"
}

# Migration cleanup: remove the defunct cli_version key (and its preceding
# comment block, if present) from .slopstopper.yml. The CLI pin is the
# `cli:` block now. `awk` + mv keeps this portable across BSD (macOS) and GNU.
strip_legacy_cli_version() {
  local cfg="$1"
  [ -f "$cfg" ] || return 0
  grep -qE '^cli_version:' "$cfg" 2>/dev/null || return 0
  awk '
    /^# ── slopstopper-cli version pin/ { skip=1 }
    skip && /^cli_version:/             { skip=0; next }
    skip                                { next }
    /^cli_version:/                     { next }
    { print }
  ' "$cfg" > "$cfg.tmp" && mv "$cfg.tmp" "$cfg"
}

# Migration cleanup: remove the dead node_version key (and its preceding comment
# block, if present) from .slopstopper.yml. The key was never read and node
# lives in mise.toml ([tools] node), so the key is removed outright.
strip_legacy_node_version() {
  local cfg="$1"
  [ -f "$cfg" ] || return 0
  grep -qE '^node_version:' "$cfg" 2>/dev/null || return 0
  awk '
    /^# ── Node version pin/ { skip=1 }
    skip && /^node_version:/  { skip=0; next }
    skip                      { next }
    /^node_version:/          { next }
    { print }
  ' "$cfg" > "$cfg.tmp" && mv "$cfg.tmp" "$cfg"
}

# Set a [tools] entry in a mise config, portable across BSD/GNU and offline.
# It needs no mise binary and no network: the pins are written, and
# `mise install` resolves them when the adopter runs it. Creates the file
# and [tools] section if absent; replaces the entry if present.
write_mise_tool() {
  local cfg="$1" key="$2" ver="$3"
  local line="\"$key\" = \"$ver\""
  local pat="^[[:space:]]*\"?${key}\"?[[:space:]]*="
  if [ ! -f "$cfg" ]; then
    mkdir -p "$(dirname "$cfg")"
    printf '[tools]\n%s\n' "$line" > "$cfg"
  elif grep -qE "$pat" "$cfg" 2>/dev/null; then
    awk -v pat="$pat" -v repl="$line" '$0 ~ pat {print repl; next} {print}' \
      "$cfg" > "$cfg.tmp" && mv "$cfg.tmp" "$cfg"
  elif grep -qE '^\[tools\]' "$cfg" 2>/dev/null; then
    awk -v repl="$line" '{print} /^\[tools\]/ && !d {print repl; d=1}' \
      "$cfg" > "$cfg.tmp" && mv "$cfg.tmp" "$cfg"
  else
    printf '\n[tools]\n%s\n' "$line" >> "$cfg"
  fi
}

# Remove a [tools] entry from a mise config, if present. Exit 0 when it
# removed something, 1 otherwise, so callers can say so.
remove_mise_tool() {
  local cfg="$1" key="$2"
  local pat="^[[:space:]]*\"?${key}\"?[[:space:]]*="
  [ -f "$cfg" ] || return 1
  grep -qE "$pat" "$cfg" 2>/dev/null || return 1
  awk -v pat="$pat" '$0 ~ pat {next} {print}' "$cfg" > "$cfg.tmp" && mv "$cfg.tmp" "$cfg"
}

# True if the target already declares a Node version in a place mise (and the
# workflows) honour, which means a `node` [tools] entry in the mise config or
# a .node-version / .nvmrc file. We seed our own node pin only when none of these
# exist, so a re-run never clobbers an adopter's existing Node setup.
target_has_node_pin() {
  local mcfg="$1"
  [ -f "$TARGET_DIR/.node-version" ] && return 0
  [ -f "$TARGET_DIR/.nvmrc" ] && return 0
  [ -f "$mcfg" ] && grep -qE '^[[:space:]]*"?node"?[[:space:]]*=' "$mcfg" 2>/dev/null && return 0
  return 1
}

# ── prerequisite check ────────────────────────────────────────────────────────
#
# Surface missing tools up front rather than letting the install fail mid-flight
# with a cryptic "command not found". git, python3, pipx and mise are
# hard-required (the ref is read with git, the profile set is resolved with
# python3, the CLI is installed with pipx, the scanners with mise); node is
# soft-required (you'll need it to run the browser checks, but the install
# itself succeeds without it).

preflight() {
  local missing_hard=()
  local missing_soft=()

  command -v git     >/dev/null 2>&1 || missing_hard+=("git (reads the ref this checkout is at)")
  command -v python3 >/dev/null 2>&1 || missing_hard+=("python3 (slopstopper-cli is a Python package): https://www.python.org/downloads/")
  # pipx installs the CLI from this repository's git ref; mise installs the
  # pinned task/node/snyk/gitleaks from mise.toml. Both are skipped under
  # the SKIP_CLI_INSTALL test seam (no real install happens).
  if [ "${SKIP_CLI_INSTALL:-0}" != "1" ]; then
    command -v pipx >/dev/null 2>&1 || missing_hard+=("pipx (installs slopstopper-cli from the pinned git ref): https://pipx.pypa.io")
    command -v mise >/dev/null 2>&1 || missing_hard+=("mise (installs the pinned task, node, snyk and gitleaks): https://mise.jdx.dev")
  fi
  command -v node    >/dev/null 2>&1 || missing_soft+=("node (Playwright, Lighthouse CI, markdownlint; mise.toml pins it, so 'mise install' provides it)")

  if [ "${#missing_hard[@]}" -gt 0 ]; then
    for tool in "${missing_hard[@]}"; do
      echo "  ❌ Required tool missing: $tool" >&2
    done
    error "Install the tools above and re-run."
  fi

  if [ "${#missing_soft[@]}" -gt 0 ]; then
    warn "The following tools aren't installed. You'll need them to run the checks SlopStopper installs:"
    for tool in "${missing_soft[@]}"; do
      echo "      • $tool"
    done
    info "Continuing with install; install the tools above before running any task."
  fi
}

preflight

# ── locate source directory ───────────────────────────────────────────────────

# Resolve the directory that contains this script. It must be a checkout
# of this repository: nothing is cloned or downloaded at run time.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd)" || true

# Resolve the absolute target so we can compare against SCRIPT_DIR. If they
# are the same directory, the user is running install.sh from inside the
# target itself (an adopter's copy, or this repo against itself), and the cp
# Taskfile.ss.yml step would copy a file onto itself: refuse rather than
# pretend to refresh.
TARGET_ABS="$(cd "$TARGET_DIR" 2>/dev/null && pwd || echo "$TARGET_DIR")"

# Decide whether SCRIPT_DIR is the genuine slopstopper checkout. Markers:
#  - templates/  (only slopstopper has it; adopters get the templates
#    expanded into .slopstopper.yml, public/_headers, etc.)
#  - install.sh  (this script itself, sanity check)
#  - cli/slopstopper/data/profiles.json (the profile mapping the installer reads)
#  - SCRIPT_DIR != TARGET_ABS
SCRIPT_IS_SOURCE=0
if [ -n "$SCRIPT_DIR" ] \
   && [ -d "$SCRIPT_DIR/templates" ] \
   && [ -f "$SCRIPT_DIR/install.sh" ] \
   && [ -f "$SCRIPT_DIR/cli/slopstopper/data/profiles.json" ] \
   && [ "$SCRIPT_DIR" != "$TARGET_ABS" ]; then
  SCRIPT_IS_SOURCE=1
fi

if [ "$SCRIPT_IS_SOURCE" -eq 0 ]; then
  echo "  ❌ install.sh must run from a checkout of the slopstopper repository, against a different directory." >&2
  echo "     Nothing is downloaded at run time. From inside your repo:" >&2
  echo "" >&2
  echo "       git clone --branch v0.1.0 ${DEFAULT_CLI_SOURCE}" >&2
  echo "       bash slopstopper-snyk-safe/install.sh" >&2
  echo "" >&2
  echo "     (use the tag you want; the CLI is pinned to the same ref)" >&2
  exit 1
fi

# ── validate target ───────────────────────────────────────────────────────────

[ -d "$TARGET_DIR" ] || error "Target directory does not exist: $TARGET_DIR"

# ── resolve the CLI pin ──────────────────────────────────────────────────────
#
# The ref is the checkout's own: a tag when HEAD is exactly on one, else the
# full commit SHA. The workflows, shims, templates and skills copied below
# all come from this tree, so pinning the CLI to the same ref is the only
# combination that has been tested together. --cli-ref overrides it (for a
# source that is an unpacked archive, or to pin ahead deliberately).

resolve_cli_ref() {
  if [ -n "$CLI_REF_FLAG" ]; then
    echo "$CLI_REF_FLAG"
    return 0
  fi
  [ -e "$SCRIPT_DIR/.git" ] || return 0
  local tag
  tag="$(git -C "$SCRIPT_DIR" describe --tags --exact-match HEAD 2>/dev/null || true)"
  if [ -n "$tag" ]; then
    echo "$tag"
    return 0
  fi
  git -C "$SCRIPT_DIR" rev-parse HEAD 2>/dev/null || true
}

CLI_REF="$(resolve_cli_ref)"
if [ -z "$CLI_REF" ]; then
  error "could not determine the slopstopper-cli ref: $SCRIPT_DIR is not a git checkout. Clone this repository at a tag (git clone --branch vX.Y.Z ${DEFAULT_CLI_SOURCE}) and re-run, or pass --cli-ref <tag-or-sha>."
fi
# The ref and the source are spliced into a pip requirement, where `@`,
# `#` and whitespace change its meaning. Refuse anything that would.
case "$CLI_REF" in
  *[[:space:]]*|*@*|*#*|"")
    error "--cli-ref '$CLI_REF' is not a usable git ref (no whitespace, '@' or '#')." ;;
esac
case "$CLI_SOURCE" in
  *[[:space:]]*|*#*|"")
    error "--cli-source '$CLI_SOURCE' is not a usable git URL (no whitespace or '#')." ;;
esac
CLI_PIP_SPEC="slopstopper-cli @ git+${CLI_SOURCE}@${CLI_REF}#subdirectory=cli"

sep
echo "  🛠  SlopStopper: installing tooling"
echo "  Source : $SCRIPT_DIR"
echo "  Target : $TARGET_DIR"
echo "  CLI    : ${CLI_SOURCE} @ ${CLI_REF}"
sep

# ── project-shape profile helpers ────────────────────────────────────────────
#
# A profile is a preset for `workflows.disabled`: it names the workflows a
# repo of that shape has no use for (an API has no Core Web Vitals; a
# library has no URL at all). The mapping lives in
# cli/slopstopper/data/profiles.json and is read here through the CLI's own
# slopstopper.profiles module, imported straight from the source tree
# ($SCRIPT_DIR/cli) rather than from an installed package.
#
# That matters: the installer must be able to resolve the workflow set
# before (and independently of) the CLI install step succeeding, and
# there must be exactly one implementation of "which workflows does this
# repo carry" shared by the installer and the CLI. python3 is a hard
# prereq (see preflight) and $SCRIPT_DIR is always a slopstopper checkout
# by this point, so both hold.

ss_profiles_py() {
  # Run a python snippet with slopstopper importable and CWD = the target
  # repo, because slopstopper.config reads .slopstopper.yml from CWD.
  # Extra args are passed through as sys.argv[1:].
  local snippet="$1"
  shift
  ( cd "$TARGET_DIR" && PYTHONPATH="$SCRIPT_DIR/cli" python3 -c "$snippet" "$@" )
}

profile_names() {
  ss_profiles_py 'from slopstopper import profiles; print(" ".join(profiles.names()))'
}

profile_is_valid() {
  ss_profiles_py '
import sys
from slopstopper import profiles
sys.exit(0 if profiles.describe(sys.argv[1]) else 1)
' "$1" >/dev/null 2>&1
}

profile_active() {
  ss_profiles_py 'from slopstopper import profiles; print(profiles.active_name())'
}

profile_summary() {
  ss_profiles_py '
from slopstopper import profiles
entry = profiles.describe(profiles.active_name()) or {}
print(entry.get("summary", ""))
'
}

# Newline-separated workflow filenames this repo should not carry:
# the profile's set minus workflows.enabled, plus workflows.disabled.
profile_effective_disabled() {
  ss_profiles_py '
from slopstopper import profiles
for name in sorted(profiles.effective_disabled()):
    print(name)
'
}

# Advisory shape detection: prints "<profile>\t<reason>". Never applied
# automatically, because an install may be scripted and non-interactive,
# and silently switching checks off is worse than the default superset
# going red.
profile_detect() {
  ss_profiles_py '
from slopstopper import profiles
name, reason = profiles.detect()
print(name + "\t" + reason)
'
}

# Workflows the active profile holds back because the repo shows no API,
# one per line. Empty when there is an API or the profile has no such list.
profile_held_for_api() {
  ss_profiles_py '
from slopstopper import profiles
if not profiles.api_signal():
    dropped = set(profiles.without_api(profiles.active_name())) & profiles.effective_disabled()
    for name in sorted(dropped):
        print(name)
'
}

# Write `profile: <name>` into the target .slopstopper.yml. It replaces an
# existing top-level key in place, or appends a documented block if the
# file predates the key. Comment-preserving line edit.
write_profile_key() {
  local name="$1"
  ss_profiles_py '
import sys
from pathlib import Path

name = sys.argv[1]
path = Path(".slopstopper.yml")
if not path.is_file():
    sys.exit(0)

lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
for i, line in enumerate(lines):
    if line.startswith("profile:"):
        if line.strip() == f"profile: {name}":
            print("unchanged")
            sys.exit(0)
        lines[i] = f"profile: {name}\n"
        path.write_text("".join(lines), encoding="utf-8")
        print("replaced")
        sys.exit(0)

block = (
    "\n# ── Project-shape profile ────────────────────────────\n"
    "# Which checks apply to a repo of this shape. See\n"
    "# `slopstopper profile list` for the full mapping.\n"
    f"profile: {name}\n"
)
with path.open("a", encoding="utf-8") as fh:
    fh.write(block)
print("appended")
' "$name"
}

# Write the `cli:` block (source + ref) into the target .slopstopper.yml.
# An existing block has its two keys replaced in place (comments and any
# other key kept); a config without one gets a documented block appended.
# Prints unchanged / replaced / appended, so a re-run from the same ref
# leaves the file byte-for-byte.
write_cli_pin() {
  ss_profiles_py '
import sys
from pathlib import Path

source, ref = sys.argv[1], sys.argv[2]
path = Path(".slopstopper.yml")
if not path.is_file():
    sys.exit(0)

lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
start = next((i for i, line in enumerate(lines) if line.startswith("cli:")), None)

if start is None:
    block = (
        "\n# ── slopstopper-cli pin ──────────────────────────────\n"
        "# The git repository and exact ref (tag or commit) the CLI is installed\n"
        "# from, locally (pipx) and in CI (ss-setup). Written by install.sh from\n"
        "# the checkout it ran from; to move it, clone the new tag and re-run.\n"
        "cli:\n"
        f"  source: {source}\n"
        f"  ref: {ref}\n"
    )
    with path.open("a", encoding="utf-8") as fh:
        fh.write(block)
    print("appended")
    sys.exit(0)

# The block is the indented lines after `cli:` (blank lines are skipped over).
last = start
i = start + 1
while i < len(lines):
    if not lines[i].strip():
        i += 1
        continue
    if lines[i][0] in " \t":
        last = i
        i += 1
        continue
    break

found = {}
for i in range(start + 1, last + 1):
    stripped = lines[i].strip()
    for key in ("source", "ref"):
        if stripped.startswith(key + ":"):
            found[key] = i

changed = False
for key, value in (("source", source), ("ref", ref)):
    new = f"  {key}: {value}\n"
    if key in found:
        if lines[found[key]] != new:
            lines[found[key]] = new
            changed = True
    else:
        last += 1
        lines.insert(last, new)
        changed = True

if changed:
    path.write_text("".join(lines), encoding="utf-8")
    print("replaced")
else:
    print("unchanged")
' "$1" "$2"
}

if [ -n "$PROFILE_FLAG" ]; then
  if ! profile_is_valid "$PROFILE_FLAG"; then
    error "Unknown --profile '$PROFILE_FLAG'. Expected one of: $(profile_names)"
  fi
fi

# ── install files ─────────────────────────────────────────────────────────────

# 1. Taskfile.ss.yml: always refreshed (SlopStopper-owned, safe to overwrite).
cp "$SCRIPT_DIR/Taskfile.ss.yml" "$TARGET_DIR/Taskfile.ss.yml"
success "Taskfile.ss.yml installed (refreshed)"

# 2. Root Taskfile.yml: only create it if missing.
# If the consumer already has one, print clear instructions instead of editing it.
if [ -f "$TARGET_DIR/Taskfile.yml" ]; then
  if grep -qE "^[[:space:]]+ss:[[:space:]]*$" "$TARGET_DIR/Taskfile.yml" 2>/dev/null \
     || grep -q "Taskfile.ss.yml" "$TARGET_DIR/Taskfile.yml" 2>/dev/null; then
    info "Taskfile.yml already references Taskfile.ss.yml, so no changes are needed"
  else
    warn "Taskfile.yml already exists, so leaving it alone."
    echo ""
    echo "  Add the following block to your existing Taskfile.yml so the"
    echo "  SlopStopper tasks become available as 'task ss:<name>':"
    echo ""
    echo "    includes:"
    echo "      ss:"
    echo "        taskfile: ./Taskfile.ss.yml"
    echo ""
  fi
else
  # Write a minimal root Taskfile inline rather than copying slopstopper's
  # own, because slopstopper's root file carries `contributing:*` + `decisions:*`
  # tasks that are scoped to working on slopstopper itself, not the suite
  # being shipped to adopters.
  cat > "$TARGET_DIR/Taskfile.yml" <<'YAML'
version: '3'

includes:
  ss:
    taskfile: ./Taskfile.ss.yml
YAML
  success "Taskfile.yml installed"
fi

# 3. .slopstopper.yml is seeded from templates/slopstopper.yml.starter, which
#    holds the handful of keys most repos set and a pointer to the full
#    schema reference (.slopstopper.yml.example) for everything else. An
#    existing config is never overwritten; only `profile:` (with --profile)
#    and the `cli:` block (every run) are written into it, line by line.
#
# If an `.ss/` overlay (specs, playwright config, lighthouse config) is
# already present in the target, the CLI's templates module prefers
# those files over the package data, in the same shape as the workflows.
if [ ! -f "$TARGET_DIR/.slopstopper.yml" ] && [ -f "$SCRIPT_DIR/templates/slopstopper.yml.starter" ]; then
  cp "$SCRIPT_DIR/templates/slopstopper.yml.starter" "$TARGET_DIR/.slopstopper.yml"
  success ".slopstopper.yml: seeded $TARGET_DIR/.slopstopper.yml as a starter. Every other key is in .slopstopper.yml.example in the slopstopper checkout ($SCRIPT_DIR/.slopstopper.yml.example)"
fi

# Apply --profile / SLOPSTOPPER_PROFILE by writing the key into the
# (possibly just-seeded) config. The config is the home for this choice,
# not the flag: it has to survive a re-run and be visible in review, and
# the workflow set below is derived from the key rather than the flag.
# Without the flag an existing `profile:` is left exactly as it is.
if [ -n "$PROFILE_FLAG" ]; then
  case "$(write_profile_key "$PROFILE_FLAG")" in
    unchanged) info ".slopstopper.yml: profile already '$PROFILE_FLAG'" ;;
    replaced)  success ".slopstopper.yml: profile set to '$PROFILE_FLAG'" ;;
    appended)  success ".slopstopper.yml: profile '$PROFILE_FLAG' added" ;;
    *)         warn ".slopstopper.yml: no config file in target, so profile '$PROFILE_FLAG' was not written" ;;
  esac
fi

# Migrate off the old mechanisms: the pre-git-pin cli_version key and the
# dead node_version key (nothing ever read it; node lives in mise.toml).
strip_legacy_cli_version "$TARGET_DIR/.slopstopper.yml"
strip_legacy_node_version "$TARGET_DIR/.slopstopper.yml"

# The CLI pin: cli.source + cli.ref, read by the ss-setup composite action
# in CI and by the adopter when refreshing.
case "$(write_cli_pin "$CLI_SOURCE" "$CLI_REF")" in
  unchanged) info ".slopstopper.yml: cli pin already ${CLI_REF}" ;;
  replaced)  success ".slopstopper.yml: cli pin set to ${CLI_REF} (${CLI_SOURCE})" ;;
  appended)  success ".slopstopper.yml: cli pin ${CLI_REF} added (${CLI_SOURCE})" ;;
  *)         warn ".slopstopper.yml: no config file in target, so the cli pin was not written" ;;
esac

# 4. Toolchain pins in mise.toml: task, node, snyk and gitleaks. Written
#    into the file directly (no network, no mise binary needed), so the
#    install is deterministic and `mise install` is the one step that
#    fetches tools, from the versions reviewed in this file. Seed `node`
#    only when the adopter hasn't already declared one (mise.toml /
#    .node-version / .nvmrc), so we never override their build's Node.
sync_mise_pins() {
  local mcfg
  mcfg="$(mise_config_path)"
  write_mise_tool "$mcfg" "task" "$TASK_VERSION"
  target_has_node_pin "$mcfg" || write_mise_tool "$mcfg" "node" "$DEFAULT_NODE_VERSION"
  write_mise_tool "$mcfg" "npm:snyk" "$SNYK_VERSION"
  write_mise_tool "$mcfg" "gitleaks" "$GITLEAKS_VERSION"
  # The upstream suite pinned the CLI as a pipx package here. The CLI is
  # a git ref in .slopstopper.yml now, so a left-over pin would install a
  # second, unpinned-by-us copy from a package index.
  if remove_mise_tool "$mcfg" "pipx:slopstopper-cli"; then
    info "$(basename "$mcfg"): removed the legacy pipx:slopstopper-cli pin (the CLI is pinned by cli.ref in .slopstopper.yml now)"
  fi
  success "toolchain pinned in $(basename "$mcfg"): task $TASK_VERSION · snyk $SNYK_VERSION · gitleaks $GITLEAKS_VERSION$(target_has_node_pin "$mcfg" && grep -qE '^[[:space:]]*"?node"?[[:space:]]*=' "$mcfg" 2>/dev/null && echo " · node $(sed -n 's/^[[:space:]]*"\{0,1\}node"\{0,1\}[[:space:]]*=[[:space:]]*"\([^"]*\)".*/\1/p' "$mcfg" | head -1)")"
}

sync_mise_pins

# 5. slopstopper-cli: installed locally by pipx from the pinned git ref, so
#    the CLI on this machine is the one the workflows were written against.
#    SLOPSTOPPER_CLI_SPEC (a full pip requirement, for an internal index)
#    replaces the git spec for this local install only; CI honours the same
#    variable from its environment.
install_cli() {
  local spec="${SLOPSTOPPER_CLI_SPEC:-$CLI_PIP_SPEC}"
  if [ "${SKIP_CLI_INSTALL:-0}" = "1" ]; then
    info "Skipping the CLI install (SKIP_CLI_INSTALL=1). It would run: pipx install --force \"$spec\""
    return 0
  fi
  info "Installing slopstopper-cli via pipx from: $spec"
  if ! pipx install --force "$spec" >/dev/null; then
    error "pipx install failed. Check that ${CLI_SOURCE} is reachable and that ref '${CLI_REF}' exists there (or pass --cli-source for a mirror), then re-run."
  fi
  if command -v slopstopper >/dev/null 2>&1; then
    success "slopstopper-cli installed ($(slopstopper --version 2>/dev/null || echo "slopstopper @ $CLI_REF"))"
  else
    success "slopstopper-cli installed by pipx (@ $CLI_REF)"
    warn "'slopstopper' is not on PATH in this shell. Run 'pipx ensurepath' and open a new shell."
  fi
}

install_cli

# 6. .ss/ overlay: nothing is seeded by default. Every CLI-managed
#    file (Playwright specs, Playwright config, lighthouserc dev/prod,
#    server.js) lives inside slopstopper-cli and is resolved at runtime
#    via .ss/ override → package-data fallback. Adopters who want to
#    customise a file run `slopstopper templates eject <name>` to copy
#    the bundled version into .ss/<name>.
mkdir -p "$TARGET_DIR/.ss"

# Clean up any legacy .ss/scripts/ left over from pre-CLI installs.
# Every script previously copied into adopter repos is now bundled in
# slopstopper-cli.
if [ -d "$TARGET_DIR/.ss/scripts" ]; then
  rm -rf "$TARGET_DIR/.ss/scripts"
  success ".ss/scripts/ removed (logic now lives in slopstopper-cli)"
fi

# Clean up legacy byte-equal template copies from pre-lift installs.
# If an adopter has customised any of these, leave the file alone, because
# the CLI's templates resolver keeps using the .ss/ override.
# Detection: compare bytes against the package data in this checkout, which
# is the same tree the CLI is pinned to.
DATA_DIR="$SCRIPT_DIR/cli/slopstopper/data"
if [ -d "$DATA_DIR" ]; then
  scrub_if_byte_equal() {
    local rel="$1"
    local adopter="$TARGET_DIR/.ss/$rel"
    local pkg="$DATA_DIR/$rel"
    if [ -e "$adopter" ] && [ -e "$pkg" ] && cmp -s "$adopter" "$pkg"; then
      rm -rf "$adopter"
      success ".ss/$rel removed (unmodified copy of package-data fallback)"
    fi
  }
  scrub_if_byte_equal "playwright.config.js"
  scrub_if_byte_equal "lighthouserc.json"
  scrub_if_byte_equal "lighthouserc.prod.json"
  scrub_if_byte_equal "server.js"
  # Spec by spec: a run under CLI <= 0.17 auto-ejected only the specs it
  # ran, so a partial set of unmodified copies is the common case.
  if [ -d "$TARGET_DIR/.ss/tests" ] && [ -d "$DATA_DIR/tests" ]; then
    for spec in "$DATA_DIR"/tests/*.spec.ts; do
      scrub_if_byte_equal "tests/$(basename "$spec")"
    done
    rmdir "$TARGET_DIR/.ss/tests" 2>/dev/null || true
  fi
fi

# 7. .github/workflows/: install the ss- prefixed generic workflows.
WORKFLOWS_SRC="$SCRIPT_DIR/.github/workflows"
WORKFLOWS_DST="$TARGET_DIR/.github/workflows"
mkdir -p "$WORKFLOWS_DST"

# Every workflow ships by default. The two layers are documented in
# the post-install matrix; consumers can delete any they don't want and
# re-running the installer will respect that deletion (tracked via
# .ss/.workflows-installed).
GENERIC_WORKFLOWS=(
  # Layer 1: static analysis (works on any code)
  "ss-hygiene-complexity-check.yml"
  "ss-hygiene-csp-exceptions-check.yml"
  "ss-hygiene-docs-accuracy-check.yml"
  "ss-hygiene-docs-size-check.yml"
  "ss-hygiene-docs-structure-check.yml"
  "ss-hygiene-entry-files-check.yml"
  "ss-hygiene-auto-label-pr.yml"
  "ss-security-sast-check.yml"
  "ss-security-secrets-check.yml"
  "ss-security-dependencies-check.yml"
  "ss-security-iac-check.yml"
  "ss-security-sbom-check.yml"
  "ss-workflow-failure-issue.yml"
  "ss-pr-summary.yml"
  # Layer 2: web-app / API dynamic (needs a URL)
  "ss-reliability-smoke-tests.yml"
  "ss-reliability-e2e-check.yml"
  "ss-reliability-accessibility-check.yml"
  "ss-reliability-broken-links-check.yml"
  "ss-reliability-core-web-vitals.yml"
  "ss-reliability-seo-check.yml"
  "ss-reliability-llms-txt-check.yml"
  "ss-reliability-robots-txt-check.yml"
  "ss-reliability-sitemap-check.yml"
  "ss-reliability-api-health-check.yml"
  "ss-reliability-api-latency-check.yml"
  "ss-security-api-headers-check.yml"
  # The one hygiene check that needs a URL: drift means the spec measured
  # against the thing it documents, so it lives in layer 2 with the rest.
  "ss-hygiene-openapi-check.yml"
)

# Artefacts an earlier slopstopper release shipped that this suite does not:
# the ZAP DAST scan, the Trivy/dependency-review vulnerability pair, the
# Cloudflare post-deploy trigger and the gh-aw doc-updater, plus the ZAP
# rules file the DAST check read. Nothing here reads them any more, so a
# re-run removes them; an adopter who wants one keeps it under another name.
OBSOLETE_WORKFLOWS=(
  "ss-security-dast-check.yml"
  "ss-security-vulnerability-all-check.yml"
  "ss-security-vulnerability-new-check.yml"
  "ss-cloudflare-deployed.yml"
  "ss-hygiene-doc-updater.md"
  "ss-hygiene-doc-updater.lock.yml"
)
OBSOLETE_FILES=(
  ".zap/rules.tsv"
)

for obsolete in "${OBSOLETE_WORKFLOWS[@]}"; do
  if [ -f "$WORKFLOWS_DST/$obsolete" ]; then
    rm -f "$WORKFLOWS_DST/$obsolete"
    info "removed obsolete workflow .github/workflows/$obsolete (no longer shipped by this suite)"
  fi
done
for obsolete in "${OBSOLETE_FILES[@]}"; do
  if [ -f "$TARGET_DIR/$obsolete" ]; then
    rm -f "$TARGET_DIR/$obsolete"
    rmdir "$TARGET_DIR/$(dirname "$obsolete")" 2>/dev/null || true
    info "removed obsolete file $obsolete (nothing in this suite reads it)"
  fi
done

MARKER_FILE="$TARGET_DIR/.ss/.workflows-installed"

# Workflows this repo shouldn't carry: the `profile:` preset minus
# workflows.enabled, plus workflows.disabled. Resolved once here and
# honoured both in the install loop below (skip, and remove a copy an
# earlier profile left behind) and in the sweep further down (which also
# catches names outside GENERIC_WORKFLOWS).
PROFILE_ACTIVE="$(profile_active)" || true
DISABLED_WORKFLOWS="$(profile_effective_disabled)" || true

# Degrade safely and never silently. If the profiles module couldn't be
# resolved (no python3, unreadable cli/ tree), an empty name must not look
# like "this repo carries fewer checks". The full `ui` set must not land in
# a repo whose config explicitly opted out of it either. So a non-default
# `profile:` aborts here, before any workflow has been written, and the
# default warns and continues.
if [ -z "$PROFILE_ACTIVE" ]; then
  CONFIGURED_PROFILE="$(sed -n "s/^profile:[[:space:]]*[\"']\{0,1\}\([A-Za-z0-9_-]*\).*/\1/p" "$TARGET_DIR/.slopstopper.yml" 2>/dev/null | head -n 1)"
  if [ -n "$CONFIGURED_PROFILE" ] && [ "$CONFIGURED_PROFILE" != "ui" ]; then
    error "could not resolve the workflow set for profile '$CONFIGURED_PROFILE' (python3 or the slopstopper.profiles module is unavailable). Refusing to install the full 'ui' set into a repo that opted out of it. No workflows were written. Fix the toolchain and re-run."
  fi
  warn "profile lookup failed (python3 or slopstopper.profiles unavailable), so defaulting to 'ui' and installing every check. Re-run once the toolchain works if that is not what you want."
  PROFILE_ACTIVE="ui"
fi

workflow_is_disabled() {
  [ -n "$DISABLED_WORKFLOWS" ] && printf '%s\n' "$DISABLED_WORKFLOWS" | grep -Fxq "$1"
}

# Load the set of workflows we installed last time (if any). If a workflow
# is in this list AND missing from the consumer's repo now, they deleted it
# and we won't re-add it. Compatible with bash 3.2 (no associative arrays).
was_previously_installed() {
  [ "$IGNORE_STALE_MARKER" -eq 1 ] && return 1
  [ -f "$MARKER_FILE" ] && grep -Fxq "$1" "$MARKER_FILE"
}

# Stale-marker detection: a marker lists every workflow as previously-
# installed but zero `ss-*.yml` files exist on disk. The only realistic
# explanation is the marker was left over from a prior branch (e.g. a
# revert) and the deletion-respect logic would now skip every workflow,
# yielding a silent zero-workflows install. A real user would not delete
# every workflow; treat this as a stale marker and re-install fresh.
#
# Use `find` rather than `ls` for the count, because `ls path/glob*` returns
# non-zero when the glob has no matches, which combines with `set -e -o
# pipefail` to abort the script. `find` returns 0 even on no matches.
IGNORE_STALE_MARKER=0
if [ -f "$MARKER_FILE" ]; then
  on_disk_count=$(find "$WORKFLOWS_DST" -maxdepth 1 -name 'ss-*.yml' -type f 2>/dev/null | wc -l | tr -d ' ')
  marker_count=$(grep -c '^ss-' "$MARKER_FILE" 2>/dev/null || true)
  [ -z "$marker_count" ] && marker_count=0
  if [ "$on_disk_count" = "0" ] && [ "$marker_count" -gt 0 ]; then
    IGNORE_STALE_MARKER=1
    warn "Stale .ss/.workflows-installed detected ($marker_count entries, 0 workflows on disk), so treating this as a fresh install and re-adding all workflows. If you genuinely deleted every workflow, set workflows.disabled in .slopstopper.yml instead so the choice survives re-runs."
  fi
fi

INSTALLED_WORKFLOWS=0
REFRESHED_WORKFLOWS=0
DELETED_RESPECTED=0
PROFILE_SKIPPED=0
PROFILE_REMOVED=0
NEW_MARKER_CONTENT=""

for wf in "${GENERIC_WORKFLOWS[@]}"; do
  SRC="$WORKFLOWS_SRC/$wf"
  DST="$WORKFLOWS_DST/$wf"
  [ -f "$SRC" ] || continue

  # Profile / config says this repo doesn't carry this workflow. Remove a
  # copy an earlier install left behind (so switching ui → api cleans up),
  # and deliberately keep it out of the marker: the marker means "we
  # installed this", so omitting it lets a later profile change bring the
  # workflow back instead of the deletion-respect rule below suppressing it.
  if workflow_is_disabled "$wf"; then
    if [ -f "$DST" ]; then
      rm -f "$DST"
      (( PROFILE_REMOVED++ )) || true
    fi
    (( PROFILE_SKIPPED++ )) || true
    continue
  fi

  # Respect deletions: if we installed it before AND it's gone now, leave it gone.
  if was_previously_installed "$wf" && [ ! -f "$DST" ]; then
    (( DELETED_RESPECTED++ )) || true
    continue
  fi

  if [ -f "$DST" ]; then
    cp "$SRC" "$DST"
    (( REFRESHED_WORKFLOWS++ )) || true
  else
    cp "$SRC" "$DST"
    (( INSTALLED_WORKFLOWS++ )) || true
  fi
  NEW_MARKER_CONTENT+="$wf"$'\n'
done

# Write the updated marker (atomic via tmp + mv).
printf '%s' "$NEW_MARKER_CONTENT" > "$MARKER_FILE.tmp" && mv "$MARKER_FILE.tmp" "$MARKER_FILE"

success "$INSTALLED_WORKFLOWS workflow(s) installed, $REFRESHED_WORKFLOWS refreshed, $DELETED_RESPECTED previously-deleted skipped"

# Composite actions every ss-* workflow builds on (`uses: ./.github/actions/ss-*`).
# Plumbing, not checks: always copied, never tracked in the marker, never
# dropped by a profile. Each shipped action is replaced wholesale (so a file
# removed from it upstream goes too). An action slopstopper stops shipping,
# or renames, goes in OBSOLETE_ACTIONS so re-runs delete the old directory.
# This is the same contract as install-skill.sh's OBSOLETE_SKILLS.
OBSOLETE_ACTIONS=()
ACTIONS_SRC="$SCRIPT_DIR/.github/actions"
ACTIONS_DST="$TARGET_DIR/.github/actions"
if [ -d "$ACTIONS_SRC" ]; then
  mkdir -p "$ACTIONS_DST"
  for obsolete in ${OBSOLETE_ACTIONS[@]+"${OBSOLETE_ACTIONS[@]}"}; do
    if [ -d "$ACTIONS_DST/$obsolete" ]; then
      rm -rf "${ACTIONS_DST:?}/${obsolete:?}"
      info "removed obsolete composite action .github/actions/$obsolete"
    fi
  done
  ACTIONS_INSTALLED=""
  for action_dir in "$ACTIONS_SRC"/ss-*/; do
    [ -d "$action_dir" ] || continue
    action_name="$(basename "$action_dir")"
    rm -rf "${ACTIONS_DST:?}/${action_name:?}"
    cp -R "$action_dir" "$ACTIONS_DST/$action_name"
    ACTIONS_INSTALLED="${ACTIONS_INSTALLED:+$ACTIONS_INSTALLED, }$action_name"
  done
  success "composite actions refreshed under .github/actions/ ($ACTIONS_INSTALLED)"
fi

if [ "$PROFILE_SKIPPED" -gt 0 ]; then
  info "profile '$PROFILE_ACTIVE': $PROFILE_SKIPPED workflow(s) not installed for this repo shape ($PROFILE_REMOVED removed from a previous install)"
  info "  see 'slopstopper profile show'; list one under workflows.enabled in .slopstopper.yml to keep it"
fi

# Post-process workflows for --no-task mode: rewrite `task ss:<X> -- args` into
# `slopstopper run <X> args`. The CLI accepts the same bare-positional URL
# adopters' shims pass through, so the resulting workflows do the same job
# without any `task ss:*` invocation. The toolchain step (ss-setup) is
# unchanged: it still installs the pinned slopstopper-cli from cli.source /
# cli.ref (mise also installs `task`, which simply goes unused here).
#
# Idempotent: re-running install.sh without --no-task re-copies the
# Task-flavour workflow from source on top, so adopters can switch modes
# by re-running with/without the flag.
if [ "$USE_TASK" = "false" ]; then
  python3 - "$WORKFLOWS_DST" <<'PY'
import re
import sys
from pathlib import Path

workflows_dst = Path(sys.argv[1])

# Order matters: handle the `-- ` separator form first, then bare invocations.
# Shim names match CLI check names one-to-one, so no alias mapping is needed.
# `task -x` (pass the command's exit code through instead of Task's 201)
# is matched too; the CLI returns the check's own code natively.
invoke_with_args = re.compile(r"task (?:-x )?ss:([a-z][a-z0-9_:-]+) -- ")
invoke_bare      = re.compile(r"task (?:-x )?ss:([a-z][a-z0-9_:-]+)")

transformed = 0
for path in sorted(workflows_dst.glob("ss-*.yml")):
    text = path.read_text()
    original = text
    text = invoke_with_args.sub(r"slopstopper run \1 ", text)
    text = invoke_bare.sub(r"slopstopper run \1", text)
    if text != original:
        path.write_text(text)
        transformed += 1

print(f"  ✅ Post-processed {transformed} workflow(s) for --no-task mode (invocations rewritten to slopstopper-cli)")
PY
fi

# 8. Merge devDependencies into package.json if one exists.
PKG="$TARGET_DIR/package.json"
SRC_PKG="$SCRIPT_DIR/package.json"
if [ -f "$PKG" ] && command -v node &>/dev/null; then
  _TMP_MERGE="$(mktemp)"
  _TMP_ADDED="$(mktemp)"
  node - "$PKG" "$SRC_PKG" "$_TMP_ADDED" >"$_TMP_MERGE" <<'JS' || true
const fs = require('fs');
const [, , dst, src, addedFile] = process.argv;
const d = JSON.parse(fs.readFileSync(dst, 'utf8'));
const s = JSON.parse(fs.readFileSync(src, 'utf8'));
const added = [];
const conflicts = [];
d.devDependencies = d.devDependencies || {};
for (const [k, v] of Object.entries(s.devDependencies || {})) {
  if (!d.devDependencies[k]) { d.devDependencies[k] = v; added.push(k); }
  else if (d.devDependencies[k] !== v) { conflicts.push(`${k}: existing=${d.devDependencies[k]} template=${v}`); }
}
process.stdout.write(JSON.stringify(d, null, 2) + '\n');
fs.writeFileSync(addedFile, JSON.stringify({ added, conflicts }));
JS
  MERGED="$(cat "$_TMP_MERGE" 2>/dev/null || true)"
  rm -f "$_TMP_MERGE"
  if [ -n "$MERGED" ]; then
    echo "$MERGED" > "$PKG"
    if [ -f "$_TMP_ADDED" ]; then
      ADDED_PKGS="$(node -pe "JSON.parse(require('fs').readFileSync('$_TMP_ADDED','utf8')).added.join(', ')" 2>/dev/null || true)"
      CONFLICT_PKGS="$(node -pe "JSON.parse(require('fs').readFileSync('$_TMP_ADDED','utf8')).conflicts.join('; ')" 2>/dev/null || true)"
      rm -f "$_TMP_ADDED"
      if [ -n "$ADDED_PKGS" ]; then
        success "Added devDependencies: $ADDED_PKGS"
      else
        info "No new devDependencies to add"
      fi
      if [ -n "$CONFLICT_PKGS" ]; then
        warn "Version conflicts (your version kept): $CONFLICT_PKGS"
      fi
    fi
  fi
elif [ ! -f "$PKG" ]; then
  # A minimal manifest named after the target, carrying only slopstopper's
  # devDependencies. No build script: the browser-check workflows skip the
  # build when there is none and serve the repo as-is, so a site that needs
  # building says so by adding scripts.build.
  if python3 - "$PKG" "$SRC_PKG" "$(basename "$(cd "$TARGET_DIR" && pwd)")" <<'PY'
import json, re, sys
dst, src, name = sys.argv[1:]
dev = json.load(open(src)).get("devDependencies", {})
name = re.sub(r"[^a-z0-9._-]+", "-", name.lower()).strip("-._") or "site"
with open(dst, "w") as f:
    json.dump({"name": name, "private": True, "devDependencies": dev}, f, indent=2)
    f.write("\n")
PY
  then
    success "package.json created (devDependencies only; add scripts.build if your site needs building)"
  else
    warn "package.json: could not create one (python3 failed). Add slopstopper's devDependencies by hand."
  fi
fi

# 9. Seed adopter-default templates. seed_template() copies <src> to <dst>
# only if <dst> does not exist; never overwrites. The point is to remove
# the cliff between "install lands" and "first PR is green". These
# defaults handle the common cases (no config file, no auto-label config,
# no headers, no gitignore entries) so an adopter's first CI run isn't a
# discovery pass.

seed_template() {
  local label="$1"
  local src="$2"
  local dst="$3"
  if [ ! -f "$src" ]; then
    return 0  # template missing from source (e.g. an old install.sh run against a new repo), so skip silently
  fi
  if [ -f "$dst" ]; then
    info "$label: $dst already exists, so leaving it alone"
    return 0
  fi
  mkdir -p "$(dirname "$dst")"
  cp "$src" "$dst"
  success "$label: seeded $dst"
}

# The adopter's config, .slopstopper.yml, comes from the starter template and
# is seeded earlier, before the cli pin and mise pins are written into it.

# .github/labeler.yml: config for ss-hygiene-auto-label-pr.yml
seed_template ".github/labeler.yml" \
  "$SCRIPT_DIR/templates/labeler.yml.example" \
  "$TARGET_DIR/.github/labeler.yml"

# public/_headers: static-host header baseline (commented out)
#
# Idempotent append-with-markers (same pattern as the .gitignore block
# below). Adopters often arrive with a public/_headers that holds only
# cache rules; a plain skip-if-exists would deny them the security
# baseline forever. Instead: detect our begin-marker; append the block
# bracketed by markers if it isn't there yet; leave existing content
# untouched. The block ships fully commented so adopters opt in by
# uncommenting, which keeps the same default safety as the fresh-install case.
#
# A _headers that already sets a security header (an uncommented CSP, HSTS
# or X-Content-Type-Options line) is configured, so it gets nothing: the
# commented baseline would sit next to the live policy as a second,
# conflicting one, and an adopter who removed the block would get it back
# on every refresh (#453).
headers_already_configured() {
  grep -Eiq '^[[:space:]]*(Content-Security-Policy|Strict-Transport-Security|X-Content-Type-Options):' "$1" 2>/dev/null
}

seed_headers_block() {
  if [ ! -d "$TARGET_DIR/public" ]; then
    info "public/_headers: no public/ directory in target, so skipping. Add one and re-run if you want the baseline."
    return 0
  fi
  local dst="$TARGET_DIR/public/_headers"
  local src="$SCRIPT_DIR/templates/_headers.example"
  if [ ! -f "$src" ]; then
    return 0
  fi
  if [ -f "$dst" ] && grep -Fq "# slopstopper security headers begin" "$dst" 2>/dev/null; then
    info "public/_headers: slopstopper security headers block already present, so leaving it alone"
    return 0
  fi
  if [ -f "$dst" ] && headers_already_configured "$dst"; then
    info "public/_headers: already sets security headers, so leaving it alone (the baseline is templates/_headers.example upstream)"
    return 0
  fi
  mkdir -p "$(dirname "$dst")"
  if [ -f "$dst" ] && [ -s "$dst" ]; then
    printf '\n' >> "$dst"
    cat "$src" >> "$dst"
    success "public/_headers: appended slopstopper security headers block (commented; uncomment to enable)"
  else
    cat "$src" > "$dst"
    success "public/_headers: seeded $dst"
  fi
}

seed_headers_block

# .markdownlint.json: defaults that let real docs pass (MD013 off, etc.).
# Adopters invoke markdownlint directly (`npx markdownlint "docs/**/*.md"`);
# markdownlint auto-discovers the closest config upward, so seeding at repo
# root works whether they call it from root or from docs/.
seed_template ".markdownlint.json" \
  "$SCRIPT_DIR/templates/markdownlint.json.example" \
  "$TARGET_DIR/.markdownlint.json"

# The AGENTS.md-first entry-file scaffolds (README.md, AGENTS.md, CLAUDE.md, docs/README.md).
# Templates ship inside the slopstopper-cli package under data/templates/entry-files/
# AND in the repo's cli/slopstopper/data/templates/entry-files/, which is where
# install.sh resolves them from (the checkout it runs from).
# Each file is seeded only when absent, so adopters who already have entry
# files keep them untouched. The ss:hygiene:entry-files check enforces the budgets,
# the pure `@AGENTS.md` include and the explicit-route rule, and emits a
# paste-ready snippet for any file that exists but breaks one; this seed
# handles only the missing-file case.
ENTRY_FILES_TEMPLATES_DIR="$SCRIPT_DIR/cli/slopstopper/data/templates/entry-files"
seed_template "README.md" \
  "$ENTRY_FILES_TEMPLATES_DIR/README.md" \
  "$TARGET_DIR/README.md"
seed_template "AGENTS.md" \
  "$ENTRY_FILES_TEMPLATES_DIR/AGENTS.md" \
  "$TARGET_DIR/AGENTS.md"
seed_template "CLAUDE.md" \
  "$ENTRY_FILES_TEMPLATES_DIR/CLAUDE.md" \
  "$TARGET_DIR/CLAUDE.md"
if [ -f "$TARGET_DIR/docs/index.md" ] && [ ! -f "$TARGET_DIR/docs/README.md" ]; then
  # A pre-0.15 install seeded docs/index.md as the map. The map is a README
  # now (the repo UI renders it in place) and its rows are explicit routes.
  # The rename is the adopter's commit, not the installer's, and the check's
  # report walks through it.
  warn "docs/index.md is the pre-0.15 map and the map is docs/README.md now. Run \`git mv docs/index.md docs/README.md\` and rewrite its rows as routes (task ss:hygiene:entry-files explains how)."
else
  seed_template "docs/README.md" \
    "$ENTRY_FILES_TEMPLATES_DIR/docs/README.md" \
    "$TARGET_DIR/docs/README.md"
fi

# .gitignore: the block between `# slopstopper begin` and `# slopstopper end`
# is slopstopper-owned, like Taskfile.ss.yml. Every run replaces it with the
# current template so new ignore lines reach existing installs; lines outside
# the markers are never touched. A begin marker with no end marker is a
# hand-edited block, so it's left alone with a warning rather than guessed at.
GI="$TARGET_DIR/.gitignore"
GI_BLOCK_SRC="$SCRIPT_DIR/templates/gitignore.block"
if [ -f "$GI_BLOCK_SRC" ]; then
  if [ -f "$GI" ] && grep -Fq "# slopstopper begin" "$GI" 2>/dev/null; then
    GI_RESULT="$(python3 - "$GI" "$GI_BLOCK_SRC" <<'PY'
import sys
gi, src = sys.argv[1:]
lines = open(gi).read().splitlines(keepends=True)
block = open(src).read()
if not block.endswith("\n"):
    block += "\n"
begin = next((i for i, l in enumerate(lines) if l.strip() == "# slopstopper begin"), None)
end = None if begin is None else next((i for i in range(begin, len(lines)) if lines[i].strip() == "# slopstopper end"), None)
if begin is None:
    print("no-begin")
elif end is None:
    print("no-end")
elif "".join(lines[begin:end + 1]) == block:
    print("current")
else:
    open(gi, "w").write("".join(lines[:begin]) + block + "".join(lines[end + 1:]))
    print("updated")
PY
)" || GI_RESULT=""
    case "$GI_RESULT" in
      updated) success ".gitignore: slopstopper block updated to the current template" ;;
      current) info ".gitignore: slopstopper block already current" ;;
      no-end) warn ".gitignore: '# slopstopper begin' has no '# slopstopper end', so the block was left alone. Restore the end marker and re-run to refresh it." ;;
      no-begin) warn ".gitignore: mentions '# slopstopper begin' but has no line that is exactly that marker, so the block was left alone. Restore the marker line and re-run to refresh it." ;;
      *) warn ".gitignore: could not refresh the slopstopper block (python3 failed), so it was left alone" ;;
    esac
  else
    [ -f "$GI" ] && [ -s "$GI" ] && printf '\n' >> "$GI"
    cat "$GI_BLOCK_SRC" >> "$GI"
    success ".gitignore: appended slopstopper block"
  fi
fi

# 10. Sweep the rest of the disabled set. The workflow loop above already
# skipped (and cleaned up) every disabled workflow in GENERIC_WORKFLOWS;
# this catches names that aren't in that list, such as a workflow from an
# older slopstopper release or one the adopter added and then listed. Driven by
# the same resolved set ($DISABLED_WORKFLOWS = (profile − workflows.enabled)
# ∪ workflows.disabled), so config drives deletions rather than "delete the
# file and trust the marker". Both work, but config-driven is easier to
# audit and survives clone-and-rebuild.
if [ -n "$DISABLED_WORKFLOWS" ]; then
  DISABLED_COUNT=0
  while IFS= read -r wf_name; do
    [ -z "$wf_name" ] && continue
    wf_path="$TARGET_DIR/.github/workflows/$wf_name"
    if [ -f "$wf_path" ]; then
      rm -f "$wf_path"
      (( DISABLED_COUNT++ )) || true
    fi
  done <<< "$DISABLED_WORKFLOWS"
  if [ "$DISABLED_COUNT" -gt 0 ]; then
    info "Removed $DISABLED_COUNT further workflow(s) from the disabled set"
  fi
fi

# ── install Claude Code skills (project level) ──────────────────────────────

# Skills land in <target>/.claude/skills/slopstopper-*/ (SKILL.md + references/) so every
# contributor that clones the repo gets them automatically (Claude Code
# auto-discovers project-level skills the same way as user-level). Disable
# with --no-skills / SLOPSTOPPER_NO_SKILLS.
#
# install-skill.sh is the one implementation (skill list, obsolete-skill
# cleanup, staging, validation). It runs here against the checkout this
# script is running from, which is the tree the workflows and templates were
# copied from, so the skills always match them and no network is needed.
# A skill failure is non-fatal for the suite install: the rest is in place,
# and install-skill.sh can be re-run on its own.

install_claude_skills() {
  if [ "$INSTALL_SKILLS" = "false" ]; then
    info "Skipping Claude Code skill install (--no-skills / SLOPSTOPPER_NO_SKILLS)."
    return 0
  fi

  echo ""
  sep
  echo "  🧠  Installing the SlopStopper Claude Code skills (project level)…"
  sep

  if ! SLOPSTOPPER_REPO_RAW="file://$SCRIPT_DIR" SLOPSTOPPER_SKILLS_QUIET=1 \
      bash "$SCRIPT_DIR/install-skill.sh" "$TARGET_DIR"; then
    warn "Claude Code skills not fully installed (non-fatal). Re-run install-skill.sh from this checkout to retry."
  fi
}

install_claude_skills

# ── install the pre-push hygiene hook ────────────────────────────────────────
#
# A pre-push hook that runs the fast static hygiene checks (`task
# ss:hygiene:test`) before every push, so the first CI run confirms rather than
# discovers. The hook script is slopstopper-owned and always refreshed (like
# Taskfile.ss.yml); it lives in `.githooks/` (tracked, reviewable) and git is
# pointed at it via `core.hooksPath`.
#
# Respectful wiring: we set core.hooksPath ONLY when it's safe, meaning the
# adopter hasn't already set a custom hooksPath and isn't running another hook manager
# (husky / lefthook / pre-commit). Otherwise we still drop the hook file in but
# leave their git config alone and print a one-line manual opt-in, so we never
# hijack an existing setup. Disable the whole step with --no-hooks.

# True if the target already manages git hooks some other way, through a
# non-default core.hooksPath or a husky / lefthook / pre-commit config. We only auto-wire
# core.hooksPath when none of these exist, mirroring target_has_node_pin's
# "never clobber the adopter's setup" stance.
target_has_hook_manager() {
  local existing
  existing="$(git -C "$TARGET_DIR" config --local --get core.hooksPath 2>/dev/null || true)"
  [ -n "$existing" ] && [ "$existing" != ".githooks" ] && return 0
  [ -d "$TARGET_DIR/.husky" ] && return 0
  [ -f "$TARGET_DIR/lefthook.yml" ] && return 0
  [ -f "$TARGET_DIR/.lefthook.yml" ] && return 0
  [ -f "$TARGET_DIR/lefthook.yaml" ] && return 0
  [ -f "$TARGET_DIR/.pre-commit-config.yaml" ] && return 0
  return 1
}

install_git_hook() {
  if [ "$INSTALL_HOOKS" = "false" ]; then
    info "Skipping pre-push hook install (--no-hooks / SLOPSTOPPER_NO_HOOKS)."
    return 0
  fi

  local src="$SCRIPT_DIR/.githooks/pre-push"
  if [ ! -f "$src" ]; then
    return 0  # an old checkout without the hook has nothing to install
  fi

  # Not a git repo → nothing to hook into. Not fatal; the rest of the install
  # is still useful.
  if ! git -C "$TARGET_DIR" rev-parse --git-dir >/dev/null 2>&1; then
    info "Target is not a git repository, so skipping the pre-push hook."
    return 0
  fi

  mkdir -p "$TARGET_DIR/.githooks"
  cp "$src" "$TARGET_DIR/.githooks/pre-push"
  chmod +x "$TARGET_DIR/.githooks/pre-push"

  if target_has_hook_manager; then
    success "pre-push hook written to .githooks/pre-push (refreshed)"
    warn "You already manage git hooks, so leaving core.hooksPath alone."
    info "To enable the slopstopper gate, either run:"
    info "    git config core.hooksPath .githooks"
    info "  or add 'task ss:hygiene:test' to your existing hook manager."
  else
    git -C "$TARGET_DIR" config core.hooksPath .githooks
    success "pre-push hook installed (core.hooksPath → .githooks)"
    info "Pushes now run 'task ss:hygiene:test' locally first. Bypass once with 'git push --no-verify'."
  fi
}

install_git_hook

# ── record the source commit ─────────────────────────────────────────────────
#
# Written last, once every step above has succeeded. Everything installed
# (workflows, actions, Taskfile.ss.yml, skills, the config schema those read)
# is this commit's, so a refresh can diff upstream between the committed
# value and the new one (the slopstopper-install skill's "Spot newly-shipped
# knobs"). A source that isn't a git checkout has no commit to record, so
# the marker is removed rather than left naming an install these files
# didn't come from.
SOURCE_SHA=""
if [ -e "$SCRIPT_DIR/.git" ]; then
  SOURCE_SHA="$(git -C "$SCRIPT_DIR" rev-parse HEAD 2>/dev/null || true)"
fi
if [ -n "$SOURCE_SHA" ]; then
  printf '%s\n' "$SOURCE_SHA" > "$TARGET_DIR/.ss/.installed-from"
else
  rm -f "$TARGET_DIR/.ss/.installed-from"
fi

# ── post-install guidance ─────────────────────────────────────────────────────

sep
echo ""
echo "  🎉 Installation complete!"
echo ""
echo "  📦 slopstopper-cli pinned to ${CLI_REF}"
echo "       cli.source / cli.ref in .slopstopper.yml; CI installs exactly that."
echo "       To move the pin, clone the new tag and re-run install.sh (or --cli-ref)."
echo "  🔧 Toolchain pinned in $(basename "$(mise_config_path)"): task · node · snyk ${SNYK_VERSION} · gitleaks ${GITLEAKS_VERSION}"
echo ""
echo "  ── SlopStopper status for this repo ──────────────────────────────"
echo ""
echo "  🧭 Profile: $PROFILE_ACTIVE"
echo "       $(profile_summary)"
if [ "$PROFILE_SKIPPED" -gt 0 ]; then
  echo "       $PROFILE_SKIPPED check(s) left out as not applying to this shape."
  echo "       'slopstopper profile show' lists them; workflows.enabled keeps one."
fi
HELD_FOR_API="$(profile_held_for_api 2>/dev/null || true)"
if [ -n "$HELD_FOR_API" ]; then
  HELD_COUNT="$(printf '%s\n' "$HELD_FOR_API" | wc -l | tr -d ' ')"
  echo "       No API found (no OpenAPI spec, server framework or configured API check),"
  echo "       so $HELD_COUNT API check$([ "$HELD_COUNT" = 1 ] || echo s) $([ "$HELD_COUNT" = 1 ] && echo isn\'t || echo aren\'t) installed. To add them, set api.* in"
  echo "       .slopstopper.yml (or list them under workflows.enabled) and re-run."
fi
echo ""
# The shape suggestion is advisory only, and skipped when the run set the
# profile explicitly. Never applied automatically: an install may be
# scripted and non-interactive, and silently switching checks off is worse
# than the default superset going visibly red.
if [ -z "$PROFILE_FLAG" ]; then
  DETECTED_LINE="$(profile_detect 2>/dev/null || true)"
  DETECTED_NAME="${DETECTED_LINE%%$'\t'*}"
  DETECTED_REASON="${DETECTED_LINE#*$'\t'}"
  if [ -n "$DETECTED_NAME" ] && [ "$DETECTED_NAME" != "$PROFILE_ACTIVE" ]; then
    echo "  💡 This looks more like a '$DETECTED_NAME' repo ($DETECTED_REASON),"
    echo "     which would drop the checks that don't apply to that shape."
    echo "     To switch, set this in .slopstopper.yml and re-run install.sh:"
    echo ""
    echo "       profile: $DETECTED_NAME"
    echo ""
    echo "     Nothing was changed for you. See 'slopstopper profile list'."
    echo ""
  fi
fi
# GitHub disables scheduled workflows after 60 days without repo activity,
# and a disabled workflow runs on no trigger, PRs included. A refresh is
# often run on exactly such a quiet repo, so say so here rather than let
# the checks vanish from the next PR. Silent when gh can't reach the repo.
DISABLED_WORKFLOWS="$(ss_profiles_py '
from slopstopper import workflow_state as w
for row in w.disabled_ss_workflows() or []:
    print("\t".join([str(row.get("name")), str(row.get("state")), w.enable_command(row)]))
' 2>/dev/null || true)"
if [ -n "$DISABLED_WORKFLOWS" ]; then
  echo "  ⛔ GitHub has switched off $(printf '%s\n' "$DISABLED_WORKFLOWS" | wc -l | tr -d ' ') slopstopper workflow(s). They run on no trigger, PRs included:"
  while IFS=$'\t' read -r wf_name wf_state wf_enable; do
    echo "       $wf_name ($wf_state) → $wf_enable"
  done <<< "$DISABLED_WORKFLOWS"
  echo "     GitHub disables scheduled workflows after 60 days without repo activity."
  echo ""
fi
echo "  ✅ Active now on any code, with no config needed:"
echo "       Secrets (Gitleaks) · Complexity · Doc Structure · Doc Accuracy · Doc Size"
echo "       Auto-label PRs · Workflow-failure tracker"
echo ""
echo "  🔐 Active once SNYK_TOKEN is set (the Snyk checks exit 2 until it is):"
echo "       SAST (Snyk Code) · Dependencies (Snyk Open Source) · IaC (Snyk IaC) · SBOM"
echo ""
echo "     Settings → Secrets and variables → Actions:"
echo "       secret   SNYK_TOKEN   a Snyk service-account token for your organisation (required)"
echo "       variable SNYK_ORG     the Snyk organisation slug to scan under (optional;"
echo "                             or security.snyk.org in .slopstopper.yml)"
echo "       variable SNYK_API     your regional API, e.g. https://api.eu.snyk.io (optional)"
echo "     Locally, export SNYK_TOKEN (or run 'snyk auth') before 'task ss:security:scan'."
echo ""
if [ "$INSTALL_HOOKS" = "true" ]; then
  echo "  🪝 Local pre-push gate: the hygiene checks run before every push"
  echo "       (task ss:hygiene:test) so CI confirms rather than discovers."
  echo "       Bypass a single push with 'git push --no-verify'."
  echo ""
fi
# Which URL-driven checks this repo actually carries depends on the
# profile, so build the list from what landed on disk rather than naming
# them all unconditionally.
DYNAMIC_LABELS=""
add_dynamic_label() {
  [ -f "$TARGET_DIR/.github/workflows/$1" ] || return 0
  if [ -z "$DYNAMIC_LABELS" ]; then
    DYNAMIC_LABELS="$2"
  else
    DYNAMIC_LABELS="$DYNAMIC_LABELS · $2"
  fi
}
add_dynamic_label "ss-reliability-smoke-tests.yml"          "Smoke"
add_dynamic_label "ss-reliability-e2e-check.yml"            "E2E"
add_dynamic_label "ss-reliability-accessibility-check.yml"  "Accessibility"
add_dynamic_label "ss-reliability-core-web-vitals.yml"      "Core Web Vitals"
add_dynamic_label "ss-reliability-seo-check.yml"            "SEO"
add_dynamic_label "ss-reliability-broken-links-check.yml"   "Broken Links"
add_dynamic_label "ss-reliability-llms-txt-check.yml"       "llms.txt"
add_dynamic_label "ss-reliability-robots-txt-check.yml"     "robots.txt"
add_dynamic_label "ss-reliability-sitemap-check.yml"        "Sitemap"
add_dynamic_label "ss-reliability-api-health-check.yml"     "API Health"
add_dynamic_label "ss-reliability-api-latency-check.yml"    "API Latency"
add_dynamic_label "ss-security-api-headers-check.yml"       "API Headers"
add_dynamic_label "ss-hygiene-openapi-check.yml"            "OpenAPI Drift"

if [ -n "$DYNAMIC_LABELS" ]; then
  echo "  ⏳ Active once you point them at your app (edit .slopstopper.yml):"
  echo "       $DYNAMIC_LABELS"
  echo ""
  echo "     # in .slopstopper.yml:"
  echo "     urls:"
  echo "       production: https://your-site.example.com"
  echo "       preview:    https://staging.your-site.example.com"
  echo ""
  echo "     # node is pinned in mise.toml ([tools] node), read by mise locally and"
  echo "     # in CI; change it there if your build needs a different version."
  echo ""
else
  echo "  💤 URL-driven checks: none installed. Profile '$PROFILE_ACTIVE' has no"
  echo "       served surface to audit, so nothing here needs a URL."
  echo "       Take one back with workflows.enabled in .slopstopper.yml."
  echo ""
fi
echo "  🚀 Deploy stays wherever it is today; this suite doesn't deploy."
echo "       Point urls.production in .slopstopper.yml at the deployed site"
echo "       (including any sub-path) so scheduled and main runs audit it."
echo ""
echo "  If you don't want a check, delete its workflow from .github/workflows/"
echo "  (or set a profile). Re-running this installer won't bring it back"
echo "  (tracked via .ss/.workflows-installed)."
echo ""
sep
echo ""
if [ "$USE_TASK" = "true" ]; then
  echo "  Canonical invocation: task ss:<check>"
  echo ""
  echo "    Workflows are Task-driven by default. They call task ss:<check>,"
  echo "    which is the canonical interface for humans, agents and CI alike,"
  echo "    so the suite sits naturally alongside any other task build / task"
  echo "    deploy tasks in your codebase. task is pinned in mise.toml, so"
  echo "    'mise install' provides it locally and in CI."
  echo ""
  echo "    Don't want Task in your CI? Re-run install.sh with --no-task"
  echo "    to get workflows that call slopstopper-cli directly:"
  echo ""
  echo "       bash install.sh --no-task"
  echo ""
  echo "  Next steps:"
  echo "    1. mise install                 # the pinned task, node, snyk and gitleaks"
  echo "    2. npm install"
  echo "    3. slopstopper doctor           # confirms every tool the checks need is on PATH"
  echo "    4. task --list"
  echo "    5. slopstopper badges           # generate README status badges (paste into README.md)"
  echo "    6. Set the SNYK_TOKEN secret (above), open a PR, and every check runs automatically."
else
  echo "  Installed in --no-task mode."
  echo ""
  echo "    Workflows call slopstopper-cli directly (no Task install step,"
  echo "    no task ss:* invocations). To switch to the canonical Task-driven"
  echo "    flow later, re-run install.sh without --no-task."
  echo ""
  echo "  Next steps:"
  echo "    1. mise install                 # the pinned node, snyk and gitleaks"
  echo "    2. npm install"
  echo "    3. slopstopper doctor           # confirms every tool the checks need is on PATH"
  echo "    4. slopstopper checks list      # see every check shipped"
  echo "    5. slopstopper badges           # generate README status badges (paste into README.md)"
  echo "    6. Set the SNYK_TOKEN secret (above), open a PR, and every check runs automatically."
fi
echo ""
sep
