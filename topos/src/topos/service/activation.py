"""Adopt systemd-passed listening sockets without changing their nodes."""

from __future__ import annotations

import os
import socket
from collections.abc import MutableMapping
from typing import Callable


class ActivationError(ValueError):
    """Invalid systemd socket-activation environment or descriptor."""


def adopt_listeners(
    expected_names: set[str],
    environ: MutableMapping[str, str] = os.environ,
    pid: Callable[[], int] = os.getpid,
) -> dict[str, list[socket.socket]] | None:
    """Adopt named, listening AF_UNIX/SOCK_STREAM descriptors from systemd.

    A different or absent LISTEN_PID means the variables belong to another
    process, so that case returns without reading or changing the environment.
    """
    raw_pid = environ.get("LISTEN_PID")
    if raw_pid is None:
        return None
    try:
        listen_pid = int(raw_pid)
    except ValueError as exc:
        raise ActivationError("LISTEN_PID must be an integer") from exc
    if listen_pid != pid():
        return None

    raw_count = environ.get("LISTEN_FDS")
    try:
        count = int(raw_count) if raw_count is not None else 0
    except ValueError as exc:
        raise ActivationError("LISTEN_FDS must be an integer greater than or equal to 1") from exc
    if count < 1:
        raise ActivationError("LISTEN_FDS must be an integer greater than or equal to 1")

    raw_names = environ.get("LISTEN_FDNAMES")
    names = raw_names.split(":") if raw_names is not None else []
    if len(names) != count or any(not name for name in names):
        raise ActivationError("LISTEN_FDNAMES must contain one non-empty name per LISTEN_FDS descriptor")
    unknown = next((name for name in names if name not in expected_names), None)
    if unknown is not None:
        raise ActivationError(f"LISTEN_FDNAMES contains unknown socket name {unknown!r}")

    adopted: dict[str, list[socket.socket]] = {}
    wrapped: list[socket.socket] = []
    try:
        for index, name in enumerate(names):
            fd = 3 + index
            try:
                listener = socket.socket(fileno=fd)
            except OSError as exc:
                raise ActivationError(f"fd {fd} from LISTEN_FDS is not a socket: {exc}") from exc
            wrapped.append(listener)
            try:
                listener.set_inheritable(False)
            except OSError as exc:
                raise ActivationError(f"fd {fd} could not be marked close-on-exec: {exc}") from exc
            if listener.family != socket.AF_UNIX:
                raise ActivationError(f"fd {fd} is not AF_UNIX")
            try:
                socket_type = listener.getsockopt(socket.SOL_SOCKET, socket.SO_TYPE)
            except OSError as exc:
                raise ActivationError(f"fd {fd} could not be checked for SOCK_STREAM: {exc}") from exc
            if socket_type != socket.SOCK_STREAM:
                raise ActivationError(f"fd {fd} is not SOCK_STREAM")
            try:
                accepting = listener.getsockopt(socket.SOL_SOCKET, socket.SO_ACCEPTCONN)
            except OSError as exc:
                raise ActivationError(f"fd {fd} could not be checked for listening state: {exc}") from exc
            if accepting != 1:
                raise ActivationError(f"fd {fd} is not listening (SO_ACCEPTCONN={accepting})")
            adopted.setdefault(name, []).append(listener)
    except BaseException:
        for listener in wrapped:
            listener.close()
        raise

    for key in ("LISTEN_PID", "LISTEN_FDS", "LISTEN_FDNAMES"):
        environ.pop(key, None)
    return adopted


__all__ = ["ActivationError", "adopt_listeners"]
