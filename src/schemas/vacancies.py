"""HTTP-представления вакансий, включая неизменённые исторические результаты."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from src.models.company import Company
from src.models.filters import Employment, Grade, Status
from src.models.salary import STANDARD_PERIODS

SelectionView = Literal["all", "candidates", "accepted", "maybe", "rejected"]
PoolView = Literal["all", "open", "new", "viewed", "dismissed", "applied", "interview", "rejected", "offer"]
PoolStatus = Literal["new", "viewed", "dismissed", "applied", "interview", "rejected", "offer"]


class PoolStatusInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    status: PoolStatus
    note: str = Field(default="", max_length=2000)


class SelectionCheck(BaseModel):
    field: str
    status: Status
    reason: str
    kind: Literal["hard", "soft"] = "soft"
    supported: bool = True
    conflicts: list[str] = Field(default_factory=list)


class VacancySelection(BaseModel):
    status: Literal["accepted", "maybe", "rejected", "unknown"]
    checks: list[SelectionCheck]


class VacancyLocation(BaseModel):
    country: str | None = None
    city: str | None = None
    format: Literal["office", "hybrid", "remote_country", "remote_worldwide", "remote"] | None = None
    excluded: bool = False
    worldwide: bool = False


class VacancyOrigin(BaseModel):
    run_id: str
    query: str
    request_url: str
    page: int


class VacancyResponse(BaseModel):
    id: str
    title: str
    url: str
    company: Company | None = None
    location: str | None = None
    salary: dict[str, JsonValue] = Field(default_factory=dict)
    published_at: str | None = None
    experience: str | None = None
    work_formats: list[str] = Field(default_factory=list)
    summary: str | None = None
    summary_html: str | None = None
    description: str | None = None
    description_html: str | None = None
    skills: list[str] = Field(default_factory=list)
    locations: list[VacancyLocation] = Field(default_factory=list)
    employment: list[Employment] = Field(default_factory=list)
    min_experience: float | None = None
    min_experience_exclusive: bool = False
    grades: list[Grade] = Field(default_factory=list)
    salary_hidden: bool = False
    source_data: dict[str, JsonValue] = Field(default_factory=dict)
    source: str | None = None
    selection: VacancySelection | None = None
    origins: list[VacancyOrigin] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def former_salary_shape(cls, data: Any) -> Any:
        """TRANSITIONAL (plan §13, decision 3; stage N+1): the response keeps the former salary shape —
        a dict of the first record with an amount, and `salary_hidden` — while vacancies hold records."""
        if not isinstance(data, dict) or not isinstance(data.get("salary"), list):
            return data
        records = [r for r in data["salary"] if isinstance(r, dict)]
        shown = next((r for r in records if r.get("from") is not None or r.get("to") is not None), None)
        salary: dict[str, Any] = {}
        if shown:
            unit = shown.get("unit")
            salary = {
                "from": shown.get("from"),
                "to": shown.get("to"),
                "currencyCode": shown.get("currency"),
                "period": unit if unit in STANDARD_PERIODS else None,
                "gross": shown.get("gross"),
            }
        hidden = any(r.get("state") == "hidden" for r in records)
        return {**data, "salary": salary, "salary_hidden": hidden}


class PoolVacancy(VacancyResponse):
    key: str
    user_status: PoolStatus
    note: str
    status_updated_at: float | None
    first_seen: float
    last_seen: float
    first_run_id: str
    last_run_id: str
    has_card: bool


class RunVacancy(VacancyResponse):
    detail_status: str
    error: str | None
    is_new: bool


class PageMetadata(BaseModel):
    total: int
    page: int
    page_size: int
    pages: int


class PoolVacancyPage(PageMetadata):
    items: list[PoolVacancy]


class RunVacancyPage(PageMetadata):
    items: list[RunVacancy]


class VacancyNavigation(BaseModel):
    key: str | None
    page: PoolVacancyPage | None
