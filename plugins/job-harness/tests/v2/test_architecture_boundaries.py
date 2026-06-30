from __future__ import annotations

import ast
import re
import unittest
from pathlib import Path

_PLUGIN_ROOT_PARENT_INDEX = 2
_SRC_ROOT = Path(__file__).resolve().parents[_PLUGIN_ROOT_PARENT_INDEX] / "src" / "job_harness" / "v2"
_SOURCE_ROOT = _SRC_ROOT / "runtime" / "sources"
_RUNTIME_PREFIX = "job_harness.v2.runtime"
_CONTRACTS_PREFIX = "job_harness.v2.contracts"
_POSTPROCESSING_PREFIX = "job_harness.v2.postprocessing"
_PRESENTATION_PREFIX = "job_harness.v2.presentation"
_PERSISTENCE_PREFIX = "job_harness.v2.persistence"
_APPLICATION_MODULE = "job_harness.v2.application"
_CLI_MODULE = "job_harness.v2.cli"
_GEOGRAPHY_MODULE = "job_harness.v2.geography"
_GEOGRAPHY_CITIES_MODULE = "job_harness.v2.geography.cities"
_GEOGRAPHY_COUNTRIES_MODULE = "job_harness.v2.geography.countries"
_MATCHING_MODULE = "job_harness.v2.matching"
_PORTS_MODULE = "job_harness.v2.ports"
_SERIALIZATION_MODULE = "job_harness.v2.serialization"
_SOURCE_CATALOG_MODULE = "job_harness.v2.source_catalog"
_FILESYSTEM_ALLOWED_MODULES = {
    _APPLICATION_MODULE,
    _CLI_MODULE,
    _SOURCE_CATALOG_MODULE,
    "job_harness.v2.persistence.sqlite_run_store",
    "job_harness.v2.presentation.report",
    "job_harness.v2.runtime.config",
    "job_harness.v2.runtime.pipeline",
    "job_harness.v2.runtime.run_layout",
}
_FILESYSTEM_IMPORTS = {
    "sqlite3",
}
_FILESYSTEM_CALLS = {
    "exists",
    "mkdir",
    "read_text",
    "write_text",
}
_FILESYSTEM_MODULE_CALLS = {
    ("files", "importlib.resources"),
}
_COUNTRY_NAME_PATTERN = re.compile(r"country", re.I)
_SOURCE_GEOGRAPHY_MAPPING_NAME_PATTERN = re.compile(r"(country|city|geography|geo|location)", re.I)
_ISO_COUNTRY_CODE_PATTERN = re.compile(r"^[A-Z]{2}$")
_COUNTRY_NORMALIZATION_HELPER_NAMES = frozenset(
    {
        "_city_country_lookup",
        "_city_to_country",
        "_country_from_text",
        "_country_from_city",
        "_first_country_code",
        "_is_country_code_token",
    }
)

_ALLOWED_RUNTIME_IMPORTS = {
    "job_harness.v2.runtime.application_channels": (
        _CONTRACTS_PREFIX,
        _PORTS_MODULE,
        _SERIALIZATION_MODULE,
        "job_harness.v2.runtime.application_channel_profiles",
        "job_harness.v2.runtime.application_channel_records",
        "job_harness.v2.runtime.application_channel_resolver",
        "job_harness.v2.runtime.application_channel_sources",
        "job_harness.v2.runtime.config",
    ),
    "job_harness.v2.runtime.application_channel_profiles": (
        _CONTRACTS_PREFIX,
        _PORTS_MODULE,
        "job_harness.v2.runtime.application_channel_resolver",
        "job_harness.v2.runtime.application_channel_sources",
        "job_harness.v2.runtime.errors",
    ),
    "job_harness.v2.runtime.application_channel_records": (
        _CONTRACTS_PREFIX,
        _SERIALIZATION_MODULE,
    ),
    "job_harness.v2.runtime.application_channel_resolver": (
        _CONTRACTS_PREFIX,
        _PORTS_MODULE,
        "job_harness.v2.runtime.application_channel_sources",
        "job_harness.v2.runtime.errors",
    ),
    "job_harness.v2.runtime.application_channel_sources": (_CONTRACTS_PREFIX,),
    "job_harness.v2.runtime.artifacts": (),
    "job_harness.v2.runtime.catalog": (_CONTRACTS_PREFIX,),
    "job_harness.v2.runtime.config": (
        "job_harness.v2.runtime.retry",
        _SERIALIZATION_MODULE,
    ),
    "job_harness.v2.runtime.detail_enrichment": (
        _CONTRACTS_PREFIX,
        _PORTS_MODULE,
        "job_harness.v2.runtime.catalog",
        "job_harness.v2.runtime.config",
        "job_harness.v2.runtime.errors",
    ),
    "job_harness.v2.runtime.errors": (_CONTRACTS_PREFIX,),
    "job_harness.v2.runtime.http": (
        _CONTRACTS_PREFIX,
        "job_harness.v2.runtime.errors",
    ),
    "job_harness.v2.runtime.orchestrator": (
        _CONTRACTS_PREFIX,
        _PORTS_MODULE,
        "job_harness.v2.runtime.catalog",
        "job_harness.v2.runtime.errors",
        "job_harness.v2.runtime.retry",
    ),
    "job_harness.v2.runtime.pipeline": (
        _CONTRACTS_PREFIX,
        _PORTS_MODULE,
        _POSTPROCESSING_PREFIX,
        _PRESENTATION_PREFIX,
        _SERIALIZATION_MODULE,
        "job_harness.v2.runtime.application_channels",
        "job_harness.v2.runtime.application_channel_profiles",
        "job_harness.v2.runtime.application_channel_records",
        "job_harness.v2.runtime.application_channel_resolver",
        "job_harness.v2.runtime.application_channel_sources",
        "job_harness.v2.runtime.catalog",
        "job_harness.v2.runtime.config",
        "job_harness.v2.runtime.detail_enrichment",
        "job_harness.v2.runtime.http",
        "job_harness.v2.runtime.orchestrator",
        "job_harness.v2.runtime.run_layout",
        "job_harness.v2.runtime.source_registry",
    ),
    "job_harness.v2.runtime.retry": (_CONTRACTS_PREFIX,),
    "job_harness.v2.runtime.run_layout": ("job_harness.v2.runtime.artifacts",),
    "job_harness.v2.runtime.source_registry": (
        _CONTRACTS_PREFIX,
        "job_harness.v2.runtime.catalog",
        "job_harness.v2.runtime.sources",
        _SOURCE_CATALOG_MODULE,
    ),
}
_PURE_HELPER_MODULES = {
    _GEOGRAPHY_CITIES_MODULE,
    _MATCHING_MODULE,
    _SERIALIZATION_MODULE,
}
_DIRECT_ALLOWED_PREFIXES = {
    _GEOGRAPHY_COUNTRIES_MODULE: (_GEOGRAPHY_CITIES_MODULE,),
}


def _python_modules() -> tuple[Path, ...]:
    return tuple(sorted(path for path in _SRC_ROOT.rglob("*.py") if path.name != "__init__.py"))


def _source_modules() -> tuple[Path, ...]:
    return tuple(sorted(path for path in _SOURCE_ROOT.rglob("*.py") if path.name != "__init__.py"))


def _module_name(path: Path) -> str:
    relative = path.relative_to(_SRC_ROOT).with_suffix("")
    return ".".join(("job_harness", "v2", *relative.parts))


def _internal_imports(path: Path) -> tuple[str, ...]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imports: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names if alias.name.startswith("job_harness"))
        if (
            isinstance(node, ast.ImportFrom)
            and node.level == 0
            and node.module is not None
            and node.module.startswith("job_harness")
        ):
            imports.append(node.module)
    return tuple(imports)


def _allowed_prefixes(module: str) -> tuple[str, ...]:
    if module in _PURE_HELPER_MODULES:
        return ()
    direct_allowed = _DIRECT_ALLOWED_PREFIXES.get(module)
    if direct_allowed is not None:
        return direct_allowed
    if module == _CLI_MODULE:
        return (
            _APPLICATION_MODULE,
            _CONTRACTS_PREFIX,
            _PERSISTENCE_PREFIX,
            _POSTPROCESSING_PREFIX,
            _PRESENTATION_PREFIX,
            _PORTS_MODULE,
            _RUNTIME_PREFIX,
            _SERIALIZATION_MODULE,
            _SOURCE_CATALOG_MODULE,
        )
    if module == _APPLICATION_MODULE:
        return (
            _CONTRACTS_PREFIX,
            _PERSISTENCE_PREFIX,
            _POSTPROCESSING_PREFIX,
            _PRESENTATION_PREFIX,
            _PORTS_MODULE,
            _RUNTIME_PREFIX,
            _SERIALIZATION_MODULE,
        )
    if module == _SOURCE_CATALOG_MODULE:
        return (_CONTRACTS_PREFIX,)
    if module == _PORTS_MODULE:
        return (_CONTRACTS_PREFIX, _SERIALIZATION_MODULE)
    if module.startswith(f"{_CONTRACTS_PREFIX}."):
        return (_CONTRACTS_PREFIX, _GEOGRAPHY_MODULE)
    if module.startswith(f"{_POSTPROCESSING_PREFIX}."):
        return (
            _CONTRACTS_PREFIX,
            _GEOGRAPHY_MODULE,
            _MATCHING_MODULE,
            _POSTPROCESSING_PREFIX,
            _SERIALIZATION_MODULE,
        )
    if module.startswith(f"{_PRESENTATION_PREFIX}."):
        return (
            _PRESENTATION_PREFIX,
            _SERIALIZATION_MODULE,
        )
    if module.startswith(f"{_PERSISTENCE_PREFIX}."):
        return (
            _CONTRACTS_PREFIX,
            _PERSISTENCE_PREFIX,
            _PORTS_MODULE,
            _SERIALIZATION_MODULE,
        )
    if module.startswith(f"{_RUNTIME_PREFIX}.sources."):
        return (
            _CONTRACTS_PREFIX,
            _SOURCE_CATALOG_MODULE,
            "job_harness.v2.runtime.sources",
        )
    return _ALLOWED_RUNTIME_IMPORTS[module]


def _filesystem_references(path: Path) -> tuple[str, ...]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    references: list[str] = []
    import_aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in _FILESYSTEM_IMPORTS:
                    references.append(f"import {alias.name}")
                import_aliases[alias.asname or alias.name] = alias.name
        if isinstance(node, ast.ImportFrom) and node.module is not None:
            root_module = node.module.split(".", maxsplit=1)[0]
            if root_module in _FILESYSTEM_IMPORTS:
                references.append(f"from {node.module}")
            for alias in node.names:
                import_aliases[alias.asname or alias.name] = node.module
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                module = import_aliases.get(node.func.id)
                if (node.func.id, module) in _FILESYSTEM_MODULE_CALLS:
                    references.append(f"{module}.{node.func.id}()")
            if isinstance(node.func, ast.Attribute) and node.func.attr in _FILESYSTEM_CALLS:
                references.append(f".{node.func.attr}()")
    return tuple(references)


def _assigned_name(target: ast.expr) -> str | None:
    if isinstance(target, ast.Name):
        return target.id
    if isinstance(target, ast.Attribute):
        return target.attr
    return None


def _is_collection_literal(value: ast.expr | None) -> bool:
    return isinstance(value, (ast.Dict, ast.List, ast.Set, ast.Tuple))


def _call_name(value: ast.expr) -> str | None:
    if isinstance(value, ast.Name):
        return value.id
    if isinstance(value, ast.Attribute):
        return value.attr
    return None


def _source_country_policy_violations(path: Path) -> tuple[str, ...]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            violations.extend(_source_country_assignment_violations(tuple(node.targets), node.value))
        if isinstance(node, ast.AnnAssign):
            violations.extend(_source_country_assignment_violations((node.target,), node.value))
        if isinstance(node, ast.FunctionDef) and node.name in _COUNTRY_NORMALIZATION_HELPER_NAMES:
            violations.append(f"{node.name}()")
        if isinstance(node, ast.Call) and _call_name(node.func) == "RawListing":
            violations.extend(_raw_listing_country_violations(node))
    return tuple(violations)


def _source_country_assignment_violations(targets: tuple[ast.expr, ...], value: ast.expr | None) -> tuple[str, ...]:
    if not _is_collection_literal(value):
        return ()
    violations: list[str] = []
    has_iso_country_code = _collection_literal_has_iso_country_code(value)
    for target in targets:
        name = _assigned_name(target)
        if name and _COUNTRY_NAME_PATTERN.search(name):
            violations.append(f"{name} collection")
        if name and _SOURCE_GEOGRAPHY_MAPPING_NAME_PATTERN.search(name) and has_iso_country_code:
            violations.append(f"{name} geography mapping")
    return tuple(violations)


def _raw_listing_country_violations(node: ast.Call) -> tuple[str, ...]:
    violations: list[str] = []
    for keyword in node.keywords:
        if (
            keyword.arg == "country"
            and isinstance(keyword.value, ast.Constant)
            and isinstance(keyword.value.value, str)
            and _ISO_COUNTRY_CODE_PATTERN.fullmatch(keyword.value.value)
        ):
            violations.append(f'RawListing(country="{keyword.value.value}")')
    return tuple(violations)


def _collection_literal_has_iso_country_code(value: ast.expr | None) -> bool:
    if isinstance(value, ast.Dict):
        return any(_ast_value_has_iso_country_code(item) for item in (*value.keys, *value.values))
    if isinstance(value, (ast.List, ast.Set, ast.Tuple)):
        return any(_ast_value_has_iso_country_code(item) for item in value.elts)
    return False


def _ast_value_has_iso_country_code(value: ast.expr | None) -> bool:
    if isinstance(value, ast.Constant) and isinstance(value.value, str):
        return bool(_ISO_COUNTRY_CODE_PATTERN.fullmatch(value.value))
    if isinstance(value, ast.Dict):
        return _collection_literal_has_iso_country_code(value)
    if isinstance(value, (ast.List, ast.Set, ast.Tuple)):
        return _collection_literal_has_iso_country_code(value)
    return False


class V2ArchitectureBoundaryTest(unittest.TestCase):
    def test_v2_source_modules_do_not_import_legacy_job_harness_layers(self) -> None:
        for path in _python_modules():
            with self.subTest(path=path.relative_to(_SRC_ROOT)):
                # Arrange / Act
                imports = _internal_imports(path)

                # Assert
                legacy_imports = tuple(module for module in imports if not module.startswith("job_harness.v2"))
                self.assertEqual((), legacy_imports)

    def test_v2_layer_dependencies_point_downward_or_to_same_layer_helpers(self) -> None:
        for path in _python_modules():
            with self.subTest(path=path.relative_to(_SRC_ROOT)):
                # Arrange
                module = _module_name(path)
                allowed_prefixes = _allowed_prefixes(module)

                # Act
                imports = tuple(module for module in _internal_imports(path) if module.startswith("job_harness.v2"))

                # Assert
                illegal_imports = tuple(
                    imported
                    for imported in imports
                    if not any(imported == prefix or imported.startswith(f"{prefix}.") for prefix in allowed_prefixes)
                )
                self.assertEqual((), illegal_imports)

    def test_filesystem_and_database_access_stays_in_boundary_modules(self) -> None:
        for path in _python_modules():
            with self.subTest(path=path.relative_to(_SRC_ROOT)):
                # Arrange
                module = _module_name(path)

                # Act
                references = _filesystem_references(path)

                # Assert
                if module in _FILESYSTEM_ALLOWED_MODULES:
                    continue
                self.assertEqual((), references)

    def test_source_parsers_do_not_hardcode_country_normalization(self) -> None:
        for path in _source_modules():
            with self.subTest(path=path.relative_to(_SRC_ROOT)):
                # Arrange / Act
                violations = _source_country_policy_violations(path)

                # Assert
                self.assertEqual((), violations)


if __name__ == "__main__":
    unittest.main()
