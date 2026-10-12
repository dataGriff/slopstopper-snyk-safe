"""Documentation accuracy validator.

Ports .ss/scripts/check-docs-accuracy.py + generate-docs-accuracy-md.py.
Scans docs/**/*.md plus the four repo-root entry files (README.md,
AGENTS.md, CLAUDE.md, CONTRIBUTING.md) for four kinds of drift:

  - broken_link:        markdown link target missing on disk
  - stale_task_ref:     `task <namespace:name>` doesn't match Taskfile
  - stale_workflow_ref: .github/workflows/<file> doesn't exist
  - stale_file_ref:     backtick-quoted filepath not found on disk: not at
                        the repo root, not next to the doc, and no file in
                        the repo (git ls-files) has that name or ends with
                        that path

Optionally scans more files, with only the checks that are precise for
them: `stale_task_ref` and `stale_workflow_ref` for markdown, and for
both markdown and HTML a fifth kind:

  - broken_repo_link:   a `github.com/<this repo>/blob|tree/<ref>/<path>`
                        link whose path does not exist. For the default
                        or current branch that means the working tree
                        (what the branch holds once this change lands);
                        for a tag, SHA or other branch git knows, the
                        path at that ref; a ref this checkout can't
                        resolve is not checked rather than guessed at

That is how a marketing page quoting a script that was deleted two
releases ago gets caught, which the docs/-only scan never could. The
relative-link and backtick-path checks are not applied outside docs/:
a skill or a site page describes an adopter's tree, not this one.

Configuration (.slopstopper.yml, all optional):

    hygiene:
      docs_accuracy:
        extra_paths: []   # repo-relative globs, e.g. [app/*.html, .claude/skills/**/*.md]

Docs matching `hygiene.docs.exclude` (default `docs/archive/**`, see
_docs.py) are not scanned, whether the docs/ pass or extra_paths would
have reached them, and the report lists every one it left out.

Writes a JSON report (machine-readable) and a Markdown report (human-
readable).

Exit codes:
  0: clean
  1: issues
  2: docs/ missing (nothing to check), or arguments were passed
      (this check takes none)
"""

from __future__ import annotations

import json
import os
import re
from functools import lru_cache
from pathlib import Path

from slopstopper import config, output
from slopstopper.badges import detect_owner_repo
from slopstopper.checks import _docs, _report, _tools
from slopstopper.checks._contract import reject_extra_args

REPORT_DIR = Path(".ss/reports/docs")
REPORT_JSON = REPORT_DIR / "docs-accuracy-report.json"
REPORT_MD = REPORT_DIR / "docs-accuracy-report.md"

# Consumed by `slopstopper emit hygiene:docs-accuracy --target {pr-comment,issue}`.
# Discriminator matches the H1 of the report ("# 🔎 Documentation Accuracy Report")
# so the same bot comment is reused after the workflow flip. Issue title,
# labels, and follow-up string are byte-identical to the legacy block. The
# close behaviour is also driven by emit (`--on-pass=close`) since PR 1 of
# the issue-emission unification, and close_comment matches the legacy YAML
# message so adopters see the same friendly recovery line.
META = {
    "report_path": str(REPORT_MD),
    "comment_discriminator": "🔎 Documentation Accuracy",
    "issue_title": "🔎 Documentation Accuracy Issues",
    "issue_labels": ["documentation-accuracy", "documentation"],
    "issue_followup": "🔔 Documentation accuracy issues detected again",
    "issue_close_comment": "✅ Documentation accuracy checks now pass. Closing automatically.",
}

DOCS_DIR = Path("docs")
ROOT_ENTRY_FILES = ("README.md", "AGENTS.md", "CLAUDE.md", "CONTRIBUTING.md")

# Refs that look like file paths but represent something else:
_GITHUB_WEB_PATH_SEGMENTS = frozenset({
    "issues", "discussions", "pulls", "milestones", "projects", "wiki",
    "pulse", "graphs", "settings", "compare", "tree", "blob", "raw",
    "blame", "commits", "tags", "releases", "packages", "actions",
})

_TRACKED_EXTENSIONS = frozenset(
    (".html", ".js", ".css", ".json", ".yml", ".yaml", ".toml", ".py", ".sh", ".md")
)
_SKIP_REF_PREFIXES = ("http", "mailto", "/")
_PLACEHOLDER_SUBS = ("example", "your-", "<", "YYYY")

# Phrases that indicate a backtick-quoted file ref is an example or
# suggestion, not a real path. Used to suppress false positives in tutorial
# prose and "how to add a new check" instructions.
_SUGGESTION_CTX = re.compile(
    r'(split into|could create|create a|for example|e\.g\.|such as|'
    r'examples?:|add\b.*\bhere|when.*needed|naming|format|add a\b|generat\w*)',
    re.IGNORECASE,
)

_TASKFILE_TASK_RE = re.compile(r"^  ([a-z][a-z0-9:_-]+):", re.MULTILINE)
_MD_LINK_RE = re.compile(r'\[([^\]]*)\]\(([^)]+)\)')
# A leading backtick counts as a word boundary: docs write `task ss:x`,
# and requiring whitespace before `task` silently exempted every one.
# The trailing lookahead stops `task ss:hygiene:*` (a wildcard in prose)
# from half-matching as `ss:hygiene:`.
# `task <ns:name>` after any non-word character (start of line, space,
# backtick, `(`, `"`). The name never ends in `:`, and a name followed by
# `:`, `*`, `<` or `{` is a placeholder (`ss:hygiene:<check>`), not a ref.
_TASK_REF_RE = re.compile(r'(?<![\w-])task\s+([a-z][a-z0-9_-]*(?::[a-z0-9_-]+)+)(?![*:\w<{-])')
_WF_REF_RE = re.compile(r'\.github/workflows/([a-z0-9_.-]+\.(?:yml|md))')
_BACKTICK_FILE_RE = re.compile(r'`([a-zA-Z0-9_./-]+\.[a-z]{1,4})`')


def _get_taskfile_tasks() -> set[str]:
    tasks: set[str] = set()
    root = Path("Taskfile.yml")
    if root.exists():
        tasks |= {m.group(1) for m in _TASKFILE_TASK_RE.finditer(root.read_text())}
    ss = Path("Taskfile.ss.yml")
    if ss.exists():
        tasks |= {"ss:" + m.group(1) for m in _TASKFILE_TASK_RE.finditer(ss.read_text())}
    return tasks


def _get_workflow_files() -> set[str]:
    wf_dir = Path(".github/workflows")
    if not wf_dir.is_dir():
        return set()
    return {f.name for f in wf_dir.iterdir() if f.is_file()}


def _line_of(content: str, position: int) -> int:
    return content[:position].count("\n") + 1


def _check_broken_links(md_path: Path) -> list[dict]:
    issues: list[dict] = []
    content = md_path.read_text()
    base = md_path.parent
    for m in _MD_LINK_RE.finditer(content):
        display, target = m.group(1), m.group(2)
        if target.startswith(("http://", "https://", "mailto:", "#")):
            continue
        target_path = target.split("?")[0].split("#")[0]
        if not target_path:
            continue
        if any(part in _GITHUB_WEB_PATH_SEGMENTS for part in Path(target_path).parts):
            continue
        resolved = (base / target_path).resolve()
        if not resolved.exists():
            issues.append({
                "type": "broken_link",
                "file": str(md_path),
                "line": _line_of(content, m.start()),
                "message": f"Broken link: [{display}]({target}) points to a path that does not exist",
            })
    return issues


def _check_task_references(md_path: Path, valid_tasks: set[str]) -> list[dict]:
    issues: list[dict] = []
    content = md_path.read_text()
    for m in _TASK_REF_RE.finditer(content):
        task_name = m.group(1)
        if task_name not in valid_tasks:
            issues.append({
                "type": "stale_task_ref",
                "file": str(md_path),
                "line": _line_of(content, m.start()),
                "message": f"Task reference `task {task_name}` not found in Taskfile.yml",
            })
    return issues


def _check_workflow_references(md_path: Path, valid_workflows: set[str]) -> list[dict]:
    issues: list[dict] = []
    content = md_path.read_text()
    for m in _WF_REF_RE.finditer(content):
        wf_name = m.group(1)
        if wf_name not in valid_workflows:
            issues.append({
                "type": "stale_workflow_ref",
                "file": str(md_path),
                "line": _line_of(content, m.start()),
                "message": f"Workflow reference `.github/workflows/{wf_name}` does not exist",
            })
    return issues


def _ref_is_placeholder_or_external(ref: str, ext: str) -> bool:
    if ref.startswith(_SKIP_REF_PREFIXES):
        return True
    if ext not in _TRACKED_EXTENSIONS:
        return True
    if any(sub in ref for sub in _PLACEHOLDER_SUBS):
        return True
    if ext in (".yml", ".yaml") and "/" not in ref:
        return True
    return False


@lru_cache(maxsize=None)
def _repo_files(root: str) -> tuple[frozenset[str], tuple[str, ...]]:
    """(basenames, paths) of the files git knows about under `root`, tracked
    or untracked-but-not-ignored. Empty outside a git checkout."""
    listing = _tools.git_stdout(root, "ls-files", "--cached", "--others", "--exclude-standard")
    paths = tuple(line for line in (listing or "").splitlines() if line)
    return frozenset(p.rsplit("/", 1)[-1] for p in paths), paths


def _exists_in_repo(ref: str) -> bool:
    """Prose names files loosely: "each pack has a `pack.json`", "run
    `render.sh`". A bare name resolves if any file in the repo has it, and
    a partial path if any file's path ends with it."""
    ref = ref.removeprefix("./")
    basenames, paths = _repo_files(os.getcwd())
    if "/" not in ref:
        return ref in basenames
    return any(p == ref or p.endswith("/" + ref) for p in paths)


def _should_skip_file_ref(ref: str, ext: str, base: Path, content: str, m: re.Match) -> bool:
    if _ref_is_placeholder_or_external(ref, ext):
        return True
    if Path(ref).exists() or (base / ref).exists() or _exists_in_repo(ref):
        return True
    start = max(0, m.start() - 250)
    end = min(len(content), m.end() + 250)
    return bool(_SUGGESTION_CTX.search(content[start:end]))


def _check_source_file_references(md_path: Path) -> list[dict]:
    issues: list[dict] = []
    content = md_path.read_text()
    base = md_path.parent
    for m in _BACKTICK_FILE_RE.finditer(content):
        ref = m.group(1)
        ext = Path(ref).suffix
        if _should_skip_file_ref(ref, ext, base, content, m):
            continue
        issues.append({
            "type": "stale_file_ref",
            "file": str(md_path),
            "line": _line_of(content, m.start()),
            "message": f"Possible stale file reference: `{ref}` not found on disk",
        })
    return issues


def _collect_targets(docs: list[Path] | None = None) -> list[Path]:
    targets = list(docs) if docs is not None else _docs.split(sorted(DOCS_DIR.rglob("*.md")))[0]
    for entry in ROOT_ENTRY_FILES:
        entry_path = Path(entry)
        if entry_path.exists():
            targets.append(entry_path)
    return targets


def _is_repo_relative_glob(text: str) -> bool:
    return bool(text) and not (
        text.startswith(("/", "~"))
        or ":" in text.split("/", 1)[0]
        or ".." in Path(text).parts
    )


def _glob_extra_pattern(pattern: object, root: Path) -> list[Path]:
    """Files one `extra_paths` entry names inside the repo. Warns and returns
    [] for an entry that isn't a usable repo-relative glob, or matches nothing."""
    text = str(pattern).strip() if pattern is not None else ""
    if not _is_repo_relative_glob(text):
        output.warn(
            f"hygiene.docs_accuracy.extra_paths entry {pattern!r} is not a "
            "repo-relative glob, so it is ignored"
        )
        return []
    try:
        hits = sorted(Path(".").glob(text))
    except (ValueError, NotImplementedError) as exc:
        output.warn(f"hygiene.docs_accuracy.extra_paths entry {pattern!r} is invalid ({exc}), so it is ignored")
        return []
    hits = [h for h in hits if h.is_file() and h.resolve().is_relative_to(root)]
    if not hits:
        output.warn(f"hygiene.docs_accuracy.extra_paths entry {pattern!r} matched no files")
    return hits


def _collect_extra_targets(
    primary_targets: list[Path] | None = None, excluded_out: list[Path] | None = None
) -> list[Path]:
    """Files named by `hygiene.docs_accuracy.extra_paths`, deduplicated, in order.

    Files the primary scan already covers (`docs/**` and the root entry
    files) are left out, so an overlapping glob like `**/*.md` never
    double-counts an issue; files `hygiene.docs.exclude` names are left
    out too, and appended to `excluded_out` so the report can list them.
    An entry that is empty, absolute, climbs out of the repo with `..`, or
    matches nothing warns and is skipped. That matches the config.py
    contract that a bad value never surfaces as a traceback or as a
    silent pass.
    """
    primary = set(primary_targets if primary_targets is not None else _collect_targets())
    excludes = _docs.exclude_patterns()
    root = Path.cwd().resolve()
    raw = config.get("hygiene.docs_accuracy.extra_paths", []) or []
    patterns = raw if isinstance(raw, list) else [raw]
    out: list[Path] = []
    for pattern in patterns:
        for hit in _glob_extra_pattern(pattern, root):
            if hit in primary or hit in out:
                continue
            if _docs.is_excluded(hit, excludes):
                if excluded_out is not None and hit not in excluded_out:
                    excluded_out.append(hit)
                continue
            out.append(hit)
    return out


# Matches `https://github.com/<owner>/<repo>/blob|tree/<ref>/<path>`, the
# shape every "View source →" link on a site takes. Only links into *this* repo
# are checked; anyone else's paths are not ours to verify.
_REPO_LINK_RE = re.compile(
    r"""https://github\.com/([^/"'\s]+)/([^/"'\s]+)/(?:blob|tree)/([^"'`\s#?)<>]+)"""
)
# A bare URL at the end of a sentence carries the sentence's punctuation.
_LINK_TRAILING_PUNCT = ".,;:!"


_git = _tools.git_stdout


def _working_tree_refs() -> set[str]:
    """Refs whose links are checked against the working tree.

    A link to `blob/main/...` is a claim about what main will hold once
    this change lands, which is the working tree, not main's current
    commit. Same for the branch being worked on.
    """
    cwd = str(Path.cwd())
    refs = {"main", "master"}
    origin_head = _git(cwd, "symbolic-ref", "--short", "refs/remotes/origin/HEAD")
    if origin_head:
        refs.add(origin_head.split("/", 1)[-1])
    current = _git(cwd, "rev-parse", "--abbrev-ref", "HEAD")
    if current and current != "HEAD":
        refs.add(current)
    for var in ("GITHUB_BASE_REF", "GITHUB_HEAD_REF"):
        if os.environ.get(var):
            refs.add(os.environ[var])
    return refs


def _repo_link_resolves(ref_and_path: str) -> bool | None:
    """Whether `<ref>/<path>` from a blob/tree URL points at something real.

    True / False when it can tell; None when the ref is one this checkout
    can't resolve (the link is then not checked, because guessing
    produced both false passes and false failures).

    A ref can contain slashes (`feat/x`), so the ref/path boundary is
    ambiguous from the URL alone. It is never resolved by "does any
    suffix of the path exist": `docs/gone/README.md` would then pass on
    the root README.md.
    """
    # 1. The default or current branch: the working tree.
    for ref in sorted(_working_tree_refs(), key=len, reverse=True):
        if ref_and_path.startswith(ref + "/"):
            return Path(ref_and_path[len(ref) + 1:]).exists()
    # 2. A tag, SHA or other branch git knows: the path at that ref.
    cwd = str(Path.cwd())
    parts = ref_and_path.split("/")
    for i in range(1, len(parts)):
        ref, path = "/".join(parts[:i]), "/".join(parts[i:])
        for candidate in (ref, f"origin/{ref}"):
            if _git(cwd, "rev-parse", "--verify", "--quiet", f"{candidate}^{{commit}}") is not None:
                return _git(cwd, "cat-file", "-e", f"{candidate}:{path}") is not None
    return None


def _check_repo_links(path: Path, owner: str, repo: str) -> list[dict]:
    issues: list[dict] = []
    content = path.read_text(errors="replace")
    for m in _REPO_LINK_RE.finditer(content):
        link_owner, link_repo, ref_and_path = m.groups()
        # GitHub owner and repo names are case-insensitive.
        if (link_owner.lower(), link_repo.removesuffix(".git").lower()) != (owner.lower(), repo.lower()):
            continue
        ref_and_path = ref_and_path.rstrip(_LINK_TRAILING_PUNCT).rstrip("/")
        if "/" not in ref_and_path:
            continue  # a bare ref (`tree/main`) names no file; nothing to check
        if _repo_link_resolves(ref_and_path) is not False:
            continue  # resolves, or the ref can't be checked from here
        target = ref_and_path.split("/", 1)[1]
        issues.append({
            "type": "broken_repo_link",
            "file": str(path),
            "line": _line_of(content, m.start()),
            "message": f"Link into this repo points at `{target}`, which does not exist",
        })
    return issues


def _collect_all_issues(targets: list[Path], valid_tasks: set[str], valid_workflows: set[str]) -> list[dict]:
    issues: list[dict] = []
    for md_file in targets:
        issues += _check_broken_links(md_file)
        issues += _check_task_references(md_file, valid_tasks)
        issues += _check_workflow_references(md_file, valid_workflows)
        issues += _check_source_file_references(md_file)
    return issues


def _collect_extra_issues(
    extra: list[Path], valid_tasks: set[str], valid_workflows: set[str]
) -> list[dict]:
    """Route each extra file to the checks that are precise for it.

    Files outside docs/ get only the checks whose references name a real
    target in *this* repo: `task ss:…`, `.github/workflows/…`, and links
    into this repo on github.com. They deliberately do not get the
    relative-link or backtick-path checks, because a skill or a marketing
    page describes an adopter's tree, so `vercel.json` or `[category/](category/)`
    in it is an example, not a claim about this checkout, and flagging
    those would train people to ignore the check.
    """
    supported = {".md", ".html", ".htm"}
    files = [p for p in extra if p.suffix.lower() in supported]
    skipped = [p for p in extra if p.suffix.lower() not in supported]
    if skipped:
        sample = ", ".join(str(p) for p in skipped[:3]) + (" …" if len(skipped) > 3 else "")
        output.warn(
            f"{len(skipped)} file(s) matched hygiene.docs_accuracy.extra_paths but only "
            f".md and .html are scanned. Skipped: {sample}"
        )
    issues: list[dict] = []
    for path in files:
        if path.suffix.lower() == ".md":
            issues += _check_task_references(path, valid_tasks)
            issues += _check_workflow_references(path, valid_workflows)
    if not files:
        return issues
    owner, repo = detect_owner_repo()
    if not owner or not repo:
        output.warn(
            "could not detect this repo's owner/name (no $GITHUB_REPOSITORY, no github.com "
            f"remote), so links into this repo in {len(files)} extra file(s) were not checked"
        )
        return issues
    for path in files:
        issues += _check_repo_links(path, owner, repo)
    return issues


_TYPE_LABELS = {
    "broken_link": "Broken Internal Links",
    "stale_task_ref": "Stale Taskfile References",
    "stale_workflow_ref": "Stale Workflow References",
    "stale_file_ref": "Possible Stale File References",
    "broken_repo_link": "Broken Links Into This Repository",
}


def _build_md_report(data: dict, generated_at: str) -> str:
    issues = data.get("issues", [])
    is_clean = data.get("clean", True)

    lines = [
        "# 🔎 Documentation Accuracy Report",
        "",
        f"**Generated:** {generated_at}",
        "",
        "## Status",
        "",
    ]
    if is_clean:
        lines.append("✅ All documentation accuracy checks passed. No stale references detected.")
    else:
        lines.append(f"⚠️ Found **{len(issues)}** accuracy issue(s) that may need attention.")

    lines += ["", "## Issues", ""]

    if not issues:
        lines.append("No issues found.")
    else:
        by_type: dict[str, list[dict]] = {}
        for issue in issues:
            by_type.setdefault(issue["type"], []).append(issue)
        for itype, group in by_type.items():
            label = _TYPE_LABELS.get(itype, itype)
            lines.append(f"### {label}")
            lines.append("")
            for item in group:
                lines.append(f"- **{item['file']}** (line {item['line']}): {item['message']}")
            lines.append("")

    excluded = _docs.report_section([Path(p) for p in data.get("excluded", [])])
    if excluded:
        lines += ["", excluded.rstrip("\n"), ""]
    lines += [
        "## How to Fix",
        "",
        "1. Review each issue above.",
        "2. Update the referenced docs to match the current project state.",
        "3. Run `task ss:hygiene:docs-accuracy` locally to verify fixes.",
        "",
    ]
    return "\n".join(lines) + "\n"


def run(args: list[str] | None = None) -> int:
    if args:
        return reject_extra_args("hygiene:docs-accuracy", args)
    output.running("Checking documentation accuracy…")

    if not DOCS_DIR.is_dir():
        output.error("docs/ directory not found")
        return 2

    valid_tasks = _get_taskfile_tasks()
    valid_workflows = _get_workflow_files()
    docs, excluded = _docs.split(sorted(DOCS_DIR.rglob("*.md")))
    targets = _collect_targets(docs)
    issues = _collect_all_issues(targets, valid_tasks, valid_workflows)
    extra = _collect_extra_targets(targets, excluded_out=excluded)
    issues += _collect_extra_issues(extra, valid_tasks, valid_workflows)
    if excluded:
        output.status("🚫", f"{len(excluded)} doc(s) excluded by hygiene.docs.exclude (listed in the report)")

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    data = {
        "issues": issues,
        "issue_count": len(issues),
        "clean": len(issues) == 0,
        "excluded": [p.as_posix() for p in excluded],
    }
    REPORT_JSON.write_text(json.dumps(data, indent=2))
    REPORT_MD.write_text(_build_md_report(data, _report.generated_at()))

    if issues:
        output.warn(f"Found {len(issues)} accuracy issue(s)")
        for issue in issues:
            output._emit(f"  {issue['file']}:{issue['line']}: {issue['message']}")
        output.footer(REPORT_DIR, [REPORT_MD.name])
        return 1
    output.success("Documentation accuracy checks passed")
    output.footer(REPORT_DIR, [REPORT_MD.name])
    return 0
