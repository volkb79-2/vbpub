"""Load Claude Code usage metadata without retaining transcript content."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
from collections import defaultdict
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..adapters import claude_code
from ..stats import extract_compaction_metadata
from .model import (
    Call,
    Corpus,
    CostState,
    CostStateModel,
    LoadWarnings,
    Node,
    Session,
    SourceRef,
    UsageEvent,
)


class UsageLoadError(ValueError):
    """Raised when the Claude Code transcript source cannot be scanned."""


@dataclass(slots=True)
class _AgentMeta:
    agent_id: str
    source_id: str
    agent_type: str | None = None
    topic: str | None = None
    tool_use_id: str | None = None
    spawn_depth: int | None = None
    parent_agent_id: str | None = None
    is_fork: bool = False


@dataclass(slots=True)
class _SessionBuild:
    session_id: str
    project_dir: Path
    session_dir: Path
    root_file: Path | None
    root: Node
    agent_files: dict[str, Path] = field(default_factory=dict)
    metas: dict[str, _AgentMeta] = field(default_factory=dict)
    tool_use_owners: dict[str, set[str | None]] = field(default_factory=dict)
    titles: list[str] = field(default_factory=list)
    agent_names: dict[str, str] = field(default_factory=dict)
    events: list[UsageEvent] = field(default_factory=list)
    clipped_sources: dict[str, tuple[bool, bool]] = field(default_factory=dict)


_SAFE_ERROR_CODE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_SAFE_RECORD_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_SAFE_TAG = re.compile(r"^[A-Za-z0-9._:+-]{1,128}$")
_BURST_GAP_SECONDS = 3600


def default_projects_root() -> Path:
    """Return Claude Code's configured projects root without probing it."""
    from ..locate import _claude_projects_root

    return _claude_projects_root()


def _opaque_id(prefix: str, *parts: object) -> str:
    def encode_value(value: object) -> str:
        if isinstance(value, datetime):
            return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        raise TypeError(f"unsupported opaque-ID component: {type(value).__name__}")

    material = json.dumps(
        parts,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        default=encode_value,
    )
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()[:24]
    return f"{prefix}_{digest}"


def _canonical_source(path: Path) -> tuple[str, str]:
    try:
        canonical = str(path.resolve(strict=True))
    except OSError:
        canonical = str(path.absolute())
    return canonical, _opaque_id("src", canonical)


def _project_key(value: str | None) -> str | None:
    if not value:
        return None
    normalized = str(Path(value).expanduser().resolve(strict=False))
    return _opaque_id("project", normalized)


def _normalise_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def _clean_label(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    text = " ".join(value.split())
    if not text or "/" in text or "\\" in text:
        return None
    return text[:120]


def _clean_tag(value: object) -> str | None:
    return value if isinstance(value, str) and _SAFE_TAG.fullmatch(value) else None


def _read_int(source: dict[str, Any], key: str, warnings: LoadWarnings) -> int:
    value = source.get(key, 0)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        warnings.invalid_usage_fields += 1
        return 0
    return value


def _nonnegative_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _finite_number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        number = float(value)
    except OverflowError:
        return None
    return number if math.isfinite(number) and number >= 0 else None


def _cost_state_from_row(
    row: dict[str, Any], *, session_id: str, source_id: str,
    source_line: int, corpus: Corpus,
) -> CostState | None:
    """Parse only the observed, content-free cost-state usage schema."""
    model_usage = row.get("modelUsage")
    total_cost = _finite_number(row.get("totalCostUSD"))
    unknown = row.get("hasUnknownModelCost")
    if not isinstance(model_usage, dict) or total_cost is None or not isinstance(unknown, bool):
        corpus.malformed_cost_states += 1
        return None
    models: list[CostStateModel] = []
    for model_name, raw in sorted(model_usage.items()):
        if not isinstance(model_name, str) or _clean_tag(model_name) is None or not isinstance(raw, dict):
            corpus.malformed_cost_states += 1
            return None
        numeric = {
            key: _nonnegative_int(raw.get(key))
            for key in (
                "inputTokens", "outputTokens", "cacheReadInputTokens",
                "cacheCreationInputTokens",
            )
        }
        model_cost = _finite_number(raw.get("costUSD"))
        if any(value is None for value in numeric.values()) or model_cost is None:
            corpus.malformed_cost_states += 1
            return None
        split_5m = _nonnegative_int(raw.get("cacheCreationEphemeral5mInputTokens"))
        split_1h = _nonnegative_int(raw.get("cacheCreationEphemeral1hInputTokens"))
        models.append(CostStateModel(
            model=model_name,
            input_tokens=numeric["inputTokens"],
            output_tokens=numeric["outputTokens"],
            cache_read_tokens=numeric["cacheReadInputTokens"],
            cache_creation_tokens=numeric["cacheCreationInputTokens"],
            cache_write_5m_tokens=split_5m,
            cache_write_1h_tokens=split_1h,
            cost_usd=model_cost,
        ))
    return CostState(
        session_id=session_id,
        source_id=source_id,
        source_line=source_line,
        has_unknown_model_cost=unknown,
        total_cost_usd=total_cost,
        models=tuple(models),
    )


def _usage_signature(call: Call) -> tuple[object, ...]:
    return (
        call.model,
        call.effort,
        call.speed,
        call.service_tier,
        call.inference_geo,
        call.input_tokens,
        call.output_tokens,
        call.cache_read_tokens,
        call.cache_write_5m_tokens,
        call.cache_write_1h_tokens,
        call.cache_write_unclassified_tokens,
        call.thinking_tokens,
    )


def _same_stream_call(left: Call, right: Call) -> bool:
    return (
        left.message_id == right.message_id
        and _usage_signature(left) == _usage_signature(right)
        and left.agent_id == right.agent_id
        and left.sidechain == right.sidechain
    )


def _representative_key(call: Call) -> tuple[object, ...]:
    """Prefer the parent source for copied history, then a stable source ID."""
    return (
        not call.source_is_root,
        call.has_fork_source,
        call.source_spawn_depth if call.source_spawn_depth is not None else 10_000,
        call.source_id,
        call.source_line,
    )


def _call_from_row(
    row: dict[str, Any],
    *,
    session_id: str,
    file_agent_id: str | None,
    source_id: str,
    source_line: int,
    meta: _AgentMeta | None,
    warnings: LoadWarnings,
) -> Call | None:
    message = row.get("message")
    if not isinstance(message, dict):
        return None
    usage = message.get("usage")
    if not isinstance(usage, dict):
        warnings.missing_usage += 1
        return None
    model = message.get("model")
    if not isinstance(model, str) or not model:
        return None
    if model == "<synthetic>":
        warnings.synthetic_models += 1
        return None
    if _clean_tag(model) is None:
        warnings.invalid_metadata_values += 1
        return None
    timestamp = _normalise_timestamp(row.get("timestamp"))
    if timestamp is None:
        warnings.missing_timestamps += 1
        return None

    request_id = row.get("requestId")
    if request_id is not None and (
        not isinstance(request_id, str) or not _SAFE_RECORD_ID.fullmatch(request_id)
    ):
        warnings.invalid_identity_fields += 1
        request_id = None
    if not isinstance(request_id, str) or not request_id:
        request_id = None
    message_id = message.get("id")
    if message_id is not None and (
        not isinstance(message_id, str) or not _SAFE_RECORD_ID.fullmatch(message_id)
    ):
        warnings.invalid_identity_fields += 1
        message_id = None
    if not isinstance(message_id, str) or not message_id:
        message_id = None

    input_tokens = _read_int(usage, "input_tokens", warnings)
    output_tokens = _read_int(usage, "output_tokens", warnings)
    cache_read = _read_int(usage, "cache_read_input_tokens", warnings)
    cache_total = _read_int(usage, "cache_creation_input_tokens", warnings)
    creation = usage.get("cache_creation")
    creation = creation if isinstance(creation, dict) else {}
    has_5m = "ephemeral_5m_input_tokens" in creation
    has_1h = "ephemeral_1h_input_tokens" in creation
    cache_5m = _read_int(creation, "ephemeral_5m_input_tokens", warnings) if has_5m else 0
    cache_1h = _read_int(creation, "ephemeral_1h_input_tokens", warnings) if has_1h else 0
    if not has_5m and not has_1h:
        cache_other = cache_total
    else:
        cache_other = max(0, cache_total - cache_5m - cache_1h)

    output_details = usage.get("output_tokens_details")
    output_details = output_details if isinstance(output_details, dict) else {}
    thinking = _read_int(output_details, "thinking_tokens", warnings)
    effort = row.get("effort")
    if not isinstance(effort, str) or not effort:
        effort = row.get("perTurnEffort")
    if not isinstance(effort, str) or not effort:
        effort = None
    elif _clean_tag(effort) is None:
        warnings.invalid_metadata_values += 1
        effort = None
    speed = usage.get("speed", row.get("speed"))
    tier = usage.get("service_tier", row.get("service_tier"))
    geo = usage.get("inference_geo", row.get("inference_geo"))
    if not isinstance(speed, str) or _clean_tag(speed) is None:
        if speed is not None:
            warnings.invalid_metadata_values += 1
        speed = None
    if not isinstance(tier, str) or _clean_tag(tier) is None:
        if tier is not None:
            warnings.invalid_metadata_values += 1
        tier = None
    if not isinstance(geo, str) or _clean_tag(geo) is None:
        if geo is not None:
            warnings.invalid_metadata_values += 1
        geo = None
    project = row.get("cwd")
    if not isinstance(project, str) or not project:
        project = None
    row_agent_id = row.get("agentId")
    if row_agent_id is not None and (
        not isinstance(row_agent_id, str) or not _SAFE_RECORD_ID.fullmatch(row_agent_id)
    ):
        warnings.invalid_identity_fields += 1
        row_agent_id = None
    agent_id = file_agent_id
    if agent_id is None and isinstance(row_agent_id, str) and row_agent_id:
        agent_id = row_agent_id
    identity_kind = "request" if request_id else "message" if message_id else "line"
    identity_value = request_id or message_id or source_line
    call_id = _opaque_id("call", "source", session_id, source_id, identity_kind, identity_value)
    return Call(
        call_id=call_id,
        request_id=request_id,
        request_ids=[request_id] if request_id else [],
        message_id=message_id,
        timestamp=timestamp,
        session_id=session_id,
        agent_id=agent_id,
        model=model,
        effort=effort,
        speed=speed,
        service_tier=tier,
        inference_geo=geo,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_tokens=cache_read,
        cache_write_5m_tokens=cache_5m,
        cache_write_1h_tokens=cache_1h,
        cache_write_unclassified_tokens=cache_other,
        thinking_tokens=thinking,
        context_size=input_tokens + cache_read + cache_total,
        project=project,
        source_id=source_id,
        source_line=source_line,
        provenance=[SourceRef(source_id, source_line)],
        source_ids=[source_id],
        source_is_root=file_agent_id is None,
        has_fork_source=bool(meta and meta.is_fork),
        source_spawn_depth=meta.spawn_depth if meta else None,
        sidechain=row.get("isSidechain") is True,
    )


def _in_window(timestamp: datetime, since: datetime | None, until: datetime | None) -> bool:
    return (since is None or timestamp >= since) and (until is None or timestamp < until)


def _call_local_keys(call: Call) -> tuple[tuple[str, str], ...]:
    """Return namespaced file-local identities that can link stream rows."""
    keys = []
    if call.request_id is not None:
        keys.append(("request", call.request_id))
    if call.message_id is not None:
        keys.append(("message", call.message_id))
    return tuple(keys)


def _collapse_file_calls(
    calls: list[Call],
    *,
    since: datetime | None,
    until: datetime | None,
    corpus: Corpus,
) -> list[Call]:
    parents = list(range(len(calls)))

    def find(index: int) -> int:
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    def union(left: int, right: int) -> None:
        left_root = find(left)
        right_root = find(right)
        if left_root != right_root:
            parents[max(left_root, right_root)] = min(left_root, right_root)

    first_by_key: dict[tuple[str, str], int] = {}
    for index, call in enumerate(calls):
        for key in _call_local_keys(call):
            previous = first_by_key.setdefault(key, index)
            union(index, previous)

    groups_by_root: dict[int, list[Call]] = defaultdict(list)
    for index, call in enumerate(calls):
        groups_by_root[find(index)].append(call)
    groups = sorted(
        groups_by_root.values(),
        key=lambda group: min((call.source_line, call.call_id) for call in group),
    )
    result: list[Call] = []
    for group in groups:
        group = sorted(group, key=lambda call: (call.source_line, call.call_id))
        key = min(
            (identity for call in group for identity in _call_local_keys(call)),
            default=("line", group[0].call_id),
        )
        if len(group) > 1:
            corpus.warnings.duplicate_requests += len(group) - 1
        in_window = [call for call in group if _in_window(call.timestamp, since, until)]
        if not in_window:
            continue
        if any(not _same_stream_call(group[0], call) for call in group[1:]):
            corpus.warnings.duplicate_conflicts += 1
            corpus.warnings.conflicted_calls_excluded += 1
            corpus.conflicts.append({
                "kind": "duplicate_conflict",
                "identity_id": _opaque_id("conflict", group[0].source_id, *key),
                "session_id": group[0].session_id,
                "candidate_count": len(group),
                "source_ids": sorted({call.source_id for call in group}),
                "source_lines": sorted(call.source_line for call in group),
                "provenance": sorted(
                    (asdict(ref) for call in group for ref in call.provenance),
                    key=lambda ref: (ref["source_id"], ref["line"]),
                ),
            })
            continue
        chosen = next(
            (call for call in reversed(group) if _in_window(call.timestamp, since, until)),
            group[-1],
        )
        chosen.provenance = [ref for call in group for ref in call.provenance]
        chosen.request_ids = sorted({
            request_id for call in group for request_id in call.request_ids
        })
        if chosen.request_id is None and chosen.request_ids:
            chosen.request_id = chosen.request_ids[0]
        chosen.source_ids = sorted({call.source_id for call in group})
        chosen.stream_block_count = len(group)
        chosen.copied_source_count = 1
        result.append(chosen)
    return result


def _copied_prefix_key(call: Call) -> tuple[object, ...] | None:
    if call.message_id is None:
        return None
    return (
        call.session_id,
        call.message_id,
        call.timestamp,
        call.model,
        _usage_signature(call),
    )


def _collapse_copied_prefixes(calls: list[Call], corpus: Corpus) -> list[Call]:
    by_message: dict[tuple[str, str], list[Call]] = defaultdict(list)
    for call in calls:
        if call.message_id is not None:
            by_message[(call.session_id, call.message_id)].append(call)

    excluded: set[int] = set()
    exact_groups: dict[tuple[object, ...], list[Call]] = defaultdict(list)
    exact_calls: list[Call] = []
    for identity, group in sorted(by_message.items()):
        source_ids = {call.source_id for call in group}
        if len(source_ids) < 2:
            continue
        fingerprints = {_copied_prefix_key(call) for call in group}
        if len(fingerprints) != 1:
            corpus.warnings.cross_file_conflicts += 1
            representative = min(group, key=_representative_key)
            representative.conflicted = True
            corpus.warnings.conflicted_calls_excluded += len(group) - 1
            corpus.conflicts.append({
                "kind": "cross_file_conflict",
                "identity_id": _opaque_id("conflict", *identity),
                "session_id": identity[0],
                "candidate_count": len(group),
                "representative_call_id": representative.call_id,
                "source_ids": sorted(source_ids),
                "source_lines": sorted(call.source_line for call in group),
                "provenance": sorted(
                    (asdict(ref) for call in group for ref in call.provenance),
                    key=lambda ref: (ref["source_id"], ref["line"]),
                ),
            })
            excluded.update(id(call) for call in group if call is not representative)
            continue
        key = next(iter(fingerprints))
        # The per-message group is non-empty and every member has that
        # message_id, so its copied-prefix fingerprint cannot be None.
        exact_groups[key].extend(group)

    for identity, group in sorted(exact_groups.items(), key=lambda item: repr(item[0])):
        unique_sources = {call.source_id for call in group}
        representative = min(group, key=_representative_key)
        representative.call_id = _opaque_id("call", "copy", *identity)
        representative.provenance = sorted(
            [ref for call in group for ref in call.provenance],
            key=lambda ref: (ref.source_id, ref.line),
        )
        representative.request_ids = sorted({
            request_id for call in group for request_id in call.request_ids
        })
        representative.source_ids = sorted(unique_sources)
        representative.stream_block_count = sum(call.stream_block_count for call in group)
        representative.copied_source_count = len(unique_sources)
        representative.has_fork_source = any(call.has_fork_source for call in group)
        exact_calls.append(representative)
        corpus.warnings.copied_prefixes += len(unique_sources) - 1
        excluded.update(id(call) for call in group)

    return [call for call in calls if id(call) not in excluded] + exact_calls


def _event_id(kind: str, session_id: str, source_id: str, source_line: int, marker: object) -> str:
    return _opaque_id("event", kind, session_id, source_id, source_line, marker)


def _make_compaction_event(
    row: dict[str, Any],
    *,
    session_id: str,
    agent_id: str | None,
    source_id: str,
    source_line: int,
    warnings: LoadWarnings,
) -> UsageEvent | None:
    metadata = extract_compaction_metadata(row)
    if metadata is None:
        return None
    timestamp = _normalise_timestamp(row.get("timestamp"))
    if timestamp is None:
        warnings.missing_timestamps += 1
        return None
    boundary_id = row.get("uuid")
    return UsageEvent(
        event_id=_event_id("compaction", session_id, source_id, source_line, boundary_id),
        kind="compaction",
        timestamp=timestamp,
        session_id=session_id,
        agent_id=agent_id,
        source_record_id=(
            boundary_id
            if isinstance(boundary_id, str) and _SAFE_RECORD_ID.fullmatch(boundary_id)
            else None
        ),
        source_id=source_id,
        source_line=source_line,
        provenance=[SourceRef(source_id, source_line)],
        source_ids=[source_id],
        pre_tokens=metadata["pre_tokens"],
        post_tokens=metadata["post_tokens"],
        duration_ms=metadata["duration_ms"],
        trigger=metadata["trigger"],
    )


def _make_rejection_event(
    row: dict[str, Any],
    *,
    session_id: str,
    agent_id: str | None,
    source_id: str,
    source_line: int,
    warnings: LoadWarnings,
) -> UsageEvent | None:
    timestamp = _normalise_timestamp(row.get("timestamp"))
    if timestamp is None:
        warnings.missing_timestamps += 1
        return None
    error = row.get("error")
    error_code = error if isinstance(error, str) and _SAFE_ERROR_CODE.fullmatch(error) else None
    status = row.get("apiErrorStatus")
    status = status if isinstance(status, int) and not isinstance(status, bool) else None
    limit_kind = "rate_limit" if error_code == "rate_limit" or status == 429 else None
    reset_at = None
    quota = row.get("quotaLimits")
    if not isinstance(quota, dict):
        params = row.get("apiErrorParams")
        quota = params.get("rate_limit_info") if isinstance(params, dict) else None
    if isinstance(quota, dict) and quota.get("status") == "rejected":
        observed_type = quota.get("rateLimitType")
        if observed_type == "five_hour":
            limit_kind = "five_hour"
        elif observed_type == "seven_day":
            limit_kind = "weekly"
        raw_reset = quota.get("resetsAt")
        if isinstance(raw_reset, int) and not isinstance(raw_reset, bool):
            try:
                reset_at = datetime.fromtimestamp(raw_reset, tz=timezone.utc)
            except (OverflowError, OSError, ValueError):
                reset_at = None
    if limit_kind is None:
        warnings.unclassified_rejections += 1
    return UsageEvent(
        event_id=_event_id("api_rejection", session_id, source_id, source_line, error_code),
        kind="api_rejection",
        timestamp=timestamp,
        session_id=session_id,
        agent_id=agent_id,
        source_record_id=(
            row.get("uuid")
            if isinstance(row.get("uuid"), str) and _SAFE_RECORD_ID.fullmatch(row.get("uuid"))
            else None
        ),
        source_id=source_id,
        source_line=source_line,
        provenance=[SourceRef(source_id, source_line)],
        source_ids=[source_id],
        limit_kind=limit_kind,
        error_code=error_code,
        status_code=status,
        reset_at=reset_at,
    )


def _read_meta(path: Path, agent_id: str, warnings: LoadWarnings) -> _AgentMeta | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        warnings.missing_meta += 1
        return None
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        warnings.missing_meta += 1
        warnings.malformed_meta += 1
        return None
    if not isinstance(value, dict):
        warnings.missing_meta += 1
        warnings.malformed_meta += 1
        return None
    agent_type = _clean_tag(value.get("agentType"))
    if value.get("agentType") is not None and agent_type is None:
        warnings.invalid_metadata_values += 1
    tool_use_id = value.get("toolUseId")
    if not isinstance(tool_use_id, str) or not _SAFE_RECORD_ID.fullmatch(tool_use_id):
        tool_use_id = None
    spawn_depth = value.get("spawnDepth")
    if isinstance(spawn_depth, bool) or not isinstance(spawn_depth, int) or spawn_depth < 1:
        spawn_depth = None
    parent_agent_id = value.get("parentAgentId")
    if not isinstance(parent_agent_id, str) or not _SAFE_RECORD_ID.fullmatch(parent_agent_id):
        parent_agent_id = None
    return _AgentMeta(
        agent_id=agent_id,
        source_id=_canonical_source(path)[1],
        agent_type=agent_type,
        topic=_clean_label(value.get("description")),
        tool_use_id=tool_use_id,
        spawn_depth=spawn_depth,
        parent_agent_id=parent_agent_id,
        is_fork=value.get("isFork") is True,
    )


def _discover(root: Path, corpus: Corpus) -> list[_SessionBuild]:
    if not root.is_dir():
        raise UsageLoadError(f"Claude Code projects directory is unavailable: {root}")
    root = root.resolve()
    try:
        project_dirs = sorted(path for path in root.iterdir() if path.is_dir())
    except OSError as exc:
        raise UsageLoadError(f"cannot read Claude Code projects directory: {exc}") from exc
    groups: list[_SessionBuild] = []
    for project_dir in project_dirs:
        try:
            root_files = {
                path.stem: path for path in project_dir.glob("*.jsonl") if path.is_file()
            }
            session_dirs = {
                path.name: path
                for path in project_dir.iterdir()
                if path.is_dir() and (path / "subagents").is_dir()
            }
        except OSError:
            corpus.warnings.unreadable_files += 1
            continue
        session_ids = sorted(set(root_files) | set(session_dirs))
        for session_id in session_ids:
            session_dir = session_dirs.get(session_id, project_dir / session_id)
            root_file = root_files.get(session_id)
            try:
                agent_files = sorted(
                    path for path in (session_dir / "subagents").glob("agent-*.jsonl")
                    if path.is_file()
                )
            except OSError:
                agent_files = []
                corpus.warnings.unreadable_files += 1
            files = ([root_file] if root_file is not None else []) + agent_files
            if not files:
                continue
            root_source_id = _canonical_source(root_file)[1] if root_file else None
            root_node = Node(
                node_id=session_id,
                parent_id=None,
                session_id=session_id,
                agent_id=None,
                kind="main",
                source_ids=[root_source_id] if root_source_id else [],
                lineage_status="root",
            )
            build = _SessionBuild(
                session_id=session_id,
                project_dir=project_dir,
                session_dir=session_dir,
                root_file=root_file,
                root=root_node,
            )
            for agent_file in agent_files:
                agent_id = agent_file.stem.removeprefix("agent-")
                build.agent_files[agent_id] = agent_file
            for agent_id, agent_file in build.agent_files.items():
                meta_path = agent_file.with_name(f"agent-{agent_id}.meta.json")
                meta = _read_meta(meta_path, agent_id, corpus.warnings)
                if meta is not None:
                    build.metas[agent_id] = meta
            try:
                meta_paths = sorted((session_dir / "subagents").glob("agent-*.meta.json"))
            except OSError:
                meta_paths = []
                corpus.warnings.unreadable_files += 1
            for meta_path in meta_paths:
                agent_id = meta_path.name.removeprefix("agent-").removesuffix(".meta.json")
                if agent_id in build.metas:
                    continue
                meta = _read_meta(meta_path, agent_id, corpus.warnings)
                if meta is not None:
                    build.metas[agent_id] = meta
            groups.append(build)
    return groups


def _scan_file(
    path: Path,
    *,
    build: _SessionBuild,
    file_agent_id: str | None,
    since: datetime | None,
    until: datetime | None,
    corpus: Corpus,
) -> list[Call]:
    _, source_id = _canonical_source(path)
    try:
        handle = path.open("rb")
    except OSError:
        corpus.warnings.unreadable_files += 1
        return []
    source_calls: list[Call] = []
    boundaries: dict[str, UsageEvent] = {}
    local_events: list[UsageEvent] = []
    line_number = 0
    try:
        with handle:
            initial = os.fstat(handle.fileno())
            remaining = initial.st_size
            while remaining > 0:
                raw_line = handle.readline(remaining)
                if not raw_line:
                    break
                remaining -= len(raw_line)
                line_number += 1
                if not raw_line.strip():
                    continue
                try:
                    row = json.loads(raw_line)
                except (json.JSONDecodeError, UnicodeDecodeError):
                    if not raw_line.endswith(b"\n"):
                        corpus.warnings.truncated_lines += 1
                    else:
                        corpus.warnings.malformed_lines += 1
                    continue
                if not isinstance(row, dict):
                    corpus.warnings.malformed_lines += 1
                    continue
                record_type = row.get("type")
                if record_type == "cost-state":
                    row_session = row.get("sessionId")
                    if not isinstance(row_session, str) or not row_session:
                        corpus.malformed_cost_states += 1
                        continue
                    if isinstance(row_session, str) and row_session and row_session != build.session_id:
                        corpus.warnings.session_id_mismatches += 1
                        corpus.malformed_cost_states += 1
                        continue
                    cost_state = _cost_state_from_row(
                        row,
                        session_id=build.session_id,
                        source_id=source_id,
                        source_line=line_number,
                        corpus=corpus,
                    )
                    if cost_state is not None:
                        corpus.cost_states.append(cost_state)
                    continue
                if record_type not in {
                    "assistant", "system", "user", "ai-title", "custom-title", "agent-name",
                }:
                    continue
                row_session = row.get("sessionId")
                if isinstance(row_session, str) and row_session and row_session != build.session_id:
                    corpus.warnings.session_id_mismatches += 1
                message = row.get("message")
                if record_type == "assistant" and isinstance(message, dict):
                    for tool_use_id in claude_code.tool_use_ids_in_message(message):
                        build.tool_use_owners.setdefault(tool_use_id, set()).add(file_agent_id)
                    if row.get("isApiErrorMessage") is True:
                        rejection = _make_rejection_event(
                            row,
                            session_id=build.session_id,
                            agent_id=file_agent_id,
                            source_id=source_id,
                            source_line=line_number,
                            warnings=corpus.warnings,
                        )
                        if rejection is not None:
                            local_events.append(rejection)
                        continue
                    call = _call_from_row(
                        row,
                        session_id=build.session_id,
                        file_agent_id=file_agent_id,
                        source_id=source_id,
                        source_line=line_number,
                        meta=build.metas.get(file_agent_id) if file_agent_id else None,
                        warnings=corpus.warnings,
                    )
                    if call is not None:
                        if since is not None and call.timestamp < since:
                            prior = build.clipped_sources.get(source_id, (False, False))
                            build.clipped_sources[source_id] = (True, prior[1])
                        elif until is not None and call.timestamp >= until:
                            prior = build.clipped_sources.get(source_id, (False, False))
                            build.clipped_sources[source_id] = (prior[0], True)
                        source_calls.append(call)
                    continue
                if record_type in {"ai-title", "custom-title"}:
                    title_key = "aiTitle" if record_type == "ai-title" else "customTitle"
                    label = _clean_label(row.get(title_key))
                    if label is not None:
                        build.titles.append(label)
                    continue
                if record_type == "agent-name":
                    label = _clean_label(row.get("agentName"))
                    if label is not None:
                        if file_agent_id is None:
                            build.titles.append(label)
                        else:
                            build.agent_names[file_agent_id] = label
                    continue
                if record_type == "system" and row.get("subtype") == "compact_boundary":
                    compact = _make_compaction_event(
                        row,
                        session_id=build.session_id,
                        agent_id=file_agent_id,
                        source_id=source_id,
                        source_line=line_number,
                        warnings=corpus.warnings,
                    )
                    if compact is not None:
                        local_events.append(compact)
                        boundary_uuid = row.get("uuid")
                        if isinstance(boundary_uuid, str) and boundary_uuid:
                            boundaries[boundary_uuid] = compact
                    continue
                if record_type == "user" and row.get("isCompactSummary") is True:
                    parent_uuid = row.get("parentUuid")
                    summary = boundaries.get(parent_uuid) if isinstance(parent_uuid, str) else None
                    summary_timestamp = _normalise_timestamp(row.get("timestamp"))
                    if summary is None:
                        corpus.warnings.unpaired_compaction_summaries += 1
                    elif summary_timestamp is None:
                        corpus.warnings.missing_timestamps += 1
                    else:
                        summary.summary_present = True
                        summary.summary_timestamp = summary_timestamp
            final = os.fstat(handle.fileno())
            if (initial.st_dev, initial.st_ino) != (final.st_dev, final.st_ino) or final.st_size < initial.st_size:
                corpus.warnings.concurrent_changes += 1
                source_calls = []
                local_events = []
            elif final.st_size > initial.st_size:
                # The report uses the complete-line snapshot bounded by the
                # initial size and makes the concurrent append visible.
                corpus.warnings.concurrent_changes += 1
            elif final.st_mtime_ns != initial.st_mtime_ns:
                corpus.warnings.concurrent_changes += 1
    except OSError:
        corpus.warnings.unreadable_files += 1
        return []

    before, after = build.clipped_sources.get(source_id, (False, False))
    build.clipped_sources[source_id] = (before, after)
    clipped = _collapse_file_calls(source_calls, since=since, until=until, corpus=corpus)
    for event in local_events:
        if _in_window(event.timestamp, since, until):
            build.events.append(event)
    return clipped


def _node_for_agent(build: _SessionBuild, agent_id: str, meta: _AgentMeta | None) -> Node:
    source_ids = []
    agent_file = build.agent_files.get(agent_id)
    if agent_file is not None:
        source_ids.append(_canonical_source(agent_file)[1])
    if meta is not None:
        source_ids.append(meta.source_id)
    clips = [build.clipped_sources[source_id] for source_id in source_ids if source_id in build.clipped_sources]
    return Node(
        node_id=f"{build.session_id}:agent:{agent_id}",
        parent_id=None,
        session_id=build.session_id,
        agent_id=agent_id,
        agent_type=(meta.agent_type if meta and meta.agent_type else "unknown"),
        topic=(meta.topic if meta and meta.topic else build.agent_names.get(agent_id)),
        kind="fork" if meta and meta.is_fork else "subagent",
        spawn_depth=meta.spawn_depth if meta else None,
        is_fork=bool(meta and meta.is_fork),
        source_ids=sorted(set(source_ids)),
        clipped_before=any(clip[0] for clip in clips),
        clipped_after=any(clip[1] for clip in clips),
    )


def _would_cycle(node: Node, parent: Node, nodes_by_id: dict[str, Node]) -> bool:
    current: Node | None = parent
    seen: set[str] = set()
    while current is not None:
        if current.node_id == node.node_id or current.node_id in seen:
            return True
        seen.add(current.node_id)
        current = nodes_by_id.get(current.parent_id) if current.parent_id is not None else None
    return False


def _append_lineage_warnings(status: str, corpus: Corpus) -> None:
    if status in {"unresolved_ambiguous_tool_use"}:
        corpus.warnings.ambiguous_parent_edges += 1
        corpus.warnings.unattributed_agents += 1
    elif status in {"unresolved_missing_lineage_metadata", "unresolved_missing_tool_use_owner"}:
        corpus.warnings.missing_lineage_metadata += 1
        corpus.warnings.unattributed_agents += 1
    elif status.startswith("unresolved_parent_conflict") or status == "unresolved_unknown_parent":
        corpus.warnings.parent_conflicts += 1
        corpus.warnings.unattributed_agents += 1
    elif status.startswith("unresolved_"):
        corpus.warnings.unattributed_agents += 1


def _event_sort_key(event: UsageEvent) -> tuple[object, ...]:
    return (event.timestamp, event.session_id, event.source_id, event.source_line, event.event_id)


def _cross_file_event_key(event: UsageEvent) -> tuple[object, ...] | None:
    if event.kind == "compaction":
        if event.source_record_id is None:
            return None
        return (
            event.session_id,
            event.kind,
            event.source_record_id,
            event.timestamp,
            event.pre_tokens,
            event.post_tokens,
            event.duration_ms,
            event.trigger,
        )
    if event.kind == "api_rejection":
        if event.source_record_id is None:
            return None
        return (
            event.session_id, event.kind, event.source_record_id,
            event.timestamp, event.error_code, event.status_code,
        )
    return None


def _deduplicate_events(events: list[UsageEvent]) -> list[UsageEvent]:
    groups: dict[tuple[object, ...], list[UsageEvent]] = defaultdict(list)
    unique: list[UsageEvent] = []
    for event in events:
        key = _cross_file_event_key(event)
        if key is None:
            unique.append(event)
        else:
            groups[key].append(event)
    for key, group in sorted(groups.items(), key=lambda item: repr(item[0])):
        source_ids = {event.source_id for event in group}
        chosen = min(group, key=lambda event: (event.agent_id is not None, event.source_id, event.source_line))
        chosen.provenance = sorted(
            [ref for event in group for ref in event.provenance],
            key=lambda ref: (ref.source_id, ref.line),
        )
        chosen.source_ids = sorted(source_ids)
        summaries = [event.summary_timestamp for event in group if event.summary_timestamp is not None]
        chosen.summary_present = bool(summaries)
        chosen.summary_timestamp = min(summaries) if summaries else None
        chosen.event_id = _opaque_id("event", *key)
        unique.append(chosen)
    return sorted(unique, key=_event_sort_key)


def _detect_unexplained_drops(nodes: list[Node]) -> list[UsageEvent]:
    events: list[UsageEvent] = []
    for node in nodes:
        calls = sorted(
            node.calls,
            key=lambda call: (call.timestamp, call.source_id, call.source_line, call.call_id),
        )
        for previous, current in zip(calls, calls[1:]):
            if previous.context_size <= 0 or current.context_size * 10 > previous.context_size * 7:
                continue
            previous_position = (
                previous.timestamp, previous.session_id, previous.source_id,
                previous.source_line, previous.call_id,
            )
            current_position = (
                current.timestamp, current.session_id, current.source_id,
                current.source_line, current.call_id,
            )
            if any(
                event.kind == "compaction"
                and previous_position < (
                    event.timestamp, event.session_id, event.source_id,
                    event.source_line, event.event_id,
                ) <= current_position
                for event in node.events
            ):
                continue
            events.append(UsageEvent(
                event_id=_opaque_id("event", "unexplained_drop", previous.call_id, current.call_id),
                kind="unexplained_drop",
                timestamp=current.timestamp,
                session_id=current.session_id,
                agent_id=current.agent_id,
                source_record_id=None,
                source_id=current.source_id,
                source_line=current.source_line,
                provenance=list(current.provenance),
                source_ids=list(current.source_ids),
                previous_call_id=previous.call_id,
                current_call_id=current.call_id,
            ))
    return events


def _assemble(
    groups: list[_SessionBuild],
    calls: list[Call],
    corpus: Corpus,
) -> list[Session]:
    calls_by_session: dict[str, list[Call]] = defaultdict(list)
    for call in calls:
        calls_by_session[call.session_id].append(call)
    events_by_session: dict[str, list[UsageEvent]] = defaultdict(list)
    for build in groups:
        events_by_session[build.session_id].extend(build.events)

    sessions: list[Session] = []
    for build in groups:
        session_calls = sorted(
            calls_by_session.get(build.session_id, []),
            key=lambda call: (call.timestamp, call.source_id, call.source_line, call.call_id),
        )
        session_events = _deduplicate_events(events_by_session.get(build.session_id, []))
        agent_ids = set(build.agent_files) | set(build.metas)
        agent_ids.update(call.agent_id for call in session_calls if call.agent_id is not None)
        known_agent_ids = set(agent_ids)
        metadata = {
            agent_id: {
                "parentAgentId": meta.parent_agent_id,
                "toolUseId": meta.tool_use_id,
            }
            for agent_id, meta in build.metas.items()
        }
        lineage = claude_code.resolve_agent_lineage(
            metadata,
            build.tool_use_owners,
            known_agent_ids=known_agent_ids,
            root_available=build.root_file is not None,
        )
        nodes = {
            agent_id: _node_for_agent(build, agent_id, build.metas.get(agent_id))
            for agent_id in sorted(agent_ids)
        }
        by_node_id = {build.root.node_id: build.root}
        by_node_id.update({node.node_id: node for node in nodes.values()})
        unresolved: list[Node] = []
        for agent_id in sorted(nodes):
            node = nodes[agent_id]
            edge = lineage.get(agent_id)
            if edge is None:
                node.lineage_status = "unresolved_missing_lineage_metadata"
                unresolved.append(node)
                _append_lineage_warnings(node.lineage_status, corpus)
                continue
            node.lineage_status = edge.status
            node.claimed_parent_agent_id = edge.claimed_parent_agent_id
            node.lineage_tool_use_id = edge.tool_use_id
            node.lineage_candidates = sorted(
                build.root.node_id if candidate is None else f"{build.session_id}:agent:{candidate}"
                for candidate in edge.candidate_agent_ids
            )
            parent = build.root if edge.parent_is_root else nodes.get(edge.parent_agent_id or "")
            if parent is None or _would_cycle(node, parent, by_node_id):
                if parent is not None:
                    node.lineage_status = "unresolved_cycle"
                unresolved.append(node)
                _append_lineage_warnings(node.lineage_status, corpus)
                continue
            node.parent_id = parent.node_id
            parent.children.append(node)
            if edge.status == "resolved_tool_use_id_parent_metadata_conflict":
                corpus.warnings.parent_conflicts += 1

        for node in unresolved:
            node.parent_id = f"{build.session_id}:unattributed"
        if unresolved:
            group_node = Node(
                node_id=f"{build.session_id}:unattributed",
                parent_id=build.root.node_id,
                session_id=build.session_id,
                agent_id=None,
                agent_type="unknown",
                topic="(unattributed)",
                kind="unattributed",
                source_ids=sorted({source_id for node in unresolved for source_id in node.source_ids}),
                lineage_status="unresolved_group",
            )
            group_node.children = unresolved
            build.root.children.append(group_node)

        for call in session_calls:
            # session_calls contributes every non-root ID to agent_ids above.
            owner = build.root if call.agent_id is None else nodes[call.agent_id]
            owner.calls.append(call)
        for event in session_events:
            owner = build.root if event.agent_id is None else nodes.get(event.agent_id)
            if owner is None:
                owner = build.root
            owner.events.append(event)
        for node in [build.root, *nodes.values()]:
            node.calls.sort(key=lambda call: (call.timestamp, call.source_id, call.source_line, call.call_id))
            node.events.sort(key=_event_sort_key)
        root_source = build.root.source_ids[0] if build.root.source_ids else None
        if root_source in build.clipped_sources:
            build.root.clipped_before, build.root.clipped_after = build.clipped_sources[root_source]
        if not build.root.topic and build.titles:
            build.root.topic = build.titles[-1]
        build.root.project = next((call.project for call in build.root.calls if call.project), None)
        if build.root.project is None:
            build.root.project = next((call.project for call in session_calls if call.project), None)
        for call in session_calls:
            owner = build.root if call.agent_id is None else nodes.get(call.agent_id, build.root)
            call.topic = owner.topic
            call.agent_type = owner.agent_type or "main"
        for node in nodes.values():
            node.project = next((call.project for call in node.calls if call.project), build.root.project)
        all_nodes = [build.root, *nodes.values()]
        drop_events = _detect_unexplained_drops(all_nodes)
        for event in drop_events:
            owner = build.root if event.agent_id is None else nodes.get(event.agent_id, build.root)
            owner.events.append(event)
        for node in all_nodes:
            node.events.sort(key=_event_sort_key)
        if any(node.calls or node.events for node in [build.root, *nodes.values()]):
            sessions.append(Session(build.session_id, build.root))
    return sessions


def load_corpus(
    root: Path | None = None,
    *,
    since: datetime | None = None,
    until: datetime | None = None,
) -> Corpus:
    """Read each selected transcript once and retain only usage metadata.

    A source file is read only through its initial byte-size snapshot, so an
    append during the scan cannot reorder or partially replace the report.
    A concurrent append is disclosed in coverage. Calls use their record's
    own timezone-aware timestamp and the half-open ``[since, until)`` range.
    """
    source_root = Path(root) if root is not None else default_projects_root()
    if since is not None:
        if since.tzinfo is None or since.utcoffset() is None:
            raise ValueError("since must include a timezone")
        since = since.astimezone(timezone.utc)
    if until is not None:
        if until.tzinfo is None or until.utcoffset() is None:
            raise ValueError("until must include a timezone")
        until = until.astimezone(timezone.utc)
    if since is not None and until is not None and until < since:
        raise ValueError("until must not be before since")
    corpus = Corpus(sessions=[])
    groups = _discover(source_root, corpus)
    all_calls: list[Call] = []
    seen_sources: set[tuple[str, str]] = set()
    for build in groups:
        files: list[tuple[Path, str | None]] = []
        if build.root_file is not None:
            files.append((build.root_file, None))
        files.extend((path, agent_id) for agent_id, path in sorted(build.agent_files.items()))
        for path, agent_id in files:
            canonical, source_id = _canonical_source(path)
            source_key = (build.session_id, canonical)
            if source_key in seen_sources:
                corpus.warnings.symlink_aliases += 1
                continue
            seen_sources.add(source_key)
            corpus.scanned_files += 1
            all_calls.extend(_scan_file(
                path,
                build=build,
                file_agent_id=agent_id,
                since=since,
                until=until,
                corpus=corpus,
            ))
    all_calls = _collapse_copied_prefixes(all_calls, corpus)
    all_calls.sort(key=lambda call: (call.timestamp, call.session_id, call.source_id, call.source_line, call.call_id))
    corpus.conflicts.sort(key=lambda conflict: (str(conflict.get("kind")), str(conflict.get("identity_id"))))
    corpus.sessions = _assemble(groups, all_calls, corpus)
    return corpus
