"""Source composition: pure parsers describe requests; the engine executes them."""

from abc import abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from src.models.brief import BriefInput
from src.models.company import Company
from src.models.vacancy import Vacancy
from src.parsers.models import Location, Place

Fetch = Callable[[str], Awaitable[str]]
ParameterMode = Literal["native", "local", "none", "ignored"]
SourceType = Literal["aggregator", "career_site", "ats", "telegram"]
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


@dataclass(frozen=True)
class SearchPage:
    vacancies: list[Vacancy] = field(default_factory=list)
    total: int | None = None
    next_requests: tuple[SearchRequest, ...] = ()


@dataclass(frozen=True)
class SearchPlan:
    requests: tuple[SearchRequest, ...]
    application: dict[str, ParameterMode]
    notes: tuple[str, ...] = ()


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


# Normalized facts actually emitted by parsers; absent extraction means none.
FILTER_FIELDS = {
    "locations": frozenset({"locations"}),
    "minimum_work_format": frozenset({"work_formats"}),
    "employment": frozenset({"employment"}),
    "experience_years": frozenset({"min_experience"}),
    "salary": frozenset({"salary"}),
    "published_after": frozenset({"published_at"}),
    "relocation_support": frozenset({"description", "summary"}),
    "excluded_companies": frozenset({"company"}),
    "excluded_text": frozenset({"description", "summary", "title"}),
}


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
        )

    @property
    def unsupported_filters(self) -> frozenset[str]:
        return frozenset(k for k, v in self.capabilities.query_parameters.items() if v == "none")
