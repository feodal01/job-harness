from typing import Literal

from pydantic import BaseModel


class HealthResponse(BaseModel):
    status: Literal["ok"]
    sources: list[str]
    filters: bool


class StatsResponse(BaseModel):
    briefs: int
    runs: int
    vacancies: int
    events: int
