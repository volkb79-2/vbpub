"""Speed paths of glyphgrid must be BIT-IDENTICAL to the reference implementation.

``glyphgrid.reference_mode()`` runs the original, unoptimised code (kept in the module); every fast path
(shared transition masks, banded upscale probe, log-step run removal with memcpy rolls, strip-based cell
extraction, translate-based bit packing, value-row candidate scoring) must give the same dict on the same
pixels.  ``GlyphReader`` (geometry cache + dirty cells) must give the same rows/conf/flags/unknown cells as
a full ``read_image`` of each frame.  The committed 20-frame corpus is read both ways below.  The full 242-frame
series is NOT in git (5.4 MB under ~/console-corpus), so a pytest test over it could only skip or fail the gate;
``tools/glyphgrid/bench_glyphgrid.py --check --series DIR`` does the identity check and the timing in one run
(see README).  No numpy anywhere: it was measured and rejected (README).
"""
import glob
import json
import os
import random
import sys

import pytest
from PIL import Image, ImageChops

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "tools", "glyphgrid"))
sys.path.insert(0, HERE)
import glyphgrid as gg  # noqa: E402
from test_glyphgrid import FONT, render, text_lines  # noqa: E402

FIX = os.path.join(HERE, "fixtures", "glyphgrid")
CORPUS = os.path.join(HERE, "corpus", "console", "truenas-27-vanilla", "frames")
FRAMES = sorted(glob.glob(os.path.join(CORPUS, "*.png")))


def canon(res):
    return json.dumps(res, sort_keys=True, default=str)


def both(fn):
    fast = fn()
    with gg.reference_mode():
        ref = fn()
    return fast, ref


@pytest.fixture(scope="module")
def atlas():
    return gg.Atlas.merge(gg.Atlas.load(os.path.join(FIX, "fonts.atlas.json")),
                          gg.Atlas.load(os.path.join(FIX, "truenas-27-vanilla.atlas.corrected-gt.json")))


# ----------------------------------------------------------------------------- building blocks
@pytest.mark.parametrize("size", [(7, 5), (40, 33), (200, 16)])
def test_roll_equals_imagechops_offset(size):
    rng = random.Random(size[0])
    im = Image.frombytes("L", size, bytes(rng.randrange(256) for _ in range(size[0] * size[1])))
    for sx, sy in [(0, 0), (1, 0), (0, 1), (3, 2), (-1, 0), (0, -4), (size[0], 0), (size[0] + 3, -size[1] - 1), (-5, 7)]:
        assert gg._roll(im, sx, sy).tobytes() == ImageChops.offset(im, -sx, -sy).tobytes(), (size, sx, sy)


@pytest.mark.parametrize("horizontal", [True, False])
@pytest.mark.parametrize("k", [1, 2, 3, 5, 8, 17, 33])
def test_remove_long_runs_fast_equals_reference(k, horizontal):
    rng = random.Random(k)
    W, H = 90, 70
    px = [255 if rng.random() < 0.55 else 0 for _ in range(W * H)]
    for _ in range(12):   # long straight runs, some wrapping the border
        if horizontal:
            y, x0, n = rng.randrange(H), rng.randrange(W), rng.randrange(1, 60)
            for i in range(n):
                px[y * W + (x0 + i) % W] = 255
        else:
            x, y0, n = rng.randrange(W), rng.randrange(H), rng.randrange(1, 50)
            for i in range(n):
                px[((y0 + i) % H) * W + x] = 255
    mask = Image.frombytes("L", (W, H), bytes(px))
    fast, ref = both(lambda: gg._remove_long_runs(mask, k, horizontal).tobytes())
    assert fast == ref


def _screens():
    lines = text_lines(14, 60)
    plain = render(lines)
    rev = render(lines, reverse_cols={(2, 3), (2, 4), (5, 9)})
    colour = render(lines, fg=(255, 170, 0), bg=(0, 0, 170))
    noise = Image.frombytes("RGB", (320, 192), bytes(random.Random(5).randrange(256) for _ in range(320 * 192 * 3)))
    gradient = Image.linear_gradient("L").resize((256, 256)).convert("RGB")
    blurred = plain.resize((plain.size[0] * 3 // 2, plain.size[1] * 3 // 2), Image.BILINEAR)
    up2 = plain.resize((plain.size[0] * 2, plain.size[1] * 2), Image.NEAREST)
    return {"plain": plain, "reverse": rev, "colour": colour, "noise": noise, "gradient": gradient, "blurred": blurred,
            "nearest2x": up2, "uniform": Image.new("RGB", (160, 96), (0, 0, 0))}


@pytest.mark.parametrize("name", sorted(_screens()))
def test_probe_grid_and_extraction_identical(name):
    im = _screens()[name]
    for fn in (lambda: gg.probe_scale(im), lambda: gg.ramp_ratio(im), lambda: gg.detect_grid(im), lambda: gg.detect_grid(im, hints=[(8, 16)])):
        fast, ref = both(fn)
        assert fast == ref
    g = gg.detect_grid(im)
    if g["status"] == "ok":
        def cells():
            ex = gg.extract(im, g)
            return [[(c.row, c.col, c.kind, c.key, c.bg, c.fg, c.ncolors) for c in row] for row in ex.rows]
        fast, ref = both(cells)
        assert fast == ref


def test_tied_two_colour_cell_uses_the_frame_colour_ranking_identically():
    """A cell with exactly half of each colour is decided by the frame-wide colour frequency (bg = the more common colour)."""
    im = Image.new("RGB", (8 * 20, 16 * 6), (0, 0, 0))
    px = im.load()
    for y in range(16):
        for x in range(8):
            px[8 + x, y] = (200, 200, 200) if y < 8 else (0, 0, 0)       # tied cell: half grey, half black
    for y in range(16 * 3, 16 * 6):                                      # make grey the more common colour overall
        for x in range(8 * 20):
            px[x, y] = (200, 200, 200)
    g = {"cell_w": 8, "cell_h": 16, "origin_x": 0, "origin_y": 0, "cols": 20, "rows": 6}

    def cell():
        c = gg.extract(im, g).rows[0][1]
        return (c.kind, c.key, c.bg, c.fg)
    fast, ref = both(cell)
    assert fast == ref and fast[0] == "glyph"
    px[8, 0] = (7, 7, 7)                                                  # three colours: refused, same in both
    fast, ref = both(lambda: gg.extract(im, g).rows[0][1].kind)
    assert fast == ref == "multicolor"


def test_read_image_synthetic_identical(atlas):
    lines = text_lines(14, 60)
    a = gg.Atlas()
    g = gg.detect_grid(render(lines))
    ex = gg.extract(render(lines), gg.grid_with_origin(g, render(lines).size, 0, 0))
    import learn_atlas as la
    pairs, _ = la.align(ex, lines)
    for p in pairs:
        if p["kind"] == "glyph":
            for src in ("t1", "t2"):
                a.add("syn", 8, 16, p["key"], p["char"], src)
    for im in (render(lines), render(lines, reverse_cols={(1, 1), (1, 2)}), render(lines).resize((1280, 768), Image.NEAREST)):
        fast, ref = both(lambda: canon(gg.read_image(im, a)))
        assert fast == ref


# ----------------------------------------------------------------------------- corpus-wide identity
@pytest.mark.parametrize("path", FRAMES, ids=[os.path.basename(p) for p in FRAMES])
def test_committed_corpus_frame_identical_to_reference(atlas, path):
    assert len(FRAMES) == 20, "the committed corpus has 20 curated frames"
    fast, ref = both(lambda: canon(gg.read_image(path, atlas)))
    assert fast == ref


# ----------------------------------------------------------------------------- GlyphReader (geometry cache + dirty cells)
CONTENT = ("rows", "conf", "flags", "unknown_cells", "aliases", "font", "font_share", "status", "grid", "probe")


def same_content(a, b):
    return all(canon(a.get(k)) == canon(b.get(k)) for k in CONTENT)


def paste_cells(im, cells, src_cell=(40, 10), w=8, h=16):
    """Copy the pixels of one cell over several others (a few changed cells, like typing)."""
    out = im.copy()
    sx, sy = src_cell
    blk = im.crop((sx * w, sy * h, (sx + 1) * w, (sy + 1) * h))
    for c, r in cells:
        out.paste(blk, (c * w, r * h))
    return out


@pytest.fixture(scope="module")
def frame_pair():
    path = [p for p in FRAMES if "18-kernel" in p or "kernel" in os.path.basename(p)] or FRAMES
    base = Image.open(path[0]).convert("RGB")
    return base


def test_reader_paths_and_identity_on_real_frame(atlas, frame_pair):
    base = frame_pair
    rd = gg.GlyphReader(atlas)
    r0 = rd.read(base)
    assert r0["session"]["path"] == "full" and same_content(r0, gg.read_image(base, atlas))
    again = rd.read(base)
    assert again["session"]["path"] == "identical" and same_content(again, r0)
    again["rows"][0] = "mutated"                                   # callers may mutate a returned dict
    assert rd.read(base)["rows"][0] != "mutated"
    edited = paste_cells(base, [(3, 5), (4, 5), (5, 5), (60, 9)])
    r1 = rd.read(edited)
    assert r1["session"]["path"] == "incremental" and 0 < r1["session"]["dirty_cells"] <= 4
    assert same_content(r1, gg.read_image(edited, atlas))
    r2 = rd.read(paste_cells(edited, [(100, 20)]))                 # chained increments keep matching a fresh read
    assert r2["session"]["path"] == "incremental" and same_content(r2, gg.read_image(paste_cells(edited, [(100, 20)]), atlas))
    r3 = rd.read(base)                                             # and changes back
    assert r3["session"]["path"] == "incremental" and same_content(r3, r0)


def test_reader_falls_back_to_full_when_geometry_or_scale_cannot_be_trusted(atlas, frame_pair):
    base = frame_pair
    rd = gg.GlyphReader(atlas)
    rd.read(base)
    shifted = ImageChops.offset(base, 0, 3)                        # whole screen moved 3 px: stale phase
    r = rd.read(shifted)
    assert r["session"]["path"] == "full" and same_content(r, gg.read_image(shifted, atlas))
    rd.read(base)
    heavy = paste_cells(base, [(c, r_) for r_ in range(0, 30) for c in range(0, 100, 7)])
    r = rd.read(heavy)
    assert r["session"]["path"] == "full" and same_content(r, gg.read_image(heavy, atlas))
    other_size = base.crop((0, 0, base.size[0] - 8, base.size[1]))
    r = rd.read(other_size)
    assert r["session"]["path"] == "full" and same_content(r, gg.read_image(other_size, atlas))
    rd.read(base)
    blurred = base.resize((base.size[0] * 3 // 2, base.size[1] * 3 // 2), Image.BILINEAR).resize(base.size, Image.BILINEAR)
    r = rd.read(blurred)
    assert r["status"] == gg.read_image(blurred, atlas)["status"] and r["session"]["path"] == "full"


def test_reader_margin_change_and_reference_mode_take_the_full_path(atlas, frame_pair):
    base = frame_pair
    rd = gg.GlyphReader(atlas)
    r0 = rd.read(base)
    g = r0["grid"]
    if g["origin_y"] > 0:
        edited = base.copy()
        edited.putpixel((3, 0), (1, 2, 3))
        assert rd.read(edited)["session"]["path"] == "full"
    with gg.reference_mode():
        assert rd.read(base)["session"]["path"] == "full"


def test_reader_synthetic_text_updates_match_full_reads():
    """Typing into a synthetic screen: every step incremental, every step equal to a fresh read."""
    lines = text_lines(14, 60)
    base_im = render(lines)
    import learn_atlas as la
    g = gg.detect_grid(base_im)
    ex = gg.extract(base_im, gg.grid_with_origin(g, base_im.size, 0, 0))
    pairs, _ = la.align(ex, lines)
    a = gg.Atlas()
    for p in pairs:
        if p["kind"] == "glyph":
            for src in ("t1", "t2"):
                a.add("syn", 8, 16, p["key"], p["char"], src)
    rd = gg.GlyphReader(a, grid_hints=[(8, 16)])
    cur = [ln.ljust(60) for ln in lines]
    assert rd.read(render(cur))["session"]["path"] == "full"
    rng = random.Random(11)
    chars = sorted(set("".join(lines)) - {" "})   # glyphs the atlas knows: an unknown glyph in a changed cell is (by design) a full-path frame
    for _ in range(6):
        r_ = rng.randrange(0, 14)
        row = list(cur[r_])
        for c in rng.sample(range(60), 3):
            row[c] = rng.choice(chars)
        cur[r_] = "".join(row)
        im = render(cur)
        got = rd.read(im)
        assert got["session"]["path"] == "incremental", got["session"]
        assert same_content(got, gg.read_image(im, a, grid_hints=[(8, 16)]))
