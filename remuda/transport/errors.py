"""Transport failures, classified transient vs permanent (FR-4).

Classification follows `development/observability.md` §7: a transient failure
cools the model down and redistributes its work WITHOUT consuming a
validation attempt; a permanent failure removes the model from the run,
because retrying it cannot help.
"""

from remuda.errors import RemudaError


class TransportError(RemudaError):
    """A model call failed at the transport layer."""


class TransientTransportError(TransportError):
    """Temporary: timeout, connection refused, 5xx. Cool down and retry."""


class RateLimitedError(TransientTransportError):
    """The provider rate-limited the call (429)."""

    def __init__(self, detail: str, retry_after_seconds: float | None = None) -> None:
        self.retry_after_seconds = retry_after_seconds
        super().__init__(detail)


class PermanentTransportError(TransportError):
    """Unrecoverable for this model: auth, bad request, unknown model."""


class TransportConfigurationError(PermanentTransportError):
    """The provider or model is misconfigured — e.g. its key_env is unset."""
