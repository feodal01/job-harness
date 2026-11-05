from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from src.enrichment.registry import default_enrichment
from src.models.company import Company
from src.models.vacancy import Vacancy
from src.parsers.aggregators.hh import parser as hh

FIXTURES = Path(__file__).parent / "fixtures"
enrich = default_enrichment().enrich


def listing(body):
    search = hh.HHSearch()
    return search.parse(body, search.build("QA"))


def body(name):
    return (FIXTURES / f"{name}.html").read_text()


def test_listing_real_response_preserves_employer_and_pagination():
    page = listing(body("success"))
    assert page.total == 615
    assert parse_qs(urlsplit(page.next_requests[0].url).query)["page"] == ["1"]
    first = page.vacancies[0]
    assert first.id == "134371846"
    assert first.company.id == "5174681"
    assert first.company.name == "MANGO FZCO"
    assert first.company.site_url == "https://windi.com"
    assert first.company.profile_url == "https://hh.ru/employer/5174681"
    assert first.source_data["compensation"]["to"] == 250000  # HH's value; the record is a fact.
    assert enrich(first, "hh_ru").salary[0].to == 250000
    assert first.location == "Москва"


def test_pagination_and_real_empty_response():
    assert parse_qs(urlsplit(listing(body("pagination")).next_requests[0].url).query)["page"] == ["2"]
    empty = listing(body("no_results"))
    assert empty.total == 0
    assert empty.vacancies == []


def test_detail_retains_full_description_and_source_fields():
    vacancy = listing(body("success")).vacancies[0]
    detail = hh.HHVacancyDetails().parse(body("detail_structured"), vacancy)
    assert detail.description
    assert "Pytest" in detail.skills
    # The parser keeps HH codes; the HH normalizer translates them (§12).
    assert enrich(detail, "hh_ru").work_formats == ["remote_country"]
    assert detail.experience == "between1And3"


def test_company_profile_finds_official_link():
    company = hh.HHCompanyProfile().parse(body("employer_profile_official_site"), Company(id="9498112"))
    assert company.site_url == "https://crowd.yandex.ru/vacancies"
    assert company.location == "Москва"


def test_real_block_is_not_empty_search():
    with pytest.raises(hh.ParseError) as error:
        listing(body("blocked"))
    assert error.value.kind == "blocked"


def test_query_is_user_supplied_and_area_is_explicit():
    params = parse_qs(urlsplit(hh.HHSearch().build("бухгалтер").url).query)
    assert params["text"] == ["бухгалтер"]
    assert params["page"] == ["0"]
    assert "area" not in params
    assert "search_field" not in params


def test_current_live_detail_extracts_nested_vacancy_full():
    vacancy = Vacancy(id="137433305", title="QA Engineer", url="https://hh.ru/vacancy/137433305")
    detail = hh.HHVacancyDetails().parse(body("live-detail-137433305"), vacancy)
    assert detail.skills[:3] == ["API", "Postman", "SOAP"]
    assert detail.company.id == "6591"
    assert detail.company.name == "Банк ПСБ, Бизнес и инфраструктура"
    assert detail.company.site_url == "http://job.psbank.ru"
    assert detail.location == "Москва"
    assert detail.source_data["compensation"] == {"noCompensation": True}
    assert [r.state for r in enrich(detail, "hh_ru").salary] == ["not_specified"]
    assert detail.experience == "between3And6"
    assert enrich(detail, "hh_ru").work_formats == ["office", "hybrid"]


def test_detail_from_another_vacancy_is_rejected():
    vacancy = Vacancy(id="wrong", title="QA", url="https://hh.ru/vacancy/wrong")
    with pytest.raises(hh.ParseError, match="identity"):
        hh.HHVacancyDetails().parse(body("live-detail-137433305"), vacancy)


def test_hh_normalizes_formats_and_minimum_experience():
    import html
    import json

    from src.models.vacancy import Vacancy
    from src.parsers.aggregators.hh.parser import HHVacancyDetails

    view = {
        "vacancyId": 1,
        "workFormats": ["ON_SITE", "REMOTE"],
        "workExperience": "between3And6",
        "employmentForm": "FULL",
        "description": "<p>Details</p>",
    }
    body = (
        '<template id="HH-Lux-InitialState">' + html.escape(json.dumps({"vacancyView": view})) + "</template>"
    )
    v = HHVacancyDetails().parse(body, Vacancy(id="1", title="QA", url="https://hh.ru/vacancy/1"))
    assert enrich(v, "hh_ru").work_formats == ["office", "remote_country"]
    assert v.employment == ["full_time"]
    assert v.min_experience == 3


@pytest.mark.parametrize(
    ("description", "expected"),
    [
        ("Удалённая работа из любой точки мира, кроме России и Германии.", "remote_worldwide"),
        ("Work remotely worldwide except Germany.", "remote_worldwide"),
        ("Our clients are worldwide. Remote work within Russia.", "remote_country"),
        ("Нельзя работать из любой точки мира, только Россия.", "remote_country"),
    ],
)
def test_hh_worldwide_requires_explicit_work_permission(description, expected):
    from src.enrichment.registry import default_enrichment

    enrich = default_enrichment().enrich

    original = Vacancy(
        id="1",
        title="QA",
        url="https://hh.ru/vacancy/1",
        description=description,
        work_formats=["remote_country"],
        source_data={"area": {"countryIsoCode": "RU", "name": "Москва"}},
    )
    result = enrich(original, "hh_ru")
    assert result.work_formats == [expected]
    assert result.locations[0].format == expected
    assert original.work_formats == ["remote_country"]
    if expected == "remote_worldwide":
        assert any(p.country == "DE" and p.excluded for p in result.locations)
    assert enrich(result, "hh_ru") == result
