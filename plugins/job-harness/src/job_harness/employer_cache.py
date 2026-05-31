"""JSON-based cache for employer career page data.

Two-tier cache:
- Local cache (data/company-careers.json): all entries, including null results.
  Not committed to git — used by the resolver to avoid re-checking companies.
- Public cache (data/company-careers-public.json): only entries with a
  careers_url. Committed to git — shared across users as a crowdsourced
  knowledge base.

On load, entries from the public cache are merged into the local cache
(if the public entry is newer or the local one doesn't exist). On save,
both files are written.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from dataclasses import dataclass, asdict

LOCAL_CACHE_PATH = Path("data/company-careers.json")
PUBLIC_CACHE_PATH = Path("data/company-careers-public.json")
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

    def is_useful(self) -> bool:
        """Whether this entry has a careers_url — worth sharing publicly."""
        return self.careers_url is not None


class EmployerCache:
    def __init__(self, path: Path | str | None = None, public_path: Path | str | None = None):
        self.path = Path(path) if path else LOCAL_CACHE_PATH
        self.public_path = Path(public_path) if public_path else PUBLIC_CACHE_PATH
        self._data: dict[str, CompanyEntry] = {}
        self._load()

    def _load(self) -> None:
        # Load public cache first (as baseline), then local cache on top
        self._merge_from(self.public_path)
        self._merge_from(self.path)

    def _merge_from(self, path: Path) -> None:
        if not path.exists():
            return
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            for k, v in raw.items():
                incoming = CompanyEntry(**v)
                existing = self._data.get(k)
                if existing is None or self._is_newer(incoming, existing):
                    self._data[k] = incoming
        except (json.JSONDecodeError, TypeError):
            pass

    @staticmethod
    def _is_newer(a: CompanyEntry, b: CompanyEntry) -> bool:
        """Is entry `a` more recently checked than `b`?"""
        if not a.last_checked:
            return False
        if not b.last_checked:
            return True
        return a.last_checked >= b.last_checked

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Always write local cache with all entries
        self._write(self.path, self._data)
        # Write public cache with only useful entries
        public = {k: v for k, v in self._data.items() if v.is_useful()}
        self._write(self.public_path, public)

    @staticmethod
    def _write(path: Path, data: dict[str, CompanyEntry]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        serialized = {k: asdict(v) for k, v in data.items()}
        path.write_text(json.dumps(serialized, ensure_ascii=False, indent=2), encoding="utf-8")

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
