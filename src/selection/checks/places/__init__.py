"""Brief places: geography, work format and relocation of one complete option.

An option is one vacancy place with its work format, against one brief place with its own
minimum format and relocation requirement. The three conditions are judged on the same option:
a format or relocation cannot be borrowed from another place (spec 3.3, plan §11).

    model.py       what both readings share: Match, the Geography protocol, place labels
    geography.py   places with facts: the place against the brief place, and exclusions
    legacy.py      places without facts (HH, ATS) until their stages
    format.py      the option's format against the minimum
    relocation.py  the place's relocation fact against the requirement
"""

from dataclasses import dataclass

from src.models.filters import SearchPlace, VacancyPlace, WorkFormat
from src.models.vacancy import Vacancy
from src.selection.checks.base import Check, Context, Verdict
from src.selection.checks.places.format import format_ok
from src.selection.checks.places.geography import FactGeography, has_facts
from src.selection.checks.places.legacy import LegacyGeography
from src.selection.checks.places.model import Geography, Match, label
from src.selection.checks.places.relocation import relocation_ok, relocation_reason

FORMAT_REASON = "Сравнение свободы формата с выбранным минимумом для варианта с подходящей географией"
CONDITIONS = ("locations", "minimum_work_format", "relocation_support")


@dataclass(frozen=True)
class Option:
    place: VacancyPlace
    geography: Match
    format_ok: bool | None
    relocation_ok: bool | None
    minimum: WorkFormat | None
    requires_relocation: bool

    def score(self) -> tuple[bool, bool, bool, int, int]:
        """Which option explains the vacancy best, criteria from the strongest:
        every condition passes; none fails; the geography is proven; most passes; fewest failures."""
        values = (self.geography.value, self.format_ok, self.relocation_ok)
        return (
            all(v is True for v in values),
            not any(v is False for v in values),
            self.geography.value is True,
            sum(v is True for v in values),
            -sum(v is False for v in values),
        )


@dataclass(frozen=True)
class Summary:
    geography: bool | None
    format_ok: bool | None
    relocation_ok: bool | None
    best: Option  # The option whose format and relocation are reported.
    explains: Option  # An option with the summed-up geography, named in its reason.


def _options(vacancy: Vacancy, context: Context) -> list[Option]:
    reading: Geography = (
        FactGeography(context.catalog) if has_facts(vacancy) else LegacyGeography(context.catalog)
    )
    # A condition the source cannot check is unknown already here, so it cannot sway the choice of
    # the option; filtering._report then reports it as unsupported.
    checked = {name: name not in context.unsupported for name in CONDITIONS}
    filters = context.filters
    exclusions = [p for p in vacancy.locations if p.excluded]
    wanted_places: list[SearchPlace | None] = list(filters.locations) or [None]
    options = []
    for mode, place in reading.alternatives(vacancy):
        for wanted in wanted_places:
            minimum, required = filters.location_conditions(wanted)
            geography = Match(True, "")
            if wanted is not None and checked["locations"]:
                geography = reading.decide(wanted, place, mode, exclusions)
            fmt = format_ok(mode, minimum) if checked["minimum_work_format"] or minimum is None else None
            relocation = (
                relocation_ok(place, required=required)
                if checked["relocation_support"] or not required
                else None
            )
            options.append(Option(place, geography, fmt, relocation, minimum, required))
    return options


def _any(values: list[bool | None]) -> bool | None:
    if True in values:
        return True
    return None if not values or None in values else False


def _summarize(options: list[Option]) -> Summary:
    viable = [o for o in options if o.geography.value is not False]
    if not viable:
        return Summary(False, None, None, options[0], options[0])
    best = max(viable, key=Option.score)
    geography = _any([o.geography.value for o in options])
    explains = max((o for o in options if o.geography.value is geography), key=Option.score)
    # An option of unknown geography proves nothing about format or relocation.
    unproven = best.geography.value is None
    fmt = None if unproven and best.format_ok is True else best.format_ok
    relocation = None if unproven and best.relocation_ok is True else best.relocation_ok
    return Summary(geography, fmt, relocation, best, explains)


def _verdicts(summary: Summary, has_places: bool) -> list[Verdict]:
    verdicts = []
    if has_places:
        where, basis = label(summary.explains.place), summary.explains.geography.basis
        reason = f"Место вакансии {where}: {basis}" if basis else where
        verdicts.append(Verdict("locations", summary.geography, reason, kind="hard"))
    if summary.best.minimum:
        verdicts.append(Verdict("minimum_work_format", summary.format_ok, FORMAT_REASON))
    if summary.best.requires_relocation:
        reason = relocation_reason(summary.relocation_ok, label(summary.best.place))
        verdicts.append(Verdict("relocation_support", summary.relocation_ok, reason))
    return verdicts


def run(vacancy: Vacancy, context: Context) -> list[Verdict]:
    return _verdicts(_summarize(_options(vacancy, context)), bool(context.filters.locations))


CHECK = Check(
    fields={
        "locations": frozenset({"locations"}),
        # Read from places with facts too; every source with fact places also declares work_formats.
        "minimum_work_format": frozenset({"work_formats"}),
        # A source declares it when its places carry the relocation fact (its normalizer sets it).
        "relocation_support": frozenset({"locations.relocation_support"}),
    },
    run=run,
)
