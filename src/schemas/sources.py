from typing import Literal

from pydantic import BaseModel

from src.parsers.interfaces import SourceCapabilities, SourceType


class SourceAvailability(BaseModel):
    status: Literal["ready", "blocked"]
    until: float | None


class SourceResponse(BaseModel):
    id: str
    name: str
    type: SourceType
    state: SourceAvailability
    geography: Literal["country_city", "regions"] | None
    capabilities: SourceCapabilities
