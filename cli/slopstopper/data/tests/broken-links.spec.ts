import { test, expect } from '@playwright/test';

const targetUrl =
  process.env.BROKEN_LINKS_TEST_URL ||
  process.env.SMOKE_TEST_URL ||
  process.env.BASE_URL ||
  'http://localhost:8080';

const pagesToScan = (process.env.BROKEN_LINKS_PAGES ?? '/')
  .split(',')
  .map((s) => s.trim())
  .filter(Boolean);

/**
 * A configured page path, resolved under the base URL. Paths are relative to
 * the base even with a leading `/`, so `/about` under
 * `https://org.github.io/project/` is `/project/about`, never the host root.
 */
function pageUrl(path: string): string {
  const base = targetUrl.endsWith('/') ? targetUrl : `${targetUrl}/`;
  return new URL(path.replace(/^\/+/, ''), base).href;
}

test.describe('Broken Link Checks', () => {
  test.use({ baseURL: targetUrl });

  test('internal links return successful responses', async ({ page, request }) => {
    const root = pageUrl('/');
    const links = new Set<string>();

    for (const path of pagesToScan) {
      await page.goto(pageUrl(path));
      const hrefs = await page.locator('a[href]').evaluateAll((anchors) =>
        anchors
          .map((a) => a.getAttribute('href'))
          .filter((href): href is string => Boolean(href)),
      );

      for (const href of hrefs) {
        if (/^(#|mailto:|tel:|javascript:|data:|vbscript:)/i.test(href)) {
          continue;
        }

        const resolved = new URL(href, page.url());
        // Off-site, or on this host but outside the base path (another
        // project's site under the same github.io origin): not ours to check.
        if (!resolved.href.startsWith(root)) {
          continue;
        }

        // Fragments do not affect HTTP retrieval, so only pathname+query are checked.
        links.add(`${resolved.pathname}${resolved.search}`);
      }
    }

    const brokenLinks: string[] = [];
    for (const linkPath of links) {
      const response = await request.get(linkPath);
      if (response.status() >= 400) {
        brokenLinks.push(`${linkPath} returned ${response.status()}`);
      }
    }

    expect(brokenLinks, brokenLinks.join('\n')).toEqual([]);
  });
});
