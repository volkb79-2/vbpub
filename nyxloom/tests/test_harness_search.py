"""Behavioral coverage for local harness transcript search."""

from __future__ import annotations

import io
import json
import os
import sqlite3
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

import pytest

from nyxloom import harness_search as search
from nyxloom.cli_registry import harness_cli
from nyxloom.session_extract.locate import LocateError


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


def test_claude_signature_scan_stops_at_its_fifty_line_bound(tmp_path):
    path = tmp_path / "large.jsonl"
    path.write_text(
        "{}\n" * 50 + json.dumps({"sessionId": "late", "parentUuid": None}) + "\n",
        encoding="utf-8",
    )
    assert search._is_claude_transcript(path) is False


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


def test_streaming_record_filter_keeps_records_whose_query_crosses_a_chunk(
    tmp_path, monkeypatch,
):
    monkeypatch.setattr(search, "_JSONL_CHUNK_SIZE", 24)
    path = tmp_path / "chunked.jsonl"
    prefix = b'{"content":"'
    padding = b" " * (search._JSONL_CHUNK_SIZE - len(prefix) - 2)
    path.write_bytes(prefix + padding + b"qcow2" + b'"}\n')

    assert search._filtered_jsonl_counts(path, ("qcow",), "prefix") == Counter(qcow=1)


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
