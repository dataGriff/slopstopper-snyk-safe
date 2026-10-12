"""Code complexity analyzer (Lizard wrapper).

Ports the bash hygiene:complexity flow:

  .ss/scripts/check-tool   (the Taskfile.ss.yml step that installs lizard)
  + python3 -m lizard . ... --csv > complexity-report.csv
  + .ss/scripts/generate-complexity-md.py

into one self-contained check. Subprocess-invokes `python -m lizard`
(using sys.executable so the same Python running the CLI provides
lizard), captures the CSV, parses it, writes both the CSV and the
markdown report.

This is the CLI's first external-tool integration. Subprocess-invoke
boundary is the load-bearing licensing rule (see plan doc): we call
lizard's CLI, never `import lizard`. Adopter installs lizard
themselves (or via the test extra in cli/pyproject.toml's [test]
group, which is what CI does).

The check owns its own pass/fail: any function whose cyclomatic
complexity exceeds `max_ccn` fails it. This is the single source of
truth: `task ss:hygiene:complexity`, the pre-push hook and CI all
inherit this exit code, so behaviour is identical everywhere.

Configuration (.slopstopper.yml, all optional):

    hygiene:
      complexity:
        max_ccn: 15   # CCN ceiling; a function above this fails the check
        exclude: []   # extra path globs to skip, on top of DEFAULT_EXCLUDES

Whatever .gitignore ignores is skipped (`git ls-files --ignored`), so
build output such as `dist/` never counts and a local run sees what CI
sees. Test files are skipped by the common naming conventions in
DEFAULT_EXCLUDES (`*_test.go`, `*.spec.ts`, `tests/` …): the gate is for
production code.

Exit codes:
  0: analysis completed, no function over `max_ccn`
  1: a function exceeds `max_ccn`
  2: lizard is not installed, or arguments were passed (this check
      takes none)
"""

from __future__ import annotations

import csv
import io
import os
import subprocess
import sys
from pathlib import Path

from slopstopper import config, output
from slopstopper.checks import _report, _tools
from slopstopper.checks._contract import reject_extra_args

REPORT_DIR = Path(".ss/reports/complexity")
REPORT_CSV = REPORT_DIR / "complexity-report.csv"
REPORT_MD = REPORT_DIR / "complexity-report.md"

# Consumed by `slopstopper emit hygiene:complexity --target {pr-comment,issue}`.
# The comment_discriminator is the H1 of the generated report. Before the
# flip the JS prepended its own "## 📊 Code Complexity Analysis" heading, but the
# report itself starts with "# Code Complexity Analysis Report", so the
# substring "Code Complexity Analysis" matches the same comment after the
# flip. Issue strings are byte-for-byte identical to the legacy block.
META = {
    "report_path": str(REPORT_MD),
    "comment_discriminator": "Code Complexity Analysis",
    "issue_title": "⚠️ Code Complexity Issues in Main Branch",
    "issue_labels": ["code-complexity", "technical-debt"],
    "issue_followup": "🔔 Code complexity issues detected again in commit",
}

# Test files by the common conventions, plus tooling and vendored trees.
# Matched with fnmatch against repo-relative paths, where `*` also
# crosses `/`, so `*_test.go` catches every Go test file at any depth.
DEFAULT_EXCLUDES = (
    "tests/*", "*/tests/*",
    "test/*", "*/test/*",
    "__tests__/*", "*/__tests__/*",
    "spec/*", "*/spec/*",
    "*_test.go",
    "test_*.py", "*/test_*.py", "*_test.py",
    "*.test.*", "*.spec.*",
    "*_spec.rb",
    "*Test.java", "*Tests.java", "*Test.kt", "*Tests.cs",
    ".ss/*", ".github/*",
    "node_modules/*", "*/node_modules/*",
    "vendor/*",
)

DEFAULT_MAX_CCN = 15

_LIZARD_INSTALL_HELP = (
    "lizard is not installed.\n"
    "Install with:\n"
    "  pip3 install --user lizard\n"
    "  python3 -m pip install --user lizard\n"
    "Note: do NOT install via 'brew install lizard'. That is lz4's lizard,\n"
    "a completely different tool that will shadow the Python package."
)


def _lizard_available() -> bool:
    try:
        result = subprocess.run(
            [sys.executable, "-m", "lizard", "--version"],
            capture_output=True,
            text=True,
            check=False,
        )
        return result.returncode == 0
    except OSError:
        return False


def _excludes() -> tuple[str, ...]:
    extra = config.get("hygiene.complexity.exclude", []) or []
    if not isinstance(extra, list):
        extra = [extra]
    return DEFAULT_EXCLUDES + tuple(str(p).strip() for p in extra if str(p).strip())


def _gitignored() -> list[str]:
    """Paths .gitignore ignores, directories collapsed (`dist/`). Empty outside git."""
    listing = _tools.git_stdout(os.getcwd(), "ls-files", "--others", "--ignored", "--exclude-standard", "--directory")
    return [line for line in (listing or "").splitlines() if line]


def _lizard_patterns(excludes: tuple[str, ...], ignored: list[str]) -> list[str]:
    """lizard -x patterns. lizard matches against `./<path>`, so a pattern that
    doesn't start with `*` is anchored at the root."""
    patterns = [p if p.startswith("*") else f"./{p}" for p in excludes]
    patterns += [f"./{p}*" if p.endswith("/") else f"./{p}" for p in ignored]
    return patterns


def _run_lizard() -> str:
    """Invoke `python -m lizard . -x ... --csv` and return stdout."""
    cmd = [sys.executable, "-m", "lizard", "."]
    for pattern in _lizard_patterns(_excludes(), _gitignored()):
        cmd += ["-x", pattern]
    cmd.append("--csv")
    return subprocess.run(cmd, capture_output=True, text=True, check=False).stdout


def _parse_csv_rows(csv_text: str) -> list[tuple]:
    """Parse lizard's CSV output.

    Columns (0-indexed):
      0 nloc, 1 ccn, 2 tokens, 3 params, 4 length,
      5 location ("function@start-end@./path"),
      6 file path, 7 function name, 8 long name, ...

    Returns [(nloc, ccn, tokens, params, length, location, file), ...].
    Rows with non-numeric leading columns (header rows from fixtures) are
    silently skipped.
    """
    rows: list[tuple] = []
    reader = csv.reader(io.StringIO(csv_text))
    for row in reader:
        if len(row) < 6:
            continue
        try:
            nloc = int(row[0])
            ccn = int(row[1])
            tokens = int(row[2])
            params = int(row[3])
            length = int(row[4])
        except ValueError:
            continue
        location = row[5].strip('"')
        if len(row) > 6 and row[6]:
            file_path = row[6].strip('"')
        elif "@" in location:
            file_path = location.split("@")[-1]
        else:
            file_path = location.split(":")[0]
        rows.append((nloc, ccn, tokens, params, length, location, file_path))
    return rows


def _compute_summary_lines(rows: list[tuple], max_ccn: int) -> list[str]:
    if not rows:
        return [
            "Total NLOC   Avg.NLOC  Avg.CCN  Avg.Tokens  Fun Cnt  Warning Cnt",
            "----------------------------------------------------------------",
            "         0        0.0      0.0         0.0        0            0",
            "",
            "No functions analyzed.",
        ]

    fun_cnt = len(rows)
    total_nloc = sum(r[0] for r in rows)
    avg_nloc = total_nloc / fun_cnt
    avg_ccn = sum(r[1] for r in rows) / fun_cnt
    avg_tokens = sum(r[2] for r in rows) / fun_cnt
    warning_cnt = sum(1 for r in rows if r[1] > max_ccn)

    files = {r[6] for r in rows}
    file_count = len(files)
    file_word = "file" if file_count == 1 else "files"

    return [
        "Total NLOC   Avg.NLOC  Avg.CCN  Avg.Tokens  Fun Cnt  Warning Cnt",
        "----------------------------------------------------------------",
        f"{total_nloc:>10}  {avg_nloc:>9.1f}  {avg_ccn:>7.1f}  {avg_tokens:>10.1f}  {fun_cnt:>7}  {warning_cnt:>11}",
        "",
        f"{file_count} {file_word} analyzed.",
    ]


def _format_summary_section(summary_lines: list[str]) -> str:
    if not summary_lines:
        return ""
    return "```\n" + "\n".join(summary_lines) + "\n```\n\n"


def _format_high_complexity_section(rows: list[tuple], max_ccn: int) -> str:
    high = [r for r in rows if r[1] > max_ccn]
    if not high:
        return f"## ✅ Complexity Status\n\nNo high-complexity items found (all CCN ≤ {max_ccn})\n\n"

    out = f"## ⚠️ High Complexity Items (CCN > {max_ccn})\n\n"
    out += "| NLOC | CCN | Tokens | Params | Length | Location |\n"
    out += "|------|-----|--------|--------|--------|----------|\n"
    for nloc, ccn, tokens, params, length, location, _file in high:
        out += f"| {nloc} | {ccn} | {tokens} | {params} | {length} | `{location}` |\n"
    return out


def _build_md_report(rows: list[tuple], max_ccn: int) -> str:
    summary = _compute_summary_lines(rows, max_ccn)
    md = "# Code Complexity Analysis Report\n\n"
    md += f"**Generated**: {_report.generated_at()}\n\n"
    md += "## Summary\n\n"
    md += _format_summary_section(summary)
    md += _format_high_complexity_section(rows, max_ccn)
    md += "## Guidelines\n\n"
    md += "- **Cyclomatic Complexity (CCN)**: Measure of code complexity based on decision points\n"
    md += f"  - **Threshold**: any function with CCN > {max_ccn} fails the check and must be refactored\n"
    md += "  - Tune the ceiling with `hygiene.complexity.max_ccn` in `.slopstopper.yml`\n\n"
    md += "- **Function Length (NLOC)**: Number of lines of code\n"
    md += "  - Target: Keep functions under 50 lines\n"
    md += "  - For this static template, code should be minimal\n\n"
    md += "## More Information\n\n"
    md += "- Generated by [Lizard](http://www.lizard.ws/)\n"
    md += "- Reports location: `.ss/reports/complexity/`\n"
    md += "  - `complexity-report.md` (this file)\n"
    md += "  - `complexity-report.csv` (machine-readable)\n"
    return md


def run(args: list[str] | None = None) -> int:
    if args:
        return reject_extra_args("hygiene:complexity", args)
    if not _lizard_available():
        output.error(_LIZARD_INSTALL_HELP)
        return 2

    max_ccn = config.get_int("hygiene.complexity.max_ccn", DEFAULT_MAX_CCN)

    output.running("Analyzing code complexity…")
    REPORT_DIR.mkdir(parents=True, exist_ok=True)

    csv_text = _run_lizard()
    REPORT_CSV.write_text(csv_text)

    rows = _parse_csv_rows(csv_text)
    REPORT_MD.write_text(_build_md_report(rows, max_ccn))

    high_count = sum(1 for r in rows if r[1] > max_ccn)
    output.footer(REPORT_DIR, [REPORT_MD.name, REPORT_CSV.name])
    if high_count:
        output.error(f"{high_count} function(s) exceed CCN {max_ccn}. Refactor them before merging.")
        return 1
    output.success(f"No high-complexity functions found (all CCN ≤ {max_ccn}).")
    return 0
