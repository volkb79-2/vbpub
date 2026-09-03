#!/usr/bin/env python3
"""soulmask_rcon.py — Source RCON protocol engine for a Soulmask WSServer.

Must run INSIDE the target container's network namespace — RCON is
loopback-whitelisted, so the caller is expected to invoke this via:

  nsenter --net=/proc/<WSServer-PID>/ns/net -- python3 soulmask_rcon.py \
      --port <RCON_PORT> --password <RCON_PASSWORD> <command...>

Three modes:
  one-shot (default): connect, auth, run ONE command (the positional args
    joined with spaces — omit them for a connection test only), print the
    reply, exit. Used by exec-soulmask-rcon.py: a fresh connection per
    invocation is fine there, it's a human-driven manual tool, not a poll
    loop.
  --interactive: connect, auth once, then read one command per line from
    stdin and print each plain-text reply, until stdin closes. Used by
    exec-soulmask-rcon.py's -i flag for a REPL against a real terminal.
  --relay: same persistent loop as --interactive, but replies are
    JSON-encoded ({"ok": true/false, "reply"/"error": ...}) so a parent
    process can frame them reliably even if a reply contains embedded
    newlines. Used by soulmask-monitor.py, the one process in this project
    that actually polls RCON continuously: it spawns this in --relay mode
    ONCE per monitored server and reuses the connection for the monitor's
    whole lifetime, instead of paying a fresh connect+auth (and the
    server's own close-time log line — see below) on every tick. The child
    dies naturally when its parent exits and closes its stdin — no daemon
    lifecycle to manage.

Why not itzg/rcon-cli: same wire protocol, but there is no way to keep a
connection open across separate script invocations without driving its
interactive REPL's human-oriented stdout, which has no reliable per-reply
framing. This implements the (~80-line) protocol directly instead, adapted
from scripts/damon-analysis/rcon_probe.py — already proven against this
game's RCON server, see SOULMASK.md 4b.

Confirmed live against the production server (2026-08-27): every RCON
connection — regardless of client, and regardless of how or whether the
client half-closes first — ends with the server logging "Error: Receive
error: SE_EWOULDBLOCK" / "Closing connection" a couple of seconds after
its own idle-connection check fires. That is the server's own teardown
path misreporting a normal non-blocking-recv condition as an error, not a
symptom of a badly-behaved client. A persistent connection pays that cost
once per connection instead of once per command — it doesn't eliminate
the log line, it just amortizes it.
"""
from __future__ import annotations

import argparse
import json
import socket
import struct
import sys

_AUTH = 3
_EXEC = 2


class RconError(Exception):
    pass


def _pack(req_id: int, pkt_type: int, body: str) -> bytes:
    # req_id is a signed int32 on the wire: the auth-failure sentinel is -1,
    # which an unsigned format can neither pack nor, when unpacked, ever
    # compare equal to -1 (it decodes as 4294967295 instead).
    payload = body.encode() + b"\x00\x00"
    header = struct.pack("<Iii", 4 + 4 + len(payload), req_id, pkt_type)
    return header + payload


def _recvn(sock: socket.socket, n: int) -> bytes:
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise RconError("connection closed by server")
        buf += chunk
    return buf


def _recv_packet(sock: socket.socket) -> tuple[int, int, str]:
    length = struct.unpack("<I", _recvn(sock, 4))[0]
    data = _recvn(sock, length)
    req_id, pkt_type = struct.unpack("<ii", data[:8])
    return req_id, pkt_type, data[8:-2].decode("utf-8", errors="replace")


def connect(host: str, port: int, password: str, timeout: float = 5.0) -> socket.socket:
    sock = socket.create_connection((host, port), timeout=timeout)
    sock.settimeout(timeout)
    sock.sendall(_pack(1, _AUTH, password))
    req_id, _, _ = _recv_packet(sock)
    if req_id == -1:
        sock.close()
        raise RconError("authentication failed (wrong RCON_PASSWORD?)")
    return sock


def exec_command(sock: socket.socket, cmd: str, req_id: int = 2) -> str:
    sock.sendall(_pack(req_id, _EXEC, cmd))
    _, _, body = _recv_packet(sock)
    return body


def _run_one_shot(args: argparse.Namespace) -> int:
    cmd = " ".join(args.command)
    try:
        sock = connect(args.host, args.port, args.password)
    except (OSError, RconError) as e:
        print(f"soulmask_rcon: {e}", file=sys.stderr)
        return 1
    try:
        if cmd:
            print(exec_command(sock, cmd))
    except (OSError, RconError) as e:
        print(f"soulmask_rcon: {e}", file=sys.stderr)
        return 1
    finally:
        sock.close()
    return 0


def _run_loop(args: argparse.Namespace, *, as_json: bool) -> int:
    try:
        sock = connect(args.host, args.port, args.password)
    except (OSError, RconError) as e:
        if as_json:
            print(json.dumps({"ok": False, "error": str(e)}), flush=True)
        else:
            print(f"soulmask_rcon: {e}", file=sys.stderr)
        return 1
    if as_json:
        print(json.dumps({"ok": True, "event": "connected"}), flush=True)
    else:
        print("connected. type commands (Ctrl-D to quit):", file=sys.stderr)

    req_id = 2
    try:
        for line in sys.stdin:
            cmd = line.rstrip("\n")
            if not cmd:
                continue
            try:
                body = exec_command(sock, cmd, req_id)
            except (OSError, RconError) as e:
                if not as_json:
                    print(f"soulmask_rcon: {e}", file=sys.stderr)
                    break
                print(json.dumps({"ok": False, "error": str(e)}), flush=True)
                try:
                    sock.close()
                except OSError:
                    pass
                try:
                    sock = connect(args.host, args.port, args.password)
                except (OSError, RconError) as e2:
                    print(json.dumps({"ok": False, "error": f"reconnect failed: {e2}"}), flush=True)
                    return 1
                req_id = (req_id % 60000) + 2
                continue
            print(json.dumps({"ok": True, "reply": body}), flush=True) if as_json else print(body)
            req_id = (req_id % 60000) + 2
    finally:
        sock.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--password", required=True)
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--interactive", action="store_true", help="persistent plain-text REPL over stdin/stdout")
    mode.add_argument("--relay", action="store_true", help="persistent JSON-line relay over stdin/stdout")
    ap.add_argument("command", nargs="*", help="RCON command + args (one-shot mode only; omit for a connection test)")
    args = ap.parse_args(argv)
    if (args.interactive or args.relay) and args.command:
        ap.error("--interactive/--relay take no positional command (commands come from stdin)")
    if args.relay:
        return _run_loop(args, as_json=True)
    if args.interactive:
        return _run_loop(args, as_json=False)
    return _run_one_shot(args)


if __name__ == "__main__":
    sys.exit(main())
