from __future__ import annotations

import unittest

from job_harness.v2.runtime.fact_derivers import derive_selection_facts


class SelectionFactDeriverTest(unittest.TestCase):
    def test_derives_explicit_grade_from_title_without_mutating_source_grade(self) -> None:
        derivation = derive_selection_facts(
            {
                "title": "Junior Software Development Engineer in Test",
                "native_grade": None,
                "description": "AI quality tooling",
            }
        )[0]

        self.assertEqual(["junior"], derivation.payload["grade"]["resolved"])
        self.assertEqual(["junior"], derivation.payload["grade"]["title_evidence"])

    def test_native_grade_is_used_when_title_has_no_grade(self) -> None:
        derivation = derive_selection_facts(
            {
                "title": "QA Engineer",
                "native_grade": "senior",
            }
        )[0]

        self.assertEqual(["senior"], derivation.payload["grade"]["resolved"])
        self.assertEqual(["senior"], derivation.payload["grade"]["source_evidence"])

    def test_title_grade_wins_and_records_source_conflict(self) -> None:
        derivation = derive_selection_facts(
            {
                "title": "Middle/Senior QA Engineer",
                "native_grade": "lead",
            }
        )[0]

        self.assertEqual("selection-facts.v6", derivation.output_schema_id)
        self.assertEqual(
            {
                "title_evidence": ["middle", "senior"],
                "source_evidence": ["lead"],
                "resolved": ["middle", "senior"],
                "conflict": True,
                "evidence": ["title", "native_grade"],
            },
            derivation.payload["grade"],
        )

    def test_preserves_mixed_location_components(self) -> None:
        derivation = derive_selection_facts(
            {
                "title": "Data Analyst",
                "location": {
                    "text": "London | Vilnius",
                    "cities": ["London", "Vilnius"],
                    "countries": ["GB", "LT"],
                    "regions": ["EU"],
                },
            }
        )[0]

        self.assertEqual(
            {
                "raw_text": "London | Vilnius",
                "cities": ["London", "Vilnius"],
                "countries": ["GB", "LT"],
                "regions": ["EU"],
                "evidence": [
                    "location.text",
                    "location.cities",
                    "location.countries",
                    "location.regions",
                ],
            },
            derivation.payload["location"],
        )

    def test_keeps_physical_location_separate_from_remote_scope(self) -> None:
        derivation = derive_selection_facts(
            {
                "title": "Data Analyst",
                "location": {
                    "text": "London, Vilnius; Remote, Germany",
                    "cities": ["London", "Vilnius"],
                    "countries": ["GB", "LT"],
                    "regions": ["EU"],
                },
                "work_formats": ["remote", "hybrid"],
                "remote_scopes": [{"kind": "country", "code": "DE"}],
            }
        )[0]

        self.assertEqual(["remote", "hybrid"], derivation.payload["workplace"]["formats"])
        self.assertEqual(["country:DE"], derivation.payload["workplace"]["remote_scopes"])

    def test_normalizes_complete_compensation_without_converting_currency(self) -> None:
        derivation = derive_selection_facts(
            {
                "title": "QA Lead",
                "salary": {
                    "salary_from": 300_000,
                    "salary_to": 400_000,
                    "currency": "RUR",
                    "period": "month",
                    "gross": True,
                },
            }
        )[0]

        self.assertEqual(
            {
                "minimum": 300_000,
                "maximum": 400_000,
                "currency": "RUB",
                "period": "month",
                "gross": True,
                "evidence": [
                    "salary.salary_from",
                    "salary.salary_to",
                    "salary.currency",
                    "salary.period",
                    "salary.gross",
                ],
            },
            derivation.payload["compensation"],
        )

    def test_keeps_relocation_and_visa_sponsorship_as_distinct_facts(self) -> None:
        visa_only = derive_selection_facts(
            {
                "title": "Model Quality Engineer",
                "description": "Visa sponsorship is available for this role.",
            }
        )[0]
        relocation = derive_selection_facts(
            {
                "title": "Model Quality Engineer",
                "description": "Relocation assistance is available for this role.",
            }
        )[0]
        no_visa = derive_selection_facts(
            {
                "title": "Model Quality Engineer",
                "description": "We do not sponsor employment visas.",
            }
        )[0]

        self.assertEqual(
            {"supported": None, "destinations": [], "evidence": []},
            visa_only.payload["relocation"],
        )
        self.assertEqual(
            {"supported": True, "evidence": ["description"]},
            visa_only.payload["visa_sponsorship"],
        )
        self.assertEqual(
            {"supported": True, "destinations": [], "evidence": ["description"]},
            relocation.payload["relocation"],
        )
        self.assertEqual(
            {"supported": None, "evidence": []},
            relocation.payload["visa_sponsorship"],
        )
        self.assertEqual(
            {"supported": None, "destinations": [], "evidence": []},
            no_visa.payload["relocation"],
        )
        self.assertEqual(
            {"supported": False, "evidence": ["description"]},
            no_visa.payload["visa_sponsorship"],
        )

    def test_preserves_structured_relocation_destinations(self) -> None:
        derivation = derive_selection_facts(
            {
                "title": "AI Evaluation Engineer",
                "relocation": True,
                "relocation_destinations": [
                    {
                        "text": "United States",
                        "cities": [],
                        "countries": ["US"],
                        "regions": [],
                    }
                ],
            }
        )[0]

        self.assertEqual(
            {
                "supported": True,
                "destinations": ["US"],
                "evidence": ["relocation", "relocation_destinations"],
            },
            derivation.payload["relocation"],
        )

    def test_normalizes_structured_employer_geographies(self) -> None:
        derivation = derive_selection_facts(
            {
                "title": "AI Lead",
                "company": {"employer_geographies": ["Russia", "country:AM"]},
            }
        )[0]

        self.assertEqual(
            ["country:RU", "country:AM"],
            derivation.payload["employer_geographies"],
        )

    def test_normalizes_company_profile_locations_merged_into_fact_set(self) -> None:
        derivation = derive_selection_facts(
            {
                "title": "AI Lead",
                "locations": [{"text": "Moscow, Russia"}],
            }
        )[0]

        self.assertEqual(["country:RU"], derivation.payload["employer_geographies"])

    def test_derives_explicit_remote_in_russia_from_detail_conditions(self) -> None:
        derivation = derive_selection_facts(
            {
                "title": "Manual QA Engineer",
                "work_formats": [],
                "remote_scopes": [],
                "conditions": ["Полностью удаленная работа на территории РФ."],
            }
        )[0]

        self.assertEqual(["remote"], derivation.payload["workplace"]["formats"])
        self.assertEqual(["country:RU"], derivation.payload["workplace"]["remote_scopes"])

    def test_derives_global_scope_only_from_explicit_worldwide_language(self) -> None:
        derivation = derive_selection_facts(
            {
                "title": "QA Engineer",
                "description": "This is a fully remote role. Work from anywhere in the world.",
                "work_formats": [],
                "remote_scopes": [],
            }
        )[0]

        self.assertEqual(["remote"], derivation.payload["workplace"]["formats"])
        self.assertEqual(["global"], derivation.payload["workplace"]["remote_scopes"])

    def test_does_not_infer_work_arrangement_from_ambiguous_text(self) -> None:
        derivation = derive_selection_facts(
            {
                "title": "QA Engineer",
                "description": "Collaborate with distributed teams and test office software.",
                "work_formats": [],
                "remote_scopes": [],
            }
        )[0]

        self.assertEqual([], derivation.payload["workplace"]["formats"])
        self.assertEqual([], derivation.payload["workplace"]["remote_scopes"])


if __name__ == "__main__":
    unittest.main()
