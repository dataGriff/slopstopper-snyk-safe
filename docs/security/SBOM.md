# Software Bill of Materials (SBOM)

`security:sbom` runs **Snyk SBOM** and keeps what it produces: a CycloneDX (or SPDX) document listing every dependency Snyk resolved from the repository's manifests. The SBOM is the artifact a procurement, compliance or incident-response team asks for, so the check's job is to produce it on every PR and on `main`, upload it, and fail when it could not be produced.

## Quick Start

### Generate Locally

```bash
export SNYK_TOKEN=…        # a Snyk token; CI uses the SNYK_TOKEN secret
task ss:security:sbom
ls .ss/reports/sbom/
```

### What You Get Automatically

- ✅ An SBOM written to `.ss/reports/sbom/` on every PR and push to `main`
- ✅ The SBOM uploaded as a workflow artifact, so every commit on `main` has a retrievable bill of materials
- ✅ A failing check when no SBOM could be produced

### The verdict

| Outcome | Exit | Meaning |
|---------|------|---------|
| An SBOM was written and parses as the requested format | 0 | pass |
| Snyk found no manifest to build one from | 1 (default) | an empty SBOM on a repository that ships software is a compliance gap; a repo with genuinely no dependencies sets `allow_empty: true` |
| Snyk could not run (no token, a crash, an unsupported format) | 2 | could not run |

### Common Issues

| Problem | Solution |
|---------|----------|
| Exit 1, "no supported manifest" | The repo has no lock file or manifest Snyk resolves. If it genuinely has no dependencies (a docs repo, a pure-shell tool), set `security.sbom.allow_empty: true`. Otherwise add the manifest or lock file the ecosystem expects |
| Exit 2, "format not accepted" | `security.sbom.format` must be one `snyk sbom --format` lists: `cyclonedx1.4+json`, `cyclonedx1.4+xml`, `cyclonedx1.5+json`, `cyclonedx1.5+xml`, `cyclonedx1.6+json`, `cyclonedx1.6+xml`, `spdx2.3+json` |
| Exit 2, could not authenticate | `SNYK_TOKEN` is missing |
| Don't want an SBOM? | List `ss-security-sbom-check.yml` under `workflows.disabled`, or delete it. Think twice: it is the cheapest compliance evidence the suite produces |

---

## Overview

The SBOM workflow:

- ✅ Runs on every PR to `main` and push to `main`
- ✅ Runs `snyk sbom --all-projects --format <format>` under the org named by `SNYK_ORG` / `security.snyk.org`
- ✅ Writes the document to `.ss/reports/sbom/` and uploads it as a workflow artifact
- ✅ Fails when the SBOM is empty (unless allowed) or could not be produced

**What leaves the runner.** The dependency graph (package names and versions), exactly as for `security:dependencies`; Snyk assembles the SBOM from it. No source code.

## Formats

```yaml
# .slopstopper.yml
security:
  sbom:
    format: cyclonedx1.5+json   # any format `snyk sbom --format` accepts
    allow_empty: false          # true passes a repo with no manifests
  snyk:
    org: my-org-slug            # or SNYK_ORG
```

CycloneDX JSON is the default because it is what most SBOM consumers (Dependency-Track, GUAC, the GitHub dependency submission API via a converter) ingest first. Use SPDX (`spdx2.3+json`) when a customer or a regulation names it. Both carry the same component list; only the envelope differs.

## Retention as a compliance artifact

The workflow artifact is the record. Keep three things in mind:

- **Retention.** GitHub keeps artifacts for the repository's artifact retention period (90 days by default on public repos; the organisation setting governs private ones). For a bill of materials that must be producible for a release years later, that is not enough: on a release, download the artifact and attach it to the GitHub Release, or push it to the SBOM store the organisation uses (Dependency-Track, an S3 bucket with object lock, an artifact registry). The `main`-branch run for the tagged commit is the one to keep.
- **Provenance.** The artifact name carries the commit; the document's metadata carries the tool and the timestamp. Record which commit an SBOM describes when you archive it.
- **Diffing.** Two SBOMs from consecutive releases diff cleanly as JSON, which is the quickest answer to "what changed in our supply chain between 1.4 and 1.5".

## Files Involved

| File | Purpose |
|------|---------|
| `.github/workflows/ss-security-sbom-check.yml` | GitHub Actions workflow (generate and upload) |
| `Taskfile.ss.yml` (`security:sbom` task) | Local shim → `slopstopper run security:sbom` |
| `cli/slopstopper/checks/sbom.py` | Check implementation (subprocess-invokes `snyk sbom`, validates the document, renders the MD report) |
| `cli/slopstopper/checks/_snyk.py` | Shared Snyk plumbing |

## Reports

`.ss/reports/sbom/`: the SBOM document in the requested format plus a short markdown report naming the file, the format and the component count. Uploaded as a workflow artifact.
