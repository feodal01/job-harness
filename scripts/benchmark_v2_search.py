#!/usr/bin/env python3
"""Run the fixed v2 live-search speed gate profile."""

from __future__ import annotations

import argparse
import json
import sqlite3
import subprocess
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Any

JsonObject = dict[str, Any]

_DEFAULT_PROFILE = Path("benchmarks/v2-search-speed-gate.json")
_HEALTHY_OUTCOMES = frozenset({"success", "no_results", "partial_success"})


def main() -> int:
    args = _parse_args()
    repo_root = Path(__file__).resolve().parents[1]
    profile_path = (repo_root / args.profile).resolve() if not args.profile.is_absolute() else args.profile
    profile = _load_profile(profile_path)
    result = _run_profile(repo_root=repo_root, profile=profile)
    result_path = _write_result(repo_root=repo_root, profile=profile, result=result)
    _print_summary(result=result, result_path=result_path)
    if args.baseline is not None:
        baseline_path = (repo_root / args.baseline).resolve() if not args.baseline.is_absolute() else args.baseline
        return _compare_to_baseline(
            baseline_path=baseline_path,
            candidate=result,
            profile=profile,
            min_speedup=_min_speedup(args, profile),
            check_shape=not args.skip_shape_check,
        )
    return int(result["returncode"] != 0)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", type=Path, default=_DEFAULT_PROFILE)
    parser.add_argument("--baseline", type=Path, help="Previous benchmark result JSON to compare against.")
    parser.add_argument(
        "--min-speedup",
        type=float,
        help="Required wall-clock speedup when --baseline is provided. Defaults to profile min_wall_speedup.",
    )
    parser.add_argument(
        "--skip-shape-check",
        action="store_true",
        help="Do not require matching result/raw counts and outcomes against the baseline.",
    )
    return parser.parse_args()


def _load_profile(path: Path) -> JsonObject:
    with path.open(encoding="utf-8") as handle:
        profile = json.load(handle)
    if not isinstance(profile, dict):
        raise ValueError("benchmark profile must be a JSON object")
    for key in ("name", "runner", "search_args", "runs_dir", "result_dir"):
        if key not in profile:
            raise ValueError(f"benchmark profile is missing {key}")
    return profile


def _run_profile(*, repo_root: Path, profile: JsonObject) -> JsonObject:
    run_id = _run_id(str(profile["name"]))
    command = _profile_command(repo_root=repo_root, profile=profile, run_id=run_id)
    shape_sources = _shape_sources(profile)
    started_at = datetime.now(UTC)
    started = perf_counter()
    completed = subprocess.run(
        command,
        cwd=repo_root,
        text=True,
        capture_output=True,
        check=False,
    )
    wall_seconds = perf_counter() - started
    finished_at = datetime.now(UTC)
    cli_payload = _decode_cli_payload(completed.stdout)
    database_path = _database_path(repo_root=repo_root, cli_payload=cli_payload)
    processed_payload = _processed_payload(database_path)
    manifest_payload = _run_manifest_payload(database_path)
    return {
        "schema_version": 1,
        "record_type": "v2_search_speed_gate_result",
        "profile_name": profile["name"],
        "run_id": run_id,
        "started_at": _iso_z(started_at),
        "finished_at": _iso_z(finished_at),
        "wall_seconds": round(wall_seconds, 3),
        "returncode": completed.returncode,
        "command": command,
        "stdout_json": cli_payload,
        "stderr": completed.stderr,
        "database_path": str(database_path) if database_path is not None else None,
        "processed_summary": _processed_summary(processed_payload),
        "attempt_summary": _attempt_summary(cli_payload),
        "shape_sources": list(shape_sources) if shape_sources is not None else [],
        "source_shape_summary": _attempt_summary(cli_payload, source_ids=shape_sources)
        if shape_sources is not None
        else {},
        "runtime_summary": _runtime_summary(cli_payload),
        "manifest_summary": _manifest_summary(manifest_payload),
    }


def _profile_command(*, repo_root: Path, profile: JsonObject, run_id: str) -> list[str]:
    runner = _string_list(profile["runner"], "runner")
    search_args = _string_list(profile["search_args"], "search_args")
    runs_dir = Path(str(profile["runs_dir"]))
    resolved_runs_dir = runs_dir if runs_dir.is_absolute() else repo_root / runs_dir
    return [
        *runner,
        *search_args,
        "--run-id",
        run_id,
        "--runs-dir",
        str(resolved_runs_dir),
    ]


def _decode_cli_payload(stdout: str) -> JsonObject | None:
    if not stdout.strip():
        return None
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _database_path(*, repo_root: Path, cli_payload: JsonObject | None) -> Path | None:
    if cli_payload is None:
        return None
    artifacts = cli_payload.get("artifacts")
    if not isinstance(artifacts, dict):
        return None
    raw_path = artifacts.get("database")
    if not isinstance(raw_path, str) or not raw_path:
        return None
    path = Path(raw_path)
    return path if path.is_absolute() else repo_root / path


def _processed_payload(database_path: Path | None) -> JsonObject | None:
    if database_path is None or not database_path.exists():
        return None
    with sqlite3.connect(str(database_path)) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            """
            SELECT payload_json
            FROM processed_results
            WHERE phase = 'final'
            ORDER BY append_sequence DESC
            LIMIT 1
            """
        ).fetchone()
    if row is None:
        return None
    payload = json.loads(row["payload_json"])
    return payload if isinstance(payload, dict) else None


def _run_manifest_payload(database_path: Path | None) -> JsonObject | None:
    if database_path is None or not database_path.exists():
        return None
    with sqlite3.connect(str(database_path)) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            """
            SELECT payload_json
            FROM run_manifest
            ORDER BY updated_at DESC
            LIMIT 1
            """
        ).fetchone()
    if row is None:
        return None
    payload = json.loads(row["payload_json"])
    return payload if isinstance(payload, dict) else None


def _processed_summary(payload: JsonObject | None) -> JsonObject:
    if payload is None:
        return {}
    results = _list(payload.get("results"))
    filtered_out = _list(payload.get("filtered_out_results"))
    detail_summary = payload.get("detail_summary")
    channel_summary = payload.get("application_channel_summary")
    return {
        "result_count": payload.get("result_count", len(results)),
        "results": len(results),
        "filtered_out_results": len(filtered_out),
        "detail_summary": detail_summary if isinstance(detail_summary, dict) else {},
        "application_channel_summary": channel_summary if isinstance(channel_summary, dict) else {},
    }


def _attempt_summary(cli_payload: JsonObject | None, source_ids: tuple[str, ...] | None = None) -> JsonObject:
    if cli_payload is None:
        return {}
    attempts = _list(cli_payload.get("attempts"))
    if source_ids is not None:
        allowed_sources = set(source_ids)
        attempts = [
            item
            for item in attempts
            if isinstance(item, dict) and isinstance(item.get("source"), str) and item["source"] in allowed_sources
        ]
    return _attempt_summary_for_attempts(attempts)


def _attempt_summary_for_attempts(attempts: list[Any]) -> JsonObject:
    elapsed = [item.get("elapsed_ms") for item in attempts if isinstance(item, dict)]
    elapsed_ms = [int(value) for value in elapsed if isinstance(value, int)]
    outcomes = Counter(
        item.get("outcome")
        for item in attempts
        if isinstance(item, dict) and isinstance(item.get("outcome"), str)
    )
    raw_written = sum(
        int(item.get("raw_listings_written", 0))
        for item in attempts
        if isinstance(item, dict) and isinstance(item.get("raw_listings_written", 0), int)
    )
    return {
        "attempts": len(attempts),
        "raw_listings_written": raw_written,
        "elapsed_ms_sum": sum(elapsed_ms),
        "elapsed_ms_max": max(elapsed_ms) if elapsed_ms else 0,
        "outcomes": dict(sorted(outcomes.items())),
        "slowest_sources": _slowest_sources(attempts, limit=8),
    }


def _runtime_summary(cli_payload: JsonObject | None) -> JsonObject:
    if cli_payload is None:
        return {}
    runtime_summary = cli_payload.get("runtime_summary")
    return runtime_summary if isinstance(runtime_summary, dict) else {}


def _manifest_summary(payload: JsonObject | None) -> JsonObject:
    if payload is None:
        return {}
    attempts = _list(payload.get("source_attempts"))
    return {
        "raw_records_written_this_call": payload.get("raw_records_written_this_call"),
        "source_attempts": len(attempts),
    }


def _slowest_sources(attempts: list[Any], *, limit: int) -> list[JsonObject]:
    rows: list[JsonObject] = []
    for item in attempts:
        if not isinstance(item, dict):
            continue
        elapsed_ms = item.get("elapsed_ms")
        if not isinstance(elapsed_ms, int):
            continue
        rows.append(
            {
                "source": item.get("source"),
                "elapsed_ms": elapsed_ms,
                "outcome": item.get("outcome"),
                "raw_listings_written": item.get("raw_listings_written"),
            }
        )
    return sorted(rows, key=lambda row: int(row["elapsed_ms"]), reverse=True)[:limit]


def _write_result(*, repo_root: Path, profile: JsonObject, result: JsonObject) -> Path:
    result_dir = repo_root / str(profile["result_dir"])
    result_dir.mkdir(parents=True, exist_ok=True)
    path = result_dir / f"{result['run_id']}.json"
    with path.open("w", encoding="utf-8") as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
    return path


def _print_summary(*, result: JsonObject, result_path: Path) -> None:
    processed = result["processed_summary"]
    attempts = result["attempt_summary"]
    print(f"result_path={result_path}")
    print(f"returncode={result['returncode']}")
    print(f"wall_seconds={result['wall_seconds']}")
    print(f"processed_results={processed.get('result_count')}")
    print(f"filtered_out_results={processed.get('filtered_out_results')}")
    print(f"source_attempts={attempts.get('attempts')}")
    print(f"source_elapsed_ms_max={attempts.get('elapsed_ms_max')}")
    print(f"raw_listings_written={attempts.get('raw_listings_written')}")
    print(f"outcomes={attempts.get('outcomes')}")
    shape_sources = _list(result.get("shape_sources"))
    source_shape = result.get("source_shape_summary")
    if shape_sources and isinstance(source_shape, dict):
        print(f"shape_sources={','.join(str(source) for source in shape_sources)}")
        print(f"shape_source_attempts={source_shape.get('attempts')}")
        print(f"shape_source_elapsed_ms_max={source_shape.get('elapsed_ms_max')}")
        print(f"shape_source_raw_listings_written={source_shape.get('raw_listings_written')}")
        print(f"shape_source_outcomes={source_shape.get('outcomes')}")


def _compare_to_baseline(
    *,
    baseline_path: Path,
    candidate: JsonObject,
    profile: JsonObject,
    min_speedup: float,
    check_shape: bool,
) -> int:
    with baseline_path.open(encoding="utf-8") as handle:
        baseline = json.load(handle)
    shape_sources = _shape_sources(profile)
    baseline_wall = _positive_float(baseline.get("wall_seconds"), "baseline.wall_seconds")
    candidate_wall = _positive_float(candidate.get("wall_seconds"), "candidate.wall_seconds")
    speedup = (baseline_wall - candidate_wall) / baseline_wall
    print(f"baseline_wall_seconds={baseline_wall:.3f}")
    print(f"candidate_wall_seconds={candidate_wall:.3f}")
    print(f"speedup={speedup:.2%}")
    if shape_sources is not None:
        print(f"shape_scope=sources:{','.join(shape_sources)}")
    if candidate["returncode"] != 0:
        print("speed_gate=failed: candidate run failed")
        return 1
    limit_errors = _limit_errors(profile=profile, candidate=candidate)
    if limit_errors:
        for error in limit_errors:
            print(f"speed_gate=failed: {error}")
        return 1
    if check_shape:
        shape_errors = _shape_errors(
            baseline=baseline,
            candidate=candidate,
            policy=_shape_policy(profile),
            source_ids=shape_sources,
        )
        if shape_errors:
            for error in shape_errors:
                print(f"speed_gate=failed: {error}")
            return 1
    if _requires_speedup(profile) and speedup < min_speedup:
        print(f"speed_gate=failed: speedup below {min_speedup:.2%}")
        return 1
    print("speed_gate=passed")
    return 0


def _shape_errors(
    *,
    baseline: JsonObject,
    candidate: JsonObject,
    policy: str,
    source_ids: tuple[str, ...] | None,
) -> tuple[str, ...]:
    if policy == "exact":
        return _exact_shape_errors(baseline=baseline, candidate=candidate, source_ids=source_ids)
    if policy == "at_least_baseline":
        return _at_least_baseline_shape_errors(baseline=baseline, candidate=candidate, source_ids=source_ids)
    if policy == "presentation_at_least":
        return _presentation_at_least_shape_errors(baseline=baseline, candidate=candidate, source_ids=source_ids)
    raise ValueError(f"unsupported shape_policy: {policy}")


def _exact_shape_errors(
    *,
    baseline: JsonObject,
    candidate: JsonObject,
    source_ids: tuple[str, ...] | None,
) -> tuple[str, ...]:
    scope = _shape_scope_label(source_ids)
    checks = _exact_shape_checks(source_ids)
    errors: list[str] = []
    for label, path in checks:
        baseline_value = _shape_metric_value(baseline, path, source_ids)
        candidate_value = _shape_metric_value(candidate, path, source_ids)
        if baseline_value != candidate_value:
            errors.append(f"{scope} {label} changed from {baseline_value!r} to {candidate_value!r}")
    return tuple(errors)


def _at_least_baseline_shape_errors(
    *,
    baseline: JsonObject,
    candidate: JsonObject,
    source_ids: tuple[str, ...] | None,
) -> tuple[str, ...]:
    scope = _shape_scope_label(source_ids)
    errors: list[str] = []
    for label, path in _at_least_baseline_shape_checks(source_ids):
        baseline_value = _shape_metric(baseline, path, source_ids)
        candidate_value = _shape_metric(candidate, path, source_ids)
        if candidate_value < baseline_value:
            errors.append(f"{scope} {label} decreased from {baseline_value!r} to {candidate_value!r}")
    baseline_unhealthy = _unhealthy_outcomes(baseline, source_ids)
    candidate_unhealthy = _unhealthy_outcomes(candidate, source_ids)
    if candidate_unhealthy > baseline_unhealthy:
        errors.append(f"{scope} unhealthy outcomes increased from {baseline_unhealthy!r} to {candidate_unhealthy!r}")
    if _detail_shape_applies(baseline=baseline, candidate=candidate, source_ids=source_ids):
        errors.extend(_detail_shape_errors(baseline=baseline, candidate=candidate, scope=scope))
    return tuple(errors)


def _presentation_at_least_shape_errors(
    *,
    baseline: JsonObject,
    candidate: JsonObject,
    source_ids: tuple[str, ...] | None,
) -> tuple[str, ...]:
    scope = _shape_scope_label(source_ids)
    errors: list[str] = []
    baseline_rows = _shape_metric(baseline, ("processed_summary", "presentation_rows"), source_ids)
    candidate_rows = _shape_metric(candidate, ("processed_summary", "presentation_rows"), source_ids)
    if candidate_rows < baseline_rows:
        errors.append(f"{scope} processed presentation rows decreased from {baseline_rows!r} to {candidate_rows!r}")
    baseline_unhealthy = _unhealthy_outcomes(baseline, source_ids)
    candidate_unhealthy = _unhealthy_outcomes(candidate, source_ids)
    if candidate_unhealthy > baseline_unhealthy:
        errors.append(f"{scope} unhealthy outcomes increased from {baseline_unhealthy!r} to {candidate_unhealthy!r}")
    if _detail_shape_applies(baseline=baseline, candidate=candidate, source_ids=source_ids):
        errors.extend(_detail_shape_errors(baseline=baseline, candidate=candidate, scope=scope))
    return tuple(errors)


def _exact_shape_checks(source_ids: tuple[str, ...] | None) -> tuple[tuple[str, tuple[str, ...]], ...]:
    if source_ids is not None:
        return (
            ("attempt_summary.attempts", ("attempt_summary", "attempts")),
            ("attempt_summary.raw_listings_written", ("attempt_summary", "raw_listings_written")),
            ("attempt_summary.outcomes", ("attempt_summary", "outcomes")),
        )
    return (
        ("processed_summary.result_count", ("processed_summary", "result_count")),
        ("processed_summary.filtered_out_results", ("processed_summary", "filtered_out_results")),
        ("attempt_summary.attempts", ("attempt_summary", "attempts")),
        ("attempt_summary.raw_listings_written", ("attempt_summary", "raw_listings_written")),
        ("attempt_summary.outcomes", ("attempt_summary", "outcomes")),
    )


def _at_least_baseline_shape_checks(source_ids: tuple[str, ...] | None) -> tuple[tuple[str, tuple[str, ...]], ...]:
    checks = (
        ("attempt_summary.attempts", ("attempt_summary", "attempts")),
        ("attempt_summary.raw_listings_written", ("attempt_summary", "raw_listings_written")),
    )
    if source_ids is not None:
        return checks
    return (*checks, ("processed presentation rows", ("processed_summary", "presentation_rows")))


def _shape_metric(payload: JsonObject, path: tuple[str, ...], source_ids: tuple[str, ...] | None) -> int:
    value = _shape_metric_value(payload, path, source_ids)
    return _int_value(value)


def _shape_metric_value(payload: JsonObject, path: tuple[str, ...], source_ids: tuple[str, ...] | None) -> Any:
    if path == ("processed_summary", "presentation_rows"):
        summary = payload.get("processed_summary")
        if not isinstance(summary, dict):
            return 0
        return _int_value(summary.get("result_count")) + _int_value(summary.get("filtered_out_results"))
    if path[0] == "attempt_summary" and source_ids is not None:
        return _nested_value(_comparison_attempt_summary(payload, source_ids), path[1:])
    return _nested_value(payload, path)


def _unhealthy_outcomes(payload: JsonObject, source_ids: tuple[str, ...] | None) -> int:
    outcomes = _nested_value(_comparison_attempt_summary(payload, source_ids), ("outcomes",))
    if not isinstance(outcomes, dict):
        return 0
    return sum(
        _int_value(count)
        for outcome, count in outcomes.items()
        if isinstance(outcome, str) and outcome not in _HEALTHY_OUTCOMES
    )


def _detail_shape_applies(
    *,
    baseline: JsonObject,
    candidate: JsonObject,
    source_ids: tuple[str, ...] | None,
) -> bool:
    if not (_has_detail_summary(baseline) or _has_detail_summary(candidate)):
        return False
    if source_ids is None:
        return True
    attempt_sources = _attempt_sources(baseline) | _attempt_sources(candidate)
    return bool(attempt_sources) and attempt_sources <= set(source_ids)


def _has_detail_summary(payload: JsonObject) -> bool:
    detail = _nested_value(payload, ("processed_summary", "detail_summary"))
    if not isinstance(detail, dict):
        return False
    return any(
        _int_value(detail.get(key)) > 0
        for key in ("total_detail_work_items", "attempted", "enriched", "failed")
    ) or bool(_list(detail.get("stopped_sources")))


def _attempt_sources(payload: JsonObject) -> set[str]:
    stdout_json = payload.get("stdout_json")
    attempts = _list(stdout_json.get("attempts") if isinstance(stdout_json, dict) else None)
    return {
        source
        for item in attempts
        if isinstance(item, dict) and isinstance(source := item.get("source"), str)
    }


def _detail_shape_errors(*, baseline: JsonObject, candidate: JsonObject, scope: str) -> tuple[str, ...]:
    baseline_detail = _detail_summary(baseline)
    candidate_detail = _detail_summary(candidate)
    errors: list[str] = []
    for label in ("total_detail_work_items", "attempted", "enriched"):
        baseline_value = _int_value(baseline_detail.get(label))
        candidate_value = _int_value(candidate_detail.get(label))
        if candidate_value < baseline_value:
            errors.append(f"{scope} detail_summary.{label} decreased from {baseline_value!r} to {candidate_value!r}")
    baseline_failed = _int_value(baseline_detail.get("failed"))
    candidate_failed = _int_value(candidate_detail.get("failed"))
    if candidate_failed > baseline_failed:
        errors.append(f"{scope} detail_summary.failed increased from {baseline_failed!r} to {candidate_failed!r}")
    baseline_stopped = len(_list(baseline_detail.get("stopped_sources")))
    candidate_stopped = len(_list(candidate_detail.get("stopped_sources")))
    if candidate_stopped > baseline_stopped:
        errors.append(
            f"{scope} detail_summary.stopped_sources increased from {baseline_stopped!r} to {candidate_stopped!r}"
        )
    return tuple(errors)


def _detail_summary(payload: JsonObject) -> JsonObject:
    detail = _nested_value(payload, ("processed_summary", "detail_summary"))
    return detail if isinstance(detail, dict) else {}


def _limit_errors(*, profile: JsonObject, candidate: JsonObject) -> tuple[str, ...]:
    errors: list[str] = []
    source_ids = _shape_sources(profile)
    max_wall_seconds = _optional_positive_float(profile.get("max_wall_seconds"), "profile.max_wall_seconds")
    if max_wall_seconds is not None and float(candidate["wall_seconds"]) > max_wall_seconds:
        errors.append(f"wall_seconds exceeded {max_wall_seconds:.3f}")
    max_source_elapsed_ms = _optional_positive_float(
        profile.get("max_source_elapsed_ms"),
        "profile.max_source_elapsed_ms",
    )
    source_summary = _comparison_attempt_summary(candidate, source_ids)
    source_elapsed_ms = _nested_value(source_summary, ("elapsed_ms_max",))
    if max_source_elapsed_ms is not None and _int_value(source_elapsed_ms) > max_source_elapsed_ms:
        errors.append(f"{_shape_scope_label(source_ids)} source_elapsed_ms_max exceeded {max_source_elapsed_ms:.0f}")
    return tuple(errors)


def _comparison_attempt_summary(payload: JsonObject, source_ids: tuple[str, ...] | None) -> JsonObject:
    if source_ids is None:
        summary = payload.get("attempt_summary")
        return summary if isinstance(summary, dict) else {}
    stdout_json = payload.get("stdout_json")
    cli_payload = stdout_json if isinstance(stdout_json, dict) else None
    return _attempt_summary(cli_payload, source_ids=source_ids)


def _nested_value(payload: JsonObject, path: tuple[str, ...]) -> Any:
    current: Any = payload
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _string_list(value: Any, field_name: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"{field_name} must be a list of strings")
    return value


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _int_value(value: Any) -> int:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return 0


def _positive_float(value: Any, field_name: str) -> float:
    if not isinstance(value, int | float) or isinstance(value, bool) or value <= 0:
        raise ValueError(f"{field_name} must be a positive number")
    return float(value)


def _optional_positive_float(value: Any, field_name: str) -> float | None:
    if value is None:
        return None
    return _positive_float(value, field_name)


def _min_speedup(args: argparse.Namespace, profile: JsonObject) -> float:
    if args.min_speedup is not None:
        return _positive_float(args.min_speedup, "--min-speedup")
    value = profile.get("min_wall_speedup", 0.03)
    return _positive_float(value, "profile.min_wall_speedup")


def _shape_policy(profile: JsonObject) -> str:
    policy = profile.get("shape_policy", "exact")
    if policy not in {"exact", "at_least_baseline", "presentation_at_least"}:
        raise ValueError("profile.shape_policy must be exact, at_least_baseline, or presentation_at_least")
    return str(policy)


def _shape_sources(profile: JsonObject) -> tuple[str, ...] | None:
    value = profile.get("shape_sources")
    if value is None:
        return None
    sources = tuple(_string_list(value, "profile.shape_sources"))
    if not sources:
        raise ValueError("profile.shape_sources must not be empty when provided")
    if any(source.strip() != source or not source for source in sources):
        raise ValueError("profile.shape_sources values must be non-empty normalized source ids")
    if len(set(sources)) != len(sources):
        raise ValueError("profile.shape_sources must not contain duplicates")
    return sources


def _shape_scope_label(source_ids: tuple[str, ...] | None) -> str:
    if source_ids is None:
        return "all sources"
    return f"shape_sources[{','.join(source_ids)}]"


def _requires_speedup(profile: JsonObject) -> bool:
    value = profile.get("require_speedup", True)
    if not isinstance(value, bool):
        raise ValueError("profile.require_speedup must be a boolean")
    return value


def _run_id(profile_name: str) -> str:
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    return f"{profile_name}-{stamp}"


def _iso_z(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


if __name__ == "__main__":
    sys.exit(main())
