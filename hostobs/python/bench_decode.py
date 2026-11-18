#!/usr/bin/env python3
"""Print the decode timings of WIRE.md 7.8 (trend evidence only, never a gate).

    nice -n 19 python3 -I python/bench_decode.py

Min of 7 repeats of 200 decodes (microseconds per decode) for the steady-state frames (bare
blocks against a held dictionary, two distinct rows bodies alternating) and the
dictionary-bearing frames (dictionary decoded every time), at 100 and 1,000 cgroups, plus
`measured_list()` of one 1,000-row column.
"""

from __future__ import annotations

import platform
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "tests"))
sys.path.insert(0, str(HERE / "reference"))

import hostobs_wire_ref as ref  # noqa: E402
import sp4_frames  # noqa: E402
from hostobs_client.wire import Dictionary, decode_block  # noqa: E402

REPEATS, LOOPS = 7, 200


def best(fn) -> float:
    times = []
    for _ in range(REPEATS):
        start = time.perf_counter()
        for _ in range(LOOPS):
            fn()
        times.append((time.perf_counter() - start) / LOOPS * 1e6)
    return min(times)


def main() -> None:
    print(f"python {sys.version.split()[0]} ({sys.version.splitlines()[0]}) on {platform.platform()} {platform.machine()}")
    for n in (100, 1000):
        held = Dictionary.decode(ref.encode_dictionary(sp4_frames.dictionary(n)))
        bare = [bytearray(sp4_frames.frame(n, v)) for v in (0, 1)]
        withdict = [bytearray(sp4_frames.frame(n, v, with_dictionary=True)) for v in (0, 1)]
        decode_block(bare[0], held)
        state = [0]

        def steady():
            state[0] ^= 1
            decode_block(bare[state[0]], held)

        def with_dictionary():
            state[0] ^= 1
            decode_block(withdict[state[0]], None)

        print(f"steady-state {len(bare[0]):>7} B, {n:>4} rows: {best(steady):8.1f} us per decode")
        print(f"dictionary   {len(withdict[0]):>7} B, {n:>4} rows: {best(with_dictionary):8.1f} us per decode")
        if n == 1000:
            column = decode_block(bare[0], held).column("mem.current")
            print(f"measured_list() of one {n}-row column: {best(column.measured_list):8.1f} us")


if __name__ == "__main__":
    main()
