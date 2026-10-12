"""HTTP plumbing shared by every URL-driven check.

Eight checks carried their own copy of the SSRF guard, seven their own
`urlopen` wrapper, four their own HEAD probe, three their own URL
joiner. Four of the `_fetch` copies were byte-identical down to the
`nosec` comments. Eight guards is eight places to forget one; this is
the one.

The guard is the point. `urllib.request.urlopen` happily handles
`file://` and `ftp://`, so a hostile `*_TEST_URL` value such as
`file:///etc/passwd` would be read by a checker and, on some paths,
echoed into a report. Every request a check makes goes through
`open_url`, which refuses anything but http/https before opening it
(raising ValueError, since that is a bad input) and again on every
redirect: urllib follows a `302 Location: ftp://…` on its own, so a guard
on the first URL alone is not a guard. A refused redirect is an HTTPError.
That is a fact about the target, reported like any other status and never
an abort.

Checks keep a module-level `_fetch` / `_head_ok` name that delegates
here, so tests can monkeypatch the check they are testing without
reaching into this module. Every check uses `DEFAULT_TIMEOUT`.
"""

from __future__ import annotations

import functools
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

ALLOWED_SCHEMES = ("http", "https")
DEFAULT_TIMEOUT = 15


def is_http_url(url: str) -> bool:
    """True for an http/https URL. This is the one scheme test every check shares."""
    return urllib.parse.urlparse(url).scheme.lower() in ALLOWED_SCHEMES


def require_safe_url(url: str, label: str = "slopstopper") -> None:
    """Reject any URL whose scheme isn't http/https (blocks file:// SSRF).

    `label` names the check in the error, e.g. "API health check".
    """
    if not is_http_url(url):
        scheme = urllib.parse.urlparse(url).scheme.lower()
        raise ValueError(
            f"{label} refuses scheme {scheme!r} (only http/https allowed). url={url!r}"
        )


class _GuardedRedirects(urllib.request.HTTPRedirectHandler):
    """Follow redirects only to http/https. urllib's default handler also
    follows `ftp://`, which would walk straight past the first-hop guard.

    A refused redirect raises `HTTPError` carrying the 3xx response, which
    is what urllib raises for a `file://` redirect too, so every caller's
    existing HTTPError handling reports it as data about the target ("the
    endpoint answered 302 to somewhere we won't follow"), and the caller
    that handles the error owns closing the response.
    """

    def __init__(self, label: str) -> None:
        self.label = label

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not is_http_url(newurl):
            scheme = urllib.parse.urlparse(newurl).scheme.lower()
            raise urllib.error.HTTPError(
                newurl, code,
                f"{msg} - {self.label} refuses to follow a redirect to scheme "
                f"{scheme!r} (only http/https allowed)",
                headers, fp,
            )
        return super().redirect_request(req, fp, code, msg, headers, newurl)


@functools.lru_cache(maxsize=None)
def _opener(label: str) -> urllib.request.OpenerDirector:
    """One opener per check label, built once. A sitemap crawl or a latency
    run makes hundreds of requests; none of them should pay for this."""
    return urllib.request.build_opener(_GuardedRedirects(label))


def _send(req: urllib.request.Request, timeout: int, label: str) -> Any:
    """Open an already-guarded request, re-guarding every redirect hop."""
    # The scheme is guarded on the first hop and on every redirect.
    return _opener(label).open(req, timeout=timeout)  # nosec B310


def open_url(
    url: str,
    user_agent: str,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    timeout: int = DEFAULT_TIMEOUT,
    label: str = "slopstopper",
) -> Any:
    """Open `url` after the scheme guard. Returns the response context manager.

    Raises the same things `urllib.request.urlopen` raises (HTTPError,
    URLError, TimeoutError) plus ValueError for a refused scheme on the URL
    itself. A redirect off http/https raises HTTPError with the 3xx status,
    so it lands in the handlers every check already has for statuses.
    """
    require_safe_url(url, label)
    request_headers = {"User-Agent": user_agent, **(headers or {})}
    req = urllib.request.Request(url, headers=request_headers, method=method)
    return _send(req, timeout, label)


def fetch_text(
    url: str, user_agent: str, *, timeout: int = DEFAULT_TIMEOUT, label: str = "slopstopper"
) -> tuple[int, str, str]:
    """GET the URL. Returns (status, content_type, body). 4xx/5xx raise HTTPError."""
    with open_url(url, user_agent, timeout=timeout, label=label) as resp:
        return (
            resp.status,
            resp.headers.get("Content-Type", ""),
            resp.read().decode("utf-8", errors="replace"),
        )


def head_ok(
    url: str, user_agent: str, *, timeout: int = DEFAULT_TIMEOUT, label: str = "slopstopper"
) -> tuple[bool, str]:
    """HEAD the URL. Returns (ok, detail), where ok means reachable and not 4xx/5xx."""
    try:
        with open_url(url, user_agent, method="HEAD", timeout=timeout, label=label) as resp:
            if resp.status >= 400:
                return False, f"HTTP {resp.status}"
            return True, f"HTTP {resp.status}"
    except urllib.error.HTTPError as e:
        return False, f"HTTP {e.code}"
    except (urllib.error.URLError, TimeoutError, ValueError) as e:
        return False, f"{type(e).__name__}: {e}"


def join_url(url: str, base_path: str, path: str) -> str:
    """`https://host` + `/api/v1` + `health` → `https://host/api/v1/health`."""
    prefix = (base_path or "").rstrip("/")
    suffix = path if path.startswith("/") else f"/{path}"
    return f"{url.rstrip('/')}{prefix}{suffix}"
