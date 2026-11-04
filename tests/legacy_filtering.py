"""FROZEN copy of src/selection/filtering.py before stage 4 (docs/vacancy-facts-plan.md §11).

Used only to prove that sources without place facts (HH, ATS) keep their decisions and for the
stage 4 acceptance report. Deleted together with the transitional branch (stage N+1).
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from src.geography.catalog import default_catalog
from src.models.filters import (
    WORK_FORMATS,
    FilterCheck,
    FilterName,
    FilterResult,
    SearchFilters,
    SearchPlace,
    Status,
    WorkFormat,
    WorkLocation,
)
from src.models.vacancy import Vacancy
from src.selection.profile_matching import match_profile

# Keyed by str: vacancy data may carry formats outside the model, which have no rank.
FORMAT_RANK: dict[str, int] = {fmt: rank for rank, fmt in enumerate(WORK_FORMATS)}

# Normalized vacancy fields each local check reads; a source without them cannot check the condition.
FILTER_FIELDS: dict[FilterName, frozenset[str]] = {
    "locations": frozenset({"locations"}),
    "minimum_work_format": frozenset({"work_formats"}),
    "employment": frozenset({"employment"}),
    "experience_years": frozenset({"min_experience"}),
    "salary": frozenset({"salary"}),
    "published_after": frozenset({"published_at"}),
    "relocation_support": frozenset({"description", "summary"}),
    "excluded_companies": frozenset({"company"}),
    "excluded_text": frozenset({"description", "summary", "title"}),
}


def _match_city(wanted: SearchPlace, actual: str) -> bool | None:
    # Only cities confirmed in the brief editor (GeoNames ID) can be compared; stage 4 passes the
    # application's catalog explicitly, until then the same process-wide instance is used.
    if wanted.geoname_id is None:
        return None
    return default_catalog().matches(wanted.geoname_id, actual)


def _status(value: bool | None) -> Status:
    return "unknown" if value is None else "accepted" if value else "rejected"


def _any(values: list[bool | None]) -> bool | None:
    if True in values:
        return True
    return None if not values or None in values else False


def _excluded(wanted: SearchPlace, exclusions: list[WorkLocation], mode: str | None) -> bool | None:
    partial = False
    for place in exclusions:
        if mode in {"office", "hybrid"} and place.format not in {None, mode}:
            continue
        if place.country != wanted.country:
            continue
        if not place.city:
            return True
        if not wanted.city:
            partial = True
        else:
            match = _match_city(wanted, place.city)
            if match is True:
                return True
            if match is None:
                partial = True
    return None if partial else False


def _place_matches(wanted: SearchPlace, actual: WorkLocation, mode: str | None) -> bool | None:
    if mode == "remote_worldwide":
        return True
    if actual.country is None:
        return None
    if mode is None:
        # An unknown format might allow worldwide or country-wide remote work.
        if actual.country != wanted.country:
            return None
        if not wanted.city or (actual.city and _match_city(wanted, actual.city)):
            return True
        return None
    if actual.country != wanted.country:
        return False
    if mode == "remote_country" or not wanted.city:
        return True
    return _match_city(wanted, actual.city) if actual.city else None


def _alternatives(vacancy: Vacancy) -> list[tuple[str | None, WorkLocation]]:
    locations = [p for p in vacancy.locations if not p.excluded]
    modes = {"remote_country" if m == "remote" else m for m in vacancy.work_formats}
    modes &= FORMAT_RANK.keys()
    alternatives: list[tuple[str | None, WorkLocation]] = []
    for place in locations:
        if place.worldwide or place.format == "remote_worldwide":
            alternatives.append(("remote_worldwide", place))
        elif place.format:
            alternatives.append((place.format, place))
        else:
            alternatives.extend((m, place) for m in sorted(modes))
            if not modes:
                alternatives.append((None, place))
    represented = {mode for mode, _ in alternatives}
    alternatives.extend((m, WorkLocation()) for m in sorted(modes - represented))
    return alternatives or [(None, WorkLocation())]


@dataclass(frozen=True)
class LocationEvaluation:
    geography: bool | None
    format_ok: bool | None
    relocation_ok: bool | None
    minimum: WorkFormat | None
    requires_relocation: bool


def _location_and_format(
    vacancy: Vacancy, filters: SearchFilters, *, unsupported: frozenset[str], relocation: bool | None
) -> LocationEvaluation:
    branches: list[LocationEvaluation] = []
    exclusions = [p for p in vacancy.locations if p.excluded]
    wanted_places: list[SearchPlace | None] = list(filters.locations) or [None]
    for mode, actual in _alternatives(vacancy):
        for wanted in wanted_places:
            geography: bool | None = True
            if wanted is not None and "locations" not in unsupported:
                excluded = _excluded(wanted, exclusions, mode)
                geography = (
                    False
                    if excluded is True
                    else None
                    if excluded is None
                    else _place_matches(wanted, actual, mode)
                )
            minimum, needs_relocation = filters.location_conditions(wanted)
            format_ok = (
                True
                if minimum is None
                else None
                if mode is None or "minimum_work_format" in unsupported
                else FORMAT_RANK[mode] >= FORMAT_RANK[minimum]
            )
            relocation_ok = (
                True if not needs_relocation else None if "relocation_support" in unsupported else relocation
            )
            branches.append(
                LocationEvaluation(geography, format_ok, relocation_ok, minimum, needs_relocation)
            )
    viable = [b for b in branches if b.geography is not False]
    if not viable:
        first = branches[0]
        return LocationEvaluation(False, None, None, first.minimum, first.requires_relocation)

    def score(branch: LocationEvaluation) -> tuple[bool, bool, bool, int, int]:
        values = (branch.geography, branch.format_ok, branch.relocation_ok)
        return (
            all(v is True for v in values),
            not any(v is False for v in values),
            branch.geography is True,
            sum(v is True for v in values),
            -sum(v is False for v in values),
        )

    # Evaluate one complete option: format and relocation cannot be borrowed from different places.
    best = max(viable, key=score)
    return LocationEvaluation(
        _any([b.geography for b in branches]),
        None if best.geography is None and best.format_ok is True else best.format_ok,
        None if best.geography is None and best.relocation_ok is True else best.relocation_ok,
        best.minimum,
        best.requires_relocation,
    )


def _text_contains(text: str, term: str) -> bool:
    return re.search(r"(?<!\w)" + re.escape(term) + r"(?!\w)", text, re.IGNORECASE) is not None


def _relocation(text: str) -> tuple[bool | None, str]:
    positive, negative = [], []
    for sentence in re.split(r"[.!?\n]", text):
        sentence = sentence.strip()
        assistance = re.search(
            r"помо(?:щ[ьи]|га\w*)[^.!?]{0,45}(?:переезд|релокац)|"
            r"релокационн\w* пакет|relocation (?:assistance|support|package)|"
            r"(?:оплачива\w*|компенсиру\w*)[^.!?]{0,30}переезд",
            sentence,
            re.I,
        )
        if not assistance:
            continue
        if re.search(r"\b(?:experience|required|administer\w*)\b|\bопыт\b|обязател\w*", sentence, re.I):
            continue
        if re.search(
            r"\b(?:may|might|if|only|depending)\b|возможн|при условии|в отдельных|только для", sentence, re.I
        ):
            continue
        if re.search(
            r"\bno relocation (?:support|assistance|package)\b|"
            r"\bnot (?:provide|offer|cover|support)\w*[^.!?]{0,30}relocation|"
            r"relocation (?:support|assistance|package)[^.!?]{0,15}not (?:provided|available|offered)|"
            r"не (?:предоставля\w*|оплачива\w*|компенсиру\w*|помога\w*)[^.!?]{0,45}(?:релокац|переезд)|"
            r"(?:релокац\w*|переезд\w*|пакет)\s+не (?:предоставля\w*|оплачива\w*|компенсиру\w*)|"
            r"без помощи[^.!?]{0,20}(?:переезд|релокац)",
            sentence,
            re.I,
        ):
            negative.append(sentence)
        elif not re.search(r"\b(?:not|no)\b|\bне\b|\bбез\b", sentence, re.I):
            positive.append(sentence)
    if positive and negative:
        return None, "Противоречивые сведения о помощи с переездом"
    if positive:
        return True, positive[0]
    if negative:
        return False, negative[0]
    return None, "Нет явного подтверждения или отказа в помощи с переездом"


def evaluate(
    vacancy: Vacancy,
    filters: SearchFilters,
    *,
    complete: bool = False,
    queries: Sequence[str] = (),
    unsupported: frozenset[str] = frozenset(),
) -> FilterResult:
    checks: list[FilterCheck] = []

    def add(field: str, value: bool | None, reason: str, *, kind: Literal["hard", "soft"] = "soft") -> None:
        supported = field not in unsupported
        checks.append(
            FilterCheck(
                field=field,
                status=_status(value) if supported else "unknown",
                reason=reason if supported else "Источник не поддерживает проверку этого условия",
                kind=kind,
                supported=supported,
            )
        )

    value, reason = match_profile(vacancy.title, vacancy.skills, queries)
    add("profile", value, reason, kind="hard")
    relocation, relocation_reason = (
        _relocation(vacancy.description)
        if complete and vacancy.description
        else (None, "Для проверки помощи с переездом требуется полное описание")
    )
    location = _location_and_format(vacancy, filters, unsupported=unsupported, relocation=relocation)
    if filters.locations:
        add(
            "locations",
            location.geography,
            "Страна и город с учётом формата и явных географических исключений",
            kind="hard",
        )
    if location.minimum:
        add(
            "minimum_work_format",
            location.format_ok,
            "Сравнение свободы формата с выбранным минимумом для варианта с подходящей географией",
        )
    if filters.employment:
        add(
            "employment",
            bool(set(filters.employment) & set(vacancy.employment)) if vacancy.employment else None,
            "Сравнение типа занятости",
        )
    if filters.experience_years is not None:
        add(
            "experience_years",
            (
                (filters.experience_years > vacancy.min_experience)
                if vacancy.min_experience_exclusive
                else filters.experience_years >= vacancy.min_experience
            )
            if vacancy.min_experience is not None
            else None,
            (
                f"Ваш опыт: {filters.experience_years:g} лет; требование: "
                f"{'больше' if vacancy.min_experience_exclusive else 'от'} {vacancy.min_experience:g} лет"
                if vacancy.min_experience is not None
                else "Минимальный требуемый опыт не указан"
            ),
        )
    if f := filters.salary:
        salary = vacancy.salary
        low, high = salary.get("from"), salary.get("to")
        if low is None and high is None:
            value, reason = True, "Зарплата не указана: вакансия проходит условие"
        elif salary.get("currencyCode") != f.currency or salary.get("period") != f.period:
            value, reason = None, "Валюта или период отличаются либо неизвестны; пересчёт не выполняется"
        else:
            bound = high if high is not None else low
            value = bound >= f.minimum if isinstance(bound, (int, float)) else None
            reason = (
                "Верхняя граница вилки или единственная сумма сравнивается с минимумом без пересчёта налогов"
            )
        add("salary", value, reason)
    if filters.published_after:
        try:
            value = datetime.fromisoformat(vacancy.published_at or "").date() >= filters.published_after
        except ValueError:
            value = None
        add("published_after", value, f"Сравнение даты публикации с {filters.published_after.isoformat()}")
    if location.requires_relocation:
        add("relocation_support", location.relocation_ok, relocation_reason)
    if filters.excluded_companies:
        name = vacancy.company.name if vacancy.company else None
        add(
            "excluded_companies",
            name.casefold() not in {n.casefold() for n in filters.excluded_companies} if name else None,
            "Точное сравнение названия компании без учёта регистра",
            kind="hard",
        )
    if filters.excluded_text:
        text = "\n".join([vacancy.title, vacancy.description or ""])
        found = next((t for t in filters.excluded_text if _text_contains(text, t)), None)
        add(
            "excluded_text",
            False if found else True if complete and vacancy.description else None,
            f"Найдено исключение: {found}" if found else "Проверка исключений в названии и полном тексте",
            kind="hard",
        )
    status: Literal["accepted", "maybe", "rejected"] = "accepted"
    if any(c.status == "rejected" and c.kind == "hard" for c in checks):
        status = "rejected"
    elif any(c.status == "rejected" and c.kind == "soft" for c in checks):
        status = "maybe"
    return FilterResult(status=status, checks=checks)
