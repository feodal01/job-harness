"""HTTP and HTML helpers for non-browser scrapers.

The two entry points `fetch_text` and `fetch_json` enforce a deadline,
classify HTTP responses into a closed exception taxonomy, and emit
domain exceptions that the HTTP runner maps to `FailureMode`s.

Status-code handling (verified empirically with `urllib.request`):

  • 200                           → return body
  • 429 / 503 with Retry-After    → sleep min(retry_after, remaining);
                                     if it exceeds remaining → RateLimited
                                     immediately without sleep
  • 5xx without Retry-After       → one retry inside budget, then
                                     HttpServerError
  • 403 / 451                     → AntiBotBlocked, no retry
  • 4xx (not 429)                 → HttpClientError, no retry
  • 30x → /login,/auth,…         → LoginRequired
  • URLError/OSError/socket.timeout → retry inside budget, then NetworkError
  • JSONDecodeError (fetch_json)  → ParseError, no retry

Backward compatibility: `timeout_seconds` is still accepted so existing
scrapers do not need a callsite change. The engine passes `deadline_ms`,
which takes precedence when both are given.
"""

from __future__ import annotations

import json
import re
import ssl
import time
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from html import unescape
from html.parser import HTMLParser
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/136.0.0.0 Safari/537.36"
)
FETCH_TIMEOUT_SECONDS = 15.0
VOID_TAGS = {
    "area", "base", "br", "col", "embed", "hr", "img", "input",
    "link", "meta", "param", "source", "track", "wbr",
}
LOGIN_PATH_RE = re.compile(r"^/(?:login|auth|sign[-_]?in|users/sign_in)(?:/|$)")
ANTI_BOT_BODY_MARKERS: tuple[str, ...] = (
    "cloudflare",
    "__cf_chl_",
    "cf-chl-bypass",
    "distil_r_captcha",
    "distil-bot",
    "akamai bot manager",
    "imperva incapsula",
)
DEFAULT_RETRIES = 2
MIN_ATTEMPT_TIMEOUT_S = 1.0
MAX_ATTEMPT_TIMEOUT_S = 10.0


# ---------------------------------------------------------------------------
# Domain exceptions
# ---------------------------------------------------------------------------


class FetchError(Exception):
    """Base class for fetch_text / fetch_json domain failures.

    Carries the HTTP status code when available so the HTTP runner can
    map it to the right FailureMode without re-inspecting the cause.
    """

    status_code: int | None = None


class NetworkError(FetchError):
    """OS-level connection / DNS / TLS / read failure."""


class HttpClientError(FetchError):
    """HTTP 4xx (non-429, non-403-anti-bot)."""


class HttpServerError(FetchError):
    """HTTP 5xx without an actionable Retry-After header."""


class RateLimited(FetchError):
    """HTTP 429 or 503 with a Retry-After exceeding the remaining budget."""

    def __init__(self, message: str, *, retry_after_s: float | None = None, status_code: int = 429):
        super().__init__(message)
        self.retry_after_s = retry_after_s
        self.status_code = status_code


class AntiBotBlocked(FetchError):
    """403 (or other) response that looks like an anti-bot interstitial."""

    status_code = 403

    def __init__(self, message: str, *, marker: str | None = None, status_code: int | None = None):
        super().__init__(message)
        self.marker = marker
        if status_code is not None:
            self.status_code = status_code


class LoginRequired(FetchError):
    """30x redirect to a login path."""

    def __init__(self, message: str, *, final_url: str | None = None):
        super().__init__(message)
        self.final_url = final_url


class ParseError(FetchError):
    """Response body could not be parsed (JSON decode etc.)."""


# ---------------------------------------------------------------------------
# Public dataclass — preserved
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Anchor:
    href: str
    text: str
    attrs: dict[str, str]


# ---------------------------------------------------------------------------
# fetch_text / fetch_json
# ---------------------------------------------------------------------------


def fetch_text(
    url: str,
    *,
    verify_ssl: bool = True,
    timeout_seconds: float | None = None,
    deadline_ms: int | None = None,
    retries: int = DEFAULT_RETRIES,
) -> str:
    """Fetch a URL as decoded text.

    Either `timeout_seconds` (legacy) or `deadline_ms` (preferred) bounds
    the entire call including retries. If both are given, `deadline_ms`
    wins. If neither is given, defaults to FETCH_TIMEOUT_SECONDS.
    """
    body, _resp = _do_fetch(
        url,
        verify_ssl=verify_ssl,
        timeout_seconds=timeout_seconds,
        deadline_ms=deadline_ms,
        retries=retries,
        accept="*/*",
    )
    return body


def fetch_json(
    url: str,
    *,
    timeout_seconds: float | None = None,
    deadline_ms: int | None = None,
    retries: int = DEFAULT_RETRIES,
) -> dict:
    """Fetch a URL and decode the body as JSON.

    Same deadline rules as `fetch_text`. JSONDecodeError on the body is
    raised as `ParseError` — not retried, because a WAF/anti-bot HTML
    body will keep failing.
    """
    body, _resp = _do_fetch(
        url,
        verify_ssl=True,
        timeout_seconds=timeout_seconds,
        deadline_ms=deadline_ms,
        retries=retries,
        accept="application/json",
    )
    try:
        return json.loads(body)
    except json.JSONDecodeError as exc:
        raise ParseError(f"non-JSON body from {url}: {exc}") from exc


def _do_fetch(
    url: str,
    *,
    verify_ssl: bool,
    timeout_seconds: float | None,
    deadline_ms: int | None,
    retries: int,
    accept: str,
) -> tuple[str, object]:
    total_deadline_s = _resolve_total_deadline_s(timeout_seconds, deadline_ms)
    if total_deadline_s <= 0:
        raise NetworkError(f"deadline already expired for {url}")

    started = time.monotonic()
    last_exc: FetchError | None = None
    attempts_left = max(1, retries + 1)
    attempts_made = 0
    ctx = ssl.create_default_context() if verify_ssl else ssl._create_unverified_context()

    while attempts_left > 0:
        remaining = total_deadline_s - (time.monotonic() - started)
        if remaining <= 0:
            break
        attempt_timeout = max(
            MIN_ATTEMPT_TIMEOUT_S, min(remaining / attempts_left, MAX_ATTEMPT_TIMEOUT_S)
        )
        attempts_left -= 1
        attempts_made += 1
        try:
            request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": accept})
            with urlopen(request, timeout=attempt_timeout, context=ctx) as response:
                final_url = response.geturl()
                if _is_login_redirect(final_url):
                    raise LoginRequired(
                        f"redirected to login at {final_url}", final_url=final_url
                    )
                body = response.read().decode("utf-8", errors="replace")
                return body, response
        except HTTPError as exc:
            domain_exc = _classify_http_error(exc, remaining_s=remaining)
            if isinstance(domain_exc, _RetryableHttpFailure):
                last_exc = domain_exc.wrapped
                sleep_for = min(domain_exc.sleep_for_s, max(0.0, remaining - 0.1))
                if sleep_for > 0:
                    time.sleep(sleep_for)
                continue
            # Terminal — never retry.
            raise domain_exc from exc
        except URLError as exc:
            last_exc = NetworkError(f"network failure for {url}: {exc.reason}")
            last_exc.__cause__ = exc
        except TimeoutError as exc:
            # urlopen socket timeout — treat as a retryable network event.
            last_exc = NetworkError(f"timeout fetching {url}: {exc}")
            last_exc.__cause__ = exc
        except OSError as exc:
            last_exc = NetworkError(f"OS error fetching {url}: {exc}")
            last_exc.__cause__ = exc

        # Linear backoff, capped by remaining budget.
        if attempts_left > 0:
            remaining = total_deadline_s - (time.monotonic() - started)
            if remaining <= 0:
                break
            sleep_for = min(0.5 * attempts_made, max(0.0, remaining - 0.1))
            if sleep_for > 0:
                time.sleep(sleep_for)

    raise last_exc if last_exc is not None else NetworkError(
        f"exhausted attempts fetching {url}"
    )


# ---------------------------------------------------------------------------
# Classification helpers
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _RetryableHttpFailure:
    """Internal sentinel returned by `_classify_http_error` for 429/5xx-with-retry.

    Contains the wrapped exception (for last_exc tracking if budget runs
    out) and how long to sleep before the next attempt.
    """

    wrapped: FetchError
    sleep_for_s: float


def _classify_http_error(exc: HTTPError, *, remaining_s: float) -> FetchError | _RetryableHttpFailure:
    code = exc.code
    headers = exc.headers if exc.headers is not None else {}
    # Try body sniffing for anti-bot markers. body may be missing or large.
    body_lower = ""
    try:
        raw = exc.read() if hasattr(exc, "read") else b""
        body_lower = raw.decode("utf-8", errors="replace").casefold() if raw else ""
    except Exception:
        body_lower = ""
    finally:
        # urlopen exposes the underlying response as a file-like object
        # attached to the exception. Closing it suppresses ResourceWarning.
        try:
            exc.close()
        except Exception:
            pass

    # Access blocks are terminal and should not look like scraper success.
    if code in (403, 451):
        marker = next((m for m in ANTI_BOT_BODY_MARKERS if m in body_lower), None)
        suffix = f" (marker={marker})" if marker else ""
        return AntiBotBlocked(
            f"access blocked with HTTP {code}{suffix}",
            marker=marker,
            status_code=code,
        )

    if code in (429, 503):
        retry_after = _parse_retry_after(headers.get("Retry-After"))
        if retry_after is not None and retry_after > remaining_s:
            limited = RateLimited(
                f"rate limited, Retry-After={retry_after}s exceeds remaining {remaining_s:.1f}s",
                retry_after_s=retry_after,
                status_code=code,
            )
            return limited
        # Inside budget: schedule a retry sleep equal to Retry-After (or
        # a short fallback if header is absent on 503).
        sleep_for = retry_after if retry_after is not None else min(1.0, max(0.1, remaining_s / 4))
        limited = RateLimited(
            f"rate limited, retrying after {sleep_for:.1f}s",
            retry_after_s=sleep_for,
            status_code=code,
        )
        return _RetryableHttpFailure(wrapped=limited, sleep_for_s=sleep_for)

    if 500 <= code < 600:
        server = HttpServerError(f"HTTP {code} server error for {exc.url}")
        server.status_code = code
        # One retry: surface as retryable with a tiny backoff.
        return _RetryableHttpFailure(wrapped=server, sleep_for_s=0.5)

    if 400 <= code < 500:
        client = HttpClientError(f"HTTP {code} client error for {exc.url}")
        client.status_code = code
        return client

    # Other (e.g. 3xx not auto-followed) — treat as terminal client error.
    other = HttpClientError(f"HTTP {code} for {exc.url}")
    other.status_code = code
    return other


def _parse_retry_after(value: str | None) -> float | None:
    if not value:
        return None
    value = value.strip()
    # Numeric seconds.
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    # HTTP-date.
    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if when is None:
        return None
    from datetime import UTC, datetime
    now = datetime.now(UTC)
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    delta = (when - now).total_seconds()
    return max(0.0, delta)


def _is_login_redirect(final_url: str) -> bool:
    parsed = urlparse(final_url)
    return bool(parsed.path) and bool(LOGIN_PATH_RE.match(parsed.path))


def _resolve_total_deadline_s(timeout_seconds: float | None, deadline_ms: int | None) -> float:
    if deadline_ms is not None:
        return max(0.0, deadline_ms / 1000.0)
    if timeout_seconds is not None:
        return max(0.0, float(timeout_seconds))
    return FETCH_TIMEOUT_SECONDS


# ---------------------------------------------------------------------------
# Text utilities (unchanged)
# ---------------------------------------------------------------------------


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", unescape(value)).strip()


def absolute_url(base_url: str, href: str) -> str:
    return urljoin(base_url, href)


class AnchorExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.anchors: list[Anchor] = []
        self._current: dict | None = None
        self._depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "a" and self._current is None:
            attr = {key: value or "" for key, value in attrs}
            self._current = {"href": attr.get("href", ""), "attrs": attr, "parts": []}
            self._depth = 1
            return

        if self._current is not None and tag not in VOID_TAGS:
            self._depth += 1

    def handle_data(self, data: str) -> None:
        if self._current is not None:
            self._current["parts"].append(data)

    def handle_endtag(self, tag: str) -> None:
        if self._current is None:
            return
        if tag not in VOID_TAGS:
            self._depth -= 1
        if self._depth == 0:
            self.anchors.append(Anchor(
                href=self._current["href"],
                text=normalize_text(" ".join(self._current["parts"])),
                attrs=self._current["attrs"],
            ))
            self._current = None


def extract_anchors(html: str) -> list[Anchor]:
    parser = AnchorExtractor()
    parser.feed(html)
    return parser.anchors


def extract_next_data(html: str) -> dict:
    match = re.search(
        r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
        html,
        re.S,
    )
    if match is None:
        raise ValueError("Missing __NEXT_DATA__ payload")
    return json.loads(match.group(1))
