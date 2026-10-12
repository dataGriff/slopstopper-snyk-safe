# Project-shape profiles

Not every check applies to every repo. The nine browser-and-SEO reliability
checks (smoke, E2E, accessibility, Core Web Vitals, SEO, broken links, llms.txt,
robots.txt, sitemap) assume HTML, a DOM and a public web surface. On an HTTP API
they don't quietly no-op. They build, serve and audit nothing, and go red. On a
library there is nothing to serve at all.

A **profile** is a named preset for `workflows.disabled`: it says which
workflows a repo of that shape shouldn't carry.

| Profile | Shape | Drops |
| ------- | ----- | ----- |
| `ui` (default) | Serves HTML to a browser | Nothing while the repo has an API. With no API found, the four API checks (api-health, api-latency, api-headers, OpenAPI drift) are left out until one appears |
| `api` | JSON/gRPC endpoints, no browser surface | The nine browser-and-SEO checks. Keeps the four API checks (api-health, api-latency, api-headers, OpenAPI drift) and CSP exceptions (APIs still set response headers) |
| `library` | Library, CLI or package; nothing deployed | The nine above, plus CSP exceptions and the four API checks, which is everything that needs a URL. What remains is the static layer: the four Snyk checks, secrets, complexity and the docs checks |

**The Snyk checks apply to every profile.** Snyk Code, the dependency scan,
the IaC scan and the SBOM read the repository, not a URL, so a library gets
the same security coverage as a site. The checks that *do* need a URL skip
rather than fail until configured, under the "unconfigured is not failing"
contract: `hygiene:openapi` and the three API checks exit 0 with a note until
`api.*` is set.

```bash
bash install.sh --profile api      # writes `profile: api` into .slopstopper.yml
slopstopper profile show           # what this repo resolves to, and why
slopstopper profile detect         # suggest one from the repo's contents
slopstopper profile list           # the full mapping
slopstopper profile disabled       # this repo's effective set, one per line (what install.sh reads)
slopstopper profile show --json    # any of these as JSON, for a script or an agent
```

## Design decisions

**The config is the home, not the flag.** `--profile` writes `profile:` into
`.slopstopper.yml` and the installed workflow set is derived from that key on
every run. The choice has to survive a re-run and be visible in review. That is
the same reasoning as `workflows.disabled` itself, which exists so opting out
isn't "delete the file and trust the marker".

**A preset over the existing mechanism, not new machinery.** A profile expands
into the same disabled set that `workflows.disabled` already fed, resolved in
one place ([`profiles.effective_disabled()`](../../cli/slopstopper/profiles.py)):

```
effective = (profile.disables − workflows.enabled) ∪ workflows.disabled
```

So `slopstopper doctor` skipping a missing tool, `install.sh` deleting a
workflow, and `slopstopper badges` (which globs what's on disk) all follow from
the profile without knowing it exists. `workflows.enabled` is the escape hatch:
an API that does serve a docs site can list `ss-reliability-broken-links-check.yml`
and keep that one check. A profile only ever subtracts.

**One mapping, two readers.** The profile → workflow table lives in
[`cli/slopstopper/data/profiles.json`](../../cli/slopstopper/data/profiles.json).
The CLI reads it through `slopstopper.profiles`; `install.sh` imports that same
module from the source tree with `python3` (a hard prereq) rather than requiring
the installed wheel, so the installer can resolve the workflow set before the
mise/CLI step has run and there's no bash-side copy of the mapping to drift.

**Detection suggests, never applies.** `slopstopper profile detect` sniffs for
framework configs, HTML, OpenAPI specs and server-framework dependencies, and
`install.sh` prints the suggestion when it differs from the active profile. It
never writes the key: an install run from a script is non-interactive, and a wrong
silent pick (checks quietly off) is worse than the default superset (checks
visibly red). When signals conflict, UI wins for the same reason.

**Profiles made `api` quiet before they made it covered.** Subtraction removes
checks that don't apply; it doesn't add the ones that should. The API-shaped
analogues were a separate increment, not a gap in the mechanism, and have all
landed: smoke → [api-health](../reliability/README.md#api-health-check), Core
Web Vitals → [api-latency](../reliability/README.md#api-latency-check), CSP
exceptions → [api-headers](../security/API_HEADERS.md), docs-accuracy →
[openapi](../hygiene/README.md#openapi-drift). Each is inert until configured. Under `ui`
the four API checks ship only when the repo has an API: an OpenAPI/Swagger spec or proto file, a server-framework dependency, or an API check configured under `api.*` in `.slopstopper.yml`
(`disables_without_api` in `cli/slopstopper/data/profiles.json`), so a static site doesn't run four
jobs that check nothing and a UI with API routes still gets them without
finding `workflows.enabled`. Re-run the installer after adding one to pick
them up. Out of reach: detecting *undocumented* routes, which needs
framework-specific route introspection.
