"""Sources without place facts (HH, ATS) keep their decisions after the stage 4 rewrite (§11).

The frozen pre-stage-4 filter (tests/legacy_filtering.py) and the new one must agree on every
check for places without `scope`. Relocation moved to the HH normalizer, so HH vacancies are
compared after enrichment: the old filter parsed the description, the new one reads the place.
Deleted with the transitional branch (stage N+1).
"""

import itertools
from pathlib import Path

import pytest

from src.enrichment.registry import default_enrichment
from src.models.filters import SearchFilters
from src.models.vacancy import Vacancy
from src.parsers.aggregators.hh import parser as hh
from src.parsers.ats.normalization import locations as ats_locations
from tests import legacy_filtering
from tests.filter_support import evaluate

FIXTURES = Path(__file__).parent / "fixtures"
TOMSK = {"country": "RU", "city": "Tomsk", "geoname_id": 1489425}
MOSCOW = {"country": "RU", "city": "Moscow", "geoname_id": 524901}
TROITSK = {"country": "RU", "city": "Troitsk", "geoname_id": 481608}

PLACES = [
    {"country": "RU", "city": "Москва"},
    {"country": "RU", "city": "Томск", "format": "office"},
    {"country": "RU", "city": "Троицк (Москва)", "format": "hybrid"},
    {"country": "RU", "city": "Троицк"},
    {"country": "KZ", "city": "Алматы", "format": "remote_country"},
    {"country": "RU", "format": "remote_country"},
    {"worldwide": True, "format": "remote_worldwide"},
    {"country": "RU", "excluded": True, "format": "remote_worldwide"},
    {"country": "RU", "city": "Москва", "excluded": True},
    {"format": "remote_country"},
    {},
]
FORMATS = [[], ["office"], ["hybrid", "remote_country"], ["remote_worldwide"]]
BRIEFS = [
    SearchFilters(),
    SearchFilters(locations=[TOMSK]),
    SearchFilters(locations=[{**MOSCOW, "minimum_work_format": "hybrid"}]),
    SearchFilters(locations=[{"country": "RU", "scope": "country", "minimum_work_format": "office"}]),
    SearchFilters(locations=[{"country": "KZ", "scope": "country"}, TROITSK]),
    SearchFilters(locations=[{"country": "RU"}], minimum_work_format="remote_country"),
    SearchFilters(locations=[{**MOSCOW, "relocation_support": True}]),
]


def decisions(result):
    return result.status, [(c.field, c.status, c.kind, c.supported) for c in result.checks]


def both(vacancy, filters, **options):
    old = legacy_filtering.evaluate(vacancy, filters, **options)
    return decisions(old), decisions(evaluate(vacancy, filters, **options))


@pytest.mark.parametrize("filters", BRIEFS)
def test_places_without_scope_decide_as_before(filters):
    for size in (1, 2):
        for places, formats in itertools.product(itertools.combinations(PLACES, size), FORMATS):
            vacancy = Vacancy(
                id="1",
                title="QA",
                url="https://hh.ru/vacancy/1",
                locations=list(places),
                work_formats=formats,
            )
            old, new = both(vacancy, filters)
            assert new == old, (places, formats)


def test_ats_places_decide_as_before():
    for raw in [
        {"country": "Germany", "city": "Berlin", "format": "hybrid"},
        {"country": "RU", "format": "remote"},
        {"city": "Remote", "worldwide": True},
        {"country": "Kazakhstan", "city": "Almaty", "format": "on-site"},
    ]:
        vacancy = Vacancy(id="1", title="QA", url="https://example.org/1", locations=ats_locations(**raw))
        for filters in BRIEFS:
            old, new = both(vacancy, filters)
            assert new == old, raw


def hh_vacancies():
    search = hh.HHSearch()
    listing = search.parse((FIXTURES / "success.html").read_text(), search.build("QA")).vacancies
    cards = [hh.HHVacancyDetails().parse((FIXTURES / "detail_structured.html").read_text(), listing[0])] + [
        hh.HHVacancyDetails().parse(
            (FIXTURES / f"live-detail-{i}.html").read_text(),
            Vacancy(id=i, title="QA", url=f"https://hh.ru/vacancy/{i}"),
        )
        for i in ("137433305", "137520939")
    ]
    without_formats = [
        Vacancy(
            id="f",
            title="QA",
            url="https://hh.ru/vacancy/1",
            description=text,
            source_data={"area": {"countryIsoCode": "RU", "name": "Москва"}},
            # As the HH parser builds it: a place from `area` when HH gives its country.
            locations=[{"country": "RU", "city": "Москва"}],
        )
        for text in (
            "Помощь с релокацией",
            "Удалённо из любой точки мира, кроме России. Помощь с релокацией.",
        )
    ]
    texts = [
        Vacancy(
            id=str(i),
            title="QA",
            url="https://hh.ru/vacancy/1",
            description=text,
            work_formats=["office"],
            source_data={"area": {"countryIsoCode": "RU", "name": "Москва"}},
        )
        for i, text in enumerate(
            ["Помощь с релокацией", "Без помощи с переездом", "Relocation assistance may be available.", "QA"]
        )
    ]
    return [default_enrichment().enrich(v, "hh_ru") for v in [*listing, *cards, *texts, *without_formats]]


@pytest.mark.parametrize("filters", BRIEFS + [SearchFilters(relocation_support=True)])
def test_hh_fixtures_decide_as_before_including_relocation(filters):
    for vacancy in hh_vacancies():
        complete = bool(vacancy.description)
        old, new = both(vacancy, filters, complete=complete, queries=["QA"])
        assert new == old, vacancy.id


def test_ats_relocation_is_unknown_until_the_platform_is_translated():
    """The one planned difference for ATS: the description is no longer parsed by the filter."""
    vacancy = Vacancy(
        id="1",
        title="QA",
        url="https://example.org/1",
        description="We provide relocation assistance.",
        locations=ats_locations(country="Germany", city="Berlin", format="hybrid"),
    )
    filters = SearchFilters(locations=[{"country": "DE", "scope": "country", "relocation_support": True}])
    old, new = both(vacancy, filters, complete=True)
    assert old[1] == [(f, "accepted" if f == "relocation_support" else s, k, sup) for f, s, k, sup in new[1]]
    assert dict((f, s) for f, s, *_ in new[1])["relocation_support"] == "unknown"
