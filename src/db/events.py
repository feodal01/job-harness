import json
import time
from typing import Any

from .connection import Connection, encoded
from .protocols import RunReader


class EventRepository:
    def __init__(self, connection: Connection, runs: RunReader) -> None:
        self._connection = connection
        self._runs = runs

    def event(self, run_id: str | None, event: str, **data: Any) -> None:
        with self._connection.transaction():
            self._connection.db.execute(
                "INSERT INTO events (run_id,time,event,data) VALUES (?,?,?,?)",
                (run_id, time.time(), event, encoded(data)),
            )

    def events(self, run_id: str | None, after: int = 0) -> list[dict[str, Any]]:
        if run_id is not None:
            self._runs.run(run_id)
        return [
            {**dict(r), "data": json.loads(r["data"])}
            for r in self._connection.db.execute(
                "SELECT * FROM events WHERE run_id IS ? AND id>? ORDER BY id LIMIT 1000", (run_id, after)
            )
        ]
