from __future__ import annotations

import unittest
from datetime import date

from job_harness.v2.contracts import RemoteMode
from job_harness.v2.postprocessing import VacancyFilterCriteria, VacancyFilterFacts, decide_vacancy_filter


class VacancyFilterPolicyTest(unittest.TestCase):
    def test_title_mismatch_is_removed_without_second_chance(self) -> None:
        decision = decide_vacancy_filter(
            criteria=VacancyFilterCriteria(queries=("QA",)),
            vacancy=VacancyFilterFacts(
                title="Ֆրանչայզինգային սրճարանների որակի վերահսկման մասնագետ",
                company="Coffee House Company",
            ),
        )

        self.assertFalse(decision.keep)
        self.assertFalse(decision.title_matches)
        self.assertFalse(decision.include_in_filtered_out)
        self.assertEqual(("query_mismatch",), decision.reasons)

    def test_country_only_listing_does_not_satisfy_compatible_remote(self) -> None:
        decision = decide_vacancy_filter(
            criteria=VacancyFilterCriteria(
                queries=("Quality Assurance",),
                remote_mode=RemoteMode.COMPATIBLE_REMOTE,
                work_from_geographies=("RU",),
                vacancy_geographies=("RU", "AM"),
                hybrid_ok=True,
                office_ok=True,
            ),
            vacancy=VacancyFilterFacts(
                title="Quality Assurance Specialist",
                company="Coffee House Company",
                countries=("AM",),
                remote_scopes=("onsite",),
                city="Yerevan",
            ),
        )

        self.assertFalse(decision.keep)
        self.assertTrue(decision.title_matches)
        self.assertTrue(decision.include_in_filtered_out)
        self.assertEqual(("remote_eligibility_mismatch",), decision.reasons)

    def test_unknown_positive_filter_facts_do_not_remove_vacancy(self) -> None:
        decision = decide_vacancy_filter(
            criteria=VacancyFilterCriteria(
                queries=("QA",),
                grades=("senior",),
                salary_from=5000,
                published_since=date(2026, 1, 1),
                relocation=True,
                remote_mode=RemoteMode.COMPATIBLE_REMOTE,
                work_from_geographies=("RU",),
                vacancy_geographies=("RU",),
            ),
            vacancy=VacancyFilterFacts(
                title="QA Engineer",
                remote_scopes=("unknown",),
            ),
        )

        self.assertTrue(decision.keep)
        self.assertTrue(decision.title_matches)
        self.assertFalse(decision.include_in_filtered_out)
        self.assertEqual((), decision.reasons)

    def test_title_matches_any_query_variant(self) -> None:
        decision = decide_vacancy_filter(
            criteria=VacancyFilterCriteria(queries=("Quality Assurance", "SDET")),
            vacancy=VacancyFilterFacts(title="Senior SDET Engineer"),
        )

        self.assertTrue(decision.keep)
        self.assertTrue(decision.title_matches)


if __name__ == "__main__":
    unittest.main()
