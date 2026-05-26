"""JSON-based cache for employer career page data."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from dataclasses import dataclass, asdict

DEFAULT_CACHE_PATH = Path("data/company-careers.json")
FRESHNESS_DAYS = 7


@dataclass
class CompanyEntry:
    company: str
    careers_url: str | None = None
    ats_type: str = "unknown"
    scraper_name: str | None = None
    last_checked: str | None = None  # ISO date
    last_found_roles: bool = False
    ignored: bool = False

    def is_fresh(self, days: int = FRESHNESS_DAYS) -> bool:
        if not self.last_checked:
            return False
        try:
            checked = datetime.fromisoformat(self.last_checked)
        except ValueError:
            return False
        return datetime.now() - checked < timedelta(days=days)


class EmployerCache:
    def __init__(self, path: Path | str | None = None):
        self.path = Path(path) if path else DEFAULT_CACHE_PATH
        self._data: dict[str, CompanyEntry] = {}
        self._load()

    def _load(self) -> None:
        if self.path.exists():
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
                for k, v in raw.items():
                    self._data[k] = CompanyEntry(**v)
            except (json.JSONDecodeError, TypeError):
                self._data = {}

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = {k: asdict(v) for k, v in self._data.items()}
        self.path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def get(self, company: str) -> CompanyEntry | None:
        return self._data.get(self._key(company))

    def get_fresh(self, company: str) -> CompanyEntry | None:
        entry = self.get(company)
        if entry and entry.is_fresh() and not entry.ignored:
            return entry
        return None

    def upsert(self, entry: CompanyEntry) -> None:
        key = self._key(entry.company)
        entry.last_checked = datetime.now().strftime("%Y-%m-%d")
        self._data[key] = entry

    def mark_ignored(self, company: str) -> None:
        entry = self.get(company)
        if entry:
            entry.ignored = True
        else:
            self.upsert(CompanyEntry(company=company, ignored=True))

    def all_entries(self) -> list[CompanyEntry]:
        return list(self._data.values())

    @staticmethod
    def _key(company: str) -> str:
        return company.strip().lower()
