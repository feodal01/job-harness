"""Provenance of vacancy facts (docs/vacancy-facts-plan.md §3).

Facts are written in the brief's vocabulary by a chain of enrichers. Each value keeps where it
came from, so a weaker method never silently replaces a stronger one and conflicts stay visible.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, JsonValue

# structured: a source field; text: parsed from vacancy text; inferred: a model or heuristic guess.
EnrichMethod = Literal["structured", "text", "inferred"]
METHOD_RANK: dict[EnrichMethod, int] = {"structured": 0, "text": 1, "inferred": 2}


class Evidence(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enricher: str
    version: str
    method: EnrichMethod
    field: str | None = None
    raw: JsonValue = None
    # False: another enricher's value won; this one is kept as a visible conflict.
    applied: bool = True


class Fact(BaseModel):
    """A fact outside the Vacancy fields, declared by an enricher in the fact registry."""

    model_config = ConfigDict(extra="forbid")
    value: JsonValue
    evidence: list[Evidence]


class IsFact:
    """Marks a Vacancy field as a fact set by enrichers: `Annotated[T, FACT]`.

    The model is the only list of fact fields; the enrichment registry reads the marks.
    """


FACT = IsFact()
