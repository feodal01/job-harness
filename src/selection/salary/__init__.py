"""The brief's salary minimum against a vacancy's salary records.

Spec: docs/brief-parameters/salary-minimum-check.md.

    preprocess  every record in the brief's currency and period, or set aside with the reason;
    zones       each record as a zone on the line, part «точно», part «возможно» (k₁, k₂);
    groups      zones merged when they touch, split when a named low exceeds the group's lowest named high;
    P and E     P — the top of «точно» of the cheapest group, E — the top of the whole line;
    verdict     M ≤ P → yes, P < M ≤ E → possible, M > E → no.

Exact arithmetic with fractions; nothing is rounded before the comparison.
"""

from dataclasses import dataclass
from fractions import Fraction

from src.models.salary import PER_MONTH, SalaryRecord, in_period
from src.selection.salary.settings import SalaryRange

SOURCE_UNITS = {
    "shift": "за смену",
    "fly_in_fly_out": "за вахту",
    "service": "за услугу",
    "project": "за проект",
}


@dataclass(frozen=True)
class Zone:
    low: Fraction | None  # The named low («от A», A of «A–B», S).
    high: Fraction | None  # The named high («до B», B of «A–B», S).
    start: Fraction
    end: Fraction  # Including the «возможно» tail.


@dataclass(frozen=True)
class Assessment:
    verdict: bool | None  # True — yes, None — possible (not checked), False — no.
    p: Fraction | None
    e: Fraction | None
    reason: str
    bonus: bool  # The label «есть премии».


def _zone(low: Fraction | None, high: Fraction | None, k: SalaryRange) -> Zone:
    k2 = Fraction(str(k.possible))
    if low is not None and high is not None:
        return Zone(low, high, low, high)
    if high is not None:
        return Zone(None, high, high / k2, high)
    assert low is not None
    return Zone(low, None, low, low * k2)


def _components(zones: list[Zone]) -> list[list[Zone]]:
    """Merge zones that overlap or touch (merge intervals)."""
    groups: list[list[Zone]] = []
    end: Fraction | None = None
    for zone in sorted(zones, key=lambda z: (z.start, z.end)):
        if groups and end is not None and zone.start <= end:
            groups[-1].append(zone)
            end = max(end, zone.end)
        else:
            groups.append([zone])
            end = zone.end
    return groups


def _groups(zones: list[Zone]) -> list[list[Zone]]:
    """Connected zones, split while a named low is above the lowest named high of its group."""
    result = []
    for group in _components(zones):
        highs = [z.high for z in group if z.high is not None]
        ceiling = min(highs) if highs else None
        above = [z for z in group if ceiling is not None and z.low is not None and z.low > ceiling]
        if above:
            result += _groups([z for z in group if z not in above]) + _groups(above)
        else:
            result.append(group)
    return sorted(result, key=lambda g: min(z.start for z in g))


def _tops(group: list[Zone], k: SalaryRange) -> tuple[Fraction, Fraction]:
    """(top of «точно», top of «возможно») of one group."""
    highs = [z.high for z in group if z.high is not None]
    if highs:
        return max(highs), max(highs)
    lowest_offer = max(z.low for z in group if z.low is not None)
    return lowest_offer * Fraction(str(k.exact)), lowest_offer * Fraction(str(k.possible))


def _amount(value: Fraction) -> str:
    return f"{float(value):,.2f}".rstrip("0").rstrip(".").replace(",", " ")


def _describe(low: Fraction | None, high: Fraction | None) -> str:
    if low is not None and high is not None:
        return _amount(low) if low == high else f"{_amount(low)}–{_amount(high)}"
    return f"от {_amount(low)}" if low is not None else f"до {_amount(high)}"  # type: ignore[arg-type]


def _set_aside(record: SalaryRecord, currency: str) -> str | None:
    """Why a specified record cannot be compared with the brief, or None."""
    if record.from_ is not None and record.to is not None and record.from_ > record.to:
        return f"некорректная запись {record.from_:g}–{record.to:g}"
    if record.currency != currency:
        return f"валюта {record.currency or 'не указана'}"
    if record.unit is None:
        return "период не указан"
    if record.unit in SOURCE_UNITS:
        return f"единица источника «{SOURCE_UNITS[record.unit]}»"
    if record.unit not in PER_MONTH:
        return f"неизвестная единица «{record.unit}»"
    return None


def assess(
    records: list[SalaryRecord], minimum: Fraction | int | float, currency: str, period: str, k: SalaryRange
) -> Assessment:
    wanted = Fraction(str(minimum))
    bonus = any(r.bonus or r.total for r in records)
    hidden = any(r.state == "hidden" for r in records)
    aside: list[str] = []
    pairs: dict[tuple[Fraction | None, Fraction | None], None] = {}
    for record in records:
        if record.state != "specified" or (record.from_ is None and record.to is None):
            continue
        if why := _set_aside(record, currency):
            aside.append(why)
            continue
        assert record.unit is not None
        low, high = (
            None if v is None else in_period(v, record.unit, period) for v in (record.from_, record.to)
        )
        if record.total:
            # «Совокупно» includes bonuses: the base salary is at most the largest named number.
            low, high = None, max(v for v in (low, high) if v is not None)
        pairs[(low, high)] = None  # Duplicates after conversion are one record.
    notes = (["зарплата скрыта источником"] if hidden else []) + (
        [f"несравнимы: {', '.join(aside)}"] if aside else []
    )
    if not pairs:
        if aside:
            return Assessment(None, None, None, "; ".join(notes), bonus)
        base = "зарплата скрыта источником" if hidden else "зарплата не указана"
        return Assessment(True, None, None, f"{base}: вакансия проходит условие", bonus)
    groups = _groups([_zone(low, high, k) for low, high in pairs])
    p = _tops(groups[0], k)[0]
    e = max(_tops(group, k)[1] for group in groups)
    verdict = True if wanted <= p else None if wanted <= e else False
    listed = ", ".join(_describe(low, high) for low, high in pairs)
    reason = f"{listed} {currency}/{period}: точно до {_amount(p)}, возможно до {_amount(e)}"
    return Assessment(verdict, p, e, "; ".join([reason, *notes]), bonus)


__all__ = ["Assessment", "SalaryRange", "assess"]
