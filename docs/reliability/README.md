# Reliability Testing

## Overview

SlopStopper's reliability checks, wired against any reachable URL: portable smoke tests, broken-link audits, and the API health and latency audits (all below), plus seven audits with their own guide. Read the one whose trigger matches.

## Routes

| When you are…                                                        | Do this                                                                          |
| -------------------------------------------------------------------- | -------------------------------------------------------------------------------- |
| fixing an axe-core finding or tuning the WCAG audit                   | Read [ACCESSIBILITY.md](ACCESSIBILITY.md), the audit, its thresholds and report   |
| fixing a broken user journey, or adding journeys specific to your site | Read [E2E.md](E2E.md), the nav, anchor, disclosure and back-button walk            |
| tuning Lighthouse budgets or debugging a Core Web Vitals failure       | Read [CWV.md](CWV.md), Lighthouse CI configuration and the budget files           |
| fixing a missing or wrong SEO / social-share metatag                   | Read [SEO.md](SEO.md), what the metatag audit asserts                             |
| adding or fixing the site's `llms.txt`                                 | Read [LLMS_TXT.md](LLMS_TXT.md), the llmstxt.org shape the check asserts          |
| changing `robots.txt`, or when the de-index guard fires                | Read [ROBOTS_TXT.md](ROBOTS_TXT.md), discoverability rules and the guard          |
| adding pages, or when the sitemap drift check fires                    | Read [SITEMAP.md](SITEMAP.md), completeness + drift against the served site       |


## Configuration (env vars)

Every reliability check takes its base URL as a positional argument (`task ss:reliability:<check> -- https://host/`; a sub-path such as `https://user.github.io/proj/` is kept, and every page path is resolved under it) or from `<CHECK>_TEST_URL`; the browser checks fall back to `SMOKE_TEST_URL`, then `BASE_URL`, then `http://localhost:8080`. Each check's own variables (pages, thresholds, paths) are in its section below or its doc: [ACCESSIBILITY.md](ACCESSIBILITY.md), [E2E.md](E2E.md), [CWV.md](CWV.md), [SEO.md](SEO.md), [LLMS_TXT.md](LLMS_TXT.md), [ROBOTS_TXT.md](ROBOTS_TXT.md), [SITEMAP.md](SITEMAP.md). `.slopstopper.yml` (`pages.*`, `api.*`, `reliability.*`) is the committed form of the same settings.

## API Health Check

The API-shaped analogue of the smoke test. Smoke drives a browser over HTML pages; this probes the one endpoint an API is expected to expose for exactly this purpose, and asserts the contract around it.

**Why not just point smoke at `/health`:** smoke asserts DOM-shaped things that a JSON endpoint will never satisfy, such as a `<title>`, a linked stylesheet and a shareable og-image. A health endpoint also has assertions of its own. `{"status": "degraded"}` returned with HTTP 200 is a **pass** to any reachability probe and a **failure** here, which is the whole reason a health endpoint returns a body at all.

### What it checks

| Assertion | Hard fail? | Knob |
|---|---|---|
| Endpoint reachable | yes | none |
| Status code matches | yes | `api.health.expect_status` (default 200) |
| JSON content-type (`application/json`, `+json` suffixes) | yes | `api.health.require_json` |
| Body parses as JSON and is non-empty | yes | `api.health.require_json` |
| Declared fields present (dot-paths, e.g. `deps.db`) | yes | `api.health.require_fields` |
| Declared fields match an expected value | yes | `api.health.expect_fields` |
| Response time | only with a budget set | `api.health.max_response_ms` |

### Configuration

```yaml
# .slopstopper.yml
api:
  base_path: ''          # prefix the API is served under, e.g. /api/v1
  health:
    path: /health        # unset → the check skips gracefully (exit 0)
    expect_status: 200
    require_json: true
    require_fields: [status, deps.db]
    expect_fields:
      status: ok
    max_response_ms:     # unset → timing reported, never enforced
```

With `api.health.path` unset the check exits 0 with a note, which is the same contract as [`hygiene:csp-exceptions`](../security/README.md#csp-exceptions) with `headers.source: null`. An unconfigured check is not a failing check, so a fresh install's first PR is green.

### Running locally

```bash
# Pass the base URL positionally (works for any reliability check)
task ss:reliability:api-health -- https://api.example.com

# Override config from the command line
task ss:reliability:api-health -- https://api.example.com \
  --path /readyz --expect-status 204 --require-field status --max-response-ms 500

# Or set the env var
API_HEALTH_TEST_URL=https://api.example.com task ss:reliability:api-health
```

Report: `.ss/reports/api-health/api-health-report.{md,json}`.

### Running in CI

`ss-reliability-api-health-check.yml` audits `urls.preview` on pull requests and `urls.production` on pushes to main, schedules, `deployment_status` events and a `url` dispatch. Unlike the browser checks it **never builds and serves the repo locally**, because an API isn't a static bundle `slopstopper serve` can host, and guessing a start command would be worse than not guessing. With neither URL configured the PR run emits a notice and skips; the deployed-main and scheduled runs still cover the endpoint.

The check ships under the `api` [project-shape profile](../architecture/PROFILES.md), and under `ui` once the repo shows an API (a spec, a server framework or an API check configured under `api.*`). It stays inert until configured.

## API Latency Check

The API-shaped analogue of Core Web Vitals. There is no rendering on a JSON API, so the equivalent question is narrower and more answerable: how long does the endpoint take to answer, and how much does it send back. Each configured path is sampled several times; the check reports the **median**, the **slowest** sample and the response size.

**Why not p95:** five samples cannot support a 95th percentile, and calling `max()` a percentile would dress up one noisy reading as statistics. Budgets gate on the **median**, which resists flakes because one slow sample from a shared CI runner moves the maximum and not the middle. `slowest_ms` is an opt-in tail ceiling for anyone who wants one.

### What it checks

| Assertion | Hard fail? | Knob |
|---|---|---|
| Every sample completes | yes | none |
| Status is 2xx/3xx | yes | none |
| Median response time | only with a budget set | `api.latency.median_ms` |
| Slowest sample | only with a budget set | `api.latency.slowest_ms` |
| Response size | only with a budget set | `api.latency.max_bytes` |

Reachability is the floor and never opt-in: a 500 that answers in 3ms is not fast, and a timing from a connection error is not a measurement. Everything else is advisory until you set a budget.

### Configuration

```yaml
# .slopstopper.yml
api:
  base_path: ''          # prefix the API is served under, e.g. /api/v1
  latency:
    paths: []            # empty → the check skips gracefully (exit 0)
    samples: 5           # measured requests per endpoint
    warmup: 1            # discarded first requests (DNS, TLS, cold start)
    median_ms:           # unset → timings reported, never enforced
    slowest_ms:
    max_bytes:
```

Warmup requests are thrown away rather than averaged in: the first hit pays for DNS, TLS and whatever cold start the platform imposes, none of which is the steady-state latency a budget is about.

**Derive budgets from numbers you have observed, not from numbers that sound right.** A guessed budget on a shared runner is noise, and a check people learn to ignore is worse than no check. Run it with no budgets for a week, read the reports, then set one.

### Running locally

```bash
task ss:reliability:api-latency -- https://api.example.com

# Override config from the command line
task ss:reliability:api-latency -- https://api.example.com \
  --path /v1/items --samples 9 --median-ms 400 --max-bytes 200000

# Or set the env vars
API_LATENCY_TEST_URL=https://api.example.com \
  API_LATENCY_PATHS=/health,/v1/items task ss:reliability:api-latency
```

Report: `.ss/reports/api-latency/api-latency-report.{md,json}`.

### Running in CI

`ss-reliability-api-latency-check.yml` resolves its URL exactly as the API health workflow does (`urls.preview` on pull requests, `urls.production` on main, schedules, `deployment_status` events and a `url` dispatch) and never builds or serves the repo locally.

One caveat specific to this check: **timings from a preview environment are not timings from production.** A cold-started preview deploy is slower and noisier. Either set budgets loose enough for it, or leave them unset on PRs and rely on the scheduled production run.

Ships under `api`, and under `ui` once the repo shows an API (see [profiles](../architecture/PROFILES.md)). Inert until configured.

## Broken Link Checks

The broken-link check can run against any site you provide via `BROKEN_LINKS_TEST_URL`.
It visits each path in `BROKEN_LINKS_PAGES`, collects all anchor links, keeps only same-origin HTTP(S) links, then asserts each destination returns a non-4xx/5xx status.
External links are intentionally skipped to reduce noise from third-party outages,
rate limits, or bot protections outside your control.

### Running broken-link checks locally

```bash
# Pass URL directly
task ss:reliability:broken-links -- https://your-site.example.com

# Or set environment variables
BROKEN_LINKS_TEST_URL=https://your-site.example.com \
BROKEN_LINKS_PAGES="/,/features.html,/tools.html" \
task ss:reliability:broken-links
```

### Running broken-link checks in CI

```bash
BROKEN_LINKS_TEST_URL=https://your-site.example.com \
BROKEN_LINKS_PAGES="/,/features.html,/tools.html" \
task ss:reliability:broken-links -- --ci
```

Set `pages.broken_links` in `.slopstopper.yml` (or `BROKEN_LINKS_PAGES`) to the pages whose outbound links matter, so they are validated on every scheduled/deploy/PR run.

## Smoke Tests

Smoke tests are lightweight, critical-path tests that verify a deployed site is functioning correctly. These tests run against live URLs (production or staging) and check for:

- Page availability (200 status codes)
- Core navigation functionality
- Asset loading
- Response times
- Critical content rendering

### Running smoke tests locally

```bash
# Pass URL as a positional argument (works for any reliability check)
task ss:reliability:smoke -- https://your-site.example.com

# Add --ci for retries + HTML report + single worker
task ss:reliability:smoke -- https://your-site.example.com --ci

# Or set the env var (handy when chaining `BROKEN_LINKS_PAGES=… SMOKE_TEST_URL=…`)
SMOKE_TEST_URL=https://your-site.example.com task ss:reliability:smoke
```

`task ss:reliability:smoke` shells through to `slopstopper run reliability:smoke`, which launches the bundled Playwright spec via the config baked into the `slopstopper-cli` wheel. The config is [`cli/slopstopper/data/playwright.config.js`](../../cli/slopstopper/data/playwright.config.js) and the spec is [`cli/slopstopper/data/tests/smoke.spec.ts`](../../cli/slopstopper/data/tests/smoke.spec.ts). Adopters don't vendor those files; the CLI owns them. To customise, run `slopstopper templates eject playwright.config.js` to drop an editable copy into `.ss/`; the CLI picks `.ss/<filename>` up automatically. Each run copies the resolved config and spec (your override if there is one, else the bundled file) into `.ss/.run/` so Playwright can reach your `node_modules`; that directory ignores itself and is rewritten on every run, so don't edit it.

### Running in CI

The canonical shape is the ready-to-run workflow the installer copies to [`.github/workflows/ss-reliability-smoke-tests.yml`](../../.github/workflows/ss-reliability-smoke-tests.yml). It runs on PRs, pushes to `main`, an hourly schedule, `deployment_status` events, and `workflow_dispatch` with a `url` input; on failure it opens (or updates) a tracking issue, and on recovery it closes it. The job calls:

```yaml
- name: Run smoke tests
  run: task ss:reliability:smoke -- ${{ steps.url.outputs.url }} --ci
```

That is the same Task command you ran locally, with the positional URL resolved from the trigger context.

### What the Smoke Tests Check

1. **Homepage Availability** - Ensures the main page loads with 200 status
2. **Secondary Pages** - Verifies all critical pages are accessible
3. **Navigation** - Tests basic user navigation flows
4. **Static Assets** - Confirms CSS and JS files load correctly
5. **Performance** - Validates pages load within acceptable timeframes (< 5s)
6. **Error Detection** - Checks for JavaScript console errors

### Test configuration

The portable spec is configured via [`cli/slopstopper/data/playwright.config.js`](../../cli/slopstopper/data/playwright.config.js), which is bundled in the `slopstopper-cli` wheel. `testDir: './tests'` resolves to `.ss/.run/tests/`, where each run stages the spec, so SlopStopper's specs never collide with your own `tests/` directory. An ejected config is staged there too, so any relative path in it resolves from `.ss/.run/`. To customise, eject an editable copy into your repo's `.ss/` directory:

```bash
slopstopper templates eject playwright.config.js
```

The CLI's template resolver prefers `.ss/<filename>` over the bundled version, so edits to the ejected copy take effect immediately.

### Adding pages to the smoke check

The portable smoke spec at [`cli/slopstopper/data/tests/smoke.spec.ts`](../../cli/slopstopper/data/tests/smoke.spec.ts) iterates over `SMOKE_PAGES`. Adding coverage is an env var, not a code change:

```bash
SMOKE_TEST_URL=https://your-site.example.com \
  SMOKE_PAGES="/,/login,/pricing,/about" \
  task ss:reliability:smoke
```

For assertions beyond "page returns 200 and loads cleanly" (e.g. specific element visibility), add your own specs under your repo's own `tests/` directory. A `playwright.config.js` you write in your repo root picks those up; SlopStopper's bundled config does not.

### Troubleshooting

- CI installs the Playwright browsers with `npx playwright install --with-deps chromium`, five minutes per attempt and one retry, so a stalled download fails the job instead of holding it. Locally, run that once (or let the spec install on demand).
- Timeouts and retries live in the bundled Playwright config: `slopstopper templates eject playwright.config.js` and edit the copy under `.ss/`.
