"""Experience: the candidate's years against the vacancy's minimum ("from N" or "more than N")."""

from src.models.vacancy import Vacancy
from src.selection.checks.base import Check, Context, Verdict


def run(vacancy: Vacancy, context: Context) -> list[Verdict]:
    years = context.filters.experience_years
    if years is None:
        return []
    required = vacancy.min_experience
    if required is None:
        return [Verdict("experience_years", None, "Минимальный требуемый опыт не указан")]
    exclusive = vacancy.min_experience_exclusive
    value = years > required if exclusive else years >= required
    reason = f"Ваш опыт: {years:g} лет; требование: {'больше' if exclusive else 'от'} {required:g} лет"
    return [Verdict("experience_years", value, reason)]


CHECK = Check(fields={"experience_years": frozenset({"min_experience"})}, run=run)
