"""Shared mechanics for ATS adapters; source formats belong to platform packages."""

import json
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit

from pydantic import Field

from src.models.brief import BriefInput
from src.parsers.ats.models import Board
from src.parsers.blocking import classify_common
from src.parsers.interfaces import (
    Classify,
    Fetch,
    ParameterMode,
    ParseError,
    RequestSettings,
    Search,
    SearchPlan,
    SearchRequest,
    SourceAdapter,
    VacancyDetails,
    filter_parameters,
)
from src.parsers.models import Location


class AtsSettings(RequestSettings):
    max_items_per_query: int = Field(default=5000, ge=1, le=5000)
    max_pages_per_query: int = Field(default=50, ge=1, le=50)


class BoardSearch(Search):
    """A board feed or a native search, with explicit provider filter values."""

    query_notes = "Обход доски компании целиком; условия брифа проверяются общими фильтрами."
    detail_fields: frozenset[str] = frozenset()
    query_parameter: str | None = None
    filter_names: frozenset[str] = frozenset()
    # None means the provider validates multiplicity against its discovered form.
    multi_filter_names: frozenset[str] | None = frozenset()

    def __init__(self, board: Board, listing_url: str) -> None:
        if unsupported := board.native_filters.keys() - self.filter_names:
            raise ValueError(f"Unsupported native filters: {', '.join(sorted(unsupported))}")
        if self.multi_filter_names is not None:
            for name, value in board.native_filters.items():
                if isinstance(value, list) and len(value) > 1 and name not in self.multi_filter_names:
                    raise ValueError(f"Native filter {name} accepts only one value")
        self.board = board
        self.listing_url = listing_url
        if self.query_parameter:
            self.query_notes = (
                f"Каждая фраза передаётся в {self.query_parameter}; "
                "остальные условия проверяются общими фильтрами."
            )

    @property
    def parameters(self) -> dict[str, ParameterMode]:
        return filter_parameters(
            self.fields | self.detail_fields, {"query": "native" if self.query_parameter else "ignored"}
        )

    async def prepare(self, brief: BriefInput, fetch: Fetch, *, second_chance: bool = False) -> SearchPlan:
        queries = (brief.queries or [""]) if self.query_parameter else [""]
        return SearchPlan(tuple(self.build(query) for query in queries), self.parameters)

    def build(self, query: str, location: Location | None = None) -> SearchRequest:
        if location is not None:
            raise ValueError("This ATS feed has no native geography filter")
        values = dict(self.board.native_filters)
        if self.query_parameter and query:
            values[self.query_parameter] = query
        return SearchRequest(with_query(self.listing_url, values), query)


def with_query(url: str, values: dict[str, str | list[str]]) -> str:
    """Replace selected query values, preserving repeated provider parameters."""
    if not values:
        return url
    address = urlsplit(url)
    params = parse_qs(address.query, keep_blank_values=True)
    params.update({key: value if isinstance(value, list) else [value] for key, value in values.items()})
    return address._replace(query=urlencode(params, doseq=True)).geturl()


def filtered_next_url(url: str, request: SearchRequest, names: frozenset[str]) -> str:
    """Provider pagination links may omit or reset the active search controls."""
    active = parse_qs(urlsplit(request.url).query, keep_blank_values=True)
    return with_query(url, {name: values for name, values in active.items() if name in names})


def adapter(
    platform: str,
    board: Board,
    search: Search,
    *,
    key: str,
    resource: str,
    allowed_hosts: tuple[str, ...],
    settings: RequestSettings | None = None,
    details: VacancyDetails | None = None,
    classify: Classify | None = classify_common,
) -> SourceAdapter:
    return SourceAdapter(
        id=f"ats:{platform}:{key}",
        name=board.company,
        type="ats",
        # Each company board has its own pacing and breaker, even when boards share one API host;
        # the network caps concurrent requests per host separately.
        resource=f"{resource}/{key}",
        allowed_hosts=allowed_hosts,
        settings=settings if settings is not None else AtsSettings(),
        search=search,
        vacancy_details=details,
        classify=classify,
    )


def text(value: object) -> str | None:
    return value.strip() or None if isinstance(value, str) else None


def required_text(value: object, field: str) -> str:
    result = text(value)
    if result is None:
        raise ParseError(f"ATS response is missing {field}")
    return result


def object_value(value: object, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ParseError(f"ATS {field} must be an object")
    return value


def array_value(value: object, field: str) -> list[Any]:
    if not isinstance(value, list):
        raise ParseError(f"ATS {field} must be an array")
    return value


def json_object(body: str) -> dict[str, Any]:
    try:
        value = json.loads(body)
    except (ValueError, TypeError) as exc:
        raise ParseError("ATS response is not valid JSON") from exc
    return object_value(value, "response")


def json_array(body: str) -> list[Any]:
    try:
        value = json.loads(body)
    except (ValueError, TypeError) as exc:
        raise ParseError("ATS response is not valid JSON") from exc
    return array_value(value, "response")


def host(url: str) -> str:
    return urlsplit(url).hostname or ""
