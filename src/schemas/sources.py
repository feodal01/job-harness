from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from src.models.sources import SourceGroup
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
