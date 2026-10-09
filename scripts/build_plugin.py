"""Build a self-contained plugin wheel and locked runtime; no checkout paths in the installed package."""

import argparse
import hashlib
import json
import shutil
import subprocess
import tempfile
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins/job-search"
RUNTIME = PLUGIN / "runtime"


def source_digest() -> str:
    paths = sorted(p for p in (ROOT / "src").rglob("*") if p.is_file() and "__pycache__" not in p.parts)
    paths += [ROOT / name for name in ("pyproject.toml", "uv.lock", "settings.toml")]
    digest = hashlib.sha256()
    for path in paths:
        digest.update(path.relative_to(ROOT).as_posix().encode() + b"\0" + path.read_bytes())
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Fail if the bundled runtime is stale")
    args = parser.parse_args()
    metadata = RUNTIME / "build.json"
    if args.check:
        saved = json.loads(metadata.read_text())
        assert saved["source_sha256"] == source_digest(), "Rebuild plugin: source files changed"
        wheel = RUNTIME / saved["wheel"]
        assert hashlib.sha256(wheel.read_bytes()).hexdigest() == saved["wheel_sha256"]
        lock = tomllib.loads((RUNTIME / "uv.lock").read_text())
        package = next(p for p in lock["package"] if p["name"] == "hh-search-prototype")
        assert package["wheels"][0]["hash"] == "sha256:" + saved["wheel_sha256"]
        print("Plugin runtime matches source and wheel checksum")
        return
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    version = project["version"]
    manifest = json.loads((PLUGIN / ".codex-plugin/plugin.json").read_text())
    assert manifest["version"] == version, "Plugin and Python package versions must match"
    RUNTIME.mkdir(exist_ok=True)
    for old in RUNTIME.glob("*.whl"):
        old.unlink()
    subprocess.run(["uv", "lock"], cwd=ROOT, check=True)
    # uv marks its build directory as Git-ignored. Build elsewhere so the
    # distributable wheel and lock remain part of the marketplace repository.
    with tempfile.TemporaryDirectory(prefix="job-search-wheel-") as directory:
        subprocess.run(["uv", "build", "--wheel", "--out-dir", directory], cwd=ROOT, check=True)
        built = next(Path(directory).glob("*.whl"))
        wheel = RUNTIME / built.name
        shutil.copyfile(built, wheel)
    (RUNTIME / "pyproject.toml").write_text(
        '[project]\nname = "job-search-plugin-runtime"\n'
        f'version = "{version}"\nrequires-python = ">=3.12,<3.13"\n'
        'dependencies = ["hh-search-prototype"]\n\n[tool.uv.sources]\n'
        f'hh-search-prototype = {{ path = "{wheel.name}" }}\n'
    )
    subprocess.run(
        [
            "uv",
            "lock",
            "--project",
            str(RUNTIME),
            "--refresh-package",
            "hh-search-prototype",
            "--upgrade-package",
            "hh-search-prototype",
        ],
        cwd=ROOT,
        check=True,
    )
    metadata.write_text(
        json.dumps(
            {
                "source_sha256": source_digest(),
                "wheel": wheel.name,
                "wheel_sha256": hashlib.sha256(wheel.read_bytes()).hexdigest(),
            },
            indent=2,
        )
        + "\n"
    )
    print(f"Built {wheel}")


if __name__ == "__main__":
    main()
