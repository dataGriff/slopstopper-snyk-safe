"""Tests for the hygiene:docs-size check."""

from __future__ import annotations

from pathlib import Path

from slopstopper.checks import docs_size


def test_split_doc_files_finds_markdown(docs_tree):
    docs_tree({"a.md": "alpha", "sub/b.md": "beta", "c.txt": "ignored"})
    found = docs_size._split_doc_files(Path("docs"))[0]
    names = sorted(p.name for p in found)
    assert names == ["a.md", "b.md"]


def test_split_doc_files_excludes_archive(docs_tree):
    docs_tree({"a.md": "alpha", "archive/old.md": "archived"})
    found = docs_size._split_doc_files(Path("docs"))[0]
    rels = sorted(str(p) for p in found)
    assert rels == ["docs/a.md"]


def test_docs_exclude_replaces_the_archive_default(docs_tree, write_config):
    write_config("hygiene:\n  docs:\n    exclude: [docs/course/**]\n")
    docs_tree({"a.md": "alpha", "course/01/lesson.md": "lesson", "archive/old.md": "archived"})
    rels = sorted(str(p) for p in docs_size._split_doc_files(Path("docs"))[0])
    assert rels == ["docs/a.md", "docs/archive/old.md"]


def test_run_lists_excluded_docs_in_the_report(docs_tree, write_config):
    write_config("hygiene:\n  docs:\n    exclude: [docs/course/**]\n")
    docs_tree({"a.md": "alpha", "course/lesson.md": "lesson"})
    assert docs_size.run() == 0
    report = docs_size.REPORT_FILE.read_text()
    assert "**Total Files:** 1" in report
    assert "## Excluded" in report
    assert "`docs/course/lesson.md`" in report


def test_split_doc_files_missing_dir_returns_empty(isolated_cwd):
    assert docs_size._split_doc_files(Path("nonexistent"))[0] == []


def test_compute_stats_basic():
    files = [(Path("a.md"), 2048), (Path("b.md"), 4096)]
    stats = docs_size._compute_stats(files)
    assert stats["file_count"] == 2
    assert stats["total_size_bytes"] == 6144
    assert stats["total_size_kb"] == 6
    assert stats["estimated_tokens"] == 1536
    assert stats["avg_size_kb"] == 3


def test_compute_stats_empty():
    stats = docs_size._compute_stats([])
    assert stats["file_count"] == 0
    assert stats["total_size_kb"] == 0
    assert stats["avg_size_kb"] == 0


def test_format_largest_top5_sorted_desc():
    files = [
        (Path("docs/a.md"), 1024),
        (Path("docs/b.md"), 5120),
        (Path("docs/c.md"), 3072),
        (Path("docs/d.md"), 8192),
        (Path("docs/e.md"), 2048),
        (Path("docs/f.md"), 6144),
    ]
    out = docs_size._format_largest(files)
    lines = out.splitlines()
    assert len(lines) == 5
    assert lines[0] == "docs/d.md (8 KB)"
    assert lines[1] == "docs/f.md (6 KB)"
    assert lines[2] == "docs/b.md (5 KB)"


def test_compute_alerts_clean():
    stats = {"total_size_kb": 50, "file_count": 5}
    thresholds = {"max_total_size_kb": 150, "max_file_size_kb": 20, "max_files": 25}
    files = [(Path("docs/a.md"), 1024)]
    assert docs_size._compute_alerts(stats, files, thresholds) == []


def test_compute_alerts_total_size_exceeded():
    stats = {"total_size_kb": 200, "file_count": 5}
    thresholds = {"max_total_size_kb": 150, "max_file_size_kb": 20, "max_files": 25}
    alerts = docs_size._compute_alerts(stats, [], thresholds)
    assert any("Total documentation size" in a for a in alerts)


def test_compute_alerts_file_count_exceeded():
    stats = {"total_size_kb": 50, "file_count": 30}
    thresholds = {"max_total_size_kb": 150, "max_file_size_kb": 20, "max_files": 25}
    alerts = docs_size._compute_alerts(stats, [], thresholds)
    assert any("Number of documentation files (30)" in a for a in alerts)


def test_compute_alerts_oversized_file_listed_by_basename():
    stats = {"total_size_kb": 50, "file_count": 2}
    thresholds = {"max_total_size_kb": 150, "max_file_size_kb": 20, "max_files": 25}
    files = [
        (Path("docs/normal.md"), 1024),
        (Path("docs/sub/huge.md"), 30 * 1024),
    ]
    alerts = docs_size._compute_alerts(stats, files, thresholds)
    assert len(alerts) == 1
    assert "huge.md" in alerts[0]
    assert "20 KB" in alerts[0]


def test_compute_alerts_oversized_threshold_is_strictly_greater():
    """Match Bash's `find -size +20k`, which matches strictly > 20*1024 bytes."""
    stats = {"total_size_kb": 50, "file_count": 1}
    thresholds = {"max_total_size_kb": 150, "max_file_size_kb": 20, "max_files": 25}
    exact = [(Path("docs/exact.md"), 20 * 1024)]
    over = [(Path("docs/over.md"), 20 * 1024 + 1)]
    assert docs_size._compute_alerts(stats, exact, thresholds) == []
    assert len(docs_size._compute_alerts(stats, over, thresholds)) == 1


def test_build_report_md_clean_status():
    stats = {"file_count": 3, "total_size_kb": 6, "estimated_tokens": 1500, "avg_size_kb": 2}
    thresholds = {"max_total_size_kb": 150, "max_file_size_kb": 20, "max_files": 25}
    md = docs_size._build_report_md(
        stats=stats,
        largest_files="docs/a.md (2 KB)",
        alerts=[],
        thresholds=thresholds,
        generated_at="2026-06-12 00:00:00 UTC",
    )
    assert "**Total Files:** 3" in md
    assert "✅ Documentation size within acceptable limits" in md
    assert "### Alerts" not in md
    assert "**Max File Count:** 25" in md


def test_build_report_md_alert_status_includes_alerts_section():
    stats = {"file_count": 30, "total_size_kb": 50, "estimated_tokens": 12000, "avg_size_kb": 1}
    thresholds = {"max_total_size_kb": 150, "max_file_size_kb": 20, "max_files": 25}
    md = docs_size._build_report_md(
        stats=stats,
        largest_files="docs/a.md (1 KB)",
        alerts=["⚠️  Number of documentation files (30) exceeds threshold (25)"],
        thresholds=thresholds,
        generated_at="2026-06-12 00:00:00 UTC",
    )
    assert "❌ Documentation size exceeds thresholds!" in md
    assert "### Alerts" in md
    assert "Number of documentation files (30)" in md


def test_load_thresholds_defaults(isolated_cwd):
    t = docs_size._load_thresholds()
    assert t["max_total_size_kb"] == docs_size.DEFAULT_MAX_TOTAL_SIZE_KB
    assert t["max_file_size_kb"] == docs_size.DEFAULT_MAX_FILE_SIZE_KB
    assert t["max_files"] == docs_size.DEFAULT_MAX_FILES


def test_load_thresholds_reads_config(write_config):
    write_config(
        "hygiene:\n"
        "  docs_size:\n"
        "    max_total_size_kb: 999\n"
        "    max_file_size_kb: 99\n"
        "    max_files: 9\n"
    )
    t = docs_size._load_thresholds()
    assert t == {"max_total_size_kb": 999, "max_file_size_kb": 99, "max_files": 9}


def test_load_thresholds_falls_back_on_garbage(write_config):
    write_config(
        "hygiene:\n"
        "  docs_size:\n"
        "    max_files: notanumber\n"
    )
    t = docs_size._load_thresholds()
    assert t["max_files"] == docs_size.DEFAULT_MAX_FILES


def test_run_writes_report_and_returns_zero(docs_tree, capsys):
    docs_tree({"a.md": "alpha content", "b.md": "beta content"})
    rc = docs_size.run()
    assert rc == 0
    report = docs_size.REPORT_FILE.read_text()
    assert "📚 Documentation Size Report" in report
    assert "**Total Files:** 2" in report
    assert "✅ Documentation size within acceptable limits" in report


def test_run_against_oversized_corpus_flags_alerts(docs_tree, write_config, capsys):
    write_config(
        "hygiene:\n"
        "  docs_size:\n"
        "    max_files: 1\n"
    )
    docs_tree({"a.md": "alpha", "b.md": "beta"})
    docs_size.run()
    report = docs_size.REPORT_FILE.read_text()
    assert "❌ Documentation size exceeds thresholds!" in report
    assert "Number of documentation files (2)" in report


def test_a_docs_exclude_entry_that_is_not_repo_relative_is_ignored_with_a_warning(docs_tree, write_config, capsys):
    write_config("hygiene:\n  docs:\n    exclude: [/etc/**, ../**, docs/archive/**]\n")
    docs_tree({"a.md": "alpha", "archive/old.md": "archived"})
    rels = sorted(str(p) for p in docs_size._split_doc_files(Path("docs"))[0])
    assert rels == ["docs/a.md"]
    out = capsys.readouterr().out
    assert "/etc/**" in out and "../**" in out and "ignored" in out
