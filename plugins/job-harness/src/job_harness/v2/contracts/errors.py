"""Shared source and transport errors expressed in contract terms."""

from __future__ import annotations

from job_harness.v2.contracts.enums import SourceOutcome
from job_harness.v2.contracts.records import AttemptEvidence


class ClassifiedSourceError(Exception):
    """A source or transport failure that already maps to a canonical outcome."""

    def __init__(
        self,
        outcome: SourceOutcome,
        message: str,
        *,
        evidence: AttemptEvidence | None = None,
    ) -> None:
        super().__init__(message)
        self.outcome = outcome
        self.evidence = evidence or AttemptEvidence(error=message)
