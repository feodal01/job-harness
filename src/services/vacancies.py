from src.db.protocols import VacancyStore


class VacancyService:
    def __init__(self, store: VacancyStore) -> None:
        self.store = store

    def list_vacancies(
        self,
        brief_id: str,
        page: int = 1,
        page_size: int = 50,
        selection: str = "candidates",
        status: str = "all",
        q: str = "",
        sort: str = "first_seen",
        source: str = "",
    ) -> dict:
        return self.store.pool_page(brief_id, page, page_size, selection, status, q, sort, source)

    def adjacent(
        self,
        brief_id: str,
        vacancy_key: str,
        direction: str = "next",
        page_size: int = 50,
        selection: str = "candidates",
        status: str = "all",
        q: str = "",
        sort: str = "first_seen",
        anchor_time: float | None = None,
        source: str = "",
    ) -> dict:
        return self.store.pool_adjacent(
            brief_id, vacancy_key, direction, page_size, selection, status, q, sort, anchor_time, source
        )

    def get(self, brief_id: str, vacancy_key: str) -> dict:
        return self.store.pool_item(brief_id, vacancy_key)

    def set_status(self, brief_id: str, vacancy_key: str, status: str, note: str) -> dict:
        self.store.set_pool_status(brief_id, vacancy_key, status, note)
        return self.store.pool_item(brief_id, vacancy_key)
