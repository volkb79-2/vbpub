#!/usr/bin/env python3
"""Learn a glyphgrid atlas from (frame, ground-truth text) pairs.

Ground truth is a plain text file per frame: the visible text line by line, WITHOUT box-drawing and
WITHOUT column offsets.  Alignment therefore works on the blank/non-blank pattern: a ground-truth line
is placed on the next grid row that contains exactly that pattern of spaces and ink.  Rows that
cannot be placed (or are ambiguous) are reported and skipped, never guessed.

Usage: learn_atlas.py CORPUS_DIR --out atlas.json [--report report.json]
CORPUS_DIR holds frames/NN-name.png and ground-truth/NN-name.txt.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import glyphgrid as gg  # noqa: E402


def prepare_frame(path: str, grid_hint=None) -> dict:
    """Load, probe and grid a frame.  ``ext`` is filled in later by ``learn`` (origin chosen by atlas evidence)."""
    im = gg.load_image(path)
    probe = gg.probe_scale(im)
    grid = gg.detect_grid(im, hints=grid_hint)
    return {"id": os.path.basename(path)[:-4], "path": path, "grid": grid, "probe": probe, "im": im, "ext": None,
            "size": im.size}


def read_gt(path: str):
    with open(path, encoding="utf-8") as f:
        return [ln.rstrip("\n") for ln in f.read().split("\n")]


LINEY = set("_|=-+[]/\\<>")  # ASCII glyphs that may legitimately touch the cell boundary


def boxlike(key: bytes, w: int, h: int) -> bool:
    """Ink in the top/bottom pixel row or a row spanning the full width: box-drawing rules/corners connect
    to their neighbours, text glyphs keep a margin."""
    rb = (w + 7) // 8
    rows = [int.from_bytes(key[y * rb:(y + 1) * rb], "big") >> (rb * 8 - w) for y in range(h)]
    left, right = 1 << (w - 1), 1
    return bool(rows[0] or rows[-1] or any((r & left) and (r & right) for r in rows))


def _row_patterns(ext):
    """'0' blank, '1' ink, 'M' multicolour (matches anything), 'B' ink touching the cell boundary (box drawing / rules / '_')."""
    w, h = ext.grid["cell_w"], ext.grid["cell_h"]
    memo = {}
    out = []
    for row in ext.rows:
        s = []
        for c in row:
            if c.kind == "blank":
                s.append("0")
            elif c.kind == "multicolor":
                s.append("M")
            else:
                v = memo.get(c.key)
                if v is None:
                    v = memo[c.key] = "B" if boxlike(c.key, w, h) else "1"
                s.append(v)
        out.append("".join(s))
    return out


def _windows(pat: str, tokens, min_start: int = 0):
    """All placements of the whitespace-separated ``tokens`` on a row pattern: list of cell-index lists.

    Ground truth collapsed runs of spaces, so a gap of ANY width >= 1 of blank/box cells may separate
    tokens; every token character must sit on an inked, non-box cell (LINEY characters may also sit
    on box-like cells).
    """
    parts = []
    for t in tokens:
        parts.append("(" + "".join("[1BM]" if not ch.isalnum() else "[1M]" for ch in t) + ")")
    rx = "(?=" + "[0B]+".join(parts) + ")"
    out = []
    for m in re.finditer(rx, pat):
        if m.start() < min_start:
            continue
        idx = []
        for ti, t in enumerate(tokens):
            st = m.start(ti + 1)
            idx.extend(range(st, st + len(t)))
        out.append(idx)
    return out


def align(ext, gt_lines, atlas: "gg.Atlas | None" = None, font: str | None = None, skip_ambiguous: bool = True):
    """-> (pairs, report).  pairs: dict(row, col, char, key, kind); report: aligned/ambiguous/unaligned."""
    pats = _row_patterns(ext)
    pairs, report = [], {"lines": 0, "aligned": 0, "ambiguous": [], "unaligned": []}
    r0 = 0
    last = None  # (row, end col) of the previous placement: a second line may share the row (buttons)
    for line in gt_lines:
        tokens = line.split()
        if not tokens:
            continue
        text = "".join(tokens)
        report["lines"] += 1
        found = None
        if last is not None:
            wins = _windows(pats[last[0]], tokens, last[1] + 2)
            if len(wins) == 1:
                found = (last[0], wins[0])
        if found is None:
            for r in range(r0, len(pats)):
                wins = _windows(pats[r], tokens)
                if not wins:
                    continue
                if len(wins) > 1 and atlas is not None and font:
                    wins = _prefer_known(ext.rows[r], text, wins, atlas, font)
                if len(wins) == 1:
                    found = (r, wins[0])
                else:
                    report["ambiguous"].append({"line": line.strip(), "row": r, "windows": len(wins)})
                    found = ("amb", r) if skip_ambiguous else (r, wins[0])
                break
        if found is None:
            report["unaligned"].append(line.strip())
            continue
        if found[0] == "amb":
            r0 = found[1] + 1
            last = None
            continue
        r, idx = found
        r0 = r + 1
        last = (r, idx[-1])
        report["aligned"] += 1
        # '?' at the START of a longer token is the transcriber's "unreadable glyph" marker (README)
        marker = {i for i, ch in enumerate(text) if ch == "?" and _token_start(tokens, i) and _token_len(tokens, i) > 1}
        for i, (ch, c_i) in enumerate(zip(text, idx)):
            c = ext.rows[r][c_i]
            pairs.append({"row": r, "col": c_i, "char": ch, "key": c.key, "kind": c.kind, "fg": c.fg, "bg": c.bg,
                          "marker": i in marker})
    return pairs, report


def _token_start(tokens, i):
    pos = 0
    for t in tokens:
        if i == pos:
            return True
        pos += len(t)
    return False


def _token_len(tokens, i):
    pos = 0
    for t in tokens:
        if pos <= i < pos + len(t):
            return len(t)
        pos += len(t)
    return 0


def _prefer_known(row, text, wins, atlas, font):
    """Among equal-pattern windows keep those with no contradiction against the atlas and max agreement."""
    best, keep = None, []
    for idx in wins:
        agree = contra = 0
        for ch, ci in zip(text, idx):
            c = row[ci]
            if c.kind != "glyph":
                continue
            r = atlas.lookup(font, c.key)
            if r and r[0] == "exact":
                if r[1] == ch:
                    agree += 1
                else:
                    contra += 1
        score = (contra == 0, agree - 5 * contra)
        if best is None or score > best:
            best, keep = score, [idx]
        elif score == best:
            keep.append(idx)
    return keep if best and best[0] else wins


def build_atlas(frames, aligned, fontmap, trust_question_marks: bool = False):
    """-> (Atlas, info).  A '?' that opens a longer token is the transcriber's unreadable-glyph marker
    (README caveat, frame 19): quarantined, never learned, listed in info."""
    atlas = gg.Atlas()
    quarantined = []
    for f in frames:
        pairs = aligned.get(f["id"])
        if not pairs or f["id"] not in fontmap:
            continue
        g = f["grid"]
        for p in pairs:
            if p["kind"] != "glyph" or p["char"] == " ":
                continue
            if p["char"] == "?" and p.get("marker") and not trust_question_marks:
                quarantined.append({"font": fontmap[f["id"]], "hex": p["key"].hex(), "frame": f["id"],
                                    "row": p["row"], "col": p["col"]})
                continue
            atlas.add(fontmap[f["id"]], g["cell_w"], g["cell_h"], p["key"], p["char"], f["id"],
                       phase=(f["ext"].grid["origin_x"], f["ext"].grid["origin_y"]))
    return atlas, {"quarantined_question_marks": quarantined}


def ascii_art(hexkey: str, w: int, h: int) -> str:
    b = bytes.fromhex(hexkey)
    rb = (w + 7) // 8
    out = []
    for y in range(h):
        v = int.from_bytes(b[y * rb:(y + 1) * rb], "big") >> (rb * 8 - w)
        out.append("".join("#" if v >> (w - 1 - x) & 1 else "." for x in range(w)))
    return "\n".join(out)


def load_corpus(corpus: str):
    items = []
    for fp in sorted(glob.glob(os.path.join(corpus, "frames", "*.png"))):
        base = os.path.basename(fp)[:-4]
        gp = os.path.join(corpus, "ground-truth", base + ".txt")
        items.append((base, fp, gp if os.path.exists(gp) else None))
    return items


def pick_font(atlas, ex, min_share: float = 0.08, min_hits: int = 12):
    """Existing font of this cell size reading >= max(min_hits, min_share * glyph cells) cells exactly, else None."""
    n = sum(1 for row in ex.rows for c in row if c.kind == "glyph")
    best = (0, None)
    for fn in atlas.fonts_for(ex.grid["cell_w"], ex.grid["cell_h"]):
        h = gg._count_hits(ex, atlas, fn)
        if h > best[0]:
            best = (h, fn)
    return best[1] if n and best[0] >= max(min_hits, min_share * n) else None


def learn(corpus: str, items=None, skip=()):
    """Learn from every (frame, ground-truth) pair of the corpus, except ids in ``skip``."""
    items = items or load_corpus(corpus)
    frames = []
    for base, fp, gp in items:
        if base in skip:
            continue
        f = prepare_frame(fp)
        f["gt"] = gp
        frames.append(f)
    # biggest text first: plain consoles fix the canonical origin, dialogs/GRUB then follow by atlas evidence
    for f in frames:
        f["_mass"] = 0
        if f["grid"]["status"] == "ok":
            f["_ext0"] = gg.extract(f["im"], f["grid"])
            f["_mass"] = sum(1 for row in f["_ext0"].rows for c in row if c.kind == "glyph")
    frames.sort(key=lambda f: -f["_mass"])
    atlas = gg.Atlas()
    fontmap, aligned, reports = {}, {}, {}
    for f in frames:
        if f["grid"]["status"] != "ok" or not f.get("gt"):
            reports[f["id"]] = {"skipped": "no grid (%s)" % "; ".join(f["grid"]["reasons"]) if f["grid"]["status"] != "ok" else "no ground truth"}
            continue
        ex, hits = gg.choose_extraction(f["im"], f["grid"], atlas)
        f["ext"] = ex
        font = pick_font(atlas, ex)
        if font is None:
            g = f["grid"]
            font = "f%dx%d-%d" % (g["cell_w"], g["cell_h"], len(atlas.fonts) + 1)
        fontmap[f["id"]] = font
        pairs, rep = align(ex, read_gt(f["gt"]), atlas, font)
        rep["origin"] = [ex.grid["origin_x"], ex.grid["origin_y"]]
        rep["font"] = font
        aligned[f["id"]] = pairs
        reports[f["id"]] = rep
        for p in pairs:  # provisional, for later frames' phase/font choice
            if p["kind"] == "glyph" and p["char"] not in (" ", "?"):
                atlas.add(font, ex.grid["cell_w"], ex.grid["cell_h"], p["key"], p["char"], f["id"])
    frames = [f for f in frames if f["ext"] is not None]
    final, info = build_atlas(frames, aligned, fontmap)
    return final, {"frames": frames, "fontmap": fontmap, "aligned": aligned, "reports": reports, **info}


def conflict_report(atlas, state):
    out = []
    for font, key, g in atlas.conflicts():
        w = atlas.fonts[font]["cell_w"]
        h = atlas.fonts[font]["cell_h"]
        out.append({"font": font, "hex": key, "chars": g["c"], "frames": g["f"], "art": ascii_art(key, w, h)})
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("corpus")
    ap.add_argument("--out", required=True)
    ap.add_argument("--report")
    a = ap.parse_args(argv)
    atlas, st = learn(a.corpus)
    atlas.save(a.out)
    rep = {"fonts": {n: {"cell": [f["cell_w"], f["cell_h"]], "glyphs": len(f["glyphs"])} for n, f in atlas.fonts.items()},
           "frames": st["reports"],
           "fontmap": st["fontmap"], "conflicts": conflict_report(atlas, st),
           "quarantined_question_marks": st["quarantined_question_marks"]}
    if a.report:
        with open(a.report, "w", encoding="utf-8") as f:
            json.dump(rep, f, indent=1, ensure_ascii=False)
    print("fonts:", {n: len(f["glyphs"]) for n, f in atlas.fonts.items()})
    print("conflicts:", len(rep["conflicts"]), "quarantined '?':", len(rep["quarantined_question_marks"]))
    for fid, r in st["reports"].items():
        if "lines" in r:
            print("%-40s lines %2d aligned %2d ambiguous %d unaligned %d" % (fid, r["lines"], r["aligned"], len(r["ambiguous"]), len(r["unaligned"])))
        else:
            print("%-40s %s" % (fid, r))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
