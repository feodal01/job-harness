from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import UTC, date, datetime
from typing import Any

from tests.v2._support.contract_runtime import listing

from job_harness.v2.contracts import (
    AttemptCounts,
    AttemptEvidence,
    CriteriaDiagnostics,
    DescriptionAvailability,
    Grade,
    RawSearchRecord,
    RetryInfo,
    RetryNextAction,
    SearchCriterion,
    SearchRequest,
    SourceAttemptRecord,
    SourceOutcome,
    SourceType,
    TextExclusion,
    WorkFormat,
)
from job_harness.v2.postprocessing import ProcessingPhase, ResultTablePostProcessor
from job_harness.v2.serialization import to_jsonable


def _process_payload(
    *,
    request: SearchRequest,
    raw_records: tuple[RawSearchRecord, ...],
    source_attempts: tuple[SourceAttemptRecord, ...],
) -> dict[str, Any]:
    result = ResultTablePostProcessor().process(
        request=request,
        run_id="r-test",
        append_sequence=0,
        phase=ProcessingPhase.FINAL,
        raw_records=tuple(_json_object(record) for record in raw_records),
        source_attempts=tuple(_source_attempt_payload(record) for record in source_attempts),
    )
    return result.payload


def _source_attempt_payload(record: SourceAttemptRecord) -> dict[str, Any]:
    payload = _json_object(record)
    payload["record_type"] = "source_attempt"
    return payload


def _json_object(value: object) -> dict[str, Any]:
    payload = to_jsonable(value)
    if not isinstance(payload, dict):
        raise TypeError("expected JSON object payload")
    return payload


class ResultTablePostProcessorTest(unittest.TestCase):
    def test_builds_filtered_deduped_processed_results(self) -> None:
        # Arrange
        payload = _process_payload(
            request=SearchRequest(
                query_variants=("QA",),
                exclude_companies=("blocked",),
                exclude_text=(TextExclusion("legacy stack"),),
            ),
            raw_records=(
                _raw_record("1", company="Acme"),
                _raw_record("1", company="Acme"),
                _raw_record("2", company="BlockedCorp"),
            ),
            source_attempts=(_attempt_record(),),
        )

        # Assert
        self.assertEqual(3, payload["raw_records_read"])
        self.assertEqual(
            {
                "append_to_run_id": None,
                "exclude_companies": ["blocked"],
                "exclude_text": [
                    {
                        "case_sensitive": False,
                        "fields": [],
                        "mode": "substring",
                        "pattern": "legacy stack",
                    }
                ],
                "grades": [],
                "published_since": None,
                "query_variants": ["QA"],
                "relocation": None,
                "remote_scopes": [],
                "salary_from": None,
                "source_types": [],
                "sources": [],
                "vacancy_geographies": [],
                "work_formats": [],
            },
            payload["search_request"],
        )
        self.assertEqual(1, payload["result_count"])
        self.assertEqual("1", payload["results"][0]["source_listing_id"])
        self.assertEqual({"excluded_company": 1}, payload["removed_counts"])
        self.assertEqual(1, len(payload["filtered_out_results"]))
        self.assertEqual("2", payload["filtered_out_results"][0]["source_listing_id"])
        self.assertEqual("filtered_out", payload["filtered_out_results"][0]["decision"])
        self.assertEqual(["excluded_company"], payload["filtered_out_results"][0]["decision_reasons"])
        self.assertEqual(
            "none_native_request",
            payload["source_criteria_plan"][0]["actions"][0]["action"],
        )

    def test_propagates_detail_parse_status_into_processed_results(self) -> None:
        # Arrange
        payload = _process_payload(
            request=SearchRequest(query_variants=("QA",)),
            raw_records=(
                _raw_record(
                    "1",
                    company="Acme",
                    description="",
                    raw_text="QA Engineer from search snippet",
                    description_availability=DescriptionAvailability.DETAIL_BLOCKED,
                    detail_fetched=True,
                    detail_parse_error="hh.ru account captcha on vacancy detail",
                ),
            ),
            source_attempts=(_attempt_record(),),
        )

        # Assert
        result = payload["results"][0]
        self.assertEqual("detail_blocked", result["description_availability"])
        self.assertTrue(result["detail_fetched"])
        self.assertEqual(
            "hh.ru account captcha on vacancy detail",
            result["detail_parse_error"],
        )
        self.assertIsNone(result["description"])

    def test_builds_hh_source_facts_from_raw_structured_fields(self) -> None:
        # Arrange / Act
        payload = _process_payload(
            request=SearchRequest(query_variants=("QA",)),
            raw_records=(
                _raw_record(
                    "134519442",
                    company="Норд Клан",
                    raw={
                        "compensation": {"noCompensation": {}},
                        "workExperience": "between1And3",
                        "employmentForm": "FULL",
                        "acceptLaborContract": True,
                        "workScheduleByDays": ["FIVE_ON_TWO_OFF"],
                        "workingHours": ["HOURS_8"],
                        "workFormats": ["REMOTE"],
                    },
                ),
            ),
            source_attempts=(_attempt_record(),),
        )

        # Assert
        self.assertEqual("не указан", payload["results"][0]["display_salary"])
        self.assertEqual("1–3 года", payload["results"][0]["display_experience"])
        self.assertEqual("удалённо", payload["results"][0]["display_work_format"])
        self.assertEqual(
            [
                {"label": "Employment", "value": "полная занятость"},
                {"label": "Contract", "value": "трудовой договор"},
                {"label": "Schedule", "value": "5/2"},
                {"label": "Working hours", "value": "8"},
            ],
            payload["results"][0]["source_facts"],
        )

    def test_extracts_application_channels_from_normalized_raw_listing(self) -> None:
        # Arrange / Act
        payload = _process_payload(
            request=SearchRequest(query_variants=("QA",)),
            raw_records=(
                _raw_record(
                    "134371846",
                    company="MANGO FZCO",
                    raw={
                        "application_channels": [
                            {
                                "type": "company_site",
                                "label": "Site",
                                "url": "https://windi.com/",
                                "status": "source_provided",
                                "source": "hh_ru.company_site_url",
                            },
                            {
                                "type": "aggregator_company_profile",
                                "label": "Profile",
                                "url": "https://hh.ru/employer/5174681",
                                "status": "source_provided",
                                "source": "hh_ru.company_profile_url",
                            },
                        ]
                    },
                ),
            ),
            source_attempts=(_attempt_record(),),
        )

        # Assert
        self.assertEqual(
            [
                {
                    "type": "company_site",
                    "label": "Site",
                    "url": "https://windi.com/",
                    "status": "source_provided",
                    "source": "hh_ru.company_site_url",
                },
                {
                    "type": "aggregator_company_profile",
                    "label": "Profile",
                    "url": "https://hh.ru/employer/5174681",
                    "status": "source_provided",
                    "source": "hh_ru.company_profile_url",
                },
            ],
            payload["results"][0]["application_channels"],
        )

    def test_extracts_company_contacts_from_normalized_raw_listing(self) -> None:
        # Arrange / Act
        payload = _process_payload(
            request=SearchRequest(query_variants=("QA",)),
            raw_records=(
                _raw_record(
                    "1000163567",
                    source="habr_career",
                    company="SimbirSoft",
                    raw={
                        "company_contacts": [
                            {
                                "type": "email",
                                "label": "Email",
                                "value": "hr@simbirsoft.com",
                                "url": "mailto:hr@simbirsoft.com",
                                "source": "habr_career.company_profile",
                            },
                            {
                                "type": "telegram",
                                "label": "Telegram",
                                "value": "@simbirsoft_dev",
                                "url": "https://telegram.me/simbirsoft_dev",
                                "source": "habr_career.company_profile",
                            },
                            {"type": "broken", "label": "Broken"},
                        ]
                    },
                ),
            ),
            source_attempts=(_attempt_record(source="habr_career"),),
        )

        # Assert
        self.assertEqual(
            [
                {
                    "type": "email",
                    "label": "Email",
                    "value": "hr@simbirsoft.com",
                    "url": "mailto:hr@simbirsoft.com",
                    "source": "habr_career.company_profile",
                },
                {
                    "type": "telegram",
                    "label": "Telegram",
                    "value": "@simbirsoft_dev",
                    "url": "https://telegram.me/simbirsoft_dev",
                    "source": "habr_career.company_profile",
                },
            ],
            payload["results"][0]["company_contacts"],
        )

    def test_prefers_resolved_application_channels_from_raw_listing(self) -> None:
        # Arrange / Act
        payload = _process_payload(
            request=SearchRequest(query_variants=("QA",)),
            raw_records=(
                _raw_record(
                    "134371846",
                    company="MANGO FZCO",
                    raw={
                        "company": {
                            "companySiteUrl": "https://windi.com",
                            "employerUrl": "https://hh.ru/employer/5174681",
                        },
                        "application_channels": [
                            {
                                "type": "company_career_page",
                                "label": "Careers",
                                "url": "https://windi.com/careers",
                                "status": "resolved",
                                "source": "company_site_homepage",
                            }
                        ],
                    },
                ),
            ),
            source_attempts=(_attempt_record(),),
        )

        # Assert
        self.assertEqual(
            [
                {
                    "type": "company_career_page",
                    "label": "Careers",
                    "url": "https://windi.com/careers",
                    "status": "resolved",
                    "source": "company_site_homepage",
                }
            ],
            payload["results"][0]["application_channels"],
        )

    def test_uses_source_work_format_before_boolean_remote_fallback(self) -> None:
        # Arrange / Act
        payload = _process_payload(
            request=SearchRequest(query_variants=("QA",)),
            raw_records=(
                _raw_record(
                    "676604",
                    company="",
                    source="hirify",
                    title="Senior QA Automation Engineer (Fintech)",
                    remote_in_country=False,
                    raw={"work_format": "hybrid"},
                ),
            ),
            source_attempts=(_attempt_record(source="hirify"),),
        )

        # Assert
        self.assertEqual("hybrid", payload["results"][0]["display_work_format"])

    def test_vk_hybrid_work_format_aliases_win_before_remote_boolean(self) -> None:
        # Arrange / Act
        payload = _process_payload(
            request=SearchRequest(
                query_variants=("QA",),
                work_formats=(WorkFormat.REMOTE, WorkFormat.HYBRID),
                remote_scopes=("country:GB",),
                vacancy_geographies=("country:GB",),

            ),
            raw_records=(
                _raw_record(
                    "45608",
                    company="VK",
                    source="career:vk",
                    country="UK",
                    remote_in_country=True,
                    remote_global=False,
                    raw={"work_format": "Комбинированный"},
                ),
                _raw_record(
                    "45130",
                    company="VK",
                    source="career:vk",
                    country="PL",
                    remote_in_country=True,
                    remote_global=False,
                    raw={"work_format": "гибкий"},
                ),
            ),
            source_attempts=(_attempt_record(source="career:vk"),),
        )

        # Assert
        self.assertEqual(["45608"], [row["source_listing_id"] for row in payload["results"]])
        self.assertEqual("hybrid", payload["results"][0]["display_work_format"])
        self.assertEqual(["hybrid"], payload["results"][0]["work_formats"])
        self.assertEqual("unknown", payload["results"][0]["remote_scope"])
        self.assertEqual("hybrid", payload["filtered_out_results"][0]["display_work_format"])
        self.assertEqual(["hybrid"], payload["filtered_out_results"][0]["work_formats"])
        self.assertEqual("unknown", payload["filtered_out_results"][0]["remote_scope"])
        self.assertEqual({"vacancy_geography_mismatch": 1}, payload["removed_counts"])

    def test_linkedin_workplace_tags_are_fallback_after_explicit_work_format(self) -> None:
        # Arrange / Act
        payload = _process_payload(
            request=SearchRequest(query_variants=("QA",)),
            raw_records=(
                _raw_record(
                    "1",
                    company="JetBrains",
                    source="career:jetbrains",
                    raw={"work_format": "office", "linkedin_workplace_tags": ["#LI-HYBRID"]},
                ),
                _raw_record(
                    "2",
                    company="JetBrains",
                    source="career:jetbrains",
                    remote_in_country=False,
                    remote_global=False,
                    raw={"linkedin_workplace_tags": ["#LI-HYBRID", "#LI-REMOTE"]},
                ),
                _raw_record(
                    "3",
                    company="JetBrains",
                    source="career:jetbrains",
                    remote_in_country=False,
                    remote_global=False,
                ),
            ),
            source_attempts=(_attempt_record(source="career:jetbrains"),),
        )

        # Assert
        self.assertEqual(["office", "remote, hybrid", "office"], [
            row["display_work_format"] for row in payload["results"]
        ])
        self.assertEqual([["office"], ["remote", "hybrid"], ["office"]], [
            row["work_formats"] for row in payload["results"]
        ])

    def test_source_lists_multiple_workplace_options_without_collapsing_work_formats(self) -> None:
        # Arrange / Act
        payload = _process_payload(
            request=SearchRequest(
                query_variants=("QA",),
                work_formats=(WorkFormat.REMOTE,),
                remote_scopes=("region:EU",),
            ),
            raw_records=(
                _raw_record(
                    "1",
                    company="CoinsPaid",
                    source="career:coinspaid",
                    country=None,
                    location_text="Remote - European Region",
                    remote_in_country=True,
                    remote_global=False,
                    raw={"work_format": ["remote", "hybrid"], "remote_locations": ["Europe"]},
                ),
                _raw_record(
                    "2",
                    company="Acme",
                    source="career:acme",
                    country="US",
                    remote_in_country=True,
                    remote_global=False,
                    raw={"work_format": ["remote", "office"], "remote_locations": ["US"]},
                ),
            ),
            source_attempts=(_attempt_record(source="career:coinspaid"),),
        )

        # Assert
        self.assertEqual(["1"], [row["source_listing_id"] for row in payload["results"]])
        self.assertEqual(
            [["remote_scope_mismatch"]],
            [row["decision_reasons"] for row in payload["filtered_out_results"]],
        )
        self.assertEqual("remote, hybrid", payload["results"][0]["display_work_format"])
        self.assertEqual(["remote", "hybrid"], payload["results"][0]["work_formats"])
        self.assertEqual("region:EU", payload["results"][0]["remote_scope"])
        self.assertEqual("remote, office", payload["filtered_out_results"][0]["display_work_format"])
        self.assertEqual(["remote", "office"], payload["filtered_out_results"][0]["work_formats"])
        self.assertEqual("country:US", payload["filtered_out_results"][0]["remote_scope"])

    def test_linkedin_remote_tag_drives_remote_scope_before_boolean_fallbacks(self) -> None:
        # Arrange / Act
        payload = _process_payload(
            request=SearchRequest(
                query_variants=("QA",),
                work_formats=(WorkFormat.REMOTE,),
                remote_scopes=("country:US",),
            ),
            raw_records=(
                _raw_record(
                    "1",
                    company="JetBrains",
                    source="career:jetbrains",
                    country="US",
                    remote_in_country=False,
                    remote_global=False,
                    raw={"linkedin_workplace_tags": ["#LI-REMOTE"]},
                ),
                _raw_record(
                    "2",
                    company="JetBrains",
                    source="career:jetbrains",
                    country="US",
                    remote_in_country=False,
                    remote_global=False,
                ),
            ),
            source_attempts=(_attempt_record(source="career:jetbrains"),),
        )

        # Assert
        self.assertEqual(["1"], [row["source_listing_id"] for row in payload["results"]])
        self.assertEqual("country:US", payload["results"][0]["remote_scope"])
        self.assertEqual("remote", payload["results"][0]["display_work_format"])
        self.assertEqual({"work_format_mismatch": 1}, payload["removed_counts"])

    def test_requested_hybrid_accepts_linkedin_hybrid_tag_with_matching_geography(self) -> None:
        # Arrange / Act
        payload = _process_payload(
            request=SearchRequest(
                query_variants=("QA",),
                work_formats=(WorkFormat.REMOTE, WorkFormat.HYBRID),
                remote_scopes=("country:CY",),
                vacancy_geographies=("country:CY",),
            ),
            raw_records=(
                _raw_record(
                    "1",
                    company="JetBrains",
                    source="career:jetbrains",
                    country="Cyprus",
                    remote_in_country=False,
                    remote_global=False,
                    raw={"linkedin_workplace_tags": ["#LI-HYBRID"]},
                ),
                _raw_record(
                    "2",
                    company="JetBrains",
                    source="career:jetbrains",
                    country="US",
                    remote_in_country=False,
                    remote_global=False,
                    raw={"linkedin_workplace_tags": ["#LI-HYBRID"]},
                ),
            ),
            source_attempts=(_attempt_record(source="career:jetbrains"),),
        )

        # Assert
        self.assertEqual(["1"], [row["source_listing_id"] for row in payload["results"]])
        self.assertEqual("hybrid", payload["results"][0]["display_work_format"])
        self.assertEqual("unknown", payload["results"][0]["remote_scope"])
        self.assertEqual({"vacancy_geography_mismatch": 1}, payload["removed_counts"])

    def test_normalizes_country_values_during_postprocessing(self) -> None:
        # Arrange / Act
        payload = _process_payload(
            request=SearchRequest(query_variants=("QA",)),
            raw_records=(
                _raw_record("1", company="Acme", country="Кипр"),
                _raw_record("2", company="Acme", country="United States"),
                _raw_record("3", company="Acme", country="CZ"),
                _raw_record("4", company="Acme", country=None, raw={"regions": ["cyprus"]}),
                _raw_record("5", company="Acme", country="europe"),
                _raw_record("6", company="Acme", country="turkey"),
                _raw_record("7", company="Acme", country=None, raw={"regions": ["czech_republic"]}),
                _raw_record("8", company="Acme", country="UK"),
                _raw_record("9", company="Acme", country=None, raw={"regions": ["cote_d_ivoire"]}),
                _raw_record("10", company="Acme", country="EU"),
                _raw_record("11", company="Acme", country=None, raw={"remote_type": "europe"}),
                _raw_record("12", company="Acme", country=None, raw={"remote_restrictions": ["russia"]}),
                _raw_record("13", company="Acme", country=None, raw={"remote_type": "global"}),
                _raw_record(
                    "14",
                    company="Acme",
                    country=None,
                    location_text=(
                        "Boston, Massachusetts; Foster City, California; Marlton, New Jersey; "
                        "Remote, United States"
                    ),
                ),
                _raw_record("15", company="Acme", country="BY", location_text="Россия, Беларусь"),
                _raw_record(
                    "16",
                    company="Acme",
                    country=None,
                    location_text=(
                        "Amsterdam, Netherlands; Belgrade, Serbia; Berlin, Germany; Limassol, Cyprus; "
                        "London, United Kingdom; Madrid, Spain; Prague, Czech Republic"
                    ),
                    remote_in_country=True,
                    remote_global=False,
                ),
            ),
            source_attempts=(_attempt_record(),),
        )

        # Assert
        self.assertEqual(
            [
                "CY",
                "US",
                "CZ",
                "CY",
                "EU",
                "TR",
                "CZ",
                "GB",
                "CI",
                "EU",
                "EU",
                "RU",
                None,
                "US",
                "RU, BY",
                "NL, RS, DE, CY, GB, ES, CZ",
            ],
            [row["country"] for row in payload["results"]],
        )
        self.assertEqual(["RU", "BY"], payload["results"][14]["countries"])
        self.assertEqual("country:US", payload["results"][13]["remote_scope"])
        self.assertEqual(
            "country:NL, country:RS, country:DE, country:CY, country:GB, country:ES, country:CZ",
            payload["results"][15]["remote_scope"],
        )

    def test_country_filter_uses_normalized_country_values(self) -> None:
        # Arrange / Act
        payload = _process_payload(
            request=SearchRequest(query_variants=("QA",), vacancy_geographies=("country:CY",)),
            raw_records=(
                _raw_record("1", company="Acme", country="Кипр"),
                _raw_record("2", company="Acme", country="Украина"),
            ),
            source_attempts=(_attempt_record(),),
        )

        # Assert
        self.assertEqual(["CY"], [row["country"] for row in payload["results"]])
        self.assertEqual({"vacancy_geography_mismatch": 1}, payload["removed_counts"])
        self.assertEqual("UA", payload["filtered_out_results"][0]["country"])

    def test_country_filter_matches_any_normalized_country_value(self) -> None:
        # Arrange / Act
        payload = _process_payload(
            request=SearchRequest(query_variants=("QA",), vacancy_geographies=("country:RU",)),
            raw_records=(
                _raw_record("1", company="Acme", country="BY", location_text="Россия, Беларусь"),
                _raw_record("2", company="Acme", country="BY", location_text="Минск, Беларусь"),
            ),
            source_attempts=(_attempt_record(),),
        )

        # Assert
        self.assertEqual(["RU, BY"], [row["country"] for row in payload["results"]])
        self.assertEqual(["BY"], [row["country"] for row in payload["filtered_out_results"]])
        self.assertEqual({"vacancy_geography_mismatch": 1}, payload["removed_counts"])

    def test_country_filter_uses_explicit_region_scope_membership(self) -> None:
        # Arrange / Act
        payload = _process_payload(
            request=SearchRequest(query_variants=("QA",), vacancy_geographies=("country:PL",)),
            raw_records=(
                _raw_record("1", company="Acme", country="europe"),
                _raw_record("2", company="Acme", country="EU"),
                _raw_record("3", company="Acme", country="United States"),
            ),
            source_attempts=(_attempt_record(),),
        )

        # Assert
        self.assertEqual(["EU", "EU"], [row["country"] for row in payload["results"]])
        self.assertEqual({"vacancy_geography_mismatch": 1}, payload["removed_counts"])
        self.assertEqual("US", payload["filtered_out_results"][0]["country"])

    def test_country_filter_does_not_treat_russia_as_europe_scope_member(self) -> None:
        # Arrange / Act
        payload = _process_payload(
            request=SearchRequest(query_variants=("QA",), vacancy_geographies=("country:RU",)),
            raw_records=(
                _raw_record("1", company="Acme", country="europe"),
                _raw_record("2", company="Acme", country="Russia"),
            ),
            source_attempts=(_attempt_record(),),
        )

        # Assert
        self.assertEqual(["RU"], [row["country"] for row in payload["results"]])
        self.assertEqual({"vacancy_geography_mismatch": 1}, payload["removed_counts"])
        self.assertEqual("EU", payload["filtered_out_results"][0]["country"])

    def test_remote_scope_filter_matches_global_and_intersecting_scope(self) -> None:
        # Arrange / Act
        payload = _process_payload(
            request=SearchRequest(
                query_variants=("QA",),
                work_formats=(WorkFormat.REMOTE,),
                remote_scopes=("region:EU",),
            ),
            raw_records=(
                _raw_record("1", company="Acme", country="US", remote_in_country=True, remote_global=True),
                _raw_record("2", company="Acme", country="US", remote_in_country=True, remote_global=False),
                _raw_record("3", company="Acme", country="PL", remote_in_country=True, remote_global=False),
                _raw_record("4", company="Acme", country="RU", remote_in_country=True, remote_global=False),
                _raw_record(
                    "5",
                    company="Acme",
                    country="CY",
                    remote_in_country=True,
                    remote_global=False,
                    raw={"remote_restrictions": ["EU"]},
                ),
            ),
            source_attempts=(_attempt_record(),),
        )

        # Assert
        self.assertEqual(["1", "3", "5"], [row["source_listing_id"] for row in payload["results"]])
        self.assertEqual({"remote_scope_mismatch": 2}, payload["removed_counts"])
        self.assertEqual(
            ["country:US", "country:RU"],
            [row["remote_scope"] for row in payload["filtered_out_results"]],
        )

    def test_country_only_listing_does_not_satisfy_requested_remote_work_format(self) -> None:
        # Arrange / Act
        payload = _process_payload(
            request=SearchRequest(
                query_variants=("Quality Assurance",),
                work_formats=(WorkFormat.REMOTE, WorkFormat.HYBRID, WorkFormat.OFFICE),
                remote_scopes=("country:RU",),
                vacancy_geographies=("country:RU", "country:AM"),

            ),
            raw_records=(
                _raw_record(
                    "am",
                    company="Coffee House Company",
                    source="staff_am",
                    query_variant="Quality Assurance",
                    title="Quality Assurance Specialist",
                    country="AM",
                    city="Yerevan",
                ),
                _raw_record(
                    "ru",
                    company="Acme",
                    source="staff_am",
                    query_variant="Quality Assurance",
                    title="Quality Assurance Engineer",
                    country="RU",
                    city="Moscow",
                ),
            ),
            source_attempts=(
                _attempt_record(
                    source="staff_am",
                    query_variant="Quality Assurance",
                    requested=frozenset(
                        {
                            SearchCriterion.QUERY,
                            SearchCriterion.WORK_FORMATS,
                            SearchCriterion.VACANCY_GEOGRAPHIES,
                        }
                    ),
                    native=frozenset({SearchCriterion.QUERY}),
                    structured=frozenset({SearchCriterion.WORK_FORMATS, SearchCriterion.VACANCY_GEOGRAPHIES}),
                    postprocess=frozenset({SearchCriterion.WORK_FORMATS, SearchCriterion.VACANCY_GEOGRAPHIES}),
                ),
            ),
        )

        # Assert
        self.assertEqual([], payload["results"])
        self.assertEqual({"work_format_mismatch": 2}, payload["removed_counts"])
        self.assertEqual(["unknown", "unknown"], [row["remote_scope"] for row in payload["filtered_out_results"]])
        self.assertEqual([None, None], [row["display_work_format"] for row in payload["filtered_out_results"]])

    def test_remote_scope_prefers_explicit_remote_locations_over_vacancy_locations(self) -> None:
        # Arrange / Act
        payload = _process_payload(
            request=SearchRequest(
                query_variants=("QA",),
                work_formats=(WorkFormat.REMOTE,),
                remote_scopes=("country:BY",),
            ),
            raw_records=(
                _raw_record(
                    "1",
                    company="Acme",
                    country="BY",
                    location_text="Минск (Беларусь), Россия",
                    remote_in_country=True,
                    remote_global=False,
                    raw={"remote_locations": ["Россия"]},
                ),
            ),
            source_attempts=(_attempt_record(),),
        )

        # Assert
        self.assertEqual([], payload["results"])
        self.assertEqual({"remote_scope_mismatch": 1}, payload["removed_counts"])
        self.assertEqual("country:RU", payload["filtered_out_results"][0]["remote_scope"])
        self.assertEqual("BY, RU", payload["filtered_out_results"][0]["country"])

    def test_remote_in_country_uses_city_derived_country_scope(self) -> None:
        # Arrange / Act
        payload = _process_payload(
            request=SearchRequest(
                query_variants=("QA",),
                work_formats=(WorkFormat.REMOTE,),
                remote_scopes=("country:RU",),
            ),
            raw_records=(
                _raw_record(
                    "1",
                    company="Acme",
                    city="Москва",
                    location_text="Москва",
                    remote_in_country=True,
                    remote_global=None,
                ),
            ),
            source_attempts=(_attempt_record(),),
        )

        # Assert
        self.assertEqual(["1"], [row["source_listing_id"] for row in payload["results"]])
        self.assertEqual("RU", payload["results"][0]["country"])
        self.assertEqual("country:RU", payload["results"][0]["remote_scope"])

    def test_remote_city_listing_infers_country_scope_without_remote_in_country_flag(self) -> None:
        # Arrange / Act
        payload = _process_payload(
            request=SearchRequest(
                query_variants=("Engineer",),
                work_formats=(WorkFormat.REMOTE,),
                remote_scopes=("country:ES",),
            ),
            raw_records=(
                _raw_record(
                    "1",
                    company="Collectly",
                    source="career:collectly",
                    query_variant="Engineer",
                    title="Senior DevOps Engineer (remote from GMT-7 to GMT+4 timezones)",
                    location_text="Barcelona",
                    remote_in_country=None,
                    remote_global=False,
                    raw={"remote_locations": ["Barcelona"], "work_format": ["remote"]},
                ),
            ),
            source_attempts=(_attempt_record(source="career:collectly"),),
        )

        # Assert
        self.assertEqual(["1"], [row["source_listing_id"] for row in payload["results"]])
        self.assertEqual("ES", payload["results"][0]["country"])
        self.assertEqual("remote", payload["results"][0]["display_work_format"])
        self.assertEqual("country:ES", payload["results"][0]["remote_scope"])

    def test_remote_timezone_hint_without_geography_is_removed_from_global_remote_scope(self) -> None:
        # Arrange / Act
        payload = _process_payload(
            request=SearchRequest(
                query_variants=("Engineer",),
                work_formats=(WorkFormat.REMOTE,), remote_scopes=("global",),
            ),
            raw_records=(
                _raw_record(
                    "1",
                    company="Collectly",
                    source="career:collectly",
                    query_variant="Engineer",
                    title="Senior DevOps Engineer (remote from GMT-7 to GMT+4 timezones)",
                    remote_in_country=None,
                    remote_global=None,
                    raw={},
                ),
            ),
            source_attempts=(_attempt_record(source="career:collectly"),),
        )

        # Assert
        self.assertEqual([], [row["source_listing_id"] for row in payload["results"]])
        self.assertEqual("remote", payload["filtered_out_results"][0]["display_work_format"])
        self.assertEqual("unknown", payload["filtered_out_results"][0]["remote_scope"])
        self.assertEqual({"remote_scope_mismatch": 1}, payload["removed_counts"])

    def test_remote_eu_locations_use_region_scope(self) -> None:
        # Arrange / Act
        payload = _process_payload(
            request=SearchRequest(
                query_variants=("Engineer",),
                work_formats=(WorkFormat.REMOTE,),
                remote_scopes=("region:EU",),
            ),
            raw_records=(
                _raw_record(
                    "1",
                    company="AppFollow",
                    source="career:appfollow",
                    query_variant="Engineer",
                    title="Senior Backend Engineer",
                    location_text="Remote",
                    remote_in_country=None,
                    remote_global=False,
                    raw={"remote_locations": ["Europe"], "work_format": ["remote"]},
                ),
            ),
            source_attempts=(_attempt_record(source="career:appfollow"),),
        )

        # Assert
        self.assertEqual(["1"], [row["source_listing_id"] for row in payload["results"]])
        self.assertEqual("EU", payload["results"][0]["country"])
        self.assertEqual("region:EU", payload["results"][0]["remote_scope"])

    def test_remote_multi_city_listing_infers_multiple_country_scopes(self) -> None:
        # Arrange / Act
        payload = _process_payload(
            request=SearchRequest(
                query_variants=("Engineer",),
                work_formats=(WorkFormat.REMOTE,),
                remote_scopes=("country:PT",),
            ),
            raw_records=(
                _raw_record(
                    "1",
                    company="Acme",
                    query_variant="Engineer",
                    title="Engineer",
                    location_text="Warsaw, Bucharest, Lisbon",
                    remote_in_country=None,
                    remote_global=False,
                    raw={"work_format": ["remote"]},
                ),
            ),
            source_attempts=(_attempt_record(),),
        )

        # Assert
        self.assertEqual(["1"], [row["source_listing_id"] for row in payload["results"]])
        self.assertEqual("PL, RO, PT", payload["results"][0]["country"])
        self.assertEqual(
            "country:PL, country:RO, country:PT",
            payload["results"][0]["remote_scope"],
        )

    def test_remote_us_city_state_listing_infers_country_scope(self) -> None:
        # Arrange / Act
        payload = _process_payload(
            request=SearchRequest(
                query_variants=("Engineer",),
                work_formats=(WorkFormat.REMOTE,),
                remote_scopes=("country:US",),
            ),
            raw_records=(
                _raw_record(
                    "1",
                    company="Veryfi, Inc.",
                    source="career:veryfi",
                    query_variant="Engineer",
                    title="Senior ML Engineer",
                    location_text="San Mateo, California / Remote",
                    remote_in_country=None,
                    remote_global=None,
                    raw={"work_format": ["remote"]},
                ),
            ),
            source_attempts=(_attempt_record(source="career:veryfi"),),
        )

        # Assert
        self.assertEqual(["1"], [row["source_listing_id"] for row in payload["results"]])
        self.assertEqual("US", payload["results"][0]["country"])
        self.assertEqual("country:US", payload["results"][0]["remote_scope"])

    def test_remote_city_region_country_listing_does_not_infer_unrelated_city_country(self) -> None:
        # Arrange / Act
        payload = _process_payload(
            request=SearchRequest(
                query_variants=("Engineer",),
                work_formats=(WorkFormat.REMOTE,),
                remote_scopes=("country:CO",),
            ),
            raw_records=(
                _raw_record(
                    "1",
                    company="Veryfi, Inc.",
                    source="career:veryfi",
                    query_variant="Engineer",
                    title="Data Annotation Engineer",
                    location_text="Medellín, Antioquia, CO / Remote (Medellín, Antioquia, CO)",
                    remote_in_country=None,
                    remote_global=None,
                    raw={"remote_locations": ["Medellín, Antioquia, CO"], "work_format": ["remote"]},
                ),
            ),
            source_attempts=(_attempt_record(source="career:veryfi"),),
        )

        # Assert
        self.assertEqual(["1"], [row["source_listing_id"] for row in payload["results"]])
        self.assertEqual("CO", payload["results"][0]["country"])
        self.assertEqual("country:CO", payload["results"][0]["remote_scope"])

    def test_physical_city_listing_infers_country_for_hybrid_filtering(self) -> None:
        # Arrange / Act
        payload = _process_payload(
            request=SearchRequest(
                query_variants=("Engineer",),
                work_formats=(WorkFormat.REMOTE, WorkFormat.HYBRID),
                remote_scopes=("country:GB",),
                vacancy_geographies=("country:GB",),
            ),
            raw_records=(
                _raw_record(
                    "1",
                    company="Acme",
                    query_variant="Engineer",
                    title="Engineer",
                    location_text="London",
                    remote_in_country=None,
                    remote_global=False,
                    raw={"work_format": ["hybrid"]},
                ),
            ),
            source_attempts=(_attempt_record(),),
        )

        # Assert
        self.assertEqual(["1"], [row["source_listing_id"] for row in payload["results"]])
        self.assertEqual("GB", payload["results"][0]["country"])
        self.assertEqual("unknown", payload["results"][0]["remote_scope"])

    def test_source_offices_contribute_vacancy_country_but_not_remote_scope(self) -> None:
        # Arrange / Act
        payload = _process_payload(
            request=SearchRequest(
                query_variants=("Campus Ambassador",),
                work_formats=(WorkFormat.REMOTE,),
                remote_scopes=("country:GB",),
                vacancy_geographies=("region:EU",),
            ),
            raw_records=(
                _raw_record(
                    "1",
                    company="JetBrains",
                    source="career:jetbrains",
                    query_variant="Campus Ambassador",
                    title="Campus Ambassador (Universities in Europe)",
                    location_text="Remote",
                    remote_in_country=None,
                    remote_global=None,
                    raw={"offices": ["Amsterdam", "London"]},
                ),
            ),
            source_attempts=(_attempt_record(source="career:jetbrains"),),
        )

        # Assert
        row = payload["filtered_out_results"][0]
        self.assertEqual("NL, GB", row["country"])
        self.assertEqual("unknown", row["remote_scope"])
        self.assertEqual(["remote_scope_mismatch"], row["decision_reasons"])

    def test_specific_location_does_not_merge_hidden_source_office_countries(self) -> None:
        # Arrange / Act
        payload = _process_payload(
            request=SearchRequest(query_variants=("Engineer",)),
            raw_records=(
                _raw_record(
                    "1",
                    company="Wrike",
                    source="career:wrike",
                    query_variant="Engineer",
                    title="AI-Enabled SW Engineer - Talent Pool",
                    location_text="Prague",
                    remote_in_country=False,
                    remote_global=False,
                    raw={"offices": ["Prague", "Nicosia", "Tallinn"]},
                ),
            ),
            source_attempts=(_attempt_record(source="career:wrike"),),
        )

        # Assert
        row = payload["results"][0]
        self.assertEqual("CZ", row["country"])
        self.assertEqual("unknown", row["remote_scope"])

    def test_lever_country_contributes_to_specific_locations_only(self) -> None:
        # Arrange / Act
        payload = _process_payload(
            request=SearchRequest(query_variants=("Engineer",)),
            raw_records=(
                _raw_record(
                    "barcelona",
                    company="Collectly",
                    source="career:collectly",
                    query_variant="Engineer",
                    title="Senior DevOps Engineer",
                    location_text="Barcelona",
                    remote_in_country=None,
                    remote_global=False,
                    raw={"lever_country": "ES", "remote_locations": ["Barcelona"]},
                ),
                _raw_record(
                    "remote",
                    company="Termius",
                    source="career:termius",
                    query_variant="Engineer",
                    title="Senior Software Engineer",
                    location_text="Remote",
                    remote_in_country=None,
                    remote_global=None,
                    raw={"lever_country": "GE"},
                ),
            ),
            source_attempts=(
                _attempt_record(
                    source="career:collectly",
                    source_type=SourceType.COMPANY_CAREER,
                    query_variant="Engineer",
                    structured=frozenset({SearchCriterion.QUERY}),
                    native=frozenset(),
                    postprocess=frozenset({SearchCriterion.QUERY}),
                ),
                _attempt_record(
                    source="career:termius",
                    source_type=SourceType.COMPANY_CAREER,
                    query_variant="Engineer",
                    structured=frozenset({SearchCriterion.QUERY}),
                    native=frozenset(),
                    postprocess=frozenset({SearchCriterion.QUERY}),
                ),
            ),
        )

        # Assert
        rows = {row["source_listing_id"]: row for row in payload["results"]}
        self.assertEqual("ES", rows["barcelona"]["country"])
        self.assertEqual("unknown", rows["barcelona"]["remote_scope"])
        self.assertIsNone(rows["remote"]["country"])
        self.assertEqual("unknown", rows["remote"]["remote_scope"])

    def test_vacancy_geography_removes_global_remote_without_matching_vacancy_location(self) -> None:
        # Arrange / Act
        payload = _process_payload(
            request=SearchRequest(
                query_variants=("QA",),
                work_formats=(WorkFormat.REMOTE,),
                remote_scopes=("region:EU",),
                vacancy_geographies=("region:EU",),
            ),
            raw_records=(
                _raw_record("1", company="Acme", country="US", remote_in_country=True, remote_global=True),
                _raw_record("2", company="Acme", country="PL", remote_in_country=True, remote_global=False),
                _raw_record("3", company="Acme", country=None, remote_in_country=True, remote_global=True),
            ),
            source_attempts=(_attempt_record(),),
        )

        # Assert
        self.assertEqual(["2"], [row["source_listing_id"] for row in payload["results"]])
        self.assertEqual({"vacancy_geography_mismatch": 2}, payload["removed_counts"])

    def test_country_gb_remote_scope_and_region_eu_vacancy_require_both_dimensions(self) -> None:
        # Arrange / Act
        payload = _process_payload(
            request=SearchRequest(
                query_variants=("QA",),
                work_formats=(WorkFormat.REMOTE,),
                remote_scopes=("country:GB",),
                vacancy_geographies=("region:EU",),

            ),
            raw_records=(
                _raw_record("1", company="Acme", country="US", remote_in_country=True, remote_global=True),
                _raw_record("2", company="Acme", country="PL", remote_in_country=True, remote_global=False),
                _raw_record(
                    "3",
                    company="Acme",
                    country="PL",
                    remote_in_country=True,
                    remote_global=False,
                    raw={"remote_restrictions": ["europe"]},
                ),
                _raw_record(
                    "4",
                    company="Acme",
                    country="UK",
                    remote_in_country=True,
                    remote_global=False,
                    raw={"regions": ["europe"], "remote_locations": ["UK"]},
                ),
                _raw_record("5", company="Acme", country="PL", raw={"work_format": "hybrid"}),
                _raw_record("6", company="Acme", country="PL", raw={"work_format": "office"}),
                _raw_record(
                    "7",
                    company="Acme",
                    country="UK",
                    raw={"regions": ["europe"], "work_format": "hybrid"},
                ),
            ),
            source_attempts=(_attempt_record(),),
        )

        # Assert
        self.assertEqual(["4"], [row["source_listing_id"] for row in payload["results"]])
        self.assertEqual(
            {
                "remote_scope_mismatch": 2,
                "vacancy_geography_mismatch": 1,
                "work_format_mismatch": 3,
            },
            payload["removed_counts"],
        )
        self.assertEqual(
            ["global", "country:PL", "region:EU", "unknown", "unknown", "unknown"],
            [row["remote_scope"] for row in payload["filtered_out_results"]],
        )

    def test_region_eu_vacancy_keeps_country_gb_inside_multi_country_listing(self) -> None:
        # Arrange / Act
        payload = _process_payload(
            request=SearchRequest(
                query_variants=("QA",),
                work_formats=(WorkFormat.REMOTE,),
                remote_scopes=("country:GB",),
                vacancy_geographies=("region:EU",),
            ),
            raw_records=(
                _raw_record(
                    "1",
                    company="JetBrains",
                    source="career:jetbrains",
                    country="NL, RS, DE, CY, GB, ES, CZ, PL, AM",
                    location_text=(
                        "Amsterdam, Netherlands; Berlin, Germany; London, United Kingdom; "
                        "Madrid, Spain; Prague, Czech Republic; Remote, Germany"
                    ),
                    remote_in_country=True,
                    remote_global=False,
                    raw={
                        "remote_locations": ["NL", "RS", "DE", "CY", "GB", "ES", "CZ", "PL", "AM"],
                    },
                ),
            ),
            source_attempts=(_attempt_record(source="career:jetbrains"),),
        )

        # Assert
        self.assertEqual(["1"], [row["source_listing_id"] for row in payload["results"]])
        self.assertEqual({}, payload["removed_counts"])
        expected_remote_scope = (
            "country:NL, country:RS, country:DE, country:CY, country:GB, "
            "country:ES, country:CZ, country:PL, country:AM"
        )
        self.assertEqual(
            expected_remote_scope,
            payload["results"][0]["remote_scope"],
        )

    def test_country_gb_vacancy_accepts_requested_physical_and_remote_formats(self) -> None:
        # Arrange / Act
        payload = _process_payload(
            request=SearchRequest(
                query_variants=("QA",),
                work_formats=(WorkFormat.REMOTE, WorkFormat.HYBRID, WorkFormat.OFFICE),
                remote_scopes=("country:GB",),
                vacancy_geographies=("country:GB",),
            ),
            raw_records=(
                _raw_record("1", company="Acme", country="US", remote_in_country=True, remote_global=True),
                _raw_record("2", company="Acme", country="United Kingdom", remote_in_country=True),
                _raw_record("3", company="Acme", country="UK", raw={"work_format": "hybrid"}),
                _raw_record("4", company="Acme", country="GB", raw={"work_format": "office"}),
            ),
            source_attempts=(_attempt_record(),),
        )

        # Assert
        self.assertEqual(["2", "3", "4"], [row["source_listing_id"] for row in payload["results"]])
        self.assertEqual({"vacancy_geography_mismatch": 1}, payload["removed_counts"])

    def test_country_gb_vacancy_remote_only_rejects_global_with_mismatched_vacancy_location(
        self,
    ) -> None:
        # Arrange / Act
        payload = _process_payload(
            request=SearchRequest(
                query_variants=("QA",),
                work_formats=(WorkFormat.REMOTE,),
                remote_scopes=("country:GB",),
                vacancy_geographies=("country:GB",),
            ),
            raw_records=(
                _raw_record("1", company="Acme", country="US", remote_in_country=True, remote_global=True),
                _raw_record("2", company="Acme", country="United Kingdom", remote_in_country=True),
                _raw_record("3", company="Acme", country="UK", raw={"work_format": "hybrid"}),
                _raw_record("4", company="Acme", country="GB", raw={"work_format": "office"}),
            ),
            source_attempts=(_attempt_record(),),
        )

        # Assert
        self.assertEqual(["2"], [row["source_listing_id"] for row in payload["results"]])
        self.assertEqual({"vacancy_geography_mismatch": 1, "work_format_mismatch": 2}, payload["removed_counts"])

    def test_work_formats_and_remote_scopes_distinguish_unknown_global_and_physical_evidence(self) -> None:
        # Arrange / Act
        global_payload = _process_payload(
            request=SearchRequest(query_variants=("QA",), work_formats=(WorkFormat.REMOTE,), remote_scopes=("global",)),
            raw_records=(
                _raw_record("1", company="Acme", remote_in_country=True, remote_global=True),
                _raw_record("2", company="Acme", remote_in_country=True, remote_global=False, country="PL"),
                _raw_record("3", company="Acme"),
            ),
            source_attempts=(_attempt_record(),),
        )
        non_remote_payload = _process_payload(
            request=SearchRequest(query_variants=("QA",), work_formats=(WorkFormat.HYBRID, WorkFormat.OFFICE)),
            raw_records=(
                _raw_record("1", company="Acme", remote_in_country=False, remote_global=False),
                _raw_record("2", company="Acme", remote_in_country=True, remote_global=True),
                _raw_record("3", company="Acme"),
            ),
            source_attempts=(_attempt_record(),),
        )

        # Assert
        self.assertEqual(["1"], [row["source_listing_id"] for row in global_payload["results"]])
        self.assertEqual({"remote_scope_mismatch": 1, "work_format_mismatch": 1}, global_payload["removed_counts"])
        self.assertEqual(["1"], [row["source_listing_id"] for row in non_remote_payload["results"]])
        self.assertEqual({"work_format_mismatch": 2}, non_remote_payload["removed_counts"])

    def test_bare_remote_without_global_evidence_is_removed_from_global_remote_scope(self) -> None:
        # Arrange / Act
        payload = _process_payload(
            request=SearchRequest(query_variants=("QA",), work_formats=(WorkFormat.REMOTE,), remote_scopes=("global",)),
            raw_records=(
                _raw_record(
                    "1",
                    company="Acme",
                    location_text="Remote",
                    remote_in_country=True,
                    remote_global=None,
                    raw={"locations": [{"city": None, "country": None, "remote": True}]},
                ),
            ),
            source_attempts=(_attempt_record(),),
        )

        # Assert
        self.assertEqual([], [row["source_listing_id"] for row in payload["results"]])
        self.assertEqual({"remote_scope_mismatch": 1}, payload["removed_counts"])
        self.assertEqual("unknown", payload["filtered_out_results"][0]["remote_scope"])

    def test_requested_hybrid_and_office_accept_physical_formats_in_vacancy_geography(self) -> None:
        # Arrange / Act
        payload = _process_payload(
            request=SearchRequest(
                query_variants=("QA",),
                work_formats=(WorkFormat.REMOTE, WorkFormat.HYBRID, WorkFormat.OFFICE),
                remote_scopes=("country:RU",),
                vacancy_geographies=("country:RU",),
            ),
            raw_records=(
                _raw_record("1", company="Acme", country="US", remote_in_country=True, remote_global=True),
                _raw_record("2", company="Acme", country="RU", raw={"work_format": "hybrid"}),
                _raw_record("3", company="Acme", country="RU", raw={"work_format": "office"}),
                _raw_record("4", company="Acme", country="TR", raw={"work_format": "hybrid"}),
                _raw_record("5", company="Acme", country=None, raw={"work_format": "office"}),
            ),
            source_attempts=(_attempt_record(),),
        )

        # Assert
        self.assertEqual(["2", "3"], [row["source_listing_id"] for row in payload["results"]])
        self.assertEqual({"vacancy_geography_mismatch": 3}, payload["removed_counts"])
        self.assertEqual("hybrid", payload["results"][0]["display_work_format"])
        self.assertEqual(["hybrid"], payload["results"][0]["work_formats"])
        self.assertEqual(
            [
                ["vacancy_geography_mismatch"],
                ["vacancy_geography_mismatch"],
                ["vacancy_geography_mismatch"],
            ],
            [row["decision_reasons"] for row in payload["filtered_out_results"]],
        )

    def test_hybrid_and_office_do_not_bypass_remote_policy_when_not_requested(self) -> None:
        # Arrange / Act
        payload = _process_payload(
            request=SearchRequest(
                query_variants=("QA",),
                work_formats=(WorkFormat.REMOTE,),
                remote_scopes=("country:RU",),
            ),
            raw_records=(
                _raw_record("1", company="Acme", country="RU", remote_in_country=True, raw={"work_format": "hybrid"}),
                _raw_record("2", company="Acme", country="RU", raw={"work_format": "office"}),
                _raw_record("3", company="Acme", country="US", remote_in_country=True, remote_global=True),
            ),
            source_attempts=(_attempt_record(),),
        )

        # Assert
        self.assertEqual(["3"], [row["source_listing_id"] for row in payload["results"]])
        self.assertEqual(
            {"work_format_mismatch": 2},
            payload["removed_counts"],
        )

    def test_global_remote_scope_removes_physical_formats(self) -> None:
        # Arrange / Act
        payload = _process_payload(
            request=SearchRequest(
                query_variants=("QA",),
                work_formats=(WorkFormat.REMOTE,), remote_scopes=("global",),
            ),
            raw_records=(
                _raw_record("1", company="Acme", country="CY", raw={"work_format": "office"}),
                _raw_record("2", company="Acme", country="CY", raw={"work_format": "hybrid"}),
                _raw_record("3", company="Acme", country="US", remote_in_country=True, remote_global=True),
            ),
            source_attempts=(_attempt_record(),),
        )

        # Assert
        self.assertEqual(["3"], [row["source_listing_id"] for row in payload["results"]])
        self.assertEqual(
            {"work_format_mismatch": 2},
            payload["removed_counts"],
        )
        self.assertEqual(
            [
                ["work_format_mismatch"],
                ["work_format_mismatch"],
            ],
            [row["decision_reasons"] for row in payload["filtered_out_results"]],
        )

    def test_explicit_onsite_work_format_overrides_raw_global_marker(self) -> None:
        # Arrange / Act
        payload = _process_payload(
            request=SearchRequest(query_variants=("QA",), work_formats=(WorkFormat.REMOTE,), remote_scopes=("global",)),
            raw_records=(
                _raw_record(
                    "1",
                    company="Acme",
                    remote_in_country=False,
                    remote_global=False,
                    raw={"remote_type": "global", "work_format": "onsite"},
                ),
            ),
            source_attempts=(_attempt_record(),),
        )

        # Assert
        self.assertEqual([], payload["results"])
        self.assertEqual({"work_format_mismatch": 1}, payload["removed_counts"])
        self.assertEqual("unknown", payload["filtered_out_results"][0]["remote_scope"])

    def test_marks_text_enrichment_required_from_source_attempt_diagnostics(self) -> None:
        # Arrange / Act
        payload = _process_payload(
            request=SearchRequest(
                query_variants=("QA",),
                work_formats=(WorkFormat.REMOTE,), remote_scopes=("global",),
            ),
            raw_records=(_raw_record("1", company="Acme"),),
            source_attempts=(
                _attempt_record(
                    requested=frozenset({SearchCriterion.QUERY, SearchCriterion.WORK_FORMATS}),
                    native=frozenset({SearchCriterion.QUERY}),
                    unsupported=frozenset({SearchCriterion.WORK_FORMATS}),
                    postprocess=frozenset({SearchCriterion.WORK_FORMATS}),
                ),
            ),
        )

        # Assert
        actions = {action["criterion"]: action for action in payload["source_criteria_plan"][0]["actions"]}
        self.assertEqual("text_enrichment_required", actions["work_formats"]["action"])
        self.assertTrue(actions["work_formats"]["requires_enrichment"])

    def test_filters_query_by_title_even_when_source_applied_native_query(self) -> None:
        # Arrange / Act
        payload = _process_payload(
            request=SearchRequest(query_variants=("QA",)),
            raw_records=(
                _raw_record("1", company="Staff", source="staff_am", title="QA Engineer"),
                _raw_record(
                    "2",
                    company="Coffee House Company",
                    source="staff_am",
                    title="Ֆրանչայզինգային սրճարանների որակի վերահսկման մասնագետ",
                ),
            ),
            source_attempts=(
                _attempt_record(
                    source="staff_am",
                    requested=frozenset({SearchCriterion.QUERY}),
                    native=frozenset({SearchCriterion.QUERY}),
                    structured=frozenset({SearchCriterion.QUERY}),
                    postprocess=frozenset({SearchCriterion.QUERY}),
                ),
            ),
        )

        # Assert
        self.assertEqual(1, payload["result_count"])
        self.assertEqual("QA Engineer", payload["results"][0]["title"])
        self.assertEqual({"query_mismatch": 1}, payload["removed_counts"])
        self.assertEqual([], payload["filtered_out_results"])

    def test_short_query_token_does_not_match_description_only_mentions(self) -> None:
        # Arrange / Act
        payload = _process_payload(
            request=SearchRequest(query_variants=("QA",)),
            raw_records=(
                _raw_record(
                    "1",
                    company="JetBrains",
                    source="career:jetbrains",
                    title="AI Lead",
                    description="Works with product managers, developers, and QA specialists.",
                ),
                _raw_record(
                    "2",
                    company="JetBrains",
                    source="career:jetbrains",
                    title="QA Engineer",
                    description="Tests product behavior.",
                ),
            ),
            source_attempts=(
                _attempt_record(
                    source="career:jetbrains",
                    source_type=SourceType.COMPANY_CAREER,
                    requested=frozenset({SearchCriterion.QUERY}),
                    native=frozenset(),
                    structured=frozenset({SearchCriterion.QUERY}),
                    postprocess=frozenset({SearchCriterion.QUERY}),
                ),
            ),
        )

        # Assert
        self.assertEqual(["QA Engineer"], [row["title"] for row in payload["results"]])

    def test_query_filter_matches_any_request_query_variant(self) -> None:
        # Arrange / Act
        payload = _process_payload(
            request=SearchRequest(query_variants=("Quality Assurance", "SDET")),
            raw_records=(
                _raw_record(
                    "1",
                    company="JetBrains",
                    source="career:jetbrains",
                    query_variant="Quality Assurance",
                    title="Senior SDET Engineer",
                ),
            ),
            source_attempts=(
                _attempt_record(
                    source="career:jetbrains",
                    source_type=SourceType.COMPANY_CAREER,
                    query_variant="Quality Assurance",
                    requested=frozenset({SearchCriterion.QUERY}),
                    native=frozenset(),
                    structured=frozenset({SearchCriterion.QUERY}),
                    postprocess=frozenset({SearchCriterion.QUERY}),
                ),
            ),
        )

        # Assert
        self.assertEqual(1, payload["result_count"])
        self.assertEqual(["Senior SDET Engineer"], [row["title"] for row in payload["results"]])

    def test_filtered_out_results_only_include_title_query_matches(self) -> None:
        # Arrange / Act
        payload = _process_payload(
            request=SearchRequest(query_variants=("QA",), work_formats=(WorkFormat.REMOTE,), remote_scopes=("global",)),
            raw_records=(
                _raw_record(
                    "1",
                    company="JetBrains",
                    source="career:jetbrains",
                    title="QA Engineer",
                    remote_in_country=False,
                    remote_global=False,
                ),
                _raw_record(
                    "2",
                    company="JetBrains",
                    source="career:jetbrains",
                    title="Account Manager",
                    remote_in_country=False,
                    remote_global=False,
                ),
            ),
            source_attempts=(
                _attempt_record(
                    source="career:jetbrains",
                    source_type=SourceType.COMPANY_CAREER,
                    requested=frozenset({SearchCriterion.QUERY, SearchCriterion.WORK_FORMATS}),
                    native=frozenset(),
                    structured=frozenset({SearchCriterion.QUERY, SearchCriterion.WORK_FORMATS}),
                    postprocess=frozenset({SearchCriterion.QUERY, SearchCriterion.WORK_FORMATS}),
                ),
            ),
        )

        # Assert
        self.assertEqual(0, payload["result_count"])
        self.assertEqual(
            {"query_mismatch": 1, "work_format_mismatch": 2},
            payload["removed_counts"],
        )
        self.assertEqual(["QA Engineer"], [row["title"] for row in payload["filtered_out_results"]])
        self.assertEqual(["work_format_mismatch"], payload["filtered_out_results"][0]["decision_reasons"])

    def test_grade_filter_keeps_unknown_native_grade(self) -> None:
        # Arrange / Act
        payload = _process_payload(
            request=SearchRequest(query_variants=("QA",), grades=(Grade.MIDDLE,)),
            raw_records=(
                _raw_record(
                    "1",
                    company="JetBrains",
                    source="career:jetbrains",
                    title="QA Engineer",
                    native_grade=None,
                ),
            ),
            source_attempts=(
                _attempt_record(
                    source="career:jetbrains",
                    source_type=SourceType.COMPANY_CAREER,
                    requested=frozenset({SearchCriterion.QUERY, SearchCriterion.GRADES}),
                    native=frozenset(),
                    structured=frozenset({SearchCriterion.QUERY}),
                    unsupported=frozenset({SearchCriterion.GRADES}),
                    postprocess=frozenset({SearchCriterion.QUERY}),
                ),
            ),
        )

        # Assert
        self.assertEqual(1, payload["result_count"])
        self.assertEqual("QA Engineer", payload["results"][0]["title"])
        self.assertEqual({}, payload["removed_counts"])

    def test_grade_filter_rejects_known_mismatched_native_grade(self) -> None:
        # Arrange / Act
        payload = _process_payload(
            request=SearchRequest(query_variants=("QA",), grades=(Grade.MIDDLE,)),
            raw_records=(
                _raw_record(
                    "1",
                    company="Talanto",
                    title="QA Engineer",
                    native_grade="senior",
                ),
            ),
            source_attempts=(
                _attempt_record(
                    requested=frozenset({SearchCriterion.QUERY, SearchCriterion.GRADES}),
                    native=frozenset({SearchCriterion.QUERY}),
                    structured=frozenset({SearchCriterion.GRADES}),
                    postprocess=frozenset({SearchCriterion.GRADES}),
                ),
            ),
        )

        # Assert
        self.assertEqual(0, payload["result_count"])
        self.assertEqual({"grade_mismatch": 1}, payload["removed_counts"])
        self.assertEqual(["QA Engineer"], [row["title"] for row in payload["filtered_out_results"]])

    def test_unknown_requested_filter_facts_do_not_remove_listing(self) -> None:
        # Arrange / Act
        payload = _process_payload(
            request=SearchRequest(
                query_variants=("QA",),
                salary_from=100000,
                published_since=date(2026, 1, 1),
                relocation=True,
                work_formats=(WorkFormat.REMOTE,),
                remote_scopes=("global",),
                vacancy_geographies=("city:Berlin",),
            ),
            raw_records=(
                _raw_record(
                    "1",
                    company="JetBrains",
                    source="career:jetbrains",
                    title="QA Engineer",
                    salary_min=None,
                    salary_max=None,
                    posted_at=None,
                    relocation=None,
                    remote_in_country=None,
                    remote_global=None,
                    country=None,
                    city=None,
                ),
            ),
            source_attempts=(
                _attempt_record(
                    source="career:jetbrains",
                    source_type=SourceType.COMPANY_CAREER,
                    requested=frozenset(
                        {
                            SearchCriterion.QUERY,
                            SearchCriterion.SALARY_FROM,
                            SearchCriterion.PUBLISHED_SINCE,
                            SearchCriterion.RELOCATION,
                            SearchCriterion.WORK_FORMATS,
                            SearchCriterion.VACANCY_GEOGRAPHIES,
                        }
                    ),
                    native=frozenset(),
                    structured=frozenset({SearchCriterion.QUERY}),
                    unsupported=frozenset(
                        {
                            SearchCriterion.SALARY_FROM,
                            SearchCriterion.PUBLISHED_SINCE,
                            SearchCriterion.RELOCATION,
                            SearchCriterion.WORK_FORMATS,
                            SearchCriterion.VACANCY_GEOGRAPHIES,
                        }
                    ),
                    postprocess=frozenset(
                        {
                            SearchCriterion.QUERY,
                            SearchCriterion.SALARY_FROM,
                            SearchCriterion.PUBLISHED_SINCE,
                            SearchCriterion.RELOCATION,
                            SearchCriterion.WORK_FORMATS,
                            SearchCriterion.VACANCY_GEOGRAPHIES,
                        }
                    ),
                ),
            ),
        )

        # Assert
        self.assertEqual(0, payload["result_count"])
        self.assertEqual({"work_format_mismatch": 1, "vacancy_geography_mismatch": 1}, payload["removed_counts"])

    def test_query_postprocess_does_not_match_description_only_role_mentions(self) -> None:
        # Arrange / Act
        payload = _process_payload(
            request=SearchRequest(query_variants=("Developer",)),
            raw_records=(
                _raw_record(
                    "1",
                    company="JetBrains",
                    source="career:jetbrains",
                    query_variant="Developer",
                    title="Account Manager (China)",
                    description="JetBrains creates developer tools and AI-powered IDEs.",
                    additional_sections={"responsibilities": "Work with developer tool customers."},
                    raw_text="Account Manager (China) JetBrains creates developer tools.",
                ),
                _raw_record(
                    "2",
                    company="JetBrains",
                    source="career:jetbrains",
                    query_variant="Developer",
                    title="Developer Advocate",
                    description="Works with technical communities.",
                ),
            ),
            source_attempts=(
                _attempt_record(
                    source="career:jetbrains",
                    source_type=SourceType.COMPANY_CAREER,
                    query_variant="Developer",
                    requested=frozenset({SearchCriterion.QUERY}),
                    native=frozenset(),
                    structured=frozenset({SearchCriterion.QUERY}),
                    postprocess=frozenset({SearchCriterion.QUERY}),
                ),
            ),
        )

        # Assert
        self.assertEqual(["Developer Advocate"], [row["title"] for row in payload["results"]])
        self.assertEqual({"query_mismatch": 1}, payload["removed_counts"])

    def test_fuzzy_query_postprocess_matches_title_tokens(self) -> None:
        # Arrange / Act
        payload = _process_payload(
            request=SearchRequest(query_variants=("QA",)),
            raw_records=(
                _raw_record(
                    "1",
                    company="VK",
                    source="career:vk",
                    title="Инженер по тестированию",
                    description="",
                    raw_text="",
                ),
                _raw_record("2", company="VK", source="career:vk", title="AQA", description="", raw_text=""),
                _raw_record(
                    "3",
                    company="VK",
                    source="career:vk",
                    title="Account Manager",
                    description="",
                    raw_text="",
                ),
            ),
            source_attempts=(
                _attempt_record(
                    source="career:vk",
                    source_type=SourceType.COMPANY_CAREER,
                    requested=frozenset({SearchCriterion.QUERY}),
                    native=frozenset(),
                    structured=frozenset({SearchCriterion.QUERY}),
                    postprocess=frozenset({SearchCriterion.QUERY}),
                ),
            ),
        )

        # Assert
        self.assertEqual(["AQA"], [row["title"] for row in payload["results"]])
        self.assertEqual({"query_mismatch": 2}, payload["removed_counts"])

    def test_fuzzy_query_postprocess_matches_russian_inflected_title(self) -> None:
        # Arrange / Act
        payload = _process_payload(
            request=SearchRequest(query_variants=("тестировщик",)),
            raw_records=(
                _raw_record(
                    "1",
                    company="VK",
                    source="career:vk",
                    query_variant="тестировщик",
                    title="Инженер по тестированию",
                    description="",
                    raw_text="",
                ),
            ),
            source_attempts=(
                _attempt_record(
                    source="career:vk",
                    source_type=SourceType.COMPANY_CAREER,
                    requested=frozenset({SearchCriterion.QUERY}),
                    native=frozenset(),
                    structured=frozenset({SearchCriterion.QUERY}),
                    postprocess=frozenset({SearchCriterion.QUERY}),
                ),
            ),
        )

        # Assert
        self.assertEqual(["Инженер по тестированию"], [row["title"] for row in payload["results"]])

    def test_fuzzy_city_filter_matches_case_and_inflection(self) -> None:
        # Arrange / Act
        payload = _process_payload(
            request=SearchRequest(query_variants=("QA",), vacancy_geographies=("city:москве",)),
            raw_records=(
                _raw_record("1", company="VK", city="Москва"),
                _raw_record("2", company="VK", city="Санкт-Петербург"),
            ),
            source_attempts=(
                _attempt_record(
                    requested=frozenset({SearchCriterion.QUERY, SearchCriterion.VACANCY_GEOGRAPHIES}),
                    structured=frozenset({SearchCriterion.VACANCY_GEOGRAPHIES}),
                    postprocess=frozenset({SearchCriterion.VACANCY_GEOGRAPHIES}),
                ),
            ),
        )

        # Assert
        self.assertEqual(["Москва"], [row["city"] for row in payload["results"]])
        self.assertEqual({"vacancy_geography_mismatch": 1}, payload["removed_counts"])

    def test_preserves_additional_sections_when_title_matches_query(self) -> None:
        # Arrange / Act
        payload = _process_payload(
            request=SearchRequest(query_variants=("automation testing",)),
            raw_records=(
                _raw_record(
                    "1",
                    company="JetBrains",
                    source="career:jetbrains",
                    query_variant="automation testing",
                    title="Automation Testing Engineer",
                    description="Build product services.",
                    additional_sections={
                        "responsibilities": "Own automation testing infrastructure.",
                        "benefits": "Relocation support.",
                    },
                ),
            ),
            source_attempts=(
                _attempt_record(
                    source="career:jetbrains",
                    source_type=SourceType.COMPANY_CAREER,
                    requested=frozenset({SearchCriterion.QUERY}),
                    native=frozenset(),
                    structured=frozenset({SearchCriterion.QUERY}),
                    postprocess=frozenset({SearchCriterion.QUERY}),
                ),
            ),
        )

        # Assert
        self.assertEqual(1, payload["result_count"])
        self.assertEqual(
            {
                "benefits": "Relocation support.",
                "responsibilities": "Own automation testing infrastructure.",
            },
            payload["results"][0]["additional_sections"],
        )

    def test_processes_unicode_line_separators_inside_fields(self) -> None:
        # Arrange
        description = "Modern QA role\u2028Second paragraph with test strategy."

        # Act
        payload = _process_payload(
            request=SearchRequest(query_variants=("QA",)),
            raw_records=(_raw_record("1", company="Acme", description=description),),
            source_attempts=(_attempt_record(),),
        )

        # Assert
        self.assertEqual(description, payload["results"][0]["description"])


def _raw_record(
    source_listing_id: str,
    *,
    company: str,
    source: str = "hh_ru",
    query_variant: str = "QA",
    title: str | None = None,
    description: str | None = None,
    additional_sections: dict[str, str] | None = None,
    raw_text: str | None = None,
    country: str | None = None,
    city: str | None = None,
    location_text: str | None = None,
    salary_min: int | None = None,
    salary_max: int | None = None,
    posted_at: str | None = None,
    remote_in_country: bool | None = None,
    remote_global: bool | None = None,
    relocation: bool | None = None,
    native_grade: str | None = None,
    description_availability: DescriptionAvailability = DescriptionAvailability.NOT_REQUESTED,
    detail_fetched: bool = False,
    detail_parse_error: str | None = None,
    raw: dict[str, object] | None = None,
) -> RawSearchRecord:
    raw_listing = listing(source, source_listing_id)
    effective_description = description if description is not None else (title or "Modern QA role")
    effective_raw = raw
    if effective_raw is None and remote_global is True:
        effective_raw = {"remote_type": "global"}
    raw_listing = replace(
        raw_listing,
        company=company,
        title=title or raw_listing.title,
        country=country,
        city=city,
        location_text=location_text,
        salary_min=salary_min,
        salary_max=salary_max,
        posted_at=posted_at,
        description=effective_description,
        additional_sections=additional_sections or {},
        remote_in_country=remote_in_country,
        remote_global=remote_global,
        relocation=relocation,
        native_grade=native_grade,
        raw_text=raw_text if raw_text is not None else effective_description,
        raw=effective_raw or {},
    )
    return RawSearchRecord(
        run_id="r-test",
        append_sequence=0,
        query_variant=query_variant,
        source=source,
        source_type=SourceType.COMPANY_CAREER if source.startswith("career:") else SourceType.AGGREGATOR,
        collected_at=datetime(2026, 6, 22, 10, 0, tzinfo=UTC),
        listing=raw_listing,
        description_availability=description_availability,
        detail_fetched=detail_fetched,
        detail_parse_error=detail_parse_error,
        source_url=f"https://example.test/{source}/search?q=QA",
    )


def _attempt_record(
    *,
    source: str = "hh_ru",
    source_type: SourceType = SourceType.AGGREGATOR,
    query_variant: str = "QA",
    requested: frozenset[SearchCriterion] = frozenset({SearchCriterion.QUERY}),
    native: frozenset[SearchCriterion] = frozenset({SearchCriterion.QUERY}),
    structured: frozenset[SearchCriterion] = frozenset(),
    unsupported: frozenset[SearchCriterion] = frozenset(),
    postprocess: frozenset[SearchCriterion] = frozenset(),
) -> SourceAttemptRecord:
    now = datetime(2026, 6, 22, 10, 0, tzinfo=UTC)
    return SourceAttemptRecord(
        source=source,
        source_type=source_type,
        query_variant=query_variant,
        attempt=1,
        outcome=SourceOutcome.SUCCESS,
        started_at=now,
        finished_at=now,
        elapsed_ms=0,
        source_limit=10,
        limit_reached=False,
        counts=AttemptCounts(raw_listings_written=1, pages_visited=1),
        criteria=CriteriaDiagnostics(
            requested=requested,
            native_applied=native,
            structured_evidence_available=structured,
            unsupported=unsupported,
            postprocess=postprocess,
        ),
        retry=RetryInfo(attempts=1, max_attempts=1, next_action=RetryNextAction.NONE),
        evidence=AttemptEvidence(),
    )


if __name__ == "__main__":
    unittest.main()
