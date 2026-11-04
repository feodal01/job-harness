from typing import Literal

from pydantic import BaseModel

from src.parsers.interfaces import SourceCapabilities, SourceType


class SourceAvailability(BaseModel):
    status: Literal["ready", "blocked"]
    until: float | None


class SourceDefaultLimits(BaseModel):
    max_items_per_query: int
    max_pages_per_query: int


class SourceResponse(BaseModel):
    id: str
    name: str
    type: SourceType
    state: SourceAvailability
    geography: Literal["country_city", "regions"] | None
    capabilities: SourceCapabilities
    default_limits: SourceDefaultLimits
