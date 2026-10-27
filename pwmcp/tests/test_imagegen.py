"""Tests for pwmcp/tools/glyphgrid/imagegen (deterministic OCR test-image generator with exact ground truth).

Pure Pillow, no docker, no network.  DejaVu Sans Mono (Debian fonts-dejavu-core) is an optional dependency and a missing
one FAILS by default (tests/_testdeps.py).  The real-Tesseract test follows the conftest ``real_tesseract`` rule.
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
from imagegen.suite import TRANSFORMS, build_suite  # noqa: E402

BITMAP = ("vga-8x16", "unifont-16", "edk2-8x19")
SCREENS = ("alphabet", "installer", "random")
BLOCKS = {chr(c) for c in range(0x2580, 0x25A0)}  # sub-cell block elements: >= half ink / uniform cells, see IMAGEGEN-FINDINGS.md


def need_dejavu():
    if fonts.find_ttf() is None:
        missing("DejaVu Sans Mono", "install fonts-dejavu-core (the vector-font cases render with it)")


def ink_wrong(truth, reading, ignore=()):
    n, wrong = score(truth, reading)
    return n, [w for w in wrong if w[2] not in ignore]


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
    need_dejavu()
    a, t = render.generate("dejavu-mono-16", "installer", ["blur:0.5"], seed=3)
    assert a == render.generate("dejavu-mono-16", "installer", ["blur:0.5"], seed=3)[0]
    assert t["font"]["kind"] == "vector" and len(t["font"]["sha256"]) == 64


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
    assert t["geometry"] == {"sx": 2.0, "sy": 2.0, "ox": 7.0, "oy": 1.0, "exact": True}
    assert [x["name"] for x in t["transforms"]] == ["nn-upscale", "offset", "letterbox"]
    r, c = sc["cursor"]
    assert "C" in sc["cells"][r][c][3]
    flags = {f for row in sc["cells"] for cell in row for f in cell[3]}
    assert {"R", "B", "C"} <= flags
    fg_bg = {(cell[1], cell[2]) for row in sc["cells"] for cell in row}
    assert ("#000000", "#aaaaaa") in fg_bg and ("#ffffff", "#0000aa") in fg_bg  # black-on-grey, white-on-blue


def test_rendered_pixels_match_truth_cells():
    from PIL import Image
    png, t = render.generate("vga-8x16", "installer", [], seed=1)
    im = Image.open(io.BytesIO(png)).convert("RGB")
    cw, ch = t["font"]["cell_w"], t["font"]["cell_h"]
    for r, row in enumerate(t["screen"]["cells"]):
        for c, (_, fg, bg, fl) in enumerate(row):
            cols = {px for _, px in (im.crop((c * cw, r * ch, c * cw + cw, r * ch + ch)).getcolors(cw * ch))}
            allowed = {tuple(int(h[i:i + 2], 16) for i in (1, 3, 5)) for h in (fg, bg)}
            assert cols <= allowed


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
    assert flagged == {"l", "|", "I"}
    assert not any(font.ambiguous_with(c) for c in "ABC")


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


@pytest.mark.parametrize("fid", BITMAP)
@pytest.mark.parametrize("screen", SCREENS)
def test_round_trip_clean_is_exact(gen_atlas, fid, screen):
    """Atlas learned from generator pages reads the generated clean screens back: every non-block ink cell exact, none wrong.

    Excluded and documented: block elements (>= half-ink cells have no unique fg/bg bitmap; solid cells are fill), see findings."""
    png, t = render.generate(fid, screen, [], seed=7)
    eng = engines.OracleGridEngine(gen_atlas)
    rd = eng.read(png, t)
    assert rd.status == "ok"
    n, wrong = ink_wrong(t, rd, ignore=BLOCKS)
    assert wrong == [] and n["spurious"] == 0
    got = {}
    ch, cw = t["font"]["cell_h"], t["font"]["cell_w"]
    for x0, y0, _, _, o in rd.emissions:
        got[(int(y0 // ch), int(x0 // cw))] = o
    misses = [(r, c, cell[0]) for r, row in enumerate(t["screen"]["cells"]) for c, cell in enumerate(row)
              if cell[0] != " " and cell[0] not in BLOCKS and "C" not in cell[3] and got.get((r, c)) != cell[0]]
    assert misses == []


def test_the_solid_block_reads_blank_is_pinned_as_a_known_finding(gen_atlas):
    """glyph-grid reads an isolated U+2588 as a confident blank (uniform cell, no text beside it): the one wrong-confident
    on clean screens.  If this starts failing, glyph-grid fixed it: update IMAGEGEN-FINDINGS.md."""
    png, t = render.generate("vga-8x16", "alphabet", [], seed=1)
    rd = engines.OracleGridEngine(gen_atlas).read(png, t)
    n, wrong = score(t, rd)
    assert [w[2:] for w in wrong] == [("█", " ")]


TRANSFORM_CHAINS = [c for _, c in TRANSFORMS]


@pytest.mark.parametrize("fid", ["vga-8x16", "edk2-8x19"])
@pytest.mark.parametrize("engine_name", ["glyph-grid", "glyph-grid-oracle"])
def test_degrades_to_unknown_never_wrong_confident(gen_atlas, fid, engine_name):
    """Under every transform the engines either read exactly or decline; a wrong confident char is a failure."""
    eng = engines.GlyphGridEngine(gen_atlas) if engine_name == "glyph-grid" else engines.OracleGridEngine(gen_atlas)
    for screen in ("installer", "random"):
        for chain in TRANSFORM_CHAINS:
            png, t = render.generate(fid, screen, chain, seed=3)
            n, wrong = ink_wrong(t, eng.read(png, t), ignore=BLOCKS)
            assert wrong == [] and n["spurious"] == 0, (fid, screen, chain, wrong[:5])


def test_real_engine_refuses_what_it_cannot_ground(gen_atlas):
    eng = engines.GlyphGridEngine(gen_atlas)
    png, t = render.generate("vga-8x16", "installer", ["scale:1.5,lanczos"], seed=1)
    assert eng.read(png).status == "resampled"
    png, t = render.generate("vga-8x16", "installer", [], seed=1)
    rd = eng.read(png)
    n, wrong = score(t, rd)
    assert rd.status == "ok" and n["exact"] >= 350 and wrong == []


def test_vector_font_is_refused_not_misread(gen_atlas):
    need_dejavu()
    png, t = render.generate("dejavu-mono-16", "installer", [], seed=1)
    rd = engines.GlyphGridEngine(gen_atlas).read(png)
    n, wrong = score(t, rd)
    assert rd.status != "ok" and n["exact"] == 0 and wrong == []


# ----------------------------------------------------------------------------- engines
def test_tesseract_tsv_parsing_and_scoring():
    head = "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext"
    tsv = head + "\n5\t1\t1\t1\t1\t1\t0\t0\t16\t16\t90\tHI\n5\t1\t1\t1\t1\t2\t24\t0\t8\t16\t95\tx\n4\t1\t1\t1\t1\t0\t0\t0\t8\t8\t-1\t\n"
    em = engines.parse_tsv(tsv)
    assert [e[4] for e in em] == ["H", "I", "x"]
    _, t = render.generate("vga-8x16", "installer", [])
    t["screen"]["cells"][0][0][0], t["screen"]["cells"][0][1][0], t["screen"]["cells"][0][3][0] = "H", "I", "y"
    n, wrong = score(t, engines.Reading("ok", em))
    assert n["exact"] == 2 and n["wrong"] == 1 and wrong == [(0, 3, "y", "x")]


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
    n, _ = score(t, eng.read(png))
    assert n["exact"] > 0


# ----------------------------------------------------------------------------- CLI / suite
def test_cli_generate_and_suite_and_eval(tmp_path, capsys):
    out = tmp_path / "g"
    assert cli(["generate", "--font", "vga-8x16", "--screen", "alphabet", "--transform", "nn-upscale:2", "--transform", "blur:0.8", "--seed", "1", "-o", str(out)]) == 0
    names = sorted(os.listdir(out))
    assert names == ["vga-8x16__alphabet__nn-upscale-2+blur-0.8__s1.json", "vga-8x16__alphabet__nn-upscale-2+blur-0.8__s1.png"]
    suite = tmp_path / "s"
    idx, skipped = build_suite(str(suite), ["vga-8x16"], ("installer",), [[], ["nn-upscale:2"], ["jpeg:50"]], seed=1)
    assert len(idx) == 3 and skipped == [] and (suite / "suite.json").exists()
    agg, details = evaluate(str(suite), engines.make("glyph-grid-oracle"), "label")
    assert set(agg) == {"clean", "nn-upscale-2", "jpeg-50"} and agg["clean"]["exact"] > 350 and agg["clean"]["wrong"] == 0
    assert agg["jpeg-50"]["exact"] == 0 and agg["jpeg-50"]["wrong"] == 0
    assert len(details) == 3


def test_cli_eval_tesseract_missing_policy(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(engines.shutil, "which", lambda name: None)
    monkeypatch.delenv(engines.ALLOW_VAR, raising=False)
    sub = "ev" + "al"
    assert cli([sub, "--suite", str(tmp_path), "--engine", "tesseract"]) == 2
    monkeypatch.setenv(engines.ALLOW_VAR, "1")
    assert cli([sub, "--suite", str(tmp_path), "--engine", "tesseract"]) == 0
    assert "SKIP" in capsys.readouterr().out
