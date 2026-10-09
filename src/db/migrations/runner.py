"""Safety envelope for Alembic; migration ordering and revision state belong to Alembic."""

import hashlib
import logging
import sqlite3
from pathlib import Path
from typing import Any, cast

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import URL, Connection, Engine, create_engine, event

from src.core.errors import DatabaseVersionError
from src.db.migrations.safety import backup, structure, validate_integrity

logger = logging.getLogger(__name__)
SCRIPT_LOCATION = Path(__file__).parent
BASE_SCHEMA = SCRIPT_LOCATION.parent / "schema.sql"
# Frozen compatibility marker, not the Alembic revision counter. Older releases reject it.
ALEMBIC_FORMAT_VERSION = 10
LEGACY_REVISIONS = {8: "0008", 9: "0009"}


def configuration(connection: Connection | None = None) -> Config:
    config = Config()
    config.set_main_option("script_location", str(SCRIPT_LOCATION).replace("%", "%%"))
    if connection is not None:
        config.attributes["connection"] = connection
    return config


def migration_engine(path: Path | None = None) -> Engine:
    engine = create_engine(URL.create("sqlite+pysqlite", database=str(path) if path else ":memory:"))

    @event.listens_for(engine, "connect")
    def disable_implicit_transactions(dbapi_connection: sqlite3.Connection, record: Any) -> None:
        dbapi_connection.isolation_level = None

    @event.listens_for(engine, "begin")
    def begin(connection: Connection) -> None:
        # Explicit BEGIN is required for atomic SQLite DDL on supported Python versions.
        connection.exec_driver_sql("BEGIN IMMEDIATE")

    return engine


def raw_connection(connection: Connection) -> sqlite3.Connection:
    return cast(sqlite3.Connection, connection.connection.driver_connection)


def _expected_structure(revision: str) -> dict[str, object]:
    engine = migration_engine()
    try:
        with engine.begin() as connection:
            command.upgrade(configuration(connection), revision)
            return structure(raw_connection(connection))
    finally:
        engine.dispose()


def _validate_schema(db: sqlite3.Connection, revision: str, *, legacy: bool = False) -> None:
    expected = _expected_structure(revision)
    if legacy:
        expected.pop("alembic_version")
    if structure(db) != expected:
        raise DatabaseVersionError(f"Database schema differs from revision {revision}; no repair attempted")


def _legacy_revision(db: sqlite3.Connection, version: int) -> str:
    if version not in LEGACY_REVISIONS:
        raise DatabaseVersionError(f"Unsupported database schema version {version}; expected legacy 8 or 9")
    revision = LEGACY_REVISIONS[version]
    _validate_schema(db, revision, legacy=True)
    if version == 9:
        path = SCRIPT_LOCATION / "legacy/0009_migration_history.sql"
        expected = [(9, path.stem, hashlib.sha256(path.read_bytes()).hexdigest())]
        saved = [tuple(r) for r in db.execute("SELECT version,name,checksum FROM schema_migrations")]
        if saved != expected:
            raise DatabaseVersionError("Legacy migration history or checksums do not match")
    return revision


def initialize_schema(db: sqlite3.Connection, path: Path) -> None:
    scripts = ScriptDirectory.from_config(configuration())
    head = scripts.get_current_head()
    if head is None:
        raise DatabaseVersionError("Alembic has no head revision")
    version = db.execute("PRAGMA user_version").fetchone()[0]
    tables = {r[0] for r in db.execute("SELECT name FROM sqlite_schema WHERE type='table'")}
    fresh = version == 0 and not db.execute("SELECT 1 FROM sqlite_schema LIMIT 1").fetchone()
    legacy = not fresh and "alembic_version" not in tables
    revision = None
    if legacy:
        revision = _legacy_revision(db, version)
    elif not fresh:
        rows = db.execute("SELECT version_num FROM alembic_version").fetchall()
        if len(rows) != 1 or rows[0][0] not in {r.revision for r in scripts.walk_revisions()}:
            raise DatabaseVersionError("Unknown or ambiguous Alembic revision; database was not changed")
        revision = rows[0][0]
        if revision in LEGACY_REVISIONS.values():
            raise DatabaseVersionError(
                "Incomplete adoption of legacy Alembic revision; database was not changed"
            )
        if version != ALEMBIC_FORMAT_VERSION:
            raise DatabaseVersionError(f"Unexpected database compatibility marker {version}")
        _validate_schema(db, revision)
        if revision == head:
            return
    saved_backup = None
    if not fresh:
        validate_integrity(db)
        saved_backup = backup(db, path, str(revision))
        logger.info("database.backup_created path=%s", saved_backup)
    engine = migration_engine(path)
    try:
        with engine.begin() as connection:
            config = configuration(connection)
            if legacy:
                # Adopt only the revision whose structure and old journal were verified.
                assert revision is not None
                command.stamp(config, revision)
            command.upgrade(config, "head")
            _validate_schema(raw_connection(connection), head)
            validate_integrity(raw_connection(connection))
    except Exception as exc:
        raise DatabaseVersionError(
            f"Alembic upgrade {revision or 'empty'}->{head} failed and was rolled back. "
            f"Backup: {saved_backup}"
        ) from exc
    finally:
        engine.dispose()
    if fresh:
        db.execute("PRAGMA journal_mode=WAL")
    logger.info("database.schema_ready revision=%s previous_revision=%s", head, revision)
