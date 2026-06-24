from __future__ import annotations

import ast
import unittest
from pathlib import Path

_PLUGIN_ROOT_PARENT_INDEX = 2
_SRC_ROOT = Path(__file__).resolve().parents[_PLUGIN_ROOT_PARENT_INDEX] / "src" / "job_harness" / "v2"
_RUNTIME_PREFIX = "job_harness.v2.runtime"
_CONTRACTS_PREFIX = "job_harness.v2.contracts"
_POSTPROCESSING_PREFIX = "job_harness.v2.postprocessing"
_PRESENTATION_PREFIX = "job_harness.v2.presentation"
_PERSISTENCE_PREFIX = "job_harness.v2.persistence"
_APPLICATION_MODULE = "job_harness.v2.application"
_CLI_MODULE = "job_harness.v2.cli"
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

_ALLOWED_RUNTIME_IMPORTS = {
    "job_harness.v2.runtime.artifacts": (),
    "job_harness.v2.runtime.catalog": (_CONTRACTS_PREFIX,),
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
    "job_harness.v2.runtime.retry": (_CONTRACTS_PREFIX,),
    "job_harness.v2.runtime.run_layout": ("job_harness.v2.runtime.artifacts",),
    "job_harness.v2.runtime.source_registry": (
        _CONTRACTS_PREFIX,
        "job_harness.v2.runtime.catalog",
        "job_harness.v2.runtime.sources",
        _SOURCE_CATALOG_MODULE,
    ),
}


def _python_modules() -> tuple[Path, ...]:
    return tuple(sorted(path for path in _SRC_ROOT.rglob("*.py") if path.name != "__init__.py"))


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
    if module == _MATCHING_MODULE:
        return ()
    if module == _PORTS_MODULE:
        return (_CONTRACTS_PREFIX, _SERIALIZATION_MODULE)
    if module == _SERIALIZATION_MODULE:
        return ()
    if module.startswith(f"{_CONTRACTS_PREFIX}."):
        return (_CONTRACTS_PREFIX,)
    if module.startswith(f"{_POSTPROCESSING_PREFIX}."):
        return (
            _CONTRACTS_PREFIX,
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


if __name__ == "__main__":
    unittest.main()
