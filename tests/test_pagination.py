import httpx
import pytest

from src.db import Database
from src.models.brief import BriefInput
from src.models.vacancy import Vacancy
from tests.test_service import client_for


@pytest.fixture
def populated(tmp_path):
    store = Database(tmp_path / "test.sqlite")
    brief = store.briefs.save_brief(BriefInput(name="Большая лента"))
    run = store.runs.create_run(brief["id"], {}, {})
    for n in range(123):
        selection = {"status": ["accepted", "maybe", "rejected"][n % 3], "checks": []}
        store.vacancies.save_vacancy(
            run["id"],
            brief["id"],
            Vacancy(id=str(n), title=f"QA {n}", url=f"https://example.com/{n}"),
            source="hh_ru",
            selection=selection,
        )
        store.vacancies.add_origin(
            run["id"], f"hh_ru:{n}", "QA", f"https://example.com/list/{n // 10}", n // 10
        )
        if n % 2:
            store.vacancies.set_pool_status(brief["id"], f"hh_ru:{n}", "applied", "")
    # Equal timestamps still have a stable insertion order.
    with store._connection.db:
        store._connection.db.execute("UPDATE brief_vacancies SET first_seen=1")
    store.close()
    return brief["id"], run["id"]


@pytest.mark.parametrize("kind", ["pool", "results"])
async def test_pages_are_bounded_and_cover_every_vacancy(tmp_path, populated, kind):
    brief, run = populated
    path = f"/api/v1/briefs/{brief}/vacancies" if kind == "pool" else f"/api/v1/runs/{run}/results"
    async with client_for(tmp_path, lambda r: httpx.Response(200)) as client:
        client.params = {"selection": "all"}
        first = (await client.get(path)).json()
        assert first["total"] == 123
        assert first["page_size"] == 50
        assert first["pages"] == 3
        ids = []
        for page in range(1, 4):
            data = (await client.get(path, params={"page": page})).json()
            assert data["page"] == page
            assert len(data["items"]) <= 50
            ids.extend(v["id"] for v in data["items"])
            assert all(v["origins"][0]["page"] == int(v["id"]) // 10 for v in data["items"])
        assert ids == list(map(str, range(123)))
        last = (await client.get(path, params={"page": 999})).json()
        assert last["page"] == 3 and len(last["items"]) == 23
        for params in [{"page": 0}, {"page_size": 0}, {"page_size": 101}, {"selection": "bad"}]:
            assert (await client.get(path, params=params)).status_code == 422
        filtered = (
            await client.get(path, params={"selection": "accepted", "page_size": 10, "page": 2})
        ).json()
        assert filtered["total"] == 41
        assert [v["id"] for v in filtered["items"]] == list(map(str, range(30, 60, 3)))


async def test_pool_filters_before_paging_and_clamps_empty_pages(tmp_path, populated):
    brief, _ = populated
    path = f"/api/v1/briefs/{brief}/vacancies"
    async with client_for(tmp_path, lambda r: httpx.Response(200)) as client:
        client.params = {"selection": "all"}
        data = (
            await client.get(path, params={"selection": "candidates", "status": "open", "page_size": 10})
        ).json()
        expected = [str(n) for n in range(123) if n % 3 != 2 and n % 2 == 0]
        assert data["total"] == len(expected)
        assert [v["id"] for v in data["items"]] == expected[:10]
        empty = (await client.get(path, params={"status": "dismissed", "page": 20})).json()
        assert empty == {"items": [], "total": 0, "page": 1, "page_size": 50, "pages": 1}
        assert (await client.get(path, params={"status": "bad"})).status_code == 422


async def test_status_change_removes_last_page_and_returns_updated_card(tmp_path, populated):
    brief, _ = populated
    path = f"/api/v1/briefs/{brief}/vacancies"
    async with client_for(tmp_path, lambda r: httpx.Response(200)) as client:
        client.params = {"selection": "all"}
        params = {"status": "open", "page_size": 61, "page": 2}
        last = (await client.get(path, params=params)).json()
        assert last["total"] == 62 and len(last["items"]) == 1
        key = last["items"][0]["key"]
        updated = await client.patch(f"{path}/{key}", json={"status": "applied", "note": "Откликнулся"})
        assert updated.status_code == 200
        assert updated.json()["note"] == "Откликнулся"
        assert updated.json()["user_status"] == "applied"
        last = (await client.get(path, params=params)).json()
        assert last["page"] == last["pages"] == 1
        assert last["total"] == 61 and len(last["items"]) == 61
        assert all(v["key"] != key for v in last["items"])


def test_new_cards_append_and_only_page_rows_are_loaded(tmp_path, populated, monkeypatch):
    brief, run = populated
    store = Database(tmp_path / "test.sqlite")
    try:
        calls = []
        complete = store.vacancies._complete_vacancy
        monkeypatch.setattr(
            store.vacancies, "_complete_vacancy", lambda data: (calls.append(data["id"]), complete(data))[1]
        )
        before = store.vacancies.pool_page(brief, page=2, page_size=10, selection="all")
        assert len(calls) == 10
        store.vacancies.save_vacancy(
            run, brief, Vacancy(id="new", title="New", url="https://example.com/new"), source="hh_ru"
        )
        after = store.vacancies.pool_page(brief, page=2, page_size=10, selection="all")
        assert [v["id"] for v in before["items"]] == [v["id"] for v in after["items"]]
        assert after["total"] == 124
        assert store.vacancies.pool_page(brief, page=999, selection="all")["items"][-1]["id"] == "new"
        assert store.vacancies.pool_page(brief, selection="candidates")["total"] == 83
    finally:
        store.close()


async def test_reader_crosses_pages_in_both_directions(tmp_path, populated):
    brief, _ = populated
    root = f"/api/v1/briefs/{brief}/vacancies"
    async with client_for(tmp_path, lambda r: httpx.Response(200)) as client:
        client.params = {"selection": "all"}
        next_item = (await client.get(f"{root}/hh_ru:49/adjacent")).json()
        assert next_item["key"] == "hh_ru:50"
        assert next_item["page"]["page"] == 2
        assert next_item["page"]["items"][0]["id"] == "50"
        previous = (await client.get(f"{root}/hh_ru:50/adjacent", params={"direction": "previous"})).json()
        assert previous["key"] == "hh_ru:49"
        assert previous["page"]["page"] == 1
        assert (await client.get(f"{root}/hh_ru:122/adjacent")).json() == {"key": None, "page": None}
        assert (await client.get(f"{root}/hh_ru:0/adjacent", params={"direction": "previous"})).json() == {
            "key": None,
            "page": None,
        }


async def test_reader_does_not_skip_cards_after_status_changes(tmp_path, populated):
    brief, _ = populated
    root = f"/api/v1/briefs/{brief}/vacancies"
    async with client_for(tmp_path, lambda r: httpx.Response(200)) as client:
        client.params = {"selection": "all"}
        # Last item of the first page of the inbox; removing it moves the next item onto page 1.
        first = (await client.get(root, params={"status": "open"})).json()
        assert first["items"][-1]["id"] == "98"
        await client.patch(f"{root}/hh_ru:98", json={"status": "applied"})
        next_item = (await client.get(f"{root}/hh_ru:98/adjacent", params={"status": "open"})).json()
        assert next_item["key"] == "hh_ru:100"
        assert next_item["page"]["page"] == 1
        assert next_item["page"]["items"][-1]["id"] == "100"


@pytest.mark.parametrize("sort", ["first_seen", "newest", "updated"])
async def test_reader_keeps_filters_and_sort_order(tmp_path, populated, sort):
    brief, _ = populated
    root = f"/api/v1/briefs/{brief}/vacancies"
    params = {"sort": sort, "selection": "candidates", "status": "applied", "q": "QA 1", "page_size": 2}
    async with client_for(tmp_path, lambda r: httpx.Response(200)) as client:
        client.params = {"selection": "all"}
        first = (await client.get(root, params=params)).json()
        second = (await client.get(root, params={**params, "page": 2})).json()
        boundary = first["items"][-1]["key"]
        response = await client.get(f"{root}/{boundary}/adjacent", params=params)
        assert response.status_code == 200
        assert response.json()["key"] == second["items"][0]["key"]
        assert response.json()["page"] == second
        reverse = (
            await client.get(
                f"{root}/{second['items'][0]['key']}/adjacent", params={**params, "direction": "previous"}
            )
        ).json()
        assert reverse["key"] == boundary
        assert reverse["page"] == first


async def test_reader_retains_position_when_sort_timestamp_changes(tmp_path, populated):
    brief, _ = populated
    root = f"/api/v1/briefs/{brief}/vacancies"
    async with client_for(tmp_path, lambda r: httpx.Response(200)) as client:
        client.params = {"selection": "all"}
        params = {"sort": "updated", "page_size": 2}
        first = (await client.get(root, params=params)).json()
        second = (await client.get(root, params={**params, "page": 2})).json()
        anchor = first["items"][-1]
        await client.patch(f"{root}/{anchor['key']}", json={"status": "interview"})
        result = (
            await client.get(
                f"{root}/{anchor['key']}/adjacent",
                params={
                    **params,
                    "anchor_time": anchor["status_updated_at"] or anchor["first_seen"],
                },
            )
        ).json()
        assert result["key"] == second["items"][0]["key"]
