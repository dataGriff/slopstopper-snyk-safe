"""Smoke tests for the .slopstopper.yml subset parser."""

from __future__ import annotations

from slopstopper import config


def test_missing_file_returns_default(isolated_cwd):
    assert config.get("hygiene.docs_size.max_files", 25) == 25
    assert config.get("anything.at.all") is None


def test_top_level_scalar(write_config):
    write_config("answer: '42'\n")
    assert config.get("answer") == "42"


def test_nested_dot_path(write_config):
    write_config(
        "hygiene:\n"
        "  docs_size:\n"
        "    max_files: 10\n"
    )
    assert config.get("hygiene.docs_size.max_files") == "10"
    assert config.get("hygiene.docs_size.missing", "fallback") == "fallback"


def test_inline_list(write_config):
    write_config("workflows:\n  disabled: [foo, bar]\n")
    assert config.get("workflows.disabled") == ["foo", "bar"]


def test_block_list(write_config):
    write_config(
        "workflows:\n"
        "  disabled:\n"
        "    - foo\n"
        "    - bar\n"
    )
    assert config.get("workflows.disabled") == ["foo", "bar"]


def test_null_value_returns_default(write_config):
    write_config("headers:\n  source: null\n")
    assert config.get("headers.source", "fallback") == "fallback"


def test_comment_stripping(write_config):
    write_config("answer: '42'  # inline comment\n# whole-line comment\n")
    assert config.get("answer") == "42"


# ── comment handling ─────────────────────────────────────────────


def test_hash_inside_a_value_is_not_a_comment(write_config):
    """Real YAML: `#` starts a comment only after whitespace.

    Without this an unquoted URL fragment was silently dropped, and the
    check audited a different, valid-looking URL.
    """
    write_config("urls:\n  production: https://example.com/#anchor\n")
    assert config.get("urls.production") == "https://example.com/#anchor"


def test_hash_after_whitespace_is_a_comment(write_config):
    write_config("urls:\n  production: https://example.com/  # prod\n")
    assert config.get("urls.production") == "https://example.com/"


def test_hash_in_quotes_is_kept(write_config):
    write_config("tag: '#42'\n")
    assert config.get("tag") == "#42"


# ── typed accessors ──────────────────────────────────────────────
#
# The parser never coerces. These are the one place that does.


def test_scalars_come_back_as_strings(write_config):
    """Documents the parser contract the typed accessors exist for."""
    write_config("num: 200\nflag: true\nquoted: \"false\"\n")
    assert config.get("num") == "200"
    assert config.get("flag") is True
    assert config.get("quoted") == "false"


def test_get_int_coerces_and_falls_back(write_config):
    write_config("hygiene:\n  complexity:\n    max_ccn: 20\n    bad: fifteen\n")
    assert config.get_int("hygiene.complexity.max_ccn", 15) == 20
    assert config.get_int("hygiene.complexity.bad", 15) == 15
    assert config.get_int("hygiene.complexity.missing", 15) == 15
    assert config.get_int("hygiene.complexity.missing") is None


def test_get_int_does_not_treat_a_boolean_as_one(write_config):
    write_config("n: true\n")
    assert config.get_int("n", 7) == 7


def test_get_bool_understands_quoted_and_word_spellings(write_config):
    """`bool("false")` is True, the footgun every hand-rolled bool() had."""
    write_config(
        "a: \"false\"\nb: 'no'\nc: off\nd: 0\ne: yes\nf: TRUE\ng: 1\nh: maybe\n"
    )
    assert config.get_bool("a", True) is False
    assert config.get_bool("b", True) is False
    assert config.get_bool("c", True) is False
    assert config.get_bool("d", True) is False
    assert config.get_bool("e", False) is True
    assert config.get_bool("f", False) is True
    assert config.get_bool("g", False) is True
    assert config.get_bool("h", True) is True  # unrecognised → default
    assert config.get_bool("missing", True) is True


def test_get_str(write_config):
    write_config("map_path: docs/index.md\nempty: null\n")
    assert config.get_str("map_path", "x") == "docs/index.md"
    assert config.get_str("empty", "x") == "x"
    assert config.get_str("missing", "x") == "x"


# ── outside the subset: warn, never silently skip ────────────────


def test_tab_indented_block_warns_and_is_ignored(write_config, capsys):
    write_config("hygiene:\n\tdocs_size:\n\t\tmax_files: 10\n")
    assert config.get("hygiene.docs_size.max_files", 25) == 25
    err = capsys.readouterr().err
    assert ".slopstopper.yml:2" in err
    assert "tab indentation" in err


def test_inline_map_is_a_raw_string(write_config):
    """Not supported; documented so nobody relies on it by accident."""
    write_config("m: {a: 1}\n")
    assert config.get("m") == "{a: 1}"


def test_folded_scalar_body_warns(write_config, capsys):
    write_config("desc: >\n  folded line one\n  folded line two\n")
    assert config.get("desc") == ">"
    assert "outside the supported YAML subset" in capsys.readouterr().err


def test_supported_shapes_produce_no_warning(write_config, capsys):
    write_config(
        "# comment\n"
        "profile: ui\n"
        "urls:\n"
        "  production: https://example.com\n"
        "  preview:\n"
        "pages:\n"
        "  smoke: /,/a.html\n"
        "workflows:\n"
        "  disabled: []\n"
        "  enabled:\n"
        "    - one\n"
        "    - two\n"
        "api:\n"
        "  health:\n"
        "    expect_fields:\n"
        "      # status: ok\n"
    )
    config.get("profile")
    assert capsys.readouterr().err == ""


def test_the_shipped_example_config_parses_without_warnings(isolated_cwd, capsys):
    """`.slopstopper.yml.example` is the schema reference, so it must be in-subset."""
    from pathlib import Path

    example = Path(__file__).resolve().parents[2] / ".slopstopper.yml.example"
    (isolated_cwd / ".slopstopper.yml").write_text(example.read_text())
    config.reload()
    assert config.get("profile") == "ui"
    assert capsys.readouterr().err == ""


def test_document_markers_are_not_warned_about(write_config, capsys):
    write_config("---\nprofile: ui\n...\n")
    assert config.get("profile") == "ui"
    assert capsys.readouterr().err == ""


def test_apostrophe_inside_a_plain_scalar_does_not_swallow_the_comment(write_config):
    write_config("desc: don't panic # comment\nname: O'Brien # c\ntitle: \"a # b\" # real\n")
    assert config.get("desc") == "don't panic"
    assert config.get("name") == "O'Brien"
    assert config.get("title") == "a # b"


def test_get_int_warns_when_set_but_not_a_number(write_config, capsys):
    write_config("hygiene:\n  complexity:\n    max_ccn: fifteen\n")
    assert config.get_int("hygiene.complexity.max_ccn", 15) == 15
    err = capsys.readouterr().err
    assert "hygiene.complexity.max_ccn" in err and "fifteen" in err and "not an integer" in err


def test_get_bool_warns_when_set_but_not_a_boolean(write_config, capsys):
    write_config("hygiene:\n  entry_files:\n    require_map_pointer: maybe\n")
    assert config.get_bool("hygiene.entry_files.require_map_pointer", True) is True
    err = capsys.readouterr().err
    assert "require_map_pointer" in err and "maybe" in err and "not a boolean" in err


def test_typed_accessors_are_silent_when_the_key_is_simply_unset(write_config, capsys):
    write_config("profile: ui\n")
    assert config.get_int("hygiene.complexity.max_ccn", 15) == 15
    assert config.get_bool("hygiene.entry_files.require_map_pointer", True) is True
    assert capsys.readouterr().err == ""
