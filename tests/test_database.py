"""Storage lifetime, version gates, and atomic repository operations use temporary files only."""

import fcntl
import json
import sqlite3
from pathlib import Path

import pytest

from src.core.errors import DatabaseVersionError, NotFoundError
from src.db import Database
from src.db import connection as connection_module
from src.db.migrations import ALEMBIC_FORMAT_VERSION
from src.models.brief import BriefInput
from src.models.vacancy import Vacancy


def assert_unlocked(path: Path) -> None:
    with path.with_suffix(".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(lock, fcntl.LOCK_UN)


def snapshot(path: Path) -> list[str]:
    connection = sqlite3.connect(path)
    try:
        return list(connection.iterdump())
    finally:
        connection.close()


def test_empty_database_creates_current_schema_and_enforces_references(tmp_path: Path) -> None:
    path = tmp_path / "search.sqlite"
    database = Database(path)
    try:
        assert database._connection.db.execute("PRAGMA user_version").fetchone()[0] == ALEMBIC_FORMAT_VERSION
        assert database.stats() == {"briefs": 0, "runs": 0, "vacancies": 0, "events": 0}
        assert database.resources.resource("source") == {"failures": 0, "open_until": 0, "next_request_at": 0}
        with pytest.raises(sqlite3.IntegrityError):
            database.events.event("absent", "invalid")
        assert database.stats()["events"] == 0
    finally:
        database.close()
    database.close()
    assert_unlocked(path)


@pytest.mark.parametrize("version", [0, 1, 7, 10])
def test_unsupported_database_is_untouched_and_releases_handles(tmp_path: Path, version: int) -> None:
    path = tmp_path / "search.sqlite"
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE sentinel (value TEXT)")
    connection.execute("INSERT INTO sentinel VALUES ('historical data')")
    connection.execute(f"PRAGMA user_version={version}")
    connection.commit()
    connection.close()
    before = path.read_bytes()
    for _ in range(2):
        with pytest.raises(DatabaseVersionError, match=f"schema version {version}"):
            Database(path)
        assert path.read_bytes() == before
        assert not path.with_name(path.name + "-wal").exists()
        assert_unlocked(path)


def test_current_version_open_preserves_metadata_snapshots_and_pending_work(tmp_path: Path) -> None:
    path = tmp_path / "search.sqlite"
    database = Database(path)
    brief = database.briefs.save_brief(BriefInput(name="Saved", queries=["QA"]))
    run = database.runs.create_run(brief["id"], {"old_option": "original"}, {"source": {"status": "queued"}})
    historical = {
        "name": "Saved",
        "migration_note": "keep",
        "filters_migration_notice": "review",
        "filters": {},
    }
    with database.transaction():
        database._connection.db.execute(
            "UPDATE briefs SET data=? WHERE id=?", (json.dumps(historical), brief["id"])
        )
    database.close()
    before = snapshot(path)
    reopened = Database(path)
    try:
        loaded = reopened.briefs.brief(brief["id"])
        assert loaded is not None
        assert loaded["migration_note"] == "keep"
        assert loaded["filters_migration_notice"] == "review"
        current = reopened.runs.run(run["id"])
        assert current["status"] == "queued"
        assert current["request"] == run["request"]
        assert current["sources"] == {"source": {"status": "queued"}}
    finally:
        reopened.close()
    assert snapshot(path) == before


def test_recovery_is_explicit_and_only_changes_unfinished_work(tmp_path: Path) -> None:
    database = Database(tmp_path / "search.sqlite")
    try:
        brief = database.briefs.save_brief(BriefInput(name="Saved"))
        sources = {"pending": {"status": "running", "metrics": {"found": 1}}, "done": {"status": "completed"}}
        run = database.runs.create_run(brief["id"], {}, sources)
        database.vacancies.save_vacancy(
            run["id"], brief["id"], Vacancy(id="1", title="QA", url="https://example.test/1"), source="test"
        )
        done = database.runs.create_run(brief["id"], {}, {"done": {"status": "completed"}})
        database.runs.progress(done["id"], {"found": 0}, status="completed")
        completed_before = database.runs.run(done["id"])
        database.runs.recover_unfinished()
        recovered = database.runs.run(run["id"])
        assert recovered["status"] == "interrupted"
        assert recovered["finished_at"] is not None
        assert recovered["sources"]["pending"] == {"status": "interrupted", "metrics": {"found": 1}}
        assert recovered["sources"]["done"] == sources["done"]
        assert database.vacancies.results(run["id"])[0]["detail_status"] == "interrupted"
        assert database.runs.run(done["id"]) == completed_before
        database.runs.recover_unfinished()
        assert database.runs.run(run["id"]) == recovered
    finally:
        database.close()


def test_nested_repository_writes_roll_back_as_one_operation(tmp_path: Path) -> None:
    database = Database(tmp_path / "search.sqlite")
    try:
        brief = database.briefs.save_brief(BriefInput(name="Original"))
        run = database.runs.create_run(brief["id"], {}, {})
        database.vacancies.save_vacancy(
            run["id"],
            brief["id"],
            Vacancy(id="1", title="QA", url="https://example.test/1"),
            source="test",
            selection={"status": "accepted"},
        )
        with pytest.raises(RuntimeError, match="selection failed"):
            with database.transaction():
                database.briefs.save_brief(BriefInput(name="Changed"), brief["id"])
                with database.transaction():
                    database.vacancies.set_pool_selection(brief["id"], "test:1", {"status": "rejected"})
                    database.events.event(run["id"], "selection_changed")
                    database.runs.progress(run["id"], {"changed": 1})
                raise RuntimeError("selection failed")
        assert database.briefs.brief(brief["id"]) == brief
        assert database.vacancies.pool_item(brief["id"], "test:1")["selection"] == {"status": "accepted"}
        assert database.events.events(run["id"]) == []
        assert database.runs.run(run["id"])["status"] == "queued"
        with database.transaction():
            database.briefs.save_brief(BriefInput(name="Committed"), brief["id"])
            database.vacancies.set_pool_selection(brief["id"], "test:1", {"status": "rejected"})
        assert database.briefs.brief(brief["id"])["name"] == "Committed"
        assert database.vacancies.pool_item(brief["id"], "test:1")["selection"]["status"] == "rejected"
    finally:
        database.close()


def test_exclusive_lock_is_released_after_close(tmp_path: Path) -> None:
    path = tmp_path / "search.sqlite"
    database = Database(path)
    try:
        with pytest.raises(RuntimeError, match="already has a running service"):
            Database(path)
        assert database.stats()["briefs"] == 0
    finally:
        database.close()
    reopened = Database(path)
    reopened.close()
    assert_unlocked(path)


def test_connection_failure_releases_file_lock(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "search.sqlite"

    def fail_connect(*args: object, **kwargs: object) -> sqlite3.Connection:
        raise sqlite3.OperationalError("cannot connect")

    with monkeypatch.context() as context:
        context.setattr(connection_module.sqlite3, "connect", fail_connect)
        with pytest.raises(sqlite3.OperationalError, match="cannot connect"):
            Database(path)
    assert_unlocked(path)
    database = Database(path)
    database.close()


def test_initialization_failure_closes_connection_and_releases_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "search.sqlite"
    connections: list[sqlite3.Connection] = []

    def fail_initialize(connection: connection_module.Connection) -> None:
        connections.append(connection.db)
        raise RuntimeError("initialization failed")

    with monkeypatch.context() as context:
        context.setattr(connection_module.Connection, "_initialize_schema", fail_initialize)
        with pytest.raises(RuntimeError, match="initialization failed"):
            Database(path)
    with pytest.raises(sqlite3.ProgrammingError, match="closed database"):
        connections[0].execute("SELECT 1")
    assert_unlocked(path)
    database = Database(path)
    database.close()


def test_missing_entities_raise_domain_error(tmp_path: Path) -> None:
    database = Database(tmp_path / "search.sqlite")
    try:
        with pytest.raises(NotFoundError):
            database.runs.run("absent")
        with pytest.raises(NotFoundError):
            database.briefs.save_brief(BriefInput(name="Saved"), "absent")
        with pytest.raises(NotFoundError):
            database.runs.create_run("absent", {}, {})
        with pytest.raises(NotFoundError):
            database.runs.update_source("absent", "source", {})
        with pytest.raises(NotFoundError):
            database.runs.progress("absent", {})
        with pytest.raises(NotFoundError):
            database.vacancies.set_pool_selection("absent", "absent", {})
    finally:
        database.close()
