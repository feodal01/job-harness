#!/usr/bin/env python3
"""Canonical repository verification entrypoint."""

from __future__ import annotations

import argparse
import json
import os
import queue
import shlex
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import IO

ROOT = Path(__file__).resolve().parents[1]
PLUGIN_DIR = ROOT / "plugins" / "job-harness"
PLUGIN_ARG = PLUGIN_DIR.relative_to(ROOT).as_posix()
SECRETS_BASELINE = ROOT / ".secrets.baseline"
SECRET_SCAN_EXCLUDE_REGEX = (
    r"(^|/)(\.git|\.venv|__pycache__|\.mypy_cache|\.ruff_cache|\.pytest_cache|\.job-harness)/"
    r"|uv\.lock$|company-directory\.json$|company-careers-public\.json$"
)

SECRET_SCAN_EXACT_EXCLUDES = {
    ".secrets.baseline",
    "plugins/job-harness/uv.lock",
    "plugins/job-harness/data/company-careers-public.json",
    "plugins/job-harness/data/company-directory.json",
}
SECRET_SCAN_PREFIX_EXCLUDES = (
    ".git/",
    ".mypy_cache/",
    ".ruff_cache/",
    ".pytest_cache/",
    ".job-harness/",
    ".venv/",
    "plugins/job-harness/.venv/",
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "profile",
        nargs="?",
        default="default",
        choices=("default", "full", "lint", "types", "secrets", "tests", "live"),
        help="Verification profile to run.",
    )
    args = parser.parse_args()

    profiles: dict[str, tuple[Callable[[], int], ...]] = {
        "lint": (_run_lint,),
        "types": (_run_types,),
        "secrets": (_run_secrets,),
        "tests": (_run_tests,),
        "live": (
            _run_live_mcp_smoke,
            _run_live_registered_source_smokes,
            _run_live_company_batch_smoke,
        ),
        "default": (_run_lint, _run_types, _run_secrets, _run_tests),
        "full": (
            _run_lint,
            _run_types,
            _run_secrets,
            _run_tests,
            _run_live_mcp_smoke,
            _run_live_registered_source_smokes,
            _run_live_company_batch_smoke,
        ),
    }

    for check in profiles[args.profile]:
        code = check()
        if code != 0:
            return code
    return 0


def _run_lint() -> int:
    return _run(["uv", "--directory", PLUGIN_ARG, "run", "ruff", "check", "src", "scripts", "tests", "../../scripts"])


def _run_types() -> int:
    return _run(["uv", "--directory", PLUGIN_ARG, "run", "mypy", "src/job_harness", "scripts", "tests", "../../scripts"])


def _run_tests() -> int:
    return _run(["uv", "--directory", PLUGIN_ARG, "run", "python", "-m", "unittest", "discover", "-s", "tests", "-v"])


def _run_live_mcp_smoke() -> int:
    config_path = PLUGIN_DIR / ".mcp.json"
    print(f"+ MCP stdio smoke via {config_path.relative_to(ROOT)}", flush=True)
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
        server = config["mcpServers"]["job-harness"]
        cmd = [server["command"], *server["args"]]
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        print(f"live mcp smoke failed: invalid .mcp.json: {exc}", file=sys.stderr)
        return 1

    env = os.environ.copy()
    env["PLUGIN_ROOT"] = str(PLUGIN_DIR)
    env.setdefault("PYTHONUNBUFFERED", "1")
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=ROOT,
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
    except OSError as exc:
        print(f"live mcp smoke failed: could not start server: {exc}", file=sys.stderr)
        return 1

    output: queue.Queue[tuple[str, str]] = queue.Queue()
    assert proc.stdout is not None
    assert proc.stderr is not None
    _start_pipe_reader("stdout", proc.stdout, output)
    _start_pipe_reader("stderr", proc.stderr, output)
    stderr_lines: list[str] = []

    try:
        _write_mcp_message(
            proc,
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "verify-repo", "version": "0"},
                },
            },
        )
        initialize = _read_mcp_response(
            proc,
            output,
            request_id=1,
            timeout_s=15,
            stderr_lines=stderr_lines,
        )
        if "error" in initialize:
            return _fail_live_mcp(f"initialize returned error: {initialize['error']!r}")

        _write_mcp_message(
            proc,
            {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
        )
        _write_mcp_message(
            proc,
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        )
        tools_response = _read_mcp_response(
            proc,
            output,
            request_id=2,
            timeout_s=10,
            stderr_lines=stderr_lines,
        )
        tools_result = tools_response.get("result")
        if not isinstance(tools_result, dict):
            return _fail_live_mcp(f"tools/list returned unexpected result: {tools_result!r}")
        tools = tools_result.get("tools")
        if not isinstance(tools, list):
            return _fail_live_mcp(f"tools/list returned unexpected tools field: {tools!r}")
        tool_names = {
            tool.get("name")
            for tool in tools
            if isinstance(tool, dict)
        }
        required_tools = {
            "list_sources",
            "search_start",
            "search_status",
            "search_results",
        }
        missing_tools = sorted(required_tools - tool_names)
        if missing_tools:
            return _fail_live_mcp(f"tools/list missing required tools: {missing_tools!r}")

        _write_mcp_message(
            proc,
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "list_sources", "arguments": {}},
            },
        )
        call_response = _read_mcp_response(
            proc,
            output,
            request_id=3,
            timeout_s=10,
            stderr_lines=stderr_lines,
        )
        result = call_response.get("result")
        if not isinstance(result, dict):
            return _fail_live_mcp(f"list_sources returned unexpected result: {result!r}")
        if result.get("isError") is True:
            return _fail_live_mcp(f"list_sources returned tool error: {result!r}")
        structured = result.get("structuredContent")
        if not isinstance(structured, dict) or "hh_ru" not in structured:
            return _fail_live_mcp(
                f"list_sources returned unexpected structuredContent keys: {sorted(structured) if isinstance(structured, dict) else type(structured).__name__}"
            )
    except (BrokenPipeError, RuntimeError, TimeoutError, json.JSONDecodeError) as exc:
        return _fail_live_mcp(
            f"{exc}; stderr={_tail(stderr_lines, max_lines=8)!r}",
        )
    finally:
        _terminate_process(proc)

    print("live mcp ok: stdio server started and list_sources responded")
    return 0


def _start_pipe_reader(
    name: str,
    stream: IO[str],
    output: queue.Queue[tuple[str, str]],
) -> None:
    def read_lines() -> None:
        for line in stream:
            output.put((name, line.rstrip("\n")))

    thread = threading.Thread(target=read_lines, daemon=True)
    thread.start()


def _write_mcp_message(proc: subprocess.Popen[str], payload: dict[str, object]) -> None:
    if proc.stdin is None:
        raise RuntimeError("mcp process has no stdin pipe")
    proc.stdin.write(json.dumps(payload, separators=(",", ":")) + "\n")
    proc.stdin.flush()


def _read_mcp_response(
    proc: subprocess.Popen[str],
    output: queue.Queue[tuple[str, str]],
    *,
    request_id: int,
    timeout_s: float,
    stderr_lines: list[str],
) -> dict[str, object]:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"mcp process exited early with code {proc.returncode}")
        try:
            stream_name, line = output.get(timeout=min(0.1, deadline - time.monotonic()))
        except queue.Empty:
            continue
        if stream_name == "stderr":
            stderr_lines.append(line)
            continue
        payload = json.loads(line)
        if payload.get("id") == request_id:
            return payload
    raise TimeoutError(f"mcp response id={request_id} timed out after {timeout_s:g}s")


def _fail_live_mcp(message: str) -> int:
    print(f"live mcp smoke failed: {message}", file=sys.stderr)
    return 1


def _terminate_process(proc: subprocess.Popen[str]) -> None:
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)


def _tail(lines: list[str], *, max_lines: int) -> str:
    return "\n".join(lines[-max_lines:])


# Registered scraper sources returned by `job-harness list-sources`.
# This is not the 400+ company directory. The `company_directory` source
# below is a local JSON lookup with `--max-results 1`; it does not visit
# every employer career page.
_LIVE_REGISTERED_SOURCE_CASES: tuple[tuple[str, str, str], ...] = (
    ("hirehi", "RU", "QA"),
    ("hirify", "RU", "QA"),
    ("staff_am", "AM", "QA"),
    ("geekjob", "RU", "QA"),
    ("talento", "RU", "QA"),
    ("finder_work", "RU", "QA"),
    ("it_jobs_uz", "UZ", "QA"),
    ("jobturbo", "RU", "QA"),
    ("getmatch", "RU", "QA"),
    ("company_directory", "RU", "QA"),
    ("habr_career", "RU", "QA"),
    ("hh_ru", "RU", "QA"),
    ("hh_kz", "KZ", "QA"),
    ("hh_uz", "UZ", "QA"),
    ("rabota_by", "BY", "QA"),
    ("headhunter_kg", "KG", "QA"),
    ("career:ibs", "RU", "QA"),
    ("career:vk", "RU", "QA"),
)


def _run_live_registered_source_smokes() -> int:
    failures: list[str] = []
    coverage_failure = _check_live_source_case_coverage()
    if coverage_failure is not None:
        failures.append(coverage_failure)

    for source, country, query in _LIVE_REGISTERED_SOURCE_CASES:
        failure = _run_live_source_smoke(
            source=source,
            country=country,
            query=query,
        )
        if failure is not None:
            failures.append(failure)

    if failures:
        print("live source smoke failed:", file=sys.stderr)
        for failure in failures:
            print(f"  - {failure}", file=sys.stderr)
        return 1

    print(f"live registered source smoke passed: {len(_LIVE_REGISTERED_SOURCE_CASES)} sources")
    return 0


def _check_live_source_case_coverage() -> str | None:
    cmd = ["uv", "--directory", PLUGIN_ARG, "run", "job-harness", "list-sources", "--json"]
    print("+ " + " ".join(shlex.quote(part) for part in cmd), flush=True)
    result = subprocess.run(
        cmd,
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        return (
            f"list-sources exited {result.returncode}; "
            f"stderr={result.stderr.strip()[:500]!r}; stdout={result.stdout.strip()[:500]!r}"
        )
    try:
        registered = set(json.loads(result.stdout))
    except json.JSONDecodeError as exc:
        return f"list-sources returned invalid JSON: {exc}; stdout={result.stdout.strip()[:500]!r}"

    covered = {source for source, _country, _query in _LIVE_REGISTERED_SOURCE_CASES}
    missing = sorted(registered - covered)
    stale = sorted(covered - registered)
    if missing or stale:
        return f"live source case mismatch: missing={missing!r}, stale={stale!r}"
    return None


def _run_live_source_smoke(*, source: str, country: str, query: str) -> str | None:
    cmd = [
        "uv",
        "--directory",
        PLUGIN_ARG,
        "run",
        "job-harness",
        "search",
        "--query",
        query,
        "--country",
        country,
        "--sources",
        source,
        "--max-results",
        "1",
        "--format",
        "json",
        "--source-timeout-ms",
        "30000",
        "--total-timeout-ms",
        "45000",
    ]
    print("+ " + " ".join(shlex.quote(part) for part in cmd), flush=True)
    try:
        result = subprocess.run(
            cmd,
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
            timeout=60,
        )
    except subprocess.TimeoutExpired:
        return f"{source}: process did not exit within 60s"

    if result.returncode != 0:
        return (
            f"{source}: process exited {result.returncode}; "
            f"stderr={result.stderr.strip()[:500]!r}; stdout={result.stdout.strip()[:500]!r}"
        )

    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        return f"{source}: invalid JSON output: {exc}; stdout={result.stdout.strip()[:500]!r}"

    statuses = {
        status.get("source"): status
        for status in payload.get("summary", {}).get("source_statuses", [])
        if isinstance(status, dict)
    }
    source_status = statuses.get(source) or {}
    errors = payload.get("errors") or []
    total = int(payload.get("total") or 0)
    raw_count = int(source_status.get("raw_count") or 0)
    if (
        errors
        or not source_status
        or source_status.get("state") != "ok"
        or source_status.get("failure_mode") is not None
    ):
        return (
            f"{source}: total={total}, raw_count={raw_count}, "
            f"state={source_status.get('state')!r}, "
            f"failure_mode={source_status.get('failure_mode')!r}, errors={errors!r}"
        )

    print(
        "live source ok: "
        f"{source} total={total}, raw_count={raw_count}, "
        f"duration_ms={source_status.get('duration_ms')}"
    )
    return None


def _run_live_company_batch_smoke() -> int:
    """Exercise the separate employer career-page batch path for the full bundle.

    The registered source smoke covers the local `company_directory`
    lookup. This pass checks the claimed bundled employer coverage by
    visiting every company in `data/company-directory.json`. It disables
    local employer-cache merging so a developer's private cache cannot
    change the verified target set.
    """
    with tempfile.TemporaryDirectory(prefix="job-harness-company-batch-") as tmp:
        tmpdir = Path(tmp)
        output_jsonl = tmpdir / "companies.jsonl"
        summary_json = tmpdir / "summary.json"
        directory_path = PLUGIN_DIR / "data" / "company-directory.json"
        expected_companies = _company_directory_size(directory_path)
        script = """
import asyncio
import json
import sys
from pathlib import Path

from job_harness.company_career_batch import run_company_career_batch


async def main() -> int:
    summary = await run_company_career_batch(
        "QA",
        output_jsonl=Path(sys.argv[2]),
        summary_json=Path(sys.argv[3]),
        workers=12,
        timeout_ms=15000,
        directory_path=Path(sys.argv[1]),
        employer_cache_paths=[],
        headless=True,
    )
    print(json.dumps({
        "companies_considered": summary.get("companies_considered"),
        "companies_recorded": summary.get("companies_recorded"),
        "companies_checked": summary.get("companies_checked"),
        "companies_skipped": summary.get("companies_skipped"),
        "companies_error": summary.get("companies_error"),
        "companies_pending": summary.get("companies_pending"),
        "total": summary.get("total"),
    }, ensure_ascii=False))
    return 0


raise SystemExit(asyncio.run(main()))
"""
        cmd = [
            "uv",
            "--directory",
            PLUGIN_ARG,
            "run",
            "python",
            "-c",
            script,
            str(directory_path),
            str(output_jsonl),
            str(summary_json),
        ]
        print(
            "+ uv --directory "
            f"{shlex.quote(PLUGIN_ARG)} run python <full company-live-batch smoke>",
            flush=True,
        )
        try:
            result = subprocess.run(
                cmd,
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
                timeout=900,
            )
        except subprocess.TimeoutExpired:
            print("live company batch failed: process did not exit within 900s", file=sys.stderr)
            return 1

        if result.returncode != 0:
            print(
                "live company batch failed: "
                f"process exited {result.returncode}; "
                f"stderr={_tail_text(result.stderr, max_lines=20)!r}; "
                f"stdout={_tail_text(result.stdout, max_lines=20)!r}",
                file=sys.stderr,
            )
            return 1

        try:
            summary = json.loads(summary_json.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"live company batch failed: invalid summary: {exc}", file=sys.stderr)
            return 1

        if (
            summary.get("companies_considered") != expected_companies
            or summary.get("companies_recorded") != expected_companies
            or summary.get("companies_error") != 0
            or summary.get("companies_pending") != 0
            or summary.get("companies_skipped") != 0
            or summary.get("errors")
            or summary.get("skipped")
        ):
            print(
                "live company batch failed: "
                f"considered={summary.get('companies_considered')}, "
                f"recorded={summary.get('companies_recorded')}, "
                f"checked={summary.get('companies_checked')}, "
                f"skipped={summary.get('companies_skipped')}, "
                f"errors={summary.get('companies_error')}, "
                f"pending={summary.get('companies_pending')}, "
                f"total={summary.get('total')}",
                file=sys.stderr,
            )
            for item in summary.get("errors", [])[:25]:
                print(
                    "  - "
                    f"{item.get('company')}: {item.get('error')} "
                    f"({item.get('careers_url') or item.get('alternate_url')})",
                    file=sys.stderr,
                )
            for item in summary.get("skipped", [])[:25]:
                print(
                    "  - "
                    f"{item.get('company')}: skipped {item.get('reason')} "
                    f"({item.get('careers_url') or item.get('linkedin_jobs_url')})",
                    file=sys.stderr,
                )
            return 1

        access_issues = summary.get("access_issues") or []
        print(
            "live company batch ok: "
            f"companies_checked={summary.get('companies_checked')}/{expected_companies}, "
            f"access_issues={summary.get('companies_access_issue', 0)}, "
            f"total={summary.get('total')}"
        )
        for item in access_issues[:25]:
            print(
                "live company access issue: "
                f"{item.get('company')}: {item.get('error')} "
                f"({item.get('careers_url') or item.get('alternate_url')}); "
                f"{item.get('remediation')}"
            )
        return 0


def _company_directory_size(path: Path) -> int:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError(f"company directory must be a JSON list: {path}")
    return len(raw)


def _tail_text(text: str, *, max_lines: int) -> str:
    return "\n".join(text.splitlines()[-max_lines:])


def _run_secrets() -> int:
    if not SECRETS_BASELINE.exists():
        print(f"Missing secrets baseline: {SECRETS_BASELINE}", file=sys.stderr)
        print(
            "Generate it with: "
            "uv --directory plugins/job-harness run detect-secrets scan --all-files "
            f"--exclude-files {shlex.quote(SECRET_SCAN_EXCLUDE_REGEX)} > .secrets.baseline",
            file=sys.stderr,
        )
        return 1

    files = _secret_scan_files()
    if not files:
        print("No files selected for secret scanning.")
        return 0

    print(f"# secrets baseline: {SECRETS_BASELINE.relative_to(ROOT)}")
    print(f"# secret scan files: {len(files)}")
    absolute_files = [str(ROOT / path) for path in files]
    return _run(
        [
            "uv",
            "--directory",
            PLUGIN_ARG,
            "run",
            "detect-secrets-hook",
            "--baseline",
            str(SECRETS_BASELINE),
            *absolute_files,
        ]
    )


def _secret_scan_files() -> list[str]:
    result = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=ROOT,
        check=True,
        stdout=subprocess.PIPE,
    )
    files = [part.decode() for part in result.stdout.split(b"\0") if part]
    return [path for path in files if not _is_secret_scan_excluded(path)]


def _is_secret_scan_excluded(path: str) -> bool:
    return path in SECRET_SCAN_EXACT_EXCLUDES or any(path.startswith(prefix) for prefix in SECRET_SCAN_PREFIX_EXCLUDES)


def _run(cmd: list[str]) -> int:
    print("+ " + " ".join(shlex.quote(part) for part in cmd), flush=True)
    return subprocess.run(cmd, cwd=ROOT, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
