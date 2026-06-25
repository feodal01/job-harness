#!/usr/bin/env python3
"""Dedicated verification gate for the v2 contract-first engine."""

from __future__ import annotations

import argparse
import json
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLUGIN_DIR = ROOT / "plugins" / "job-harness"
PLUGIN_ARG = PLUGIN_DIR.relative_to(ROOT).as_posix()
V2_LIVE_E2E_SCRIPT = ROOT / "scripts" / "v2_live_e2e.py"
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
LIVE_HEALTHY_OUTCOMES = frozenset({"success", "no_results", "partial_success"})


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--skip-live",
        action="store_true",
        help="Run only deterministic v2 checks. The default includes a full-catalog live e2e run.",
    )
    parser.add_argument(
        "--live-profile",
        choices=("full", "light"),
        default="full",
        help="Live e2e source profile to run when live checks are enabled.",
    )
    args = parser.parse_args()

    checks = [
        _run_no_compat_comments,
        _run_v2_module_structure_check,
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
        checks.append(lambda: _run_v2_live_e2e(args.live_profile))

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
            "../../scripts/check_v2_module_structure.py",
            "../../scripts/verify_v2.py",
            "../../scripts/v2_live_e2e.py",
        ]
    )


def _run_no_compat_comments() -> int:
    return _run([sys.executable, "scripts/check_no_compat_comments.py"])


def _run_v2_module_structure_check() -> int:
    return _run([sys.executable, "scripts/check_v2_module_structure.py"])


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
            "../../scripts/check_v2_module_structure.py",
            "../../scripts/verify_v2.py",
            "../../scripts/v2_live_e2e.py",
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
            "tests.v2.test_persistence_sqlite_run_store",
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


def _run_v2_live_e2e(live_profile: str) -> int:
    print(f"+ v2 live e2e (V2SearchApplication, {live_profile} profile)", flush=True)
    with tempfile.TemporaryDirectory(prefix="job-harness-v2-live-") as tmp:
        runs_dir = Path(tmp)

        initial_report = _run_live_e2e_phase(runs_dir=runs_dir, phase="initial", live_profile=live_profile)
        if initial_report is None:
            return 1
        expected_source_ids = _report_expected_source_ids(initial_report)
        if expected_source_ids is None:
            return 1
        print(
            f"v2 live e2e selected sources: {len(expected_source_ids)}",
            flush=True,
        )

        first = _report_execution(initial_report, key="execution")
        if first is None:
            return 1
        if not _validate_live_execution(
            first,
            expected_append_sequence=0,
            expected_source_ids=expected_source_ids,
        ):
            return 1
        _print_live_execution_summary(first, label=_live_phase_label(live_profile, phase="initial"))

        append_to_run_id = first.get("run_id")
        if not isinstance(append_to_run_id, str) or not append_to_run_id:
            print("v2 live e2e failed: initial run_id missing", file=sys.stderr)
            return 1

        append_report = _run_live_e2e_phase(
            runs_dir=runs_dir,
            phase="append",
            live_profile=live_profile,
            append_to_run_id=append_to_run_id,
        )
        if append_report is None:
            return 1

        second = _report_execution(append_report, key="execution")
        if second is None:
            return 1
        if not _validate_live_execution(
            second,
            expected_append_sequence=1,
            expected_source_ids=expected_source_ids,
        ):
            return 1
        _print_live_execution_summary(
            second,
            label=_live_phase_label(live_profile, phase="append"),
        )
        return _validate_append_artifacts(first, second)


def _live_phase_label(live_profile: str, *, phase: str) -> str:
    if live_profile == "light" and phase == "initial":
        return "light run 1 (Developer, career:jetbrains+jobturbo)"
    if live_profile == "light" and phase == "append":
        return "light run 2 (append QA, exclude_text)"
    if live_profile == "full" and phase == "initial":
        return "full run 1 (QA, grade=middle, salary_from=150000, RU+AM)"
    if live_profile == "full" and phase == "append":
        return "full run 2 (append тестировщик, RU+AM, exclude_text)"
    raise ValueError(f"unsupported live e2e label: profile={live_profile!r}, phase={phase!r}")


def _run_live_e2e_phase(
    *,
    runs_dir: Path,
    phase: str,
    live_profile: str,
    append_to_run_id: str | None = None,
) -> dict[str, object] | None:
    cmd = [
        "uv",
        "--directory",
        PLUGIN_ARG,
        "run",
        "python",
        str(V2_LIVE_E2E_SCRIPT),
        "--runs-dir",
        str(runs_dir),
        "--phase",
        phase,
        "--profile",
        live_profile,
    ]
    if append_to_run_id is not None:
        cmd.extend(["--append-to-run-id", append_to_run_id])

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
        print(f"v2 live e2e failed: live_e2e stdout is not JSON: {exc}", file=sys.stderr)
        print(completed.stdout, file=sys.stderr)
        return None
    if not isinstance(value, dict):
        print("v2 live e2e failed: live_e2e returned non-object JSON", file=sys.stderr)
        return None

    expected_record_type = {
        "initial": "v2_live_e2e_initial_report",
        "append": "v2_live_e2e_append_report",
    }.get(phase)
    if expected_record_type is None:
        print(f"v2 live e2e failed: unsupported phase {phase!r}", file=sys.stderr)
        return None
    if value.get("record_type") != expected_record_type:
        print("v2 live e2e failed: unexpected live_e2e record_type", file=sys.stderr)
        return None
    return value


def _report_expected_source_ids(report: dict[str, object]) -> tuple[str, ...] | None:
    raw_ids = report.get("expected_source_ids")
    if not isinstance(raw_ids, list) or not raw_ids:
        print("v2 live e2e failed: missing expected_source_ids", file=sys.stderr)
        return None
    source_ids = tuple(item for item in raw_ids if isinstance(item, str) and item)
    if len(source_ids) != len(raw_ids):
        print("v2 live e2e failed: invalid expected_source_ids entries", file=sys.stderr)
        return None
    selected_source_count = report.get("selected_source_count")
    if selected_source_count != len(source_ids):
        print(
            "v2 live e2e failed: selected source count does not match expected sources",
            file=sys.stderr,
        )
        return None
    catalog_source_count = report.get("catalog_source_count")
    if not isinstance(catalog_source_count, int) or catalog_source_count < len(source_ids):
        print(
            "v2 live e2e failed: catalog source count is smaller than selected sources",
            file=sys.stderr,
        )
        return None
    return source_ids


def _report_execution(report: dict[str, object], *, key: str) -> dict[str, object] | None:
    execution = report.get(key)
    if not isinstance(execution, dict):
        print(f"v2 live e2e failed: missing {key} execution", file=sys.stderr)
        return None
    if execution.get("record_type") != "v2_search_execution":
        print(f"v2 live e2e failed: unexpected {key} record_type", file=sys.stderr)
        return None
    return execution


def _validate_live_execution(
    payload: dict[str, object],
    *,
    expected_append_sequence: int,
    expected_source_ids: tuple[str, ...],
) -> bool:
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
    if not _validate_live_attempt_outcomes(
        attempts,
        expected_source_ids=expected_source_ids,
        artifacts=artifacts,
    ):
        return False
    if not _validate_live_processed_artifact(payload, artifacts=artifacts):
        return False
    return _validate_live_engine_outputs(payload, artifacts=artifacts, attempts=attempts)


def _validate_live_attempt_outcomes(
    attempts: list[object],
    *,
    expected_source_ids: tuple[str, ...],
    artifacts: dict[str, object],
) -> bool:
    outcomes = [_required_text(attempt, "outcome") for attempt in attempts if isinstance(attempt, dict)]
    if any(outcome not in CANONICAL_OUTCOMES for outcome in outcomes):
        print(f"v2 live e2e failed: non-canonical outcome: {outcomes}", file=sys.stderr)
        return False
    if "success" not in outcomes:
        print(f"v2 live e2e failed: no source succeeded: {outcomes}", file=sys.stderr)
        return False
    if not _validate_all_live_sources_attempted(attempts, expected_source_ids=expected_source_ids):
        return False
    for source_id in expected_source_ids:
        if source_id == "career:vk":
            if not _validate_vk_live_attempt(attempts, artifacts):
                return False
            continue
        if not _validate_healthy_live_attempt(attempts, source_id=source_id):
            return False
    return True


def _validate_live_processed_artifact(
    payload: dict[str, object],
    *,
    artifacts: dict[str, object],
) -> bool:
    append_sequence = payload.get("append_sequence")
    if not isinstance(append_sequence, int):
        print("v2 live e2e failed: invalid append_sequence for processed lookup", file=sys.stderr)
        return False
    processed = _read_processed_payload(artifacts, append_sequence=append_sequence)
    if processed.get("record_type") != "processed_results":
        print("v2 live e2e failed: processed artifact has wrong record_type", file=sys.stderr)
        return False
    if not isinstance(processed.get("results"), list):
        print("v2 live e2e failed: processed results is not a list", file=sys.stderr)
        return False
    processed_count = payload.get("processed_result_count")
    if not isinstance(processed_count, int) or processed_count != processed.get("result_count"):
        print("v2 live e2e failed: processed_result_count mismatch", file=sys.stderr)
        return False
    return True


def _validate_live_engine_outputs(
    payload: dict[str, object],
    *,
    artifacts: dict[str, object],
    attempts: list[object],
) -> bool:
    append_sequence = payload.get("append_sequence")
    raw_written = payload.get("raw_records_written_this_call")
    if not isinstance(raw_written, int) or raw_written < 0:
        print("v2 live e2e failed: invalid raw_records_written_this_call", file=sys.stderr)
        return False
    if append_sequence == 0 and raw_written <= 0:
        print("v2 live e2e failed: expected raw_records_written_this_call > 0", file=sys.stderr)
        return False

    raw_records = _read_raw_records(artifacts)
    if append_sequence == 0 and len(raw_records) < raw_written:
        print("v2 live e2e failed: raw_listings table shorter than reported writes", file=sys.stderr)
        return False

    source_attempts = _read_source_attempt_records(artifacts)
    if not source_attempts:
        print("v2 live e2e failed: source_attempts table is empty", file=sys.stderr)
        return False

    if append_sequence == 0:
        processed_count = payload.get("processed_result_count")
        if not isinstance(processed_count, int) or processed_count <= 0:
            print("v2 live e2e failed: expected processed results on initial live run", file=sys.stderr)
            return False

        success_with_raw = any(
            isinstance(attempt, dict)
            and attempt.get("outcome") == "success"
            and isinstance(attempt.get("raw_listings_written"), int)
            and attempt.get("raw_listings_written", 0) > 0
            for attempt in attempts
        )
        if not success_with_raw:
            print("v2 live e2e failed: no successful source wrote raw listings", file=sys.stderr)
            return False
    return True


def _validate_all_live_sources_attempted(
    attempts: list[object],
    *,
    expected_source_ids: tuple[str, ...],
) -> bool:
    observed = {
        attempt.get("source")
        for attempt in attempts
        if isinstance(attempt, dict) and isinstance(attempt.get("source"), str)
    }
    missing = [source_id for source_id in expected_source_ids if source_id not in observed]
    if missing:
        print(f"v2 live e2e failed: missing live attempts for sources: {missing}", file=sys.stderr)
        return False
    return True


def _validate_healthy_live_attempt(attempts: list[object], *, source_id: str) -> bool:
    source_attempts = [
        attempt for attempt in attempts if isinstance(attempt, dict) and attempt.get("source") == source_id
    ]
    if not source_attempts:
        print(f"v2 live e2e failed: {source_id} attempt is missing", file=sys.stderr)
        return False
    for attempt in source_attempts:
        outcome = _required_text(attempt, "outcome")
        if outcome in LIVE_HEALTHY_OUTCOMES:
            continue
        print(f"v2 live e2e failed: unhealthy {source_id} live outcome: {attempt}", file=sys.stderr)
        return False
    return True


def _print_live_execution_summary(payload: dict[str, object], *, label: str) -> None:
    attempts = payload.get("attempts")
    if not isinstance(attempts, list):
        return
    print(f"v2 live e2e summary ({label}):", flush=True)
    for attempt in sorted(
        (item for item in attempts if isinstance(item, dict)),
        key=lambda item: str(item.get("source", "")),
    ):
        source = attempt.get("source", "?")
        outcome = attempt.get("outcome", "?")
        raw = attempt.get("raw_listings_written", 0)
        pages = attempt.get("pages_visited", 0)
        elapsed_ms = attempt.get("elapsed_ms", 0)
        print(
            f"  {source:<20} {outcome:<15} raw={raw:<4} pages={pages:<2} ms={elapsed_ms}",
            flush=True,
        )


def _validated_live_artifacts(payload: dict[str, object]) -> dict[str, object] | None:
    artifacts = payload.get("artifacts")
    if not isinstance(artifacts, dict):
        print("v2 live e2e failed: missing artifacts object", file=sys.stderr)
        return None
    database_path = Path(_required_text(artifacts, "database"))
    if not database_path.exists():
        print(f"v2 live e2e failed: missing run database: {database_path}", file=sys.stderr)
        return None
    report_html_path = Path(_required_text(artifacts, "report_html"))
    if not report_html_path.exists():
        print(f"v2 live e2e failed: missing report HTML: {report_html_path}", file=sys.stderr)
        return None
    expected_tables = {
        "raw_listings_table": "raw_listings",
        "source_attempts_table": "source_attempts",
        "run_manifest_table": "run_manifest",
        "processed_results_table": "processed_results",
    }
    for key, expected_value in expected_tables.items():
        if artifacts.get(key) != expected_value:
            print(f"v2 live e2e failed: unexpected {key}", file=sys.stderr)
            return None
    return artifacts


def _validate_vk_live_attempt(attempts: list[object], artifacts: dict[str, object]) -> bool:
    vk_attempts = [
        attempt for attempt in attempts if isinstance(attempt, dict) and attempt.get("source") == "career:vk"
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
    return _read_source_attempt_records(artifacts)


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
    manifest = _read_run_manifest(artifacts)
    if manifest.get("latest_append_sequence") != 1:
        print("v2 live e2e failed: manifest did not advance latest_append_sequence", file=sys.stderr)
        return 1
    return 0


def _read_processed_payload(artifacts: dict[str, object], *, append_sequence: int) -> dict[str, object]:
    rows = _read_json_rows(
        artifacts,
        """
        SELECT payload_json
        FROM processed_results
        WHERE append_sequence = ? AND phase = 'final'
        """,
        (append_sequence,),
    )
    if len(rows) != 1:
        raise ValueError(f"expected one final processed_results row for append_sequence={append_sequence}")
    return rows[0]


def _read_raw_records(artifacts: dict[str, object]) -> list[dict[str, object]]:
    return _read_json_rows(
        artifacts,
        """
        SELECT record_json
        FROM raw_listings
        ORDER BY append_sequence, id
        """,
        (),
    )


def _read_source_attempt_records(artifacts: dict[str, object]) -> list[dict[str, object]]:
    return _read_json_rows(
        artifacts,
        """
        SELECT payload_json
        FROM source_attempts
        ORDER BY append_sequence, id
        """,
        (),
    )


def _read_run_manifest(artifacts: dict[str, object]) -> dict[str, object]:
    rows = _read_json_rows(
        artifacts,
        """
        SELECT payload_json
        FROM run_manifest
        """,
        (),
    )
    if len(rows) != 1:
        raise ValueError("expected one run_manifest row")
    return rows[0]


def _read_json_rows(
    artifacts: dict[str, object],
    sql: str,
    parameters: tuple[object, ...],
) -> list[dict[str, object]]:
    database_path = Path(_required_text(artifacts, "database"))
    connection = sqlite3.connect(str(database_path), timeout=30.0)
    try:
        rows = connection.execute(sql, parameters).fetchall()
    finally:
        connection.close()
    payloads: list[dict[str, object]] = []
    for row in rows:
        payload = json.loads(row[0])
        if not isinstance(payload, dict):
            raise ValueError("database payload row is not a JSON object")
        payloads.append(payload)
    return payloads


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
