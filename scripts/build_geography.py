"""Build the packaged GeoNames city SQLite from fixed local inputs (CLDR: scripts/build_cldr.py)."""

import argparse
import hashlib
import io
import json
import sqlite3
from collections import Counter
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
from zipfile import ZipFile

NON_NAME_TAGS = {"link", "wkdt", "post", "iata", "icao", "faac", "unlc", "nuts", "lauc", "phon"}


def rows(path: Path, member: str):
    with ZipFile(path) as archive, archive.open(member) as stream:
        for line in io.TextIOWrapper(stream, encoding="utf-8"):
            yield line.rstrip("\r\n").split("\t")


def key(value: str) -> str:
    import unicodedata

    return unicodedata.normalize("NFC", " ".join(value.casefold().split()))


def build(source: Path, output: Path, iso: Path) -> dict:
    codes = set(json.loads(iso.read_text(encoding="utf-8"))["codes"])
    regions = {}
    for line in (source / "admin1CodesASCII.txt").read_text(encoding="utf-8").splitlines():
        code, name, ascii_name, gid = line.split("\t")
        regions[code] = (int(gid), name, ascii_name)
    inputs = {}
    for name in ("cities15000.zip", "alternateNamesV2.zip", "admin1CodesASCII.txt"):
        with (source / name).open("rb") as f:
            inputs[name] = {
                "sha256": hashlib.file_digest(f, "sha256").hexdigest(),
                "url": f"https://download.geonames.org/export/dump/{name}",
            }
    output.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=output, prefix=".geography-") as temporary:
        database = Path(temporary) / "cities.sqlite"
        city_countries = {}
        region_countries = {}
        languages: Counter[str] = Counter()
        with closing(sqlite3.connect(database)) as c:
            c.executescript("""
                PRAGMA user_version=1;
                CREATE TABLE cities (
                    geoname_id INTEGER PRIMARY KEY, country TEXT NOT NULL, name TEXT NOT NULL,
                    ascii_name TEXT NOT NULL, region_id INTEGER, region TEXT,
                    latitude REAL NOT NULL, longitude REAL NOT NULL, population INTEGER NOT NULL
                );
                CREATE TABLE names (
                    city_id INTEGER NOT NULL REFERENCES cities(geoname_id), country TEXT NOT NULL,
                    name TEXT NOT NULL, search_key TEXT NOT NULL, language TEXT NOT NULL,
                    preferred INTEGER NOT NULL, historic INTEGER NOT NULL, colloquial INTEGER NOT NULL
                );
                CREATE TABLE region_names (region_id INTEGER NOT NULL, name TEXT NOT NULL,
                    search_key TEXT NOT NULL,
                    language TEXT NOT NULL, preferred INTEGER NOT NULL);
                CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            """)
            city_rows, names = [], []
            for r in rows(source / "cities15000.zip", "cities15000.txt"):
                gid, country = int(r[0]), r[8]
                if country not in codes:
                    # GeoNames includes non-ISO geographic codes; those cannot be chosen as ISO countries.
                    continue
                if not r[2] or not r[2].isascii():
                    raise ValueError(f"Missing ASCII city name: {gid}")
                region = regions.get(f"{country}.{r[10]}")
                region_id, region_name = (region[0], region[1]) if region else (None, None)
                if region:
                    region_countries[region_id] = country
                city_countries[gid] = country
                city_rows.append(
                    (gid, country, r[1], r[2], region_id, region_name, float(r[4]), float(r[5]), int(r[14]))
                )
                for name in sorted({r[1], r[2]}):
                    names.append((gid, country, name, key(name), "", 0, 0, 0))
            c.executemany("INSERT INTO cities VALUES (?,?,?,?,?,?,?,?,?)", city_rows)
            c.executemany("INSERT INTO names VALUES (?,?,?,?,?,?,?,?)", names)
            names.clear()
            for gid, name, ascii_name in regions.values():
                if gid in region_countries:
                    for label in sorted({name, ascii_name}):
                        c.execute(
                            "INSERT INTO region_names VALUES (?,?,?,?,?)", (gid, label, key(label), "", 0)
                        )
            for r in rows(source / "alternateNamesV2.zip", "alternateNamesV2.txt"):
                gid = int(r[1])
                if r[2] in NON_NAME_TAGS or not r[3].strip():
                    continue
                if gid in city_countries:
                    names.append(
                        (
                            gid,
                            city_countries[gid],
                            r[3],
                            key(r[3]),
                            r[2],
                            int(r[4] == "1"),
                            int(r[7] == "1"),
                            int(r[6] == "1"),
                        )
                    )
                    languages[r[2]] += 1
                    if len(names) >= 10000:
                        c.executemany("INSERT INTO names VALUES (?,?,?,?,?,?,?,?)", names)
                        names.clear()
                if gid in region_countries and r[6] != "1" and r[7] != "1":
                    c.execute(
                        "INSERT INTO region_names VALUES (?,?,?,?,?)",
                        (gid, r[3], key(r[3]), r[2], int(r[4] == "1")),
                    )
            c.executemany("INSERT INTO names VALUES (?,?,?,?,?,?,?,?)", names)
            c.execute("CREATE INDEX name_lookup ON names(country,search_key,city_id)")
            c.execute("CREATE INDEX city_names ON names(city_id,language)")
            c.execute("CREATE INDEX region_lookup ON region_names(region_id,search_key)")
            metadata = {
                "format_version": 1,
                "dataset": "cities15000",
                "cities": len(city_countries),
                "country_codes": len(set(city_countries.values())),
                "languages": dict(sorted(languages.items())),
                "inputs": inputs,
                "iso_sha256": hashlib.sha256(iso.read_bytes()).hexdigest(),
                "attribution": "GeoNames, CC BY 4.0",
            }
            c.execute("INSERT INTO metadata VALUES ('build',?)", (json.dumps(metadata, ensure_ascii=False),))
            c.execute("ANALYZE")
            c.commit()
            if c.execute("PRAGMA integrity_check").fetchone() != ("ok",):
                raise ValueError("Invalid SQLite artifact")
            if c.execute("PRAGMA foreign_key_check").fetchall():
                raise ValueError("Invalid city name references")
        database.replace(output / "cities.sqlite")
    return metadata


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--iso", type=Path, default=Path("src/geography/data/iso3166.json"))
    parser.add_argument("--output", type=Path, default=Path("src/geography/data"))
    args = parser.parse_args()
    result = build(args.source, args.output, args.iso)
    print(json.dumps({k: v for k, v in result.items() if k not in {"languages", "inputs"}}, indent=2))
