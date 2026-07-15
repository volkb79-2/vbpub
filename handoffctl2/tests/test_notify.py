"""Tests for P06 notifications module."""

from __future__ import annotations

import http.server
import json
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from handoffctl import storage, paths
from handoffctl.config import NotifyConfig, ProjectConfig
from handoffctl.notify import digest, notification_for, notify_event, send
from handoffctl.types import (
    Actor, ActorKind, Event, EventType, TaskStateFile, TaskState, utc_now,
)


# =========================================================================
# Oracle 1: notification_for shape tests
# =========================================================================

def test_notification_for_decision_opened():
    """Oracle 1: DECISION_OPENED shape."""
    ev = Event(
        schema_version=1,
        sequence=1,
        timestamp=utc_now(),
        project="demo",
        actor=Actor(ActorKind.OPERATOR, "op1"),
        type=EventType.DECISION_OPENED,
        payload={},
        decision_id="D-013",
    )
    note = notification_for(ev)
    assert note is not None
    assert note["title"] == "Decision needed: D-013"
    assert note["priority"] == 5
    assert note["click"].endswith("/www/index.html")


def test_notification_for_task_blocked():
    """Oracle 1: TASK_BLOCKED shape."""
    ev = Event(
        schema_version=1,
        sequence=2,
        timestamp=utc_now(),
        project="demo",
        actor=Actor(ActorKind.OPERATOR, "op1"),
        type=EventType.TASK_BLOCKED,
        payload={},
        task_id="t1",
    )
    note = notification_for(ev)
    assert note is not None
    assert note["title"] == "demo/t1 BLOCKED"
    assert note["priority"] == 4
    assert note["click"].endswith("/www/task/demo/t1.html")


def test_notification_for_spec_attention():
    """Oracle 1: SPEC_ATTENTION shape; reason from payload."""
    ev = Event(
        schema_version=1,
        sequence=3,
        timestamp=utc_now(),
        project="demo",
        actor=Actor(ActorKind.TICK, "tick1"),
        type=EventType.SPEC_ATTENTION,
        payload={"reason": "ratchet"},
    )
    note = notification_for(ev)
    assert note is not None
    assert "ratchet" in note["title"]
    assert note["priority"] == 4


def test_notification_for_wave_closed():
    """Oracle 1: WAVE_CLOSED; body contains count."""
    ev = Event(
        schema_version=1,
        sequence=4,
        timestamp=utc_now(),
        project="demo",
        actor=Actor(ActorKind.OPERATOR, "op1"),
        type=EventType.WAVE_CLOSED,
        payload={"task_ids": ["a", "b"]},
    )
    note = notification_for(ev)
    assert note is not None
    assert "2" in note["body"]
    assert note["priority"] == 3


def test_notification_for_unhandled_type():
    """Oracle 1: unhandled type (ARTIFACT_REGISTERED) returns None."""
    ev = Event(
        schema_version=1,
        sequence=5,
        timestamp=utc_now(),
        project="demo",
        actor=Actor(ActorKind.OPERATOR, "op1"),
        type=EventType.ARTIFACT_REGISTERED,
        payload={},
    )
    note = notification_for(ev)
    assert note is None


# =========================================================================
# Oracle 2: Injection boundary — hostile payload strings must not leak
# =========================================================================

def test_injection_boundary_task_blocked():
    """Oracle 2: TASK_BLOCKED with hostile payload; evil strings must not appear."""
    ev = Event(
        schema_version=1,
        sequence=6,
        timestamp=utc_now(),
        project="demo",
        actor=Actor(ActorKind.OPERATOR, "op1"),
        type=EventType.TASK_BLOCKED,
        payload={
            "blocker": {
                "type": "contract",
                "unblock_condition": "EVIL1",
                "detail": "EVIL2",
            },
            "notes": "EVIL3",
        },
        task_id="t1",
    )
    note = notification_for(ev)
    assert note is not None

    # Check that none of the evil strings appear in title, body, or any header
    for evil in ["EVIL1", "EVIL2", "EVIL3"]:
        assert evil not in note.get("title", "")
        assert evil not in note.get("body", "")
        assert evil not in note.get("click", "")
        for tag in note.get("tags", []):
            assert evil not in tag


def test_injection_boundary_needs_operator():
    """Oracle 2: NEEDS_OPERATOR with hostile detail; evil must not appear."""
    ev = Event(
        schema_version=1,
        sequence=7,
        timestamp=utc_now(),
        project="demo",
        actor=Actor(ActorKind.OPERATOR, "op1"),
        type=EventType.NEEDS_OPERATOR,
        payload={"detail": "EVIL4"},
    )
    note = notification_for(ev)
    assert note is not None
    assert "EVIL4" not in note.get("title", "")
    assert "EVIL4" not in note.get("body", "")
    assert "EVIL4" not in note.get("click", "")


# =========================================================================
# Oracle 3: send function — ntfy and webhook integration
# =========================================================================

def test_send_ntfy_success():
    """Oracle 3: send via ntfy returns (True, ...) on 200 OK."""
    # Start a local HTTP server to capture the POST
    received = {"request": None}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            received["request"] = {
                "path": self.path,
                "headers": dict(self.headers),
                "body": self.rfile.read(int(self.headers.get("Content-Length", 0))),
            }
            self.send_response(200)
            self.end_headers()

        def log_message(self, format, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_port
    thread = threading.Thread(target=server.handle_request, daemon=True)
    thread.start()

    nc = NotifyConfig(
        ntfy_url=f"http://127.0.0.1:{port}",
        ntfy_topic="alerts",
    )
    note = {
        "title": "Test",
        "body": "Test body",
        "click": "http://example.com",
        "priority": 4,
        "tags": ["test"],
    }

    ok, detail = send(nc, note)
    thread.join(timeout=2)
    server.server_close()

    assert ok is True
    assert received["request"] is not None
    assert received["request"]["path"] == "/alerts"
    assert received["request"]["headers"]["Title"] == "Test"
    assert received["request"]["headers"]["Priority"] == "4"
    assert received["request"]["body"] == b"Test body"


def test_send_ntfy_server_error():
    """Oracle 3: ntfy server returning 500 returns (False, ...)."""
    received = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            self.send_response(500)
            self.end_headers()

        def log_message(self, format, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_port
    thread = threading.Thread(target=server.handle_request, daemon=True)
    thread.start()

    nc = NotifyConfig(
        ntfy_url=f"http://127.0.0.1:{port}",
        ntfy_topic="alerts",
    )
    note = {"title": "Test", "body": "Test body", "click": "http://example.com", "priority": 4, "tags": []}

    ok, detail = send(nc, note)
    thread.join(timeout=2)
    server.server_close()

    assert ok is False


def test_send_connection_refused():
    """Oracle 3: connection refused returns (False, ...) and does NOT raise."""
    nc = NotifyConfig(
        ntfy_url="http://127.0.0.1:1",  # Closed port
        ntfy_topic="alerts",
    )
    note = {"title": "Test", "body": "Test body", "click": "http://example.com", "priority": 4, "tags": []}

    # Should not raise; must return (False, ...) within 1s
    start = time.time()
    ok, detail = send(nc, note)
    elapsed = time.time() - start

    assert ok is False
    assert elapsed < 1.0  # Must not hang waiting for timeout


def test_send_webhook_fallback():
    """Oracle 3: ntfy fails, webhook fallback succeeds."""
    received = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            received["body"] = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            self.send_response(200)
            self.end_headers()

        def log_message(self, format, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    webhook_port = server.server_port
    thread = threading.Thread(target=server.handle_request, daemon=True)
    thread.start()

    nc = NotifyConfig(
        ntfy_url="http://127.0.0.1:1",  # Closed port (will fail)
        ntfy_topic="alerts",
        webhook_url=f"http://127.0.0.1:{webhook_port}/webhook",
    )
    note = {
        "title": "Test",
        "body": "Test body",
        "click": "http://example.com",
        "priority": 4,
        "tags": ["test"],
    }

    ok, detail = send(nc, note)
    thread.join(timeout=2)
    server.server_close()

    assert ok is True
    assert "webhook" in detail.lower()
    # Webhook should have received JSON
    assert received.get("body") is not None
    webhook_data = json.loads(received["body"])
    assert webhook_data["title"] == "Test"


# =========================================================================
# Oracle 4: notify_event — appends notification events
# =========================================================================

def test_notify_event_task_blocked_with_send_success(tmp_state, sample_project, monkeypatch):
    """Oracle 4: TASK_BLOCKED event triggers NOTIFICATION_REQUESTED then NOTIFICATION_DELIVERED."""
    # Configure notify channels
    cfg = sample_project
    cfg.notify.ntfy_url = "http://127.0.0.1:9999"
    cfg.notify.ntfy_topic = "test"

    # Monkeypatch send to return success
    monkeypatch.setattr("handoffctl.notify.send", lambda nc, note: (True, "ok"))

    # Create a task and a TASK_BLOCKED event
    states = storage.list_states("demo")
    if not states:
        tsf = TaskStateFile(
            schema_version=1,
            task_id="t1",
            project="demo",
            state=TaskState.QUEUED,
            since=utc_now(),
        )
        storage.save_state(tsf)
        states = {"t1": tsf}

    ev = storage.append_event(
        "demo",
        actor=Actor(ActorKind.OPERATOR, "op1"),
        type=EventType.TASK_BLOCKED,
        payload={},
        task_id="t1",
    )

    # Call notify_event
    notify_event(cfg, states, ev)

    # Check that NOTIFICATION_REQUESTED and NOTIFICATION_DELIVERED were appended
    events = list(storage.iter_events("demo", since=0))
    notif_events = [e for e in events if e.type in (EventType.NOTIFICATION_REQUESTED, EventType.NOTIFICATION_DELIVERED)]

    assert len(notif_events) >= 2
    assert notif_events[0].type == EventType.NOTIFICATION_REQUESTED
    assert notif_events[1].type == EventType.NOTIFICATION_DELIVERED
    # Both should carry the task_id
    assert notif_events[0].task_id == "t1"
    assert notif_events[1].task_id == "t1"


def test_notify_event_send_failure(tmp_state, sample_project, monkeypatch):
    """Oracle 4: send returns (False, 'boom') triggers NOTIFICATION_FAILED."""
    # Configure notify channels
    cfg = sample_project
    cfg.notify.ntfy_url = "http://127.0.0.1:9999"
    cfg.notify.ntfy_topic = "test"

    monkeypatch.setattr("handoffctl.notify.send", lambda nc, note: (False, "boom"))

    states = storage.list_states("demo")
    if not states:
        tsf = TaskStateFile(
            schema_version=1,
            task_id="t1",
            project="demo",
            state=TaskState.QUEUED,
            since=utc_now(),
        )
        storage.save_state(tsf)
        states = {"t1": tsf}

    ev = storage.append_event(
        "demo",
        actor=Actor(ActorKind.OPERATOR, "op1"),
        type=EventType.TASK_BLOCKED,
        payload={},
        task_id="t1",
    )

    notify_event(cfg, states, ev)

    # Check for NOTIFICATION_FAILED
    events = list(storage.iter_events("demo", since=0))
    failed_events = [e for e in events if e.type == EventType.NOTIFICATION_FAILED]

    assert len(failed_events) >= 1
    assert failed_events[0].payload.get("detail") == "boom"


def test_notify_event_recursion_guard(tmp_state, sample_project):
    """Oracle 4: NOTIFICATION_DELIVERED input triggers no new events (recursion guard)."""
    states = storage.list_states("demo")

    # Create a NOTIFICATION_DELIVERED event
    ev = storage.append_event(
        "demo",
        actor=Actor(ActorKind.NOTIFIER, "notify"),
        type=EventType.NOTIFICATION_DELIVERED,
        payload={"detail": "ok"},
        task_id="t1",
    )

    # Count events before
    events_before = list(storage.iter_events("demo", since=0))
    count_before = len(events_before)

    # Call notify_event — should return immediately (recursion guard)
    notify_event(sample_project, states, ev)

    # Count events after — should be the same
    events_after = list(storage.iter_events("demo", since=0))
    count_after = len(events_after)

    assert count_before == count_after


def test_notify_event_not_in_push_classes(tmp_state, sample_project):
    """Oracle 4: event type not in push_classes does not trigger notification."""
    states = storage.list_states("demo")

    # Create an ATTEMPT_CREATED event (not in push_classes)
    ev = storage.append_event(
        "demo",
        actor=Actor(ActorKind.TICK, "tick1"),
        type=EventType.ATTEMPT_CREATED,
        payload={},
        task_id="t1",
    )

    # Count events before
    events_before = list(storage.iter_events("demo", since=0))
    count_before = len(events_before)

    # Call notify_event
    notify_event(sample_project, states, ev)

    # Count events after — should be the same (no notification appended)
    events_after = list(storage.iter_events("demo", since=0))
    count_after = len(events_after)

    assert count_before == count_after


def test_notify_event_both_unconfigured(tmp_state, monkeypatch):
    """Oracle 4: both ntfy and webhook unconfigured doesn't call send; uses 'unconfigured' detail."""
    states = storage.list_states("demo")
    if not states:
        tsf = TaskStateFile(
            schema_version=1,
            task_id="t1",
            project="demo",
            state=TaskState.QUEUED,
            since=utc_now(),
        )
        storage.save_state(tsf)
        states = {"t1": tsf}

    # Create a config with no notification channels
    cfg = ProjectConfig(
        project_id="demo",
        root=Path("/tmp/demo"),
        default_branch="main",
        worktree_root=".worktrees",
        handoff_globs=["handoff/*.md"],
        gates={},
        mutexes={},
        policy=MagicMock(),
        notify=NotifyConfig(ntfy_url=None, webhook_url=None),
    )

    # Monkeypatch send to raise if called
    send_called = False

    def mock_send(nc, note):
        nonlocal send_called
        send_called = True
        raise AssertionError("send should not be called when both are unconfigured")

    monkeypatch.setattr("handoffctl.notify.send", mock_send)

    ev = storage.append_event(
        "demo",
        actor=Actor(ActorKind.OPERATOR, "op1"),
        type=EventType.TASK_BLOCKED,
        payload={},
        task_id="t1",
    )

    # Call notify_event
    notify_event(cfg, states, ev)

    # send should NOT have been called
    assert send_called is False

    # Check for NOTIFICATION_FAILED with 'unconfigured' detail
    events = list(storage.iter_events("demo", since=0))
    failed_events = [e for e in events if e.type == EventType.NOTIFICATION_FAILED]

    assert len(failed_events) >= 1
    assert failed_events[0].payload.get("detail") == "unconfigured"


# =========================================================================
# Oracle 5: digest function
# =========================================================================

def test_digest_counts_and_tasks(tmp_state, sample_project):
    """Oracle 5: digest reports MERGE_RECORDED count, merged task IDs, decision count."""
    # Create events:
    # 2x MERGE_RECORDED for t1 and t2 (different task_ids)
    # 1x TASK_TRANSITIONED
    # 1x DECISION_OPENED (left open)

    storage.append_event(
        "demo",
        actor=Actor(ActorKind.OPERATOR, "op1"),
        type=EventType.MERGE_RECORDED,
        payload={"merge_commit": "abc123"},
        task_id="t1",
    )

    storage.append_event(
        "demo",
        actor=Actor(ActorKind.OPERATOR, "op1"),
        type=EventType.MERGE_RECORDED,
        payload={"merge_commit": "def456"},
        task_id="t2",
    )

    storage.append_event(
        "demo",
        actor=Actor(ActorKind.TICK, "tick1"),
        type=EventType.TASK_TRANSITIONED,
        payload={"from": "QUEUED", "to": "ACTIVE"},
        task_id="t3",
    )

    storage.append_event(
        "demo",
        actor=Actor(ActorKind.OPERATOR, "op1"),
        type=EventType.DECISION_OPENED,
        payload={},
        decision_id="D-001",
    )

    result = digest(sample_project, "demo", 0)

    assert "MERGE_RECORDED: 2" in result
    assert "t1" in result
    assert "t2" in result
    assert "decisions open: 1" in result
    assert "TASK_TRANSITIONED: 1" in result


def test_digest_since_seq_filter(tmp_state, sample_project):
    """Oracle 5: digest filters by since_seq; only counts events after the given sequence."""
    seq1 = storage.append_event(
        "demo",
        actor=Actor(ActorKind.OPERATOR, "op1"),
        type=EventType.MERGE_RECORDED,
        payload={"merge_commit": "abc123"},
        task_id="t1",
    ).sequence

    seq2 = storage.append_event(
        "demo",
        actor=Actor(ActorKind.OPERATOR, "op1"),
        type=EventType.MERGE_RECORDED,
        payload={"merge_commit": "def456"},
        task_id="t2",
    ).sequence

    # digest from seq1 onwards should see only one merge (the one after seq1)
    result = digest(sample_project, "demo", since_seq=seq1)

    assert "MERGE_RECORDED: 1" in result
    assert "t2" in result


def test_digest_determinism(tmp_state, sample_project):
    """Oracle 5: two calls with same data produce identical string."""
    storage.append_event(
        "demo",
        actor=Actor(ActorKind.OPERATOR, "op1"),
        type=EventType.MERGE_RECORDED,
        payload={"merge_commit": "abc123"},
        task_id="t1",
    )

    storage.append_event(
        "demo",
        actor=Actor(ActorKind.OPERATOR, "op1"),
        type=EventType.MERGE_RECORDED,
        payload={"merge_commit": "def456"},
        task_id="t2",
    )

    result1 = digest(sample_project, "demo", 0)
    result2 = digest(sample_project, "demo", 0)

    assert result1 == result2


def test_digest_empty(tmp_state, sample_project):
    """Oracle 5: digest with no relevant events returns empty string."""
    result = digest(sample_project, "demo", 0)
    assert result == ""


# =========================================================================
# Additional integration tests
# =========================================================================

def test_notification_for_budget_warning():
    """Test BUDGET_WARNING notification shape."""
    ev = Event(
        schema_version=1,
        sequence=1,
        timestamp=utc_now(),
        project="demo",
        actor=Actor(ActorKind.TICK, "tick1"),
        type=EventType.BUDGET_WARNING,
        payload={"remaining": 42.5},
    )
    note = notification_for(ev)
    assert note is not None
    assert note["priority"] == 4
    assert "42.5" in note["body"]


def test_notification_for_budget_exhausted():
    """Test BUDGET_EXHAUSTED notification shape."""
    ev = Event(
        schema_version=1,
        sequence=1,
        timestamp=utc_now(),
        project="demo",
        actor=Actor(ActorKind.TICK, "tick1"),
        type=EventType.BUDGET_EXHAUSTED,
        payload={},
    )
    note = notification_for(ev)
    assert note is not None
    assert note["priority"] == 5
    assert note["title"] == "Budget exhausted"


class TestTokenAuth:
    def test_bearer_token_header_from_env(self, monkeypatch):
        """Deny-all ntfy servers need the token; value comes from env only."""
        import http.server, threading, json as _json
        from handoffctl.config import NotifyConfig
        from handoffctl import notify

        captured = {}

        class H(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                captured["auth"] = self.headers.get("Authorization")
                self.send_response(200); self.end_headers()
            def log_message(self, *a): pass

        srv = http.server.HTTPServer(("127.0.0.1", 0), H)
        t = threading.Thread(target=srv.serve_forever, daemon=True); t.start()
        try:
            nc = NotifyConfig(ntfy_url=f"http://127.0.0.1:{srv.server_port}",
                              ntfy_topic="t")
            monkeypatch.setenv("NTFY_TOKEN", "tk_secret123")
            ok, _ = notify.send(nc, {"title": "x", "body": "y", "click": "",
                                     "priority": 3, "tags": []})
            assert ok and captured["auth"] == "Bearer tk_secret123"
            monkeypatch.delenv("NTFY_TOKEN")
            notify.send(nc, {"title": "x", "body": "y", "click": "",
                             "priority": 3, "tags": []})
            assert captured["auth"] is None
        finally:
            srv.shutdown()
