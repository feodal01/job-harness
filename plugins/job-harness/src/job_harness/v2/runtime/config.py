"""Service-owned runtime configuration for v2 search execution."""

from __future__ import annotations

import json
from dataclasses import dataclass
from importlib.resources import files

from job_harness.v2.runtime.retry import RetryPolicy
from job_harness.v2.serialization import JsonObject

_CONFIG_RESOURCE = "search_service_config.json"


@dataclass(frozen=True)
class RetryServiceConfig:
    max_attempts: int
    backoff_seconds: float

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("retry.max_attempts must be >= 1")
        if self.backoff_seconds < 0:
            raise ValueError("retry.backoff_seconds must be >= 0")

    def to_retry_policy(self) -> RetryPolicy:
        return RetryPolicy(
            max_attempts=self.max_attempts,
            backoff_seconds=self.backoff_seconds,
        )


@dataclass(frozen=True)
class DetailServiceConfig:
    per_source_concurrency: int
    default_request_delay_seconds: float
    request_delay_seconds_by_source: dict[str, float]
    stop_on_blocked: bool
    stop_on_rate_limited: bool

    def __post_init__(self) -> None:
        if self.per_source_concurrency < 1:
            raise ValueError("detail.per_source_concurrency must be >= 1")
        if self.default_request_delay_seconds < 0:
            raise ValueError("detail.default_request_delay_seconds must be >= 0")
        for source_id, delay in self.request_delay_seconds_by_source.items():
            if not source_id.strip():
                raise ValueError("detail.request_delay_seconds_by_source keys must be non-empty")
            if delay < 0:
                raise ValueError("detail.request_delay_seconds_by_source delays must be >= 0")

    def delay_for_source(self, source_id: str) -> float:
        return self.request_delay_seconds_by_source.get(source_id, self.default_request_delay_seconds)


@dataclass(frozen=True)
class ApplicationChannelServiceConfig:
    enabled: bool = True


@dataclass(frozen=True)
class SearchServiceConfig:
    source_attempt_timeout_seconds: float
    run_timeout_seconds: float
    fetch_timeout_seconds: float
    retry: RetryServiceConfig
    detail: DetailServiceConfig
    application_channels: ApplicationChannelServiceConfig = ApplicationChannelServiceConfig()

    def __post_init__(self) -> None:
        if self.source_attempt_timeout_seconds <= 0:
            raise ValueError("source_attempt_timeout_seconds must be > 0")
        if self.run_timeout_seconds <= 0:
            raise ValueError("run_timeout_seconds must be > 0")
        if self.fetch_timeout_seconds <= 0:
            raise ValueError("fetch_timeout_seconds must be > 0")

    @classmethod
    def from_package_resource(cls) -> SearchServiceConfig:
        raw = files("job_harness.v2.runtime").joinpath(_CONFIG_RESOURCE).read_text(encoding="utf-8")
        parsed = json.loads(raw)
        if not isinstance(parsed, dict):
            raise ValueError("search service config must be a JSON object")
        return cls.from_json_object(parsed)

    @classmethod
    def from_json_object(cls, payload: JsonObject) -> SearchServiceConfig:
        retry = _required_object(payload, "retry")
        detail = _required_object(payload, "detail")
        application_channels = _optional_object(payload, "application_channels")
        return cls(
            source_attempt_timeout_seconds=_required_float(payload, "source_attempt_timeout_seconds"),
            run_timeout_seconds=_required_float(payload, "run_timeout_seconds"),
            fetch_timeout_seconds=_required_float(payload, "fetch_timeout_seconds"),
            retry=RetryServiceConfig(
                max_attempts=_required_int(retry, "max_attempts"),
                backoff_seconds=_required_float(retry, "backoff_seconds"),
            ),
            detail=DetailServiceConfig(
                per_source_concurrency=_required_int(detail, "per_source_concurrency"),
                default_request_delay_seconds=_required_float(detail, "default_request_delay_seconds"),
                request_delay_seconds_by_source=_float_mapping(
                    detail,
                    "request_delay_seconds_by_source",
                ),
                stop_on_blocked=_required_bool(detail, "stop_on_blocked"),
                stop_on_rate_limited=_required_bool(detail, "stop_on_rate_limited"),
            ),
            application_channels=ApplicationChannelServiceConfig(
                enabled=_optional_bool(application_channels, "enabled", default=True),
            ),
        )


def _required_object(payload: JsonObject, key: str) -> JsonObject:
    value = payload.get(key)
    if not isinstance(value, dict):
        raise ValueError(f"{key} must be a JSON object")
    return value


def _optional_object(payload: JsonObject, key: str) -> JsonObject:
    value = payload.get(key)
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"{key} must be a JSON object")
    return value


def _required_float(payload: JsonObject, key: str) -> float:
    value = payload.get(key)
    if not isinstance(value, int | float) or isinstance(value, bool):
        raise ValueError(f"{key} must be a number")
    return float(value)


def _required_int(payload: JsonObject, key: str) -> int:
    value = payload.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{key} must be an integer")
    return value


def _required_bool(payload: JsonObject, key: str) -> bool:
    value = payload.get(key)
    if not isinstance(value, bool):
        raise ValueError(f"{key} must be a boolean")
    return value


def _optional_bool(payload: JsonObject, key: str, *, default: bool) -> bool:
    value = payload.get(key)
    if value is None:
        return default
    if not isinstance(value, bool):
        raise ValueError(f"{key} must be a boolean")
    return value


def _float_mapping(payload: JsonObject, key: str) -> dict[str, float]:
    value = payload.get(key)
    if not isinstance(value, dict):
        raise ValueError(f"{key} must be a JSON object")
    parsed: dict[str, float] = {}
    for raw_key, raw_value in value.items():
        if not isinstance(raw_key, str):
            raise ValueError(f"{key} keys must be strings")
        if not isinstance(raw_value, int | float) or isinstance(raw_value, bool):
            raise ValueError(f"{key}.{raw_key} must be a number")
        parsed[raw_key] = float(raw_value)
    return parsed
