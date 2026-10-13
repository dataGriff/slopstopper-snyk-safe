"""Every URL check, pointed at a target that redirects off http(s).

The redirect is refused (see `_http._GuardedRedirects`), but refusing it
is a fact about the target, reported like any other status. It must not
abort the check: no exit 2 ("could not run"), and a report is written, so
the PR comment and the main-branch issue still see the finding.
"""

from __future__ import annotations

import pytest

from slopstopper.checks import (
    api_headers,
    api_health,
    api_latency,
    llms_txt,
    openapi,
    robots_txt,
    seo,
    sitemap,
)

# (module, extra .slopstopper.yml, argv after the URL)
CASES = {
    "api-health": (api_health, "api:\n  health:\n    path: /health\n", []),
    "api-headers": (api_headers, "api:\n  headers:\n    paths: [/v1/items]\n", []),
    "api-latency": (api_latency, "api:\n  latency:\n    paths: [/v1/items]\n", []),
    "llms-txt": (llms_txt, "", []),
    "robots-txt": (robots_txt, "", []),
    "seo": (seo, "", []),
    "sitemap": (sitemap, "", []),
}


@pytest.mark.parametrize("name", sorted(CASES))
def test_a_redirect_off_http_is_a_finding_not_an_abort(name, redirecting_server, write_config):
    module, config_body, extra = CASES[name]
    write_config(config_body or "{}\n")
    url = redirecting_server("ftp://127.0.0.1/elsewhere")
    rc = module.run([url, *extra])
    assert rc != 2, f"{name}: a refused redirect aborted the check (exit 2)"
    assert module.REPORT_MD.exists(), f"{name}: no report written"


def test_openapi_served_spec_redirect_is_a_finding(redirecting_server, write_config, isolated_cwd):
    (isolated_cwd / "openapi.json").write_text('{"openapi": "3.0.0", "info": {}, "paths": {"/x": {"get": {}}}}')
    url = redirecting_server("ftp://127.0.0.1/elsewhere")
    write_config(f"api:\n  openapi:\n    spec: openapi.json\n    served_spec: {url}/openapi.json\n")
    assert openapi.run([url]) != 2
    assert openapi.REPORT_MD.exists()
