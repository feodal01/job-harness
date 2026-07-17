from __future__ import annotations

import unittest

from job_harness.v2.postprocessing import CriteriaProcessingPlanner


class CriteriaProcessingPlannerTest(unittest.TestCase):
    def test_builds_actions_from_source_attempt_diagnostics(self) -> None:
        # Arrange
        source_attempts = (
            _attempt(
                requested=("query", "published_since", "work_formats"),
                native=("query",),
                structured=("published_since",),
                unsupported=("work_formats",),
                postprocess=("published_since", "work_formats"),
            ),
        )
        rows = (_row(raw_text="Remote worldwide policy mentioned in the vacancy"),)

        # Act
        plan = CriteriaProcessingPlanner().build_plan(
            source_attempts=source_attempts,
            rows=rows,
        )

        # Assert
        actions = _actions_by_criterion(plan[0])
        self.assertEqual("none_native_request", actions["query"]["action"])
        self.assertEqual("structured_postprocess", actions["published_since"]["action"])
        self.assertEqual("text_enrichment_required", actions["work_formats"]["action"])
        self.assertTrue(actions["work_formats"]["requires_enrichment"])

    def test_marks_missing_text_when_unsupported_criterion_cannot_be_enriched(self) -> None:
        # Arrange
        source_attempts = (
            _attempt(
                requested=("query", "relocation"),
                native=("query",),
                structured=(),
                unsupported=("relocation",),
                postprocess=("relocation",),
            ),
        )
        rows = (_row(raw_text=""),)

        # Act
        plan = CriteriaProcessingPlanner().build_plan(
            source_attempts=source_attempts,
            rows=rows,
        )

        # Assert
        actions = _actions_by_criterion(plan[0])
        self.assertEqual("missing_text_for_enrichment", actions["relocation"]["action"])
        self.assertFalse(actions["relocation"]["requires_enrichment"])

    def test_marks_salary_text_enrichment_required_when_text_exists(self) -> None:
        # Arrange
        source_attempts = (
            _attempt(
                requested=("query", "compensation"),
                native=("query",),
                structured=(),
                unsupported=("compensation",),
                postprocess=("compensation",),
            ),
        )
        rows = (_row(raw_text="Competitive salary discussed with successful candidates"),)

        # Act
        plan = CriteriaProcessingPlanner().build_plan(
            source_attempts=source_attempts,
            rows=rows,
        )

        # Assert
        actions = _actions_by_criterion(plan[0])
        self.assertEqual("text_enrichment_required", actions["compensation"]["action"])
        self.assertTrue(actions["compensation"]["requires_enrichment"])

    def test_requires_structured_evidence_for_non_text_enrichable_criterion(self) -> None:
        # Arrange
        source_attempts = (
            _attempt(
                requested=("query", "published_since"),
                native=("query",),
                structured=(),
                unsupported=("published_since",),
                postprocess=("published_since",),
            ),
        )
        rows = (_row(raw_text="This vacancy was recently opened"),)

        # Act
        plan = CriteriaProcessingPlanner().build_plan(
            source_attempts=source_attempts,
            rows=rows,
        )

        # Assert
        actions = _actions_by_criterion(plan[0])
        self.assertEqual(
            "unsupported_requires_structured_evidence",
            actions["published_since"]["action"],
        )
        self.assertFalse(actions["published_since"]["requires_enrichment"])


def _attempt(
    *,
    requested: tuple[str, ...],
    native: tuple[str, ...],
    structured: tuple[str, ...],
    unsupported: tuple[str, ...],
    postprocess: tuple[str, ...],
) -> dict[str, object]:
    return {
        "source": "habr_career",
        "query_variant": "QA",
        "outcome": "success",
        "criteria": {
            "requested": list(requested),
            "native_applied": list(native),
            "structured_evidence_available": list(structured),
            "unsupported": list(unsupported),
            "postprocess": list(postprocess),
        },
    }


def _row(*, raw_text: str) -> dict[str, object]:
    return {
        "source": "habr_career",
        "query_variant": "QA",
        "title": "",
        "description": "",
        "requirements": "",
        "skills": (),
        "raw_text": raw_text,
    }


def _actions_by_criterion(plan: dict[str, object]) -> dict[str, dict[str, object]]:
    actions = plan["actions"]
    if not isinstance(actions, tuple):
        raise TypeError("plan actions must be a tuple")
    result: dict[str, dict[str, object]] = {}
    for action in actions:
        if not isinstance(action, dict):
            raise TypeError("plan action must be a dict")
        criterion = action["criterion"]
        if not isinstance(criterion, str):
            raise TypeError("plan action criterion must be a string")
        result[criterion] = action
    return result


if __name__ == "__main__":
    unittest.main()
