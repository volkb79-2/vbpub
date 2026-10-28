"""Metadata-only data model for Claude Code usage reports.

These types deliberately have no message, prompt, or tool-result fields.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime


@dataclass(slots=True)
class Call:
    """One de-duplicated Claude API request and its token metadata."""

    request_id: str | None
    timestamp: datetime
    session_id: str
    agent_id: str | None
    model: str
    effort: str | None
    speed: str | None
    service_tier: str | None
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_5m_tokens: int
    cache_write_1h_tokens: int
    cache_write_unclassified_tokens: int
    thinking_tokens: int
    context_size: int
    project: str | None
    kind: str = "normal"
    compact_pre: int | None = None
    compact_post: int | None = None
    weighted_cost: float | None = None
    usd_cost: float | None = None
    compaction_before: bool = False
    sidechain: bool = False
    source_order: int = 0
    topic: str | None = None
    agent_type: str | None = None

    @property
    def raw_token_units(self) -> int:
        """Sum billable input/output classes; thinking is an output subset."""
        return (
            self.input_tokens + self.cache_read_tokens
            + self.cache_write_5m_tokens + self.cache_write_1h_tokens
            + self.cache_write_unclassified_tokens + self.output_tokens
        )


@dataclass(slots=True)
class Compaction:
    """A compact-boundary metadata event, with no summary text."""

    timestamp: datetime
    pre_tokens: int | None
    post_tokens: int | None
    trigger: str | None = None


@dataclass(slots=True)
class Aggregate:
    """Self or subtree totals for a node."""

    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_5m_tokens: int = 0
    cache_write_1h_tokens: int = 0
    cache_write_unclassified_tokens: int = 0
    thinking_tokens: int = 0
    raw_token_units: int = 0
    peak_context: int = 0
    peak_context_at: datetime | None = None
    start: datetime | None = None
    end: datetime | None = None
    duration_seconds: int = 0
    models: dict[str, int] = field(default_factory=dict)
    efforts: dict[str, int] = field(default_factory=dict)
    weighted_units: float | None = None
    usd: float | None = None

    @classmethod
    def from_calls(cls, calls: list[Call]) -> Aggregate:
        if not calls:
            return cls()
        ordered = sorted(calls, key=lambda call: (call.timestamp, call.source_order))
        start = ordered[0].timestamp
        end = ordered[-1].timestamp
        peak_call = max(
            ordered,
            key=lambda call: (call.context_size, -call.timestamp.timestamp(), -call.source_order),
        )
        models = Counter(call.model for call in ordered)
        efforts = Counter(call.effort for call in ordered if call.effort)
        values = {
            "calls": len(ordered),
            "input_tokens": sum(call.input_tokens for call in ordered),
            "output_tokens": sum(call.output_tokens for call in ordered),
            "cache_read_tokens": sum(call.cache_read_tokens for call in ordered),
            "cache_write_5m_tokens": sum(call.cache_write_5m_tokens for call in ordered),
            "cache_write_1h_tokens": sum(call.cache_write_1h_tokens for call in ordered),
            "cache_write_unclassified_tokens": sum(
                call.cache_write_unclassified_tokens for call in ordered
            ),
            "thinking_tokens": sum(call.thinking_tokens for call in ordered),
            "raw_token_units": sum(call.raw_token_units for call in ordered),
            "peak_context": peak_call.context_size,
            "peak_context_at": peak_call.timestamp,
            "start": start,
            "end": end,
            "duration_seconds": int((end - start).total_seconds()),
            "models": dict(sorted(models.items())),
            "efforts": dict(sorted(efforts.items())),
        }
        return cls(**values)


@dataclass(slots=True)
class Node:
    """A session or attributed subagent in the usage forest."""

    node_id: str
    parent_id: str | None
    session_id: str
    agent_id: str | None
    agent_type: str | None = None
    topic: str | None = None
    kind: str = "main"
    project: str | None = None
    calls: list[Call] = field(default_factory=list)
    compactions: list[Compaction] = field(default_factory=list)
    children: list[Node] = field(default_factory=list)
    self_aggregate: Aggregate = field(default_factory=Aggregate)
    subtree_aggregate: Aggregate = field(default_factory=Aggregate)
    omitted_descendants: int = 0


@dataclass(slots=True)
class Session:
    """One transcript family, including its root and subagent tree."""

    session_id: str
    root: Node


@dataclass(slots=True)
class LoadWarnings:
    """Counts for records that could not contribute to full attribution."""

    malformed_lines: int = 0
    malformed_meta: int = 0
    unreadable_files: int = 0
    missing_timestamps: int = 0
    missing_usage: int = 0
    invalid_usage_fields: int = 0
    synthetic_models: int = 0
    missing_meta: int = 0
    unattributed_agents: int = 0
    duplicate_requests: int = 0
    duplicate_conflicts: int = 0
    session_id_mismatches: int = 0
    parent_conflicts: int = 0


@dataclass(slots=True)
class Corpus:
    """Loaded transcript metadata and candidate-scan coverage counts."""

    sessions: list[Session]
    warnings: LoadWarnings = field(default_factory=LoadWarnings)
    scanned_files: int = 0
    pruned_files: int = 0
    pruned_sessions: int = 0


@dataclass(frozen=True, slots=True)
class Window:
    """An inclusive UTC query interval."""

    since: datetime
    until: datetime
    requested_since: str
    requested_until: str | None = None
