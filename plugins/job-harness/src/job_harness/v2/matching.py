"""Reusable fuzzy text matching helpers."""

from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher

_TOKEN_PATTERN = re.compile(r"[\w+#]+")
_MIN_SUBSTRING_TOKEN_LENGTH = 3
_MAX_INTERVENING_TOKENS = 3
_ROLE_ALIAS_VERSION = "1"
_ROLE_PHRASE_ALIASES: tuple[tuple[tuple[str, ...], str], ...] = (
    (("software", "development", "engineer", "in", "test"), "sdet"),
    (("quality", "assurance"), "qa"),
    (("test", "automation"), "aqa"),
    (("artificial", "intelligence"), "ai"),
    (("machine", "learning"), "ml"),
)


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


@dataclass(frozen=True)
class RoleMatch:
    matched: bool
    query_variant: str | None
    matched_positions: tuple[int, ...]
    strength: float
    alias_version: str = _ROLE_ALIAS_VERSION


class RoleMatcher:
    def __init__(self, query_variants: tuple[str, ...]) -> None:
        queries: list[tuple[str, tuple[str, ...]]] = []
        for query in query_variants:
            tokens = _canonical_role_tokens(query)
            if tokens:
                queries.append((query, tokens))
        self._queries = tuple(queries)

    def match(self, title: str) -> RoleMatch:
        title_tokens = _canonical_role_tokens(title)
        candidates = tuple(
            result
            for query, query_tokens in self._queries
            if (result := _match_role_query(query, query_tokens, title_tokens)).matched
        )
        if not candidates:
            return RoleMatch(False, None, (), 0.0)
        return max(
            candidates,
            key=lambda item: (
                item.strength,
                -sum(item.matched_positions),
                item.query_variant or "",
            ),
        )


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


def _match_role_query(
    query: str,
    query_tokens: tuple[str, ...],
    title_tokens: tuple[str, ...],
) -> RoleMatch:
    chains: tuple[tuple[int, ...], ...] = tuple(
        (index,)
        for index, token in enumerate(title_tokens)
        if token == query_tokens[0]
    )
    for token in query_tokens[1:]:
        expanded = (
            chain + (index,)
            for chain in chains
            for index in range(
                chain[-1] + 1,
                min(len(title_tokens), chain[-1] + _MAX_INTERVENING_TOKENS + 2),
            )
            if title_tokens[index] == token
        )
        best_by_last: dict[int, tuple[int, ...]] = {}
        for chain in expanded:
            previous = best_by_last.get(chain[-1])
            if previous is None or _role_chain_key(chain) < _role_chain_key(previous):
                best_by_last[chain[-1]] = chain
        chains = tuple(best_by_last.values())
        if not chains:
            return RoleMatch(False, None, (), 0.0)
    if not chains:
        return RoleMatch(False, None, (), 0.0)
    positions = min(chains, key=_role_chain_key)
    total_gaps = _role_total_gaps(positions)
    exact = tuple(title_tokens[positions[0] : positions[-1] + 1]) == query_tokens
    strength = 1.0 if exact else round(max(0.5, 0.9 - total_gaps * 0.1), 2)
    return RoleMatch(True, query, positions, strength)


def _role_chain_key(positions: tuple[int, ...]) -> tuple[int, int, tuple[int, ...]]:
    return (_role_total_gaps(positions), sum(positions), positions)


def _role_total_gaps(positions: tuple[int, ...]) -> int:
    return sum(
        right - left - 1
        for left, right in zip(positions, positions[1:], strict=False)
    )


def _canonical_role_tokens(value: str) -> tuple[str, ...]:
    raw = tuple(token.casefold() for token in _TOKEN_PATTERN.findall(value))
    canonical: list[str] = []
    index = 0
    while index < len(raw):
        alias = next(
            (
                (phrase, replacement)
                for phrase, replacement in _ROLE_PHRASE_ALIASES
                if raw[index : index + len(phrase)] == phrase
            ),
            None,
        )
        if alias is None:
            canonical.append(raw[index])
            index += 1
            continue
        phrase, replacement = alias
        canonical.append(replacement)
        index += len(phrase)
    return tuple(canonical)
