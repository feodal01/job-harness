"""Service-owned runtime configuration for v2 search execution."""

from __future__ import annotations

import json
from dataclasses import dataclass
from importlib.resources import files

from job_harness.v2.runtime.request_retry import RequestRetryPolicy
from job_harness.v2.serialization import JsonObject

_CONFIG_RESOURCE = "search_service_config.json"


@dataclass(frozen=True)
class RequestRetryServiceConfig:
    max_attempts: int
    base_delay_seconds: float
    max_delay_seconds: float
    request_budget_seconds: float

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("request_retry.max_attempts must be >= 1")
        if self.base_delay_seconds < 0:
            raise ValueError("request_retry.base_delay_seconds must be >= 0")
        if self.max_delay_seconds < self.base_delay_seconds:
            raise ValueError("request_retry.max_delay_seconds must be >= base_delay_seconds")
        if self.request_budget_seconds <= 0:
            raise ValueError("request_retry.request_budget_seconds must be > 0")

    def to_policy(self, *, attempt_timeout_seconds: float) -> RequestRetryPolicy:
        return RequestRetryPolicy(
            max_attempts=self.max_attempts,
            attempt_timeout_seconds=attempt_timeout_seconds,
            base_delay_seconds=self.base_delay_seconds,
            max_delay_seconds=self.max_delay_seconds,
            request_budget_seconds=self.request_budget_seconds,
        )


@dataclass(frozen=True)
class ResourceServiceConfig:
    default_max_concurrency: int
    default_min_interval_seconds: float
    max_concurrency_by_resource: dict[str, int]
    min_interval_seconds_by_resource: dict[str, float]
    resource_key_by_host_suffix: dict[str, str]

    def __post_init__(self) -> None:
        if self.default_max_concurrency < 1:
            raise ValueError("resources.default_max_concurrency must be >= 1")
        if self.default_min_interval_seconds < 0:
            raise ValueError("resources.default_min_interval_seconds must be >= 0")
        for resource_key, delay in self.min_interval_seconds_by_resource.items():
            if not resource_key.strip():
                raise ValueError("resources.min_interval_seconds_by_resource keys must be non-empty")
            if delay < 0:
                raise ValueError("resources.min_interval_seconds_by_resource values must be >= 0")
        for resource_key, concurrency in self.max_concurrency_by_resource.items():
            if not resource_key.strip():
                raise ValueError("resources.max_concurrency_by_resource keys must be non-empty")
            if concurrency < 1:
                raise ValueError("resources.max_concurrency_by_resource values must be >= 1")
        for suffix, resource_key in self.resource_key_by_host_suffix.items():
            if not suffix.strip() or not resource_key.strip():
                raise ValueError("resources.resource_key_by_host_suffix must contain non-empty strings")

    def min_interval_for_resource(self, resource_key: str) -> float:
        return self.min_interval_seconds_by_resource.get(
            resource_key,
            self.default_min_interval_seconds,
        )

    def concurrency_for_resource(self, resource_key: str) -> int:
        return self.max_concurrency_by_resource.get(resource_key, self.default_max_concurrency)

    def resource_key_for_host(self, host: str) -> str:
        normalized = host.casefold().strip().rstrip(".")
        matches = (
            (suffix.casefold().strip().lstrip("."), resource_key)
            for suffix, resource_key in self.resource_key_by_host_suffix.items()
        )
        matching = tuple(
            (suffix, resource_key)
            for suffix, resource_key in matches
            if normalized == suffix or normalized.endswith(f".{suffix}")
        )
        if not matching:
            return normalized
        return max(matching, key=lambda item: len(item[0]))[1]


@dataclass(frozen=True)
class ApplicationChannelServiceConfig:
    enabled: bool = True
    request_concurrency_by_source: int = 1

    def __post_init__(self) -> None:
        if self.request_concurrency_by_source < 1:
            raise ValueError("application_channels.request_concurrency_by_source must be >= 1")


@dataclass(frozen=True)
class SearchServiceConfig:
    source_attempt_timeout_seconds: float
    run_timeout_seconds: float
    fetch_timeout_seconds: float
    request_retry: RequestRetryServiceConfig
    resources: ResourceServiceConfig
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
        request_retry = _required_object(payload, "request_retry")
        resources = _required_object(payload, "resources")
        application_channels = _optional_object(payload, "application_channels")
        return cls(
            source_attempt_timeout_seconds=_required_float(payload, "source_attempt_timeout_seconds"),
            run_timeout_seconds=_required_float(payload, "run_timeout_seconds"),
            fetch_timeout_seconds=_required_float(payload, "fetch_timeout_seconds"),
            request_retry=RequestRetryServiceConfig(
                max_attempts=_required_int(request_retry, "max_attempts"),
                base_delay_seconds=_required_float(request_retry, "base_delay_seconds"),
                max_delay_seconds=_required_float(request_retry, "max_delay_seconds"),
                request_budget_seconds=_required_float(request_retry, "request_budget_seconds"),
            ),
            resources=ResourceServiceConfig(
                default_max_concurrency=_required_int(resources, "default_max_concurrency"),
                default_min_interval_seconds=_required_float(
                    resources,
                    "default_min_interval_seconds",
                ),
                max_concurrency_by_resource=_int_mapping(
                    resources,
                    "max_concurrency_by_resource",
                ),
                min_interval_seconds_by_resource=_float_mapping(
                    resources,
                    "min_interval_seconds_by_resource",
                ),
                resource_key_by_host_suffix=_string_mapping(
                    resources,
                    "resource_key_by_host_suffix",
                ),
            ),
            application_channels=ApplicationChannelServiceConfig(
                enabled=_optional_bool(application_channels, "enabled", default=True),
                request_concurrency_by_source=_optional_int(
                    application_channels,
                    "request_concurrency_by_source",
                    default=1,
                ),
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


def _optional_bool(payload: JsonObject, key: str, *, default: bool) -> bool:
    value = payload.get(key)
    if value is None:
        return default
    if not isinstance(value, bool):
        raise ValueError(f"{key} must be a boolean")
    return value


def _optional_int(payload: JsonObject, key: str, *, default: int) -> int:
    value = payload.get(key)
    if value is None:
        return default
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{key} must be an integer")
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


def _int_mapping(payload: JsonObject, key: str) -> dict[str, int]:
    value = payload.get(key, {})
    if not isinstance(value, dict):
        raise ValueError(f"{key} must be a JSON object")
    parsed: dict[str, int] = {}
    for raw_key, raw_value in value.items():
        if not isinstance(raw_key, str):
            raise ValueError(f"{key} keys must be strings")
        if not isinstance(raw_value, int) or isinstance(raw_value, bool):
            raise ValueError(f"{key}.{raw_key} must be an integer")
        parsed[raw_key] = raw_value
    return parsed


def _string_mapping(payload: JsonObject, key: str) -> dict[str, str]:
    value = payload.get(key, {})
    if not isinstance(value, dict):
        raise ValueError(f"{key} must be a JSON object")
    parsed: dict[str, str] = {}
    for raw_key, raw_value in value.items():
        if not isinstance(raw_key, str) or not isinstance(raw_value, str):
            raise ValueError(f"{key} keys and values must be strings")
        parsed[raw_key] = raw_value
    return parsed
