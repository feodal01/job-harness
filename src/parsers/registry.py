"""The one registry of vacancy sources: every kind the service knows, supported or not.

A kind is either a fixed source (one adapter for the whole service, like HH) or a board platform
(one adapter per company board from the company catalog, like Greenhouse). Catalog rows name their
kind in `provider`, so a new company needs no code and a new platform is one line in `KINDS`.
"""

import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace

from src.core.config import Settings
from src.geography.catalog import GeographyCatalog
from src.parsers.aggregators.getmatch.parser import create_adapter as getmatch_adapter
from src.parsers.aggregators.hh.parser import create_adapter as hh_adapter
from src.parsers.ats.ashby.parser import create_adapter as ashby
from src.parsers.ats.bamboohr.parser import create_adapter as bamboohr
from src.parsers.ats.breezy.parser import create_adapter as breezy
from src.parsers.ats.comeet.parser import create_adapter as comeet
from src.parsers.ats.dreamjob.parser import create_adapter as dreamjob
from src.parsers.ats.greenhouse.parser import create_adapter as greenhouse
from src.parsers.ats.huntflow.parser import create_adapter as huntflow
from src.parsers.ats.icims.parser import create_adapter as icims
from src.parsers.ats.jazzhr.parser import create_adapter as jazzhr
from src.parsers.ats.jobvite.parser import create_adapter as jobvite
from src.parsers.ats.join.parser import create_adapter as join
from src.parsers.ats.lever.parser import create_adapter as lever
from src.parsers.ats.models import Board
from src.parsers.ats.personio.parser import create_adapter as personio
from src.parsers.ats.recruitee.parser import create_adapter as recruitee
from src.parsers.ats.smartrecruiters.parser import create_adapter as smartrecruiters
from src.parsers.ats.successfactors.parser import create_adapter as successfactors
from src.parsers.ats.taleo.parser import create_adapter as taleo
from src.parsers.ats.teamtailor.parser import create_adapter as teamtailor
from src.parsers.ats.workable.parser import create_adapter as workable
from src.parsers.ats.workday.parser import create_adapter as workday
from src.parsers.ats.ycombinator.parser import create_adapter as ycombinator
from src.parsers.interfaces import Fetch, SourceAdapter, SourceCapabilities, SourceGroup

BoardBuilder = Callable[[Board], SourceAdapter]
FixedBuilder = Callable[[Settings, GeographyCatalog | None], SourceAdapter]
CountryLookup = Callable[[SourceAdapter, Fetch, GeographyCatalog], Awaitable[frozenset[str]]]


@dataclass(frozen=True)
class Coverage:
    """Countries a source can return vacancies for: ISO codes, or None for every country."""

    countries: frozenset[str] | None
    known: bool

    def covers(self, wanted: frozenset[str]) -> bool:
        # A source with unknown coverage still takes part: dropping it would hide vacancies.
        return self.countries is None or not wanted or bool(self.countries & wanted)


WORLD = Coverage(None, known=True)
UNKNOWN = Coverage(None, known=False)


async def hh_countries(adapter: SourceAdapter, fetch: Fetch, catalog: GeographyCatalog) -> frozenset[str]:
    """HH's own country list resolved to ISO codes through CLDR, as HH search resolves them."""
    countries = await adapter.geography.countries(fetch)  # type: ignore[union-attr]
    codes = (catalog.country(country.name) for country in countries)
    return frozenset(code.value for code in codes if code.value)


@dataclass(frozen=True)
class SourceKind:
    key: str  # a fixed source id, or the catalog `provider` of a board platform
    name: str
    group: SourceGroup
    fixed: FixedBuilder | None = None
    board: BoardBuilder | None = None
    example: str | None = None  # a board URL of this platform, to read the platform's capabilities
    coverage: Coverage | CountryLookup = UNKNOWN
    reason: str | None = None  # why the kind is not supported yet

    @property
    def supported(self) -> bool:
        return self.fixed is not None or self.board is not None


def _platform(key: str, name: str, build: BoardBuilder, example: str) -> SourceKind:
    return SourceKind(key, name, "company_site", board=build, example=example)


def _planned(key: str, name: str, group: SourceGroup, reason: str = "Парсера пока нет") -> SourceKind:
    return SourceKind(key, name, group, reason=reason)


KINDS: tuple[SourceKind, ...] = (
    SourceKind(
        "hh_ru",
        "HH",
        "aggregator",
        fixed=lambda settings, geography: hh_adapter(settings.sources.hh_ru, geography),
        coverage=hh_countries,
    ),
    SourceKind(
        "getmatch",
        "Getmatch",
        "it_board",
        fixed=lambda settings, _: getmatch_adapter(settings.sources.getmatch),
        coverage=WORLD,  # Russian zones, every other country through the relocation zone
    ),
    _planned("habr_career", "Хабр Карьера", "it_board"),
    _planned("wellfound", "Wellfound", "it_board"),
    _planned("linkedin", "LinkedIn", "aggregator"),
    _planned("telegram", "Telegram-каналы", "telegram", "Telegram пока не поддерживается"),
    _platform("ats:ashby", "Ashby", ashby, "https://jobs.ashbyhq.com/example"),
    _platform("ats:bamboohr", "BambooHR", bamboohr, "https://example.bamboohr.com/careers"),
    _platform("ats:breezy", "Breezy HR", breezy, "https://example.breezy.hr/"),
    _platform("ats:comeet", "Comeet", comeet, "https://www.comeet.com/jobs/example/AB.C12"),
    _platform("ats:dreamjob", "DreamJob", dreamjob, "https://dreamjob.ru/employers/1"),
    _platform("ats:greenhouse", "Greenhouse", greenhouse, "https://job-boards.greenhouse.io/example"),
    _platform("ats:huntflow", "Huntflow", huntflow, "https://example.huntflow.io/"),
    _platform("ats:icims", "iCIMS", icims, "https://careers-example.icims.com/jobs/search"),
    _platform("ats:jazzhr", "JazzHR", jazzhr, "https://example.applytojob.com/apply/jobs"),
    _platform("ats:jobvite", "Jobvite", jobvite, "https://jobs.jobvite.com/example"),
    _platform("ats:join", "JOIN", join, "https://join.com/companies/example"),
    _platform("ats:lever", "Lever", lever, "https://jobs.lever.co/example"),
    _platform("ats:personio", "Personio", personio, "https://example.jobs.personio.de/"),
    _platform("ats:recruitee", "Recruitee", recruitee, "https://example.recruitee.com/"),
    _platform(
        "ats:smartrecruiters",
        "SmartRecruiters",
        smartrecruiters,
        "https://careers.smartrecruiters.com/Example",
    ),
    _platform(
        "ats:successfactors",
        "SAP SuccessFactors",
        successfactors,
        "https://career012.successfactors.eu/career?company=example",
    ),
    _platform(
        "ats:taleo",
        "Taleo",
        taleo,
        "https://phf.tbe.taleo.net/phf01/ats/careers/v2/searchResults?org=EXAMPLE&cws=1",
    ),
    _platform("ats:teamtailor", "Teamtailor", teamtailor, "https://example.teamtailor.com/jobs"),
    _platform("ats:workable", "Workable", workable, "https://apply.workable.com/example/"),
    _platform("ats:workday", "Workday", workday, "https://example.wd5.myworkdayjobs.com/en-US/Careers"),
    _platform(
        "ats:ycombinator", "Y Combinator", ycombinator, "https://www.ycombinator.com/companies/example/jobs"
    ),
    _planned("ats:deel", "Deel", "company_site"),
    _planned("ats:dover", "Dover", "company_site"),
    _planned("ats:gem", "Gem", "company_site"),
    _planned("ats:gupy", "Gupy", "company_site"),
    _planned("ats:manatal", "Manatal", "company_site"),
    _planned("ats:paycom", "Paycom", "company_site"),
    _planned("ats:pinpoint", "Pinpoint", "company_site"),
    _planned("ats:rippling", "Rippling", "company_site"),
    _planned("web", "Своя карьерная страница", "company_site", "Общего парсера карьерных страниц пока нет"),
    _planned(
        "hh_employer",
        "Работодатель на HH",
        "company_site",
        "Поиск по работодателю через HH пока не подключён",
    ),
)

# Catalog `provider` values that name a kind under another key.
CATALOG_ALIASES = {"hh_ru": "hh_employer"}


@dataclass(frozen=True)
class Unsupported:
    kind: str
    reason: str


class SourceRegistry:
    """Builds fixed sources once, and company boards from catalog rows when they are needed."""

    def __init__(
        self,
        settings: Settings,
        geography: GeographyCatalog | None = None,
        kinds: tuple[SourceKind, ...] = KINDS,
    ) -> None:
        keys = [kind.key for kind in kinds]
        if duplicates := sorted({key for key in keys if keys.count(key) > 1}):
            raise ValueError(f"Duplicate source kinds: {', '.join(duplicates)}")
        self.kinds = {kind.key: kind for kind in kinds}
        self.geography = geography
        self.fixed: dict[str, SourceAdapter] = {}
        self.capabilities: dict[str, SourceCapabilities] = {}
        self._coverage: dict[str, tuple[float, Coverage]] = {}
        for kind in kinds:
            if kind.fixed is not None:
                source = replace(kind.fixed(settings, geography), group=kind.group)
                if source.id != kind.key:
                    raise ValueError(f"Source kind {kind.key} builds {source.id}")
                self.fixed[source.id] = source
                self.capabilities[kind.key] = source.capabilities
            elif kind.board is not None:
                if kind.example is None:
                    raise ValueError(f"Board platform {kind.key} needs an example board")
                self.capabilities[kind.key] = kind.board(Board(kind.example, kind.name)).capabilities

    def kind_of(self, source_id: str) -> SourceKind | None:
        """The kind of a source id: `hh_ru` → hh_ru, `ats:greenhouse:abbyy` → ats:greenhouse."""
        if source_id in self.kinds:
            return self.kinds[source_id]
        prefix = ":".join(source_id.split(":", 2)[:2])
        kind = self.kinds.get(prefix)
        return kind if kind is not None and kind.board is not None else None

    def catalog_kind(self, provider: str) -> SourceKind | None:
        key = provider.strip().lower()
        return self.kinds.get(CATALOG_ALIASES.get(key, key))

    def board(self, provider: str, url: str, company: str, company_id: str) -> SourceAdapter | Unsupported:
        """A company board from one catalog row, or why it cannot be searched."""
        kind = self.catalog_kind(provider)
        if kind is None:
            return Unsupported(provider.strip().lower(), "Этот вид сайта пока не поддерживается")
        if kind.board is None:
            return Unsupported(kind.key, kind.reason or "Этот вид сайта пока не поддерживается")
        try:
            source = kind.board(Board(url.strip(), company.strip()))
        except ValueError:
            return Unsupported(kind.key, f"Адрес не похож на доску {kind.name}")
        return replace(source, group=kind.group, company_ids=(company_id,))

    def unsupported_parameters(self, source_id: str) -> frozenset[str] | None:
        """Conditions a source cannot check, by its id; None for a source the registry no longer knows."""
        kind = self.kind_of(source_id)
        capabilities = self.capabilities.get(kind.key) if kind else None
        if capabilities is None:
            return None
        return frozenset(k for k, mode in capabilities.query_parameters.items() if mode == "none")

    async def coverage(self, kind: SourceKind, fetch: Fetch | None, *, lookup: bool = True) -> Coverage:
        """Countries of a kind; a lookup that fails leaves the coverage unknown, never empty.

        Answers are kept for a while, failures for a short while, so a slow or blocked source
        does not delay every list of sources. Without `lookup` only a known answer is returned.
        """
        if isinstance(kind.coverage, Coverage):
            return kind.coverage
        cached = self._coverage.get(kind.key)
        if cached and cached[0] > time.monotonic():
            return cached[1]
        source = self.fixed.get(kind.key)
        if not lookup or source is None or fetch is None or self.geography is None:
            return UNKNOWN
        try:
            coverage, ttl = Coverage(await kind.coverage(source, fetch, self.geography), known=True), 86400.0
        except Exception:  # noqa: BLE001 - a lookup failure must not hide the source
            coverage, ttl = UNKNOWN, 300.0
        self._coverage[kind.key] = (time.monotonic() + ttl, coverage)
        return coverage


def build_sources(settings: Settings, catalog: GeographyCatalog | None = None) -> dict[str, SourceAdapter]:
    """Fixed adapters of the registry; `catalog` is the application's single geography catalog."""
    return SourceRegistry(settings, catalog).fixed
