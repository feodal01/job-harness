"""The single geography reference of the application (src/geography/README.md).

One object holds both reference sources, each in exactly one version: Unicode CLDR
(`data/cldr.json.gz`: country names, alternative names, groups) and GeoNames (`data/cities.sqlite`).
Briefs, the geography API and vacancy enrichment use the same instance (`default_catalog()`),
so a brief and a vacancy are always resolved against the same data.
"""

import gzip
import json
import sqlite3
import unicodedata
from collections import defaultdict
from contextlib import closing
from dataclasses import dataclass
from functools import cache, cached_property
from pathlib import Path
from typing import Any, Literal

from src.core.errors import InvalidSearchError
from src.geography.names import split_city

DATA = Path(__file__).with_name("data")
DEFAULT_LOCALE = "en"


def normalize(value: str) -> str:
    return unicodedata.normalize("NFC", " ".join(value.casefold().split()))


@dataclass(frozen=True)
class Resolved[T]:
    """Result of resolving a label: one value, several candidates, or nothing."""

    status: Literal["found", "ambiguous", "not_found"]
    value: T | None = None
    candidates: tuple[T, ...] = ()


def _one[T](values: set[T]) -> Resolved[T]:
    if len(values) == 1:
        return Resolved("found", next(iter(values)))
    if values:
        return Resolved("ambiguous", candidates=tuple(sorted(values)))  # type: ignore[type-var]
    return Resolved("not_found")


class GeographyCatalog:
    def __init__(
        self, cldr_path: Path = DATA / "cldr.json.gz", cities_path: Path = DATA / "cities.sqlite"
    ) -> None:
        self.cldr = json.loads(gzip.decompress(cldr_path.read_bytes()))
        if self.cldr["metadata"]["format_version"] != 1:
            raise RuntimeError("Unsupported CLDR catalog format")
        self._city_regions: dict[int, str | None] = {}
        self.path = cities_path.resolve(strict=True)
        self.uri = self.path.as_uri() + "?mode=ro"
        with closing(self.connect()) as c:
            if c.execute("PRAGMA user_version").fetchone()[0] != 1:
                raise RuntimeError("Unsupported geography database format")
            if c.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise RuntimeError("Corrupt geography database")
            metadata = json.loads(c.execute("SELECT value FROM metadata WHERE key='build'").fetchone()[0])
            if metadata["format_version"] != 1 or metadata["dataset"] != "cities15000":
                raise RuntimeError("Unexpected geography dataset")

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.uri, uri=True)
        connection.row_factory = sqlite3.Row
        return connection

    # --- Countries (CLDR) -------------------------------------------------------------------

    @cached_property
    def codes(self) -> frozenset[str]:
        return frozenset(self.cldr["countries"])

    def country_names(self, locale: str = DEFAULT_LOCALE) -> dict[str, str]:
        locales = self.cldr["locales"]
        if locale not in locales:
            raise InvalidSearchError(f"Unsupported geography locale: {locale}")
        return locales[locale]

    @cached_property
    def _country_index(self) -> dict[str, frozenset[str]]:
        """Normalized country name or alternative name in any CLDR locale → ISO codes."""
        index: defaultdict[str, set[str]] = defaultdict(set)
        for names in self.cldr["locales"].values():
            for code, name in names.items():
                index[normalize(name)].add(code)
        for alternates in self.cldr["alternates"].values():
            for code, names in alternates.items():
                for name in names:
                    index[normalize(name)].add(code)
        return {name: frozenset(codes) for name, codes in index.items()}

    @cached_property
    def _group_index(self) -> dict[str, frozenset[str]]:
        index: defaultdict[str, set[str]] = defaultdict(set)
        for names in self.cldr["group_names"].values():
            for code, name in names.items():
                index[normalize(name)].add(code)
        return {name: frozenset(codes) for name, codes in index.items()}

    def country(self, label: str) -> Resolved[str]:
        """ISO code from a code or a country name in any CLDR language; no guessing."""
        text = label.strip()
        if len(text) == 2 and text.upper() in self.codes:
            return Resolved("found", text.upper())
        return _one(set(self._country_index.get(normalize(text), ())))

    def group(self, label: str) -> Resolved[str]:
        """A CLDR region or grouping (`150` Europe, `EU`, `001` world) by its name in any language."""
        return _one(set(self._group_index.get(normalize(label.strip()), ())))

    @cached_property
    def _group_members(self) -> dict[str, frozenset[str]]:
        return {code: frozenset(group["countries"]) for code, group in self.cldr["groups"].items()}

    def group_countries(self, code: str) -> frozenset[str]:
        return self._group_members.get(code, frozenset())

    # --- Cities and regions (GeoNames) ------------------------------------------------------

    def search(self, country: str, text: str, locale: str = DEFAULT_LOCALE) -> list[dict[str, Any]]:
        """City suggestions for the brief editor."""
        labels = self.country_names(locale)
        if country not in self.codes:
            raise InvalidSearchError("Choose an ISO country from the catalog")
        query = normalize(text)
        if not 1 <= len(query) <= 100:
            raise InvalidSearchError("Enter 1–100 city characters")
        with closing(self.connect()) as c:
            found = c.execute(
                """
                WITH candidates AS (
                    SELECT city_id, name, search_key,
                        ROW_NUMBER() OVER (PARTITION BY city_id ORDER BY
                            CASE WHEN search_key=? THEN 0 ELSE 1 END,
                            historic, colloquial, preferred DESC, length(name), name, language) AS position
                    FROM names WHERE country=? AND search_key>=? AND search_key<?
                )
                SELECT cities.*, candidates.name AS display_name, candidates.search_key=? AS exact
                FROM candidates JOIN cities ON cities.geoname_id=candidates.city_id
                WHERE position=1 ORDER BY exact DESC, population DESC, geoname_id LIMIT 20
            """,
                (query, country, query, query + "\U0010ffff", query),
            ).fetchall()
        return [self.response(dict(row), row["display_name"], labels[country]) for row in found]

    def response(self, city: dict[str, Any], display_name: str, country_label: str) -> dict[str, Any]:
        return {
            "geoname_id": city["geoname_id"],
            "country": city["country"],
            "country_name_en": self.country_names()[city["country"]],
            "name": display_name,
            "ascii_name": city["ascii_name"],
            "region": city["region"],
            "path": [country_label] + ([city["region"]] if city["region"] else []),
        }

    def get(self, geoname_id: int) -> dict[str, Any]:
        with closing(self.connect()) as c:
            row = c.execute("SELECT * FROM cities WHERE geoname_id=?", (geoname_id,)).fetchone()
        if row is None:
            raise InvalidSearchError("Choose a city from the suggestions")
        return dict(row)

    def resolve(self, country: str, geoname_id: int, display_name: str) -> dict[str, Any]:
        """Confirm a brief's selected city: the ID belongs to the country and the name is its alias."""
        city = self.get(geoname_id)
        if city["country"] != country:
            raise InvalidSearchError("Selected city does not belong to the country")
        with closing(self.connect()) as c:
            alias = c.execute(
                "SELECT name FROM names WHERE country=? AND search_key=? AND city_id=? "
                "ORDER BY historic,colloquial,preferred DESC,name LIMIT 1",
                (country, normalize(display_name), geoname_id),
            ).fetchone()
        if alias is None:
            raise InvalidSearchError("Select the full city name from the suggestions")
        return self.response(city, alias["name"], self.country_names()[country])

    def label(self, geoname_id: int, language: str) -> str:
        city = self.get(geoname_id)
        with closing(self.connect()) as c:
            alias = c.execute(
                "SELECT name FROM names WHERE city_id=? AND language=? "
                "AND historic=0 AND colloquial=0 ORDER BY preferred DESC,name LIMIT 1",
                (geoname_id, language),
            ).fetchone()
        # A source request uses an available localized name, otherwise the canonical ASCII name.
        return alias["name"] if alias else city["ascii_name"]

    def matches(self, geoname_id: int, actual: str, regions: tuple[str, ...] = ()) -> bool | None:
        """Does a city label (with optional region qualifiers) denote the given city?"""
        city = self.get(geoname_id)
        name, qualifier = split_city(actual)
        with closing(self.connect()) as c:
            candidates = {
                r[0]: r[1]
                for r in c.execute(
                    "SELECT DISTINCT city_id,region_id FROM names "
                    "JOIN cities ON cities.geoname_id=names.city_id "
                    "WHERE names.country=? AND search_key=?",
                    (city["country"], normalize(name)),
                )
            }
            if geoname_id not in candidates:
                return False if candidates else None
            for region in (*regions, *([qualifier] if qualifier else [])):
                region_ids = {
                    row[0]
                    for row in c.execute(
                        "SELECT DISTINCT region_names.region_id FROM region_names "
                        "JOIN cities ON cities.region_id=region_names.region_id "
                        "WHERE cities.country=? AND region_names.search_key=?",
                        (city["country"], normalize(region)),
                    )
                }
                if region_ids:
                    candidates = {gid: rid for gid, rid in candidates.items() if rid in region_ids}
                    if geoname_id not in candidates:
                        return False
                elif region == qualifier:
                    # An explicit but unknown qualifier cannot establish city identity.
                    return None
        return True if len(candidates) == 1 else None

    def region(self, country: str, label: str) -> Resolved[int]:
        """GeoNames first-level region (`region_id`) by its name in any language."""
        with closing(self.connect()) as c:
            rows = c.execute(
                "SELECT DISTINCT region_names.region_id FROM region_names "
                "JOIN cities ON cities.region_id=region_names.region_id "
                "WHERE cities.country=? AND region_names.search_key=?",
                (country, normalize(label)),
            ).fetchall()
        return _one({row[0] for row in rows})

    def city(self, country: str, label: str, region_label: str | None = None) -> Resolved[int]:
        """GeoNames city by its name in any language; an optional region narrows homonyms."""
        with closing(self.connect()) as c:
            rows = c.execute(
                "SELECT DISTINCT names.city_id, cities.region_id FROM names "
                "JOIN cities ON cities.geoname_id=names.city_id "
                "WHERE names.country=? AND names.search_key=?",
                (country, normalize(label)),
            ).fetchall()
        found = {row[0]: row[1] for row in rows}
        if region_label is not None and len(found) > 1:
            wanted = self.region(country, region_label)
            if wanted.status == "found":
                found = {gid: rid for gid, rid in found.items() if rid == wanted.value}
        return _one(set(found))

    def region_name(self, region_id: int) -> str | None:
        """Canonical GeoNames name of a region, the value briefs store in SearchPlace.region."""
        with closing(self.connect()) as c:
            row = c.execute("SELECT region FROM cities WHERE region_id=? LIMIT 1", (region_id,)).fetchone()
        return row[0] if row else None

    def city_region(self, geoname_id: int) -> str | None:
        # Read-only reference: a brief city's region is looked up once, not per compared vacancy place.
        if geoname_id not in self._city_regions:
            self._city_regions[geoname_id] = self.get(geoname_id)["region"]
        return self._city_regions[geoname_id]


@cache
def default_catalog() -> GeographyCatalog:
    """The process-wide instance; the application passes it explicitly to its components."""
    return GeographyCatalog()
