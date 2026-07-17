from __future__ import annotations

import unittest

from job_harness.v2.matching import RoleMatcher


class RoleMatcherTest(unittest.TestCase):
    def test_matches_ordered_role_tokens_with_bounded_gaps(self) -> None:
        matcher = RoleMatcher(("Data Analyst",))

        match = matcher.match("Senior BI / Data Platform Analyst")

        self.assertTrue(match.matched)
        self.assertEqual("Data Analyst", match.query_variant)
        self.assertEqual((2, 4), match.matched_positions)

    def test_rejects_reversed_role_tokens(self) -> None:
        matcher = RoleMatcher(("Java Engineer",))

        match = matcher.match("QA Automation Engineer (Java)")

        self.assertFalse(match.matched)
        self.assertEqual(0.0, match.strength)

    def test_rejects_more_than_three_intervening_title_tokens(self) -> None:
        matcher = RoleMatcher(("Data Analyst",))

        self.assertFalse(
            matcher.match("Data and BI platform reporting principal Analyst").matched
        )

    def test_applies_versioned_phrase_aliases_before_matching(self) -> None:
        matcher = RoleMatcher(("QA Engineer",))

        match = matcher.match("Quality Assurance Engineer")

        self.assertTrue(match.matched)
        self.assertEqual("1", match.alias_version)


if __name__ == "__main__":
    unittest.main()
