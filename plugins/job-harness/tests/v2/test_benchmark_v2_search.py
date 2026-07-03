from __future__ import annotations

import runpy
import unittest
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[4]
_BENCHMARK = runpy.run_path(str(_REPO_ROOT / "scripts" / "benchmark_v2_search.py"))


def _call(name: str, *args: Any, **kwargs: Any) -> Any:
    value = _BENCHMARK[name]
    if not callable(value):
        raise AssertionError(f"{name} is not callable")
    return value(*args, **kwargs)


def _result(attempts: list[dict[str, Any]], *, presentation_rows: int = 2) -> dict[str, Any]:
    stdout_json = {"attempts": attempts}
    return {
        "stdout_json": stdout_json,
        "attempt_summary": _call("_attempt_summary", stdout_json),
        "processed_summary": {
            "result_count": presentation_rows,
            "filtered_out_results": 0,
            "detail_summary": {},
        },
        "wall_seconds": 10.0,
        "returncode": 0,
    }


class BenchmarkV2SearchTest(unittest.TestCase):
    def test_source_scoped_shape_ignores_unrelated_source_volatility(self) -> None:
        baseline = _result(
            [
                _attempt(source="target", elapsed_ms=500, outcome="success", raw_listings_written=10),
                _attempt(source="noisy", elapsed_ms=100, outcome="success", raw_listings_written=100),
            ]
        )
        candidate = _result(
            [
                _attempt(source="target", elapsed_ms=450, outcome="success", raw_listings_written=10),
                _attempt(source="noisy", elapsed_ms=5000, outcome="http_server_error", raw_listings_written=0),
            ],
            presentation_rows=0,
        )

        scoped_errors = _call(
            "_shape_errors",
            baseline=baseline,
            candidate=candidate,
            policy="at_least_baseline",
            source_ids=("target",),
        )
        global_errors = _call(
            "_shape_errors",
            baseline=baseline,
            candidate=candidate,
            policy="at_least_baseline",
            source_ids=None,
        )

        self.assertEqual((), scoped_errors)
        self.assertTrue(any("all sources unhealthy outcomes increased" in error for error in global_errors))

    def test_source_scoped_elapsed_limit_ignores_unrelated_slowest_source(self) -> None:
        candidate = _result(
            [
                _attempt(source="target", elapsed_ms=450, outcome="success", raw_listings_written=10),
                _attempt(source="noisy", elapsed_ms=5000, outcome="success", raw_listings_written=100),
            ]
        )

        scoped_errors = _call(
            "_limit_errors",
            profile={"max_source_elapsed_ms": 1000, "shape_sources": ["target"]},
            candidate=candidate,
        )
        global_errors = _call(
            "_limit_errors",
            profile={"max_source_elapsed_ms": 1000},
            candidate=candidate,
        )

        self.assertEqual((), scoped_errors)
        self.assertEqual(("all sources source_elapsed_ms_max exceeded 1000",), global_errors)

    def test_at_least_baseline_allows_filtered_out_growth_without_lost_rows(self) -> None:
        baseline = _result([_attempt(source="target", elapsed_ms=500, outcome="success", raw_listings_written=10)])
        candidate = _result([_attempt(source="target", elapsed_ms=450, outcome="success", raw_listings_written=10)])
        baseline["processed_summary"] = {
            "result_count": 51,
            "filtered_out_results": 221,
            "detail_summary": {},
        }
        candidate["processed_summary"] = {
            "result_count": 51,
            "filtered_out_results": 227,
            "detail_summary": {},
        }

        errors = _call(
            "_shape_errors",
            baseline=baseline,
            candidate=candidate,
            policy="at_least_baseline",
            source_ids=None,
        )

        self.assertEqual((), errors)

    def test_at_least_baseline_rejects_detail_enrichment_regression(self) -> None:
        baseline = _result([_attempt(source="hirify", elapsed_ms=500, outcome="success", raw_listings_written=10)])
        candidate = _result([_attempt(source="hirify", elapsed_ms=450, outcome="success", raw_listings_written=10)])
        baseline["processed_summary"]["detail_summary"] = {
            "total_detail_work_items": 50,
            "attempted": 50,
            "enriched": 50,
            "failed": 0,
            "stopped_sources": [],
        }
        candidate["processed_summary"]["detail_summary"] = {
            "total_detail_work_items": 50,
            "attempted": 14,
            "enriched": 10,
            "failed": 4,
            "stopped_sources": ["hirify"],
        }

        errors = _call(
            "_shape_errors",
            baseline=baseline,
            candidate=candidate,
            policy="at_least_baseline",
            source_ids=("hirify",),
        )

        self.assertTrue(any("detail_summary.enriched decreased" in error for error in errors))
        self.assertTrue(any("detail_summary.failed increased" in error for error in errors))
        self.assertTrue(any("detail_summary.stopped_sources increased" in error for error in errors))

    def test_presentation_at_least_allows_raw_dedupe_but_rejects_visible_loss(self) -> None:
        baseline = _result(
            [
                _attempt(source="career:jetbrains", elapsed_ms=500, outcome="success", raw_listings_written=101),
                _attempt(source="career:jetbrains", elapsed_ms=500, outcome="success", raw_listings_written=101),
            ],
            presentation_rows=50,
        )
        candidate = _result(
            [_attempt(source="career:jetbrains", elapsed_ms=450, outcome="success", raw_listings_written=101)],
            presentation_rows=50,
        )
        visible_loss = _result(
            [_attempt(source="career:jetbrains", elapsed_ms=450, outcome="success", raw_listings_written=101)],
            presentation_rows=49,
        )

        errors = _call(
            "_shape_errors",
            baseline=baseline,
            candidate=candidate,
            policy="presentation_at_least",
            source_ids=None,
        )
        loss_errors = _call(
            "_shape_errors",
            baseline=baseline,
            candidate=visible_loss,
            policy="presentation_at_least",
            source_ids=None,
        )

        self.assertEqual((), errors)
        self.assertTrue(any("processed presentation rows decreased" in error for error in loss_errors))

    def test_shape_sources_rejects_duplicate_profile_entries(self) -> None:
        with self.assertRaisesRegex(ValueError, "duplicates"):
            _call("_shape_sources", {"shape_sources": ["target", "target"]})


def _attempt(
    *,
    source: str,
    elapsed_ms: int,
    outcome: str,
    raw_listings_written: int,
) -> dict[str, Any]:
    return {
        "source": source,
        "elapsed_ms": elapsed_ms,
        "outcome": outcome,
        "raw_listings_written": raw_listings_written,
    }
