import httpx

from src.app import create_app
from src.core.config import Settings
from src.models.brief import BriefInput
from src.models.vacancy import Vacancy


async def test_brief_update_rolls_back_conditions_and_pool_when_reevaluation_fails(tmp_path, monkeypatch):
    app = create_app(Settings(database_path=tmp_path / "test.sqlite"))
    async with app.router.lifespan_context(app):
        store = app.state.database
        original = BriefInput(name="QA", queries=["QA"])
        brief = store.briefs.save_brief(original)
        run = store.runs.create_run(brief["id"], {}, {})
        for key in ["1", "2"]:
            store.vacancies.save_vacancy(
                run["id"],
                brief["id"],
                Vacancy(id=key, title="QA", url=f"https://hh.ru/vacancy/{key}"),
                source="hh_ru",
                selection={"status": "accepted", "checks": []},
            )
        before = store.vacancies.pool(brief["id"])
        set_selection = store.vacancies.set_pool_selection

        def fail_on_second(brief_id, key, selection):
            if key == "hh_ru:2":
                raise RuntimeError("test storage failure")
            set_selection(brief_id, key, selection)

        monkeypatch.setattr(store.vacancies, "set_pool_selection", fail_on_second)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app, raise_app_exceptions=False), base_url="http://localhost"
        ) as client:
            response = await client.put(
                f"/api/v1/briefs/{brief['id']}", json={"name": "Changed", "queries": ["Sales"]}
            )
            assert response.status_code == 500
        assert store.briefs.brief(brief["id"])["name"] == "QA"
        assert store.vacancies.pool(brief["id"]) == before


async def test_sources_are_described_without_availability_probes(tmp_path):
    calls = []
    app = create_app(
        Settings(database_path=tmp_path / "test.sqlite"),
        transport=httpx.MockTransport(lambda r: calls.append(r) or httpx.Response(503)),
    )
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://localhost"
        ) as client:
            response = await client.get("/api/v1/sources")
            assert response.status_code == 200
            assert {s["id"] for s in response.json()} == {"hh_ru", "getmatch"}
            assert all(s["state"]["status"] == "ready" for s in response.json())
            assert calls == []


def test_brief_service_works_without_http_and_commits_pool_update(tmp_path):
    from src.db import Database
    from src.services.briefs import BriefService
    from src.services.vacancies import VacancyService

    store = Database(tmp_path / "direct.sqlite")
    try:
        briefs = BriefService(store.briefs, store.vacancies, store.transaction, {})
        vacancies = VacancyService(store.vacancies)
        brief = briefs.create(BriefInput(name="Original", queries=["QA"]))
        run = store.runs.create_run(brief["id"], {}, {})
        store.vacancies.save_vacancy(
            run["id"],
            brief["id"],
            Vacancy(id="1", title="QA", url="https://hh.ru/vacancy/1"),
            source="hh_ru",
            selection={"status": "accepted", "checks": []},
        )
        briefs.update(
            brief["id"], BriefInput(name="Changed", queries=["QA"], filters={"excluded_text": ["QA"]})
        )
        assert briefs.list_briefs()[0]["name"] == "Changed"
        assert vacancies.get(brief["id"], "hh_ru:1")["selection"]["status"] == "rejected"
    finally:
        store.close()
