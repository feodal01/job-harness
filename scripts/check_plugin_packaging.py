#!/usr/bin/env python3
"""Validate cross-host job-harness plugin packaging."""

from __future__ import annotations

import json
import sys
import tomllib
from pathlib import Path
from typing import cast

REQUIRED_JSON_PATHS = (
    ".agents/plugins/marketplace.json",
    ".claude-plugin/marketplace.json",
    ".cursor-plugin/marketplace.json",
    "plugins/job-harness/.codex-plugin/plugin.json",
    "plugins/job-harness/.claude-plugin/plugin.json",
    "plugins/job-harness/.cursor-plugin/plugin.json",
)
EXPECTED_NAME = "job-harness"
EXPECTED_VERSION = "0.5.1"
PLUGIN_SOURCE = "./plugins/job-harness"
EXPECTED_CURSOR_COMPONENT_PATHS = {
    "skills": "./skills/",
    "commands": "./commands/",
    "agents": "./agents/",
    "mcpServers": "./.mcp.json",
}


def _read_json(root: Path, relative_path: str, errors: list[str]) -> dict[str, object] | None:
    path = root / relative_path
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        errors.append(f"invalid JSON in {relative_path}: {exc}")
        return None
    if not isinstance(payload, dict):
        errors.append(f"invalid JSON object in {relative_path}")
        return None
    return cast(dict[str, object], payload)


def _first_plugin_entry(
    manifest: dict[str, object],
    relative_path: str,
    errors: list[str],
) -> dict[str, object] | None:
    plugins = manifest.get("plugins")
    if not isinstance(plugins, list) or len(plugins) != 1 or not isinstance(plugins[0], dict):
        errors.append(f"{relative_path} must contain exactly one plugin entry")
        return None
    return cast(dict[str, object], plugins[0])


def _require_name(payload: dict[str, object], context: str, errors: list[str]) -> None:
    if payload.get("name") != EXPECTED_NAME:
        errors.append(f"{context} name must be {EXPECTED_NAME!r}")


def _read_project_version(root: Path, errors: list[str]) -> str | None:
    relative_path = "plugins/job-harness/pyproject.toml"
    try:
        payload = tomllib.loads((root / relative_path).read_text(encoding="utf-8"))
        project = payload["project"]
        if not isinstance(project, dict):
            raise TypeError("project must be a table")
        version = project["version"]
        if not isinstance(version, str):
            raise TypeError("project.version must be a string")
        return version
    except (OSError, KeyError, TypeError, tomllib.TOMLDecodeError) as exc:
        errors.append(f"invalid project version in {relative_path}: {exc}")
        return None


def _read_lock_version(root: Path, errors: list[str]) -> str | None:
    relative_path = "plugins/job-harness/uv.lock"
    try:
        payload = tomllib.loads((root / relative_path).read_text(encoding="utf-8"))
        packages = payload["package"]
        if not isinstance(packages, list):
            raise TypeError("package must be an array")
        matches = [
            package
            for package in packages
            if isinstance(package, dict) and package.get("name") == EXPECTED_NAME
        ]
        if len(matches) != 1:
            raise ValueError(f"expected one {EXPECTED_NAME!r} package, found {len(matches)}")
        source = matches[0].get("source")
        if not isinstance(source, dict) or source.get("editable") != ".":
            errors.append(f"{relative_path} {EXPECTED_NAME} source must be editable '.'")
        version = matches[0].get("version")
        if not isinstance(version, str):
            raise TypeError("package version must be a string")
        return version
    except (OSError, KeyError, TypeError, ValueError, tomllib.TOMLDecodeError) as exc:
        errors.append(f"invalid package version in {relative_path}: {exc}")
        return None


def validate_packaging(root: Path) -> tuple[str, ...]:
    errors = [
        f"missing required manifest: {relative_path}"
        for relative_path in REQUIRED_JSON_PATHS
        if not (root / relative_path).is_file()
    ]
    manifests = {
        relative_path: manifest
        for relative_path in REQUIRED_JSON_PATHS
        if (root / relative_path).is_file()
        and (manifest := _read_json(root, relative_path, errors)) is not None
    }

    version_values: dict[str, str | None] = {
        "plugins/job-harness/pyproject.toml": _read_project_version(root, errors),
        "plugins/job-harness/uv.lock": _read_lock_version(root, errors),
    }

    for relative_path, manifest in manifests.items():
        _require_name(manifest, relative_path, errors)
        if relative_path.endswith("/plugin.json"):
            version = manifest.get("version")
            version_values[relative_path] = version if isinstance(version, str) else None

    for relative_path in (
        ".agents/plugins/marketplace.json",
        ".claude-plugin/marketplace.json",
        ".cursor-plugin/marketplace.json",
    ):
        manifest = manifests.get(relative_path)
        if manifest is None:
            continue
        entry = _first_plugin_entry(manifest, relative_path, errors)
        if entry is None:
            continue
        _require_name(entry, f"{relative_path} plugin entry", errors)
        if relative_path == ".agents/plugins/marketplace.json":
            source = entry.get("source")
            if not isinstance(source, dict) or source.get("path") != PLUGIN_SOURCE:
                errors.append(f"{relative_path} source.path must be {PLUGIN_SOURCE!r}")
            continue
        if entry.get("source") != PLUGIN_SOURCE:
            errors.append(f"{relative_path} source must be {PLUGIN_SOURCE!r}")
        version = entry.get("version")
        version_values[f"{relative_path} plugin entry"] = version if isinstance(version, str) else None

    codex_manifest = manifests.get("plugins/job-harness/.codex-plugin/plugin.json")
    if codex_manifest is not None:
        for field, expected in {"skills": "./skills/", "mcpServers": "./.mcp.json"}.items():
            if codex_manifest.get(field) != expected:
                errors.append(f"Codex manifest {field} must be {expected!r}")

    claude_manifest = manifests.get("plugins/job-harness/.claude-plugin/plugin.json")
    if claude_manifest is not None and claude_manifest.get("mcpServers") != "./.mcp.json":
        errors.append("Claude manifest mcpServers must be './.mcp.json'")

    cursor_manifest = manifests.get("plugins/job-harness/.cursor-plugin/plugin.json")
    if cursor_manifest is not None:
        for field, expected in EXPECTED_CURSOR_COMPONENT_PATHS.items():
            if cursor_manifest.get(field) != expected:
                errors.append(f"Cursor manifest {field} must be {expected!r}")

    for context, version in version_values.items():
        if version != EXPECTED_VERSION:
            errors.append(f"{context} version must be {EXPECTED_VERSION!r}, got {version!r}")

    return tuple(errors)


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    errors = validate_packaging(root)
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return 1
    print(f"plugin packaging ok: version {EXPECTED_VERSION}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
