"""Base error type shared by every remuda module.

Consumers embedding remuda (FR-7) catch `RemudaError` to trap anything the
library refuses, without importing per-module exception classes.
"""


class RemudaError(Exception):
    """Base class for every error raised by remuda."""
