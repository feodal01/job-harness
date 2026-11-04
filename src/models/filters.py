"""Source-independent search conditions. Site IDs never belong in a brief."""

from datetime import date
from typing import Literal, get_args

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from src.geography.catalog import default_catalog
from src.models.facts import Evidence

# Ordered from the strictest to the most flexible: a minimum accepts itself and every later format.
WorkFormat = Literal["office", "hybrid", "remote_country", "remote_worldwide"]
WORK_FORMATS: tuple[WorkFormat, ...] = get_args(WorkFormat)
REMOTE_FORMATS: frozenset[WorkFormat] = frozenset(f for f in WORK_FORMATS if f.startswith("remote_"))
OFFICE_FORMATS: frozenset[WorkFormat] = frozenset(WORK_FORMATS) - REMOTE_FORMATS
Employment = Literal["full_time", "part_time", "contract", "internship", "volunteer"]
Grade = Literal["intern", "junior", "middle", "senior", "lead"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class SearchPlace(StrictModel):
    country: str
    scope: Literal["city", "country"] = Field(
        default="city",
        description="city requires a selected GeoNames ID; country explicitly selects the whole country",
    )
    city: str | None = Field(default=None, min_length=1, max_length=200)
    region: str | None = Field(default=None, min_length=1, max_length=100)
    geoname_id: int | None = Field(
        default=None, gt=0, description="Selected GeoNames ID; required for scope=city"
    )
    country_name_en: str | None = None
    city_display_name: str | None = Field(
        default=None,
        min_length=1,
        max_length=400,
        description="Full name from the city suggestion; required for scope=city",
    )
    minimum_work_format: WorkFormat | None = Field(
        default=None, description="Minimum work format for this location; more flexible formats also qualify"
    )
    relocation_support: bool | None = Field(
        default=None, description="Require employer relocation assistance for this location"
    )

    @field_validator("country")
    @classmethod
    def known_country(cls, value: str) -> str:
        if value not in default_catalog().codes:
            raise ValueError("Choose a country from the catalog")
        return value

    @model_validator(mode="after")
    def region_needs_city(self) -> "SearchPlace":
        if self.scope == "country" and any(
            value is not None for value in (self.city, self.region, self.geoname_id, self.city_display_name)
        ):
            raise ValueError("Whole-country selection must not contain city or region data")
        if self.region and not self.city:
            raise ValueError("Region requires a city")
        return self


class SalaryFilter(StrictModel):
    minimum: float = Field(gt=0, allow_inf_nan=False)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    period: Literal["month", "year", "hour"]


class SearchFilters(StrictModel):
    locations: list[SearchPlace] = Field(default_factory=list, max_length=20)
    minimum_work_format: WorkFormat | None = Field(
        default=None,
        description="Historical general condition; use locations[].minimum_work_format for new briefs",
        json_schema_extra={"deprecated": True},
    )
    employment: list[Employment] = Field(default_factory=list, max_length=5)
    experience_years: float | None = Field(default=None, ge=0, le=80, allow_inf_nan=False)
    salary: SalaryFilter | None = None
    published_after: date | None = None
    relocation_support: bool = Field(
        default=False,
        description="Historical general condition; use locations[].relocation_support for new briefs",
        json_schema_extra={"deprecated": True},
    )
    excluded_companies: list[str] = Field(default_factory=list, max_length=50)
    excluded_text: list[str] = Field(default_factory=list, max_length=50)

    def location_conditions(self, place: SearchPlace | None) -> tuple[WorkFormat | None, bool]:
        """Read historical general conditions until a brief is resaved with per-city conditions."""
        minimum = place.minimum_work_format if place else None
        relocation = place.relocation_support if place else None
        return (
            minimum if minimum is not None else self.minimum_work_format,
            relocation if relocation is not None else self.relocation_support,
        )

    @field_validator("excluded_companies", "excluded_text")
    @classmethod
    def nonempty_terms(cls, values: list[str]) -> list[str]:
        if any(not v.strip() or len(v) > 200 for v in values):
            raise ValueError("Conditions must contain 1–200 characters")
        return list(dict.fromkeys(v.strip() for v in values))


# Names of brief conditions: exactly the SearchFilters fields. Work format and relocation are
# now set per place, but keep these names (tests/test_filter_names.py guards the match).
FilterName = Literal[
    "locations",
    "minimum_work_format",
    "employment",
    "experience_years",
    "salary",
    "published_after",
    "relocation_support",
    "excluded_companies",
    "excluded_text",
]
FILTER_NAMES: tuple[FilterName, ...] = get_args(FilterName)


def accepted_formats(minimum: WorkFormat | None) -> tuple[WorkFormat, ...]:
    """The minimum and every more flexible format; no minimum accepts all."""
    return WORK_FORMATS[WORK_FORMATS.index(minimum) :] if minimum else WORK_FORMATS


# What is known about a vacancy place: a city, an administrative region, a whole country,
# the whole world, or a named group of countries (e.g. "Europe").
PlaceScope = Literal["city", "region", "country", "world", "group"]


class VacancyPlace(StrictModel):
    """A vacancy place in the brief's vocabulary: the mirror of SearchPlace (docs/vacancy-facts-plan.md §3).

    `city` and `worldwide` are transitional string facts; they are removed once every source
    fills `scope`, `geoname_id` and `region`.
    """

    country: str | None = None
    city: str | None = None
    format: WorkFormat | None = None
    excluded: bool = False
    worldwide: bool = False
    scope: PlaceScope | None = None
    geoname_id: int | None = None
    region: str | None = None
    # CLDR region or grouping code for scope=group ("150" Europe, "EU"); None if the group is unknown.
    group: str | None = None
    relocation_support: bool | None = None
    label: str | None = None
    evidence: Evidence | None = None

    @field_validator("format", mode="before")
    @classmethod
    def legacy_remote(cls, value: object) -> object:
        return "remote_country" if value == "remote" else value


# Transitional name; removed together with the string facts above.
WorkLocation = VacancyPlace


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
