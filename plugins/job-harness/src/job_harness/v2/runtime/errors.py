"""Runtime errors shared across application and adapter layers."""

from __future__ import annotations

from job_harness.v2.contracts import AttemptEvidence, SourceOutcome


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

