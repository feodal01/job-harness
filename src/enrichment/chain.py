"""Run the enrichers of one source and merge their facts by method priority."""

from functools import cache
from typing import Any

from pydantic import TypeAdapter

from src.enrichment.contract import Enricher, FactUpdate
from src.enrichment.registry import FACT_TYPES, VACANCY_FACTS
from src.models.facts import METHOD_RANK, Evidence, Fact
from src.models.vacancy import Vacancy


def run(vacancy: Vacancy, enrichers: tuple[Enricher, ...]) -> Vacancy:
    result = vacancy.model_copy(deep=True)
    result.evidence, result.facts = {}, {}
    for enricher in enrichers:
        for update in enricher.enrich(result):
            if update.name not in enricher.facts:
                raise ValueError(f"{enricher.id} did not declare fact {update.name}")
            _apply(result, enricher, update)
    for name, fact in result.facts.items():
        fact.evidence = result.evidence[name]
    return result


def _apply(vacancy: Vacancy, enricher: Enricher, update: FactUpdate) -> None:
    value = _adapter(update.name).validate_python(update.value)
    evidence = Evidence(
        enricher=enricher.id,
        version=enricher.version,
        method=enricher.method,
        field=update.field,
        raw=update.raw,
    )
    history = vacancy.evidence.setdefault(update.name, [])
    current = next((e for e in history if e.applied), None)
    # An equal or stronger method replaces; a weaker one only fills an empty fact.
    stronger = current is None or METHOD_RANK[enricher.method] <= METHOD_RANK[current.method]
    if stronger or _empty(_get(vacancy, update.name)):
        if current is not None:
            current.applied = False
        _set(vacancy, update.name, value, evidence)
        history.append(evidence)
    elif value != _get(vacancy, update.name):
        history.append(evidence.model_copy(update={"applied": False}))  # A visible conflict.


@cache
def _adapter(name: str) -> TypeAdapter[Any]:
    return TypeAdapter(FACT_TYPES[name])


def _get(vacancy: Vacancy, name: str) -> Any:
    if name in VACANCY_FACTS:
        return getattr(vacancy, name)
    fact = vacancy.facts.get(name)
    return fact.value if fact else None


def _set(vacancy: Vacancy, name: str, value: Any, evidence: Evidence) -> None:
    if name in VACANCY_FACTS:
        setattr(vacancy, name, value)
    else:
        vacancy.facts[name] = Fact(value=value, evidence=[evidence])


def _empty(value: Any) -> bool:
    return value is None or value == [] or value == {}


__all__ = ["FactUpdate", "run"]
