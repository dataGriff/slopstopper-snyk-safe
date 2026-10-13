# sitemap.xml Completeness + Drift Check

`ss:reliability:sitemap`, implemented in [`cli/slopstopper/checks/sitemap.py`](../../cli/slopstopper/checks/sitemap.py), crawls your site from `/` and cross-checks the served `sitemap.xml` against the pages it actually reaches. Where [`ss:reliability:llms-txt`](LLMS_TXT.md) and [`ss:reliability:robots-txt`](ROBOTS_TXT.md) assert those files *exist and are well-formed*, this check asserts `sitemap.xml` is **complete and current**. It is a black-box assertion that the served sitemap mirrors the reachable site, **independent of how the file is produced** (hand-authored or generated).

The check is Python stdlib only (`urllib` + `html.parser` + `ElementTree`), so it adds no dependencies on top of Python 3.

## What gets validated

The check performs one BFS crawl from `/` (following same-origin `<a href>` links), then diffs the reachable page set against the sitemap and, advisorily, against `llms.txt`.

**Hard-fail (exit 1):**

- `sitemap.xml` is reachable (HTTP 200) and valid XML
- **No reachable page is missing from the sitemap**, the incompleteness guard
- **No sitemap `<loc>` 404s**, because a dead entry is stale drift

**Advisory (notes only, unless escalated via config):**

- **Orphans.** A sitemap entry that resolves (HTTP 200) but nothing internally links to. Escalate with `allow_orphans: false` / `--strict-orphans`.
- **llms.txt gaps.** A reachable page not listed in `llms.txt`. `llms.txt` is a *curated* map, so this is advisory; escalate with `require_llms_complete` / `--require-llms-complete`.
- **Crawl cap.** Hitting `max_pages` is logged and noted (never silent), so a truncated crawl can't masquerade as full coverage.

> **Scope note.** The crawler sees only server-rendered `<a href>` links, so a JS-rendered SPA will under-crawl. Use `ignore_paths` / `max_pages` to bound noisy sections, or point the check at your built static output.

## Flat vs nested sitemaps

Both shapes are supported transparently and **neither is flagged**:

- A **flat `<urlset>`**, one file listing every URL.
- A **nested `<sitemapindex>`** pointing at child sitemaps (`sitemap-0.xml`, `sitemap-1.xml`, …), which the check fetches and recurses into.

A flat sitemap is fully spec-valid. Per the [sitemaps.org protocol](https://www.sitemaps.org/protocol.html) the index is the *optional* scale-out mechanism for when you exceed the 50,000-URL / 50 MB-per-file limit, so it is **recommended, not required**. Frameworks such as [`@astrojs/sitemap`](https://docs.astro.build/en/guides/integrations-guide/sitemap/) emit the index by default regardless of size; a small static site with a single flat sitemap is equally correct, so the check enforces neither shape.

## Keeping it complete: generate, don't hand-edit

The durable fix for drift is **not** to hand-patch each missing `<loc>`. It is to **generate `sitemap.xml` (and `llms.txt`) from your route inventory at build time**, so the file is *derived* from your routes and can't drift. Two common routes:

- **A framework sitemap integration**, such as `@astrojs/sitemap`, Next.js `app/sitemap.ts` or your framework's equivalent.
- **A build-time endpoint that iterates your content**, such as an Astro `src/pages/sitemap-*.xml.ts` (or `src/pages/llms.txt.ts`) that walks your content collections. Every new post/page appears automatically.

The check still earns its place when generation makes drift impossible at the source, because a generator can still have bugs, such as a collection left out of the iterator, an over-aggressive `draft` filter or a route type not enumerated. **Generation prevents drift; this check independently verifies the generator is actually complete.** The two are complementary, so when the check fails, reach for the generator first and treat a manual `<loc>` edit as the stopgap.

## Configuration

Read from environment variables or `.slopstopper.yml`'s `reliability.sitemap` block:

| Variable / key | Default | Purpose |
| --- | --- | --- |
| `SITEMAP_TEST_URL` | (none, required) | Base URL to crawl + audit |
| `SITEMAP_PATH` | `/sitemap.xml` | Path to the sitemap (`--path`) |
| `reliability.sitemap.max_pages` | `200` | Crawl bound; hitting it is logged, not silent (`--max-pages`) |
| `reliability.sitemap.ignore_paths` | `[]` | Globs excluded from crawl + diff (`--ignore`) |
| `reliability.sitemap.allow_orphans` | `true` | Sitemap entries nothing links to are advisory (`--strict-orphans` to fail) |
| `reliability.sitemap.require_llms_complete` | `false` | Fail if a reachable page is absent from llms.txt (`--require-llms-complete`) |
| `reliability.sitemap.llms_path` | `/llms.txt` | Path to llms.txt for the cross-check (`--llms-path`) |

Sitemap and `llms.txt` `<loc>`s carry absolute production URLs even when served from a local build, so the check **rebases every path onto the origin under test**. It never HEADs the production domain from a localhost run, and behaves identically in local, CI and deployed runs.

## Running it

```bash
# Crawl + audit a deployed site
task ss:reliability:sitemap -- https://your-site.example.com

# Audit the local build, bound the crawl, and require llms.txt to be complete
SITEMAP_TEST_URL=http://localhost:8080 \
task ss:reliability:sitemap -- --max-pages 50 --require-llms-complete
```

Generated reports are written to:

- `.ss/reports/sitemap/sitemap-report.md` (human-readable)
- `.ss/reports/sitemap/sitemap-report.json` (machine-readable)

Generate it from the route inventory at build time where the framework allows, so it cannot drift.

## Why this exists

`sitemap.xml` is the one discovery file *meant* to be exhaustive, yet nothing normally guards it: add a page and nothing fails if it's missing from the sitemap; delete a page and its stale `<loc>` sits there unflagged. Both failures are silent. Search engines quietly under-index new content or waste crawl budget on 404s. Making completeness and dead-entry detection a deploy-time gate turns slow, invisible SEO decay into a red check on the PR that introduced it, and points the fix at the durable remedy: generate the file from your routes.

## CI integration

The [`ss-reliability-sitemap-check.yml`](../../.github/workflows/ss-reliability-sitemap-check.yml) workflow runs on every PR, every push to `main`, on `deployment_status` success, daily, and on a `url` dispatch. PR runs comment back with pass/fail and a link to the artefact.
