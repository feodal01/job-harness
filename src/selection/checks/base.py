"""What a local check is (docs/vacancy-facts-plan.md §11).

A check compares brief conditions with vacancy facts already written in the brief's vocabulary
by the enrichers. It parses nothing and guesses nothing: a missing fact is "unknown" (None).
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Literal

from src.geography.catalog import GeographyCatalog
from src.models.filters import FilterName, SearchFilters
from src.models.vacancy import Vacancy


@dataclass(frozen=True)
class Context:
    filters: SearchFilters
    catalog: GeographyCatalog
    complete: bool
    queries: tuple[str, ...]
    # Conditions the source cannot check: their verdicts are reported as unknown.
    unsupported: frozenset[str]


@dataclass(frozen=True)
class Verdict:
    """One condition's outcome: True passes, False fails, None is unknown."""

    field: str
    value: bool | None
    reason: str
    kind: Literal["hard", "soft"] = "soft"
    conflicts: tuple[str, ...] = ()  # Disagreeing facts behind the value (Vacancy.conflicts).


@dataclass(frozen=True)
class Check:
    # Brief conditions this check decides → vacancy fields it reads. A source that lacks
    # those fields cannot check the condition locally (src/parsers/interfaces.py).
    fields: Mapping[FilterName, frozenset[str]]
    run: Callable[[Vacancy, Context], list[Verdict]]
