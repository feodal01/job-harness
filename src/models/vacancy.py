from typing import Annotated, Any

from pydantic import BaseModel, Field, JsonValue, model_validator

from src.models.company import Company
from src.models.facts import FACT, Conflict, Evidence, Fact, IsFact
from src.models.filters import Employment, Grade, WorkLocation
from src.models.salary import SalaryRecord, legacy_salary


class Vacancy(BaseModel):
    """Fields marked `FACT` are vacancy facts in the brief's vocabulary, set by enrichers."""

    id: str
    title: str = Field(min_length=1)
    url: str
    company: Company | None = None
    location: str | None = None
    # Salary records (listing, card, text) in the brief's vocabulary, set by the source normalizers.
    salary: Annotated[list[SalaryRecord], FACT] = Field(default_factory=list)
    published_at: Annotated[str | None, FACT] = None
    experience: str | None = None
    work_formats: Annotated[list[str], FACT] = Field(default_factory=list)
    summary: str | None = None
    summary_html: str | None = None
    description: str | None = None
    description_html: str | None = None
    skills: list[str] = Field(default_factory=list)
    locations: Annotated[list[WorkLocation], FACT] = Field(default_factory=list)
    employment: Annotated[list[Employment], FACT] = Field(default_factory=list)
    min_experience: Annotated[float | None, FACT] = None
    min_experience_exclusive: Annotated[bool, FACT] = False
    grades: list[Grade] = Field(default_factory=list)
    source_data: dict[str, JsonValue] = Field(default_factory=dict)
    # Derived by the enrichment chain from the fields above; recomputed on every enrichment.
    evidence: dict[str, list[Evidence]] = Field(default_factory=dict)
    facts: dict[str, Fact] = Field(default_factory=dict)
    conflicts: list[Conflict] = Field(default_factory=list)

    @classmethod
    def from_stored(cls, row: dict[str, Any]) -> "Vacancy":
        """A vacancy from a stored corpus row: the row's other keys (source, selection…) are not fields.
        TRANSITIONAL (stage N+1): the former `salary_hidden` key is kept for `legacy_salary_dict`."""
        return cls.model_validate(
            {k: v for k, v in row.items() if k in cls.model_fields or k == "salary_hidden"}
        )

    @model_validator(mode="before")
    @classmethod
    def legacy_salary_dict(cls, data: Any) -> Any:
        """TRANSITIONAL (stage N+1): a stored or ATS salary dict with `salary_hidden` becomes records."""
        if isinstance(data, dict) and (isinstance(data.get("salary"), dict) or "salary_hidden" in data):
            data = dict(data)
            hidden = bool(data.pop("salary_hidden", False))
            raw = data.get("salary")
            if isinstance(raw, dict) or hidden:
                data["salary"] = legacy_salary(raw if isinstance(raw, dict) else {}, hidden)
        return data


def fact_fields() -> frozenset[str]:
    return frozenset(
        name
        for name, field in Vacancy.model_fields.items()
        if any(isinstance(m, IsFact) for m in field.metadata)
    )
