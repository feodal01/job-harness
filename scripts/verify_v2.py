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
V2_SPEED_GATE_SCRIPT = ROOT / "scripts" / "benchmark_v2_search.py"
DEFAULT_V2_SPEED_PROFILE = ROOT / "benchmarks" / "v2-search-speed-gate.json"
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
LIVE_HEALTHY_SOURCE_STATUSES = frozenset({"succeeded", "no_results", "limit_reached"})


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
    parser.add_argument(
        "--speed-gate",
        action="store_true",
        help="Run the v2 live speed gate after deterministic checks. Requires --speed-baseline.",
    )
    parser.add_argument(
        "--speed-profile",
        type=Path,
        default=DEFAULT_V2_SPEED_PROFILE,
        help="Benchmark profile JSON for --speed-gate.",
    )
    parser.add_argument(
        "--speed-baseline",
        type=Path,
        help="Benchmark result JSON to compare against for --speed-gate.",
    )
    parser.add_argument(
        "--speed-min-speedup",
        type=float,
        help="Override benchmark profile min_wall_speedup for --speed-gate.",
    )
    parser.add_argument(
        "--speed-skip-shape-check",
        action="store_true",
        help="Run --speed-gate without requiring matching result/item counts and source-plan statuses.",
    )
    args = parser.parse_args()
    if args.speed_gate and args.speed_baseline is None:
        parser.error("--speed-gate requires --speed-baseline")

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
        _run_v2_graph_runtime_tests,
        _run_v2_persistence_tests,
        _run_v2_postprocessing_tests,
        _run_v2_application_cli_tests,
        _run_v2_tests,
    ]
    if not args.skip_live:
        checks.append(lambda: _run_v2_live_e2e(args.live_profile))
    if args.speed_gate:
        checks.append(
            lambda: _run_v2_speed_gate(
                profile=args.speed_profile,
                baseline=args.speed_baseline,
                min_speedup=args.speed_min_speedup,
                skip_shape_check=args.speed_skip_shape_check,
            )
        )

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
            "../../scripts/benchmark_v2_search.py",
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
            "../../scripts/benchmark_v2_search.py",
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


def _run_v2_graph_runtime_tests() -> int:
    return _run_unittest_modules(
        "v2 independent scraper graph runtime tests",
        (
            "tests.v2.test_contracts_independent_scrapers",
            "tests.v2.test_runtime_direct_scraper_executor",
            "tests.v2.test_runtime_parser_runtime",
            "tests.v2.test_runtime_resource_gate",
            "tests.v2.test_runtime_managed_task_runner",
            "tests.v2.test_runtime_graph_coordinator",
            "tests.v2.test_runtime_graph_pipeline",
            "tests.v2.test_runtime_final_assembler",
            "tests.v2.test_runtime_independent_source_bundles",
        ),
    )


def _run_v2_persistence_tests() -> int:
    return _run_unittest_modules(
        "v2 durable graph persistence and run-layout tests",
        (
            "tests.v2.test_persistence_graph_repository",
            "tests.v2.test_persistence_graph_company_observations",
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


def _run_v2_speed_gate(
    *,
    profile: Path,
    baseline: Path | None,
    min_speedup: float | None,
    skip_shape_check: bool,
) -> int:
    if baseline is None:
        print("v2 speed gate failed: baseline path is required", file=sys.stderr)
        return 1
    print("+ v2 live speed gate", flush=True)
    cmd = [
        sys.executable,
        str(V2_SPEED_GATE_SCRIPT),
        "--profile",
        str(profile),
        "--baseline",
        str(baseline),
    ]
    if min_speedup is not None:
        cmd.extend(["--min-speedup", str(min_speedup)])
    if skip_shape_check:
        cmd.append("--skip-shape-check")
    return _run(cmd)


def _live_phase_label(live_profile: str, *, phase: str) -> str:
    if live_profile == "light" and phase == "initial":
        return "light run 1 (Developer, career:jetbrains+jobturbo)"
    if live_profile == "light" and phase == "append":
        return "light run 2 (append QA, exclude_text)"
    if live_profile == "full" and phase == "initial":
        return "full run 1 (QA, grade=middle, compensation=150000 RUB/month, RU+AM)"
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

    diagnostics = payload.get("diagnostics")
    if not isinstance(diagnostics, dict):
        print("v2 live e2e failed: missing diagnostics", file=sys.stderr)
        return False
    source_plans = diagnostics.get("source_plans")
    if not isinstance(source_plans, list) or not source_plans:
        print("v2 live e2e failed: missing source plans", file=sys.stderr)
        return False
    if not _validate_live_source_plans(
        source_plans,
        expected_source_ids=expected_source_ids,
    ):
        return False
    if not _validate_live_processed_artifact(payload, artifacts=artifacts):
        return False
    return _validate_live_engine_outputs(payload, diagnostics=diagnostics, source_plans=source_plans)


def _validate_live_source_plans(
    source_plans: list[object],
    *,
    expected_source_ids: tuple[str, ...],
) -> bool:
    if not _validate_all_live_sources_planned(
        source_plans,
        expected_source_ids=expected_source_ids,
    ):
        return False
    return all(
        _validate_healthy_live_source_plan(source_plans, source_id=source_id)
        for source_id in expected_source_ids
    )


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
    processed_count = payload.get("result_count")
    if not isinstance(processed_count, int) or processed_count != processed.get("result_count"):
        print("v2 live e2e failed: result_count mismatch", file=sys.stderr)
        return False
    return True


def _validate_live_engine_outputs(
    payload: dict[str, object],
    *,
    diagnostics: dict[str, object],
    source_plans: list[object],
) -> bool:
    append_sequence = payload.get("append_sequence")
    raw_written = diagnostics.get("listing_observation_count")
    if not isinstance(raw_written, int) or raw_written < 0:
        print("v2 live e2e failed: invalid listing_observation_count", file=sys.stderr)
        return False
    if append_sequence == 0 and raw_written <= 0:
        print("v2 live e2e failed: expected listing observations on initial run", file=sys.stderr)
        return False

    if append_sequence == 0:
        processed_count = payload.get("result_count")
        if not isinstance(processed_count, int) or processed_count <= 0:
            print("v2 live e2e failed: expected processed results on initial live run", file=sys.stderr)
            return False

        success_with_items = any(
            isinstance(plan, dict)
            and plan.get("status") in {"succeeded", "partial", "limit_reached"}
            and isinstance(plan.get("items"), dict)
            and isinstance(plan["items"].get("used"), int)
            and plan["items"]["used"] > 0
            for plan in source_plans
        )
        if not success_with_items:
            print("v2 live e2e failed: no successful source stored listings", file=sys.stderr)
            return False
    return True


def _validate_all_live_sources_planned(
    source_plans: list[object],
    *,
    expected_source_ids: tuple[str, ...],
) -> bool:
    observed = {
        plan.get("source_id")
        for plan in source_plans
        if isinstance(plan, dict) and isinstance(plan.get("source_id"), str)
    }
    missing = [source_id for source_id in expected_source_ids if source_id not in observed]
    if missing:
        print(f"v2 live e2e failed: missing live source plans: {missing}", file=sys.stderr)
        return False
    return True


def _validate_healthy_live_source_plan(source_plans: list[object], *, source_id: str) -> bool:
    matching_plans = [
        plan for plan in source_plans if isinstance(plan, dict) and plan.get("source_id") == source_id
    ]
    if not matching_plans:
        print(f"v2 live e2e failed: {source_id} source plan is missing", file=sys.stderr)
        return False
    for plan in matching_plans:
        status = _required_text(plan, "status")
        if status in LIVE_HEALTHY_SOURCE_STATUSES:
            continue
        print(f"v2 live e2e failed: unhealthy {source_id} source plan: {plan}", file=sys.stderr)
        return False
    return True


def _print_live_execution_summary(payload: dict[str, object], *, label: str) -> None:
    diagnostics = payload.get("diagnostics")
    source_plans = diagnostics.get("source_plans") if isinstance(diagnostics, dict) else None
    if not isinstance(source_plans, list):
        return
    print(f"v2 live e2e summary ({label}):", flush=True)
    for plan in sorted(
        (item for item in source_plans if isinstance(item, dict)),
        key=lambda item: str(item.get("source_id", "")),
    ):
        source = plan.get("source_id", "?")
        status = plan.get("status", "?")
        items = plan.get("items")
        units = plan.get("units")
        item_count = items.get("used", 0) if isinstance(items, dict) else 0
        unit_count = units.get("used", 0) if isinstance(units, dict) else 0
        print(
            f"  {source:<28} {status:<15} items={item_count:<4} units={unit_count:<3}",
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
    execution_json_path = Path(_required_text(artifacts, "execution_json"))
    if not execution_json_path.exists():
        print(f"v2 live e2e failed: missing execution receipt: {execution_json_path}", file=sys.stderr)
        return None
    return artifacts


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
        SELECT final.payload_json
        FROM final_vacancies AS final
        JOIN search_executions AS execution
          ON execution.execution_id = final.execution_id
        WHERE execution.append_sequence = ?
        ORDER BY final.final_vacancy_id
        """,
        (append_sequence,),
    )
    return {
        "record_type": "processed_results",
        "result_count": len(rows),
        "results": rows,
    }


def _read_run_manifest(artifacts: dict[str, object]) -> dict[str, object]:
    database_path = Path(_required_text(artifacts, "database"))
    connection = sqlite3.connect(str(database_path), timeout=30.0)
    try:
        row = connection.execute(
            "SELECT MAX(append_sequence) FROM search_executions"
        ).fetchone()
    finally:
        connection.close()
    return {"latest_append_sequence": None if row is None else row[0]}


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
