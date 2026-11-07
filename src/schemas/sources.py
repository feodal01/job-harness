from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from src.models.sources import SourceGroup, SourceRules
from src.parsers.interfaces import SourceCapabilities, SourceType

SourceStatusFilter = Literal["supported", "unsupported", "all"]
CatalogStatus = Literal["ok", "not_requested", "login_required", "forbidden", "unavailable"]


class SourceAvailability(BaseModel):
    status: Literal["ready", "blocked"]
    until: float | None


class SourceDefaultLimits(BaseModel):
    max_items_per_query: int
    max_pages_per_query: int


class SourceCoverage(BaseModel):
    """ISO countries the source can return vacancies for; null countries mean every country."""

    countries: list[str] | None
    known: bool


class SourceResponse(BaseModel):
    id: str
    name: str
    kind: str
    kind_name: str
    group: SourceGroup
    supported: bool
    reason: str | None = None
    type: SourceType | None = None
    company_ids: list[str] = Field(default_factory=list)
    coverage: SourceCoverage
    capabilities: SourceCapabilities | None = None
    state: SourceAvailability | None = None
    geography: Literal["country_city", "regions"] | None = None
    default_limits: SourceDefaultLimits | None = None


class CatalogState(BaseModel):
    """Whether company sites could be read from the catalog for this answer."""

    status: CatalogStatus
    message: str | None = None
    login_url: str | None = None


class SourceFilter(BaseModel):
    groups: list[SourceGroup] | None = None
    kinds: list[str] | None = None
    countries: list[str] | None = Field(default=None, description="ISO-коды стран мест брифа.")
    supports: list[str] | None = Field(default=None, description="Условия, которые источник проверяет.")
    company_ids: list[UUID] | None = None
    status: SourceStatusFilter = "all"
    name: str | None = Field(default=None, min_length=1, max_length=200)


class SourceQuery(SourceFilter):
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=50, ge=1, le=200)


class SourcesPage(BaseModel):
    items: list[SourceResponse]
    total: int
    page: int
    page_size: int
    catalog: CatalogState


class SourceGroupCount(BaseModel):
    group: SourceGroup
    supported: int
    unsupported: int


class SourceCounts(BaseModel):
    groups: list[SourceGroupCount]
    supported: int
    catalog: CatalogState


Countries = Annotated[
    list[Annotated[str, Field(pattern="^[A-Za-z]{2}$")]],
    Field(default_factory=list, max_length=20, description="ISO-коды стран мест брифа; пусто — все страны."),
]


class SourceRulesRequest(BaseModel):
    """Rules as the brief form holds them now, possibly unsaved, and the countries of its places."""

    model_config = ConfigDict(extra="forbid")
    source_rules: SourceRules = Field(default_factory=SourceRules)
    countries: Countries


class SourceGroupPreview(BaseModel):
    group: SourceGroup
    available: int = Field(description="Поддерживаемые источники типа, покрывающие страны.")
    selected: int = Field(description="Сколько из них войдёт в поиск по правилам.")


RuleItemStatus = Literal["included", "excluded", "missing"]


class RuleItemPreview(BaseModel):
    item: str = Field(description="Элемент include или exclude как в правилах.")
    name: str | None = None
    group: SourceGroup | None = None
    status: RuleItemStatus
    reason: str | None = None


class PreviewCatalogState(CatalogState):
    rows: int | None = Field(default=None, description="Строк в каталоге компаний, если он прочитан.")


class SourcePreview(BaseModel):
    countries: list[str]
    groups: list[SourceGroupPreview]
    total: int = Field(description="Источников в поиске по этим правилам.")
    excluded: int = Field(description="Источников, которые убрал exclude.")
    builtin: list[str] = Field(description="Встроенные источники (не сайты компаний), которые войдут в поиск.")
    requires_query: bool = Field(description="Все источники поиска требуют запрос: без запросов искать не по чему.")
    items: list[RuleItemPreview]
    catalog: PreviewCatalogState


class SourceSearchRequest(SourceRulesRequest):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    groups: list[SourceGroup] | None = None
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=200)


Participation = Literal["included", "excluded", "not_selected", "not_covered", "unsupported"]


class SourceMatch(SourceResponse):
    participation: Participation
    participation_reason: str
    rule_item: str | None = Field(
        description="Что положить в include или exclude: company:<uuid> для сайта компании, иначе id."
    )


class SourceMatchGroup(BaseModel):
    group: SourceGroup
    total: int


class SourceSearchPage(BaseModel):
    items: list[SourceMatch]
    groups: list[SourceMatchGroup]
    total: int
    page: int
    page_size: int
    catalog: CatalogState
