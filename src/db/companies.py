import time

from src.models.company import Company

from .connection import Connection


class CompanyRepository:
    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def known_company(self, key: str) -> Company | None:
        row = self._connection.db.execute("SELECT data FROM company_profiles WHERE id=?", (key,)).fetchone()
        return Company.model_validate_json(row[0]) if row else None

    def save_company(self, key: str, company: Company) -> None:
        with self._connection.transaction():
            self._connection.db.execute(
                "INSERT INTO company_profiles VALUES (?,?,?) ON CONFLICT(id) DO NOTHING",
                (key, company.model_dump_json(), time.time()),
            )
