"""Host/session ownership for the TUI.

The controller is the authority for the active host and session generation.
Views may keep a session reference only for one generation. Switching first
invalidates that generation, asks its workers to stop, waits for them to drain,
and only then closes the old client and opens the replacement.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Callable, Mapping

from .errors import ProfileError, TransportError
from .profile import Profile, SecretEntry, read_credential
from .session import Session, SessionOpener, open_session


@dataclass
class WorkerLease:
    generation: int
    session: Session | None
    cancelled: threading.Event = field(default_factory=threading.Event)
    finished: threading.Event = field(default_factory=threading.Event)


@dataclass(frozen=True)
class SessionTransition:
    generation: int
    previous_generation: int
    target_host: str | None
    previous_session: Session | None
    workers: tuple[WorkerLease, ...]
    allow_write: bool


class SessionController:
    """Own one selected host session and reject work whose captured generation is stale."""

    def __init__(self, hosts: Mapping[str, Profile], default_host: str | None,
                 secret_entries: Mapping[str, SecretEntry], session_opener: SessionOpener = open_session, *,
                 insecure_ack: bool = False, initial_session: Session | None = None,
                 initial_write: bool = False):
        self.hosts = dict(hosts)
        if initial_session is not None:
            self.hosts.setdefault(initial_session.profile.name, initial_session.profile)
        if default_host is not None and default_host not in self.hosts:
            raise ProfileError(f"default host {default_host!r} is not configured")
        self.default_host = default_host
        self.secret_entries = dict(secret_entries)
        if (initial_session is not None and initial_session.credential is not None
                and initial_session.profile.name not in self.secret_entries):
            credential = initial_session.credential
            if initial_session.profile.auth == "api_key":
                self.secret_entries[initial_session.profile.name] = SecretEntry(api_key=credential)
            else:
                self.secret_entries[initial_session.profile.name] = SecretEntry(password=credential)
        self.session_opener = session_opener
        self.insecure_ack = insecure_ack
        self.generation = 0
        self.session = initial_session
        self.active_host = initial_session.profile.name if initial_session else None
        self.write_modes: dict[str, bool] = {}
        if initial_session is not None:
            self.write_modes[self.active_host] = not initial_session.rpc.read_only
        self._initial_write = bool(initial_write)
        self._first_selection = initial_session is None
        self._lock = threading.RLock()
        self._workers: dict[int, WorkerLease] = {}
        self._next_worker = 0
        self._switching = False
        self.last_status: dict[str, str] = {name: "not tested" for name in self.hosts}
        if initial_session is not None:
            self.last_status[initial_session.profile.name] = "connected"

    def capture(self) -> tuple[int, Session | None]:
        with self._lock:
            return self.generation, self.session

    def is_current(self, generation: int, session: Session | None) -> bool:
        with self._lock:
            return (not self._switching and generation == self.generation and session is not None
                    and session is self.session)

    def accepts_generation(self, generation: int) -> bool:
        with self._lock:
            return not self._switching and generation == self.generation

    def register_worker(self, generation: int, session: Session | None) -> WorkerLease | None:
        """Register a host-bound read/apply or connection test for switch draining."""
        with self._lock:
            if generation != self.generation or self._switching:
                return None
            if session is not None and session is not self.session:
                return None
            self._next_worker += 1
            lease = WorkerLease(generation, session)
            self._workers[self._next_worker] = lease
            return lease

    def finish_worker(self, lease: WorkerLease) -> None:
        with self._lock:
            for ident, current in tuple(self._workers.items()):
                if current is lease:
                    del self._workers[ident]
                    break
            lease.finished.set()

    def begin_switch(self, host_name: str, *, write_mode: bool | None = None) -> SessionTransition:
        with self._lock:
            if host_name not in self.hosts:
                raise ProfileError(f"unknown host {host_name!r} (have: {', '.join(sorted(self.hosts))})")
            if self._switching:
                raise ProfileError("a host transition is already in progress")
            previous_generation = self.generation
            old_session = self.session
            workers = tuple(lease for lease in self._workers.values() if lease.generation == previous_generation)
            for lease in workers:
                lease.cancelled.set()
            self.generation += 1
            self.session = None
            self.active_host = host_name
            self._switching = True
            if write_mode is not None:
                self.write_modes[host_name] = bool(write_mode)
            elif host_name not in self.write_modes:
                self.write_modes[host_name] = self._initial_write if self._first_selection else False
            self._first_selection = False
            return SessionTransition(self.generation, previous_generation, host_name, old_session, workers,
                                     self.write_modes[host_name])

    def finish_switch(self, transition: SessionTransition) -> Session | None:
        """Drain old-generation workers, close the old client, then connect to the selected host."""
        for lease in transition.workers:
            lease.finished.wait()
        if transition.previous_session is not None:
            transition.previous_session.close()
        if transition.target_host is None:
            with self._lock:
                if transition.generation == self.generation:
                    self.session = None
                    self.active_host = None
                    self._switching = False
            return None
        profile = self.hosts[transition.target_host]
        credential = None
        try:
            credential = read_credential(profile, self.secret_entries)
            session = self.session_opener(profile, secret=credential, insecure_ack=self.insecure_ack,
                                          allow_write=transition.allow_write)
        except Exception as exc:
            safe_error = credential.scrub(str(exc)) if credential is not None else str(exc)
            with self._lock:
                if transition.generation == self.generation:
                    self._switching = False
                    self.session = None
                    self.last_status[transition.target_host] = f"connection failed: {safe_error}"
            raise ProfileError(f"cannot connect to host {transition.target_host!r}: {safe_error}") from None
        with self._lock:
            if transition.generation != self.generation:
                session.close()
                return None
            self.session = session
            self.active_host = transition.target_host
            self._switching = False
            self.last_status[transition.target_host] = "connected"
            return session

    def switch(self, host_name: str, *, write_mode: bool | None = None) -> Session | None:
        return self.finish_switch(self.begin_switch(host_name, write_mode=write_mode))

    def begin_disconnect(self) -> SessionTransition:
        with self._lock:
            if self._switching:
                raise ProfileError("a host transition is already in progress")
            previous_generation = self.generation
            workers = tuple(lease for lease in self._workers.values() if lease.generation == previous_generation)
            for lease in workers:
                lease.cancelled.set()
            old_session = self.session
            self.generation += 1
            self.session = None
            self.active_host = None
            self._switching = True
            return SessionTransition(self.generation, previous_generation, None, old_session, workers, False)

    def set_write_mode(self, enabled: bool) -> Session | None:
        with self._lock:
            host_name = self.active_host
            if host_name is None:
                raise ProfileError("choose a host before changing its write mode")
        return self.switch(host_name, write_mode=enabled)

    def write_mode_for(self, host_name: str) -> bool:
        with self._lock:
            return self.write_modes.get(host_name, False)

    def test_host(self, host_name: str) -> str:
        with self._lock:
            profile = self.hosts.get(host_name)
            if profile is None:
                raise ProfileError(f"unknown host {host_name!r}")
            generation = self.generation
            current = self.session
        lease = self.register_worker(generation, current)
        if lease is None:
            raise ProfileError("host transition is in progress")
        temporary: Session | None = None
        credential = None
        try:
            credential = read_credential(profile, self.secret_entries)
            temporary = self.session_opener(profile, secret=credential, insecure_ack=self.insecure_ack,
                                             allow_write=False)
            status = temporary.banner()
            if not lease.cancelled.is_set():
                with self._lock:
                    if generation == self.generation:
                        self.last_status[host_name] = "connected"
            return status
        except Exception as exc:
            safe_error = credential.scrub(str(exc)) if credential is not None else str(exc)
            if not lease.cancelled.is_set():
                with self._lock:
                    if generation == self.generation:
                        self.last_status[host_name] = f"connection failed: {safe_error}"
            raise ProfileError(f"cannot test host {host_name!r}: {safe_error}") from None
        finally:
            if temporary is not None:
                temporary.close()
            self.finish_worker(lease)

    def reconnect_current(self, generation: int, session: Session) -> Session:
        """Reconnect the selected host in place after reboot, retaining its write mode."""
        with self._lock:
            if (self._switching or generation != self.generation or session is not self.session
                    or session.profile.name != self.active_host):
                raise ProfileError("the selected host session is no longer active")
            host_name = self.active_host
            profile = self.hosts[host_name]
            allow_write = self.write_modes.get(host_name, not session.rpc.read_only)
        credential = read_credential(profile, self.secret_entries)
        replacement: Session | None = None
        try:
            replacement = self.session_opener(profile, secret=credential, insecure_ack=self.insecure_ack,
                                              allow_write=allow_write)
            with self._lock:
                if (self._switching or generation != self.generation or session is not self.session
                        or session.profile.name != self.active_host):
                    replacement.close()
                    raise ProfileError("the selected host session is no longer active")
                old_rpc = session.rpc
                session.rpc = replacement.rpc
                session.backend = replacement.backend
                session.insecure_tls = replacement.insecure_tls
                session.missing_methods = replacement.missing_methods
                session.credential = credential
                self.last_status[host_name] = "connected"
            old_rpc.close()
            return session
        except ProfileError:
            raise
        except TransportError as exc:
            safe_error = credential.scrub(str(exc))
            with self._lock:
                if generation == self.generation:
                    self.last_status[host_name] = f"connection failed: {safe_error}"
            raise TransportError(exc.kind, safe_error) from None
        except Exception as exc:
            safe_error = credential.scrub(str(exc))
            with self._lock:
                if generation == self.generation:
                    self.last_status[host_name] = f"connection failed: {safe_error}"
            raise ProfileError(f"cannot reconnect to host {host_name!r}: {safe_error}") from None

    def update_hosts(self, hosts: Mapping[str, Profile], default_host: str | None,
                     secret_entries: Mapping[str, SecretEntry]) -> None:
        if default_host is not None and default_host not in hosts:
            raise ProfileError(f"default host {default_host!r} is not configured")
        with self._lock:
            self.hosts = dict(hosts)
            self.default_host = default_host
            self.secret_entries = dict(secret_entries)
            for name in self.hosts:
                self.last_status.setdefault(name, "not tested")
            for name in tuple(self.last_status):
                if name not in self.hosts:
                    del self.last_status[name]

    def close(self) -> None:
        with self._lock:
            previous_generation = self.generation
            leases = tuple(lease for lease in self._workers.values() if lease.generation == previous_generation)
            for lease in leases:
                lease.cancelled.set()
            session = self.session
            self.generation += 1
            self.session = None
            self.active_host = None
            self._switching = True
        for lease in leases:
            lease.finished.wait()
        if session is not None:
            session.close()
        with self._lock:
            self._switching = False
