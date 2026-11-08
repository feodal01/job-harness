import httpx
import pytest
from geo_support import confirmed

from src.core.errors import ConflictError
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
            await client.put(f"/api/v1/briefs/{b['id']}", json={"name": "AI edited", "filters": confirmed()})
            assert (await client.get(f"{path}/hh_ru:3")).json()["user_status"] == "applied"


async def test_saved_search_options_do_not_start_search(tmp_path):
    async with client_for(tmp_path, lambda r: pytest.fail("Saving must not call sources")) as client:
        search = {
            "source_rules": {"mode": "selected", "include": ["getmatch"]},
            "limits": {"getmatch": {"max_items_per_query": 20}},
            "company_profiles": False,
        }
        b = (
            await client.post("/api/v1/briefs", json={"name": "AI", "search": search, "filters": confirmed()})
        ).json()
        assert b["search"]["source_rules"]["include"] == ["getmatch"]
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
        assert (await client.get("/")).status_code == 303  # closed behind the login screen
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
        response = await client.put(
            f"/api/v1/briefs/{b['id']}", json={"name": "Edited", "queries": ["QA"], "filters": confirmed()}
        )
        assert response.status_code == 200
        item = (await client.get(f"/api/v1/briefs/{b['id']}/vacancies/retired_source:one")).json()
        assert item["selection"]["status"] == "accepted"


async def test_funnel_filters_by_source_and_navigation_keeps_the_filter(tmp_path):
    store = Database(tmp_path / "test.sqlite")
    b = store.briefs.save_brief(BriefInput(name="Sources"))
    r = store.runs.create_run(b["id"], {}, {})
    for n, source in enumerate(["hh_ru", "getmatch", "hh_ru", "getmatch"]):
        store.vacancies.save_vacancy(r["id"], b["id"], Vacancy(id=str(n), title="QA", url="u"), source=source)
    store.close()
    async with client_for(tmp_path, lambda r: httpx.Response(200)) as client:
        base = f"/api/v1/briefs/{b['id']}/vacancies"
        page = (await client.get(base, params={"source": "getmatch"})).json()
        assert [v["key"] for v in page["items"]] == ["getmatch:1", "getmatch:3"] and page["total"] == 2
        assert (await client.get(base)).json()["total"] == 4
        step = (await client.get(f"{base}/getmatch:1/adjacent", params={"source": "getmatch"})).json()
        assert step["key"] == "getmatch:3" and step["page"]["total"] == 2


async def test_delete_brief_removes_its_runs_funnel_and_notes_only(tmp_path):
    store = Database(tmp_path / "test.sqlite")
    gone = store.briefs.save_brief(BriefInput(name="Gone", queries=["QA"]))
    kept = store.briefs.save_brief(BriefInput(name="Kept", queries=["QA"]))
    for brief in (gone, kept):
        run = store.runs.create_run(brief["id"], {}, {})
        store.vacancies.save_vacancy(
            run["id"], brief["id"], Vacancy(id="1", title="QA", url="u"), source="hh_ru"
        )
        store.vacancies.set_pool_status(brief["id"], "hh_ru:1", "applied", "Note")
    store.close()
    async with client_for(tmp_path, lambda r: httpx.Response(200)) as client:
        deleted = await client.delete(f"/api/v1/briefs/{gone['id']}")
        assert deleted.status_code == 200 and deleted.json() == {"id": gone["id"], "removed_vacancies": 1}
        assert [b["id"] for b in (await client.get("/api/v1/briefs")).json()] == [kept["id"]]
        assert (await client.get(f"/api/v1/runs?brief_id={gone['id']}")).json() == []
        assert (await client.get(f"/api/v1/briefs/{gone['id']}/vacancies")).status_code == 404
        survivor = (
            await client.get(f"/api/v1/briefs/{kept['id']}/vacancies", params={"status": "all"})
        ).json()
        assert survivor["items"][0]["note"] == "Note" and survivor["items"][0]["user_status"] == "applied"
        assert len((await client.get(f"/api/v1/runs?brief_id={kept['id']}")).json()) == 1
        assert (await client.delete(f"/api/v1/briefs/{gone['id']}")).status_code == 404


def test_delete_brief_is_refused_while_its_search_runs(tmp_path):
    store = Database(tmp_path / "test.sqlite")
    brief = store.briefs.save_brief(BriefInput(name="Busy", queries=["QA"]))
    store.runs.create_run(brief["id"], {}, {})
    with pytest.raises(ConflictError, match="идёт поиск"):
        store.briefs.delete_brief(brief["id"])
    assert store.briefs.brief(brief["id"]) is not None
    store.close()
