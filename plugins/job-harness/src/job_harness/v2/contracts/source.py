"""Source catalog contracts."""

from __future__ import annotations

import re
from dataclasses import dataclass

from job_harness.v2.contracts.enums import (
    ALL_SEARCH_CRITERIA,
    CriterionCapability,
    SearchCriterion,
    SourceType,
    Transport,
)

_SOURCE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_:-]*$")


@dataclass(frozen=True)
class CriterionDeclaration:
    criterion: SearchCriterion
    capability: CriterionCapability


@dataclass(frozen=True)
class SourceDescriptor:
    source_id: str
    source_type: SourceType
    transport: Transport
    countries: tuple[str, ...]
    source_limit: int
    criteria: tuple[CriterionDeclaration, ...]
    identity_namespace: str | None = None

    def __post_init__(self) -> None:
        source_id = self.source_id.strip()
        if not _SOURCE_ID_RE.fullmatch(source_id):
            raise ValueError("source_id must match ^[a-z0-9][a-z0-9_:-]*$")
        if self.source_limit < 1:
            raise ValueError("source_limit must be >= 1")
        identity_namespace = (self.identity_namespace or source_id).strip()
        if not _SOURCE_ID_RE.fullmatch(identity_namespace):
            raise ValueError("identity_namespace must match ^[a-z0-9][a-z0-9_:-]*$")
        object.__setattr__(self, "source_id", source_id)
        object.__setattr__(self, "identity_namespace", identity_namespace)
        object.__setattr__(self, "countries", tuple(country.upper() for country in self.countries))

        seen = {item.criterion for item in self.criteria}
        expected = set(ALL_SEARCH_CRITERIA)
        if seen != expected:
            missing = ", ".join(sorted(item.value for item in expected - seen))
            extra = ", ".join(sorted(item.value for item in seen - expected))
            details = _criteria_error_details(missing=missing, extra=extra)
            raise ValueError(f"criteria must declare every SearchCriterion exactly once ({details})")
        if len(self.criteria) != len(seen):
            raise ValueError("criteria must not contain duplicate SearchCriterion values")

    @classmethod
    def from_capabilities(
        cls,
        *,
        source_id: str,
        source_type: SourceType,
        transport: Transport,
        countries: tuple[str, ...],
        source_limit: int,
        capabilities: dict[SearchCriterion, CriterionCapability],
        identity_namespace: str | None = None,
    ) -> SourceDescriptor:
        return cls(
            source_id=source_id,
            source_type=source_type,
            transport=transport,
            countries=countries,
            source_limit=source_limit,
            criteria=tuple(
                CriterionDeclaration(criterion, capabilities[criterion])
                for criterion in ALL_SEARCH_CRITERIA
            ),
            identity_namespace=identity_namespace,
        )

    def capability_for(self, criterion: SearchCriterion) -> CriterionCapability:
        for declaration in self.criteria:
            if declaration.criterion == criterion:
                return declaration.capability
        raise ValueError(f"criterion is not declared: {criterion.value}")

    @property
    def native_request_criteria(self) -> frozenset[SearchCriterion]:
        return frozenset(
            declaration.criterion
            for declaration in self.criteria
            if declaration.capability == CriterionCapability.NATIVE_REQUEST
        )

    @property
    def structured_output_criteria(self) -> frozenset[SearchCriterion]:
        return frozenset(
            declaration.criterion
            for declaration in self.criteria
            if declaration.capability == CriterionCapability.STRUCTURED_OUTPUT
        )

    @property
    def unsupported_criteria(self) -> frozenset[SearchCriterion]:
        return frozenset(
            declaration.criterion
            for declaration in self.criteria
            if declaration.capability == CriterionCapability.UNSUPPORTED
        )


def _criteria_error_details(*, missing: str, extra: str) -> str:
    parts = []
    if missing:
        parts.append(f"missing: {missing}")
    if extra:
        parts.append(f"extra: {extra}")
    return "; ".join(parts)
