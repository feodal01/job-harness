"""Enricher contract: one tool that sets vacancy facts from data it understands."""

from dataclasses import dataclass
from typing import Any, Protocol

from pydantic import JsonValue

from src.models.facts import EnrichMethod
from src.models.vacancy import Vacancy


@dataclass(frozen=True)
class FactUpdate:
    """A proposed value for one registered fact (see registry.FACT_TYPES)."""

    name: str
    value: Any
    field: str | None = None  # Where it came from: a source_data path or a text field.
    raw: JsonValue = None


class Enricher(Protocol):
    """A source normalizer (structured data) or a shared tool (e.g. text parsing).

    `enrich` reads the vacancy as the chain has built it so far and proposes updates;
    the chain decides by method priority what is applied. Enrichers must be deterministic
    for the same input and `version`, so stored vacancies re-enrich to the same facts.
    """

    id: str
    version: str
    method: EnrichMethod
    facts: frozenset[str]

    def enrich(self, vacancy: Vacancy) -> list[FactUpdate]: ...
