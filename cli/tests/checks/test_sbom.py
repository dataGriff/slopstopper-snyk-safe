"""Tests for the security:sbom check (Snyk SBOM wrapper)."""

from __future__ import annotations

import json
from pathlib import Path

from slopstopper.checks import _snyk, sbom


def _stub_snyk(monkeypatch, rc: int = 0, body: str | None = None, stderr=None):
    def fake_run(fmt, out_path):
        sbom.REPORT_DIR.mkdir(parents=True, exist_ok=True)
        if body is not None:
            out_path.write_text(body)
        return rc, stderr or []

    monkeypatch.setattr(sbom, "_snyk_available", lambda: True)
    monkeypatch.setattr(sbom, "_run_snyk", fake_run)


CYCLONEDX = json.dumps({"bomFormat": "CycloneDX", "specVersion": "1.5", "components": [{"name": "lodash"}, {"name": "qs"}]})
SPDX = json.dumps({"spdxVersion": "SPDX-2.3", "packages": [{"name": "lodash"}]})


def test_the_default_format_is_cyclonedx_json(isolated_cwd):
    assert sbom._format() == "cyclonedx1.5+json"
    assert sbom._sbom_path("cyclonedx1.5+json") == Path(".ss/reports/sbom/sbom.cyclonedx.json")


def test_an_unknown_format_is_refused_before_snyk_runs(write_config, capsys, monkeypatch):
    write_config("security:\n  sbom:\n    format: swid\n")
    monkeypatch.setattr(sbom, "_snyk_available", lambda: True)
    monkeypatch.setattr(sbom, "_run_snyk", lambda *a: (_ for _ in ()).throw(AssertionError("must not run")))
    assert sbom.run() == 2
    assert "security.sbom.format: 'swid' is not one of" in capsys.readouterr().out


def test_spdx_and_xml_paths_follow_the_format(isolated_cwd):
    assert sbom._sbom_path("spdx2.3+json") == Path(".ss/reports/sbom/sbom.spdx.json")
    assert sbom._sbom_path("cyclonedx1.6+xml") == Path(".ss/reports/sbom/sbom.cyclonedx.xml")


def test_component_count_reads_cyclonedx_and_spdx(isolated_cwd):
    p = Path("a.json")
    p.write_text(CYCLONEDX)
    assert sbom._component_count(p, "cyclonedx1.5+json") == 2
    p.write_text(SPDX)
    assert sbom._component_count(p, "spdx2.3+json") == 1
    p.write_text("<bom/>")
    assert sbom._component_count(p, "cyclonedx1.5+xml") == -1
    p.write_text("not json")
    assert sbom._component_count(p, "cyclonedx1.5+json") is None


def test_run_returns_two_when_snyk_is_missing(monkeypatch, isolated_cwd, capsys):
    monkeypatch.setattr(sbom, "_snyk_available", lambda: False)
    assert sbom.run() == 2
    assert "snyk is not installed" in capsys.readouterr().out


def test_run_rejects_arguments(isolated_cwd, capsys):
    assert sbom.run(["--format=spdx"]) == 2
    assert "takes no arguments" in capsys.readouterr().out


def test_run_writes_the_sbom_and_a_summary(monkeypatch, isolated_cwd, capsys):
    _stub_snyk(monkeypatch, 0, CYCLONEDX)
    assert sbom.run() == 0
    out = capsys.readouterr().out
    assert "SBOM written to .ss/reports/sbom/sbom.cyclonedx.json (2 components)" in out
    md = sbom.REPORT_MD.read_text()
    assert "SBOM produced" in md and "Components: 2" in md and "cyclonedx1.5+json" in md


def test_no_manifests_fails_by_default(monkeypatch, isolated_cwd, capsys):
    _stub_snyk(monkeypatch, 3, None, ["No supported projects found"])
    assert sbom.run() == 1
    assert "no SBOM could be produced" in capsys.readouterr().out
    assert "- ❌ No SBOM" in sbom.REPORT_MD.read_text()


def test_allow_empty_turns_no_manifests_into_a_pass(monkeypatch, write_config, capsys):
    write_config("security:\n  sbom:\n    allow_empty: true\n")
    _stub_snyk(monkeypatch, 3, None)
    assert sbom.run() == 0
    assert "allow_empty is set" in capsys.readouterr().out


def test_a_failed_run_is_could_not_run(monkeypatch, isolated_cwd):
    _stub_snyk(monkeypatch, 2, None, ["Authentication error"])
    assert sbom.run() == 2
    md = sbom.REPORT_MD.read_text()
    assert "Scan did not complete" in md and "SNYK_TOKEN" in md


def test_an_unparseable_sbom_is_could_not_run(monkeypatch, isolated_cwd):
    _stub_snyk(monkeypatch, 0, "garbage")
    assert sbom.run() == 2


def test_run_snyk_builds_the_sbom_command(monkeypatch, isolated_cwd):
    seen = {}

    def fake(args, report_dir, out_path):
        seen["args"] = args
        seen["out"] = out_path
        return 0, []

    monkeypatch.setenv("SNYK_ORG", "acme")
    monkeypatch.setattr(_snyk, "run_to_file", fake)
    sbom._run_snyk("spdx2.3+json", Path("x.json"))
    assert seen["args"] == ["sbom", "--format=spdx2.3+json", "--all-projects", "--org=acme"]
    assert seen["out"] == Path("x.json")
