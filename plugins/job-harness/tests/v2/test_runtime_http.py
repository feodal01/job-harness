from __future__ import annotations

import ssl
import unittest
from unittest.mock import patch

import httpx

from job_harness.v2.contracts import SourceFetchRequest, SourceOutcome
from job_harness.v2.runtime import ClassifiedSourceError, HttpArtifactFetcher


class HttpArtifactFetcherTest(unittest.IsolatedAsyncioTestCase):
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
            ).fetch(request)

        self.assertEqual(SourceOutcome.RATE_LIMITED, caught.exception.outcome)


if __name__ == "__main__":
    unittest.main()
