from __future__ import annotations

import ssl
import unittest
from unittest.mock import patch

import httpx

from job_harness.v2.contracts import SourceFetchRequest, SourceOutcome
from job_harness.v2.ports import HttpAction
from job_harness.v2.runtime import ClassifiedSourceError, HttpArtifactFetcher
from job_harness.v2.runtime.http import HttpxTransport
from job_harness.v2.runtime.request_retry import RequestRetryPolicy


class HttpArtifactFetcherTest(unittest.IsolatedAsyncioTestCase):
    async def test_retryable_status_retries_only_the_current_fetch(self) -> None:
        request = SourceFetchRequest(
            source_id="career:test",
            query_variant="QA",
            url="https://example.test/jobs?page=2",
        )
        calls = 0
        sleeps: list[float] = []

        def respond(_request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            if calls == 1:
                return httpx.Response(503)
            return httpx.Response(200, text="page two")

        async def record_sleep(delay: float) -> None:
            sleeps.append(delay)

        fetcher = HttpArtifactFetcher(
            timeout_seconds=1,
            transport=httpx.MockTransport(respond),
            request_retry_policy=RequestRetryPolicy(
                attempt_timeout_seconds=1,
                request_budget_seconds=5,
                random_fraction=lambda: 0.5,
            ),
            sleep=record_sleep,
        )

        artifact = await fetcher.fetch(request)

        self.assertEqual(artifact.body, "page two")
        self.assertEqual(calls, 2)
        self.assertEqual(sleeps, [0.5])

    def test_http_client_uses_explicit_pool_limits_for_full_catalog_runs(self) -> None:
        # Arrange
        fetcher = HttpArtifactFetcher(timeout_seconds=1)

        # Act
        with patch("job_harness.v2.runtime.http.httpx.AsyncClient") as async_client:
            fetcher._http_client()

        # Assert
        limits = async_client.call_args.kwargs["limits"]
        self.assertEqual(256, limits.max_connections)
        self.assertEqual(64, limits.max_keepalive_connections)

    async def test_tls_certificate_verification_failure_is_network_error(self) -> None:
        # Arrange
        request = SourceFetchRequest(
            source_id="career:vk",
            query_variant="QA",
            url="https://team.vk.company/vacancy/?specialty=284",
        )
        ssl_error = ssl.SSLCertVerificationError(
            "certificate verify failed: unable to get local issuer certificate"
        )

        def raise_tls_error(_request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError(str(ssl_error))

        # Act / Assert
        with self.assertRaises(ClassifiedSourceError) as caught:
            await HttpArtifactFetcher(
                timeout_seconds=1,
                transport=httpx.MockTransport(raise_tls_error),
            ).fetch(request)

        self.assertEqual(SourceOutcome.NETWORK_ERROR, caught.exception.outcome)
        self.assertIn("certificate verify failed", caught.exception.evidence.error or "")

    async def test_http_rate_limit_status_is_rate_limited(self) -> None:
        # Arrange
        request = SourceFetchRequest(
            source_id="rate_limited_jobs",
            query_variant="QA",
            url="https://example.test/jobs",
        )

        # Act / Assert
        with self.assertRaises(ClassifiedSourceError) as caught:
            await HttpArtifactFetcher(
                timeout_seconds=1,
                transport=httpx.MockTransport(lambda _request: httpx.Response(429)),
                request_retry_policy=RequestRetryPolicy(
                    max_attempts=1,
                    attempt_timeout_seconds=1,
                    request_budget_seconds=1,
                ),
            ).fetch(request)

        self.assertEqual(SourceOutcome.RATE_LIMITED, caught.exception.outcome)


class HttpxTransportTest(unittest.IsolatedAsyncioTestCase):
    async def test_connects_to_validated_ip_with_original_host_and_tls_sni(self) -> None:
        requests: list[httpx.Request] = []

        def respond(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(200, text="ok")

        transport = HttpxTransport(transport=httpx.MockTransport(respond))
        action = HttpAction(
            method="GET",
            url="https://jobs.example.com:8443/openings?page=2",
            connection_addresses=("93.184.216.34",),
        )
        self.addAsyncCleanup(transport.aclose)

        response = await transport.send(action, timeout_seconds=1.0)

        self.assertEqual(len(requests), 1)
        self.assertEqual(str(requests[0].url), "https://93.184.216.34:8443/openings?page=2")
        self.assertEqual(requests[0].headers["host"], "jobs.example.com:8443")
        self.assertEqual(requests[0].extensions["sni_hostname"], "jobs.example.com")
        self.assertEqual(response.requested_url, action.url)
        self.assertEqual(response.final_url, action.url)

    async def test_tries_each_validated_address_without_resolving_the_hostname(self) -> None:
        requested_hosts: list[str] = []

        def respond(request: httpx.Request) -> httpx.Response:
            requested_hosts.append(request.url.host)
            if request.url.host == "93.184.216.34":
                raise httpx.ConnectError("first address unavailable")
            return httpx.Response(200, text="ok")

        transport = HttpxTransport(transport=httpx.MockTransport(respond))
        action = HttpAction(
            method="GET",
            url="https://jobs.example.com/openings",
            connection_addresses=("93.184.216.34", "93.184.216.35"),
        )
        self.addAsyncCleanup(transport.aclose)

        response = await transport.send(action, timeout_seconds=1.0)

        self.assertEqual(requested_hosts, ["93.184.216.34", "93.184.216.35"])
        self.assertEqual(response.body, b"ok")

    async def test_does_not_fail_over_after_a_request_may_have_been_sent(self) -> None:
        requested_hosts: list[str] = []

        def respond(request: httpx.Request) -> httpx.Response:
            requested_hosts.append(request.url.host)
            if request.url.host == "93.184.216.34":
                raise httpx.ReadTimeout("response timed out")
            return httpx.Response(200, text="unexpected duplicate")

        transport = HttpxTransport(transport=httpx.MockTransport(respond))
        action = HttpAction(
            method="POST",
            url="https://jobs.example.com/search",
            body=b"query=qa",
            connection_addresses=("93.184.216.34", "93.184.216.35"),
        )
        self.addAsyncCleanup(transport.aclose)

        with self.assertRaises(OSError):
            await transport.send(action, timeout_seconds=1.0)

        self.assertEqual(requested_hosts, ["93.184.216.34"])


if __name__ == "__main__":
    unittest.main()
