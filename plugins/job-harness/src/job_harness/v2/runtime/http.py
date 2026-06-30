"""HTTP artifact fetcher for the contract-first runtime."""

from __future__ import annotations

import asyncio
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

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


class HttpArtifactFetcher:
    def __init__(self, *, timeout_seconds: float = 15.0) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be > 0")
        self._timeout_seconds = timeout_seconds

    async def fetch(self, request: SourceFetchRequest) -> SourceResponseArtifact:
        return await asyncio.to_thread(self._fetch_sync, request)

    def _fetch_sync(self, request: SourceFetchRequest) -> SourceResponseArtifact:
        headers = {"User-Agent": _USER_AGENT, **request.headers}
        http_request = Request(
            request.url,
            data=request.body,
            headers=headers,
            method=request.method.value,
        )
        try:
            with urlopen(http_request, timeout=self._timeout_seconds) as response:
                body = response.read().decode("utf-8", errors="replace")
                media_type = response.headers.get_content_type()
        except HTTPError as exc:
            raise ClassifiedSourceError(_http_outcome(exc.code), str(exc)) from exc
        except (OSError, TimeoutError, URLError) as exc:
            raise ClassifiedSourceError(SourceOutcome.NETWORK_ERROR, str(exc)) from exc

        return SourceResponseArtifact(
            source_id=request.source_id,
            url=request.url,
            media_type=media_type,
            body=body,
        )


def _http_outcome(status_code: int) -> SourceOutcome:
    if status_code == _HTTP_RATE_LIMITED:
        return SourceOutcome.RATE_LIMITED
    if _HTTP_CLIENT_ERROR_MIN <= status_code < _HTTP_SERVER_ERROR_MIN:
        return SourceOutcome.HTTP_CLIENT_ERROR
    if _HTTP_SERVER_ERROR_MIN <= status_code < _HTTP_SERVER_ERROR_MAX:
        return SourceOutcome.HTTP_SERVER_ERROR
    return SourceOutcome.NETWORK_ERROR
