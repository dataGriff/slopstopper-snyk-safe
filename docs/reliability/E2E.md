# E2E Journeys

## Overview

Smoke proves each page renders. The E2E check proves a visitor can get
around: it drives a browser through the journeys any HTML site offers,
without knowing anything about yours. From every start path the bundled
Playwright spec:

1. **Walks the primary navigation.** Clicks each same-origin link inside the
   first `<nav>` on the page, plus any other link in the `<header>` that
   holds it (a call-to-action button, say), and asserts the destination
   answers, shows an `<h1>` and still carries a nav, with no JavaScript
   errors along the way.
   Links a visitor could not follow in the same tab are left out, not
   failed: hidden ones (a collapsed mobile menu, a hover dropdown),
   `target="_blank"`, downloads and non-HTML files. `/about.html`,
   `/about/` and `/about` count as the same destination, so a host that
   rewrites URLs still passes.
2. **Follows in-page anchors.** Every `href="#…"` must point at an element
   that exists.
3. **Toggles every disclosure.** Each visible `<details>` flips on click
   and flips back on the next, whichever state it started in.
4. **Presses back.** After following a nav link, the browser's back button
   must land on the start page.

A journey with nothing to do is skipped, not failed: a page with no
`<nav>`, no anchors or no `<details>` reports skips for those steps. The
start path itself must answer, though: a `pages.e2e` entry that 404s
fails every journey for that path.

The spec names no selector or page of this site, so it runs unchanged on
any adopter's site. Journeys specific to yours (log in, add to basket,
submit a form) belong in an ejected copy, as
[Adding your own journeys](#adding-your-own-journeys) describes.

## Quick Start

```bash
# Against a running site (any reliability check takes the URL positionally)
task ss:reliability:e2e -- https://your-site.example.com

# CI mode: retries, HTML report, single worker
task ss:reliability:e2e -- https://your-site.example.com --ci

# Or via the env var
E2E_TEST_URL=https://your-site.example.com task ss:reliability:e2e
```

Report: `.ss/reports/reliability/e2e-report.md`, with Playwright's HTML
report under `playwright-report/` naming the failing step.

## Configuration

```yaml
# .slopstopper.yml
pages:
  e2e: /,/pricing      # start paths for the journeys (default /)
e2e:
  max_links: 25        # nav links followed per start path
```

| Variable | Default | Description |
|---|---|---|
| `E2E_TEST_URL` | *(falls back to `SMOKE_TEST_URL` / `BASE_URL` / `localhost:8080`)* | Base URL to walk |
| `E2E_PAGES` | `pages.e2e`, else `/` | Comma-separated start paths. In CI the workflow sets it from `slopstopper discover e2e`, so `reliability.coverage.*` (sitemap or changed files) applies here as to the other page-walking checks |
| `E2E_MAX_LINKS` | `e2e.max_links`, else `25` | Nav links followed per start path; also caps the `<details>` toggled |

Env vars win over `.slopstopper.yml`, so a one-off run can widen or narrow
the walk without editing config.

## GitHub Actions Workflow

**File:** [`.github/workflows/ss-reliability-e2e-check.yml`](../../.github/workflows/ss-reliability-e2e-check.yml)

| Trigger | URL walked |
|---|---|
| `pull_request` → `main` | Local build (`localhost:8080`) |
| `push` → `main` | Local build (`localhost:8080`) |
| `deployment_status` (success) | The deployment's `environment_url` (else `target_url`); never a GitHub Actions log link |
| `schedule` (daily 05:00 UTC) | Production URL (`urls.production`) |
| `workflow_dispatch` | The `url` input, or the local build when blank |

The job builds and serves the repo with `slopstopper serve` on PRs and
pushes, runs `task ss:reliability:e2e -- <url> --ci`, uploads the
Playwright report, posts a rolling PR comment, and on `main` opens (or
closes) a tracking issue. It ships under the `ui` profile only: like the
other browser checks, `--profile api` and `--profile library` drop it (see
[project-shape profiles](../architecture/PROFILES.md)).

## Adding your own journeys

The bundled spec is deliberately generic. For journeys that know your
site, eject it and extend the copy; the CLI picks `.ss/tests/e2e.spec.ts`
up in place of the bundled one:

```bash
slopstopper templates eject tests/e2e.spec.ts
```

```ts
// .ss/tests/e2e.spec.ts, appended to the bundled journeys
test('pricing → checkout', async ({ page }) => {
  await page.goto('/pricing');
  await page.getByRole('link', { name: 'Start free trial' }).click();
  await expect(page.getByRole('heading', { name: 'Create your account' })).toBeVisible();
});
```

An ejected copy is yours: a later CLI release will not overwrite it, so
pull in bundled improvements by diffing against
[`cli/slopstopper/data/tests/e2e.spec.ts`](../../cli/slopstopper/data/tests/e2e.spec.ts).

## Understanding failures

The test title says which start path and which journey failed; the
assertion message says which link, anchor or disclosure:

```
✘ /: primary navigation round trip
  features.html: page should have a heading
```

| Message | Usual cause |
|---|---|
| `start page should answer 2xx/3xx` | A `pages.e2e` entry (or `E2E_PAGES`) names a path the site doesn't serve |
| `should land on that page, got …` | The link redirected somewhere else (an off-site hop, a login wall, a locale redirect that changes the path entirely) |
| `page should have a heading` | The destination rendered without an `<h1>`, or an error page |
| `primary navigation should still be present` | The destination is outside the site shell (a bare document, a redirect off-site) |
| `in-page anchors with no target element` | A table-of-contents link to a heading that was renamed or removed (`#top` and `<a name>` targets are accepted) |
| `<details> … should open on click` | A script intercepts the click, or the summary is covered by another element |
| `back button should return to the start page` | The nav link navigated with `location.replace`, or the destination redirected |

## Troubleshooting

**Every journey is skipped:** the start page's header has no visible
same-origin links in its first `<nav>` or beside it, perhaps because the
menu is collapsed behind a hamburger at desktop width. Either wrap your primary navigation in a
visible `<nav>` (good for accessibility too) or eject the spec and open
the menu in `navTargets` before collecting links.

**`e2e.max_links` is ignored:** it must be a positive integer; anything
else falls back to 25 with a warning in the check's output.

**The walk is slow:** each nav link is a fresh page load from the start
path. Lower `e2e.max_links`, or list fewer start paths in `pages.e2e`.

**Tests fail locally but pass in CI (or vice versa):** check the URL the
run resolved to (`E2E_TEST_URL` or the positional), that it is reachable,
and that the Playwright browsers are installed
(`npx playwright install --with-deps chromium`).

## Related Documentation

- [Reliability README](README.md), the env-var contract shared by every reliability check
- [Accessibility](ACCESSIBILITY.md), the other page-walking Playwright check
- [Playwright locators](https://playwright.dev/docs/locators), for writing your own journeys
