"""Documentation size monitor.

Ports .ss/scripts/check-docs-size.sh + generate-docs-size-report.py into
one self-contained check. Writes .ss/reports/docs/docs-size-report.md
and prints a coloured summary to stdout. It always exits 0, because
alerts are reported, not enforced.

Configuration (.slopstopper.yml, all keys optional):

    hygiene:
      docs_size:
        max_total_size_kb: 150   # total .md under docs/ (excl. hygiene.docs.exclude)
        max_file_size_kb: 20     # largest single doc
        max_files: 25            # total doc count

Docs matching `hygiene.docs.exclude` (default `docs/archive/**`, see
_docs.py) are left out and listed in the report.

See .slopstopper.yml.example for the canonical schema.

Exit codes:
  0: always; the thresholds are advisory and the report carries the
      verdict (`❌ Status: THRESHOLDS EXCEEDED`), never the exit code
  2: arguments were passed (this check takes none)
"""

from __future__ import annotations

from pathlib import Path

from slopstopper import config, output
from slopstopper.checks import _docs, _report
from slopstopper.checks._contract import reject_extra_args

DOCS_DIR = Path("docs")
REPORT_DIR = Path(".ss/reports/docs")
REPORT_FILE = REPORT_DIR / "docs-size-report.md"

# Consumed by `slopstopper emit hygiene:docs-size --target {pr-comment,issue}`.
# Strings are byte-for-byte identical to the discriminator / title / labels
# the legacy actions/github-script@v7 block in
# .github/workflows/ss-hygiene-docs-size-check.yml used, so the same bot
# comments/issues are matched after the workflow flip.
META = {
    "report_path": str(REPORT_FILE),
    "comment_discriminator": "📚 Documentation Size Report",
    "issue_title": "📚 Documentation Size Exceeds Thresholds",
    "issue_labels": ["documentation-size", "maintenance"],
    "issue_followup": "🔔 Documentation size thresholds exceeded again in commit",
}

DEFAULT_MAX_TOTAL_SIZE_KB = 150
DEFAULT_MAX_FILE_SIZE_KB = 20
DEFAULT_MAX_FILES = 25

def _split_doc_files(docs_dir: Path = DOCS_DIR) -> tuple[list[Path], list[Path]]:
    """`(scanned, excluded)` markdown under docs_dir."""
    if not docs_dir.exists():
        return [], []
    return _docs.split(sorted(docs_dir.rglob("*.md")))



def _load_thresholds() -> dict[str, int]:
    return {
        "max_total_size_kb": config.get_int("hygiene.docs_size.max_total_size_kb", DEFAULT_MAX_TOTAL_SIZE_KB),
        "max_file_size_kb": config.get_int("hygiene.docs_size.max_file_size_kb", DEFAULT_MAX_FILE_SIZE_KB),
        "max_files": config.get_int("hygiene.docs_size.max_files", DEFAULT_MAX_FILES),
    }


def _compute_stats(files_with_size: list[tuple[Path, int]]) -> dict[str, int]:
    file_count = len(files_with_size)
    total_size = sum(size for _, size in files_with_size)
    return {
        "file_count": file_count,
        "total_size_bytes": total_size,
        "total_size_kb": total_size // 1024,
        "estimated_tokens": total_size // 4,
        "avg_size_kb": (total_size // file_count // 1024) if file_count else 0,
    }


def _format_largest(files_with_size: list[tuple[Path, int]]) -> str:
    top5 = sorted(files_with_size, key=lambda fs: fs[1], reverse=True)[:5]
    return "\n".join(f"{p} ({size // 1024} KB)" for p, size in top5)


def _compute_alerts(
    stats: dict[str, int],
    files_with_size: list[tuple[Path, int]],
    thresholds: dict[str, int],
) -> list[str]:
    parts: list[str] = []
    if stats["total_size_kb"] > thresholds["max_total_size_kb"]:
        parts.append(
            f"⚠️  Total documentation size ({stats['total_size_kb']} KB) "
            f"exceeds threshold ({thresholds['max_total_size_kb']} KB)"
        )
    if stats["file_count"] > thresholds["max_files"]:
        parts.append(
            f"⚠️  Number of documentation files ({stats['file_count']}) "
            f"exceeds threshold ({thresholds['max_files']})"
        )
    oversized = [p for p, size in files_with_size if size > thresholds["max_file_size_kb"] * 1024]
    if oversized:
        oversized_lines = "\n".join(f"     - {p.name}" for p in oversized)
        parts.append(f"⚠️  Files exceeding {thresholds['max_file_size_kb']} KB:\n{oversized_lines}")
    return parts


def _build_report_md(
    *,
    stats: dict[str, int],
    largest_files: str,
    alerts: list[str],
    thresholds: dict[str, int],
    generated_at: str | None = None,
    excluded: list[Path] | None = None,
) -> str:
    has_alerts = bool(alerts)
    alerts_str = ("\n".join(alerts) + "\n") if has_alerts else ""
    status_line = (
        "❌ Documentation size exceeds thresholds!"
        if has_alerts
        else "✅ Documentation size within acceptable limits"
    )
    generated = generated_at or _report.generated_at()

    report = (
        f"# 📚 Documentation Size Report\n"
        f"\n"
        f"**Generated:** {generated}\n"
        f"\n"
        f"## Summary\n"
        f"\n"
        f"- **Total Files:** {stats['file_count']}\n"
        f"- **Total Size:** {stats['total_size_kb']} KB\n"
        f"- **Estimated Tokens:** ~{stats['estimated_tokens']}\n"
        f"- **Average File Size:** {stats['avg_size_kb']} KB\n"
        f"\n"
        f"## Largest Files\n"
        f"\n"
        f"```\n"
        f"{largest_files}\n"
        f"```\n"
        f"\n"
        f"## Status\n"
        f"\n"
        f"{status_line}\n"
        f"\n"
    )
    if has_alerts:
        report += f"### Alerts\n\n{alerts_str}\n"
    report += _docs.report_section(excluded or [])
    report += (
        f"## Thresholds\n"
        f"\n"
        f"- **Max Total Size:** {thresholds['max_total_size_kb']} KB\n"
        f"- **Max File Size:** {thresholds['max_file_size_kb']} KB\n"
        f"- **Max File Count:** {thresholds['max_files']}\n"
        f"\n"
        f"## Recommendations\n"
        f"\n"
        f"If thresholds are exceeded:\n"
        f"- Consider consolidating related documentation\n"
        f"- Move historical/completed content to archive\n"
        f"- Split large files into focused topics\n"
        f"- Remove redundant information\n"
        f"\n"
        f"---\n"
        f"\n"
        f"*This report was generated by the Documentation Size Monitor.*\n"
    )
    return report


def _blue(value: object, unit: str = "") -> str:
    """A figure in the summary: blue on a terminal, bare text elsewhere."""
    text = f"{value} {unit}" if unit else str(value)
    return output.colour(text, "blue")


def _print_terminal_summary(
    stats: dict[str, int],
    largest_files: str,
    alerts: list[str],
    thresholds: dict[str, int],
) -> None:
    # Everything goes through the shared output module, so --quiet
    # suppresses all of it and colour only reaches a real terminal.
    output.blank()
    output.status("📚", "Documentation Size Monitor")
    output.separator()
    output.blank()
    output.status("📊", "Documentation Statistics:")
    output.separator()
    output._emit(f"  Total Files:        {_blue(stats['file_count'])}")
    output._emit(f"  Total Size:         {_blue(stats['total_size_kb'], 'KB')}")
    output._emit(f"  Estimated Tokens:   {_blue('~' + str(stats['estimated_tokens']))}")
    output._emit(f"  Average File Size:  {_blue(stats['avg_size_kb'], 'KB')}")
    output.blank()
    output.status("📈", "Largest Files:")
    output.separator()
    output._emit(largest_files)
    output.blank()
    output.status("📋", "Threshold Limits:")
    output.separator()
    output._emit(f"  Max Total Size:     {_blue(thresholds['max_total_size_kb'], 'KB')}")
    output._emit(f"  Max File Size:      {_blue(thresholds['max_file_size_kb'], 'KB')}")
    output._emit(f"  Max File Count:     {_blue(thresholds['max_files'])}")
    output.blank()
    if alerts:
        output._emit(output.colour("❌ Status: THRESHOLDS EXCEEDED", "red"), force=True)
        output.blank()
        output.status("⚠️", "Alerts:")
        output.separator()
        output._emit("\n".join(alerts))
    else:
        output._emit(output.colour("✅ Status: Within acceptable limits", "green"))
    output.blank()


def run(args: list[str] | None = None) -> int:
    if args:
        return reject_extra_args("hygiene:docs-size", args)
    thresholds = _load_thresholds()
    scanned, excluded = _split_doc_files()
    files_with_size = [(p, p.stat().st_size) for p in scanned]
    stats = _compute_stats(files_with_size)
    largest_files = _format_largest(files_with_size)
    alerts = _compute_alerts(stats, files_with_size, thresholds)

    _print_terminal_summary(stats, largest_files, alerts, thresholds)
    if excluded:
        output.status("🚫", f"{len(excluded)} doc(s) excluded by hygiene.docs.exclude (listed in the report)")

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_FILE.write_text(_build_report_md(
        stats=stats,
        largest_files=largest_files,
        alerts=alerts,
        thresholds=thresholds,
        excluded=excluded,
    ))

    output.footer(REPORT_DIR, [f"{REPORT_FILE.name} (human-readable)"])
    return 0
