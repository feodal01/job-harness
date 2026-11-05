"""Salary records of HH and Getmatch (plan §13; spec docs/brief-parameters/salary-minimum-check.md)."""

import pytest

from src.enrichment.registry import default_enrichment
from src.models.vacancy import Vacancy

enrich = default_enrichment().enrich


def hh(**source_data):
    vacancy = Vacancy(id="1", title="QA", url="https://hh.ru/vacancy/1", source_data=source_data)
    return enrich(vacancy, "hh_ru").salary


def getmatch(**fields):
    vacancy = Vacancy(id="1", title="QA", url="https://getmatch.ru/1", source_data=fields)
    return enrich(vacancy, "getmatch").salary


@pytest.mark.parametrize(
    ("compensation", "expected"),
    [
        (
            {"from": 50000, "to": 70000, "currencyCode": "RUR", "gross": False, "mode": "MONTH"},
            {
                "state": "specified",
                "from_": 50000,
                "to": 70000,
                "currency": "RUB",
                "unit": "month",
                "gross": False,
            },
        ),
        ({"from": 500, "currencyCode": "RUR", "mode": "HOUR"}, {"unit": "hour", "to": None}),
        # HH's own units (dictionary salary_range_mode) are kept as they are, not converted.
        ({"from": 5000, "currencyCode": "RUR", "mode": "SHIFT"}, {"unit": "shift", "converted": {}}),
        ({"from": 90000, "currencyCode": "RUR", "mode": "FLY_IN_FLY_OUT"}, {"unit": "fly_in_fly_out"}),
        ({"to": 3000, "currencyCode": "USD", "mode": "SERVICE"}, {"unit": "service", "currency": "USD"}),
        ({"from": 1000, "currencyCode": "KZT", "mode": "NEW_UNIT"}, {"unit": None}),
        # «Не указана», not hidden: the old parser marked this as salary_hidden.
        ({"noCompensation": True, "period": None}, {"state": "not_specified", "from_": None}),
    ],
)
def test_hh_compensation_becomes_a_record(compensation, expected):
    [record] = hh(compensation=compensation)
    assert {k: getattr(record, k) for k in expected} == expected
    assert record.original == compensation
    assert record.evidence is not None
    assert (record.evidence.enricher, record.evidence.method, record.evidence.field) == (
        "hh_ru",
        "structured",
        "compensation",
    )


def test_hh_amount_per_unit_is_per_mode_not_from_to():
    # Live listing 139040588 (04.11.2026): 400–600 per hour; HH's from/to hold its own recomputation.
    compensation = {
        "from": 6000,
        "to": 144000,
        "currencyCode": "RUR",
        "gross": False,
        "perModeFrom": 400,
        "perModeTo": 600,
        "mode": "HOUR",
        "frequency": "DAILY",
    }
    [record] = hh(compensation=compensation)
    assert (record.from_, record.to, record.unit) == (400, 600, "hour")
    assert record.converted["month"] == {"from": 69333.33, "to": 104000.0}
    assert record.original == compensation


def test_hh_month_record_is_converted_to_every_standard_period():
    [record] = hh(compensation={"from": 173333.33, "currencyCode": "RUR", "mode": "MONTH"})
    assert record.converted["hour"]["from"] == 1000.0
    assert set(record.converted) == {"hour", "day", "week", "month", "year"}


def test_hh_listing_and_card_are_two_records_and_the_card_does_not_overwrite():
    listing = {"from": 200000, "currencyCode": "RUR", "mode": "MONTH"}
    card = {"from": 200000, "to": 300000, "currencyCode": "RUR", "mode": "MONTH"}
    records = hh(compensation=card, listing={"compensation": listing})
    assert [(r.from_, r.to, r.evidence.field) for r in records] == [
        (200000, 300000, "compensation"),
        (200000, None, "listing.compensation"),
    ]


def test_hh_card_parse_keeps_the_listing_salary():
    import html
    import json

    from src.parsers.aggregators.hh.parser import HHVacancyDetails

    listed = Vacancy(
        id="1",
        title="QA",
        url="https://hh.ru/vacancy/1",
        source_data={"compensation": {"from": 100, "currencyCode": "RUR", "mode": "MONTH"}},
    )
    view = {"vacancyId": 1, "description": "<p>QA</p>", "compensation": {"from": 150, "currencyCode": "RUR"}}
    body = (
        '<template id="HH-Lux-InitialState">' + html.escape(json.dumps({"vacancyView": view})) + "</template>"
    )
    card = HHVacancyDetails().parse(body, listed)
    assert card.source_data["compensation"] == {"from": 150, "currencyCode": "RUR"}
    assert card.source_data["listing"] == {
        "compensation": {"from": 100, "currencyCode": "RUR", "mode": "MONTH"}
    }


def test_hh_without_compensation_has_no_record():
    assert hh() == []


@pytest.mark.parametrize(
    ("fields", "expected"),
    [
        (
            {
                "salary_display_from": 250000,
                "salary_display_to": 350000,
                "salary_currency": "RUB",
                "salary_taxes": "net",
                "salary_description": "250 000 — 350 000 ₽/мес на руки",
            },
            {
                "state": "specified",
                "from_": 250000,
                "to": 350000,
                "currency": "RUB",
                "unit": "month",
                "gross": False,
                "total": False,
            },
        ),
        (
            {"salary_display_from": 5000, "salary_currency": "USD", "salary_taxes": "gross"},
            {"currency": "USD", "unit": "month", "gross": True},
        ),
        # «Совокупно»: includes bonuses — compared as «до X» with the label.
        (
            {
                "salary_display_from": 200000,
                "salary_display_to": 300000,
                "salary_currency": "RUB",
                "salary_is_total": True,
            },
            {"total": True, "bonus": False},
        ),
        # Hidden by Getmatch: its own label is kept as the original.
        (
            {
                "salary_hidden": True,
                "salary_is_total": True,
                "salary_description": "от 500 000 ₽/мес до налогов, совокупно",
            },
            {"state": "hidden", "from_": None, "original": "от 500 000 ₽/мес до налогов, совокупно"},
        ),
        ({"salary_hidden": False}, {"state": "not_specified"}),
    ],
)
def test_getmatch_salary_becomes_a_record(fields, expected):
    [record] = getmatch(**fields)
    assert {k: getattr(record, k) for k in expected} == expected
    assert record.evidence is not None
    assert (record.evidence.enricher, record.evidence.method) == ("getmatch", "structured")


def test_getmatch_without_salary_fields_keeps_a_stored_record():
    # Stored before the salary stage: the former dict is read as a record and not lost.
    stored = Vacancy(
        id="1",
        title="QA",
        url="https://getmatch.ru/1",
        salary={"from": 200, "to": 300, "currencyCode": "RUB", "period": "month", "gross": None},
    )
    [record] = enrich(stored, "getmatch").salary
    assert (record.from_, record.to, record.unit, record.evidence.enricher) == (200, 300, "month", "legacy")


def test_stored_hh_hidden_flag_from_no_compensation_is_not_hidden():
    stored = Vacancy.model_validate(
        {
            "id": "1",
            "title": "QA",
            "url": "https://hh.ru/vacancy/1",
            "salary": {"noCompensation": True, "period": None},
            "salary_hidden": True,
        }
    )
    assert [r.state for r in stored.salary] == ["not_specified"]


def test_salary_is_reenriched_identically():
    vacancy = Vacancy(
        id="1",
        title="QA",
        url="https://hh.ru/vacancy/1",
        source_data={"compensation": {"from": 1, "currencyCode": "RUR", "mode": "MONTH"}},
    )
    once = enrich(vacancy, "hh_ru")
    assert enrich(once, "hh_ru") == once
    assert Vacancy.model_validate(once.model_dump(mode="json")) == once


def test_a_stored_hidden_salary_survives_loading_from_the_corpus():
    # Review 05.11.2026: loaders keep only model fields; the stored `salary_hidden` must not be lost.
    from src.db.vacancies import VacancyRepository

    row = {
        "id": "1",
        "title": "QA",
        "url": "https://getmatch.ru/1",
        "salary": {"from": None, "to": None, "currencyCode": None, "period": None, "gross": None},
        "salary_hidden": True,
        "source_data": {"salary_description": "от 500 000 ₽/мес"},
        "source": "getmatch",
        "selection": {"status": "accepted"},
    }
    stored = Vacancy.from_stored(row)
    assert [r.state for r in stored.salary] == ["hidden"]
    assert [r.state for r in enrich(stored, "getmatch").salary] == ["hidden"]
    completed = VacancyRepository._complete_vacancy(row)
    assert [r["state"] for r in completed["salary"]] == ["hidden"]
