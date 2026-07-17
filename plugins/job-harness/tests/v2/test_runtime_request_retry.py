from __future__ import annotations

import unittest

from job_harness.v2.ports import RetrySafety
from job_harness.v2.runtime.request_retry import (
    RequestFailureKind,
    RequestRetryDisposition,
    RequestRetryPolicy,
)


class RequestRetryPolicyTest(unittest.TestCase):
    def setUp(self) -> None:
        self.policy = RequestRetryPolicy(
            max_attempts=3,
            attempt_timeout_seconds=15.0,
            base_delay_seconds=1.0,
            max_delay_seconds=8.0,
            request_budget_seconds=55.0,
            random_fraction=lambda: 0.5,
        )

    def test_timeout_schedules_full_jitter_delay_for_safe_page(self) -> None:
        decision = self.policy.decide(
            retry_safety=RetrySafety.SAFE,
            failure_kind=RequestFailureKind.TIMEOUT,
            attempt_number=1,
            elapsed_seconds=15.0,
        )

        self.assertEqual(decision.disposition, RequestRetryDisposition.SCHEDULE)
        self.assertEqual(decision.delay_seconds, 0.5)

    def test_exponential_delay_is_capped_before_full_jitter(self) -> None:
        policy = RequestRetryPolicy(
            max_attempts=6,
            attempt_timeout_seconds=1.0,
            base_delay_seconds=4.0,
            max_delay_seconds=8.0,
            request_budget_seconds=100.0,
            random_fraction=lambda: 0.75,
        )

        decision = policy.decide(
            retry_safety=RetrySafety.SAFE,
            failure_kind=RequestFailureKind.NETWORK,
            attempt_number=4,
            elapsed_seconds=10.0,
        )

        self.assertEqual(decision.disposition, RequestRetryDisposition.SCHEDULE)
        self.assertEqual(decision.delay_seconds, 6.0)

    def test_last_attempt_exhausts_retryable_failure(self) -> None:
        decision = self.policy.decide(
            retry_safety=RetrySafety.SAFE,
            failure_kind=RequestFailureKind.TIMEOUT,
            attempt_number=3,
            elapsed_seconds=45.0,
        )

        self.assertEqual(decision.disposition, RequestRetryDisposition.EXHAUSTED)
        self.assertEqual(decision.delay_seconds, 0.0)

    def test_non_retryable_http_status_is_terminal(self) -> None:
        decision = self.policy.decide(
            retry_safety=RetrySafety.SAFE,
            failure_kind=RequestFailureKind.HTTP_STATUS,
            status_code=404,
            attempt_number=1,
            elapsed_seconds=0.1,
        )

        self.assertEqual(decision.disposition, RequestRetryDisposition.TERMINAL)

    def test_retryable_http_statuses_are_explicit(self) -> None:
        for status_code in (408, 425, 429, 500, 502, 503, 504):
            with self.subTest(status_code=status_code):
                decision = self.policy.decide(
                    retry_safety=RetrySafety.SAFE,
                    failure_kind=RequestFailureKind.HTTP_STATUS,
                    status_code=status_code,
                    attempt_number=1,
                    elapsed_seconds=0.1,
                )

                self.assertEqual(decision.disposition, RequestRetryDisposition.SCHEDULE)

    def test_unsafe_action_is_terminal_even_for_timeout(self) -> None:
        decision = self.policy.decide(
            retry_safety=RetrySafety.NEVER,
            failure_kind=RequestFailureKind.TIMEOUT,
            attempt_number=1,
            elapsed_seconds=15.0,
        )

        self.assertEqual(decision.disposition, RequestRetryDisposition.TERMINAL)

    def test_retry_after_is_the_minimum_delay(self) -> None:
        decision = self.policy.decide(
            retry_safety=RetrySafety.SAFE,
            failure_kind=RequestFailureKind.HTTP_STATUS,
            status_code=429,
            retry_after_seconds=7.0,
            attempt_number=1,
            elapsed_seconds=1.0,
        )

        self.assertEqual(decision.disposition, RequestRetryDisposition.SCHEDULE)
        self.assertEqual(decision.delay_seconds, 7.0)

    def test_retry_after_beyond_remaining_page_budget_is_exhausted(self) -> None:
        decision = self.policy.decide(
            retry_safety=RetrySafety.SAFE,
            failure_kind=RequestFailureKind.HTTP_STATUS,
            status_code=429,
            retry_after_seconds=30.0,
            attempt_number=1,
            elapsed_seconds=20.0,
        )

        self.assertEqual(decision.disposition, RequestRetryDisposition.EXHAUSTED)

    def test_random_fraction_must_be_inside_unit_interval(self) -> None:
        policy = RequestRetryPolicy(
            max_attempts=3,
            attempt_timeout_seconds=15.0,
            base_delay_seconds=1.0,
            max_delay_seconds=8.0,
            request_budget_seconds=55.0,
            random_fraction=lambda: 1.1,
        )

        with self.assertRaisesRegex(ValueError, "random_fraction"):
            policy.decide(
                retry_safety=RetrySafety.SAFE,
                failure_kind=RequestFailureKind.TIMEOUT,
                attempt_number=1,
                elapsed_seconds=15.0,
            )


if __name__ == "__main__":
    unittest.main()
