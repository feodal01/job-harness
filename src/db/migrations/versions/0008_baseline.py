"""Initial application schema, matching the shipped SQLite version 8."""

import sqlite3
from pathlib import Path

from alembic import op

revision = "0008"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # complete_statement respects SQL comments and quoted semicolons. Never use
    # sqlite3.executescript here: it would commit the surrounding transaction.
    sql = (Path(__file__).parents[2] / "schema.sql").read_text()
    statement = ""
    for line in sql.splitlines(keepends=True):
        statement += line
        if sqlite3.complete_statement(statement):
            op.get_bind().exec_driver_sql(statement)
            statement = ""
    if statement.strip():
        raise RuntimeError("Incomplete baseline SQL statement")
    op.execute("PRAGMA user_version=8")


def downgrade() -> None:
    raise RuntimeError("Restore a verified backup instead of dropping user data")
