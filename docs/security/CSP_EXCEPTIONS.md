# CSP exceptions

This file is the **single source of truth** for every per-path
Content-Security-Policy relaxation on the site. The
`ss:hygiene:csp-exceptions` check fails the build if the headers file
named by `headers.source` in `.slopstopper.yml` and this file disagree.

## Why this exists

A site should ship a strict `default-src 'self'; script-src 'self';
style-src 'self'` CSP on every path. But almost every real-world site
eventually needs to admit *something* third-party: Google Tag Manager,
Sentry, Intercom, an analytics tag, an embedded video, a comments widget.
Telling adopters "just don't" is unhelpful. The honest answer is a pattern:

1. **Default to the strictest CSP you can.** Set it once for the whole
   site (the catch-all entry in your headers file).
2. **Relax per page, not site-wide.** When you need a third-party on one
   page, add an entry to the headers file scoped to that exact path. The
   rest of the site stays strict, so XSS or supply-chain issues stay
   contained.
3. **Pin external scripts with SRI** wherever the host supports it. SRI
   locks the relaxation to one exact bundle hash, so a compromise of the
   third-party CDN can't ship new JavaScript to your visitors. The cost
   is that when the third-party releases a new version, your SRI hash
   stops matching and the widget silently breaks until you refresh it.
   Make sure there's a fallback link on the page so the page stays
   useful in that window.
4. **Document every exception here.** What origin you let in, on which
   page, what data leaves, who approved it, and how to refresh the SRI
   hash. The hygiene check verifies that what's in the headers file
   matches what's documented here.

## The headers source

`headers.source` names the file; `headers.format` names its shape:

| `format` | Source | Typical host |
| --- | --- | --- |
| `json` | a `[{for, values}]` JSON array, one entry per path pattern | a Worker or edge function that imports it |
| `cloudflare-text` | the native `_headers` text format | Cloudflare Pages, Netlify |
| `meta` | the built HTML root; each page's `<meta http-equiv="Content-Security-Policy">` is read | GitHub Pages, S3 without CloudFront, any host that can't send headers |
| `auto` | a directory → `meta`, `.json` → `json`, anything else → `cloudflare-text` | |

With `source: null` (the installer's seed) the check skips. Under `meta`
the policy most pages share is the baseline and a page allowing an extra
origin is the exception to document here; the root only exists after a
build, so CI builds first and an unbuilt root skips locally.

## Schema

Each exception is a `### /path` heading under `## Exceptions` followed
by these fields:

| Field | Purpose |
| --- | --- |
| `**Origin allowed:**` | One or more origins added to the CSP for this path |
| `**Directives added:**` | Which CSP directives changed (script-src, frame-src, style-src, …) |
| `**Loader SRI:**` | `sha384-…` hash of the external script, or `n/a` if SRI is not applicable / unsupported |
| `**Why:**` | One sentence on what feature this enables and why a third-party is required |
| `**Approved by:**` | PR number where this was reviewed |
| `**Data leaving site:**` | What visitor data the third-party will receive (IP, UA, identity, page URL, etc.) |
| `**Refresh policy:**` | When and how to update the SRI hash |

The check parser is literal, so keep the field labels exactly as above.

## Computing an SRI hash

```bash
curl -sSL https://widget.example.com/client.js | openssl dgst -sha384 -binary | openssl base64 -A
# prepend "sha384-" to the output
```

Re-run when the widget breaks after a third-party release.

---

## Exceptions

This repository is a CLI and a set of workflows with no served pages, so
it has no exceptions and sets no `headers.source`. An adopter's copy of
this file lists one `### /path` block per relaxation, in the schema above.

### Worked example

A comments widget admitted on a single page would be recorded as:

- **Origin allowed:** `https://widget.example.com`
- **Directives added:** `script-src https://widget.example.com`, `frame-src https://widget.example.com`
- **Loader SRI:** `sha384-…` (refreshed when the widget publishes a new loader)
- **Why:** Embedded comments on the feedback page without leaving the site
- **Approved by:** the PR that introduced the page
- **Data leaving site:** visitor IP, User-Agent and referrer when the iframe is fetched; the commenter's identity when posting
- **Refresh policy:** recompute the hash with the command above, update the `integrity=` attribute on the page and the line above, open a PR; the check green-lights when both sides match

---

## Adopters: how to use this in your own repo

1. Keep the catch-all entry in your headers file strict by default
   - On a host that can't send headers, the CSP is a `<meta>` tag per
     page: set `headers.format: meta` with `headers.source` at the built
     HTML root. The policy most pages share is the baseline; a page
     allowing an extra origin is the exception to document here
2. Add a new entry per page that needs a relaxation; **list the full
   replacement directives**, not just additions
3. Add an entry to this file mirroring the schema above
4. Install `ss-hygiene-csp-exceptions-check.yml` via the SlopStopper
   installer so its check enforces drift between the headers file and
   this file
5. If your repo has no third-party widgets, leave `## Exceptions` empty;
   the check passes when both sides are empty
