"""Transports: how remuda reaches a model (FR-3, FR-4)."""

from remuda.transport.errors import (
    PermanentTransportError,
    RateLimitedError,
    TransientTransportError,
    TransportConfigurationError,
    TransportError,
)
from remuda.transport.models import (
    CompletionRequest,
    CompletionResult,
    Transport,
    Usage,
)
from remuda.transport.openai_compat import (
    OpenAICompatTransport,
    build_request_extras,
    build_transport,
)

__all__ = [
    "CompletionRequest",
    "CompletionResult",
    "OpenAICompatTransport",
    "PermanentTransportError",
    "RateLimitedError",
    "TransientTransportError",
    "Transport",
    "TransportConfigurationError",
    "TransportError",
    "Usage",
    "build_request_extras",
    "build_transport",
]
