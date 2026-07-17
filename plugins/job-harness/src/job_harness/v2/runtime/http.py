"""HTTP artifact fetcher for the contract-first runtime."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from time import monotonic

import httpx

from job_harness.v2.contracts import SourceFetchRequest, SourceOutcome, SourceResponseArtifact
from job_harness.v2.ports import HttpAction, HttpResponse, RetrySafety
from job_harness.v2.runtime.errors import ClassifiedSourceError
from job_harness.v2.runtime.request_retry import (
    InMemoryRequestRetrier,
    RequestAttemptError,
    RequestFailureKind,
    RequestRetryPolicy,
    is_retryable_http_status,
)

_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/136.0.0.0 Safari/537.36"
)
_HTTP_RATE_LIMITED = 429
_HTTP_CLIENT_ERROR_MIN = 400
_HTTP_SERVER_ERROR_MIN = 500
_HTTP_SERVER_ERROR_MAX = 600
_MAX_CONNECTIONS = 256
_MAX_KEEPALIVE_CONNECTIONS = 64
_MAX_CONNECTIONS_PER_ORIGIN = 16
_MAX_KEEPALIVE_CONNECTIONS_PER_ORIGIN = 8


class HttpArtifactFetcher:
    """Fetch read-only source artifacts under the request retry policy."""

    def __init__(
        self,
        *,
        timeout_seconds: float = 15.0,
        transport: httpx.AsyncBaseTransport | None = None,
        request_retry_policy: RequestRetryPolicy | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be > 0")
        self._timeout_seconds = timeout_seconds
        self._transport = transport
        self._client: httpx.AsyncClient | None = None
        self._retrier = InMemoryRequestRetrier(
            policy=request_retry_policy or RequestRetryPolicy(),
            sleep=sleep,
            clock=clock,
        )

    async def fetch(self, request: SourceFetchRequest) -> SourceResponseArtifact:
        try:
            response = await self._retrier.run(lambda: self._fetch_once(request))
        except RequestAttemptError as exc:
            raise ClassifiedSourceError(_request_error_outcome(exc), str(exc)) from exc

        if response.status_code >= _HTTP_CLIENT_ERROR_MIN:
            raise ClassifiedSourceError(
                _http_outcome(response.status_code),
                f"HTTP {response.status_code}: {response.reason_phrase}",
            )

        return SourceResponseArtifact(
            source_id=request.source_id,
            url=request.url,
            media_type=_media_type(response),
            body=response.text,
        )

    async def _fetch_once(self, request: SourceFetchRequest) -> httpx.Response:
        try:
            response = await self._http_client().request(
                request.method.value,
                request.url,
                content=request.body,
                headers={"User-Agent": _USER_AGENT, **request.headers},
            )
        except httpx.TimeoutException as exc:
            raise RequestAttemptError(
                failure_kind=RequestFailureKind.TIMEOUT,
                retry_safety=RetrySafety.SAFE,
                message=str(exc) or "request timed out",
            ) from exc
        except httpx.HTTPError as exc:
            raise RequestAttemptError(
                failure_kind=RequestFailureKind.NETWORK,
                retry_safety=RetrySafety.SAFE,
                message=str(exc) or "network request failed",
            ) from exc
        if is_retryable_http_status(response.status_code):
            raise RequestAttemptError(
                failure_kind=RequestFailureKind.HTTP_STATUS,
                retry_safety=RetrySafety.SAFE,
                message=f"HTTP {response.status_code}: {response.reason_phrase}",
                status_code=response.status_code,
                retry_after_seconds=_retry_after_seconds(response),
            )
        return response

    async def aclose(self) -> None:
        if self._client is None:
            return
        await self._client.aclose()
        self._client = None

    def _http_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                follow_redirects=True,
                limits=httpx.Limits(
                    max_connections=_MAX_CONNECTIONS,
                    max_keepalive_connections=_MAX_KEEPALIVE_CONNECTIONS,
                ),
                timeout=self._timeout_seconds,
                transport=self._transport,
            )
        return self._client


class HttpxTransport:
    def __init__(self, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._transport = transport
        self._clients: dict[tuple[str, str, int | None], httpx.AsyncClient] = {}

    async def send(self, action: HttpAction, *, timeout_seconds: float) -> HttpResponse:
        if not action.connection_addresses:
            raise ValueError("HTTP action must contain validated connection addresses")
        original_url = httpx.URL(action.url)
        headers = {
            "User-Agent": _USER_AGENT,
            **{
                key: value
                for key, value in action.headers.items()
                if key.casefold() != "host"
            },
            "Host": original_url.netloc.decode("ascii"),
        }
        response: httpx.Response | None = None
        last_error: httpx.HTTPError | None = None
        for address in action.connection_addresses:
            try:
                response = await self._http_client(original_url).request(
                    action.method,
                    original_url.copy_with(host=address),
                    content=action.body,
                    headers=headers,
                    timeout=timeout_seconds,
                    extensions={"sni_hostname": original_url.host},
                )
                break
            except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
                last_error = exc
            except httpx.HTTPError as exc:
                raise OSError(str(exc)) from exc
        if response is None:
            if last_error is None:
                raise RuntimeError("validated address set unexpectedly produced no request")
            raise OSError(str(last_error)) from last_error
        return HttpResponse(
            requested_url=action.url,
            final_url=action.url,
            status_code=response.status_code,
            media_type=_media_type(response),
            body=response.content,
            headers=dict(response.headers),
        )

    async def aclose(self) -> None:
        if not self._clients:
            return
        clients = tuple(self._clients.values())
        self._clients.clear()
        await asyncio.gather(*(client.aclose() for client in clients))

    def _http_client(self, url: httpx.URL) -> httpx.AsyncClient:
        origin = (url.scheme, url.host, url.port)
        client = self._clients.get(origin)
        if client is None:
            client = httpx.AsyncClient(
                follow_redirects=False,
                limits=httpx.Limits(
                    max_connections=_MAX_CONNECTIONS_PER_ORIGIN,
                    max_keepalive_connections=_MAX_KEEPALIVE_CONNECTIONS_PER_ORIGIN,
                ),
                transport=self._transport,
            )
            self._clients[origin] = client
        return client


def _http_outcome(status_code: int) -> SourceOutcome:
    if status_code == _HTTP_RATE_LIMITED:
        return SourceOutcome.RATE_LIMITED
    if _HTTP_CLIENT_ERROR_MIN <= status_code < _HTTP_SERVER_ERROR_MIN:
        return SourceOutcome.HTTP_CLIENT_ERROR
    if _HTTP_SERVER_ERROR_MIN <= status_code < _HTTP_SERVER_ERROR_MAX:
        return SourceOutcome.HTTP_SERVER_ERROR
    return SourceOutcome.NETWORK_ERROR


def _request_error_outcome(exc: RequestAttemptError) -> SourceOutcome:
    if exc.failure_kind == RequestFailureKind.TIMEOUT:
        return SourceOutcome.SOURCE_TIMEOUT
    if exc.failure_kind == RequestFailureKind.HTTP_STATUS and exc.status_code is not None:
        return _http_outcome(exc.status_code)
    return SourceOutcome.NETWORK_ERROR


def _retry_after_seconds(response: httpx.Response) -> float | None:
    value = response.headers.get("retry-after")
    if value is None:
        return None
    try:
        parsed = float(value)
    except ValueError:
        return None
    return parsed if parsed >= 0 else None


def _media_type(response: httpx.Response) -> str:
    content_type = response.headers.get("content-type", "")
    media_type = content_type.split(";", 1)[0].strip()
    return media_type or "application/octet-stream"
