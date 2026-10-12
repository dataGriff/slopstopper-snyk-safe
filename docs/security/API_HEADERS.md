# API Headers & CORS

The API-shaped analogue of [CSP Exceptions](CSP_EXCEPTIONS.md). On an HTML surface, CSP is the header that decides what a document may load. On a JSON API there is no document to restrict, so CSP is largely irrelevant and **CORS** becomes the control that decides who may *read* the response. A permissive CORS policy on a credentialed API is a cross-origin data leak, not a style violation.

`security:api-headers` probes each endpoint in `api.headers.paths` and splits its findings into hard failures and advisory notes. A check that fails on advice becomes noise; one that only advises on a credentialed wildcard isn't doing its job.

## What it checks

| Finding | Hard fail? | Why |
|---|---|---|
| `Access-Control-Allow-Origin: *` **with** `Access-Control-Allow-Credentials: true` | **always** | Browsers reject this pair, so the policy you intended was never actually enforced. It signals a misconfigured allowlist, not a working wildcard |
| `Access-Control-Allow-Origin: *` alone | unless `allow_wildcard_cors` | Correct for a genuinely public read-only API; a leak on anything else |
| An arbitrary `Origin` reflected back, **with** credentials | yes | Any site can read authenticated responses. The check sends `https://slopstopper-cors-probe.invalid`, an [RFC 2606](https://www.rfc-editor.org/rfc/rfc2606) reserved TLD that can never resolve, and fails if it comes back allowed |
| An arbitrary `Origin` reflected back, without credentials | advisory | Leaks less, but an allowlist is still the right shape |
| A configured `allowed_origins` entry that is no longer allowed | yes | Catches an allowlist that silently stopped matching, such as a renamed frontend domain or a regex that no longer fires |
| `Vary: Origin` missing while the origin is echoed | advisory | A shared cache can serve one origin's allowance to another |
| `Strict-Transport-Security` missing | on https, unless `require_hsts: false` | Not asserted on http targets, where the header has no effect |
| `X-Content-Type-Options: nosniff` missing | unless `require_nosniff: false` | A JSON response a browser sniffs as HTML is an XSS vector |
| `X-Powered-By`, `X-AspNet-Version`, a versioned `Server` | advisory | Hands an attacker a version to look up; not the vulnerability itself |

## Configuration

```yaml
# .slopstopper.yml
api:
  base_path: ''                  # prefix the API is served under
  headers:
    paths: [/health, /v1/items]  # empty → the check skips gracefully (exit 0)
    require_hsts: true
    require_nosniff: true
    allow_wildcard_cors: false
    allowed_origins: [https://app.example.com]
```

With `api.headers.paths` empty the check exits 0 with a note. That is the same contract as [CSP exceptions](CSP_EXCEPTIONS.md) with `headers.source: null`. List the endpoints whose policy you care about, not every route.

## Running locally

```bash
task ss:security:api-headers -- https://api.example.com

# Override config from the command line
task ss:security:api-headers -- https://api.example.com \
  --path /health --path /v1/items --allowed-origin https://app.example.com

API_HEADERS_TEST_URL=https://api.example.com task ss:security:api-headers
```

Report: `.ss/reports/api-headers/api-headers-report.{md,json}`. Also part of the `task ss:security:scan` aggregate.

**Caveat:** this probes a *live* origin. The CORS and HSTS headers usually come from your edge or proxy, which a local process often doesn't reproduce, so a clean local run is weaker evidence than a clean run against a deployed environment.

Env vars: `API_HEADERS_TEST_URL` (base URL), `API_HEADERS_PATHS` (comma-separated paths).
