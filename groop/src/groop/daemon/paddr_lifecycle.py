from __future__ import annotations

import json
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Callable

from groop.config import DamonConfig
from groop.damon.control import (
    DamonControlError,
    DamonSession,
    owned_markers,
    stop_owned_sessions,
    default_state_dir,
)
from groop.damon.paddr import plan_start_paddr_session, start_planned_paddr_session
from groop.damon.passive import DEFAULT_DAMON_ROOT


class PaddrLifecycleOutcome(Enum):
    """Outcome of a ``DaemonPaddrLifecycle.start()`` call."""

    DISABLED = "disabled"
    STARTED = "started"
    ADOPTED = "adopted"


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
    damon_root **and** the referenced kdamond is live (state ``on``,
    operations ``paddr``), the lifecycle adopts it rather than allocating a
    duplicate. A stale marker (kdamond is ``off``) is cleaned up so a fresh
    session can start. A malformed marker, a marker pointing at a
    non-existent kdamond, or a marker whose kdamond is running a different
    monitoring mode raises ``PaddrLifecycleStartError``.

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
    _outcome: PaddrLifecycleOutcome = PaddrLifecycleOutcome.DISABLED

    @property
    def session(self) -> DamonSession | None:
        """The owned paddr session, or None if not started or disabled."""
        return self._session

    @property
    def started(self) -> bool:
        """True if paddr was started (or adopted) on this daemon run."""
        return self._started

    @property
    def outcome(self) -> PaddrLifecycleOutcome:
        """The outcome of the most recent ``start()`` call."""
        return self._outcome

    def start(self) -> None:
        """Start (or adopt) a groop-owned paddr session.

        Raises ``PaddrLifecycleStartError`` on bounded failure. The daemon
        is expected to catch this and continue without paddr.

        Sets ``outcome`` to one of ``PaddrLifecycleOutcome.DISABLED``,
        ``STARTED``, or ``ADOPTED``.
        """
        if not self.config.paddr_enabled:
            self._outcome = PaddrLifecycleOutcome.DISABLED
            return

        root = self.state_dir or default_state_dir()

        # Check for an existing groop-owned paddr marker first (idempotent).
        existing = self._find_existing_groop_paddr(root)
        if existing is not None:
            self._session = existing
            self._started = True
            self._outcome = PaddrLifecycleOutcome.ADOPTED
            return

        # No existing session — plan and start a new one.
        try:
            plan = plan_start_paddr_session(
                damon_root=self.damon_root,
                state_dir=root,
                config=self.config,
                require_root=self.require_root,
                is_root=self.is_root,
            )
        except DamonControlError as exc:
            raise PaddrLifecycleStartError(f"cannot plan paddr session: {exc}") from exc

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
            self._outcome = PaddrLifecycleOutcome.STARTED
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

        if self._outcome is PaddrLifecycleOutcome.ADOPTED:
            # This run verified but did not create the persistent session.
            # Leave it running; explicit `damon stop --all-mine` remains the
            # operator-controlled cleanup path.
            self._session = None
            self._started = False
            self._outcome = PaddrLifecycleOutcome.DISABLED
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
            raise PaddrLifecycleStopError(f"cannot stop paddr session: {exc}") from exc

        self._session = None
        self._started = False
        self._outcome = PaddrLifecycleOutcome.DISABLED
        return stopped

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _kdamond_state(self, idx: int) -> str | None:
        """Read the state of kdamond *idx* under ``self.damon_root``.

        Returns the stripped state string, or ``None`` if the slot does not
        exist.
        """
        state_path = self.damon_root / str(idx) / "state"
        try:
            raw = state_path.read_text(encoding="utf-8")
        except (OSError, FileNotFoundError):
            return None
        return raw.strip()

    def _kdamond_operations(self, idx: int) -> str | None:
        """Read the operations of context 0 for kdamond *idx*.

        Returns the stripped operations string, or ``None`` if the path does
        not exist.
        """
        ops_path = self.damon_root / str(idx) / "contexts" / "0" / "operations"
        try:
            raw = ops_path.read_text(encoding="utf-8")
        except (OSError, FileNotFoundError):
            return None
        return raw.strip()

    def _find_existing_groop_paddr(self, state_dir: Path) -> DamonSession | None:
        """Find an existing groop-owned paddr marker for this damon_root.

        Validates that the referenced kdamond slot exists, is in state
        ``on``, and has operations ``paddr``.  A marker pointing at a
        kdamond whose state is ``off`` is treated as stale — the marker is
        deleted and ``None`` is returned so the caller can start fresh.

        Malformed markers, missing kdamond slots, or kdamond slots running a
        different monitoring mode raise ``PaddrLifecycleStartError``.

        Returns a ``DamonSession`` if a valid live session is found, or
        ``None``.
        """
        for marker in owned_markers(state_dir):
            payload = self._read_marker_payload(marker)

            if payload.get("owner") != "groop":
                continue
            if payload.get("mode") != "paddr":
                continue

            raw_root = payload.get("damon_root")
            if not isinstance(raw_root, str) or not raw_root:
                raise PaddrLifecycleStartError(
                    f"paddr marker {marker} has no valid damon_root"
                )
            marker_root = Path(raw_root)
            if marker_root != self.damon_root:
                continue

            # Found a groop-owned paddr marker for our damon_root.
            idx = payload.get("kdamond_idx")
            marker_idx = _marker_index(marker)
            if not isinstance(idx, int) or isinstance(idx, bool) or idx < 0:
                raise PaddrLifecycleStartError(
                    f"paddr marker {marker} has invalid kdamond_idx"
                )
            if marker_idx != idx:
                raise PaddrLifecycleStartError(
                    f"paddr marker {marker} index does not match kdamond_idx {idx}"
                )

            # Check if the kdamond slot exists.
            state = self._kdamond_state(idx)
            if state is None:
                raise PaddrLifecycleStartError(
                    f"marker {marker} references kdamond-{idx} which does not "
                    f"exist under {self.damon_root}"
                )

            if state == "off":
                # Stale marker: kdamond is not live.  Clean up safely.
                marker.unlink(missing_ok=True)
                return None

            if state != "on":
                raise PaddrLifecycleStartError(
                    f"kdamond-{idx} has unexpected state {state!r} for marker {marker}"
                )

            # Verify operations matches the marker's claimed mode.
            ops = self._kdamond_operations(idx)
            if ops is None:
                raise PaddrLifecycleStartError(
                    f"kdamond-{idx} has no operations path for marker {marker}"
                )
            if ops != "paddr":
                raise PaddrLifecycleStartError(
                    f"kdamond-{idx} operations is {ops!r} but marker "
                    f"{marker} claims paddr mode — refusing adoption"
                )

            return DamonSession(
                entity_key="",
                pids=(),
                kdamond_idx=idx,
                marker_path=marker,
            )

        return None

    def _read_marker_payload(self, marker: Path) -> dict[str, object]:
        """Read and parse a JSON marker file.

        Ownership is uncertain when a marker in groop's marker directory is
        unreadable or malformed, so startup fails closed instead of deleting
        it and potentially orphaning a live session.
        """
        try:
            payload = json.loads(marker.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise PaddrLifecycleStartError(
                f"cannot safely inspect paddr marker {marker}: {exc}"
            ) from exc
        if not isinstance(payload, dict):
            raise PaddrLifecycleStartError(
                f"paddr marker {marker} must contain a JSON object"
            )
        return payload


def _marker_index(marker: Path) -> int | None:
    try:
        return int(marker.stem.rsplit("-", 1)[-1])
    except ValueError:
        return None
