"""Source composition: pure parsers describe requests; the engine executes them."""

import json
from abc import abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import date
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from src.models.brief import BriefInput
from src.models.company import Company
from src.models.filters import (
    Employment,
    FilterName,
    SalaryFilter,
    SearchFilters,
    SearchPlace,
    WorkFormat,
    accepted_formats,
)
from src.models.sources import SourceGroup
from src.models.vacancy import Vacancy
from src.parsers.models import Location, Place
from src.selection.filtering import FILTER_FIELDS

Fetch = Callable[[str], Awaitable[str]]
ParameterMode = Literal["native", "local", "none", "ignored"]
SourceType = Literal["aggregator", "career_site", "ats", "telegram"]
# How sources are grouped for people (mockup 8о): job boards, IT boards, company career sites, Telegram.
# Outcomes of a response that HTTP status alone does not reveal; neither is retried.
ResponseClass = Literal["captcha", "vpn_blocked", "blocked"]


@dataclass(frozen=True)
class ResponseInfo:
    url: str
    status_code: int
    headers: dict[str, str]
    text: str


Classify = Callable[[ResponseInfo], ResponseClass | None]


class ParseError(ValueError):
    def __init__(self, message: str, kind: str = "parse_error") -> None:
        super().__init__(message)
        self.kind = kind


@dataclass(frozen=True)
class SearchRequest:
    url: str
    query: str
    stage: Literal["listing", "preparation"] = "listing"
    location: Location | None = None
    method: Literal["GET", "POST"] = "GET"
    json_body: dict[str, JsonValue] | None = None
    form_body: dict[str, str | list[str]] | None = None

    def __post_init__(self) -> None:
        if self.method not in {"GET", "POST"}:
            raise ValueError("Search requests support GET or read-only POST")
        if self.method == "GET" and (self.json_body is not None or self.form_body is not None):
            raise ValueError("A GET search request cannot have a body")
        if self.method == "POST" and (self.json_body is None) == (self.form_body is None):
            raise ValueError("A POST search request requires exactly one JSON or form body")

    @property
    def identity(self) -> tuple[str, str, str]:
        body = {"form": self.form_body} if self.form_body is not None else {"json": self.json_body}
        return self.method, self.url, json.dumps(body, sort_keys=True, allow_nan=False)


@dataclass(frozen=True)
class SearchPage:
    vacancies: list[Vacancy] = field(default_factory=list)
    total: int | None = None
    next_requests: tuple[SearchRequest, ...] = ()


PreparationStatus = Literal["exact", "broader", "unsupported", "ambiguous", "not_found", "error"]
STATUS_ORDER: tuple[PreparationStatus, ...] = (
    "exact",
    "broader",
    "unsupported",
    "ambiguous",
    "not_found",
    "error",
)
NativeParams = dict[str, tuple[str, ...]]
# A brief condition, or the search phrase, which is not a filter.
Condition = FilterName | Literal["query"]


@dataclass(frozen=True)
class Prepared:
    """One brief condition translated on its own, without knowing the other conditions.

    `condition` is the common filter name (locations, minimum_work_format, published_after...).
    `params` are native request parameters; `accepted` carries the meaning a later step may
    need (e.g. accepted work formats). Status `error` means the branch is not sent.
    """

    condition: Condition
    mode: ParameterMode
    status: PreparationStatus = "exact"
    params: NativeParams = field(default_factory=dict)
    accepted: tuple[str, ...] = ()
    reason: str | None = None
    origin: str | None = None

    def to_json(self) -> dict[str, JsonValue]:
        return {
            "condition": self.condition,
            "mode": self.mode,
            "status": self.status,
            "params": {k: list(v) for k, v in self.params.items()},
            "accepted": list(self.accepted),
            "reason": self.reason,
            "origin": self.origin,
        }


@dataclass(frozen=True)
class PlacePreparation:
    """Translation of one brief place: its own conditions plus the accuracy notes of combining."""

    place: SearchPlace
    minimum_work_format: WorkFormat | None
    conditions: tuple[Prepared, ...]

    @property
    def status(self) -> PreparationStatus:
        return max((c.status for c in self.conditions), key=STATUS_ORDER.index, default="unsupported")

    def to_json(self) -> dict[str, JsonValue]:
        return {
            "place": self.place.model_dump(mode="json", exclude_none=True),
            "minimum_work_format": self.minimum_work_format,
            "status": self.status,
            "conditions": [c.to_json() for c in self.conditions],
        }


@dataclass(frozen=True)
class SearchPlan:
    requests: tuple[SearchRequest, ...]
    application: dict[str, ParameterMode]
    notes: tuple[str, ...] = ()
    places: tuple[PlacePreparation, ...] = ()


class Search(Protocol):
    fields: frozenset[str]
    query_notes: str

    @property
    def parameters(self) -> dict[str, ParameterMode]: ...

    @abstractmethod
    async def prepare(
        self, brief: BriefInput, fetch: Fetch, *, second_chance: bool = False
    ) -> SearchPlan: ...

    @abstractmethod
    def build(self, query: str, location: Location | None = None) -> SearchRequest: ...
    @abstractmethod
    def parse(self, body: str, request: SearchRequest) -> SearchPage: ...


@dataclass(frozen=True)
class Draft:
    """Everything prepared for one branch (place × phrase), before the source combines it."""

    place: SearchPlace | None
    query: str
    conditions: dict[Condition, Prepared]

    def get(self, condition: Condition) -> Prepared | None:
        return self.conditions.get(condition)


@dataclass(frozen=True)
class Combined:
    """Native parameters of each request of a branch, and accuracy added by combining."""

    requests: tuple[NativeParams, ...]
    notes: tuple[Prepared, ...] = ()


class StagedSearch(Search):
    """Search whose prepare() runs common steps; the source implements each step separately.

    1. Brief conditions, once: salary, employment, experience, publication date.
    2. Per place: geography, work format, relocation — each step independent of the others.
    3. Branches = places × phrases (one empty phrase when the source ignores phrases).
    4. `_combine` turns one branch's prepared conditions into request parameters; it is the
       only step that may couple conditions (default: merge every step's parameters).
    5. `_request` builds the first page of each request; equal requests are merged.

    Second chance is handled here: soft conditions (work format, salary, employment,
    experience, date) are not prepared, so sources never see the flag. Geography and
    phrases are hard and always prepared. A branch with an `error` condition is not sent;
    the engine reports it. Sources with their own prepare() keep implementing Search.
    """

    notes: tuple[str, ...] = ()

    async def prepare(self, brief: BriefInput, fetch: Fetch, *, second_chance: bool = False) -> SearchPlan:
        filters = brief.filters
        shared: list[Prepared | None] = []
        if not second_chance:
            if filters.salary:
                shared.append(self._prepare_salary(filters.salary))
            if filters.employment:
                shared.append(self._prepare_employment(filters.employment))
            if filters.experience_years is not None:
                shared.append(self._prepare_experience(filters.experience_years))
            if filters.published_after:
                shared.append(self._prepare_published(filters.published_after))
        phrases = [(q, self._prepare_query(q)) for q in brief.queries or [""]]
        if all(p is not None and p.mode == "ignored" for _, p in phrases):
            phrases = [("", phrases[0][1])]  # The source ignores phrases: one branch per place.
        prepared: list[Prepared] = [p for p in shared if p is not None]
        prepared.extend(p for _, p in phrases if p is not None)
        places: list[PlacePreparation] = []
        requests: dict[tuple[str, str, str], SearchRequest] = {}
        selected: list[SearchPlace | None] = [*filters.locations] or [None]
        for place in selected:
            # Without a minimum (or under second chance) every work format is accepted.
            minimum = None if second_chance else filters.location_conditions(place)[0]
            own = await self._prepare_place(fetch, filters, place, minimum, second_chance)
            notes: tuple[Prepared, ...] = ()
            if all(c.status != "error" for c in own):
                for query, phrase in phrases:
                    conditions = [*own, *(p for p in shared if p), *([phrase] if phrase else [])]
                    combined = self._combine(Draft(place, query, {c.condition: c for c in conditions}))
                    notes = combined.notes
                    for params in combined.requests:
                        request = self._request(query, params)
                        requests[request.identity] = request  # Equal requests of branches merge here.
            prepared.extend((*own, *notes))
            if place is not None:
                places.append(PlacePreparation(place, minimum, (*own, *notes)))
        return SearchPlan(tuple(requests.values()), self._modes(prepared), self.notes, tuple(places))

    async def _prepare_place(
        self,
        fetch: Fetch,
        filters: SearchFilters,
        place: SearchPlace | None,
        minimum: WorkFormat | None,
        second_chance: bool,
    ) -> list[Prepared]:
        own: list[Prepared] = []
        if place is not None:
            own.append(await self._prepare_geography(fetch, place))
        own.append(self._prepare_work_format(minimum))
        relocation = filters.location_conditions(place)[1]
        if relocation and not second_chance:
            if (step := self._prepare_relocation()) is not None:
                own.append(step)
        return own

    def _modes(self, prepared: list[Prepared]) -> dict[str, ParameterMode]:
        """A condition is native only if every branch expressed it natively."""
        application = self._application()
        for name in {p.condition for p in prepared}:
            modes = {p.mode for p in prepared if p.condition == name}
            application[name] = (
                "native"
                if modes == {"native"}
                else next((m for m in ("ignored", "local", "none") if m in modes), "local")
            )
        return application

    @abstractmethod
    def _application(self) -> dict[str, ParameterMode]:
        """Default mode of every common condition when no step expresses it natively."""

    @abstractmethod
    async def _prepare_geography(self, fetch: Fetch, place: SearchPlace) -> Prepared:
        """Native expression of the place alone; condition `locations`."""

    def _prepare_work_format(self, minimum: WorkFormat | None) -> Prepared:
        """Accepted work formats alone; condition `minimum_work_format`."""
        return Prepared("minimum_work_format", "local", accepted=accepted_formats(minimum))

    def _prepare_relocation(self) -> Prepared | None:
        return None

    def _prepare_query(self, query: str) -> Prepared | None:
        return None

    def _prepare_salary(self, salary: SalaryFilter) -> Prepared | None:
        return None

    def _prepare_employment(self, employment: list[Employment]) -> Prepared | None:
        return None

    def _prepare_experience(self, years: float) -> Prepared | None:
        return None

    def _prepare_published(self, published_after: date) -> Prepared | None:
        return None

    def _combine(self, draft: Draft) -> Combined:
        """Default: one request with the parameters of every condition; a clash is a source bug."""
        params: NativeParams = {}
        for prepared in draft.conditions.values():
            for key, values in prepared.params.items():
                if key in params and params[key] != values:
                    raise ValueError(f"Conditions disagree on native parameter {key}")
                params[key] = values
        return Combined((params,))

    @abstractmethod
    def _request(self, query: str, params: NativeParams) -> SearchRequest:
        """First page of one request."""


class VacancyDetails(Protocol):
    fields: frozenset[str]

    @abstractmethod
    def url(self, vacancy: Vacancy) -> str: ...
    @abstractmethod
    def parse(self, body: str, vacancy: Vacancy) -> Vacancy: ...


class CompanyProfile(Protocol):
    fields: frozenset[str]

    @abstractmethod
    def url(self, company: Company) -> str | None: ...

    @abstractmethod
    def parse(self, body: str, company: Company) -> Company: ...


class CountryCityGeography(Protocol):
    mode: Literal["country_city"]

    @abstractmethod
    async def countries(self, fetch: Fetch) -> list[Place]: ...
    @abstractmethod
    async def cities(self, fetch: Fetch, country_id: str, text: str) -> list[Place]: ...


class RegionGeography(Protocol):
    mode: Literal["regions"]
    options: tuple[Place, ...]


class RequestSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # Search limits per phrase: new unique vacancies found by it, and pages of each native request.
    max_items_per_query: int = Field(default=50, ge=1, le=5000)
    max_pages_per_query: int = Field(default=2, ge=1, le=50)
    concurrency: int = Field(default=1, ge=1, le=10)
    interval_seconds: float = Field(default=1, ge=0)
    request_timeout_seconds: float = Field(default=20, gt=0)
    max_attempts: int = Field(default=2, ge=1, le=5)
    retry_base_seconds: float = Field(default=1, ge=0)
    breaker_threshold: int = Field(default=3, ge=1)
    breaker_seconds: float = Field(default=30, gt=0)


class SourceCapabilities(BaseModel):
    actions: list[str]
    fields: dict[str, list[str]]
    query_parameters: dict[str, ParameterMode]
    query_notes: str
    native_filters: list[str] = Field(default_factory=list)


def filter_parameters(
    fields: frozenset[str], native: dict[str, ParameterMode] | None = None
) -> dict[str, ParameterMode]:
    return {
        **{name: "local" if fields & required else "none" for name, required in FILTER_FIELDS.items()},
        **(native or {}),
    }


@dataclass(frozen=True)
class SourceAdapter:
    id: str
    name: str
    type: SourceType
    resource: str
    allowed_hosts: tuple[str, ...]
    settings: RequestSettings
    search: Search
    vacancy_details: VacancyDetails | None = None
    company_profile: CompanyProfile | None = None
    geography: CountryCityGeography | RegionGeography | None = None
    classify: Classify | None = None
    # Set by the source registry: the group people see and the company catalog rows of a board.
    group: SourceGroup | None = None
    company_ids: tuple[str, ...] = ()
    # A search without queries is refused (HH lists every vacancy then); a board lists its whole feed.
    requires_query: bool = False

    @property
    def capabilities(self) -> SourceCapabilities:
        actions: list[str] = []
        fields: dict[str, list[str]] = {}
        for stage, component in (
            ("search", self.search),
            ("vacancy_details", self.vacancy_details),
            ("company_profile", self.company_profile),
        ):
            if component is not None:
                actions.append(stage)
                for name in sorted(component.fields):
                    fields.setdefault(name, []).append(stage)
        return SourceCapabilities(
            actions=actions,
            fields=fields,
            query_parameters=filter_parameters(
                frozenset(fields),
                {k: v for k, v in self.search.parameters.items() if v in {"native", "ignored"}},
            ),
            query_notes=self.search.query_notes,
            native_filters=sorted(getattr(self.search, "filter_names", ())),
        )

    @property
    def unsupported_filters(self) -> frozenset[str]:
        return frozenset(k for k, v in self.capabilities.query_parameters.items() if v == "none")
