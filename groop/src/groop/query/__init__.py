"""groop.query — the unified bounded frame query core (P88).

One aggregation engine over a single typed ``FrameSource`` boundary, consumed by
CLI/TUI/HTTP/MCP.  See ``handoff/P88-unified-frame-query-core.md``.
"""

from __future__ import annotations

from .engine import (
    Caps,
    MetricRef,
    Query,
    Result,
    Selector,
    SortSpec,
    format_result,
    run_query,
    subtree_aggregate,
)
from .errors import (
    BoundExceededError,
    IncompatibleQueryError,
    InvalidQueryError,
    QueryError,
    UnknownFieldError,
)
from .semantics import ValueSemantic, canonical_semantic, resolve_semantic
from .source import (
    DaemonHistoryFrameSource,
    FrameSource,
    RecordingFrameSource,
    SourceFrame,
    SourceProvenance,
)

__all__ = [
    "Caps",
    "MetricRef",
    "Query",
    "Result",
    "Selector",
    "SortSpec",
    "format_result",
    "run_query",
    "subtree_aggregate",
    "QueryError",
    "UnknownFieldError",
    "InvalidQueryError",
    "IncompatibleQueryError",
    "BoundExceededError",
    "ValueSemantic",
    "canonical_semantic",
    "resolve_semantic",
    "FrameSource",
    "RecordingFrameSource",
    "DaemonHistoryFrameSource",
    "SourceFrame",
    "SourceProvenance",
]
