from pydantic import BaseModel, Field, JsonValue

from src.models.company import Company
from src.models.filters import Employment, Grade, WorkLocation


class Vacancy(BaseModel):
    id: str
    title: str = Field(min_length=1)
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
    locations: list[WorkLocation] = Field(default_factory=list)
    employment: list[Employment] = Field(default_factory=list)
    min_experience: float | None = None
    min_experience_exclusive: bool = False
    grades: list[Grade] = Field(default_factory=list)
    salary_hidden: bool = False
    source_data: dict[str, JsonValue] = Field(default_factory=dict)
