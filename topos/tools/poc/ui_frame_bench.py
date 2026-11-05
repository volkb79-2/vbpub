#!/usr/bin/env python3
"""CPU cost per frame of the real ``ToposApp`` over a recording (a tool, not a test).

    PYTHONPATH=src python3 tools/poc/ui_frame_bench.py RECORDING.jsonl [--frames N] [--size WxH ...]

Measures the whole single view (meters, tree, side pane, status) at 100x30, 170x48 and
240x60 by default and prints CPU ms per frame next to the ratatui PoC's figures. When a size
exceeds 50 ms per frame it profiles that size and prints what dominates.

Drives the app headlessly (Textual ``run_test``), feeds the recorded frames one
by one, moves the cursor on every frame and flips the sort order every fifth
frame, then prints CPU milliseconds per frame from ``time.process_time``.
Nothing here asserts on timing.
"""
from __future__ import annotations

import argparse
import asyncio
import cProfile
import io
import pstats
import resource
import time
from pathlib import Path

from topos.config import ToposConfig
from topos.record.replay import ReplayDriver
from topos.ui.app import ToposApp

RATATUI_MS = {(100, 30): 0.69, (170, 48): 1.22, (240, 60): 1.89}
SLOW_MS = 50.0


async def bench(path: Path, *, frames: int, size: tuple[int, int], settle: float, profile: bool = False) -> float:
    config = ToposConfig()
    driver = ReplayDriver.from_path(path, config=config)
    recorded = driver.frames
    app = ToposApp((), config=config, replay_driver=driver, replay_step=True, source_label="BENCH")
    async with app.run_test(size=size) as pilot:
        await pilot.pause()
        await asyncio.sleep(settle)
        rows = len(app._visible_row_keys)
        profiler = cProfile.Profile()
        if profile:
            profiler.enable()
        started = time.process_time()
        for i in range(1, frames + 1):
            if i % 5 == 0:
                app.sort_reverse = not app.sort_reverse
            app._apply_frame(recorded[i % len(recorded)])
            app._move_selection(1)
            await pilot.pause()
            await asyncio.sleep(settle)
        elapsed = time.process_time() - started
        profiler.disable()
        per_frame = elapsed / frames * 1e3
        # Sleeping is not CPU, so settle time does not inflate the figure.
        ref = RATATUI_MS.get(size)
        print(
            f"frames={frames} rows={rows} size={size[0]}x{size[1]} "
            f"cpu_ms_per_frame={per_frame:.1f}"
            + (f" ratatui_ms={ref}" if ref is not None else "")
            + f" rss_mib={resource.getrusage(resource.RUSAGE_SELF).ru_maxrss // 1024}"
        )
        if profile:
            out = io.StringIO()
            pstats.Stats(profiler, stream=out).sort_stats("tottime").print_stats(14)
            print("  profiled run (cProfile inflates CPU time; the epoll line is idle waiting, not CPU):")
            print("\n".join("  " + line for line in out.getvalue().splitlines() if line.strip()))
        return per_frame


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("recording", type=Path)
    parser.add_argument("--frames", type=int, default=40)
    parser.add_argument("--size", action="append", help="WIDTHxHEIGHT of the headless terminal (repeatable; default 100x30 170x48 240x60)")
    parser.add_argument("--settle", type=float, default=0.25, help="seconds to let each frame render")
    parser.add_argument("--profile", action="store_true", help="always add a profiled run (done automatically above 50 ms per frame)")
    args = parser.parse_args()
    for text in args.size or ["100x30", "170x48", "240x60"]:
        width, height = (int(part) for part in text.split("x"))
        per_frame = asyncio.run(bench(args.recording, frames=args.frames, size=(width, height), settle=args.settle))
        if per_frame > SLOW_MS or args.profile:
            asyncio.run(bench(args.recording, frames=args.frames, size=(width, height), settle=args.settle, profile=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
