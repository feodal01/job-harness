"""Employment: any of the wanted types."""

from src.models.vacancy import Vacancy
from src.selection.checks.base import Check, Context, Verdict


def run(vacancy: Vacancy, context: Context) -> list[Verdict]:
    wanted = context.filters.employment
    if not wanted:
        return []
    value = bool(set(wanted) & set(vacancy.employment)) if vacancy.employment else None
    return [Verdict("employment", value, "Сравнение типа занятости")]


CHECK = Check(fields={"employment": frozenset({"employment"})}, run=run)
