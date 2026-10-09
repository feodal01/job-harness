"""Build the bundled GeoNames snapshot from explicitly downloaded source files."""

import argparse
import gzip
import hashlib
import io
import json
from datetime import date
from pathlib import Path
from zipfile import ZipFile

# Run from the repository root: python -m scripts.build_geography SOURCE_DIRECTORY.
from src.geography.countries import COUNTRIES

SOURCES = {
    "cities500.zip": "https://download.geonames.org/export/dump/cities500.zip",
    "admin1CodesASCII.txt": "https://download.geonames.org/export/dump/admin1CodesASCII.txt",
    "alternateNamesV2.zip": "https://download.geonames.org/export/dump/alternateNamesV2.zip",
}


def build(source: Path, output: Path, downloaded_on: date) -> None:
    cities = {}
    with ZipFile(source / "cities500.zip") as archive:
        with archive.open("cities500.txt") as raw:
            for line in io.TextIOWrapper(raw, encoding="utf-8"):
                row = line.rstrip("\n").split("\t")
                if row[8] in COUNTRIES:
                    cities[row[0]] = row
    regions = {}
    for line in (source / "admin1CodesASCII.txt").read_text().splitlines():
        key, name, ascii_name, gid = line.split("\t")
        if key.split(".")[0] in COUNTRIES:
            regions[key] = (gid, name, ascii_name)
    selected = set(cities) | {r[0] for r in regions.values()}
    aliases: dict[str, set[str]] = {gid: set() for gid in selected}
    russian: dict[str, list[tuple[int, str]]] = {}
    with ZipFile(source / "alternateNamesV2.zip") as archive:
        with archive.open("alternateNamesV2.txt") as raw:
            for line in io.TextIOWrapper(raw, encoding="utf-8"):
                row = line.rstrip("\n").split("\t")
                gid = row[1]
                if gid not in selected or row[2] not in {"ru", "en"} or row[6] == "1" or row[7] == "1":
                    continue
                aliases[gid].add(row[3])
                if row[2] == "ru":
                    russian.setdefault(gid, []).append((0 if row[4] == "1" else 1, row[3]))

    def label(gid: str, default: str) -> str:
        return min(russian[gid])[1] if gid in russian else default

    rows = []
    for gid, row in sorted(cities.items(), key=lambda item: int(item[0])):
        name = label(gid, row[1])
        region = regions.get(f"{row[8]}.{row[10]}")
        region_name = label(region[0], region[1]) if region else None
        # Federal cities have no separate region selector.
        if region_name == name:
            region_name = None
        names = sorted(aliases[gid] | {row[1], row[2], name})
        rows.append([row[8], name, region_name, int(row[14]), names])
    output.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(rows, ensure_ascii=False, separators=(",", ":")).encode()
    (output / "cities.json.gz").write_bytes(gzip.compress(payload, mtime=0))
    metadata = {
        "format_version": 1,
        "downloaded_on": downloaded_on.isoformat(),
        "attribution": "GeoNames geographical database",
        "license": "https://creativecommons.org/licenses/by/4.0/",
        "homepage": "https://www.geonames.org/",
        "coverage": "cities500: populated places over 500 people or administrative seats; 32 countries",
        "countries": list(COUNTRIES),
        "cities": len(rows),
        "inputs": [
            {"file": name, "url": url, "sha256": hashlib.sha256((source / name).read_bytes()).hexdigest()}
            for name, url in SOURCES.items()
        ],
        "snapshot_sha256": hashlib.sha256((output / "cities.json.gz").read_bytes()).hexdigest(),
    }
    (output / "metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n")
    print(f"Built {len(rows)} cities in {len(COUNTRIES)} countries: {output}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--output", type=Path, default=Path("src/geography/data"))
    parser.add_argument("--downloaded-on", type=date.fromisoformat, required=True)
    args = parser.parse_args()
    build(args.source, args.output, args.downloaded_on)
