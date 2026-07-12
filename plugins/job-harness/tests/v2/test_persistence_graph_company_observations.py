from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from job_harness.v2.contracts import (
    CompanyProfileInput,
    CompanyProfileOutput,
    CompanyProfileResult,
    CompanySiteInput,
    CompanySiteOutput,
    CompanySiteResult,
    DiscoveredEndpoint,
    InvocationScope,
    LeasedParserInvocation,
    ParserInvocationSpec,
    ParserManifest,
    ParserType,
    SingletonResultOutcome,
    TaskClass,
    TransportKind,
)
from job_harness.v2.persistence.graph_repository import SqliteGraphRepository


def _manifest(parser_type: ParserType) -> ParserManifest:
    parser_id = {
        ParserType.COMPANY_PROFILE: "hh.company-profile",
        ParserType.COMPANY_SITE: "generic.company-site",
    }[parser_type]
    return ParserManifest(
        parser_id=parser_id,
        parser_type=parser_type,
        implementation_version="1.0",
        input_schema_id=f"{parser_id}.input.v1",
        output_schema_id=f"{parser_id}.output.v1",
        transport=TransportKind.HTTP,
        provider_ids=("hh",) if parser_type == ParserType.COMPANY_PROFILE else ("web",),
        supported_url_patterns=(r"https://.*",),
        output_facts=("officialSiteUrl", "careerEndpoints"),
        invocation_scope=InvocationScope.STATELESS_UNIT,
    )


class GraphCompanyObservationTest(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self._temporary_directory.cleanup)
        self.database_path = Path(self._temporary_directory.name) / "run.sqlite"
        self.repository = SqliteGraphRepository(self.database_path)
        self.addCleanup(self.repository.close)
        self.execution_id = self.repository.create_execution(
            run_id="r-test",
            intent={"kind": "standalone"},
            append_sequence=0,
            policy_version="policy-v1",
            runtime_config_version="runtime-v1",
            deadline_at=1000.0,
        )

    def test_profile_observation_creates_strong_claims_and_endpoint(self) -> None:
        manifest = _manifest(ParserType.COMPANY_PROFILE)
        invocation = self._lease(
            manifest=manifest,
            parser_input=CompanyProfileInput(
                target_provider_id="hh",
                company_profile_url="https://hh.ru/employer/10",
                source_company_id="10",
            ),
            task_class=TaskClass.PROFILE,
            task_key="profile-10",
        )
        result = CompanyProfileResult(
            outcome=SingletonResultOutcome.SUCCESS,
            item=CompanyProfileOutput(
                target_provider_id="hh",
                profile_url="https://hh.ru/employer/10",
                source_company_id="10",
                company_name="Example",
                description="Employer profile",
                industry="Software",
                size_text="100-500",
                locations=(),
                official_site_url="https://example.com",
                career_endpoints=(
                    DiscoveredEndpoint(
                        kind="career_page",
                        url="https://example.com/careers",
                        provider_hint=None,
                        confidence="confirmed",
                        discovery_method="explicit_link",
                    ),
                ),
                contacts=(),
                social_links=(),
            ),
        )

        self.repository.commit_profile_result(invocation, result, manifest, now=101.0)

        self.assertEqual(self._scalar("SELECT COUNT(*) FROM companies"), 1)
        self.assertEqual(self._scalar("SELECT COUNT(*) FROM company_profile_observations"), 1)
        self.assertEqual(self._scalar("SELECT COUNT(*) FROM company_identity_claims"), 3)
        self.assertEqual(self._scalar("SELECT COUNT(*) FROM discovered_endpoints"), 1)
        self.assertEqual(
            self._scalar("SELECT event_type FROM domain_events"),
            "company_profile_observation_stored",
        )

    def test_site_observation_uses_verified_domain_as_standalone_company_identity(self) -> None:
        manifest = _manifest(ParserType.COMPANY_SITE)
        invocation = self._lease(
            manifest=manifest,
            parser_input=CompanySiteInput(site_url="https://www.example.com/about"),
            task_class=TaskClass.SITE,
            task_key="site-example",
        )
        result = CompanySiteResult(
            outcome=SingletonResultOutcome.SUCCESS,
            item=CompanySiteOutput(
                canonical_site_url="https://example.com",
                company_name="Example",
                contacts=(),
                social_links=(),
                career_endpoints=(),
            ),
        )

        self.repository.commit_site_result(invocation, result, manifest, now=101.0)

        self.assertEqual(self._scalar("SELECT COUNT(*) FROM companies"), 1)
        self.assertEqual(self._scalar("SELECT COUNT(*) FROM company_site_observations"), 1)
        self.assertEqual(
            self._scalar("SELECT claim_type FROM company_identity_claims"),
            "verified_domain",
        )
        self.assertEqual(
            self._scalar("SELECT claim_value FROM company_identity_claims"),
            "example.com",
        )

    def test_profile_official_site_and_site_observation_share_one_company(self) -> None:
        profile_manifest = _manifest(ParserType.COMPANY_PROFILE)
        profile = self._lease(
            manifest=profile_manifest,
            parser_input=CompanyProfileInput(
                target_provider_id="hh",
                company_profile_url="https://hh.ru/employer/10",
                source_company_id="10",
            ),
            task_class=TaskClass.PROFILE,
            task_key="profile-10",
        )
        self.repository.commit_profile_result(
            profile,
            CompanyProfileResult(
                outcome=SingletonResultOutcome.SUCCESS,
                item=CompanyProfileOutput(
                    target_provider_id="hh",
                    profile_url="https://hh.ru/employer/10",
                    source_company_id="10",
                    company_name="Example",
                    description=None,
                    industry=None,
                    size_text=None,
                    locations=(),
                    official_site_url="https://example.com",
                    career_endpoints=(),
                    contacts=(),
                    social_links=(),
                ),
            ),
            profile_manifest,
            now=101.0,
        )
        site_manifest = _manifest(ParserType.COMPANY_SITE)
        site = self._lease(
            manifest=site_manifest,
            parser_input=CompanySiteInput(site_url="https://example.com"),
            task_class=TaskClass.SITE,
            task_key="site-example",
        )
        self.repository.commit_site_result(
            site,
            CompanySiteResult(
                outcome=SingletonResultOutcome.SUCCESS,
                item=CompanySiteOutput(
                    canonical_site_url="https://example.com",
                    company_name="Example",
                    contacts=(),
                    social_links=(),
                    career_endpoints=(),
                ),
            ),
            site_manifest,
            now=103.0,
        )

        self.assertEqual(self._scalar("SELECT COUNT(*) FROM companies"), 1)
        self.assertEqual(
            self._scalar("SELECT COUNT(DISTINCT company_id) FROM company_identity_claims"),
            1,
        )
        self.assertEqual(
            self._scalar(
                "SELECT COUNT(DISTINCT company_id) FROM ("
                "SELECT company_id FROM company_profile_observations UNION ALL "
                "SELECT company_id FROM company_site_observations)"
            ),
            1,
        )

    def _lease(
        self,
        *,
        manifest: ParserManifest,
        parser_input: CompanyProfileInput | CompanySiteInput,
        task_class: TaskClass,
        task_key: str,
    ) -> LeasedParserInvocation:
        self.repository.enqueue_invocation(
            ParserInvocationSpec(
                execution_id=self.execution_id,
                source_plan_id=None,
                parent_invocation_id=None,
                cause_event_id=None,
                parser_ref=manifest.ref,
                parser_type=manifest.parser_type,
                input_schema_id=manifest.input_schema_id,
                parser_input=parser_input,
                task_class=task_class,
                task_key=task_key,
                available_at=0.0,
                reserved_collection_units=None,
            )
        )
        return self.repository.lease_ready_invocations(
            execution_id=self.execution_id,
            owner_id="worker",
            limit=1,
            lease_seconds=30.0,
            now=100.0,
        )[0]

    def _scalar(self, query: str) -> object:
        with closing(sqlite3.connect(self.database_path)) as connection:
            row = connection.execute(query).fetchone()
        if row is None:
            self.fail("expected one row")
        return row[0]


if __name__ == "__main__":
    unittest.main()
