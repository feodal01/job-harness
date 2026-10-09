from pydantic import BaseModel, ConfigDict, Field, field_validator

from src.models.filters import SearchFilters


class SourceLimitsInput(BaseModel):
    """Per-phrase limits for one source; omitted values come from the source settings."""

    model_config = ConfigDict(extra="forbid")
    max_items_per_query: int | None = Field(default=None, ge=1, le=5000)
    max_pages_per_query: int | None = Field(default=None, ge=1, le=50)


class SearchOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sources: list[str] = Field(default_factory=list, max_length=20)
    limits: dict[str, SourceLimitsInput] = Field(default_factory=dict)
    company_profiles: bool | None = None
    second_chance: bool | None = None


class BriefInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=200)
    text: str = Field(default="", max_length=10000)
    queries: list[str] = Field(default_factory=list, max_length=10)
    filters: SearchFilters = Field(default_factory=SearchFilters)
    search: SearchOptions | None = None

    @field_validator("queries")
    @classmethod
    def clean_queries(cls, values: list[str]) -> list[str]:
        if any(not v.strip() or len(v) > 500 for v in values):
            raise ValueError("Queries must contain 1–500 non-whitespace characters")
        return list(dict.fromkeys(v.strip() for v in values))
