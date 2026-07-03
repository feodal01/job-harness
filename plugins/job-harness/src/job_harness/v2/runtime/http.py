"""HTTP artifact fetcher for the contract-first runtime."""

from __future__ import annotations

import httpx

from job_harness.v2.contracts import SourceFetchRequest, SourceOutcome, SourceResponseArtifact
from job_harness.v2.runtime.errors import ClassifiedSourceError

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


class HttpArtifactFetcher:
    def __init__(
        self,
        *,
        timeout_seconds: float = 15.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be > 0")
        self._timeout_seconds = timeout_seconds
        self._transport = transport
        self._client: httpx.AsyncClient | None = None

    async def fetch(self, request: SourceFetchRequest) -> SourceResponseArtifact:
        try:
            response = await self._http_client().request(
                request.method.value,
                request.url,
                content=request.body,
                headers={"User-Agent": _USER_AGENT, **request.headers},
            )
        except httpx.HTTPError as exc:
            raise ClassifiedSourceError(SourceOutcome.NETWORK_ERROR, str(exc)) from exc

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


def _http_outcome(status_code: int) -> SourceOutcome:
    if status_code == _HTTP_RATE_LIMITED:
        return SourceOutcome.RATE_LIMITED
    if _HTTP_CLIENT_ERROR_MIN <= status_code < _HTTP_SERVER_ERROR_MIN:
        return SourceOutcome.HTTP_CLIENT_ERROR
    if _HTTP_SERVER_ERROR_MIN <= status_code < _HTTP_SERVER_ERROR_MAX:
        return SourceOutcome.HTTP_SERVER_ERROR
    return SourceOutcome.NETWORK_ERROR


def _media_type(response: httpx.Response) -> str:
    content_type = response.headers.get("content-type", "")
    media_type = content_type.split(";", 1)[0].strip()
    return media_type or "application/octet-stream"
