"""Excluded companies: exact name, case-insensitive."""

from src.models.vacancy import Vacancy
from src.selection.checks.base import Check, Context, Verdict


def run(vacancy: Vacancy, context: Context) -> list[Verdict]:
    excluded = context.filters.excluded_companies
    if not excluded:
        return []
    name = vacancy.company.name if vacancy.company else None
    value = name.casefold() not in {n.casefold() for n in excluded} if name else None
    return [
        Verdict("excluded_companies", value, "Точное сравнение названия компании без учёта регистра", "hard")
    ]


CHECK = Check(fields={"excluded_companies": frozenset({"company"})}, run=run)
