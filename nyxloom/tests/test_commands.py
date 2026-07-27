"""Tests for nyxloom.commands (P12: ntfy inbound command listener)."""

from __future__ import annotations

import http.server
import json
import subprocess
import threading
from pathlib import Path

from nyxloom import paths, storage
from nyxloom.commands import (
    CommandListener, HELP_TEXT, REPLY_TAG, UNKNOWN_REPLY,
)
from nyxloom.config import load_registry, register_project
from nyxloom.types import (
    Actor, ActorKind, EventType, TaskState, TaskStateFile, utc_now,
)


# =========================================================================
# Oracle 1: help / unknown-command / strict-regex rejection
# =========================================================================

def test_help_lists_all_five_verbs(sample_project):
    cl = CommandListener(load_registry())
    reply = cl.handle_message("help", [])
    for verb in ("help", "status", "pause", "resume", "digest"):
        assert verb in reply
    assert reply == HELP_TEXT


def test_garbage_command_is_rejected(sample_project):
    cl = CommandListener(load_registry())
    assert cl.handle_message("rm -rf /", []) == UNKNOWN_REPLY


def test_shell_metacharacters_rejected_by_strict_regex(sample_project):
    cl = CommandListener(load_registry())
    assert cl.handle_message("resume; rm x", []) == UNKNOWN_REPLY


# =========================================================================
# Oracle 2: pause / resume -- CLI-equivalent flag + event semantics
# =========================================================================

def test_resume_clears_flag_and_appends_cleared_event(tmp_state, sample_project):
    flag = paths.pause_flag("demo")
    flag.parent.mkdir(parents=True, exist_ok=True)
    flag.touch()
    assert flag.exists()

    cl = CommandListener(load_registry())
    reply = cl.handle_message("resume demo", [])

    assert "resumed" in reply
    assert not flag.exists()
    events = list(storage.iter_events("demo"))
    cleared = [e for e in events if e.type is EventType.PAUSE_CLEARED]
    assert len(cleared) == 1
    assert cleared[0].actor.id == "ntfy-cmd"
    assert cleared[0].actor.kind is ActorKind.OPERATOR


def test_pause_sets_flag_and_appends_set_event(tmp_state, sample_project):
    flag = paths.pause_flag("demo")
    assert not flag.exists()

    cl = CommandListener(load_registry())
    reply = cl.handle_message("pause demo", [])

    assert "paused" in reply
    assert flag.exists()
    # P15 2026-07-15: default (no mode word) is 'handoffs' -> drain-handoffs,
    # the legacy meaning of a bare pause; the flag's CONTENT is now the mode.
    assert flag.read_text(encoding="utf-8") == "drain-handoffs"
    events = list(storage.iter_events("demo"))
    set_evs = [e for e in events if e.type is EventType.PAUSE_SET]
    assert len(set_evs) == 1
    assert set_evs[0].actor.id == "ntfy-cmd"
    assert set_evs[0].actor.kind is ActorKind.OPERATOR
    assert set_evs[0].payload == {"mode": "drain-handoffs"}


# =========================================================================
# P15 2026-07-15: factory-state pause MODES -- ntfy verb surface (oracle 7:
# "UI/CLI/ntfy verb each set the mode file + event").
# =========================================================================

def test_pause_agents_mode_sets_flag_content_and_event(tmp_state, sample_project):
    flag = paths.pause_flag("demo")
    cl = CommandListener(load_registry())
    reply = cl.handle_message("pause demo agents", [])

    assert "drain-agents" in reply
    assert flag.read_text(encoding="utf-8") == "drain-agents"
    set_evs = [e for e in storage.iter_events("demo") if e.type is EventType.PAUSE_SET]
    assert len(set_evs) == 1
    assert set_evs[0].payload == {"mode": "drain-agents"}


def test_pause_handoffs_mode_explicit(tmp_state, sample_project):
    flag = paths.pause_flag("demo")
    cl = CommandListener(load_registry())
    reply = cl.handle_message("pause demo handoffs", [])

    assert "drain-handoffs" in reply
    assert flag.read_text(encoding="utf-8") == "drain-handoffs"
    set_evs = [e for e in storage.iter_events("demo") if e.type is EventType.PAUSE_SET]
    assert set_evs[0].payload == {"mode": "drain-handoffs"}


def test_pause_unknown_mode_rejected_no_flag_no_event(tmp_state, sample_project):
    flag = paths.pause_flag("demo")
    assert not flag.exists()
    cl = CommandListener(load_registry())
    reply = cl.handle_message("pause demo bogus", [])

    assert "unknown mode" in reply
    assert not flag.exists()
    assert not any(e.type is EventType.PAUSE_SET for e in storage.iter_events("demo"))


# =========================================================================
# Oracle 2b: the ntfy chat-ops resume guard -- mirrors `cli.cmd_resume`'s
# project-level RP03 pre-resume drift check (test_resume_guard.py) on this,
# the SECOND resume surface. There is no --force over ntfy: a refusal here
# is unconditional. Fixture shape (`_make_merged_drift`) is the SAME one
# test_resume_guard.py uses -- both surfaces sit on the identical shared
# detector (`resync.resync_plan`), so the same drift fixture proves it.
# =========================================================================

def _run_git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
        cwd=root, check=True, capture_output=True, text=True,
    )


def _make_merged_drift(root: Path, task_id: str, branch: str) -> None:
    """A task believed MERGE_READY whose branch was ACTUALLY merged into
    `main` (a real `git branch --merged` hit) while the project sat
    paused -- the statefile just never caught up."""
    _run_git(root, "checkout", "-b", branch)
    (root / f"marker-{task_id}.txt").write_text("work\n")
    _run_git(root, "add", "-A")
    _run_git(root, "commit", "-qm", f"{task_id} work")
    _run_git(root, "checkout", "main")
    _run_git(root, "merge", "--no-ff", "-m", f"merge {task_id}", branch)
    storage.save_state(TaskStateFile(
        schema_version=1, task_id=task_id, project="demo",
        state=TaskState.MERGE_READY, since=utc_now(),
    ))


def test_ntfy_resume_verified_clean_resumes(tmp_state, sample_project):
    """No drift (no statefiles at all -> the shared planner's scan comes
    back `[]`, verified clean) -- resume proceeds exactly as it did before
    the guard existed: flag removed, PAUSE_CLEARED appended once with an
    empty payload, 'resumed: ...' reply."""
    flag = paths.pause_flag("demo")
    flag.parent.mkdir(parents=True, exist_ok=True)
    flag.touch()

    cl = CommandListener(load_registry())
    reply = cl.handle_message("resume demo", [])

    assert reply == "resumed: demo"
    assert not flag.exists()
    events = list(storage.iter_events("demo"))
    cleared = [e for e in events if e.type is EventType.PAUSE_CLEARED]
    assert len(cleared) == 1
    assert cleared[0].payload == {}


def test_ntfy_resume_drift_refuses_and_writes_nothing(tmp_state, sample_project):
    """A task believed MERGE_READY whose branch was actually merged (real
    drift, the shared planner's `[ProposedTransition(...)]`) -> the guard
    refuses. The reply names the drifted task id AND the repair command;
    the pause flag is untouched and NO PAUSE_CLEARED event is appended --
    a refusal writes nothing, not just 'prints something different'. A
    second call (no --force exists over ntfy) refuses identically,
    proving the first refusal changed nothing that would flip the
    outcome."""
    root = sample_project.root
    _make_merged_drift(root, "demo-P60-drift", "feat/demo-P60-drift")

    flag = paths.pause_flag("demo")
    flag.parent.mkdir(parents=True, exist_ok=True)
    flag.touch()

    cl = CommandListener(load_registry())
    reply = cl.handle_message("resume demo", [])

    assert "demo-P60-drift" in reply
    assert "nyxloom resync demo" in reply
    assert flag.exists()
    assert list(storage.iter_events("demo")) == []

    reply2 = cl.handle_message("resume demo", [])
    assert "demo-P60-drift" in reply2
    assert flag.exists()
    assert list(storage.iter_events("demo")) == []


def test_ntfy_resume_scan_failure_refuses_distinguishably(
    tmp_state, sample_project, monkeypatch,
):
    """The scan itself raising (e.g. the git subprocess call blows up while
    gathering ground truth) must NEVER be read as 'no drift' -- refuses
    with 'could not verify' wording DISTINCT from the drift-detected
    reply above ('drift detected' must NOT appear), and writes nothing."""
    def _boom(*a, **k):
        raise RuntimeError("git subprocess exploded")
    monkeypatch.setattr("nyxloom.resync.gather_git_facts", _boom)

    flag = paths.pause_flag("demo")
    flag.parent.mkdir(parents=True, exist_ok=True)
    flag.touch()

    cl = CommandListener(load_registry())
    reply = cl.handle_message("resume demo", [])

    assert "could not verify" in reply
    assert "drift detected" not in reply
    assert flag.exists()
    assert list(storage.iter_events("demo")) == []


def test_ntfy_resume_cfg_load_failure_refuses_without_crashing(
    tmp_state, sample_project, monkeypatch,
):
    """A project config that fails to load (malformed project.toml, say)
    must degrade to the same 'could not verify' refusal -- not raise out
    of `handle_message` and crash the chat-ops dispatch loop. Writes
    nothing, same as the other two refusal paths."""
    def _boom(root):
        raise RuntimeError("bad project.toml")
    monkeypatch.setattr("nyxloom.commands.ProjectConfig.load", _boom)

    flag = paths.pause_flag("demo")
    flag.parent.mkdir(parents=True, exist_ok=True)
    flag.touch()

    cl = CommandListener(load_registry())
    reply = cl.handle_message("resume demo", [])

    assert "could not verify" in reply
    assert flag.exists()
    assert list(storage.iter_events("demo")) == []


# =========================================================================
# Oracle 3: status reflects seeded statefiles
# =========================================================================

def test_status_reflects_seeded_statefiles(tmp_state, sample_project):
    for i in range(3):
        storage.save_state(TaskStateFile(
            schema_version=1, task_id=f"t{i}", project="demo",
            state=TaskState.QUEUED, since=utc_now(),
        ))
    storage.save_state(TaskStateFile(
        schema_version=1, task_id="tA", project="demo",
        state=TaskState.ACTIVE, since=utc_now(),
    ))

    cl = CommandListener(load_registry())
    reply = cl.handle_message("status demo", [])

    assert reply.startswith("demo:")
    assert "3 QUEUED" in reply
    assert "1 ACTIVE" in reply


def test_status_reflects_paused_flag(tmp_state, sample_project):
    flag = paths.pause_flag("demo")
    flag.parent.mkdir(parents=True, exist_ok=True)
    flag.touch()

    cl = CommandListener(load_registry())
    reply = cl.handle_message("status demo", [])

    assert reply.endswith("(paused)")


def test_status_unknown_project(sample_project):
    cl = CommandListener(load_registry())
    assert cl.handle_message("status ghost", []) == "unknown project: ghost"


def test_status_missing_project_arg(sample_project):
    cl = CommandListener(load_registry())
    reply = cl.handle_message("status", [])
    assert "missing project" in reply


# =========================================================================
# Oracle 4: nyxloomd-reply tag is the loop guard
# =========================================================================

def test_nyxloomd_reply_tag_is_ignored(sample_project):
    cl = CommandListener(load_registry())
    assert cl.handle_message("resume demo", [REPLY_TAG]) is None
    assert cl.handle_message("anything at all, really", ["x", REPLY_TAG]) is None


# =========================================================================
# Oracle 5: transport -- reply POST + reconnect-with-backoff over `since`
# =========================================================================

class _FakeNtfyServer:
    """Streams one prepared JSON message line then blocks the GET
    connection open (simulating a long-poll) until the test releases it,
    at which point the handler returns and the connection closes -- from
    the listener's point of view, the long-poll ends and it must
    reconnect. Also captures the reply POST the listener issues back."""

    def __init__(self) -> None:
        self.events: list[dict] = []
        self._lock = threading.Lock()
        self._release = threading.Event()
        self.first_get_seen = threading.Event()
        # B25 (de-flaking): two more Events, set the instant the relevant
        # request lands, so callers can `.wait()` on them instead of
        # sleep-polling `gets()`/`posts()`.
        self.reply_seen = threading.Event()
        self.second_get_seen = threading.Event()
        server = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, fmt, *args):  # noqa: A002
                pass

            def do_GET(self):
                with server._lock:
                    server.events.append({"method": "GET", "path": self.path,
                                           "headers": dict(self.headers)})
                    get_count = sum(1 for e in server.events if e["method"] == "GET")
                self.send_response(200)
                self.send_header("Content-Type", "application/x-ndjson")
                self.end_headers()
                line = json.dumps({
                    "id": "m1", "time": 1, "event": "message",
                    "message": "status cmdproj", "tags": [],
                })
                self.wfile.write((line + "\n").encode("utf-8"))
                self.wfile.flush()
                server.first_get_seen.set()
                if get_count >= 2:
                    server.second_get_seen.set()
                # Hold the connection open (long-poll) until released.
                server._release.wait(timeout=5)

            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(length)
                with server._lock:
                    server.events.append({"method": "POST", "path": self.path,
                                           "headers": dict(self.headers),
                                           "body": body})
                server.reply_seen.set()
                self.send_response(200)
                self.end_headers()

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(
            target=self.httpd.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True,
        )
        self.thread.start()

    def release_first_connection(self) -> None:
        self._release.set()

    def gets(self) -> list[dict]:
        with self._lock:
            return [e for e in self.events if e["method"] == "GET"]

    def posts(self) -> list[dict]:
        with self._lock:
            return [e for e in self.events if e["method"] == "POST"]

    def stop(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


_CMD_PROJECT_TOML = """\
[project]
id = "cmdproj"
default_branch = "main"
worktree_root = ".worktrees"
handoff_globs = ["handoff/*.md"]

[policy]

[notify]
ntfy_url = "http://127.0.0.1:{port}"
cmd_topic = "nyxloom-cmd"
token_env = "NTFY_TOKEN"
cmd_token_env = "NTFY_CMD_TOKEN"
"""


def _register_cmd_project(tmp_path, port: int):
    root = tmp_path / "cmd-repo"
    (root / ".nyxloom").mkdir(parents=True)
    (root / ".nyxloom" / "project.toml").write_text(_CMD_PROJECT_TOML.format(port=port))
    register_project("cmdproj", root)
    paths.ensure_layout("cmdproj")
    return root


def test_transport_reply_and_reconnect_carries_since(tmp_state, tmp_path, monkeypatch):
    # NTFY_URL (P39) is authoritative over this fixture's toml ntfy_url, so an
    # ambient one would point the listener at the real server, not _FakeNtfyServer.
    monkeypatch.delenv("NTFY_URL", raising=False)
    monkeypatch.setenv("NTFY_CMD_TOKEN", "read-tok")
    monkeypatch.setenv("NTFY_TOKEN", "write-tok")

    server = _FakeNtfyServer()
    _register_cmd_project(tmp_path, server.port)

    cl = CommandListener(load_registry(), poll_timeout=10)
    cl.start()
    try:
        assert server.first_get_seen.wait(timeout=5), "listener never connected"

        assert server.reply_seen.wait(timeout=5), "listener did not send a reply"
        posts = server.posts()
        assert posts, "listener did not send a reply"
        assert posts[0]["headers"].get("Authorization") == "Bearer write-tok"
        assert REPLY_TAG in posts[0]["headers"].get("Tags", "")
        # notify.send() posts the reply's plain body text (typed reply
        # from handle_message), never a re-serialized envelope.
        assert posts[0]["body"].decode("utf-8").startswith("cmdproj:")

        first_gets = server.gets()
        assert len(first_gets) == 1
        assert first_gets[0]["headers"].get("Authorization") == "Bearer read-tok"
        assert "since=0" in first_gets[0]["path"]

        # Release the first (still-open) long-poll connection -- from the
        # listener's perspective this ends the poll, forcing a reconnect.
        server.release_first_connection()

        assert server.second_get_seen.wait(timeout=5), "listener did not reconnect"
        gets = server.gets()
        assert len(gets) >= 2, "listener did not reconnect"
        assert "since=m1" in gets[1]["path"]
    finally:
        cl.stop()
        server.stop()


# =========================================================================
# logging-P05b: command dispatch -> DEBUG/INFO per §5; failures -> WARNING
# =========================================================================

def _read_log_records(path) -> list[dict]:
    if not path.exists():
        return []
    lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    return [json.loads(ln) for ln in lines]


def test_pause_and_resume_emit_info(tmp_state, sample_project, tmp_path):
    """§5: pause/resume are the canonical INFO example -- 'one line per
    decision that changed the world'."""
    from nyxloom import log as nyx_log

    log_dir = tmp_path / "logs"
    nyx_log.configure(level=nyx_log.INFO, log_dir=log_dir, console=False)
    try:
        cl = CommandListener(load_registry())
        cl.handle_message("pause demo", [])
        cl.handle_message("resume demo", [])

        records = _read_log_records(log_dir / "nyxloom.jsonl")
        paused = [r for r in records if r["msg"] == "project paused"]
        resumed = [r for r in records if r["msg"] == "project resumed"]
        assert len(paused) == 1 and paused[0]["level"] == "info"
        assert paused[0]["project"] == "demo"
        assert paused[0]["mode"] == "drain-handoffs"
        assert len(resumed) == 1 and resumed[0]["level"] == "info"
        assert resumed[0]["project"] == "demo"
    finally:
        nyx_log.configure(level=nyx_log.CRITICAL, log_dir=None, console=False)


def test_pause_unknown_mode_emits_warning(tmp_state, sample_project, tmp_path):
    """§5: a rejected control command (bad mode word) is a WARNING, and --
    non-hollow control -- distinct from the INFO a successful pause emits."""
    from nyxloom import log as nyx_log

    log_dir = tmp_path / "logs"
    nyx_log.configure(level=nyx_log.INFO, log_dir=log_dir, console=False)
    try:
        cl = CommandListener(load_registry())
        reply = cl.handle_message("pause demo bogus-mode", [])
        assert "unknown mode" in reply

        records = _read_log_records(log_dir / "nyxloom.jsonl")
        rejected = [r for r in records if r["msg"] == "pause command rejected"]
        assert len(rejected) == 1
        assert rejected[0]["level"] == "warning"
        assert rejected[0]["project"] == "demo"
        assert rejected[0]["mode"] == "bogus-mode"
        # The control: no "project paused" INFO was emitted for the rejection.
        assert not any(r["msg"] == "project paused" for r in records)
    finally:
        nyx_log.configure(level=nyx_log.CRITICAL, log_dir=None, console=False)


def test_transport_failure_logs_warning(tmp_state, tmp_path, monkeypatch):
    """§5: command dispatch failures -> WARNING. Points the listener at an
    unreachable (closed) port so `_listen_once` genuinely raises inside
    `_run`'s try/except, and asserts the real WARNING record.

    B25 (de-flaking): rather than sleep-polling the rendered JSONL log file
    for the record to appear, a threading.Event is set the INSTANT the real
    `commands.log.warning(...)` call fires -- `_Watcher` below forwards
    every call to the real bound logger unchanged (so the actual persisted
    record this test asserts on is byte-identical to before) and only
    additionally flips the Event when the specific transport-failure
    message is logged. This still drives the REAL `_run()` background
    thread and its REAL try/except around `_listen_once` -- only the
    "wait for the async side-effect" mechanism changed."""
    from nyxloom import commands as commands_mod
    from nyxloom import log as nyx_log

    monkeypatch.delenv("NTFY_URL", raising=False)
    monkeypatch.setenv("NTFY_CMD_TOKEN", "read-tok")

    root = tmp_path / "fail-repo"
    (root / ".nyxloom").mkdir(parents=True)
    (root / ".nyxloom" / "project.toml").write_text(_CMD_PROJECT_TOML.format(port=1))
    register_project("failproj", root)
    paths.ensure_layout("failproj")

    log_dir = tmp_path / "logs"
    nyx_log.configure(level=nyx_log.WARNING, log_dir=log_dir, console=False)

    warned = threading.Event()
    real_log = commands_mod.log

    class _Watcher:
        def __getattr__(self, name):
            return getattr(real_log, name)

        def warning(self, event, *args, **kwargs):
            real_log.warning(event, *args, **kwargs)
            if event == "command listener transport failed":
                warned.set()

    monkeypatch.setattr(commands_mod, "log", _Watcher())

    try:
        cl = CommandListener(load_registry(), poll_timeout=2)
        cl.BACKOFF_INITIAL = 0.02
        cl.BACKOFF_MAX = 0.1
        cl.start()
        try:
            assert warned.wait(timeout=5), "transport failure warning never fired"
        finally:
            cl.stop()

        records = _read_log_records(log_dir / "nyxloom.jsonl")
        failures = [r for r in records if r["msg"] == "command listener transport failed"]
        assert failures, "expected at least one transport-failure WARNING record"
        assert failures[0]["level"] == "warning"
    finally:
        nyx_log.configure(level=nyx_log.CRITICAL, log_dir=None, console=False)


def test_listener_start_stop_emit_info(sample_project, tmp_path):
    """§5: component lifecycle (start/stop) is an INFO-worthy decision,
    mirroring the daemon's own start/stop convention (§4.4)."""
    from nyxloom import log as nyx_log

    log_dir = tmp_path / "logs"
    nyx_log.configure(level=nyx_log.INFO, log_dir=log_dir, console=False)
    try:
        cl = CommandListener(load_registry())
        cl.start()
        cl.stop()

        records = _read_log_records(log_dir / "nyxloom.jsonl")
        assert any(r["msg"] == "command listener started" and r["level"] == "info" for r in records)
        assert any(r["msg"] == "command listener stopped" and r["level"] == "info" for r in records)
    finally:
        nyx_log.configure(level=nyx_log.CRITICAL, log_dir=None, console=False)


def test_status_and_digest_query_log_debug(tmp_state, sample_project, tmp_path):
    """§5: read-only queries (status/digest) are DEBUG, not INFO -- they
    don't change the world, unlike pause/resume above."""
    from nyxloom import log as nyx_log

    log_dir = tmp_path / "logs"
    nyx_log.configure(level=nyx_log.DEBUG, log_dir=log_dir, console=False)
    try:
        cl = CommandListener(load_registry())
        cl.handle_message("status demo", [])
        cl.handle_message("digest demo", [])

        records = _read_log_records(log_dir / "nyxloom.jsonl")
        assert any(r["msg"] == "status queried" and r["level"] == "debug"
                   and r["project"] == "demo" for r in records)
        assert any(r["msg"] == "digest queried" and r["level"] == "debug"
                   and r["project"] == "demo" for r in records)
    finally:
        nyx_log.configure(level=nyx_log.CRITICAL, log_dir=None, console=False)


def test_unmatched_and_missing_project_commands_log_debug(sample_project, tmp_path):
    """§5: benign command-parse misses (no verb match, missing/unknown
    project) are DEBUG guard-evaluation notes, not WARNING/ERROR noise."""
    from nyxloom import log as nyx_log

    log_dir = tmp_path / "logs"
    nyx_log.configure(level=nyx_log.DEBUG, log_dir=log_dir, console=False)
    try:
        cl = CommandListener(load_registry())
        cl.handle_message("rm -rf /", [])
        cl.handle_message("status", [])
        cl.handle_message("status nope", [])

        records = _read_log_records(log_dir / "nyxloom.jsonl")
        assert any(r["msg"] == "command unmatched" for r in records)
        assert any(r["msg"] == "command missing project" and r["verb"] == "status" for r in records)
        assert any(r["msg"] == "command unknown project" and r["project"] == "nope" for r in records)
        assert all(r["level"] == "debug" for r in records)
    finally:
        nyx_log.configure(level=nyx_log.CRITICAL, log_dir=None, console=False)


# =========================================================================
# Oracle 6: injection -- hostile prose appended after a valid verb is
# rejected outright (regex has no such form), never echoed in the reply.
# =========================================================================

def test_hostile_prose_after_verb_is_rejected(sample_project):
    cl = CommandListener(load_registry())
    reply = cl.handle_message("help EVILPROSE", [])
    assert reply == UNKNOWN_REPLY
    assert "EVILPROSE" not in reply


def test_hostile_prose_as_project_arg_is_rejected(sample_project):
    cl = CommandListener(load_registry())
    reply = cl.handle_message("resume demo AND EXTRA STUFF", [])
    assert reply == UNKNOWN_REPLY
    assert "EXTRA" not in reply
    assert "STUFF" not in reply


# =========================================================================
# Extra coverage: digest verb (not separately oracle-numbered, but part
# of the owned interface / help text contract).
# =========================================================================

def test_digest_verb_uses_notify_digest(tmp_state, sample_project):
    storage.append_event(
        "demo", actor=Actor(ActorKind.TICK, "tick"),
        type=EventType.MERGE_RECORDED, payload={"merge_commit": "abc123"},
        task_id="demo-P01-sample",
    )
    cl = CommandListener(load_registry())
    reply = cl.handle_message("digest demo", [])
    assert "MERGE_RECORDED" in reply


def test_digest_verb_truncates_to_max_chars(tmp_state, sample_project, monkeypatch):
    monkeypatch.setattr(
        "nyxloom.commands.notify.digest",
        lambda cfg, project, since: "x" * 3000,
    )
    cl = CommandListener(load_registry())
    reply = cl.handle_message("digest demo", [])
    assert len(reply) == 1500


def test_digest_verb_empty_digest_has_fixed_reply(tmp_state, sample_project, monkeypatch):
    monkeypatch.setattr(
        "nyxloom.commands.notify.digest",
        lambda cfg, project, since: "",
    )
    cl = CommandListener(load_registry())
    reply = cl.handle_message("digest demo", [])
    assert reply == "no recent activity"
