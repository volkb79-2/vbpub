"""Behavioral coverage for local harness transcript search."""

from __future__ import annotations

import io
import json
import os
import sqlite3
from collections import Counter
from concurrent.futures import Future
from pathlib import Path
from types import SimpleNamespace

import pytest
from nyxloom.cli_registry import harness_cli
from nyxloom.session_extract.locate import LocateError

from nyxloom import harness_search as search


@pytest.fixture
def home(tmp_path, monkeypatch):
    path = tmp_path / "home"
    path.mkdir()
    monkeypatch.setenv("HOME", str(path))
    for name in ("CLAUDE_CONFIG_DIR", "CODEX_HOME", "OPENCODE_DB", "XDG_DATA_HOME"):
        monkeypatch.delenv(name, raising=False)
    return path


def _write_jsonl(path: Path, *records) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records),
        encoding="utf-8",
    )
    return path


def _opencode_db(path: Path, *, messages=(), parts=()) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.executescript(
        "CREATE TABLE message (id TEXT, session_id TEXT, data TEXT);"
        "CREATE TABLE part (id TEXT, message_id TEXT, session_id TEXT, data TEXT);"
    )
    connection.executemany("INSERT INTO message VALUES (?, ?, ?)", messages)
    connection.executemany("INSERT INTO part VALUES (?, ?, ?, ?)", parts)
    connection.commit()
    connection.close()
    return path


def test_query_words_are_casefolded_split_on_punctuation_and_deduplicated():
    assert search._terms("CLI-Extended gate, CLI backlog!") == (
        "cli", "extended", "gate", "backlog",
    )
    assert search._terms("Straße STRASSE") == ("strasse",)


def test_query_without_word_characters_is_rejected():
    with pytest.raises(search.SearchError, match="at least one word"):
        search._terms("--- !!!")


def test_root_availability_distinguishes_missing_non_directory_and_scan_failure(
    tmp_path, monkeypatch,
):
    assert search._root_is_available(tmp_path / "missing") is False
    file_path = tmp_path / "file"
    file_path.write_text("not a directory", encoding="utf-8")
    with pytest.raises(search.SearchError, match="not a directory"):
        search._root_is_available(file_path)

    root = tmp_path / "root"
    root.mkdir()

    def fail_scan(_root):
        raise LocateError("scan is incomplete")

    monkeypatch.setattr(search, "_scan_directory", fail_scan)
    with pytest.raises(search.SearchError, match="scan is incomplete"):
        search._root_is_available(root)


def test_root_stat_error_refuses_to_claim_an_empty_store(tmp_path, monkeypatch):
    root = tmp_path / "inaccessible"
    original_stat = Path.stat

    def fail_stat(path, *args, **kwargs):
        if path == root:
            raise PermissionError("forced root-stat failure")
        return original_stat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", fail_stat)
    with pytest.raises(search.SearchError, match="indeterminate.*PermissionError"):
        search._root_is_available(root)


def test_walk_files_includes_regular_files_and_file_symlinks_only(tmp_path):
    root = tmp_path / "tree"
    nested = root / "nested"
    nested.mkdir(parents=True)
    regular = root / "session.jsonl"
    regular.write_text("{}\n", encoding="utf-8")
    nested_file = nested / "other.jsonl"
    nested_file.write_text("{}\n", encoding="utf-8")
    file_link = root / "linked.jsonl"
    file_link.symlink_to(regular)
    (root / "dangling.jsonl").symlink_to(root / "absent.jsonl")
    (root / "directory-link").symlink_to(nested, target_is_directory=True)
    os.mkfifo(root / "pipe")

    assert set(search._walk_files(root)) == {regular, nested_file, file_link}


def test_walk_files_refuses_unreadable_entries_and_symlink_targets(tmp_path, monkeypatch):
    root = tmp_path / "tree"
    root.mkdir()
    bad_entry = root / "bad.jsonl"
    bad_entry.write_text("{}\n", encoding="utf-8")
    original_lstat = Path.lstat

    def fail_lstat(path, *args, **kwargs):
        if path == bad_entry:
            raise PermissionError("forced lstat failure")
        return original_lstat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "lstat", fail_lstat)
    with pytest.raises(search.SearchError, match="indeterminate.*bad.jsonl"):
        list(search._walk_files(root))

    monkeypatch.setattr(Path, "lstat", original_lstat)
    link_target = tmp_path / "target.jsonl"
    link_target.write_text("{}\n", encoding="utf-8")
    link = root / "linked.jsonl"
    link.symlink_to(link_target)
    original_stat = Path.stat

    def fail_target_stat(path, *args, **kwargs):
        if path == link:
            raise PermissionError("forced link-stat failure")
        return original_stat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", fail_target_stat)
    with pytest.raises(search.SearchError, match="indeterminate.*linked.jsonl"):
        list(search._walk_files(root))


def test_walk_files_translates_directory_scan_errors(tmp_path, monkeypatch):
    root = tmp_path / "tree"
    root.mkdir()

    def fail_scan(_directory):
        raise LocateError("directory scan failed")

    monkeypatch.setattr(search, "_scan_directory", fail_scan)
    with pytest.raises(search.SearchError, match="directory scan failed"):
        list(search._walk_files(root))


def test_claude_discovery_filters_transcripts_and_names_subagents(tmp_path):
    root = tmp_path / "projects"
    session = _write_jsonl(
        root / "project" / "session-1.jsonl",
        {"sessionId": "session-1", "parentUuid": None, "message": "needle"},
    )
    (root / "project" / "not-a-session.jsonl").write_text(
        "malformed json\n{\"type\": \"message\"}\n", encoding="utf-8",
    )
    (root / "project" / "notes.txt").write_text("ignore", encoding="utf-8")
    agent = _write_jsonl(
        root / "project" / "session-1" / "subagents" / "agent-worker-7.jsonl",
        {"sessionId": "session-1", "parentUuid": "parent", "message": "needle"},
    )

    found = search._claude_sessions(root)
    assert [(row.session_id, row.source) for row in found] == [
        ("session-1", str(session)), ("worker-7", str(agent)),
    ]
    assert search._claude_sessions(root / "absent") == []


def test_claude_discovery_deduplicates_physical_transcript_aliases(tmp_path):
    root = tmp_path / "projects"
    session_id = "12345678-1234-1234-1234-123456789abc"
    original = _write_jsonl(
        root / "project" / f"{session_id}.jsonl",
        {"sessionId": session_id, "parentUuid": None},
    )
    (original.parent / "000-alias.jsonl").symlink_to(original)
    found = search._claude_sessions(root)
    assert len(found) == 1
    assert found[0].session_id == session_id
    assert found[0].source == str(original.resolve())


def test_claude_hard_link_alias_uses_source_session_id_and_canonical_path(
    tmp_path,
):
    root = tmp_path / "projects"
    session_id = "12345678-1234-1234-1234-123456789abc"
    original = _write_jsonl(
        root / "project" / f"{session_id}.jsonl",
        {"sessionId": session_id, "parentUuid": None},
    )
    os.link(original, original.with_name("000-alias.jsonl"))

    found = search._claude_sessions(root)

    assert [(row.session_id, row.source) for row in found] == [
        (session_id, str(original.resolve())),
    ]


def test_claude_hard_link_subagent_alias_prefers_agent_id_path(tmp_path):
    root = tmp_path / "projects"
    agent_id = "a36c6ff1d3cc69767"
    parent_id = "12345678-1234-1234-1234-123456789abc"
    original = _write_jsonl(
        root / "project" / parent_id / "subagents" / f"agent-{agent_id}.jsonl",
        {"sessionId": parent_id, "parentUuid": "parent"},
    )
    os.link(original, original.with_name("000-alias.jsonl"))

    found = search._claude_sessions(root)

    assert [(row.session_id, row.source) for row in found] == [
        (agent_id, str(original.resolve())),
    ]


def test_claude_hard_links_with_conflicting_agent_ids_are_indeterminate(tmp_path):
    root = tmp_path / "projects"
    parent_id = "12345678-1234-1234-1234-123456789abc"
    original = _write_jsonl(
        root / "project" / parent_id / "subagents" / "agent-aaaaaaaaaaaaaaaaa.jsonl",
        {"sessionId": parent_id, "parentUuid": "parent"},
    )
    os.link(
        original,
        original.with_name("agent-bbbbbbbbbbbbbbbbb.jsonl"),
    )

    with pytest.raises(search.SearchError, match="conflicting session IDs"):
        search._claude_sessions(root)


def test_claude_hard_link_agent_ids_that_only_differ_by_case_are_equivalent(
    tmp_path,
):
    root = tmp_path / "projects"
    parent_id = "12345678-1234-1234-1234-123456789abc"
    agent_id = "a36c6ff1d3cc69767"
    original = _write_jsonl(
        root / "project" / parent_id / "subagents" / f"agent-{agent_id}.jsonl",
        {"sessionId": parent_id, "parentUuid": "parent"},
    )
    os.link(original, original.with_name(f"agent-{agent_id.upper()}.jsonl"))

    found = search._claude_sessions(root)

    assert len(found) == 1
    assert found[0].session_id.casefold() == agent_id.casefold()


def test_claude_cross_root_hard_link_aliases_upgrade_to_agent_filename(tmp_path):
    first_root = tmp_path / "first-projects"
    second_root = tmp_path / "second-projects"
    parent_id = "12345678-1234-1234-1234-123456789abc"
    agent_id = "a36c6ff1d3cc69767"
    original = _write_jsonl(
        second_root / "project" / parent_id / "subagents" / f"agent-{agent_id}.jsonl",
        {"sessionId": parent_id, "parentUuid": "parent"},
    )
    alias = first_root / "project" / "000-alias.jsonl"
    alias.parent.mkdir(parents=True)
    os.link(original, alias)

    found = search._store_sessions(
        "claude", source_roots=(first_root, second_root),
    )

    assert [(row.session_id, row.source) for row in found] == [
        (agent_id, str(original.resolve())),
    ]


def test_claude_conflicting_hard_link_agent_ids_across_roots_are_indeterminate(
    tmp_path,
):
    first_root = tmp_path / "first-projects"
    second_root = tmp_path / "second-projects"
    parent_id = "12345678-1234-1234-1234-123456789abc"
    original = _write_jsonl(
        first_root / "project" / parent_id / "subagents" / "agent-aaaaaaaaaaaaaaaaa.jsonl",
        {"sessionId": parent_id, "parentUuid": "parent"},
    )
    alias = second_root / "project" / parent_id / "subagents" / "agent-bbbbbbbbbbbbbbbbb.jsonl"
    alias.parent.mkdir(parents=True)
    os.link(original, alias)

    with pytest.raises(search.SearchError, match="conflicting session IDs"):
        search._store_sessions(
            "claude", source_roots=(first_root, second_root),
        )


def test_claude_signature_scan_stops_at_its_fifty_line_bound(tmp_path):
    path = tmp_path / "large.jsonl"
    path.write_text(
        "{}\n" * 50 + json.dumps({"sessionId": "late", "parentUuid": None}) + "\n",
        encoding="utf-8",
    )
    assert search._is_claude_transcript(path) is False


def test_claude_signature_scan_skips_bad_and_non_mapping_signature_records(tmp_path):
    path = tmp_path / "invalid.jsonl"
    path.write_text(
        '{"sessionId":"broken","parentUuid":,}\n'
        '["sessionId","parentUuid"]\n',
        encoding="utf-8",
    )
    assert search._is_claude_transcript(path) is False


def test_claude_discovery_falls_back_to_filename_when_session_id_is_not_text(
    tmp_path,
):
    root = tmp_path / "projects"
    path = _write_jsonl(
        root / "project" / "filename-id.jsonl",
        {"sessionId": None, "parentUuid": None},
    )

    found = search._claude_sessions(root)

    assert [(row.session_id, row.source) for row in found] == [
        ("filename-id", str(path.resolve())),
    ]


def test_claude_transcript_read_error_is_indeterminate(tmp_path, monkeypatch):
    root = tmp_path / "projects"
    path = _write_jsonl(
        root / "project" / "session.jsonl",
        {"sessionId": "session", "parentUuid": None},
    )
    original_open = Path.open

    def fail_open(candidate, *args, **kwargs):
        if candidate == path:
            raise PermissionError("forced Claude read failure")
        return original_open(candidate, *args, **kwargs)

    monkeypatch.setattr(Path, "open", fail_open)
    with pytest.raises(search.SearchError, match="could not inspect Claude Code session"):
        search._is_claude_transcript(path)


def test_claude_transcript_path_resolution_error_is_indeterminate(tmp_path, monkeypatch):
    root = tmp_path / "projects"
    path = _write_jsonl(
        root / "project" / "session.jsonl",
        {"sessionId": "session", "parentUuid": None},
    )
    original_resolve = Path.resolve

    def fail_resolve(candidate, *args, **kwargs):
        if candidate == path:
            raise PermissionError("forced Claude resolve failure")
        return original_resolve(candidate, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", fail_resolve)
    with pytest.raises(search.SearchError, match="could not resolve Claude Code session"):
        search._claude_sessions(root)


def test_store_sessions_uses_the_configured_claude_projects_root(tmp_path, monkeypatch):
    root = tmp_path / "claude-projects"
    path = _write_jsonl(
        root / "project" / "claude-id.jsonl",
        {"sessionId": "claude-id", "parentUuid": None},
    )
    monkeypatch.setattr(search, "_claude_projects_root", lambda: root)
    assert search._store_sessions("claude") == [
        search._DiscoveredSession("claude", "claude-id", str(path)),
    ]


def test_claude_search_deduplicates_transcript_aliases_across_source_roots(tmp_path):
    first_root = tmp_path / "first-projects"
    second_root = tmp_path / "second-projects"
    original = _write_jsonl(
        first_root / "project" / "first-name.jsonl",
        {"sessionId": "session", "parentUuid": None, "content": "needle"},
    )
    alias = second_root / "other-project" / "different-name.jsonl"
    alias.parent.mkdir(parents=True)
    alias.symlink_to(original)

    found = search._store_sessions(
        "claude", source_roots=(second_root, first_root),
    )
    assert len(found) == 1
    assert found[0].source == str(original)


def test_codex_discovery_scans_each_existing_root_once_and_requires_metadata(tmp_path):
    root = tmp_path / "codex-sessions"
    supported = _write_jsonl(
        root / "2026" / "rollout-supported.jsonl",
        {"type": "session_meta", "payload": {
            "id": "session-id", "session_id": "thread-id", "cli_version": "1",
        }},
        {"type": "event_msg", "payload": {"message": "query needle"}},
    )
    unsupported = _write_jsonl(
        root / "rollout-unsupported.jsonl",
        {"type": "session_meta", "payload": {"session_id": "legacy"}},
    )
    _write_jsonl(root / "not-a-rollout.jsonl", {"type": "session_meta"})

    found = search._codex_sessions((root, root, tmp_path / "missing"))
    assert found == [search._DiscoveredSession("codex", "session-id", str(supported))]
    assert search._codex_metadata(unsupported) is None


def test_store_sessions_dispatches_to_successful_codex_discovery(tmp_path, monkeypatch):
    root = tmp_path / "codex-sessions"
    path = _write_jsonl(
        root / "rollout-session.jsonl",
        {"type": "session_meta", "payload": {
            "id": "session-id", "session_id": "thread-id", "cli_version": "1",
        }},
    )
    monkeypatch.setattr(search, "_codex_sessions_roots", lambda: [root])
    assert search._store_sessions("codex") == [
        search._DiscoveredSession("codex", "session-id", str(path)),
    ]


def test_codex_metadata_uses_first_session_payload_but_detects_later_support(tmp_path):
    path = _write_jsonl(
        tmp_path / "rollout-first.jsonl",
        {"type": "session_meta", "payload": {"session_id": "thread"}},
        {"type": "session_meta", "payload": {"cli_version": "1"}},
    )
    assert search._codex_metadata(path) == {"session_id": "thread"}


def test_codex_metadata_handles_bad_records_and_refuses_read_errors(tmp_path, monkeypatch):
    path = tmp_path / "rollout.jsonl"
    path.write_text(
        "not json\n[]\n{\"type\":\"other\"}\n"
        "{\"type\":\"session_meta\",\"payload\":}\n"
        "{\"type\":\"session_meta\",\"payload\":[]}\n",
        encoding="utf-8",
    )
    assert search._codex_metadata(path) is None
    original_open = Path.open

    def fail_open(candidate, *args, **kwargs):
        if candidate == path:
            raise PermissionError("forced Codex read failure")
        return original_open(candidate, *args, **kwargs)

    monkeypatch.setattr(Path, "open", fail_open)
    with pytest.raises(search.SearchError, match="could not inspect Codex session"):
        search._codex_metadata(path)


def test_codex_metadata_skips_json_records_that_are_not_session_metadata(tmp_path):
    path = _write_jsonl(
        tmp_path / "rollout.jsonl",
        ["session_meta"],
        {"type": "other", "session_meta": True},
        {"type": "session_meta", "payload": {
            "session_id": "thread", "cli_version": "1",
        }},
    )
    assert search._codex_metadata(path) == {
        "session_id": "thread", "cli_version": "1",
    }


def test_codex_metadata_stops_at_the_adapter_scan_limit(tmp_path):
    path = tmp_path / "rollout.jsonl"
    path.write_text(
        "{}\n" * search.codex._SNIFF_SCAN_LINES
        + json.dumps({"type": "session_meta", "payload": {
            "session_id": "too-late", "cli_version": "1",
        }})
        + "\n",
        encoding="utf-8",
    )
    assert search._codex_metadata(path) is None


def test_store_sessions_translates_codex_root_discovery_errors(monkeypatch):
    def fail_roots():
        raise LocateError("cannot enumerate Codex profiles")

    monkeypatch.setattr(search, "_codex_sessions_roots", fail_roots)
    with pytest.raises(search.SearchError, match="cannot enumerate Codex profiles"):
        search._store_sessions("codex")


def test_opencode_discovery_skips_missing_and_duplicate_store_paths(tmp_path, monkeypatch):
    database = tmp_path / "opencode.db"
    database.touch()
    alias = tmp_path / "alias.db"
    alias.symlink_to(database)
    missing = tmp_path / "missing.db"
    monkeypatch.setattr(
        search, "_opencode_db_candidates", lambda: [missing, database, alias],
    )
    calls = []

    def list_agents(store):
        calls.append(store)
        return [SimpleNamespace(id="session-1", path=str(store), last_ts="2026-01-01")]

    monkeypatch.setattr(search.opencode, "list_agents", list_agents)
    assert search._store_sessions("opencode") == [
        search._DiscoveredSession("opencode", "session-1", str(database), "2026-01-01"),
    ]
    assert calls == [database.resolve()]


def test_opencode_store_resolution_and_listing_errors_are_reported(tmp_path, monkeypatch):
    bad = tmp_path / "loop.db"
    bad.symlink_to(bad)
    monkeypatch.setattr(search, "_opencode_db_candidates", lambda: [bad])
    with pytest.raises(search.SearchError, match="indeterminate.*loop.db"):
        search._store_sessions("opencode")

    existing = tmp_path / "existing.db"
    existing.touch()
    monkeypatch.setattr(search, "_opencode_db_candidates", lambda: [existing])

    def fail_list(_store):
        raise sqlite3.OperationalError("forced OpenCode query failure")

    monkeypatch.setattr(search.opencode, "list_agents", fail_list)
    with pytest.raises(search.SearchError, match="forced OpenCode query failure"):
        search._store_sessions("opencode")


def test_opencode_store_resolution_oserror_is_indeterminate(tmp_path, monkeypatch):
    database = _opencode_db(tmp_path / "opencode.db")
    original_resolve = Path.resolve

    def fail_resolve(path, *args, **kwargs):
        if path == database:
            raise PermissionError("forced resolve failure")
        return original_resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", fail_resolve)
    with pytest.raises(search.SearchError, match="indeterminate.*forced resolve failure"):
        search._store_sessions("opencode", source_roots=(database,))


def test_opencode_explicit_alias_stores_are_scanned_once_with_progress(
    tmp_path, monkeypatch,
):
    database = _opencode_db(tmp_path / "opencode.db")
    alias = tmp_path / "alias.db"
    alias.symlink_to(database)
    monkeypatch.setattr(search, "_opencode_stores", lambda _roots: (database, alias))
    calls = []
    monkeypatch.setattr(
        search.opencode,
        "list_agents",
        lambda store: calls.append(store) or [],
    )
    progress = []
    assert search._store_sessions(
        "opencode", source_roots=(database,), progress=lambda *event: progress.append(event),
    ) == []
    assert calls == [database.resolve()]
    assert progress and "Discovering OpenCode sessions" in progress[0][0]


def test_opencode_root_deduplication_keeps_a_compatible_database_alias(
    tmp_path, monkeypatch,
):
    database = _opencode_db(tmp_path / "store.sqlite")
    compatible_alias = tmp_path / "store.db"
    compatible_alias.symlink_to(database)
    calls = []
    monkeypatch.setattr(
        search.opencode,
        "list_agents",
        lambda store: calls.append(store) or [],
    )

    assert search.search_sessions(
        "needle",
        client="opencode",
        source_roots=(database, compatible_alias),
    ) == []
    assert calls == [database.resolve()]


def test_text_collection_skips_metadata_and_counts_unicode_words():
    record = {
        "role": "assistant",
        "Content": ["CLI-Extended cli", {"text": "Straße"}],
        "nested": {"type": "hidden", "answer": "Gate/backlog"},
        "ordinal": 99,
        "score": 1,
    }
    assert list(search._text_values(record)) == [
        "CLI-Extended cli", "Straße", "Gate/backlog",
    ]
    counts = Counter()
    search._add_text(counts, record)
    assert counts == Counter({
        "cli": 2, "extended": 1, "strasse": 1, "gate": 1, "backlog": 1,
    })


def test_targeted_text_counts_only_requested_ascii_terms_and_keeps_prefixes():
    value = {"content": "qcow QCOW2 _qcow qcow_name"}
    exact = Counter()
    prefix = Counter()
    search._add_text(exact, value, ("qcow", "qc"), "exact")
    search._add_text(prefix, value, ("qcow", "qc"), "prefix")

    assert exact == Counter(qcow=3)
    assert prefix == Counter(qcow=4, qc=4)

    overlapping = Counter()
    search._add_text(overlapping, {"content": "qc"}, ("qcow", "qc"), "prefix")
    assert overlapping == Counter(qc=1)

    nonmatching_unicode = Counter()
    search._add_text(
        nonmatching_unicode, {"content": "ordinary café"}, ("qcow",), "exact",
    )
    assert nonmatching_unicode == Counter()


def test_targeted_text_counts_saturate_each_query_term_independently():
    counts = Counter()
    search._add_text(
        counts,
        {"content": "qcow qcow qc qc qc qcow"},
        ("qcow", "qc"),
        "prefix",
        term_frequency_cap=3,
    )

    assert counts == Counter(qcow=3, qc=3)


def test_targeted_text_counts_do_not_revisit_ascii_text_without_matches():
    counts = Counter()
    search._add_text(counts, {"content": "ordinary words"}, ("qcow",))
    assert counts == Counter()


def test_targeted_ascii_count_stops_if_active_query_pattern_disappears(monkeypatch):
    original_pattern = search._query_text_pattern
    calls = 0

    def pattern_once(terms, term_match):
        nonlocal calls
        calls += 1
        if calls == 2:
            return None
        return original_pattern(terms, term_match)

    monkeypatch.setattr(search, "_query_text_pattern", pattern_once)
    counts = Counter()
    search._add_text(counts, {"content": "qcow"}, ("qcow",))
    assert counts == Counter()


def test_default_targeted_text_frequency_cap_is_64():
    counts = Counter()
    search._add_text(counts, {"content": "qcow " * 100}, ("qcow",))

    assert counts == Counter(qcow=64)


def test_unicode_targeted_count_stops_after_each_term_reaches_its_cap():
    counts = Counter()
    search._add_text(
        counts,
        {"content": ["qcow cloud café", "ordinary café"]},
        ("qcow", "cloud"),
        term_frequency_cap=1,
    )
    assert counts == Counter(qcow=1, cloud=1)


def test_search_worker_count_stays_inline_without_importable_main(
    tmp_path, monkeypatch,
):
    user_script = tmp_path / "nyxloom-harness"
    user_script.write_text("from nyxloom import search_sessions\n", encoding="utf-8")
    monkeypatch.setitem(
        search.sys.modules,
        "__main__",
        SimpleNamespace(__file__=str(user_script)),
    )
    monkeypatch.setattr(search.sys, "argv", [str(user_script)])

    assert search._search_process_worker_count() == 1


def test_search_worker_count_uses_bounded_workers_for_guarded_cli(
    tmp_path, monkeypatch,
):
    scripts_directory = tmp_path / "scripts"
    scripts_directory.mkdir()
    cli_entrypoint = scripts_directory / "nyxloom-harness"
    monkeypatch.setattr(
        search.sysconfig,
        "get_path",
        lambda key: str(scripts_directory) if key == "scripts" else None,
    )
    monkeypatch.setitem(
        search.sys.modules,
        "__main__",
        SimpleNamespace(__file__=str(cli_entrypoint)),
    )
    monkeypatch.setattr(search.os, "process_cpu_count", lambda: 100, raising=False)
    monkeypatch.setattr(search.os, "cpu_count", lambda: 100)

    cli_entrypoint.write_text(
        '"""Installed harness command."""\n'
        "import sys\n"
        "from nyxloom.cli_harness import main\n"
        "if __name__ == '__main__':\n"
        "    sys.exit(main())\n",
        encoding="utf-8",
    )
    assert search._search_process_worker_count() == search._RIPGREP_MAX_WORKERS


def test_search_worker_count_uses_bounded_workers_for_module_entrypoint(
    monkeypatch,
):
    module_entrypoint = Path(search.__file__).with_name("cli_harness.py")
    monkeypatch.setitem(
        search.sys.modules,
        "__main__",
        SimpleNamespace(__file__=str(module_entrypoint)),
    )
    monkeypatch.setattr(search.os, "process_cpu_count", lambda: 100, raising=False)
    monkeypatch.setattr(search.os, "cpu_count", lambda: 100)

    assert search._search_process_worker_count() == search._RIPGREP_MAX_WORKERS


@pytest.mark.parametrize(
    "source",
    (
        (
            "import os\nfrom nyxloom.cli_harness import main\n"
            "if __name__ == '__main__': main()\n"
        ),
        (
            "import sys\nfrom other import main\n"
            "if __name__ == '__main__': main()\n"
        ),
        (
            "import sys\nfrom nyxloom.cli_harness import main\n"
            "if __name__ != '__main__': main()\n"
        ),
    ),
)
def test_guarded_console_entrypoint_rejects_unsafe_top_level_shapes(
    tmp_path, source,
):
    entrypoint = tmp_path / "nyxloom-harness"
    entrypoint.write_text(source, encoding="utf-8")
    assert not search._is_guarded_harness_console_entrypoint(entrypoint)


def test_guarded_console_entrypoint_rejects_unreadable_or_invalid_source(tmp_path):
    missing = tmp_path / "missing-entrypoint"
    invalid = tmp_path / "invalid-entrypoint"
    invalid.write_text("if __name__ == :\n", encoding="utf-8")

    assert not search._is_guarded_harness_console_entrypoint(missing)
    assert not search._is_guarded_harness_console_entrypoint(invalid)


def test_search_worker_count_rejects_unguarded_canonical_console_script(
    tmp_path, monkeypatch,
):
    scripts_directory = tmp_path / "scripts"
    scripts_directory.mkdir()
    cli_entrypoint = scripts_directory / "nyxloom-harness"
    cli_entrypoint.write_text(
        "from nyxloom.cli_harness import main\n"
        "main()\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        search.sysconfig,
        "get_path",
        lambda key: str(scripts_directory) if key == "scripts" else None,
    )
    monkeypatch.setitem(
        search.sys.modules,
        "__main__",
        SimpleNamespace(__file__=str(cli_entrypoint)),
    )

    assert search._search_process_worker_count() == 1


def test_jsonl_content_skips_bad_lines_and_keeps_latest_string_timestamp(tmp_path):
    path = tmp_path / "transcript.jsonl"
    path.write_text(
        "not json\n"
        + json.dumps({"content": "cli gate", "timestamp": "2026-01-01T00:00:00Z"})
        + "\n"
        + json.dumps({"content": "backlog", "timestamp": 42})
        + "\n"
        + json.dumps({"content": "cli", "timestamp": "2026-01-02T00:00:00Z"})
        + "\n",
        encoding="utf-8",
    )
    counts, latest = search._jsonl_content(path)
    assert counts == Counter({"cli": 2, "gate": 1, "backlog": 1})
    assert latest == "2026-01-02T00:00:00Z"


def test_jsonl_targeted_search_keeps_exact_root_timestamp_without_parsing_other_text(tmp_path):
    path = tmp_path / "transcript.jsonl"
    path.write_text(
        json.dumps({"timestamp": "2026-01-01", "content": "qcow2"}) + "\n"
        + json.dumps({"nested": {"timestamp": "wrong"}, "content": "ordinary words"}) + "\n"
        + '{"timestamp":"malformed","content":}\n'
        + json.dumps({"content": "debian qcow"}) + "\n",
        encoding="utf-8",
    )
    counts, latest = search._jsonl_content(path, ("qcow",))
    assert counts == Counter({"qcow": 1})
    assert latest == "2026-01-01"


def test_targeted_raw_filter_preserves_unicode_and_json_escaped_matches():
    terms = ("strasse", "qcow")
    assert search._raw_may_match("Straße appears here", terms, "exact")
    assert search._raw_may_match(r'{"content":"\u0071cow"}', terms, "exact")
    assert not search._raw_may_match("qcow2 only", ("qcow",), "exact")
    assert search._raw_may_match("qcow2 only", ("qcow",), "prefix")
    assert search._raw_bytes_may_match("Straße".encode(), ("strasse",), "exact")
    assert search._raw_bytes_may_match(br'{"content":"\u0071cow"}', ("qcow",), "exact")
    assert not search._raw_bytes_may_match("ordinary café".encode(), ("qcow",), "exact")

    pattern = search._ripgrep_pattern(("strasse", "qcow"), "exact")
    assert pattern is not None and "ß" in pattern and r"\u0071" in pattern


def test_streaming_record_filter_skips_exact_misses_and_keeps_unicode_matches(tmp_path):
    path = tmp_path / "candidate.jsonl"
    path.write_text('{"content":"qcow2"}\n', encoding="utf-8")
    assert search._filtered_jsonl_counts(path, ("qcow",), "exact") == Counter()
    assert search._filtered_jsonl_counts(path, ("qcow",), "prefix") == Counter(qcow=1)

    path.write_text(r'{"content":"\u0071cow"}' + "\n", encoding="utf-8")
    assert search._filtered_jsonl_counts(path, ("qcow",), "exact") == Counter(qcow=1)
    assert search._jsonl_content(path, ("qcow",))[0] == Counter(qcow=1)
    path.write_text('{"content":"Straße"}\n', encoding="utf-8")
    assert search._filtered_jsonl_counts(path, ("strasse",), "exact") == Counter(strasse=1)
    assert search._jsonl_content(path, ("strasse",))[0] == Counter(strasse=1)


def test_streaming_record_filter_counts_repeated_query_words_in_a_candidate_record(tmp_path):
    path = tmp_path / "candidate.jsonl"
    path.write_text('{"content":"qcow qcow qcow2"}\n', encoding="utf-8")
    assert search._filtered_jsonl_counts(path, ("qcow",), "prefix") == Counter(qcow=3)


def test_streaming_record_filter_keeps_unsaturated_terms_after_one_term_caps(
    tmp_path, monkeypatch,
):
    monkeypatch.setattr(search, "_TERM_FREQUENCY_CAP", 3)
    path = tmp_path / "saturated-term.jsonl"
    path.write_text(
        ''.join('{"content":"qcow"}\n' for _ in range(8))
        + ''.join('{"content":"cloud"}\n' for _ in range(3)),
        encoding="utf-8",
    )

    assert search._filtered_jsonl_counts(
        path, ("qcow", "cloud"), "exact",
    ) == Counter(qcow=3, cloud=3)


def test_streaming_record_filter_keeps_records_whose_query_crosses_a_chunk(
    tmp_path, monkeypatch,
):
    monkeypatch.setattr(search, "_JSONL_CHUNK_SIZE", 24)
    path = tmp_path / "chunked.jsonl"
    prefix = b'{"content":"'
    padding = b" " * (search._JSONL_CHUNK_SIZE - len(prefix) - 2)
    path.write_bytes(prefix + padding + b"qcow2" + b'"}\n')

    assert search._filtered_jsonl_counts(path, ("qcow",), "prefix") == Counter(qcow=1)


def test_streaming_record_filter_handles_a_large_record_across_many_chunks(
    tmp_path, monkeypatch,
):
    monkeypatch.setattr(search, "_JSONL_CHUNK_SIZE", 64)
    path = tmp_path / "large-record.jsonl"
    path.write_bytes(b'{"content":"' + b"x" * (256 * 1024) + b" qcow" + b'"}\n')
    assert search._filtered_jsonl_counts(path, ("qcow",), "exact") == Counter(qcow=1)


def test_ripgrep_candidate_stream_parses_records_and_preserves_exact_path(
    tmp_path, monkeypatch,
):
    path = tmp_path / "rollout-search.jsonl"
    output = (
        os.fsencode(path)
        + b"\0"
        + json.dumps({"content": "qcow qcow2"}).encode("utf-8")
        + b"\n"
    )
    calls = []

    class FakeProcess:
        stdout = io.BytesIO(output)

        def poll(self):
            return 0

        def wait(self):
            return 0

    def fake_popen(args, **kwargs):
        calls.append((args, kwargs))
        return FakeProcess()

    monkeypatch.setattr(search.shutil, "which", lambda _name: "/usr/bin/rg")
    monkeypatch.setattr(search.subprocess, "Popen", fake_popen)

    result = search._ripgrep_jsonl_counts(
        (path,), ("qcow",), "prefix", client="codex",
    )

    assert result == {str(path): Counter(qcow=2)}
    assert "--null" in calls[0][0]
    assert calls[0][0][-1] == str(path)


def test_ripgrep_candidate_stream_uses_bounded_process_workers(
    tmp_path, monkeypatch,
):
    path = tmp_path / "parallel-search.jsonl"
    records = ["qcow"] * 8 + ["cloud"] * 3 + ["qcow"]
    output = b"".join(
        os.fsencode(path) + b"\0"
        + json.dumps({"content": word}).encode("utf-8") + b"\n"
        for word in records
    )

    class FakeProcess:
        def __init__(self):
            self.stdout = io.BytesIO(output)

        def poll(self):
            return 0

        def wait(self):
            return 0

        def terminate(self):
            pytest.fail("a completed ripgrep process should not be terminated")

    monkeypatch.setattr(search.shutil, "which", lambda _name: "/usr/bin/rg")
    monkeypatch.setattr(
        search.subprocess, "Popen", lambda *_args, **_kwargs: FakeProcess(),
    )
    monkeypatch.setattr(search, "_RIPGREP_PARALLEL_AFTER_CANDIDATES", 1)
    monkeypatch.setattr(search, "_RIPGREP_MAX_WORKERS", 2)
    monkeypatch.setattr(search, "_RIPGREP_BATCH_RECORD_LIMIT", 2)
    monkeypatch.setattr(search, "_RIPGREP_MAX_PENDING_PER_WORKER", 1)
    monkeypatch.setattr(search, "_search_process_worker_count", lambda: 2)
    monkeypatch.setattr(search, "_TERM_FREQUENCY_CAP", 3)

    result = search._ripgrep_jsonl_counts(
        (path,), ("qcow", "cloud"), "exact", client="codex",
    )

    assert result == {str(path): Counter(qcow=3, cloud=3)}


def test_count_ripgrep_batch_skips_capped_terms_and_raw_record_misses():
    source = "/sessions/one.jsonl"
    raw_record = b'{"content":"qcow only"}'

    assert search._count_ripgrep_batch(
        ((source, raw_record),),
        {source: Counter(qcow=2, cloud=2)},
        ("qcow", "cloud"),
        "exact",
        2,
    ) == {}
    assert search._count_ripgrep_batch(
        ((source, raw_record),),
        {source: Counter(qcow=2)},
        ("qcow", "cloud"),
        "exact",
        2,
    ) == {}


def test_submit_ripgrep_batch_ignores_empty_batch():
    class NoSubmitExecutor:
        def submit(self, *_args):
            pytest.fail("empty batches must not be submitted")

    pending = set()
    search._submit_ripgrep_batch(
        NoSubmitExecutor(), pending, [], {}, ("qcow",), "exact", 64, 2,
    )
    assert pending == set()


def test_ripgrep_reuses_one_process_pool_across_path_batches(
    tmp_path, monkeypatch,
):
    first = tmp_path / "first.jsonl"
    second = tmp_path / "second.jsonl"
    records_by_path = {
        str(first): ("qcow",),
        str(second): ("cloud",),
    }

    class FakeProcess:
        def __init__(self, path):
            self.stdout = io.BytesIO(b"".join(
                os.fsencode(path) + b"\0"
                + json.dumps({"content": word}).encode("utf-8") + b"\n"
                for word in records_by_path[str(path)]
            ))

        def poll(self):
            return 0

        def wait(self):
            return 0

        def terminate(self):
            pytest.fail("a completed ripgrep process should not be terminated")

    def fake_popen(args, **_kwargs):
        return FakeProcess(Path(args[-1]))

    batches = ((first,), (second,))
    monkeypatch.setattr(search.shutil, "which", lambda _name: "/usr/bin/rg")
    monkeypatch.setattr(search.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(search, "_ripgrep_path_batches", lambda _paths: iter(batches))
    monkeypatch.setattr(search, "_RIPGREP_PARALLEL_AFTER_CANDIDATES", 1)
    monkeypatch.setattr(search, "_RIPGREP_BATCH_RECORD_LIMIT", 1)
    monkeypatch.setattr(search, "_RIPGREP_MAX_PENDING_PER_WORKER", 1)
    monkeypatch.setattr(search, "_search_process_worker_count", lambda: 2)
    original_executor = search.ProcessPoolExecutor
    executor_creations = []

    def record_executor_creation(**kwargs):
        executor_creations.append(kwargs["max_workers"])
        return original_executor(**kwargs)

    monkeypatch.setattr(search, "ProcessPoolExecutor", record_executor_creation)

    result = search._ripgrep_jsonl_counts(
        (first, second), ("qcow", "cloud"), "exact", client="codex",
    )

    assert result == {
        str(first): Counter(qcow=1),
        str(second): Counter(cloud=1),
    }
    assert executor_creations == [2]


def test_ripgrep_flushes_byte_limited_batches_and_counts_oversized_records_inline(
    tmp_path, monkeypatch,
):
    path = tmp_path / "bounded-batches.jsonl"
    oversized = {"content": "qcow", "padding": "x" * 512}
    payloads = (
        oversized,
        {"content": "qcow"},
        {"content": "cloud"},
        oversized,
    )
    encoded = tuple(json.dumps(payload).encode("utf-8") for payload in payloads)
    path_bytes = os.fsencode(path)
    output = b"".join(
        path_bytes + b"\0" + record + b"\n"
        for record in encoded
    )

    class FakeProcess:
        def __init__(self):
            self.stdout = io.BytesIO(output)

        def poll(self):
            return 0

        def wait(self):
            return 0

        def terminate(self):
            pytest.fail("a completed ripgrep process should not be terminated")

    submitted_batches = []

    class RecordingExecutor:
        def __init__(self, *, max_workers):
            assert max_workers == 2

        def submit(self, function, records, *args):
            submitted_batches.append(records)
            future = Future()
            future.set_result(function(records, *args))
            return future

        def shutdown(self, *, wait, cancel_futures):
            assert wait is True
            assert cancel_futures is False

    small_sizes = tuple(len(path_bytes) + len(record) for record in encoded[1:3])
    monkeypatch.setattr(search.shutil, "which", lambda _name: "/usr/bin/rg")
    monkeypatch.setattr(
        search.subprocess, "Popen", lambda *_args, **_kwargs: FakeProcess(),
    )
    monkeypatch.setattr(search, "_RIPGREP_PARALLEL_AFTER_CANDIDATES", 1)
    monkeypatch.setattr(search, "_RIPGREP_MAX_WORKERS", 2)
    monkeypatch.setattr(search, "_RIPGREP_MAX_PENDING_PER_WORKER", 1)
    monkeypatch.setattr(search, "_RIPGREP_BATCH_RECORD_LIMIT", 128)
    monkeypatch.setattr(search, "_RIPGREP_BATCH_BYTE_LIMIT", max(small_sizes) + 1)
    monkeypatch.setattr(search, "_search_process_worker_count", lambda: 2)
    monkeypatch.setattr(search, "ProcessPoolExecutor", RecordingExecutor)

    result = search._ripgrep_jsonl_counts(
        (path,), ("qcow", "cloud"), "exact", client="codex",
    )

    assert small_sizes[0] < search._RIPGREP_BATCH_BYTE_LIMIT
    assert small_sizes[1] < search._RIPGREP_BATCH_BYTE_LIMIT
    assert sum(small_sizes) > search._RIPGREP_BATCH_BYTE_LIMIT
    assert len(path_bytes) + len(encoded[0]) > search._RIPGREP_BATCH_BYTE_LIMIT
    assert len(submitted_batches) == 2
    assert all(len(batch) == 1 for batch in submitted_batches)
    assert all(
        len(os.fsencode(source)) + len(record) <= search._RIPGREP_BATCH_BYTE_LIMIT
        for batch in submitted_batches
        for source, record in batch
    )
    assert all(
        record != encoded[0]
        for batch in submitted_batches
        for _source, record in batch
    )
    assert result == {str(path): Counter(qcow=3, cloud=1)}


def test_documents_uses_ripgrep_counts_for_supported_transcripts(tmp_path, monkeypatch):
    path = _write_jsonl(
        tmp_path / "rollout-search.jsonl",
        {"content": "cli cli", "timestamp": "2026-01-01"},
    )
    session = search._DiscoveredSession("codex", "session", str(path))
    monkeypatch.setattr(search, "_store_sessions", lambda *_args, **_kwargs: [session])
    calls = []

    def counts(paths, terms, term_match, **kwargs):
        calls.append((paths, terms, term_match, kwargs))
        return {str(path): Counter(cli=2)}

    monkeypatch.setattr(search, "_ripgrep_jsonl_counts", counts)
    monkeypatch.setattr(
        search, "_jsonl_last_activity", lambda *_args, **_kwargs: "2026-01-01",
    )

    documents = search._documents("codex", ("cli",))

    assert len(calls) == 1
    assert calls[0][0] == (path,)
    assert documents == [
        search._Document("codex", "session", str(path), "2026-01-01", Counter(cli=2)),
    ]


def test_targeted_jsonl_search_does_not_decode_noncandidate_records(tmp_path, monkeypatch):
    path = _write_jsonl(
        tmp_path / "transcript.jsonl",
        *({"content": f"ordinary transcript row {index}"} for index in range(20)),
    )
    calls = []
    original_loads = search.json.loads

    def count_decodes(value, *args, **kwargs):
        calls.append(value)
        return original_loads(value, *args, **kwargs)

    monkeypatch.setattr(search.json, "loads", count_decodes)
    counts, activity = search._jsonl_content(path, ("needle",))
    assert counts == Counter()
    assert activity is None
    assert calls == []


def test_jsonl_content_read_error_is_not_treated_as_no_matches(tmp_path):
    path = tmp_path / "directory.jsonl"
    path.mkdir()
    with pytest.raises(search.SearchError, match="could not read session transcript"):
        search._jsonl_content(path)


def test_opencode_term_counts_reads_messages_and_parts_and_skips_malformed_json(tmp_path):
    database = _opencode_db(
        tmp_path / "opencode.db",
        messages=(
            ("m1", "s1", json.dumps({"role": "assistant", "content": "cli cli"})),
            ("m2", "s1", "{"),
            ("m3", "s2", None),
        ),
        parts=(("p1", "m1", "s1", json.dumps({"text": "gate backlog"})),),
    )
    assert search._opencode_term_counts(database) == {
        "s1": Counter({"cli": 2, "gate": 1, "backlog": 1}),
    }


def test_opencode_targeted_counts_cap_two_terms_and_omit_empty_sessions(
    tmp_path, monkeypatch,
):
    database = _opencode_db(
        tmp_path / "opencode-capped.db",
        messages=(
            ("m1", "matched", json.dumps({"content": "qcow cloud"})),
            ("m2", "matched", json.dumps({"content": "qcow cloud"})),
            ("m3", "metadata-only", json.dumps({"timestamp": "qcow"})),
        ),
    )
    monkeypatch.setattr(search, "_TERM_FREQUENCY_CAP", 1)

    assert search._opencode_term_counts(
        database, ("qcow", "cloud"),
    ) == {"matched": Counter(qcow=1, cloud=1)}


def test_opencode_term_counts_rejects_non_store_and_database_errors(tmp_path):
    with pytest.raises(search.SearchError, match="not an OpenCode session store"):
        search._opencode_term_counts(tmp_path / "not-a-store")

    malformed = tmp_path / "malformed.db"
    malformed.touch()
    with pytest.raises(search.SearchError, match="could not search OpenCode store"):
        search._opencode_term_counts(malformed)


def test_opencode_connect_error_is_wrapped_before_a_connection_exists(tmp_path, monkeypatch):
    database = _opencode_db(tmp_path / "connect-error.db")

    def fail_connect(*_args, **_kwargs):
        raise sqlite3.OperationalError("forced connect failure")

    monkeypatch.setattr(search.sqlite3, "connect", fail_connect)
    with pytest.raises(search.SearchError, match="forced connect failure"):
        search._opencode_term_counts(database)


def test_documents_deduplicates_file_sources_and_uses_transcript_timestamp(tmp_path, monkeypatch):
    path = _write_jsonl(
        tmp_path / "claude.jsonl",
        {"sessionId": "s", "parentUuid": None, "content": "cli"},
        {"content": "gate", "timestamp": "2026-01-03"},
    )
    monkeypatch.setattr(search, "_store_sessions", lambda _client, **_kwargs: [
        search._DiscoveredSession("claude", "s", str(path), "stale"),
        search._DiscoveredSession("claude", "s", str(path), "duplicate"),
    ])
    docs = search._documents("claude")
    assert len(docs) == 1
    assert docs[0].term_counts == Counter({"cli": 1, "gate": 1})
    assert docs[0].last_activity == "2026-01-03"


def test_documents_reads_opencode_store_counts_once_per_store(tmp_path, monkeypatch):
    store = tmp_path / "opencode.db"
    sessions = [
        search._DiscoveredSession("opencode", "s1", str(store), "new"),
        search._DiscoveredSession("opencode", "s2", str(store), "old"),
        search._DiscoveredSession("opencode", "s1", str(store), "duplicate"),
    ]
    monkeypatch.setattr(search, "_store_sessions", lambda _client, **_kwargs: sessions)
    calls = []

    def counts(path, *_args, **_kwargs):
        calls.append(path)
        return {"s1": Counter({"cli": 1}), "s2": Counter({"gate": 1})}

    monkeypatch.setattr(search, "_opencode_term_counts", counts)
    docs = search._documents("opencode")
    assert [doc.session_id for doc in docs] == ["s1", "s2"]
    assert [doc.last_activity for doc in docs] == ["new", "old"]
    assert [doc.term_counts for doc in docs] == [Counter(cli=1), Counter(gate=1)]
    assert calls == [store]


def test_documents_with_no_sessions_does_not_open_any_opencode_database(monkeypatch):
    monkeypatch.setattr(search, "_store_sessions", lambda _client, **_kwargs: [])
    monkeypatch.setattr(
        search, "_opencode_term_counts",
        lambda _store, *_args, **_kwargs: pytest.fail("empty discovery must not read a store"),
    )
    assert search._documents("opencode") == []


def test_search_best_and_date_orders_use_match_count_relevance_and_activity(monkeypatch):
    documents = {
        "codex": [
            search._Document("codex", "many-repeats", "c1", "2026-01-01", Counter(cli=3)),
        ],
        "claude": [
            search._Document("claude", "two-terms", "a1", "2025-01-01", Counter(cli=1, gate=1)),
        ],
        "opencode": [
            search._Document("opencode", "rare-term", "o1", "2027-01-01", Counter(backlog=1)),
            search._Document("opencode", "no-match", "o2", None, Counter(other=1)),
        ],
    }
    seen = []

    def get_documents(client, *_args, **_kwargs):
        seen.append(client)
        return documents[client]

    monkeypatch.setattr(search, "_documents", get_documents)
    best = search.search_sessions("CLI gate backlog cli", sort_by="best")
    assert [row.session_id for row in best] == ["two-terms", "many-repeats", "rare-term"]
    assert best[0].matched_terms == ("cli", "gate")
    assert len({row.session_id for row in best}) == 3
    assert seen == ["codex", "claude", "opencode"]

    seen.clear()
    by_date = search.search_sessions("cli gate backlog", sort_by="date")
    assert [row.session_id for row in by_date] == ["rare-term", "many-repeats", "two-terms"]
    assert seen == ["codex", "claude", "opencode"]


def test_search_validates_client_sort_order_and_empty_query_before_discovery(monkeypatch):
    monkeypatch.setattr(
        search, "_documents", lambda _client, *_args, **_kwargs: pytest.fail("invalid input must not scan stores"),
    )
    with pytest.raises(search.SearchError, match="at least one word"):
        search.search_sessions("...", client="codex")
    with pytest.raises(search.SearchError, match="unknown client"):
        search.search_sessions("word", client="reasonix")
    with pytest.raises(search.SearchError, match="unknown sort order"):
        search.search_sessions("word", sort_by="random")
    with pytest.raises(search.SearchError, match="unknown word match mode"):
        search.search_sessions("word", word_match="xor")
    with pytest.raises(search.SearchError, match="unknown term match mode"):
        search.search_sessions("word", term_match="fuzzy")


def test_search_client_filter_and_no_matches(monkeypatch):
    calls = []

    def documents(client, *_args, **_kwargs):
        calls.append(client)
        if client == "codex":
            return [search._Document("codex", "session", "source", None, Counter(unrelated=1))]
        return []

    monkeypatch.setattr(search, "_documents", documents)
    assert search.search_sessions("needle", client="codex") == []
    assert calls == ["codex"]


def test_search_any_all_and_prefix_modes(monkeypatch):
    docs = [
        search._Document("codex", "prefix-only", "a", None, Counter(qcow=1)),
        search._Document("codex", "both", "b", None, Counter(debian=1, qcow=2)),
    ]
    monkeypatch.setattr(search, "_documents", lambda *_args, **_kwargs: docs)
    assert {row.session_id for row in search.search_sessions("debian qcow", client="codex")} == {
        "both", "prefix-only",
    }
    assert [row.session_id for row in search.search_sessions(
        "debian qcow", client="codex", word_match="all", term_match="prefix",
    )] == ["both"]
    assert [row.session_id for row in search.search_sessions(
        "debian qcow", client="codex", word_match="all",
    )] == ["both"]

    exact = Counter()
    prefix = Counter()
    search._add_text(exact, {"content": "qcow2"}, ("qcow",), "exact")
    search._add_text(prefix, {"content": "qcow2"}, ("qcow",), "prefix")
    assert exact == Counter()
    assert prefix == Counter(qcow=1)


def test_search_accepts_repeated_clients_and_deduplicates_source_root_aliases(
    tmp_path,
):
    codex_root = tmp_path / "codex" / "sessions"
    codex_path = _write_jsonl(
        codex_root / "rollout-codex.jsonl",
        {"type": "session_meta", "payload": {
            "id": "codex-id", "session_id": "codex-thread", "cli_version": "1",
        }},
        {"payload": {"text": "needle"}},
    )
    claude_root = tmp_path / "claude" / "projects"
    claude_path = _write_jsonl(
        claude_root / "project" / "claude-id.jsonl",
        {"sessionId": "claude-id", "parentUuid": None, "content": "needle"},
    )
    alias = tmp_path / "codex-alias"
    alias.symlink_to(codex_root, target_is_directory=True)
    progress = []
    results = search.search_sessions(
        "needle",
        client=("codex", "claude", "codex"),
        source_roots=(codex_root, alias, claude_root),
        progress=lambda message, current, total: progress.append(
            (message, current, total),
        ),
    )
    assert {(row.client, row.source) for row in results} == {
        ("codex", str(codex_path)), ("claude", str(claude_path)),
    }
    assert any("Discovering Codex sessions" in message for message, _, _ in progress)
    assert any("Reading codex transcript" in message for message, _, _ in progress)
    default_clients = search.search_sessions(
        "needle", source_roots=(codex_root,),
    )
    assert {(row.client, row.source) for row in default_clients} == {
        ("codex", str(codex_path)),
    }


def test_search_rejects_a_missing_explicit_source_root(tmp_path):
    with pytest.raises(search.SearchError, match="could not inspect explicit source root"):
        search.search_sessions(
            "needle", client="codex", source_roots=(tmp_path / "missing",),
        )


def test_search_rejects_source_roots_incompatible_with_selected_client(tmp_path):
    database = _opencode_db(tmp_path / "opencode.db")
    with pytest.raises(search.SearchError, match="codex requires.*directory"):
        search.search_sessions("needle", client="codex", source_roots=(database,))

    directory = tmp_path / "empty-store"
    directory.mkdir()
    with pytest.raises(search.SearchError, match="no OpenCode database"):
        search.search_sessions("needle", client="opencode", source_roots=(directory,))


@pytest.mark.parametrize(
    ("results", "expected_text"),
    [
        (
            [search.SearchResult("codex", "session-123", "/store/session.jsonl", "2026-01-02", ("cli", "gate"), 1.25)],
            "session-123",
        ),
        ([], "No matching sessions."),
    ],
)
def test_search_cli_routes_query_options_and_prints_only_result_metadata(
    results, expected_text, monkeypatch, capsys, tmp_path,
):
    calls = []

    def fake_search(query, **kwargs):
        calls.append((query, kwargs))
        kwargs["progress"]("Fixture progress", 1, 1)
        return results

    monkeypatch.setattr(search, "search_sessions", fake_search)
    status = harness_cli().run(
        argv=[
            "search", "cli-extended", "gate", "backlog",
            "--sort-by", "date", "--client", "codex", "--client", "claude",
            "--word-match", "all", "--term-match", "prefix",
            "--source-root", str(tmp_path),
        ],
        interactive_extra="nyxloom[interactive]",
    )
    captured = capsys.readouterr()
    output = captured.out
    assert status == 0
    assert calls[0][0] == "cli-extended gate backlog"
    assert calls[0][1]["client"] == ["codex", "claude"]
    assert calls[0][1]["sort_by"] == "date"
    assert calls[0][1]["word_match"] == "all"
    assert calls[0][1]["term_match"] == "prefix"
    assert calls[0][1]["source_roots"] == [str(tmp_path)]
    assert callable(calls[0][1]["progress"])
    assert "Fixture progress (1/1)" in captured.err
    assert expected_text in output
    assert "confidential transcript body" not in output


def test_cmd_search_without_a_progress_renderer_keeps_search_options(
    monkeypatch, capsys, tmp_path,
):
    from nyxloom.cli import cmd_search

    calls = []
    result = search.SearchResult("codex", "id", "/tmp/rollout.jsonl", None, ("qcow",), 1.0)
    monkeypatch.setattr(
        search,
        "search_sessions",
        lambda query, **kwargs: calls.append((query, kwargs)) or [result],
    )
    args = SimpleNamespace(
        words=["debian", "qcow"], client=["codex"], sort_by="best",
        source_root=[str(tmp_path)], word_match="all", term_match="prefix",
        runtime=None,
    )

    assert cmd_search(args) == 0
    assert calls == [(
        "debian qcow",
        {
            "client": ["codex"], "sort_by": "best", "source_roots": [str(tmp_path)],
            "word_match": "all", "term_match": "prefix",
        },
    )]
    assert "id" in capsys.readouterr().out


def test_cmd_search_finishes_progress_inside_its_context(monkeypatch):
    from nyxloom.cli import cmd_search

    events = []

    class Progress:
        active = False

        def __enter__(self):
            self.active = True
            return self

        def __exit__(self, *_args):
            self.active = False

        def update(self, message, **kwargs):
            events.append(("update", message, self.active))

        def finish(self, message):
            events.append(("finish", message, self.active))

    progress = Progress()
    monkeypatch.setattr(search, "search_sessions", lambda *_args, **_kwargs: [])
    args = SimpleNamespace(
        words=["qcow"], client=None, sort_by="best", source_root=None,
        word_match="any", term_match="exact",
        runtime=SimpleNamespace(progress=lambda: progress),
    )
    assert cmd_search(args) == 0
    assert events[-1] == ("finish", "Session search complete", True)


def test_explicit_root_validation_covers_empty_special_and_unreadable_paths(
    tmp_path, monkeypatch,
):
    fifo = tmp_path / "pipe"
    os.mkfifo(fifo)
    with pytest.raises(search.SearchError, match="not a regular file or directory"):
        search._source_roots((fifo,))
    with pytest.raises(search.SearchError, match="at least one --source-root"):
        search._source_roots(())

    regular = tmp_path / "ordinary.txt"
    regular.write_text("not a store", encoding="utf-8")
    assert search._directory_roots((regular,), "codex", required=False) == ()
    with pytest.raises(search.SearchError, match="codex requires at least one directory"):
        search._directory_roots((regular,), "codex", required=True)

    original_stat = Path.stat

    def fail_root_stat(path, *args, **kwargs):
        if path == regular:
            raise PermissionError("forced explicit-root failure")
        return original_stat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", fail_root_stat)
    with pytest.raises(search.SearchError, match="could not inspect explicit source root"):
        search._directory_roots((regular,), "codex", required=False)
    with pytest.raises(search.SearchError, match="could not inspect explicit source root"):
        search._opencode_stores((regular,))


def test_explicit_opencode_roots_accept_db_files_and_directories_only(tmp_path):
    database = tmp_path / "opencode.db"
    database.touch()
    unrelated_file = tmp_path / "notes.txt"
    unrelated_file.write_text("ignore", encoding="utf-8")
    directory = tmp_path / "store-dir"
    directory.mkdir()

    assert search._opencode_stores((database, unrelated_file, directory)) == (
        database, directory / "opencode.db",
    )
    assert search._store_sessions(
        "claude", source_roots=(unrelated_file,), require_compatible_roots=False,
    ) == []
    assert search._store_sessions(
        "codex", source_roots=(unrelated_file,), require_compatible_roots=False,
    ) == []

    fifo = tmp_path / "not-a-store-type"
    os.mkfifo(fifo)
    assert search._opencode_stores((fifo,)) == ()


def test_claude_default_root_type_missing_and_stat_errors(tmp_path, monkeypatch):
    regular = tmp_path / "not-a-directory"
    regular.write_text("x", encoding="utf-8")
    monkeypatch.setattr(search, "_claude_projects_root", lambda: regular)
    assert search._store_sessions("claude") == []

    missing = tmp_path / "missing"
    monkeypatch.setattr(search, "_claude_projects_root", lambda: missing)
    assert search._store_sessions("claude") == []

    original_stat = Path.stat

    def fail_stat(path, *args, **kwargs):
        if path == missing:
            raise PermissionError("forced Claude root failure")
        return original_stat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", fail_stat)
    with pytest.raises(search.SearchError, match="could not inspect Claude Code session root"):
        search._store_sessions("claude")


def test_claude_progress_reports_discovery_and_file_walk(tmp_path):
    root = tmp_path / "projects"
    _write_jsonl(
        root / "project" / "session.jsonl",
        {"sessionId": "session", "parentUuid": None, "content": "needle"},
    )
    progress = []
    assert search._store_sessions(
        "claude", source_roots=(root,), progress=lambda *event: progress.append(event),
    )
    assert any("Discovering Claude Code sessions" in event[0] for event in progress)
    assert any("Walking" in event[0] for event in progress)


def test_walk_progress_and_physical_directory_cycle_are_deduplicated(tmp_path, monkeypatch):
    root = tmp_path / "root"
    nested = root / "nested"
    nested.mkdir(parents=True)
    for index in range(501):
        (root / f"{index}.txt").touch()
    progress = []
    assert len(list(search._walk_files(root, progress=lambda *event: progress.append(event)))) == 501
    assert sum("Walking" in event[0] for event in progress) == 2

    normal_identity = search._path_identity

    def alias_root_and_nested(path):
        if path in (root, nested):
            return (101, 202)
        return normal_identity(path)

    monkeypatch.setattr(search, "_path_identity", alias_root_and_nested)
    assert len(list(search._walk_files(root))) == 501


def test_claude_transcript_aliases_deduplicate_by_canonical_path_without_inode(
    tmp_path, monkeypatch,
):
    root = tmp_path / "projects"
    original = _write_jsonl(
        root / "project" / "session.jsonl",
        {"sessionId": "session", "parentUuid": None},
    )
    alias = original.with_name("000-alias.jsonl")
    alias.symlink_to(original)
    monkeypatch.setattr(search, "_path_identity", lambda _path: None)
    found = search._claude_sessions(root)
    assert [(row.session_id, row.source) for row in found] == [
        ("session", str(original.resolve())),
    ]


def test_codex_search_skips_non_directory_roots_and_deduplicates_aliases(
    tmp_path, monkeypatch,
):
    regular = tmp_path / "regular"
    regular.touch()
    assert search._codex_sessions((regular,)) == []

    root = tmp_path / "sessions"
    transcript = _write_jsonl(
        root / "rollout-session.jsonl",
        {"type": "session_meta", "payload": {
            "id": "stable-id", "session_id": "thread", "cli_version": "1",
        }},
    )
    file_alias = root / "rollout-alias.jsonl"
    file_alias.symlink_to(transcript)
    root_alias = tmp_path / "sessions-alias"
    root_alias.symlink_to(root, target_is_directory=True)
    dedupe = search._dedupe_paths
    monkeypatch.setattr(search, "_dedupe_paths", lambda paths: tuple(paths))
    monkeypatch.setattr(search, "_codex_sessions_roots", lambda: (root, root_alias))
    found = search._codex_sessions((root, root_alias))
    assert len(found) == 1
    assert found[0].session_id == "stable-id"
    monkeypatch.setattr(search, "_dedupe_paths", dedupe)


def test_codex_discovery_root_stat_errors_and_missing_identity_fallback(
    tmp_path, monkeypatch,
):
    root = tmp_path / "sessions"
    path = _write_jsonl(
        root / "rollout-session.jsonl",
        {"type": "session_meta", "payload": {
            "session_id": "thread", "cli_version": "1",
        }},
    )
    original_identity = search._path_identity

    def no_identity(candidate):
        if candidate == path or candidate == root:
            return None
        return original_identity(candidate)

    monkeypatch.setattr(search, "_path_identity", no_identity)
    found = search._codex_sessions((root,))
    assert found == [search._DiscoveredSession("codex", str(path), str(path))]

    def roots_without_identity(paths):
        return tuple(paths)

    monkeypatch.setattr(search, "_dedupe_paths", roots_without_identity)
    original_stat = Path.stat

    def fail_stat(candidate, *args, **kwargs):
        if candidate == root:
            raise PermissionError("forced Codex root failure")
        return original_stat(candidate, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", fail_stat)
    with pytest.raises(search.SearchError, match="could not inspect Codex session root"):
        search._codex_sessions((root,))


def test_codex_metadata_uses_source_path_when_session_id_is_missing(tmp_path):
    root = tmp_path / "sessions"
    path = _write_jsonl(
        root / "rollout-without-id.jsonl",
        {"type": "session_meta", "payload": {
            "session_id": "thread", "cli_version": "1",
        }},
    )
    assert search._codex_sessions((root,)) == [
        search._DiscoveredSession("codex", str(path), str(path)),
    ]


def test_unique_physical_paths_keeps_missing_lexical_paths_and_refuses_stat_errors(
    tmp_path, monkeypatch,
):
    missing = tmp_path / "missing.jsonl"
    from nyxloom.session_extract import locate

    assert locate._unique_physical_paths([missing, missing]) == [missing]

    original_stat = Path.stat

    def fail_stat(path, *args, **kwargs):
        if path == missing:
            raise PermissionError("forced physical-path failure")
        return original_stat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", fail_stat)
    with pytest.raises(LocateError, match="could not inspect.*missing.jsonl"):
        locate._unique_physical_paths([missing])


def test_non_ascii_query_uses_the_python_streaming_fallback(tmp_path, monkeypatch):
    path = tmp_path / "unicode.jsonl"
    path.write_text(json.dumps({"content": "café"}, ensure_ascii=False) + "\n", encoding="utf-8")
    terms = ("café",)
    assert search._query_text_pattern(terms, "exact") is None
    assert search._query_bytes_pattern(terms, "exact") is None
    assert search._candidate_bytes_pattern(terms, "exact") is None
    assert search._raw_bytes_may_match(b"no literal query here", terms, "exact")
    assert search._filtered_jsonl_counts(path, terms, "exact") is None
    assert search._jsonl_content(path, terms)[0] == Counter({"café": 1})


def test_filtered_jsonl_handles_empty_invalid_incomplete_and_large_progress_files(
    tmp_path, monkeypatch,
):
    path = tmp_path / "records.jsonl"
    path.touch()
    assert search._filtered_jsonl_counts(path, ("qcow",), "exact") == Counter()

    path.write_bytes(b"qcow\n" + b'{"content":"qcow"}')
    progress = []
    assert search._filtered_jsonl_counts(
        path, ("qcow",), "exact", progress=lambda *event: progress.append(event),
    ) == Counter(qcow=1)

    monkeypatch.setattr(search, "_JSONL_CHUNK_SIZE", 16)
    path.write_bytes(b'{"content":"qcow' + b' and more"}\n')
    assert search._filtered_jsonl_counts(path, ("qcow",), "exact") == Counter(qcow=1)
    path.write_bytes(b'{"content":"qcow"}')
    assert search._filtered_jsonl_counts(path, ("qcow",), "exact") == Counter(qcow=1)

    path.write_bytes(
        b'{"content":"qcow"}\n' * 4096
    )
    progress.clear()
    assert search._filtered_jsonl_counts(
        path, ("qcow",), "exact", progress=lambda *event: progress.append(event),
    ) == Counter(qcow=search._TERM_FREQUENCY_CAP)
    assert any("candidate records" in event[0] for event in progress)

    monkeypatch.setattr(search, "_PROGRESS_FILE_THRESHOLD", 8)
    monkeypatch.setattr(search, "_PROGRESS_BYTE_STEP", 8)
    path.write_bytes(b"ordinary text without the query\n" * 8)
    progress.clear()
    assert search._filtered_jsonl_counts(
        path, ("qcow",), "exact", progress=lambda *event: progress.append(event),
    ) == Counter()
    assert len(progress) >= 2

    path.unlink()
    path.mkdir()
    with pytest.raises(search.SearchError, match="could not scan session transcript"):
        search._filtered_jsonl_counts(path, ("qcow",), "exact")


def test_filtered_jsonl_retries_candidate_matches_in_incomplete_chunks(tmp_path, monkeypatch):
    monkeypatch.setattr(search, "_JSONL_CHUNK_SIZE", 16)
    path = tmp_path / "incomplete.jsonl"
    path.write_bytes(b'{"content":"qcow and more"}\n')
    assert search._filtered_jsonl_counts(path, ("qcow",), "exact") == Counter(qcow=1)


def test_ripgrep_candidate_process_handles_unavailable_startup_bad_output_and_exit_status(
    tmp_path, monkeypatch,
):
    path = tmp_path / "session.jsonl"
    monkeypatch.setattr(search.shutil, "which", lambda _name: None)
    assert search._ripgrep_jsonl_counts((path,), ("qcow",), "exact", client="codex") is None
    monkeypatch.setattr(search.shutil, "which", lambda _name: "/usr/bin/rg")
    assert search._ripgrep_jsonl_counts((path,), ("café",), "exact", client="codex") is None

    def fail_start(*_args, **_kwargs):
        raise OSError("forced rg startup failure")

    monkeypatch.setattr(search.subprocess, "Popen", fail_start)
    with pytest.raises(search.SearchError, match="could not start ripgrep"):
        search._ripgrep_jsonl_counts((path,), ("qcow",), "exact", client="codex")

    class Process:
        def __init__(self, output=b"", code=0, running=False):
            self.stdout = io.BytesIO(output)
            self.code = code
            self.running = running
            self.terminated = False
            self.waited = False

        def poll(self):
            return None if self.running and not self.terminated else self.code

        def terminate(self):
            self.terminated = True

        def wait(self):
            self.waited = True
            return self.code

    process = Process(b"malformed output", running=True)
    monkeypatch.setattr(search.subprocess, "Popen", lambda *_args, **_kwargs: process)
    with pytest.raises(search.SearchError, match="malformed candidate"):
        search._ripgrep_jsonl_counts((path,), ("qcow",), "exact", client="codex")
    assert process.terminated and process.waited and process.stdout.closed

    process = Process(code=1)
    monkeypatch.setattr(search.subprocess, "Popen", lambda *_args, **_kwargs: process)
    assert search._ripgrep_jsonl_counts((path,), ("qcow",), "exact", client="codex") == {}

    def fail_exit(args, **kwargs):
        kwargs["stderr"].write(b"rg rejected input")
        return Process(code=2)

    monkeypatch.setattr(search.subprocess, "Popen", fail_exit)
    with pytest.raises(search.SearchError, match="rg rejected input"):
        search._ripgrep_jsonl_counts((path,), ("qcow",), "exact", client="codex")


def test_ripgrep_candidate_stream_skips_nonmatching_and_bad_records_and_reports_progress(
    tmp_path, monkeypatch,
):
    path = tmp_path / "session.jsonl"
    rows = [
        os.fsencode(path) + b"\0" + b"ordinary caf\xc3\xa9\n",
        os.fsencode(path) + b"\0" + b"qcow not-json\n",
    ]
    rows.extend(
        os.fsencode(path) + b"\0" + b'{"content":"qcow"}\n'
        for _ in range(4096)
    )

    class Process:
        def __init__(self, output):
            self.stdout = io.BytesIO(output)

        def poll(self):
            return 0

        def wait(self):
            return 0

        def terminate(self):
            pytest.fail("a completed ripgrep process should not be terminated")

    monkeypatch.setattr(search.shutil, "which", lambda _name: "/usr/bin/rg")
    monkeypatch.setattr(
        search.subprocess, "Popen", lambda *_args, **_kwargs: Process(b"".join(rows)),
    )
    progress = []
    result = search._ripgrep_jsonl_counts(
        (path,), ("qcow",), "exact", client="codex",
        progress=lambda *event: progress.append(event),
    )
    assert result == {
        str(path): Counter(qcow=search._TERM_FREQUENCY_CAP),
    }
    assert any("candidate records" in event[0] for event in progress)


def test_ripgrep_batches_paths_by_argument_budget(tmp_path, monkeypatch):
    monkeypatch.setattr(search, "_RG_ARGV_PATH_BUDGET", 10)
    paths = tuple(tmp_path / f"{index}.jsonl" for index in range(3))
    batches = tuple(search._ripgrep_path_batches(paths))
    assert batches == tuple((path,) for path in paths)


def test_jsonl_fallback_counts_unicode_and_reports_full_scan_progress(
    tmp_path, monkeypatch,
):
    monkeypatch.setattr(search, "_candidate_bytes_pattern", lambda *_args: None)
    path = tmp_path / "unicode.jsonl"
    path.write_text(
        json.dumps({"content": "café"}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    counts, activity = search._jsonl_content(path, ("café",))
    assert counts == Counter({"café": 1})
    assert activity is None

    path.write_text("{}\n" * 4096, encoding="utf-8")
    progress = []
    search._jsonl_content(
        path, progress=lambda *event: progress.append(event),
    )
    assert any("4,096 records" in event[0] for event in progress)


def test_jsonl_last_activity_reads_unterminated_records_and_reports_long_scans(
    tmp_path, monkeypatch,
):
    path = tmp_path / "activity.jsonl"
    path.write_text('{"timestamp":"2026-01-01"}', encoding="utf-8")
    assert search._jsonl_last_activity(path) == "2026-01-01"
    path.write_text('{}\n{"timestamp":"2026-01-02"}\n', encoding="utf-8")
    assert search._jsonl_last_activity(path) == "2026-01-02"
    path.write_text('{"timestamp": invalid}', encoding="utf-8")
    assert search._jsonl_last_activity(path) is None
    path.write_text("", encoding="utf-8")
    assert search._jsonl_last_activity(path) is None

    monkeypatch.setattr(search, "_PROGRESS_BYTE_STEP", 1024 * 1024)
    with path.open("wb") as output:
        output.truncate(2 * 1024 * 1024)
    progress = []
    assert search._jsonl_last_activity(
        path, progress=lambda *event: progress.append(event),
    ) is None
    assert progress

    path.unlink()
    path.mkdir()
    with pytest.raises(search.SearchError, match="could not read session activity"):
        search._jsonl_last_activity(path)


def test_jsonl_last_activity_handles_a_large_unterminated_record_across_chunks(
    tmp_path, monkeypatch,
):
    path = tmp_path / "large-final-record.jsonl"
    monkeypatch.setattr(search, "_ACTIVITY_CHUNK_SIZE", 128)
    path.write_text(
        '{"timestamp":"2026-01-01"}\n'
        + json.dumps({"timestamp": "2026-01-02", "content": "x" * 32_000}),
        encoding="utf-8",
    )

    assert search._jsonl_last_activity(path) == "2026-01-02"


def test_opencode_targeted_counts_skip_raw_misses_and_report_large_result_sets(tmp_path):
    messages = tuple(
        (f"m{index}", "s", json.dumps({"content": "ordinary text"}))
        for index in range(4999)
    ) + (("hit", "s", json.dumps({"content": "qcow qcow2"})),)
    database = _opencode_db(tmp_path / "opencode.db", messages=messages)
    progress = []
    counts = search._opencode_term_counts(
        database, ("qcow",), progress=lambda *event: progress.append(event),
    )
    assert counts == {"s": Counter(qcow=1)}
    assert any("5,000 records" in event[0] for event in progress)


def test_documents_fallback_and_ripgrep_identity_failures(tmp_path, monkeypatch):
    path = _write_jsonl(tmp_path / "rollout.jsonl", {"content": "needle"})
    session = search._DiscoveredSession("codex", "id", str(path))
    monkeypatch.setattr(search, "_store_sessions", lambda *_args, **_kwargs: [session])
    monkeypatch.setattr(search, "_ripgrep_jsonl_counts", lambda *_args, **_kwargs: None)
    assert search._documents("codex", ("needle",))[0].term_counts == Counter(needle=1)

    original_identity = search._path_identity

    def no_identity(candidate):
        if candidate == path:
            return None
        return original_identity(candidate)

    monkeypatch.setattr(search, "_path_identity", no_identity)
    monkeypatch.setattr(search, "_jsonl_content", lambda *_args, **_kwargs: (Counter(), None))
    assert search._documents("codex", ("needle",))[0].term_counts == Counter()

    monkeypatch.setattr(search, "_path_identity", original_identity)
    monkeypatch.setattr(
        search, "_ripgrep_jsonl_counts",
        lambda *_args, **_kwargs: {str(path.resolve()): Counter(needle=1)},
    )
    monkeypatch.setattr(search, "_jsonl_last_activity", lambda *_args, **_kwargs: None)
    assert search._documents("codex", ("needle",))[0].term_counts == Counter(needle=1)

    monkeypatch.setattr(
        search, "_ripgrep_jsonl_counts",
        lambda *_args, **_kwargs: {str(tmp_path / "missing.jsonl"): Counter(needle=1)},
    )
    with pytest.raises(search.SearchError, match="transcript that disappeared"):
        search._documents("codex", ("needle",))

    monkeypatch.setattr(
        search, "_ripgrep_jsonl_counts",
        lambda *_args, **_kwargs: {str(tmp_path / "unexpected.jsonl"): Counter(needle=1)},
    )
    unexpected = tmp_path / "unexpected.jsonl"
    unexpected.touch()
    with pytest.raises(search.SearchError, match="unexpected codex transcript"):
        search._documents("codex", ("needle",))


def test_documents_memoizes_activity_for_duplicate_physical_sources(tmp_path, monkeypatch):
    path = _write_jsonl(tmp_path / "rollout.jsonl", {"content": "needle"})
    sessions = [
        search._DiscoveredSession("codex", f"id-{index}", str(path))
        for index in range(25)
    ]
    monkeypatch.setattr(search, "_store_sessions", lambda *_args, **_kwargs: sessions)
    monkeypatch.setattr(
        search, "_ripgrep_jsonl_counts",
        lambda *_args, **_kwargs: {str(path): Counter(needle=1)},
    )
    activity_calls = []

    def activity(*_args, **_kwargs):
        activity_calls.append(True)
        return "2026-01-01"

    monkeypatch.setattr(search, "_jsonl_last_activity", activity)
    progress = []
    documents = search._documents(
        "codex", ("needle",), progress=lambda *event: progress.append(event),
    )
    assert len(documents) == 25
    assert len(activity_calls) == 1
    assert any("25/25" in event[0] for event in progress)


def test_source_root_file_errors_are_reported_without_silent_empty_results(
    tmp_path, monkeypatch,
):
    root = tmp_path / "unreadable"
    root.mkdir()
    original_stat = Path.stat

    def fail_stat(path, *args, **kwargs):
        if path == root:
            raise PermissionError("forced root inspection failure")
        return original_stat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", fail_stat)
    with pytest.raises(search.SearchError, match="could not inspect explicit source root"):
        search._source_roots((root,))


def test_ripgrep_candidate_stream_accepts_final_records_without_a_newline(
    tmp_path, monkeypatch,
):
    path = tmp_path / "session.jsonl"
    output = os.fsencode(path) + b'\0{"content":"qcow"}'

    class Process:
        def __init__(self):
            self.stdout = io.BytesIO(output)

        def poll(self):
            return 0

        def wait(self):
            return 0

        def terminate(self):
            pytest.fail("completed stream must not be terminated")

    monkeypatch.setattr(search.shutil, "which", lambda _name: "/usr/bin/rg")
    monkeypatch.setattr(search.subprocess, "Popen", lambda *_args, **_kwargs: Process())
    assert search._ripgrep_jsonl_counts(
        (path,), ("qcow",), "exact", client="codex",
    ) == {str(path): Counter(qcow=1)}


def test_ripgrep_stream_handles_newlines_in_paths_and_heartbeats(tmp_path, monkeypatch):
    path = tmp_path / "root with\nnewline" / "rollout.jsonl"
    output = (
        os.fsencode(path)
        + b"\0"
        + json.dumps({"content": "qcow"}).encode("utf-8")
        + b"\n"
    )

    class Process:
        def __init__(self):
            self.stdout = io.BytesIO(output)

        def poll(self):
            return 0

        def wait(self):
            return 0

        def terminate(self):
            pytest.fail("completed stream must not be terminated")

    readiness = iter((False, True, True))
    monkeypatch.setattr(search.shutil, "which", lambda _name: "/usr/bin/rg")
    monkeypatch.setattr(search.subprocess, "Popen", lambda *_args, **_kwargs: Process())
    monkeypatch.setattr(search, "_ripgrep_stdout_ready", lambda *_args: next(readiness, True))
    progress = []
    result = search._ripgrep_jsonl_counts(
        (path,), ("qcow",), "exact", client="codex",
        progress=lambda *event: progress.append(event),
    )
    assert result == {str(path): Counter(qcow=1)}
    assert any("still searching" in event[0] for event in progress)


def test_ripgrep_stdout_readiness_handles_selectable_and_memory_streams(monkeypatch):
    class Selectable:
        def fileno(self):
            return 29

    monkeypatch.setattr(search.select, "select", lambda *args: ([29], [], []))
    assert search._ripgrep_stdout_ready(Selectable(), 0.1) is True
    monkeypatch.setattr(search.select, "select", lambda *args: ([], [], []))
    assert search._ripgrep_stdout_ready(Selectable(), 0.1) is False
    assert search._ripgrep_stdout_ready(io.BytesIO(), 0.1) is True


def test_targeted_fallback_skips_raw_nonmatches_and_invalid_timestamp_records(
    tmp_path, monkeypatch,
):
    monkeypatch.setattr(search, "_candidate_bytes_pattern", lambda *_args: None)
    path = tmp_path / "fallback.jsonl"
    path.write_text(
        '{"content":"ordinary text"}\n{"type":"needle"}\n',
        encoding="utf-8",
    )
    decodes = []
    original_loads = search.json.loads

    def track_loads(raw, *args, **kwargs):
        decodes.append(raw)
        return original_loads(raw, *args, **kwargs)

    monkeypatch.setattr(search.json, "loads", track_loads)
    counts, activity = search._jsonl_content(path, ("needle",))
    assert counts == Counter()
    assert activity is None
    assert decodes == ['{"type":"needle"}\n']

    path.write_text(
        '{}\n{"timestamp": }\n{"timestamp":12}\n',
        encoding="utf-8",
    )
    assert search._jsonl_last_activity(path) is None


def test_unicode_raw_filters_can_have_no_special_case_patterns(monkeypatch):
    monkeypatch.setattr(search, "_ripgrep_casefold_chars", lambda _terms: ())
    search._query_casefold_bytes_pattern.cache_clear()
    assert search._query_casefold_bytes_pattern(("needle",)) is None
    monkeypatch.setattr(search, "_ripgrep_escape_literals", lambda _terms: ())
    search._query_escape_bytes_pattern.cache_clear()
    assert search._query_escape_bytes_pattern(("needle",)) is None
