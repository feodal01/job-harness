from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


class CompanyLink(BaseModel):
    id: UUID
    name: str
    url: str
    link_kind: str
    provider: str
    categories: list[str]
    specialization: str | None


class CompanyLinksPage(BaseModel):
    items: list[CompanyLink]
    page: int
    page_size: int
    total: int = Field(ge=0)


CompanyFilterField = Literal["category", "specialization"]


class CompanyFilterOption(BaseModel):
    value: str
    link_count: int = Field(ge=1, description="Количество ссылок каталога с этим значением.")


class CompanyFiltersPage(BaseModel):
    field: CompanyFilterField
    items: list[CompanyFilterOption]
    page: int
    page_size: int
    total: int = Field(ge=0, description="Количество разных значений после фильтрации.")
