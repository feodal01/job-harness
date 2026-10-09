import httpx
import pytest

from src.db import Database
from src.models.brief import BriefInput
from src.models.vacancy import Vacancy
from tests.test_service import client_for


async def test_funnel_counts_stages_search_and_persistence(tmp_path):
    store = Database(tmp_path / "test.sqlite")
    b = store.briefs.save_brief(BriefInput(name="AI"))
    other = store.briefs.save_brief(BriefInput(name="Empty"))
    run = store.runs.create_run(b["id"], {}, {})
    statuses = ["new", "viewed", "dismissed", "applied", "interview", "rejected", "offer"]
    for n, status in enumerate(statuses):
        store.vacancies.save_vacancy(
            run["id"],
            b["id"],
            Vacancy(id=str(n), title=f"Руководитель {n}", url="https://example.com"),
            source="hh_ru",
            selection={"status": "accepted", "checks": []},
        )
        store.vacancies.set_pool_status(b["id"], f"hh_ru:{n}", status, f"Note {n}")
    store.close()
    for _ in range(2):
        async with client_for(tmp_path, lambda r: httpx.Response(200)) as client:
            overviews = {x["id"]: x for x in (await client.get("/api/v1/briefs")).json()}
            assert overviews[b["id"]]["funnel"]["all"] == 7
            assert overviews[b["id"]]["funnel"]["open"] == 2
            assert overviews[other["id"]]["funnel"] == {"all": 0, "open": 0}
            path = f"/api/v1/briefs/{b['id']}/vacancies"
            for n, status in enumerate(statuses):
                response = await client.get(path, params={"status": status, "q": "РУКОВОДИТЕЛЬ"})
                assert response.status_code == 200
                assert response.json()["total"] == 1
                assert response.json()["items"][0]["note"] == f"Note {n}"
            detail = await client.get(f"{path}/hh_ru:3")
            assert detail.json()["user_status"] == "applied"
            updated = await client.patch(f"{path}/hh_ru:3", json={"status": "offer", "note": "Получен оффер"})
            assert updated.status_code == 200
            assert updated.json()["user_status"] == "offer"
            assert (await client.patch(f"{path}/hh_ru:3", json={"status": "invalid"})).status_code == 422
            # Restore for the restart assertion.
            await client.patch(f"{path}/hh_ru:3", json={"status": "applied", "note": "Note 3"})
            await client.put(f"/api/v1/briefs/{b['id']}", json={"name": "AI edited"})
            assert (await client.get(f"{path}/hh_ru:3")).json()["user_status"] == "applied"


async def test_saved_search_options_do_not_start_search(tmp_path):
    async with client_for(tmp_path, lambda r: pytest.fail("Saving must not call sources")) as client:
        search = {
            "sources": ["getmatch"],
            "limits": {"getmatch": {"max_items_per_query": 20}},
            "company_profiles": False,
        }
        b = (await client.post("/api/v1/briefs", json={"name": "AI", "search": search})).json()
        assert b["search"]["sources"] == ["getmatch"]
        assert (await client.get("/api/v1/runs")).json() == []
        loaded = (await client.get("/api/v1/briefs")).json()[0]
        assert loaded["search"] == b["search"]


async def test_frontend_assets_and_search_sorting(tmp_path):
    store = Database(tmp_path / "test.sqlite")
    b = store.briefs.save_brief(BriefInput(name="Ordering"))
    r = store.runs.create_run(b["id"], {}, {})
    for n, company in enumerate(["ЯНДЕКС", "Другой", "Яндекс"]):
        store.vacancies.save_vacancy(
            r["id"],
            b["id"],
            Vacancy(id=str(n), title="QA", url="u", company={"name": company}),
            source="hh_ru",
        )
    store.vacancies.set_pool_status(b["id"], "hh_ru:0", "offer", "Saved")
    # Finding the same vacancy again must not reset the funnel stage or note.
    store.vacancies.save_vacancy(
        r["id"], b["id"], Vacancy(id="0", title="QA", url="u", company={"name": "ЯНДЕКС"}), source="hh_ru"
    )
    store.close()
    async with client_for(tmp_path, lambda r: httpx.Response(200)) as client:
        page = (
            await client.get(f"/api/v1/briefs/{b['id']}/vacancies", params={"q": "яндекс", "sort": "newest"})
        ).json()
        assert [v["id"] for v in page["items"]] == ["2", "0"]
        assert page["items"][1]["user_status"] == "offer"
        assert page["items"][1]["note"] == "Saved"
        updated = (await client.get(f"/api/v1/briefs/{b['id']}/vacancies", params={"sort": "updated"})).json()
        assert updated["items"][0]["id"] == "0"
        assert "/assets/app.js" in (await client.get("/")).text
        assert (await client.get("/assets/app.js")).status_code == 200
        assert (await client.get("/assets/app.css")).status_code == 200


async def test_hidden_vacancies_are_excluded_from_default_feed_counts_and_navigation(tmp_path):
    store = Database(tmp_path / "test.sqlite")
    brief = store.briefs.save_brief(BriefInput(name="QA", queries=["QA"]))
    run = store.runs.create_run(brief["id"], {}, {})
    for n, status in enumerate(["accepted", "rejected", "maybe"]):
        store.vacancies.save_vacancy(
            run["id"],
            brief["id"],
            Vacancy(id=str(n), title="QA", url="u"),
            source="hh_ru",
            selection={"status": status, "checks": []},
        )
    store.close()
    async with client_for(tmp_path, lambda r: pytest.fail("Read-only feed must not fetch sources")) as client:
        path = f"/api/v1/briefs/{brief['id']}/vacancies"
        page = (await client.get(path)).json()
        assert [v["id"] for v in page["items"]] == ["0", "2"]
        assert (await client.get(path, params={"selection": "all"})).json()["total"] == 3
        assert (await client.get(path, params={"selection": "rejected"})).json()["total"] == 1
        adjacent = (await client.get(f"{path}/hh_ru:0/adjacent")).json()
        assert adjacent["key"] == "hh_ru:2"
        overview = (await client.get("/api/v1/briefs")).json()[0]
        assert overview["funnel"] == {"new": 2, "all": 2, "open": 2}
        assert overview["hidden_count"] == 1


async def test_edit_brief_with_historical_unregistered_source(tmp_path):
    store = Database(tmp_path / "test.sqlite")
    b = store.briefs.save_brief(BriefInput(name="Old source", queries=["QA"]))
    run = store.runs.create_run(b["id"], {}, {})
    store.vacancies.save_vacancy(
        run["id"], b["id"], Vacancy(id="one", title="QA", url="u"), source="retired_source"
    )
    store.close()
    async with client_for(tmp_path, lambda r: pytest.fail("Local reevaluation only")) as client:
        response = await client.put(f"/api/v1/briefs/{b['id']}", json={"name": "Edited", "queries": ["QA"]})
        assert response.status_code == 200
        item = (await client.get(f"/api/v1/briefs/{b['id']}/vacancies/retired_source:one")).json()
        assert item["selection"]["status"] == "accepted"
