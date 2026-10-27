#!/usr/bin/env python3
"""Timing and identity harness for glyphgrid (fast paths vs the reference implementation, GlyphReader vs fresh reads).

  python3 bench_glyphgrid.py --series DIR [--stride N]                 per-stage time per frame (median / p95 / max), peak RSS
  python3 bench_glyphgrid.py --series DIR --reference                  same with the unoptimised reference code
  python3 bench_glyphgrid.py --series DIR --check [--stride N]         fast == reference on every frame (slow: runs both)
  python3 bench_glyphgrid.py --series DIR --reader [--max-dirty F]     sequential GlyphReader vs fresh read_image per frame
  python3 bench_glyphgrid.py --series DIR --typing                     typical console update: one text row of a real frame changes

Run it single-process under ``nice -n 10`` (the dev host is shared).  DIR holds the series PNGs (the 242-frame TrueNAS series lives
outside git).  The atlas is the committed fonts atlas merged with the committed learned atlas.
"""
from __future__ import annotations

import argparse
import collections
import glob
import json
import os
import resource
import statistics
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import glyphgrid as gg  # noqa: E402

FIX = os.path.join(HERE, "..", "..", "tests", "fixtures", "glyphgrid")
STAGES = ("load_image", "probe_scale", "detect_grid", "choose_extraction", "decode")
# the whole result dict is compared except bookkeeping: ``session``, and ``grid_report`` (length/contrast/candidates of the detect_grid run that
# ESTABLISHED the cached geometry; an incremental frame runs no search, so it differs from a fresh read by design)
NOT_COMPARED = ("session", "grid_report")


def atlas_default():
    return gg.Atlas.merge(gg.Atlas.load(os.path.join(FIX, "fonts.atlas.json")),
                          gg.Atlas.load(os.path.join(FIX, "truenas-27-vanilla.atlas.corrected-gt.json")))


def canon(res):
    return json.dumps(res, sort_keys=True, default=str)


def pct(xs, p):
    xs = sorted(xs)
    return xs[min(int(p * len(xs)), len(xs) - 1)]


def summary(name, xs):
    print("%-22s n=%-4d median %7.1f ms  p95 %7.1f ms  max %7.1f ms  total %6.1f s" % (
        name, len(xs), 1000 * statistics.median(xs), 1000 * pct(xs, 0.95), 1000 * max(xs), sum(xs)))


def timed_stages():
    acc = collections.defaultdict(float)
    for name in STAGES:
        fn = getattr(gg, name)

        def wrap(*a, __fn=fn, __n=name, **k):
            t = time.perf_counter()
            try:
                return __fn(*a, **k)
            finally:
                acc[__n] += time.perf_counter() - t
        setattr(gg, name, wrap)
    return acc


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--series", required=True)
    ap.add_argument("--stride", type=int, default=1)
    ap.add_argument("--reference", action="store_true")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--reader", action="store_true")
    ap.add_argument("--typing", action="store_true")
    ap.add_argument("--max-dirty", type=float, default=0.25)
    a = ap.parse_args()
    files = sorted(glob.glob(os.path.join(a.series, "*.png")))[::a.stride]
    atlas = atlas_default()
    if a.reference:
        gg._REFERENCE = True

    if a.check:
        bad = []
        for f in files:
            fast = canon(gg.read_image(f, atlas))
            with gg.reference_mode():
                ref = canon(gg.read_image(f, atlas))
            if fast != ref:
                bad.append(os.path.basename(f))
        print("fast == reference on %d/%d frames%s" % (len(files) - len(bad), len(files), "" if not bad else "; DIFFERENT: %s" % bad))
        return 1 if bad else 0

    if a.reader:
        rd = gg.GlyphReader(atlas, max_dirty=a.max_dirty)
        paths, times, bad = collections.Counter(), collections.defaultdict(list), []
        for f in files:
            t = time.perf_counter()
            r = rd.read(f)
            times[r["session"]["path"]].append(time.perf_counter() - t)
            paths[r["session"]["path"]] += 1
            ref = gg.read_image(f, atlas)
            if any(canon(r.get(k)) != canon(ref.get(k)) for k in (set(r) | set(ref)) - set(NOT_COMPARED)):
                bad.append(os.path.basename(f))
        print("paths", dict(paths), "| identical content to a fresh read_image on %d/%d frames%s" % (
            len(files) - len(bad), len(files), "" if not bad else "; DIFFERENT: %s" % bad))
        for k, v in times.items():
            summary("reader[%s]" % k, v)
        return 1 if bad else 0

    if a.typing:
        from PIL import Image
        rd = gg.GlyphReader(atlas)
        for f in files[::max(1, len(files) // 6)]:
            base = Image.open(f).convert("RGB")
            first = rd.read(base)
            if first.get("status") != "ok":
                continue
            g = first["grid"]
            w, h = g["cell_w"], g["cell_h"]
            sr, sc = next((r_, c_) for r_, cf in enumerate(first["conf"]) for c_, ch in enumerate(cf) if ch in "Fe" and first["rows"][r_][c_] not in "-_=|")
            src = base.crop((g["origin_x"] + sc * w, g["origin_y"] + sr * h, g["origin_x"] + (sc + 1) * w, g["origin_y"] + (sr + 1) * h))
            fulls, incs = [], []
            for n in range(6):
                edited = base.copy()
                for c in range(10 + n, 14 + n):   # a handful of cells in one row, like typing a word
                    edited.paste(src, (g["origin_x"] + c * w, g["origin_y"] + 5 * h))
                t = time.perf_counter()
                r = rd.read(edited)
                incs.append(time.perf_counter() - t)
                t = time.perf_counter()
                gg.read_image(edited, atlas)
                fulls.append(time.perf_counter() - t)
                rd.read(base)
            print("%-14s font %-18s path %-11s dirty %s  incremental %6.1f ms  fresh read_image %6.1f ms" % (
                os.path.basename(f), first["font"], r["session"]["path"], r["session"]["dirty_cells"],
                1000 * statistics.median(incs), 1000 * statistics.median(fulls)))
        return 0

    acc = timed_stages()
    ts = []
    for f in files:
        t = time.perf_counter()
        gg.read_image(f, atlas)
        ts.append(time.perf_counter() - t)
    summary("read_image (%s)" % ("reference" if a.reference else "fast"), ts)
    for k in STAGES:
        print("  %-18s %7.1f ms/frame (%2.0f%%)" % (k, 1000 * acc[k] / len(ts), 100 * acc[k] / sum(ts)))
    print("  peak RSS %d MB" % (resource.getrusage(resource.RUSAGE_SELF).ru_maxrss // 1024))
    return 0


if __name__ == "__main__":
    sys.exit(main())
