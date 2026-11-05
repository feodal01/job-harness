"""Getmatch `salary_*` fields → a salary record (spec: docs/brief-parameters/salary-minimum-check.md).

Every Getmatch salary is per month: the site states it in each label («₽/мес», «$/month» in all labels of
the 04.11.2026 feed) and in its form («Зарплата, ₽/мес»). `salary_hidden` — Getmatch knows the salary and
filters by it but does not show it; its label is kept as the original. `salary_is_total` («совокупно»)
includes bonuses. `salary_by_our_version` is Getmatch's own estimate, marked `inferred`.
"""

from typing import Any

from src.models.facts import Evidence
from src.models.salary import SalaryRecord, salary_record

FIELDS = ("salary_display_from", "salary_display_to", "salary_currency", "salary_hidden")


def salary_records(source_data: dict[str, Any], enricher: str, version: str) -> list[SalaryRecord] | None:
    """The record; None for a vacancy stored before Getmatch salary fields were kept."""
    if not any(key in source_data for key in FIELDS):
        return None
    raw = {k: v for k, v in source_data.items() if k.startswith("salary_")}
    estimate = source_data.get("salary_by_our_version") is True
    evidence = Evidence(
        enricher=enricher,
        version=version,
        method="inferred" if estimate else "structured",
        field="salary_*",
        raw=raw,
    )
    label = source_data.get("salary_description") or None
    if source_data.get("salary_hidden") is True:
        return [SalaryRecord(state="hidden", original=label, evidence=evidence)]
    low, high = (
        source_data.get(k) if isinstance(source_data.get(k), (int, float)) else None
        for k in ("salary_display_from", "salary_display_to")
    )
    if low is None and high is None:
        return [SalaryRecord(state="not_specified", original=label, evidence=evidence)]
    taxes = source_data.get("salary_taxes")
    currency = source_data.get("salary_currency")
    return [
        salary_record(
            low,
            high,
            currency=currency if isinstance(currency, str) else None,
            unit="month",
            total=source_data.get("salary_is_total") is True,
            gross=taxes == "gross" if taxes in {"net", "gross"} else None,
            original=label or raw,
            evidence=evidence,
        )
    ]
