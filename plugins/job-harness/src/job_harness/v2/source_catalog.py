"""SQLite-backed source catalog for the contract-first engine."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from functools import cache
from importlib.resources import files
from typing import cast

from job_harness.v2.contracts import (
    ALL_SEARCH_CRITERIA,
    CriterionCapability,
    ParserFixtureCase,
    ParserFixtureKind,
    ParserFixtureSuite,
    ParserRef,
    RequiredParserFixtures,
    SearchCriterion,
    SourceDescriptor,
    SourceType,
    Transport,
)

_CATALOG_RESOURCE = "source_catalog.sql"


@dataclass(frozen=True)
class CountryCatalogEntry:
    """Country row available to v2 source selection and future reference tools."""

    country_code: str
    display_name: str
    search_enabled: bool


@dataclass(frozen=True)
class SourceFixtureRecord:
    """Catalog row for one required real parser fixture."""

    name: str
    kind: ParserFixtureKind
    captured_artifact_path: str
    metadata_path: str
    golden_path: str
    real_capture: bool
    golden_reviewed_by: str

    def as_case(self) -> ParserFixtureCase:
        return ParserFixtureCase(
            name=self.name,
            kind=self.kind,
            captured_artifact_path=self.captured_artifact_path,
            metadata_path=self.metadata_path,
            golden_path=self.golden_path,
            real_capture=self.real_capture,
            golden_reviewed_by=self.golden_reviewed_by,
        )


@dataclass(frozen=True)
class ListingParserBinding:
    """Explicit catalog binding from a source id to a pinned listing parser."""

    source_id: str
    source_type: SourceType
    parser_ref: ParserRef

    def __post_init__(self) -> None:
        if not self.source_id.strip():
            raise ValueError("source_id must be non-empty")


@dataclass(frozen=True)
class SourceCatalogEntry:
    """ORM-style source row used to derive runtime contracts."""

    source_id: str
    source_type: SourceType
    transport: Transport
    countries: tuple[str, ...]
    source_limit: int
    identity_namespace: str | None
    listing_parser_ref: ParserRef
    native_request_criteria: frozenset[SearchCriterion]
    structured_output_criteria: frozenset[SearchCriterion]
    required_fixture_kinds: RequiredParserFixtures
    fixture_records: tuple[SourceFixtureRecord, ...]

    def __post_init__(self) -> None:
        overlap = self.native_request_criteria & self.structured_output_criteria
        if overlap:
            names = ", ".join(sorted(criterion.value for criterion in overlap))
            raise ValueError(f"native and structured criteria overlap for {self.source_id}: {names}")

    def descriptor(self) -> SourceDescriptor:
        return SourceDescriptor.from_capabilities(
            source_id=self.source_id,
            source_type=self.source_type,
            transport=self.transport,
            countries=self.countries,
            source_limit=self.source_limit,
            capabilities=self.capabilities(),
            identity_namespace=self.identity_namespace,
        )

    def capabilities(self) -> dict[SearchCriterion, CriterionCapability]:
        values = dict.fromkeys(ALL_SEARCH_CRITERIA, CriterionCapability.UNSUPPORTED)
        for criterion in self.native_request_criteria:
            values[criterion] = CriterionCapability.NATIVE_REQUEST
        for criterion in self.structured_output_criteria:
            values[criterion] = CriterionCapability.STRUCTURED_OUTPUT
        return values

    def fixture_suite(self) -> ParserFixtureSuite:
        return ParserFixtureSuite(
            source_id=self.source_id,
            cases=tuple(record.as_case() for record in self.fixture_records),
        )

    def listing_binding(self) -> ListingParserBinding:
        return ListingParserBinding(
            source_id=self.source_id,
            source_type=self.source_type,
            parser_ref=self.listing_parser_ref,
        )


def source_catalog_entries() -> tuple[SourceCatalogEntry, ...]:
    return _load_catalog()


def listing_parser_bindings() -> tuple[ListingParserBinding, ...]:
    return tuple(entry.listing_binding() for entry in source_catalog_entries())


def country_catalog_entries() -> tuple[CountryCatalogEntry, ...]:
    return _load_countries()


def source_catalog_entry(source_id: str) -> SourceCatalogEntry:
    for entry in source_catalog_entries():
        if entry.source_id == source_id:
            return entry
    raise ValueError(f"unknown v2 source catalog entry: {source_id}")


def source_descriptor(source_id: str) -> SourceDescriptor:
    return source_catalog_entry(source_id).descriptor()


def source_required_fixture_kinds(source_id: str) -> RequiredParserFixtures:
    return source_catalog_entry(source_id).required_fixture_kinds


def source_fixture_suite(source_id: str) -> ParserFixtureSuite:
    return source_catalog_entry(source_id).fixture_suite()


@cache
def _load_catalog() -> tuple[SourceCatalogEntry, ...]:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    try:
        connection.executescript(_catalog_sql())
        return _read_entries(connection)
    finally:
        connection.close()


@cache
def _load_countries() -> tuple[CountryCatalogEntry, ...]:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    try:
        connection.executescript(_catalog_sql())
        return _read_countries_catalog(connection)
    finally:
        connection.close()


def _catalog_sql() -> str:
    return files("job_harness.v2").joinpath(_CATALOG_RESOURCE).read_text(encoding="utf-8")


def _read_countries_catalog(connection: sqlite3.Connection) -> tuple[CountryCatalogEntry, ...]:
    rows = _fetch_rows(
        connection,
        """
        SELECT country_code, display_name, search_enabled
        FROM countries
        ORDER BY country_code
        """,
    )
    return tuple(
        CountryCatalogEntry(
            country_code=_row_text(row, "country_code"),
            display_name=_row_text(row, "display_name"),
            search_enabled=_row_bool(row, "search_enabled"),
        )
        for row in rows
    )


def _read_entries(connection: sqlite3.Connection) -> tuple[SourceCatalogEntry, ...]:
    rows = _fetch_rows(
        connection,
        """
        SELECT source_id, source_type, transport, source_limit, identity_namespace,
               listing_parser_id, listing_parser_version
        FROM sources
        ORDER BY sort_order
        """,
    )
    return tuple(_entry_from_row(connection, row) for row in rows)


def _entry_from_row(connection: sqlite3.Connection, row: sqlite3.Row) -> SourceCatalogEntry:
    source_id = _row_text(row, "source_id")
    criteria = _read_criteria(connection, source_id)
    fixture_records = _read_fixture_records(connection, source_id)
    return SourceCatalogEntry(
        source_id=source_id,
        source_type=SourceType(_row_text(row, "source_type")),
        transport=Transport(_row_text(row, "transport")),
        countries=_read_countries(connection, source_id),
        source_limit=_row_int(row, "source_limit"),
        identity_namespace=_row_optional_text(row, "identity_namespace"),
        listing_parser_ref=ParserRef(
            _row_text(row, "listing_parser_id"),
            _row_text(row, "listing_parser_version"),
        ),
        native_request_criteria=frozenset(
            criterion
            for criterion, capability in criteria.items()
            if capability == CriterionCapability.NATIVE_REQUEST
        ),
        structured_output_criteria=frozenset(
            criterion
            for criterion, capability in criteria.items()
            if capability == CriterionCapability.STRUCTURED_OUTPUT
        ),
        required_fixture_kinds=_read_required_fixture_kinds(connection, source_id, fixture_records),
        fixture_records=fixture_records,
    )


def _read_countries(connection: sqlite3.Connection, source_id: str) -> tuple[str, ...]:
    rows = _fetch_rows(
        connection,
        """
        SELECT country
        FROM source_countries
        WHERE source_id = ?
        ORDER BY country_order
        """,
        (source_id,),
    )
    return tuple(_row_text(row, "country") for row in rows)


def _read_criteria(
    connection: sqlite3.Connection,
    source_id: str,
) -> dict[SearchCriterion, CriterionCapability]:
    rows = _fetch_rows(
        connection,
        """
        SELECT criterion, capability
        FROM source_criteria
        WHERE source_id = ?
        ORDER BY criterion_order
        """,
        (source_id,),
    )
    criteria: dict[SearchCriterion, CriterionCapability] = {}
    for row in rows:
        criterion = SearchCriterion(_row_text(row, "criterion"))
        if criterion in criteria:
            raise ValueError(f"duplicate criterion for {source_id}: {criterion.value}")
        criteria[criterion] = CriterionCapability(_row_text(row, "capability"))

    missing = set(ALL_SEARCH_CRITERIA) - set(criteria)
    if missing:
        names = ", ".join(sorted(criterion.value for criterion in missing))
        raise ValueError(f"source catalog row {source_id} is missing criteria: {names}")
    return criteria


def _read_required_fixture_kinds(
    connection: sqlite3.Connection,
    source_id: str,
    fixture_records: tuple[SourceFixtureRecord, ...],
) -> RequiredParserFixtures:
    rows = _fetch_rows(
        connection,
        """
        SELECT kind
        FROM source_required_fixture_kinds
        WHERE source_id = ?
        ORDER BY kind
        """,
        (source_id,),
    )
    kinds = frozenset(ParserFixtureKind(_row_text(row, "kind")) for row in rows)
    return RequiredParserFixtures(
        success_non_empty=any(record.kind == ParserFixtureKind.SUCCESS_NON_EMPTY for record in fixture_records),
        no_results=ParserFixtureKind.NO_RESULTS in kinds,
        pagination=ParserFixtureKind.PAGINATION in kinds,
        detail=ParserFixtureKind.DETAIL in kinds,
        optional_fields=ParserFixtureKind.OPTIONAL_FIELDS in kinds,
        blocked=ParserFixtureKind.BLOCKED in kinds,
        rate_limited=ParserFixtureKind.RATE_LIMITED in kinds,
        login=ParserFixtureKind.LOGIN in kinds,
        geo_blocked=ParserFixtureKind.GEO_BLOCKED in kinds,
        malformed_source=ParserFixtureKind.MALFORMED_SOURCE in kinds,
    )


def _read_fixture_records(
    connection: sqlite3.Connection,
    source_id: str,
) -> tuple[SourceFixtureRecord, ...]:
    rows = _fetch_rows(
        connection,
        """
        SELECT
            name,
            kind,
            captured_artifact_path,
            metadata_path,
            golden_path,
            real_capture,
            golden_reviewed_by
        FROM parser_fixtures
        WHERE source_id = ?
        ORDER BY fixture_order
        """,
        (source_id,),
    )
    return tuple(
        SourceFixtureRecord(
            name=_row_text(row, "name"),
            kind=ParserFixtureKind(_row_text(row, "kind")),
            captured_artifact_path=_row_text(row, "captured_artifact_path"),
            metadata_path=_row_text(row, "metadata_path"),
            golden_path=_row_text(row, "golden_path"),
            real_capture=_row_bool(row, "real_capture"),
            golden_reviewed_by=_row_text(row, "golden_reviewed_by"),
        )
        for row in rows
    )


def _fetch_rows(
    connection: sqlite3.Connection,
    query: str,
    parameters: tuple[object, ...] = (),
) -> tuple[sqlite3.Row, ...]:
    cursor = connection.execute(query, parameters)
    return tuple(cast(list[sqlite3.Row], cursor.fetchall()))


def _row_text(row: sqlite3.Row, key: str) -> str:
    value: object = row[key]
    if not isinstance(value, str) or not value.strip():
        raise TypeError(f"SQLite catalog column {key} must be a non-empty string")
    return value


def _row_optional_text(row: sqlite3.Row, key: str) -> str | None:
    value: object = row[key]
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise TypeError(f"SQLite catalog column {key} must be NULL or a non-empty string")
    return value


def _row_int(row: sqlite3.Row, key: str) -> int:
    value: object = row[key]
    if not isinstance(value, int):
        raise TypeError(f"SQLite catalog column {key} must be an integer")
    return value


def _row_bool(row: sqlite3.Row, key: str) -> bool:
    value = _row_int(row, key)
    if value not in {0, 1}:
        raise ValueError(f"SQLite catalog column {key} must be 0 or 1")
    return bool(value)
