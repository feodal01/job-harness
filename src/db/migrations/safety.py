"""Integrity checks and SQLite-native backups around Alembic upgrades."""

import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path

from src.core.errors import DatabaseVersionError


def structure(db: sqlite3.Connection) -> dict[str, object]:
    """Compare columns, references and indexes independently of historical DDL formatting."""
    result: dict[str, object] = {}
    for kind, name, table, sql in db.execute(
        "SELECT type,name,tbl_name,sql FROM sqlite_schema WHERE name NOT LIKE 'sqlite_%' ORDER BY name"
    ):
        quoted = '"' + name.replace('"', '""') + '"'
        if kind == "table":
            result[name] = (
                kind,
                [tuple(r) for r in db.execute(f"PRAGMA table_xinfo({quoted})")],
                sorted(tuple(r) for r in db.execute(f"PRAGMA foreign_key_list({quoted})")),
                sorted(tuple(r)[1:] for r in db.execute(f"PRAGMA index_list({quoted})")),
            )
        elif kind == "index":
            result[name] = (kind, table, [tuple(r) for r in db.execute(f"PRAGMA index_xinfo({quoted})")])
        else:
            result[name] = (kind, table, sql)
    return result


def validate_integrity(db: sqlite3.Connection) -> None:
    if [tuple(row) for row in db.execute("PRAGMA integrity_check")] != [("ok",)]:
        raise DatabaseVersionError("Database integrity check failed")
    if db.execute("PRAGMA foreign_key_check").fetchone() is not None:
        raise DatabaseVersionError("Database contains broken references")


def backup(db: sqlite3.Connection, path: Path, version: str) -> Path:
    directory = path.parent / "backups"
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    destination = directory / f"{path.stem}-v{version}-{stamp}-{uuid.uuid4().hex[:8]}.sqlite"
    temporary = destination.with_suffix(".partial")
    temporary.touch(mode=0o600, exist_ok=False)
    copy = sqlite3.connect(temporary)
    try:
        db.backup(copy)
        validate_integrity(copy)
    except BaseException:
        copy.close()
        temporary.unlink(missing_ok=True)
        raise
    else:
        copy.close()
    temporary.replace(destination)
    return destination
