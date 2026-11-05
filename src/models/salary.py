"""A salary record of a vacancy in the brief's vocabulary.

Spec: docs/brief-parameters/salary-minimum-check.md.

A vacancy may carry several records (listing, card, text). Each keeps the source's own form — its unit,
which may be a source unit like «за смену», and the original value — and, when the unit is a standard
period, the amounts in every standard period, so a brief in any period compares without re-parsing.
"""

from fractions import Fraction
from typing import Any, Literal, get_args

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from src.models.facts import Evidence

SalaryState = Literal["specified", "not_specified", "hidden"]
StandardPeriod = Literal["hour", "day", "week", "month", "year"]
STANDARD_PERIODS: tuple[StandardPeriod, ...] = get_args(StandardPeriod)

# Standard week: 40 hours, 5 days of 8 hours, 52 weeks a year (spec, preprocessing step 2).
PER_MONTH: dict[StandardPeriod, Fraction] = {
    "hour": Fraction(40 * 52, 12),
    "day": Fraction(5 * 52, 12),
    "week": Fraction(52, 12),
    "month": Fraction(1),
    "year": Fraction(1, 12),
}


def in_period(amount: float, unit: str, period: str) -> Fraction | None:
    """An amount per `unit` expressed per `period`; None for a unit that is not a standard period."""
    if unit not in PER_MONTH or period not in PER_MONTH:
        return None
    monthly = Fraction(str(amount)) * PER_MONTH[unit]  # type: ignore[index]
    return monthly / PER_MONTH[period]  # type: ignore[index]


class SalaryRecord(BaseModel):
    model_config = ConfigDict(
        extra="forbid", validate_by_name=True, validate_by_alias=True, serialize_by_alias=True
    )
    state: SalaryState = "specified"
    from_: float | None = Field(default=None, alias="from")
    to: float | None = None
    currency: str | None = None  # ISO 4217.
    # A standard period, or the source's own unit kept as is ("shift", "fly_in_fly_out", "service"…).
    unit: str | None = None
    # The amounts in every standard period; empty for a source unit or an unknown one.
    converted: dict[str, dict[str, float | None]] = Field(default_factory=dict)
    total: bool = False  # «Совокупно»: the amount includes bonuses.
    gross: bool | None = None
    bonus: bool = False  # A bonus is mentioned: the label «есть премии».
    original: JsonValue = None  # The record as the source wrote it.
    evidence: Evidence | None = None


def salary_record(
    low: float | None,
    high: float | None,
    *,
    currency: str | None = "RUB",
    unit: str | None = "month",
    total: bool = False,
    gross: bool | None = None,
    bonus: bool = False,
    original: JsonValue = None,
    evidence: Evidence | None = None,
) -> SalaryRecord:
    """A specified record with its conversions into every standard period."""
    converted: dict[str, dict[str, float | None]] = {}
    if unit in PER_MONTH:
        for period in STANDARD_PERIODS:
            converted[period] = {
                key: None if value is None else round(float(in_period(value, unit, period) or 0), 2)
                for key, value in (("from", low), ("to", high))
            }
    return SalaryRecord.model_validate(
        {
            "from": low,
            "to": high,
            "currency": currency,
            "unit": unit,
            "converted": converted,
            "total": total,
            "gross": gross,
            "bonus": bonus,
            "original": original,
            "evidence": evidence,
        }
    )


def legacy_salary(raw: dict[str, Any], hidden: bool) -> list[SalaryRecord]:
    """TRANSITIONAL (stage N+1): the former free-form dict (`from`, `to`, `currencyCode`, `period`, `gross`)
    of stored vacancies and ATS parsers. HH's `noCompensation` is «not specified», not hidden."""
    evidence = Evidence(enricher="legacy", version="1", method="structured", field="salary", raw=raw or None)
    if "noCompensation" in raw:
        return [SalaryRecord(state="not_specified", original=raw, evidence=evidence)]
    low, high = raw.get("from"), raw.get("to")
    numbers = [v for v in (low, high) if isinstance(v, (int, float))]
    if not numbers:
        return [SalaryRecord(state="hidden", original=raw or None, evidence=evidence)] if hidden else []
    period = raw.get("period")
    return [
        salary_record(
            low if isinstance(low, (int, float)) else None,
            high if isinstance(high, (int, float)) else None,
            currency=raw.get("currencyCode") if isinstance(raw.get("currencyCode"), str) else None,
            unit=period if period in STANDARD_PERIODS else None,
            gross=raw.get("gross") if isinstance(raw.get("gross"), bool) else None,
            original=raw,
            evidence=evidence,
        )
    ]
