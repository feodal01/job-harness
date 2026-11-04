"""Local checks: one module per brief condition (docs/vacancy-facts-plan.md §11)."""

from src.models.filters import FilterName
from src.selection.checks import (
    companies,
    employment,
    excluded_text,
    experience,
    places,
    profile,
    published,
    salary,
)
from src.selection.checks.base import Check, Context, Verdict

CHECKS: tuple[Check, ...] = (
    profile.CHECK,
    places.CHECK,
    employment.CHECK,
    experience.CHECK,
    salary.CHECK,
    published.CHECK,
    companies.CHECK,
    excluded_text.CHECK,
)

# Vacancy fields each brief condition reads; a source without them cannot check the condition.
FILTER_FIELDS: dict[FilterName, frozenset[str]] = {
    name: fields for check in CHECKS for name, fields in check.fields.items()
}

__all__ = ["CHECKS", "FILTER_FIELDS", "Check", "Context", "Verdict"]
