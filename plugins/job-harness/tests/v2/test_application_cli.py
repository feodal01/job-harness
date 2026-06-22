from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from job_harness.v2.application import V2SearchApplication, V2SearchConfig
from job_harness.v2.cli import main as cli_main
from job_harness.v2.contracts import SearchRequest, SourceFetchRequest, SourceResponseArtifact
from job_harness.v2.runtime import RetryPolicy

_FIXTURES = Path(__file__).parent / "fixtures" / "scrapers"
_HABR_QA_URL = "https://career.habr.com/vacancies?q=QA&type=all"
_HABR_QA_PAGE_2_URL = "https://career.habr.com/vacancies?q=QA&type=all&page=2"


class FixtureFetcher:
    def __init__(self, mapping: dict[str, Path]) -> None:
        self._mapping = mapping

    async def fetch(self, request: SourceFetchRequest) -> SourceResponseArtifact:
        path = self._mapping[request.url]
        return SourceResponseArtifact(
            source_id=request.source_id,
            url=request.url,
            media_type="text/html",
            body=path.read_text(encoding="utf-8"),
        )


class V2ApplicationCliTest(unittest.IsolatedAsyncioTestCase):
    async def test_application_runs_search_save_postprocess_and_append(self) -> None:
        # Arrange
        with tempfile.TemporaryDirectory() as tmp:
            app = V2SearchApplication(
                config=V2SearchConfig(
                    runs_dir=Path(tmp),
                    source_ids=("habr_career",),
                    retry_policy=RetryPolicy(max_attempts=1),
                ),
                fetcher=FixtureFetcher(
                    {
                        _HABR_QA_URL: _FIXTURES / "habr_career" / "success" / "response.html",
                        _HABR_QA_PAGE_2_URL: _FIXTURES / "habr_career" / "pagination" / "response.html",
                    }
                ),
            )

            # Act
            first = await app.search(SearchRequest(query_variants=("QA",), max_results=3), run_id="r-test")
            second = await app.search(
                SearchRequest(query_variants=("QA",), max_results=3, append_to_run_id="r-test")
            )

            # Assert
            self.assertEqual(0, first.append_sequence)
            self.assertEqual(1, second.append_sequence)
            self.assertEqual(first.paths.run_dir, second.paths.run_dir)
            self.assertTrue(first.paths.raw_listings_path.exists())
            self.assertTrue(first.paths.source_attempts_path.exists())
            self.assertTrue(first.paths.run_manifest_path.exists())
            self.assertTrue(first.paths.processed_results_path.exists())

            raw_lines = first.paths.raw_listings_path.read_text(encoding="utf-8").splitlines()
            manifest = json.loads(first.paths.run_manifest_path.read_text(encoding="utf-8"))
            processed = json.loads(first.paths.processed_results_path.read_text(encoding="utf-8"))
            self.assertEqual(100, len(raw_lines))
            self.assertEqual(1, manifest["latest_append_sequence"])
            self.assertEqual(3, processed["result_count"])

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
        self.assertEqual(["RU"], [country["country_code"] for country in payload["countries"]])
        self.assertEqual(
            [
                "habr_career",
                "hh_ru",
                "talanto",
                "career:vk",
                "career:jetbrains",
                "geekjob",
                "talento",
                "finder_work",
                "getmatch",
                "it_jobs_uz",
                "hirify",
                "jobturbo",
            ],
            [source["source_id"] for source in payload["sources"]],
        )


if __name__ == "__main__":
    unittest.main()
