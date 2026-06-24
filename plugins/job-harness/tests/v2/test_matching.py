from __future__ import annotations

import unittest

from job_harness.v2.matching import FuzzyBounds, fuzzy_tokens_match, normalized_tokens


class FuzzyMatchingTest(unittest.TestCase):
    def test_normalizes_case_and_yo(self) -> None:
        self.assertEqual(("ведущий", "qa"), normalized_tokens("Ведущий QA"))
        self.assertEqual(("тест",), normalized_tokens("Тёст"))

    def test_matches_short_query_token_inside_compound_title(self) -> None:
        self.assertTrue(fuzzy_tokens_match("QA", "AQA"))
        self.assertTrue(fuzzy_tokens_match("QA", "QA-инженер"))
        self.assertFalse(fuzzy_tokens_match("QA", "Account Manager"))

    def test_matches_inflected_russian_role_tokens(self) -> None:
        self.assertTrue(fuzzy_tokens_match("тестировщик", "Инженер по тестированию"))

    def test_rejects_scores_below_configured_bounds(self) -> None:
        bounds = FuzzyBounds(token_score=0.9, short_token_score=0.9)
        self.assertFalse(fuzzy_tokens_match("тестировщик", "Инженер по тестированию", bounds=bounds))


if __name__ == "__main__":
    unittest.main()
