"""Pure versioned derivation of normalized selection facts."""

from __future__ import annotations

import re

from job_harness.v2.contracts import (
    BooleanEvidenceFact,
    CanonicalSelectionFacts,
    CompensationFact,
    CompensationPeriod,
    FactDerivation,
    GradeFact,
    LocationFact,
    RelocationFact,
    WorkplaceFact,
)
from job_harness.v2.geography import is_region_scope, normalize_source_geographies
from job_harness.v2.serialization import JsonObject, to_jsonable

_GRADE_PATTERNS = (
    ("intern", re.compile(r"\b(?:intern|internship|стаж[её]р)\w*\b", re.I)),
    ("junior", re.compile(r"\b(?:junior|jr\.?|младш\w*)\b", re.I)),
    ("lead", re.compile(r"\b(?:lead|head|руководител\w*|тимлид\w*)\b", re.I)),
    ("senior", re.compile(r"\b(?:senior|sr\.?|staff|principal|старш\w*|ведущ\w*)\b", re.I)),
    ("middle", re.compile(r"\b(?:middle|mid(?:-level)?|мидл\w*)\b", re.I)),
)
_NATIVE_GRADE_ALIASES = {
    "internship": "intern",
    "jr": "junior",
    "mid": "middle",
    "mid-level": "middle",
    "sr": "senior",
    "staff": "senior",
    "principal": "senior",
    "head": "lead",
}
_RELOCATION_NEGATIVE_PATTERNS = (
    re.compile(r"\bno relocation (?:support|assistance|package|benefit)\b", re.I),
    re.compile(r"\brelocation (?:support|assistance) (?:is )?not (?:available|provided)\b", re.I),
    re.compile(r"\bбез релокации\b", re.I),
    re.compile(r"\bрелокаци\w+ не (?:предоставляется|поддерживается)\b", re.I),
)
_RELOCATION_POSITIVE_PATTERNS = (
    re.compile(r"\brelocation (?:support|assistance|package|benefit|provided|available)\b", re.I),
    re.compile(r"\bimmigration (?:support|assistance)\b", re.I),
    re.compile(r"\b(?:поддержка|помощь) (?:с |при )?релокаци\w*\b", re.I),
    re.compile(r"\bрелокаци\w+ (?:предоставляется|поддерживается|оплачивается)\b", re.I),
)
_VISA_SPONSORSHIP_NEGATIVE_PATTERNS = (
    re.compile(r"\b(?:do not|does not|don't|doesn't|cannot|can't|unable to) sponsor\b", re.I),
    re.compile(r"\bno (?:visa|immigration) sponsorship\b", re.I),
    re.compile(r"\bvisa sponsorship (?:is )?not (?:available|provided|offered)\b", re.I),
    re.compile(r"\bбез визовой поддержки\b", re.I),
)
_VISA_SPONSORSHIP_POSITIVE_PATTERNS = (
    re.compile(r"\bvisa sponsorship (?:is )?(?:available|provided|offered)\b", re.I),
    re.compile(r"\b(?:we|company|employer) sponsor(?:s|ing)? (?:employment )?visas?\b", re.I),
    re.compile(r"\bвизов(?:ая|ую) поддержк\w+\b", re.I),
)
_WORK_FORMAT_TEXT_PATTERNS = {
    "remote": (
        re.compile(r"\b(?:fully\s+|100%\s+)?remote(?:[- ](?:first|only|role|position|work|job))?\b", re.I),
        re.compile(
            r"\b(?:удал[её]нн\w*\s+(?:работ\w*|формат\w*|ваканси\w*)|"
            r"(?:работ\w*|формат\w*)\s+удал[её]нн\w*|удал[её]нно)\b",
            re.I,
        ),
    ),
    "hybrid": (
        re.compile(r"\bhybrid(?:[- ](?:role|position|work|job))?\b", re.I),
        re.compile(r"\bгибридн\w*\s+(?:работ\w*|формат\w*|ваканси\w*)\b", re.I),
    ),
    "office": (
        re.compile(r"\b(?:on[- ]?site|office[- ]based)(?:\s+(?:role|position|work|job))?\b", re.I),
        re.compile(
            r"\b(?:(?:работ\w*|формат\w*)\s+(?:из\s+)?офис\w*|офисн\w*\s+формат\w*)\b",
            re.I,
        ),
    ),
}
_GLOBAL_REMOTE_SCOPE_PATTERNS = (
    re.compile(r"\b(?:work|working) from anywhere(?: in the world)?\b", re.I),
    re.compile(r"\b(?:worldwide|globally) remote\b|\bremote worldwide\b", re.I),
    re.compile(r"\b(?:из любой точки мира|по всему миру|из любой страны)\b", re.I),
)
_RUSSIA_REMOTE_SCOPE_PATTERNS = (
    re.compile(r"\bremote.{0,60}\b(?:within|from|across) (?:russia|the russian federation)\b", re.I),
    re.compile(r"\b(?:based|located|residing) in (?:russia|the russian federation)\b", re.I),
    re.compile(r"\b(?:на территории|по территории|из любой точки) (?:рф|россии)\b", re.I),
)


def derive_selection_facts(facts: JsonObject) -> tuple[FactDerivation, ...]:
    canonical = CanonicalSelectionFacts(
        location=_location_fact(facts),
        workplace=_workplace_fact(facts),
        grade=_grade_fact(facts),
        compensation=_compensation_fact(facts),
        relocation=_relocation_fact(facts),
        visa_sponsorship=_boolean_fact(facts, kind="visa_sponsorship"),
        employer_geographies=_employer_geographies(facts),
    )
    payload = to_jsonable(canonical)
    if not isinstance(payload, dict):
        raise TypeError("canonical selection facts must serialize to an object")
    return (
        FactDerivation(
            deriver_id="structured-selection-facts",
            deriver_version="6.0",
            output_schema_id="selection-facts.v6",
            payload=payload,
        ),
    )


def _grade_fact(facts: JsonObject) -> GradeFact:
    title = _text(facts.get("title")) or ""
    positioned = sorted(
        (match.start(), grade)
        for grade, pattern in _GRADE_PATTERNS
        for match in pattern.finditer(title)
    )
    title_grades = tuple(dict.fromkeys(grade for _, grade in positioned))
    native = _text(facts.get("native_grade"))
    source_grades: tuple[str, ...] = ()
    if native:
        normalized = native.casefold().strip().rstrip(".")
        source_grades = (_NATIVE_GRADE_ALIASES.get(normalized, normalized),)
    resolved = title_grades or source_grades
    conflict = bool(
        title_grades
        and source_grades
        and not set(title_grades) & set(source_grades)
    )
    evidence = tuple(
        field
        for present, field in (
            (bool(title_grades), "title"),
            (bool(source_grades), "native_grade"),
        )
        if present
    )
    return GradeFact(title_grades, source_grades, resolved, conflict, evidence)


def _location_fact(facts: JsonObject) -> LocationFact:
    location = _object(facts.get("location")) or {}
    raw_text = _text(location.get("text"))
    cities = _strings(location.get("cities"))
    countries = tuple(value.upper() for value in _strings(location.get("countries")))
    regions = tuple(value.upper() for value in _strings(location.get("regions")))
    evidence = tuple(
        path
        for present, path in (
            (raw_text is not None, "location.text"),
            (bool(cities), "location.cities"),
            (bool(countries), "location.countries"),
            (bool(regions), "location.regions"),
        )
        if present
    )
    return LocationFact(raw_text, cities, countries, regions, evidence)


def _workplace_fact(facts: JsonObject) -> WorkplaceFact:
    structured_formats = tuple(
        "office" if value == "onsite" else value
        for value in _strings(facts.get("work_formats"))
        if value in {"remote", "hybrid", "office", "onsite"}
    )
    formats = tuple(dict.fromkeys(structured_formats))
    evidence: list[str] = ["work_formats"] if formats else []
    if not formats:
        for work_format, patterns in _WORK_FORMAT_TEXT_PATTERNS.items():
            matching_paths = _matching_text_paths(facts, patterns)
            if matching_paths:
                formats += (work_format,)
                evidence.extend(matching_paths)

    scopes: list[str] = []
    raw_scopes = facts.get("remote_scopes")
    if isinstance(raw_scopes, list):
        for raw_scope in raw_scopes:
            scope = _object(raw_scope)
            if scope is None:
                continue
            kind = _text(scope.get("kind"))
            code = _text(scope.get("code"))
            if kind == "worldwide":
                scopes.append("global")
            elif kind in {"country", "region"} and code:
                scopes.append(f"{kind}:{code.upper()}")
        if scopes:
            evidence.append("remote_scopes")

    if "remote" in formats and not scopes:
        global_paths = _matching_text_paths(facts, _GLOBAL_REMOTE_SCOPE_PATTERNS)
        russian_paths = _matching_text_paths(facts, _RUSSIA_REMOTE_SCOPE_PATTERNS)
        if global_paths:
            scopes.append("global")
            evidence.extend(global_paths)
        if russian_paths:
            scopes.append("country:RU")
            evidence.extend(russian_paths)

    return WorkplaceFact(
        formats=tuple(dict.fromkeys(formats)),
        remote_scopes=tuple(dict.fromkeys(scopes)),
        evidence=tuple(dict.fromkeys(evidence)),
    )


def _compensation_fact(facts: JsonObject) -> CompensationFact:
    salary = _object(facts.get("salary")) or {}
    minimum = _integer(salary.get("salary_from"))
    maximum = _integer(salary.get("salary_to"))
    currency = _text(salary.get("currency"))
    if currency:
        currency = currency.upper()
        if currency == "RUR":
            currency = "RUB"
    raw_period = _text(salary.get("period"))
    period = (
        CompensationPeriod(raw_period)
        if raw_period in {item.value for item in CompensationPeriod}
        else None
    )
    gross = salary.get("gross") if isinstance(salary.get("gross"), bool) else None
    evidence = tuple(
        path
        for present, path in (
            (minimum is not None, "salary.salary_from"),
            (maximum is not None, "salary.salary_to"),
            (currency is not None, "salary.currency"),
            (period is not None, "salary.period"),
            (gross is not None, "salary.gross"),
        )
        if present
    )
    return CompensationFact(minimum, maximum, currency, period, gross, evidence)


def _boolean_fact(facts: JsonObject, *, kind: str) -> BooleanEvidenceFact:
    explicit = facts.get(kind)
    if isinstance(explicit, bool):
        return BooleanEvidenceFact(explicit, (kind,))
    negative: tuple[re.Pattern[str], ...]
    positive: tuple[re.Pattern[str], ...]
    if kind == "relocation":
        negative = _RELOCATION_NEGATIVE_PATTERNS
        positive = _RELOCATION_POSITIVE_PATTERNS
    elif kind == "visa_sponsorship":
        negative = _VISA_SPONSORSHIP_NEGATIVE_PATTERNS
        positive = _VISA_SPONSORSHIP_POSITIVE_PATTERNS
    else:
        raise ValueError(f"unsupported boolean fact kind: {kind}")
    negative_paths = _matching_text_paths(facts, negative)
    if negative_paths:
        return BooleanEvidenceFact(False, negative_paths)
    positive_paths = _matching_text_paths(facts, positive)
    if positive_paths:
        return BooleanEvidenceFact(True, positive_paths)
    return BooleanEvidenceFact(None, ())


def _relocation_fact(facts: JsonObject) -> RelocationFact:
    destinations = _relocation_destinations(facts.get("relocation_destinations"))
    explicit = facts.get("relocation")
    if isinstance(explicit, bool):
        evidence = ("relocation",) + (
            ("relocation_destinations",) if destinations else ()
        )
        return RelocationFact(
            supported=explicit,
            destinations=destinations if explicit else (),
            evidence=evidence,
        )
    if destinations:
        return RelocationFact(True, destinations, ("relocation_destinations",))
    inferred = _boolean_fact(facts, kind="relocation")
    return RelocationFact(inferred.supported, (), inferred.evidence)


def _relocation_destinations(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    destinations: list[str] = []
    for item in value:
        location = _object(item)
        if location is None:
            continue
        structured = (
            *_strings(location.get("cities")),
            *(country.upper() for country in _strings(location.get("countries"))),
            *(region.upper() for region in _strings(location.get("regions"))),
        )
        if structured:
            destinations.extend(structured)
            continue
        raw_text = _text(location.get("text"))
        if raw_text:
            destinations.extend(normalize_source_geographies(raw_text) or (raw_text,))
    return tuple(dict.fromkeys(destinations))


def _matching_text_paths(
    facts: JsonObject,
    patterns: tuple[re.Pattern[str], ...],
) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            path
            for path, text in _selection_text_by_path(facts)
            if any(pattern.search(text) for pattern in patterns)
        )
    )


def _selection_text_by_path(facts: JsonObject) -> tuple[tuple[str, str], ...]:
    values: list[tuple[str, str]] = []
    for field in (
        "title",
        "summary",
        "description",
        "requirements",
        "responsibilities",
        "conditions",
        "raw_text",
    ):
        value = facts.get(field)
        if isinstance(value, str) and value.strip():
            values.append((field, value))
        elif isinstance(value, list | tuple):
            values.extend(
                (field, item)
                for item in value
                if isinstance(item, str) and item.strip()
            )
    additional = _object(facts.get("additional_sections")) or {}
    values.extend(
        (f"additional_sections.{key}", value)
        for key, value in additional.items()
        if isinstance(value, str) and value.strip()
    )
    return tuple(values)


def _employer_geographies(facts: JsonObject) -> tuple[str, ...]:
    company = _object(facts.get("company"))
    values: list[str] = []
    if company:
        values.extend(_strings(company.get("employer_geographies")))
    company_profile = _object(facts.get("company_profile"))
    if company_profile:
        values.extend(_strings(company_profile.get("locations")))
    raw_locations = facts.get("locations")
    if isinstance(raw_locations, list | tuple):
        for raw_location in raw_locations:
            location = _object(raw_location)
            if location:
                location_text = _text(location.get("text"))
                if location_text:
                    values.append(location_text)
    normalized: list[str] = []
    for value in values:
        if value.startswith("country:"):
            geographies = normalize_source_geographies(value.removeprefix("country:"))
        elif value.startswith("region:"):
            geographies = normalize_source_geographies(value.removeprefix("region:"))
        else:
            geographies = normalize_source_geographies(value)
        for geography in geographies:
            scoped = f"region:{geography}" if is_region_scope(geography) else f"country:{geography}"
            if scoped not in normalized:
                normalized.append(scoped)
    return tuple(normalized)


def _object(value: object) -> JsonObject | None:
    return value if isinstance(value, dict) else None


def _strings(value: object) -> tuple[str, ...]:
    if not isinstance(value, list | tuple):
        return ()
    return tuple(item for item in value if isinstance(item, str) and item)


def _text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _integer(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None
