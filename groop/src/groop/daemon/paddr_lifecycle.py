from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from groop.config import DamonConfig
from groop.damon.control import (
    DamonControlError,
    DamonSession,
    stop_owned_sessions,
    _owned_markers,
    _read_json,
    default_state_dir,
)
from groop.damon.paddr import plan_start_paddr_session, start_planned_paddr_session
from groop.damon.passive import DEFAULT_DAMON_ROOT


class DamonPaddrLifecycleError(RuntimeError):
    """Bounded, explicit failure in daemon paddr lifecycle."""


class PaddrLifecycleStartError(DamonPaddrLifecycleError):
    """Startup failed but the daemon can continue without paddr."""


class PaddrLifecycleStopError(DamonPaddrLifecycleError):
    """Shutdown of daemon-owned paddr session failed."""


@dataclass
class DaemonPaddrLifecycle:
    """Daemon-owned paddr session lifecycle.

    The daemon creates one instance at startup. When ``paddr_enabled`` is
    true, ``start()`` plans and starts exactly one groop-owned whole-host paddr
    session. On graceful shutdown ``stop()`` tears down only the session
    owned by this daemon run.

    Idempotent restart: if a groop-owned paddr marker exists for the same
    damon_root, the lifecycle adopts it rather than allocating a duplicate.
    Foreign sessions are never touched.

    Startup failure is bounded: the daemon logs or prints the error and
    continues without paddr. The daemon itself remains usable.
    """

    damon_root: Path = DEFAULT_DAMON_ROOT
    state_dir: Path | None = None
    config: DamonConfig = DamonConfig()
    now: Callable[[], float] = time.time
    require_root: bool = True
    is_root: Callable[[], bool] | None = None  # injectable root check

    _session: DamonSession | None = None
    _started: bool = False

    @property
    def session(self) -> DamonSession | None:
        """The owned paddr session, or None if not started or disabled."""
        return self._session

    @property
    def started(self) -> bool:
        """True if paddr was started (or adopted) on this daemon run."""
        return self._started

    def start(self) -> None:
        """Start (or adopt) a groop-owned paddr session.

        Raises ``PaddrLifecycleStartError`` on bounded failure. The daemon
        is expected to catch this and continue without paddr.
        """
        if not self.config.paddr_enabled:
            return

        root = self.state_dir or default_state_dir()

        # Check for an existing groop-owned paddr marker first (idempotent).
        existing = self._find_existing_groop_paddr(root)
        if existing is not None:
            self._session = existing
            self._started = True
            return

        # No existing session - plan and start a new one.
        try:
            plan = plan_start_paddr_session(
                damon_root=self.damon_root,
                state_dir=root,
                config=self.config,
                require_root=self.require_root,
                is_root=self.is_root,
            )
        except DamonControlError as exc:
            raise PaddrLifecycleStartError(
                f"cannot plan paddr session: {exc}"
            ) from exc

        try:
            self._session = start_planned_paddr_session(
                plan,
                confirmed_text="START",  # config = operator authorization
                now=self.now,
                user="daemon",
                require_root=self.require_root,
                is_root=self.is_root,
            )
            self._started = True
        except DamonControlError as exc:
            raise PaddrLifecycleStartError(
                f"cannot start paddr session: {exc}"
            ) from exc

    def stop(self) -> int:
        """Stop this daemon run's owned paddr session.

        Only stops sessions owned by the groop marker that this lifecycle
        started (or adopted). Never stops foreign sessions.

        Returns the number of sessions stopped (0 or 1). Raises
        ``PaddrLifecycleStopError`` only for unexpected conditions.
        """
        if not self._started or self._session is None:
            return 0

        root = self.state_dir or default_state_dir()

        try:
            stopped = stop_owned_sessions(
                damon_root=self.damon_root,
                state_dir=root,
                all_mine=False,
                kdamond_idx=self._session.kdamond_idx,
                now=self.now,
                user="daemon",
                require_root=self.require_root,
                is_root=self.is_root,
            )
        except DamonControlError as exc:
            raise PaddrLifecycleStopError(
                f"cannot stop paddr session: {exc}"
            ) from exc

        self._session = None
        self._started = False
        return stopped

    def _find_existing_groop_paddr(self, state_dir: Path) -> DamonSession | None:
        """Find an existing groop-owned paddr marker for this damon_root.

        Returns a DamonSession if one exists, or None if none is found.
        Foreign sessions are never touched.
        """
        for marker in _owned_markers(state_dir):
            payload = _read_json(marker)
            if payload.get("owner") != "groop":
                continue
            if payload.get("mode") != "paddr":
                continue
            marker_root = Path(str(payload.get("damon_root", str(self.damon_root))))
            if marker_root != self.damon_root:
                continue
            # Found a groop-owned paddr marker for our damon_root — adopt it.
            idx = int(payload.get("kdamond_idx", -1))
            if idx < 0:
                continue
            return DamonSession(
                entity_key="",
                pids=(),
                kdamond_idx=idx,
                marker_path=marker,
            )
        return None
