#!/usr/bin/env python3
"""Evaluate glyphgrid honestly.

  --loo      leave-one-frame-out on the ground-truth corpus (learn from N-1 frames, read the N-th)
  --curve    learning curve: atlas size and held-out accuracy as frames are added (random orders)
  --series   learn on all, read the whole unlabelled series: coverage (exact vs UNKNOWN) only
  --scale    scale probe: resampled variants of a real frame must be REFUSED; tolerance needed by a
             nearest-glyph fallback
  --colour   same glyph across colour schemes after normalisation

Ground truth is human-transcribed; every number is relative to it.  Results go to --out (JSON).
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import random
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import glyphgrid as gg  # noqa: E402
import learn_atlas as la  # noqa: E402

DEFAULT_CORPUS = "/workspaces/vbpub/.claude/worktrees/agent-acd84ff048b08a1ba/pwmcp/tests/corpus/console/truenas-27-vanilla"
DEFAULT_SERIES = "/home/vscode/console-corpus/frames"

# frame category by the transcriber's description (README "Frames" table); used only for aggregation
CATEGORY = {}
for i in (2, 3, 12, 13, 18, 19, 20):
    CATEGORY[i] = "console (light on black)"
for i in (4, 5, 6, 7, 8, 9, 10, 11, 14):
    CATEGORY[i] = "dialog (black on grey)"
for i in (1, 16, 17):
    CATEGORY[i] = "grub"
CATEGORY[15] = "uefi logo"

KEY_PHRASES = [  # (frame number, phrase) from OCR-EVAL.md "Key phrases"
    (4, "TrueNAS 27.0.0-RC.1 Console Setup"), (4, "1 Install/Upgrade"), (4, "3 Reboot System"),
    (5, "Choose Destination Media"), (5, "vda"), (6, "[*]"), (7, "Proceed with the installation?"),
    (8, "Administrative user (truenas_admin)"), (9, "Confirm Password:"),
    (13, "[100%] Installation completed successfully"), (14, "Installation Succeeded"),
    (14, "The TrueNAS installation on vda succeeded!"), (14, "Please reboot and remove the installation media."),
    (16, "TrueNAS Scale GNU/Linux 27.0.0-RC.1"), (17, "Loading initial ramdisk ..."),
    (19, "Console setup"), (19, "http://10.0.2.15"), (19, "https://10.0.2.15"),
    (19, "http://[fec0::5054:ff:fe12:3456]"), (19, "4) Change local administrator password"),
    (19, "10) Shutdown"), (19, "Enter an option from 1-10:"),
    (20, "Console setup"), (20, "https://10.0.2.15"), (20, "9) Reboot"), (20, "Enter an option from 1-10:"),
]


def fnum(fid):
    return int(fid.split("-")[0])


def lev(a: str, b: str) -> int:
    if len(a) < len(b):
        a, b = b, a
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def collapse(s: str) -> str:
    return " ".join(s.split())


def decoded_text(res, ext, drop_box=True):
    """Whole-frame text for a Tesseract-comparable character score (unknown box-like cells are not text)."""
    w, h = ext.grid["cell_w"], ext.grid["cell_h"]
    lines = []
    for r, (s, cf) in enumerate(zip(res["rows"], res["conf"])):
        out = []
        for c, (ch, st) in enumerate(zip(s, cf)):
            if st in (gg.CONF_UNKNOWN, gg.CONF_CONFLICT, gg.CONF_MULTI) and drop_box:
                cell = ext.rows[r][c]
                if st == gg.CONF_MULTI or la.boxlike(cell.key, w, h):
                    out.append(" ")
                    continue
            out.append(ch)
        lines.append("".join(out))
    return collapse(" ".join(lines))


def score_frame(res, ext, gt_lines, atlas, fast=False):
    pairs, rep = la.align(ext, gt_lines, atlas, res["font"])
    cnt = dict(cells=0, exact_correct=0, unknown=0, wrong_conf=0, excluded_question=0, single_source=0,
               single_would_be_correct=0, single_would_be_wrong=0)
    rows = {}
    wrong = []
    unk_chars = {}
    would = {(u["row"], u["col"]): u.get("would_read") for u in res["unknown_cells"] if u["status"] == gg.CONF_SINGLE}
    scored = set()
    for p in pairs:
        if p["char"] == " ":
            continue
        if p["char"] == "?" and p.get("marker"):
            cnt["excluded_question"] += 1
            continue
        pos = (p["row"], p["col"])
        scored.add(pos)
        st = res["conf"][p["row"]][p["col"]]
        ch = res["rows"][p["row"]][p["col"]]
        cnt["cells"] += 1
        if st == gg.CONF_EXACT:
            if ch == p["char"]:
                cnt["exact_correct"] += 1
                kind = "ok"
            else:
                cnt["wrong_conf"] += 1
                kind = "wrong"
                wrong.append({"row": p["row"], "col": p["col"], "gt": p["char"], "read": ch})
        elif st == gg.CONF_SINGLE:
            cnt["single_source"] += 1
            cnt["single_would_be_correct" if would.get(pos) == p["char"] else "single_would_be_wrong"] += 1
            kind = "unknown"
        else:
            cnt["unknown"] += 1
            kind = "unknown"
            unk_chars[p["char"]] = unk_chars.get(p["char"], 0) + 1
        rows.setdefault(p["row"], set()).add(kind)
    known = {ch for fo in atlas.fonts.values() for g in fo["glyphs"].values() for ch in g["c"]}
    cnt["unknown_novel_char"] = sum(n for ch, n in unk_chars.items() if ch not in known)
    row_stats = dict(rows=len(rows), exact=sum(1 for v in rows.values() if v == {"ok"}),
                     with_unknown=sum(1 for v in rows.values() if "unknown" in v and "wrong" not in v),
                     with_wrong=sum(1 for v in rows.values() if "wrong" in v))
    # whole-grid view: every non-blank cell of the decoded grid, scored or not
    nonblank = exact_all = exact_unscored = flagged = fill = 0
    for r, cf in enumerate(res["conf"]):
        for c, x in enumerate(cf):
            if x == gg.CONF_BLANK:
                continue
            nonblank += 1
            fill += x == gg.CONF_FILL
            if x == gg.CONF_EXACT:
                exact_all += 1
                if (r, c) not in scored:
                    exact_unscored += 1
                if res["flags"][r][c] != ".":
                    flagged += 1
    grid = dict(nonblank=nonblank, exact=exact_all, exact_unscored=exact_unscored, exact_flagged_rule_or_cursor=flagged, fill=fill)
    flat = [collapse(s2) for s2 in res["rows"]]
    found = n_lines = 0
    for ln in gt_lines:
        t = collapse(ln)
        if not t:
            continue
        n_lines += 1
        found += any(t in f for f in flat)
    out = {"cells": cnt, "rows": row_stats, "grid": grid, "gt_lines": n_lines, "gt_lines_found": found,
           "gt_lines_aligned": rep["aligned"], "gt_lines_unaligned": rep["unaligned"], "wrong": wrong,
           "unknown_chars": unk_chars, "font": res["font"], "status": res["status"],
           "origin": [ext.grid["origin_x"], ext.grid["origin_y"]]}
    if not fast:
        gt_text = collapse(" ".join(gt_lines))
        out_text = decoded_text(res, ext)
        out_raw = collapse(" ".join(res["rows"]))
        out["char_acc_text"] = round(1 - lev(gt_text, out_text) / max(len(gt_text), len(out_text), 1), 4)
        out["char_acc_raw"] = round(1 - lev(gt_text, out_raw) / max(len(gt_text), len(out_raw), 1), 4)
    else:
        out["char_acc_text"] = out["char_acc_raw"] = None
    return out, flat


def run_loo(state, corpus, holdout="frame", score_corr=None, learn_groups=None, fast=False, verbose=True):
    """holdout 'frame': learn from the other 19 frames.  'group': leave the frame's WHOLE screen group out."""
    frames = state["frames"]
    aligned, fontmap = state["aligned"], state["fontmap"]
    out, flats = {}, {}
    for f in frames:
        t0 = time.time()
        gid = la.group_of(f["id"], learn_groups)
        others = [g for g in frames if (la.group_of(g["id"], learn_groups) != gid if holdout == "group" else g["id"] != f["id"])]
        atlas_k, _ = la.build_atlas(others, aligned, fontmap, groups=learn_groups)
        ex, _ = gg.choose_extraction(f["im"], f["grid"], atlas_k)
        res = gg.decode(ex, atlas_k)
        gt = la.read_gt(os.path.join(corpus, "ground-truth", f["id"] + ".txt"), score_corr, f["id"])
        sc, flat = score_frame(res, ex, gt, atlas_k, fast)
        sc["category"] = CATEGORY.get(fnum(f["id"]), "?")
        out[f["id"]] = sc
        flats[f["id"]] = flat
        if verbose:
            c = sc["cells"]
            print("%-36s %-24s cells %4d exact-correct %4d single %3d unknown %4d wrong %d (%.0fs)" % (
                f["id"], sc["category"], c["cells"], c["exact_correct"], c["single_source"], c["unknown"], c["wrong_conf"], time.time() - t0), flush=True)
    for fid, rep in state["reports"].items():
        if fid not in out and "skipped" in rep:
            gt = la.read_gt(os.path.join(corpus, "ground-truth", fid + ".txt"))
            out[fid] = {"skipped": rep["skipped"], "category": CATEGORY.get(fnum(fid), "?"), "gt_lines": sum(1 for x in gt if x.strip())}
    return out, flats


KEYS = ("cells", "exact_correct", "single_source", "single_would_be_correct", "single_would_be_wrong", "unknown", "wrong_conf", "unknown_novel_char")


def aggregate(loo):
    agg = {}
    for cat in sorted({v.get("category") for v in loo.values()}):
        rows = [v for v in loo.values() if v.get("category") == cat and "cells" in v]
        if not rows:
            continue
        tot = {k: sum(v["cells"][k] for v in rows) for k in KEYS}
        tot.update(frames=len(rows), row_exact=sum(v["rows"]["exact"] for v in rows), rows=sum(v["rows"]["rows"] for v in rows),
                   lines_found=sum(v["gt_lines_found"] for v in rows), lines=sum(v["gt_lines"] for v in rows))
        agg[cat] = tot
    allv = [v for v in loo.values() if "cells" in v]
    agg["ALL"] = {k: sum(v["cells"][k] for v in allv) for k in KEYS}
    agg["ALL"].update(frames=len(allv), row_exact=sum(v["rows"]["exact"] for v in allv), rows=sum(v["rows"]["rows"] for v in allv),
                      lines_found=sum(v["gt_lines_found"] for v in allv), lines=sum(v["gt_lines"] for v in allv),
                      grid_nonblank=sum(v["grid"]["nonblank"] for v in allv), grid_exact=sum(v["grid"]["exact"] for v in allv),
                      grid_exact_unscored=sum(v["grid"]["exact_unscored"] for v in allv),
                      grid_exact_flagged=sum(v["grid"]["exact_flagged_rule_or_cursor"] for v in allv),
                      grid_fill=sum(v["grid"]["fill"] for v in allv),
                      no_font_frames=sum(1 for v in allv if v["status"] == "no_font"))
    return agg


def key_phrase_table(flats):
    out = []
    for n, ph in KEY_PHRASES:
        fid = next((k for k in flats if fnum(k) == n), None)
        found = bool(fid and any(collapse(ph) in row for row in flats[fid]))
        out.append({"frame": n, "phrase": ph, "found": found})
    return out


def run_curve(state, perms=6, seed=1, font=None):
    """Held-out exact share vs number of frames learned, within ONE font (the one with most frames)."""
    aligned, fontmap = state["aligned"], state["fontmap"]
    if font is None:
        font = max(set(fontmap.values()), key=list(fontmap.values()).count)
    frames = [f for f in state["frames"] if fontmap[f["id"]] == font]
    rng = random.Random(seed)
    curves = []
    for _ in range(perms):
        order = frames[:]
        rng.shuffle(order)
        pts = []
        for m in range(1, len(order)):
            sub, test = order[:m], order[m:]
            atlas, _ = la.build_atlas(sub, aligned, fontmap)
            n = ok = 0
            for f in test:
                prs = [p for p in aligned[f["id"]] if p["kind"] == "glyph" and p["char"] not in (" ",) and not (p["char"] == "?" and p.get("marker"))]
                best = None
                for fn in atlas.fonts_for(f["grid"]["cell_w"], f["grid"]["cell_h"]):
                    h = sum(1 for p in prs if (atlas.lookup(fn, p["key"]) or (None,))[0] == "exact")
                    if best is None or h > best[0]:
                        best = (h, fn)
                for p in prs:
                    n += 1
                    if best:
                        r = atlas.lookup(best[1], p["key"])
                        ok += bool(r and r[0] == "exact" and r[1] == p["char"])
            glyphs = sum(len(fo["glyphs"]) for fo in atlas.fonts.values())
            pts.append({"frames": m, "glyphs": glyphs, "cells": n, "exact_share": round(ok / n, 4) if n else None})
        curves.append(pts)
    return curves


def colour_report(state, atlas):
    fm = state["fontmap"]
    per = {}
    for fid, pairs in state["aligned"].items():
        for p in pairs:
            if p["kind"] != "glyph" or p["char"] == " " or p.get("marker"):
                continue
            d = per.setdefault((fm[fid], p["char"]), {"keys": set(), "schemes": set(), "frames": set()})
            d["keys"].add(p["key"].hex())
            d["schemes"].add((p["fg"], p["bg"]))
            d["frames"].add(fid)
    multi_scheme = [(k, v) for k, v in per.items() if len(v["schemes"]) > 1]
    identical = [k for k, v in multi_scheme if len(v["keys"]) == 1]
    variants = [(k, v) for k, v in per.items() if len(v["keys"]) > 1]
    return {
        "chars_seen_in_multiple_colour_schemes": len(multi_scheme),
        "of_which_one_bitmap": len(identical),
        "chars_with_more_than_one_bitmap_in_a_font": [
            {"font": k[0], "char": k[1], "bitmaps": len(v["keys"]), "schemes": len(v["schemes"]), "frames": sorted(v["frames"])}
            for k, v in variants],
        "schemes": sorted({"%s on %s" % (s[0], s[1]) for v in per.values() for s in v["schemes"]}),
    }


def run_series(atlas, series_dir, out_path, limit=None):
    files = sorted(glob.glob(os.path.join(series_dir, "*.png")))
    if limit:
        files = files[:limit]
    done = {}
    if os.path.exists(out_path):
        with open(out_path) as f:
            done = {r["file"]: r for r in json.load(f)["frames"]}
    unk = {}
    rows = []
    for fp in files:
        base = os.path.basename(fp)
        if base in done:
            rows.append(done[base])
            continue
        t0 = time.time()
        im, probe, g = gg.prepare(fp, atlas=atlas)
        rec = {"file": base, "probe": {k: probe[k] for k in ("n_colors", "soft_ratio", "resampled")}}
        if im is None:
            rec["status"] = "resampled"
        elif g["status"] != "ok":
            rec["status"] = "no_grid"
            rec["reason"] = g["reasons"][:1]
        else:
            ex, _ = gg.choose_extraction(im, g, atlas)
            res = gg.decode(ex, atlas)
            conf = "".join(res["conf"])
            glyph = sum(conf.count(x) for x in "eunxm")
            w, h = g["cell_w"], g["cell_h"]
            box_unknown = sum(1 for u in res["unknown_cells"] if u["status"] == "m" or la.boxlike(bytes.fromhex(u["hex"]), w, h))
            rec.update(status="ok", grid=[w, h], origin=[ex.grid["origin_x"], ex.grid["origin_y"]], font=res["font"],
                       glyph_cells=glyph, exact=conf.count("e"), unknown=conf.count("u"), conflict=conf.count("x"),
                       multicolor=conf.count("m"), unknown_boxlike=box_unknown,
                       unknown_hex=sorted({u["hex"] for u in res["unknown_cells"]}))
        rec["seconds"] = round(time.time() - t0, 1)
        rows.append(rec)
        if len(rows) % 10 == 0:
            with open(out_path, "w") as f:
                json.dump({"frames": rows}, f)
            print(len(rows), "/", len(files), flush=True)
    with open(out_path, "w") as f:
        json.dump({"frames": rows}, f)
    return rows


def series_summary(rows, atlas):
    ok = [r for r in rows if r["status"] == "ok"]
    summ = {"frames": len(rows), "grid_ok": len(ok), "no_grid": sum(r["status"] == "no_grid" for r in rows),
            "resampled": sum(r["status"] == "resampled" for r in rows)}
    g = sum(r["glyph_cells"] for r in ok)
    e = sum(r["exact"] for r in ok)
    bx = sum(r["unknown_boxlike"] for r in ok)
    summ.update(glyph_cells=g, exact=e, unknown=g - e, unknown_boxlike_or_multicolour=bx,
                exact_share=round(e / g, 4) if g else None,
                exact_share_excluding_boxlike=round(e / (g - bx), 4) if g - bx else None)
    summ["frames_with_zero_text_unknown"] = sum(1 for r in ok if r["unknown"] + r["conflict"] - r["unknown_boxlike"] + r["multicolor"] <= 0)
    summ["fonts_used"] = {}
    for r in ok:
        summ["fonts_used"][r["font"]] = summ["fonts_used"].get(r["font"], 0) + 1
    freq = {}
    for r in ok:
        for hx in r["unknown_hex"]:
            freq[hx] = freq.get(hx, 0) + 1
    summ["distinct_unknown_bitmaps"] = len(freq)
    summ["top_unknown_bitmaps_by_frames"] = sorted(freq.items(), key=lambda kv: -kv[1])[:12]
    return summ


# ------------------------------------------------------------------ scale probe
def _soft_bitmap(crop):
    """Experimental tolerant binarisation of a blurred cell: threshold at the luminance mid-point."""
    lum = crop.convert("L")
    px = list(lum.tobytes())
    lo, hi = min(px), max(px)
    if hi - lo < 40:
        return None
    thr = (lo + hi) / 2
    bits = [1 if p > thr else 0 for p in px]
    # fg = minority
    if sum(bits) > len(bits) / 2:
        bits = [1 - b for b in bits]
    w, h = crop.size
    rb = (w + 7) // 8
    out = bytearray()
    for y in range(h):
        acc = 0
        for x in range(w):
            acc = (acc << 1) | bits[y * w + x]
        out += (acc << (rb * 8 - w)).to_bytes(rb, "big")
    return bytes(out)


def run_scale(atlas, corpus, frame="18-kernel-boot-log"):
    from PIL import Image
    path = os.path.join(corpus, "frames", frame + ".png")
    base = gg.load_image(path)
    W, H = base.size
    variants = {
        "original": base,
        "0.9x bilinear then back (1280x800)": base.resize((int(W * .9), int(H * .9)), Image.BILINEAR).resize((W, H), Image.BILINEAR),
        "0.9x lanczos then back": base.resize((int(W * .9), int(H * .9)), Image.LANCZOS).resize((W, H), Image.LANCZOS),
        "1.25x bilinear (1600x1000)": base.resize((int(W * 1.25), int(H * 1.25)), Image.BILINEAR),
        "1.5x nearest (1920x1200, non-integer)": base.resize((int(W * 1.5), int(H * 1.5)), Image.NEAREST),
        "2x nearest (2560x1600, integer)": base.resize((W * 2, H * 2), Image.NEAREST),
        "letterboxed copy (+37 px top, 1:1)": _pad(base, 0, 37),
    }
    ex0, _ = gg.choose_extraction(base, gg.detect_grid(base), atlas)
    truth = {(c.row, c.col): c.key for row in ex0.rows for c in row if c.kind == "glyph"}
    out = {}
    for name, im in variants.items():
        res = gg.read_image(im, atlas)
        rec = {"size": list(im.size), "status": res["status"], "probe": res["probe"]}
        if res["status"] == "ok":
            conf = "".join(res["conf"])
            rec.update(exact=conf.count("e"), unknown=conf.count("u"), grid=res["grid"], font=res["font"])
        else:
            rec["message"] = res.get("message")
            if res["status"] == "no_grid":
                rec["grid_reasons"] = res["grid_report"]["reasons"][:1]
        out[name] = rec
    # what tolerance would a nearest-glyph fallback need?  (only meaningful when the consumer already
    # knows the scale and the grid: undo the resampling, threshold each cell, measure Hamming distance)
    tol = {}
    for name in ("0.9x bilinear then back (1280x800)", "0.9x lanczos then back"):
        im = variants[name]
        g = gg.grid_with_origin(gg.detect_grid(base), (W, H), 0, 0)
        font = ex0.grid and atlas.fonts_for(8, 16)
        fn = max(font, key=lambda f: gg._count_hits(ex0, atlas, f))
        dist = []
        verdict = {"correct": 0, "wrong": 0, "none": 0}
        thresholds = (0, 2, 4, 6, 8, 12, 16)
        by_thr = {t: {"correct": 0, "wrong": 0, "unknown": 0} for t in thresholds}
        for (r, c), key in truth.items():
            crop = im.crop((c * 8, r * 16, c * 8 + 8, r * 16 + 16))
            sb = _soft_bitmap(crop)
            if sb is None:
                continue
            d = gg._popcount(int.from_bytes(sb, "big") ^ int.from_bytes(key, "big"))
            dist.append(d)
            gtc = atlas.lookup(fn, key)
            near = atlas.nearest(fn, sb, 2)
            for t in thresholds:
                if not near or near[0][0] > t or (len(near) > 1 and near[1][0] == near[0][0] and near[1][1] != near[0][1]):
                    by_thr[t]["unknown"] += 1
                elif gtc and near[0][1] == gtc[1]:
                    by_thr[t]["correct"] += 1
                else:
                    by_thr[t]["wrong"] += 1
        dist.sort()
        tol[name] = {"cells": len(dist), "hamming_to_true_bitmap_p50": dist[len(dist) // 2], "p90": dist[int(len(dist) * .9)],
                     "max": dist[-1], "by_max_hamming": by_thr}
    return out, tol


def _pad(im, dx, dy):
    from PIL import Image
    out = Image.new("RGB", (im.size[0] + dx, im.size[1] + dy), (0, 0, 0))
    out.paste(im, (dx, dy))
    return out


def run_probe_calibration(corpus):
    """Choose the ramp threshold on half the frames, test on the other half (crisp frames must pass, resampled
    variants of every frame must be refused)."""
    from PIL import Image
    files = sorted(glob.glob(os.path.join(corpus, "frames", "*.png")))
    crisp, resampled = {}, {}
    for fp in files:
        n = fnum(os.path.basename(fp))
        im = gg.load_image(fp)
        W, H = im.size
        crisp[n] = gg.ramp_ratio(im)
        vs = {"0.9 bilinear back": im.resize((int(W * .9), int(H * .9)), Image.BILINEAR).resize((W, H), Image.BILINEAR),
              "0.95 lanczos back": im.resize((int(W * .95), int(H * .95)), Image.LANCZOS).resize((W, H), Image.LANCZOS),
              "1.25 bilinear": im.resize((int(W * 1.25), int(H * 1.25)), Image.BILINEAR),
              "0.75 bicubic back": im.resize((int(W * .75), int(H * .75)), Image.BICUBIC).resize((W, H), Image.BICUBIC)}
        resampled[n] = {k: gg.ramp_ratio(v) for k, v in vs.items()}
    A = [n for n in crisp if n % 2 == 0]
    B = [n for n in crisp if n % 2 == 1]
    hi_crisp = max(crisp[n] for n in A)
    lo_res = min(v for n in A for v in resampled[n].values())
    thr = (hi_crisp * lo_res) ** 0.5 if hi_crisp > 0 else lo_res / 10
    test = {"crisp_false_refusals": [n for n in B if crisp[n] > thr],
            "resampled_missed": [(n, k) for n in B for k, v in resampled[n].items() if v <= thr]}
    return {"calibration_frames": A, "test_frames": B, "max_crisp_ramp_on_calibration": hi_crisp, "min_resampled_ramp_on_calibration": lo_res,
            "chosen_threshold": thr, "default_threshold": gg.RAMP_THRESHOLD,
            "test": test, "max_crisp_all": max(crisp.values()), "min_resampled_all": min(v for r in resampled.values() for v in r.values()),
            "crisp_ramp_by_frame": crisp}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--corpus", default=DEFAULT_CORPUS)
    ap.add_argument("--series", default=DEFAULT_SERIES)
    ap.add_argument("--out", default="glyphgrid-results.json")
    ap.add_argument("--corrections", default=None, help="ground-truth-corrections.json: report original AND corrected")
    ap.add_argument("--groups", default=None, help="screen-groups.json (independent-evidence groups)")
    ap.add_argument("--loo", action="store_true")
    ap.add_argument("--probe", action="store_true")
    ap.add_argument("--curve", action="store_true")
    ap.add_argument("--colour", action="store_true")
    ap.add_argument("--scale", action="store_true")
    ap.add_argument("--run-series", action="store_true")
    ap.add_argument("--series-cache", default="series-coverage.json")
    ap.add_argument("--atlas-out", default=None, help="atlas learned from the (corrected if given) ground truth")
    ap.add_argument("--atlas-out-original", default=None)
    ap.add_argument("--limit", type=int, default=None)
    a = ap.parse_args(argv)
    groups = la.load_groups(a.groups)
    corr = la.load_corrections(a.corrections)
    res = {}
    atlas_o, st_o = la.learn(a.corpus, groups=groups)
    if corr:
        atlas, state = la.learn(a.corpus, corrections=corr, groups=groups)
    else:
        atlas, state = atlas_o, st_o
    if a.atlas_out:
        atlas.save(a.atlas_out)
    if a.atlas_out_original:
        atlas_o.save(a.atlas_out_original)
    res["atlas"] = {"fonts": {n: {"cell": [f["cell_w"], f["cell_h"]], "glyphs": len(f["glyphs"])} for n, f in atlas.fonts.items()},
                    "conflicts_original_gt": la.conflict_report(atlas_o, st_o), "conflicts_corrected_gt": la.conflict_report(atlas, state),
                    "quarantined_question_marks": state["quarantined_question_marks"],
                    "alignment": {k: {kk: vv for kk, vv in v.items() if kk in ("lines", "aligned", "unaligned", "ambiguous", "origin", "font", "skipped")}
                                  for k, v in state["reports"].items()}}
    if a.loo:
        passes = [("frame_out/original_gt", st_o, "frame", None), ("screen_group_out/original_gt", st_o, "group", None)]
        if corr:
            passes += [("frame_out/original_atlas_scored_vs_corrected_gt", st_o, "frame", corr),
                       ("frame_out/corrected_gt", state, "frame", corr), ("screen_group_out/corrected_gt", state, "group", corr)]
        res["loo"] = {}
        for name, st, hold, score_corr in passes:
            print("==", name, flush=True)
            loo, flats = run_loo(st, a.corpus, hold, score_corr, groups, fast=(name != "frame_out/original_gt" and name != "frame_out/corrected_gt"))
            agg = aggregate(loo)
            res["loo"][name] = {"aggregate": agg, "frames": loo, "key_phrases": key_phrase_table(flats)}
            print(json.dumps(agg["ALL"]), "phrases", sum(k["found"] for k in res["loo"][name]["key_phrases"]), flush=True)
    if a.probe:
        res["probe_calibration"] = run_probe_calibration(a.corpus)
        pc = res["probe_calibration"]
        print({k: v for k, v in pc.items() if k != "crisp_ramp_by_frame"})
    if a.curve:
        res["learning_curve"] = run_curve(state)
        res["learning_curve_font"] = max(set(state["fontmap"].values()), key=list(state["fontmap"].values()).count)
    if a.colour:
        res["colour"] = colour_report(state, atlas)
    if a.scale:
        res["scale_probe"], res["scale_tolerance"] = run_scale(atlas, a.corpus)
    if a.run_series:
        rows = run_series(atlas, a.series, a.series_cache, a.limit)
        res["series"] = series_summary(rows, atlas)
        print(json.dumps({k: v for k, v in res["series"].items() if k != "top_unknown_bitmaps_by_frames"}, indent=1))
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(res, f, indent=1, ensure_ascii=False, default=str)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
