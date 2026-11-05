"""HH's own place directory: the snapshot of `api.hh.ru/areas` (scripts/build_hh_areas.py), one version."""

import gzip
import json
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any

SNAPSHOT = Path(__file__).parents[1] / "data/areas.json.gz"


@dataclass(frozen=True)
class Area:
    id: str
    name: str
    parent: str | None
    has_children: bool


class HHAreas:
    def __init__(self, path: Path = SNAPSHOT) -> None:
        data = json.loads(gzip.decompress(path.read_bytes()))
        if data["metadata"]["format_version"] != 1:
            raise RuntimeError("Unsupported HH area snapshot format")
        self.metadata: dict[str, Any] = data["metadata"]
        self._areas = {key: Area(key, *value) for key, value in data["areas"].items()}

    def chain(self, area_id: str) -> list[Area]:
        """The area and its ancestors from the top (a country or «Другие регионы») down; [] if unknown."""
        chain: list[Area] = []
        current = self._areas.get(area_id)
        while current is not None:
            chain.append(current)
            current = self._areas.get(current.parent) if current.parent else None
        return chain[::-1]


@cache
def default_areas() -> HHAreas:
    return HHAreas()
