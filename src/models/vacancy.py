from typing import Annotated

from pydantic import BaseModel, Field, JsonValue

from src.models.company import Company
from src.models.facts import FACT, Evidence, Fact, IsFact
from src.models.filters import Employment, Grade, WorkLocation


class Vacancy(BaseModel):
    """Fields marked `FACT` are vacancy facts in the brief's vocabulary, set by enrichers."""

    id: str
    title: str = Field(min_length=1)
    url: str
    company: Company | None = None
    location: str | None = None
    salary: Annotated[dict[str, JsonValue], FACT] = Field(default_factory=dict)
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
    salary_hidden: Annotated[bool, FACT] = False
    source_data: dict[str, JsonValue] = Field(default_factory=dict)
    # Derived by the enrichment chain from the fields above; recomputed on every enrichment.
    evidence: dict[str, list[Evidence]] = Field(default_factory=dict)
    facts: dict[str, Fact] = Field(default_factory=dict)


def fact_fields() -> frozenset[str]:
    return frozenset(
        name
        for name, field in Vacancy.model_fields.items()
        if any(isinstance(m, IsFact) for m in field.metadata)
    )
