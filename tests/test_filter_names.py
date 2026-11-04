"""One source of truth for condition names and work formats."""

from typing import get_args

from src.models.filters import (
    FILTER_NAMES,
    OFFICE_FORMATS,
    REMOTE_FORMATS,
    WORK_FORMATS,
    SearchFilters,
    WorkFormat,
    accepted_formats,
)
from src.selection.checks import FILTER_FIELDS
from src.selection.checks.places.format import FORMAT_RANK


def test_condition_names_match_brief_filters_and_local_checks():
    # A new brief condition must be named and described for local checks, or this fails.
    assert set(FILTER_NAMES) == set(SearchFilters.model_fields)
    assert set(FILTER_FIELDS) == set(FILTER_NAMES)


def test_work_formats_are_derived_from_the_model():
    assert WORK_FORMATS == get_args(WorkFormat)
    assert OFFICE_FORMATS | REMOTE_FORMATS == set(WORK_FORMATS)
    assert not OFFICE_FORMATS & REMOTE_FORMATS
    assert [FORMAT_RANK[f] for f in WORK_FORMATS] == list(range(len(WORK_FORMATS)))
    assert accepted_formats("hybrid") == ("hybrid", "remote_country", "remote_worldwide")
    assert accepted_formats(None) == WORK_FORMATS


def test_local_checks_read_existing_vacancy_fields_and_facts_come_from_the_model():
    from src.enrichment.registry import VACANCY_FACTS
    from src.models.vacancy import Vacancy

    # "locations.relocation_support": a fact inside a vacancy field (the places).
    referenced = {name.split(".")[0] for name in set().union(*FILTER_FIELDS.values())}
    assert referenced <= set(Vacancy.model_fields), referenced - set(Vacancy.model_fields)
    # Facts are marked in the model; the registry derives them instead of keeping a copy.
    assert VACANCY_FACTS and VACANCY_FACTS <= set(Vacancy.model_fields)


def test_relocation_and_formats_are_place_facts():
    # §11: relocation is read from places, never from the description.
    assert FILTER_FIELDS["relocation_support"] == frozenset({"locations.relocation_support"})
    assert FILTER_FIELDS["locations"] == frozenset({"locations"})


def test_relocation_is_local_only_where_places_carry_the_fact():
    from src.parsers.interfaces import filter_parameters

    # An ATS board with places but no relocation fact must not claim the check.
    assert filter_parameters(frozenset({"locations", "description"}))["relocation_support"] == "none"
    assert filter_parameters(frozenset({"locations.relocation_support"}))["relocation_support"] == "local"
