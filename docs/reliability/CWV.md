# Core Web Vitals

`reliability:cwv` runs [Lighthouse CI](https://github.com/GoogleChrome/lighthouse-ci) against a URL and fails the build when a page's performance falls below budget. It is the browser-side speed check; [API Latency](README.md#api-latency-check) is its analogue for a JSON API.

## What it measures

Lighthouse loads the page three times (`numberOfRuns: 3`). Neither bundled config sets `assert.aggregationMethod`, so Lighthouse CI's default `optimistic` aggregation applies: each assertion is judged on the **best** of the three runs, which is what keeps one slow run on a noisy CI runner from failing the build. The headline metrics and their default budgets:

| Metric | Budget | Fails? |
|---|---|---|
| Performance score | ≥ 70 / 100 | yes |
| SEO score | ≥ 90 / 100 | yes |
| Largest Contentful Paint (LCP) | ≤ 4 s | yes |
| Total Blocking Time (TBT) | ≤ 600 ms | yes |
| Cumulative Layout Shift (CLS) | ≤ 0.25 | yes |
| First Contentful Paint (FCP) | ≤ 3 s | warn only |

The production config (`--prod`) keeps those and adds advisory warnings for Accessibility and Best Practices scores (≥ 80), Speed Index (≤ 5.8 s) and Time to Interactive (≤ 7.3 s). Warnings appear in the report and never fail the run.

These are deliberately loose, because a static site should clear them easily and a budget that fires on CI-runner noise is worse than none. Tighten them once you have a few weeks of numbers.

## Configuration

The budgets live in Lighthouse's own config, not `.slopstopper.yml`. The CLI ships two inside the wheel, `cli/slopstopper/data/lighthouserc.json` (dev) and `cli/slopstopper/data/lighthouserc.prod.json` (prod). To change a budget, eject the one you want and edit the copy:

```bash
slopstopper templates eject lighthouserc.json        # → .ss/lighthouserc.json
slopstopper templates eject lighthouserc.prod.json   # → .ss/lighthouserc.prod.json
```

The check resolves `.ss/lighthouserc[.prod].json` first and falls back to the bundled copy, so an ejected file is picked up automatically. `--config <path>` overrides both.

The dev config skips one audit, `is-crawlable`: preview hosts (workers.dev, Vercel, Netlify) answer `X-Robots-Tag: noindex` by design, which would fail that audit and sink the SEO category to about 0.6 on every preview. The prod config keeps it, because noindex in production is a real bug. Two preview-only SEO symptoms and their fixes are in the triage skill's gotchas.

## Running locally

```bash
task ss:reliability:cwv -- http://localhost:8080          # dev budgets
task ss:reliability:cwv -- https://example.com --prod      # production budgets
CWV_URL=http://localhost:8080 task ss:reliability:cwv
```

Needs `node` (Lighthouse runs in headless Chrome via `npx lhci`). The check generates `cwv-report.md` under `.ss/reports/cwv/`. It holds the threshold table with pass/fail per metric. The full Lighthouse HTML reports land in `.ss/reports/cwv/lighthouse/` and raw output in `.lighthouseci/`; both stay local, and the workflow uploads them as the `lighthouse-reports` artifact.

Nothing leaves the runner by default. Lighthouse reports carry screenshots and page content, so publishing them to Lighthouse's temporary public storage is opt-in: set `reliability.cwv.public_report: true` in `.slopstopper.yml` and the report links the public copy. Leave it off for private repos, authenticated previews and internal apps. The CLI sets the upload target on every run, so a Lighthouse config ejected with `slopstopper templates eject` can't enable it.

## Running in CI

`ss-reliability-core-web-vitals.yml` picks the URL and the budget set from the event, in its `Determine audit URL` step:

| Event | URL audited | Budgets |
|---|---|---|
| `pull_request`, `push` | the site built and served on `localhost:8080` | dev |
| `workflow_dispatch` | the `url` input, else `urls.production` | `--prod` when the URL is `urls.production`, else dev |
| `deployment_status` (success; e.g. a Pages or platform deploy) | that deployment's own `environment_url` (else `target_url`), so a preview deploy audits the preview. A `github.com/…/actions/` log link is never audited: `urls.production` is used instead, or a local build | `--prod` when the URL is `urls.production`, else dev |
| `schedule` (daily) | `urls.production` | `--prod` |

It posts the rolling PR comment, opens a tracking issue when `main` regresses, and closes that issue automatically on the next green run.

## Reading a failure

- **Performance score under 70 with LCP over budget.** Almost always an unoptimised hero image or a render-blocking stylesheet. Lighthouse's HTML report in `.ss/reports/cwv/lighthouse/` (linked from the check's report only with `public_report`) names the resource.
- **CLS over 0.25.** An image or embed without explicit `width`/`height`, or a web font swapping in late.
- **TBT over 600 ms.** Long JavaScript tasks on load. On a static site this usually means a third-party script.
- **Everything passes locally, fails in CI.** The CI runner is slower and noisier than your laptop. Look at all three runs in the Lighthouse output before touching a budget. The gate passes on the best one, so a failure means every run missed; if the numbers are consistently near the line, the budget is telling you something.

Dropped by `--profile api` and `--profile library`; there is no page to render.
