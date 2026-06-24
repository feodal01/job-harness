"""Reusable fuzzy text matching helpers."""

from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher

_TOKEN_PATTERN = re.compile(r"[\w+#]+")
_MIN_SUBSTRING_TOKEN_LENGTH = 3


@dataclass(frozen=True)
class FuzzyBounds:
    token_score: float = 0.78
    short_token_score: float = 0.78
    short_token_length: int = 2

    def __post_init__(self) -> None:
        for field_name in ("token_score", "short_token_score"):
            value = getattr(self, field_name)
            if not 0 <= value <= 1:
                raise ValueError(f"{field_name} must be between 0 and 1")
        if self.short_token_length < 1:
            raise ValueError("short_token_length must be >= 1")


DEFAULT_FUZZY_BOUNDS = FuzzyBounds()


def fuzzy_tokens_match(query: str, text: str, *, bounds: FuzzyBounds = DEFAULT_FUZZY_BOUNDS) -> bool:
    """Return true when every query token has a close token in text."""

    query_tokens = normalized_tokens(query)
    if not query_tokens:
        return True
    text_tokens = normalized_tokens(text)
    return bool(text_tokens) and all(
        _token_has_match(query_token, text_tokens, bounds=bounds)
        for query_token in query_tokens
    )


def fuzzy_any_match(
    query_values: tuple[str, ...],
    text: str,
    *,
    bounds: FuzzyBounds = DEFAULT_FUZZY_BOUNDS,
) -> bool:
    return any(fuzzy_tokens_match(query_value, text, bounds=bounds) for query_value in query_values)


def normalized_tokens(text: str) -> tuple[str, ...]:
    normalized = text.casefold().replace("ё", "е")
    return tuple(match.group(0) for match in _TOKEN_PATTERN.finditer(normalized))


def _token_has_match(query_token: str, text_tokens: tuple[str, ...], *, bounds: FuzzyBounds) -> bool:
    min_score = bounds.short_token_score if len(query_token) <= bounds.short_token_length else bounds.token_score
    return any(_token_score(query_token, text_token) >= min_score for text_token in text_tokens)


def _token_score(query_token: str, text_token: str) -> float:
    if query_token == text_token:
        return 1.0
    if len(query_token) >= _MIN_SUBSTRING_TOKEN_LENGTH and query_token in text_token:
        return 1.0
    return SequenceMatcher(None, query_token, text_token).ratio()
