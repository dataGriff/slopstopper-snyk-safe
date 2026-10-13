"""Stand-ins for external tools, shared by the check tests."""

from __future__ import annotations

import http.server
import json
import subprocess
import threading
from pathlib import Path

# What Playwright's JSON reporter writes when the suite ran and one test failed.
PLAYWRIGHT_FAILED_REPORT = {
    "errors": [],
    "stats": {"expected": 1, "unexpected": 1, "flaky": 0, "skipped": 0},
    "suites": [
        {
            "specs": [
                {"tests": [{"results": [{"status": "passed"}]}]},
                {"tests": [{"results": [{"status": "failed", "error": {"message": "expect(received).toBe(expected)"}}]}]},
            ]
        }
    ],
}


def playwright_failed(cmd, env, check):
    """`subprocess.run` for a Playwright suite that ran and had a failing test."""
    Path(env["PLAYWRIGHT_JSON_OUTPUT_NAME"]).write_text(json.dumps(PLAYWRIGHT_FAILED_REPORT))
    return subprocess.CompletedProcess(cmd, 1)


class Redirector(http.server.BaseHTTPRequestHandler):
    """Answers every GET/HEAD with `302 Location: <server.target>`."""

    def do_GET(self):  # noqa: N802  (the stdlib's name)
        self.send_response(302)
        self.send_header("Location", self.server.target)
        self.send_header("Content-Length", "0")
        self.end_headers()

    do_HEAD = do_GET  # noqa: N815

    def log_message(self, *a):
        pass


def start_redirecting_server():
    """A local server that 302s every request; set `server.target` per test."""
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Redirector)
    server.target = ""
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server
