import json
import sqlite3
import time
from typing import Any

from src.core.errors import NotFoundError
from src.models.vacancy import Vacancy

from .connection import Connection, encoded
from .protocols import BriefReader, RunReader


def _filled(data: dict[str, Any]) -> dict[str, Any]:
    """A listing with empty fields must not erase a fetched card."""
    return {k: v for k, v in data.items() if v is not None and v != [] and v != {}}


class VacancyRepository:
    def __init__(self, connection: Connection, briefs: BriefReader, runs: RunReader) -> None:
        self._connection = connection
        self._briefs = briefs
        self._runs = runs

    def save_vacancy(
        self,
        run_id: str,
        brief_id: str,
        vacancy: Vacancy,
        status: str = "pending",
        error: str | None = None,
        *,
        source: str,
        selection: dict[str, Any] | None = None,
        detail_fetched: bool = False,
    ) -> None:
        """Merge into the corpus, snapshot into the run, and add to or refresh the brief's pool."""
        key = f"{source}:{vacancy.id}"
        data = {**vacancy.model_dump(), "source": source, "selection": selection}
        previous = self._connection.db.execute("SELECT data FROM vacancies WHERE id=?", (key,)).fetchone()
        canonical = json.loads(previous[0]) if previous else {}
        canonical.update(_filled(vacancy.model_dump()))
        canonical["source"] = source
        now = time.time()
        with self._connection.transaction():
            self._connection.db.execute(
                "INSERT INTO vacancies VALUES (?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
                "data=excluded.data, last_seen=excluded.last_seen, "
                "detail_fetched_at=coalesce(vacancies.detail_fetched_at, excluded.detail_fetched_at)",
                (key, encoded(canonical), now, now, now if detail_fetched else None),
            )
            self._connection.db.execute(
                "INSERT INTO run_vacancies VALUES (?,?,?,?,?,?) ON CONFLICT(run_id,vacancy_id) DO "
                "UPDATE SET data=excluded.data, "
                "detail_status=excluded.detail_status,error=excluded.error",
                (run_id, key, encoded(data), status, error, previous is None),
            )
            self._connection.db.execute(
                "INSERT INTO brief_vacancies "
                "(brief_id,vacancy_id,first_run_id,last_run_id,first_seen,last_seen,selection) VALUES "
                "(?,?,?,?,?,?,?) ON CONFLICT(brief_id,vacancy_id) DO UPDATE SET "
                "last_run_id=excluded.last_run_id, last_seen=excluded.last_seen, "
                "selection=excluded.selection",
                (brief_id, key, run_id, run_id, now, now, encoded(selection) if selection else None),
            )

    def add_origin(self, run_id: str, vacancy_key: str, query: str, request_url: str, page: int) -> None:
        with self._connection.transaction():
            self._connection.db.execute(
                "INSERT OR IGNORE INTO vacancy_origins VALUES (?,?,?,?,?)",
                (run_id, vacancy_key, query, request_url, page),
            )

    def known_card(self, vacancy_key: str) -> dict[str, Any] | None:
        """Corpus data of a vacancy whose card was already fetched by any run of any brief."""
        row = self._connection.db.execute(
            "SELECT data FROM vacancies WHERE id=? AND detail_fetched_at IS NOT NULL", (vacancy_key,)
        ).fetchone()
        return json.loads(row[0]) if row else None

    def results(self, run_id: str) -> list[dict[str, Any]]:
        self._runs.run(run_id)
        rows = self._connection.db.execute(
            "SELECT * FROM run_vacancies WHERE run_id=? ORDER BY rowid", (run_id,)
        ).fetchall()
        return self._result_items(run_id, rows)

    def results_page(
        self, run_id: str, page: int = 1, page_size: int = 50, selection: str = "all"
    ) -> dict[str, Any]:
        self._runs.run(run_id)
        where, params = self._selection_clause("json_extract(data, '$.selection.status')", selection)
        rows, result = self._page(
            "run_vacancies", "run_id=?" + where, (run_id, *params), "*", "rowid", page, page_size
        )
        return {**result, "items": self._result_items(run_id, rows)}

    def _result_items(self, run_id: str, rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
        origins = self._page_origins("o.run_id=?", run_id, rows)
        return [
            {
                **json.loads(r["data"]),
                "detail_status": r["detail_status"],
                "error": r["error"],
                "is_new": bool(r["is_new"]),
                "origins": origins.get(r["vacancy_id"], []),
            }
            for r in rows
        ]

    def _origins(self, where: str, params: tuple[Any, ...]) -> dict[str, list[dict[str, Any]]]:
        found: dict[str, list[dict[str, Any]]] = {}
        for r in self._connection.db.execute(
            f"SELECT o.*, r.created_at FROM vacancy_origins o JOIN runs r ON r.id=o.run_id WHERE "
            f"{where} ORDER BY r.created_at, o.rowid",
            params,
        ):
            found.setdefault(r["vacancy_id"], []).append(
                {
                    "run_id": r["run_id"],
                    "query": r["query"],
                    "request_url": r["request_url"],
                    "page": r["page"],
                }
            )
        return found

    def pool(self, brief_id: str) -> list[dict[str, Any]]:
        if self._briefs.brief(brief_id) is None:
            raise NotFoundError(brief_id)
        rows = self._connection.db.execute(
            "SELECT b.*, v.data, v.detail_fetched_at FROM brief_vacancies b JOIN vacancies v ON "
            "v.id=b.vacancy_id WHERE b.brief_id=? ORDER BY b.first_seen, b.rowid",
            (brief_id,),
        ).fetchall()
        return self._pool_items(brief_id, rows)

    def pool_page(
        self,
        brief_id: str,
        page: int = 1,
        page_size: int = 50,
        selection: str = "candidates",
        status: str = "all",
        q: str = "",
        sort: str = "first_seen",
    ) -> dict[str, Any]:
        if self._briefs.brief(brief_id) is None:
            raise NotFoundError(brief_id)
        where, params = self._pool_filter(selection, status, q)
        column, descending = self._pool_sort(sort)
        order = self._pool_order(column, descending)
        rows, result = self._page(
            "brief_vacancies b JOIN vacancies v ON v.id=b.vacancy_id",
            "b.brief_id=?" + where,
            (brief_id, *params),
            "b.*, v.data, v.detail_fetched_at",
            order,
            page,
            page_size,
        )
        return {**result, "items": self._pool_items(brief_id, rows)}

    @classmethod
    def _pool_filter(cls, selection: str, status: str, q: str) -> tuple[str, tuple[Any, ...]]:
        where, params = cls._selection_clause("json_extract(b.selection, '$.status')", selection)
        if status == "open":
            where += " AND b.user_status IN ('new', 'viewed')"
        elif status != "all":
            where += " AND b.user_status=?"
            params += (status,)
        if q.strip():
            where += " AND (instr(casefold(json_extract(v.data, '$.title')), ?) > 0 "
            where += "OR instr(casefold(json_extract(v.data, '$.company.name')), ?) > 0)"
            params += (q.strip().casefold(), q.strip().casefold())
        return (where, params)

    @staticmethod
    def _pool_sort(sort: str) -> tuple[str, bool]:
        return {
            "first_seen": ("b.first_seen", False),
            "newest": ("b.first_seen", True),
            "updated": ("coalesce(b.status_updated_at, b.first_seen)", True),
        }[sort]

    @staticmethod
    def _pool_order(column: str, descending: bool) -> str:
        direction = "DESC" if descending else "ASC"
        return f"{column} {direction}, b.rowid {direction}"

    def pool_adjacent(
        self,
        brief_id: str,
        vacancy_key: str,
        direction: str = "next",
        page_size: int = 50,
        selection: str = "candidates",
        status: str = "all",
        q: str = "",
        sort: str = "first_seen",
        anchor_time: float | None = None,
    ) -> dict[str, Any]:
        column, descending = self._pool_sort(sort)
        anchor = self._connection.db.execute(
            f"SELECT {column}, b.rowid FROM brief_vacancies b WHERE b.brief_id=? AND b.vacancy_id=?",
            (brief_id, vacancy_key),
        ).fetchone()
        if anchor is None:
            raise NotFoundError(vacancy_key)
        timestamp = anchor[0] if anchor_time is None else anchor_time
        where, params = self._pool_filter(selection, status, q)
        where = "b.brief_id=?" + where
        params = (brief_id, *params)
        table = "brief_vacancies b JOIN vacancies v ON v.id=b.vacancy_id"
        reverse = descending != (direction == "previous")
        comparison = "<" if reverse else ">"
        target = self._connection.db.execute(
            f"SELECT b.vacancy_id, {column}, b.rowid FROM {table} WHERE {where} AND b.vacancy_id "
            f"!= ? AND ({column}, b.rowid) {comparison} (?, ?) ORDER BY "
            f"{self._pool_order(column, reverse)} LIMIT 1",
            (*params, vacancy_key, timestamp, anchor[1]),
        ).fetchone()
        if target is None:
            return {"key": None, "page": None}
        before = ">" if descending else "<"
        position = self._connection.db.execute(
            f"SELECT count(*) FROM {table} WHERE {where} AND ({column}, b.rowid) {before} (?, ?)",
            (*params, target[1], target[2]),
        ).fetchone()[0]
        page = self.pool_page(brief_id, position // page_size + 1, page_size, selection, status, q, sort)
        return {"key": target[0], "page": page}

    def pool_item(self, brief_id: str, vacancy_key: str) -> dict[str, Any]:
        rows = self._connection.db.execute(
            "SELECT b.*, v.data, v.detail_fetched_at FROM brief_vacancies b JOIN vacancies v ON "
            "v.id=b.vacancy_id WHERE b.brief_id=? AND b.vacancy_id=?",
            (brief_id, vacancy_key),
        ).fetchall()
        if not rows:
            raise NotFoundError(vacancy_key)
        return self._pool_items(brief_id, rows)[0]

    @staticmethod
    def _selection_clause(column: str, selection: str) -> tuple[str, tuple[Any, ...]]:
        if selection == "candidates":
            return (f" AND coalesce({column}, '') != 'rejected'", ())
        if selection != "all":
            return (f" AND {column}=?", (selection,))
        return ("", ())

    def _page(
        self,
        table: str,
        where: str,
        params: tuple[Any, ...],
        columns: str,
        order: str,
        page: int,
        page_size: int,
    ) -> tuple[list[sqlite3.Row], dict[str, Any]]:
        total = self._connection.db.execute(f"SELECT count(*) FROM {table} WHERE {where}", params).fetchone()[
            0
        ]
        pages = max(1, (total + page_size - 1) // page_size)
        page = min(page, pages)
        rows = self._connection.db.execute(
            f"SELECT {columns} FROM {table} WHERE {where} ORDER BY {order} LIMIT ? OFFSET ?",
            (*params, page_size, (page - 1) * page_size),
        ).fetchall()
        return (rows, {"total": total, "page": page, "page_size": page_size, "pages": pages})

    def _page_origins(
        self, where: str, scope: str, rows: list[sqlite3.Row]
    ) -> dict[str, list[dict[str, Any]]]:
        origins: dict[str, list[dict[str, Any]]] = {}
        for start in range(0, len(rows), 100):
            keys = [row["vacancy_id"] for row in rows[start : start + 100]]
            placeholders = ",".join("?" for _ in keys)
            origins.update(self._origins(f"{where} AND o.vacancy_id IN ({placeholders})", (scope, *keys)))
        return origins

    def _pool_items(self, brief_id: str, rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
        origins = self._page_origins("r.brief_id=?", brief_id, rows)
        return [
            {
                **self._complete_vacancy(json.loads(r["data"])),
                "key": r["vacancy_id"],
                "selection": json.loads(r["selection"]) if r["selection"] else None,
                "user_status": r["user_status"],
                "note": r["note"],
                "status_updated_at": r["status_updated_at"],
                "first_seen": r["first_seen"],
                "last_seen": r["last_seen"],
                "first_run_id": r["first_run_id"],
                "last_run_id": r["last_run_id"],
                "has_card": r["detail_fetched_at"] is not None,
                "origins": origins.get(r["vacancy_id"], []),
            }
            for r in rows
        ]

    @staticmethod
    def _complete_vacancy(data: dict[str, Any]) -> dict[str, Any]:
        """Corpus rows omit empty fields; the pool returns the full vacancy shape like run results."""
        fields = {k: v for k, v in data.items() if k in Vacancy.model_fields}
        return {**Vacancy.model_validate(fields).model_dump(), "source": data.get("source")}

    def set_pool_status(self, brief_id: str, vacancy_key: str, status: str, note: str) -> None:
        with self._connection.transaction():
            changed = self._connection.db.execute(
                "UPDATE brief_vacancies SET user_status=?, note=?, status_updated_at=? WHERE "
                "brief_id=? AND vacancy_id=?",
                (status, note, time.time(), brief_id, vacancy_key),
            ).rowcount
        if not changed:
            raise NotFoundError(vacancy_key)

    def set_pool_selection(self, brief_id: str, vacancy_key: str, selection: dict[str, Any]) -> None:
        with self._connection.transaction():
            changed = self._connection.db.execute(
                "UPDATE brief_vacancies SET selection=? WHERE brief_id=? AND vacancy_id=?",
                (encoded(selection), brief_id, vacancy_key),
            ).rowcount
            if not changed:
                raise NotFoundError(vacancy_key)
