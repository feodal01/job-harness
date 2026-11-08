"""Sources by filters: fixed sources from the registry and company boards read from the catalog now.

The catalog stays in Supabase: boards are read with the user's session for each answer and never
stored. Without a session, or when Supabase fails, fixed sources keep working and the answer says
why company sites are missing.
"""

import time
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field, replace

from src.core.errors import InvalidSearchError
from src.db.protocols import EventStore, ResourceStore
from src.models.sources import COMPANY_PREFIX, SourceGroup, SourceRules, company_id, unknown_sources
from src.parsers.interfaces import Fetch, SourceAdapter
from src.parsers.registry import CATALOG_ALIASES, UNKNOWN, Coverage, SourceKind, SourceRegistry, Unsupported
from src.schemas.companies import CompanyLink
from src.schemas.sources import CatalogState, SourceFilter
from src.services.auth import AuthError, LoginRequired

CatalogRows = Callable[..., Awaitable[list[CompanyLink]]]
GROUP_ORDER: tuple[SourceGroup, ...] = ("aggregator", "it_board", "company_site", "telegram")
NO_LOGIN = "Сайты компаний доступны после входа."
# Skipped-sources key for company sites the catalog could not give a run (no login, failure).
CATALOG_SKIP = "company_site"


@dataclass
class Entry:
    id: str
    name: str
    kind: SourceKind
    group: SourceGroup
    coverage: Coverage
    adapter: SourceAdapter | None = None
    reason: str | None = None
    company_ids: list[str] = field(default_factory=list)
    categories: list[str] = field(default_factory=list)  # industries of the catalog rows

    @property
    def supported(self) -> bool:
        return self.adapter is not None


def participation_of(entry: Entry, rules: SourceRules, countries: frozenset[str]) -> tuple[str, str]:
    """Whether the rules search this source, and why; the same order as resolve_rules applies them."""
    companies = set(entry.company_ids)
    excluded = [company_id(item) or item for item in rules.exclude]
    included = [company_id(item) or item for item in rules.include]
    if not entry.supported:
        return "unsupported", entry.reason or "Не поддерживается."
    if entry.id in excluded:
        return "excluded", "Исключён вами."
    if companies.intersection(excluded):
        return "excluded", "Компания исключена вами."
    if entry.id in included:
        return "included", "Добавлен вами."
    if companies.intersection(included):
        return "included", "В выбранных компаниях."
    if rules.mode == "selected":
        return "not_selected", "Нет в выбранных."
    if rules.groups is not None and entry.group not in rules.groups:
        if entry.group == "company_site":
            return "not_selected", "Нет в выбранных компаниях."
        return "not_selected", "Тип источника не выбран."
    if not entry.coverage.covers(countries):
        return "not_covered", "Не покрывает места брифа."
    return "included", "По правилам."


def rule_item(entry: Entry) -> str | None:
    """What include or exclude takes for this source: the company for company sites, else the id."""
    if not entry.supported:
        return None
    if entry.group == "company_site" and entry.company_ids:
        return COMPANY_PREFIX + entry.company_ids[0]
    return entry.id


@dataclass
class Resolved:
    """Adapters for one run, sources left out with the reason, and the catalog's answer."""

    sources: dict[str, SourceAdapter]
    skipped: dict[str, str]
    catalog: CatalogState
    countries: list[str] = field(default_factory=list)
    excluded: list[str] = field(default_factory=list)


def _merged(states: list[CatalogState]) -> CatalogState:
    """One catalog answer for a run: a failure wins, then ok, else the catalog was not asked."""
    for state in states:
        if state.status not in {"ok", "not_requested"}:
            return state
    return next((s for s in states if s.status == "ok"), CatalogState(status="not_requested"))


class SourceService:
    def __init__(
        self,
        registry: SourceRegistry,
        resources: ResourceStore,
        events: EventStore,
        catalog: CatalogRows | None = None,
        fetch: Callable[[str], Fetch] | None = None,
    ) -> None:
        self.registry, self.resources, self.event_store = registry, resources, events
        self.catalog, self.fetch = catalog, fetch

    # --- Answers -----------------------------------------------------------------------------

    async def list_sources(self, filters: SourceFilter, page: int = 1, page_size: int = 50) -> dict:
        entries, catalog = await self._entries(filters)
        start = (page - 1) * page_size
        return {
            "items": [self._describe(entry) for entry in entries[start : start + page_size]],
            "total": len(entries),
            "page": page,
            "page_size": page_size,
            "catalog": catalog.model_dump(),
        }

    async def counts(self, filters: SourceFilter) -> dict:
        entries, catalog = await self._entries(filters)
        groups = [
            {
                "group": group,
                "supported": sum(1 for e in entries if e.group == group and e.supported),
                "unsupported": sum(1 for e in entries if e.group == group and not e.supported),
            }
            for group in GROUP_ORDER
        ]
        return {
            "groups": groups,
            "supported": sum(1 for e in entries if e.supported),
            "catalog": catalog.model_dump(),
        }

    async def resolve(self, filters: SourceFilter) -> Resolved:
        """Adapters of every supported source the filters select."""
        entries, catalog = await self._entries(filters.model_copy(update={"status": "supported"}))
        sources = {e.id: e.adapter for e in entries if e.adapter is not None}
        return Resolved(sources, {}, catalog)

    def check_rules(self, rules: SourceRules) -> None:
        """Refuse rule items that name no searchable source; companies are checked at run start."""
        if unknown := unknown_sources(rules, self.registry.searchable):
            raise InvalidSearchError(f"Unknown source: {', '.join(unknown)}")

    async def resolve_rules(self, rules: SourceRules, countries: Iterable[str]) -> Resolved:
        """The run's sources from the brief's rules, read from the registry and the catalog now.

        Boards missing because of the catalog (no login, Supabase failure) are skipped with the reason;
        everything else still runs. See docs/brief-parameters/source-rules.md.
        """
        self.check_rules(rules)
        wanted = sorted({code.upper() for code in countries})
        sources: dict[str, SourceAdapter] = {}
        skipped: dict[str, str] = {}
        states: list[CatalogState] = []
        if rules.mode == "auto":
            entries, catalog = await self._entries(
                SourceFilter(groups=rules.groups, countries=wanted, status="supported")
            )
            sources.update({e.id: e.adapter for e in entries if e.adapter is not None})
            states.append(catalog)
            if catalog.status not in {"ok", "not_requested"}:
                skipped[CATALOG_SKIP] = catalog.message or NO_LOGIN
        ids = [item for item in rules.include if company_id(item) is None]
        if ids:
            explicit = await self.resolve_ids(ids)
            sources.update(explicit.sources)
            skipped.update(explicit.skipped)
            states.append(explicit.catalog)
        companies = [c for item in rules.include if (c := company_id(item)) is not None]
        if companies:
            boards, missing, catalog = await self._company_boards(companies)
            sources.update(boards)
            skipped.update(missing)
            states.append(catalog)
        excluded_ids = {item for item in rules.exclude if company_id(item) is None}
        excluded_companies = {c for item in rules.exclude if (c := company_id(item)) is not None}
        if companies and excluded_companies:
            # Boards read for included companies carry only those companies; a board an excluded
            # company shares with them is found by reading the excluded companies' rows too.
            rows, catalog = await self._catalog(ids=sorted(excluded_companies))
            excluded_ids |= {entry.id for entry in self._boards(rows)}
            states.append(catalog)  # a failed read shows in the run, like the others
        excluded = [
            key
            for key, adapter in sources.items()
            if key in excluded_ids or excluded_companies.intersection(adapter.company_ids)
        ]
        for key in excluded:
            del sources[key]
        return Resolved(sources, skipped, _merged(states), wanted, excluded)

    async def _company_boards(
        self, companies: list[str]
    ) -> tuple[dict[str, SourceAdapter], dict[str, str], CatalogState]:
        """Every supported board of the companies; companies without one are skipped with the reason."""
        rows, catalog = await self._catalog(ids=companies)
        by_company: dict[str, list[Entry]] = {}
        for entry in self._boards(rows):
            for company in entry.company_ids:
                by_company.setdefault(company, []).append(entry)
        boards: dict[str, SourceAdapter] = {}
        missing: dict[str, str] = {}
        for company in companies:
            entries = by_company.get(company, [])
            found = {e.id: e.adapter for e in entries if e.adapter is not None}
            if found:
                boards.update(found)
            elif catalog.status != "ok":
                missing[COMPANY_PREFIX + company] = catalog.message or NO_LOGIN
            elif entries:
                reasons = "; ".join(dict.fromkeys(e.reason or "" for e in entries if e.reason))
                missing[COMPANY_PREFIX + company] = f"У компании нет поддерживаемой доски: {reasons}"
            else:
                missing[COMPANY_PREFIX + company] = "Этой компании больше нет в каталоге компаний."
        return boards, missing, catalog

    async def resolve_ids(self, ids: Iterable[str]) -> Resolved:
        """Adapters for explicit source ids; boards come from the catalog, unknown fixed ids are errors."""
        wanted = list(dict.fromkeys(ids))
        unknown = [source_id for source_id in wanted if not self.registry.searchable(source_id)]
        if unknown:
            raise InvalidSearchError(f"Unknown source: {', '.join(unknown)}")
        sources = {s: self.registry.fixed[s] for s in wanted if s in self.registry.fixed}
        boards = [s for s in wanted if s not in self.registry.fixed]
        if not boards:
            return Resolved(sources, {}, CatalogState(status="not_requested"))
        providers = {kind.key for b in boards if (kind := self.registry.kind_of(b)) is not None}
        rows, catalog = await self._catalog(providers=self._catalog_providers(providers))
        found = {e.id: e.adapter for e in self._boards(rows)}
        skipped: dict[str, str] = {}
        for board in boards:
            if (adapter := found.get(board)) is not None:
                sources[board] = adapter
            elif catalog.status != "ok":
                skipped[board] = catalog.message or NO_LOGIN
            else:
                skipped[board] = "Этой доски больше нет в каталоге компаний."
        return Resolved(sources, skipped, catalog)

    async def preview(self, rules: SourceRules, countries: Iterable[str]) -> dict:
        """What the rules give for these countries now: counts by type and every rule item named.

        The catalog is read once and the run's own resolution answers from it, so the preview and
        the run cannot disagree. Nothing left to search is an answer of zero, not an error.
        """
        self.check_rules(rules)
        wanted = sorted({code.upper() for code in countries})
        rows, error = await self._read_catalog()
        local = self._answering_from(rows, error)
        pool, catalog = await local._entries(SourceFilter(countries=wanted, status="all"))
        resolved = await local.resolve_rules(rules, wanted)
        selected = [adapter.group for adapter in resolved.sources.values()]
        groups = [
            {
                "group": group,
                "available": sum(1 for e in pool if e.group == group and e.supported),
                "selected": selected.count(group),
            }
            for group in GROUP_ORDER
        ]
        if self.catalog is not None and error is None:
            catalog = CatalogState(status="ok")
        names = {str(row.id): row.name for row in rows}
        known = {e.id: e for e in pool}
        return {
            "countries": wanted,
            "groups": groups,
            "total": len(resolved.sources),
            "excluded": len(resolved.excluded),
            "builtin": [id for id, adapter in resolved.sources.items() if adapter.group != "company_site"],
            "requires_query": bool(resolved.sources)
            and all(adapter.requires_query for adapter in resolved.sources.values()),
            "items": [
                *(self._included_item(item, resolved, names, known) for item in rules.include),
                *(self._excluded_item(item, names, known) for item in rules.exclude),
            ],
            "catalog": {
                **catalog.model_dump(),
                "rows": len(rows) if catalog.status == "ok" else None,
            },
        }

    async def search(
        self,
        rules: SourceRules,
        countries: Iterable[str],
        name: str | None = None,
        groups: list[SourceGroup] | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> dict:
        """Sources by name, each with whether the rules search it and the rule item to change that."""
        self.check_rules(rules)
        wanted = frozenset(code.upper() for code in countries)
        entries, catalog = await self._entries(SourceFilter(name=name, groups=groups, status="all"))
        for entry in entries:
            if entry.group != "company_site":
                entry.coverage = await self.registry.coverage(
                    entry.kind, self._fetch(entry.kind.key), lookup=bool(wanted)
                )
        start = (page - 1) * page_size
        items = []
        for entry in entries[start : start + page_size]:
            participation, reason = participation_of(entry, rules, wanted)
            items.append(
                {
                    **self._describe(entry),
                    "participation": participation,
                    "participation_reason": reason,
                    "rule_item": rule_item(entry),
                }
            )
        return {
            "items": items,
            "groups": [
                {"group": group, "total": total}
                for group in GROUP_ORDER
                if (total := sum(1 for e in entries if e.group == group))
            ],
            "total": len(entries),
            "page": page,
            "page_size": page_size,
            "catalog": catalog.model_dump(),
        }

    def _included_item(
        self, item: str, resolved: Resolved, names: dict[str, str], known: dict[str, Entry]
    ) -> dict:
        company = company_id(item)
        if company is not None:
            described = {"item": item, "name": names.get(company), "group": "company_site"}
            if any(company in adapter.company_ids for adapter in resolved.sources.values()):
                return {**described, "status": "included"}
        else:
            described = {"item": item, **self._named(item, known)}
            if item in resolved.sources:
                return {**described, "status": "included"}
        if item in resolved.skipped:
            return {**described, "status": "missing", "reason": resolved.skipped[item]}
        return {**described, "status": "excluded", "reason": "Убран исключением компании."}

    def _excluded_item(self, item: str, names: dict[str, str], known: dict[str, Entry]) -> dict:
        company = company_id(item)
        described = (
            {"name": names.get(company), "group": "company_site"}
            if company is not None
            else self._named(item, known)
        )
        return {"item": item, **described, "status": "excluded"}

    def _named(self, source_id: str, known: dict[str, Entry]) -> dict:
        if source_id in self.registry.fixed:
            adapter = self.registry.fixed[source_id]
            return {"name": adapter.name, "group": adapter.group}
        entry = known.get(source_id)
        return (
            {"name": entry.name, "group": entry.group} if entry else {"name": None, "group": "company_site"}
        )

    async def _read_catalog(self) -> tuple[list[CompanyLink], Exception | None]:
        """The whole catalog for one answer, or why it could not be read."""
        if self.catalog is None:
            return [], None
        try:
            return await self.catalog(), None
        except (LoginRequired, AuthError) as error:
            return [], error

    def _answering_from(self, rows: list[CompanyLink], error: Exception | None) -> "SourceService":
        """This service answering catalog questions from rows already read, filtered like PostgREST."""

        async def read(
            *,
            name: str | None = None,
            ids: list[str] | None = None,
            providers: list[str] | None = None,
            **_: object,
        ) -> list[CompanyLink]:
            if error is not None:
                raise error
            wanted_ids = set(ids) if ids is not None else None
            wanted_providers = set(providers) if providers is not None else None
            return [
                row
                for row in rows
                if (name is None or name.casefold() in row.name.casefold())
                and (wanted_ids is None or str(row.id) in wanted_ids)
                and (wanted_providers is None or row.provider in wanted_providers)
            ]

        return SourceService(
            self.registry, self.resources, self.event_store, read if self.catalog else None, self.fetch
        )

    def events(self, after: int = 0) -> list[dict]:
        return self.event_store.events(None, after)

    def unsupported_parameters(self, source_id: str) -> frozenset[str] | None:
        return self.registry.unsupported_parameters(source_id)

    # --- Selection ---------------------------------------------------------------------------

    async def _entries(self, filters: SourceFilter) -> tuple[list[Entry], CatalogState]:
        countries = frozenset(code.upper() for code in filters.countries or ())
        name = (filters.name or "").casefold()
        kinds = [kind for kind in self.registry.kinds.values() if self._kind_matches(kind, filters)]
        entries: list[Entry] = []
        if not filters.company_ids:
            for kind in kinds:
                if kind.group == "company_site" or (name and name not in kind.name.casefold()):
                    continue  # company sites are catalog rows, listed below
                # The source's own country list is requested only when countries are asked for.
                coverage = await self.registry.coverage(kind, self._fetch(kind.key), lookup=bool(countries))
                if not coverage.covers(countries):
                    continue
                adapter = self.registry.fixed.get(kind.key)
                entries.append(
                    Entry(
                        kind.key,
                        adapter.name if adapter else kind.name,
                        kind,
                        kind.group,
                        coverage,
                        adapter,
                        None if adapter else kind.reason,
                    )
                )
        catalog = CatalogState(status="not_requested")
        if filters.groups is None or "company_site" in filters.groups:
            # Catalog rows are company sites whatever their platform; the kind only says how to search.
            providers = None
            if filters.kinds is not None or filters.supports or filters.status == "supported":
                matching = {
                    kind.key
                    for kind in self.registry.kinds.values()
                    if kind.fixed is None and self._kind_matches(kind, filters, group=False)
                }
                providers = self._catalog_providers(matching)
            if providers != []:
                rows, catalog = await self._catalog(
                    name=filters.name,
                    ids=[str(value) for value in filters.company_ids] if filters.company_ids else None,
                    providers=providers,
                )
                # Board coverage is unknown until the catalog keeps countries; such boards take part.
                entries.extend(self._boards(rows))
        entries = [e for e in entries if self._status_matches(e, filters)]
        entries.sort(key=lambda e: (GROUP_ORDER.index(e.group), not e.supported, e.name.casefold(), e.id))
        return entries, catalog

    def _kind_matches(self, kind: SourceKind, filters: SourceFilter, *, group: bool = True) -> bool:
        if group and filters.groups is not None and kind.group not in filters.groups:
            return False
        if filters.kinds is not None and kind.key not in filters.kinds:
            return False
        if filters.status == "supported" and not kind.supported:
            return False
        if filters.supports:
            capabilities = self.registry.capabilities.get(kind.key)
            if capabilities is None:
                return False
            modes = capabilities.query_parameters
            if any(modes.get(name, "none") == "none" for name in filters.supports):
                return False
        return True

    @staticmethod
    def _status_matches(entry: Entry, filters: SourceFilter) -> bool:
        return (
            filters.status == "all"
            or (filters.status == "supported" and entry.supported)
            or (filters.status == "unsupported" and not entry.supported)
        )

    def _catalog_providers(self, kinds: set[str]) -> list[str]:
        """Catalog `provider` values of the kinds, including aliases like hh_ru for HH employer pages."""
        aliases = [provider for provider, key in CATALOG_ALIASES.items() if key in kinds]
        return sorted(kinds | set(aliases))

    def _boards(self, rows: list[CompanyLink]) -> list[Entry]:
        """Catalog rows as sources; rows of one board become one source that keeps every company id."""
        boards: dict[str, Entry] = {}
        unsupported: list[Entry] = []
        for row in rows:
            built = self.registry.board(row.provider, row.url, row.name, str(row.id))
            if isinstance(built, Unsupported):
                kind = self.registry.kinds.get(built.kind) or SourceKind(
                    built.kind, built.kind, "company_site"
                )
                unsupported.append(
                    Entry(
                        f"catalog:{row.id}",
                        row.name,
                        kind,
                        "company_site",
                        UNKNOWN,
                        None,
                        built.reason,
                        [str(row.id)],
                        list(row.categories),
                    )
                )
                continue
            entry = boards.get(built.id)
            if entry is None:
                platform = self.registry.kind_of(built.id)
                assert platform is not None
                boards[built.id] = entry = Entry(
                    built.id, built.name, platform, "company_site", UNKNOWN, built, None, [str(row.id)]
                )
            elif str(row.id) not in entry.company_ids:
                entry.company_ids.append(str(row.id))
            entry.categories.extend(c for c in row.categories if c not in entry.categories)
        for entry in boards.values():
            assert entry.adapter is not None
            entry.adapter = replace(entry.adapter, company_ids=tuple(entry.company_ids))
        return [*boards.values(), *unsupported]

    async def _catalog(self, **filters: object) -> tuple[list[CompanyLink], CatalogState]:
        if self.catalog is None:
            return [], CatalogState(status="unavailable", message="Каталог компаний не подключён.")
        try:
            return await self.catalog(**filters), CatalogState(status="ok")
        except LoginRequired as error:
            return [], CatalogState(status="login_required", message=NO_LOGIN, login_url=error.login_url)
        except AuthError as error:
            if error.status_code == 403:
                return [], CatalogState(status="forbidden", message=str(error))
            return [], CatalogState(status="unavailable", message=str(error))

    def _fetch(self, source_id: str) -> Fetch | None:
        return self.fetch(source_id) if self.fetch and source_id in self.registry.fixed else None

    # --- Description -------------------------------------------------------------------------

    def _describe(self, entry: Entry) -> dict:
        adapter = entry.adapter
        coverage = entry.coverage
        item: dict = {
            "id": entry.id,
            "name": entry.name,
            "kind": entry.kind.key,
            "kind_name": entry.kind.name,
            "group": entry.group,
            "supported": entry.supported,
            "reason": entry.reason,
            "company_ids": entry.company_ids,
            "categories": entry.categories,
            "coverage": {
                "countries": sorted(coverage.countries) if coverage.countries is not None else None,
                "known": coverage.known,
            },
        }
        capabilities = self.registry.capabilities.get(entry.kind.key)
        if capabilities is not None:
            item["capabilities"] = capabilities.model_dump(mode="json")
        if adapter is not None:
            item.update(
                type=adapter.type,
                state=self._state(adapter.resource),
                geography=adapter.geography.mode if adapter.geography else None,
                default_limits={
                    "max_items_per_query": adapter.settings.max_items_per_query,
                    "max_pages_per_query": adapter.settings.max_pages_per_query,
                },
            )
        return item

    def _state(self, resource: str) -> dict:
        until = self.resources.resource(resource)["open_until"]
        return (
            {"status": "blocked", "until": until}
            if until > time.time()
            else {"status": "ready", "until": None}
        )
