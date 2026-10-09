"""Apply Alembic head with the same safety guarantees as normal service startup."""

from src.core.config import Settings
from src.core.logger import configure_logging
from src.db import Database

if __name__ == "__main__":
    settings = Settings()
    configure_logging(settings)
    database = Database(settings.database_path)
    try:
        revision = database._connection.db.execute("SELECT version_num FROM alembic_version").fetchone()[0]
        print(f"Database ready: {settings.database_path}; Alembic revision {revision}")
    finally:
        database.close()
