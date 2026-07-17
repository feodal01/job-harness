#!/usr/bin/env python3
"""Enforce size and ownership boundaries for key v2 modules."""

from __future__ import annotations

import argparse
import ast
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
V2_SRC = ROOT / "plugins" / "job-harness" / "src" / "job_harness" / "v2"


@dataclass(frozen=True)
class FileBudget:
    path: Path
    max_lines: int
    reason: str


@dataclass(frozen=True)
class ForbiddenImportRule:
    path: Path
    imports: tuple[str, ...]
    owner: str


@dataclass(frozen=True)
class ForbiddenNameRule:
    path: Path
    names: tuple[str, ...]
    owner: str


@dataclass(frozen=True)
class Finding:
    path: Path
    message: str


POSTPROCESSING_PIPELINE = V2_SRC / "postprocessing" / "pipeline.py"
CONTRACTS_SEARCH = V2_SRC / "contracts" / "search.py"
GEOGRAPHY_INIT = V2_SRC / "geography" / "__init__.py"
GEOGRAPHY_COUNTRIES = V2_SRC / "geography" / "countries.py"
GEOGRAPHY_CITIES = V2_SRC / "geography" / "cities.py"
REMOTE_SCOPE = V2_SRC / "postprocessing" / "remote_scope.py"
GRAPH_PIPELINE = V2_SRC / "runtime" / "graph_pipeline.py"

FILE_BUDGETS = (
    FileBudget(
        path=POSTPROCESSING_PIPELINE,
        max_lines=520,
        reason="post-processing pipeline should coordinate row processing, not own country or remote policy internals",
    ),
    FileBudget(
        path=CONTRACTS_SEARCH,
        max_lines=220,
        reason="search contract should validate request shape, not own normalization indexes",
    ),
    FileBudget(
        path=GEOGRAPHY_INIT,
        max_lines=80,
        reason="geography package exports should stay declarative",
    ),
    FileBudget(
        path=GEOGRAPHY_COUNTRIES,
        max_lines=280,
        reason="geography policy should stay focused on country and region normalization",
    ),
    FileBudget(
        path=GEOGRAPHY_CITIES,
        max_lines=180,
        reason="city geography lookup should stay dataset-backed and separate from country policy",
    ),
    FileBudget(
        path=REMOTE_SCOPE,
        max_lines=280,
        reason="remote scope policy should stay focused on listing geography and remote eligibility evidence",
    ),
    FileBudget(
        path=GRAPH_PIPELINE,
        max_lines=360,
        reason="graph pipeline should compose planning, runner, coordinator, and assembly without owning them",
    ),
)

COUNTRY_POLICY_NAMES = (
    "_COUNTRY_CODE_PATTERN",
    "_COUNTRY_NAME_SEPARATORS",
    "_COUNTRY_WORD_PATTERN",
    "_NON_COUNTRY_TOKENS",
    "_NON_COUNTRY_CODES",
    "_REGION_SCOPE_ALIASES",
    "_REGION_SCOPE_COUNTRIES",
    "_CountryLookup",
    "_GeographyLookup",
    "_country_lookup",
    "_country_code_aliases",
    "_country_name_keys",
    "_strip_accents",
    "_valid_country_code",
    "_normalized_country_code",
    "_country_candidates",
    "_country_code_from_text",
)

REMOTE_POLICY_NAMES = (
    "_GLOBAL_REMOTE_MARKERS",
    "_ONSITE_MARKERS",
    "_REMOTE_MARKERS",
    "_remote_filter_reasons",
    "_vacancy_geography_reasons",
    "_row_remote_scopes",
    "_remote_scopes_match_work_from",
    "_scope_matches_geography",
    "_geography_matches_any",
    "_geography_countries",
    "_remote_scopes",
    "_limited_remote_scopes",
    "_remote_scope_candidates",
    "_scope_from_geography",
    "_remote_scope_text",
    "_raw_mentions_global_remote",
    "_raw_mentions_onsite",
    "_raw_mentions_remote",
    "_raw_remote_tokens",
)

FORBIDDEN_IMPORTS = (
    ForbiddenImportRule(
        path=CONTRACTS_SEARCH,
        imports=("babel", "babel.core", "functools.lru_cache", "unicodedata"),
        owner="job_harness.v2.geography",
    ),
    ForbiddenImportRule(
        path=POSTPROCESSING_PIPELINE,
        imports=("babel", "babel.core", "functools.lru_cache", "unicodedata"),
        owner="job_harness.v2.geography or job_harness.v2.postprocessing.remote_scope",
    ),
)

FORBIDDEN_NAMES = (
    ForbiddenNameRule(
        path=CONTRACTS_SEARCH,
        names=COUNTRY_POLICY_NAMES,
        owner="job_harness.v2.geography",
    ),
    ForbiddenNameRule(
        path=POSTPROCESSING_PIPELINE,
        names=COUNTRY_POLICY_NAMES + REMOTE_POLICY_NAMES,
        owner="job_harness.v2.geography or job_harness.v2.postprocessing.remote_scope",
    ),
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()

    findings = tuple(_findings())
    if findings:
        for finding in findings:
            print(f"{finding.path.relative_to(ROOT)}: {finding.message}", file=sys.stderr)
        return 1
    return 0


def _findings() -> Iterable[Finding]:
    yield from _line_budget_findings(FILE_BUDGETS)
    for path in _paths_with_ast_rules():
        tree = _parse_python(path)
        yield from _forbidden_import_findings(path, tree)
        yield from _forbidden_name_findings(path, tree)


def _line_budget_findings(budgets: tuple[FileBudget, ...]) -> Iterable[Finding]:
    for budget in budgets:
        if not budget.path.exists():
            yield Finding(path=budget.path, message="required structure-checked module is missing")
            continue
        line_count = len(budget.path.read_text(encoding="utf-8").splitlines())
        if line_count > budget.max_lines:
            yield Finding(
                path=budget.path,
                message=(f"{line_count} lines exceeds budget {budget.max_lines}; {budget.reason}"),
            )


def _paths_with_ast_rules() -> tuple[Path, ...]:
    return tuple(sorted({rule.path for rule in FORBIDDEN_IMPORTS} | {rule.path for rule in FORBIDDEN_NAMES}))


def _parse_python(path: Path) -> ast.Module:
    try:
        return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except SyntaxError as exc:
        raise RuntimeError(f"could not parse {path}: {exc}") from exc


def _forbidden_import_findings(path: Path, tree: ast.Module) -> Iterable[Finding]:
    imported_names = _imported_names(tree)
    for rule in FORBIDDEN_IMPORTS:
        if rule.path != path:
            continue
        for blocked in rule.imports:
            if any(_import_matches(imported, blocked) for imported in imported_names):
                yield Finding(
                    path=path,
                    message=f"imports {blocked}; move that ownership to {rule.owner}",
                )


def _imported_names(tree: ast.Module) -> tuple[str, ...]:
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        if isinstance(node, ast.ImportFrom) and node.module is not None:
            names.append(node.module)
            names.extend(f"{node.module}.{alias.name}" for alias in node.names)
    return tuple(names)


def _import_matches(imported: str, blocked: str) -> bool:
    return imported == blocked or imported.startswith(f"{blocked}.")


def _forbidden_name_findings(path: Path, tree: ast.Module) -> Iterable[Finding]:
    defined_names = _defined_names(tree)
    for rule in FORBIDDEN_NAMES:
        if rule.path != path:
            continue
        for name in sorted(set(rule.names) & defined_names):
            yield Finding(
                path=path,
                message=f"defines {name}; move that ownership to {rule.owner}",
            )


def _defined_names(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            names.add(node.name)
        if isinstance(node, ast.Assign):
            for target in node.targets:
                names.update(_target_names(target))
        if isinstance(node, ast.AnnAssign):
            names.update(_target_names(node.target))
    return names


def _target_names(target: ast.expr) -> set[str]:
    if isinstance(target, ast.Name):
        return {target.id}
    if isinstance(target, ast.Tuple | ast.List):
        return {name for item in target.elts for name in _target_names(item)}
    return set()


if __name__ == "__main__":
    raise SystemExit(main())
