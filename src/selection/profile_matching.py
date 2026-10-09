"""Conservative role matching from titles and skills, independent of the source search."""

import re
from collections.abc import Sequence

# Shared groups describe professions, not every technology mentioned in a vacancy.
ROLE_SYNONYMS: dict[str, tuple[str, ...]] = {
    "qa": (
        "qa",
        "aqa",
        "sdet",
        "quality assurance",
        "quality engineer",
        "test engineer",
        "tester",
        "тестировщик",
        "тестировщица",
        "тестирования",
        "тестированию",
    ),
    "backend": ("backend", "back end", "бэкенд", "бэкенд-разработчик", "бекенд", "бекенд-разработчик"),
    "frontend": ("frontend", "front end", "фронтенд", "фронтенд-разработчик"),
    "sales": (
        "account manager",
        "sales manager",
        "sales representative",
        "менеджер по продажам",
        "менеджер продаж",
        "продавец",
    ),
    "recruitment": ("recruiter", "recruitment", "рекрутер", "рекрутёр", "подбору персонала"),
    "design": ("designer", "дизайнер"),
    "accounting": ("accountant", "бухгалтер"),
    "marketing": ("marketer", "маркетолог", "marketing manager"),
    "product": ("product manager", "product owner", "менеджер продукта", "продакт"),
    "project": ("project manager", "project coordinator", "менеджер проекта", "менеджер проектов"),
    "data": ("data scientist", "data engineer", "аналитик данных", "инженер данных"),
    "devops": ("devops", "sre", "site reliability"),
}


def _normalize(text: str) -> str:
    return " ".join(text.casefold().replace("ё", "е").split())


def _contains(text: str, phrase: str) -> bool:
    return bool(re.search(r"(?<!\w)" + re.escape(phrase) + r"(?!\w)", text))


def _roles(text: str) -> set[str]:
    return {
        role for role, words in ROLE_SYNONYMS.items() if any(_contains(text, _normalize(w)) for w in words)
    }


def _qualifiers(phrase: str, roles: set[str]) -> list[str]:
    """Remove role synonyms and generic role words, retaining technology and scope."""
    words = {_normalize(word) for role in roles for word in ROLE_SYNONYMS[role]}
    for word in sorted(words, key=len, reverse=True):
        phrase = re.sub(r"(?<!\w)" + re.escape(word) + r"(?!\w)", " ", phrase)
    tokens = re.findall(r"\.?\w+(?:[+#]+)?", phrase)
    fillers = {"engineer", "developer", "инженер", "разработчик", "по"}
    return [token for token in tokens if token not in fillers]


def match_profile(title: str, skills: Sequence[str], queries: Sequence[str]) -> tuple[bool | None, str]:
    phrases = [_normalize(q) for q in queries if q.strip()]
    if not phrases:
        return None, "Поисковые фразы профиля не заданы"
    normalized_title = _normalize(title)
    evidence = [normalized_title, *[_normalize(s) for s in skills]]
    wanted_roles = set().union(*[_roles(q) for q in phrases])
    actual_roles = _roles(normalized_title)
    matching_phrase = next((q for q in phrases if any(_contains(t, q) for t in evidence)), None)
    evidence_roles = set().union(*[_roles(t) for t in evidence])
    matching_roles = wanted_roles & evidence_roles
    synonym_phrase = next(
        (
            q
            for q in phrases
            if _roles(q) & evidence_roles
            and all(any(_contains(t, token) for t in evidence) for token in _qualifiers(q, _roles(q)))
        ),
        None,
    )
    if matching_phrase or synonym_phrase:
        # Mixed responsibilities and roles managing a platform are not reliable title matches.
        if (actual_roles - wanted_roles and actual_roles & wanted_roles) or re.search(
            r"\b(?:platform manager|менеджер платформы)\b", normalized_title
        ):
            return None, "Название содержит несколько ролей; соответствие профилю требует проверки"
        matched = matching_phrase or synonym_phrase
        return True, f"Совпадение профиля в названии или навыках: {matched}"
    if matching_roles:
        return None, "Профессия совпадает, но уточнения поисковой фразы не подтверждены названием и навыками"
    if actual_roles and wanted_roles and all(_roles(q) for q in phrases):
        return False, "В названии явно указана другая профессия; совпадений с профилем нет"
    return None, "В названии и навыках недостаточно данных для надёжной проверки профиля"
