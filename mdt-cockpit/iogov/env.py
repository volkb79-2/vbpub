"""Injectable clocks, sleeping, randomness and the cooperative stop flag (so every module is testable with fakes)."""

from __future__ import annotations

import os
import random
import signal
import time
from dataclasses import dataclass, field
from typing import Callable


class StopRequested(Exception):
    """A kill switch, SIGTERM/SIGINT, or an abort condition asked the run to stop at the next safe point."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class StopFlag:
    """Set by signal handlers or watchdogs; checked between IOs (a submitted IO cannot be recalled)."""

    def __init__(self) -> None:
        self.reason: str | None = None

    def set(self, reason: str) -> None:
        if self.reason is None:
            self.reason = reason

    def check(self) -> None:
        if self.reason is not None:
            raise StopRequested(self.reason)


@dataclass
class Env:
    wall: Callable[[], float] = time.time
    mono: Callable[[], float] = time.perf_counter
    sleep: Callable[[float], None] = time.sleep
    rng: random.Random = field(default_factory=random.SystemRandom)
    urandom: Callable[[int], bytes] = os.urandom
    stop: StopFlag = field(default_factory=StopFlag)

    def install_signal_handlers(self) -> Callable[[], None]:
        """SIGTERM/SIGINT only set the stop flag; cleanup runs in `finally` blocks. Returns a restore function."""

        previous = {}

        def handler(signum, _frame):
            self.stop.set(f"signal:{signal.Signals(signum).name}")

        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                previous[sig] = signal.signal(sig, handler)
            except ValueError:  # not the main thread
                pass

        def restore() -> None:
            for sig, old in previous.items():
                signal.signal(sig, old)

        return restore
