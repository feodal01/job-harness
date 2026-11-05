from pydantic import BaseModel

from src.models.brief import SearchOptions
from src.models.filters import SearchFilters
from src.schemas.runs import RunSummary


class BriefResponse(BaseModel):
    id: str
    name: str
    text: str
    queries: list[str]
    filters: SearchFilters
    search: SearchOptions | None = None
    updated_at: float
    migration_note: str | None = None
    filters_migration_notice: str | None = None


class BriefDeleted(BaseModel):
    id: str
    removed_vacancies: int


class BriefOverview(BriefResponse):
    funnel: dict[str, int]
    hidden_count: int
    last_run: RunSummary | None
