from __future__ import annotations

import tempfile
import threading
import unittest
from collections.abc import Callable
from pathlib import Path
from unittest.mock import patch

from job_harness.v2.ports import HttpAction, HttpResponse, OperationContext, RetrySafety
from job_harness.v2.runtime.errors import HttpStatusError, ResponseSizeLimitError
from job_harness.v2.runtime.parser_runtime import DefaultParserRuntime, UnsafeTargetError
from job_harness.v2.runtime.request_retry import RequestAttemptError, RequestFailureKind
from job_harness.v2.runtime.resource_gate import (
    ResourceGate,
    ResourcePolicy,
    ResourceSlotPermit,
    SqliteResourceGateBackend,
)


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
    async def test_prepared_request_offloads_resolution_and_validates_host_once(self) -> None:
        transport = _Transport(
            [
                HttpResponse(
                    requested_url="https://example.com/jobs",
                    final_url="https://example.com/jobs",
                    status_code=200,
                    media_type="application/json",
                    body=b"[]",
                )
            ]
        )
        main_thread_id = threading.get_ident()
        resolver_thread_ids: list[int] = []

        def resolve_host(_host: str) -> tuple[str, ...]:
            resolver_thread_ids.append(threading.get_ident())
            if len(resolver_thread_ids) == 1:
                return ("93.184.216.34",)
            return ("127.0.0.1",)

        runtime = self._runtime(transport, host_resolver=resolve_host)
        action = HttpAction(method="GET", url="https://example.com/jobs")

        retry_after = await runtime.prepare_http(action)
        response = await runtime.http(action)

        self.assertIsNone(retry_after)
        self.assertEqual(response.body, b"[]")
        self.assertEqual(len(resolver_thread_ids), 1)
        self.assertTrue(all(thread_id != main_thread_id for thread_id in resolver_thread_ids))
        self.assertEqual(transport.actions[0].connection_addresses, ("93.184.216.34",))

    async def test_redirect_resolution_never_blocks_the_event_loop(self) -> None:
        transport = _Transport(
            [
                HttpResponse(
                    requested_url="https://example.com/start",
                    final_url="https://example.com/start",
                    status_code=302,
                    media_type="text/plain",
                    body=b"",
                    headers={"location": "https://jobs.example.net/openings"},
                ),
                HttpResponse(
                    requested_url="https://jobs.example.net/openings",
                    final_url="https://jobs.example.net/openings",
                    status_code=200,
                    media_type="text/html",
                    body=b"ok",
                ),
            ]
        )
        main_thread_id = threading.get_ident()
        resolver_thread_ids: list[int] = []

        def resolve_host(_host: str) -> tuple[str, ...]:
            resolver_thread_ids.append(threading.get_ident())
            return ("93.184.216.34",)

        runtime = self._runtime(transport, host_resolver=resolve_host)

        response = await runtime.http(HttpAction(method="GET", url="https://example.com/start"))

        self.assertEqual(response.body, b"ok")
        self.assertEqual(len(resolver_thread_ids), 2)
        self.assertTrue(all(thread_id != main_thread_id for thread_id in resolver_thread_ids))

    async def test_cross_origin_redirect_strips_credentials(self) -> None:
        transport = _Transport(
            [
                HttpResponse(
                    requested_url="https://example.com/start",
                    final_url="https://example.com/start",
                    status_code=302,
                    media_type="text/plain",
                    body=b"",
                    headers={"location": "https://jobs.example.net/openings"},
                ),
                HttpResponse(
                    requested_url="https://jobs.example.net/openings",
                    final_url="https://jobs.example.net/openings",
                    status_code=200,
                    media_type="text/html",
                    body=b"ok",
                ),
            ]
        )
        runtime = self._runtime(transport)

        await runtime.http(
            HttpAction(
                method="GET",
                url="https://example.com/start",
                headers={
                    "Authorization": "Bearer secret",
                    "Cookie": "session=secret",
                    "X-Trace": "keep",
                },
            )
        )

        self.assertEqual(transport.actions[1].headers, {"X-Trace": "keep"})

    async def test_unused_prepared_permit_is_released_off_the_event_loop(self) -> None:
        runtime = self._runtime(_Transport([]))
        action = HttpAction(method="GET", url="https://example.com/jobs")
        release_thread_ids: list[int] = []
        original_release = runtime._resource_gate.release

        def record_release(permit: ResourceSlotPermit) -> None:
            release_thread_ids.append(threading.get_ident())
            original_release(permit)

        await runtime.prepare_http(action)
        with patch.object(
            runtime._resource_gate,
            "release",
            side_effect=record_release,
        ):
            await runtime.release_prepared_http()

        self.assertEqual(len(release_thread_ids), 1)
        self.assertNotEqual(release_thread_ids[0], threading.get_ident())

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

        with self.assertRaisesRegex(ResponseSizeLimitError, "response body exceeds"):
            await runtime.http(HttpAction(method="GET", url="https://example.com"))

        self.assertEqual(runtime.attempt_metrics.network_action_count, 1)
        self.assertEqual(runtime.attempt_metrics.last_status_code, 200)
        self.assertEqual(runtime.attempt_metrics.last_error_class, "ResponseSizeLimitError")

    async def test_rejects_non_success_status_before_parser_receives_body(self) -> None:
        transport = _Transport(
            [
                HttpResponse(
                    requested_url="https://example.com",
                    final_url="https://example.com",
                    status_code=429,
                    media_type="application/json",
                    body=b'{"message":"too many requests"}',
                )
            ]
        )
        runtime = self._runtime(transport)

        with self.assertRaises(HttpStatusError) as raised:
            await runtime.http(HttpAction(method="GET", url="https://example.com"))

        self.assertEqual(raised.exception.status_code, 429)
        self.assertEqual(raised.exception.final_url, "https://example.com")
        self.assertEqual(runtime.attempt_metrics.network_action_count, 1)
        self.assertEqual(runtime.attempt_metrics.last_status_code, 429)
        self.assertEqual(runtime.attempt_metrics.last_error_class, "HttpStatusError")
        self.assertGreaterEqual(runtime.attempt_metrics.network_elapsed_ms, 0)

    async def test_safe_network_failure_becomes_typed_request_attempt_error(self) -> None:
        transport = _Transport([OSError("connection reset")])
        runtime = self._runtime(transport)

        with self.assertRaises(RequestAttemptError) as raised:
            await runtime.http(
                HttpAction(
                    method="GET",
                    url="https://example.com/page-2",
                    retry_safety=RetrySafety.SAFE,
                )
            )

        self.assertEqual(raised.exception.failure_kind, RequestFailureKind.NETWORK)
        self.assertEqual(raised.exception.retry_safety, RetrySafety.SAFE)

    async def test_safe_rate_limit_preserves_status_and_retry_after(self) -> None:
        transport = _Transport(
            [
                HttpResponse(
                    requested_url="https://example.com",
                    final_url="https://example.com",
                    status_code=429,
                    media_type="application/json",
                    body=b"{}",
                    headers={"retry-after": "7"},
                )
            ]
        )
        runtime = self._runtime(transport)

        with self.assertRaises(RequestAttemptError) as raised:
            await runtime.http(
                HttpAction(
                    method="GET",
                    url="https://example.com",
                    retry_safety=RetrySafety.SAFE,
                )
            )

        self.assertEqual(raised.exception.failure_kind, RequestFailureKind.HTTP_STATUS)
        self.assertEqual(raised.exception.status_code, 429)
        self.assertEqual(raised.exception.retry_after_seconds, 7.0)

    async def test_safe_not_found_remains_terminal_http_status(self) -> None:
        transport = _Transport(
            [
                HttpResponse(
                    requested_url="https://example.com/missing",
                    final_url="https://example.com/missing",
                    status_code=404,
                    media_type="text/html",
                    body=b"missing",
                )
            ]
        )
        runtime = self._runtime(transport)

        with self.assertRaises(HttpStatusError) as raised:
            await runtime.http(
                HttpAction(
                    method="GET",
                    url="https://example.com/missing",
                    retry_safety=RetrySafety.SAFE,
                )
            )

        self.assertEqual(raised.exception.status_code, 404)

    def _runtime(
        self,
        transport: _Transport,
        *,
        max_response_bytes: int = 1024,
        host_resolver: Callable[[str], tuple[str, ...]] | None = None,
    ) -> DefaultParserRuntime:
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
            host_resolver=host_resolver or (lambda _: ("93.184.216.34",)),
        )


if __name__ == "__main__":
    unittest.main()
