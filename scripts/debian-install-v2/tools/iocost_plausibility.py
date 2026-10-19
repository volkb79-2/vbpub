#!/usr/bin/env python3
"""One definition of "these io.cost generator numbers cannot be a real disk".

Shared by BOTH consumers of tools/iocost_coef_gen.py so they cannot drift:
mdt-cockpit's ``mdt-io-baseline.py`` (imports ``plausibility_problem``) and
this directory's ``iocost-calibrate.sh`` (calls the command line below).
MDT installs this file beside the generator in /usr/local/lib/mdt.

Why it exists (MDT-016): the generator measures with ``fio --direct=1``, which
bypasses only the GUEST page cache.  A hypervisor write-back cache still
absorbs the writes, and the mean over a run hides the steady state.  One host
measured 4.3 GB/s read / 2 GB/s write / 94k random IOPS from a virtio disk
that saturated at ~20 MB/s with 35-60 ms write latency.

The rule (deliberately narrow, and honest about what it cannot see):

* Bandwidth is judged on the SEQUENTIAL 1 MiB-class numbers (rbps, wbps).
* IOPS are judged on RANDOM 4 KiB only (rrandiops, wrandiops).  The generator's
  "sequential 4 KiB IOPS" run has request merging disabled, so spinning media
  and small arrays legitimately report 25-50k of them; they are never judged.
* The ceilings apply when the device declares itself rotational: no spindle or
  small array sustains more than 1 GiB/s sequential or 20,000 random 4 KiB
  IOPS, so a rotational device above either is reading a cache.
* ``queue/rotational=1`` is also the DEFAULT of virtio-blk, so a genuinely fast
  virtio disk is refused (fail closed: the cost is a pinned cap, never an
  outage).  The operator who knows better sets
  ``IO_BASELINE_ROTATIONAL_FLAG=ignore`` (host-setup.env, or the environment
  of iocost-calibrate.sh), which skips the ceilings, and should then pin the
  caps from a sustained measurement.  Heuristics that guess "virtual" were
  rejected: they misclassify in both directions, an explicit opt-in does not.
* A non-rotational device is not bounded: this cannot prove a number right.

The real fix is a cache-defeating measurement (``--fsync``, a working set
beyond the visible cache, a steady-state tail); see MDT-017 / TODO.md.
"""
from __future__ import annotations

import argparse
import os
import sys

ROTATIONAL_MAX_BPS = 1024 ** 3
ROTATIONAL_MAX_RANDIOPS = 20000
OVERRIDE_ENV = "IO_BASELINE_ROTATIONAL_FLAG"
OVERRIDE_VALUE = "ignore"

_HINT = (
    "the measurement read a cache (hypervisor write-back), not the disk. virtio-blk reports rotational=1 by "
    f"default; if this disk is genuinely fast set {OVERRIDE_ENV}={OVERRIDE_VALUE} and pin the caps from a "
    "sustained measurement, otherwise pin DEV_IO_*_MAX from a sustained measurement instead"
)


def plausibility_problem(
    *,
    rbps: int,
    wbps: int,
    rrandiops: int,
    wrandiops: int,
    rotational: str,
    flag_ignored: bool | None = None,
    names: dict[str, str] | None = None,
) -> str | None:
    """Return why the numbers are implausible, or None."""
    if flag_ignored is None:
        flag_ignored = os.environ.get(OVERRIDE_ENV, "") == OVERRIDE_VALUE
    if flag_ignored or str(rotational) != "1":
        return None
    label = names or {}
    for key, value, limit, unit in (
        ("rbps", rbps, ROTATIONAL_MAX_BPS, "B/s"),
        ("wbps", wbps, ROTATIONAL_MAX_BPS, "B/s"),
        ("rrandiops", rrandiops, ROTATIONAL_MAX_RANDIOPS, "random 4 KiB IOPS"),
        ("wrandiops", wrandiops, ROTATIONAL_MAX_RANDIOPS, "random 4 KiB IOPS"),
    ):
        if int(value) > limit:
            return (
                f"implausible baseline: {label.get(key, key)}={value} exceeds {limit} {unit} on a "
                f"rotational device; {_HINT}"
            )
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Refuse cache-inflated io.cost generator numbers.")
    parser.add_argument("--rotational", required=True)
    for name in ("rbps", "wbps", "rrandiops", "wrandiops"):
        parser.add_argument(f"--{name}", type=int, required=True)
    args = parser.parse_args(argv)
    problem = plausibility_problem(
        rbps=args.rbps, wbps=args.wbps, rrandiops=args.rrandiops, wrandiops=args.wrandiops,
        rotational=args.rotational,
    )
    if problem:
        print(problem, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
