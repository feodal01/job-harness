from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from job_harness.v2.application import V2SearchApplication, V2SearchConfig
from job_harness.v2.cli import main as cli_main
from job_harness.v2.contracts import (
    ParserFixtureCase,
    ParserFixtureKind,
    SearchRequest,
    SourceFetchRequest,
    SourceResponseArtifact,
)
from job_harness.v2.runtime import RetryPolicy
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
                    retry_policy=RetryPolicy(max_attempts=1),
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
            self.assertTrue(first.paths.raw_listings_path.exists())
            self.assertTrue(first.paths.source_attempts_path.exists())
            self.assertTrue(first.paths.run_manifest_path.exists())
            self.assertTrue(first.paths.processed_results_path.exists())
            self.assertTrue(first.paths.report_html_path.exists())

            raw_lines = first.paths.raw_listings_path.read_text(encoding="utf-8").splitlines()
            manifest = json.loads(first.paths.run_manifest_path.read_text(encoding="utf-8"))
            processed = json.loads(first.paths.processed_results_path.read_text(encoding="utf-8"))
            self.assertGreater(first.raw_records_written, 0)
            self.assertEqual(first.raw_records_written + second.raw_records_written, len(raw_lines))
            self.assertEqual(1, manifest["latest_append_sequence"])
            self.assertEqual(len(raw_lines), processed["raw_records_read"])
            self.assertGreater(processed["result_count"], 1)
            self.assertLessEqual(processed["result_count"], len(raw_lines))
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


if __name__ == "__main__":
    unittest.main()
