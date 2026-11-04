"""Build the single CLDR catalog (src/geography/data/cldr.json.gz) from one CLDR version.

One version, one truth (src/geography/README.md): country names, alternative names, groups and
their members all come from the same CLDR release; nothing else in the repository holds CLDR data.
Inputs are fixed local copies of the Unicode CLDR JSON packages (nothing is downloaded here):

    cldr-localenames-full/main/<locale>/territories.json   names of countries and groups
    cldr-core/supplemental/territoryContainment.json       which countries a group contains

    .venv/bin/python scripts/build_cldr.py /path/to/cldr-localenames-full /path/to/cldr-core
"""

import argparse
import gzip
import hashlib
import json
from pathlib import Path

ALT = ("-alt-short", "-alt-variant")


def expand(code: str, containment: dict[str, list[str]], countries: set[str]) -> set[str]:
    """ISO countries inside a CLDR region or grouping, following nested regions."""
    result: set[str] = set()
    for part in containment.get(code, ()):
        result |= {part} if part in countries else expand(part, containment, countries)
    return result


def build(localenames: Path, core: Path, iso: Path, output: Path) -> dict:
    countries = set(json.loads(iso.read_text(encoding="utf-8"))["codes"])
    supplemental = json.loads((core / "supplemental/territoryContainment.json").read_text(encoding="utf-8"))
    version = supplemental["supplemental"]["version"]["_cldrVersion"]
    raw = supplemental["supplemental"]["territoryContainment"]
    # `-status-*` entries are deprecated or alternative containments, not the current one.
    containment = {k: v["_contains"] for k, v in raw.items() if "-status-" not in k}
    groups = {
        code: {
            "countries": sorted(expand(code, containment, countries)),
            "grouping": raw[code].get("_grouping") == "true",
        }
        for code in sorted(containment)
    }
    locales: dict[str, dict[str, str]] = {}
    alternates: dict[str, dict[str, list[str]]] = {}
    group_names: dict[str, dict[str, str]] = {}
    hashes = {}
    package = json.loads((localenames / "package.json").read_text(encoding="utf-8"))
    if package["version"].split(".")[0] != version:
        raise ValueError(f"CLDR packages differ: {package['version']} vs {version}")
    for path in sorted((localenames / "main").glob("*/territories.json")):
        locale = path.parent.name
        names = json.loads(path.read_text(encoding="utf-8"))["main"][locale]["localeDisplayNames"][
            "territories"
        ]
        hashes[locale] = hashlib.sha256(path.read_bytes()).hexdigest()
        tag = locale.replace("_", "-")
        # A display locale must name every ISO country with its own name.
        if all(isinstance(names.get(code), str) and names[code] != code for code in countries):
            locales[tag] = {code: names[code] for code in sorted(countries)}
        alt = {
            code: sorted({names[code + suffix] for suffix in ALT if code + suffix in names})
            for code in sorted(countries)
            if any(code + suffix in names for suffix in ALT)
        }
        if alt:
            alternates[tag] = alt
        found = {
            code: names[code] for code in groups if isinstance(names.get(code), str) and names[code] != code
        }
        if found:
            group_names[tag] = found
    if "en" not in locales:
        raise ValueError("CLDR English must cover every ISO country")
    if "EU" not in groups or not groups["EU"]["countries"]:
        raise ValueError("CLDR territory containment must list the EU members")
    metadata = {
        "format_version": 1,
        "cldr_version": version,
        "sources": {
            "cldr-localenames-full": f"https://registry.npmjs.org/cldr-localenames-full/-/cldr-localenames-full-{package['version']}.tgz",
            "cldr-core": f"https://registry.npmjs.org/cldr-core/-/cldr-core-{package['version']}.tgz",
        },
        "territories_sha256": hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest(),
        "containment_sha256": hashlib.sha256(
            (core / "supplemental/territoryContainment.json").read_bytes()
        ).hexdigest(),
        "iso_sha256": hashlib.sha256(iso.read_bytes()).hexdigest(),
        "license": "Unicode License V3",
        "countries": len(countries),
        "locales": len(locales),
        "groups": len(groups),
        "alternate_locales": len(alternates),
        "group_name_locales": len(group_names),
    }
    payload = {
        "metadata": metadata,
        "countries": locales["en"],
        "locales": locales,
        "alternates": alternates,
        "groups": groups,
        "group_names": group_names,
    }
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()
    (output / "cldr.json.gz").write_bytes(gzip.compress(data, mtime=0))
    (output / "UNICODE-LICENSE.txt").write_bytes((localenames / "LICENSE").read_bytes())
    return metadata


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("localenames", type=Path, help="cldr-localenames-full package directory")
    parser.add_argument("core", type=Path, help="cldr-core package directory")
    parser.add_argument("--iso", type=Path, default=Path("src/geography/data/iso3166.json"))
    parser.add_argument("--output", type=Path, default=Path("src/geography/data"))
    args = parser.parse_args()
    print(json.dumps(build(args.localenames, args.core, args.iso, args.output), indent=2))
