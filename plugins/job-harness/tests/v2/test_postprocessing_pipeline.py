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
    RetryInfo,
    RetryNextAction,
    SearchCriterion,
    SearchRequest,
    SourceAttemptRecord,
    SourceOutcome,
    SourceType,
    TextExclusion,
)
from job_harness.v2.postprocessing import ResultTablePostProcessor
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
                "countries": [],
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
                "remote_global": None,
                "remote_in_country": None,
                "salary_from": None,
                "source_types": [],
                "sources": [],
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

    def test_marks_text_enrichment_required_from_source_attempt_diagnostics(self) -> None:
        # Arrange / Act
        payload = _process_payload(
            request=SearchRequest(
                query_variants=("QA",),
                remote_global=True,
            ),
            raw_records=(_raw_record("1", company="Acme"),),
            source_attempts=(
                _attempt_record(
                    requested=frozenset({SearchCriterion.QUERY, SearchCriterion.REMOTE_GLOBAL}),
                    native=frozenset({SearchCriterion.QUERY}),
                    unsupported=frozenset({SearchCriterion.REMOTE_GLOBAL}),
                    postprocess=frozenset({SearchCriterion.REMOTE_GLOBAL}),
                ),
            ),
        )

        # Assert
        actions = {
            action["criterion"]: action
            for action in payload["source_criteria_plan"][0]["actions"]
        }
        self.assertEqual("text_enrichment_required", actions["remote_global"]["action"])
        self.assertTrue(actions["remote_global"]["requires_enrichment"])

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

    def test_preserves_additional_sections_and_uses_them_for_text_matching(self) -> None:
        # Arrange / Act
        payload = _process_payload(
            request=SearchRequest(query_variants=("automation testing",)),
            raw_records=(
                _raw_record(
                    "1",
                    company="JetBrains",
                    source="career:jetbrains",
                    query_variant="automation testing",
                    title="Backend Engineer",
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
    city: str | None = None,
    remote_in_country: bool | None = None,
    description_availability: DescriptionAvailability = DescriptionAvailability.NOT_REQUESTED,
    detail_fetched: bool = False,
    detail_parse_error: str | None = None,
    raw: dict[str, object] | None = None,
) -> RawSearchRecord:
    raw_listing = listing(source, source_listing_id)
    effective_description = description if description is not None else (title or "Modern QA role")
    raw_listing = replace(
        raw_listing,
        company=company,
        title=title or raw_listing.title,
        city=city,
        description=effective_description,
        additional_sections=additional_sections or {},
        remote_in_country=remote_in_country,
        raw_text=raw_text if raw_text is not None else effective_description,
        raw=raw or {},
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
        query_variant="QA",
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
