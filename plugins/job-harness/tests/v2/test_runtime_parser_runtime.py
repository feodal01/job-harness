from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from job_harness.v2.ports import HttpAction, HttpResponse, OperationContext
from job_harness.v2.runtime.parser_runtime import DefaultParserRuntime, UnsafeTargetError
from job_harness.v2.runtime.resource_gate import ResourceGate, ResourcePolicy, SqliteResourceGateBackend


class _Transport:
    def __init__(self, responses: list[HttpResponse | Exception]) -> None:
        self.responses = responses
        self.actions: list[HttpAction] = []
        self.timeouts: list[float] = []

    async def send(self, action: HttpAction, *, timeout_seconds: float) -> HttpResponse:
        self.actions.append(action)
        self.timeouts.append(timeout_seconds)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class DefaultParserRuntimeTest(unittest.IsolatedAsyncioTestCase):
    async def test_rejects_private_target_before_transport(self) -> None:
        transport = _Transport([])
        runtime = self._runtime(transport)

        with self.assertRaises(UnsafeTargetError):
            await runtime.http(HttpAction(method="GET", url="http://127.0.0.1/admin"))

        self.assertEqual(transport.actions, [])

    async def test_rejects_unsafe_final_url_after_redirect(self) -> None:
        transport = _Transport(
            [
                HttpResponse(
                    requested_url="https://example.com/start",
                    final_url="http://169.254.169.254/latest/meta-data",
                    status_code=200,
                    media_type="text/plain",
                    body=b"secret",
                )
            ]
        )
        runtime = self._runtime(transport)

        with self.assertRaises(UnsafeTargetError):
            await runtime.http(HttpAction(method="GET", url="https://example.com/start"))

    async def test_transport_failure_releases_fixed_slot(self) -> None:
        transport = _Transport(
            [
                RuntimeError("network failed"),
                HttpResponse(
                    requested_url="https://example.com/one",
                    final_url="https://example.com/two",
                    status_code=200,
                    media_type="text/plain",
                    body=b"ok",
                ),
            ]
        )
        runtime = self._runtime(transport)

        with self.assertRaisesRegex(RuntimeError, "network failed"):
            await runtime.http(HttpAction(method="GET", url="https://example.com/one"))
        response = await runtime.http(HttpAction(method="GET", url="https://example.com/two"))

        self.assertEqual(response.body, b"ok")
        self.assertEqual(len(transport.actions), 2)

    async def test_rejects_oversized_response(self) -> None:
        transport = _Transport(
            [
                HttpResponse(
                    requested_url="https://example.com",
                    final_url="https://example.com",
                    status_code=200,
                    media_type="text/plain",
                    body=b"12345",
                )
            ]
        )
        runtime = self._runtime(transport, max_response_bytes=4)

        with self.assertRaisesRegex(ValueError, "response body exceeds"):
            await runtime.http(HttpAction(method="GET", url="https://example.com"))

    def _runtime(self, transport: _Transport, *, max_response_bytes: int = 1024) -> DefaultParserRuntime:
        temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(temporary_directory.cleanup)
        backend = SqliteResourceGateBackend(Path(temporary_directory.name) / "resource-gate.sqlite")
        return DefaultParserRuntime(
            context=OperationContext("operation", execution_id=None, invocation_id=None),
            reserved_collection_units=1,
            transport=transport,
            resource_gate=ResourceGate(backend=backend, owner_id="process"),
            policy_for_resource=lambda _: ResourcePolicy(
                max_concurrency=1,
                min_interval_seconds=0.0,
                lease_seconds=10.0,
            ),
            timeout_seconds=2.0,
            max_response_bytes=max_response_bytes,
            host_resolver=lambda _: ("93.184.216.34",),
        )


if __name__ == "__main__":
    unittest.main()
