#!/usr/bin/env python3
"""CPU cost per frame of the real ``ToposApp`` over a recording (a tool, not a test).

    PYTHONPATH=src python3 tools/poc/ui_frame_bench.py RECORDING.jsonl [--frames N] [--size 170x48]

Drives the app headlessly (Textual ``run_test``), feeds the recorded frames one
by one, moves the cursor on every frame and flips the sort order every fifth
frame, then prints CPU milliseconds per frame from ``time.process_time``.
Nothing here asserts on timing.
"""
from __future__ import annotations

import argparse
import asyncio
import resource
import time
from pathlib import Path

from topos.config import ToposConfig
from topos.record.replay import ReplayDriver
from topos.ui.app import ToposApp


async def bench(path: Path, *, frames: int, size: tuple[int, int], settle: float) -> None:
    config = ToposConfig()
    driver = ReplayDriver.from_path(path, config=config)
    recorded = driver.frames
    app = ToposApp((), config=config, replay_driver=driver, replay_step=True, source_label="BENCH")
    async with app.run_test(size=size) as pilot:
        await pilot.pause()
        await asyncio.sleep(settle)
        rows = len(app._visible_row_keys)
        started = time.process_time()
        for i in range(1, frames + 1):
            if i % 5 == 0:
                app.sort_reverse = not app.sort_reverse
            app._apply_frame(recorded[i % len(recorded)])
            app._move_selection(1)
            await pilot.pause()
            await asyncio.sleep(settle)
        elapsed = time.process_time() - started
        # Sleeping is not CPU, so settle time does not inflate the figure.
        print(
            f"frames={frames} rows={rows} size={size[0]}x{size[1]} "
            f"cpu_ms_per_frame={elapsed / frames * 1e3:.1f} "
            f"rss_mib={resource.getrusage(resource.RUSAGE_SELF).ru_maxrss // 1024}"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("recording", type=Path)
    parser.add_argument("--frames", type=int, default=40)
    parser.add_argument("--size", default="170x48", help="WIDTHxHEIGHT of the headless terminal")
    parser.add_argument("--settle", type=float, default=0.25, help="seconds to let each frame render")
    args = parser.parse_args()
    width, height = (int(part) for part in args.size.split("x"))
    asyncio.run(bench(args.recording, frames=args.frames, size=(width, height), settle=args.settle))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
