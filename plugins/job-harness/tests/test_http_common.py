"""Tests for fetch_text / fetch_json with the new HTTP status taxonomy.

Uses a local http.server thread so the response and headers are
completely under test control — no httpbin.org dependency.
"""

from __future__ import annotations

import http.server
import socketserver
import threading
import time
import unittest
from collections.abc import Callable

from job_harness.scrapers.http_common import (
    AntiBotBlocked,
    HttpClientError,
    HttpServerError,
    LoginRequired,
    NetworkError,
    ParseError,
    RateLimited,
    fetch_json,
    fetch_text,
)

# ---------------------------------------------------------------------------
# Tiny test HTTP server
# ---------------------------------------------------------------------------


class _Handler(http.server.BaseHTTPRequestHandler):
    handler: Callable[[_Handler], None] = lambda self: self._not_set()

    def _not_set(self):
        self.send_response(500)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):  # noqa: N802
        type(self).handler(self)

    def log_message(self, *_a, **_k):  # silence
        return


class _Server:
    def __init__(self):
        self._srv = socketserver.TCPServer(("127.0.0.1", 0), _Handler)
        self._srv.allow_reuse_address = True
        self.port = self._srv.server_address[1]
        self.calls = 0
        self._thread = threading.Thread(target=self._srv.serve_forever, daemon=True)
        self._thread.start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/"

    def respond(self, fn):
        """Install a per-request handler. Increments calls counter on entry."""
        outer = self

        def wrapped(handler):
            outer.calls += 1
            fn(handler)

        _Handler.handler = wrapped

    def stop(self):
        self._srv.shutdown()
        self._srv.server_close()


def _h_status(code: int, *, headers: dict[str, str] | None = None, body: bytes = b""):
    def fn(h: _Handler):
        h.send_response(code)
        for k, v in (headers or {}).items():
            h.send_header(k, v)
        h.send_header("Content-Length", str(len(body)))
        h.end_headers()
        if body:
            h.wfile.write(body)
    return fn


def _h_json(payload: bytes, status: int = 200):
    def fn(h: _Handler):
        h.send_response(status)
        h.send_header("Content-Type", "application/json")
        h.send_header("Content-Length", str(len(payload)))
        h.end_headers()
        h.wfile.write(payload)
    return fn


def _h_redirect(location: str, status: int = 302):
    def fn(h: _Handler):
        h.send_response(status)
        h.send_header("Location", location)
        h.send_header("Content-Length", "0")
        h.end_headers()
    return fn


def _h_login_page(h: _Handler):
    body = b"please log in"
    h.send_response(200)
    h.send_header("Content-Type", "text/html")
    h.send_header("Content-Length", str(len(body)))
    h.end_headers()
    h.wfile.write(body)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class FetchHappyPathTest(unittest.TestCase):
    def setUp(self):
        self.s = _Server()

    def tearDown(self):
        self.s.stop()

    def test_200_body_decoded(self):
        self.s.respond(_h_status(200, body=b"hello"))
        out = fetch_text(self.s.url, deadline_ms=5000, retries=0)
        self.assertEqual(out, "hello")
        self.assertEqual(self.s.calls, 1)

    def test_legacy_timeout_seconds_still_accepted(self):
        self.s.respond(_h_status(200, body=b"ok"))
        out = fetch_text(self.s.url, timeout_seconds=5, retries=0)
        self.assertEqual(out, "ok")

    def test_deadline_ms_takes_precedence_over_timeout_seconds(self):
        self.s.respond(_h_status(200, body=b"ok"))
        # If deadline_ms wins, a deadline_ms=5_000 + timeout_seconds=0 still works.
        out = fetch_text(self.s.url, deadline_ms=5000, timeout_seconds=0, retries=0)
        self.assertEqual(out, "ok")


class FetchErrorTaxonomyTest(unittest.TestCase):
    def setUp(self):
        self.s = _Server()

    def tearDown(self):
        self.s.stop()

    def test_429_with_retry_after_inside_budget_retries(self):
        first = {"done": False}

        def fn(h: _Handler):
            if not first["done"]:
                first["done"] = True
                _h_status(429, headers={"Retry-After": "0"})(h)
            else:
                _h_status(200, body=b"after-wait")(h)

        self.s.respond(fn)
        out = fetch_text(self.s.url, deadline_ms=5000, retries=2)
        self.assertEqual(out, "after-wait")
        self.assertEqual(self.s.calls, 2)

    def test_429_with_retry_after_exceeding_budget_raises_immediately(self):
        self.s.respond(_h_status(429, headers={"Retry-After": "120"}))
        t0 = time.monotonic()
        with self.assertRaises(RateLimited) as ctx:
            fetch_text(self.s.url, deadline_ms=300, retries=2)
        elapsed = time.monotonic() - t0
        # Must NOT have slept 120 s.
        self.assertLess(elapsed, 1.0, "raised without honoring 120s sleep")
        self.assertEqual(ctx.exception.status_code, 429)
        self.assertEqual(ctx.exception.retry_after_s, 120.0)

    def test_503_with_retry_after_treated_like_429(self):
        self.s.respond(_h_status(503, headers={"Retry-After": "60"}))
        with self.assertRaises(RateLimited) as ctx:
            fetch_text(self.s.url, deadline_ms=200, retries=1)
        self.assertEqual(ctx.exception.status_code, 503)

    def test_500_without_retry_after_retries_once_then_raises_server_error(self):
        self.s.respond(_h_status(500))
        with self.assertRaises(HttpServerError) as ctx:
            fetch_text(self.s.url, deadline_ms=4000, retries=2)
        # retries=2 means up to 3 attempts; classifier asks for one retry.
        self.assertGreaterEqual(self.s.calls, 2)
        self.assertEqual(ctx.exception.status_code, 500)

    def test_404_does_not_retry(self):
        self.s.respond(_h_status(404))
        with self.assertRaises(HttpClientError) as ctx:
            fetch_text(self.s.url, deadline_ms=3000, retries=3)
        self.assertEqual(self.s.calls, 1)
        self.assertEqual(ctx.exception.status_code, 404)

    def test_403_with_cloudflare_marker_classified_as_anti_bot(self):
        body = b"<html>Sorry, you have been blocked. Ray ID: cf-chl-bypass-1234</html>"
        self.s.respond(_h_status(403, body=body))
        with self.assertRaises(AntiBotBlocked) as ctx:
            fetch_text(self.s.url, deadline_ms=3000, retries=3)
        self.assertEqual(self.s.calls, 1)
        self.assertIn("cf-chl", ctx.exception.marker or "")

    def test_403_plain_classified_as_client_error(self):
        self.s.respond(_h_status(403, body=b"nope"))
        with self.assertRaises(HttpClientError) as ctx:
            fetch_text(self.s.url, deadline_ms=3000, retries=0)
        self.assertEqual(ctx.exception.status_code, 403)
        self.assertNotIsInstance(ctx.exception, AntiBotBlocked)

    def test_302_redirect_to_login_path_classified(self):
        # The server sends 302 → /login. urlopen follows redirects by
        # default; the body comes from _h_login_page on the second call.
        cycle = {"n": 0}

        def fn(h: _Handler):
            if cycle["n"] == 0:
                cycle["n"] += 1
                _h_redirect("/login")(h)
            else:
                _h_login_page(h)

        self.s.respond(fn)
        with self.assertRaises(LoginRequired) as ctx:
            fetch_text(self.s.url, deadline_ms=3000, retries=0)
        self.assertIn("/login", ctx.exception.final_url or "")


class FetchBudgetTest(unittest.TestCase):
    def setUp(self):
        self.s = _Server()

    def tearDown(self):
        self.s.stop()

    def test_deadline_zero_does_not_open_socket(self):
        with self.assertRaises(NetworkError):
            fetch_text(self.s.url, deadline_ms=0, retries=2)
        self.assertEqual(self.s.calls, 0)

    def test_user_agent_sent(self):
        seen = {}

        def fn(h: _Handler):
            seen["ua"] = h.headers.get("User-Agent")
            _h_status(200, body=b"ok")(h)

        self.s.respond(fn)
        fetch_text(self.s.url, deadline_ms=3000, retries=0)
        self.assertIn("Chrome", seen.get("ua") or "")

    def test_network_error_dead_host_no_retry_floor(self):
        # Use an unrouted address; should fail fast within the budget.
        with self.assertRaises(NetworkError):
            fetch_text("http://127.0.0.1:1/", deadline_ms=1000, retries=1)


class FetchJsonTest(unittest.TestCase):
    def setUp(self):
        self.s = _Server()

    def tearDown(self):
        self.s.stop()

    def test_happy_path(self):
        self.s.respond(_h_json(b'{"data": [1, 2]}'))
        out = fetch_json(self.s.url, deadline_ms=3000, retries=0)
        self.assertEqual(out["data"], [1, 2])

    def test_non_json_body_raises_parse_error_no_retry(self):
        self.s.respond(_h_status(200, body=b"<html>not json</html>"))
        with self.assertRaises(ParseError):
            fetch_json(self.s.url, deadline_ms=3000, retries=3)
        self.assertEqual(self.s.calls, 1)


class RetryAfterParsingTest(unittest.TestCase):
    def test_numeric_seconds(self):
        from job_harness.scrapers.http_common import _parse_retry_after
        self.assertEqual(_parse_retry_after("7"), 7.0)

    def test_negative_clamped_to_zero(self):
        from job_harness.scrapers.http_common import _parse_retry_after
        self.assertEqual(_parse_retry_after("-3"), 0.0)

    def test_http_date(self):
        from job_harness.scrapers.http_common import _parse_retry_after
        # A clearly past date → 0.
        v = _parse_retry_after("Wed, 01 Jan 2020 00:00:00 GMT")
        self.assertEqual(v, 0.0)

    def test_malformed_returns_none(self):
        from job_harness.scrapers.http_common import _parse_retry_after
        self.assertIsNone(_parse_retry_after("not-a-date"))
        self.assertIsNone(_parse_retry_after(""))
        self.assertIsNone(_parse_retry_after(None))


if __name__ == "__main__":
    unittest.main()
