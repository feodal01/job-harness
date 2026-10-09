"""The single place where search conditions are applied (spec 5.3). Sources never filter."""

from dataclasses import dataclass

from src.models.filters import FilterResult, SearchFilters
from src.models.vacancy import Vacancy
from src.selection.filtering import evaluate


@dataclass(frozen=True)
class Decision:
    selection: FilterResult
    needs_more: bool  # Worth a card and an employer profile.


class FilterStage:
    def __init__(
        self, filters: SearchFilters, queries: tuple[str, ...] = (), unsupported: frozenset[str] = frozenset()
    ) -> None:
        self.filters, self.queries, self.unsupported = filters, queries, unsupported

    def evaluate(self, vacancy: Vacancy) -> FilterResult:
        return evaluate(
            vacancy,
            self.filters,
            complete=bool(vacancy.description),
            queries=self.queries,
            unsupported=self.unsupported,
        )

    def after_listing(self, vacancy: Vacancy) -> Decision:
        selection = self.evaluate(vacancy)
        return Decision(selection, needs_more=selection.status == "accepted")

    def after_detail(self, vacancy: Vacancy) -> Decision:
        return self.after_listing(vacancy)
