"""HH `compensation` → salary records (spec: docs/brief-parameters/salary-minimum-check.md).

One record per place HH gives a salary: the card's `compensation` and the listing's, which the card no
longer overwrites (`listing.compensation`). Units come from HH's dictionary `salary_range_mode`
(api.hh.ru/dictionaries, 05.11.2026): MONTH «За месяц», HOUR «За час» are standard periods; SHIFT «За смену»,
FLY_IN_FLY_OUT «За вахту», SERVICE «За услугу» are HH's own units, kept as they are. The amount per unit
is `perModeFrom` / `perModeTo`. `noCompensation` means the employer did not state a salary.
"""

from typing import Any

from src.models.facts import Evidence
from src.models.salary import SalaryRecord, salary_record

UNITS = {
    "MONTH": "month",
    "HOUR": "hour",
    "SHIFT": "shift",
    "FLY_IN_FLY_OUT": "fly_in_fly_out",
    "SERVICE": "service",
}
# HH writes the rouble as RUR (the code withdrawn from ISO 4217 in 1998); every other HH code is ISO.
CURRENCIES = {"RUR": "RUB"}


def _number(raw: dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        if isinstance(raw.get(key), (int, float)):
            return float(raw[key])
    return None


def salary_records(source_data: dict[str, Any], enricher: str, version: str) -> list[SalaryRecord] | None:
    """Records of the card and the listing; None when HH gave no compensation at all."""
    listing = source_data.get("listing")
    places = [
        ("compensation", source_data.get("compensation")),
        ("listing.compensation", listing.get("compensation") if isinstance(listing, dict) else None),
    ]
    found = [(field, raw) for field, raw in places if isinstance(raw, dict) and raw]
    if not found:
        return None
    records = []
    for field, raw in found:
        evidence = Evidence(enricher=enricher, version=version, method="structured", field=field, raw=raw)
        if "noCompensation" in raw:
            records.append(SalaryRecord(state="not_specified", original=raw, evidence=evidence))
            continue
        # The amount per `mode` is perModeFrom/perModeTo; for other modes than MONTH HH's from/to hold its
        # own recomputation (live listing 139040588: 400–600 per hour, from/to 6 000–144 000).
        low, high = (_number(raw, f"perMode{k.title()}", k) for k in ("from", "to"))
        if low is None and high is None:
            continue
        code = raw.get("currencyCode")
        records.append(
            salary_record(
                low,
                high,
                currency=CURRENCIES.get(code, code) if isinstance(code, str) else None,
                unit=UNITS.get(raw.get("mode") or ""),
                gross=raw.get("gross") if isinstance(raw.get("gross"), bool) else None,
                original=raw,
                evidence=evidence,
            )
        )
    return records
