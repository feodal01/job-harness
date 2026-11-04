"""Publication date: not earlier than the wanted date."""

from datetime import datetime

from src.models.vacancy import Vacancy
from src.selection.checks.base import Check, Context, Verdict


def run(vacancy: Vacancy, context: Context) -> list[Verdict]:
    after = context.filters.published_after
    if after is None:
        return []
    try:
        value: bool | None = datetime.fromisoformat(vacancy.published_at or "").date() >= after
    except ValueError:
        value = None
    return [Verdict("published_after", value, f"Сравнение даты публикации с {after.isoformat()}")]


CHECK = Check(fields={"published_after": frozenset({"published_at"})}, run=run)
