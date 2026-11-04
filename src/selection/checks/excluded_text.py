"""Excluded words: the user's own terms searched as whole words in the title and the full text.

The only text search in the filter: the terms are the brief's condition, not our dictionary.
"""

import re

from src.models.vacancy import Vacancy
from src.selection.checks.base import Check, Context, Verdict


def _contains(text: str, term: str) -> bool:
    return re.search(r"(?<!\w)" + re.escape(term) + r"(?!\w)", text, re.IGNORECASE) is not None


def run(vacancy: Vacancy, context: Context) -> list[Verdict]:
    terms = context.filters.excluded_text
    if not terms:
        return []
    text = "\n".join([vacancy.title, vacancy.description or ""])
    found = next((t for t in terms if _contains(text, t)), None)
    if found:
        return [Verdict("excluded_text", False, f"Найдено исключение: {found}", "hard")]
    value = True if context.complete and vacancy.description else None
    return [Verdict("excluded_text", value, "Проверка исключений в названии и полном тексте", "hard")]


CHECK = Check(fields={"excluded_text": frozenset({"description", "summary", "title"})}, run=run)
