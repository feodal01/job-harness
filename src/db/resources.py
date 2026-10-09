import json
from typing import Any

from .connection import Connection, encoded


class ResourceRepository:
    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def resource(self, resource: str, update: dict[str, Any] | None = None) -> dict[str, Any]:
        if update is not None:
            with self._connection.transaction():
                self._connection.db.execute(
                    "INSERT INTO resource_state VALUES (?,?) ON CONFLICT(id) DO UPDATE SET "
                    "data=excluded.data",
                    (resource, encoded(update)),
                )
        row = self._connection.db.execute(
            "SELECT data FROM resource_state WHERE id=?", (resource,)
        ).fetchone()
        return json.loads(row[0]) if row else {"failures": 0, "open_until": 0, "next_request_at": 0}
