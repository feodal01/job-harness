"""Alembic uses the transaction prepared by the application's safe upgrade entrypoint."""

from alembic import context

connection = context.config.attributes.get("connection")
if connection is None:
    raise RuntimeError(
        "Run `python -m src.db.migrations` to upgrade with locking, backup and validation. "
        "Direct upgrade/stamp/downgrade bypasses those safeguards."
    )
context.configure(connection=connection, target_metadata=None, transactional_ddl=True, render_as_batch=True)
with context.begin_transaction():
    context.run_migrations()
