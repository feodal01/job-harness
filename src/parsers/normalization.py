"""Shared normalization for freshly parsed facts and previously stored vacancies."""

import re
from typing import cast

from src.models.filters import COUNTRIES, WorkFormat, WorkLocation
from src.models.vacancy import Vacancy

FORMATS: dict[str, WorkFormat] = {
    "ON_SITE": "office",
    "HYBRID": "hybrid",
    "REMOTE": "remote_country",
    "remote": "remote_country",
    "office": "office",
    "hybrid": "hybrid",
    "remote_country": "remote_country",
    "remote_worldwide": "remote_worldwide",
}
WORLDWIDE = re.compile(
    r"из\s+любой\s+точки\s+мира|из\s+любой\s+страны|"
    r"(?:remote|work(?:ing)?)(?:\s+\w+){0,3}\s+(?:worldwide|anywhere)|"
    r"worldwide\s+(?:remote|work)|удал[её]н\w*[^.!?;\n]{0,25}по\s+всему\s+миру",
    re.I,
)
COUNTRY_EN = dict(
    zip(
        COUNTRIES,
        (
            "Russia",
            "Belarus",
            "Kazakhstan",
            "Armenia",
            "Uzbekistan",
            "Kyrgyzstan",
            "Azerbaijan",
            "Georgia",
            "Ukraine",
            "Moldova",
            "Serbia",
            "Montenegro",
            "Turkey",
            "Cyprus",
            "UAE",
            "Israel",
            "Germany",
            "Poland",
            "Netherlands",
            "France",
            "Spain",
            "Portugal",
            "UK",
            "USA",
            "Canada",
            "Australia",
            "Thailand",
            "Indonesia",
            "Vietnam",
            "Singapore",
            "Switzerland",
            "Czechia",
        ),
        strict=True,
    )
)


def _worldwide(text: str) -> bool:
    for sentence in re.split(r"[.!?;\n]", text):
        match = WORLDWIDE.search(sentence)
        if match and not re.search(r"\b(?:не|нет|нельзя|not|no)\b.{0,35}$", sentence[: match.start()], re.I):
            return True
    return False


def _excluded_countries(text: str) -> set[str]:
    excluded = set()
    for match in re.finditer(r"(?:кроме|за исключением|except|excluding)\s+([^.!?;\n]+)", text, re.I):
        for code, name in COUNTRIES.items():
            # Country names after кроме usually take the Russian genitive case.
            variants = {name, COUNTRY_EN[code]}
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


def getmatch_locations(data: dict) -> list[WorkLocation]:
    places = []
    for place in data.get("location_requirements") or []:
        raw_format = place.get("format")
        if raw_format not in FORMATS:
            continue
        country = next((code for code, name in COUNTRIES.items() if name == place.get("country")), None)
        worldwide = place.get("location_id") == "_cu-world"
        work_format = "remote_worldwide" if worldwide else FORMATS[raw_format]
        places.append(
            WorkLocation(
                country=country,
                city=place.get("city") or None,
                format=cast(WorkFormat, work_format),
                excluded=bool(place.get("exclude")),
                worldwide=worldwide,
            )
        )
    return places


def normalize_stored_vacancy(vacancy: Vacancy, source: str) -> Vacancy:
    """Return a copy; preserve identity, text, source evidence and enrichment."""
    result = vacancy.model_copy(deep=True)
    result.work_formats = list(dict.fromkeys(FORMATS[f] for f in result.work_formats if f in FORMATS))
    if source == "getmatch":
        places = getmatch_locations(result.source_data)
        if places:
            result.locations = places
        for place in result.locations:
            if place.worldwide and place.format == "remote_country":
                place.format = "remote_worldwide"
        declared = [p.format for p in result.locations if p.format and not p.excluded]
        if declared:
            result.work_formats = list(dict.fromkeys(declared))
    elif source == "hh_ru":
        text = "\n".join(t for t in (result.summary, result.description) if t)
        # A worldwide phrase alone may describe the company's business. Require
        # an explicit remote work statement or an already structured remote format.
        if _worldwide(text) and (
            "remote_country" in result.work_formats
            or re.search(r"удал[её]н|remote|work from anywhere", text, re.I)
        ):
            result.work_formats = [
                "remote_worldwide" if f == "remote_country" else f for f in result.work_formats
            ]
            if "remote_worldwide" not in result.work_formats:
                result.work_formats.append("remote_worldwide")
        raw_area = result.source_data.get("area")
        area = raw_area if isinstance(raw_area, dict) else {}
        raw_country, raw_city = area.get("countryIsoCode"), area.get("name")
        country = raw_country if isinstance(raw_country, str) else None
        city = raw_city if isinstance(raw_city, str) else result.location
        existing = [p for p in result.locations if not p.excluded]
        if not country and existing:
            country = existing[0].country
        excluded = [p for p in result.locations if p.excluded]
        if result.work_formats:
            result.locations = [
                WorkLocation(
                    country=country,
                    city=city,
                    format=cast(WorkFormat, fmt),
                    worldwide=fmt == "remote_worldwide",
                )
                for fmt in result.work_formats
            ] + excluded
        # Preserve explicit exclusions in ordinary Russian/English statements.
        for code in sorted(_excluded_countries(text)):
            if not any(p.excluded and p.country == code for p in result.locations):
                result.locations.append(WorkLocation(country=code, format="remote_worldwide", excluded=True))
    return result
