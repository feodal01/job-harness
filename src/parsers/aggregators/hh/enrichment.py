"""HH vacancy facts from its `area` and work formats, plus remote geography and relocation from the text.

Stage 2: moved from src/parsers/normalization.py; country names now come from CLDR (all ISO
countries, Russian and English) instead of the 32-country table. Stage 4: the relocation wording
moved here unchanged from the local filter, which no longer parses text (§11). The expressions are
an HH-only heuristic for Russian/English wording, not a general text tool; whether they stay is
decided at the HH stage (docs/vacancy-facts-plan.md).
"""

import re
from typing import cast

from src.enrichment.contract import FactUpdate
from src.geography.catalog import GeographyCatalog
from src.models.facts import EnrichMethod
from src.models.filters import VacancyPlace, WorkFormat
from src.models.vacancy import Vacancy

WORLDWIDE = re.compile(
    r"из\s+любой\s+точки\s+мира|из\s+любой\s+страны|"
    r"(?:remote|work(?:ing)?)(?:\s+\w+){0,3}\s+(?:worldwide|anywhere)|"
    r"worldwide\s+(?:remote|work)|удал[её]н\w*[^.!?;\n]{0,25}по\s+всему\s+миру",
    re.I,
)


def _worldwide(text: str) -> bool:
    for sentence in re.split(r"[.!?;\n]", text):
        match = WORLDWIDE.search(sentence)
        if match and not re.search(r"\b(?:не|нет|нельзя|not|no)\b.{0,35}$", sentence[: match.start()], re.I):
            return True
    return False


def _excluded_countries(text: str, catalog: GeographyCatalog) -> set[str]:
    excluded = set()
    russian, english = catalog.country_names("ru"), catalog.country_names("en")
    for match in re.finditer(r"(?:кроме|за исключением|except|excluding)\s+([^.!?;\n]+)", text, re.I):
        for code, name in russian.items():
            # Country names after кроме usually take the Russian genitive case.
            variants = {name, english[code]}
            if name.endswith("ия"):
                variants.add(name[:-1] + "и")
            elif name.endswith("а"):
                variants.update({name[:-1] + "ы", name[:-1] + "и"})
            elif name.endswith("ь"):
                variants.add(name[:-1] + "и")
            else:
                variants.add(name + "а")
            if code == "RU":
                variants.add("РФ")
            if any(re.search(r"(?<!\w)" + re.escape(v) + r"(?!\w)", match[1], re.I) for v in variants):
                excluded.add(code)
    return excluded


def _relocation(text: str) -> bool | None:
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
        return None  # Contradictory statements stay unknown.
    if positive:
        return True
    if negative:
        return False
    return None


class HHEnricher:
    id = "hh_ru"
    version = "3"
    method: EnrichMethod = "structured"
    facts = frozenset({"locations", "work_formats"})

    def __init__(self, catalog: GeographyCatalog) -> None:
        self.catalog = catalog

    def enrich(self, vacancy: Vacancy) -> list[FactUpdate]:
        formats = list(vacancy.work_formats)
        locations = [p.model_copy() for p in vacancy.locations]
        text = "\n".join(t for t in (vacancy.summary, vacancy.description) if t)
        # A worldwide phrase alone may describe the company's business. Require
        # an explicit remote work statement or an already structured remote format.
        if _worldwide(text) and (
            "remote_country" in formats or re.search(r"удал[её]н|remote|work from anywhere", text, re.I)
        ):
            formats = ["remote_worldwide" if f == "remote_country" else f for f in formats]
            if "remote_worldwide" not in formats:
                formats.append("remote_worldwide")
        raw_area = vacancy.source_data.get("area")
        area = raw_area if isinstance(raw_area, dict) else {}
        raw_country, raw_city = area.get("countryIsoCode"), area.get("name")
        country = raw_country if isinstance(raw_country, str) else None
        city = raw_city if isinstance(raw_city, str) else vacancy.location
        existing = [p for p in locations if not p.excluded]
        if not country and existing:
            country = existing[0].country
        excluded = [p for p in locations if p.excluded]
        if formats:
            locations = [
                VacancyPlace(
                    country=country,
                    city=city,
                    format=cast(WorkFormat, fmt),
                    worldwide=fmt == "remote_worldwide",
                )
                for fmt in formats
            ] + excluded
        # Preserve explicit exclusions in ordinary Russian/English statements.
        for code in sorted(_excluded_countries(text, self.catalog)):
            if not any(p.excluded and p.country == code for p in locations):
                locations.append(VacancyPlace(country=code, format="remote_worldwide", excluded=True))
        return [
            FactUpdate("work_formats", formats, field="work_formats"),
            FactUpdate(
                "locations", _with_relocation(locations, vacancy.description), field="area", raw=raw_area
            ),
        ]


def _with_relocation(locations: list[VacancyPlace], description: str | None) -> list[VacancyPlace]:
    """Relocation of the vacancy's places: promised by the full card text only, a summary proves nothing.

    Relocation is a fact of a place; without a place (HH gave no `area` country) it is not recorded.
    """
    relocation = _relocation(description) if description else None
    for place in locations:
        place.relocation_support = None if place.excluded else relocation
    return locations
