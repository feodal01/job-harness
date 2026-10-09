import pytest

from src.models.filters import SearchFilters
from src.models.vacancy import Vacancy
from src.selection.filtering import evaluate


def vacancy(**values):
    return Vacancy(id="1", url="https://example.org/1", **{"title": "QA", **values})


def check(result, field):
    return next(c for c in result.checks if c.field == field)


@pytest.mark.parametrize(
    ("title", "skills", "queries", "expected"),
    [
        ("Senior QA Engineer", [], ["тестировщик"], "accepted"),
        ("Инженер по автоматизации тестирования", [], ["QA"], "accepted"),
        ("SDET", [], ["AQA"], "accepted"),
        ("Software Engineer", ["Quality Assurance"], ["QA"], "accepted"),
        ("Senior Python Developer", [], ["Python developer"], "accepted"),
        ("Python Backend Engineer", [], ["бэкенд-разработчик"], "accepted"),
        ("Account Manager", [], ["QA", "AQA"], "rejected"),
        ("Frontend developer", [], ["QA"], "rejected"),
        ("Менеджер по продажам", [], ["тестировщик"], "rejected"),
        ("Project coordinator", [], ["quantum researcher"], "unknown"),
        ("Специалист", [], ["QA"], "unknown"),
        ("QA Platform Manager", [], ["QA"], "unknown"),
        ("Aquaculture specialist", [], ["QA"], "unknown"),
        ("JavaScript developer", [], ["Java"], "unknown"),
    ],
)
def test_profile_uses_title_skills_synonyms_and_conservative_mismatches(title, skills, queries, expected):
    result = evaluate(vacancy(title=title, skills=skills), SearchFilters(), queries=queries)
    assert check(result, "profile").status == expected
    assert result.status == ("rejected" if expected == "rejected" else "accepted")


def test_profile_does_not_trust_description_or_search_engine():
    result = evaluate(
        vacancy(title="Account Manager", description="Work closely with QA"),
        SearchFilters(),
        queries=["QA"],
        complete=True,
    )
    assert result.status == "rejected"


@pytest.mark.parametrize(
    ("mode", "country", "city", "expected"),
    [
        ("office", "RU", "Томск", "accepted"),
        ("hybrid", "RU", "Москва", "rejected"),
        ("remote_country", "RU", "Москва", "accepted"),
        ("remote", "RU", "Москва", "accepted"),
        ("remote_country", "KZ", "Алматы", "rejected"),
        ("remote_worldwide", None, None, "accepted"),
        (None, "RU", "Москва", "accepted"),
    ],
)
def test_geography_weakens_with_freer_format(mode, country, city, expected):
    result = evaluate(
        vacancy(
            work_formats=[mode] if mode else [],
            locations=[{"country": country, "city": city, "format": mode}],
        ),
        SearchFilters(locations=[{"country": "RU", "city": "Томск"}]),
    )
    assert result.status == expected
    if mode is None:
        assert check(result, "locations").status == "unknown"


def test_remote_uses_publication_country_when_no_format_on_location():
    result = evaluate(
        vacancy(work_formats=["remote_country"], locations=[{"country": "RU", "city": "Москва"}]),
        SearchFilters(locations=[{"country": "RU", "city": "Томск"}]),
    )
    assert check(result, "locations").status == "accepted"


@pytest.mark.parametrize(
    ("actual", "expected"),
    [
        ("office", "maybe"),
        ("hybrid", "accepted"),
        ("remote_country", "accepted"),
        ("remote_worldwide", "accepted"),
    ],
)
def test_minimum_format_accepts_freer_formats(actual, expected):
    assert (
        evaluate(vacancy(work_formats=[actual]), SearchFilters(minimum_work_format="hybrid")).status
        == expected
    )


def test_format_and_geography_must_pass_on_same_alternative():
    v = vacancy(
        locations=[
            {"country": "RU", "city": "Томск", "format": "office"},
            {"country": "KZ", "format": "remote_country"},
        ]
    )
    result = evaluate(
        v, SearchFilters(minimum_work_format="remote_country", locations=[{"country": "RU", "city": "Томск"}])
    )
    assert result.status == "maybe"
    assert check(result, "locations").status == "accepted"
    assert check(result, "minimum_work_format").status == "rejected"


def test_unknown_geography_does_not_prove_freer_alternative():
    v = vacancy(
        locations=[{"country": "RU", "city": "Томск", "format": "office"}, {"format": "remote_country"}]
    )
    result = evaluate(
        v, SearchFilters(minimum_work_format="remote_country", locations=[{"country": "RU", "city": "Томск"}])
    )
    assert result.status == "accepted"
    assert check(result, "minimum_work_format").status == "unknown"


def test_worldwide_respects_explicit_country_exclusions_and_alternative_places():
    v = vacancy(
        locations=[
            {"worldwide": True, "format": "remote_worldwide"},
            {"country": "RU", "format": "remote_country", "excluded": True},
        ]
    )
    assert evaluate(v, SearchFilters(locations=[{"country": "RU", "city": "Томск"}])).status == "rejected"
    assert evaluate(v, SearchFilters(locations=[{"country": "RU"}, {"country": "KZ"}])).status == "accepted"


def test_worldwide_city_exclusion_does_not_exclude_whole_country():
    v = vacancy(
        locations=[
            {"worldwide": True, "format": "remote_worldwide"},
            {"country": "RU", "city": "Москва", "excluded": True},
        ]
    )
    assert evaluate(v, SearchFilters(locations=[{"country": "RU"}])).status == "accepted"
    assert evaluate(v, SearchFilters(locations=[{"country": "RU", "city": "Москва"}])).status == "rejected"


def test_unsupported_fields_are_explicit_and_never_reject():
    result = evaluate(
        vacancy(title="Account Manager", employment=["part_time"]),
        SearchFilters(employment=["full_time"], locations=[{"country": "RU"}]),
        queries=["QA"],
        unsupported=frozenset({"profile", "employment", "locations"}),
    )
    assert result.status == "accepted"
    for field in ("profile", "employment", "locations"):
        item = check(result, field)
        assert item.status == "unknown"
        assert item.supported is False
        assert "источник" in item.reason.lower()


def test_country_exclusion_is_not_masked_by_an_earlier_city_exclusion():
    v = vacancy(
        locations=[
            {"worldwide": True},
            {"country": "RU", "city": "Москва", "excluded": True},
            {"country": "RU", "excluded": True},
        ]
    )
    assert evaluate(v, SearchFilters(locations=[{"country": "RU"}])).status == "rejected"


@pytest.mark.parametrize(
    ("title", "skills", "queries", "expected"),
    [
        ("Тестировщик", [], ["QA Engineer"], "accepted"),
        ("Manual QA Engineer", [], ["LLM QA"], "unknown"),
        ("Manual QA Engineer", ["LLM"], ["LLM QA"], "accepted"),
        ("Тестировщик LLM", [], ["LLM QA"], "accepted"),
        ("QA Engineer", ["JavaScript"], ["Java QA"], "unknown"),
        ("Тестировщик", ["C++"], ["C++ QA Engineer"], "accepted"),
        ("Тестировщик", ["C"], ["C++ QA Engineer"], "unknown"),
        ("QA Engineer", [], ["LLM QA", "тестировщик"], "accepted"),
    ],
)
def test_profile_synonyms_preserve_query_qualifiers(title, skills, queries, expected):
    result = evaluate(vacancy(title=title, skills=skills), SearchFilters(), queries=queries)
    assert check(result, "profile").status == expected
    assert result.status == "accepted"
