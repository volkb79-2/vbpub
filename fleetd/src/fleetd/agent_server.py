"""Unix-socket and forced-command transports for the stdlib host agent."""

from __future__ import annotations

import errno
import os
import select
import socket
import stat
import threading
import time
from pathlib import Path
from typing import Callable

from .admission import AdmissionError, ExitCode
from .agent_policy import AgentIdentity, AgentPolicy
from .agent_protocol import FrameError, read_frame, write_frame
from .agent_verbs import AGENT_SCHEMA, AgentVerbService, write_agent_reply


class AgentServer:
    """Accept authenticated local peers on a path or inherited listener."""

    def __init__(
        self,
        listener: socket.socket,
        service: AgentVerbService,
        policy: AgentPolicy,
        *,
        monotonic: Callable[[], float] = time.monotonic,
        select_waiter: Callable[..., tuple[list[object], list[object], list[object]]] = select.select,
    ) -> None:
        self.listener = listener
        self.service = service
        self.policy = policy
        self._monotonic = monotonic
        self._select_waiter = select_waiter
        self._wake_read, self._wake_write = socket.socketpair()
        self._state_lock = threading.Lock()
        self._closed = False
        self._serving = False

    @classmethod
    def on_path(cls, path: Path, service: AgentVerbService, policy: AgentPolicy) -> AgentServer:
        return cls(open_agent_socket(path), service, policy)

    @classmethod
    def from_fd(cls, fd: int, service: AgentVerbService, policy: AgentPolicy) -> AgentServer:
        return cls(inherited_listener(fd), service, policy)

    def serve_once(self) -> None:
        """Accept and handle one client synchronously; useful for lifecycle probes."""
        readable, _, _ = self._select_waiter([self.listener, self._wake_read], [], [], None)
        if self._wake_read in readable:
            return
        connection, _address = self.listener.accept()
        with connection:
            try:
                identity = self.policy.resolve_socket_peer(connection)
            except AdmissionError as exc:
                _write_error(connection, self.service, exc.exit_code, exc.reason)
                return
            serve_connection(connection, connection, self.service, self.policy, identity)

    def serve_forever(self) -> None:
        """Serve each connection independently so a held lease does not block peers."""
        with self._state_lock:
            if self._closed:
                return
            if self._serving:
                raise AdmissionError(ExitCode.RUNNER_FAILURE, "fleetd agent listener is already serving")
            self._serving = True
        next_reconcile = self._monotonic() + self.policy.lease_reconcile_interval_s
        try:
            while True:
                timeout = max(0.0, next_reconcile - self._monotonic())
                readable, _, _ = self._select_waiter(
                    [self.listener, self._wake_read], [], [], timeout,
                )
                if self._wake_read in readable:
                    return
                if not readable:
                    now = self._monotonic()
                    if now >= next_reconcile:
                        self.service.reconcile_leases()
                        next_reconcile = now + self.policy.lease_reconcile_interval_s
                    continue
                connection, _address = self.listener.accept()
                threading.Thread(
                    target=self._serve_connection,
                    args=(connection,),
                    name="fleetd-agent-client",
                    daemon=True,
                ).start()
        finally:
            with self._state_lock:
                self._serving = False
                self.listener.close()
                self._wake_read.close()
                self._wake_write.close()

    def _serve_connection(self, connection: socket.socket) -> None:
        with connection:
            try:
                identity = self.policy.resolve_socket_peer(connection)
            except AdmissionError as exc:
                _write_error(connection, self.service, exc.exit_code, exc.reason)
                return
            try:
                serve_connection(connection, connection, self.service, self.policy, identity)
            except OSError:
                return

    def close(self) -> None:
        """Close only the listener; the containing runtime directory is host-owned."""
        with self._state_lock:
            if self._closed:
                return
            self._closed = True
            serving = self._serving
            if serving:
                try:
                    self._wake_write.send(b"x")
                except OSError:
                    pass
            else:
                self._wake_read.close()
                self._wake_write.close()
                self.listener.close()


def _check_socket_parent(path: Path) -> None:
    parent = path.parent
    if not parent.exists() or parent.is_symlink() or not parent.is_dir():
        raise AdmissionError(ExitCode.RUNNER_FAILURE, f"agent socket parent {parent} must be an existing real directory")
    for ancestor in parent.parents:
        if ancestor.is_symlink():
            raise AdmissionError(ExitCode.RUNNER_FAILURE, f"agent socket parent {ancestor} must not be a symlink")


def _remove_stale_socket(path: Path) -> None:
    try:
        before = path.lstat()
    except FileNotFoundError:
        return
    if not stat.S_ISSOCK(before.st_mode):
        raise AdmissionError(ExitCode.RUNNER_FAILURE, f"agent socket path {path} exists and is not a socket")

    probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        try:
            probe.connect(str(path))
        except OSError as exc:
            if exc.errno not in {errno.ECONNREFUSED, errno.ENOENT}:
                raise AdmissionError(ExitCode.RUNNER_FAILURE, f"cannot inspect existing agent socket {path}: {exc}") from exc
        else:
            raise AdmissionError(ExitCode.RUNNER_FAILURE, f"agent socket {path} already has a listener")
    finally:
        probe.close()

    try:
        after = path.lstat()
        if not stat.S_ISSOCK(after.st_mode) or (after.st_dev, after.st_ino) != (before.st_dev, before.st_ino):
            raise AdmissionError(ExitCode.RUNNER_FAILURE, f"agent socket path {path} changed during restart")
        path.unlink()
    except FileNotFoundError:
        return
    except OSError as exc:
        raise AdmissionError(ExitCode.RUNNER_FAILURE, f"cannot remove stale agent socket {path}: {exc}") from exc


def open_agent_socket(path: Path) -> socket.socket:
    """Bind a Unix listener without creating or removing its parent directory."""
    socket_path = Path(path)
    if not socket_path.is_absolute() or socket_path.name in {"", ".", ".."}:
        raise AdmissionError(ExitCode.RUNNER_FAILURE, "agent socket path must be an absolute file path")
    _check_socket_parent(socket_path)
    _remove_stale_socket(socket_path)
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        listener.bind(str(socket_path))
        os.chmod(socket_path, 0o660, follow_symlinks=False)
        listener.listen(socket.SOMAXCONN)
        return listener
    except OSError as exc:
        listener.close()
        raise AdmissionError(ExitCode.RUNNER_FAILURE, f"cannot bind fleetd agent socket {socket_path}: {exc}") from exc


def inherited_listener(fd: int) -> socket.socket:
    """Duplicate and validate an inherited AF_UNIX stream listening descriptor."""
    if type(fd) is not int or fd < 0:
        raise AdmissionError(ExitCode.RUNNER_FAILURE, "inherited agent listener fd must be a non-negative integer")
    try:
        listener = socket.fromfd(fd, socket.AF_UNIX, socket.SOCK_STREAM)
        if listener.family != socket.AF_UNIX or listener.getsockopt(socket.SOL_SOCKET, socket.SO_TYPE) != socket.SOCK_STREAM:
            raise OSError("descriptor is not an AF_UNIX stream socket")
        if listener.getsockopt(socket.SOL_SOCKET, socket.SO_ACCEPTCONN) != 1:
            raise OSError("descriptor is not listening")
        return listener
    except (OSError, ValueError) as exc:
        if "listener" in locals():
            listener.close()
        raise AdmissionError(ExitCode.RUNNER_FAILURE, f"invalid inherited agent listener fd {fd}: {exc}") from exc


def _write_error(stream: object, service: AgentVerbService, exit_code: int, reason: str) -> None:
    try:
        write_frame(stream, {
            "schema": AGENT_SCHEMA,
            "agent_version": service.agent_version,
            "exit_code": int(exit_code),
            "reason": reason,
        })
    except FrameError:
        return


def serve_connection(
    input_stream: object,
    output_stream: object,
    service: AgentVerbService,
    policy: AgentPolicy,
    identity: AgentIdentity,
) -> int:
    """Read one full request before dispatch and return its bounded frame/body."""
    try:
        request = read_frame(input_stream, policy.idle_connection_s)
        if request.get("verb") == "lease":
            service.serve_lease(request, identity, input_stream)
            return ExitCode.PASS
        reply = service.handle(request, identity, body_stream=input_stream)
        write_agent_reply(output_stream, reply)
        return ExitCode.PASS
    except AdmissionError as exc:
        _write_error(output_stream, service, exc.exit_code, exc.reason)
        return exc.exit_code
    except FrameError as exc:
        _write_error(output_stream, service, ExitCode.IDENTITY_OR_TRANSPORT, str(exc))
        return ExitCode.IDENTITY_OR_TRANSPORT
    except Exception as exc:
        _write_error(output_stream, service, ExitCode.RUNNER_FAILURE, f"unexpected agent failure: {exc}")
        return ExitCode.RUNNER_FAILURE


def serve_forced_command(
    input_stream: object,
    output_stream: object,
    service: AgentVerbService,
    policy: AgentPolicy,
    group: str,
) -> int:
    """Serve the same framed protocol on ssh forced-command stdin/stdout."""
    try:
        identity = policy.resolve_forced_group(group)
    except AdmissionError as exc:
        _write_error(output_stream, service, exc.exit_code, exc.reason)
        return exc.exit_code
    return serve_connection(input_stream, output_stream, service, policy, identity)
