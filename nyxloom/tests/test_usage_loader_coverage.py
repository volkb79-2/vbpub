"""Behavioral cases for usage-loader validation and refusal paths."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

import pytest

from nyxloom.session_extract.usage import loader
from nyxloom.session_extract.usage.model import Corpus, LoadWarnings
from test_usage_report import SESSION, _assistant, _usage, _write_jsonl


NOW = datetime(2026, 10, 28, 11, 0, tzinfo=timezone.utc)


def _row(**changes):
    row = _assistant("req-1", "2026-10-28T11:00:00Z", usage=_usage())
    row.update(changes)
    return row


def _parse_call(row, *, file_agent_id=None, meta=None):
    return loader._call_from_row(
        row,
        session_id=SESSION,
        file_agent_id=file_agent_id,
        source_id="source-1",
        source_line=1,
        meta=meta,
        warnings=LoadWarnings(),
    )


def test_loader_identity_and_value_helpers_cover_invalid_values(tmp_path, monkeypatch):
    assert loader._opaque_id("test", NOW).startswith("test_")
    with pytest.raises(TypeError, match="unsupported opaque-ID component"):
        loader._opaque_id("test", object())
    assert loader._normalise_timestamp(None) is None
    assert loader._normalise_timestamp("not-a-time") is None
    assert loader._normalise_timestamp("2026-10-28T11:00:00") is None
    assert loader._clean_label(None) is None
    assert loader._clean_label("  \n ") is None
    assert loader._clean_label("../private") is None
    assert len(loader._clean_label("x" * 130)) == 120

    source = tmp_path / "source.jsonl"
    source.write_text("{}", encoding="utf-8")
    real_resolve = Path.resolve

    def fail_resolve(path, *args, **kwargs):
        if path == source:
            raise OSError("permission denied")
        return real_resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", fail_resolve)
    assert loader._canonical_source(source)[0] == str(source.absolute())


def test_default_projects_root_uses_the_configured_locator(tmp_path, monkeypatch):
    from nyxloom.session_extract import locate

    monkeypatch.setattr(locate, "_claude_projects_root", lambda: tmp_path)

    assert loader.default_projects_root() == tmp_path




def test_loader_integer_and_number_validation_counts_bad_usage_fields():
    warnings = LoadWarnings()
    assert loader._read_int({"n": 4}, "n", warnings) == 4
    for value in (True, "4", -1):
        assert loader._read_int({"n": value}, "n", warnings) == 0
    assert loader._read_int({}, "missing", warnings) == 0
    assert warnings.invalid_usage_fields == 3
    assert loader._nonnegative_int(0) == 0
    for value in (True, -1, 1.5):
        assert loader._nonnegative_int(value) is None
    assert loader._finite_number(0) == 0.0
    for value in (True, "1", -1, float("inf"), float("nan"), 10**10000):
        assert loader._finite_number(value) is None


@pytest.mark.parametrize(
    "row",
    [
        {},
        {"message": []},
        {"message": {"usage": []}},
        {"message": {"usage": {}, "model": ""}},
        {"message": {"usage": {}, "model": "<synthetic>"}},
        {"message": {"usage": {}, "model": "bad/model"}},
        {"message": {"usage": {}, "model": "claude-sonnet-5-5"}},
    ],
)
def test_call_parser_rejects_missing_synthetic_invalid_and_untimed_rows(row):
    warnings = LoadWarnings()
    result = loader._call_from_row(
        row, session_id=SESSION, file_agent_id=None, source_id="source",
        source_line=1, meta=None, warnings=warnings,
    )
    assert result is None
    message = row.get("message") if isinstance(row.get("message"), dict) else {}
    if isinstance(row.get("message"), dict) and not isinstance(message.get("usage"), dict):
        assert warnings.missing_usage == 1
    if message.get("model") == "<synthetic>":
        assert warnings.synthetic_models == 1
    if message.get("model") == "bad/model":
        assert warnings.invalid_metadata_values == 1
    if message.get("model") == "claude-sonnet-5-5":
        assert warnings.missing_timestamps == 1


def test_call_parser_sanitizes_optional_metadata_and_invalid_token_values():
    row = _row(
        requestId="bad/id", agentId="bad/agent", cwd="",
        effort="bad/effort", speed=False, service_tier="bad/tier", inference_geo="bad/geo",
    )
    row["message"]["id"] = "bad/message"
    row["message"]["usage"]["input_tokens"] = True
    row["message"]["usage"]["cache_creation"] = "not-a-dict"
    row["message"]["usage"].pop("speed")
    row["message"]["usage"].pop("service_tier")
    warnings = LoadWarnings()
    call = loader._call_from_row(
        row, session_id=SESSION, file_agent_id="file-agent", source_id="source",
        source_line=7, meta=loader._AgentMeta("file-agent", "meta-source", is_fork=True),
        warnings=warnings,
    )
    assert call is not None
    assert call.request_id is None and call.message_id is None
    assert call.agent_id == "file-agent" and call.project is None
    assert call.input_tokens == 0 and call.cache_write_unclassified_tokens == 13
    assert call.has_fork_source is True
    assert warnings.invalid_identity_fields == 3
    assert warnings.invalid_metadata_values == 4
    assert warnings.invalid_usage_fields == 1
    assert loader._call_local_keys(call) == ()


def test_call_parser_uses_per_turn_effort_and_message_identity_fallback():
    row = _row()
    row.pop("effort", None)
    row["perTurnEffort"] = "high"
    row.pop("requestId")
    warnings = LoadWarnings()
    call = loader._call_from_row(
        row, session_id=SESSION, file_agent_id=None, source_id="source",
        source_line=9, meta=None, warnings=warnings,
    )
    assert call is not None
    assert call.effort == "high"
    assert call.request_id is None
    assert loader._call_local_keys(call) == (("message", call.message_id),)

    row["message"]["id"] = "message-1"
    row["speed"] = "standard"
    row["message"]["usage"].pop("speed")
    assert _parse_call(row).speed == "standard"

    row["agentId"] = "row-agent"
    assert _parse_call(row).agent_id == "row-agent"

    row.pop("effort", None)
    row.pop("perTurnEffort")
    assert _parse_call(row).effort is None


def test_event_parsers_report_missing_times_and_rejection_classification():
    warnings = LoadWarnings()
    compact_base = {
        "type": "system", "subtype": "compact_boundary", "uuid": "unsafe/id",
        "compactMetadata": {"trigger": "auto", "preTokens": 100, "postTokens": 20},
    }
    assert loader._make_compaction_event(
        {**compact_base, "timestamp": "invalid"}, session_id=SESSION, agent_id=None,
        source_id="source", source_line=1, warnings=warnings,
    ) is None
    assert warnings.missing_timestamps == 1
    compact = loader._make_compaction_event(
        {**compact_base, "timestamp": "2026-10-28T11:00:00Z"}, session_id=SESSION,
        agent_id=None, source_id="source", source_line=2, warnings=warnings,
    )
    assert compact is not None and compact.source_record_id is None
    assert loader._make_compaction_event(
        {"type": "system"}, session_id=SESSION, agent_id=None,
        source_id="source", source_line=3, warnings=warnings,
    ) is None

    assert loader._make_rejection_event(
        {"timestamp": "bad"}, session_id=SESSION, agent_id=None,
        source_id="source", source_line=4, warnings=warnings,
    ) is None
    unclassified = loader._make_rejection_event(
        {"timestamp": "2026-10-28T11:00:00Z", "error": "unsafe code", "apiErrorStatus": True},
        session_id=SESSION, agent_id=None, source_id="source", source_line=5,
        warnings=warnings,
    )
    assert unclassified is not None and unclassified.limit_kind is None
    assert unclassified.error_code is None and unclassified.status_code is None
    params = {
        "timestamp": "2026-10-28T11:00:00Z", "uuid": "reject-1",
        "apiErrorParams": {"rate_limit_info": {
            "status": "rejected", "rateLimitType": "seven_day", "resetsAt": 10**100,
        }},
    }
    weekly = loader._make_rejection_event(
        params, session_id=SESSION, agent_id=None, source_id="source", source_line=6,
        warnings=warnings,
    )
    assert weekly is not None and weekly.limit_kind == "weekly" and weekly.reset_at is None
    assert weekly.source_record_id == "reject-1"
    assert warnings.unclassified_rejections == 1


def test_metadata_reader_handles_missing_malformed_non_object_and_invalid_fields(tmp_path):
    warnings = LoadWarnings()
    assert loader._read_meta(tmp_path / "missing.json", "a", warnings) is None
    malformed = tmp_path / "malformed.json"
    malformed.write_text("{", encoding="utf-8")
    assert loader._read_meta(malformed, "a", warnings) is None
    non_object = tmp_path / "array.json"
    non_object.write_text("[]", encoding="utf-8")
    assert loader._read_meta(non_object, "a", warnings) is None
    invalid = tmp_path / "invalid.json"
    invalid.write_text(json.dumps({
        "agentType": "unsafe/type", "description": " /private/path ",
        "toolUseId": "unsafe/id", "spawnDepth": True,
        "parentAgentId": "unsafe/id", "isFork": True,
    }), encoding="utf-8")
    meta = loader._read_meta(invalid, "a", warnings)
    assert meta is not None
    assert (meta.agent_type, meta.topic, meta.tool_use_id, meta.spawn_depth, meta.parent_agent_id) == (
        None, None, None, None, None,
    )
    assert meta.is_fork is True
    assert warnings.missing_meta == 3 and warnings.malformed_meta == 2
    assert warnings.invalid_metadata_values == 1


def test_discovery_rejects_missing_root_and_reports_unreadable_project(tmp_path, monkeypatch):
    corpus = Corpus(sessions=[])
    with pytest.raises(loader.UsageLoadError, match="directory is unavailable"):
        loader._discover(tmp_path / "missing", corpus)

    root = tmp_path / "projects"
    root.mkdir()
    project = root / "project"
    project.mkdir()
    real_glob = Path.glob

    def failing_glob(path, pattern):
        if path == project and pattern == "*.jsonl":
            raise PermissionError("denied")
        return real_glob(path, pattern)

    monkeypatch.setattr(Path, "glob", failing_glob)
    assert loader._discover(root, corpus) == []
    assert corpus.warnings.unreadable_files == 1


def test_discovery_translates_root_iteration_oserror(tmp_path, monkeypatch):
    root = tmp_path / "projects"
    root.mkdir()
    real_iterdir = Path.iterdir

    def fail_root(path):
        if path == root:
            raise PermissionError("denied")
        return real_iterdir(path)

    monkeypatch.setattr(Path, "iterdir", fail_root)
    with pytest.raises(loader.UsageLoadError, match="cannot read Claude Code projects directory"):
        loader._discover(root, Corpus(sessions=[]))


@pytest.mark.parametrize("root_file_present", [False, True])
def test_discovery_continues_when_subagent_transcript_glob_fails(
    tmp_path, monkeypatch, root_file_present,
):
    root = tmp_path / "projects"
    project = root / "project"
    session_dir = project / SESSION
    subagents = session_dir / "subagents"
    subagents.mkdir(parents=True)
    if root_file_present:
        (project / f"{SESSION}.jsonl").write_text("", encoding="utf-8")
    real_glob = Path.glob

    def fail_agent_glob(path, pattern):
        if path == subagents and pattern == "agent-*.jsonl":
            raise PermissionError("denied")
        return real_glob(path, pattern)

    monkeypatch.setattr(Path, "glob", fail_agent_glob)
    corpus = Corpus(sessions=[])
    groups = loader._discover(root, corpus)
    assert len(groups) == int(root_file_present)
    assert corpus.warnings.unreadable_files == 1


def test_discovery_continues_when_subagent_metadata_glob_fails(tmp_path, monkeypatch):
    root = tmp_path / "projects"
    project = root / "project"
    session_dir = project / SESSION
    subagents = session_dir / "subagents"
    subagents.mkdir(parents=True)
    (project / f"{SESSION}.jsonl").write_text("", encoding="utf-8")
    _write_jsonl(subagents / "agent-a.jsonl", [])
    real_glob = Path.glob
    seen_patterns = []

    def fail_meta_glob(path, pattern):
        seen_patterns.append(pattern)
        if pattern == "agent-*.meta.json":
            raise PermissionError("denied")
        return real_glob(path, pattern)

    monkeypatch.setattr(Path, "glob", fail_meta_glob)
    corpus = Corpus(sessions=[])
    groups = loader._discover(root, corpus)
    assert len(groups) == 1
    assert groups[0].agent_files.keys() == {"a"}
    assert "agent-*.meta.json" in seen_patterns
    assert corpus.warnings.unreadable_files == 1


def test_discovery_counts_unreadable_orphan_metadata_files(tmp_path):
    root = tmp_path / "projects"
    project = root / "project"
    subagents = project / SESSION / "subagents"
    subagents.mkdir(parents=True)
    (project / f"{SESSION}.jsonl").write_text("", encoding="utf-8")
    _write_jsonl(subagents / "agent-a.jsonl", [])
    (subagents / "agent-orphan.meta.json").write_text("{", encoding="utf-8")

    corpus = Corpus(sessions=[])
    groups = loader._discover(root, corpus)
    assert len(groups) == 1
    assert corpus.warnings.malformed_meta == 1


def test_scanner_counts_malformed_and_truncated_records_and_open_errors(tmp_path, monkeypatch):
    root = tmp_path / "project"
    root.mkdir()
    bad = root / "bad.jsonl"
    bad.write_bytes(b"\n{bad}\n[]\n{truncated")
    corpus = Corpus(sessions=[])
    build = loader._SessionBuild(
        session_id=SESSION, project_dir=root, session_dir=root,
        root_file=bad, root=loader.Node(SESSION, None, SESSION, None),
    )
    calls = loader._scan_file(
        bad, build=build, file_agent_id=None, since=None, until=None, corpus=corpus,
    )
    assert calls == []
    assert corpus.warnings.malformed_lines == 2
    assert corpus.warnings.truncated_lines == 1

    denied = root / "denied.jsonl"
    real_open = Path.open

    def fail_open(path, *args, **kwargs):
        if path == denied:
            raise PermissionError("denied")
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", fail_open)
    assert loader._scan_file(
        denied, build=build, file_agent_id=None, since=None, until=None, corpus=corpus,
    ) == []
    assert corpus.warnings.unreadable_files == 1


def test_scanner_event_summary_refusal_and_window_filter_paths(tmp_path):
    path = tmp_path / "events.jsonl"
    boundary = {
        "type": "system", "subtype": "compact_boundary", "uuid": "compact-1",
        "timestamp": "2026-10-28T11:00:00Z",
        "compactMetadata": {"trigger": "auto", "preTokens": 100, "postTokens": 20},
    }
    rows = [
        {"type": "agent-name", "agentName": "../path"},
        {"type": "assistant", "isApiErrorMessage": True, "timestamp": "bad", "message": {}},
        {"type": "assistant", "isApiErrorMessage": True,
         "timestamp": "2026-10-28T11:00:00Z", "error": "rate_limit", "message": {}},
        {"type": "system", "subtype": "compact_boundary"},
        boundary,
        {"type": "user", "isCompactSummary": True, "parentUuid": "unknown",
         "timestamp": "2026-10-28T11:01:00Z"},
        {"type": "user", "isCompactSummary": True, "parentUuid": "compact-1",
         "timestamp": "bad"},
        {"type": "user", "isCompactSummary": True, "parentUuid": "compact-1",
         "timestamp": "2026-10-28T11:02:00Z"},
    ]
    _write_jsonl(path, rows)
    root = path.parent
    build = loader._SessionBuild(
        session_id=SESSION, project_dir=root, session_dir=root,
        root_file=path, root=loader.Node(SESSION, None, SESSION, None),
    )
    corpus = Corpus(sessions=[])
    events = loader._scan_file(
        path, build=build, file_agent_id=None,
        since=datetime(2026, 10, 28, 11, 1, tzinfo=timezone.utc),
        until=datetime(2026, 10, 28, 11, 3, tzinfo=timezone.utc), corpus=corpus,
    )
    assert events == []
    assert build.events == []
    assert corpus.warnings.unpaired_compaction_summaries == 1
    assert corpus.warnings.missing_timestamps >= 2
    assert corpus.warnings.unclassified_rejections == 0
    assert build.titles == []


def test_scanner_keeps_valid_title_records_and_skips_unknown_record_types(tmp_path):
    root_path = tmp_path / f"{SESSION}.jsonl"
    agent_path = tmp_path / "agent-a.jsonl"
    _write_jsonl(root_path, [
        {"type": "ai-title", "aiTitle": "AI title"},
        {"type": "custom-title", "customTitle": "Custom title"},
        {"type": "agent-name", "agentName": "Root agent name"},
        {"type": "unknown-record", "private": "ignored"},
        _assistant("root-call", "2026-10-28T11:00:00Z"),
    ])
    _write_jsonl(agent_path, [{"type": "agent-name", "agentName": "Worker name"}])
    build = loader._SessionBuild(
        session_id=SESSION, project_dir=tmp_path, session_dir=tmp_path,
        root_file=root_path, root=loader.Node(SESSION, None, SESSION, None),
        agent_files={"agent-a": agent_path},
    )
    corpus = Corpus(sessions=[])

    root_calls = loader._scan_file(
        root_path, build=build, file_agent_id=None,
        since=None, until=None, corpus=corpus,
    )
    agent_calls = loader._scan_file(
        agent_path, build=build, file_agent_id="agent-a",
        since=None, until=None, corpus=corpus,
    )
    sessions = loader._assemble([build], root_calls + agent_calls, corpus)

    assert len(root_calls) == 1 and agent_calls == []
    assert build.titles == ["AI title", "Custom title", "Root agent name"]
    assert build.agent_names == {"agent-a": "Worker name"}
    assert sessions[0].root.topic == "Root agent name"




def test_scanner_tracks_api_and_paired_summary_timestamp_failures(tmp_path):
    path = tmp_path / "timestamps.jsonl"
    _write_jsonl(path, [
        {"type": "assistant", "isApiErrorMessage": True, "timestamp": "bad", "message": {}},
        {
            "type": "system", "subtype": "compact_boundary", "uuid": "compact-1",
            "timestamp": "2026-10-28T11:00:00Z",
            "compactMetadata": {"trigger": "auto", "preTokens": 100, "postTokens": 20},
        },
        {"type": "user", "isCompactSummary": True, "parentUuid": "compact-1", "timestamp": "bad"},
    ])
    build = loader._SessionBuild(
        session_id=SESSION, project_dir=tmp_path, session_dir=tmp_path,
        root_file=path, root=loader.Node(SESSION, None, SESSION, None),
    )
    corpus = Corpus(sessions=[])
    loader._scan_file(
        path, build=build, file_agent_id=None, since=None, until=None, corpus=corpus,
    )
    assert corpus.warnings.missing_timestamps == 2
    assert corpus.warnings.unpaired_compaction_summaries == 0
    assert len(build.events) == 1 and build.events[0].kind == "compaction"


@pytest.mark.parametrize("change", ["truncate", "append", "mtime", "error"])
def test_scanner_discloses_snapshot_changes_and_read_errors(tmp_path, monkeypatch, change):
    path = tmp_path / "snapshot.jsonl"
    _write_jsonl(path, [_assistant("scan-call", "2026-10-28T11:00:00Z")])
    root = path.parent
    build = loader._SessionBuild(
        session_id=SESSION, project_dir=root, session_dir=root,
        root_file=path, root=loader.Node(SESSION, None, SESSION, None),
    )
    corpus = Corpus(sessions=[])
    real_fstat = os.fstat
    calls = 0

    def mutate_after_snapshot(fd):
        nonlocal calls
        calls += 1
        initial = real_fstat(fd)
        if calls == 1:
            if change == "truncate":
                path.write_bytes(b"")
            elif change == "append":
                with path.open("ab") as handle:
                    handle.write(b"\n")
            elif change == "mtime":
                os.utime(path, ns=(initial.st_atime_ns, initial.st_mtime_ns + 1_000_000_000))
        elif change == "error":
            raise OSError("snapshot unavailable")
        return initial

    monkeypatch.setattr(loader.os, "fstat", mutate_after_snapshot)
    result = loader._scan_file(
        path, build=build, file_agent_id=None, since=None, until=None, corpus=corpus,
    )
    assert corpus.warnings.concurrent_changes == int(change in {"truncate", "append", "mtime"})
    assert corpus.warnings.unreadable_files == int(change == "error")
    if change in {"truncate", "error"}:
        assert result == []
    elif change in {"append", "mtime"}:
        assert len(result) == 1


def test_unresolved_lineage_warning_and_cross_file_event_identity_edges():
    corpus = Corpus(sessions=[])
    for status in (
        "unresolved_ambiguous_tool_use", "unresolved_missing_lineage_metadata",
        "unresolved_parent_conflict_cycle", "unresolved_unknown_parent", "unresolved_cycle",
        "resolved_tool_use_id",
    ):
        loader._append_lineage_warnings(status, corpus)
    assert corpus.warnings.ambiguous_parent_edges == 1
    assert corpus.warnings.missing_lineage_metadata == 1
    assert corpus.warnings.parent_conflicts == 2
    assert corpus.warnings.unattributed_agents == 5

    base = dict(
        event_id="e", kind="api_rejection", timestamp=NOW, session_id=SESSION,
        agent_id=None, source_record_id=None, source_id="source", source_line=1,
    )
    assert loader._cross_file_event_key(loader.UsageEvent(**base)) is None
    assert loader._cross_file_event_key(loader.UsageEvent(**{
        **base, "source_record_id": "reject-1",
    })) is not None
    assert loader._cross_file_event_key(loader.UsageEvent(**{**base, "kind": "other"})) is None
    assert loader._cross_file_event_key(loader.UsageEvent(**{**base, "kind": "compaction"})) is None


def test_assembly_handles_partial_lineage_results_cycles_and_orphan_events(tmp_path, monkeypatch):
    call = _parse_call(_row())
    assert call is not None
    call.agent_id = "agent-a"
    root = loader.Node(SESSION, None, SESSION, None)
    build = loader._SessionBuild(
        session_id=SESSION, project_dir=tmp_path, session_dir=tmp_path,
        root_file=None, root=root, agent_files={"agent-a": tmp_path / "agent-a.jsonl"},
    )
    monkeypatch.setattr(loader.claude_code, "resolve_agent_lineage", lambda *_a, **_k: {})
    corpus = Corpus(sessions=[])
    sessions = loader._assemble([build], [call], corpus)
    assert sessions[0].root.children[0].kind == "unattributed"
    assert corpus.warnings.missing_lineage_metadata == 1

    cycle_call = _parse_call(_row())
    assert cycle_call is not None
    cycle_call.agent_id = "agent-a"
    cycle_build = loader._SessionBuild(
        session_id=SESSION, project_dir=tmp_path, session_dir=tmp_path,
        root_file=None, root=loader.Node(SESSION, None, SESSION, None),
        agent_files={"agent-a": tmp_path / "agent-a.jsonl"},
    )
    lineage = loader.claude_code.AgentLineage(
        agent_id="agent-a", parent_agent_id="agent-a", parent_is_root=False,
        status="resolved_parent_agent_id", candidate_agent_ids=("agent-a",),
        claimed_parent_agent_id="agent-a", tool_use_id=None,
    )
    monkeypatch.setattr(
        loader.claude_code, "resolve_agent_lineage",
        lambda *_a, **_k: {"agent-a": lineage},
    )
    cycle_corpus = Corpus(sessions=[])
    cycle_sessions = loader._assemble([cycle_build], [cycle_call], cycle_corpus)
    cycle_node = cycle_sessions[0].root.children[0].children[0]
    assert cycle_node.lineage_status == "unresolved_cycle"

    orphan_event = loader.UsageEvent(
        event_id="orphan-event", kind="api_rejection", timestamp=NOW,
        session_id=SESSION, agent_id="not-a-node", source_record_id=None,
        source_id="source", source_line=2,
    )
    event_build = loader._SessionBuild(
        session_id=SESSION, project_dir=tmp_path, session_dir=tmp_path,
        root_file=None, root=loader.Node(SESSION, None, SESSION, None),
        events=[orphan_event],
    )
    monkeypatch.setattr(loader.claude_code, "resolve_agent_lineage", lambda *_a, **_k: {})
    event_sessions = loader._assemble([event_build], [], Corpus(sessions=[]))
    assert event_sessions[0].root.events == [orphan_event]


def test_copied_prefix_key_retains_calls_without_message_ids():
    call = _parse_call(_row())
    assert call is not None
    call.message_id = None
    corpus = Corpus(sessions=[])
    assert loader._copied_prefix_key(call) is None
    assert loader._collapse_copied_prefixes([call], corpus) == [call]


@pytest.mark.parametrize(
    "since,until,message",
    [
        (datetime(2026, 10, 28, 11), None, "since must include a timezone"),
        (None, datetime(2026, 10, 28, 11), "until must include a timezone"),
        (datetime(2026, 10, 28, 12, tzinfo=timezone.utc),
         datetime(2026, 10, 28, 11, tzinfo=timezone.utc), "until must not be before since"),
    ],
)
def test_load_corpus_rejects_invalid_bounds_before_discovery(tmp_path, since, until, message):
    with pytest.raises(ValueError, match=message):
        loader.load_corpus(tmp_path, since=since, until=until)


def test_load_corpus_accepts_agent_only_session_and_skips_symlink_aliases(tmp_path):
    root = tmp_path / "projects"
    session_dir = root / "project" / SESSION
    subagents = session_dir / "subagents"
    subagents.mkdir(parents=True)
    agent_path = subagents / "agent-only.jsonl"
    _write_jsonl(agent_path, [_assistant("agent-only", "2026-10-28T11:00:00Z", agent_id="only")])
    agent_only = loader.load_corpus(root)
    assert agent_only.scanned_files == 1
    assert agent_only.sessions[0].root.calls == []

    project_dir = root / "alias-project"
    project_dir.mkdir()
    root_file = project_dir / f"{SESSION}.jsonl"
    _write_jsonl(root_file, [_assistant("root", "2026-10-28T11:00:00Z")])
    alias_session = project_dir / SESSION / "subagents"
    alias_session.mkdir(parents=True)
    (alias_session / "agent-alias.jsonl").symlink_to(root_file)
    aliased = loader.load_corpus(root)
    assert aliased.warnings.symlink_aliases == 1
