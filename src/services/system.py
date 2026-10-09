from collections.abc import Callable

from src.parsers.interfaces import SourceAdapter


class SystemService:
    def __init__(self, stats: Callable[[], dict[str, int]], sources: dict[str, SourceAdapter]) -> None:
        self._stats = stats
        self.sources = sources

    def health(self) -> dict:
        return {"status": "ok", "sources": list(self.sources), "filters": True}

    def stats(self) -> dict:
        return self._stats()
