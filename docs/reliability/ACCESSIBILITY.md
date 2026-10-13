# Accessibility Monitoring

## Overview

This directory documents automated accessibility monitoring for the site.
Accessibility is audited on every pull request, push to `main`, successful deployment, and on a daily schedule.

The audit uses [axe-core](https://github.com/dequelabs/axe-core) via [`@axe-core/playwright`](https://github.com/dequelabs/axe-core-packages/tree/develop/packages/playwright) to check all pages against **WCAG 2.1 AA** rules.

## Quick Start

**Run locally against the local build (recommended for development):**

```bash
# Builds and starts the server automatically, then audits all pages
task ss:reliability:accessibility
```

**Run against a deployed URL:**

```bash
task ss:reliability:accessibility -- https://your-site.example.com

# Or using an environment variable
ACCESSIBILITY_TEST_URL=https://your-site.example.com task ss:reliability:accessibility
```

**Run in CI mode (retries + HTML report):**

```bash
task ss:reliability:accessibility -- https://your-site.example.com --ci
```

## Configuration

All settings are controlled via environment variables. Defaults are deliberately strict.

| Variable | Default | Description |
|---|---|---|
| `ACCESSIBILITY_TEST_URL` | *(falls back to `SMOKE_TEST_URL` / `BASE_URL` / `localhost:8080`)* | Base URL to audit |
| `ACCESSIBILITY_PAGES` | `/` | Comma-separated paths to audit, e.g. `/,/features.html,/tools.html` |
| `ACCESSIBILITY_IMPACT` | `serious` | Minimum [axe impact level](https://github.com/dequelabs/axe-core/blob/develop/doc/API.md#axe-core-tags) to flag: `critical`, `serious`, `moderate`, `minor` |
| `ACCESSIBILITY_THRESHOLD` | `0` | Maximum violations allowed before the check fails |

### Impact Levels

axe-core classifies each violation by impact:

| Impact | Meaning |
|---|---|
| `critical` | Completely blocks access for some users (e.g. no alt text on a link image) |
| `serious` | Significantly impairs access (e.g. insufficient colour contrast) |
| `moderate` | Creates difficulties but not a complete barrier |
| `minor` | Minor issue, best-practice recommendation |

The default threshold (`serious`) means the workflow fails when any `critical` or `serious` violation is found, and reports (but does not fail for) `moderate`/`minor` issues.

## GitHub Actions Workflow

**File:** [`.github/workflows/ss-reliability-accessibility-check.yml`](../../.github/workflows/ss-reliability-accessibility-check.yml)

### Triggers

| Trigger | URL audited |
|---|---|
| `pull_request` → `main` | Local build (`localhost:8080`) |
| `push` → `main` | Local build (`localhost:8080`) |
| `deployment_status` (success) | The deployment's `environment_url` (else `target_url`); never a GitHub Actions log link |
| `schedule` (daily 06:00 UTC) | Production URL (`urls.production`) |
| `workflow_dispatch` | The `url` input, or the local build when blank |

### Manual Trigger

Run the workflow manually from the **Actions** tab with optional inputs:

- **URL** – override the audit target
- **Impact** – minimum impact level to fail on (`critical` / `serious` / `moderate` / `minor`)
- **Threshold** – maximum allowed violations (useful for gradual remediation)

### What the Workflow Does

1. Starts the local Node.js server (PR/push builds) or uses the deployment URL
2. Runs `task ss:reliability:accessibility -- --ci` which executes the accessibility spec via Playwright
3. Uploads the Playwright HTML report as a workflow artifact (30-day retention)
4. Posts a PR comment with the audit result and a local reproduction command
5. Creates (or updates) a GitHub issue when violations land on `main`
6. Closes the issue automatically when the audit passes again

## Pages Audited

The portable spec at [`cli/slopstopper/data/tests/accessibility.spec.ts`](../../cli/slopstopper/data/tests/accessibility.spec.ts) reads the page list from the `ACCESSIBILITY_PAGES` env var (comma-separated; defaults to `/`). For this repo's CI, the accessibility workflow sets:

```yaml
ACCESSIBILITY_PAGES: /,/features.html,/tools.html
```

To add coverage for your own app:

```bash
ACCESSIBILITY_PAGES="/,/login,/dashboard" \
  ACCESSIBILITY_TEST_URL=https://your-site.example.com \
  task ss:reliability:accessibility
```

No code changes required.

## Understanding Results

When violations are found, the test output shows:

```
[SERIOUS] color-contrast: Ensures the contrast between foreground and background colors meets WCAG 2 AA contrast ratio thresholds
  - .hero-subtitle
  - .nav-link

[CRITICAL] image-alt: Ensures <img> elements have alternate text or a role of none or presentation
  - img.hero-logo
```

Each entry includes:
- **Impact level** in brackets
- **Rule ID** – links to axe documentation
- **Description** – what the rule checks
- **Element selectors** – which elements triggered the violation (first 3 shown)

## Fixing Violations

### Critical: Missing alt text

```html
<!-- ❌ Before -->
<img src="logo.png">

<!-- ✅ After -->
<img src="logo.png" alt="SlopStopper logo">
```

### Serious: Insufficient colour contrast

Use a [contrast checker](https://webaim.org/resources/contrastchecker/) to find accessible colour combinations. WCAG AA requires a ratio of **4.5:1** for normal text and **3:1** for large text.

### Moderate: Missing form labels

```html
<!-- ❌ Before -->
<input type="text" placeholder="Enter text">

<!-- ✅ After -->
<label for="text-input">Enter text</label>
<input id="text-input" type="text">
```

### Minor: Landmark regions

```html
<!-- ✅ Wrap main content in a <main> landmark -->
<main>
  <h1>Page heading</h1>
  ...
</main>
```

## Gradual Remediation

If the audit reveals many existing violations and you want to fix them incrementally, temporarily raise the threshold:

```bash
# Allow up to 5 violations while you work through the backlog
ACCESSIBILITY_THRESHOLD=5 task ss:reliability:accessibility
```

Update `ACCESSIBILITY_THRESHOLD` in the workflow `workflow_dispatch` default and in the scheduled job once remediation progresses.

## Troubleshooting

**Tests fail locally but pass in CI (or vice versa):**
- Ensure `ACCESSIBILITY_TEST_URL` (or the positional URL) points to the correct target
- Verify the URL is actually reachable (`curl -I "$URL"`), starting your dev server first if you're auditing a local build
- The CI workflow installs the Playwright browsers via `npx playwright install --with-deps chromium`; locally, run the same command once before your first invocation

**False positives:**
- Suppress specific rules with `axeBuilder.disableRules(['rule-id'])` in the test file
- Consult the [axe-core rule descriptions](https://dequeuniversity.com/rules/axe/4.7/) to confirm whether the finding is applicable

**Tests timeout:**
- Check the target URL is reachable
- Increase the Playwright timeout by running `slopstopper templates eject playwright.config.js` and editing the ejected copy that lands in your `.ss/` directory

## Related Documentation

- [axe-core API documentation](https://github.com/dequelabs/axe-core/blob/develop/doc/API.md)
- [@axe-core/playwright](https://github.com/dequelabs/axe-core-packages/tree/develop/packages/playwright)
- [WCAG 2.1 guidelines](https://www.w3.org/TR/WCAG21/)
- [WebAIM contrast checker](https://webaim.org/resources/contrastchecker/)
- [Reliability Smoke Tests](README.md)
