from copy import deepcopy
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from src.api.errors import register_error_handlers
from src.api.v1.router import router
from src.core.errors import ConflictError, InvalidSearchError, NotFoundError
from src.dependencies import get_runs
from src.schemas.briefs import BriefResponse
from src.schemas.runs import RunResponse
from src.schemas.vacancies import RunVacancy


@pytest.fixture
def api() -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    register_error_handlers(app)
    return app


def test_every_api_operation_declares_a_structured_response(api: FastAPI) -> None:
    document = api.openapi()
    operations = [operation for path in document["paths"].values() for operation in path.values()]
    assert len(operations) == 29
    for operation in operations:
        assert operation.get("description")
        success = next(value for code, value in operation["responses"].items() if code.startswith("2"))
        schema = success["content"]["application/json"]["schema"]
        if schema.get("type") == "array":
            assert "$ref" in schema["items"]
        else:
            assert "$ref" in schema
        assert "500" in operation["responses"]
    components = document["components"]["schemas"]
    assert components["PoolVacancyPage"]["properties"]["items"]["items"]["$ref"].endswith("/PoolVacancy")
    assert components["RunVacancyPage"]["properties"]["items"]["items"]["$ref"].endswith("/RunVacancy")
    assert components["VacancyNavigation"]["properties"]["page"]["anyOf"][0]["$ref"].endswith(
        "/PoolVacancyPage"
    )
    assert components["RunResponse"]["properties"]["metrics"]["$ref"].endswith("/RunMetrics")
    assert "исторические" in components["RunResponse"]["properties"]["request"]["description"]


def test_domain_errors_and_framework_validation_are_documented(api: FastAPI) -> None:
    paths = api.openapi()["paths"]
    responses = paths["/api/v1/runs"]["post"]["responses"]
    assert {"202", "404", "409", "422", "500"} <= responses.keys()
    validation = responses["422"]["content"]["application/json"]["schema"]
    assert {option["$ref"].split("/")[-1] for option in validation["anyOf"]} == {
        "ErrorResponse",
        "ValidationErrorResponse",
    }
    for code in ("404", "409", "500"):
        assert responses[code]["content"]["application/json"]["schema"]["$ref"].endswith("/ErrorResponse")
    assert "404" in paths["/api/v1/briefs/{brief_id}/vacancies/{vacancy_key}"]["get"]["responses"]


@pytest.fixture
def historical_run() -> dict:
    return {
        "id": "old-run",
        "brief_id": "brief",
        "status": "complete",
        "created_at": 1.0,
        "finished_at": 2.0,
        "error": None,
        "limit_reached": False,
        "request": {"brief": {"filters": {"legacy_grade": ["senior"]}}, "removed_field": {"v": [1, None]}},
        "metrics": {"unknown": 2, "hh_total": 50, "filtered": 3},
        "sources": {"hh_ru": {"status": "complete", "error": None, "metrics": {"hh_total": 50}}},
    }


def test_historical_request_and_absent_metric_fields_are_preserved(historical_run: dict) -> None:
    original = deepcopy(historical_run)
    response = RunResponse.model_validate(historical_run)
    assert response.model_dump(mode="json", exclude_unset=True) == original
    assert historical_run == original


def test_historical_vacancy_selection_and_location_are_not_reinterpreted() -> None:
    original = {
        "id": "1",
        "title": "Engineer",
        "url": "https://example.test/1",
        "source": "hh_ru",
        "locations": [{"country": "RU", "format": "remote"}],
        "selection": {
            "status": "unknown",
            "checks": [{"field": "salary", "status": "unknown", "reason": ""}],
        },
        "detail_status": "ready",
        "error": None,
        "is_new": False,
        "salary": {"amount": 2000, "currency": "EUR"},
        "source_data": {"native": [1, {"flag": True}]},
        "origins": [
            {"run_id": "old-run", "query": "python", "request_url": "https://example.test?q=p", "page": 1}
        ],
    }
    assert RunVacancy.model_validate(original).model_dump(mode="json", exclude_unset=True) == original


def test_brief_migration_notices_stay_visible_without_injecting_optional_fields() -> None:
    original = {
        "id": "brief",
        "name": "Brief",
        "text": "",
        "queries": ["python"],
        "filters": {},
        "updated_at": 1.0,
        "migration_note": "Check location",
        "filters_migration_notice": "Review filters",
    }
    assert BriefResponse.model_validate(original).model_dump(mode="json", exclude_unset=True) == original


async def test_http_serialization_preserves_historical_snapshot(api: FastAPI, historical_run: dict) -> None:
    service = SimpleNamespace(get=lambda run_id: historical_run)
    api.dependency_overrides[get_runs] = lambda: service
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=api), base_url="http://testserver"
    ) as client:
        response = await client.get("/api/v1/runs/old-run")
    assert response.status_code == 200
    assert response.json() == historical_run


async def test_framework_422_retains_validation_list(api: FastAPI) -> None:
    api.dependency_overrides[get_runs] = lambda: object()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=api), base_url="http://testserver"
    ) as client:
        response = await client.post("/api/v1/runs", json={})
    assert response.status_code == 422
    assert isinstance(response.json()["detail"], list)
    assert response.json()["detail"][0]["loc"] == ["body", "brief_id"]


@pytest.mark.parametrize(
    ("failure", "status"),
    [
        (NotFoundError("Missing brief"), 404),
        (ConflictError("Busy"), 409),
        (InvalidSearchError("Unsupported search"), 422),
        (KeyError("internal-key"), 500),
        (ValueError("internal-value"), 500),
        (RuntimeError("internal-state"), 500),
    ],
)
async def test_only_explicit_domain_errors_become_client_errors(
    api: FastAPI, failure: Exception, status: int
) -> None:
    def start(request: object) -> None:
        raise failure

    api.dependency_overrides[get_runs] = lambda: SimpleNamespace(start=start)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=api, raise_app_exceptions=False), base_url="http://testserver"
    ) as client:
        response = await client.post("/api/v1/runs", json={"brief_id": "brief"})
    assert response.status_code == status
    assert response.json() == {"detail": "Internal server error" if status == 500 else str(failure)}
