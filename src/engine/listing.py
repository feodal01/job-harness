"""Order of listing requests: every phrase is walked, phrases take turns, limits are per phrase (5.1)."""

from collections import deque
from dataclasses import dataclass, field

from src.engine.models import Limits
from src.parsers.interfaces import SearchPage, SearchRequest


@dataclass
class NativeRequest:
    """One native request of a phrase and its pagination (e.g. one HH area)."""

    pending: deque[SearchRequest]
    pages: int = 0
    visited: set[tuple[str, str, str]] = field(default_factory=set)


@dataclass
class Phrase:
    query: str
    requests: list[NativeRequest]
    new_items: int = 0
    turn: int = 0  # Next native request to serve; requests of one phrase also take turns.

    def next_request(self) -> NativeRequest | None:
        for offset in range(len(self.requests)):
            index = (self.turn + offset) % len(self.requests)
            if self.requests[index].pending:
                self.turn = index + 1
                return self.requests[index]
        return None


@dataclass(frozen=True)
class Step:
    phrase: Phrase
    native: NativeRequest
    request: SearchRequest

    @property
    def page(self) -> int:
        return self.native.pages


class ListingWalker:
    """Round-robin over phrases: the first pages of all phrases are requested before anyone's second page."""

    def __init__(self, requests: tuple[SearchRequest, ...], limits: Limits) -> None:
        self.limits = limits
        grouped: dict[str, list[NativeRequest]] = {}
        for request in requests:
            grouped.setdefault(request.query, []).append(NativeRequest(deque([request])))
        self.phrases = [Phrase(query, native) for query, native in grouped.items()]
        self._turn = 0
        self.limit_reached = False

    def next(self) -> Step | None:
        for offset in range(len(self.phrases)):
            phrase = self.phrases[(self._turn + offset) % len(self.phrases)]
            if self.capacity(phrase) == 0:
                continue
            native = phrase.next_request()
            if native is None:
                continue
            request = native.pending.popleft()
            if request.stage == "listing" and native.pages >= self.limits.max_pages_per_query:
                native.pending.clear()
                self.limit_reached = True
                continue
            self._turn = (self._turn + offset + 1) % len(self.phrases)
            return Step(phrase, native, request)
        return None

    def capacity(self, phrase: Phrase) -> int:
        """How many more new unique vacancies this phrase may contribute."""
        left = self.limits.max_items_per_query - phrase.new_items
        if left <= 0 and any(n.pending for n in phrase.requests):
            self.limit_reached = True
        return max(left, 0)

    def repeated(self, step: Step) -> bool:
        if step.request.identity in step.native.visited:
            return True
        step.native.visited.add(step.request.identity)
        return False

    def finished(self, step: Step, page: SearchPage, new_items: int) -> None:
        if step.request.stage == "listing":
            step.native.pages += 1
        step.phrase.new_items += new_items
        step.native.pending.extend(page.next_requests)
