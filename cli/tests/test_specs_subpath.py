"""The bundled Playwright specs, run for real against a site under a sub-path.

`fixtures/subpath-site/` is served from its root, but the site under test
lives at `/project/` (a GitHub Pages project site). Its nav uses relative
hrefs, and the host root carries a different site, so a spec that sends
`/about/` to the host root, or resolves a relative href against the page
it landed on, fails here. Skipped when the repo's Playwright install
(`npm ci` + `npx playwright install chromium`) isn't present.
"""

from __future__ import annotations

import functools
import http.server
import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
NODE_MODULES = REPO_ROOT / "node_modules"
SITE = Path(__file__).parent / "fixtures" / "subpath-site"

# Under CI the specs run regardless (ci-cli.yml runs `npm ci` first), so a
# workflow edit that drops that step fails here instead of skipping.
pytestmark = pytest.mark.skipif(
    (shutil.which("npx") is None or not (NODE_MODULES / "@playwright" / "test").is_dir()) and not os.environ.get("CI"),
    reason="needs node and the repo's Playwright install (npm ci)",
)

CHECKS = {
    "reliability:smoke": "SMOKE_PAGES",
    "reliability:e2e": "E2E_PAGES",
    "reliability:accessibility": "ACCESSIBILITY_PAGES",
    "reliability:broken-links": "BROKEN_LINKS_PAGES",
}


@pytest.fixture(scope="module")
def base_url():
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(SITE))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}/project/"
    server.shutdown()


@pytest.mark.parametrize("check,pages_env", CHECKS.items())
def test_spec_passes_under_a_sub_path(check, pages_env, base_url, tmp_path):
    (tmp_path / "node_modules").symlink_to(NODE_MODULES)
    env = {
        **os.environ,
        pages_env: "/,/about/",
        "SMOKE_OG_IMAGE_PATH": "",
    }
    proc = subprocess.run(
        [sys.executable, "-m", "slopstopper", "run", check, "--", base_url],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
