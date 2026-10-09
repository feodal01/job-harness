from pydantic import Field

from src.parsers.interfaces import RequestSettings


class HHSettings(RequestSettings):
    concurrency: int = Field(default=2, ge=1, le=10)
    interval_seconds: float = Field(default=1.5, ge=0)
    geography_cache_seconds: float = Field(default=300, ge=0)
