import json
import time
from typing import Any
from uuid import uuid4

from src.core.errors import NotFoundError

from .connection import UNFINISHED, Connection, encoded
from .protocols import BriefReader


class RunRepository:
    def __init__(self, connection: Connection, briefs: BriefReader) -> None:
        self._connection = connection
        self._briefs = briefs

    def create_run(
        self, brief_id: str, request: dict[str, Any], sources: dict[str, dict[str, Any]]
    ) -> dict[str, Any]:
        brief = self._briefs.brief(brief_id)
        if brief is None:
            raise NotFoundError(brief_id)
        run_id = uuid4().hex
        with self._connection.transaction():
            self._connection.db.execute(
                "INSERT INTO runs (id,brief_id,request,status,created_at,sources) VALUES (?,?,?,?,?,?)",
                (
                    run_id,
                    brief_id,
                    encoded({**request, "brief": brief}),
                    "queued",
                    time.time(),
                    encoded(sources),
                ),
            )
        return self.run(run_id)

    def run(self, run_id: str) -> dict[str, Any]:
        row = self._connection.db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        if row is None:
            raise NotFoundError(run_id)
        return {
            **dict(row),
            "request": json.loads(row["request"]),
            "metrics": json.loads(row["metrics"]),
            "sources": json.loads(row["sources"]),
            "limit_reached": bool(row["limit_reached"]),
        }

    def runs(self, brief_id: str | None = None) -> list[dict[str, Any]]:
        where = " WHERE brief_id=?" if brief_id is not None else ""
        params = (brief_id,) if brief_id is not None else ()
        return [
            self.run(r[0])
            for r in self._connection.db.execute(
                "SELECT id FROM runs" + where + " ORDER BY created_at DESC LIMIT 100", params
            )
        ]

    def progress(
        self,
        run_id: str,
        metrics: dict[str, Any],
        *,
        status: str = "running",
        error: str | None = None,
        limit_reached: bool = False,
    ) -> None:
        finished = None if status in UNFINISHED else time.time()
        with self._connection.transaction():
            changed = self._connection.db.execute(
                "UPDATE runs SET metrics=?,status=?,error=?,limit_reached=?,finished_at=? WHERE id=?",
                (encoded(metrics), status, error, limit_reached, finished, run_id),
            ).rowcount
            if not changed:
                raise NotFoundError(run_id)
            if finished is not None:
                self._connection.db.execute(
                    "UPDATE run_vacancies SET detail_status=?,error=? WHERE run_id=? AND "
                    "detail_status='pending'",
                    ("cancelled" if status == "cancelled" else "interrupted", error, run_id),
                )

    def update_source(self, run_id: str, source: str, state: dict[str, Any]) -> None:
        with self._connection.transaction():
            row = self._connection.db.execute("SELECT sources FROM runs WHERE id=?", (run_id,)).fetchone()
            if row is None:
                raise NotFoundError(run_id)
            sources = {**json.loads(row[0]), source: state}
            self._connection.db.execute("UPDATE runs SET sources=? WHERE id=?", (encoded(sources), run_id))

    def recover_unfinished(self) -> None:
        """Mark abandoned work after startup, separately from opening the database."""
        with self._connection.transaction():
            for row in self._connection.db.execute(
                "SELECT id, sources FROM runs WHERE status IN (?,?)", UNFINISHED
            ).fetchall():
                sources = {
                    key: {**state, "status": "interrupted"} if state.get("status") in UNFINISHED else state
                    for key, state in json.loads(row["sources"]).items()
                }
                self._connection.db.execute(
                    "UPDATE runs SET status='interrupted', finished_at=?, sources=?, "
                    "error='Service restarted; start a new search' WHERE id=?",
                    (time.time(), encoded(sources), row["id"]),
                )
            self._connection.db.execute(
                "UPDATE run_vacancies SET detail_status='interrupted', "
                "error='Service restarted before detail completed' WHERE detail_status='pending'"
            )
