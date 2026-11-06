"""Reading every matching catalog row now, with the user's session, page by page."""

import json

import httpx
import pytest

from src.services.auth import AuthError, LoginRequired
from src.services.companies import CompanyCatalog

LOGIN = "http://127.0.0.1:8765/auth"


def row(index: int, provider: str = "ats:lever") -> dict:
    return {
        "id": f"00000000-0000-4000-8000-{index:012d}",
        "name": f"Company {index}",
        "url": f"https://jobs.lever.co/company{index}",
        "link_kind": "ats_board",
        "provider": provider,
        "categories": [],
        "specialization": None,
    }


class FakeAuth:
    """Supabase behind the user's session; `answer` decides each response."""

    login_url = LOGIN

    def __init__(self, answer, token: str | None = "token") -> None:
        self.answer, self.token = answer, token
        self.requests: list[dict] = []
        self.invalidated: list[str] = []

    async def access_token(self) -> str:
        if self.token is None:
            raise LoginRequired(LOGIN)
        return self.token

    async def invalidate(self, token: str) -> None:
        self.invalidated.append(token)

    async def request(self, method: str, path: str, **kwargs) -> httpx.Response:
        self.requests.append(kwargs["params"])
        return self.answer(kwargs["params"])


def table(rows: list[dict], cap: int | None = None):
    """A PostgREST table that may return fewer rows than asked for, like a server max-rows limit."""

    def answer(params: dict) -> httpx.Response:
        offset, limit = int(params["offset"]), int(params["limit"])
        if cap:
            limit = min(limit, cap)
        if offset >= len(rows) and rows:
            return httpx.Response(416, headers={"content-range": f"*/{len(rows)}"}, json={"code": "PGRST103"})
        page = rows[offset : offset + limit]
        end = offset + len(page) - 1
        return httpx.Response(206, headers={"content-range": f"{offset}-{end}/{len(rows)}"}, json=page)

    return answer


async def test_reads_every_row_even_when_the_server_returns_short_pages():
    rows = [row(i) for i in range(7)]
    auth = FakeAuth(table(rows, cap=3))
    result = await CompanyCatalog(auth).rows(page_size=1000)
    assert [str(r.id) for r in result] == [r["id"] for r in rows]
    assert [p["offset"] for p in auth.requests] == ["0", "3", "6"]  # offsets follow received rows


@pytest.mark.parametrize("count", [0, 1, 5, 6])
async def test_page_boundaries(count):
    rows = [row(i) for i in range(count)]
    auth = FakeAuth(table(rows))
    assert len(await CompanyCatalog(auth).rows(page_size=5)) == count
    assert len(auth.requests) == (1 if count <= 5 else 2)


async def test_filters_are_sent_to_supabase_and_values_cannot_break_out():
    auth = FakeAuth(table([row(1)]))
    await CompanyCatalog(auth).rows(
        name="100%_*", ids=["00000000-0000-4000-8000-000000000001"], providers=["ats:lever", 'a"b,c)']
    )
    sent = auth.requests[0]
    assert sent["name"] == "ilike.%100\\%\\_\\*%"
    assert sent["id"] == "in.(00000000-0000-4000-8000-000000000001)"
    assert sent["provider"] == 'in.("ats:lever","a\\"b,c)")'
    assert sent["order"] == "id.asc"


async def test_empty_id_or_provider_lists_ask_nothing_and_bad_ids_are_refused():
    auth = FakeAuth(table([row(1)]))
    catalog = CompanyCatalog(auth)
    assert await catalog.rows(ids=[]) == []
    assert await catalog.rows(providers=[]) == []
    assert auth.requests == []
    with pytest.raises(ValueError):
        await catalog.rows(ids=["1); drop table"])


async def test_without_a_session_nothing_is_requested():
    auth = FakeAuth(table([row(1)]), token=None)
    with pytest.raises(LoginRequired) as error:
        await CompanyCatalog(auth).rows()
    assert error.value.login_url == LOGIN and auth.requests == []


async def test_an_expired_session_is_dropped_and_asks_to_log_in():
    auth = FakeAuth(lambda _: httpx.Response(401, json={}))
    with pytest.raises(LoginRequired):
        await CompanyCatalog(auth).rows()
    assert auth.invalidated == ["token"]


@pytest.mark.parametrize(
    ("response", "status", "message"),
    [
        (httpx.Response(403, json={}), 403, "нет доступа"),
        (httpx.Response(500, json={}), 502, "Не удалось получить"),
        (httpx.Response(206, json=[row(1)]), 502, "некорректный"),  # no content-range
        (httpx.Response(206, headers={"content-range": "0-0/1"}, content=b"not json"), 502, "некорректный"),
        (httpx.Response(206, headers={"content-range": "0-0/1"}, json=[{"id": "x"}]), 502, "некорректный"),
        (httpx.Response(206, headers={"content-range": "0-0/-1"}, json=[]), 502, "некорректный"),
    ],
)
async def test_supabase_failures_are_errors_with_a_reason(response, status, message):
    with pytest.raises(AuthError) as error:
        await CompanyCatalog(FakeAuth(lambda _: response)).rows()
    assert error.value.status_code == status and message in str(error.value)


async def test_a_catalog_larger_than_the_limit_is_refused_instead_of_read_halfway():
    rows = [row(i) for i in range(5)]
    with pytest.raises(AuthError, match="слишком большой"):
        await CompanyCatalog(FakeAuth(table(rows))).rows(page_size=2, max_rows=4)


async def test_rows_that_change_shape_mid_read_stop_cleanly():
    pages = iter(
        [
            httpx.Response(206, headers={"content-range": "0-1/4"}, json=[row(1), row(2)]),
            httpx.Response(206, headers={"content-range": "*/2"}, json=[]),  # rows deleted meanwhile
        ]
    )
    result = await CompanyCatalog(FakeAuth(lambda _: next(pages))).rows(page_size=2)
    assert len(result) == 2


def test_fixture_rows_are_valid_catalog_rows():
    from src.schemas.companies import CompanyLink

    CompanyLink.model_validate_json(json.dumps(row(1)))
