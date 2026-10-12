import { test, expect } from '@playwright/test';

/**
 * Portable smoke tests for any web app.
 *
 * Checks each page returns 200, loads without console errors, and responds
 * within an acceptable time. It makes no site-specific assertions (title,
 * selectors) because the accessibility audit covers content; this just
 * proves the pages are reachable and don't crash.
 *
 * Configuration:
 *   SMOKE_TEST_URL  base URL to hit (required when running outside a repo
 *                     with its own webServer config)
 *   SMOKE_PAGES     comma-separated list of paths to check, default '/'
 *                     e.g. '/,/about,/pricing'
 *   SMOKE_TIMEOUT   per-request timeout in ms, default 5000
 *
 * Usage:
 *   SMOKE_TEST_URL=https://your-site task ss:reliability:smoke
 *   SMOKE_TEST_URL=https://your-site SMOKE_PAGES='/,/login' task ss:reliability:smoke
 */

const targetUrl = process.env.SMOKE_TEST_URL || process.env.BASE_URL || 'http://localhost:8080';

/**
 * A configured page path, resolved under the base URL. Paths are relative to
 * the base even with a leading `/`, so `/about` under
 * `https://org.github.io/project/` is `/project/about`, never the host root.
 */
function pageUrl(path: string): string {
  const base = targetUrl.endsWith('/') ? targetUrl : `${targetUrl}/`;
  return new URL(path.replace(/^\/+/, ''), base).href;
}

const pagesToCheck = (process.env.SMOKE_PAGES ?? '/')
  .split(',')
  .map((s) => s.trim())
  .filter(Boolean);

const maxLoadMs = parseInt(process.env.SMOKE_TIMEOUT || '5000', 10);

test.describe('Smoke Tests', () => {
  test.use({ baseURL: targetUrl });

  for (const path of pagesToCheck) {
    test(`${path} returns 200 and loads cleanly`, async ({ page }) => {
      const errors: Error[] = [];
      page.on('pageerror', (e) => errors.push(e));

      const startTime = Date.now();
      const response = await page.goto(pageUrl(path));
      const loadTime = Date.now() - startTime;

      expect(response, `${path}: navigation returned no response`).not.toBeNull();
      expect(response!.status(), `${path} should return 200`).toBe(200);

      await page.waitForLoadState('networkidle');

      expect(errors, `${path}: page emitted JS errors: ${errors.map((e) => e.message).join('; ')}`)
        .toHaveLength(0);

      expect(loadTime, `${path}: load took ${loadTime}ms (limit ${maxLoadMs}ms)`)
        .toBeLessThan(maxLoadMs);
    });
  }

  test('homepage has at least one stylesheet linked', async ({ page }) => {
    await page.goto(pageUrl(pagesToCheck[0]));
    const stylesheets = await page.locator('link[rel="stylesheet"]').count();
    expect(stylesheets, 'expected at least one <link rel="stylesheet"> on the homepage')
      .toBeGreaterThan(0);
  });

  // Opt-in via SMOKE_OG_IMAGE_PATH (default: /og-image.png for backward compat).
  // Set to empty string in your .slopstopper.yml under smoke.og_image_path to
  // disable it if you ship per-post share images instead of one
  // site-wide image.
  const ogImagePath = process.env.SMOKE_OG_IMAGE_PATH ?? '/og-image.png';
  if (ogImagePath) {
    test(`og image (${ogImagePath}) is publicly shareable`, async ({ request }) => {
      const response = await request.get(pageUrl(ogImagePath));
      expect(response.status(), `expected ${ogImagePath} to return 200`).toBe(200);
      expect(response.headers()['content-type']).toContain('image/png');
      expect(response.headers()['cross-origin-resource-policy']).toBe('cross-origin');
    });
  }
});
