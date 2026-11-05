"""The sampler as a frame source for the UI, and the choice between it and local collection.

``SamplerSource`` is re-iterable (the UI asks for a fresh stream after a failure) and runs
entirely on the UI's worker thread: every socket wait is bounded by the client's timeouts,
and a sampler that stops producing ticks is reported as an error instead of freezing the
last frame as if it were live.

``choose_source`` implements the zero-argument rule: use the sampler when it answers
``hello``, otherwise fall back to local collection and say why; ``sampler`` fails closed.
"""
from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from topos.config import ToposConfig
from topos.model import Entity, Frame
from topos.sampler_adapter import SamplerOverlay, convert_current
from topos.sampler_client import SamplerClient, SamplerError, SamplerRemoteError, SamplerTimeout

SOURCE_MODES = ("auto", "sampler", "local")
BACKFILL_WINDOW_S = 60
AUX_EVERY_S = 5.0
EVENTS_WINDOW_S = 300
MIN_POLL_S = 0.25
_BACKFILL = (
    ("host_psi_cpu_some_avg10", "psi.cpu.some.avg10"),
    ("host_psi_mem_some_avg10", "psi.memory.some.avg10"),
    ("host_psi_io_some_avg10", "psi.io.some.avg10"),
)


class SourceUnavailable(Exception):
    """An explicitly requested source cannot be used (fail closed)."""


class SamplerSource:
    """Frames from a sampler socket; see the module docstring."""

    def __init__(
        self,
        client: SamplerClient,
        *,
        enrich: Callable[[dict[str, Entity]], dict[str, Entity]] | None = None,
        poll_s: float | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.client = client
        self.enrich = enrich
        self.poll_s = poll_s
        self.stop_event = threading.Event()
        self.backfill: dict[str, list[tuple[float, float]]] = {}
        self.hello: dict[str, Any] | None = None
        self.overlay = SamplerOverlay(client.socket_path)
        self._clock = clock

    def retry_delay(self, attempt: int) -> float:
        return min(10.0, 1.0 + attempt)

    def __iter__(self) -> Iterator[Frame]:
        return self._stream()

    # ── pieces ────────────────────────────────────────────────────────

    def _load_backfill(self) -> None:
        """Sparkline history from ``series`` so the trends are not empty at the first frame."""
        fresh: dict[str, list[tuple[float, float]]] = {}
        for host_key, metric in _BACKFILL:
            try:
                answer = self.client.series("cg:/", metric, tier="hot", window_s=BACKFILL_WINDOW_S)
            except SamplerRemoteError:
                continue  # store disabled or metric unknown: the trend simply starts live
            points: list[tuple[float, float]] = []
            for point in answer.get("points") or ():
                if isinstance(point, list) and len(point) >= 2 and isinstance(point[1], (int, float)) and not isinstance(point[1], bool):
                    points.append((float(point[0]) / 1000.0, float(point[1])))
            fresh[host_key] = points
        self.backfill = fresh

    def _aux(self) -> tuple[dict[str, Any] | None, list[dict[str, Any]] | None]:
        """Sampler health and recent hard events; either may be missing without breaking the view."""
        status = events = None
        try:
            status = self.client.status()
        except SamplerRemoteError:
            pass
        try:
            events = self.client.events(window_s=EVENTS_WINDOW_S, limit=50).get("events")
        except SamplerRemoteError:
            pass
        return status, events

    def _stream(self) -> Iterator[Frame]:
        client = self.client
        hello = self.hello = client.hello()
        client.take_epoch_changes()  # the epoch at start is the baseline, not a change
        self._load_backfill()
        tick_s = float(hello.get("tick_ms") or 1000) / 1000.0
        poll = self.poll_s if self.poll_s is not None else max(MIN_POLL_S, tick_s)
        stale_after = max(5.0 * tick_s, 5.0)
        last_tick: Any = None
        last_progress = self._clock()
        next_aux = 0.0
        status = events = None
        changed_ts: float | None = None
        while not self.stop_event.is_set():
            cur = client.current(procs=True)
            ts = float(cur["ts_ms"]) / 1000.0
            if client.take_epoch_changes():
                hello = self.hello = client.hello()
                self._load_backfill()
                changed_ts, last_tick, status, events, next_aux = ts, None, None, None, 0.0
            if cur.get("tick") != last_tick:
                last_tick, last_progress = cur.get("tick"), self._clock()
                if self._clock() >= next_aux:
                    status, events = self._aux()
                    next_aux = self._clock() + AUX_EVERY_S
                    if client.take_epoch_changes():  # restarted between the two requests
                        changed_ts, last_tick, next_aux = ts, None, 0.0
                yield convert_current(
                    cur,
                    hello=hello,
                    status=status,
                    events=events,
                    epoch_changed_ts=changed_ts,
                    socket_path=str(client.socket_path),
                    enrich=self.enrich,
                )
            elif self._clock() - last_progress > stale_after:
                raise SamplerTimeout(f"the sampler answers but has produced no new tick for {stale_after:.0f}s")
            if self.stop_event.wait(poll):
                return


# ── source choice ─────────────────────────────────────────────────────


def local_namespace_note(cgroup_root: Path, proc_root: Path = Path("/proc"), socket_path: Path | str = "") -> str | None:
    """A sentence when the local collector can only see its own cgroup namespace, else ``None``.

    Evidence, all of it required: this process sits at ``/`` of its cgroup tree, and the tree
    has neither ``init.scope`` nor ``system.slice`` (a real host root always has them).
    """
    try:
        text = (proc_root / "self" / "cgroup").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    paths = [line.split(":", 2)[2] for line in text.splitlines() if line.startswith("0::")]
    if paths != ["/"]:
        return None
    if (cgroup_root / "init.scope").exists() or (cgroup_root / "system.slice").exists():
        return None
    return (
        "NAMESPACED cgroup view: this process sees only its own cgroup namespace (it sits at /), "
        "so the tree and totals are this container's slice, not the host's; "
        f"for the whole host connect to the daemon socket on the host ({socket_path or 'see [sampler] socket_path'})"
    )


@dataclass(frozen=True)
class Choice:
    kind: str
    """``sampler`` or ``local``."""
    source: SamplerSource | None
    overlay: SamplerOverlay | None
    label: str
    note: str
    """Shown on screen next to the data: the reason for a fallback, a namespaced warning."""


def probe_sampler(config: ToposConfig, *, timeout_s: float | None = None) -> tuple[SamplerClient, dict[str, Any]]:
    """Connect and ask ``hello``; any failure is a :class:`SamplerError`."""
    client = SamplerClient(config.sampler.socket_path, timeout_s=config.sampler.timeout_s if timeout_s is None else timeout_s)
    try:
        return client, client.hello()
    except SamplerError:
        client.close()
        raise


def choose_source(
    mode: str,
    config: ToposConfig,
    *,
    enrich: Callable[[dict[str, Entity]], dict[str, Entity]] | None = None,
    local_forced: str | None = None,
    proc_root: Path = Path("/proc"),
    probe_timeout_s: float = 1.0,
) -> Choice:
    """Pick the live source.

    *mode* is ``auto`` (sampler if it answers, else local with the reason on screen),
    ``sampler`` (fail closed) or ``local``. *local_forced* is a reason local collection is
    the only possible source (for example ``--cgroup-root``); it makes ``auto`` skip the
    sampler and ``sampler`` an error.
    """
    if mode not in SOURCE_MODES:
        raise SourceUnavailable(f"unknown --source {mode!r}")
    path = config.sampler.socket_path
    reason = ""
    if mode == "local":
        reason = ""
    elif local_forced is not None:
        if mode == "sampler":
            raise SourceUnavailable(f"--source sampler cannot be combined with {local_forced}")
        reason = f"local collection: {local_forced}"
    else:
        try:
            client, _hello = probe_sampler(config, timeout_s=min(probe_timeout_s, config.sampler.timeout_s))
        except SamplerError as exc:
            if mode == "sampler":
                raise SourceUnavailable(f"sampler at {path} is not available: {exc}") from exc
            reason = f"sampler unavailable ({exc}); showing local collection"
        else:
            client.timeout_s = config.sampler.timeout_s
            source = SamplerSource(client, enrich=enrich)
            return Choice("sampler", source, source.overlay, "SAMPLER", "")
    notes = [reason] if reason else []
    ns = local_namespace_note(config.cgroup_root, proc_root, path)
    if ns is not None:
        notes.append(ns)
    return Choice("local", None, None, "LOCAL", "; ".join(notes))
