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
from src.parsers.interfaces import Fetch, SourceAdapter, SourceGroup
from src.parsers.registry import CATALOG_ALIASES, UNKNOWN, Coverage, SourceKind, SourceRegistry, Unsupported
from src.schemas.companies import CompanyLink
from src.schemas.sources import CatalogState, SourceFilter
from src.services.auth import AuthError, LoginRequired

CatalogRows = Callable[..., Awaitable[list[CompanyLink]]]
GROUP_ORDER: tuple[SourceGroup, ...] = ("aggregator", "it_board", "company_site", "telegram")
NO_LOGIN = "Сайты компаний доступны после входа."


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

    @property
    def supported(self) -> bool:
        return self.adapter is not None


@dataclass
class Resolved:
    """Adapters for one run, sources left out with the reason, and the catalog's answer."""

    sources: dict[str, SourceAdapter]
    skipped: dict[str, str]
    catalog: CatalogState


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

    async def resolve_ids(self, ids: Iterable[str]) -> Resolved:
        """Adapters for explicit source ids; boards come from the catalog, unknown fixed ids are errors."""
        wanted = list(dict.fromkeys(ids))
        sources: dict[str, SourceAdapter] = {}
        boards: list[str] = []
        unknown: list[str] = []
        for source_id in wanted:
            if source_id in self.registry.fixed:
                sources[source_id] = self.registry.fixed[source_id]
            elif (kind := self.registry.kind_of(source_id)) is not None and kind.board is not None:
                boards.append(source_id)
            else:
                unknown.append(source_id)
        if unknown:
            raise InvalidSearchError(f"Unknown source: {', '.join(unknown)}")
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
                    )
                )
                continue
            entry = boards.get(built.id)
            if entry is None:
                platform = self.registry.kind_of(built.id)
                assert platform is not None
                boards[built.id] = Entry(
                    built.id, built.name, platform, "company_site", UNKNOWN, built, None, [str(row.id)]
                )
            elif str(row.id) not in entry.company_ids:
                entry.company_ids.append(str(row.id))
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
