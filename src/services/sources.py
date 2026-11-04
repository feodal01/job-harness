import time

from src.db.protocols import EventStore, ResourceStore
from src.parsers.interfaces import SourceAdapter


class SourceService:
    """Describe configured adapters and known local state, without probing websites."""

    def __init__(
        self, sources: dict[str, SourceAdapter], resources: ResourceStore, events: EventStore
    ) -> None:
        self.sources, self.resources, self.event_store = sources, resources, events

    def list_sources(self) -> list[dict]:
        return [
            {
                "id": source.id,
                "name": source.name,
                "type": source.type,
                "state": self._state(source.resource),
                "geography": source.geography.mode if source.geography else None,
                "capabilities": source.capabilities.model_dump(mode="json"),
                "default_limits": {
                    "max_items_per_query": source.settings.max_items_per_query,
                    "max_pages_per_query": source.settings.max_pages_per_query,
                },
            }
            for source in self.sources.values()
        ]

    def events(self, after: int = 0) -> list[dict]:
        return self.event_store.events(None, after)

    def _state(self, resource: str) -> dict:
        until = self.resources.resource(resource)["open_until"]
        return (
            {"status": "blocked", "until": until}
            if until > time.time()
            else {"status": "ready", "until": None}
        )
