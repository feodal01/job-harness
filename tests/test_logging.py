import asyncio
import json
import logging
import re
import subprocess
import sys

import pytest
from geo_support import confirmed
from starlette.types import Message, Receive, Scope, Send

from src.core.logger import JsonFormatter
from src.core.observability import ContextFilter, RequestLoggingMiddleware, log_context


def context_record() -> logging.LogRecord:
    record = logging.LogRecord("test", logging.INFO, __file__, 1, "context", (), None)
    ContextFilter().filter(record)
    return record


def test_nested_context_is_restored_after_exception() -> None:
    assert context_record().request_id == "-"
    with log_context(request_id="outer", run_id="run"):
        with pytest.raises(RuntimeError), log_context(request_id="inner", source_id="hh"):
            record = context_record()
            assert (record.request_id, record.run_id, record.source_id) == ("inner", "run", "hh")
            raise RuntimeError("failure")
        record = context_record()
        assert (record.request_id, record.source_id) == ("outer", "-")
    assert context_record().request_id == "-"


async def test_parallel_tasks_keep_independent_context() -> None:
    ready = asyncio.Event()
    entered = 0

    async def operation(identity: str) -> tuple[str, str]:
        nonlocal entered
        with log_context(request_id=identity, source_id=identity):
            entered += 1
            if entered == 2:
                ready.set()
            await ready.wait()
            await asyncio.sleep(0)
            record = context_record()
            return record.request_id, record.source_id

    assert await asyncio.gather(operation("one"), operation("two")) == [("one", "one"), ("two", "two")]
    assert context_record().source_id == "-"


def http_scope(request_ids: list[bytes]) -> Scope:
    return {
        "type": "http",
        "method": "GET",
        "path": "/api/runs",
        "query_string": b"brief=PRIVATE_BRIEF",
        "headers": [(b"x-request-id", value) for value in request_ids],
    }


async def receive() -> Message:
    return {"type": "http.request", "body": b"PRIVATE_BODY"}


@pytest.mark.parametrize(
    "request_ids", [[b"valid.ID-123"], [], [b"bad\nvalue"], [b"a" * 65], [b"\xff"], [b"a", b"b"]]
)
async def test_request_header_and_safe_fields(
    request_ids: list[bytes], caplog: pytest.LogCaptureFixture
) -> None:
    messages: list[Message] = []
    captured: list[logging.LogRecord] = []

    class Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            ContextFilter().filter(record)
            captured.append(record)

    logger = logging.getLogger("src.core.observability")
    handler = Capture()
    logger.addHandler(handler)

    async def application(scope: Scope, receive: Receive, send: Send) -> None:
        await send(
            {"type": "http.response.start", "status": 204, "headers": [(b"x-request-id", b"replaced")]}
        )
        await send({"type": "http.response.body", "body": b""})

    async def send(message: Message) -> None:
        messages.append(message)

    try:
        with caplog.at_level(logging.INFO):
            await RequestLoggingMiddleware(application)(http_scope(request_ids), receive, send)
    finally:
        logger.removeHandler(handler)
    echoed = dict(messages[0]["headers"])[b"x-request-id"].decode()
    if request_ids == [b"valid.ID-123"]:
        assert echoed == "valid.ID-123"
    else:
        assert re.fullmatch(r"[0-9a-f]{32}", echoed)
    record = captured[-1]
    assert (record.request_id, record.status, record.method, record.path) == (echoed, 204, "GET", "/api/runs")
    assert record.duration_ms >= 0
    assert "PRIVATE" not in JsonFormatter().format(record)
    assert context_record().request_id == "-"


async def test_exception_keeps_context_and_reraises(caplog: pytest.LogCaptureFixture) -> None:
    formatted: list[str] = []

    class Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            ContextFilter().filter(record)
            formatted.append(JsonFormatter().format(record))

    async def application(scope: Scope, receive: Receive, send: Send) -> None:
        raise RuntimeError("PRIVATE_BRIEF authorization=PRIVATE_TOKEN")

    async def send(message: Message) -> None:
        pass

    logger = logging.getLogger("src.core.observability")
    handler = Capture()
    logger.addHandler(handler)
    try:
        with caplog.at_level(logging.INFO), pytest.raises(RuntimeError, match="PRIVATE_BRIEF"):
            await RequestLoggingMiddleware(application)(http_scope([b"failed-request"]), receive, send)
    finally:
        logger.removeHandler(handler)
    payload = json.loads(formatted[0])
    assert payload["request_id"] == "failed-request"
    assert payload["status"] == 500
    assert payload["message"] == "request.failed"
    assert "RuntimeError" in payload["exception"]
    assert "application" in payload["exception"]
    assert "PRIVATE_TOKEN" not in payload["exception"]
    assert context_record().request_id == "-"


async def test_cancellation_restores_context(caplog: pytest.LogCaptureFixture) -> None:
    async def application(scope: Scope, receive: Receive, send: Send) -> None:
        raise asyncio.CancelledError

    async def send(message: Message) -> None:
        pass

    with caplog.at_level(logging.INFO), log_context(request_id="parent"):
        with pytest.raises(asyncio.CancelledError):
            await RequestLoggingMiddleware(application)(http_scope([b"cancelled"]), receive, send)
        assert context_record().request_id == "parent"
    assert context_record().request_id == "-"
    assert caplog.records[-1].message == "request.cancelled"


@pytest.mark.parametrize("log_format", ["text", "json"])
def test_configuration_stdout_levels_and_no_duplicates(log_format: str) -> None:
    script = """
import logging
from src.core.config import Settings
from src.core.logger import configure_logging
from src.core.observability import log_context
settings = Settings(log_format=FORMAT, log_level="INFO")
configure_logging(settings)
configure_logging(settings)
with log_context(request_id="request-one", run_id="run-one", source_id="hh", stage="fetch"):
    logging.getLogger("src.example").info("app.event")
    logging.getLogger("uvicorn.error").info("server.event")
    logging.getLogger("uvicorn.access").info("PRIVATE_QUERY")
    logging.getLogger("httpx").info("PRIVATE_URL")
    logging.getLogger("httpcore").debug("PRIVATE_HEADERS")
    logging.getLogger("src.example").debug("hidden.debug")
configure_logging(Settings(log_format=FORMAT, log_level="ERROR"))
logging.getLogger("src.example").info("hidden.info")
logging.getLogger("src.example").error("error.event")
""".replace("FORMAT", repr(log_format))
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, check=True)
    assert result.stderr == ""
    lines = result.stdout.splitlines()
    assert len(lines) == 3
    assert "PRIVATE" not in result.stdout
    assert "hidden" not in result.stdout
    if log_format == "json":
        payloads = [json.loads(line) for line in lines]
        assert [payload["message"] for payload in payloads] == ["app.event", "server.event", "error.event"]
        assert payloads[0]["request_id"] == "request-one"
        assert payloads[0]["stage"] == "fetch"
        assert payloads[-1]["request_id"] == "-"
    else:
        assert 'request_id="request-one"' in lines[0]
        assert 'stage="fetch"' in lines[0]
        assert 'request_id="-"' in lines[-1]


async def test_concurrent_http_runs_keep_engine_source_and_network_contexts(
    tmp_path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    from types import SimpleNamespace

    import httpx

    from src.app import create_app
    from src.core.config import Settings
    from src.models.vacancy import Vacancy
    from src.parsers.interfaces import RequestSettings, SearchPage, SearchPlan, SearchRequest, SourceAdapter

    def source(source_id: str) -> SourceAdapter:
        host = f"{source_id}.example.test"

        def build(query: str) -> SearchRequest:
            return SearchRequest(f"https://{host}/listing?phrase={query}", query)

        async def prepare(brief, fetch, *, second_chance=False) -> SearchPlan:
            return SearchPlan(tuple(build(query) for query in brief.queries), {"query": "native"})

        def parse(body: str, request: SearchRequest) -> SearchPage:
            return SearchPage(
                vacancies=[
                    Vacancy(id=request.query, title="Engineer", url=f"https://{host}/detail/{request.query}")
                ],
                total=1,
            )

        return SourceAdapter(
            id=source_id,
            name=source_id,
            type="career_site",
            resource=host,
            allowed_hosts=(host,),
            settings=RequestSettings(concurrency=2, interval_seconds=0, max_attempts=1),
            search=SimpleNamespace(
                prepare=prepare,
                build=build,
                parse=parse,
                parameters={"query": "native"},
                fields=frozenset({"title"}),
                query_notes="",
            ),
            vacancy_details=SimpleNamespace(
                fields=frozenset({"description"}),
                url=lambda vacancy: vacancy.url,
                parse=lambda body, vacancy: vacancy.model_copy(update={"description": body}),
            ),
        )

    adapters = {name: source(name) for name in ("left", "right")}
    monkeypatch.setattr("src.app.build_sources", lambda settings, catalog=None: adapters)
    settings = Settings(database_path=tmp_path / "logging.sqlite")
    arrived = 0
    all_listings_started = asyncio.Event()
    transport_contexts: list[tuple[str, str, str, str]] = []

    async def transport(request: httpx.Request) -> httpx.Response:
        nonlocal arrived
        record = context_record()
        transport_contexts.append((record.request_id, record.run_id, record.source_id, record.stage))
        assert record.source_id == request.url.host.split(".")[0]
        if request.url.path == "/listing":
            arrived += 1
            if arrived == 4:
                all_listings_started.set()
            # All four run/source pairs must overlap before any listing can finish.
            await asyncio.wait_for(all_listings_started.wait(), timeout=5)
        return httpx.Response(200, text="PRIVATE_RESPONSE_BODY")

    records: list[dict] = []

    class Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(json.loads(JsonFormatter().format(record)))

    handler = Capture()
    handler.addFilter(lambda record: record.name.startswith("src."))
    handler.addFilter(ContextFilter())
    root = logging.getLogger()
    root.addHandler(handler)
    app = create_app(settings, transport=httpx.MockTransport(transport))
    try:
        with caplog.at_level(logging.DEBUG, logger="src"):
            async with app.router.lifespan_context(app):
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app), base_url="http://testserver"
                ) as client:
                    briefs = []
                    for number in (1, 2):
                        response = await client.post(
                            "/api/v1/briefs",
                            json={
                                "name": "Logging",
                                "filters": confirmed(),
                                "text": "PRIVATE_BRIEF",
                                "queries": [f"PRIVATE_QUERY_{number}"],
                            },
                        )
                        assert response.status_code == 201
                        briefs.append(response.json()["id"])
                    launched = await asyncio.gather(
                        *(
                            client.post(
                                "/api/v1/runs",
                                json={"brief_id": brief_id, "sources": ["left", "right"]},
                                headers={
                                    "X-Request-Id": f"request-{number}",
                                    "Authorization": "PRIVATE_TOKEN",
                                },
                            )
                            for number, brief_id in enumerate(briefs, 1)
                        )
                    )
                    assert all(response.status_code == 202 for response in launched)
                    expected_requests = {
                        response.json()["id"]: f"request-{number}"
                        for number, response in enumerate(launched, 1)
                    }
                    await asyncio.wait_for(asyncio.gather(*app.state.engine.tasks.values()), timeout=5)
                    for run_id in expected_requests:
                        result = await client.get(f"/api/v1/runs/{run_id}")
                        assert result.json()["status"] == "complete"
                    health = await client.get("/api/v1/health", headers={"X-Request-Id": "after-runs"})
                    assert health.headers["X-Request-Id"] == "after-runs"
    finally:
        root.removeHandler(handler)

    assert arrived == 4
    run_records = [record for record in records if record["message"] in {"run.started", "run.finished"}]
    assert len(run_records) == 4
    for record in run_records:
        assert record["request_id"] == expected_requests[record["run_id"]]
        assert record["source_id"] == "-"
        assert record["stage"] == "search"

    for event in ("source.started", "source.finished"):
        source_records = [record for record in records if record["message"] == event]
        assert {(record["run_id"], record["source_id"]) for record in source_records} == {
            (run_id, source_id) for run_id in expected_requests for source_id in adapters
        }
        assert len(source_records) == 4
        for record in source_records:
            assert record["request_id"] == expected_requests[record["run_id"]]
            assert record["stage"] == "source"

    for event in ("source.request.started", "source.request.finished"):
        network_records = [record for record in records if record["message"] == event]
        assert {(record["run_id"], record["source_id"], record["stage"]) for record in network_records} == {
            (run_id, source_id, stage)
            for run_id in expected_requests
            for source_id in adapters
            for stage in ("listing", "detail")
        }
        assert len(network_records) == 8
        for record in network_records:
            assert record["request_id"] == expected_requests[record["run_id"]]
    assert len(transport_contexts) == 8
    assert all(request_id == expected_requests[run_id] for request_id, run_id, _, _ in transport_contexts)
    health_record = next(record for record in records if record["request_id"] == "after-runs")
    assert (health_record["run_id"], health_record["source_id"], health_record["stage"]) == ("-", "-", "-")
    outside = context_record()
    assert (outside.request_id, outside.run_id, outside.source_id, outside.stage) == ("-", "-", "-", "-")
    assert "PRIVATE" not in json.dumps(records)
