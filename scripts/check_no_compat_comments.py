#!/usr/bin/env python3
"""Reject comments that justify compatibility shims in active development code."""

from __future__ import annotations

import argparse
import re
import sys
import tokenize
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PATHS = (
    ROOT / "plugins" / "job-harness" / "src" / "job_harness" / "v2",
    ROOT / "plugins" / "job-harness" / "tests" / "v2",
    ROOT / "scripts",
)
TEXT_SUFFIXES = {".css", ".html", ".js", ".sql", ".ts"}
PYTHON_SUFFIX = ".py"
SKIP_PARTS = {
    ".git",
    ".job-harness",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "fixtures",
}
BLOCKED_PATTERNS = (
    re.compile(r"\bfallbacks?\b", re.IGNORECASE),
    re.compile(r"\bfall\s+backs?\b", re.IGNORECASE),
    re.compile(r"\bback(?:ward|wards)?[-_\s]+compat(?:ibility|ible)?\b", re.IGNORECASE),
    re.compile(r"\bcompat(?:ibility|ible)\b", re.IGNORECASE),
    re.compile(r"обратн\w*\s+совместим\w*", re.IGNORECASE),
    re.compile(r"совместим\w*", re.IGNORECASE),
)


@dataclass(frozen=True)
class Finding:
    path: Path
    line: int
    text: str


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="*", type=Path, default=list(DEFAULT_PATHS))
    args = parser.parse_args()

    findings = tuple(_scan_paths(args.paths))
    if findings:
        for finding in findings:
            rel_path = finding.path.relative_to(ROOT)
            print(f"{rel_path}:{finding.line}: {finding.text.strip()}", file=sys.stderr)
        print(
            "Do not add compatibility shims in comments. The plugin is in active early development; "
            "change the contract directly and update callers/tests instead.",
            file=sys.stderr,
        )
        return 1
    return 0


def _scan_paths(paths: Iterable[Path]) -> Iterable[Finding]:
    for path in paths:
        resolved = path if path.is_absolute() else ROOT / path
        if not resolved.exists():
            continue
        if resolved.is_file():
            yield from _scan_file(resolved)
            continue
        for child in sorted(resolved.rglob("*")):
            if child.is_file():
                yield from _scan_file(child)


def _scan_file(path: Path) -> Iterable[Finding]:
    if _should_skip(path):
        return ()
    if path.suffix == PYTHON_SUFFIX:
        return tuple(_scan_python_comments(path))
    if path.suffix in TEXT_SUFFIXES:
        return tuple(_scan_text_comments(path))
    return ()


def _should_skip(path: Path) -> bool:
    rel = path.relative_to(ROOT)
    return any(part in SKIP_PARTS for part in rel.parts)


def _scan_python_comments(path: Path) -> Iterable[Finding]:
    try:
        with path.open("rb") as handle:
            tokens = tokenize.tokenize(handle.readline)
            for token in tokens:
                if token.type == tokenize.COMMENT and _is_blocked(token.string):
                    yield Finding(path=path, line=token.start[0], text=token.string)
    except (OSError, SyntaxError, tokenize.TokenError) as exc:
        raise RuntimeError(f"could not scan {path}: {exc}") from exc


def _scan_text_comments(path: Path) -> Iterable[Finding]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except UnicodeDecodeError:
        return ()
    findings: list[Finding] = []
    for index, line in enumerate(lines, start=1):
        stripped = line.lstrip()
        if _starts_comment(stripped) and _is_blocked(stripped):
            findings.append(Finding(path=path, line=index, text=stripped))
    return tuple(findings)


def _starts_comment(value: str) -> bool:
    return value.startswith(("#", "//", "/*", "*", "<!--", "--"))


def _is_blocked(value: str) -> bool:
    return any(pattern.search(value) for pattern in BLOCKED_PATTERNS)


if __name__ == "__main__":
    raise SystemExit(main())
