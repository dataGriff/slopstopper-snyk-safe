"""Smoke tests against a live URL (Playwright wrapper).

Implements the reliability:smoke flow:

  slopstopper run reliability:smoke -- --url https://your-site.example.com

  which is, under the covers:

  SMOKE_TEST_URL=...
  SMOKE_OG_IMAGE_PATH=<smoke.og_image_path from .slopstopper.yml>
  SMOKE_PAGES=<pages.smoke from .slopstopper.yml>
  npx playwright test --config=<resolved playwright config>
                      <resolved smoke spec>
                      --reporter=list[,html]

Subprocess-invokes `npx playwright`. Playwright is Apache-2.0, and the
slopstopper-cli wheel ships zero Playwright code. The test specs and
Playwright config are bundled in the wheel; adopters can override by
writing same-named files under .ss/ (see slopstopper.templates).

Configuration (.slopstopper.yml, all optional):

    pages:
      smoke: /,/blog,/about       # which paths to smoke-test
    smoke:
      og_image_path: /og-image.png  # '' to skip the og-image assertion

See .slopstopper.yml.example for the canonical schema.

Exit codes:
  0: playwright tests passed
  1: playwright tests ran and failed (report still written)
  2: npx (Node.js) not available, the URL is missing, the bundled
      spec could not be found, or Playwright exited with any other
      non-zero code (the suite didn't run to a verdict).
      Playwright exiting 1 without running the tests (a config error,
      no tests found, the browser failing to launch) is 2 as well
"""

from __future__ import annotations

from slopstopper import config
from slopstopper.checks import _playwright

CHECK = _playwright.Check(
    name="smoke",
    spec_name="smoke",
    url_env="SMOKE_TEST_URL",
    url_fallbacks=(),
    title="## Smoke Test Results",
    failure_hint=(
        "The smoke tests are failing. Investigate the failing assertion in the "
        "[Playwright HTML report](playwright-report/index.html) (uploaded as an artifact in CI)."
    ),
    banner="Running smoke tests against: {url}",
    verb="smoke-test",
)

# Consumed by `slopstopper emit reliability:smoke --target {pr-comment,issue}`.
# Issue title + label match the strings the legacy workflow used in raw
# `gh issue create` so existing open issues continue to dedup post-migration.
META = {
    "report_path": str(CHECK.report_md),
    "comment_discriminator": CHECK.title,
    "issue_title": "❌ Smoke Tests Failing",
    "issue_labels": ["smoke-test-failure", "reliability"],
    "issue_followup": "🔔 Smoke tests failing again in commit",
    "issue_close_comment": "✅ Smoke tests are now passing on `main`. Closing automatically.",
}


def _build_env(url: str, ci_mode: bool) -> dict[str, str]:
    env = _playwright.base_env(CHECK, url, ci_mode)
    env.setdefault("SMOKE_OG_IMAGE_PATH", str(config.get("smoke.og_image_path", "/og-image.png")))
    env.setdefault("SMOKE_PAGES", str(config.get("pages.smoke", "/")))
    return env


def run(args: list[str] | None = None) -> int:
    return _playwright.run_check(CHECK, args, build_env=_build_env)
