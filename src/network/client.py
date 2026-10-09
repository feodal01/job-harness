"""One source request budget for listing, detail and company requests across all runs."""

import asyncio
import logging
import random
import time
from email.utils import parsedate_to_datetime
from urllib.parse import urljoin, urlsplit

import httpx

from src.core.observability import log_context
from src.db.protocols import EventStore, ResourceStore
from src.parsers.interfaces import ResponseInfo, SourceAdapter

logger = logging.getLogger(__name__)


class FetchError(RuntimeError):
    def __init__(self, kind: str, message: str) -> None:
        self.kind = kind
        super().__init__(f"{kind}: {message}")


class Network:
    def __init__(
        self, source: SourceAdapter, resources: ResourceStore, events: EventStore, client: httpx.AsyncClient
    ) -> None:
        self.source, self.settings, self.client = source, source.settings, client
        self.resources, self.events = resources, events
        self.slots = asyncio.Semaphore(source.settings.concurrency)
        self.pacing = asyncio.Lock()
        self.state = resources.resource(source.resource)

    def block(self) -> None:
        self.state["open_until"] = time.time() + self.settings.breaker_seconds
        self.resources.resource(self.source.resource, self.state)

    def availability(self) -> dict:
        open_until = self.state["open_until"]
        if open_until > time.time():
            return {"status": "blocked", "until": open_until}
        return {"status": "ready", "until": None}

    async def _admit(self) -> None:
        async with self.pacing:
            if self.state["open_until"] > time.time():
                raise FetchError(
                    "circuit_open", f"{self.source.name} circuit breaker is open; retry after cooldown"
                )
            while (delay := self.state["next_request_at"] - time.time()) > 0:  # noqa: ASYNC110 - timed admission
                await asyncio.sleep(delay)
            # Another in-flight response may have opened the breaker while we waited.
            if self.state["open_until"] > time.time():
                raise FetchError("circuit_open", f"{self.source.name} circuit breaker opened while waiting")
            self.state["next_request_at"] = time.time() + self.settings.interval_seconds
            self.resources.resource(self.source.resource, self.state)

    async def get(self, run_id: str | None, url: str, stage: str) -> str:
        with log_context(run_id=run_id or "-", source_id=self.source.id, stage=stage):
            logger.debug("source.request.started")
            try:
                return await self._request(run_id, url, stage)
            finally:
                logger.debug("source.request.finished")

    async def _request(self, run_id: str | None, url: str, stage: str) -> str:
        redirects = 0
        attempt = 0
        while attempt < self.settings.max_attempts:
            host = urlsplit(url).hostname or ""
            if urlsplit(url).scheme != "https" or not any(
                host == allowed or host.endswith("." + allowed) for allowed in self.source.allowed_hosts
            ):
                raise FetchError(
                    "unsupported_redirect", f"URL is outside allowed HTTPS hosts for {self.source.id}"
                )
            started = time.monotonic()
            attempt += 1
            delay = self.settings.retry_base_seconds * 2 ** (attempt - 1) * random.uniform(0.8, 1.2)
            error = None
            try:
                async with self.slots:
                    await self._admit()
                    sent = time.monotonic()
                    self.events.event(
                        run_id, "http.started", source=self.source.id, url=url, stage=stage, attempt=attempt
                    )
                    # Account for time spent committing the event before the actual send.
                    self.state["next_request_at"] = time.time() + self.settings.interval_seconds
                    response = await self.client.get(url, timeout=self.settings.request_timeout_seconds)
                    self.events.event(
                        run_id,
                        "http.finished",
                        source=self.source.id,
                        url=url,
                        stage=stage,
                        attempt=attempt,
                        status_code=response.status_code,
                        elapsed_ms=round((time.monotonic() - sent) * 1000, 2),
                        wait_ms=round((sent - started) * 1000, 2),
                        bytes=len(response.content),
                    )
                classified = self.source.classify and self.source.classify(
                    ResponseInfo(url, response.status_code, dict(response.headers), response.text)
                )
                if classified:
                    self.block()
                    raise FetchError(classified, f"{self.source.name} returned a {classified} page")
                if response.is_redirect:
                    redirects += 1
                    if redirects > 5 or "location" not in response.headers:
                        raise FetchError("redirect_error", "Redirect limit or missing Location")
                    url = urljoin(url, response.headers["location"])
                    attempt -= 1
                    continue
                if response.status_code == 429:
                    retry_after = response.headers.get("retry-after", "30")
                    try:
                        delay = max(delay, float(retry_after))
                    except ValueError:
                        try:
                            delay = max(delay, parsedate_to_datetime(retry_after).timestamp() - time.time())
                        except (TypeError, ValueError, OverflowError):
                            delay = max(delay, 30)
                    self.state["next_request_at"] = max(self.state["next_request_at"], time.time() + delay)
                    self.resources.resource(self.source.resource, self.state)
                    error = FetchError("rate_limited", f"{self.source.name} returned HTTP 429")
                elif response.status_code in {401, 403, 451}:
                    self.block()
                    raise FetchError("blocked", f"{self.source.name} returned HTTP {response.status_code}")
                elif response.status_code >= 500:
                    error = FetchError(
                        "http_error", f"{self.source.name} returned HTTP {response.status_code}"
                    )
                elif response.status_code in {404, 410}:
                    raise FetchError("not_found", f"{self.source.name} returned HTTP {response.status_code}")
                elif response.is_error:
                    raise FetchError("http_error", f"{self.source.name} returned HTTP {response.status_code}")
                else:
                    self.state["failures"] = 0
                    self.resources.resource(self.source.resource, self.state)
                    return response.text
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                error = FetchError(
                    "timeout" if isinstance(exc, httpx.TimeoutException) else "network_error",
                    str(exc) or type(exc).__name__,
                )
                self.events.event(
                    run_id,
                    "http.error",
                    source=self.source.id,
                    url=url,
                    stage=stage,
                    attempt=attempt,
                    error=str(error),
                    elapsed_ms=round((time.monotonic() - started) * 1000, 2),
                )
            if error is None:
                raise RuntimeError("Unexpected empty HTTP outcome")
            if error.kind != "rate_limited":
                self.state["failures"] += 1
                if self.state["failures"] >= self.settings.breaker_threshold:
                    self.block()
                self.resources.resource(self.source.resource, self.state)
            if attempt == self.settings.max_attempts:
                raise error
            self.events.event(
                run_id,
                "http.retry",
                source=self.source.id,
                url=url,
                stage=stage,
                attempt=attempt,
                delay_seconds=delay,
                error=str(error),
            )
            await asyncio.sleep(delay)
        raise RuntimeError("Unreachable request loop")
