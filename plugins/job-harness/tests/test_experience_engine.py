from __future__ import annotations

import json
import unittest
from pathlib import Path

from job_harness.experience_engine import (
    annotate_listing_experience,
    assess_listing_experience,
    parse_experience_levels,
)
from job_harness.models import JobListing
from job_harness.types import FilterSupport

_FIXTURE_PATH = Path(__file__).with_name("fixtures") / "experience_engine_real_world_samples.json"


class ExperienceEngineTest(unittest.TestCase):
    def test_real_world_fixture_contains_enough_estimated_samples(self) -> None:
        fixture = json.loads(_FIXTURE_PATH.read_text(encoding="utf-8"))

        estimated_cases = [
            case for case in fixture["cases"]
            if case["expected"]["origin"] == "estimated"
        ]

        self.assertGreaterEqual(len(estimated_cases), 15)
        self.assertTrue(
            all("native" not in case["id"] for case in estimated_cases)
        )
        self.assertTrue(
            all("experience" not in case["listing"] for case in estimated_cases)
        )

    def test_real_world_fixture_samples_keep_expected_assessments(self) -> None:
        fixture = json.loads(_FIXTURE_PATH.read_text(encoding="utf-8"))

        for case in fixture["cases"]:
            with self.subTest(case=case["id"]):
                listing = JobListing(**case["listing"], source=case["source"])
                expected = case["expected"]

                assessment = assess_listing_experience(
                    listing,
                    case["source"],
                    FilterSupport(case["support"]),
                )

                self.assertEqual(tuple(expected["levels"]), assessment.levels)
                self.assertEqual(expected["origin"], assessment.origin)
                self.assertEqual(expected["confidence"], assessment.confidence)
                for evidence in expected["evidence_contains"]:
                    self.assertIn(evidence, assessment.evidence)

    def test_native_valid_grade_wins_for_structured_source(self) -> None:
        listing = JobListing(
            title="QA Engineer",
            url="https://example.test/1",
            company="Acme",
            source="finder_work",
            experience="middle",
        )

        assessment = assess_listing_experience(
            listing, "finder_work", FilterSupport.CLIENT
        )

        self.assertEqual(("middle",), assessment.levels)
        self.assertEqual("native", assessment.origin)
        self.assertEqual("high", assessment.confidence)

    def test_unsupported_source_estimates_from_title(self) -> None:
        listing = JobListing(
            title="Middle QA Engineer",
            url="https://example.test/1",
            company="Acme",
            source="career:ibs",
        )

        assessment = assess_listing_experience(
            listing, "career:ibs", FilterSupport.UNSUPPORTED
        )

        self.assertEqual(("middle",), assessment.levels)
        self.assertEqual("estimated", assessment.origin)
        self.assertIn("title: middle", assessment.evidence)

    def test_non_native_source_ignores_scraper_experience_hint(self) -> None:
        listing = JobListing(
            title="Middle QA Engineer",
            url="https://example.test/1",
            company="Acme",
            source="hirehi",
            experience="senior",
        )

        assessment = assess_listing_experience(
            listing, "hirehi", FilterSupport.BEST_EFFORT
        )

        self.assertEqual(("middle",), assessment.levels)
        self.assertEqual("estimated", assessment.origin)
        self.assertEqual(("title: middle",), assessment.evidence)

    def test_no_data_is_unknown(self) -> None:
        listing = JobListing(
            title="QA Engineer",
            url="https://example.test/1",
            company="Acme",
            source="career:ibs",
        )

        assessment = assess_listing_experience(
            listing, "career:ibs", FilterSupport.UNSUPPORTED
        )

        self.assertEqual((), assessment.levels)
        self.assertEqual("unknown", assessment.origin)

    def test_conflicting_weak_data_is_unknown(self) -> None:
        listing = JobListing(
            title="Junior Senior QA Engineer",
            url="https://example.test/1",
            company="Acme",
            source="career:ibs",
        )

        assessment = assess_listing_experience(
            listing, "career:ibs", FilterSupport.UNSUPPORTED
        )

        self.assertEqual((), assessment.levels)
        self.assertEqual("unknown", assessment.origin)

    def test_explicit_multi_grade_keeps_both_levels(self) -> None:
        listing = JobListing(
            title="Middle/Senior QA Engineer",
            url="https://example.test/1",
            company="Acme",
            source="career:ibs",
        )

        assessment = assess_listing_experience(
            listing, "career:ibs", FilterSupport.UNSUPPORTED
        )

        self.assertEqual(("middle", "senior"), assessment.levels)
        self.assertEqual("estimated", assessment.origin)

    def test_vk_specialty_is_not_grade(self) -> None:
        listing = JobListing(
            title="QA Engineer",
            url="https://team.vk.company/vacancy/1/",
            company="VK",
            source="career:vk",
            raw={"specialty": "Quality Assurance"},
        )

        assessment = assess_listing_experience(
            listing, "career:vk", FilterSupport.UNSUPPORTED
        )

        self.assertEqual((), assessment.levels)
        self.assertEqual("unknown", assessment.origin)

    def test_parse_rejects_invalid_levels(self) -> None:
        with self.assertRaises(ValueError):
            parse_experience_levels(["midle"])

    def test_annotate_mutates_listing_with_assessment(self) -> None:
        listing = JobListing(
            title="Lead QA Engineer",
            url="https://example.test/1",
            company="Acme",
            source="src",
        )

        annotate_listing_experience(listing, "src", FilterSupport.UNSUPPORTED)

        self.assertEqual(["senior"], listing.experience_levels)
        self.assertEqual("estimated", listing.experience_origin)


if __name__ == "__main__":
    unittest.main()
