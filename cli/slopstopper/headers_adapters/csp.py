"""What a Content-Security-Policy value says, for every adapter and the
drift check (its own module so an adapter can import it without a
circular import through the package)."""

from __future__ import annotations


def csp_origins(csp: str) -> set[str]:
    """The external origins a policy allows, trailing slash dropped: what a
    per-path relaxation adds over the baseline."""
    return {
        token.rstrip("/")
        for directive in csp.split(";")
        for token in directive.split()
        if token.startswith(("http://", "https://"))
    }
