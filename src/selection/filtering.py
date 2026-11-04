"""The local filter: brief conditions against vacancy facts, the same for feed and card data.

Each condition is one module in `checks/`; this module only runs them and sums up the outcome
(docs/vacancy-facts-plan.md §11). Sources never filter and the filter never parses text.
"""

from collections.abc import Sequence
from typing import Literal

from src.geography.catalog import GeographyCatalog
from src.models.filters import FILTER_NAMES, FilterCheck, FilterResult, SearchFilters, Status
from src.models.vacancy import Vacancy
from src.selection.checks import CHECKS, FILTER_FIELDS, Context, Verdict

# Report order: the profile, then the brief's conditions in their declared order.
ORDER = {name: i for i, name in enumerate(("profile", *FILTER_NAMES))}
UNSUPPORTED = "Источник не поддерживает проверку этого условия"


def _status(value: bool | None) -> Status:
    return "unknown" if value is None else "accepted" if value else "rejected"


def _report(verdict: Verdict, unsupported: frozenset[str]) -> FilterCheck:
    supported = verdict.field not in unsupported
    return FilterCheck(
        field=verdict.field,
        status=_status(verdict.value) if supported else "unknown",
        reason=verdict.reason if supported else UNSUPPORTED,
        kind=verdict.kind,
        supported=supported,
    )


def evaluate(
    vacancy: Vacancy,
    filters: SearchFilters,
    *,
    catalog: GeographyCatalog,
    complete: bool = False,
    queries: Sequence[str] = (),
    unsupported: frozenset[str] = frozenset(),
) -> FilterResult:
    context = Context(filters, catalog, complete, tuple(queries), unsupported)
    verdicts = sorted(
        (v for check in CHECKS for v in check.run(vacancy, context)), key=lambda v: ORDER[v.field]
    )
    checks = [_report(v, unsupported) for v in verdicts]
    status: Literal["accepted", "maybe", "rejected"] = "accepted"
    if any(c.status == "rejected" and c.kind == "hard" for c in checks):
        status = "rejected"
    elif any(c.status == "rejected" and c.kind == "soft" for c in checks):
        status = "maybe"
    return FilterResult(status=status, checks=checks)


__all__ = ["FILTER_FIELDS", "evaluate"]
