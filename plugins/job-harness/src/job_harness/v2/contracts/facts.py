"""Fact requirements and provider declarations for graph selection."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from job_harness.v2.contracts.independent import ParserRef
from job_harness.v2.contracts.json_types import JsonObject


class ProviderStage(StrEnum):
    NATIVE_REQUEST = "native_request"
    LISTING_OUTPUT = "listing_output"
    DETAIL_OUTPUT = "detail_output"
    PROFILE_OUTPUT = "profile_output"
    SITE_OUTPUT = "site_output"
    DERIVED_FACT = "derived_fact"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class FactProviderSpec:
    provider_id: str
    stage: ProviderStage
    parser_ref: ParserRef | None
    fact_path: str
    depends_on_fact_paths: tuple[str, ...]
    required_for_final: bool
    cost_class: str
    ordering: int
    deriver_id: str | None = None
    deriver_version: str | None = None

    def __post_init__(self) -> None:
        for value, name in (
            (self.provider_id, "provider_id"),
            (self.fact_path, "fact_path"),
            (self.cost_class, "cost_class"),
        ):
            if not value.strip():
                raise ValueError(f"{name} must be non-empty")
        parser_stages = {
            ProviderStage.DETAIL_OUTPUT,
            ProviderStage.PROFILE_OUTPUT,
            ProviderStage.SITE_OUTPUT,
        }
        if (self.stage in parser_stages) != (self.parser_ref is not None):
            raise ValueError("parser_ref is required exactly for parser provider stages")
        if self.stage == ProviderStage.DERIVED_FACT:
            if not self.deriver_id or not self.deriver_version:
                raise ValueError("derived_fact provider requires a pinned deriver")
        elif self.deriver_id is not None or self.deriver_version is not None:
            raise ValueError("only derived_fact providers may declare a deriver")


@dataclass(frozen=True)
class SelectionDecision:
    keep: bool
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class FactDerivation:
    deriver_id: str
    deriver_version: str
    output_schema_id: str
    payload: JsonObject

    def __post_init__(self) -> None:
        for value, name in (
            (self.deriver_id, "deriver_id"),
            (self.deriver_version, "deriver_version"),
            (self.output_schema_id, "output_schema_id"),
        ):
            if not value.strip():
                raise ValueError(f"{name} must be non-empty")
