# SEO & Social-Share Metatag Check

SlopStopper ships two complementary SEO-flavoured checks:

1. **Lighthouse SEO category** runs as part of `ss:reliability:cwv`, gated at score ≥ 0.9 (see [`cli/slopstopper/data/lighthouserc.json`](../../cli/slopstopper/data/lighthouserc.json)). It covers the SEO basics of meta description, `viewport`, `lang`, canonical, robots, indexability and link-text quality.
2. **SEO metatag check** is `ss:reliability:seo`, implemented in [`cli/slopstopper/checks/seo.py`](../../cli/slopstopper/checks/seo.py). It validates the social-share tags Lighthouse does **not** flag (OpenGraph + Twitter Card) plus reachability of the OG image.

The metatag check is intentionally Python stdlib only, so it adds no dependencies on top of Python 3.

## What gets validated

For each page in `SEO_PAGES`, the script asserts the following are present and non-empty:

**Core SEO**

- `<title>` (warns if > 70 chars)
- `<meta name="description">` (warns if > 160 chars)
- `<meta name="viewport">`
- `<link rel="canonical">`

**OpenGraph**

- `og:title`, `og:description`, `og:type`, `og:url`
- `og:image` (skip it with `SEO_REQUIRE_OG_IMAGE=0`)
- If `og:image` is present and `SEO_VERIFY_OG_IMAGE` is not `0`, the script HEAD-fetches the URL and asserts a 200 response with an `image/*` content type.

**Twitter Card**

- `twitter:card` (warns if not `summary`, `summary_large_image`, `app` or `player`)
- `twitter:title`, `twitter:description`
- `twitter:image` (same toggle as `og:image`)

## Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `SEO_TEST_URL` | (none, required) | Base URL to audit |
| `SEO_PAGES` | `/` | Comma-separated paths, e.g. `/,/features.html,/tools.html` |
| `SEO_REQUIRE_OG_IMAGE` | `1` | `0` to skip OG/Twitter image presence assertions |
| `SEO_VERIFY_OG_IMAGE` | `1` | `0` to skip the HEAD reachability check on the image URL |

## Running it

```bash
# Audit a deployed site
task ss:reliability:seo -- https://your-site.example.com

# Audit the local build with specific paths
SEO_TEST_URL=http://localhost:8080 \
SEO_PAGES="/,/features.html,/tools.html" \
task ss:reliability:seo

# Skip the og:image reachability check (useful if the image lives behind auth or a CDN)
SEO_TEST_URL=https://your-site.com \
SEO_VERIFY_OG_IMAGE=0 \
task ss:reliability:seo
```

Generated reports are written to:

- `.ss/reports/seo/seo-metatags-report.md` (human-readable)
- `.ss/reports/seo/seo-metatags-report.json` (machine-readable)

## Why this exists

Lighthouse's SEO category passes a site with no OpenGraph or Twitter Card metadata as long as the basics (description, viewport, canonical) are present. But the moment that site gets shared in Slack, Twitter/X, LinkedIn or Discord, the preview is empty, with no image, no title and no description. This check closes that gap. It's also what tools like [metatags.io](https://metatags.io/) verify by eye; this check makes the same verification deterministic and CI-gated.

## CI integration

The [`ss-reliability-seo-check.yml`](../../.github/workflows/ss-reliability-seo-check.yml) workflow runs on every PR, every push to `main`, on `deployment_status` success, daily, and on a `url` dispatch. PR runs comment back with pass/fail and a link to the artefact.
