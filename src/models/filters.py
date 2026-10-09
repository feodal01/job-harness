"""Source-independent search conditions. Site IDs never belong in a brief."""

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from src.geography.countries import COUNTRIES

WorkFormat = Literal["office", "hybrid", "remote_country", "remote_worldwide"]
Employment = Literal["full_time", "part_time", "contract", "internship", "volunteer"]
Grade = Literal["intern", "junior", "middle", "senior", "lead"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class SearchPlace(StrictModel):
    country: str
    city: str | None = Field(default=None, min_length=1, max_length=100)
    region: str | None = Field(default=None, min_length=1, max_length=100)

    @field_validator("country")
    @classmethod
    def known_country(cls, value: str) -> str:
        if value not in COUNTRIES:
            raise ValueError("Choose a country from the catalog")
        return value

    @model_validator(mode="after")
    def region_needs_city(self) -> "SearchPlace":
        if self.region and not self.city:
            raise ValueError("Region requires a city")
        return self


class SalaryFilter(StrictModel):
    minimum: float = Field(gt=0, allow_inf_nan=False)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    period: Literal["month", "year", "hour"]


class SearchFilters(StrictModel):
    locations: list[SearchPlace] = Field(default_factory=list, max_length=20)
    minimum_work_format: WorkFormat | None = None
    employment: list[Employment] = Field(default_factory=list, max_length=5)
    experience_years: float | None = Field(default=None, ge=0, le=80, allow_inf_nan=False)
    salary: SalaryFilter | None = None
    published_after: date | None = None
    relocation_support: bool = False
    excluded_companies: list[str] = Field(default_factory=list, max_length=50)
    excluded_text: list[str] = Field(default_factory=list, max_length=50)

    @field_validator("excluded_companies", "excluded_text")
    @classmethod
    def nonempty_terms(cls, values: list[str]) -> list[str]:
        if any(not v.strip() or len(v) > 200 for v in values):
            raise ValueError("Conditions must contain 1–200 characters")
        return list(dict.fromkeys(v.strip() for v in values))


class WorkLocation(StrictModel):
    country: str | None = None
    city: str | None = None
    format: WorkFormat | None = None
    excluded: bool = False
    worldwide: bool = False

    @field_validator("format", mode="before")
    @classmethod
    def legacy_remote(cls, value: object) -> object:
        return "remote_country" if value == "remote" else value


Status = Literal["accepted", "rejected", "unknown"]


class FilterCheck(StrictModel):
    field: str
    status: Status
    reason: str
    kind: Literal["hard", "soft"] = "soft"
    supported: bool = True


class FilterResult(StrictModel):
    status: Literal["accepted", "maybe", "rejected"]
    checks: list[FilterCheck]
