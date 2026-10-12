"""Software bill of materials (Snyk SBOM wrapper).

Subprocess-invokes `snyk sbom --all-projects` and keeps what it prints: a
CycloneDX (or SPDX) document listing every dependency Snyk resolved from
the repository's manifests. The SBOM is the artifact a procurement or
compliance team asks for, so the check's job is to produce it on every
PR and on `main`, upload it, and fail when it could not be produced.

The verdict: an SBOM was written and parses as the requested format
(exit 0); Snyk found no manifest to build one from (exit 1 by default,
because an empty SBOM on a repository that ships software is a compliance
gap, and the repository that genuinely has no dependencies says so with
`security.sbom.allow_empty: true`); or Snyk could not run (exit 2).

What leaves the runner: the dependency graph (package names and
versions), as for `security:dependencies`. The scan runs under the
organisation named by `SNYK_ORG` / `security.snyk.org`, authenticated by
`SNYK_TOKEN`.

Configuration (.slopstopper.yml, optional):

    security:
      sbom:
        format: cyclonedx1.5+json   # any format `snyk sbom --format` accepts
        allow_empty: false          # true passes a repo with no manifests
      snyk:
        org: my-org-slug            # or SNYK_ORG

Exit codes:
  0: the SBOM was written to `.ss/reports/sbom/`
  1: Snyk found no supported manifest and `security.sbom.allow_empty` is false
  2: snyk is not installed, could not authenticate, exited with an error,
      wrote nothing readable, `security.sbom.format` is not one Snyk
      accepts, or arguments were passed (this check takes none)
"""

from __future__ import annotations

import json
from pathlib import Path

from slopstopper import config, output
from slopstopper.checks import _report, _snyk
from slopstopper.checks._contract import EXIT_CANNOT_RUN, reject_extra_args, scan_incomplete

REPORT_DIR = Path(".ss/reports/sbom")
REPORT_MD = REPORT_DIR / "sbom-report.md"
TITLE = "Software Bill of Materials"

# Consumed by `slopstopper emit security:sbom --target {pr-comment,issue}`.
META = {
    "report_path": str(REPORT_MD),
    "comment_discriminator": "Software Bill of Materials",
    "issue_title": "⚠️ SBOM could not be produced for Main Branch",
    "issue_labels": ["sbom", "security"],
    "issue_followup": "🔔 The SBOM could not be produced again in commit",
}

# The formats `snyk sbom --format` accepts. A typo must stop the check
# rather than reach Snyk and come back as an opaque exit 2.
FORMATS = (
    "cyclonedx1.4+json", "cyclonedx1.4+xml",
    "cyclonedx1.5+json", "cyclonedx1.5+xml",
    "cyclonedx1.6+json", "cyclonedx1.6+xml",
    "spdx2.3+json",
)
DEFAULT_FORMAT = "cyclonedx1.5+json"


def _snyk_available() -> bool:
    return _snyk.available()


def _format() -> str | None:
    raw = config.get_str("security.sbom.format", DEFAULT_FORMAT).strip().lower()
    if raw in FORMATS:
        return raw
    output.error(f"security.sbom.format: {raw!r} is not one of {', '.join(FORMATS)}")
    return None


def _sbom_path(fmt: str) -> Path:
    ext = "xml" if fmt.endswith("+xml") else "json"
    family = "spdx" if fmt.startswith("spdx") else "cyclonedx"
    return REPORT_DIR / f"sbom.{family}.{ext}"


def _run_snyk(fmt: str, out_path: Path) -> tuple[int, list[str]]:
    return _snyk.run_to_file(
        ["sbom", f"--format={fmt}", "--all-projects", *_snyk.org_args()],
        REPORT_DIR,
        out_path,
    )


def _component_count(path: Path, fmt: str) -> int | None:
    """How many components the SBOM lists, or None when it does not parse.

    Only the JSON formats are inspected; an XML SBOM is kept as written
    and counts as produced when it is non-empty.
    """
    if fmt.endswith("+xml"):
        return -1 if path.exists() and path.stat().st_size > 0 else None
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    components = data.get("components") if fmt.startswith("cyclonedx") else data.get("packages")
    return len(components) if isinstance(components, list) else None


def _build_md_report(fmt: str, out_path: Path, count: int | None, nothing_to_scan: bool, allow_empty: bool) -> str:
    md = f"# {TITLE}\n\n**Generated**: {_report.generated_at()}\n\n"
    if nothing_to_scan:
        md += "## Summary\n\n"
        md += "Snyk found no dependency manifest it supports, so no SBOM was produced.\n\n"
        if allow_empty:
            md += "`security.sbom.allow_empty: true`, so this passes: the repository declares it ships no dependencies.\n\n"
        else:
            md += "- ❌ No SBOM: no supported manifest found (set `security.sbom.allow_empty: true` only if this repository genuinely ships no dependencies)\n\n"
        return md
    md += "## ✅ SBOM produced\n\n"
    md += f"- Format: `{fmt}`\n- File: `{out_path}`\n"
    if count is not None and count >= 0:
        md += f"- Components: {count}\n"
    md += "\n## More Information\n\n"
    md += "- Generated by [Snyk SBOM](https://docs.snyk.io/snyk-cli/commands/sbom)\n"
    md += "- Reports location: `.ss/reports/sbom/` (the SBOM and this summary; CI uploads both as an artifact)\n"
    md += "- Run `task ss:security:sbom` locally to regenerate it (needs `SNYK_TOKEN`)\n"
    return md


def run(args: list[str] | None = None) -> int:
    if args:
        return reject_extra_args("security:sbom", args)
    if not _snyk_available():
        output.error(_snyk.INSTALL_HELP)
        return 2
    fmt = _format()
    if fmt is None:
        return EXIT_CANNOT_RUN

    out_path = _sbom_path(fmt)
    output.running(f"Generating the SBOM with Snyk ({fmt})…")
    rc, stderr_tail = _run_snyk(fmt, out_path)
    allow_empty = config.get_bool("security.sbom.allow_empty", False)

    if _snyk.found_nothing(rc):
        REPORT_MD.write_text(_build_md_report(fmt, out_path, None, True, allow_empty))
        if allow_empty:
            output.warn("Snyk found no supported manifests; allow_empty is set, so no SBOM is fine")
            output.footer(REPORT_DIR, [REPORT_MD.name])
            return 0
        output.error("Snyk found no supported manifests, so no SBOM could be produced")
        output.footer(REPORT_DIR, [REPORT_MD.name])
        return 1

    count = _component_count(out_path, fmt) if rc == 0 else None
    if count is None:
        return scan_incomplete(
            REPORT_DIR, REPORT_MD, TITLE,
            _snyk.incomplete_detail("Snyk SBOM", rc, out_path.exists(), stderr_tail, out_path),
        )

    REPORT_MD.write_text(_build_md_report(fmt, out_path, count, False, allow_empty))
    output.success(f"SBOM written to {out_path}" + (f" ({count} components)" if count >= 0 else ""))
    output.footer(REPORT_DIR, [REPORT_MD.name, out_path.name])
    return 0
