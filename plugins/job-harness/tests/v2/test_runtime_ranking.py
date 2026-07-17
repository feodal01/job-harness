from __future__ import annotations

import unittest

from job_harness.v2.contracts import Grade, SearchRequest
from job_harness.v2.runtime.ranking import GraphVacancyRanker


class GraphVacancyRankerTest(unittest.TestCase):
    def test_exact_llm_eval_and_grade_evidence_rank_above_generic_ai_quality(self) -> None:
        ranker = GraphVacancyRanker(
            SearchRequest(
                query_variants=("LLM evaluation", "AI quality"),
                grades=(Grade.SENIOR, Grade.LEAD),
            )
        )
        exact = ranker.score(
            {
                "title": "Senior LLM Evaluation Lead",
                "description": "Own model evaluation systems and LLM-as-a-judge pipelines.",
                "derived_facts": {
                    "structured-selection-facts": {
                        "grade": {"resolved": ["senior"], "conflict": False}
                    }
                },
            }
        )
        generic = ranker.score(
            {
                "title": "AI Quality Control Assistant",
                "description": "Review routine operations data.",
                "derived_facts": {
                    "structured-selection-facts": {
                        "grade": {"resolved": [], "conflict": False}
                    }
                },
            }
        )

        self.assertGreater(exact, generic)
        self.assertGreater(exact, 0)
        self.assertGreaterEqual(generic, 0)

    def test_role_mismatch_cannot_be_promoted_by_description_tokens(self) -> None:
        ranker = GraphVacancyRanker(SearchRequest(query_variants=("Java Engineer",)))

        mismatch = ranker.score(
            {
                "title": "QA Automation Engineer (Java)",
                "description": "Java engineer Java engineer",
                "derived_facts": {
                    "structured-selection-facts": {
                        "grade": {"resolved": [], "conflict": False}
                    }
                },
            }
        )
        match = ranker.score(
            {
                "title": "Senior Java Platform Engineer",
                "derived_facts": {
                    "structured-selection-facts": {
                        "grade": {"resolved": ["senior"], "conflict": False}
                    }
                },
            }
        )

        self.assertEqual(0.0, mismatch)
        self.assertGreater(match, mismatch)


if __name__ == "__main__":
    unittest.main()
