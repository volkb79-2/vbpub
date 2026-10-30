"""Metadata-only data model for the frozen usage-report-v1 contract."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True, slots=True)
class SourceRef:
    """Opaque transcript provenance; never contains a filesystem path."""

    source_id: str
    line: int


@dataclass(slots=True)
class Call:
    """One de-duplicated Claude API request and its token metadata."""

    call_id: str
    request_id: str | None
    request_ids: list[str]
    message_id: str | None
    timestamp: datetime
    session_id: str
    agent_id: str | None
    model: str
    effort: str | None
    speed: str | None
    service_tier: str | None
    inference_geo: str | None
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_5m_tokens: int
    cache_write_1h_tokens: int
    cache_write_unclassified_tokens: int
    thinking_tokens: int
    context_size: int
    project: str | None
    source_id: str
    source_line: int
    provenance: list[SourceRef] = field(default_factory=list)
    source_ids: list[str] = field(default_factory=list)
    stream_block_count: int = 1
    copied_source_count: int = 1
    source_is_root: bool = False
    has_fork_source: bool = False
    source_spawn_depth: int | None = None
    sidechain: bool = False
    topic: str | None = None
    agent_type: str | None = None
    weighted_cost: float | None = None
    usd_cost: float | None = None
    conflicted: bool = False

    @property
    def raw_token_units(self) -> int:
        """Sum billable input/output classes; thinking is an output subset."""
        return (
            self.input_tokens + self.cache_read_tokens
            + self.cache_write_5m_tokens + self.cache_write_1h_tokens
            + self.cache_write_unclassified_tokens + self.output_tokens
        )

    @property
    def source_order(self) -> int:
        """Compatibility alias for the stable one-based source line number."""
        return self.source_line


@dataclass(slots=True)
class UsageEvent:
    """A typed, content-free usage lifecycle or API rejection event."""

    event_id: str
    kind: str
    timestamp: datetime
    session_id: str
    agent_id: str | None
    source_record_id: str | None
    source_id: str
    source_line: int
    provenance: list[SourceRef] = field(default_factory=list)
    source_ids: list[str] = field(default_factory=list)
    pre_tokens: int | None = None
    post_tokens: int | None = None
    duration_ms: int | None = None
    trigger: str | None = None
    summary_present: bool = False
    summary_timestamp: datetime | None = None
    limit_kind: str | None = None
    error_code: str | None = None
    status_code: int | None = None
    reset_at: datetime | None = None
    previous_call_id: str | None = None
    current_call_id: str | None = None


@dataclass(frozen=True, slots=True)
class CostStateModel:
    """Content-free per-model cost-state counters used only for diagnostics."""

    model: str
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_creation_tokens: int
    cache_write_5m_tokens: int | None
    cache_write_1h_tokens: int | None
    cost_usd: float


@dataclass(frozen=True, slots=True)
class CostState:
    """A cost-state line in source order; startTime is not a snapshot time."""

    session_id: str
    source_id: str
    source_line: int
    has_unknown_model_cost: bool
    total_cost_usd: float
    models: tuple[CostStateModel, ...]


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
    wall_duration_seconds: int = 0
    active_span_seconds: int = 0
    models: dict[str, int] = field(default_factory=dict)
    efforts: dict[str, int] = field(default_factory=dict)
    weighted_units: float | None = None
    usd: float | None = None

    @property
    def duration_seconds(self) -> int:
        """Backward-compatible name; duration is wall-clock elapsed time."""
        return self.wall_duration_seconds

    @classmethod
    def from_calls(cls, calls: list[Call]) -> Aggregate:
        if not calls:
            return cls()
        ordered = sorted(
            calls,
            key=lambda call: (call.timestamp, call.source_id, call.source_line, call.call_id),
        )
        start = ordered[0].timestamp
        end = ordered[-1].timestamp
        peak_call = max(
            ordered,
            key=lambda call: (
                call.context_size, -call.timestamp.timestamp(), call.source_id,
                -call.source_line,
            ),
        )
        models = Counter(call.model for call in ordered)
        efforts = Counter(call.effort for call in ordered if call.effort)
        active_span = 0
        burst_start = burst_end = ordered[0].timestamp
        for call in ordered[1:]:
            gap = (call.timestamp - burst_end).total_seconds()
            if gap > 3600:
                active_span += int((burst_end - burst_start).total_seconds())
                burst_start = call.timestamp
            burst_end = call.timestamp
        active_span += int((burst_end - burst_start).total_seconds())
        return cls(
            calls=len(ordered),
            input_tokens=sum(call.input_tokens for call in ordered),
            output_tokens=sum(call.output_tokens for call in ordered),
            cache_read_tokens=sum(call.cache_read_tokens for call in ordered),
            cache_write_5m_tokens=sum(call.cache_write_5m_tokens for call in ordered),
            cache_write_1h_tokens=sum(call.cache_write_1h_tokens for call in ordered),
            cache_write_unclassified_tokens=sum(
                call.cache_write_unclassified_tokens for call in ordered
            ),
            thinking_tokens=sum(call.thinking_tokens for call in ordered),
            raw_token_units=sum(call.raw_token_units for call in ordered),
            peak_context=peak_call.context_size,
            peak_context_at=peak_call.timestamp,
            start=start,
            end=end,
            wall_duration_seconds=int((end - start).total_seconds()),
            active_span_seconds=active_span,
            models=dict(sorted(models.items())),
            efforts=dict(sorted(efforts.items())),
            weighted_units=(
                sum(call.weighted_cost for call in ordered)
                if all(call.weighted_cost is not None for call in ordered) else None
            ),
            usd=(
                sum(call.usd_cost for call in ordered)
                if all(call.usd_cost is not None for call in ordered) else None
            ),
        )


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
    spawn_depth: int | None = None
    is_fork: bool = False
    source_ids: list[str] = field(default_factory=list)
    lineage_status: str = "root"
    lineage_candidates: list[str] = field(default_factory=list)
    claimed_parent_agent_id: str | None = None
    lineage_tool_use_id: str | None = None
    clipped_before: bool = False
    clipped_after: bool = False
    calls: list[Call] = field(default_factory=list)
    events: list[UsageEvent] = field(default_factory=list)
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
    truncated_lines: int = 0
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
    invalid_identity_fields: int = 0
    invalid_metadata_values: int = 0
    conflicted_calls_excluded: int = 0
    copied_prefixes: int = 0
    cross_file_conflicts: int = 0
    session_id_mismatches: int = 0
    parent_conflicts: int = 0
    ambiguous_parent_edges: int = 0
    missing_lineage_metadata: int = 0
    unpaired_compaction_summaries: int = 0
    unclassified_rejections: int = 0
    concurrent_changes: int = 0
    symlink_aliases: int = 0


@dataclass(slots=True)
class Corpus:
    """Loaded transcript metadata and candidate-scan coverage counts."""

    sessions: list[Session]
    warnings: LoadWarnings = field(default_factory=LoadWarnings)
    conflicts: list[dict[str, object]] = field(default_factory=list)
    scanned_files: int = 0
    cost_states: list[CostState] = field(default_factory=list)
    malformed_cost_states: int = 0


@dataclass(frozen=True, slots=True)
class Window:
    """A half-open UTC query interval: ``[since, until)``."""

    since: datetime
    until: datetime
    requested_since: str
    requested_until: str | None = None
