"""Utilities for propagating a per-request correlation ID through async context."""

from contextvars import ContextVar

# Each request sets its own ID here. Since ContextVar is async-safe,
# concurrent requests never see each other's IDs.
_request_id: ContextVar[str | None] = ContextVar("request_id", default=None)


def set_request_id(request_id: str | None) -> None:
    """Bind a request ID to the current async context for log correlation."""
    _request_id.set(request_id)


def get_request_id() -> str | None:
    """Retrieve the request ID bound to the current async context, if any."""
    return _request_id.get()
