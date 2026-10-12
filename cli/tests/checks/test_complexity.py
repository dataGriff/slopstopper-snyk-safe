"""Tests for the hygiene:complexity check."""

from __future__ import annotations

import csv
import io
from pathlib import Path

import pytest

from slopstopper.checks import complexity


# Sample CSV in lizard's shape: nloc,ccn,tokens,params,length,location,file,...
SAMPLE_CSV_LOW = (
    '5,2,42,1,7,"foo@10-14@./src/a.py","./src/a.py","foo","foo( x )",10,14\n'
    '8,3,80,2,12,"bar@20-31@./src/a.py","./src/a.py","bar","bar( x, y )",20,31\n'
)
# A function at CCN 16, over the default max_ccn of 15.
SAMPLE_CSV_OVER = (
    SAMPLE_CSV_LOW
    + '40,16,300,1,55,"complicated@40-94@./src/b.py","./src/b.py","complicated","complicated( x )",40,94\n'
)
# A function at CCN 12: under the default 15, but over a stricter max_ccn of 10.
SAMPLE_CSV_MID = (
    SAMPLE_CSV_LOW
    + '30,12,220,1,40,"midfn@40-79@./src/c.py","./src/c.py","midfn","midfn( x )",40,79\n'
)

DEFAULT = complexity.DEFAULT_MAX_CCN  # 15


def _mock_lizard(monkeypatch, csv_text: str) -> None:
    monkeypatch.setattr(complexity, "_lizard_available", lambda: True)
    monkeypatch.setattr(complexity, "_run_lizard", lambda target_dir=".": csv_text)


def test_default_max_ccn_is_fifteen():
    assert complexity.DEFAULT_MAX_CCN == 15


# ── CSV parsing ──────────────────────────────────────────────────


def test_parse_csv_rows_extracts_numeric_fields():
    rows = complexity._parse_csv_rows(SAMPLE_CSV_LOW)
    assert len(rows) == 2
    assert rows[0][:5] == (5, 2, 42, 1, 7)
    assert rows[0][5] == "foo@10-14@./src/a.py"
    assert rows[0][6] == "./src/a.py"


def test_parse_csv_rows_skips_header_rows():
    csv_with_header = "NLOC,CCN,token,PARAM,length,location,file\n" + SAMPLE_CSV_LOW
    rows = complexity._parse_csv_rows(csv_with_header)
    assert len(rows) == 2


def test_parse_csv_rows_handles_missing_file_column():
    # Older fixtures may omit column 6, so fall back to location's @-segment.
    csv_no_file_col = '5,2,42,1,7,"foo@10-14@./src/a.py"\n'
    rows = complexity._parse_csv_rows(csv_no_file_col)
    assert rows[0][6] == "./src/a.py"


def test_parse_csv_rows_falls_back_for_colon_location():
    # Even older format: "path:line" instead of "func@start-end@./path".
    csv_colon = "5,2,42,1,7,src/a.py:10\n"
    rows = complexity._parse_csv_rows(csv_colon)
    assert rows[0][6] == "src/a.py"


def test_parse_csv_rows_returns_empty_on_empty_input():
    assert complexity._parse_csv_rows("") == []


# ── summary ──────────────────────────────────────────────────────


def test_compute_summary_lines_empty_block():
    lines = complexity._compute_summary_lines([], DEFAULT)
    assert "No functions analyzed." in lines
    assert any("Total NLOC" in line for line in lines)


def test_compute_summary_lines_aggregates_correctly():
    rows = complexity._parse_csv_rows(SAMPLE_CSV_LOW)
    lines = complexity._compute_summary_lines(rows, DEFAULT)
    # Total NLOC = 5 + 8 = 13, fun_cnt = 2, files = {"./src/a.py"} → 1
    assert "13" in lines[2]
    assert "2 files" in lines[4] or "1 file" in lines[4]


def test_compute_summary_lines_counts_over_threshold():
    rows = complexity._parse_csv_rows(SAMPLE_CSV_OVER)
    lines = complexity._compute_summary_lines(rows, DEFAULT)
    # Only the CCN=16 row is > 15 → warning_cnt = 1
    assert lines[2].split()[-1] == "1"


def test_compute_summary_lines_respects_custom_threshold():
    rows = complexity._parse_csv_rows(SAMPLE_CSV_MID)
    # CCN 12 is over a max_ccn of 10 but not the default 15.
    assert complexity._compute_summary_lines(rows, 10)[2].split()[-1] == "1"
    assert complexity._compute_summary_lines(rows, DEFAULT)[2].split()[-1] == "0"


def test_format_summary_section_wraps_in_fence():
    section = complexity._format_summary_section(["line a", "line b"])
    assert section.startswith("```\n")
    assert section.endswith("```\n\n")
    assert "line a" in section


def test_format_summary_section_empty():
    assert complexity._format_summary_section([]) == ""


# ── high-complexity section ──────────────────────────────────────


def test_format_high_complexity_section_clean():
    rows = complexity._parse_csv_rows(SAMPLE_CSV_LOW)
    section = complexity._format_high_complexity_section(rows, DEFAULT)
    assert "✅" in section
    assert "all CCN ≤ 15" in section


def test_format_high_complexity_section_with_over_item():
    rows = complexity._parse_csv_rows(SAMPLE_CSV_OVER)
    section = complexity._format_high_complexity_section(rows, DEFAULT)
    assert "⚠️" in section
    assert "CCN > 15" in section
    assert "| 40 | 16 |" in section  # row table entry
    assert "complicated@40-94" in section


def test_format_high_complexity_section_threshold_drives_heading():
    rows = complexity._parse_csv_rows(SAMPLE_CSV_MID)
    # At max_ccn=10 the CCN-12 function is flagged; at 15 it is clean.
    assert "| 30 | 12 |" in complexity._format_high_complexity_section(rows, 10)
    assert "✅" in complexity._format_high_complexity_section(rows, DEFAULT)


# ── report builder ───────────────────────────────────────────────


def test_build_md_report_contains_all_sections():
    rows = complexity._parse_csv_rows(SAMPLE_CSV_LOW)
    md = complexity._build_md_report(rows, DEFAULT)
    assert "# Code Complexity Analysis Report" in md
    assert "## Summary" in md
    assert "## Guidelines" in md
    assert "## More Information" in md
    assert "CCN > 15" in md
    assert "hygiene.complexity.max_ccn" in md


# ── lizard availability ──────────────────────────────────────────


def test_lizard_available_returns_false_when_subprocess_errors(monkeypatch):
    def fake_run(*args, **kwargs):
        raise OSError("nope")

    monkeypatch.setattr(complexity.subprocess, "run", fake_run)
    assert complexity._lizard_available() is False


def test_lizard_available_returns_true_on_success(monkeypatch):
    class FakeResult:
        returncode = 0

    monkeypatch.setattr(complexity.subprocess, "run", lambda *a, **k: FakeResult())
    assert complexity._lizard_available() is True


# ── run() exit codes ─────────────────────────────────────────────


def test_run_returns_two_when_lizard_missing(monkeypatch, isolated_cwd, capsys):
    """A missing tool is 'could not run', not 'the repo failed'."""
    monkeypatch.setattr(complexity, "_lizard_available", lambda: False)
    rc = complexity.run()
    assert rc == 2
    assert "lizard is not installed" in capsys.readouterr().out


def test_run_passes_when_all_under_threshold(monkeypatch, isolated_cwd, capsys):
    _mock_lizard(monkeypatch, SAMPLE_CSV_LOW)
    rc = complexity.run()
    assert rc == 0
    assert "No high-complexity functions found" in capsys.readouterr().out
    assert complexity.REPORT_CSV.read_text() == SAMPLE_CSV_LOW


def test_run_fails_when_function_over_threshold(monkeypatch, isolated_cwd, capsys):
    _mock_lizard(monkeypatch, SAMPLE_CSV_OVER)
    rc = complexity.run()
    assert rc == 1
    out = capsys.readouterr().out
    assert "exceed CCN 15" in out
    md = complexity.REPORT_MD.read_text()
    assert "⚠️ High Complexity Items" in md
    assert "complicated@40-94" in md


def test_run_config_tightens_threshold(monkeypatch, write_config, capsys):
    # max_ccn: 10 turns the CCN-12 function into a failure.
    write_config("hygiene:\n  complexity:\n    max_ccn: 10\n")
    _mock_lizard(monkeypatch, SAMPLE_CSV_MID)
    rc = complexity.run()
    assert rc == 1
    assert "exceed CCN 10" in capsys.readouterr().out


def test_run_config_loosens_threshold(monkeypatch, write_config, capsys):
    # max_ccn: 20 lets the CCN-16 function through.
    write_config("hygiene:\n  complexity:\n    max_ccn: 20\n")
    _mock_lizard(monkeypatch, SAMPLE_CSV_OVER)
    rc = complexity.run()
    assert rc == 0
    assert "all CCN ≤ 20" in capsys.readouterr().out


# ── scope: what lizard actually scans (real lizard, temp git repo) ──


def _scanned_files(csv_text: str) -> set[str]:
    return {Path(row[6].strip('"')).as_posix().removeprefix("./") for row in csv.reader(io.StringIO(csv_text)) if len(row) > 6}


@pytest.fixture
def scope_repo(git_repo):
    if not complexity._lizard_available():
        pytest.skip("lizard not installed")
    files = {
        "main.go": "package main\n\nfunc Add(a, b int) int {\n\tif a > b {\n\t\treturn a\n\t}\n\treturn a + b\n}\n",
        "main_test.go": "package main\n\nfunc TestAdd(t *T) {\n\tif Add(1, 2) != 3 {\n\t\tt.Fail()\n\t}\n}\n",
        "src/site.js": "function go(x) {\n  if (x) { return 1; }\n  return 2;\n}\n",
        "dist/site.js": "function go(x) {\n  if (x) { return 1; }\n  return 2;\n}\n",
        "src/legacy/old.js": "function old(x) {\n  if (x) { return 1; }\n  return 2;\n}\n",
        ".gitignore": "dist/\n",
    }
    for name, body in files.items():
        Path(name).parent.mkdir(parents=True, exist_ok=True)
        Path(name).write_text(body)
    return git_repo


def test_scan_skips_go_test_files_and_gitignored_output(scope_repo):
    scanned = _scanned_files(complexity._run_lizard())
    assert {"main.go", "src/site.js"} <= scanned
    assert "main_test.go" not in scanned
    assert "dist/site.js" not in scanned


def test_config_exclude_adds_to_the_defaults(scope_repo, write_config):
    write_config("hygiene:\n  complexity:\n    exclude: ['src/legacy/*']\n")
    scanned = _scanned_files(complexity._run_lizard())
    assert "src/legacy/old.js" not in scanned
    assert "main_test.go" not in scanned
    assert "src/site.js" in scanned
