"""Salary (spec §3.5): the upper bound or the only amount against the minimum, without conversion."""

from src.models.vacancy import Vacancy
from src.selection.checks.base import Check, Context, Verdict


def run(vacancy: Vacancy, context: Context) -> list[Verdict]:
    wanted = context.filters.salary
    if wanted is None:
        return []
    salary = vacancy.salary
    low, high = salary.get("from"), salary.get("to")
    if low is None and high is None:
        return [Verdict("salary", True, "Зарплата не указана: вакансия проходит условие")]
    if salary.get("currencyCode") != wanted.currency or salary.get("period") != wanted.period:
        return [
            Verdict("salary", None, "Валюта или период отличаются либо неизвестны; пересчёт не выполняется")
        ]
    bound = high if high is not None else low
    value = bound >= wanted.minimum if isinstance(bound, (int, float)) else None
    reason = "Верхняя граница вилки или единственная сумма сравнивается с минимумом без пересчёта налогов"
    return [Verdict("salary", value, reason)]


CHECK = Check(fields={"salary": frozenset({"salary"})}, run=run)
