#!/usr/bin/env python3
"""Dedicated verification gate for the v2 contract-first engine."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLUGIN_DIR = ROOT / "plugins" / "job-harness"
PLUGIN_ARG = PLUGIN_DIR.relative_to(ROOT).as_posix()
V2_RUFF_RULES = "E,F,W,I,B,UP,C4,SIM,RET,ARG,PLC,PLE,PLR"
V2_RUFF_IGNORES = "PLR0911,PLR0913"
V2_MYPY_STRICT_FLAGS = (
    "--disallow-any-generics",
    "--disallow-subclassing-any",
    "--disallow-untyped-calls",
    "--disallow-untyped-defs",
    "--disallow-incomplete-defs",
    "--check-untyped-defs",
    "--no-implicit-optional",
    "--warn-redundant-casts",
    "--warn-unused-ignores",
    "--warn-return-any",
    "--strict-equality",
    "--extra-checks",
    "--no-error-summary",
)
CANONICAL_OUTCOMES = {
    "success",
    "no_results",
    "partial_success",
    "skipped_by_policy",
    "cancelled",
    "source_timeout",
    "run_timeout",
    "blocked",
    "rate_limited",
    "http_client_error",
    "http_server_error",
    "network_error",
    "parse_error",
    "invalid_source_output",
    "resource_failure",
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--skip-live",
        action="store_true",
        help="Run only deterministic v2 checks. The default includes live e2e smoke.",
    )
    args = parser.parse_args()

    checks = [
        _run_v2_lint,
        _run_v2_types,
        _run_v2_architecture_boundary_tests,
        _run_v2_search_contract_tests,
        _run_v2_source_catalog_tests,
        _run_v2_scraper_fixture_tests,
        _run_v2_transport_tests,
        _run_v2_orchestrator_tests,
        _run_v2_persistence_tests,
        _run_v2_postprocessing_tests,
        _run_v2_application_cli_tests,
        _run_v2_tests,
    ]
    if not args.skip_live:
        checks.append(_run_v2_live_e2e)

    for check in checks:
        code = check()
        if code != 0:
            return code
    return 0


def _run_v2_lint() -> int:
    return _run(
        [
            "uv",
            "--directory",
            PLUGIN_ARG,
            "run",
            "ruff",
            "check",
            "--select",
            V2_RUFF_RULES,
            "--ignore",
            V2_RUFF_IGNORES,
            "src/job_harness/v2",
            "tests/v2",
            "../../scripts/verify_v2.py",
        ]
    )


def _run_v2_types() -> int:
    return _run(
        [
            "uv",
            "--directory",
            PLUGIN_ARG,
            "run",
            "mypy",
            "src/job_harness/v2",
            "tests/v2",
            "../../scripts/verify_v2.py",
            *V2_MYPY_STRICT_FLAGS,
        ]
    )


def _run_v2_tests() -> int:
    return _run_unittest_discover("v2 full deterministic test sweep", "test_*.py")


def _run_v2_architecture_boundary_tests() -> int:
    return _run_unittest_modules(
        "v2 architecture boundary tests",
        ("tests.v2.test_architecture_boundaries",),
    )


def _run_v2_search_contract_tests() -> int:
    return _run_unittest_modules(
        "v2 search request and core contract tests",
        (
            "tests.v2.test_contracts_criteria",
            "tests.v2.test_contracts_search",
            "tests.v2.test_contracts_records_and_scraper",
            "tests.v2.test_contracts_source_and_fixtures",
        ),
    )


def _run_v2_source_catalog_tests() -> int:
    return _run_unittest_modules(
        "v2 source catalog and registry tests",
        (
            "tests.v2.test_source_catalog",
            "tests.v2.test_runtime_catalog",
            "tests.v2.test_runtime_source_registry",
        ),
    )


def _run_v2_scraper_fixture_tests() -> int:
    return _run_unittest_modules(
        "v2 source-specific parser fixture tests",
        ("tests.v2.test_runtime_sources_contract_first",),
    )


def _run_v2_transport_tests() -> int:
    return _run_unittest_modules(
        "v2 transport classification tests",
        ("tests.v2.test_runtime_http",),
    )


def _run_v2_orchestrator_tests() -> int:
    return _run_unittest_modules(
        "v2 scraper orchestrator tests",
        ("tests.v2.test_runtime_orchestrator",),
    )


def _run_v2_persistence_tests() -> int:
    return _run_unittest_modules(
        "v2 run artifact storage tests",
        (
            "tests.v2.test_runtime_corpus",
            "tests.v2.test_runtime_run_layout",
        ),
    )


def _run_v2_postprocessing_tests() -> int:
    return _run_unittest_modules(
        "v2 post-processing layer tests",
        (
            "tests.v2.test_postprocessing_criteria_plan",
            "tests.v2.test_postprocessing_pipeline",
        ),
    )


def _run_v2_application_cli_tests() -> int:
    return _run_unittest_modules(
        "v2 application and CLI adapter tests",
        ("tests.v2.test_application_cli",),
    )


def _run_v2_live_e2e() -> int:
    print("+ v2 live e2e smoke", flush=True)
    with tempfile.TemporaryDirectory(prefix="job-harness-v2-live-") as tmp:
        first = _run_cli_search(
            runs_dir=Path(tmp),
            args=(
                "--query",
                "QA",
                "--source",
                "habr_career",
                "--source",
                "hh_ru",
                "--source",
                "talanto",
                "--source",
                "career:vk",
                "--source",
                "career:jetbrains",
                "--max-results",
                "5",
            ),
        )
        if first is None:
            return 1
        if not _validate_live_execution(first, expected_append_sequence=0):
            return 1

        run_id = _required_text(first, "run_id")
        second = _run_cli_search(
            runs_dir=Path(tmp),
            args=(
                "--query",
                "тестировщик",
                "--source",
                "habr_career",
                "--source",
                "hh_ru",
                "--source",
                "talanto",
                "--source",
                "career:vk",
                "--source",
                "career:jetbrains",
                "--max-results",
                "5",
                "--append-to-run-id",
                run_id,
            ),
        )
        if second is None:
            return 1
        if not _validate_live_execution(second, expected_append_sequence=1):
            return 1
        return _validate_append_artifacts(first, second)


def _run_cli_search(*, runs_dir: Path, args: tuple[str, ...]) -> dict[str, object] | None:
    cmd = (
        "uv",
        "--directory",
        PLUGIN_ARG,
        "run",
        "job-harness-v2",
        "search",
        "--runs-dir",
        str(runs_dir),
        "--retry-attempts",
        "1",
        "--source-attempt-timeout",
        "25",
        "--run-timeout",
        "60",
        "--fetch-timeout",
        "20",
        *args,
    )
    completed = subprocess.run(
        cmd,
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        print(completed.stdout, end="")
        print(completed.stderr, end="", file=sys.stderr)
        return None
    try:
        value = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        print(f"v2 live e2e failed: CLI stdout is not JSON: {exc}", file=sys.stderr)
        print(completed.stdout, file=sys.stderr)
        return None
    if not isinstance(value, dict):
        print("v2 live e2e failed: CLI returned non-object JSON", file=sys.stderr)
        return None
    return value


def _validate_live_execution(payload: dict[str, object], *, expected_append_sequence: int) -> bool:
    if payload.get("record_type") != "v2_search_execution":
        print("v2 live e2e failed: unexpected record_type", file=sys.stderr)
        return False
    if payload.get("append_sequence") != expected_append_sequence:
        print("v2 live e2e failed: unexpected append_sequence", file=sys.stderr)
        return False

    artifacts = _validated_live_artifacts(payload)
    if artifacts is None:
        return False

    attempts = payload.get("attempts")
    if not isinstance(attempts, list) or not attempts:
        print("v2 live e2e failed: missing attempts", file=sys.stderr)
        return False
    outcomes = [_required_text(attempt, "outcome") for attempt in attempts if isinstance(attempt, dict)]
    if any(outcome not in CANONICAL_OUTCOMES for outcome in outcomes):
        print(f"v2 live e2e failed: non-canonical outcome: {outcomes}", file=sys.stderr)
        return False
    if "success" not in outcomes:
        print(f"v2 live e2e failed: no source succeeded: {outcomes}", file=sys.stderr)
        return False
    if not _validate_vk_live_attempt(attempts, artifacts):
        return False
    for source_id in ("hh_ru", "talanto", "career:jetbrains"):
        if not _validate_required_success_live_attempt(attempts, source_id=source_id):
            return False

    processed = json.loads(Path(_required_text(artifacts, "processed_results")).read_text(encoding="utf-8"))
    if processed.get("record_type") != "processed_results":
        print("v2 live e2e failed: processed artifact has wrong record_type", file=sys.stderr)
        return False
    if not isinstance(processed.get("results"), list):
        print("v2 live e2e failed: processed results is not a list", file=sys.stderr)
        return False
    return True


def _validated_live_artifacts(payload: dict[str, object]) -> dict[str, object] | None:
    artifacts = payload.get("artifacts")
    if not isinstance(artifacts, dict):
        print("v2 live e2e failed: missing artifacts object", file=sys.stderr)
        return None
    for name in ("raw_listings", "source_attempts", "run_manifest", "processed_results"):
        path = Path(_required_text(artifacts, name))
        if not path.exists():
            print(f"v2 live e2e failed: missing artifact {name}: {path}", file=sys.stderr)
            return None
    return artifacts


def _validate_required_success_live_attempt(attempts: list[object], *, source_id: str) -> bool:
    source_attempts = [
        attempt
        for attempt in attempts
        if isinstance(attempt, dict) and attempt.get("source") == source_id
    ]
    if not source_attempts:
        print(f"v2 live e2e failed: {source_id} attempt is missing", file=sys.stderr)
        return False
    for attempt in source_attempts:
        if _required_text(attempt, "outcome") != "success":
            print(f"v2 live e2e failed: unexpected {source_id} live outcome: {attempt}", file=sys.stderr)
            return False
    return True


def _validate_vk_live_attempt(attempts: list[object], artifacts: dict[str, object]) -> bool:
    vk_attempts = [
        attempt
        for attempt in attempts
        if isinstance(attempt, dict) and attempt.get("source") == "career:vk"
    ]
    if not vk_attempts:
        print("v2 live e2e failed: career:vk attempt is missing", file=sys.stderr)
        return False

    for attempt in vk_attempts:
        outcome = _required_text(attempt, "outcome")
        if outcome == "success":
            continue
        if outcome == "network_error" and _has_known_vk_tls_chain_error(attempt, artifacts):
            print("v2 live e2e detected known career:vk TLS chain issue", flush=True)
            continue
        print(f"v2 live e2e failed: unexpected career:vk live outcome: {attempt}", file=sys.stderr)
        return False
    return True


def _has_known_vk_tls_chain_error(attempt: dict[str, object], artifacts: dict[str, object]) -> bool:
    detailed_attempts = _read_source_attempt_artifact(artifacts)
    query_variant = attempt.get("query_variant")
    for detailed in detailed_attempts:
        if detailed.get("source") != "career:vk":
            continue
        if detailed.get("outcome") != "network_error":
            continue
        if detailed.get("query_variant") != query_variant:
            continue
        if _is_known_vk_tls_chain_error(detailed):
            return True
    return False


def _read_source_attempt_artifact(artifacts: dict[str, object]) -> list[dict[str, object]]:
    path = Path(_required_text(artifacts, "source_attempts"))
    records: list[dict[str, object]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        record = json.loads(line)
        if isinstance(record, dict):
            records.append(record)
    return records


def _is_known_vk_tls_chain_error(detailed_attempt: dict[str, object]) -> bool:
    evidence = detailed_attempt.get("evidence")
    if not isinstance(evidence, dict):
        return False
    error = evidence.get("error")
    if not isinstance(error, str):
        return False
    known_fragments = (
        "CERTIFICATE_VERIFY_FAILED",
        "certificate verify failed",
        "unable to get local issuer certificate",
    )
    return any(fragment in error for fragment in known_fragments)


def _validate_append_artifacts(first: dict[str, object], second: dict[str, object]) -> int:
    if first.get("run_id") != second.get("run_id"):
        print("v2 live e2e failed: append used a different run_id", file=sys.stderr)
        return 1
    if first.get("run_dir") != second.get("run_dir"):
        print("v2 live e2e failed: append used a different run_dir", file=sys.stderr)
        return 1

    artifacts = second["artifacts"]
    if not isinstance(artifacts, dict):
        print("v2 live e2e failed: append artifacts missing", file=sys.stderr)
        return 1
    manifest = json.loads(Path(_required_text(artifacts, "run_manifest")).read_text(encoding="utf-8"))
    if manifest.get("latest_append_sequence") != 1:
        print("v2 live e2e failed: manifest did not advance latest_append_sequence", file=sys.stderr)
        return 1
    return 0


def _required_text(value: dict[str, object], key: str) -> str:
    item = value.get(key)
    if not isinstance(item, str) or not item:
        raise ValueError(f"expected non-empty text field: {key}")
    return item


def _run_unittest_modules(label: str, modules: tuple[str, ...]) -> int:
    print(f"+ {label}", flush=True)
    return _run(
        [
            "uv",
            "--directory",
            PLUGIN_ARG,
            "run",
            "python",
            "-m",
            "unittest",
            "-v",
            *modules,
        ]
    )


def _run_unittest_discover(label: str, pattern: str) -> int:
    print(f"+ {label}", flush=True)
    return _run(
        [
            "uv",
            "--directory",
            PLUGIN_ARG,
            "run",
            "python",
            "-m",
            "unittest",
            "discover",
            "-s",
            "tests/v2",
            "-p",
            pattern,
            "-v",
        ]
    )


def _run(cmd: list[str]) -> int:
    print("+ " + " ".join(cmd), flush=True)
    return subprocess.call(cmd, cwd=ROOT)


if __name__ == "__main__":
    raise SystemExit(main())
