#!/usr/bin/env python3
"""Receive and record Run-Gate's selected lane-cap fact.

This is an acceptance fixture, not a fact builder. It validates the actual
child environment against independent canonical bytes and records them only
after the proof mode selected on argv has completed.
"""

from __future__ import annotations

import os
from pathlib import Path
import secrets
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
    "P7A_EXPECTED_EXEC_CONTAINER_ID",
    "P7A_EXPECTED_EXEC_CONTAINER_NAME",
    "P7A_EXPECTED_EXEC_CIU_INSTANCE",
    "P7A_EXPECTED_EXEC_CIU_REPO_ROOT",
    "P7A_EXPECTED_EXEC_COMPOSE_SERVICE",
)
ACCEPT_FAILSAFE_SECONDS = 120
READY_PREFIX = "P7A_CAPS_READY "

_ELIGIBLE_VALUES = {
    "p7a-lane-caps-smoke": (
        b'{"environment":"tester-unified","lane":"p7a-lane-caps-smoke",'
        b'"memory_max_bytes":2147483648,"pids_max":8192}'
    ),
    "p7a-lane-caps-outer-smoke": (
        b'{"environment":"tester-unified","lane":"p7a-lane-caps-outer-smoke",'
        b'"memory_max_bytes":2147483648,"pids_max":8192}'
    ),
}


class ProbeFailure(Exception):
    """A receiver-side refusal; the process exits nonzero and writes nothing."""


def expected_eligible_bytes(lane: str) -> bytes:
    """Return a fixed literal for one declared eligible lane."""
    try:
        return _ELIGIBLE_VALUES[lane]
    except KeyError as exc:
        raise ProbeFailure(f"unknown eligible lane {lane!r}") from exc


def validate_environment(mode: str, lane: str) -> bytes:
    """Validate the actual child environment and return its observed bytes."""
    if not lane or any(ord(char) < 32 for char in lane):
        raise ProbeFailure("lane name is empty or contains a control character")
    leaked = [name for name in OBSERVER_CONTROL_NAMES if name in os.environ]
    if leaked:
        raise ProbeFailure(
            "observer-control variables reached the target child: "
            + ", ".join(leaked))

    if CAP_NAME not in os.environ:
        raise ProbeFailure(f"{CAP_NAME} is absent from the target child")
    if hasattr(os, "environb"):
        observed = os.environb[CAP_NAME.encode("ascii")]
    else:
        # Windows has no environb; retain its native filesystem encoding.
        observed = os.fsencode(os.environ[CAP_NAME])
    if mode == "eligible":
        expected = expected_eligible_bytes(lane)
        if observed != expected:
            raise ProbeFailure(
                f"{CAP_NAME} is not the exact canonical value for lane {lane!r}")
    elif mode == "empty":
        if lane != "p7a-lane-caps-ineligible-smoke":
            raise ProbeFailure(f"unknown empty-mode lane {lane!r}")
        if observed != b"":
            raise ProbeFailure(f"{CAP_NAME} must be present and exactly empty")
    else:
        raise ProbeFailure(f"unknown mode {mode!r}; expected 'eligible' or 'empty'")
    return observed


def _socket_identity(path: Path) -> tuple[int, int] | None:
    try:
        observed = os.lstat(path)
    except OSError:
        return None
    if not stat.S_ISSOCK(observed.st_mode):
        return None
    return observed.st_dev, observed.st_ino


def _read_to_eof(connection: socket.socket) -> bytes:
    """Read the entire decision, rejecting a stream larger than 64 bytes."""
    chunks: list[bytes] = []
    total = 0
    while True:
        try:
            chunk = connection.recv(min(65 - total, 16))
        except socket.timeout as exc:
            raise ProbeFailure(
                "observer connection did not reach EOF before the hang failsafe") from exc
        except OSError as exc:
            raise ProbeFailure(f"observer connection failed: {exc}") from exc
        if not chunk:
            return b"".join(chunks)
        chunks.append(chunk)
        total += len(chunk)
        if total > 64:
            raise ProbeFailure("observer decision exceeds the 64-byte limit")


def receive_pass(socket_path: Path) -> str:
    """Bind, announce a fresh nonce, and accept exactly one PASS line through EOF."""
    if os.path.lexists(socket_path):
        raise ProbeFailure(
            f"observer socket path already exists: {socket_path}; "
            "the receiver never removes another owner's path")
    if "\n" in str(socket_path) or "\r" in str(socket_path):
        raise ProbeFailure("observer socket path contains a line break")
    parent = socket_path.parent
    if not parent.is_dir():
        raise ProbeFailure(f"observer socket parent is not a directory: {parent}")

    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    owned_inode: tuple[int, int] | None = None
    connection: socket.socket | None = None
    nonce = secrets.token_hex(16)
    try:
        try:
            listener.bind(str(socket_path))
        except OSError as exc:
            raise ProbeFailure(
                f"cannot bind observer socket {socket_path}: {exc}") from exc
        owned_inode = _socket_identity(socket_path)
        if owned_inode is None:
            raise ProbeFailure(
                f"bound observer path is not a socket: {socket_path}")
        try:
            listener.listen(1)
        except OSError as exc:
            raise ProbeFailure(f"cannot listen on observer socket: {exc}") from exc

        print(f"{READY_PREFIX}{nonce} {socket_path}", flush=True)
        listener.settimeout(ACCEPT_FAILSAFE_SECONDS)
        try:
            connection, _address = listener.accept()
        except socket.timeout as exc:
            raise ProbeFailure(
                "no observer connected before the acceptance hang failsafe") from exc
        except OSError as exc:
            raise ProbeFailure(f"cannot accept observer connection: {exc}") from exc
        connection.settimeout(ACCEPT_FAILSAFE_SECONDS)
        decision = _read_to_eof(connection)
        expected = f"PASS {nonce}\n".encode("ascii")
        if decision != expected:
            raise ProbeFailure("observer did not send the exact nonce-bound PASS line")
        return nonce
    finally:
        if connection is not None:
            connection.close()
        listener.close()
        if owned_inode is not None:
            if _socket_identity(socket_path) == owned_inode:
                try:
                    os.unlink(socket_path)
                except FileNotFoundError:
                    pass


def write_artifact_exclusive(path: Path, data: bytes) -> None:
    """Create the artifact without replacing anything and remove a partial write."""
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    descriptor = os.open(path, flags, 0o666)
    owned = os.fstat(descriptor)
    try:
        with os.fdopen(descriptor, "wb", closefd=True) as stream:
            view = memoryview(data)
            while view:
                written = stream.write(view)
                if written is None or written <= 0:
                    raise OSError("short write while creating observation artifact")
                view = view[written:]
            stream.flush()
        current = os.lstat(path)
        if (current.st_dev, current.st_ino) != (owned.st_dev, owned.st_ino):
            raise ProbeFailure("artifact path changed while the observation was written")
    except BaseException:
        try:
            current = os.lstat(path)
        except OSError:
            current = None
        if current is not None \
                and (current.st_dev, current.st_ino) == (owned.st_dev, owned.st_ino):
            try:
                os.unlink(path)
            except OSError:
                pass
        raise


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) not in (3, 4):
        print(
            "usage: lane_caps_probe.py eligible|empty LANE ARTIFACT "
            "[SOCKET|--posthoc]",
            file=sys.stderr)
        return 2
    mode, lane, artifact_text = args[:3]
    artifact = Path(artifact_text)
    proof = args[3] if len(args) == 4 else None
    if proof is not None and proof != "--posthoc":
        socket_path = Path(proof)
        if proof.startswith("--") or not socket_path.is_absolute():
            print(
                "run-gate lane-caps receiver refused: fourth argument must be "
                "--posthoc or an absolute socket path",
                file=sys.stderr)
            return 2
    try:
        observed = validate_environment(mode, lane)
        if proof == "--posthoc":
            write_artifact_exclusive(artifact, observed)
        elif proof is not None:
            receive_pass(socket_path)
            write_artifact_exclusive(artifact, observed)
        # The legacy three-argument form remains validation-only. It is not
        # posthoc proof and therefore never creates the artifact.
        if proof is None:
            print(f"P7A_CAPS_VALIDATION_ONLY {mode} {lane}")
        else:
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
