from __future__ import annotations

import ast
import unittest
from pathlib import Path

_PLUGIN_ROOT_PARENT_INDEX = 2
_SRC_ROOT = Path(__file__).resolve().parents[_PLUGIN_ROOT_PARENT_INDEX] / "src" / "job_harness" / "v2"
_RUNTIME_PREFIX = "job_harness.v2.runtime"
_CONTRACTS_PREFIX = "job_harness.v2.contracts"
_POSTPROCESSING_PREFIX = "job_harness.v2.postprocessing"
_APPLICATION_MODULE = "job_harness.v2.application"
_CLI_MODULE = "job_harness.v2.cli"
_SOURCE_CATALOG_MODULE = "job_harness.v2.source_catalog"

_ALLOWED_RUNTIME_IMPORTS = {
    "job_harness.v2.runtime.artifacts": (),
    "job_harness.v2.runtime.catalog": (_CONTRACTS_PREFIX,),
    "job_harness.v2.runtime.corpus": (
        _CONTRACTS_PREFIX,
        "job_harness.v2.runtime.artifacts",
        "job_harness.v2.runtime.serialization",
    ),
    "job_harness.v2.runtime.errors": (_CONTRACTS_PREFIX,),
    "job_harness.v2.runtime.http": (
        _CONTRACTS_PREFIX,
        "job_harness.v2.runtime.errors",
    ),
    "job_harness.v2.runtime.orchestrator": (
        _CONTRACTS_PREFIX,
        "job_harness.v2.runtime.catalog",
        "job_harness.v2.runtime.errors",
        "job_harness.v2.runtime.ports",
        "job_harness.v2.runtime.retry",
    ),
    "job_harness.v2.runtime.ports": (_CONTRACTS_PREFIX,),
    "job_harness.v2.runtime.retry": (_CONTRACTS_PREFIX,),
    "job_harness.v2.runtime.run_layout": ("job_harness.v2.runtime.artifacts",),
    "job_harness.v2.runtime.serialization": (),
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
            _RUNTIME_PREFIX,
            _SOURCE_CATALOG_MODULE,
        )
    if module == _APPLICATION_MODULE:
        return (
            _CONTRACTS_PREFIX,
            _POSTPROCESSING_PREFIX,
            _RUNTIME_PREFIX,
        )
    if module == _SOURCE_CATALOG_MODULE:
        return (_CONTRACTS_PREFIX,)
    if module.startswith(f"{_CONTRACTS_PREFIX}."):
        return (_CONTRACTS_PREFIX,)
    if module.startswith(f"{_POSTPROCESSING_PREFIX}."):
        return (
            _CONTRACTS_PREFIX,
            _POSTPROCESSING_PREFIX,
            "job_harness.v2.runtime.serialization",
        )
    if module.startswith(f"{_RUNTIME_PREFIX}.sources."):
        return (
            _CONTRACTS_PREFIX,
            _SOURCE_CATALOG_MODULE,
            "job_harness.v2.runtime.sources",
        )
    return _ALLOWED_RUNTIME_IMPORTS[module]


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


if __name__ == "__main__":
    unittest.main()
