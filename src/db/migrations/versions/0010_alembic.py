"""Replace the custom migration journal with Alembic version tracking."""

from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_table("schema_migrations")
    # Compatibility guard: older application releases must refuse this database.
    # Alembic's revision, not this constant, tracks all subsequent schema changes.
    op.execute("PRAGMA user_version=10")


def downgrade() -> None:
    raise RuntimeError("Restore a verified backup instead of downgrading user data")
