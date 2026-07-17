"""Runtime errors shared across application and adapter layers."""

from job_harness.v2.contracts.errors import ClassifiedSourceError


class HttpStatusError(Exception):
    """Raised when an HTTP response cannot be passed to a parser as content."""

    def __init__(
        self,
        *,
        status_code: int,
        final_url: str,
        retry_after_seconds: float | None = None,
    ) -> None:
        self.status_code = status_code
        self.final_url = final_url
        self.retry_after_seconds = retry_after_seconds
        super().__init__(f"HTTP {status_code} for {final_url}")


class UnsafeTargetError(ValueError):
    """Raised before a scraper can access a disallowed network target."""


class ResponseSizeLimitError(RuntimeError):
    """Raised when a response exceeds the invocation's configured resource limit."""


class RedirectLimitError(RuntimeError):
    """Raised when an HTTP redirect chain exceeds the configured bound."""


__all__ = [
    "ClassifiedSourceError",
    "HttpStatusError",
    "RedirectLimitError",
    "ResponseSizeLimitError",
    "UnsafeTargetError",
]
