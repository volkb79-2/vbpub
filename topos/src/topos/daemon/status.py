"""Client-side socket and protocol status checks for ``topos daemon status``."""

from __future__ import annotations

import grp
import os
import stat
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from topos.daemon.client import (
    DaemonClient,
    DaemonClientError,
    DaemonConnectError,
    DaemonProtocolError,
    DaemonResponseError,
)
from topos.service.config import DEFAULTS


DEFAULT_DAEMON_SOCKET = DEFAULTS.sockets.observe.path
DEFAULT_DAEMON_GROUP = DEFAULTS.sockets.observe.group
DEFAULT_SOCKET_MODE = DEFAULTS.sockets.observe.mode


@dataclass(frozen=True)
class SocketNodeStatus:
    """Whether the mounted observe socket has the metadata doctor expects."""

    ok: bool
    message: str

    def to_jsonable(self) -> dict[str, object]:
        return {"ok": self.ok, "message": self.message}


@dataclass(frozen=True)
class ProtocolStatus:
    """Result of the observe hello and current-frame protocol checks."""

    ok: bool
    message: str
    schema_version: int | None = None
    frame_ts: float | None = None
    entity_count: int | None = None

    def to_jsonable(self) -> dict[str, object]:
        result: dict[str, object] = {"ok": self.ok, "message": self.message}
        if self.schema_version is not None:
            result["schema_version"] = self.schema_version
        if self.frame_ts is not None:
            result["frame_ts"] = self.frame_ts
        if self.entity_count is not None:
            result["entity_count"] = self.entity_count
        return result


@dataclass(frozen=True)
class DaemonStatusReport:
    """Combined socket-node and protocol status for a client account."""

    socket_path: Path
    group_name: str
    socket_node: SocketNodeStatus
    protocol: ProtocolStatus

    @property
    def ok(self) -> bool:
        return self.socket_node.ok and self.protocol.ok

    def to_jsonable(self) -> dict[str, object]:
        return {
            "ok": self.ok,
            "socket": str(self.socket_path),
            "group": self.group_name,
            "socket_node": self.socket_node.to_jsonable(),
            "protocol": self.protocol.to_jsonable(),
        }

    def to_text(self) -> str:
        lines = [
            f"topos daemon status: {self.socket_path}",
            f"Group: {self.group_name}",
            "",
            "--- Socket node ---",
            f"  {'OK' if self.socket_node.ok else 'FAIL'}: {self.socket_node.message}",
            "",
            "--- Protocol ---",
            f"  {'OK' if self.protocol.ok else 'FAIL'}: {self.protocol.message}",
        ]
        if self.protocol.schema_version is not None:
            lines.append(f"  schema version: {self.protocol.schema_version}")
        if self.protocol.frame_ts is not None:
            lines.append(f"  frame timestamp: {self.protocol.frame_ts}")
        if self.protocol.entity_count is not None:
            lines.append(f"  entities: {self.protocol.entity_count}")
        lines.extend(("", f"Overall: {'OK' if self.ok else 'DEGRADED'}"))
        return "\n".join(lines)


STALE_FRAME_INTERVALS = 3


def _check_socket_node(socket_path: Path, group_name: str) -> SocketNodeStatus:
    try:
        info = socket_path.lstat()
    except OSError as exc:
        return SocketNodeStatus(False, f"{socket_path}: {exc.strerror or type(exc).__name__}")
    if not stat.S_ISSOCK(info.st_mode):
        return SocketNodeStatus(False, f"{socket_path}: not a Unix socket")
    try:
        expected_gid = grp.getgrnam(group_name).gr_gid
    except KeyError:
        return SocketNodeStatus(False, f"configured group {group_name!r} does not exist")
    mode = stat.S_IMODE(info.st_mode)
    expected_mode = int(DEFAULT_SOCKET_MODE, 8)
    if info.st_uid != 0 or info.st_gid != expected_gid or mode != expected_mode:
        return SocketNodeStatus(
            False,
            f"{socket_path}: {info.st_uid}:{info.st_gid} {mode:04o}, expected "
            f"root:{group_name} {expected_mode:04o}",
        )
    return SocketNodeStatus(True, f"{socket_path} is a socket owned by root:{group_name} mode {mode:04o}")


def _check_protocol(socket_path: Path, *, now: Callable[[], float] = time.time) -> ProtocolStatus:
    """Run the same hello access probe as doctor, then fetch a current frame."""
    try:
        client = DaemonClient(socket_path)
        hello = client.request_hello()
        frame = client.current_frame()
    except DaemonConnectError as exc:
        guidance = " Try: toposd doctor --check connect."
        if socket_path != DEFAULT_DAEMON_SOCKET:
            guidance = f" Check the mounted socket path {socket_path} and its observe-group access."
        return ProtocolStatus(False, f"Cannot connect: {exc}{guidance}")
    except (DaemonProtocolError, DaemonResponseError) as exc:
        return ProtocolStatus(
            False,
            f"Protocol error: {exc}. Check that the process at the socket is a compatible topos daemon and review daemon logs.",
        )
    except DaemonClientError as exc:
        return ProtocolStatus(False, f"Daemon error: {exc}")

    version = hello.identity.get("version", "unknown")
    age_s = now() - frame.ts
    if frame.interval_s > 0 and age_s > STALE_FRAME_INTERVALS * frame.interval_s:
        return ProtocolStatus(
            False,
            f"Stale frame: newest frame is {age_s:.0f}s old "
            f"(more than {STALE_FRAME_INTERVALS} sampling intervals of {frame.interval_s:g}s); "
            "the collector may have stopped. Check the daemon logs.",
            schema_version=frame.schema_version,
            frame_ts=frame.ts,
            entity_count=len(frame.entities),
        )
    return ProtocolStatus(
        True,
        f"Hello from topos {version}; current frame retrieved successfully.",
        schema_version=frame.schema_version,
        frame_ts=frame.ts,
        entity_count=len(frame.entities),
    )


def build_daemon_status(
    socket_path: Path = DEFAULT_DAEMON_SOCKET,
    *,
    group_name: str | None = None,
) -> DaemonStatusReport:
    """Read socket metadata and exercise the same hello used by service doctor."""
    path = Path(socket_path)
    group = group_name if group_name is not None else DEFAULT_DAEMON_GROUP
    return DaemonStatusReport(
        socket_path=path,
        group_name=group,
        socket_node=_check_socket_node(path, group),
        protocol=_check_protocol(path),
    )


__all__ = [
    "DEFAULT_DAEMON_GROUP", "DEFAULT_DAEMON_SOCKET", "DaemonStatusReport", "ProtocolStatus",
    "SocketNodeStatus", "build_daemon_status",
]
