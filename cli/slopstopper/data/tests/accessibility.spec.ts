import { test, expect } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';

/**
 * Accessibility Tests
 *
 * Uses axe-core to audit pages for WCAG 2.1 AA violations.
 *
 * Configuration:
 *   ACCESSIBILITY_TEST_URL  - Base URL to audit (falls back to SMOKE_TEST_URL / BASE_URL / localhost)
 *   ACCESSIBILITY_PAGES     - Comma-separated list of paths to audit (default: '/')
 *                             e.g. '/,/features.html,/tools.html'
 *   ACCESSIBILITY_THRESHOLD - Maximum allowed violations before failing (default: 0)
 *   ACCESSIBILITY_IMPACT    - Minimum impact level to flag: critical|serious|moderate|minor (default: serious)
 *
 * Run locally:
 *   task ss:reliability:accessibility
 *   ACCESSIBILITY_TEST_URL=https://your-site.example.com \
 *     ACCESSIBILITY_PAGES='/,/about' task ss:reliability:accessibility
 */

const targetUrl =
  process.env.ACCESSIBILITY_TEST_URL ||
  process.env.SMOKE_TEST_URL ||
  process.env.BASE_URL ||
  'http://localhost:8080';

// Impact levels to report as violations (everything at or above this level fails)
const IMPACT_LEVELS = ['minor', 'moderate', 'serious', 'critical'] as const;
type ImpactLevel = typeof IMPACT_LEVELS[number];

const rawImpact = (process.env.ACCESSIBILITY_IMPACT || 'serious').toLowerCase() as ImpactLevel;
const minImpact: ImpactLevel = IMPACT_LEVELS.includes(rawImpact) ? rawImpact : 'serious';
const impactThreshold = IMPACT_LEVELS.indexOf(minImpact);

// Maximum number of violations allowed before the test fails
const maxViolations = parseInt(process.env.ACCESSIBILITY_THRESHOLD || '0', 10);

/**
 * A configured page path, resolved under the base URL. Paths are relative to
 * the base even with a leading `/`, so `/about` under
 * `https://org.github.io/project/` is `/project/about`, never the host root.
 */
function pageUrl(path: string): string {
  const base = targetUrl.endsWith('/') ? targetUrl : `${targetUrl}/`;
  return new URL(path.replace(/^\/+/, ''), base).href;
}

const pagesToAudit = (process.env.ACCESSIBILITY_PAGES ?? '/')
  .split(',')
  .map((s) => s.trim())
  .filter(Boolean)
  .map((path) => ({ path, label: path === '/' ? 'homepage' : path }));

test.describe('Accessibility Audit', () => {
  test.use({ baseURL: targetUrl });

  for (const { path, label } of pagesToAudit) {
    test(`${label} meets accessibility threshold`, async ({ page }) => {
      await page.goto(pageUrl(path));
      await page.waitForLoadState('networkidle');

      const results = await new AxeBuilder({ page })
        .withTags(['wcag2a', 'wcag2aa', 'wcag21aa'])
        .analyze();

      // Filter to violations at or above the configured impact level
      const flaggedViolations = results.violations.filter((v) => {
        const idx = IMPACT_LEVELS.indexOf((v.impact ?? 'minor') as ImpactLevel);
        return idx >= impactThreshold;
      });

      if (flaggedViolations.length > 0) {
        const summary = flaggedViolations
          .map(
            (v) =>
              `[${v.impact?.toUpperCase()}] ${v.id}: ${v.description}\n` +
              v.nodes
                .slice(0, 3)
                .map((n) => `  - ${n.target.join(', ')}`)
                .join('\n'),
          )
          .join('\n\n');

        console.log(
          `\n♿ Accessibility violations on ${label} (${flaggedViolations.length} at or above "${minImpact}" impact):\n\n${summary}\n`,
        );
      }

      expect(
        flaggedViolations.length,
        `${label}: expected ≤${maxViolations} violations at "${minImpact}" impact or above, ` +
          `but found ${flaggedViolations.length}`,
      ).toBeLessThanOrEqual(maxViolations);
    });
  }
});
