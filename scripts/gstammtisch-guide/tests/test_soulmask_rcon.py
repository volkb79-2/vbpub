"""R0 tests for soulmask_rcon.py — the Source RCON protocol engine.

Fast and self-contained: talks to a real local FakeRconServer (plain
localhost TCP, no docker/nsenter/root involved — see fake_rcon_server.py),
and drives the module's own main() in-process (no subprocess spawn) so
these stay fast while still exercising the real CLI code paths.
"""
from __future__ import annotations

import importlib.util
import io
import json
import sys
from pathlib import Path

import pytest

from fake_rcon_server import FakeRconServer

HERE = Path(__file__).resolve().parent
MODULE_PATH = HERE.parent / "files" / "usr" / "local" / "sbin" / "soulmask_rcon.py"


def load_module():
    spec = importlib.util.spec_from_file_location("soulmask_rcon", MODULE_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


rcon = load_module()


@pytest.fixture
def server():
    s = FakeRconServer(password="pw123")
    yield s
    s.stop()


def run_main(argv, stdin_text=""):
    """Call rcon.main() in-process, capturing stdout/stderr and feeding stdin."""
    old_stdin, old_stdout, old_stderr = sys.stdin, sys.stdout, sys.stderr
    sys.stdin = io.StringIO(stdin_text)
    sys.stdout = io.StringIO()
    sys.stderr = io.StringIO()
    try:
        rc = rcon.main(argv)
        return rc, sys.stdout.getvalue(), sys.stderr.getvalue()
    finally:
        sys.stdin, sys.stdout, sys.stderr = old_stdin, old_stdout, old_stderr


# ── protocol-level (direct function calls) ──────────────────────────────────

def test_connect_and_exec_round_trip(server):
    sock = rcon.connect("127.0.0.1", server.port, "pw123")
    try:
        assert rcon.exec_command(sock, "ping") == "echo:ping"
        assert rcon.exec_command(sock, "ping2", req_id=99) == "echo:ping2"
    finally:
        sock.close()


def test_connect_wrong_password_raises(server):
    with pytest.raises(rcon.RconError, match="authentication failed"):
        rcon.connect("127.0.0.1", server.port, "wrong")


def test_connect_refused_raises_oserror():
    # Nothing listening on this port.
    with pytest.raises(OSError):
        rcon.connect("127.0.0.1", 1, "pw123", timeout=0.5)


def test_pack_unpack_round_trip():
    # exercise the wire format directly via a loopback socket pair
    import socket
    a, b = socket.socketpair()
    try:
        a.sendall(rcon._pack(7, 2, "hello"))
        req_id, pkt_type, body = rcon._recv_packet(b)
        assert (req_id, pkt_type, body) == (7, 2, "hello")
    finally:
        a.close()
        b.close()


# ── one-shot mode ────────────────────────────────────────────────────────────

def test_one_shot_prints_reply(server, capsys):
    rc = rcon.main(["--port", str(server.port), "--password", "pw123", "ServerFPS"])
    out = capsys.readouterr().out
    assert rc == 0
    assert out.strip() == "echo:ServerFPS"


def test_one_shot_connection_test_only_no_output(server, capsys):
    rc = rcon.main(["--port", str(server.port), "--password", "pw123"])
    out = capsys.readouterr().out
    assert rc == 0
    assert out == ""


def test_one_shot_auth_failure_is_reported(server, capsys):
    rc = rcon.main(["--port", str(server.port), "--password", "WRONG", "ServerFPS"])
    err = capsys.readouterr().err
    assert rc == 1
    assert "authentication failed" in err


def test_one_shot_multiword_command_is_joined(server):
    received = []

    def responder(cmd):
        received.append(cmd)
        return "ok"

    server.responder = responder
    rcon.main(["--port", str(server.port), "--password", "pw123",
               "broadcast", "Reboot", "in", "60s"])
    assert received == ["broadcast Reboot in 60s"]


# ── --interactive mode (plain text loop) ─────────────────────────────────────

def test_interactive_mode_plain_text_replies(server):
    rc, out, err = run_main(
        ["--port", str(server.port), "--password", "pw123", "--interactive"],
        stdin_text="cmd1\ncmd2\n",
    )
    assert rc == 0
    assert out.splitlines() == ["echo:cmd1", "echo:cmd2"]
    assert "connected" in err


def test_interactive_and_relay_are_mutually_exclusive(server):
    with pytest.raises(SystemExit):
        rcon.main(["--port", str(server.port), "--password", "pw123",
                   "--interactive", "--relay"])


def test_relay_rejects_positional_command(server):
    with pytest.raises(SystemExit):
        rcon.main(["--port", str(server.port), "--password", "pw123",
                   "--relay", "ServerFPS"])


# ── --relay mode (JSON-line loop) ────────────────────────────────────────────

def test_relay_mode_json_lines(server):
    rc, out, err = run_main(
        ["--port", str(server.port), "--password", "pw123", "--relay"],
        stdin_text="ServerFPS\nServerFPS\n",
    )
    assert rc == 0
    lines = [json.loads(line) for line in out.splitlines()]
    assert lines[0] == {"ok": True, "event": "connected"}
    assert lines[1] == {"ok": True, "reply": "echo:ServerFPS"}
    assert lines[2] == {"ok": True, "reply": "echo:ServerFPS"}


def test_relay_mode_auth_failure_reported_as_json(server):
    rc, out, err = run_main(
        ["--port", str(server.port), "--password", "WRONG", "--relay"],
        stdin_text="",
    )
    assert rc == 1
    event = json.loads(out.strip())
    assert event["ok"] is False
    assert "authentication failed" in event["error"]


def test_relay_mode_reconnects_after_server_drops_connection():
    # drop_after=1: the fake server closes the connection right after
    # replying to the 1st command on it (reconnects get one fresh command
    # each before dropping again too). 3 commands over 2 connections:
    # cmd1 succeeds, cmd2 discovers the drop (error + reconnect), cmd3
    # succeeds on the new connection — proving the reconnect path works.
    server = FakeRconServer(password="pw123", drop_after=1)
    try:
        rc, out, err = run_main(
            ["--port", str(server.port), "--password", "pw123", "--relay"],
            stdin_text="ServerFPS\nServerFPS\nServerFPS\n",
        )
        lines = [json.loads(line) for line in out.splitlines()]
        assert lines[0] == {"ok": True, "event": "connected"}
        assert lines[1] == {"ok": True, "reply": "echo:ServerFPS"}
        assert lines[2]["ok"] is False
        assert lines[3] == {"ok": True, "reply": "echo:ServerFPS"}
        assert rc == 0
    finally:
        server.stop()
