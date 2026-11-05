"""Salary: the brief's minimum against the vacancy's salary records (spec: P and E, «да / возможно / нет»).

The calculation is src/selection/salary; this check only reports it. «Возможно» is unknown (not checked),
«нет» fails the soft condition.
"""

from src.models.vacancy import Vacancy
from src.selection.checks.base import Check, Context, Verdict
from src.selection.salary import assess


def run(vacancy: Vacancy, context: Context) -> list[Verdict]:
    wanted = context.filters.salary
    if wanted is None:
        return []
    result = assess(vacancy.salary, wanted.minimum, wanted.currency, wanted.period, context.salary_range)
    reason = result.reason + ("; есть премии" if result.bonus else "")
    return [Verdict("salary", result.verdict, reason)]


CHECK = Check(fields={"salary": frozenset({"salary"})}, run=run)
