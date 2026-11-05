import asyncio
import base64
import hashlib
import json
import time
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from src.app import create_app
from src.auth.storage import SessionStore
from src.core.config import Settings
from src.services.auth import AuthService

ORIGIN = "http://127.0.0.1:8765"
ACTION = {"Origin": ORIGIN, "X-Job-Search-Auth": "1"}
USER_ID = "11111111-1111-4111-8111-111111111111"


class Supabase:
    def __init__(self):
        self.requests = []
        self.exchanges = 0
        self.refreshes = 0
        self.refresh_status = 200
        self.catalog_status = 206
        self.filters_status = 206
        self.enabled = {"github": True, "google": False, "email": False}
        self.expires_in = 3600
        self.anonymous = False

    def session(self, token="access-one", refresh="refresh-one"):
        return {
            "access_token": token,
            "refresh_token": refresh,
            "expires_in": self.expires_in,
            "user": {
                "id": USER_ID,
                "email": "test@example.test",
                "role": "authenticated",
                "is_anonymous": self.anonymous,
                "user_metadata": {"full_name": "Test User"},
            },
        }

    async def __call__(self, request):
        self.requests.append(request)
        assert request.url.host == "tcsqtzjfrtsuapnqiyke.supabase.co"
        assert request.headers["apikey"].startswith("sb_publishable_")
        if request.url.path == "/auth/v1/settings":
            return httpx.Response(200, json={"external": self.enabled})
        if request.url.path == "/auth/v1/token":
            payload = json.loads(request.content)
            if request.url.params["grant_type"] == "pkce":
                assert payload["auth_code"] == "one-use-code"
                self.exchanges += 1
                return httpx.Response(200, json=self.session())
            assert payload["refresh_token"] == "refresh-one"
            self.refreshes += 1
            await asyncio.sleep(0.01)
            self.expires_in = 3600
            return httpx.Response(self.refresh_status, json=self.session("access-two", "refresh-two"))
        if request.url.path == "/auth/v1/logout":
            assert request.url.params["scope"] == "local"
            return httpx.Response(204)
        if request.url.path == "/rest/v1/rpc/company_filter_options":
            assert request.method == "GET"
            assert request.headers["authorization"].startswith("Bearer access-")
            assert request.headers["prefer"] == "count=exact"
            if self.filters_status == 416:
                return httpx.Response(416, headers={"content-range": "*/44"}, json={"code": "PGRST103"})
            return httpx.Response(
                self.filters_status,
                headers={"content-range": "0-0/44"},
                json=[{"value": "Финтех", "link_count": 102}],
            )
        if request.url.path == "/rest/v1/company_links":
            assert request.method == "GET"
            assert request.headers["authorization"].startswith("Bearer access-")
            assert request.headers["prefer"] == "count=exact"
            if self.catalog_status == 416:
                return httpx.Response(416, headers={"content-range": "*/772"}, json={"code": "PGRST103"})
            return httpx.Response(
                self.catalog_status,
                headers={"content-range": "0-0/772"},
                json=[
                    {
                        "id": USER_ID,
                        "name": "Example",
                        "url": "https://example.test/jobs",
                        "link_kind": "career_page",
                        "provider": "web",
                        "categories": ["Финтех"],
                        "specialization": "Платежи",
                    }
                ],
            )
        raise AssertionError(f"Unexpected remote request: {request.method} {request.url.path}")


@pytest.fixture
async def setup(tmp_path):
    remote = Supabase()
    settings = Settings(database_path=tmp_path / "search.sqlite")
    app = create_app(settings, transport=httpx.MockTransport(remote), url=ORIGIN)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN) as client:
            yield client, app.state.auth_service, remote


async def start_login(client, provider="github"):
    page = await client.get("/auth")
    if page.status_code == 303:
        # Already signed in: the page sends the browser to the app, so the flow cookie is set by hand.
        client.cookies.set(client._transport.app.state.auth_service.cookie_name, "b" * 43)
    else:
        assert page.status_code == 200
    response = await client.post("/api/v1/auth/start", json={"provider": provider}, headers=ACTION)
    assert response.status_code == 200, response.text
    return parse_qs(urlsplit(response.json()["url"]).query)


async def login(client, provider="github"):
    query = await start_login(client, provider)
    result = await client.get(query["redirect_to"][0] + "&code=one-use-code")
    assert result.status_code == 303
    assert result.headers["location"] == ORIGIN + "/auth?result=success"
    return query


async def test_authorize_is_one_cheap_call_without_starting_expiring_oauth(setup):
    client, service, remote = setup
    for _ in range(2):
        response = await client.post("/api/v1/auth/authorize")
        assert response.json() == {"authenticated": False, "user": None, "login_url": ORIGIN + "/auth"}
        assert response.headers["cache-control"] == "no-store"
    assert not remote.requests and not service._flows
    response = await client.get("/api/v1/companies")
    assert response.status_code == 401 and ORIGIN + "/auth" in response.json()["detail"]


async def test_pkce_real_contract_private_persistence_and_public_status(setup):
    client, service, remote = setup
    query = await login(client)
    assert query["redirect_to"][0].startswith(ORIGIN + "/auth/callback?state=")
    assert query["code_challenge_method"] == ["s256"]
    exchanged = json.loads(remote.requests[-1].content)
    verifier = exchanged["code_verifier"]
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    assert query["code_challenge"] == [challenge]
    result = await client.get("/api/v1/auth")
    assert result.json()["authenticated"] and result.json()["user"]["id"] == USER_ID
    assert result.json()["login_url"] is None
    for secret in ("access-one", "refresh-one", verifier, "one-use-code"):
        assert secret not in result.text
    assert service.store.path.stat().st_mode & 0o777 == 0o600
    assert service.store.path.parent.stat().st_mode & 0o777 == 0o700
    reloaded = AuthService(service.settings, SessionStore(service.store.path), service.client, ORIGIN)
    assert (await reloaded.status()).authenticated
    assert remote.refreshes == 0


async def test_only_enabled_supported_providers_and_choice_creates_fresh_flow(setup):
    client, service, remote = setup
    assert (await client.get("/api/v1/auth/providers")).json() == {
        "providers": [{"id": "github", "name": "GitHub"}]
    }
    first = await start_login(client)
    second = await start_login(client)
    assert first["code_challenge"] != second["code_challenge"]
    disabled = await client.post("/api/v1/auth/start", json={"provider": "google"}, headers=ACTION)
    assert disabled.status_code == 422
    remote.enabled["google"] = True
    remote.enabled["unimplemented_provider"] = True
    options = (await client.get("/api/v1/auth/providers")).json()["providers"]
    assert [option["id"] for option in options] == ["github", "google"]
    query = await login(client, "google")
    assert query["provider"] == ["google"]


async def test_callback_is_browser_bound_one_use_and_no_tokens_in_redirect(setup):
    client, service, remote = setup
    query = await start_login(client)
    callback = query["redirect_to"][0] + "&code=one-use-code"
    original = client.cookies.get(service.cookie_name)
    client.cookies.clear()
    assert "failed" in (await client.get(callback)).headers["location"]
    assert remote.exchanges == 0
    client.cookies.set(service.cookie_name, original)
    assert "success" in (await client.get(callback)).headers["location"]
    client.cookies.set(service.cookie_name, original)
    assert "failed" in (await client.get(callback)).headers["location"]
    assert remote.exchanges == 1


@pytest.mark.parametrize(
    "query,outcome",
    [
        ("error=invalid_request&error_code=bad_oauth_state", "expired"),
        ("error=access_denied", "cancelled"),
        ("error_description=untrusted-secret", "failed"),
    ],
)
async def test_oauth_errors_remove_provider_details_from_redirect(setup, query, outcome):
    client, _, remote = setup
    response = await client.get("/auth/callback?" + query)
    assert response.headers["location"] == ORIGIN + "/auth?result=" + outcome
    assert response.headers["referrer-policy"] == "no-referrer"
    assert not remote.requests


async def test_expired_attempt_does_not_exchange_code(setup):
    client, service, remote = setup
    query = await start_login(client)
    next(iter(service._flows.values())).expires_at = time.time() - 1
    response = await client.get(query["redirect_to"][0] + "&code=one-use-code")
    assert "failed" in response.headers["location"]
    assert remote.exchanges == 0


@pytest.mark.parametrize("headers", [{}, {"Origin": "https://evil.test", "X-Job-Search-Auth": "1"}])
async def test_browser_mutations_require_same_origin_and_custom_header(setup, headers):
    client, _, remote = setup
    await client.get("/auth")
    for endpoint, body in (("start", {"provider": "github"}), ("logout", {})):
        response = await client.post("/api/v1/auth/" + endpoint, json=body, headers=headers)
        assert response.status_code == 403
    assert not remote.requests


async def test_refresh_is_serialized_and_rotated_token_survives_restart(setup):
    client, service, remote = setup
    remote.expires_in = 1
    await login(client)
    results = await asyncio.gather(*(service.status() for _ in range(8)))
    assert all(result.authenticated for result in results)
    assert remote.refreshes == 1
    saved = service.store.load()
    assert saved.refresh_token.get_secret_value() == "refresh-two"
    reloaded = AuthService(service.settings, service.store, service.client, ORIGIN)
    assert await reloaded.access_token() == "access-two"


@pytest.mark.parametrize("status", [400, 401, 403])
async def test_revoked_refresh_clears_session_and_returns_login_url(setup, status):
    client, service, remote = setup
    remote.expires_in = 1
    await login(client)
    remote.refresh_status = status
    result = await client.post("/api/v1/auth/authorize")
    assert result.status_code == 200 and not result.json()["authenticated"]
    assert not service.store.path.exists()


async def test_temporary_refresh_failure_preserves_session(setup):
    client, service, remote = setup
    remote.expires_in = 1
    await login(client)
    before = service.store.path.read_bytes()
    remote.refresh_status = 503
    response = await client.get("/api/v1/auth")
    assert response.status_code == 502 and service.store.path.read_bytes() == before


async def test_logout_clears_credentials_and_pending_logins(setup):
    client, service, remote = setup
    await login(client)
    query = await start_login(client)
    response = await client.post("/api/v1/auth/logout", json={}, headers=ACTION)
    assert response.status_code == 200 and not response.json()["authenticated"]
    assert not service.store.path.exists() and not service._flows
    assert remote.requests[-1].url.params["scope"] == "local"
    assert "failed" in (await client.get(query["redirect_to"][0] + "&code=one-use-code")).headers["location"]
    assert remote.exchanges == 1
    assert (await client.get("/api/v1/companies")).status_code == 401


async def test_anonymous_auth_account_is_not_persisted(setup):
    client, service, remote = setup
    remote.anonymous = True
    query = await start_login(client)
    response = await client.get(query["redirect_to"][0] + "&code=one-use-code")
    assert "failed" in response.headers["location"]
    assert not service.store.path.exists()
    assert remote.exchanges == 1


async def test_corrupt_session_only_blocks_auth_and_can_be_cleared(setup):
    client, service, _ = setup
    service.store.path.parent.mkdir()
    service.store.path.write_text("broken-json")
    assert (await client.get("/api/v1/auth")).status_code == 503
    assert (await client.get("/api/v1/briefs")).status_code == 200
    assert (await client.post("/api/v1/auth/logout", json={}, headers=ACTION)).status_code == 200
    assert not service.store.path.exists()


async def test_companies_use_user_token_pagination_and_separate_filters(setup):
    client, _, remote = setup
    await login(client)
    result = await client.get(
        "/api/v1/companies",
        params={
            "page": 2,
            "page_size": 5,
            "name": "100%",
            "category": 'Финтех",other',
            "specialization": "Банк",
        },
    )
    assert result.status_code == 200, result.text
    assert result.json()["total"] == 772 and len(result.json()["items"]) == 1
    sent = remote.requests[-1]
    assert sent.url.params["offset"] == "5" and sent.url.params["limit"] == "5"
    assert sent.url.params["name"] == "ilike.%100\\%%"
    assert sent.url.params["categories"] == 'cs.{"Финтех\\",other"}'
    assert "access-one" not in result.text and "refresh-one" not in result.text


async def test_invalid_access_token_returns_actionable_login_and_clears_session(setup):
    client, service, remote = setup
    await login(client)
    remote.catalog_status = 401
    response = await client.get("/api/v1/companies")
    assert response.status_code == 401 and ORIGIN + "/auth" in response.json()["detail"]
    assert not service.store.path.exists()


async def test_catalog_page_beyond_last_row_is_empty_with_exact_total(setup):
    client, _, remote = setup
    await login(client)
    remote.catalog_status = 416
    response = await client.get("/api/v1/companies", params={"page": 1000})
    assert response.status_code == 200
    assert response.json() == {"items": [], "page": 1000, "page_size": 20, "total": 772}


async def test_catalog_permission_denial_keeps_account_signed_in(setup):
    client, service, remote = setup
    await login(client)
    remote.catalog_status = 403
    assert (await client.get("/api/v1/companies")).status_code == 403
    assert service.store.path.exists()
    assert (await client.get("/api/v1/auth")).json()["authenticated"]


async def test_filter_dictionary_requires_login_without_remote_request(setup):
    client, _, remote = setup
    result = await client.get("/api/v1/companies/filters")
    assert result.status_code == 401 and ORIGIN + "/auth" in result.json()["detail"]
    assert not remote.requests


async def test_filter_dictionary_sends_filters_and_pagination_to_database(setup):
    client, _, remote = setup
    await login(client)
    before = len(remote.requests)
    result = await client.get(
        "/api/v1/companies/filters",
        params={
            "field": "specialization",
            "category": "Финтех",
            "query": "100%_CRM",
            "page": 2,
            "page_size": 5,
        },
    )
    assert result.status_code == 200
    assert result.json() == {
        "field": "specialization",
        "items": [{"value": "Финтех", "link_count": 102}],
        "page": 2,
        "page_size": 5,
        "total": 44,
    }
    assert len(remote.requests) == before + 1  # No download of the company list for local aggregation.
    sent = remote.requests[-1]
    assert sent.url.path == "/rest/v1/rpc/company_filter_options"
    assert dict(sent.url.params) == {
        "p_field": "specialization",
        "p_category": "Финтех",
        "p_query": "100%_CRM",
        "order": "link_count.desc,value.asc",
        "offset": "5",
        "limit": "5",
    }
    assert "access-one" not in result.text


async def test_invalid_filter_field_never_reaches_database(setup):
    client, _, remote = setup
    response = await client.get("/api/v1/companies/filters", params={"field": "arbitrary_column"})
    assert response.status_code == 422
    assert not remote.requests


async def test_filter_dictionary_out_of_range_page_preserves_total(setup):
    client, _, remote = setup
    await login(client)
    remote.filters_status = 416
    response = await client.get("/api/v1/companies/filters", params={"page": 100})
    assert response.status_code == 200
    assert response.json() == {"field": "category", "items": [], "total": 44, "page": 100, "page_size": 50}


@pytest.mark.parametrize("status", [401, 403])
async def test_filter_dictionary_respects_remote_auth_errors(setup, status):
    client, service, remote = setup
    await login(client)
    remote.filters_status = status
    response = await client.get("/api/v1/companies/filters")
    assert response.status_code == status
    assert service.store.path.exists() == (status == 403)


async def test_page_protects_login_cookie_and_canonical_origin(setup):
    client, _, _ = setup
    response = await client.get("/auth")
    assert "HttpOnly" in response.headers["set-cookie"]
    assert "SameSite=lax" in response.headers["set-cookie"]
    assert response.headers["x-frame-options"] == "DENY"
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
    alternate = await client.get("http://localhost:8765/auth")
    assert alternate.status_code == 303 and alternate.headers["location"] == ORIGIN + "/auth"


def test_secret_or_legacy_keys_cannot_be_shipped():
    with pytest.raises(ValueError, match="publishable"):
        Settings(auth={"publishable_key": "sb_secret_not_allowed"})


async def test_web_app_is_closed_behind_the_login_screen(setup):
    client, _, remote = setup
    closed = await client.get("/")
    assert closed.status_code == 303 and closed.headers["location"] == ORIGIN + "/auth"
    assert (await client.get("/auth")).status_code == 200
    await login(client)
    app = await client.get("/")
    assert app.status_code == 200 and "/assets/app.js" in app.text
    signed_in = await client.get("/auth")
    assert signed_in.status_code == 303 and signed_in.headers["location"] == ORIGIN + "/"
    await client.post("/api/v1/auth/logout", json={}, headers=ACTION)
    assert (await client.get("/")).headers["location"] == ORIGIN + "/auth"


async def test_failed_session_check_keeps_the_app_closed(setup):
    client, service, remote = setup
    await login(client)
    service._session.expires_at = 0
    remote.refresh_status = 500
    closed = await client.get("/")
    assert closed.status_code == 303 and closed.headers["location"] == ORIGIN + "/auth"
    assert (await client.get("/auth")).status_code == 200
