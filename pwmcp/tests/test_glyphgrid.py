"""Tests for pwmcp/tools/glyphgrid (deterministic glyph-grid matching).

Synthetic cells exercise every rule (colour schemes, UNKNOWN is never a guess, conflicts, refusal of
resampled captures, no-grid); a few cropped real frames (tests/fixtures/glyphgrid/) pin behaviour on the
real TrueNAS 27.0.0-RC.1 console and installer fonts.  Pure Python + Pillow, no docker, no network.
"""
import io
import json
import os
import random
import sys

import pytest
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "tools", "glyphgrid"))
import glyphgrid as gg  # noqa: E402
import learn_atlas as la  # noqa: E402

FIX = os.path.join(HERE, "fixtures", "glyphgrid")
CHARS = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
BLACK, GREY, WHITE, BLUE = (0, 0, 0), (170, 170, 170), (255, 255, 255), (0, 0, 170)


def make_font(seed=7):
    """Distinct pseudo glyphs for CHARS: 7x10 ink in an 8x16 cell (margins like a VGA font)."""
    rng = random.Random(seed)
    font, seen = {}, set()
    for ch in CHARS:
        while True:
            rows = tuple(rng.getrandbits(7) | 0b0000010 for _ in range(10))
            if rows not in seen:
                seen.add(rows)
                font[ch] = rows
                break
    return font


FONT = make_font()


def render(lines, fg=GREY, bg=BLACK, cols=80, rows=24, font=FONT, reverse_cols=()):
    im = Image.new("RGB", (cols * 8, rows * 16), bg)
    px = im.load()
    for r, line in enumerate(lines):
        for c, ch in enumerate(line):
            if ch == " ":
                continue
            f, b = (bg, fg) if (r, c) in reverse_cols else (fg, bg)
            if (r, c) in reverse_cols:  # reverse video: paint the cell background too
                for y in range(16):
                    for x in range(8):
                        px[c * 8 + x, r * 16 + y] = b
            for gy, bits in enumerate(font[ch]):
                for gx in range(7):
                    if bits >> (6 - gx) & 1:
                        px[c * 8 + gx, r * 16 + 3 + gy] = f
    return im


def text_lines(n=14, width=60, seed=3):
    rng = random.Random(seed)
    words = ["Alpha", "Bravo", "c0de", "Delta9", "echo", "Fox", "G0lf", "hotel", "India", "Juliet", "k1lo", "Lima"]
    return [" ".join(rng.choice(words) for _ in range(width // 5))[:width] for _ in range(n)]


def png(im):
    b = io.BytesIO()
    im.save(b, "PNG")
    return b.getvalue()


def learn_from(im, lines):
    g = gg.detect_grid(im)
    assert g["status"] == "ok"
    ex = gg.extract(im, gg.grid_with_origin(g, im.size, 0, 0))
    pairs, rep = la.align(ex, lines)
    assert rep["aligned"] == len([ln for ln in lines if ln.strip()])
    atlas = gg.Atlas()
    for p in pairs:
        if p["kind"] == "glyph":
            atlas.add("syn", 8, 16, p["key"], p["char"], "t")
    return atlas


# ----------------------------------------------------------------------------- grid detection
def test_detect_grid_finds_8x16_without_hints():
    im = render(text_lines())
    g = gg.detect_grid(im)
    assert (g["status"], g["cell_w"], g["cell_h"]) == ("ok", 8, 16)
    assert [0, 0] in g["candidates"]


def test_detect_grid_hint_restricts_search():
    g = gg.detect_grid(render(text_lines()), hints=[(8, 16)])
    assert g["status"] == "ok" and (g["cell_w"], g["cell_h"]) == (8, 16)


def test_no_grid_for_noise_and_for_smooth_gradient():
    rng = random.Random(1)
    noise = Image.new("RGB", (640, 384))
    noise.putdata([(rng.randrange(256), rng.randrange(256), rng.randrange(256)) for _ in range(640 * 384)])
    assert gg.detect_grid(noise)["status"] == "no_grid"
    grad = Image.new("RGB", (640, 384))
    grad.putdata([(x * 255 // 639, y * 255 // 383, 128) for y in range(384) for x in range(640)])
    assert gg.detect_grid(grad)["status"] == "no_grid"
    blank = Image.new("RGB", (640, 384), BLACK)
    assert gg.detect_grid(blank)["status"] == "no_grid"


# ----------------------------------------------------------------------------- normalisation
def test_same_glyph_bitmap_in_every_colour_scheme():
    lines = text_lines(4, 40)
    variants = [render(lines, GREY, BLACK), render(lines, BLACK, GREY), render(lines, WHITE, BLUE)]
    keys = []
    for im in variants:
        g = gg.grid_with_origin({"cell_w": 8, "cell_h": 16, "origin_x": 0, "origin_y": 0}, im.size, 0, 0)
        ex = gg.extract(im, g)
        keys.append([c.key for row in ex.rows[:4] for c in row])
    assert keys[0] == keys[1] == keys[2]


def test_reverse_video_cell_reads_like_normal_cell():
    lines = ["Alpha Bravo"]
    normal = render(lines)
    rev = render(lines, reverse_cols={(0, c) for c in range(0, 5)})
    g = {"cell_w": 8, "cell_h": 16, "origin_x": 0, "origin_y": 0, "cols": 80, "rows": 24}
    a, b = gg.extract(normal, g), gg.extract(rev, g)
    assert [c.key for c in a.rows[0][:11]] == [c.key for c in b.rows[0][:11]]


def test_three_colour_cell_is_refused_not_binarised():
    im = render(["Alpha"])
    im.putpixel((2, 4), (255, 0, 0))  # a third colour inside the first cell
    g = {"cell_w": 8, "cell_h": 16, "origin_x": 0, "origin_y": 0, "cols": 80, "rows": 24}
    c = gg.extract(im, g).rows[0][0]
    assert c.kind == "multicolor"


# ----------------------------------------------------------------------------- learn + decode
def test_learn_then_read_exact_both_polarities():
    lines = text_lines(14, 60, seed=3)
    atlas = learn_from(render(lines), lines)
    other = text_lines(10, 60, seed=11)
    for fg, bg in ((GREY, BLACK), (BLACK, GREY), (WHITE, BLUE)):
        res = gg.read_image(render(other, fg, bg), atlas, grid_hints=[(8, 16)])
        assert res["status"] == "ok"
        assert res["rows"][:10] == [ln.ljust(80) for ln in other]
        assert res["unknown_cells"] == []
        assert set("".join(res["conf"][:10])) <= {"e", "b"}


def test_unknown_is_never_a_guess_and_reports_evidence():
    lines = text_lines(14, 60)
    atlas = learn_from(render(lines), lines)
    font = atlas.fonts["syn"]
    # forget 'k'
    for key, g in list(font["glyphs"].items()):
        if "k" in g["c"]:
            del font["glyphs"][key]
    res = gg.read_image(render(["k1lo Lima k"] + text_lines(8, 60, seed=5)), atlas, grid_hints=[(8, 16)])
    row = res["rows"][0]
    assert row.startswith(gg.UNKNOWN_CHAR + "1lo")
    assert res["conf"][0][0] == "u"
    cell = res["unknown_cells"][0]
    assert (cell["row"], cell["col"]) == (0, 0)
    assert "nearest" in cell and cell["distance"] > 0  # evidence only, never output
    assert "k" not in row


def test_hamming_tolerance_is_off_by_default():
    lines = text_lines(14, 60)
    atlas = learn_from(render(lines), lines)
    im = render(["Alpha Bravo"] + text_lines(8, 60, seed=5))
    px = im.load()
    px[2, 6] = BLACK if px[2, 6] != BLACK else GREY  # flip one pixel of the first glyph
    flipped = px[2, 6]
    res = gg.read_image(im, atlas, grid_hints=[(8, 16)])
    assert res["conf"][0][0] == "u" and res["rows"][0][0] == gg.UNKNOWN_CHAR
    assert res["unknown_cells"][0]["distance"] == 1
    tol = gg.read_image(im, atlas, grid_hints=[(8, 16)], max_hamming=1)
    assert tol["conf"][0][0] == "n" and tol["rows"][0][0] == "A"
    assert flipped in (BLACK, GREY)


def test_conflicting_labels_are_never_resolved_by_majority():
    lines = text_lines(14, 60)
    atlas = learn_from(render(lines), lines)
    key = next(iter(atlas.fonts["syn"]["glyphs"]))
    ch = next(iter(atlas.fonts["syn"]["glyphs"][key]["c"]))
    for _ in range(5):
        atlas.add("syn", 8, 16, bytes.fromhex(key), ch, "t")
    atlas.add("syn", 8, 16, bytes.fromhex(key), "~", "other")
    assert len(atlas.conflicts()) == 1
    assert atlas.lookup("syn", bytes.fromhex(key))[0] == "conflict"
    res = gg.read_image(render(lines[:2]), atlas, grid_hints=[(8, 16)])
    assert "x" in "".join(res["conf"])
    assert not any(c["status"] == "e" for c in res["unknown_cells"])


def test_unseen_font_reads_all_unknown():
    lines = text_lines(14, 60)
    atlas = learn_from(render(lines), lines)
    other = render(lines[:3], font=make_font(seed=99))
    res = gg.read_image(other, atlas, grid_hints=[(8, 16)])
    assert res["status"] == "ok"
    shown = "".join(res["rows"][:3]).replace(" ", "").replace(gg.UNKNOWN_CHAR, "")
    assert shown == ""  # nothing guessed
    assert res["unknown_cells"]


def test_atlas_json_roundtrip(tmp_path):
    lines = text_lines(14, 60)
    atlas = learn_from(render(lines), lines)
    p = tmp_path / "a.json"
    atlas.save(p)
    again = gg.Atlas.load(p)
    assert again.fonts == atlas.fonts
    with pytest.raises(ValueError):
        gg.Atlas.from_json('{"format": "other"}')


def test_find_phrase_and_region_demand_exact_cells():
    lines = text_lines(14, 60)
    atlas = learn_from(render(lines), lines)
    font = atlas.fonts["syn"]
    for key, g in list(font["glyphs"].items()):
        if "G" in g["c"]:
            del font["glyphs"][key]
    res = gg.read_image(render(["Alpha G0lf Bravo", "Alpha Bravo"] + text_lines(8, 60, seed=5)), atlas, grid_hints=[(8, 16)])
    assert gg.find_phrase(res, "Alpha Bravo") == [{"row": 1, "col": 0, "unknown_in_row": 0}]
    assert gg.find_phrase(res, "G0lf") == []
    text, bad = gg.region(res, 0, 1, 0, 16)
    assert bad == 1 and gg.UNKNOWN_CHAR in text
    assert gg.region(res, 1, 2, 0, 11)[1] == 0


# ----------------------------------------------------------------------------- resampling refusal
def test_resampled_capture_is_refused_not_guessed():
    lines = text_lines(14, 60)
    base = render(lines)
    atlas = learn_from(base, lines)
    w, h = base.size
    for name, im in {
        "0.9 bilinear round trip": base.resize((int(w * .9), int(h * .9)), Image.BILINEAR).resize((w, h), Image.BILINEAR),
        "1.25 bilinear": base.resize((int(w * 1.25), int(h * 1.25)), Image.BILINEAR),
        "0.9 lanczos round trip": base.resize((int(w * .9), int(h * .9)), Image.LANCZOS).resize((w, h), Image.LANCZOS),
    }.items():
        res = gg.read_image(im, atlas)
        assert res["status"] == "resampled", name
        assert res["rows"] == [] and "exact matching unavailable" in res["message"]


def test_integer_nearest_upscale_is_undone_exactly():
    lines = text_lines(14, 60)
    base = render(lines)
    atlas = learn_from(base, lines)
    big = base.resize((base.size[0] * 2, base.size[1] * 2), Image.NEAREST)
    assert gg.probe_scale(big)["integer_upscale"] == 2
    res = gg.read_image(big, atlas, grid_hints=[(8, 16)])
    assert res["status"] == "ok" and res["rows"][:14] == [ln.ljust(80) for ln in lines]


def test_non_integer_nearest_upscale_is_never_read_as_text():
    lines = text_lines(14, 60)
    base = render(lines)
    atlas = learn_from(base, lines)
    odd = base.resize((int(base.size[0] * 1.5), int(base.size[1] * 1.5)), Image.NEAREST)
    res = gg.read_image(odd, atlas)
    if res["status"] == "ok":  # crisp pixels pass the blur probe; the odd tiling is not in the atlas
        assert "e" not in "".join(res["conf"])
        assert "".join(res["rows"]).replace(" ", "").replace(gg.UNKNOWN_CHAR, "") == ""
    else:
        assert res["status"] in ("no_grid", "resampled")


# ----------------------------------------------------------------------------- alignment rules
def test_alignment_allows_any_gap_width_and_skips_box_rows():
    pat = "0B1111000111B0"
    assert la._windows(pat, ["abcd", "efg"]) == [[2, 3, 4, 5, 9, 10, 11]]
    assert la._windows("BBBBBBBB", ["a"]) == []  # a ruler row never carries text


# ----------------------------------------------------------------------------- real cropped frames
@pytest.fixture(scope="module")
def real():
    with open(os.path.join(FIX, "expected.json"), encoding="utf-8") as f:
        exp = json.load(f)
    return gg.Atlas.load(os.path.join(FIX, "truenas-27-vanilla.atlas.json")), exp


@pytest.mark.parametrize("name", ["crop-14-dialog.png", "crop-18-kernel.png", "crop-20-menu.png"])
def test_real_crops_read_exactly_with_zero_unknown_text_cells(real, name):
    atlas, exp = real
    res = gg.read_image(os.path.join(FIX, name), atlas, grid_hints=[(8, 16)])
    assert res["status"] == "ok"
    e = exp[name]
    assert res["font"] == e["font"]
    rows = [r.rstrip() for r in res["rows"]]
    for r, want in e["rows"].items():
        assert rows[int(r)] == want
    # every non-blank cell of the text rows is an exact match
    for r in e["rows"]:
        assert set(res["conf"][int(r)]) <= {"e", "b"}


def test_real_dialog_black_on_grey_key_phrase(real):
    atlas, exp = real
    res = gg.read_image(os.path.join(FIX, "crop-14-dialog.png"), atlas, grid_hints=[(8, 16)])
    for phrase in ("The TrueNAS installation on vda succeeded!", "Please reboot and remove the installation media.",
                   "Installation Succeeded"):
        hits = gg.find_phrase(res, phrase)
        assert len(hits) == 1, phrase
    # box-drawing cells are not in the transcribed ground truth: they stay UNKNOWN, never guessed
    assert any(u["row"] == 3 for u in res["unknown_cells"])
    assert all(c in "eub" for c in "".join(res["conf"]))


def test_real_digits_are_not_confused(real):
    atlas, exp = real
    res = gg.read_image(os.path.join(FIX, "crop-20-menu.png"), atlas, grid_hints=[(8, 16)])
    assert gg.find_phrase(res, "https://10.0.2.15")
    assert not gg.find_phrase(res, "16.0.2.15")
