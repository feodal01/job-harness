"""Contract-first ZeroAvia career source backed by Workable public jobs markdown."""

from __future__ import annotations

import re

from job_harness.v2.contracts import (
    RawListing,
    RequiredParserFixtures,
    SearchRequest,
    SourceDescriptor,
    SourceFetchRequest,
    SourceOutcome,
    SourceResponseArtifact,
    SourceScraper,
    SourceSearchParseResult,
)
from job_harness.v2.source_catalog import source_descriptor, source_required_fixture_kinds

_BOARD_URL = "https://apply.workable.com/zeroavia/jobs.md"
_SOURCE_ID = "career:zeroavia"
_COMPANY = "ZeroAvia"
_DETAIL_LINK_RE = re.compile(r"\[View\]\((?P<url>https://apply\.workable\.com/zeroavia/jobs/view/(?P<id>[^/)]+)\.md)\)")
_SALARY_AMOUNT_RE = re.compile(r"([$£€]\s?\d|\b(?:USD|GBP|EUR)\b|\b\d[\d,]*(?:k|K)?\s?(?:USD|GBP|EUR)\b)")


class ZeroAviaCareerSource(SourceScraper):
    @property
    def descriptor(self) -> SourceDescriptor:
        return source_descriptor(_SOURCE_ID)

    @property
    def required_fixture_kinds(self) -> RequiredParserFixtures:
        return source_required_fixture_kinds(_SOURCE_ID)

    def build_search_requests(self, request: SearchRequest) -> tuple[SourceFetchRequest, ...]:
        return tuple(
            SourceFetchRequest(
                source_id=self.descriptor.source_id,
                query_variant=query_variant,
                url=_BOARD_URL,
            )
            for query_variant in request.query_variants
        )

    def parse_search_response(
        self,
        response: SourceResponseArtifact,
        _request: SourceFetchRequest,
    ) -> SourceSearchParseResult:
        rows = tuple(_table_rows(response.body))
        if not rows:
            raise ValueError("ZeroAvia Workable jobs markdown contains no vacancy rows")
        listings = tuple(_listing(row) for row in rows)
        return SourceSearchParseResult(outcome=SourceOutcome.SUCCESS, listings=listings)


def _table_rows(body: str) -> tuple[dict[str, str], ...]:
    rows: list[dict[str, str]] = []
    headers: tuple[str, ...] = ()
    for raw_line in body.splitlines():
        line = raw_line.strip()
        if not line.startswith("|") or not line.endswith("|"):
            continue
        cells = tuple(cell.strip() for cell in line.strip("|").split("|"))
        if not headers:
            headers = cells
            continue
        if all(set(cell) <= {"-"} for cell in cells):
            continue
        if len(cells) != len(headers):
            continue
        rows.append(dict(zip(headers, cells, strict=True)))
    return tuple(rows)


def _listing(row: dict[str, str]) -> RawListing:
    title = _required_cell(row, "Title")
    detail_url, source_listing_id = _detail_url_and_id(_required_cell(row, "Details"))
    location = _location(_required_cell(row, "Location"))
    workplace = _workplace(row["Location"])
    work_format = _work_format(workplace)
    salary_text = _salary_text(row.get("Salary", ""))
    raw: dict[str, object] = {
        "detail_markdown_url": detail_url,
        "department": _normalized_cell(row.get("Department", "")),
        "employment_type": _normalized_cell(row.get("Type", "")),
        "salary": _normalized_cell(row.get("Salary", "")),
        "workplace": workplace,
        "work_format": (work_format,),
        "location": row["Location"],
    }

    return RawListing(
        source_listing_id=source_listing_id,
        title=title,
        url=f"https://apply.workable.com/zeroavia/j/{source_listing_id}",
        source=_SOURCE_ID,
        company=_COMPANY,
        country=location.country,
        city=location.city,
        location_text=location.location_text,
        salary_text=salary_text,
        salary_min=None,
        salary_max=None,
        salary_currency=None,
        posted_at=_normalized_cell(row.get("Posted", "")),
        remote_in_country=False,
        remote_global=False,
        relocation=None,
        native_grade=None,
        description=None,
        requirements=None,
        additional_sections={},
        skills=(),
        raw_text=_join_text(title, raw["department"], location.location_text, raw["employment_type"], workplace),
        raw=raw,
    )


class _Location:
    def __init__(self, *, city: str | None, country: str | None, location_text: str) -> None:
        self.city = city
        self.country = country
        self.location_text = location_text


def _location(value: str) -> _Location:
    cleaned = value.replace("(Hybrid)", "").strip()
    if "," not in cleaned:
        return _Location(city=cleaned or None, country=None, location_text=value)
    city, country = (part.strip() for part in cleaned.rsplit(",", 1))
    return _Location(city=city or None, country=country or None, location_text=value)


def _workplace(location: str) -> str:
    if "(Hybrid)" in location:
        return "hybrid"
    return "on_site"


def _work_format(workplace: str) -> str:
    if workplace == "hybrid":
        return "hybrid"
    return "office"


def _salary_text(value: str) -> str | None:
    normalized = _normalized_cell(value)
    if normalized is None or not _SALARY_AMOUNT_RE.search(normalized):
        return None
    return normalized


def _detail_url_and_id(value: str) -> tuple[str, str]:
    match = _DETAIL_LINK_RE.fullmatch(value)
    if match is None:
        raise ValueError(f"ZeroAvia Workable row has malformed detail link: {value}")
    return match.group("url"), match.group("id")


def _required_cell(row: dict[str, str], key: str) -> str:
    value = _normalized_cell(row.get(key, ""))
    if value is None:
        raise ValueError(f"ZeroAvia Workable row is missing {key}")
    return value


def _normalized_cell(value: str) -> str | None:
    stripped = value.strip()
    if not stripped or stripped == "—":
        return None
    return stripped


def _join_text(*parts: object) -> str | None:
    text = "\n".join(str(part).strip() for part in parts if part and str(part).strip())
    return text or None
