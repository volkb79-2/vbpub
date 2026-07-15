"""Typed, bounded errors for the P88 unified frame query core.

Every failure that crosses the engine boundary is one of these — never a raw
exception with kernel/sysfs/filesystem text (README "Error disclosure").  Each
carries a stable ``code`` so a CLI/HTTP/MCP frontend can classify without string
matching.
"""

from __future__ import annotations


class QueryError(ValueError):
    """Base class for all typed query-engine errors.

    Subclasses ValueError so existing CLI ``except ValueError`` handlers treat a
    query error as a bounded user error (exit 2), never a traceback.
    """

    code: str = "query_error"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class UnknownFieldError(QueryError):
    """The query object carried a field the engine does not define."""

    code = "unknown_field"


class InvalidQueryError(QueryError):
    """A field held an out-of-domain value (bad shape/projection/window/etc.)."""

    code = "invalid_query"


class IncompatibleQueryError(QueryError):
    """Two individually valid fields cannot be combined.

    e.g. ``counter_delta`` on a metric with no raw counters, ``raw`` shape with a
    ``hierarchy`` projection, or a sort key naming an unselected metric.
    """

    code = "incompatible_query"


class BoundExceededError(QueryError):
    """A hard row/point/byte bound would be exceeded and the policy is ``error``.

    Carries the bound that fired and the observed count so a caller can widen the
    bound or switch to the ``truncate`` policy without re-parsing the message.
    """

    code = "bound_exceeded"

    def __init__(self, message: str, *, bound: str, limit: int, observed: int) -> None:
        super().__init__(message)
        self.bound = bound
        self.limit = limit
        self.observed = observed
