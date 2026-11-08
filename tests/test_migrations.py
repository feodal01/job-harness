"""Real SQLite files: backups, data preservation, schema drift and atomic failure."""

import hashlib
import json
import shutil
import sqlite3
from pathlib import Path

import pytest

from src.core.errors import DatabaseVersionError
from src.db import Database
from src.db.migrations import runner


def legacy_database(path: Path, schema_version: int = 8) -> None:
    with sqlite3.connect(path) as db:
        db.executescript(runner.BASE_SCHEMA.read_text())
        db.execute("PRAGMA user_version=8")
        db.execute("INSERT INTO briefs VALUES ('b',?,1)", (json.dumps({"name": "Мой бриф"}),))
        db.execute(
            "INSERT INTO runs (id,brief_id,request,status,created_at) VALUES ('r','b',?,'complete',1)",
            (json.dumps({"historical": "unchanged"}),),
        )
        db.execute("INSERT INTO vacancies VALUES ('hh:1','{}',1,2,2)")
        db.execute(
            "INSERT INTO run_vacancies VALUES ('r','hh:1',?,'complete',NULL,1)",
            (json.dumps({"description": "Исходное описание"}),),
        )
        db.execute(
            "INSERT INTO brief_vacancies VALUES ('b','hh:1','r','r',1,2,'{}','applied','Моя заметка',2)"
        )
        db.execute("INSERT INTO company_profiles VALUES ('hh:1','{}',2)")
        db.execute("INSERT INTO events VALUES (1,'r',2,'search.complete','{}')")
        db.execute("INSERT INTO resource_state VALUES ('hh','{}')")
        if schema_version == 9:
            migration = runner.SCRIPT_LOCATION / "legacy/0009_migration_history.sql"
            db.executescript(migration.read_text())
            db.execute(
                "INSERT INTO schema_migrations VALUES (?,?,?,?)",
                (9, migration.stem, hashlib.sha256(migration.read_bytes()).hexdigest(), 1),
            )
            db.execute("PRAGMA user_version=9")


def business_rows(path: Path) -> dict[str, list[tuple]]:
    with sqlite3.connect(path) as db:
        tables = [
            r[0]
            for r in db.execute(
                "SELECT name FROM sqlite_schema WHERE type='table' "
                "AND name NOT IN ('schema_migrations','alembic_version') ORDER BY name"
            )
        ]
        return {t: db.execute(f'SELECT * FROM "{t}" ORDER BY rowid').fetchall() for t in tables}


def version(path: Path) -> int:
    with sqlite3.connect(path) as db:
        return db.execute("PRAGMA user_version").fetchone()[0]


@pytest.mark.parametrize("schema_version", [8, 9])
def test_legacy_backup_upgrade_preserves_every_business_row_and_runs_once(
    tmp_path: Path, schema_version: int
) -> None:
    path = tmp_path / "search.sqlite"
    legacy_database(path, schema_version)
    before = business_rows(path)
    Database(path).close()
    assert version(path) == runner.ALEMBIC_FORMAT_VERSION
    assert business_rows(path) == before
    backups = list((tmp_path / "backups").glob("*.sqlite"))
    assert len(backups) == 1
    assert version(backups[0]) == schema_version
    assert business_rows(backups[0]) == before
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT version_num FROM alembic_version").fetchall() == [("0011",)]
        assert not db.execute("SELECT 1 FROM sqlite_schema WHERE name='schema_migrations'").fetchall()
        assert db.execute("PRAGMA integrity_check").fetchone() == ("ok",)
    Database(path).close()
    assert list((tmp_path / "backups").glob("*.sqlite")) == backups


def test_fresh_nested_database_needs_no_backup(tmp_path: Path) -> None:
    path = tmp_path / "absent/nested/search.sqlite"
    Database(path).close()
    assert version(path) == runner.ALEMBIC_FORMAT_VERSION
    assert not (path.parent / "backups").exists()


@pytest.mark.parametrize("failure", [False, True])
@pytest.mark.parametrize("adopted", [False, True])
def test_all_pending_alembic_revisions_commit_or_rollback_together(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: bool, adopted: bool
) -> None:
    path = tmp_path / "search.sqlite"
    legacy_database(path, 9)
    if adopted:
        Database(path).close()
    before = business_rows(path)
    previous_backups = set((tmp_path / "backups").glob("*.sqlite"))
    scripts = tmp_path / "db/migrations"
    shutil.copytree(runner.SCRIPT_LOCATION, scripts)
    shutil.copyfile(runner.BASE_SCHEMA, scripts.parent / "schema.sql")
    revision = scripts / "versions/0012_test.py"
    revision.write_text(
        'from alembic import op\nrevision="0012"\ndown_revision="0011"\n'
        "def upgrade():\n"
        '    op.execute("ALTER TABLE briefs ADD COLUMN test_value TEXT")\n'
        "    op.execute(\"UPDATE brief_vacancies SET note='migration edit'\")\n"
        + ('    op.execute("SELECT * FROM missing_table")\n' if failure else "")
    )
    monkeypatch.setattr(runner, "SCRIPT_LOCATION", scripts)
    if failure:
        with pytest.raises(DatabaseVersionError, match="rolled back"):
            Database(path)
        assert version(path) == (runner.ALEMBIC_FORMAT_VERSION if adopted else 9)
        assert business_rows(path) == before
        with sqlite3.connect(path) as db:
            if adopted:
                assert db.execute("SELECT version_num FROM alembic_version").fetchone() == ("0011",)
            else:
                assert not db.execute("SELECT 1 FROM sqlite_schema WHERE name='alembic_version'").fetchall()
                assert db.execute("SELECT version FROM schema_migrations").fetchone() == (9,)
    else:
        Database(path).close()
        with sqlite3.connect(path) as db:
            assert db.execute("SELECT version_num FROM alembic_version").fetchone() == ("0012",)
            assert db.execute("SELECT note FROM brief_vacancies").fetchone() == ("migration edit",)
    new_backups = set((tmp_path / "backups").glob("*.sqlite")) - previous_backups
    assert len(new_backups) == 1
    saved = new_backups.pop()
    assert business_rows(saved) == before


def test_backup_failure_prevents_any_upgrade(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "search.sqlite"
    legacy_database(path)
    before = path.read_bytes()

    def cannot_backup(*args: object) -> Path:
        raise PermissionError("backup denied")

    monkeypatch.setattr(runner, "backup", cannot_backup)
    with pytest.raises(PermissionError, match="backup denied"):
        Database(path)
    assert path.read_bytes() == before


@pytest.mark.parametrize("current", [False, True])
def test_schema_drift_is_rejected_without_repair(tmp_path: Path, current: bool) -> None:
    path = tmp_path / "search.sqlite"
    if current:
        Database(path).close()
    else:
        legacy_database(path)
    with sqlite3.connect(path) as db:
        db.execute("ALTER TABLE briefs ADD COLUMN unexpected TEXT")
    before = business_rows(path)
    with pytest.raises(DatabaseVersionError, match="differs"):
        Database(path)
    assert business_rows(path) == before
    assert not (tmp_path / "backups").exists()


def test_broken_references_are_not_migrated(tmp_path: Path) -> None:
    path = tmp_path / "search.sqlite"
    legacy_database(path)
    with sqlite3.connect(path) as db:
        db.execute("UPDATE runs SET brief_id='missing'")
    with pytest.raises(DatabaseVersionError, match="broken references"):
        Database(path)
    assert version(path) == 8
    assert not (tmp_path / "backups").exists()


def test_modified_legacy_migration_history_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "search.sqlite"
    legacy_database(path, 9)
    with sqlite3.connect(path) as db:
        db.execute("UPDATE schema_migrations SET checksum='changed'")
    with pytest.raises(DatabaseVersionError, match="checksums"):
        Database(path)


def test_backup_includes_uncheckpointed_wal(tmp_path: Path) -> None:
    path = tmp_path / "search.sqlite"
    legacy_database(path)
    writer = sqlite3.connect(path)
    try:
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("PRAGMA wal_autocheckpoint=0")
        writer.execute("UPDATE brief_vacancies SET note='Последняя запись в WAL'")
        writer.commit()
        assert Path(str(path) + "-wal").stat().st_size > 0
        Database(path).close()
        backup = next((tmp_path / "backups").glob("*.sqlite"))
        with sqlite3.connect(backup) as db:
            assert db.execute("SELECT note FROM brief_vacancies").fetchone() == ("Последняя запись в WAL",)
    finally:
        writer.close()


@pytest.mark.parametrize("revision", ["future-unknown", "0008"])
def test_unknown_or_false_alembic_revision_is_rejected(tmp_path: Path, revision: str) -> None:
    path = tmp_path / "search.sqlite"
    Database(path).close()
    with sqlite3.connect(path) as db:
        db.execute("UPDATE alembic_version SET version_num=?", (revision,))
    before = business_rows(path)
    with pytest.raises(DatabaseVersionError, match="revision"):
        Database(path)
    assert business_rows(path) == before
    assert not (tmp_path / "backups").exists()


def test_saved_briefs_lose_the_old_source_list_and_keep_everything_else(tmp_path: Path) -> None:
    path = tmp_path / "search.sqlite"
    legacy_database(path, 9)
    old = {
        "name": "Старый",
        "queries": ["QA"],
        "search": {
            "sources": ["hh_ru"],
            "limits": {"hh_ru": {"max_items_per_query": 5}},
        },
    }
    untouched = {"name": "Без источников", "search": {"company_profiles": False}}
    with sqlite3.connect(path) as db:
        db.execute("INSERT INTO briefs VALUES ('old',?,1)", (json.dumps(old),))
        db.execute("INSERT INTO briefs VALUES ('plain',?,1)", (json.dumps(untouched),))
    Database(path).close()
    with sqlite3.connect(path) as db:
        rows = dict(db.execute("SELECT id, data FROM briefs WHERE id IN ('old','plain')").fetchall())
    assert json.loads(rows["old"]) == {
        "name": "Старый",
        "queries": ["QA"],
        "search": {"limits": {"hh_ru": {"max_items_per_query": 5}}},
    }
    assert json.loads(rows["plain"]) == untouched
