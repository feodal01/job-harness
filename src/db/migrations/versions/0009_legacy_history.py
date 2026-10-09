"""Represent the schema shipped before adoption of Alembic."""

import hashlib
import time
from pathlib import Path

from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    path = Path(__file__).parents[1] / "legacy/0009_migration_history.sql"
    op.execute(path.read_text())
    op.get_bind().exec_driver_sql(
        "INSERT INTO schema_migrations VALUES (?,?,?,?)",
        (9, path.stem, hashlib.sha256(path.read_bytes()).hexdigest(), time.time()),
    )
    op.execute("PRAGMA user_version=9")


def downgrade() -> None:
    raise RuntimeError("Restore a verified backup instead of downgrading user data")
