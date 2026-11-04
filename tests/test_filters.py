from datetime import date

import pytest
from pydantic import ValidationError

from src.models.company import Company
from src.models.filters import SearchFilters
from src.models.vacancy import Vacancy
from tests.filter_support import evaluate


def vacancy(**values):
    return Vacancy(id="1", title="Senior QA", url="https://example.org/1", **values)


def check(result, field):
    return next(c for c in result.checks if c.field == field)


def test_empty_filters_keep_vacancy_with_unverified_profile():
    result = evaluate(vacancy(), SearchFilters())
    assert result.status == "accepted"
    assert check(result, "profile").status == "unknown"
    assert check(result, "profile").kind == "hard"


@pytest.mark.parametrize(
    "values",
    [
        {"remote_from": {"country": "KZ"}},
        {"work_formats": ["office"]},
        {"locations": [{"country": "ZZ"}]},
        {"salary_published": True},
        {"salary": {"minimum": -1, "currency": "RUB", "period": "month"}},
        {"salary": {"minimum": 100, "currency": "RUB"}},
        {"salary": {"minimum": 100, "currency": "RUB", "period": "month", "strict_minimum": True}},
        {"salary": {"minimum": 100, "currency": "RUB", "period": "month", "tax": "net"}},
        {"employer_country": "RU"},
        {"relocation": "required_move"},
        {"grades": ["senior"]},
    ],
)
def test_invalid_or_removed_filters_are_rejected(values):
    with pytest.raises(ValidationError):
        SearchFilters.model_validate(values)


# Relocation wording moved to the HH normalizer: tests/test_hh_enrichment.py (§11).


@pytest.mark.parametrize(
    ("salary", "status"),
    [
        ({}, "accepted"),
        ({"from": 200, "to": 350}, "accepted"),
        ({"from": 200}, "rejected"),
        ({"from": 250}, "accepted"),
        ({"to": 240}, "rejected"),
        ({"to": 250}, "accepted"),
        ({"to": 350, "currencyCode": "USD"}, "unknown"),
        ({"to": 350, "period": "year"}, "unknown"),
        ({"to": 350, "gross": True}, "accepted"),
    ],
)
def test_salary_compares_available_bound_without_currency_period_or_tax_conversion(salary, status):
    f = SearchFilters(salary={"minimum": 250, "currency": "RUB", "period": "month"})
    actual = {"currencyCode": "RUB", "period": "month", **salary}
    result = evaluate(vacancy(salary=actual), f)
    assert check(result, "salary").status == status
    assert result.status == ("maybe" if status == "rejected" else "accepted")


def test_filters_combine_and_shortlist_records_reasons():
    f = SearchFilters(excluded_companies=["Acme"], experience_years=3)
    result = evaluate(vacancy(company=Company(name="ACME"), min_experience=5), f)
    assert result.status == "rejected"
    assert check(result, "excluded_companies").kind == "hard"
    assert evaluate(vacancy(company=Company(name="Acme Labs"), min_experience=2), f).status == "accepted"


def test_excluded_text_waits_for_full_text_and_uses_word_boundaries():
    f = SearchFilters(excluded_text=["Java"])
    result = evaluate(vacancy(summary="JavaScript"), f)
    assert result.status == "accepted"
    assert check(result, "excluded_text").status == "unknown"
    assert evaluate(vacancy(description="JavaScript"), f, complete=True).status == "accepted"
    assert evaluate(vacancy(description="Java and Python"), f, complete=True).status == "rejected"


def test_dates_experience_and_employment_use_normalized_fields():
    f = SearchFilters(published_after=date(2026, 9, 1), experience_years=3, employment=["full_time"])
    v = vacancy(published_at="2026-09-01T00:00:00Z", min_experience=3, employment=["full_time"])
    assert evaluate(v, f).status == "accepted"
    v.min_experience = 5
    assert evaluate(v, f).status == "maybe"


def test_more_than_six_years_does_not_match_exactly_six():
    v = vacancy(min_experience=6, min_experience_exclusive=True)
    assert evaluate(v, SearchFilters(experience_years=6)).status == "maybe"
    assert evaluate(v, SearchFilters(experience_years=6.5)).status == "accepted"
