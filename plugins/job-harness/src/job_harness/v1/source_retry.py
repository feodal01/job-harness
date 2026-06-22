"""Helpers for classifying sources and validating search_retry requests."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from job_harness.v1.run_journal import JournalSnapshot
from job_harness.v1.types import SourceState, SourceStatus


class RetryAction(StrEnum):
    RETRY = "retry"
    SKIP_OK = "skip_ok"
    SKIP_POLICY = "skip_policy"


_RETRYABLE_STATES: frozenset[SourceState] = frozenset(
    {
        SourceState.TIMEOUT,
        SourceState.ERROR,
        SourceState.RATE_LIMITED,
        SourceState.BLOCKED,
        SourceState.CANCELLED,
        SourceState.PARTIAL,
    }
)

_POLICY_SKIP_STATES: frozenset[SourceState] = frozenset(
    {
        SourceState.SKIPPED,
        SourceState.SKIPPED_UNSUPPORTED_FLAG,
    }
)


def classify_source_for_retry(status: SourceStatus) -> RetryAction:
    if status.state == SourceState.OK:
        return RetryAction.SKIP_OK
    if status.state in _POLICY_SKIP_STATES:
        return RetryAction.SKIP_POLICY
    if status.state in _RETRYABLE_STATES:
        return RetryAction.RETRY
    return RetryAction.SKIP_POLICY


def build_retryable_sources(snap: JournalSnapshot) -> list[str]:
    return sorted(
        name
        for name, status in snap.sources.items()
        if classify_source_for_retry(status) == RetryAction.RETRY
    )


def build_sources_by_state(snap: JournalSnapshot) -> dict[str, list[str]]:
    grouped: dict[str, list[str]] = {}
    for name, status in snap.sources.items():
        key = status.state.value
        grouped.setdefault(key, []).append(name)
    for names in grouped.values():
        names.sort()
    return dict(sorted(grouped.items()))


def parse_sources_csv(sources: str) -> list[str]:
    return [part.strip() for part in sources.split(",") if part.strip()]


@dataclass(frozen=True)
class RetryValidation:
    retried_sources: tuple[str, ...] = ()
    skipped_sources: dict[str, dict[str, str]] = field(default_factory=dict)
    unknown_sources: tuple[str, ...] = ()
    not_in_run_sources: tuple[str, ...] = ()

    @property
    def has_invalid_sources(self) -> bool:
        return bool(self.unknown_sources or self.not_in_run_sources)

    @property
    def can_start(self) -> bool:
        return bool(self.retried_sources) and not self.has_invalid_sources


def validate_retry_sources(
    requested: list[str],
    snap: JournalSnapshot,
    registered_names: set[str],
) -> RetryValidation:
    sources_in_run = set(snap.sources.keys())
    unknown = tuple(name for name in requested if name not in registered_names)
    not_in_run = tuple(
        name
        for name in requested
        if name in registered_names and name not in sources_in_run
    )
    if unknown or not_in_run:
        return RetryValidation(
            unknown_sources=unknown,
            not_in_run_sources=not_in_run,
        )

    retried: list[str] = []
    skipped: dict[str, dict[str, str]] = {}
    for name in requested:
        status = snap.sources[name]
        action = classify_source_for_retry(status)
        if action == RetryAction.RETRY:
            retried.append(name)
            continue
        if action == RetryAction.SKIP_OK:
            skipped[name] = {
                "reason": "already_ok",
                "state": status.state.value,
            }
        else:
            skipped[name] = {
                "reason": "policy_skip",
                "state": status.state.value,
            }
    return RetryValidation(
        retried_sources=tuple(retried),
        skipped_sources=skipped,
    )
