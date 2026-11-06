"""UI defaults come from configured adapters, not duplicated constants."""

import httpx

from src.app import create_app
from src.core.config import Settings


async def test_source_catalog_exposes_actual_configured_limits(tmp_path):
    settings = Settings(
        database_path=tmp_path / "search.sqlite",
        sources={
            "hh_ru": {"max_items_per_query": 17, "max_pages_per_query": 4},
            "getmatch": {"max_items_per_query": 333, "max_pages_per_query": 7},
        },
    )
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://localhost"
        ) as client:
            sources = {s["id"]: s for s in (await client.get("/api/v1/sources")).json()["items"]}
    assert sources["hh_ru"]["default_limits"] == {"max_items_per_query": 17, "max_pages_per_query": 4}
    assert sources["getmatch"]["default_limits"] == {"max_items_per_query": 333, "max_pages_per_query": 7}
