from __future__ import annotations

import ssl
import unittest
from unittest.mock import patch
from urllib.error import URLError

from job_harness.v2.contracts import SourceFetchRequest, SourceOutcome
from job_harness.v2.runtime import ClassifiedSourceError, HttpArtifactFetcher


class HttpArtifactFetcherTest(unittest.IsolatedAsyncioTestCase):
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

        # Act / Assert
        with (
            patch("job_harness.v2.runtime.http.urlopen", side_effect=URLError(ssl_error)),
            self.assertRaises(ClassifiedSourceError) as caught,
        ):
            await HttpArtifactFetcher(timeout_seconds=1).fetch(request)

        self.assertEqual(SourceOutcome.NETWORK_ERROR, caught.exception.outcome)
        self.assertIn("certificate verify failed", caught.exception.evidence.error or "")


if __name__ == "__main__":
    unittest.main()
