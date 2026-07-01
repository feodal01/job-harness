from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

from tests.v2._support.contract_runtime import listing

from job_harness.v2.contracts import (
    AttemptCounts,
    AttemptEvidence,
    CriteriaDiagnostics,
    DescriptionAvailability,
    RawSearchRecord,
    RemoteMode,
    RetryInfo,
    RetryNextAction,
    SearchCriterion,
    SearchRequest,
    SourceAttemptRecord,
    SourceOutcome,
    SourceType,
    TextExclusion,
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
                "cities": [],
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
                "hybrid_ok": False,
                "office_ok": False,
                "published_since": None,
                "query_variants": ["QA"],
                "relocation": None,
                "remote_mode": None,
                "salary_from": None,
                "source_types": [],
                "sources": [],
                "vacancy_geographies": [],
                "work_from_geographies": [],
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
                remote_mode=RemoteMode.COMPATIBLE_REMOTE,
                work_from_geographies=("UK",),
                vacancy_geographies=("UK",),
                hybrid_ok=True,
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
        self.assertEqual("hybrid", payload["results"][0]["remote_scope"])
        self.assertEqual("hybrid", payload["filtered_out_results"][0]["display_work_format"])
        self.assertEqual(["hybrid"], payload["filtered_out_results"][0]["work_formats"])
        self.assertEqual("hybrid", payload["filtered_out_results"][0]["remote_scope"])
        self.assertEqual({"hybrid_geography_mismatch": 1, "vacancy_geography_mismatch": 1}, payload["removed_counts"])

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
        self.assertEqual(["office", "remote", "office"], [
            row["display_work_format"] for row in payload["results"]
        ])
        self.assertEqual([["office"], ["remote"], ["office"]], [
            row["work_formats"] for row in payload["results"]
        ])

    def test_remote_work_format_wins_when_source_lists_multiple_workplace_options(self) -> None:
        # Arrange / Act
        payload = _process_payload(
            request=SearchRequest(
                query_variants=("QA",),
                remote_mode=RemoteMode.COMPATIBLE_REMOTE,
                work_from_geographies=("europe",),
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
            [["remote_eligibility_mismatch"]],
            [row["decision_reasons"] for row in payload["filtered_out_results"]],
        )
        self.assertEqual("remote", payload["results"][0]["display_work_format"])
        self.assertEqual(["remote"], payload["results"][0]["work_formats"])
        self.assertEqual("region:EU", payload["results"][0]["remote_scope"])
        self.assertEqual("remote", payload["filtered_out_results"][0]["display_work_format"])
        self.assertEqual(["remote"], payload["filtered_out_results"][0]["work_formats"])
        self.assertEqual("country:US", payload["filtered_out_results"][0]["remote_scope"])

    def test_linkedin_remote_tag_drives_remote_scope_before_onsite_booleans(self) -> None:
        # Arrange / Act
        payload = _process_payload(
            request=SearchRequest(
                query_variants=("QA",),
                remote_mode=RemoteMode.COMPATIBLE_REMOTE,
                work_from_geographies=("US",),
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
        self.assertEqual({"remote_eligibility_mismatch": 1}, payload["removed_counts"])

    def test_hybrid_ok_accepts_linkedin_hybrid_tag_with_matching_geography(self) -> None:
        # Arrange / Act
        payload = _process_payload(
            request=SearchRequest(
                query_variants=("QA",),
                remote_mode=RemoteMode.COMPATIBLE_REMOTE,
                work_from_geographies=("CY",),
                hybrid_ok=True,
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
        self.assertEqual("hybrid", payload["results"][0]["remote_scope"])
        self.assertEqual({"hybrid_geography_mismatch": 1}, payload["removed_counts"])

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
            request=SearchRequest(query_variants=("QA",), vacancy_geographies=("CY",)),
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
            request=SearchRequest(query_variants=("QA",), vacancy_geographies=("RU",)),
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
            request=SearchRequest(query_variants=("QA",), vacancy_geographies=("PL",)),
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
            request=SearchRequest(query_variants=("QA",), vacancy_geographies=("RU",)),
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

    def test_compatible_remote_matches_global_and_intersecting_work_from_scope(self) -> None:
        # Arrange / Act
        payload = _process_payload(
            request=SearchRequest(
                query_variants=("QA",),
                remote_mode=RemoteMode.COMPATIBLE_REMOTE,
                work_from_geographies=("europe",),
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
        self.assertEqual({"remote_eligibility_mismatch": 2}, payload["removed_counts"])
        self.assertEqual(
            ["country:US", "country:RU"],
            [row["remote_scope"] for row in payload["filtered_out_results"]],
        )

    def test_remote_scope_prefers_explicit_remote_locations_over_vacancy_locations(self) -> None:
        # Arrange / Act
        payload = _process_payload(
            request=SearchRequest(
                query_variants=("QA",),
                remote_mode=RemoteMode.COMPATIBLE_REMOTE,
                work_from_geographies=("BY",),
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
        self.assertEqual({"remote_eligibility_mismatch": 1}, payload["removed_counts"])
        self.assertEqual("country:RU", payload["filtered_out_results"][0]["remote_scope"])
        self.assertEqual("BY, RU", payload["filtered_out_results"][0]["country"])

    def test_remote_in_country_uses_city_derived_country_scope(self) -> None:
        # Arrange / Act
        payload = _process_payload(
            request=SearchRequest(
                query_variants=("QA",),
                remote_mode=RemoteMode.COMPATIBLE_REMOTE,
                work_from_geographies=("RU",),
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
                remote_mode=RemoteMode.COMPATIBLE_REMOTE,
                work_from_geographies=("ES",),
            ),
            raw_records=(
                _raw_record(
                    "1",
                    company="Collectly",
                    source="career:collectly",
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

    def test_remote_timezone_hint_without_geography_keeps_unknown_scope(self) -> None:
        # Arrange / Act
        payload = _process_payload(
            request=SearchRequest(
                query_variants=("Engineer",),
                remote_mode=RemoteMode.GLOBAL_REMOTE_ONLY,
            ),
            raw_records=(
                _raw_record(
                    "1",
                    company="Collectly",
                    source="career:collectly",
                    title="Senior DevOps Engineer (remote from GMT-7 to GMT+4 timezones)",
                    remote_in_country=None,
                    remote_global=None,
                    raw={},
                ),
            ),
            source_attempts=(_attempt_record(source="career:collectly"),),
        )

        # Assert
        self.assertEqual([], payload["results"])
        self.assertEqual("remote", payload["filtered_out_results"][0]["display_work_format"])
        self.assertEqual("unknown", payload["filtered_out_results"][0]["remote_scope"])
        self.assertEqual({"remote_global_unknown": 1}, payload["removed_counts"])

    def test_remote_eu_locations_use_region_scope(self) -> None:
        # Arrange / Act
        payload = _process_payload(
            request=SearchRequest(
                query_variants=("Engineer",),
                remote_mode=RemoteMode.COMPATIBLE_REMOTE,
                work_from_geographies=("EU",),
            ),
            raw_records=(
                _raw_record(
                    "1",
                    company="AppFollow",
                    source="career:appfollow",
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
                remote_mode=RemoteMode.COMPATIBLE_REMOTE,
                work_from_geographies=("PT",),
            ),
            raw_records=(
                _raw_record(
                    "1",
                    company="Acme",
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

    def test_physical_city_listing_infers_country_for_hybrid_filtering(self) -> None:
        # Arrange / Act
        payload = _process_payload(
            request=SearchRequest(
                query_variants=("Engineer",),
                remote_mode=RemoteMode.COMPATIBLE_REMOTE,
                work_from_geographies=("GB",),
                hybrid_ok=True,
            ),
            raw_records=(
                _raw_record(
                    "1",
                    company="Acme",
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
        self.assertEqual("hybrid", payload["results"][0]["remote_scope"])

    def test_source_offices_contribute_vacancy_country_but_not_remote_scope(self) -> None:
        # Arrange / Act
        payload = _process_payload(
            request=SearchRequest(
                query_variants=("Campus Ambassador",),
                remote_mode=RemoteMode.COMPATIBLE_REMOTE,
                work_from_geographies=("GB",),
                vacancy_geographies=("europe",),
            ),
            raw_records=(
                _raw_record(
                    "1",
                    company="JetBrains",
                    source="career:jetbrains",
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
        self.assertEqual(["remote_eligibility_unknown"], row["decision_reasons"])

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

    def test_vacancy_geography_keeps_global_remote_scope_for_remote_search(self) -> None:
        # Arrange / Act
        payload = _process_payload(
            request=SearchRequest(
                query_variants=("QA",),
                remote_mode=RemoteMode.COMPATIBLE_REMOTE,
                work_from_geographies=("europe",),
                vacancy_geographies=("europe",),
            ),
            raw_records=(
                _raw_record("1", company="Acme", country="US", remote_in_country=True, remote_global=True),
                _raw_record("2", company="Acme", country="PL", remote_in_country=True, remote_global=False),
                _raw_record("3", company="Acme", country=None, remote_in_country=True, remote_global=True),
            ),
            source_attempts=(_attempt_record(),),
        )

        # Assert
        self.assertEqual(["1", "2", "3"], [row["source_listing_id"] for row in payload["results"]])
        self.assertEqual({}, payload["removed_counts"])

    def test_work_from_uk_and_vacancy_europe_keeps_global_and_mixed_scope_remote(self) -> None:
        # Arrange / Act
        payload = _process_payload(
            request=SearchRequest(
                query_variants=("QA",),
                remote_mode=RemoteMode.COMPATIBLE_REMOTE,
                work_from_geographies=("UK",),
                vacancy_geographies=("europe",),
                hybrid_ok=True,
                office_ok=True,
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
        self.assertEqual(["1", "4"], [row["source_listing_id"] for row in payload["results"]])
        self.assertEqual(
            {
                "remote_eligibility_mismatch": 2,
                "hybrid_geography_mismatch": 2,
                "office_geography_mismatch": 1,
            },
            payload["removed_counts"],
        )
        self.assertEqual(
            ["country:PL", "region:EU", "hybrid", "onsite", "hybrid"],
            [row["remote_scope"] for row in payload["filtered_out_results"]],
        )

    def test_work_from_uk_and_vacancy_europe_keeps_remote_country_gb_inside_multi_country_listing(self) -> None:
        # Arrange / Act
        payload = _process_payload(
            request=SearchRequest(
                query_variants=("QA",),
                remote_mode=RemoteMode.COMPATIBLE_REMOTE,
                work_from_geographies=("UK",),
                vacancy_geographies=("europe",),
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

    def test_work_from_uk_and_vacancy_uk_accepts_requested_physical_and_remote_formats(self) -> None:
        # Arrange / Act
        payload = _process_payload(
            request=SearchRequest(
                query_variants=("QA",),
                remote_mode=RemoteMode.COMPATIBLE_REMOTE,
                work_from_geographies=("UK",),
                vacancy_geographies=("UK",),
                hybrid_ok=True,
                office_ok=True,
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
        self.assertEqual(["1", "2", "3", "4"], [row["source_listing_id"] for row in payload["results"]])
        self.assertEqual({}, payload["removed_counts"])

    def test_work_from_uk_and_vacancy_uk_remote_only_accepts_in_country_and_global_remote(self) -> None:
        # Arrange / Act
        payload = _process_payload(
            request=SearchRequest(
                query_variants=("QA",),
                remote_mode=RemoteMode.COMPATIBLE_REMOTE,
                work_from_geographies=("UK",),
                vacancy_geographies=("UK",),
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
        self.assertEqual(["1", "2"], [row["source_listing_id"] for row in payload["results"]])
        self.assertEqual({"remote_eligibility_mismatch": 2}, payload["removed_counts"])

    def test_remote_modes_distinguish_unknown_global_and_non_remote_evidence(self) -> None:
        # Arrange / Act
        global_payload = _process_payload(
            request=SearchRequest(query_variants=("QA",), remote_mode=RemoteMode.GLOBAL_REMOTE_ONLY),
            raw_records=(
                _raw_record("1", company="Acme", remote_in_country=True, remote_global=True),
                _raw_record("2", company="Acme", remote_in_country=True, remote_global=False, country="PL"),
                _raw_record("3", company="Acme"),
            ),
            source_attempts=(_attempt_record(),),
        )
        non_remote_payload = _process_payload(
            request=SearchRequest(query_variants=("QA",), remote_mode=RemoteMode.NON_REMOTE_ONLY),
            raw_records=(
                _raw_record("1", company="Acme", remote_in_country=False, remote_global=False),
                _raw_record("2", company="Acme", remote_in_country=True, remote_global=True),
                _raw_record("3", company="Acme"),
            ),
            source_attempts=(_attempt_record(),),
        )

        # Assert
        self.assertEqual(["1"], [row["source_listing_id"] for row in global_payload["results"]])
        self.assertEqual(
            {"remote_global_mismatch": 1, "remote_global_unknown": 1},
            global_payload["removed_counts"],
        )
        self.assertEqual(["1"], [row["source_listing_id"] for row in non_remote_payload["results"]])
        self.assertEqual(
            {"remote_mismatch": 1, "remote_scope_unknown": 1},
            non_remote_payload["removed_counts"],
        )

    def test_bare_remote_without_global_evidence_is_unknown_for_global_remote_only(self) -> None:
        # Arrange / Act
        payload = _process_payload(
            request=SearchRequest(query_variants=("QA",), remote_mode=RemoteMode.GLOBAL_REMOTE_ONLY),
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
        self.assertEqual([], payload["results"])
        self.assertEqual({"remote_global_unknown": 1}, payload["removed_counts"])
        self.assertEqual("unknown", payload["filtered_out_results"][0]["remote_scope"])

    def test_hybrid_and_office_flags_accept_physical_formats_in_work_from_geography(self) -> None:
        # Arrange / Act
        payload = _process_payload(
            request=SearchRequest(
                query_variants=("QA",),
                remote_mode=RemoteMode.COMPATIBLE_REMOTE,
                work_from_geographies=("RU",),
                hybrid_ok=True,
                office_ok=True,
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
        self.assertEqual(["1", "2", "3"], [row["source_listing_id"] for row in payload["results"]])
        self.assertEqual(
            {"hybrid_geography_mismatch": 1, "office_geography_unknown": 1},
            payload["removed_counts"],
        )
        self.assertEqual("hybrid", payload["results"][1]["display_work_format"])
        self.assertEqual(["hybrid"], payload["results"][1]["work_formats"])
        self.assertEqual(
            [["hybrid_geography_mismatch"], ["office_geography_unknown"]],
            [row["decision_reasons"] for row in payload["filtered_out_results"]],
        )

    def test_hybrid_and_office_do_not_bypass_remote_policy_without_flags(self) -> None:
        # Arrange / Act
        payload = _process_payload(
            request=SearchRequest(
                query_variants=("QA",),
                remote_mode=RemoteMode.COMPATIBLE_REMOTE,
                work_from_geographies=("RU",),
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
            {"remote_eligibility_mismatch": 2},
            payload["removed_counts"],
        )

    def test_global_remote_only_removes_physical_formats(self) -> None:
        # Arrange / Act
        payload = _process_payload(
            request=SearchRequest(
                query_variants=("QA",),
                remote_mode=RemoteMode.GLOBAL_REMOTE_ONLY,
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
            {"remote_global_mismatch": 2},
            payload["removed_counts"],
        )
        self.assertEqual(
            [
                ["remote_global_mismatch"],
                ["remote_global_mismatch"],
            ],
            [row["decision_reasons"] for row in payload["filtered_out_results"]],
        )

    def test_explicit_onsite_work_format_overrides_raw_global_marker(self) -> None:
        # Arrange / Act
        payload = _process_payload(
            request=SearchRequest(query_variants=("QA",), remote_mode=RemoteMode.GLOBAL_REMOTE_ONLY),
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
        self.assertEqual({"remote_global_mismatch": 1}, payload["removed_counts"])
        self.assertEqual("onsite", payload["filtered_out_results"][0]["remote_scope"])

    def test_marks_text_enrichment_required_from_source_attempt_diagnostics(self) -> None:
        # Arrange / Act
        payload = _process_payload(
            request=SearchRequest(
                query_variants=("QA",),
                remote_mode=RemoteMode.GLOBAL_REMOTE_ONLY,
            ),
            raw_records=(_raw_record("1", company="Acme"),),
            source_attempts=(
                _attempt_record(
                    requested=frozenset({SearchCriterion.QUERY, SearchCriterion.REMOTE_MODE}),
                    native=frozenset({SearchCriterion.QUERY}),
                    unsupported=frozenset({SearchCriterion.REMOTE_MODE}),
                    postprocess=frozenset({SearchCriterion.REMOTE_MODE}),
                ),
            ),
        )

        # Assert
        actions = {action["criterion"]: action for action in payload["source_criteria_plan"][0]["actions"]}
        self.assertEqual("text_enrichment_required", actions["remote_mode"]["action"])
        self.assertTrue(actions["remote_mode"]["requires_enrichment"])

    def test_filters_query_when_source_did_not_apply_native_query(self) -> None:
        # Arrange / Act
        payload = _process_payload(
            request=SearchRequest(query_variants=("QA",)),
            raw_records=(
                _raw_record("1", company="JetBrains", source="career:jetbrains", title="QA Engineer"),
                _raw_record("2", company="JetBrains", source="career:jetbrains", title="Account Manager"),
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
        self.assertEqual("QA Engineer", payload["results"][0]["title"])
        self.assertEqual({"query_mismatch": 1}, payload["removed_counts"])

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
            request=SearchRequest(query_variants=("QA",), cities=("москве",)),
            raw_records=(
                _raw_record("1", company="VK", city="Москва"),
                _raw_record("2", company="VK", city="Санкт-Петербург"),
            ),
            source_attempts=(
                _attempt_record(
                    requested=frozenset({SearchCriterion.QUERY, SearchCriterion.CITIES}),
                    structured=frozenset({SearchCriterion.CITIES}),
                    postprocess=frozenset({SearchCriterion.CITIES}),
                ),
            ),
        )

        # Assert
        self.assertEqual(["Москва"], [row["city"] for row in payload["results"]])
        self.assertEqual({"city_mismatch": 1}, payload["removed_counts"])

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
    remote_in_country: bool | None = None,
    remote_global: bool | None = None,
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
        description=effective_description,
        additional_sections=additional_sections or {},
        remote_in_country=remote_in_country,
        remote_global=remote_global,
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
