"""locate.resolve_session_ref(): a bare session id -> the file/store holding
it. Every adapter root is faked under a tmp HOME (Path.home() reads $HOME on
POSIX), so these exercise the real globbing/priority/ambiguity rules without
touching this machine's own session history.
"""

from __future__ import annotations

import json
import sqlite3
import stat
from pathlib import Path

import pytest

from nyxloom.session_extract import locate
from nyxloom.session_extract.locate import LocateError, escape_cwd, resolve_session_ref

_UUID = "03b58ae4-5a21-4667-bf22-7eb364115ba3"
_OTHER_UUID = "04fcd848-15cb-48c2-b8fe-2ff2f6601000"
# Real shape, verified against all 1117 subagent files on the machine this
# was written on: 17 hex chars, NOT a uuid.
_AGENT_ID = "a36c6ff1d3cc69767"
# Real shape, verified against all 72 sessions in the real local opencode
# store: `ses_` + 26 MIXED-CASE alphanumerics, never pure hex -- deliberately
# mixed-case here so a hex-only trigger pattern (this feature's first design,
# read off the adapter docstring's truncated example) cannot pass these tests.
_OPENCODE_SID = "ses_04bd4e9b4ffeBJm48T6v130DS6"
# Real Reasonix filename stems observed under ~/.reasonix/projects/*/sessions.
_REASONIX_SID = "20260724-001821.579557040-deepseek-v4-flash"
_REASONIX_AGENT_ID = "sa_20260724_001900_000000000_abcdef1234567890"


@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / "home"
    h.mkdir()
    monkeypatch.setenv("HOME", str(h))
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    return h


def _claude_session(home: Path, project: str, uuid: str) -> Path:
    d = home / ".claude" / "projects" / project
    d.mkdir(parents=True, exist_ok=True)
    f = d / f"{uuid}.jsonl"
    f.write_text("{}\n", encoding="utf-8")
    return f


def _claude_subagent(home: Path, project: str, uuid: str, agent_id: str) -> Path:
    d = home / ".claude" / "projects" / project / uuid / "subagents"
    d.mkdir(parents=True, exist_ok=True)
    f = d / f"agent-{agent_id}.jsonl"
    f.write_text("{}\n", encoding="utf-8")
    return f


def _codex_rollout(home: Path, uuid: str) -> Path:
    d = home / ".codex" / "sessions" / "2026" / "09" / "12"
    d.mkdir(parents=True, exist_ok=True)
    f = d / f"rollout-2026-09-12T10-11-40-{uuid}.jsonl"
    f.write_text("{}\n", encoding="utf-8")
    return f


def _opencode_db(path: Path, session_ids: list[str]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.executescript(
        "CREATE TABLE session (id TEXT PRIMARY KEY, time_updated INTEGER);"
        "CREATE TABLE message (id TEXT PRIMARY KEY, session_id TEXT, time_created INTEGER, "
        "time_updated INTEGER, data TEXT);"
        "CREATE TABLE part (id TEXT PRIMARY KEY, message_id TEXT, session_id TEXT, "
        "time_created INTEGER, time_updated INTEGER, data TEXT);"
    )
    for sid in session_ids:
        conn.execute("INSERT INTO session VALUES (?, 1)", (sid,))
    conn.commit()
    conn.close()
    return path


def _reasonix_session(home: Path, project: str, session_id: str, *, subagent: bool = False) -> Path:
    directory = home / ".reasonix" / "projects" / project / "sessions"
    if subagent:
        directory /= "subagents"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{session_id}.jsonl"
    path.write_text('{"role":"user","content":"fixture"}\n', encoding="utf-8")
    return path


def test_an_existing_path_is_returned_unchanged(tmp_path):
    f = tmp_path / "session.jsonl"
    f.write_text(json.dumps({"sessionId": "s"}) + "\n", encoding="utf-8")
    ref = resolve_session_ref(str(f), tmp_path)
    assert ref.path == f
    assert ref.session_id is None


def test_escape_cwd_mirrors_claude_codes_own_project_dir_naming():
    # Both real shapes seen on this machine's 26 project dirs: a plain cwd,
    # and one with a dot-directory in it (/workspaces/vbpub/.worktrees/x ->
    # -workspaces-vbpub--worktrees-x).
    assert escape_cwd(Path("/workspaces/vbpub")) == "-workspaces-vbpub"
    assert escape_cwd(Path("/workspaces/vbpub/.worktrees/x")) == "-workspaces-vbpub--worktrees-x"


def test_bare_uuid_resolves_to_the_one_claude_code_session_file(home):
    f = _claude_session(home, "-workspaces-vbpub", _UUID)
    assert resolve_session_ref(_UUID, Path("/workspaces/vbpub")).path == f


def test_uuid_is_matched_case_insensitively(home):
    f = _claude_session(home, "-workspaces-vbpub", _UUID)
    assert resolve_session_ref(_UUID.upper(), Path("/workspaces/vbpub")).path == f


def test_a_uuid_found_only_outside_the_cwds_project_dir_still_resolves(home):
    # The cwd-matching directory is a priority, not a restriction: the full
    # scan is what makes resolution correct.
    f = _claude_session(home, "-workspaces-dstdns", _UUID)
    (home / ".claude" / "projects" / "-workspaces-vbpub").mkdir(parents=True)
    assert resolve_session_ref(_UUID, Path("/workspaces/vbpub")).path == f


def test_unique_match_survives_genuinely_empty_project_and_codex_scans(home):
    f = _claude_session(home, "-workspaces-vbpub", _UUID)
    empty_project = home / ".claude" / "projects" / "-workspaces-empty"
    (empty_project / "unrelated-session").mkdir(parents=True)
    (home / ".codex" / "sessions" / "2026" / "09" / "12").mkdir(parents=True)

    assert resolve_session_ref(_UUID, Path("/workspaces/vbpub")).path == f


def test_unique_claude_match_is_not_returned_after_a_project_scan_fails(
    home, monkeypatch
):
    expected = _claude_session(home, "-workspaces-vbpub", _UUID)
    failing_project = home / ".claude" / "projects" / "-workspaces-zbroken"
    failing_project.mkdir(parents=True)
    original_iterdir = Path.iterdir

    def fail_project_scan(path):
        if path == failing_project:
            raise PermissionError("forced project scan failure")
        return original_iterdir(path)

    monkeypatch.setattr(Path, "iterdir", fail_project_scan)

    with pytest.raises(LocateError) as e:
        resolve_session_ref(_UUID, Path("/workspaces/vbpub"))

    message = str(e.value)
    assert "indeterminate" in message
    assert "could not scan" in message
    assert str(failing_project) in message
    assert str(expected) not in message


def test_unique_claude_match_is_not_returned_after_a_codex_scan_fails(
    home, monkeypatch
):
    expected = _claude_session(home, "-workspaces-vbpub", _UUID)
    failing_root = home / ".codex" / "sessions"
    failing_root.mkdir(parents=True)
    original_iterdir = Path.iterdir

    def fail_codex_scan(path):
        if path == failing_root:
            raise PermissionError("forced Codex scan failure")
        return original_iterdir(path)

    monkeypatch.setattr(Path, "iterdir", fail_codex_scan)

    with pytest.raises(LocateError) as e:
        resolve_session_ref(_UUID, Path("/workspaces/vbpub"))

    message = str(e.value)
    assert "indeterminate" in message
    assert "could not scan" in message
    assert str(failing_root) in message
    assert str(expected) not in message


def test_a_preferred_claude_match_and_another_claude_match_are_ambiguous(home):
    # The cwd-matching directory is searched first for speed, but preference
    # cannot hide a second global match.
    preferred = _claude_session(home, "-workspaces-vbpub", _UUID)
    other = _claude_session(home, "-workspaces-dstdns", _UUID)
    with pytest.raises(LocateError) as e:
        resolve_session_ref(_UUID, Path("/workspaces/vbpub"))
    message = str(e.value)
    assert "matches 2 sessions" in message
    assert str(preferred) in message and str(other) in message
    assert message.index(str(preferred)) < message.index(str(other))


def test_a_preferred_claude_match_and_a_codex_match_are_ambiguous(home):
    preferred = _claude_session(home, "-workspaces-vbpub", _UUID)
    other = _codex_rollout(home, _UUID)
    with pytest.raises(LocateError) as e:
        resolve_session_ref(_UUID, Path("/workspaces/vbpub"))
    message = str(e.value)
    assert "matches 2 sessions" in message
    assert str(preferred) in message and str(other) in message
    assert message.index(str(preferred)) < message.index(str(other))


def test_two_matches_outside_the_cwd_error_listing_every_candidate(home):
    a = _claude_session(home, "-workspaces-dstdns", _UUID)
    b = _claude_session(home, "-workspaces-other", _UUID)
    with pytest.raises(LocateError) as e:
        resolve_session_ref(_UUID, Path("/workspaces/vbpub"))
    assert str(a) in str(e.value) and str(b) in str(e.value)
    assert "matches 2 sessions" in str(e.value)


def test_a_uuid_in_both_a_claude_project_and_a_codex_rollout_is_ambiguous(home):
    a = _claude_session(home, "-workspaces-dstdns", _UUID)
    b = _codex_rollout(home, _UUID)
    with pytest.raises(LocateError) as e:
        resolve_session_ref(_UUID, Path("/workspaces/vbpub"))
    assert str(a) in str(e.value) and str(b) in str(e.value)


def test_bare_subagent_agent_id_resolves_to_its_own_transcript(home):
    f = _claude_subagent(home, "-workspaces-vbpub", _UUID, _AGENT_ID)
    assert resolve_session_ref(_AGENT_ID, Path("/workspaces/vbpub")).path == f


def test_a_subagent_id_is_never_searched_among_codex_rollouts(home):
    # A 17-hex agentId cannot be a Codex rollout's uuid suffix; the codex
    # glob is skipped entirely for that shape rather than wasted.
    _codex_rollout(home, _UUID)
    with pytest.raises(LocateError) as e:
        resolve_session_ref(_AGENT_ID, Path("/workspaces/vbpub"))
    assert "not found under" in str(e.value)


def test_bare_uuid_resolves_to_a_codex_rollout_by_filename_suffix(home):
    f = _codex_rollout(home, _UUID)
    assert resolve_session_ref(_UUID, Path("/workspaces/vbpub")).path == f


def test_no_match_errors_and_names_where_it_looked(home):
    _claude_session(home, "-workspaces-vbpub", _OTHER_UUID)
    with pytest.raises(LocateError) as e:
        resolve_session_ref(_UUID, Path("/workspaces/vbpub"))
    assert ".claude/projects" in str(e.value) and ".codex/sessions" in str(e.value)


def test_an_unrecognized_ref_shape_errors_without_searching(home):
    with pytest.raises(LocateError) as e:
        resolve_session_ref("not-a-session-id", Path("/workspaces/vbpub"))
    assert "neither an existing path nor a recognized session id" in str(e.value)


def test_opencode_session_id_resolves_to_its_store_and_carries_the_id(home):
    sid = _OPENCODE_SID
    db = _opencode_db(home / ".local" / "share" / "opencode" / "opencode.db", [sid, "ses_dead"])
    ref = resolve_session_ref(sid, Path("/workspaces/vbpub"))
    assert ref.path == db
    assert ref.session_id == sid


def test_opencode_lookup_honors_xdg_data_home(home, tmp_path, monkeypatch):
    sid = _OPENCODE_SID
    xdg = tmp_path / "xdg"
    monkeypatch.setenv("XDG_DATA_HOME", str(xdg))
    db = _opencode_db(xdg / "opencode" / "opencode.db", [sid])
    assert resolve_session_ref(sid, Path("/workspaces/vbpub")).path == db


def test_an_opencode_id_in_two_stores_is_ambiguous(home, tmp_path, monkeypatch):
    sid = _OPENCODE_SID
    xdg = tmp_path / "xdg"
    monkeypatch.setenv("XDG_DATA_HOME", str(xdg))
    a = _opencode_db(xdg / "opencode" / "opencode.db", [sid])
    b = _opencode_db(home / ".local" / "share" / "opencode" / "opencode.db", [sid])
    with pytest.raises(LocateError) as e:
        resolve_session_ref(sid, Path("/workspaces/vbpub"))
    assert str(a) in str(e.value) and str(b) in str(e.value)
    assert "--opencode-session" in str(e.value)


def test_an_unknown_opencode_id_errors_naming_the_stores_searched(home):
    _opencode_db(home / ".local" / "share" / "opencode" / "opencode.db", ["ses_other"])
    with pytest.raises(LocateError) as e:
        resolve_session_ref(_OPENCODE_SID, Path("/workspaces/vbpub"))
    message = str(e.value)
    assert "opencode.db" in message
    assert "not found" in message
    assert "indeterminate" not in message


def test_bare_reasonix_session_id_resolves_to_the_primary_file(home):
    path = _reasonix_session(home, "-workspaces-vbpub", _REASONIX_SID)
    ref = resolve_session_ref(_REASONIX_SID, Path("/workspaces/vbpub"))
    assert ref.path == path
    assert ref.session_id is None


def test_bare_reasonix_subagent_id_resolves_to_the_subagents_file(home):
    path = _reasonix_session(
        home, "-workspaces-vbpub", _REASONIX_AGENT_ID, subagent=True
    )
    assert resolve_session_ref(_REASONIX_AGENT_ID, Path("/workspaces/vbpub")).path == path


def test_reasonix_event_snapshot_companion_is_not_a_bare_session_match(home):
    directory = home / ".reasonix" / "projects" / "-workspaces-vbpub" / "sessions"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{_REASONIX_SID}.events.jsonl").write_text(
        '{"type":"replace","messages":[]}\n', encoding="utf-8"
    )
    with pytest.raises(LocateError) as exc:
        resolve_session_ref(_REASONIX_SID, Path("/workspaces/vbpub"))
    assert "Reasonix" in str(exc.value)
    assert "not found" in str(exc.value)


def test_two_reasonix_projects_with_the_same_session_id_are_ambiguous(home):
    first = _reasonix_session(home, "-workspaces-vbpub", _REASONIX_SID)
    second = _reasonix_session(home, "-workspaces-other", _REASONIX_SID)
    with pytest.raises(LocateError) as exc:
        resolve_session_ref(_REASONIX_SID, Path("/workspaces/vbpub"))
    message = str(exc.value)
    assert "matches 2 sessions" in message
    assert str(first) in message and str(second) in message


def test_reasonix_scan_ignores_a_non_directory_project_entry(home):
    root = home / ".reasonix" / "projects"
    root.mkdir(parents=True)
    (root / "not-a-project").write_text("not a directory", encoding="utf-8")
    assert locate._reasonix_matches(_REASONIX_SID) == []


def test_reasonix_scan_ignores_nonmatching_subagent_files(home):
    directory = (
        home / ".reasonix" / "projects" / "-workspaces-vbpub" / "sessions" / "subagents"
    )
    directory.mkdir(parents=True)
    (directory / "other-session.jsonl").write_text("{}\n", encoding="utf-8")
    wanted = directory / f"{_REASONIX_SID}.jsonl"
    wanted.write_text("{}\n", encoding="utf-8")
    assert locate._reasonix_matches(_REASONIX_SID) == [wanted]


def test_xdg_data_home_equal_to_the_default_is_not_searched_twice(home, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(home / ".local" / "share"))
    assert locate._opencode_db_candidates() == [
        home / ".local" / "share" / "opencode" / "opencode.db"
    ]


def test_a_non_opencode_sqlite_file_at_the_default_path_is_skipped(home):
    # sniff() is the gate, not the filename: a DB without session/message/part
    # is not an opencode store and must not swallow the lookup.
    db = home / ".local" / "share" / "opencode" / "opencode.db"
    db.parent.mkdir(parents=True)
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE unrelated (x INTEGER)")
    conn.commit()
    conn.close()
    with pytest.raises(LocateError) as e:
        resolve_session_ref(_OPENCODE_SID, Path("/workspaces/vbpub"))
    assert "not found" in str(e.value)
    assert "indeterminate" not in str(e.value)


def test_a_preferred_project_with_two_matching_layouts_is_ambiguous(home):
    _claude_session(home, "-workspaces-vbpub", _UUID)
    _claude_subagent(home, "-workspaces-vbpub", _UUID, _UUID)
    with pytest.raises(LocateError, match="matches 2 sessions"):
        resolve_session_ref(_UUID, Path("/workspaces/vbpub"))


def test_non_directory_children_of_the_projects_root_are_ignored(home):
    root = home / ".claude" / "projects"
    root.mkdir(parents=True)
    (root / "not-a-project-directory").write_text("file", encoding="utf-8")
    with pytest.raises(LocateError):
        resolve_session_ref(_UUID, Path("/workspaces/vbpub"))


def test_duplicate_candidate_paths_are_deduplicated(home, monkeypatch):
    root = home / ".claude" / "projects"
    project = root / "-workspaces-other"
    project.mkdir(parents=True)
    candidate = project / f"{_UUID}.jsonl"
    candidate.write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(locate, "_claude_matches_in", lambda *_args: [candidate, candidate])
    assert resolve_session_ref(_UUID, Path("/workspaces/vbpub")).path == candidate


def test_scan_directory_distinguishes_missing_non_directory_and_scan_errors(tmp_path, monkeypatch):
    with pytest.raises(LocateError, match="could not inspect"):
        locate._scan_directory(tmp_path / "missing")

    regular = tmp_path / "regular"
    regular.write_text("file", encoding="utf-8")
    assert locate._scan_directory(regular) == []

    original_stat = locate.Path.stat

    def fail_stat(path, *args, **kwargs):
        if path == tmp_path / "denied":
            raise PermissionError("metadata denied")
        return original_stat(path, *args, **kwargs)

    denied = tmp_path / "denied"
    denied.mkdir()
    monkeypatch.setattr(locate.Path, "stat", fail_stat)
    with pytest.raises(LocateError, match="could not inspect"):
        locate._scan_directory(denied)


def test_scan_directory_treats_a_disappearing_directory_as_configured(tmp_path, monkeypatch):
    disappearing = tmp_path / "disappearing"
    disappearing.mkdir()
    original_iterdir = Path.iterdir

    def fail_iterdir(path):
        if path == disappearing:
            raise FileNotFoundError("directory disappeared")
        return original_iterdir(path)

    monkeypatch.setattr(locate.Path, "iterdir", fail_iterdir)
    assert locate._scan_directory(disappearing, missing_ok=True) == []
    with pytest.raises(LocateError, match="could not scan"):
        locate._scan_directory(disappearing)


def test_entry_mode_reports_metadata_failure(tmp_path):
    with pytest.raises(LocateError, match="could not inspect"):
        locate._entry_mode(tmp_path / "missing")


def test_claude_match_scan_ignores_a_non_directory_non_file_entry(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    odd = project / "socket-like"
    odd.write_text("not a session", encoding="utf-8")
    original_entry_mode = locate._entry_mode

    def fake_entry_mode(path):
        if path == odd:
            return stat.S_IFIFO
        return original_entry_mode(path)

    monkeypatch.setattr(locate, "_entry_mode", fake_entry_mode)
    assert locate._claude_matches_in(project, _UUID) == []


def test_claude_match_scan_ignores_a_non_directory_non_file_nested_entry(
    tmp_path, monkeypatch
):
    project = tmp_path / "project"
    nested = project / _UUID / "subagents"
    nested.mkdir(parents=True)
    odd = nested / "socket-like"
    odd.write_text("not a session", encoding="utf-8")
    original_entry_mode = locate._entry_mode

    def fake_entry_mode(path):
        if path == odd:
            return stat.S_IFIFO
        return original_entry_mode(path)

    monkeypatch.setattr(locate, "_entry_mode", fake_entry_mode)
    assert locate._claude_matches_in(project, _AGENT_ID) == []


def test_codex_match_scan_ignores_a_non_directory_non_file_entry(
    tmp_path, monkeypatch
):
    root = tmp_path / "sessions"
    root.mkdir()
    odd = root / "socket-like"
    odd.write_text("not a rollout", encoding="utf-8")
    original_entry_mode = locate._entry_mode

    def fake_entry_mode(path):
        if path == odd:
            return stat.S_IFIFO
        return original_entry_mode(path)

    monkeypatch.setattr(locate, "_codex_sessions_root", lambda: root)
    monkeypatch.setattr(locate, "_entry_mode", fake_entry_mode)
    assert locate._codex_matches(_UUID) == []


def test_opencode_lookup_reports_a_database_open_failure_as_indeterminate(home, monkeypatch):
    db = home / ".local" / "share" / "opencode" / "opencode.db"
    db.parent.mkdir(parents=True)
    db.write_bytes(b"not a database")
    from nyxloom.session_extract.adapters import opencode

    monkeypatch.setattr(opencode, "sniff", lambda _path: True)

    def fail_connect(*_args, **_kwargs):
        raise locate.sqlite3.OperationalError("locked")

    monkeypatch.setattr(locate.sqlite3, "connect", fail_connect)
    with pytest.raises(LocateError) as e:
        resolve_session_ref(_OPENCODE_SID, Path("/workspaces/vbpub"))
    assert "indeterminate" in str(e.value)
    assert "query failed" in str(e.value)
    assert "not found" not in str(e.value)


def test_opencode_lookup_reports_a_query_error_as_indeterminate(home, monkeypatch):
    db = home / ".local" / "share" / "opencode" / "opencode.db"
    db.parent.mkdir(parents=True)
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE unrelated (x INTEGER)")
    conn.commit()
    conn.close()
    from nyxloom.session_extract.adapters import opencode

    monkeypatch.setattr(opencode, "sniff", lambda _path: True)
    with pytest.raises(LocateError) as e:
        resolve_session_ref(_OPENCODE_SID, Path("/workspaces/vbpub"))
    assert "indeterminate" in str(e.value)
    assert "query failed" in str(e.value)
    assert "not found" not in str(e.value)


def test_opencode_sniff_failure_is_indeterminate_not_no_match(home, monkeypatch):
    db = home / ".local" / "share" / "opencode" / "opencode.db"
    db.parent.mkdir(parents=True)
    db.write_bytes(b"not a database")
    from nyxloom.session_extract.adapters import opencode

    def fail_sniff(_path):
        raise OSError("permission denied")

    monkeypatch.setattr(opencode, "sniff", fail_sniff)
    with pytest.raises(LocateError) as e:
        resolve_session_ref(_OPENCODE_SID, Path("/workspaces/vbpub"))
    assert "indeterminate" in str(e.value)
    assert "sniff failed" in str(e.value)
    assert "not found" not in str(e.value)


def test_opencode_match_is_not_resolved_when_another_store_is_indeterminate(
    home, tmp_path, monkeypatch
):
    sid = _OPENCODE_SID
    xdg = tmp_path / "xdg"
    good = _opencode_db(xdg / "opencode" / "opencode.db", [sid])
    bad = home / ".local" / "share" / "opencode" / "opencode.db"
    bad.parent.mkdir(parents=True)
    bad.write_bytes(b"locked database")
    monkeypatch.setenv("XDG_DATA_HOME", str(xdg))

    from nyxloom.session_extract.adapters import opencode

    monkeypatch.setattr(opencode, "sniff", lambda _path: True)
    with pytest.raises(LocateError) as e:
        resolve_session_ref(sid, Path("/workspaces/vbpub"))
    message = str(e.value)
    assert "indeterminate" in message
    assert str(bad) in message
    assert str(good) not in message


def test_opencode_lookup_reports_a_store_stat_failure_as_indeterminate(home, monkeypatch):
    db = home / ".local" / "share" / "opencode" / "opencode.db"
    db.parent.mkdir(parents=True)
    db.write_bytes(b"candidate")
    monkeypatch.setattr(locate, "_opencode_db_candidates", lambda: [db])

    original_stat = locate.Path.stat

    def fail_stat(path, *args, **kwargs):
        if path == db:
            raise PermissionError("metadata denied")
        return original_stat(path, *args, **kwargs)

    monkeypatch.setattr(locate.Path, "stat", fail_stat)
    with pytest.raises(LocateError) as e:
        locate._opencode_matches(_OPENCODE_SID)
    message = str(e.value)
    assert "indeterminate" in message
    assert "path lookup failed" in message
    assert "metadata denied" in message


def test_a_non_regular_opencode_candidate_is_skipped_as_a_negative(home, monkeypatch):
    db = home / ".local" / "share" / "opencode" / "opencode.db"
    db.mkdir(parents=True)
    monkeypatch.setattr(locate, "_opencode_db_candidates", lambda: [db])

    with pytest.raises(LocateError) as e:
        resolve_session_ref(_OPENCODE_SID, Path("/workspaces/vbpub"))
    assert "not found" in str(e.value)
    assert "indeterminate" not in str(e.value)


def test_opencode_false_sniff_on_expected_schema_is_indeterminate(home, monkeypatch):
    db = _opencode_db(home / ".local" / "share" / "opencode" / "opencode.db", [])
    monkeypatch.setattr(locate, "_opencode_db_candidates", lambda: [db])
    from nyxloom.session_extract.adapters import opencode

    monkeypatch.setattr(opencode, "sniff", lambda _path: False)
    with pytest.raises(LocateError) as e:
        resolve_session_ref(_OPENCODE_SID, Path("/workspaces/vbpub"))
    message = str(e.value)
    assert "indeterminate" in message
    assert "sniff failed" in message
    assert "expected opencode tables" in message


def test_opencode_probe_failure_after_a_false_sniff_is_indeterminate(home, monkeypatch):
    db = home / ".local" / "share" / "opencode" / "opencode.db"
    db.parent.mkdir(parents=True)
    db.write_bytes(b"candidate")
    monkeypatch.setattr(locate, "_opencode_db_candidates", lambda: [db])
    from nyxloom.session_extract.adapters import opencode

    monkeypatch.setattr(opencode, "sniff", lambda _path: False)

    def fail_connect(*_args, **_kwargs):
        raise locate.sqlite3.OperationalError("probe locked")

    monkeypatch.setattr(locate.sqlite3, "connect", fail_connect)
    with pytest.raises(LocateError) as e:
        resolve_session_ref(_OPENCODE_SID, Path("/workspaces/vbpub"))
    message = str(e.value)
    assert "indeterminate" in message
    assert "sniff failed" in message
    assert "probe locked" in message


def test_opencode_query_cleanup_failure_is_indeterminate(home, monkeypatch):
    db = home / ".local" / "share" / "opencode" / "opencode.db"
    db.parent.mkdir(parents=True)
    db.write_bytes(b"candidate")
    monkeypatch.setattr(locate, "_opencode_db_candidates", lambda: [db])
    from nyxloom.session_extract.adapters import opencode

    monkeypatch.setattr(opencode, "sniff", lambda _path: True)

    class Cursor:
        def fetchone(self):
            return None

    class Connection:
        def execute(self, *_args):
            return Cursor()

        def close(self):
            raise RuntimeError("query close failed")

    monkeypatch.setattr(locate.sqlite3, "connect", lambda *_args, **_kwargs: Connection())
    with pytest.raises(LocateError) as e:
        resolve_session_ref(_OPENCODE_SID, Path("/workspaces/vbpub"))
    message = str(e.value)
    assert "indeterminate" in message
    assert "query cleanup failed" in message
    assert "query close failed" in message


def test_opencode_probe_cleanup_failure_is_indeterminate(home, monkeypatch):
    db = home / ".local" / "share" / "opencode" / "opencode.db"
    db.parent.mkdir(parents=True)
    db.write_bytes(b"candidate")
    monkeypatch.setattr(locate, "_opencode_db_candidates", lambda: [db])
    from nyxloom.session_extract.adapters import opencode

    monkeypatch.setattr(opencode, "sniff", lambda _path: False)

    class Cursor:
        def fetchall(self):
            return []

    class Connection:
        def execute(self, *_args):
            return Cursor()

        def close(self):
            raise RuntimeError("probe close failed")

    monkeypatch.setattr(locate.sqlite3, "connect", lambda *_args, **_kwargs: Connection())
    with pytest.raises(LocateError) as e:
        resolve_session_ref(_OPENCODE_SID, Path("/workspaces/vbpub"))
    message = str(e.value)
    assert "indeterminate" in message
    assert "sniff failed" in message
    assert "probe close failed" in message
