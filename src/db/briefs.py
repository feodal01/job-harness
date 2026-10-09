import json
import time
from typing import Any
from uuid import uuid4

from src.core.errors import NotFoundError
from src.models.brief import BriefInput

from .connection import Connection


class BriefRepository:
    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def save_brief(self, brief: BriefInput, brief_id: str | None = None) -> dict[str, Any]:
        if brief_id is not None and self.brief(brief_id) is None:
            raise NotFoundError(brief_id)
        brief_id = brief_id or uuid4().hex
        now = time.time()
        with self._connection.transaction():
            self._connection.db.execute(
                "INSERT INTO briefs VALUES (?,?,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data, "
                "updated_at=excluded.updated_at",
                (brief_id, brief.model_dump_json(), now),
            )
        return {"id": brief_id, **brief.model_dump(), "updated_at": now}

    def brief(self, brief_id: str) -> dict[str, Any] | None:
        row = self._connection.db.execute("SELECT * FROM briefs WHERE id=?", (brief_id,)).fetchone()
        return {"id": row["id"], **json.loads(row["data"]), "updated_at": row["updated_at"]} if row else None

    def briefs(self) -> list[dict[str, Any]]:
        return [
            {"id": r["id"], **json.loads(r["data"]), "updated_at": r["updated_at"]}
            for r in self._connection.db.execute("SELECT * FROM briefs ORDER BY updated_at DESC")
        ]

    def brief_overviews(self) -> list[dict[str, Any]]:
        counts: dict[str, dict[str, int]] = {}
        for row in self._connection.db.execute(
            "SELECT brief_id, user_status, count(*) AS n FROM brief_vacancies WHERE "
            "coalesce(json_extract(selection, '$.status'), '') != 'rejected' GROUP BY brief_id, "
            "user_status"
        ):
            counts.setdefault(row["brief_id"], {})[row["user_status"]] = row["n"]
        hidden = {
            r["brief_id"]: r["n"]
            for r in self._connection.db.execute(
                "SELECT brief_id, count(*) AS n FROM brief_vacancies WHERE json_extract(selection, "
                "'$.status')='rejected' GROUP BY brief_id"
            )
        }
        latest = {
            row["brief_id"]: {
                "id": row["id"],
                "status": row["status"],
                "created_at": row["created_at"],
                "request": json.loads(row["request"]),
            }
            for row in self._connection.db.execute(
                "SELECT r.* FROM runs r WHERE r.rowid=(SELECT r2.rowid FROM runs r2 WHERE "
                "r2.brief_id=r.brief_id ORDER BY r2.created_at DESC, r2.rowid DESC LIMIT 1)"
            )
        }
        result = []
        for brief in self.briefs():
            funnel = counts.get(brief["id"], {}).copy()
            funnel["all"] = sum(funnel.values())
            funnel["open"] = funnel.get("new", 0) + funnel.get("viewed", 0)
            result.append(
                {
                    **brief,
                    "funnel": funnel,
                    "hidden_count": hidden.get(brief["id"], 0),
                    "last_run": latest.get(brief["id"]),
                }
            )
        return result
