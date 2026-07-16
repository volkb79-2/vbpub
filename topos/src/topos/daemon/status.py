"""Daemon status report combining deployment preflight with protocol checks.

This module provides a read-only DaemonStatusReport that operators can use
to answer "is the daemon deployment usable from this account, and is it
speaking the expected topos frame protocol?"
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from topos.daemon.client import (
    DaemonClient,
    DaemonClientError,
    DaemonConnectError,
    DaemonProtocolError,
    DaemonResponseError,
)
from topos.daemon.deploy import (
    DEFAULT_DAEMON_GROUP,
    DEFAULT_DAEMON_SOCKET,
    DaemonPreflightReport,
    preflight_daemon_deployment,
    preflight_report_to_jsonable,
)


@dataclass(frozen=True)
class ProtocolStatus:
    """Result of the protocol current-frame check."""

    ok: bool
    message: str
    schema_version: int | None = None
    frame_ts: float | None = None
    entity_count: int | None = None

    def to_jsonable(self) -> dict:
        d: dict = {
            "ok": self.ok,
            "message": self.message,
        }
        if self.schema_version is not None:
            d["schema_version"] = self.schema_version
        if self.frame_ts is not None:
            d["frame_ts"] = self.frame_ts
        if self.entity_count is not None:
            d["entity_count"] = self.entity_count
        return d


@dataclass(frozen=True)
class DaemonStatusReport:
    """Combined daemon deployment and protocol status."""

    socket_path: Path
    group_name: str
    preflight: DaemonPreflightReport | None
    protocol: ProtocolStatus

    @property
    def ok(self) -> bool:
        preflight_ok = self.preflight is not None and self.preflight.usable
        return preflight_ok and self.protocol.ok

    def to_jsonable(self) -> dict:
        d: dict = {
            "ok": self.ok,
            "socket": str(self.socket_path),
            "group": self.group_name,
            "protocol": self.protocol.to_jsonable(),
        }
        if self.preflight is not None:
            d["preflight"] = preflight_report_to_jsonable(self.preflight)
        return d

    def to_text(self) -> str:
        lines = [
            f"topos daemon status: {self.socket_path}",
            f"Group: {self.group_name}",
            "",
            "--- Preflight ---",
        ]
        if self.preflight is not None:
            from topos.daemon.deploy import render_preflight_text

            lines.append(render_preflight_text(self.preflight))
        else:
            lines.append("(preflight not run)")
            lines.append("")

        lines.append("")
        lines.append("--- Protocol ---")
        lines.append(f"  {self.protocol.message}")
        if self.protocol.schema_version is not None:
            lines.append(f"  schema version: {self.protocol.schema_version}")
        if self.protocol.frame_ts is not None:
            lines.append(f"  frame timestamp: {self.protocol.frame_ts}")
        if self.protocol.entity_count is not None:
            lines.append(f"  entities: {self.protocol.entity_count}")
        lines.append("")
        lines.append(f"Overall: {'OK' if self.ok else 'DEGRADED'}")
        return "\n".join(lines)


def _check_protocol(socket_path: Path) -> ProtocolStatus:
    """Run a read-only current-frame protocol check against the daemon socket.

    Returns a ProtocolStatus — never raises. On failure the message includes
    P31-style actionable guidance.
    """
    try:
        client = DaemonClient(socket_path)
        frame = client.current_frame()
    except DaemonConnectError as exc:
        guidance = ""
        if socket_path == DEFAULT_DAEMON_SOCKET:
            guidance = " Try: topos daemon preflight. If not installed: topos daemon install-plan."
        else:
            guidance = f" Try: topos daemon preflight --socket {socket_path}."
        return ProtocolStatus(
            ok=False,
            message=f"Cannot connect: {exc}{guidance}",
        )
    except (DaemonProtocolError, DaemonResponseError) as exc:
        return ProtocolStatus(
            ok=False,
            message=f"Protocol error: {exc}. Check that the process at the socket is a compatible topos daemon and review daemon logs.",
        )
    except DaemonClientError as exc:
        return ProtocolStatus(
            ok=False,
            message=f"Daemon error: {exc}",
        )

    return ProtocolStatus(
        ok=True,
        message="Current frame retrieved successfully.",
        schema_version=frame.schema_version,
        frame_ts=frame.ts,
        entity_count=len(frame.entities),
    )


def build_daemon_status(
    socket_path: Path = DEFAULT_DAEMON_SOCKET,
    *,
    group_name: str = DEFAULT_DAEMON_GROUP,
) -> DaemonStatusReport:
    """Build a combined daemon deployment and protocol status report.

    The preflight check inspects filesystem metadata and group membership.
    The protocol check attempts one current-frame request through the
    existing P16 client protocol.

    Both checks are read-only. No host state is modified.
    """
    preflight: DaemonPreflightReport | None = None
    try:
        preflight = preflight_daemon_deployment(
            socket_path,
            group_name=group_name,
            connect_timeout_s=1.0,
        )
    except (OSError, RuntimeError, ValueError):
        # Preflight may fail on stat or group lookups; report as None
        pass

    protocol = _check_protocol(socket_path)

    return DaemonStatusReport(
        socket_path=Path(socket_path),
        group_name=group_name,
        preflight=preflight,
        protocol=protocol,
    )
