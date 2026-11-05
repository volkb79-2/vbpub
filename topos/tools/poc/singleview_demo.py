#!/usr/bin/env python3
"""Run the real topos single view over a recording, with SYNTHETIC extras for layout judging.

    PYTHONPATH=src python3 tools/poc/singleview_demo.py RECORDING.jsonl [--tick 1.0]

A recording made by ``topos record`` has no per-CPU split, us/sy/io/st, D-state counts or
process rows, so those are invented here (the same generator idea as the ratatui PoC) and
handed to the app as an ``Overlay``. Every invented value is drawn in magenta with the
visible ``demo`` marker. The recording itself is replayed in a loop as a stand-in for a live
feed, so freeze (space) and step (, .) work as they do live.

This file is a PoC tool. Nothing under ``src/topos`` may import it (a test guards that), so
fabricated data cannot be reached from ``topos`` itself.
"""
from __future__ import annotations

import argparse
import dataclasses
import threading
from collections.abc import Iterator
from pathlib import Path

from topos.config import ToposConfig
from topos.model import Frame
from topos.record.replay import ReplayDriver
from topos.ui.app import run_ui
from topos.ui.overlay import ProcRow

NCPU = 8
COMMS = ("python3", "postgres", "java", "node", "dockerd", "containerd-shim", "rg", "cc1plus", "sshd", "rsync", "kworker/u16", "mysqld")


def _hash(text: str) -> int:
    h = 0xCBF29CE484222325
    for b in text.encode():
        h = ((h ^ b) * 0x100000001B3) & 0xFFFFFFFFFFFFFFFF
    return h


def _noise(seed: int) -> float:
    """Deterministic noise in 0..1."""
    z = (seed + 0x9E3779B97F4A7C15) & 0xFFFFFFFFFFFFFFFF
    z = ((z ^ (z >> 30)) * 0xBF58476D1CE4E5B9) & 0xFFFFFFFFFFFFFFFF
    z = ((z ^ (z >> 27)) * 0x94D049BB133111EB) & 0xFFFFFFFFFFFFFFFF
    return ((z ^ (z >> 31)) >> 11) / float(1 << 53)


def _metric(frame: Frame, key: str, name: str, default: float = 0.0) -> float:
    ef = frame.entities.get(key)
    m = ef.metrics.get(name) if ef is not None else None
    return float(m.v) if m is not None and m.v is not None else default


class DemoOverlay:
    """Invented per-CPU split, run/blocked counts and processes. Always labelled ``demo``."""

    label = "demo"

    def __init__(self) -> None:
        self._step = 0

    def _fi(self, frame: Frame) -> int:
        return int(frame.ts)

    def per_cpu(self, frame: Frame) -> list[tuple[float, float, float, float]]:
        busy = sum(
            _metric(frame, key, "cpu_pct") for key, ef in frame.entities.items() if key and ef.entity.parent in (None, "")
        ) / 100.0
        base = busy / NCPU
        fi = self._fi(frame)
        out = []
        for c in range(NCPU):
            tot = min(1.0, max(0.0, base * (0.3 + 1.4 * _noise(c * 131 + fi * 7))))
            sy = 0.12 + 0.15 * _noise(c * 17 + fi)
            io = 0.06 * _noise(c * 29 + fi * 3)
            st = 0.02
            us = 1.0 - sy - io - st
            out.append((tot * us, tot * sy, tot * io, tot * st))
        return out

    def processes(self, frame: Frame) -> dict[str, list[ProcRow]]:
        fi = self._fi(frame)
        out: dict[str, list[ProcRow]] = {}
        for key, ef in frame.entities.items():
            n = min(6, int(_metric(frame, key, "cgroup_procs")))
            if n <= 0:
                continue
            has_kids = any(o.entity.parent == key for o in frame.entities.values())
            cpu = _metric(frame, key, "cpu_pct") * (0.3 if has_kids else 1.0)
            io = _metric(frame, key, "psi_io_some_avg10")
            h = _hash(key)
            rows = []
            for k in range(n):
                w = 0.6 / (1.0 + k * 1.3)
                c = cpu * w * (0.85 + 0.3 * _noise(h ^ (fi * 977 + k)))
                pd = 0.05 + min(io / 20.0, 0.4)
                if k < 2 and _noise(h + (fi // 4) * 31 + k) < pd:
                    state = "D"
                elif c > 1.0:
                    state = "R"
                else:
                    state = "S"
                rows.append(ProcRow(1000 + ((h >> 8) % 60000) + k * 7, COMMS[((h >> 3) + k * 5) % len(COMMS)], c, state))
            out[key] = rows
        return out

    def counts(self, frame: Frame) -> tuple[int, int]:
        procs = self.processes(frame)
        blocked = sum(1 for plist in procs.values() for p in plist if p.state == "D")
        running = sum(1 for plist in procs.values() for p in plist if p.state == "R")
        return running, blocked


class LoopSource:
    """Replays recorded frames forever, one per tick, with strictly increasing timestamps."""

    def __init__(self, frames: list[Frame], tick_s: float) -> None:
        self._frames = frames[1:] if len(frames) > 1 else frames  # frame 0 has no rates yet
        self._tick = tick_s
        self.stop_event = threading.Event()

    def __iter__(self) -> Iterator[Frame]:
        n = 0
        step = max(self._frames[-1].ts - self._frames[0].ts, 1.0) / max(len(self._frames) - 1, 1)
        while not self.stop_event.is_set():
            for frame in self._frames:
                yield dataclasses.replace(frame, ts=self._frames[0].ts + n * step)
                n += 1
                if self.stop_event.wait(self._tick):
                    return


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("recording", type=Path)
    parser.add_argument("--tick", type=float, default=1.0, help="seconds between frames")
    args = parser.parse_args()
    config = ToposConfig()
    driver = ReplayDriver.from_path(args.recording, config=config)
    run_ui(LoopSource(list(driver.frames), args.tick), config=config, source_label="DEMO (synthetic extras)", overlay=DemoOverlay())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
