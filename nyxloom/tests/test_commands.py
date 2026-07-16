"""Tests for nyxloom.commands (P12: ntfy inbound command listener)."""

from __future__ import annotations

import http.server
import json
import threading
import time

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
    for verb in ("help", "status", "pause", "unpause", "digest"):
        assert verb in reply
    assert reply == HELP_TEXT


def test_garbage_command_is_rejected(sample_project):
    cl = CommandListener(load_registry())
    assert cl.handle_message("rm -rf /", []) == UNKNOWN_REPLY


def test_shell_metacharacters_rejected_by_strict_regex(sample_project):
    cl = CommandListener(load_registry())
    assert cl.handle_message("unpause; rm x", []) == UNKNOWN_REPLY


# =========================================================================
# Oracle 2: pause / unpause -- CLI-equivalent flag + event semantics
# =========================================================================

def test_unpause_clears_flag_and_appends_cleared_event(tmp_state, sample_project):
    flag = paths.pause_flag("demo")
    flag.parent.mkdir(parents=True, exist_ok=True)
    flag.touch()
    assert flag.exists()

    cl = CommandListener(load_registry())
    reply = cl.handle_message("unpause demo", [])

    assert "unpaused" in reply
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
    assert cl.handle_message("unpause demo", [REPLY_TAG]) is None
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
        server = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, fmt, *args):  # noqa: A002
                pass

            def do_GET(self):
                with server._lock:
                    server.events.append({"method": "GET", "path": self.path,
                                           "headers": dict(self.headers)})
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
                # Hold the connection open (long-poll) until released.
                server._release.wait(timeout=5)

            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(length)
                with server._lock:
                    server.events.append({"method": "POST", "path": self.path,
                                           "headers": dict(self.headers),
                                           "body": body})
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
    monkeypatch.setenv("NTFY_CMD_TOKEN", "read-tok")
    monkeypatch.setenv("NTFY_TOKEN", "write-tok")

    server = _FakeNtfyServer()
    _register_cmd_project(tmp_path, server.port)

    cl = CommandListener(load_registry(), poll_timeout=10)
    cl.start()
    try:
        assert server.first_get_seen.wait(timeout=5), "listener never connected"

        deadline = time.time() + 5
        while time.time() < deadline and not server.posts():
            time.sleep(0.05)
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

        deadline = time.time() + 5
        while time.time() < deadline and len(server.gets()) < 2:
            time.sleep(0.05)
        gets = server.gets()
        assert len(gets) >= 2, "listener did not reconnect"
        assert "since=m1" in gets[1]["path"]
    finally:
        cl.stop()
        server.stop()


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
    reply = cl.handle_message("unpause demo AND EXTRA STUFF", [])
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
