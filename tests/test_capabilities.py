import asyncio
from dataclasses import replace
from pathlib import Path

import httpx
from geo_support import confirmed, with_geography

from src.app import create_app
from src.core.config import Settings
from src.models.brief import BriefInput


async def test_hh_requires_query_before_starting_run(tmp_path):
    calls = []
    app = create_app(
        Settings(database_path=tmp_path / "test.sqlite"),
        transport=httpx.MockTransport(lambda r: calls.append(r) or httpx.Response(500)),
    )
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://localhost") as c:
            brief = await c.post("/api/v1/briefs", json={"name": "Empty queries", "filters": confirmed()})
            assert brief.status_code == 201
            response = await c.post(
                "/api/v1/runs", json={"brief_id": brief.json()["id"], "sources": ["hh_ru"]}
            )
            assert response.status_code == 422
            assert "query" in response.json()["detail"].lower()
            assert (await c.get("/api/v1/runs")).json() == []
            assert calls == []


async def test_catalog_distinguishes_native_parameters_from_extracted_fields(tmp_path):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, text=(Path(__file__).parent / "fixtures/no_results.html").read_text())

    app = create_app(
        Settings(database_path=tmp_path / "test.sqlite", sources={"hh_ru": {"interval_seconds": 0}}),
        transport=httpx.MockTransport(with_geography(handler)),
    )
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://localhost") as c:
            source = (await c.get("/api/v1/sources")).json()[0]
            assert "capabilities" in source
            capabilities = source["capabilities"]
            assert capabilities["query_parameters"]["query"] == "native"
            assert capabilities["query_parameters"]["locations"] == "native"
            assert capabilities["query_parameters"]["salary"] == "local"
            assert "search" in capabilities["fields"]["salary"]
            assert capabilities["fields"]["description"] == ["vacancy_details"]
            assert "company_profile" in capabilities["actions"]
            brief = (
                await c.post(
                    "/api/v1/briefs", json={"name": "Mapping", "queries": ["C++ QA"], "filters": confirmed()}
                )
            ).json()
            response = await c.post(
                "/api/v1/runs",
                json={"brief_id": brief["id"], "sources": ["hh_ru"], "company_profiles": False},
            )
            assert response.status_code == 202
            run_id = response.json()["id"]
            await asyncio.gather(*app.state.engine.tasks.values())
            listings = [r for r in calls if r.url.path == "/search/vacancy"]
            assert listings[0].url.params["text"] == "C++ QA"
            assert "area" in listings[0].url.params
            assert (await c.get(f"/api/v1/runs/{run_id}")).json()["status"] == "complete"
            events = (await c.get(f"/api/v1/runs/{run_id}/events")).json()
            mappings = [e["data"] for e in events if e["event"] == "search.request"]
            assert len(mappings) == 3
            mapping = mappings[0]
            assert mapping["parameters"]["query"] == "C++ QA"
            assert mapping["parameters"]["filters"]["locations"][0]["city"] == "Moscow"
            assert mapping["url"] == str(listings[0].url)


async def test_unsupported_operation_is_skipped_and_recorded_not_rejected(tmp_path):
    """Spec 4: what a source cannot do is skipped; the run records what was actually applied."""
    calls = []

    def handler(request):
        calls.append(request.url.path)
        return httpx.Response(200, text=(Path(__file__).parent / "fixtures/no_results.html").read_text())

    app = create_app(
        Settings(database_path=tmp_path / "test.sqlite", sources={"hh_ru": {"interval_seconds": 0}}),
        transport=httpx.MockTransport(with_geography(handler)),
    )
    async with app.router.lifespan_context(app):
        source = app.state.engine.sources["hh_ru"]
        app.state.engine.sources[source.id] = replace(source, company_profile=None)
        brief = app.state.briefs_service.create(
            BriefInput(name="Unsupported", queries=["QA"], filters=confirmed())
        )
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://localhost") as c:
            response = await c.post(
                "/api/v1/runs", json={"brief_id": brief["id"], "sources": ["hh_ru"], "company_profiles": True}
            )
            assert response.status_code == 202, response.text
            await asyncio.gather(*app.state.engine.tasks.values())
            run = (await c.get(f"/api/v1/runs/{response.json()['id']}")).json()
            assert run["status"] == "complete"
            assert run["request"]["company_profiles"] == {"hh_ru": False}
            assert not any(path.startswith("/employer/") for path in calls)


def test_getmatch_unavailable_experience_and_employment_are_explicit():
    from src.parsers.registry import build_sources

    source = build_sources(Settings())["getmatch"]
    assert source.unsupported_filters == {"experience_years", "employment"}
    assert source.capabilities.query_parameters["employment"] == "none"
    assert source.capabilities.query_parameters["experience_years"] == "none"
    assert "employment" not in source.capabilities.fields
    assert "min_experience" not in source.capabilities.fields


def test_each_claimed_local_filter_has_both_outcomes_from_parsed_facts():
    """Parser inputs, not fabricated normalized objects, prove filter capabilities."""
    import html
    import json

    from src.enrichment.registry import default_enrichment
    from src.models.filters import SearchFilters
    from src.models.vacancy import Vacancy
    from src.parsers.aggregators.getmatch.parser import GetmatchSearch, GetmatchVacancyDetails
    from src.parsers.aggregators.hh.parser import HHVacancyDetails
    from src.parsers.registry import build_sources
    from tests.filter_support import evaluate

    conditions = {
        "locations": [{"country": "RU", "city": "Moscow", "geoname_id": 524901}],
        "minimum_work_format": "hybrid",
        "employment": ["full_time"],
        "experience_years": 2,
        "salary": {"minimum": 200, "currency": "RUB", "period": "month"},
        "published_after": "2026-09-01",
        "relocation_support": True,
        "excluded_companies": ["Excluded"],
        "excluded_text": ["forbidden"],
    }
    for source_id, source in build_sources(Settings()).items():
        outcomes = {}
        for matches in (True, False):
            text = "Помощь с релокацией." if matches else "Без помощи с переездом. forbidden"
            if source_id == "hh_ru":
                view = {
                    "vacancyId": 1,
                    "name": "QA",
                    "workFormats": ["HYBRID" if matches else "ON_SITE"],
                    "workExperience": "between1And3" if matches else "between3And6",
                    "employmentForm": "FULL" if matches else "PART",
                    "description": text,
                    "publicationTimeIso": "2026-09-20" if matches else "2026-08-01",
                    "compensation": {
                        "from": 100,
                        "to": 300 if matches else 150,
                        "currencyCode": "RUR",
                        "mode": "MONTH",
                    },
                    "company": {"id": "1", "name": "Good" if matches else "Excluded"},
                    "area": {"countryIsoCode": "RU", "name": "Москва" if matches else "Томск"},
                }
                body = (
                    '<template id="HH-Lux-InitialState">'
                    + html.escape(json.dumps({"vacancyView": view}))
                    + "</template>"
                )
                vacancy = HHVacancyDetails().parse(
                    body, Vacancy(id="1", title="QA", url="https://hh.ru/vacancy/1")
                )
            else:
                offer = {
                    "id": 1,
                    "position": "QA",
                    "url": "/vacancies/1-qa",
                    "offer_description": text,
                    "company": {"id": 1, "name": "Good" if matches else "Excluded"},
                    "published_at": "2026-09-20" if matches else "2026-08-01",
                    "salary_display_from": 100,
                    "salary_display_to": 300 if matches else 150,
                    "salary_currency": "RUB",
                    "salary_description": "₽/мес",
                    "location_requirements": [
                        {
                            "location_id": "moscow" if matches else "tomsk",
                            "country": "Россия",
                            "city": "Москва" if matches else "Томск",
                            "format": "hybrid" if matches else "office",
                        },
                        # Getmatch relocation is structured: one more record of the same place.
                        {
                            "location_id": "moscow" if matches else "tomsk",
                            "country": "Россия",
                            "city": "Москва" if matches else "Томск",
                            "format": "relocation_company" if matches else "relocation_candidate",
                        },
                    ],
                }
                search = GetmatchSearch(10)
                body = json.dumps({"offers": [offer], "meta": {"total": 1, "offset": 0, "limit": 10}})
                vacancy = search.parse(body, search.build("")).vacancies[0]
                detail = '<link rel="canonical" href="https://getmatch.ru/vacancies/1-qa">'
                detail += '<section class="b-vacancy-description">' + text + "</section>"
                vacancy = GetmatchVacancyDetails().parse(detail, vacancy)
            # The engine enriches what parsers return; local checks read the enriched facts.
            vacancy = default_enrichment().enrich(vacancy, source_id)
            for name, value in conditions.items():
                result = evaluate(
                    vacancy,
                    SearchFilters(**{name: value}),
                    complete=True,
                    queries=["QA"],
                    unsupported=source.unsupported_filters,
                )
                check = next(c for c in result.checks if c.field == name)
                outcomes.setdefault(name, set()).add(check.status)
        for name, mode in source.capabilities.query_parameters.items():
            if mode == "local":
                assert outcomes[name] == {"accepted", "rejected"}, (source_id, name, outcomes[name])
