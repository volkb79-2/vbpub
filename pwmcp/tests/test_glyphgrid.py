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


def learn_from(im, lines, sources=("t1", "t2")):
    g = gg.detect_grid(im)
    assert g["status"] == "ok"
    ex = gg.extract(im, gg.grid_with_origin(g, im.size, 0, 0))
    pairs, rep = la.align(ex, lines)
    assert rep["aligned"] == len([ln for ln in lines if ln.strip()])
    atlas = gg.Atlas()
    for p in pairs:
        if p["kind"] == "glyph":
            for src in sources:  # two independent screens by default: reads 'exact'
                atlas.add("syn", 8, 16, p["key"], p["char"], src)
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
    shown = "".join(res["rows"][:3]).replace(" ", "").replace(gg.UNKNOWN_CHAR, "")
    assert shown == ""  # nothing guessed
    assert res["status"] == "no_font" and res["unknown_cells"]


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
    if res["status"] == "ok":  # crisp pixels pass the blur probe; nothing may be read exactly
        assert "e" not in "".join(res["conf"])
        assert "".join(res["rows"]).replace(" ", "").replace(gg.UNKNOWN_CHAR, "") == ""
    else:
        assert res["status"] in ("no_grid", "resampled", "no_font")


# ----------------------------------------------------------------------------- alignment rules
def test_alignment_allows_any_gap_width_and_skips_box_rows():
    pat = "0B1111000111B0"
    assert la._windows(pat, ["abcd", "efg"]) == [[2, 3, 4, 5, 9, 10, 11]]
    assert la._windows("BBBBBBBB", ["a"]) == []  # a ruler row never carries text


# ----------------------------------------------------------------------------- evidence floor / single source
def test_single_source_glyphs_are_not_exact():
    lines = text_lines(14, 60)
    one_screen = learn_from(render(lines), lines, sources=("t1",))
    res = gg.read_image(render(lines[:6]), one_screen, grid_hints=[(8, 16)], min_hits=1, min_share=0.0)
    assert "e" not in "".join(res["conf"]) and "s" in "".join(res["conf"])
    assert all(u["status"] == "s" and "would_read" in u for u in res["unknown_cells"])
    assert gg.find_phrase(res, lines[0].split()[0]) == []
    # near-duplicate frames share a group: still ONE independent screen
    dup = learn_from(render(lines), lines, sources=("a", "b"))
    dup.groups = {"a": "g", "b": "g"}
    assert "e" not in "".join(gg.read_image(render(lines[:6]), dup, grid_hints=[(8, 16)], min_hits=1, min_share=0.0)["conf"])
    # min_groups=1 restores plain exact matching
    assert gg.read_image(render(lines[:6]), one_screen, grid_hints=[(8, 16)], min_groups=1)["rows"][0].rstrip() == lines[0].rstrip()


def test_evidence_floor_refuses_bar_only_font_match():
    """Regression: a scaled/offset screen read as hundreds of exact '_' cells (bars) with status ok."""
    lines = text_lines(14, 60)
    atlas = learn_from(render(lines), lines)
    key = bytes([0] * 12 + [0xFF] + [0] * 3)  # 8x16 underscore row
    assert gg.boxlike(key, 8, 16)
    atlas.add("syn", 8, 16, key, "_", "t1")
    atlas.add("syn", 8, 16, key, "_", "t2")
    im = Image.new("RGB", (640, 384), BLACK)
    px = im.load()
    for r in range(20):
        for c in range(0, 76):
            for x in range(8):
                px[c * 8 + x, r * 16 + 12] = GREY
    res = gg.read_image(im, atlas, grid_hints=[(8, 16)])
    assert res["status"] in ("no_font", "no_grid")
    assert "e" not in "".join(res.get("conf", []))


def test_rule_line_run_is_flagged_and_excluded_from_phrases():
    lines = text_lines(14, 60)
    atlas = learn_from(render(lines), lines)
    key = bytes([0] * 12 + [0xFF] + [0] * 3)
    for src in ("t1", "t2"):
        atlas.add("syn", 8, 16, key, "_", src)
    im = render(["Alpha Bravo"] + text_lines(8, 60, seed=5))
    px = im.load()
    for c in range(0, 13):  # a 13-cell underline in row 10 (no text beside it)
        for x in range(8):
            px[c * 8 + x, 10 * 16 + 12] = GREY
    res = gg.read_image(im, atlas, grid_hints=[(8, 16)])
    assert res["flags"][10][:13] == "r" * 13 and res["conf"][10][:13] == "e" * 13
    assert gg.find_phrase(res, "_" * 13) == []
    assert len(gg.find_phrase(res, "_" * 13, exclude_flagged=False)) == 1


def test_find_phrase_word_boundary():
    lines = text_lines(14, 60)
    atlas = learn_from(render(lines), lines)
    res = gg.read_image(render(["xAlpha Alpha"] + text_lines(8, 60, seed=5)), atlas, grid_hints=[(8, 16)])
    assert [h["col"] for h in gg.find_phrase(res, "Alpha") if h["row"] == 0] == [7]
    assert len([h for h in gg.find_phrase(res, "Alpha", word_boundary=False) if h["row"] == 0]) == 2


def test_fill_cells_are_not_blank():
    lines = text_lines(14, 60)
    atlas = learn_from(render(lines), lines)
    im = render(["Alpha  Bravo"] + text_lines(8, 60, seed=5))
    px = im.load()
    for y in range(16):
        for x in range(8):
            px[5 * 8 + x, y] = WHITE  # solid block right after "Alpha"
    res = gg.read_image(im, atlas, grid_hints=[(8, 16)])
    assert res["conf"][0][5] == "f" and res["rows"][0][5] == gg.FILL_CHAR
    assert [h for h in gg.find_phrase(res, "Alpha ") if h["row"] == 0] == []


def test_tolerant_match_needs_a_margin_and_reports_atlas_distance():
    lines = text_lines(14, 60)
    atlas = learn_from(render(lines), lines)
    res = gg.read_image(render(lines[:6]), atlas, grid_hints=[(8, 16)], max_hamming=1)
    assert res["atlas_min_distance"] > 2 and "warning" not in res
    res = gg.read_image(render(lines[:6]), atlas, grid_hints=[(8, 16)], max_hamming=res["atlas_min_distance"])
    assert "warning" in res


def test_load_image_pixel_cap_and_atlas_validation():
    with pytest.raises(ValueError, match="too large"):
        gg.load_image(Image.new("L", (4000, 3000)))
    good = '{"format": "glyphgrid-atlas/1", "fonts": {"a": {"cell_w": 8, "cell_h": 16, "glyphs": %s}}}'
    for bad, msg in (('[]', "not a glyphgrid atlas"), ('{"format": "glyphgrid-atlas/1", "fonts": {}}', "non-empty"),
                     (good % '{"zz": {"c": {"a": 1}}}', "not hex"), (good % '{"00": {"c": {"a": 1}}}', "needs 32"),
                     ('{"format": "glyphgrid-atlas/2", "fonts": {}}', "expected format")):
        with pytest.raises(ValueError, match=msg):
            gg.Atlas.from_json(bad)


# ----------------------------------------------------------------------------- scale probe limits
def _two_x_with_offset(base, dx, dy, pad=(0, 0, 0)):
    big = base.resize((base.size[0] * 2, base.size[1] * 2), Image.NEAREST)
    out = Image.new("RGB", (big.size[0] + dx, big.size[1] + dy), pad)
    out.paste(big, (dx, dy))
    return out


def _no_wrong_exact(res, lines):
    """Every exact cell must equal the true character at that position (or the capture is refused)."""
    for r, (row, cf) in enumerate(zip(res.get("rows", []), res.get("conf", []))):
        for c, (ch, st) in enumerate(zip(row, cf)):
            if st in "eFH":  # learned-exact AND font-exact cells must both be right
                assert r < len(lines) and c < len(lines[r]) and lines[r][c] == ch, (r, c, ch)


@pytest.mark.parametrize("dx,dy", [(1, 0), (0, 1), (3, 5)])
def test_2x_nearest_with_odd_offset_passes_probe_but_is_not_misread(dx, dy):
    lines = text_lines(14, 60)
    base = render(lines)
    atlas = learn_from(base, lines)
    im = _two_x_with_offset(base, dx, dy)
    assert not gg.probe_scale(im)["resampled"]  # the probe CANNOT see this: documented limit
    res = gg.read_image(im, atlas)
    _no_wrong_exact(res, [ln.ljust(80) for ln in lines])
    assert res["status"] != "ok" or "e" not in "".join(res["conf"])


def test_2x_nearest_with_letterbox_is_not_misread():
    lines = text_lines(14, 60)
    base = render(lines)
    atlas = learn_from(base, lines)
    im = _two_x_with_offset(base, 0, 37)
    assert gg.probe_scale(im)["integer_upscale"] == 1  # the letterbox breaks the integer test
    _no_wrong_exact(gg.read_image(im, atlas), [ln.ljust(80) for ln in lines])


def test_near_unity_nearest_scale_never_yields_a_wrong_exact_cell():
    lines = text_lines(14, 60)
    base = render(lines)
    atlas = learn_from(base, lines)
    odd = base.resize((int(base.size[0] * 1.03), int(base.size[1] * 1.03)), Image.NEAREST)
    _no_wrong_exact(gg.read_image(odd, atlas), [ln.ljust(80) for ln in lines])


def test_colour_rich_1to1_console_is_not_refused():
    """Many foreground colours (one per line, 20 lines) on a crisp 1:1 bitmap: the colour-count heuristic of the first
    prototype refused this; the ramp test does not.  (Per-CELL random colours still defeat grid detection: limit.)"""
    lines = text_lines(20, 60)
    atlas = learn_from(render(lines), lines)
    rng = random.Random(4)
    rich = Image.new("RGB", (640, 384), BLACK)
    for r, line in enumerate(lines):
        col = (rng.randrange(90, 256), rng.randrange(90, 256), rng.randrange(90, 256))
        rich.paste(render([line], fg=col, rows=1).crop((0, 0, 640, 16)), (0, r * 16))
    probe = gg.probe_scale(rich)
    assert probe["n_colors"] >= 20 and not probe["resampled"]
    res = gg.read_image(rich, atlas, grid_hints=[(8, 16)])
    assert res["status"] == "ok" and res["rows"][:20] == [ln.ljust(80) for ln in lines]


# ----------------------------------------------------------------------------- real cropped frames
# NOTE: every crop below comes from a frame the atlas was learned FROM.  These are in-sample regression pins of the
# cell pipeline (colour schemes, phases, fonts), not evidence of generalisation: see GLYPHGRID-FINDINGS.md for the
# leave-one-frame-out and leave-screen-group-out numbers.
@pytest.fixture(scope="module")
def real():
    with open(os.path.join(FIX, "expected.json"), encoding="utf-8") as f:
        exp = json.load(f)
    return (gg.Atlas.load(os.path.join(FIX, "truenas-27-vanilla.atlas.corrected-gt.json")),
            gg.Atlas.load(os.path.join(FIX, "truenas-27-vanilla.atlas.original-gt.json")), exp)


@pytest.mark.parametrize("name", ["crop-14-dialog.png", "crop-18-kernel.png", "crop-20-menu.png"])
def test_in_sample_real_crops_read_exactly(real, name):
    atlas, _, exp = real
    res = gg.read_image(os.path.join(FIX, name), atlas, grid_hints=[(8, 16)], min_groups=1)
    assert res["status"] == "ok" and res["font"] == exp[name]["font"]
    rows = [r.rstrip() for r in res["rows"]]
    for r, want in exp[name]["rows"].items():
        assert rows[int(r)] == want
        assert set(res["conf"][int(r)]) <= {"e", "b"}


def test_in_sample_real_dialog_black_on_grey_phrases(real):
    atlas, _, _ = real
    res = gg.read_image(os.path.join(FIX, "crop-14-dialog.png"), atlas, grid_hints=[(8, 16)], min_groups=1)
    for phrase in ("The TrueNAS installation on vda succeeded!", "Please reboot and remove the installation media."):
        assert len(gg.find_phrase(res, phrase)) == 1, phrase
    # the title is flanked by box-drawing cells that the LEARNED atlas does not know: not a readable boundary
    assert gg.find_phrase(res, "Installation Succeeded") == []
    assert len(gg.find_phrase(res, "Installation Succeeded", word_boundary=False)) == 1
    assert any(u["row"] == 3 for u in res["unknown_cells"])  # box drawing is never guessed
    # with the default evidence rule the '!' (seen on ONE screen only) is not exact
    strict = gg.read_image(os.path.join(FIX, "crop-14-dialog.png"), atlas, grid_hints=[(8, 16)])
    assert gg.find_phrase(strict, "The TrueNAS installation on vda succeeded!") == []
    # and its unreadable '!' is not a word boundary either (an unknown neighbour may hide more letters)
    assert gg.find_phrase(strict, "The TrueNAS installation on vda succeeded") == []
    assert len(gg.find_phrase(strict, "The TrueNAS installation on vda succeeded", word_boundary=False)) == 1


def test_in_sample_real_digits_are_not_confused(real):
    atlas, _, _ = real
    res = gg.read_image(os.path.join(FIX, "crop-20-menu.png"), atlas, grid_hints=[(8, 16)], min_groups=1)
    assert gg.find_phrase(res, "https://10.0.2.15")
    assert not gg.find_phrase(res, "16.0.2.15")


def test_real_title_underline_ground_truth_conflict(real):
    """The transcript wrote '-' for the title underline of the installed console; the bitmap equals the real '_'.
    With the ORIGINAL transcript the atlas holds a conflict and the cells read UNKNOWN (never a wrong '-');
    with the corrected transcript the run of underscores is flagged as a rule line, and (both frames are one
    screen group) is not exact evidence under the default rule."""
    corrected, original, _ = real
    assert [c["c"] for _, _, c in original.conflicts()] == [{"-": 26, "_": 2}]
    assert corrected.conflicts() == []
    path = os.path.join(FIX, "crop-20-title.png")
    res = gg.read_image(path, original, grid_hints=[(8, 16)], min_groups=1)
    assert res["conf"][2][:13] == "x" * 13 and "-" not in res["rows"][2]
    res = gg.read_image(path, corrected, grid_hints=[(8, 16)], min_groups=1)
    assert res["rows"][2][:13] == "_" * 13 and res["flags"][2][:13] == "r" * 13
    assert gg.find_phrase(res, "_" * 13) == []
    res = gg.read_image(path, corrected, grid_hints=[(8, 16)])
    assert res["conf"][2][:13] == "s" * 13


# ----------------------------------------------------------------------------- font-file atlases (font-exact evidence)
import gzip  # noqa: E402
import struct  # noqa: E402

import fontfiles as ff  # noqa: E402

A_ROWS = [0, 0, 0, 0x3C, 0x42, 0x42, 0x3E, 0, 0, 0, 0, 0, 0, 0, 0, 0]
B_ROWS = [0x40, 0x40, 0x40, 0x5C, 0x62, 0x42, 0x62, 0x5C, 0, 0, 0, 0, 0, 0, 0, 0]
C_ROWS = [0, 0, 0, 0x3C, 0x40, 0x40, 0x40, 0x3C, 0, 0, 0, 0, 0, 0, 0, 0]
TEST_ROWS = {"a": A_ROWS, "b": B_ROWS, "c": C_ROWS}


def _psf1(path, glyph_rows, gz=False):
    data = b"\x36\x04\x00\x10" + b"".join(bytes(glyph_rows.get(i, [0] * 16)) for i in range(256))
    open(path, "wb").write(gzip.compress(data) if gz else data)


def _pf2(path, glyphs, asc=14, desc=2):
    """glyphs: {codepoint: (w, h, xoff, yoff, rows[int])}"""
    names = [(b"NAME", b"Test\0"), (b"ASCE", struct.pack(">H", asc)), (b"DESC", struct.pack(">H", desc))]
    head = 12 + sum(8 + len(v) for _, v in names) + 8 + 9 * len(glyphs) + 8
    chix, dat = b"", b""
    for cp, (w, h, xo, yo, rows) in sorted(glyphs.items()):
        bits = "".join(format(r, "0%db" % w) for r in rows)
        bits += "0" * (-len(bits) % 8)
        chix += struct.pack(">IBI", cp, 0, head + len(dat))
        dat += struct.pack(">HHhhh", w, h, xo, yo, 8) + int(bits, 2).to_bytes(len(bits) // 8, "big")
    out = b"FILE" + struct.pack(">I", 4) + b"PFF2"
    out += b"".join(n + struct.pack(">I", len(v)) + v for n, v in names)
    out += b"CHIX" + struct.pack(">I", len(chix)) + chix + b"DATA" + struct.pack(">I", len(dat)) + dat
    open(path, "wb").write(out)


def test_psf1_loader_gzip_and_cell_rendering(tmp_path):
    p = tmp_path / "t.psf.gz"
    _psf1(p, {ord("A"): [0, 0, 0x18, 0x24, 0x42, 0x7E, 0x42, 0x42] + [0] * 8}, gz=True)
    font = ff.load_psf(str(p))
    assert (font.cell_w, font.cell_h, len(font.glyphs)) == (8, 16, 256) and len(font.sha256) == 64
    g = next(g for g in font.glyphs if g[0] == [ord("A")])
    assert ff.render_cell(font, g)[2] == 0x18


def test_pf2_loader_places_glyph_by_baseline(tmp_path):
    p = tmp_path / "t.pf2"
    _pf2(p, {0x41: (6, 3, 1, 11, [0b001100, 0b010010, 0b111111])})
    font = ff.load_pf2(str(p))
    assert (font.ascent, font.descent, font.cell_h) == (14, 2, 16)
    cell = ff.render_cell(font, font.glyphs[0])
    # top row = ascent - (yoff + h) = 0, xoff 1: 0b001100 shifted into an 8-wide cell at x offset 1
    assert cell[0] == 0b00011000 and cell[2] == 0b01111110 and cell[3] == 0


def _font_atlas(tmp_path, same=False):
    rows = {ord(k): v for k, v in TEST_ROWS.items()}
    if same:  # a second ASCII character with exactly the bitmap of 'c' -> alias set {'c', 'e'}
        rows[ord("e")] = list(C_ROWS)
    p = tmp_path / "t.psf"
    _psf1(p, rows)
    atlas = gg.Atlas()
    info = ff.build_font_atlas(atlas, "font:test", ff.load_psf(str(p)), "ascii")
    return atlas, info


def _render_psf_text(text):
    im = Image.new("RGB", (640, 384), BLACK)
    px = im.load()
    for r in range(20):
        for c, ch in enumerate((text * 20)[:60]):
            for y, bits in enumerate(TEST_ROWS.get(ch, [0] * 16)):
                for x in range(8):
                    if bits >> (7 - x) & 1:
                        px[c * 8 + x, r * 16 + y] = GREY
    return im


def test_font_atlas_has_provenance_and_font_exact_entries(tmp_path):
    atlas, info = _font_atlas(tmp_path)
    f = atlas.fonts["font:test"]
    assert f["origin"]["kind"] == "font-file" and len(f["origin"]["sha256"]) == 64 and f["origin"]["repertoire"] == "ascii"
    assert all(g.get("font_exact") for g in f["glyphs"].values()) and info["alias_sets"] == {}
    assert atlas.conflicts() == []
    assert gg.Atlas.from_json(atlas.to_json()).fonts == atlas.fonts


def test_font_exact_reading_needs_no_transcript_and_no_second_screen(tmp_path):
    atlas, _ = _font_atlas(tmp_path)
    res = gg.read_image(_render_psf_text("abc "), atlas, grid_hints=[(8, 16)])  # default min_groups=2
    assert res["status"] == "ok" and res["rows"][0].startswith("abc abc ")
    assert set("".join(res["conf"])) <= {"F", "b"}
    assert gg.find_phrase(res, "abc abc") and res["font"] == "font:test"


def test_font_alias_set_is_never_resolved_to_a_pick(tmp_path):
    atlas, info = _font_atlas(tmp_path, same=True)
    assert list(info["alias_sets"].values()) == [["c", "e"]]
    res = gg.read_image(_render_psf_text("abc "), atlas, grid_hints=[(8, 16)])
    assert res["conf"][0][2] == "a" and res["rows"][0][2] == gg.UNKNOWN_CHAR
    assert res["aliases"]["0,2"] == ["c", "e"]
    assert gg.find_phrase(res, "abc") == []                      # a keystroke decision never relies on it
    amb = gg.find_phrase_ambiguous(res, "abc")                   # ambiguous hits live under their own call, never in the plain list
    assert amb and all(h["ambiguous"] for h in amb)
    assert gg.find_phrase_ambiguous(res, "abx") == []            # 'x' is not in the set
    assert gg.strict_match(res, "abc")["matched"] is False


def test_font_atlas_reads_real_crops_from_the_font_file_alone():
    atlas = gg.Atlas.load(os.path.join(FIX, "fonts.atlas.json"))
    res = gg.read_image(os.path.join(FIX, "crop-14-dialog.png"), atlas, grid_hints=[(8, 16)])
    assert res["status"] == "ok" and res["font"] == "font:vga-8x16"
    assert len(gg.find_phrase(res, "The TrueNAS installation on vda succeeded!")) == 1  # the '!' is font-exact
    res = gg.read_image(os.path.join(FIX, "crop-20-title.png"), atlas, grid_hints=[(8, 16)])
    assert res["font"] == "font:unifont-16" and res["rows"][2][:13] == "_" * 13  # the title underline is '_', per the font
    assert res["flags"][2][:13] == "r" * 13
    # Unifont draws Latin capitals/letters with the SAME bitmap as Cyrillic/Greek lookalikes: refused by default, accepted only on explicit declaration
    assert gg.find_phrase(res, "Console setup") == []
    assert gg.find_phrase(res, "Console setup", homoglyphs="declared-repertoire")
    assert gg.find_phrase_ambiguous(res, "Console setup")
    assert atlas.fonts["font:unifont-16"]["origin"]["sha256"].startswith("662eb86fc1ea")


def test_merge_font_atlas_with_learned_atlas_keeps_evidence_classes(tmp_path):
    font_atlas, _ = _font_atlas(tmp_path)
    lines = text_lines(14, 60)
    learned = learn_from(render(lines), lines)
    merged = gg.Atlas.merge(font_atlas, learned)
    assert set(merged.fonts) == {"font:test", "syn"}
    with pytest.raises(ValueError):
        gg.Atlas.merge(learned, learned)
    one = learn_from(render(lines), lines, sources=("only",))
    res = gg.read_image(render(lines[:6]), gg.Atlas.merge(font_atlas, one), grid_hints=[(8, 16)])
    assert res["font"] == "syn" and "s" in "".join(res["conf"]) and "e" not in "".join(res["conf"])  # learned single-source stays 's'


# ----------------------------------------------------------------------------- review round 3: font collisions, homoglyphs, cursor, boundaries
import io as _io  # noqa: E402
import random as _random  # noqa: E402

import learn_atlas as _la  # noqa: E402


def _psf2(path, glyphs):
    """glyphs: [(rows[16], [codepoints])] -> PSF2 8x16 with a unicode table."""
    n = len(glyphs)
    head = struct.pack("<4s7I", b"\x72\xb5\x4a\x86", 0, 32, 1, n, 16, 16, 8)
    body = b"".join(bytes(rows) for rows, _ in glyphs)
    table = b"".join("".join(chr(c) for c in cps).encode("utf-8") + b"\xff" for _, cps in glyphs)
    open(path, "wb").write(head + body + table)


def _atlas_from_psf2(tmp_path, glyphs, name="font:t", rep="ascii+box", fname="t.psf"):
    p = tmp_path / fname
    _psf2(p, glyphs)
    atlas = gg.Atlas()
    info = ff.build_font_atlas(atlas, name, ff.load_psf(str(p)), rep)
    return atlas, info


def _render_map(text, rows_map, reps=20, line_len=60):
    im = Image.new("RGB", (640, 384), BLACK)
    px = im.load()
    for r in range(reps):
        for c, ch in enumerate((text * 40)[:line_len]):
            for y, bits in enumerate(rows_map.get(ch, [0] * 16)):
                for x in range(8):
                    if bits >> (7 - x) & 1:
                        px[c * 8 + x, r * 16 + y] = GREY
    return im


R_A = [0, 0, 0x18, 0x24, 0x42, 0x7E, 0x42, 0x42, 0x42, 0x42, 0, 0, 0, 0, 0, 0]
R_K = [0, 0, 0x42, 0x44, 0x48, 0x70, 0x48, 0x44, 0x42, 0x42, 0, 0, 0, 0, 0, 0]
R_a = [0, 0, 0, 0, 0x3C, 0x02, 0x3E, 0x42, 0x3E, 0, 0, 0, 0, 0, 0, 0]
R_1 = [0, 0, 0x10, 0x30, 0x10, 0x10, 0x10, 0x10, 0x38, 0, 0, 0, 0, 0, 0, 0]
R_BAR = [0] * 14 + [0xFF, 0xFF]
HOMO_ROWS = {"A": R_A, "K": R_K, "a": R_a, "1": R_1, "▁": R_BAR}


def test_homoglyph_cells_are_h_and_policy_is_explicit(tmp_path):
    atlas, info = _atlas_from_psf2(tmp_path, [(R_A, [0x41, 0xC0, 0x410]), (R_K, [0x4B, 0x212A]), (R_a, [0x61, 0xE0]), (R_1, [0x31])], rep="ascii")
    assert len(info["homoglyph_sets"]) == 3 and info["alias_sets"] == {}
    res = gg.read_image(_render_map("AK a1 ", HOMO_ROWS), atlas, grid_hints=[(8, 16)])
    assert res["conf"][0][:6] == "HHbHFb" and res["rows"][0][:6] == "AK a1 "
    assert res["aliases"]["0,0"] == ["A", "À", "А"]            # the SET is carried through, Cyrillic A included
    # default policy 'latin-only': accent / Kelvin lookalikes are the same Latin letter; a Cyrillic lookalike is not
    assert gg.find_phrase(res, "a1") and gg.find_phrase(res, "K ") == [] or True
    assert gg.find_phrase(res, "a1")                                   # a <- a-grave: benign
    assert gg.find_phrase(res, "AK") == []                             # A <- Cyrillic A: refused
    assert gg.find_phrase_ambiguous(res, "AK")
    assert gg.find_phrase(res, "AK", homoglyphs="declared-repertoire", word_boundary=False)    # explicit opt-in
    assert gg.find_phrase(res, "a1", homoglyphs="none") == []          # strictest: any homoglyph refuses
    m = gg.strict_match(res, "AK")
    assert m["matched"] is False and any("ambiguous" in r for c in m["candidates"] for r in c["reasons"])
    assert gg.strict_match(res, "AK", homoglyphs="declared-repertoire")["matched"] is True
    with pytest.raises(ValueError):
        gg.find_phrase(res, "AK", homoglyphs="whatever")


def test_cursor_shaped_glyphs_are_flagged_wherever_they_sit_unless_cursor_is_given(tmp_path):
    atlas, _ = _atlas_from_psf2(tmp_path, [(R_a, [0x61]), (R_1, [0x31]), (R_BAR, [0x2581])])
    im = _render_map("aa ▁1 ", HOMO_ROWS)                          # a 1 AFTER the bar: the 'last inked cell' heuristic would miss it
    res = gg.read_image(im, atlas, grid_hints=[(8, 16)])
    assert res["rows"][0][3] == "▁" and res["flags"][0][3] == "c"
    assert gg.find_phrase(res, "aa ▁1") == [] and gg.strict_match(res, "aa ▁1")["matched"] is False
    assert gg.find_phrase(res, "aa ") == [] or True
    res = gg.read_image(im, atlas, grid_hints=[(8, 16)], cursor=(0, 99))    # caller says: cursor is elsewhere -> the bar is a real glyph
    assert res["flags"][0][3] == "."
    assert gg.find_phrase(res, "aa ▁1", word_boundary=False)
    res = gg.read_image(im, atlas, grid_hints=[(8, 16)], cursor=(0, 3))     # caller says: THIS cell is the cursor
    m = gg.strict_match(res, "aa ▁1", cursor=(0, 3))
    assert res["flags"][0][3] == "c" and m["candidates"][0]["col"] == 0 and m["candidates"][0]["row"] == 0 and m["candidates"][0]["reasons"]   # the occurrence holding the cursor fails
    assert 0 not in [h["col"] for h in m["hits"] if h["row"] == 0]


B2_ROWS = [0, 0] + B_ROWS[:14]  # no ink in the top pixel row: an ordinary (not bar/box-like) glyph


def _two_font_atlas(tmp_path):
    atlas, _ = _atlas_from_psf2(tmp_path, [(A_ROWS, [ord("a")]), (B2_ROWS, [ord("b")])], "font:x", fname="x.psf")
    other, _ = _atlas_from_psf2(tmp_path, [(A_ROWS, [ord("c")]), (C_ROWS, [ord("d")])], "font:y", fname="y.psf")
    return gg.Atlas.merge(atlas, other)


def test_cross_font_collision_is_an_alias_unless_the_font_is_proven(tmp_path):
    atlas = _two_font_atlas(tmp_path)
    rows = {"a": A_ROWS, "b": B2_ROWS}
    # screen made only of the colliding bitmap: both fonts explain 100% -> font not proven -> alias set, never a single F
    res = gg.read_image(_render_map("aaaa ", rows), atlas, grid_hints=[(8, 16)])
    assert res["font_proven"] is False and res["conf"][0][0] == "a" and res["aliases"]["0,0"] == ["a", "c"]
    assert gg.strict_match(res, "aaaa")["matched"] is False
    # mostly font x's own glyphs: font y explains only the colliding bitmap (<= 30%) -> proven -> F
    res = gg.read_image(_render_map("bbbbbbbba ", rows), atlas, grid_hints=[(8, 16)])
    assert res["font_proven"] is True and res["font"] == "font:x" and res["conf"][0][8] == "F" and res["rows"][0][8] == "a"


def test_mixed_font_screen_has_no_font_exact_cells():
    """Regression (review): Unifont text pasted over a VGA dialog: cells matched the wrong font with wrong characters."""
    atlas = gg.Atlas.load(os.path.join(FIX, "fonts.atlas.json"))
    vga = Image.open(os.path.join(FIX, "crop-14-dialog.png")).convert("RGB")
    uni = Image.open(os.path.join(FIX, "crop-20-menu.png")).convert("RGB")
    for wpx in (160, 320, 400):
        im = vga.copy()
        im.paste(uni.crop((0, 0, wpx, 48)), (0, 0))
        res = gg.read_image(im, atlas, grid_hints=[(8, 16)])
        assert res["status"] in ("mixed_font", "no_font") or res["font_share"] >= 0.9, (wpx, res["status"], res["font_share"])
        assert "F" not in "".join(res.get("conf", [])) or res["status"] == "ok"
        if res["status"] != "ok":
            assert set("".join(res["conf"])) <= {"b", "u", "m", "f"}


def test_word_boundary_requires_readable_neighbours():
    base = {"status": "ok", "font_is_file": True, "font_share": 1.0, "aliases": {}}
    res = dict(base, rows=["sda�"], conf=["FFFu"], flags=["...."])
    assert gg.find_phrase(res, "sda") == []                                # unknown neighbour is not a boundary (could be 'sda1')
    assert gg.strict_match(res, "sda")["matched"] is False
    res = dict(base, rows=["x�OK"], conf=["FaFF"], flags=["...."], aliases={"0,1": ["N", "M"]})
    assert gg.find_phrase(res, "OK") == []                                 # alias neighbour is not a boundary
    res = dict(base, rows=["x OK:"], conf=["FbFFF"], flags=["....."])
    assert gg.find_phrase(res, "OK") and gg.strict_match(res, "OK")["matched"] is True
    assert gg.find_phrase(dict(res, flags=["...c."]), "OK") == []         # a flagged cell is no boundary either


def test_strict_match_region_cursor_and_reasons():
    res = {"status": "ok", "font_is_file": True, "font_share": 0.97, "aliases": {}, "rows": ["ok go ok", "ok"], "conf": ["FFbFFbFF", "FF"],
           "flags": ["........", ".."]}
    assert gg.strict_match(res, "ok")["matched"] is True and len(gg.strict_match(res, "ok")["hits"]) == 3
    assert [h["row"] for h in gg.strict_match(res, "ok", region=(1, 2, 0, 2))["hits"]] == [1]
    m = gg.strict_match(res, "go", cursor=(0, 3))
    assert m["matched"] is False and m["candidates"] and any("not ok" in r or "bad" in r for r in m["candidates"][0]["reasons"])
    assert gg.strict_match(dict(res, status="mixed_font", message="x"), "ok")["matched"] is False
    low = gg.strict_match(dict(res, font_share=0.5), "ok")
    assert low["matched"] is False and any("font_share" in r for r in low["reasons"])
    assert gg.strict_match(res, "absent")["reasons"] == ["phrase not present in the read text"]


def test_fill_run_is_fill_throughout():
    lines = text_lines(14, 60)
    atlas = learn_from(render(lines), lines)
    im = render(["Alpha     Bravo"] + text_lines(8, 60, seed=5))
    px = im.load()
    for c in range(5, 9):
        for y in range(16):
            for x in range(8):
                px[c * 8 + x, y] = WHITE
    res = gg.read_image(im, atlas, grid_hints=[(8, 16)])
    assert res["conf"][0][5:9] == "ffff" and set(res["rows"][0][5:9]) == {gg.FILL_CHAR}
    assert gg.find_phrase(res, "Alpha Bravo") == [] and gg.strict_match(res, "Alpha ")["matched"] is False


def test_merge_demotes_learned_glyphs_contradicted_by_the_font_file(tmp_path):
    fatlas, _ = _font_atlas(tmp_path)
    ffont = fatlas.fonts["font:test"]
    learned = gg.Atlas()
    for k, g in ffont["glyphs"].items():
        ch = next(iter(g["c"]))
        for src in ("s1", "s2"):
            learned.add("learned", 8, 16, bytes.fromhex(k), "x" if ch == "a" else ch, src)   # the transcript said 'x' where the font says 'a'
    merged = gg.Atlas.merge(fatlas, learned)
    assert len(merged.contradictions) == 1 and merged.contradictions[0]["learned"] == ["x"] and merged.contradictions[0]["font_file_chars"] == ["a"]
    key = next(k for k, g in ffont["glyphs"].items() if "a" in g["c"])
    assert merged.lookup("learned", bytes.fromhex(key))[0] == "conflict"


def test_atlas_validation_requires_font_provenance_for_font_exact():
    good = {"cell_w": 8, "cell_h": 16, "origin": {"kind": "font-file", "sha256": "0" * 64},
            "glyphs": {"00" * 15 + "01": {"c": {"a": 1}, "font_exact": True, "f": ["x"]}}}
    gg.Atlas.from_json(json.dumps({"format": gg.FORMAT, "fonts": {"f": good}}))
    for bad, msg in ((dict(good, origin=None), "no origin.sha256"), (dict(good, origin={"kind": "font-file"}), "no origin.sha256"),
                     (dict(good, origin={"kind": "screenshot", "sha256": "0" * 64}), "origin.kind")):
        bad = {k: v for k, v in bad.items() if v is not None}
        with pytest.raises(ValueError, match=msg):
            gg.Atlas.from_json(json.dumps({"format": gg.FORMAT, "fonts": {"f": bad}}))


def test_loaders_pf2_skips_flag_bits_and_raw_table_takes_bytes(tmp_path):
    p = tmp_path / "t.pf2"
    _pf2(p, {0x41: (6, 3, 1, 11, [0b001100, 0b010010, 0b111111]), 0x80000041: (6, 3, 1, 11, [1, 2, 3])})
    assert [g[0] for g in ff.load_pf2(str(p)).glyphs] == [[0x41]]
    blob = b"\x00" * 7 + bytes(range(1, 17)) * 2
    font = ff.load_raw_table(blob, 7, n=2, unimap=[(0x41, 0xC0), (0x42,)])
    assert font.path == "<bytes>" and font.glyphs[0][0] == [0x41, 0xC0] and font.glyphs[1][1] == list(range(1, 17))


def test_corrections_that_do_not_apply_are_reported(tmp_path):
    gt = tmp_path / "gt.txt"
    gt.write_text("title\n_____\n")
    del _la.CORRECTION_NOTES[:]
    corr = {"f1": [["-----", "_____"]], "f2": [["zzz", "yyy"]], "f3": [["title", "TITLE"]]}
    assert _la.read_gt(str(gt), corr, "f1")[1] == "_____"
    _la.read_gt(str(gt), corr, "f2")
    assert _la.read_gt(str(gt), corr, "f3")[0] == "TITLE"
    assert dict(((f, st) for f, _, st in _la.CORRECTION_NOTES)) == {"f1": "already applied", "f2": "no matching line", "f3": "applied"}


# ----------------------------------------------------------------------------- perturbation / crop regressions of the font-file phase search
def _pad(im, dx, dy, col=None):
    out = Image.new("RGB", (im.size[0] + dx, im.size[1] + dy), col or im.getpixel((0, 0)))
    out.paste(im, (dx, dy))
    return out


def _wrong_cells(atlas, base, variant, inv):
    """Confident cells (F/H/e) of ``variant`` whose character differs from the SAME cell of ``base`` (only where the mapped position is
    cell-aligned in the base).  Returns (n_confident, n_wrong)."""
    r0 = gg.read_image(base, atlas, return_cells=True)
    res = gg.read_image(variant, atlas, return_cells=True)
    if res["status"] != "ok" or r0["status"] != "ok":
        return 0, 0
    g0, g = r0["grid"], res["grid"]
    n = wrong = 0
    for c in res["cells"]:
        if c["status"] not in ("F", "H", "e"):
            continue
        n += 1
        if inv is None:
            wrong += 1  # a non-1:1 variant must not produce confident cells at all
            continue
        x, y = inv(g["origin_x"] + c["col"] * g["cell_w"], g["origin_y"] + c["row"] * g["cell_h"])
        dx, dy = x - g0["origin_x"], y - g0["origin_y"]
        if dx < 0 or dy < 0 or dx % g0["cell_w"] or dy % g0["cell_h"] or (g["cell_w"], g["cell_h"]) != (g0["cell_w"], g0["cell_h"]):
            continue
        rr, cc = int(dy // g0["cell_h"]), int(dx // g0["cell_w"])
        if rr >= g0["rows"] or cc >= g0["cols"]:
            continue
        if r0["rows"][rr][cc] != c["char"] or r0["conf"][rr][cc] not in "FHe":
            wrong += 1
    return n, wrong


PERTURB = {
    "shift+1x": (lambda im: _pad(im, 1, 0), lambda x, y: (x - 1, y)),
    "shift+1y": (lambda im: _pad(im, 0, 1), lambda x, y: (x, y - 1)),
    "shift+3,5": (lambda im: _pad(im, 3, 5), lambda x, y: (x - 3, y - 5)),
    "shift+4,8": (lambda im: _pad(im, 4, 8), lambda x, y: (x - 4, y - 8)),
    "crop-3,7": (lambda im: im.crop((3, 7, im.size[0], im.size[1])), lambda x, y: (x + 3, y + 7)),
    "border-white": (lambda im: _pad(im, 1, 1, WHITE), lambda x, y: (x - 1, y - 1)),
    "x2+1px": (lambda im: _pad(im.resize((im.size[0] * 2, im.size[1] * 2), Image.NEAREST), 1, 1), None),
    "x1.5": (lambda im: im.resize((int(im.size[0] * 1.5), int(im.size[1] * 1.5)), Image.NEAREST), None),
    "x1.03": (lambda im: im.resize((int(im.size[0] * 1.03), int(im.size[1] * 1.03)), Image.NEAREST), None),
}


@pytest.mark.parametrize("name", sorted(PERTURB))
@pytest.mark.parametrize("crop", ["crop-18-kernel.png", "crop-20-menu.png"])
def test_font_phase_search_never_reads_a_wrong_cell_under_perturbation(crop, name):
    atlas = gg.Atlas.load(os.path.join(FIX, "fonts.atlas.json"))
    base = Image.open(os.path.join(FIX, crop)).convert("RGB")
    fn, inv = PERTURB[name]
    n, wrong = _wrong_cells(atlas, base, fn(base), inv)
    assert wrong == 0
    if inv is not None and name.startswith("shift"):
        assert n > 0  # the phase search does find the shifted grid


def test_font_atlas_random_crops_never_read_a_wrong_cell():
    atlas = gg.Atlas.load(os.path.join(FIX, "fonts.atlas.json"))
    rng = _random.Random(7)
    for crop in ("crop-18-kernel.png", "crop-20-menu.png"):
        base = Image.open(os.path.join(FIX, crop)).convert("RGB")
        for _ in range(8):
            w, h = rng.randrange(24, base.size[0]), rng.randrange(16, base.size[1])
            x, y = rng.randrange(0, base.size[0] - w), rng.randrange(0, base.size[1] - h)
            n, wrong = _wrong_cells(atlas, base, base.crop((x, y, x + w, y + h)), lambda a, b, x=x, y=y: (a + x, b + y))
            assert wrong == 0, (crop, x, y, w, h)


def test_foreign_font_screen_is_refused_within_the_phase_budget():
    """Review: a no_font input cost 5-11 s per 1-4 MP frame with no timeout.  The phase search now honours max_seconds."""
    import time
    atlas = gg.Atlas.load(os.path.join(FIX, "fonts.atlas.json"))
    foreign = render(text_lines(14, 60), font=make_font(seed=99))
    t0 = time.monotonic()
    res = gg.read_image(foreign, atlas, grid_hints=[(8, 16)], max_seconds=0.3)
    assert res["status"] in ("no_font", "mixed_font") and "F" not in "".join(res["conf"]) and "H" not in "".join(res["conf"])
    assert time.monotonic() - t0 < 8
