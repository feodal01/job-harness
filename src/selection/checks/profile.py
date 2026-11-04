"""Profile: the vacancy title and skills against the search queries (not a brief condition)."""

from src.models.vacancy import Vacancy
from src.selection.checks.base import Check, Context, Verdict
from src.selection.profile_matching import match_profile


def run(vacancy: Vacancy, context: Context) -> list[Verdict]:
    value, reason = match_profile(vacancy.title, vacancy.skills, context.queries)
    return [Verdict("profile", value, reason, kind="hard")]


CHECK = Check(fields={}, run=run)
