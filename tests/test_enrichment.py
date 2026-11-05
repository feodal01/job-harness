"""Stage 2 of docs/vacancy-facts-plan.md: enrichment chain skeleton without behavior change."""

from pathlib import Path

import pytest

from src.enrichment import registry
from src.enrichment.chain import run
from src.enrichment.contract import FactUpdate
from src.geography.catalog import default_catalog
from src.models.vacancy import Vacancy

FIXTURES = Path(__file__).parent / "fixtures"


def stub(enricher_id, method, updates, facts=None):
    class Stub:
        id, version = enricher_id, "1"

        def __init__(self):
            self.method = method
            self.facts = frozenset(facts or {u.name for u in updates})

        def enrich(self, vacancy):
            return updates

    return Stub()


def vacancy(**fields):
    return Vacancy(id="1", title="QA", url="https://example.org/1", **fields)


def test_weaker_method_only_fills_empty_facts_and_conflicts_stay_visible():
    structured = stub("source", "structured", [FactUpdate("employment", ["full_time"], field="type")])
    text = stub(
        "text.employment",
        "text",
        [FactUpdate("employment", ["part_time"]), FactUpdate("min_experience", 3)],
    )
    result = run(vacancy(), (structured, text))
    assert result.employment == ["full_time"]
    assert result.min_experience == 3  # Empty fact: the text tool fills it.
    applied, conflict = result.evidence["employment"]
    assert (applied.enricher, applied.applied, applied.field) == ("source", True, "type")
    assert (conflict.enricher, conflict.applied, conflict.method) == ("text.employment", False, "text")


def test_equal_or_stronger_method_replaces_and_marks_the_previous_value():
    first = stub("text.a", "text", [FactUpdate("min_experience", 1)])
    second = stub("source", "structured", [FactUpdate("min_experience", 2)])
    result = run(vacancy(), (first, second))
    assert result.min_experience == 2
    assert [(e.enricher, e.applied) for e in result.evidence["min_experience"]] == [
        ("text.a", False),
        ("source", True),
    ]


def test_undeclared_or_unregistered_facts_are_rejected():
    with pytest.raises(ValueError, match="did not declare"):
        run(vacancy(), (stub("x", "text", [FactUpdate("employment", [])], facts={"salary"}),))
    with pytest.raises(KeyError):
        run(vacancy(), (stub("x", "text", [FactUpdate("english_level", "B2")]),))


def test_new_tools_can_register_new_fact_types(monkeypatch):
    monkeypatch.setitem(registry.FACT_TYPES, "english_level", str)
    tool = stub("text.english", "inferred", [FactUpdate("english_level", "B2", field="description")])
    result = run(vacancy(), (tool,))
    assert result.facts["english_level"].value == "B2"
    assert result.facts["english_level"].evidence[0].method == "inferred"


def test_enrichers_are_found_by_source_id_without_enabled_adapters():
    # Built from the catalog alone: stored vacancies of a disabled source are still enriched.
    assert [e.id for e in registry.default_enrichment().enrichers_for("getmatch")] == [
        "formats.canonical",
        "getmatch",
    ]
    assert [e.id for e in registry.default_enrichment().enrichers_for("hh_ru")] == [
        "formats.canonical",
        "hh_ru",
    ]
    assert registry.source_key("ats:greenhouse:acme") == "ats:greenhouse"
    # No platform enricher yet: only the shared steps run.
    assert [e.id for e in registry.default_enrichment().enrichers_for("ats:greenhouse:acme")] == [
        "formats.canonical"
    ]
    assert [e.id for e in registry.default_enrichment().enrichers_for("disabled_source")] == [
        "formats.canonical"
    ]


def test_enrichment_is_idempotent_and_recomputes_evidence():
    stored = vacancy(
        work_formats=["REMOTE"],
        source_data={"area": {"countryIsoCode": "RU", "name": "Казань"}, "workFormats": ["REMOTE"]},
    )
    enrich = registry.default_enrichment().enrich
    once = enrich(stored, "hh_ru")
    assert enrich(once, "hh_ru") == once
    assert {e.enricher for e in once.evidence["work_formats"]} == {"formats.canonical", "hh_ru"}


@pytest.mark.parametrize(
    "label,expected",
    [
        ("RU", "RU"),
        ("Россия", "RU"),
        ("Germany", "DE"),
        ("Deutschland", "DE"),
        ("Объединенные Арабские Эмираты", "AE"),  # Another CLDR locale's name.
        ("ОАЭ", "AE"),
        ("США", "US"),  # CLDR alt-short name (territories.json.gz).
        ("Turkey", "TR"),  # CLDR alt-variant name.
        ("Автономная Республика Крым", None),  # No ISO country: stays unknown.
        ("Весь мир", None),
    ],
)
def test_country_labels_resolve_through_cldr_in_any_language(label, expected):
    result = default_catalog().country(label)
    assert result.value == expected
    assert result.status == ("found" if expected else "not_found")


def test_city_and_region_resolve_through_geonames_without_guessing():
    assert default_catalog().city("RU", "Казань").value == 551487
    assert default_catalog().city("RU", "Kazan").value == 551487
    assert default_catalog().region("RU", "Московская область").value == 524925
    assert (
        default_catalog().region("DE", "Bayern").value
        == default_catalog().region("DE", "Bavaria").value
        == 2951839
    )
    kirovsk = default_catalog().city("RU", "Кировск")
    assert kirovsk.status == "ambiguous" and set(kirovsk.candidates) >= {548391, 548392}
    assert default_catalog().city("RU", "Кировск", region_label="Murmansk").value == 548391
    assert default_catalog().city("RU", "Нет такого города").status == "not_found"
