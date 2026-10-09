from pydantic import Field

from src.parsers.interfaces import RequestSettings


class GetmatchSettings(RequestSettings):
    # The whole feed (934 offers on 2026-10-02) fits into the default page limit with room to spare.
    max_items_per_query: int = Field(default=2000, ge=1, le=5000)
    max_pages_per_query: int = Field(default=15, ge=1, le=50)
    page_size: int = Field(default=100, ge=1, le=100)
