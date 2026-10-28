"""Load Claude Code token metadata without retaining transcript content."""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .model import Call, Compaction, Corpus, LoadWarnings, Node, Session


class UsageLoadError(ValueError):
    """Raised when the Claude Code transcript source cannot be scanned."""


@dataclass(slots=True)
class _AgentMeta:
    agent_id: str
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
    root: Node
    agent_files: dict[str, Path] = field(default_factory=dict)
    metas: dict[str, _AgentMeta] = field(default_factory=dict)
    tool_use_parents: dict[str, str | None] = field(default_factory=dict)
    titles: list[str] = field(default_factory=list)
    agent_names: dict[str, str] = field(default_factory=dict)
    compactions: list[tuple[str | None, int, Compaction]] = field(default_factory=list)


def default_projects_root() -> Path:
    """Return Claude Code's configured projects root without probing it."""
    from ..locate import _claude_projects_root

    return _claude_projects_root()


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
    if not text:
        return None
    return text[:120]


def _read_int(source: dict[str, Any], key: str, warnings: LoadWarnings) -> int:
    value = source.get(key, 0)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        warnings.invalid_usage_fields += 1
        return 0
    return value


def _tool_use_ids(message: dict[str, Any]) -> list[str]:
    content = message.get("content")
    if not isinstance(content, list):
        return []
    found: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "tool_use" and block.get("name") == "Agent":
            identifier = block.get("id")
            if isinstance(identifier, str) and identifier:
                found.append(identifier)
    return found


def _make_call(
    row: dict[str, Any],
    *,
    session_id: str,
    file_agent_id: str | None,
    source_order: int,
    warnings: LoadWarnings,
) -> tuple[Call, str | None] | None:
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
    timestamp = _normalise_timestamp(row.get("timestamp"))
    if timestamp is None:
        warnings.missing_timestamps += 1
        return None

    request_id = row.get("requestId")
    if not isinstance(request_id, str) or not request_id:
        request_id = message.get("id")
        if not isinstance(request_id, str) or not request_id:
            request_id = row.get("messageId")
        if not isinstance(request_id, str) or not request_id:
            request_id = None

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
        cache_total = max(cache_total, cache_5m + cache_1h)
        cache_other = cache_total
    else:
        split_total = cache_5m + cache_1h
        cache_total = max(cache_total, split_total)
        cache_other = max(0, cache_total - split_total)

    output_details = usage.get("output_tokens_details")
    output_details = output_details if isinstance(output_details, dict) else {}
    thinking = _read_int(output_details, "thinking_tokens", warnings)
    effort = row.get("effort")
    if not isinstance(effort, str) or not effort:
        effort = row.get("perTurnEffort")
    if not isinstance(effort, str) or not effort:
        effort = None
    speed = usage.get("speed", row.get("speed"))
    tier = usage.get("service_tier", row.get("service_tier"))
    if not isinstance(speed, str):
        speed = None
    if not isinstance(tier, str):
        tier = None
    project = row.get("cwd")
    if not isinstance(project, str) or not project:
        project = None
    row_agent_id = row.get("agentId")
    agent_id = file_agent_id
    if agent_id is None and isinstance(row_agent_id, str) and row_agent_id:
        agent_id = row_agent_id
    call = Call(
        request_id=request_id,
        timestamp=timestamp,
        session_id=session_id,
        agent_id=agent_id,
        model=model,
        effort=effort,
        speed=speed,
        service_tier=tier,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_tokens=cache_read,
        cache_write_5m_tokens=cache_5m,
        cache_write_1h_tokens=cache_1h,
        cache_write_unclassified_tokens=cache_other,
        thinking_tokens=thinking,
        context_size=input_tokens + cache_read + cache_total,
        project=project,
        kind="compaction_summary" if row.get("isCompactSummary") is True else "normal",
        sidechain=row.get("isSidechain") is True,
        source_order=source_order,
    )
    return call, request_id


def _same_usage(left: Call, right: Call) -> bool:
    return (
        left.model == right.model
        and left.effort == right.effort
        and left.speed == right.speed
        and left.service_tier == right.service_tier
        and left.input_tokens == right.input_tokens
        and left.output_tokens == right.output_tokens
        and left.cache_read_tokens == right.cache_read_tokens
        and left.cache_write_5m_tokens == right.cache_write_5m_tokens
        and left.cache_write_1h_tokens == right.cache_write_1h_tokens
        and left.cache_write_unclassified_tokens == right.cache_write_unclassified_tokens
        and left.thinking_tokens == right.thinking_tokens
    )


def _compaction_from_row(
    row: dict[str, Any], timestamp: datetime | None,
    warnings: LoadWarnings,
) -> Compaction | None:
    metadata = row.get("compactMetadata")
    metadata = metadata if isinstance(metadata, dict) else {}
    if timestamp is None:
        warnings.missing_timestamps += 1
        return None
    pre = metadata.get("preTokens", row.get("preTokens"))
    post = metadata.get("postTokens", row.get("postTokens"))
    if isinstance(pre, bool) or not isinstance(pre, int) or pre < 0:
        pre = None
    if isinstance(post, bool) or not isinstance(post, int) or post < 0:
        post = None
    trigger = metadata.get("trigger")
    if not isinstance(trigger, str):
        trigger = None
    return Compaction(timestamp, pre, post, trigger)


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
    agent_type = value.get("agentType")
    topic = _clean_label(value.get("description"))
    tool_use_id = value.get("toolUseId")
    if not isinstance(tool_use_id, str) or not tool_use_id:
        tool_use_id = None
    spawn_depth = value.get("spawnDepth")
    if isinstance(spawn_depth, bool) or not isinstance(spawn_depth, int) or spawn_depth < 1:
        spawn_depth = None
    parent_agent_id = value.get("parentAgentId")
    if not isinstance(parent_agent_id, str) or not parent_agent_id:
        parent_agent_id = None
    return _AgentMeta(
        agent_id=agent_id,
        agent_type=agent_type if isinstance(agent_type, str) and agent_type else None,
        topic=topic,
        tool_use_id=tool_use_id,
        spawn_depth=spawn_depth,
        parent_agent_id=parent_agent_id,
        is_fork=value.get("isFork") is True,
    )


def _file_mtime(path: Path) -> float | None:
    try:
        return path.stat().st_mtime
    except OSError:
        return None


def _discover(root: Path, since: datetime | None, corpus: Corpus) -> list[_SessionBuild]:
    if not root.is_dir():
        raise UsageLoadError(f"Claude Code projects directory is unavailable: {root}")
    try:
        project_dirs = sorted(path for path in root.iterdir() if path.is_dir())
    except OSError as exc:
        raise UsageLoadError(f"cannot read Claude Code projects directory: {exc}") from exc
    groups: list[_SessionBuild] = []
    cutoff = since.timestamp() if since is not None else None
    for project_dir in project_dirs:
        try:
            root_files = {path.stem: path for path in project_dir.glob("*.jsonl") if path.is_file()}
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
            active = False
            for path in files:
                mtime = _file_mtime(path)
                if mtime is None:
                    corpus.warnings.unreadable_files += 1
                    active = True
                    continue
                if cutoff is None or mtime >= cutoff:
                    active = True
            if not active:
                corpus.pruned_files += len(files)
                corpus.pruned_sessions += 1
                continue
            root_node = Node(
                node_id=session_id,
                parent_id=None,
                session_id=session_id,
                agent_id=None,
                kind="main",
            )
            build = _SessionBuild(
                session_id=session_id,
                project_dir=project_dir,
                session_dir=session_dir,
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
            # Also retain metadata-only nodes, which can be a parent of a live
            # child even if their transcript file was pruned or removed.
            try:
                meta_paths = sorted((session_dir / "subagents").glob("agent-*.meta.json"))
            except OSError:
                meta_paths = []
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
    include_calls: bool,
    since: datetime | None,
    until: datetime | None,
    corpus: Corpus,
    next_order: list[int],
    deduplicated: dict[tuple[str, str], Call],
    unkeyed: list[Call],
) -> None:
    try:
        handle = path.open("rb")
    except OSError:
        corpus.warnings.unreadable_files += 1
        return
    with handle:
        for raw_line in handle:
            if not raw_line.strip():
                continue
            try:
                row = json.loads(raw_line)
            except (json.JSONDecodeError, UnicodeDecodeError):
                corpus.warnings.malformed_lines += 1
                continue
            if not isinstance(row, dict):
                corpus.warnings.malformed_lines += 1
                continue
            record_type = row.get("type")
            if record_type not in {
                "assistant", "system", "user", "ai-title", "custom-title", "agent-name",
            }:
                continue
            if record_type == "user" and row.get("isCompactSummary") is not True \
                    and not isinstance(row.get("compactMetadata"), dict):
                continue
            next_order[0] += 1
            order = next_order[0]
            row_session = row.get("sessionId")
            if isinstance(row_session, str) and row_session and row_session != build.session_id:
                corpus.warnings.session_id_mismatches += 1
            message = row.get("message")
            if record_type == "assistant" and isinstance(message, dict):
                for tool_use_id in _tool_use_ids(message):
                    previous = build.tool_use_parents.get(tool_use_id)
                    if tool_use_id in build.tool_use_parents and previous != file_agent_id:
                        corpus.warnings.parent_conflicts += 1
                    else:
                        build.tool_use_parents[tool_use_id] = file_agent_id
                if not include_calls:
                    continue
                result = _make_call(
                    row,
                    session_id=build.session_id,
                    file_agent_id=file_agent_id,
                    source_order=order,
                    warnings=corpus.warnings,
                )
                if result is None:
                    continue
                call, dedup_id = result
                # An explicit historical end bound must be applied before
                # requestId de-duplication: a later streaming duplicate beyond
                # the requested window cannot replace its in-window row.
                if (
                    (since is not None and call.timestamp < since)
                    or (until is not None and call.timestamp > until)
                ):
                    continue
                if dedup_id is None:
                    unkeyed.append(call)
                    continue
                key = (build.session_id, dedup_id)
                previous_call = deduplicated.get(key)
                if previous_call is not None:
                    corpus.warnings.duplicate_requests += 1
                    if not _same_usage(previous_call, call) or previous_call.agent_id != call.agent_id:
                        corpus.warnings.duplicate_conflicts += 1
                deduplicated[key] = call
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
            is_compaction = (
                record_type == "system" and row.get("subtype") == "compact_boundary"
            ) or row.get("isCompactSummary") is True or isinstance(row.get("compactMetadata"), dict)
            if is_compaction:
                event = _compaction_from_row(
                    row, _normalise_timestamp(row.get("timestamp")), corpus.warnings,
                )
                if event is not None:
                    event_agent = row.get("agentId")
                    if not isinstance(event_agent, str) or not event_agent:
                        event_agent = file_agent_id
                    build.compactions.append((event_agent, order, event))


def _ensure_agent_node(
    build: _SessionBuild,
    agent_id: str,
) -> Node:
    node_id = f"{build.session_id}:agent:{agent_id}"
    meta = build.metas.get(agent_id)
    return Node(
        node_id=node_id,
        parent_id=None,
        session_id=build.session_id,
        agent_id=agent_id,
        agent_type=(meta.agent_type if meta and meta.agent_type else "unknown"),
        topic=(meta.topic if meta and meta.topic else build.agent_names.get(agent_id)),
        kind="fork" if meta and meta.is_fork else "subagent",
    )


def _parent_for_agent(
    build: _SessionBuild,
    meta: _AgentMeta | None,
    nodes: dict[str, Node],
    warnings: LoadWarnings,
) -> Node | None:
    if meta is None:
        return None
    linked_parent = (
        build.tool_use_parents.get(meta.tool_use_id)
        if meta.tool_use_id is not None else None
    )
    if meta.tool_use_id is not None and meta.tool_use_id in build.tool_use_parents:
        if meta.parent_agent_id is not None and meta.parent_agent_id != linked_parent:
            warnings.parent_conflicts += 1
        if linked_parent is None:
            return build.root
        return nodes.get(linked_parent)
    if meta.parent_agent_id is not None:
        return nodes.get(meta.parent_agent_id)
    if meta.spawn_depth == 1:
        return build.root
    return None


def _would_cycle(node: Node, parent: Node, nodes: dict[str, Node]) -> bool:
    current: Node | None = parent
    seen: set[str] = set()
    while current is not None:
        if current.node_id == node.node_id or current.node_id in seen:
            return True
        seen.add(current.node_id)
        current = nodes.get(current.parent_id) if current.parent_id is not None else None
    return False


def _assemble(
    groups: list[_SessionBuild],
    calls_by_session: dict[str, list[Call]],
    corpus: Corpus,
) -> list[Session]:
    sessions: list[Session] = []
    for build in groups:
        agent_ids = set(build.agent_files) | set(build.metas)
        agent_ids.update(
            call.agent_id for call in calls_by_session.get(build.session_id, [])
            if call.agent_id is not None
        )
        nodes = {agent_id: _ensure_agent_node(build, agent_id)
                 for agent_id in sorted(agent_ids)}
        unattached: list[Node] = []
        for agent_id in sorted(nodes):
            node = nodes[agent_id]
            meta = build.metas.get(agent_id)
            parent = _parent_for_agent(build, meta, nodes, corpus.warnings)
            if parent is None or _would_cycle(node, parent, nodes):
                unattached.append(node)
                corpus.warnings.unattributed_agents += 1
                continue
            node.parent_id = parent.node_id
            parent.children.append(node)
        if unattached:
            group_node = Node(
                node_id=f"{build.session_id}:unattributed",
                parent_id=build.root.node_id,
                session_id=build.session_id,
                agent_id=None,
                agent_type="unknown",
                topic="(unattributed)",
                kind="unattributed",
            )
            for node in unattached:
                node.parent_id = group_node.node_id
                group_node.children.append(node)
            build.root.children.append(group_node)
        for call in calls_by_session.get(build.session_id, []):
            if call.agent_id is None:
                build.root.calls.append(call)
            elif call.agent_id in nodes:
                nodes[call.agent_id].calls.append(call)
            else:
                build.root.calls.append(call)
        for agent_id, _order, event in build.compactions:
            target = nodes.get(agent_id) if agent_id is not None else None
            if target is None:
                target = build.root
            target.compactions.append(event)
        session_calls = calls_by_session.get(build.session_id, [])
        for agent_id, _order, event in build.compactions:
            following = [
                call for call in session_calls
                if call.agent_id == agent_id and call.source_order > _order
            ]
            if following:
                next_call = min(following, key=lambda call: call.source_order)
                next_call.compaction_before = True
                next_call.compact_pre = event.pre_tokens
                next_call.compact_post = event.post_tokens
        for node in [build.root, *nodes.values()]:
            node.calls.sort(key=lambda call: (call.timestamp, call.source_order))
            node.compactions.sort(key=lambda event: event.timestamp)
        if not build.root.topic and build.titles:
            build.root.topic = build.titles[-1]
        project = next((call.project for call in build.root.calls if call.project), None)
        if project is None:
            project = next((call.project for call in session_calls if call.project), None)
        build.root.project = project
        for call in build.root.calls:
            call.topic = build.root.topic
            call.agent_type = "main"
        for node in nodes.values():
            node.project = next((call.project for call in node.calls if call.project), project)
            for call in node.calls:
                call.topic = node.topic
                call.agent_type = node.agent_type
        sessions.append(Session(build.session_id, build.root))
    return sessions


def load_corpus(
    root: Path | None = None,
    *,
    since: datetime | None = None,
    until: datetime | None = None,
) -> Corpus:
    """Read transcript files and retain only usage and safe attribution metadata.

    When ``since`` is supplied, file modification time selects active session
    families. Calls are also limited to the supplied timestamp bounds before
    de-duplication, so a duplicate outside a historical window cannot replace
    one inside it. Older sibling transcripts in an active family are read for
    Agent-tool lineage only, so resumed/nested agents keep their parent edge
    without adding old calls to the report.
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
    groups = _discover(source_root, since, corpus)
    deduplicated: dict[tuple[str, str], Call] = {}
    unkeyed: list[Call] = []
    next_order = [0]
    for build in groups:
        files: list[tuple[Path, str | None]] = []
        root_file = build.project_dir / f"{build.session_id}.jsonl"
        if root_file.is_file():
            files.append((root_file, None))
        files.extend((path, agent_id) for agent_id, path in sorted(build.agent_files.items()))
        for path, agent_id in files:
            mtime = _file_mtime(path)
            include_calls = since is None or mtime is None or mtime >= since.timestamp()
            if since is not None and not include_calls:
                corpus.pruned_files += 1
            corpus.scanned_files += 1
            _scan_file(
                path,
                build=build,
                file_agent_id=agent_id,
                include_calls=include_calls,
                since=since,
                until=until,
                corpus=corpus,
                next_order=next_order,
                deduplicated=deduplicated,
                unkeyed=unkeyed,
            )
    all_calls = list(deduplicated.values()) + unkeyed
    calls_by_session: dict[str, list[Call]] = {}
    for call in all_calls:
        calls_by_session.setdefault(call.session_id, []).append(call)
    for calls in calls_by_session.values():
        calls.sort(key=lambda call: (call.timestamp, call.source_order))
    corpus.sessions = _assemble(groups, calls_by_session, corpus)
    return corpus
