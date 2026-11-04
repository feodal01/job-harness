from collections.abc import Callable
from contextlib import AbstractContextManager

from pydantic import ValidationError

from src.core.errors import InvalidSearchError, NotFoundError
from src.db.protocols import BriefStore, VacancyStore
from src.engine.pool import reevaluate_pool
from src.enrichment.registry import Enrichment
from src.geography.catalog import GeographyCatalog
from src.models.brief import BriefInput
from src.parsers.interfaces import SourceAdapter


class BriefService:
    def __init__(
        self,
        briefs: BriefStore,
        vacancies: VacancyStore,
        transaction: Callable[[], AbstractContextManager[None]],
        sources: dict[str, SourceAdapter],
        geography: GeographyCatalog,
        enrichment: Enrichment,
    ) -> None:
        self.briefs, self.vacancies, self.transaction = briefs, vacancies, transaction
        self.sources = sources
        # The application's single geography catalog and the enrichment built on it.
        self.geography, self.enrichment = geography, enrichment

    def normalize(self, brief: BriefInput) -> BriefInput:
        places = brief.filters.locations
        if not 1 <= len(places) <= 5:
            raise InvalidSearchError("Choose between 1 and 5 search locations")
        keys = {(p.scope, p.country if p.scope == "country" else p.geoname_id) for p in places}
        if len(keys) != len(places):
            raise InvalidSearchError("Duplicate selected search location")
        normalized = brief.model_copy(deep=True)
        for place in normalized.filters.locations:
            minimum, relocation = normalized.filters.location_conditions(place)
            if place.scope == "country":
                place.country_name_en = self.geography.country_names()[place.country]
            else:
                if place.geoname_id is None or not place.city_display_name:
                    raise InvalidSearchError(
                        "Choose a city or explicitly select the whole country before saving"
                    )
                city = self.geography.resolve(place.country, place.geoname_id, place.city_display_name)
                place.city = city["ascii_name"]
                place.country_name_en = city["country_name_en"]
                place.city_display_name = city["name"]
                place.region = city["region"]
            place.minimum_work_format = minimum
            place.relocation_support = relocation
        normalized.filters.minimum_work_format = None
        normalized.filters.relocation_support = False
        return normalized

    def list_briefs(self) -> list[dict]:
        return self.briefs.brief_overviews()

    def validate_saved(self, brief_id: str) -> None:
        saved = self.briefs.brief(brief_id)
        if saved is None:
            raise NotFoundError("Brief not found")
        try:
            brief = BriefInput.model_validate(
                {k: v for k, v in saved.items() if k in BriefInput.model_fields}
            )
        except ValidationError as exc:
            raise InvalidSearchError("Review and save the brief conditions before starting") from exc
        normalized = self.normalize(brief)
        if normalized.filters.locations != brief.filters.locations:
            raise InvalidSearchError("Review and save the selected cities before starting the search")

    def create(self, brief: BriefInput) -> dict:
        return self.briefs.save_brief(self.normalize(brief))

    def update(self, brief_id: str, brief: BriefInput) -> dict:
        brief = self.normalize(brief)
        with self.transaction():
            saved = self.briefs.save_brief(brief, brief_id)
            reevaluate_pool(
                self.vacancies, brief_id, brief.filters, tuple(brief.queries), self.sources, self.enrichment
            )
        return saved
