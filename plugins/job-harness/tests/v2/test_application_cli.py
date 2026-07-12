from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from job_harness.v2.application import V2SearchApplication, V2SearchConfig
from job_harness.v2.cli import _build_parser, _query_variants, _request_from_args, main as cli_main
from job_harness.v2.contracts import (
    ParserFixtureCase,
    ParserFixtureKind,
    RawListing,
    SearchRequest,
    WorkFormat,
)
from job_harness.v2.ports import HttpAction, HttpResponse
from job_harness.v2.runtime import (
    ApplicationChannelServiceConfig,
    AtsCompanyUrlParseResult,
    DetailServiceConfig,
    RetryServiceConfig,
    SearchServiceConfig,
)
from job_harness.v2.runtime.parser_runtime import DefaultParserRuntimeFactory
from job_harness.v2.runtime.resource_gate import ResourceGate, ResourcePolicy, SqliteResourceGateBackend
from job_harness.v2.runtime.source_registry import build_independent_parser_registry
from job_harness.v2.runtime.sources.companies.ats import AtsCompanySourceConfig
from job_harness.v2.source_catalog import country_catalog_entries, source_catalog_entries, source_fixture_suite

_PLUGIN_ROOT_PARENT_INDEX = 2
_PLUGIN_ROOT = Path(__file__).resolve().parents[_PLUGIN_ROOT_PARENT_INDEX]


class FixtureTransport:
    def __init__(self, mapping: dict[str, Path]) -> None:
        self._mapping = mapping

    async def send(self, action: HttpAction, *, timeout_seconds: float) -> HttpResponse:
        del timeout_seconds
        path = self._mapping[action.url]
        return HttpResponse(
            requested_url=action.url,
            final_url=action.url,
            status_code=200,
            media_type="application/json" if path.suffix == ".json" else "text/html",
            body=path.read_bytes(),
        )


def _test_service_config() -> SearchServiceConfig:
    return SearchServiceConfig(
        source_attempt_timeout_seconds=30.0,
        run_timeout_seconds=60.0,
        fetch_timeout_seconds=15.0,
        retry=RetryServiceConfig(max_attempts=1, backoff_seconds=0.0),
        detail=DetailServiceConfig(
            per_source_concurrency=1,
            default_request_delay_seconds=0.0,
            request_delay_seconds_by_source={},
            stop_on_blocked=True,
            stop_on_rate_limited=True,
        ),
        application_channels=ApplicationChannelServiceConfig(enabled=False),
    )


def _application_fixture_source() -> tuple[str, str, dict[str, Path]]:
    for entry in source_catalog_entries():
        suite = source_fixture_suite(entry.source_id)
        success_case = _fixture_case(suite.cases, ParserFixtureKind.SUCCESS_NON_EMPTY)
        pagination_case = _fixture_case(suite.cases, ParserFixtureKind.PAGINATION)
        if success_case is None or pagination_case is None:
            continue

        return (
            entry.source_id,
            _fixture_input_query_variant(success_case),
            {
                _fixture_captured_url(success_case): _fixture_response_path(success_case),
                _fixture_captured_url(pagination_case): _fixture_response_path(pagination_case),
            },
        )
    raise AssertionError("source catalog does not include a source with success and pagination fixtures")


def _fixture_case(
    cases: tuple[ParserFixtureCase, ...],
    kind: ParserFixtureKind,
) -> ParserFixtureCase | None:
    for case in cases:
        if case.kind == kind:
            return case
    return None


def _fixture_input_query_variant(case: ParserFixtureCase) -> str:
    input_path = _fixture_response_path(case).parent / "input.json"
    payload = json.loads(input_path.read_text(encoding="utf-8"))
    query_variants = payload.get("query_variants")
    if not isinstance(query_variants, list) or len(query_variants) != 1 or not isinstance(query_variants[0], str):
        raise ValueError(f"fixture input must declare exactly one query variant: {input_path}")
    return query_variants[0]


def _fixture_captured_url(case: ParserFixtureCase) -> str:
    payload = json.loads((_PLUGIN_ROOT / case.metadata_path).read_text(encoding="utf-8"))
    captured_url = payload.get("captured_url")
    if not isinstance(captured_url, str) or not captured_url:
        raise ValueError(f"fixture metadata must declare captured_url: {case.metadata_path}")
    return captured_url


def _fixture_response_path(case: ParserFixtureCase) -> Path:
    return _PLUGIN_ROOT / case.captured_artifact_path


class V2ApplicationCliTest(unittest.IsolatedAsyncioTestCase):
    async def test_application_runs_search_save_postprocess_and_append(self) -> None:
        # Arrange
        fixture_source_id, query_variant, fixture_mapping = _application_fixture_source()
        with tempfile.TemporaryDirectory() as tmp:
            app = V2SearchApplication(
                config=V2SearchConfig(
                    runs_dir=Path(tmp),
                    source_ids=(fixture_source_id,),
                    service_config=_test_service_config(),
                ),
                registry=build_independent_parser_registry((fixture_source_id,)),
                runtime_factory=DefaultParserRuntimeFactory(
                    transport=FixtureTransport(fixture_mapping),
                    resource_gate=ResourceGate(
                        backend=SqliteResourceGateBackend(Path(tmp) / "_runtime" / "resource-gate.sqlite"),
                        owner_id="test-process",
                    ),
                    policy_for_resource=lambda _: ResourcePolicy(1, 0.0, 30.0),
                    timeout_seconds=15.0,
                    max_response_bytes=20 * 1024 * 1024,
                    host_resolver=lambda _: ("93.184.216.34",),
                ),
            )

            # Act
            first = await app.search(SearchRequest(query_variants=(query_variant,)), run_id="r-test")
            second = await app.search(
                SearchRequest(query_variants=(query_variant,), append_to_run_id="r-test")
            )

            # Assert
            self.assertEqual(0, first.append_sequence)
            self.assertEqual(1, second.append_sequence)
            self.assertEqual(first.paths.run_dir, second.paths.run_dir)
            self.assertTrue(first.paths.database_path.exists())
            self.assertTrue(first.paths.report_html_path.exists())

            self.assertGreater(len(first.final_items), 0)
            self.assertGreater(len(second.final_items), 0)
            self.assertEqual("final", second.processed_payload["phase"])
            self.assertGreater(second.processed_payload["result_count"], 0)
            self.assertIn("filtered_out_results", second.processed_payload)
            self.assertIn("job-harness-payload", first.paths.report_html_path.read_text(encoding="utf-8"))

    async def test_cli_lists_v2_sources_without_touching_v1_cli(self) -> None:
        # Arrange
        stdout = io.StringIO()

        # Act
        with contextlib.redirect_stdout(stdout):
            code = cli_main(["list-sources"])

        # Assert
        payload = json.loads(stdout.getvalue())
        self.assertEqual(0, code)
        self.assertEqual("source_catalog", payload["record_type"])
        self.assertEqual(
            [country.country_code for country in country_catalog_entries()],
            [country["country_code"] for country in payload["countries"]],
        )
        self.assertEqual(
            [entry.source_id for entry in source_catalog_entries()],
            [source["source_id"] for source in payload["sources"]],
        )
        self.assertTrue(all(source["implemented"] for source in payload["sources"]))

    def test_cli_parse_ats_url_prints_agent_readable_json(self) -> None:
        # Arrange
        stdout = io.StringIO()
        calls: list[dict[str, object]] = []

        async def fake_fetch_ats_company_listings(
            url: str,
            *,
            company: str | None = None,
            source_id: str = "adhoc:ats",
            platform: str | None = None,
            source_limit: int = 200,
            query_variant: str = "ats-url",
        ) -> AtsCompanyUrlParseResult:
            calls.append(
                {
                    "url": url,
                    "company": company,
                    "source_id": source_id,
                    "platform": platform,
                    "source_limit": source_limit,
                    "query_variant": query_variant,
                }
            )
            return AtsCompanyUrlParseResult(
                config=AtsCompanySourceConfig(
                    source_id=source_id,
                    company=company or "Airbnb",
                    platform="greenhouse",
                    board_url="https://boards-api.greenhouse.io/v1/boards/airbnb/jobs?content=true",
                    career_url="https://job-boards.greenhouse.io/airbnb",
                ),
                listings=(
                    RawListing(
                        source_listing_id="123",
                        title="QA Engineer",
                        url="https://careers.airbnb.com/positions/123",
                        source=source_id,
                        company=company or "Airbnb",
                    ),
                ),
                pages_visited=1,
                limit_reached=False,
            )

        # Act
        with (
            patch("job_harness.v2.cli.fetch_ats_company_listings", fake_fetch_ats_company_listings),
            contextlib.redirect_stdout(stdout),
        ):
            code = cli_main(
                [
                    "parse-ats-url",
                    "https://job-boards.greenhouse.io/airbnb",
                    "--company",
                    "Airbnb",
                    "--source-id",
                    "adhoc:airbnb",
                    "--source-limit",
                    "25",
                ]
            )

        # Assert
        payload = json.loads(stdout.getvalue())
        self.assertEqual(0, code)
        self.assertEqual(
            [
                {
                    "url": "https://job-boards.greenhouse.io/airbnb",
                    "company": "Airbnb",
                    "source_id": "adhoc:airbnb",
                    "platform": None,
                    "source_limit": 25,
                    "query_variant": "ats-url",
                }
            ],
            calls,
        )
        self.assertEqual("ats_url_parse", payload["record_type"])
        self.assertEqual("greenhouse", payload["platform"])
        self.assertEqual("adhoc:airbnb", payload["source_id"])
        self.assertEqual(1, payload["listing_count"])
        self.assertEqual("QA Engineer", payload["listings"][0]["title"])

    async def test_cli_accepts_pipe_separated_query_variants(self) -> None:
        # Arrange
        args = _build_parser().parse_args(
            [
                "search",
                "--query",
                "QA",
                "--queries",
                "AQA | SDET | Quality Assurance",
            ]
        )

        # Act / Assert
        self.assertEqual(("QA", "AQA", "SDET", "Quality Assurance"), _query_variants(args))

    async def test_cli_search_help_does_not_expose_runtime_controls(self) -> None:
        # Arrange
        stdout = io.StringIO()

        # Act
        with contextlib.redirect_stdout(stdout), self.assertRaises(SystemExit) as raised:
            _build_parser().parse_args(["search", "--help"])

        # Assert
        self.assertEqual(0, raised.exception.code)
        search_help = stdout.getvalue()
        self.assertIn("--queries", search_help)
        self.assertIn("--source", search_help)
        self.assertIn("--work-format", search_help)
        self.assertIn("--remote-scope", search_help)
        self.assertIn("--vacancy-geography", search_help)
        self.assertNotIn("--remote-mode", search_help)
        self.assertNotIn("--hybrid-ok", search_help)
        self.assertNotIn("--office-ok", search_help)
        self.assertNotIn("--work-from", search_help)
        self.assertNotIn("--city", search_help)
        self.assertNotIn("--remote-in-country", search_help)
        self.assertNotIn("--remote-global", search_help)
        self.assertNotIn("--country", search_help)
        self.assertNotIn("--source-attempt-timeout", search_help)
        self.assertNotIn("--run-timeout", search_help)
        self.assertNotIn("--fetch-timeout", search_help)
        self.assertNotIn("--retry-attempts", search_help)

    async def test_cli_rejects_unknown_work_format(self) -> None:
        # Arrange / Act / Assert
        with self.assertRaises(SystemExit):
            _build_parser().parse_args(["search", "--queries", "QA", "--work-format", "any-remote"])

    async def test_cli_rejects_remote_scope_without_remote_work_format(self) -> None:
        # Arrange
        stderr = io.StringIO()

        # Act
        with contextlib.redirect_stderr(stderr):
            code = cli_main(
                [
                    "search",
                    "--queries",
                    "QA",
                    "--work-format",
                    "office",
                    "--remote-scope",
                    "global",
                ]
            )

        # Assert
        self.assertEqual(1, code)
        self.assertIn("remote_scopes", stderr.getvalue())

    async def test_cli_rejects_invalid_geography_token(self) -> None:
        for args in (
            [
                "search",
                "--queries",
                "QA",
                "--work-format",
                "remote",
                "--remote-scope",
                "RU",
            ],
            ["search", "--queries", "QA", "--vacancy-geography", "RU"],
        ):
            with self.subTest(args=args):
                # Arrange
                stderr = io.StringIO()

                # Act
                with contextlib.redirect_stderr(stderr):
                    code = cli_main(args)

                # Assert
                self.assertEqual(1, code)
                self.assertIn("must use", stderr.getvalue())

    async def test_cli_rejects_pure_unknown_workplace_filters(self) -> None:
        for args in (
            ["search", "--queries", "QA", "--work-format", "unknown"],
            ["search", "--queries", "QA", "--work-format", "remote", "--remote-scope", "unknown"],
            ["search", "--queries", "QA", "--vacancy-geography", "unknown"],
        ):
            with self.subTest(args=args):
                # Arrange
                stderr = io.StringIO()

                # Act
                with contextlib.redirect_stderr(stderr):
                    code = cli_main(args)

                # Assert
                self.assertEqual(1, code)
                self.assertIn("only unknown", stderr.getvalue())

    async def test_cli_builds_remote_geography_request_fields(self) -> None:
        # Arrange
        args = _build_parser().parse_args(
            [
                "search",
                "--queries",
                "QA",
                "--work-format",
                "remote",
                "--work-format",
                "hybrid",
                "--work-format",
                "unknown",
                "--remote-scope",
                "global",
                "--remote-scope",
                "unknown",
                "--remote-scope",
                "country:RU",
                "--vacancy-geography",
                "unknown",
                "--vacancy-geography",
                "country:CY",
                "--vacancy-geography",
                "city:Limassol",
            ]
        )

        # Act
        request = _request_from_args(args)

        # Assert
        self.assertEqual((WorkFormat.REMOTE, WorkFormat.HYBRID, WorkFormat.UNKNOWN), request.work_formats)
        self.assertEqual(("global", "unknown", "country:RU"), request.remote_scopes)
        self.assertEqual(("unknown", "country:CY", "city:Limassol"), request.vacancy_geographies)

    async def test_cli_rejects_empty_pipe_separated_query_variant(self) -> None:
        # Arrange
        args = _build_parser().parse_args(["search", "--queries", "QA || SDET"])

        # Act / Assert
        with self.assertRaisesRegex(ValueError, "--queries"):
            _query_variants(args)


if __name__ == "__main__":
    unittest.main()
