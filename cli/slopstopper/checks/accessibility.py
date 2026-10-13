"""Accessibility audit (Playwright + axe-core wrapper).

Ports the bash reliability:accessibility flow:

  task ss:reliability:accessibility -- https://your-site.example.com

  which is, under the covers:

  ACCESSIBILITY_PAGES=$(python3 .ss/scripts/discover-pages.py
                                accessibility --event=local)
  ACCESSIBILITY_TEST_URL=...
  npx playwright test --config=.ss/playwright.config.js
                      .ss/tests/accessibility.spec.ts
                      --reporter=list[,html]

Subprocess-invokes `npx playwright`. Playwright is Apache-2.0, axe-core
is MPL-2.0. The slopstopper-cli wheel ships zero code from either,
because both bind in via the adopter's node_modules. Page discovery uses the
in-CLI slopstopper.discovery module.

Falls back to SMOKE_TEST_URL when ACCESSIBILITY_TEST_URL is unset,
matching the bash flow exactly.

Configuration (.slopstopper.yml, all optional):

    pages:
      accessibility: /,/blog,/about
    reliability:
      coverage:
        pr: changed     # see slopstopper.discovery for the resolution order
        main: sitemap

See .slopstopper.yml.example for the canonical schema and coverage
modes.

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

from slopstopper import discovery
from slopstopper.checks import _playwright

CHECK = _playwright.Check(
    name="accessibility",
    spec_name="accessibility",
    url_env="ACCESSIBILITY_TEST_URL",
    url_fallbacks=("SMOKE_TEST_URL",),
    title="## ♿ Accessibility Audit Results",
    failure_hint=(
        "Accessibility violations detected. Investigate the failing assertions in the "
        "[Playwright HTML report](playwright-report/index.html) (uploaded as an artifact in CI)."
    ),
    banner="Running accessibility audit against: {url}",
    banner_icon="♿",
)

# Consumed by `slopstopper emit reliability:accessibility --target {pr-comment,issue}`.
# Issue title + label match the strings the legacy workflow used in raw
# `gh issue create` so existing open issues continue to dedup post-migration.
META = {
    "report_path": str(CHECK.report_md),
    "comment_discriminator": CHECK.title,
    "issue_title": "♿ Accessibility Violations Detected on Main Branch",
    "issue_labels": ["accessibility", "reliability"],
    "issue_followup": "🔔 Accessibility violations recurred in commit",
    "issue_close_comment": "✅ Accessibility audit is now passing on `main`. Closing automatically.",
}


def _build_env(url: str, ci_mode: bool) -> dict[str, str]:
    env = _playwright.base_env(CHECK, url, ci_mode)
    if "ACCESSIBILITY_PAGES" not in env:
        pages = discovery.pages_csv("accessibility")
        if pages is not None:
            env["ACCESSIBILITY_PAGES"] = pages
    return env


def run(args: list[str] | None = None) -> int:
    return _playwright.run_check(CHECK, args, build_env=_build_env)
