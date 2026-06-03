#!/usr/bin/env python3
"""Canonical repository verification entrypoint."""

from __future__ import annotations

import argparse
import shlex
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

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
        choices=("default", "full", "lint", "types", "secrets", "tests"),
        help="Verification profile to run.",
    )
    args = parser.parse_args()

    profiles: dict[str, tuple[Callable[[], int], ...]] = {
        "lint": (_run_lint,),
        "types": (_run_types,),
        "secrets": (_run_secrets,),
        "tests": (_run_tests,),
        "default": (_run_lint, _run_types, _run_secrets, _run_tests),
        "full": (_run_lint, _run_types, _run_secrets, _run_tests),
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
