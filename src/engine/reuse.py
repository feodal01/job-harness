"""Reuse complete cards and employer profiles across runs and briefs."""

from src.db.protocols import CompanyStore, VacancyStore
from src.enrichment.registry import Enrichment
from src.models.company import Company
from src.models.vacancy import Vacancy


class KnownData:
    def __init__(self, vacancies: VacancyStore, companies: CompanyStore, enrichment: Enrichment) -> None:
        self.vacancies, self.companies, self.enrichment = vacancies, companies, enrichment

    def card(self, source: str, listing: Vacancy) -> Vacancy | None:
        """The stored card merged with this run's listing fields (the listing is saved first)."""
        data = self.vacancies.known_card(f"{source}:{listing.id}")
        if data is None or not data.get("description_html"):
            # A text-only record cannot reproduce the source's headings and lists.
            # Fetch it normally on discovery; do not invent markup from flattened text.
            return None
        fields = {k: v for k, v in data.items() if k in Vacancy.model_fields}
        return self.enrichment.enrich(Vacancy.model_validate(fields), source)

    @staticmethod
    def company_key(source: str, profile_url: str) -> str:
        return f"{source}:{profile_url}"

    def company(self, source: str, profile_url: str) -> Company | None:
        return self.companies.known_company(self.company_key(source, profile_url))

    def remember_company(self, source: str, profile_url: str, company: Company) -> None:
        self.companies.save_company(self.company_key(source, profile_url), company)
