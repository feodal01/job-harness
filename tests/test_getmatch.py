import json
from dataclasses import replace
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from src.parsers.aggregators.getmatch.parser import GetmatchSearch

FIXTURES = Path(__file__).parent / "fixtures/getmatch"


def response(name):
    return (FIXTURES / f"{name}.json").read_text()


def offers_request(query="QA Auto"):
    search = GetmatchSearch(page_size=10)
    return search, (search.build(query),)


@pytest.mark.parametrize("query", ["", "QA Python", "unknown profession"])
def test_feed_starts_without_specialization_or_text_mapping(query):
    _, (request,) = offers_request(query)
    assert urlsplit(request.url).path == "/api/offers"
    assert parse_qs(urlsplit(request.url).query) == {
        "sa": ["any"],
        "p": ["1"],
        "offset": ["0"],
        "limit": ["10"],
        "pa": ["all"],
    }
    assert request.stage == "listing"


def test_promoted_offers_do_not_skip_regular_feed_entries():
    search, (request,) = offers_request()
    first = search.parse(response("feed_page1"), request)
    assert len(first.vacancies) == 12
    assert first.total == 921
    following = first.next_requests[0]
    assert parse_qs(urlsplit(following.url).query)["offset"] == ["10"]
    assert "sp" not in parse_qs(urlsplit(following.url).query)
    second = search.parse(response("feed_page2"), following)
    assert second.vacancies[0].id == "35540"
    assert parse_qs(urlsplit(second.next_requests[0].url).query)["offset"] == ["20"]


def test_feed_stops_after_last_page():
    search, (request,) = offers_request()
    request = replace(request, url=request.url.replace("offset=0", "offset=920"))
    page = search.parse(response("feed_last"), request)
    assert len(page.vacancies) == 1
    assert page.next_requests == ()


def test_empty_feed_finishes_but_premature_empty_page_is_an_error():
    search, (request,) = offers_request()
    body = {"offers": [], "meta": {"offset": 0, "limit": 10, "total": 0}}
    page = search.parse(json.dumps(body), request)
    assert page.vacancies == [] and page.next_requests == () and page.total == 0
    body["meta"]["total"] = 20
    with pytest.raises(ValueError, match="empty page"):
        search.parse(json.dumps(body), request)


def test_captured_offer_retains_text_company_salary_tax_and_pagination():
    search, requests = offers_request()
    page = search.parse(response("qa_auto"), requests[0])
    first = page.vacancies[0]
    assert first.id == "34499"
    assert first.title == "Старший разработчик инструментов тестирования (Java)"
    assert first.company.name == "БЮРО 1440"
    assert first.company.profile_url == "https://getmatch.ru/companies/43QY9OQV-biuro-1440"
    assert first.company.site_url is None
    # Getmatch's values stay in source_data; the record is a fact of the Getmatch normalizer.
    from src.enrichment.registry import default_enrichment

    [record] = default_enrichment().enrich(first, "getmatch").salary
    assert (record.from_, record.to, record.currency, record.gross, record.unit) == (
        300000,
        350000,
        "RUB",
        True,
        "month",
    )
    assert first.work_formats == ["remote_country"] and first.location == "Россия"
    assert first.skills[0] == "Java"
    assert first.description is None
    assert "Разрабатывать инструменты тестирования" in first.summary
    assert "<b>" not in first.summary
    assert page.total == 44 and len(page.vacancies) == 10
    assert parse_qs(urlsplit(page.next_requests[0].url).query)["offset"] == ["10"]


def test_previous_page_returned_again_is_a_parse_failure():
    search, requests = offers_request()
    page = search.parse(response("qa_auto"), requests[0])
    with pytest.raises(ValueError, match="offset/limit"):
        search.parse(response("qa_auto"), page.next_requests[0])


def test_getmatch_full_text_comes_from_detail_and_retains_feed_summary():
    from src.core.config import Settings
    from src.parsers.registry import build_sources

    source = build_sources(Settings())["getmatch"]
    search, requests = offers_request()
    listing = search.parse(response("qa_auto"), requests[0]).vacancies[0]
    assert listing.description is None
    result = source.vacancy_details.parse((FIXTURES / "detail-34499.html").read_text(), listing)
    assert "Знание фундаментальной теории тестирования." in result.description
    assert "ДМС со стоматологией" in result.description
    assert "Пользовательское соглашение" not in result.description
    assert result.summary == listing.summary
    assert {k: v for k, v in result.source_data.items() if k.startswith("salary_")} == {
        k: v for k, v in listing.source_data.items() if k.startswith("salary_")
    }
    wrong = listing.model_copy(update={"id": "wrong"})
    with pytest.raises(ValueError, match="identity"):
        source.vacancy_details.parse((FIXTURES / "detail-34499.html").read_text(), wrong)


@pytest.mark.parametrize(
    ("vacancy_id", "expected_text"),
    [
        ("36107", "Что такое Alfa Risk Day"),
        ("36316", "Создание собственного серверного RPM-base-дистрибутива."),
    ],
)
def test_promoted_feed_entry_keeps_its_type_and_full_description(vacancy_id, expected_text):
    from src.parsers.aggregators.getmatch.parser import GetmatchVacancyDetails

    search, (request,) = offers_request()
    listing = next(v for v in search.parse(response("feed_page1"), request).vacancies if v.id == vacancy_id)
    body = (FIXTURES / f"detail-{vacancy_id}.html").read_text()
    result = GetmatchVacancyDetails().parse(body, listing)
    assert expected_text in result.description
    assert "Пользовательское соглашение" not in result.description
    assert result.source_data["offer_type"] == "one_day_offer_v3"
    assert result.summary == listing.summary
    wrong = listing.model_copy(update={"id": "wrong"})
    with pytest.raises(ValueError, match="identity"):
        GetmatchVacancyDetails().parse(body, wrong)


def test_regular_vacancy_does_not_accept_promotional_template():
    from src.parsers.aggregators.getmatch.parser import GetmatchVacancyDetails

    search, (request,) = offers_request()
    listing = search.parse(response("feed_page1"), request).vacancies[0]
    listing = listing.model_copy(update={"source_data": {"offer_type": "vacancy"}})
    with pytest.raises(ValueError, match="description is missing"):
        GetmatchVacancyDetails().parse((FIXTURES / "detail-36107.html").read_text(), listing)


def test_getmatch_normalizes_geography_and_salary_without_using_employer_address():
    from src.enrichment.registry import default_enrichment

    search, (request,) = offers_request()
    # The engine enriches parsed vacancies (parsers stay pure).
    v = default_enrichment().enrich(search.parse(response("feed_page1"), request).vacancies[2], "getmatch")
    assert [r.unit for r in v.salary] == ["month"]
    assert [(p.country, p.city, p.format) for p in v.locations] == [
        ("RU", "Москва", "hybrid"),
        ("RU", None, "remote_country"),
    ]
    assert not any(p.excluded for p in v.locations)


def test_worldwide_and_excluded_countries_survive_feed_and_stored_normalization():
    from src.enrichment.registry import default_enrichment

    enrich = default_enrichment().enrich

    search, (request,) = offers_request()
    payload = json.loads(response("qa_auto"))
    payload["offers"][0]["location_requirements"] = [
        {"format": "remote", "country": "", "location_id": "_cu-world"},
        {"format": "remote", "country": "Германия", "exclude": True},
    ]
    vacancy = enrich(search.parse(json.dumps(payload), request).vacancies[0], "getmatch")
    assert vacancy.work_formats == ["remote_worldwide"]
    assert vacancy.locations[0].worldwide
    assert vacancy.locations[1].country == "DE" and vacancy.locations[1].excluded
    assert enrich(vacancy, "getmatch") == vacancy
