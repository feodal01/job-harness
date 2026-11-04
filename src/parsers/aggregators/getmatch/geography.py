"""Getmatch native geography: the five static `l` zones of the public offer feed.

Verified on 2026-11-02 (docs/getmatch-geography-plan.md §2): the feed accepts only these
values, combines repeated `l` with OR and silently ignores any unknown value.

    moscow      office/hybrid in the city of Moscow
    saints_p    office/hybrid in Saint Petersburg
    regions_ru  office/hybrid in any other Russian city
    relocate    office/hybrid outside Russia OR relocation paid by the company to another country
                (not "relocation help": 27 of 49 had no help; help to Moscow is excluded, 2026-11-03)
    remote      any remote job; its allowed geography is NOT filtered (Russia, world, Armenia...)

Preparation is split (StagedSearch): this module translates only the place, the parser's
work-format step only the accepted formats, and the parser's `_combine` joins them —
the one place where Getmatch couples geography and format, because its zones do.

    place  → zone of office/hybrid jobs there:  Moscow → moscow, Kazan → regions_ru,
             Russia → moscow+saints_p+regions_ru, Armenia/Yerevan → relocate
    format → accepted formats: minimum office → office, hybrid, remote_country, remote_worldwide
    combine: office or hybrid accepted → the place zone; remote accepted → `remote`
             (remote geography is not filtered: broader)

The minimum work format means "this format or a more flexible one", so an office minimum
also asks for `remote`. Repeated `l` is a union, never an intersection: adding `remote`
does not narrow office jobs (live 2026-11: regions_ru 68 + remote 398 → both 447).

    Kazan,  minimum office/hybrid  → l=regions_ru&l=remote   (zone: broader)
    Kazan,  minimum remote_*       → l=remote                (zone not needed)
    Moscow, minimum office         → l=moscow&l=remote       (zone: exact)
    Russia, whole country          → l=moscow&l=saints_p&l=regions_ru&l=remote (zone: exact)
    Yerevan or Armenia             → l=relocate&l=remote     (zone: broader)

Hybrid cannot be separated from office (one zone), and remote geography is never exact:
both are left to local filtering. Places with equal zones (Kazan, Samara) share one request.
"""

import asyncio
import re
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from src.models.filters import SearchPlace
from src.network.client import FetchError
from src.parsers.interfaces import Fetch, Prepared, RegionGeography
from src.parsers.models import Place

FORM_URL = "https://getmatch.ru/vacancies"
REMOTE = "remote"

# Approved exception to the no-manual-mapping rule (docs/getmatch-geography-plan.md §3):
# Getmatch publishes no directory, only five static zones; two of them name single cities.
HOME_COUNTRY = "RU"
CITY_ZONES = {524901: "moscow", 498817: "saints_p"}  # GeoNames: Moscow, Saint Petersburg
HOME_REST = "regions_ru"
ABROAD = "relocate"
HOME_ZONES = (*CITY_ZONES.values(), HOME_REST)

# Form checkbox ids carry the zone value; the remote checkbox has none.
FORM_VALUE = re.compile(r"vacancies_list_filters_location_nested_\d+_([a-z_]+)")


@dataclass(frozen=True)
class FormSnapshot:
    values: frozenset[str]
    fetched_at: str
    expires: float


class GetmatchGeography(RegionGeography):
    mode: Literal["regions"] = "regions"
    options = (
        Place(id="moscow", name="Офис или гибрид в Москве"),
        Place(id="saints_p", name="Офис или гибрид в Санкт-Петербурге"),
        Place(id="regions_ru", name="Офис или гибрид в других городах России"),
        Place(id="relocate", name="Офис или гибрид в других странах (релокация)"),
        Place(id=REMOTE, name="Удалённо"),
    )

    def __init__(self, ttl: float = 86400) -> None:
        self.ttl = ttl
        self.snapshot: FormSnapshot | None = None
        self.lock = asyncio.Lock()

    async def form(self, fetch: Fetch) -> FormSnapshot:
        """Zone values present in the live vacancy form; cached per process for `ttl` seconds."""
        async with self.lock:
            if self.snapshot is None or self.snapshot.expires <= time.monotonic():
                values = frozenset(FORM_VALUE.findall(await fetch(FORM_URL)))
                if not values:
                    raise FetchError("parse_error", "Getmatch vacancy form has no location values")
                self.snapshot = FormSnapshot(
                    values,
                    datetime.now(UTC).isoformat(timespec="seconds"),
                    time.monotonic() + self.ttl,
                )
            return self.snapshot

    async def zone(self, fetch: Fetch, place: SearchPlace) -> Prepared:
        """Zones of office/hybrid jobs in the place; condition `locations`, params `l`."""
        if place.country != HOME_COUNTRY:
            values: tuple[str, ...] = (ABROAD,)
            status: Literal["exact", "broader"] = "broader"
            reason: str | None = "Getmatch объединяет все страны кроме России в одну зону"
        elif place.scope == "country":
            values, status, reason = HOME_ZONES, "exact", None
        elif place.geoname_id in CITY_ZONES:
            values, status, reason = (CITY_ZONES[place.geoname_id],), "exact", None
        else:
            values, status = (HOME_REST,), "broader"
            reason = "Getmatch объединяет города России кроме Москвы и Петербурга в одну зону"
        form = await self.form(fetch)
        missing = [v for v in values if v not in form.values]
        origin = f"form {FORM_URL} {form.fetched_at}"
        if missing:
            reason = (
                f"Значения {', '.join(missing)} отсутствуют в форме Getmatch; "
                "сервер молча проигнорировал бы их"
            )
            return Prepared("locations", "local", "error", reason=reason, origin=origin)
        mode: Literal["native", "local"] = "native" if status == "exact" else "local"
        return Prepared("locations", mode, status, {"l": values}, reason=reason, origin=origin)
