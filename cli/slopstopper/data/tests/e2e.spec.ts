import { test, expect, type Page } from '@playwright/test';

/**
 * Portable end-to-end journeys for any web site.
 *
 * Smoke proves each page renders; this proves a visitor can get around.
 * From every start path it:
 *
 *   1. clicks each link under the base URL in the primary navigation (the first
 *      <nav> on the page, plus any other link in the <header> that holds
 *      it, such as a call-to-action button) and asserts the destination
 *      answers, shows a heading and still carries a nav;
 *   2. follows every in-page anchor (href="#…") to an element that exists;
 *   3. toggles every visible <details> disclosure and back;
 *   4. presses the browser's back button and lands where it started.
 *
 * Each start path must itself answer (status < 400): a path listed in
 * E2E_PAGES that 404s is a failure, not a skip. Links the spec cannot
 * follow in the same tab (hidden, target="_blank", download, non-HTML
 * files) are left out rather than failed, and a destination is matched
 * loosely (`/about.html`, `/about/` and `/about` are the same page) so a
 * host that rewrites URLs still passes.
 *
 * Nothing here names a selector or a page of a specific site, so the spec
 * runs unchanged on any HTML site. A journey that is specific to yours
 * (log in, add to basket, submit a form) belongs in an ejected copy:
 * `slopstopper templates eject tests/e2e.spec.ts`, then edit `.ss/tests/e2e.spec.ts`.
 *
 * Configuration:
 *   E2E_TEST_URL   base URL (falls back to SMOKE_TEST_URL / BASE_URL / localhost:8080)
 *   E2E_PAGES      comma-separated start paths, default '/'
 *   E2E_MAX_LINKS  nav links followed per start path, default 25
 *
 * Usage:
 *   task ss:reliability:e2e -- https://your-site.example.com
 *   E2E_PAGES='/,/pricing' task ss:reliability:e2e -- https://your-site.example.com
 */

const targetUrl =
  process.env.E2E_TEST_URL ||
  process.env.SMOKE_TEST_URL ||
  process.env.BASE_URL ||
  'http://localhost:8080';

const startPaths = (process.env.E2E_PAGES ?? '/')
  .split(',')
  .map((s) => s.trim())
  .filter(Boolean);

const DEFAULT_MAX_LINKS = 25;
const parsedMax = parseInt(process.env.E2E_MAX_LINKS || '', 10);
const maxLinks = Number.isFinite(parsedMax) && parsedMax > 0 ? parsedMax : DEFAULT_MAX_LINKS;

/**
 * A configured page path, resolved under the base URL. Paths are relative to
 * the base even with a leading `/`, so `/about` under
 * `https://org.github.io/project/` is `/project/about`, never the host root.
 */
function pageUrl(path: string): string {
  const base = targetUrl.endsWith('/') ? targetUrl : `${targetUrl}/`;
  return new URL(path.replace(/^\/+/, ''), base).href;
}

/** Files a browser downloads or renders without a site shell, so never a page to walk. */
const NON_HTML = /\.(pdf|xml|txt|json|rss|atom|zip|gz|tar|csv|ics|png|jpe?g|gif|svg|webp|avif|ico|mp[34]|webm|woff2?|css|js|mjs)$/i;

/** `/about.html`, `/about/` and `/about` are the same page; so are `/` and `/index.html`. */
function normalisePath(pathname: string): string {
  const stripped = pathname
    .replace(/\/index\.html?$/i, '/')
    .replace(/\.html?$/i, '')
    .replace(/\/+$/, '');
  return stripped || '/';
}

/**
 * The page landed where `href` points, allowing for `.html` stripping, trailing
 * slashes and a locale prefix. `from` is the URL the link was clicked on: a
 * relative href resolves against that, not against where the click landed.
 */
function landedOn(page: Page, href: string, from: string): boolean {
  const expected = normalisePath(new URL(href, from).pathname);
  const actual = normalisePath(new URL(page.url()).pathname);
  return expected === '/' ? true : actual === expected || actual.endsWith(expected);
}

/** Quote a value for use inside a CSS attribute selector. */
function cssString(value: string): string {
  return `"${value.replace(/["\\]/g, '\\$&')}"`;
}

/** Load a start path and fail (not skip) if it doesn't answer: a wrong `pages.e2e` entry is a verdict. */
async function openStart(page: Page, start: string): Promise<void> {
  const response = await page.goto(pageUrl(start));
  expect(response, `${start}: start page should respond`).not.toBeNull();
  expect(response!.status(), `${start}: start page should answer 2xx/3xx, got ${response!.status()}`).toBeLessThan(400);
}

/** The primary navigation: the first <nav>, widened to the <header> that holds it. */
function primaryNav(page: Page) {
  // `:has()` keeps the scope to a header that actually contains a nav; a
  // page whose nav sits outside any header falls back to the nav itself.
  return page.locator('header:has(nav), nav').first();
}

/** A visible anchor in the primary navigation with exactly this href. */
function navLink(page: Page, href: string) {
  return primaryNav(page).locator(`a[href=${cssString(href)}]`).filter({ visible: true }).first();
}

/**
 * Visible, same-tab page links under the base URL from the primary navigation, in
 * document order, deduped by destination (fragment included, so a header
 * call-to-action that deep-links into the home page is walked as well as the
 * Home link). Hidden links (a collapsed mobile menu, a hover dropdown),
 * new-tab links, downloads and non-HTML files are left out: the spec can't
 * follow them in place, and a site that has them is not broken.
 */
async function navTargets(page: Page): Promise<string[]> {
  const root = pageUrl('/');
  const hrefs = await primaryNav(page).locator('a[href]').evaluateAll((anchors) =>
    anchors.flatMap((el) => {
      const a = el as HTMLAnchorElement;
      if (a.target === '_blank' || a.hasAttribute('download') || !a.checkVisibility()) return [];
      return [a.getAttribute('href') ?? ''];
    }),
  );
  const seen = new Set<string>();
  const out: string[] = [];
  for (const href of hrefs) {
    if (!href || href.startsWith('#') || /^(mailto|tel|javascript):/i.test(href)) continue;
    let url: URL;
    try {
      url = new URL(href, page.url());
    } catch {
      continue;
    }
    if (!url.href.startsWith(root) || NON_HTML.test(url.pathname)) continue;
    const key = normalisePath(url.pathname) + url.search + url.hash;
    if (seen.has(key)) continue;
    seen.add(key);
    out.push(href);
    if (out.length >= maxLinks) break;
  }
  return out;
}

test.describe('E2E Journeys', () => {
  test.use({ baseURL: targetUrl });

  for (const start of startPaths) {
    test(`${start}: primary navigation round trip`, async ({ page }) => {
      const errors: Error[] = [];
      page.on('pageerror', (e) => errors.push(e));

      await openStart(page, start);
      const targets = await navTargets(page);
      test.skip(targets.length === 0, `${start}: no followable links under the base URL inside a <nav>`);

      for (const href of targets) {
        await page.goto(pageUrl(start));
        const from = page.url();
        const link = navLink(page, href);
        await expect(link, `${start}: nav link ${href} should be visible`).toBeVisible();

        // click() waits for a navigation it starts to commit; a link to the
        // current page may not navigate at all, which is fine.
        await link.click();
        await page.waitForLoadState('domcontentloaded');

        expect(landedOn(page, href, from), `${href}: should land on that page, got ${page.url()}`).toBe(true);
        await expect(page.locator('h1').first(), `${href}: page should have a heading`).toBeVisible();
        expect(await page.locator('nav a[href]').count(), `${href}: primary navigation should still be present`)
          .toBeGreaterThan(0);
      }

      expect(errors, `${start}: journey emitted JS errors: ${errors.map((e) => e.message).join('; ')}`)
        .toHaveLength(0);
    });

    test(`${start}: in-page anchors resolve`, async ({ page }) => {
      await openStart(page, start);
      // Resolved in-page: getElementById / getElementsByName need no selector
      // escaping, and `#top` is the browser's own scroll-to-top, never an element.
      const result = await page.evaluate(() => {
        const ids = new Set<string>();
        for (const a of Array.from(document.querySelectorAll<HTMLAnchorElement>('a[href^="#"]'))) {
          const raw = (a.getAttribute('href') ?? '').slice(1);
          if (!raw) continue;
          let id = raw;
          try {
            id = decodeURIComponent(raw);
          } catch {
            /* keep the raw fragment; browsers fall back the same way */
          }
          if (id.toLowerCase() === 'top') continue;
          ids.add(id);
        }
        const missing = [...ids].filter(
          (id) => !document.getElementById(id) && document.getElementsByName(id).length === 0,
        );
        return { total: ids.size, missing };
      });
      test.skip(result.total === 0, `${start}: no in-page anchors`);

      expect(result.missing, `${start}: in-page anchors with no target element: ${result.missing.map((id) => `#${id}`).join(', ')}`)
        .toHaveLength(0);
    });

    test(`${start}: disclosure widgets open and close`, async ({ page }) => {
      await openStart(page, start);
      const summaries = page.locator('details > summary');
      const total = Math.min(await summaries.count(), maxLinks);
      test.skip(total === 0, `${start}: no <details> on the page`);

      let toggled = 0;
      for (let i = 0; i < total; i++) {
        const summary = summaries.nth(i);
        // A summary inside a closed parent <details> or a hidden menu can't be
        // clicked by a visitor either: skip it rather than fail it.
        if (!(await summary.isVisible())) continue;
        const details = summary.locator('xpath=..');
        const wasOpen = await details.evaluate((d) => (d as HTMLDetailsElement).open);

        await summary.scrollIntoViewIfNeeded();
        await summary.click();
        if (wasOpen) {
          await expect(details, `${start}: <details> #${i + 1} should close on click`).not.toHaveAttribute('open', '');
        } else {
          await expect(details, `${start}: <details> #${i + 1} should open on click`).toHaveAttribute('open', '');
        }
        await summary.click();
        if (wasOpen) {
          await expect(details, `${start}: <details> #${i + 1} should reopen on second click`).toHaveAttribute('open', '');
        } else {
          await expect(details, `${start}: <details> #${i + 1} should close on second click`).not.toHaveAttribute('open', '');
        }
        toggled++;
      }
      test.skip(toggled === 0, `${start}: no visible <details> on the page`);
    });

    test(`${start}: browser back returns to the start`, async ({ page }) => {
      await openStart(page, start);
      const startPath = normalisePath(new URL(page.url()).pathname);
      const targets = await navTargets(page);
      const away = targets.find((href) => normalisePath(new URL(href, page.url()).pathname) !== startPath);
      test.skip(!away, `${start}: no nav link leads to another page`);

      const from = page.url();
      await navLink(page, away!).click();
      await page.waitForLoadState('domcontentloaded');
      expect(landedOn(page, away!, from), `${away}: should land on that page, got ${page.url()}`).toBe(true);

      await page.goBack();
      await page.waitForLoadState('domcontentloaded');
      expect(normalisePath(new URL(page.url()).pathname), `${start}: back button should return to the start page`)
        .toBe(startPath);
    });
  }
});
