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
            if st == "e":
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
    for phrase in ("The TrueNAS installation on vda succeeded!", "Please reboot and remove the installation media.", "Installation Succeeded"):
        assert len(gg.find_phrase(res, phrase)) == 1, phrase
    assert any(u["row"] == 3 for u in res["unknown_cells"])  # box drawing is never guessed
    # with the default evidence rule the '!' (seen on ONE screen only) is not exact
    strict = gg.read_image(os.path.join(FIX, "crop-14-dialog.png"), atlas, grid_hints=[(8, 16)])
    assert gg.find_phrase(strict, "The TrueNAS installation on vda succeeded!") == []
    assert len(gg.find_phrase(strict, "The TrueNAS installation on vda succeeded")) == 1


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
