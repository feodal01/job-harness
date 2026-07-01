from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from job_harness.v2.application import V2SearchApplication, V2SearchConfig
from job_harness.v2.cli import _build_parser, _query_variants, _request_from_args, main as cli_main
from job_harness.v2.contracts import (
    ParserFixtureCase,
    ParserFixtureKind,
    RemoteMode,
    SearchRequest,
    SourceFetchRequest,
    SourceResponseArtifact,
)
from job_harness.v2.persistence import SqliteRunStore
from job_harness.v2.runtime import (
    ApplicationChannelServiceConfig,
    DetailServiceConfig,
    RetryServiceConfig,
    SearchServiceConfig,
)
from job_harness.v2.source_catalog import country_catalog_entries, source_catalog_entries, source_fixture_suite

_PLUGIN_ROOT_PARENT_INDEX = 2
_PLUGIN_ROOT = Path(__file__).resolve().parents[_PLUGIN_ROOT_PARENT_INDEX]


class FixtureFetcher:
    def __init__(self, mapping: dict[str, Path]) -> None:
        self._mapping = mapping

    async def fetch(self, request: SourceFetchRequest) -> SourceResponseArtifact:
        path = self._mapping[request.url]
        return SourceResponseArtifact(
            source_id=request.source_id,
            url=request.url,
            media_type="application/json" if path.suffix == ".json" else "text/html",
            body=path.read_text(encoding="utf-8"),
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
                fetcher=FixtureFetcher(fixture_mapping),
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

            with SqliteRunStore(first.paths.database_path, run_id="r-test") as store:
                raw_records = store.read_raw_records()
                manifest = store.read_run_manifest()
                processed = store.read_processed_results()
            self.assertGreater(first.raw_records_written, 0)
            self.assertEqual(first.raw_records_written + second.raw_records_written, len(raw_records))
            self.assertEqual(1, manifest["latest_append_sequence"])
            self.assertIn("detail_enrichment", manifest)
            self.assertEqual(len(raw_records), processed["raw_records_read"])
            self.assertEqual("final", processed["phase"])
            self.assertGreater(processed["result_count"], 1)
            self.assertLessEqual(processed["result_count"], len(raw_records))
            self.assertNotIn("truncated", processed)
            self.assertIn("filtered_out_results", processed)
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
        self.assertIn("--remote-mode", search_help)
        self.assertIn("--hybrid-ok", search_help)
        self.assertIn("--office-ok", search_help)
        self.assertIn("--work-from", search_help)
        self.assertIn("--vacancy-geography", search_help)
        self.assertNotIn("--remote-in-country", search_help)
        self.assertNotIn("--remote-global", search_help)
        self.assertNotIn("--country", search_help)
        self.assertNotIn("--source-attempt-timeout", search_help)
        self.assertNotIn("--run-timeout", search_help)
        self.assertNotIn("--fetch-timeout", search_help)
        self.assertNotIn("--retry-attempts", search_help)

    async def test_cli_rejects_any_remote_mode(self) -> None:
        # Arrange / Act / Assert
        with self.assertRaises(SystemExit):
            _build_parser().parse_args(["search", "--queries", "QA", "--remote-mode", "any-remote"])

    async def test_cli_rejects_invalid_remote_geography_combination(self) -> None:
        # Arrange
        stderr = io.StringIO()

        # Act
        with contextlib.redirect_stderr(stderr):
            code = cli_main(
                [
                    "search",
                    "--queries",
                    "QA",
                    "--remote-mode",
                    "global-remote-only",
                    "--work-from",
                    "RU",
                ]
            )

        # Assert
        self.assertEqual(1, code)
        self.assertIn("work_from_geographies", stderr.getvalue())

    async def test_cli_rejects_global_remote_with_physical_format_flags(self) -> None:
        for flag in ("--hybrid-ok", "--office-ok"):
            with self.subTest(flag=flag):
                # Arrange
                stderr = io.StringIO()

                # Act
                with contextlib.redirect_stderr(stderr):
                    code = cli_main(
                        [
                            "search",
                            "--queries",
                            "QA",
                            "--remote-mode",
                            "global-remote-only",
                            flag,
                        ]
                    )

                # Assert
                self.assertEqual(1, code)
                self.assertIn("global_remote_only", stderr.getvalue())

    async def test_cli_rejects_invalid_geography_token(self) -> None:
        for args in (
            [
                "search",
                "--queries",
                "QA",
                "--remote-mode",
                "compatible-remote",
                "--work-from",
                "global",
            ],
            ["search", "--queries", "QA", "--vacancy-geography", "moon"],
        ):
            with self.subTest(args=args):
                # Arrange
                stderr = io.StringIO()

                # Act
                with contextlib.redirect_stderr(stderr):
                    code = cli_main(args)

                # Assert
                self.assertEqual(1, code)
                self.assertIn("unsupported geography", stderr.getvalue())

    async def test_cli_builds_remote_geography_request_fields(self) -> None:
        # Arrange
        args = _build_parser().parse_args(
            [
                "search",
                "--queries",
                "QA",
                "--remote-mode",
                "compatible-remote",
                "--hybrid-ok",
                "--office-ok",
                "--work-from",
                "europe",
                "--vacancy-geography",
                "CY",
            ]
        )

        # Act
        request = _request_from_args(args)

        # Assert
        self.assertEqual(RemoteMode.COMPATIBLE_REMOTE, request.remote_mode)
        self.assertTrue(request.hybrid_ok)
        self.assertTrue(request.office_ok)
        self.assertEqual(("EU",), request.work_from_geographies)
        self.assertEqual(("CY",), request.vacancy_geographies)

    async def test_cli_rejects_empty_pipe_separated_query_variant(self) -> None:
        # Arrange
        args = _build_parser().parse_args(["search", "--queries", "QA || SDET"])

        # Act / Assert
        with self.assertRaisesRegex(ValueError, "--queries"):
            _query_variants(args)


if __name__ == "__main__":
    unittest.main()
