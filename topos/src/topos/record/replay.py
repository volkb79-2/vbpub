from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

from topos.config import ToposConfig, load
from topos.diag import annotate as annotate_frame_diagnostics
from topos.model import Frame
from topos.record.reader import iter_frames


@dataclass(frozen=True)
class ReplayFrame:
    index: int
    total: int
    frame: Frame
    delay_s: float


class ReplayDriver:
    def __init__(self, frames: list[Frame]) -> None:
        if not frames:
            raise ValueError("replay requires at least one frame")
        self._frames = frames
        self._index = 0

    @classmethod
    def from_path(cls, path: Path, *, config: ToposConfig | None = None) -> ReplayDriver:
        replay_config = config or load()
        frames = list(iter_frames(path))
        for frame in frames:
            annotate_frame_diagnostics(
                frame,
                replay_config,
                preserve_existing_findings=True,
                preserve_existing_pressure=True,
            )
        return cls(frames)

    @property
    def frames(self) -> tuple[Frame, ...]:
        return tuple(self._frames)

    @property
    def index(self) -> int:
        return self._index

    @property
    def total(self) -> int:
        return len(self._frames)

    @property
    def current(self) -> Frame:
        return self._frames[self._index]

    def seek(self, index: int) -> Frame:
        self._index = min(max(0, index), len(self._frames) - 1)
        return self.current

    def seek_timestamp(self, ts: float) -> Frame:
        """Seek to the first frame whose ts >= ts; clamp to first/last."""
        for index, frame in enumerate(self._frames):
            if frame.ts >= ts:
                self._index = index
                return self.current
        self._index = len(self._frames) - 1
        return self.current

    def step(self, delta: int) -> Frame:
        return self.seek(self._index + delta)

    def play(self, *, speed: float = 1.0, step: bool = False):
        if speed <= 0:
            raise ValueError("replay speed must be positive")
        previous_ts: float | None = None
        for index, frame in enumerate(self._frames):
            delay_s = 0.0 if previous_ts is None or step else max(0.0, (frame.ts - previous_ts) / speed)
            if delay_s:
                time.sleep(delay_s)
            self._index = index
            yield ReplayFrame(index=index, total=len(self._frames), frame=frame, delay_s=delay_s)
            previous_ts = frame.ts


def format_frame_summary(replay_frame: ReplayFrame) -> str:
    frame = replay_frame.frame
    return (
        f"frame {replay_frame.index + 1}/{replay_frame.total} "
        f"ts={frame.ts:.3f} interval={frame.interval_s:.3f} "
        f"entities={len(frame.entities)} host_metrics={len(frame.host)}"
    )
