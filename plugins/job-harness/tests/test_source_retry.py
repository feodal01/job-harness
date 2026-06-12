"""Tests for source_retry classification and validation."""

from __future__ import annotations

import unittest

from job_harness.run_journal import JournalSnapshot
from job_harness.source_retry import (
    RetryAction,
    build_retryable_sources,
    build_sources_by_state,
    classify_source_for_retry,
    parse_sources_csv,
    validate_retry_sources,
)
from job_harness.types import (
    FailureMode,
    RunState,
    SourceGroup,
    SourceState,
    SourceStatus,
)


def _snap(**sources: SourceStatus) -> JournalSnapshot:
    return JournalSnapshot(
        run_id="r-test",
        state=RunState.COMPLETED,
        started_at="2026-06-06T00:00:00Z",
        ended_at="2026-06-06T00:01:00Z",
        elapsed_ms=60_000,
        request={"query": "QA", "dedupe": True},
        sources=sources,
        listings=[],
        listings_count=0,
        errors=[],
    )


def _status(source: str, state: SourceState, *, failure_mode: FailureMode | None = None):
    if state == SourceState.OK:
        failure_mode = None
    elif failure_mode is None:
        failure_mode = FailureMode.PARSE_ERROR
    return SourceStatus(
        source=source,
        group=SourceGroup.AGGREGATOR,
        state=state,
        failure_mode=failure_mode,
        source_limit=50,
        deadline_ms=30_000,
        elapsed_ms=10,
    )


class ClassifySourceTest(unittest.TestCase):
    def test_ok_is_skip_ok(self):
        self.assertEqual(
            classify_source_for_retry(_status("hh_ru", SourceState.OK)),
            RetryAction.SKIP_OK,
        )

    def test_timeout_is_retry(self):
        self.assertEqual(
            classify_source_for_retry(
                _status("bad_src", SourceState.TIMEOUT, failure_mode=FailureMode.HTTP_TIMEOUT)
            ),
            RetryAction.RETRY,
        )

    def test_policy_skip_states(self):
        self.assertEqual(
            classify_source_for_retry(
                _status("x", SourceState.SKIPPED, failure_mode=FailureMode.NOT_IN_PROFILE)
            ),
            RetryAction.SKIP_POLICY,
        )
        self.assertEqual(
            classify_source_for_retry(
                _status(
                    "x",
                    SourceState.SKIPPED_UNSUPPORTED_FLAG,
                    failure_mode=FailureMode.UNSUPPORTED_FLAG,
                )
            ),
            RetryAction.SKIP_POLICY,
        )


class BuildRetryableSourcesTest(unittest.TestCase):
    def test_collects_retryable_only(self):
        snap = _snap(
            ok_src=_status("ok_src", SourceState.OK),
            bad_src=_status("bad_src", SourceState.ERROR),
            skip_src=_status(
                "skip_src", SourceState.SKIPPED, failure_mode=FailureMode.NOT_IN_PROFILE
            ),
        )
        self.assertEqual(build_retryable_sources(snap), ["bad_src"])
        grouped = build_sources_by_state(snap)
        self.assertEqual(grouped["ok"], ["ok_src"])
        self.assertEqual(grouped["error"], ["bad_src"])


class ValidateRetrySourcesTest(unittest.TestCase):
    def test_unknown_source_rejected(self):
        snap = _snap(
            ok=_status("ok_src", SourceState.OK),
            bad=_status("bad_src", SourceState.TIMEOUT, failure_mode=FailureMode.HTTP_TIMEOUT),
        )
        result = validate_retry_sources(
            ["bad_src", "made_up"],
            snap,
            {"ok_src", "bad_src"},
        )
        self.assertTrue(result.has_invalid_sources)
        self.assertEqual(result.unknown_sources, ("made_up",))

    def test_not_in_run_source_rejected(self):
        snap = _snap(
            ok_src=_status("ok_src", SourceState.OK),
            bad_src=_status("bad_src", SourceState.ERROR),
        )
        result = validate_retry_sources(
            ["geekjob"],
            snap,
            {"ok_src", "bad_src", "geekjob"},
        )
        self.assertEqual(result.not_in_run_sources, ("geekjob",))

    def test_ok_source_skipped_retryable_selected(self):
        snap = _snap(
            ok_src=_status("ok_src", SourceState.OK),
            bad_src=_status(
                "bad_src", SourceState.TIMEOUT, failure_mode=FailureMode.HTTP_TIMEOUT
            ),
        )
        result = validate_retry_sources(
            ["ok_src", "bad_src"],
            snap,
            {"ok_src", "bad_src"},
        )
        self.assertTrue(result.can_start)
        self.assertEqual(result.retried_sources, ("bad_src",))
        self.assertEqual(result.skipped_sources["ok_src"]["reason"], "already_ok")


class ParseSourcesCsvTest(unittest.TestCase):
    def test_splits_and_strips(self):
        self.assertEqual(
            parse_sources_csv(" hh_ru , career:vk "),
            ["hh_ru", "career:vk"],
        )


if __name__ == "__main__":
    unittest.main()
