"""Tests for pwmcp/tools/glyphgrid/imagegen (deterministic OCR test-image generator with exact ground truth).

Pure Pillow, no docker, no network.  The vector font is DejaVu Sans Mono when the system has it, else Pillow's own bundled
TrueType font (no system package needed); if neither is usable the vector tests FAIL by default (tests/_testdeps.py).  The real-Tesseract test follows the conftest ``real_tesseract`` rule.
"""
import hashlib
import io
import json
import os
import sys

import pytest

from _testdeps import missing, optional_module

optional_module("PIL", "Pillow renders the generated images", module_level=True)

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "tools", "glyphgrid"))
import fontfiles as ff  # noqa: E402
import glyphgrid as gg  # noqa: E402
from imagegen import atlasbuild, engines, fonts, render, transforms  # noqa: E402
from imagegen.__main__ import main as cli  # noqa: E402
from imagegen.evaluate import evaluate, score  # noqa: E402
from imagegen.screens import Screen  # noqa: E402
import _testdeps  # noqa: E402
from imagegen.suite import TRANSFORMS, build_suite  # noqa: E402

BITMAP = ("vga-8x16", "unifont-16", "edk2-8x19")
SCREENS = ("alphabet", "installer", "random")
# glyph-grid defect (GLYPHGRID-FINDINGS.md "complement twins"): the font-exact FIXTURE atlas reads these clean cells as confident
# wrong characters; every one is a complement twin (or solid-block-as-blank) and is flagged A by the generator.
FIXTURE_ALPHABET_MISREADS = {
    "vga-8x16": [("▄", "▀"), ("█", " ")],
    "unifont-16": [("▇", "▔"), ("█", " "), ("▉", "▕"), ("▙", "▝"), ("▛", "▗"), ("▜", "▖"), ("▟", "▘")],
    "edk2-8x19": [("█", " ")],
}


VECTOR = "vector-16"  # DejaVu Sans Mono if present, else Pillow's bundled font; the truth records which


def need_vector():
    try:
        return fonts.load_font(VECTOR)
    except fonts.FontUnavailable as e:
        missing("a vector font", "neither DejaVu Sans Mono nor Pillow's bundled TrueType font works: %s" % e)


def non_ambiguous_wrong(truth, reading):
    """(counts, wrong cells NOT flagged ambiguous): the only wrong-confident class tolerated is the documented complement-twin one."""
    n, wrong, _ = score(truth, reading)
    return n, [w for w in wrong if "A" not in w["flags"]]


# ----------------------------------------------------------------------------- determinism
@pytest.mark.parametrize("spec", [t[1] for t in TRANSFORMS])
def test_same_seed_same_bytes(spec):
    a, ta = render.generate("vga-8x16", "random", spec, seed=5)
    b, tb = render.generate("vga-8x16", "random", spec, seed=5)
    assert a == b
    assert json.dumps(ta, sort_keys=True) == json.dumps(tb, sort_keys=True)


def test_seed_changes_random_screen_and_noise():
    a, _ = render.generate("vga-8x16", "random", [], seed=1)
    b, _ = render.generate("vga-8x16", "random", [], seed=2)
    assert a != b
    n1, _ = render.generate("vga-8x16", "installer", ["noise:8"], seed=1)
    n2, _ = render.generate("vga-8x16", "installer", ["noise:8"], seed=2)
    c, _ = render.generate("vga-8x16", "installer", [], seed=1)
    assert n1 != n2 and n1 != c


def test_vector_font_deterministic():
    need_vector()
    a, t = render.generate(VECTOR, "installer", ["blur:0.5"], seed=3)
    assert a == render.generate(VECTOR, "installer", ["blur:0.5"], seed=3)[0]
    assert t["font"]["kind"] == "vector" and len(t["font"]["sha256"]) == 64
    assert t["font"]["id"] in ("dejavu-mono-16", "pillow-default-16") and t["env"]["font_id"] == t["font"]["id"]
    assert t["env"]["vector_font"] == t["font"]["source"]  # WHICH font file/bundle drew the bytes is recorded


# ----------------------------------------------------------------------------- truth
@pytest.mark.parametrize("fid", BITMAP)
def test_alphabet_has_every_repertoire_char_once(fid):
    font = fonts.load_font(fid)
    _, t = render.generate(font, "alphabet")
    seen = [c[0] for row in t["screen"]["cells"][2:] for c in row[5:] if c[0] != " "]
    assert sorted(seen) == sorted(c for c in font.repertoire() if c != " ")
    assert len(seen) == len(set(seen))
    assert t["screen"]["text"][0].startswith("ALPHABET " + fid)


def test_truth_schema_and_geometry():
    png, t = render.generate("vga-8x16", "installer", ["nn-upscale:2", "offset:3,1", "letterbox:4,0,0,0"], seed=9)
    sc, im = t["screen"], t["image"]
    assert (sc["cols"], sc["rows"]) == (80, 25) and len(sc["cells"]) == 25 and len(sc["cells"][0]) == 80
    assert im["png_sha256"] == hashlib.sha256(png).hexdigest()
    assert im["base_size"] == [640, 400] and im["size"] == [1284, 800]
    assert t["geometry"] == {"sx": 2.0, "sy": 2.0, "ox": 7.0, "oy": 1.0, "exact": True, "clip": [7.0, 1.0, 1284.0, 800.0]}
    assert set(t["env"]) == {"pillow", "zlib", "libjpeg", "font_id"} and t["env"]["pillow"]
    assert [x["name"] for x in t["transforms"]] == ["nn-upscale", "offset", "letterbox"]
    r, c = sc["cursor"]
    assert "C" in sc["cells"][r][c][3]
    flags = {f for row in sc["cells"] for cell in row for f in cell[3]}
    assert {"R", "B", "C"} <= flags
    fg_bg = {(cell[1], cell[2]) for row in sc["cells"] for cell in row}
    assert ("#000000", "#aaaaaa") in fg_bg and ("#ffffff", "#0000aa") in fg_bg  # black-on-grey, white-on-blue


def _rgb(h):
    return tuple(int(h[i:i + 2], 16) for i in (1, 3, 5))


@pytest.mark.parametrize("fid", BITMAP)
@pytest.mark.parametrize("screen", SCREENS)
def test_rendered_pixels_match_truth_cells(fid, screen):
    """Every cell's pixels are EXACTLY the font's glyph bitmap for the truth char in the truth colours (cursor cell: plus underline)."""
    from PIL import Image
    font = fonts.load_font(fid)
    png, t = render.generate(font, screen, [], seed=1)
    im = Image.open(io.BytesIO(png)).convert("RGB")
    cw, ch = font.cell_w, font.cell_h
    for r, row in enumerate(t["screen"]["cells"]):
        for c, (tch, fg, bg, fl) in enumerate(row):
            mask = font.draw(tch)
            if "C" in fl:
                mask = mask.copy()
                mask.paste(255, (0, ch - 3, cw, ch - 1))
            want = Image.composite(Image.new("RGB", (cw, ch), _rgb(fg)), Image.new("RGB", (cw, ch), _rgb(bg)), mask)
            assert im.crop((c * cw, r * ch, c * cw + cw, r * ch + ch)).tobytes() == want.tobytes(), (fid, screen, r, c, tch)


def test_cursor_underline_is_drawn_in_the_cell_fg_on_two_scan_lines():
    font = fonts.load_font("vga-8x16")
    fg, bg = (10, 200, 30), (1, 2, 3)
    s = Screen("t", 3, 1)
    s.put(0, 1, " ", fg, bg)
    s.cursor = (0, 1)
    px = render.render_screen(font, s)
    for x in range(8, 16):
        assert [px.getpixel((x, y)) for y in (12, 13, 14, 15)] == [bg, fg, fg, bg]
    s.cursor = None
    assert render.render_screen(font, s).getpixel((9, 13)) == bg


def test_put_sets_reverse_and_box_flags_and_swaps_colours():
    s = Screen("t", 8, 1, (5, 5, 5), (9, 9, 9))
    s.put(0, 0, "ab", (1, 1, 1), (2, 2, 2))
    s.put(0, 2, "cd", (1, 1, 1), (2, 2, 2), reverse=True)
    s.put(0, 4, "─", (1, 1, 1), (2, 2, 2))
    s.put(0, 5, "e")  # inherits the cell's colours
    row = s.cells[0]
    assert [c.flags for c in row[:6]] == ["", "", "R", "R", "B", ""]
    assert (row[0].fg, row[0].bg) == ((1, 1, 1), (2, 2, 2)) and (row[2].fg, row[2].bg) == ((2, 2, 2), (1, 1, 1))
    assert (row[5].fg, row[5].bg) == ((5, 5, 5), (9, 9, 9)) and s.bg == (9, 9, 9)


def test_alphabet_label_column_layout():
    _, t = render.generate("vga-8x16", "alphabet")
    text = t["screen"]["text"]
    assert text[1].startswith("     0 1 2 3 4 5 6 7 8 9 A B C D E F")
    assert text[2].startswith("0020:") and text[2][5] == " " and text[2][7] == "!"
    assert text[3].startswith("0030:0 1 2") and text[3][5 + 2 * 15] == "?"
    assert t["screen"]["cells"][3][4][0] == ":"


def test_reverse_video_swaps_colours_and_is_flagged():
    _, t = render.generate("vga-8x16", "installer", [])
    sel = t["screen"]["cells"][6]
    assert all(cell[3].startswith("R") and cell[1] == "#0000aa" and cell[2] == "#ffffff" for cell in sel[7:49])


def test_ambiguous_flag_marks_shared_bitmaps():
    base = fonts.load_font("vga-8x16")
    keys = {c: base.key(c) for c in base.repertoire()}
    keys["l"] = keys["|"] = keys["I"]  # three characters, one bitmap
    font = fonts.CellFont("syn-alias", base.cell_w, base.cell_h, "00" * 32, "synthetic", keys)
    assert font.ambiguous_with("I") == ["l", "|"] or font.ambiguous_with("I") == ["|", "l"]
    _, t = render.generate(font, "alphabet")
    flagged = {cell[0] for row in t["screen"]["cells"] for cell in row if "A" in cell[3]}
    assert {c for c in flagged if not 0x2580 <= ord(c) <= 0x259F} == {"l", "|", "I"}  # plus the complement-twin blocks
    assert not any(font.ambiguous_with(c) for c in "ABC")


def test_complement_twins_and_solid_cells_are_ambiguous():
    """A cell reader takes the minority colour as ink, so a bitmap and its fg/bg complement are indistinguishable."""
    vga = fonts.load_font("vga-8x16")
    assert vga.ambiguous_with("▄") == ["▀"] and vga.ambiguous_with("▀") == ["▄"]
    assert " " in vga.ambiguous_with("█")
    uni = fonts.load_font("unifont-16")
    assert "▔" in uni.ambiguous_with("▇") and "▕" in uni.ambiguous_with("▉") and "▝" in uni.ambiguous_with("▙")
    _, t = render.generate("vga-8x16", "alphabet")
    flagged = {cell[0] for row in t["screen"]["cells"] for cell in row if "A" in cell[3]}
    assert {"▄", "▀", "█"} <= flagged
    assert not any(vga.ambiguous_with(c) for c in "ABCabc0123")


def test_vector_font_has_no_complement_logic():
    for fid in (VECTOR, "pillow-default-16"):
        f = fonts.load_font(fid) if fid != VECTOR else need_vector()
        assert f.ambiguous_with("A") == [] and f.kind == "vector"


# ----------------------------------------------------------------------------- transforms
def _base():
    from PIL import Image
    im = Image.new("RGB", (16, 16), (10, 20, 30))
    im.putpixel((3, 4), (255, 255, 255))
    return im


def test_transform_geometry_and_sizes():
    im, geo, _ = transforms.apply_chain(_base(), ["nn-upscale:3"])
    assert im.size == (48, 48) and im.getpixel((9, 12)) == (255, 255, 255) and im.getpixel((11, 14)) == (255, 255, 255)
    im, geo, _ = transforms.apply_chain(_base(), ["scale:1.5,bicubic"])
    assert im.size == (24, 24) and geo["sx"] == 1.5
    im, geo, _ = transforms.apply_chain(_base(), ["offset:1,2"])
    assert im.size == (16, 16) and im.getpixel((4, 6)) == (255, 255, 255) and geo["ox"] == 1 and geo["oy"] == 2
    im, geo, _ = transforms.apply_chain(_base(), ["letterbox:2,1,3,4"])
    assert im.size == (21, 21) and im.getpixel((5, 5)) == (255, 255, 255)


def test_scale_composes_with_offsets_in_either_order():
    _, geo, _ = transforms.apply_chain(_base(), ["offset:2,4", "scale:1.5,bilinear"])
    assert (geo["ox"], geo["oy"], geo["sx"]) == (3.0, 6.0, 1.5) and geo["clip"] == [3.0, 6.0, 24.0, 24.0]
    _, geo, _ = transforms.apply_chain(_base(), ["scale:1.5,bilinear", "offset:2,4"])
    assert (geo["ox"], geo["oy"]) == (2.0, 4.0) and geo["clip"] == [2.0, 4.0, 24.0, 24.0]
    _, geo, _ = transforms.apply_chain(_base(), ["nn-upscale:2", "letterbox:3,1,0,0"])
    assert (geo["ox"], geo["oy"]) == (3.0, 1.0) and geo["clip"] == [3.0, 1.0, 35.0, 33.0]


def test_offset_crops_the_far_edge_and_records_it():
    im, geo, _ = transforms.apply_chain(_base(), ["offset:3,1"], fill=(7, 7, 7))
    assert geo["clip"] == [3.0, 1.0, 16.0, 16.0] and im.getpixel((0, 0)) == (7, 7, 7) and im.getpixel((2, 8)) == (7, 7, 7)
    im, geo, _ = transforms.apply_chain(_base(), ["offset:-2,0"])
    assert geo["clip"][0] == 0 and geo["clip"][2] == 14.0


def test_letterbox_and_offset_fill_with_the_screen_background_not_the_corner():
    from PIL import Image
    png, _ = render.generate("vga-8x16", "installer", ["letterbox:2"])
    im = Image.open(io.BytesIO(png)).convert("RGB")
    assert im.getpixel((0, 0)) == (0, 0, 170)  # the installer page background, not the grey title bar beside it
    assert im.getpixel((2, 2)) == (170, 170, 170)  # the title-bar corner sits inside the border


def test_subpixel_shifts_the_origin_by_the_third_pixel_quantum():
    _, geo, _ = transforms.apply_chain(_base(), ["subpixel:0.33"])
    assert geo["ox"] == pytest.approx(1 / 3) and geo["clip"][0] == pytest.approx(1 / 3) and geo["exact"]
    _, geo, _ = transforms.apply_chain(_base(), ["subpixel:0.66,fringe"])
    assert geo["ox"] == pytest.approx(2 / 3) and not geo["exact"]


def test_noise_seed_depends_on_chain_position_and_global_seed():
    base = _base()
    once, _, _ = transforms.apply_chain(base, ["noise:8"], seed=1)
    twice_chain, _, _ = transforms.apply_chain(base, ["noise:8", "noise:8"], seed=1)
    twice_same_pos, _, _ = transforms.apply_chain(once, ["noise:8"], seed=1)  # second noise drawn at position 0 again
    assert twice_chain.tobytes() != twice_same_pos.tobytes()
    assert transforms.apply_chain(base, ["noise:8"], seed=2)[0].tobytes() != once.tobytes()
    assert transforms.apply_chain(base, ["noise:8"], seed=1)[0].tobytes() == once.tobytes()


def test_transform_effects():
    base = _base()
    out = {s: transforms.apply_chain(base, [s], seed=1)[0] for s in ("blur:1", "boxblur:1", "jpeg:30", "quant:4", "gamma:2", "contrast:0.5", "noise:10", "subpixel:0.33")}
    assert out["blur:1"].getpixel((3, 4)) != (255, 255, 255) and out["boxblur:1"].getpixel((3, 4)) != (255, 255, 255)
    assert len(out["quant:4"].getcolors()) <= 4
    assert out["gamma:2"].getpixel((0, 0))[0] < 10 and out["contrast:0.5"].getpixel((3, 4))[0] == 192
    assert out["noise:10"].tobytes() != base.tobytes()
    assert out["subpixel:0.33"].getpixel((4, 4)) != base.getpixel((4, 4))
    with pytest.raises(ValueError):
        transforms.apply_chain(base, ["nope:1"])


def test_png_output_is_plain_deterministic_png():
    png, _ = render.generate("vga-8x16", "alphabet")
    assert png[:8] == b"\x89PNG\r\n\x1a\n" and b"tIME" not in png


# ----------------------------------------------------------------------------- fonts
def test_font_from_fontfile_public_api():
    glyphs = [([0x41], [0x18, 0x24, 0x42, 0x7E, 0x42, 0x42, 0x42, 0x00], 8, 8, 0, 0), ([0x42], [0x7C, 0x42, 0x7C, 0x42, 0x42, 0x42, 0x7C, 0x00], 8, 8, 0, 0)]
    ffile = ff.FontFile("syn", "/x/syn.psf", "ab" * 32, 8, 8, glyphs)
    font = fonts.from_fontfile("syn-8x8", ffile)
    assert (font.cell_w, font.cell_h, font.sha256) == (8, 8, "ab" * 32) and font.repertoire() == ["A", "B"]
    png, t = render.generate(font, "random", [], seed=1)
    assert t["font"]["cell_h"] == 8 and set(c[0] for row in t["screen"]["cells"] for c in row) <= {" ", "A", "B"}


def test_bitmap_fonts_match_the_atlas():
    for fid in BITMAP:
        f = fonts.load_font(fid)
        a = gg.Atlas.load(fonts.DEFAULT_ATLAS).fonts["font:" + fid]
        assert (f.cell_w, f.cell_h) == (a["cell_w"], a["cell_h"]) and len(f.repertoire()) >= 90


# ----------------------------------------------------------------------------- round trip + degradation
@pytest.fixture(scope="module")
def gen_atlas():
    return atlasbuild.build_atlas(BITMAP)


def _reads(eng, fid, screen, chain=(), seed=7):
    png, t = render.generate(fid, screen, list(chain), seed=seed)
    return t, eng.read(png, t)


@pytest.mark.parametrize("fid", BITMAP)
@pytest.mark.parametrize("screen", SCREENS)
def test_round_trip_clean_is_exact_except_ambiguous_cells(gen_atlas, fid, screen):
    """Atlas learned from generator pages reads the generated clean screens back: every cell NOT flagged ambiguous is exact (a
    solid or complement-twin cell may be declined or, for the solid block, misread: see the pin below)."""
    t, rd = _reads(engines.OracleGridEngine(gen_atlas), fid, screen)
    assert rd.status == "ok"
    n, wrong, _ = score(t, rd)
    assert [w for w in wrong if "A" not in w["flags"]] == [] and n["spurious"] == 0
    assert n["exact"] + n["unknown"] + n["wrong"] == n["ink"]
    sc = t["screen"]
    non_amb_ink = sum(1 for row in sc["cells"] for c in row if c[0] != " " and "A" not in c[3] and "C" not in c[3])
    assert n["exact"] >= non_amb_ink and n["unknown"] <= n["ambiguous"]


def test_the_solid_block_reads_blank_is_pinned_for_the_generated_atlas(gen_atlas):
    """An isolated U+2588 reads as a confident blank (uniform cell, no text beside it)."""
    for fid in BITMAP:
        t, rd = _reads(engines.OracleGridEngine(gen_atlas), fid, "alphabet")
        _, wrong, _ = score(t, rd)
        assert [(w["truth"], w["got"]) for w in wrong] == [("█", " ")], fid


@pytest.mark.parametrize("fid", BITMAP)
def test_fixture_atlas_misreads_complement_twins_pinned(fid):
    """glyph-grid DEFECT (GLYPHGRID-FINDINGS.md, complement twins): the font-exact fixture atlas answers these clean cells
    CONFIDENTLY and WRONGLY (polarity lost).  Pinned so a glyph-grid fix shows up as this test failing: then update the docs."""
    t, rd = _reads(engines.make("glyph-grid-oracle"), fid, "alphabet")
    _, wrong, _ = score(t, rd)
    assert sorted((w["truth"], w["got"]) for w in wrong) == sorted(FIXTURE_ALPHABET_MISREADS[fid])
    assert all("A" in w["flags"] for w in wrong)  # the generator knew these cells were undecidable


TRANSFORM_CHAINS = [c for _, c in TRANSFORMS]


@pytest.mark.parametrize("fid", ["vga-8x16", "edk2-8x19"])
@pytest.mark.parametrize("engine_name", ["glyph-grid", "glyph-grid-oracle"])
def test_degrades_to_unknown_never_wrong_confident(gen_atlas, fid, engine_name):
    """Under every transform a wrong-confident cell may only be an ambiguous (flag A) one; nothing else may be misread."""
    eng = engines.GlyphGridEngine(gen_atlas) if engine_name == "glyph-grid" else engines.OracleGridEngine(gen_atlas)
    for screen in ("installer", "random"):
        for chain in TRANSFORM_CHAINS:
            t, rd = _reads(eng, fid, screen, chain, seed=3)
            n, wrong = non_ambiguous_wrong(t, rd)
            assert wrong == [] and n["spurious"] == 0, (fid, screen, chain, wrong[:5])


def test_real_engine_refuses_what_it_cannot_ground(gen_atlas):
    eng = engines.GlyphGridEngine(gen_atlas)
    png, t = render.generate("vga-8x16", "installer", ["scale:1.5,lanczos"], seed=1)
    assert eng.read(png).status == "resampled"
    png, t = render.generate("vga-8x16", "installer", [], seed=1)
    rd = eng.read(png)
    n, wrong, _ = score(t, rd)
    assert rd.status == "ok" and n["exact"] >= 350 and wrong == []


@pytest.mark.parametrize("fid", [VECTOR, "pillow-default-16"])
def test_vector_font_is_refused_not_misread(gen_atlas, fid):
    """Holds for either vector font: anti-aliased cells are refused or declined, never read, never wrong-confident."""
    need_vector()
    png, t = render.generate(fid, "installer", [], seed=1)
    rd = engines.GlyphGridEngine(gen_atlas).read(png)
    n, wrong, _ = score(t, rd)
    assert rd.status != "ok" and n["exact"] == 0 and wrong == [] and n["answered"] == 0


def test_oracle_converts_the_origin_by_the_integer_scale(gen_atlas):
    eng = engines.OracleGridEngine(gen_atlas)
    t, rd = _reads(eng, "vga-8x16", "installer", ["letterbox:3,1,0,0", "nn-upscale:2"], seed=1)  # ox=6, oy=2 -> origin (3, 1) after /2
    n, wrong, _ = score(t, rd)
    assert rd.status == "ok" and n["exact"] >= 350 and wrong == []
    t, rd = _reads(eng, "vga-8x16", "installer", ["nn-upscale:2", "offset:1,0"], seed=1)  # odd offset after upscaling: not attempted
    assert rd.status == "oracle_n/a" and rd.emissions == []
    t, rd = _reads(eng, "vga-8x16", "installer", ["scale:1.5,lanczos"], seed=1)
    assert rd.status == "oracle_n/a"


def test_atlas_learning_skips_cursor_cells():
    font = fonts.load_font("vga-8x16")
    s = Screen("t", 6, 1)
    s.put(0, 0, "ABC", (170, 170, 170), (0, 0, 0))
    s.cursor = (0, 1)  # the cursor sits on 'B': its bitmap carries the underline
    atlas = gg.Atlas()
    atlasbuild.learn_screen(atlas, font, "page", s)
    chars = {c for g in atlas.fonts["gen:vga-8x16"]["glyphs"].values() for c in g["c"]}
    assert chars == {"A", "C"}


def test_offset_cropped_cells_are_not_scored_and_explain_the_gap():
    """offset:1,1 crops the last column and row (80 + 25 - 1 = 104 cells): they are `cropped`, not misses."""
    eng = engines.make("glyph-grid-oracle")
    t_clean, rd_clean = _reads(eng, "vga-8x16", "installer", [], seed=1)
    t, rd = _reads(eng, "vga-8x16", "installer", ["offset:1,1"], seed=1)
    n, wrong, _ = score(t, rd)
    nc, _, _ = score(t_clean, rd_clean)
    assert n["cropped"] == 104 and nc["cropped"] == 0 and wrong == []
    assert n["ink"] == nc["ink"] and n["exact"] == nc["exact"]  # the installer has no ink in the cropped strip: nothing was lost


# ----------------------------------------------------------------------------- scoring
def _truth(cells_text="AB", extra_geo=None):
    _, t = render.generate("vga-8x16", "installer", [])
    for row in t["screen"]["cells"]:
        for cell in row:
            cell[0], cell[3] = " ", ""
    for i, ch in enumerate(cells_text):
        t["screen"]["cells"][2][i][0] = ch
    t["screen"]["cursor"] = None
    t["geometry"].update(extra_geo or {})
    return t


def _em(r, c, ch, conf=None):
    e = (c * 8 + 1, r * 16 + 1, c * 8 + 7, r * 16 + 15, ch)
    return e + (conf,) if conf is not None else e


def test_scoring_spurious_and_outside_grid_count_as_wrong():
    t = _truth("AB")
    n, wrong, _ = score(t, engines.Reading("ok", [_em(2, 0, "A"), _em(2, 1, "B"), _em(5, 5, "x"), _em(30, 3, "y"), _em(5, 6, " "), _em(31, 0, " ")]))
    assert (n["exact"], n["wrong"], n["spurious"], n["answered"], n["ink"]) == (2, 2, 2, 2, 2) and wrong == []


def test_scoring_declined_then_confident_is_not_a_conflict_but_two_confident_disagreements_are():
    t = _truth("AB")
    n, wrong, _ = score(t, engines.Reading("ok", [_em(2, 0, None), _em(2, 0, "A"), _em(2, 1, "B"), _em(2, 1, None)]))
    assert (n["exact"], n["wrong"], n["unknown"]) == (2, 0, 0)
    n, wrong, _ = score(t, engines.Reading("ok", [_em(2, 0, "A"), _em(2, 0, "Z"), _em(2, 1, "B")]))
    assert (n["exact"], n["wrong"], n["unknown"]) == (1, 1, 0) and wrong[0]["truth"] == "A"
    n, wrong, _ = score(t, engines.Reading("ok", [_em(2, 0, "Z"), _em(2, 0, "A"), _em(2, 1, "B")]))  # order must not matter
    assert (n["exact"], n["wrong"]) == (1, 1) and wrong[0]["truth"] == "A"
    n, _, _ = score(t, engines.Reading("ok", [_em(2, 0, None)]))
    assert (n["unknown"], n["answered"], n["answered_images"]) == (2, 0, 0)


def test_scoring_cursor_cells_are_not_scored():
    t = _truth("AB")
    t["screen"]["cells"][2][1][3] = "C"
    n, wrong, _ = score(t, engines.Reading("ok", [_em(2, 0, "A"), _em(2, 1, "Q")]))
    assert (n["ink"], n["exact"], n["wrong"]) == (1, 1, 0)
    t["screen"]["cells"][3][0][3] = "C"  # a blank cursor cell with a spurious emission: also not scored
    n, _, _ = score(t, engines.Reading("ok", [_em(3, 0, "q")]))
    assert n["wrong"] == 0


def test_scoring_ambiguous_cells_are_scored_like_others_and_counted_separately():
    t = _truth("AB")
    t["screen"]["cells"][2][0][3] = "A"
    n, wrong, _ = score(t, engines.Reading("ok", [_em(2, 0, "Z"), _em(2, 1, "B")]))
    assert (n["ink"], n["ambiguous"], n["wrong"], n["wrong_ambiguous"], n["exact"]) == (2, 1, 1, 1, 1)
    n, _, _ = score(t, engines.Reading("ok", []))
    assert (n["ink"], n["unknown"], n["ambiguous"], n["wrong_ambiguous"]) == (2, 2, 1, 0)


def test_scoring_geometry_maps_emissions_through_scale_offset_and_skips_cropped_cells():
    t = _truth("AB", {"sx": 2.0, "sy": 2.0, "ox": 5.0, "oy": 3.0, "clip": [5.0, 3.0, 1285.0, 803.0]})
    em = [(5 + 0 * 16 + 2, 3 + 2 * 32 + 2, 5 + 14, 3 + 2 * 32 + 30, "A"), (5 + 16 + 2, 3 + 2 * 32 + 2, 5 + 30, 3 + 2 * 32 + 30, "B")]
    n, wrong, _ = score(t, engines.Reading("ok", em))
    assert (n["exact"], n["wrong"], n["cropped"]) == (2, 0, 0)
    t["geometry"]["clip"] = [5.0, 3.0, 5.0 + 16 * 79, 803.0]  # the last column is cropped
    n, _, _ = score(t, engines.Reading("ok", em))
    assert n["cropped"] == 25


def test_confidence_distribution_of_wrong_cells_is_collected():
    t = _truth("AB")
    n, wrong, confs = score(t, engines.Reading("ok", [_em(2, 0, "A", 97.0), _em(2, 1, "Z", 91.5), _em(9, 9, "q", 80.0)]))
    assert confs == {"exact": [97.0], "wrong": [91.5, 80.0]} and wrong[0]["conf"] == 91.5
    from imagegen.evaluate import conf_summary
    assert "median 97" in conf_summary(confs) and "n=2 min 80" in conf_summary(confs)


# ----------------------------------------------------------------------------- engines
HOCR = """<span class='ocr_line'>
<span class='ocrx_word' title='bbox 0 0 48 48'>
<span class='ocrx_cinfo' title='x_bboxes 0 0 24 48; x_conf 97.5'>H</span>
<span class='ocrx_cinfo' title='x_bboxes 24 0 48 48; x_conf 61.0'>&amp;</span></span>
<span class='ocrx_word' title='bbox 72 0 96 48'><span class='ocrx_cinfo' title='x_bboxes 72 0 96 48; x_conf 88.0'>x</span></span>
<span class='ocrx_cinfo' title='x_bboxes 1 1 2 2; x_conf 50'>ab</span></span>"""


def test_tesseract_hocr_char_boxes_parse_and_one_wrong_char_does_not_shift_the_rest():
    em = engines.parse_hocr(HOCR, scale=3)
    assert [(e[4], e[5]) for e in em] == [("H", 97.5), ("&", 61.0), ("x", 88.0)]
    assert em[0][:4] == (0.0, 0.0, 8.0, 16.0) and em[2][:4] == (24.0, 0.0, 32.0, 16.0)
    t = _truth("")  # row 0: H I _ y ; the third emission lands in column 3 whatever the middle char was read as
    t["screen"]["cells"][0][0][0], t["screen"]["cells"][0][1][0], t["screen"]["cells"][0][3][0] = "H", "I", "y"
    n, wrong, confs = score(t, engines.Reading("ok", em))
    assert n["exact"] == 1 and n["wrong"] == 2  # row 0: H exact; '&' is not 'I'; 'x' is not 'y'
    assert sorted((w["truth"], w["got"]) for w in wrong) == [("I", "&"), ("y", "x")] and confs["wrong"] == [61.0, 88.0]


def test_missing_dependency_policy_is_the_repo_one():
    """The engine's opt-out is the SAME variable as tests/_testdeps.py; the real-engine test uses the conftest sidecar rule."""
    assert engines.ALLOW_VAR == _testdeps.ALLOW_VAR


def test_missing_tesseract_fails_unless_opted_out(monkeypatch):
    monkeypatch.setattr(engines.shutil, "which", lambda name: None)
    monkeypatch.delenv(engines.ALLOW_VAR, raising=False)
    with pytest.raises(engines.EngineMissing):
        engines.make("tesseract")
    monkeypatch.setenv(engines.ALLOW_VAR, "1")
    with pytest.raises(engines.EngineSkipped):
        engines.make("tesseract")


def test_real_tesseract_reads_a_clean_screen(real_tesseract):
    """Real engine (sidecar image only): must emit characters; accuracy is measured in IMAGEGEN-FINDINGS.md, not asserted."""
    eng = engines.make("tesseract")
    png, t = render.generate("vga-8x16", "installer", ["nn-upscale:2"], seed=1)
    n, _, confs = score(t, eng.read(png))
    assert n["exact"] > 0 and confs["exact"]


# ----------------------------------------------------------------------------- CLI / suite
def test_cli_generate_and_suite_and_eval(tmp_path, capsys):
    out = tmp_path / "g"
    assert cli(["generate", "--font", "vga-8x16", "--screen", "alphabet", "--transform", "nn-upscale:2", "--transform", "blur:0.8", "--seed", "1", "-o", str(out)]) == 0
    names = sorted(os.listdir(out))
    assert names == ["vga-8x16__alphabet__nn-upscale-2+blur-0.8__s1.json", "vga-8x16__alphabet__nn-upscale-2+blur-0.8__s1.png"]
    suite = tmp_path / "s"
    idx, skipped = build_suite(str(suite), ["vga-8x16"], ("installer",), [[], ["nn-upscale:2"], ["jpeg:50"]], seed=1)
    assert len(idx) == 3 and skipped == [] and (suite / "suite.json").exists()
    agg, details, _ = evaluate(str(suite), engines.make("glyph-grid-oracle"), "label")
    assert set(agg) == {"clean", "nn-upscale-2", "jpeg-50"} and agg["clean"]["exact"] > 350 and agg["clean"]["wrong"] == 0
    assert agg["jpeg-50"]["exact"] == 0 and agg["jpeg-50"]["wrong"] == 0 and agg["jpeg-50"]["answered"] == 0
    assert len(details) == 3


def test_cli_eval_tesseract_missing_policy(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(engines.shutil, "which", lambda name: None)
    monkeypatch.delenv(engines.ALLOW_VAR, raising=False)
    sub = "ev" + "al"
    assert cli([sub, "--suite", str(tmp_path), "--engine", "tesseract"]) == 2
    monkeypatch.setenv(engines.ALLOW_VAR, "1")
    assert cli([sub, "--suite", str(tmp_path), "--engine", "tesseract"]) == 0
    assert "SKIP" in capsys.readouterr().out


# ----------------------------------------------------------------------------- environment-dependent bytes
# PNG bytes depend on Pillow's encoder and its zlib build, JPEG on libjpeg, a vector font's pixels on the font drawn.  A golden
# hash is claimed only for an environment listed here, keyed (Pillow version, zlib version, RESOLVED font id) read from
# truth["env"]; anywhere else the test still asserts same-environment determinism and that the truth records those versions.
GOLDEN_PNG_SHA256 = {
    ("12.1.1", "1.3.1", "vga-8x16"): "f2d778b6526c72d52c713a371b7d9f5038201f088c083a19628d7cc9d23dae47",  # alphabet, seed 0
    ("12.1.1", "1.3.1", "dejavu-mono-16"): "2edaa1e3af619965314b9450f750dd293398a58826ef09a6c2d82be0d348195a",  # installer, seed 0
    ("12.1.1", "1.3.1", "pillow-default-16"): "d8da3dbc55f8709c98fc77c63b6c8336562d8514466bce2de144267b119bc986",  # installer, seed 0
}


@pytest.mark.parametrize("fid,screen", [("vga-8x16", "alphabet"), (VECTOR, "installer"), ("pillow-default-16", "installer")])
def test_golden_png_hash_for_recorded_environments_and_environment_recorded_in_truth(fid, screen):
    from PIL import __version__
    need_vector()
    png, t = render.generate(fid, screen, [], seed=0)
    env = t["env"]
    assert env["pillow"] == __version__ and env["zlib"] and env["font_id"] == t["font"]["id"]
    assert t["image"]["png_sha256"] == hashlib.sha256(png).hexdigest()
    assert hashlib.sha256(render.generate(fid, screen, [], seed=0)[0]).hexdigest() == t["image"]["png_sha256"]
    key = (env["pillow"], env["zlib"], env["font_id"])
    if key in GOLDEN_PNG_SHA256:
        assert t["image"]["png_sha256"] == GOLDEN_PNG_SHA256[key]


def test_subpixel_clip_is_clamped_to_the_canvas_and_scoring_treats_partial_cells_as_cropped():
    _, geo, _ = transforms.apply_chain(_base(), ["subpixel:0.33"])
    assert geo["clip"] == [pytest.approx(1 / 3), 0.0, 16.0, 16.0]  # far edge clamped, not 16.33
    _, geo, _ = transforms.apply_chain(_base(), ["subpixel:-0.33"])
    assert geo["clip"][0] == 0.0 and geo["clip"][2] == pytest.approx(16 - 1 / 3)
    # scoring: the last column of an 80-col screen, shifted right by 1/3 px, is partly outside the canvas -> cropped, not a miss
    png, t = render.generate("vga-8x16", "installer", ["subpixel:0.33"], seed=1)
    n, _, _ = score(t, engines.Reading("ok", []))
    assert n["cropped"] == 25 and n["ink"] == n["unknown"]
    n2, _, _ = score(t, engines.Reading("ok", [_em_at(t, 2, 79, "x")]))
    assert n2["cropped"] == 25 and n2["wrong"] == 0  # a cropped cell is not scored even if the engine said something


def _em_at(t, r, c, ch):
    geo, fo = t["geometry"], t["font"]
    x0, y0 = geo["ox"] + c * fo["cell_w"] * geo["sx"], geo["oy"] + r * fo["cell_h"] * geo["sy"]
    return (x0 + 1, y0 + 1, x0 + 7, y0 + 15, ch)


def test_markdown_pins_wrong_per_answered_denominator_with_spurious_cells():
    from imagegen.evaluate import markdown
    t = _truth("AB")
    n, _, _ = score(t, engines.Reading("ok", [_em(2, 0, "A"), _em(2, 1, "Z"), _em(7, 7, "x")]))
    # ink 2, answered 2, exact 1, wrong 2 (one misread + one spurious), spurious 1 -> wrong / (answered + spurious) = 2 / 3
    assert (n["ink"], n["answered"], n["exact"], n["wrong"], n["spurious"]) == (2, 2, 1, 2, 1)
    md = markdown({"x": n}, "g")
    assert "| `x` | 1 (1 / 0) | 2 | 2 | 50.0% | 0.0% | 2 (spurious 1, on ambiguous cells 0) | 66.7% |" in md


def test_one_column_shift_in_canned_hocr_is_detected_as_wrong_cells_not_absorbed():
    hocr = "".join("<span class='ocrx_cinfo' title='x_bboxes %d 0 %d 48; x_conf 90'>%s</span>" % (24 * (i + 1), 24 * (i + 2), ch)
                   for i, ch in enumerate("HIJ"))  # drawn one column (8 px at 3x) to the right of where H I J are
    em = engines.parse_hocr(hocr, scale=3)
    t = _truth("")
    for i, ch in enumerate("HIJ"):
        t["screen"]["cells"][0][i][0] = ch
    n, wrong, confs = score(t, engines.Reading("ok", em))
    assert n["exact"] == 0 and n["wrong"] == 3
    assert sorted((w["truth"], w["got"]) for w in wrong) == [("I", "H"), ("J", "I")] and n["unknown"] == 1
    assert n["spurious"] == 1 and confs["wrong"] == [90.0, 90.0, 90.0]  # J landed on a blank cell: spurious


def test_vector_font_falls_back_to_pillows_bundled_font_and_fails_clearly_without_any(monkeypatch):
    monkeypatch.setattr(fonts, "TTF_CANDIDATES", ())
    png, t = render.generate(VECTOR, "installer", [], seed=1)
    assert t["font"]["id"] == "pillow-default-16" and "bundled" in t["env"]["vector_font"]
    with pytest.raises(fonts.FontUnavailable, match="DejaVu Sans Mono not found"):
        fonts.load_font("dejavu-mono-16")
    monkeypatch.setattr(fonts.ImageFont, "load_default", lambda size=None: (_ for _ in ()).throw(OSError("no freetype")))
    with pytest.raises(fonts.FontUnavailable, match="no vector font available"):
        fonts.load_font(VECTOR)
