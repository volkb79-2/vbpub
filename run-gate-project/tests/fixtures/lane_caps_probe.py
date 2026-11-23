#!/usr/bin/env python3
"""Receive and record Run-Gate's selected lane-cap fact.

This is an acceptance fixture, not a fact builder.  The process under test
must receive the value through its own environment; the fixture compares the
raw bytes with an independently written canonical literal and writes only
that observed value, and only after an explicit PASS from the image observer.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import socket
import stat
import sys


CAP_NAME = "RUN_GATE_LANE_CAPS_V1"
OBSERVER_CONTROL_NAMES = (
    "P7A_TARGET_LANE",
    "P7A_SOCKET_PATH",
    "P7A_EXPECTED_IMAGE_ID",
    "P7A_RECEIPT_PATH",
    "P7A_REAL_DOCKER",
)
ACCEPT_FAILSAFE_SECONDS = 120
READY_PREFIX = "P7A_CAPS_READY "


class ProbeFailure(Exception):
    """A receiver-side refusal; the process exits nonzero and writes nothing."""


def expected_eligible_bytes(lane: str) -> bytes:
    """The fixed acceptance literal, independent of the production builder."""
    escaped_lane = json.dumps(
        lane, ensure_ascii=True, separators=(",", ":"))[1:-1]
    return (
        '{"environment":"tester-unified","lane":"'
        + escaped_lane
        + '","memory_max_bytes":2147483648,"pids_max":8192}'
    ).encode("utf-8")


def validate_environment(mode: str, lane: str) -> bytes:
    """Validate the actual child environment and return the raw observed bytes."""
    if not lane or any(ord(char) < 32 for char in lane):
        raise ProbeFailure("lane name is empty or contains a control character")
    leaked = [name for name in OBSERVER_CONTROL_NAMES if name in os.environ]
    if leaked:
        raise ProbeFailure(
            "observer-control variables reached the target child: "
            + ", ".join(leaked))

    if CAP_NAME not in os.environ:
        raise ProbeFailure(f"{CAP_NAME} is absent from the target child")
    observed = os.environ[CAP_NAME].encode("utf-8")
    if mode == "eligible":
        expected = expected_eligible_bytes(lane)
        if observed != expected:
            raise ProbeFailure(
                f"{CAP_NAME} is not the exact canonical value for lane {lane!r}: "
                f"expected {expected!r}, observed {observed!r}")
    elif mode == "empty":
        if observed != b"":
            raise ProbeFailure(
                f"{CAP_NAME} must be present and exactly empty, observed "
                f"{observed!r}")
    else:
        raise ProbeFailure(f"unknown mode {mode!r}; expected 'eligible' or 'empty'")
    return observed


def _owned_socket_path(sock: socket.socket) -> tuple[int, int] | None:
    try:
        observed = os.lstat(sock.getsockname())
    except OSError:
        return None
    if not stat.S_ISSOCK(observed.st_mode):
        return None
    return observed.st_dev, observed.st_ino


def receive_pass(socket_path: Path) -> None:
    """Bind exclusively, block for one observer decision, and accept only PASS."""
    if os.path.lexists(socket_path):
        raise ProbeFailure(
            f"observer socket path already exists: {socket_path}; "
            "the receiver never removes another owner's path")
    parent = socket_path.parent
    if not parent.is_dir():
        raise ProbeFailure(f"observer socket parent is not a directory: {parent}")

    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    owned_inode: tuple[int, int] | None = None
    connection: socket.socket | None = None
    try:
        try:
            listener.bind(str(socket_path))
        except OSError as exc:
            raise ProbeFailure(
                f"cannot bind observer socket {socket_path}: {exc}") from exc
        owned_inode = _owned_socket_path(listener)
        if owned_inode is None:
            raise ProbeFailure(
                f"bound observer path is not the receiver's socket: {socket_path}")
        listener.listen(1)
        print(f"{READY_PREFIX}{socket_path}", flush=True)
        listener.settimeout(ACCEPT_FAILSAFE_SECONDS)
        try:
            connection, _address = listener.accept()
        except socket.timeout as exc:
            raise ProbeFailure(
                "no observer connected before the acceptance failsafe") from exc
        connection.settimeout(ACCEPT_FAILSAFE_SECONDS)
        try:
            decision = connection.recv(16)
        except socket.timeout as exc:
            raise ProbeFailure(
                "observer connected but sent no explicit decision") from exc
        except OSError as exc:
            raise ProbeFailure(f"observer connection failed: {exc}") from exc
        if decision != b"PASS":
            raise ProbeFailure(
                f"observer did not send explicit PASS (received {decision!r})")
    finally:
        if connection is not None:
            connection.close()
        listener.close()
        if owned_inode is not None:
            try:
                current = os.lstat(socket_path)
            except FileNotFoundError:
                current = None
            if current is not None \
                    and stat.S_ISSOCK(current.st_mode) \
                    and (current.st_dev, current.st_ino) == owned_inode:
                os.unlink(socket_path)


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) not in (3, 4):
        print(
            "usage: lane_caps_probe.py eligible|empty LANE ARTIFACT [SOCKET]",
            file=sys.stderr)
        return 2
    mode, lane, artifact_text = args[:3]
    socket_path = Path(args[3]) if len(args) == 4 else None
    artifact = Path(artifact_text)
    try:
        observed = validate_environment(mode, lane)
        if socket_path is not None:
            receive_pass(socket_path)
            artifact.parent.mkdir(parents=True, exist_ok=True)
            artifact.write_bytes(observed)
        print(f"P7A_CAPS_OK {mode} {lane}")
    except ProbeFailure as exc:
        print(f"run-gate lane-caps receiver refused: {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"run-gate lane-caps receiver failed: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
