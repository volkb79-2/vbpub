#!/usr/bin/env python3
"""mdt-psi-guard: pressure on the protected units throttles the dev tiers, never freezes or kills.

A stdlib service that runs OUTSIDE dev.slice. It watches pressure-stall information (PSI):

* the protected units' ``memory.pressure`` ``some``  (10%),
* the host's ``/proc/pressure/memory`` ``full``       (5%),
* the protected units' ``cpu.pressure`` ``some``      (25%),

each held for 10 s before it fires and released only after 60 s below HALF its threshold.
The stall share is the growth of the cumulative ``total=`` counter over the sampling window,
which is exactly what a kernel PSI trigger measures (a trigger would need a pollable fd;
polling the counters keeps the logic deterministic and testable on a fake cgroupfs).

``MDT_PSI_GUARD`` selects the mode:

* ``off``     - exit immediately,
* ``report``  - record events, change nothing (the shipped default),
* ``enforce`` - apply the staged actions below.

Staged actions (every one reverted on release, on stop and on the next start):

1. ``cpu.weight`` and ``io.weight`` of ``dev-gates``, ``dev-background`` and ``dev-buildkitd`` set to 1;
2. 30 s later, if the pressure is still above its threshold, ``memory.high`` of ``dev-gates`` and
   ``dev-background`` lowered to ``max(floor, 0.9 x memory.current)``.

It never freezes or kills anything: a wall-clock budget that expired while frozen would change a
gate's result. Events go to ``<state-dir>/events.jsonl`` (``/run/mdt/psi-guard``), rotated at 1 MiB to
``events.jsonl.1`` because ``/run`` is RAM. The keys the guard currently owns and their originals are
listed in ``<state-dir>/state.json``.

Known limit: systemd re-applies a unit's ``CPUWeight``/``IOWeight``/``MemoryHigh`` on ``daemon-reload``
(including ``install.sh --apply-protection``) and on ``systemctl set-property``. That silently undoes
an active throttle, and the later revert then writes the original the guard captured. The
reconcile sweep writes none of these keys, so it is not an overwriter. Do not reload systemd
during a pressure episode.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import signal
import sys
import time
from pathlib import Path
from typing import Callable

MIB = 1024 * 1024

HOLD_SECONDS = 10
RELEASE_SECONDS = 60
RELEASE_FACTOR = 0.5
ESCALATE_SECONDS = 30
MEMORY_HIGH_FLOOR = 256 * MIB
MEMORY_HIGH_FRACTION = 0.9
# events.jsonl lives on tmpfs (RAM): rotated to events.jsonl.1 at this size, so the two files
# together stay below twice this value. run-gate's reader consumes both.
MAX_EVENT_BYTES = 1024 * 1024

# signal name -> (kind, threshold as a stall fraction of wall time)
SIGNALS = {
    "unit_memory_some": ("some", 0.10),
    "host_memory_full": ("full", 0.05),
    "unit_cpu_some": ("some", 0.25),
}
WEIGHT_TIERS = ("dev-gates.slice", "dev-background.slice", "dev-buildkitd.slice")
HIGH_TIERS = ("dev-gates.slice", "dev-background.slice")
MODES = ("off", "report", "enforce")
# HOST_ROLE=dedicated-dev (MDT-TESTHOST-MEM): the host's own services are watched too, so the dev tier is throttled by
# the pressure the HOST feels, not by a constant limit. Same default list as mdt_capacity.DEDICATED_PROTECTED_UNITS_DEFAULT
# (a test keeps the two equal); DEV_HOST_PROTECTED_UNITS overrides it, `-` = none.
DEDICATED_HOST_UNITS = (
    "docker.service containerd.service cgpd.service mdt-psi-guard.service "
    "systemd-journald.service systemd-oomd.service"
)


def protected_units(cfg: dict[str, str]) -> list[str]:
    """MDT_PROTECTED_UNITS, plus the host services on HOST_ROLE=dedicated-dev (deduplicated, order kept)."""
    units = cfg.get("MDT_PROTECTED_UNITS", "").split()
    if cfg.get("HOST_ROLE", "") == "dedicated-dev":
        raw = cfg.get("DEV_HOST_PROTECTED_UNITS", "").strip()
        extra = [] if raw == "-" else (raw or DEDICATED_HOST_UNITS).split()
        units += [u for u in extra if u not in units]
    return units


def validate_mode(value: str) -> str | None:
    if value in MODES:
        return None
    return f"{value!r} is not valid: use off, report or enforce"


def slice_path(cg_root: Path, name: str) -> Path:
    """``a-b-c.slice`` nests as ``a.slice/a-b.slice/a-b-c.slice`` in cgroupfs."""
    parts = name[: -len(".slice")].split("-")
    path = cg_root
    for i in range(len(parts)):
        path = path / ("-".join(parts[: i + 1]) + ".slice")
    return path


def unit_cgroup(cg_root: Path, unit: str) -> Path | None:
    if unit.endswith(".slice"):
        path = slice_path(cg_root, unit)
        return path if path.is_dir() else None
    direct = cg_root / "system.slice" / unit
    if direct.is_dir():
        return direct
    for found in sorted(cg_root.glob(f"*/{unit}")) + sorted(cg_root.glob(f"*/*/{unit}")):
        if found.is_dir():
            return found
    return None


_PSI_LINE = re.compile(r"^(some|full)\b.*\btotal=(\d+)")


def read_psi_total(path: Path, kind: str) -> int | None:
    """Cumulative stall microseconds of one PSI line; ``None`` when unreadable."""
    try:
        for line in path.read_text().splitlines():
            match = _PSI_LINE.match(line)
            if match and match.group(1) == kind:
                return int(match.group(2))
    except OSError:
        return None
    return None


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text().strip()
    except OSError:
        return None


class Guard:
    """The state machine. ``tick(now)`` samples once; every host access is a path argument."""

    def __init__(
        self,
        *,
        cg_root: Path,
        units: list[str],
        state_dir: Path,
        mode: str = "report",
        proc_pressure: Path = Path("/proc/pressure"),
        memory_high_floor: int = MEMORY_HIGH_FLOOR,
        max_event_bytes: int = MAX_EVENT_BYTES,
    ) -> None:
        self.cg_root = cg_root
        self.units = units
        self.state_dir = state_dir
        self.mode = mode
        self.proc_pressure = proc_pressure
        self.floor = memory_high_floor
        self.last: dict[str, tuple[float, int]] = {}
        self.rate: dict[str, float] = {}
        self.above_since: dict[str, float | None] = {s: None for s in SIGNALS}
        self.below_since: dict[str, float | None] = {s: None for s in SIGNALS}
        self.active: set[str] = set()
        self.unreadable_reported: set[str] = set()
        self.max_event_bytes = max_event_bytes
        self.stage = 0
        self.stage1_at: float | None = None
        self.owned: dict[str, dict[str, str]] = {}  # "<tier>/<file>" -> {"path", "original"}

    # ------------------------------------------------------------------ events

    def event(self, name: str, now: float, **fields) -> None:
        record = {"ts": round(now, 3), "event": name, "mode": self.mode, **fields}
        self.state_dir.mkdir(parents=True, exist_ok=True)
        log = self.state_dir / "events.jsonl"
        try:
            if log.stat().st_size >= self.max_event_bytes:
                os.replace(log, self.state_dir / "events.jsonl.1")  # /run is RAM: keep at most two files
        except OSError:
            pass
        with log.open("a") as handle:
            handle.write(json.dumps(record, sort_keys=True) + "\n")

    def _save_state(self) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        payload = {"owned": self.owned, "stage": self.stage}
        tmp = self.state_dir / "state.json.tmp"
        tmp.write_text(json.dumps(payload, sort_keys=True, indent=1) + "\n")
        os.replace(tmp, self.state_dir / "state.json")

    def owned_keys(self) -> list[str]:
        return sorted(self.owned)

    # ------------------------------------------------------------ pressure input

    def _sources(self) -> dict[str, list[tuple[Path, str]]]:
        unit_paths = [p for p in (unit_cgroup(self.cg_root, u) for u in self.units) if p]
        return {
            "unit_memory_some": [(p / "memory.pressure", "some") for p in unit_paths],
            "host_memory_full": [(self.proc_pressure / "memory", "full")],
            "unit_cpu_some": [(p / "cpu.pressure", "some") for p in unit_paths],
        }

    def _sample(self, now: float) -> set[str]:
        """Update ``self.rate`` (worst stall share over the sources) for every signal.

        Counters are keyed by the source's path, never by list position: a unit whose cgroup
        disappears or appears must not have its counter compared with another unit's. Returns
        the signals with NO readable source this tick; their rate is dropped (never kept stale).
        """
        unreadable: set[str] = set()
        seen: set[str] = set()
        for name, sources in self._sources().items():
            worst = None
            readable = False
            for path, kind in sources:
                total = read_psi_total(path, kind)
                if total is None:
                    continue
                readable = True
                key = f"{name}:{path}"
                seen.add(key)
                if key in self.last:
                    t0, total0 = self.last[key]
                    if now > t0:
                        share = max(0.0, (total - total0) / 1e6 / (now - t0))
                        worst = share if worst is None else max(worst, share)
                self.last[key] = (now, total)
            if worst is not None:
                self.rate[name] = worst
            elif not readable:
                self.rate.pop(name, None)
                unreadable.add(name)
        for key in [k for k in self.last if k not in seen]:
            del self.last[key]  # a vanished source must not leave a baseline for its return
        return unreadable

    # ---------------------------------------------------------------- the machine

    def tick(self, now: float) -> None:
        if self.mode == "off":
            return
        unreadable = self._sample(now)
        for name in sorted(unreadable):
            # No readable source: unknown pressure must never keep the tiers throttled.
            self.above_since[name] = None
            self.below_since[name] = None
            if name in self.active:
                self.active.discard(name)
                self.event("unreadable", now, signal=name, was_active=True)
                self.event("released", now, signal=name, share=0.0, reason="unreadable")
            elif name not in self.unreadable_reported:
                self.event("unreadable", now, signal=name, was_active=False)
        self.unreadable_reported = set(unreadable)
        for name, (_, threshold) in SIGNALS.items():
            rate = self.rate.get(name)
            if rate is None:
                continue
            if rate >= threshold:
                self.below_since[name] = None
                if self.above_since[name] is None:
                    self.above_since[name] = now
                if name not in self.active and now - self.above_since[name] >= HOLD_SECONDS:
                    self.active.add(name)
                    self.event("pressure", now, signal=name, share=round(rate, 4), threshold=threshold)
            else:
                self.above_since[name] = None
                if name in self.active and rate < threshold * RELEASE_FACTOR:
                    if self.below_since[name] is None:
                        self.below_since[name] = now
                    if now - self.below_since[name] >= RELEASE_SECONDS:
                        self.active.discard(name)
                        self.below_since[name] = None
                        self.event("released", now, signal=name, share=round(rate, 4))
                elif name in self.active:
                    self.below_since[name] = None  # between half and full threshold: not yet released
        if self.active:
            self._escalate(now)
        elif self.stage or self.owned:
            self.revert(now, reason="released")

    def _above_now(self) -> bool:
        return any(self.rate.get(n, 0.0) >= SIGNALS[n][1] for n in self.active)

    def _escalate(self, now: float) -> None:
        if self.stage == 0:
            self.stage = 1
            self.stage1_at = now
            self._stage1(now)
        elif self.stage == 1 and self.stage1_at is not None \
                and now - self.stage1_at >= ESCALATE_SECONDS and self._above_now():
            self.stage = 2
            self._stage2(now)

    def _write(self, tier: str, filename: str, value: str, now: float) -> None:
        path = slice_path(self.cg_root, "dev.slice").joinpath(tier, filename)
        key = f"{tier}/{filename}"
        original = _read_text(path)
        if original is not None and filename == "io.weight":
            original = original.splitlines()[0] if original else original  # the "default N" line only
        if original is None:
            self.event("skip", now, key=key, reason="not readable")
            return
        if self.mode == "report":
            self.event("would_apply", now, key=key, value=value, original=original)
            return
        if key not in self.owned:
            self.owned[key] = {"path": str(path), "original": original}
        try:
            path.write_text(value + "\n")
        except OSError as exc:
            self.event("error", now, key=key, error=str(exc))
            return
        self.event("applied", now, key=key, value=value, original=original)

    def _stage1(self, now: float) -> None:
        for tier in WEIGHT_TIERS:
            self._write(tier, "cpu.weight", "1", now)
            io = slice_path(self.cg_root, "dev.slice") / tier / "io.weight"
            if io.exists():
                self._write(tier, "io.weight", "default 1", now)
        self._save_state()

    def _stage2(self, now: float) -> None:
        for tier in HIGH_TIERS:
            current = _read_text(slice_path(self.cg_root, "dev.slice") / tier / "memory.current")
            if current is None or not current.isdigit():
                continue
            target = max(self.floor, int(int(current) * MEMORY_HIGH_FRACTION))
            self._write(tier, "memory.high", str(target), now)
        self._save_state()

    # ---------------------------------------------------------------- reverting

    def revert(self, now: float, reason: str) -> None:
        """Put back every value the guard changed; idempotent."""
        for key, info in sorted(self.owned.items()):
            try:
                Path(info["path"]).write_text(info["original"] + "\n")
            except OSError as exc:
                self.event("error", now, key=key, error=str(exc))
                continue
            self.event("reverted", now, key=key, value=info["original"], reason=reason)
        self.owned.clear()
        self.stage = 0
        self.stage1_at = None
        self._save_state()

    def recover(self, now: float) -> None:
        """At start: revert whatever a previous run left owned (a crash, a kill)."""
        state = self.state_dir / "state.json"
        try:
            previous = json.loads(state.read_text())
        except (OSError, ValueError):
            return
        owned = previous.get("owned") or {}
        if not owned:
            return
        self.owned = {k: v for k, v in owned.items() if isinstance(v, dict) and "path" in v and "original" in v}
        self.revert(now, reason="boot")


def parse_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    try:
        text = path.read_text()
    except OSError:
        return values
    for line in text.splitlines():
        match = re.match(r"^([A-Z][A-Z0-9_]*)=(.*)$", line)
        if match:
            v = match.group(2).strip()
            if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
                v = v[1:-1]
            values[match.group(1)] = v
    return values


def run(
    guard: Guard,
    *,
    interval: float = 1.0,
    max_ticks: int | None = None,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
    stop: Callable[[], bool] = lambda: False,
) -> None:
    guard.recover(clock())
    ticks = 0
    try:
        while not stop():
            guard.tick(clock())
            ticks += 1
            if max_ticks is not None and ticks >= max_ticks:
                break
            sleep(interval)
    finally:
        guard.revert(clock(), reason="stop")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="PSI guard for the capacity MDT shares with other services")
    ap.add_argument("--config", type=Path, default=Path("/etc/mdt/host-setup.env"))
    ap.add_argument("--cgroup-root", type=Path, default=Path("/sys/fs/cgroup"))
    ap.add_argument("--proc-pressure", type=Path, default=Path("/proc/pressure"))
    ap.add_argument("--state-dir", type=Path, default=Path("/run/mdt/psi-guard"))
    ap.add_argument("--interval", type=float, default=1.0)
    ap.add_argument("--max-ticks", type=int, default=None)
    ap.add_argument("--mode", choices=MODES, default=None, help="override MDT_PSI_GUARD")
    args = ap.parse_args(argv)
    cfg = parse_env(args.config)
    mode = args.mode or cfg.get("MDT_PSI_GUARD", "report")
    error = validate_mode(mode)
    if error:
        print(f"ERROR: MDT_PSI_GUARD: {error}", file=sys.stderr)
        return 2
    units = protected_units(cfg)
    guard = Guard(cg_root=args.cgroup_root, units=units, state_dir=args.state_dir, mode=mode,
                  proc_pressure=args.proc_pressure)
    if mode == "off":
        guard.recover(time.monotonic())  # leave nothing owned by an earlier run
        print("mdt-psi-guard: MDT_PSI_GUARD=off; nothing to do")
        return 0
    if not units:
        print("mdt-psi-guard: no protected units (MDT_PROTECTED_UNITS empty, not HOST_ROLE=dedicated-dev); only the host memory signal is watched", file=sys.stderr)
    stopping = {"flag": False}
    signal.signal(signal.SIGTERM, lambda *_: stopping.__setitem__("flag", True))
    signal.signal(signal.SIGINT, lambda *_: stopping.__setitem__("flag", True))
    run(guard, interval=args.interval, max_ticks=args.max_ticks, stop=lambda: stopping["flag"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
