from __future__ import annotations

import re
import unittest
from pathlib import Path

from job_harness.v2.contracts.independent import ParserFailureKind

_REPO_ROOT = Path(__file__).resolve().parents[4]
_DOCS_ROOT = _REPO_ROOT / "docs"
_V2_ROOT = _REPO_ROOT / "plugins" / "job-harness" / "src" / "job_harness" / "v2"


class ArchitectureContractDocsTest(unittest.TestCase):
    def test_speculative_enrichment_contract_is_opportunistic_not_final_top_25(self) -> None:
        design = (
            _DOCS_ROOT / "superpowers" / "specs" / "2026-07-15-production-readiness-repair-design.md"
        ).read_text(encoding="utf-8")

        self.assertIn("opportunistic", design)
        self.assertIn("already started", design)
        self.assertNotIn("deterministic preliminary top 25", design)

    def test_request_retry_has_one_owner_and_no_legacy_policy_surface(self) -> None:
        python_sources = "\n".join(
            path.read_text(encoding="utf-8")
            for path in sorted(_V2_ROOT.rglob("*.py"))
        )
        config = (
            _V2_ROOT / "runtime" / "search_service_config.json"
        ).read_text(encoding="utf-8")

        self.assertFalse((_V2_ROOT / "runtime" / "retry.py").exists())
        for stale_name in (
            "RetryPolicy",
            "RetryInfo",
            "RetryNextAction",
            "RetryServiceConfig",
        ):
            self.assertIsNone(
                re.search(rf"\b(?:class|def)\s+{stale_name}\b", python_sources)
            )
        self.assertNotIn("def commit_retry", python_sources)
        self.assertNotIn("retry_wait", python_sources)
        self.assertIn('"request_retry"', config)
        self.assertNotIn('"retry"', config)

    def test_parser_failure_taxonomy_matches_the_runtime_contract(self) -> None:
        architecture = (
            _DOCS_ROOT / "v2-to-be-scraper-contract-architecture.md"
        ).read_text(encoding="utf-8")
        failure_section = architecture.split("ParserFailure = {", maxsplit=1)[1].split(
            "Operational diagnostics", maxsplit=1
        )[0]

        for failure_kind in ParserFailureKind:
            self.assertIn(f'| "{failure_kind.value}"', failure_section)

        for stale_name in ("timeout", "network", "parse", "invalid_output", "resource"):
            self.assertNotIn(f'| "{stale_name}"', failure_section)

    def test_diagrams_only_claim_implemented_resource_gate_state(self) -> None:
        architecture = (
            _DOCS_ROOT / "v2-to-be-scraper-contract-architecture.md"
        ).read_text(encoding="utf-8")
        flow = (_DOCS_ROOT / "v2-to-be-scraper-contract-flow.md").read_text(encoding="utf-8")
        event_graph = (
            _DOCS_ROOT / "v2-to-be-scraper-contract-event-graph.dot"
        ).read_text(encoding="utf-8")
        database_graph = (
            _DOCS_ROOT / "v2-to-be-scraper-contract-db-schema.dot"
        ).read_text(encoding="utf-8")
        combined = "\n".join((architecture, flow, event_graph, database_graph))

        for implemented_field in (
            "max_concurrency",
            "min_interval_seconds",
            "lease_seconds",
            "next_start_at",
            "updated_at",
        ):
            self.assertIn(implemented_field, database_graph)

        for unsupported_claim in (
            "ManagedGraphExecutor",
            "network_telemetry_jsonl",
            "Buffered network telemetry",
            "breaker_state",
            "breaker_until",
        ):
            self.assertNotIn(unsupported_claim, combined)

    def test_retry_lease_and_completion_docs_match_the_runtime_contract(self) -> None:
        paths = (
            _DOCS_ROOT / "search-system-spec.md",
            _DOCS_ROOT / "v2-to-be-scraper-contract-architecture.md",
            _DOCS_ROOT / "v2-to-be-scraper-contract-flow.md",
            _DOCS_ROOT / "v2-to-be-scraper-contract-event-graph.dot",
            _DOCS_ROOT / "v2-to-be-scraper-contract-db-schema.dot",
        )
        combined = "\n".join(path.read_text(encoding="utf-8") for path in paths)

        for stale_claim in (
            "retry_wait",
            "retryable: boolean",
            "<TR><TD>retryable</TD>",
            "parser-call retry",
            "maxAttempts` + `backoffSeconds",
            "lease_expired",
            "Retries are policy-driven and per-source.",
            '"next_action": "none"',
        ):
            self.assertNotIn(stale_claim, combined)

        for implemented_claim in (
            "RequestRetryPolicy",
            "retry_backoff",
            "resource_pacing",
            "worker_lost",
            "30-second lease",
            "10-second heartbeat",
            "execution_quality",
            "source_coverage",
        ):
            self.assertIn(implemented_claim, combined)


if __name__ == "__main__":
    unittest.main()
