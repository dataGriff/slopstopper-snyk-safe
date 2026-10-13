import { defineConfig, devices } from '@playwright/test';

/**
 * SlopStopper portable Playwright config.
 *
 * testDir is `./tests` relative to this file, so specs under `.ss/tests/`
 * are picked up regardless of what the consumer has under their own
 * `tests/` directory.
 *
 * There is no webServer block: the consumer (or task) must make sure the
 * target URL is reachable before tests run. The base URL is taken from
 * one of: ACCESSIBILITY_TEST_URL, SMOKE_TEST_URL, E2E_TEST_URL, BASE_URL, or
 * http://localhost:8080 as a final fallback.
 */
export default defineConfig({
  testDir: './tests',
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: process.env.CI ? 1 : undefined,
  reporter: 'html',
  use: {
    baseURL:
      process.env.BROKEN_LINKS_TEST_URL ||
      process.env.ACCESSIBILITY_TEST_URL ||
      process.env.SMOKE_TEST_URL ||
      process.env.E2E_TEST_URL ||
      process.env.BASE_URL ||
      'http://localhost:8080',
    trace: 'on-first-retry',
  },
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
  ],
});
