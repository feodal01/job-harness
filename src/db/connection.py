"""SQLite connection ownership and the shared, synchronous transaction boundary."""

import fcntl
import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from src.db.migrations import initialize_schema

UNFINISHED = ("queued", "running")


def encoded(value: object) -> str:
    return json.dumps(value, ensure_ascii=False)


class Connection:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = path.with_suffix(".lock").open("a")
        self._closed = False
        self._transaction_depth = 0
        try:
            fcntl.flock(self._lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self._lock.close()
            raise RuntimeError("This database already has a running service") from None
        try:
            self.db = sqlite3.connect(path, check_same_thread=False)
        except BaseException:
            self._lock.close()
            raise
        try:
            self.db.row_factory = sqlite3.Row
            self._initialize_schema()
            self.db.execute("PRAGMA foreign_keys=ON")
            self.db.create_function("casefold", 1, lambda value: (value or "").casefold(), deterministic=True)
        except BaseException:
            self.close()
            raise

    def _initialize_schema(self) -> None:
        initialize_schema(self.db, self.path)

    @contextmanager
    def transaction(self) -> Iterator[None]:
        """Nested writes join the outer operation; never await inside this boundary."""
        outer = self._transaction_depth == 0
        self._transaction_depth += 1
        try:
            if outer:
                self.db.execute("BEGIN")
            try:
                yield
                if outer:
                    self.db.commit()
            except BaseException:
                if outer:
                    self.db.rollback()
                raise
        finally:
            self._transaction_depth -= 1

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self.db.close()
        finally:
            self._lock.close()
