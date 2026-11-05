#!/usr/bin/env python3
"""Capture the single view on real sampler data (a tool, not a test).

    PYTHONPATH=src python3 tools/poc/sampler_live_capture.py SOCKET [--size 170x48 ...] [--frames N]

Connects to a sampler socket (a local one, or a remote one forwarded with ``ssh -L``),
drives the real ``ToposApp`` headlessly at each size for N live frames, prints the screen as
plain text and the CPU milliseconds per frame (``time.process_time`` over the whole run,
sleeping excluded). Nothing here asserts on timing.
"""
from __future__ import annotations

import argparse
import asyncio
import time
from pathlib import Path

from topos.config import ToposConfig
from topos.sampler_client import SamplerClient
from topos.sampler_source import SamplerSource
from topos.ui.app import ToposApp


async def capture(socket: Path, size: tuple[int, int], frames: int) -> tuple[str, float, int]:
    source = SamplerSource(SamplerClient(socket, timeout_s=5.0), poll_s=1.0)
    app = ToposApp(source, config=ToposConfig(), source_label="SAMPLER", overlay=source.overlay)
    async with app.run_test(size=size) as pilot:
        while app.frames_received < 1:
            await pilot.pause()
            await asyncio.sleep(0.05)
        start_frames, started = app.frames_received, time.process_time()
        while app.frames_received < start_frames + frames:
            await pilot.pause()
            await asyncio.sleep(0.05)
        cpu_ms = (time.process_time() - started) / (app.frames_received - start_frames) * 1e3
        await pilot.pause()
        strips = app.screen._compositor.render_strips()
        text = "\n".join("".join(s.text for s in strip).rstrip() for strip in strips)
        return text, cpu_ms, len(app._visible_row_keys)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("socket", type=Path)
    parser.add_argument("--size", action="append", default=None, help="WxH, repeatable (default 170x48 260x60)")
    parser.add_argument("--frames", type=int, default=15)
    args = parser.parse_args()
    for spec in args.size or ["170x48", "260x60"]:
        w, h = (int(x) for x in spec.split("x"))
        text, cpu_ms, rows = asyncio.run(capture(args.socket, (w, h), args.frames))
        print(f"===== {w}x{h}  rows={rows}  cpu_per_frame={cpu_ms:.1f} ms over {args.frames} live frames (1 Hz) =====")
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
