"""Briefs keep source rules, not a source list: drop the old `search.sources` from saved briefs.

Such briefs get the default rules (every supported source covering their places);
see docs/brief-parameters/source-rules.md.
"""

from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "UPDATE briefs SET data = json_remove(data, '$.search.sources') "
        "WHERE json_type(data, '$.search.sources') IS NOT NULL"
    )


def downgrade() -> None:
    raise RuntimeError("Restore a verified backup instead of downgrading user data")
