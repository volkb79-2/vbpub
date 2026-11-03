"""Tests for the console-font extensions of pwmcp/tools/glyphgrid: PSF1/PSF2 parsing (unicode tables, sequences, wide cells), the kernel's
16x32 Terminus table, and the manifest builder (sha256 check, ``same_as`` relations, tiers).  All fixture fonts are generated here; no
downloaded ISO or package is needed."""
import gzip
import hashlib
import json
import os
import struct
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "tools", "glyphgrid"))
import build_font_atlas as bfa  # noqa: E402
import fontfiles as ff  # noqa: E402
import glyphgrid as gg  # noqa: E402

FIX = os.path.join(HERE, "fixtures", "glyphgrid")


def _psf1_bytes(glyph_rows, h=8, mode=0, table=None):
    n = 512 if mode & 1 else 256
    body = b"".join(bytes(glyph_rows.get(i, [0] * h)) for i in range(n))
    return b"\x36\x04" + bytes([mode, h]) + body + (table or b"")


def _u16(*vals):
    return b"".join(struct.pack("<H", v) for v in vals)


def test_psf1_unicode_table_ends_records_at_ffff_and_drops_sequences(tmp_path):
    # glyph 0 = U+0041 and U+00C0; glyph 1 = U+0042 then a combining SEQUENCE (0xFFFE, base U+0043, mark U+0301) that is NOT a code point of glyph 1;
    # glyph 2 has an empty record; the remaining 253 records are missing (truncated table) and must come out empty, not crash.
    table = _u16(0x41, 0xC0, 0xFFFF) + _u16(0x42, 0xFFFE, 0x43, 0x301, 0xFFFF) + _u16(0xFFFF)
    p = tmp_path / "t.psf"
    p.write_bytes(_psf1_bytes({0: [0xFF] * 8}, mode=2, table=table))
    font = ff.load_psf(str(p))
    assert [g[0] for g in font.glyphs[:4]] == [[0x41, 0xC0], [0x42], [], []]
    assert len(font.glyphs) == 256 and (font.cell_w, font.cell_h) == (8, 8)


def test_psf1_512_glyph_mode_and_no_table_maps_index_to_codepoint(tmp_path):
    p = tmp_path / "t.psf"
    p.write_bytes(_psf1_bytes({300: [0x80] + [0] * 7}, mode=1))
    font = ff.load_psf(str(p))
    assert len(font.glyphs) == 512 and font.glyphs[300][0] == [300] and font.glyphs[300][1][0] == 0x80


def _psf2_bytes(w, h, glyphs, unicode_table=True):
    """glyphs: [(rows[int], 'utf-8 text of the table record')] ; rows are w-bit ints, MSB = left, padded to whole bytes per row."""
    rb = (w + 7) // 8
    bpg = rb * h
    head = struct.pack("<4s7I", b"\x72\xb5\x4a\x86", 0, 32, 1 if unicode_table else 0, len(glyphs), bpg, h, w)
    body = b"".join(b"".join((r << (rb * 8 - w)).to_bytes(rb, "big") for r in rows) for rows, _ in glyphs)
    table = b"".join((rec if isinstance(rec, bytes) else rec.encode("utf-8")) + b"\xff" for _, rec in glyphs) if unicode_table else b""
    return head + body + table


def test_psf2_wide_cell_unicode_table_and_sequences(tmp_path):
    wide = [0x8001, 0x4002, 0x2004] + [0] * 5          # 16 wide: two bytes per row
    p = tmp_path / "t.psf.gz"
    # record 1: 'A' and U+0410 (alias); then 0xFE starts a sequence 'B'+U+0301 that must be ignored; record 2: single 'C'
    p.write_bytes(gzip.compress(_psf2_bytes(16, 8, [(wide, "AА\xfe" + "B́"), (wide[::-1], "C")]).replace(b"\xc3\xbe", b"\xfe")))
    font = ff.load_psf(str(p))
    assert (font.cell_w, font.cell_h) == (16, 8) and len(font.glyphs) == 2
    assert font.glyphs[0][0] == [0x41, 0x410] and font.glyphs[1][0] == [0x43]
    assert font.glyphs[0][1][:3] == [0x8001, 0x4002, 0x2004]
    cell = ff.render_cell(font, font.glyphs[0])
    assert cell[:2] == b"\x80\x01" and len(cell) == 2 * 8      # 16-wide cell = 2 bytes per row, MSB left


def test_psf2_without_table_and_bad_magic(tmp_path):
    p = tmp_path / "t.psf"
    p.write_bytes(_psf2_bytes(8, 4, [([0xF0, 0, 0, 0xF0], ""), ([1, 2, 3, 4], "")], unicode_table=False))
    assert [g[0] for g in ff.load_psf(str(p)).glyphs] == [[0], [1]]
    q = tmp_path / "bad.psf"
    q.write_bytes(b"NOTAPSF" * 4)
    with pytest.raises(ValueError):
        ff.load_psf(str(q))


def test_raw_table_16_wide_rows_are_two_big_endian_bytes():
    rows = [0x8001, 0x4002] + [0] * 30
    glyph = b"".join(r.to_bytes(2, "big") for r in rows)
    blob = b"\xAA" * 5 + glyph + bytes(64)
    font = ff.load_raw_table(blob, 5, n=2, w=16, h=32, unimap=[(0x41,), (0x42,)])
    assert font.glyphs[0][1][:2] == [0x8001, 0x4002] and font.glyphs[1][1] == [0] * 32 and (font.cell_w, font.cell_h) == (16, 32)
    assert ff.render_cell(font, font.glyphs[0])[:4] == b"\x80\x01\x40\x02"


def test_find_ter16x32_table_needs_both_probe_glyphs():
    base = 4096
    table = bytearray(256 * 64)
    table[0x41 * 64:0x42 * 64] = ff.TER_BOLD_A
    table[0x30 * 64:0x31 * 64] = ff.TER_BOLD_0
    blob = b"\x00" * base + bytes(table) + b"\x00" * 100
    assert ff.find_ter16x32_table(blob) == base
    only_a = bytearray(blob)
    only_a[base + 0x30 * 64:base + 0x31 * 64] = bytes(64)
    with pytest.raises(ValueError):
        ff.find_ter16x32_table(bytes(only_a))


# ----------------------------------------------------------------------------- manifest builder
def _write_psf(path, glyph_rows):
    path.write_bytes(gzip.compress(_psf1_bytes(glyph_rows, h=16, mode=2, table=b"".join(_u16(0x41 + i, 0xFFFF) for i in range(256)))))


A = [0, 0, 0x18, 0x24, 0x42, 0x7E, 0x42, 0x42, 0x42, 0x42, 0, 0, 0, 0, 0, 0]
B = [0, 0, 0x7C, 0x42, 0x7C, 0x42, 0x42, 0x7C, 0, 0, 0, 0, 0, 0, 0, 0]
C = [0, 0, 0x3C, 0x42, 0x40, 0x40, 0x42, 0x3C, 0, 0, 0, 0, 0, 0, 0, 0]


def _manifest(tmp_path, entries):
    p = tmp_path / "m.json"
    p.write_text(json.dumps({"entries": entries}))
    return str(p)


def _entry(root, rel, name, **extra):
    return dict({"atlas_font": name, "kind": "psf", "file": rel, "sha256": hashlib.sha256((root / rel).read_bytes()).hexdigest(),
                 "repertoire": "ascii", "origin": {"package": "fixture", "version": "1"}}, **extra)


def test_manifest_builds_fonts_checks_sha256_and_merges_identical_sources(tmp_path):
    _write_psf(tmp_path / "one.psf.gz", {0: A, 1: B})
    _write_psf(tmp_path / "again.psf.gz", {0: A, 1: B})            # same bitmaps from a second place -> origin.also
    _write_psf(tmp_path / "other.psf.gz", {0: A, 1: C})            # same name, different bitmaps -> refused
    atlas = gg.Atlas()
    rep = bfa.build_manifest(_manifest(tmp_path, [_entry(tmp_path, "one.psf.gz", "font:fx"), _entry(tmp_path, "again.psf.gz", "font:fx")]), str(tmp_path), atlas)
    f = atlas.fonts["font:fx"]
    assert f["origin"]["sha256"] == hashlib.sha256((tmp_path / "one.psf.gz").read_bytes()).hexdigest() and f["origin"]["provenance"]["package"] == "fixture"
    assert f["origin"]["also"][0]["sha256"] == hashlib.sha256((tmp_path / "again.psf.gz").read_bytes()).hexdigest()
    assert rep["entries"][1]["merged_into"] == "font:fx"
    with pytest.raises(ValueError, match="differ"):
        bfa.build_manifest(_manifest(tmp_path, [_entry(tmp_path, "one.psf.gz", "font:fx"), _entry(tmp_path, "other.psf.gz", "font:fx")]), str(tmp_path), gg.Atlas())
    bad = _entry(tmp_path, "one.psf.gz", "font:fx")
    bad["sha256"] = "0" * 64
    with pytest.raises(ValueError, match="sha256"):
        bfa.build_manifest(_manifest(tmp_path, [bad]), str(tmp_path), gg.Atlas())


def test_manifest_same_as_reports_identical_and_subset_never_hides_a_difference(tmp_path):
    _write_psf(tmp_path / "ref.psf.gz", {0: A, 1: B, 2: C})
    _write_psf(tmp_path / "same.psf.gz", {0: A, 1: B, 2: C})
    _write_psf(tmp_path / "sub.psf.gz", {0: A, 1: B})
    _write_psf(tmp_path / "diff.psf.gz", {0: A, 1: B, 2: [0, 0, 0xFF] + [0] * 13})
    base = gg.Atlas()
    ff.build_font_atlas(base, "font:ref", ff.load_psf(str(tmp_path / "ref.psf.gz")), "ascii")
    ents = [_entry(tmp_path, n + ".psf.gz", "font:" + n, same_as="font:ref") for n in ("same", "sub", "diff")]
    rep = {r["atlas_font"]: r for r in bfa.build_manifest(_manifest(tmp_path, ents), str(tmp_path), gg.Atlas(), base)["entries"]}
    assert rep["font:same"]["identical"] is True and rep["font:same"]["only_here"] == 0
    assert rep["font:sub"]["identical"] is False and rep["font:sub"]["subset_of_reference"] is True and rep["font:sub"]["only_reference"] == 1
    assert rep["font:diff"]["identical"] is False and rep["font:diff"]["subset_of_reference"] is False and rep["font:diff"]["only_here"] == 1
    atlas = gg.Atlas()
    bfa.build_manifest(_manifest(tmp_path, ents), str(tmp_path), atlas, base)
    assert not atlas.fonts                      # same_as entries are verified, never added


def test_manifest_tiers_and_same_size_collision_stays_reported(tmp_path):
    _write_psf(tmp_path / "x.psf.gz", {0: A, 1: B})
    _write_psf(tmp_path / "y.psf.gz", {0: A, 1: C})            # shares the bitmap of 'A' (glyph 0) with x -> a cross-font collision
    ents = [_entry(tmp_path, "x.psf.gz", "font:x"), _entry(tmp_path, "y.psf.gz", "font:y", tier="ambiguous")]
    m = _manifest(tmp_path, ents)
    extra, amb = gg.Atlas(), gg.Atlas()
    bfa.build_manifest(m, str(tmp_path), extra)
    rep = bfa.build_manifest(m, str(tmp_path), amb, tier="ambiguous")
    assert list(extra.fonts) == ["font:x"] and list(amb.fonts) == ["font:y"]
    both = gg.Atlas()
    rep_all = bfa.build_manifest(m, str(tmp_path), both, tier="all")
    assert rep_all["entries"][1]["analysis"]["native"][0] == {"font": "font:x", "shared": 1, "differing_chars": 0, "fraction_of_smaller": 0.5}
    assert rep["entries"][0]["atlas_font"] == "font:y"
    merged = gg.Atlas.merge(extra, amb)
    key = bytes.fromhex(next(k for k, g in merged.fonts["font:x"]["glyphs"].items() if "A" in g["c"]))
    # the two fonts draw the same bitmap with the same character: not an alias; a bitmap with a different character in the other font would be
    assert merged.cross_font_chars("font:x", key) == []


def test_committed_extra_atlases_load_and_carry_provenance():
    """The shipped atlases validate (sha256 provenance on every font_exact glyph) and never reuse a base font name."""
    base = gg.Atlas.load(os.path.join(FIX, "fonts.atlas.json"))
    for name in ("fonts.console-extra.atlas.json", "fonts.console-ambiguous.atlas.json"):
        a = gg.Atlas.load(os.path.join(FIX, name))
        assert not set(a.fonts) & set(base.fonts)
        for fn, f in a.fonts.items():
            assert f["origin"]["kind"] == "font-file" and len(f["origin"]["sha256"]) == 64 and f["origin"]["provenance"].get("licence"), fn
        gg.Atlas.merge(base, a)                  # no name clash


@pytest.mark.parametrize("crop", ["crop-14-dialog.png", "crop-18-kernel.png", "crop-20-menu.png", "crop-20-title.png"])
def test_extra_tier_does_not_change_any_base_read(crop):
    """Merging the console-extra atlas into the base atlas must not turn one base read into an alias set or another font (TrueNAS crops)."""
    base = gg.Atlas.load(os.path.join(FIX, "fonts.atlas.json"))
    merged = gg.Atlas.merge(base, gg.Atlas.load(os.path.join(FIX, "fonts.console-extra.atlas.json")))
    a = gg.read_image(os.path.join(FIX, crop), base, grid_hints=[(8, 16)])
    b = gg.read_image(os.path.join(FIX, crop), merged, grid_hints=[(8, 16)])
    # font_proven may legitimately drop on a small crop (font:terminus-bold-8x16 explains 36% of the kernel-log crop, above PROVEN_RUNNER_UP):
    # that matters only where a bitmap is read differently by the runner-up, and then it shows as an alias cell, which this equality excludes
    assert (a["font"], a["rows"], a["conf"]) == (b["font"], b["rows"], b["conf"])


def test_ambiguous_tier_is_reported_as_alias_not_hidden():
    """font:fixed-8x16 draws every Unifont bitmap of the repertoire: merged with the base atlas a Unifont screen is no longer font-proven and
    the machinery reports the collision (alias cells) instead of picking a font."""
    base = gg.Atlas.load(os.path.join(FIX, "fonts.atlas.json"))
    merged = gg.Atlas.merge(base, gg.Atlas.load(os.path.join(FIX, "fonts.console-ambiguous.atlas.json")))
    res = gg.read_image(os.path.join(FIX, "crop-20-title.png"), merged, grid_hints=[(8, 16)])
    assert res["font_proven"] is False and "a" in "".join(res["conf"])


def test_committed_manifest_matches_committed_atlases():
    man = json.load(open(os.path.join(HERE, "..", "tools", "glyphgrid", "fonts-console-extra.manifest.json")))
    built = set()
    for name in ("fonts.console-extra.atlas.json", "fonts.console-ambiguous.atlas.json"):
        built |= set(gg.Atlas.load(os.path.join(FIX, name)).fonts)
    assert {e["atlas_font"] for e in man["entries"] if not e.get("same_as")} == built
    assert all(len(e["sha256"]) == 64 for e in man["entries"])


# ----------------------------------------------------------------------------- review follow-up: scale hazards, rival rule, mappings, PSF2 padding
from PIL import Image  # noqa: E402


def _load(*names):
    return [gg.Atlas.load(os.path.join(FIX, n)) for n in names]


def _render_font(entry, lines, cols=40, rows=15, fg=(170, 170, 170)):
    """Render text lines with the bitmaps of an atlas font entry (the committed bitmaps), one char -> its bitmap (ASCII preferred)."""
    cw, ch = entry["cell_w"], entry["cell_h"]
    bych = {}
    for k, g in entry["glyphs"].items():
        for c in sorted(g["c"], key=lambda c: (ord(c) > 127, c)):
            bych.setdefault(c, bytes.fromhex(k))
    rb = (cw + 7) // 8
    im = Image.new("RGB", (cols * cw, rows * ch), (0, 0, 0))
    px = im.load()
    for r, line in enumerate(lines[:rows]):
        for c, chh in enumerate(line[:cols]):
            key = bych.get(chh)
            for y in range(ch if key else 0):
                v = int.from_bytes(key[y * rb:(y + 1) * rb], "big") >> (rb * 8 - cw)
                for x in range(cw):
                    if v >> (cw - 1 - x) & 1:
                        px[c * cw + x, r * ch + y] = fg
    return im


def _wrong_font_exact(res, lines):
    bad = []
    for r, (row, cf) in enumerate(zip(res["rows"], res["conf"])):
        for c, (chh, st) in enumerate(zip(row, cf)):
            truth = lines[r][c] if r < len(lines) and c < len(lines[r]) else " "
            if st in "FHe" and chh != truth:
                bad.append((r, c, chh, truth, st))
    return bad


DENSE = ["┏━━━━┓ Hello World 0123456789", "┃ ok ┃ The quick brown fox jumps over",
         "┗━━━━┛ the lazy dog; ABC xyz [ ] { } ( )", "root@host:~# ls -la /etc | grep conf QWERTY",
         "╔══╗ ║ ╠ ╣ ╚══╝ ____ ---- #### ~ ` ! ? % & @ \\ /"] * 3


def test_scale_hazard_integer_double_of_vga_is_never_read_as_font_exact_vga():
    """REPRODUCTION (review): a real 16x32 Lat15-VGA screen is an exact 2x nearest-neighbour enlargement of an 8x16 screen; probe_scale undoes it and
    the VGA 8x16 font then read the heavy box characters as the light ones with conf F.  Rendered here from the committed font:csvga-16x32 bitmaps."""
    base, extra = _load("fonts.atlas.json", "fonts.console-extra.atlas.json")
    big = extra.fonts["font:csvga-16x32"]
    assert big["origin"]["scale_hazard"], "the builder must record the hazard in the atlas"
    lines = [l[:40] for l in DENSE]
    im = _render_font(big, lines)
    assert gg.probe_scale(im)["integer_upscale"] == 2           # the capture IS undone to 8x16
    res = gg.read_image(im, gg.Atlas.merge(base, extra))
    assert res["status"] == "ok" and res["font"] == "font:vga-8x16"
    assert _wrong_font_exact(res, lines) == []
    assert res["conf"][0][:6] == "aaaaaa" and set(res["rows"][0][:6]) == {gg.UNKNOWN_CHAR}    # heavy box corner/lines: alias sets, never a pick
    assert set(res["conf"][0][7:12]) <= set("FH") and res["conf"][0][7:11] == "FFFF"                      # text both fonts draw alike stays font-exact


@pytest.mark.parametrize("font,atlas_file", [("font:linux-ter16x32", "fonts.console-extra.atlas.json"), ("font:terminus-16x32", "fonts.console-extra.atlas.json"),
                                             ("font:terminus-bold-16x32", "fonts.console-ambiguous.atlas.json")])
def test_real_16x32_screens_of_the_terminus_fonts_are_never_a_wrong_f(font, atlas_file):
    """Dense text rendered from the committed 16x32 bitmaps (not a synthetic font), read with every shipped atlas merged: no wrong F/H/e cell,
    and a sparse screen made ONLY of the glyphs that are exact 2x doubles of 8x16 bitmaps (the screens probe_scale does undo) is not misread either."""
    base, extra, amb = _load("fonts.atlas.json", "fonts.console-extra.atlas.json", "fonts.console-ambiguous.atlas.json")
    merged = gg.Atlas.merge(base, extra, amb)
    entry = merged.fonts[font]
    lines = [l[:40] for l in DENSE]
    res = gg.read_image(_render_font(entry, lines), merged)
    assert res["status"] == "ok" and res["grid"]["cell_w"] == 16 and res["grid"]["cell_h"] == 32
    assert _wrong_font_exact(res, lines) == []
    assert sum(c in "FH" for row in res["conf"] for c in row) > 200      # dense text really is read
    dbl = [c for k, g in entry["glyphs"].items() for c in g["c"] if gg.reduce_cell(bytes.fromhex(k), 16, 32, 2) is not None and c.strip()]
    assert dbl, "this font has doublable glyphs"
    sparse = [(" ".join(dbl) + " ") * 3] * 6
    sres = gg.read_image(_render_font(entry, sparse), merged)
    assert sres["status"] != "ok" or _wrong_font_exact(sres, sparse) == []


def test_unscale_guard_synthetic_larger_font_makes_conflicting_bitmaps_alias(tmp_path):
    small = [0, 0, 0x18, 0x24, 0x42, 0x7E, 0x42, 0x42, 0x42, 0x42, 0, 0, 0, 0, 0, 0]
    other = [0, 0, 0x7C, 0x42, 0x7C, 0x42, 0x42, 0x7C, 0, 0, 0, 0, 0, 0, 0, 0]
    for n, rows in (("s.psf", {0: small, 1: other}),):
        (tmp_path / n).write_bytes(_psf1_bytes(rows, h=16, mode=2, table=b"".join(_u16(0x41 + i, 0xFFFF) for i in range(256))))
    # big font: 16x32 whose glyph 0 is the 2x enlargement of `small` but is the character 'Z'; glyph 1 is the enlargement of `other` with the same char 'B'
    def enlarge(rows):
        out = []
        for r in rows:
            v = 0
            for x in range(8):
                v = (v << 2) | (3 if r >> (7 - x) & 1 else 0)
            out += [v, v]
        return out
    head = struct.pack("<4s7I", b"\x72\xb5\x4a\x86", 0, 32, 1, 256, 64, 32, 16)
    body = b"".join(b"".join(r.to_bytes(2, "big") for r in (enlarge(small) if i == 0 else enlarge(other) if i == 1 else [0] * 32)) for i in range(256))
    table = b"".join((b"Z" if i == 0 else chr(0x41 + i).encode()) + b"\xff" for i in range(256))
    (tmp_path / "b.psf").write_bytes(head + body + table)
    a = gg.Atlas()
    ff.build_font_atlas(a, "font:s", ff.load_psf(str(tmp_path / "s.psf")), "ascii")
    big = gg.Atlas()
    ff.build_font_atlas(big, "font:big", ff.load_psf(str(tmp_path / "b.psf")), "ascii")
    im = _render_font(big.fonts["font:big"], ["ZBZBZBZB ZBZBZBZB"] * 8, cols=20, rows=10)
    only_small = gg.read_image(im, a)
    # without the larger font loaded the reading is wrong ('A' for 'Z') but is capped to the non-actionable class z: never F
    assert only_small["conf"][0][0] == "z" and only_small["rows"][0][0] == "A" and only_small["scale_unverified"] == 2 and only_small["scale_guard_fonts"] == []
    both = gg.read_image(im, gg.Atlas.merge(a, big))
    assert both["conf"][0][0] == "a" and both["conf"][0][1] == "F" and both["rows"][0][1] == "B"      # same character in both: stays F


def test_rival_that_draws_shared_bitmaps_alike_does_not_block_font_proof(tmp_path):
    import random
    rng = random.Random(3)
    chars = "abcdefghij"
    xrows = {c: [0, 0] + [(rng.getrandbits(8) & rng.getrandbits(8)) | 0x18 for _ in range(10)] + [0] * 4 for c in chars}
    # rival y: first 4 characters have the SAME bitmaps and characters as x (40% of the screen), the rest differ
    yrows = {c: (list(xrows[c]) if c in "abcd" else [0, 0] + [(rng.getrandbits(8) & rng.getrandbits(8)) | 0x24 for _ in range(10)] + [0] * 4) for c in chars}
    for n, rows in (("x", xrows), ("y", yrows)):
        (tmp_path / (n + ".psf")).write_bytes(_psf1_bytes({ord(c): v for c, v in rows.items()}, h=16, mode=2, table=b"".join(_u16(i, 0xFFFF) for i in range(256))))
    at = gg.Atlas()
    for n in "xy":
        ff.build_font_atlas(at, "font:" + n, ff.load_psf(str(tmp_path / (n + ".psf"))), "ascii")
    im = _render_font(at.fonts["font:x"], [chars * 4] * 12, cols=40, rows=12)
    res = gg.read_image(im, at, grid_hints=[(8, 16)])
    assert res["font"] == "font:x" and res["font_runner_up_share"] > gg.PROVEN_RUNNER_UP
    assert res["font_proven"] is True and res["font_rivals_blocking"] == []       # old rule: not proven (rival explains 40%)
    # now make one shared bitmap a DIFFERENT character in the rival: the same screen can be the rival's -> not proven
    g = at.fonts["font:y"]["glyphs"]
    k = next(k for k, v in g.items() if "a" in v["c"])
    g[k]["c"] = {"q": 1}
    at._xcache.clear()
    res = gg.read_image(im, at, grid_hints=[(8, 16)])
    assert res["font_proven"] is False and res["font_rivals_blocking"] == ["font:y"]


def test_same_as_and_merge_verify_the_character_mapping_not_only_the_bitmaps(tmp_path):
    tab = lambda perm: b"".join(_u16(0x41 + perm.get(i, i), 0xFFFF) for i in range(256))       # glyph i draws U+0041+perm(i)
    (tmp_path / "ref.psf.gz").write_bytes(gzip.compress(_psf1_bytes({0: A, 1: B}, h=16, mode=2, table=tab({}))))
    (tmp_path / "perm.psf.gz").write_bytes(gzip.compress(_psf1_bytes({0: A, 1: B}, h=16, mode=2, table=tab({0: 1, 1: 0}))))   # SAME bitmaps, A and B swapped
    base = gg.Atlas()
    ff.build_font_atlas(base, "font:ref", ff.load_psf(str(tmp_path / "ref.psf.gz")), "ascii")
    rep = bfa.build_manifest(_manifest(tmp_path, [_entry(tmp_path, "perm.psf.gz", "font:p", same_as="font:ref")]), str(tmp_path), gg.Atlas(), base)["entries"][0]
    assert rep["identical"] is False and rep["mapping_differences"] == 2 and rep["repertoire_char_differences"] == 2 and rep["subset_of_reference"] is False
    with pytest.raises(ValueError, match="another mapping|differ"):
        bfa.build_manifest(_manifest(tmp_path, [_entry(tmp_path, "ref.psf.gz", "font:q"), _entry(tmp_path, "perm.psf.gz", "font:q")]), str(tmp_path), gg.Atlas())


def test_psf2_unicode_table_shorter_than_glyph_count_pads_instead_of_dropping_glyphs(tmp_path):
    p = tmp_path / "t.psf"
    data = _psf2_bytes(8, 4, [([0xF0, 0, 0, 0xF0], "A"), ([1, 2, 3, 4], "B"), ([9, 9, 9, 9], "C")])
    head_and_body = data[:data.index(b"A\xff")]
    p.write_bytes(head_and_body + b"A\xff")                    # table holds ONE record for three glyphs
    font = ff.load_psf(str(p))
    assert len(font.glyphs) == 3 and [g[0] for g in font.glyphs] == [[0x41], [], []] and font.glyphs[2][1] == [9, 9, 9, 9]


def test_builder_fails_when_manifest_tier_disagrees_with_the_mechanical_analysis(tmp_path):
    _write_psf(tmp_path / "base.psf.gz", {0: A, 1: B, 2: C})
    _write_psf(tmp_path / "dup.psf.gz", {0: A, 1: B, 2: C})                  # shares 100% of its bitmaps with a base font: cannot be 'extra'
    base = gg.Atlas()
    ff.build_font_atlas(base, "font:base", ff.load_psf(str(tmp_path / "base.psf.gz")), "ascii")
    with pytest.raises(ValueError, match="hazard analysis says 'ambiguous'"):
        bfa.build_manifest(_manifest(tmp_path, [_entry(tmp_path, "dup.psf.gz", "font:dup", tier="extra")]), str(tmp_path), gg.Atlas(), base)
    rep = bfa.build_manifest(_manifest(tmp_path, [_entry(tmp_path, "dup.psf.gz", "font:dup", tier="ambiguous")]), str(tmp_path), gg.Atlas(), base, tier="ambiguous")
    assert rep["entries"][0]["analysis"]["native"][0]["font"] == "font:base"


def test_builder_records_scale_hazard_of_a_doubled_font(tmp_path):
    (tmp_path / "s.psf.gz").write_bytes(gzip.compress(_psf1_bytes({0: A}, h=16, mode=2, table=b"".join(_u16(0x41 + i, 0xFFFF) for i in range(256)))))
    big = gg.Atlas()
    small = gg.Atlas()
    ff.build_font_atlas(small, "font:small", ff.load_psf(str(tmp_path / "s.psf.gz")), "ascii")
    k, g = next(iter(small.fonts["font:small"]["glyphs"].items()))
    # a 16x32 font whose only glyph is the 2x enlargement of that bitmap but draws 'Q'
    rows = [int(format(r, "08b").replace("0", "00").replace("1", "11"), 2) for r in A for _ in (0, 1)]
    packed = b"".join(r.to_bytes(2, "big") for r in rows)
    big.fonts["font:big"] = {"cell_w": 16, "cell_h": 32, "glyphs": {packed.hex(): {"c": {"Q": 1}, "f": ["x"], "font_exact": True}},
                             "origin": {"kind": "font-file", "sha256": "0" * 64}}
    ana = bfa.hazard_analysis({**small.fonts, **big.fonts}, ["font:big"], ["font:small"])["font:big"]
    assert ana["scale_hazard"] is True and ana["tier"] == "extra"
    assert ana["scale"][0]["doublable"] == 1 and ana["scale"][0]["differing_chars"] == 1


def test_committed_manifest_tiers_equal_the_mechanical_analysis_of_the_committed_atlases():
    man = json.load(open(os.path.join(HERE, "..", "tools", "glyphgrid", "fonts-console-extra.manifest.json")))
    base, extra, amb = _load("fonts.atlas.json", "fonts.console-extra.atlas.json", "fonts.console-ambiguous.atlas.json")
    fonts = {**base.fonts, **extra.fonts, **amb.fonts}
    order = []
    for e in man["entries"]:
        if not e.get("same_as") and e["atlas_font"] not in order:
            order.append(e["atlas_font"])
    ana = bfa.hazard_analysis(fonts, order, list(base.fonts))
    want = {e["atlas_font"]: e.get("tier", "extra") for e in man["entries"] if not e.get("same_as")}
    assert {n: a["tier"] for n, a in ana.items()} == want
    assert {n for n, a in ana.items() if a["scale_hazard"]} == {"font:csvga-16x32"}
    assert set(extra.fonts) >= {"font:csvga-16x32", "font:csvga-16x28"}      # the larger fonts stay loaded: the unscale guard needs them


# ----------------------------------------------------------------------------- second review: every divisor, carried k, capped class z, mutation kills
def _zoom(im, m):
    return im.resize((im.size[0] * m, im.size[1] * m), Image.NEAREST) if m > 1 else im


def _placeholder_font(cw, ch, seed=1):
    """A font-file font of cell cw x ch with ONE unrelated glyph: it only makes 'a font of this size exists' true for the guard."""
    import random
    rng = random.Random(seed + cw)
    rb = (cw + 7) // 8
    key = bytes(rng.getrandbits(8) | 0x01 for _ in range(rb * ch))
    return {"cell_w": cw, "cell_h": ch, "glyphs": {key.hex(): {"c": {"§": 1}, "f": ["x"], "font_exact": True}},
            "origin": {"kind": "font-file", "sha256": "0" * 64, "file": "placeholder"}}


def _guard_complete_atlas(cell, divisors):
    base, extra = _load("fonts.atlas.json", "fonts.console-extra.atlas.json")
    a = gg.Atlas.merge(base, extra)
    for d in divisors:
        a.fonts.setdefault("font:placeholder-%dx%d" % (cell[0] * d, cell[1] * d), _placeholder_font(cell[0] * d, cell[1] * d))
    return a


@pytest.mark.parametrize("zoom,k", [(1, 2), (2, 4), (3, 6), (4, 8)])
def test_real_csvga_16x32_screen_at_any_zoom_is_never_a_wrong_f(zoom, k):
    """A real csvga-16x32 screen (2x of 8x16) at browser zoom m is a 2m-fold enlargement: probe_scale returns the LARGEST k, the divisor-2 font must
    still be consulted.  Guard made 'complete' with placeholder fonts for the other divisor sizes so the cap does not hide the alias logic."""
    base, extra = _load("fonts.atlas.json", "fonts.console-extra.atlas.json")
    lines = [l[:40] for l in DENSE]
    im = _zoom(_render_font(extra.fonts["font:csvga-16x32"], lines), zoom)
    assert gg.probe_scale(im)["integer_upscale"] == k
    divs = [d for d in range(2, k + 1) if k % d == 0]
    atlas = _guard_complete_atlas((8, 16), divs)
    res = gg.read_image(im, atlas)
    assert res["status"] == "ok" and res["font"] == "font:vga-8x16" and res["scale_unverified"] == k and res["scale_capped"] is False
    assert "font:csvga-16x32" in res["scale_guard_fonts"] and len(res["scale_guard_fonts"]) >= len(divs)
    assert _wrong_font_exact(res, lines) == []
    assert res["conf"][0][:6] == "aaaaaa"                          # heavy box characters: alias sets at every zoom, never the light ones
    assert set(res["conf"][0][7:12]) <= set("FH") and res["conf"][0][7:11] == "FFFF"


@pytest.mark.parametrize("zoom,k", [(1, 2), (2, 4)])
def test_csvga_16x28_non_square_pair_is_guarded_at_k2_and_k4(zoom, k):
    base, extra = _load("fonts.atlas.json", "fonts.console-extra.atlas.json")
    lines = [l[:40] for l in DENSE if l.isascii()] or ["Hello World 0123456789"]
    ascii_lines = ["Hello World 0123456789 ~ ` | _", "The quick brown fox jumps over the lazy dog"] * 4
    im = _zoom(_render_font(extra.fonts["font:csvga-16x28"], ascii_lines, cols=40, rows=8), zoom)
    assert gg.probe_scale(im)["integer_upscale"] == k
    atlas = _guard_complete_atlas((8, 14), [d for d in range(2, k + 1) if k % d == 0])
    res = gg.read_image(im, atlas)
    assert res["status"] == "ok" and res["grid"]["cell_w"] == 8 and res["grid"]["cell_h"] == 14
    assert "font:csvga-16x28" in res["scale_guard_fonts"] and _wrong_font_exact(res, ascii_lines) == []


@pytest.mark.parametrize("zoom,k", [(2, 4), (3, 6), (4, 8)])
def test_incomplete_guard_caps_font_exact_cells_to_class_z_and_trust_scale_is_explicit(zoom, k):
    base, extra = _load("fonts.atlas.json", "fonts.console-extra.atlas.json")
    lines = [l[:40] for l in DENSE]
    im = _zoom(_render_font(extra.fonts["font:csvga-16x32"], lines), zoom)
    res = gg.read_image(im, gg.Atlas.merge(base, extra))              # no font of 32x64 / 24x48 / ... exists
    assert res["scale_unverified"] == k and res["scale_capped"] is True and "font:csvga-16x32" in res["scale_guard_fonts"]
    flat = "".join(res["conf"])
    assert "z" in flat and "F" not in flat and "H" not in flat and _wrong_font_exact(res, lines) == []
    assert gg.find_phrase(res, "Hello World") == [] and gg.region(res, 0, 1, 7, 18)[1] > 0
    sm = gg.strict_match(res, "Hello World")
    assert sm["matched"] is False and sm["scale_unverified"] == k and sm["trust_scale"] is False and any("not actionable" in r for r in sm["reasons"])
    trusted = gg.strict_match(res, "Hello World", trust_scale=True)
    assert trusted["matched"] is True and trusted["trust_scale"] is True and trusted["scale_unverified"] == k
    assert gg.find_phrase(gg.trust_scale(res), "Hello World") and "z" in "".join(res["conf"])       # the original result is not modified


def test_plain_1to1_read_has_no_scale_fields_and_k2_with_base_extra_is_complete():
    base, extra = _load("fonts.atlas.json", "fonts.console-extra.atlas.json")
    res = gg.read_image(os.path.join(FIX, "crop-14-dialog.png"), base, grid_hints=[(8, 16)])
    assert "scale_unverified" not in res
    lines = [l[:40] for l in DENSE]
    res = gg.read_image(_zoom(_render_font(extra.fonts["font:csvga-16x32"], lines), 1), gg.Atlas.merge(base, extra))
    assert res["scale_unverified"] == 2 and res["scale_capped"] is False         # 16x32 fonts are loaded: the divisor-2 guard is complete


def test_prepare_carries_k_so_verify_fonts_and_eval_paths_cannot_bypass_the_guard(tmp_path):
    base, extra = _load("fonts.atlas.json", "fonts.console-extra.atlas.json")
    atlas = gg.Atlas.merge(base, extra)
    lines = [l[:40] for l in DENSE]
    p = tmp_path / "big.png"
    _render_font(extra.fonts["font:csvga-16x32"], lines).save(p)
    im, probe, g = gg.prepare(str(p), atlas=atlas)
    assert g["unscaled"] == 2 and probe["integer_upscale"] == 2
    ex, _ = gg.choose_extraction(im, g, atlas)
    assert ex.grid["unscaled"] == 2
    res = gg.decode(ex, atlas)                                        # the verify_fonts.py / eval_glyphgrid.py call: NO unscaled argument
    assert res["scale_unverified"] == 2 and _wrong_font_exact(res, lines) == [] and res["conf"][0][:6] == "aaaaaa"
    import verify_fonts as vf
    got, why = vf.read_frame(str(p), atlas)
    assert why == "ok" and got[1]["conf"][0][:6] == "aaaaaa"
    # a 1:1 capture carries k=1
    q = tmp_path / "plain.png"
    Image.open(os.path.join(FIX, "crop-14-dialog.png")).save(q)
    assert gg.prepare(str(q), atlas=atlas)[2]["unscaled"] == 1


def _enlarged_font(tmp_path, name, rows, k, char_of_glyph0):
    """16-row 8-wide 'rows' enlarged k-fold into a (8k)x(16k) PSF2 whose glyph 0 draws ``char_of_glyph0`` and glyph 1 a second, equal-in-both character."""
    def enl(rs):
        out = []
        for r in rs:
            v = 0
            for x in range(8):
                v = (v << k) | (((1 << k) - 1) if r >> (7 - x) & 1 else 0)
            out += [v] * k
        return out
    w, h = 8 * k, 16 * k
    other = [0, 0, 0x7C, 0x42, 0x7C, 0x42, 0x42, 0x7C, 0, 0, 0, 0, 0, 0, 0, 0]
    glyphs = [(enl(rows), char_of_glyph0), (enl(other), "B")]
    (tmp_path / name).write_bytes(_psf2_bytes(w, h, glyphs))
    return ff.load_psf(str(tmp_path / name))


R_SMALL = [0, 0, 0x18, 0x24, 0x42, 0x7E, 0x42, 0x42, 0x42, 0x42, 0, 0, 0, 0, 0, 0]


@pytest.mark.parametrize("zoom,k,fonts_k", [(1, 3, 3), (2, 6, 3), (2, 4, 2), (1, 8, 8), (4, 8, 2)])
def test_guard_unions_every_divisor_synthetic(tmp_path, zoom, k, fonts_k):
    """Native font of k_f = fonts_k x the cell, shown at zoom: total k.  Placeholder fonts make the guard complete for every divisor of k; only the
    union over divisors reaches the real font (an exact-k lookup or a range(2, 3) loop misses it for 4/6/8 and for 3)."""
    small = ff.load_psf(str(_write(tmp_path, "s.psf", _psf2_bytes(8, 16, [(R_SMALL, "A"), ([0, 0, 0x7C, 0x42, 0x7C, 0x42, 0x42, 0x7C, 0, 0, 0, 0, 0, 0, 0, 0], "B")]))))
    big = _enlarged_font(tmp_path, "b.psf", R_SMALL, fonts_k, "Z")
    a = gg.Atlas()
    ff.build_font_atlas(a, "font:s", small, "ascii")
    b = gg.Atlas()
    ff.build_font_atlas(b, "font:b", big, "ascii")
    atlas = gg.Atlas.merge(a, b)
    for d in range(2, k + 1):
        if k % d == 0 and d != fonts_k:
            atlas.fonts["font:ph%d" % d] = _placeholder_font(8 * d, 16 * d)
    cols, rows = max(1, 640 // (8 * fonts_k)), 6
    line = ("ZBZB BZBZ " * 20)[:cols]
    im = _zoom(_render_font(b.fonts["font:b"], [line] * rows, cols=cols, rows=rows), zoom)
    assert gg.probe_scale(im)["integer_upscale"] == k
    res = gg.read_image(im, atlas)
    assert res["scale_unverified"] == k and res["scale_capped"] is False
    first = next(i for i, ch in enumerate(line) if ch == "Z")
    assert res["conf"][0][first] == "a"                                  # 'A' by the small font, 'Z' by the large one: alias set
    assert _wrong_font_exact(res, [line] * rows) == []


def _write(tmp_path, name, data):
    p = tmp_path / name
    p.write_bytes(data)
    return p


def _same_result(a, b):
    drop = ("session", "grid_report")
    return {k: v for k, v in a.items() if k not in drop} == {k: v for k, v in b.items() if k not in drop}


def test_glyph_reader_goes_through_the_guard_for_a_zoomed_csvga_screen_full_and_incremental():
    """k=4 dense csvga-16x32 screen: every GlyphReader read is a FULL pipeline pass (sessions exist only for integer_upscale == 1), so the guard
    always applies; 0 wrong F/H/e cells and the same result as read_image.  A 1:1 incremental read of a font-file screen stays equal to read_image."""
    base, extra = _load("fonts.atlas.json", "fonts.console-extra.atlas.json")
    atlas = gg.Atlas.merge(base, extra)
    lines = [l[:40] for l in DENSE]
    im = _zoom(_render_font(extra.fonts["font:csvga-16x32"], lines), 2)
    assert gg.probe_scale(im)["integer_upscale"] == 4
    reader = gg.GlyphReader(atlas)
    changed = im.copy()
    changed.paste((0, 0, 0), (0, 0, 8 * 2 * 4, 16 * 2 * 4))           # blank the first 2 cells of row 0 (a different, still 4-fold frame)
    for frame in (im, im, changed, im):
        res = reader.read(frame)
        ref = gg.read_image(frame, atlas)
        assert res["session"]["path"] in ("full", "identical")           # never "incremental" for k > 1
        assert res["scale_unverified"] == 4 and res["scale_capped"] is True
        truth = lines if frame is not changed else ["  " + lines[0][2:]] + lines[1:]
        assert _wrong_font_exact(res, truth) == [] and "F" not in "".join(res["conf"]) and "H" not in "".join(res["conf"])
        assert res["conf"][0][2:6] == "aaaa"                             # heavy box cells: alias sets via the divisor-2 font
        assert _same_result(res, ref)
    # 1:1 incremental path on a real crop: identical to read_image, nothing about k
    crop = Image.open(os.path.join(FIX, "crop-14-dialog.png")).convert("RGB")
    r2 = gg.GlyphReader(base, grid_hints=[(8, 16)])
    first = r2.read(crop)
    edited = crop.copy()
    edited.paste((0, 0, 0), (0, 0, 8, 16))
    second = r2.read(edited)
    assert second["session"]["path"] == "incremental" and "scale_unverified" not in second
    assert _same_result(second, gg.read_image(edited, base, grid_hints=[(8, 16)])) and first["status"] == "ok"


def test_alias_cells_also_get_the_twin_characters(tmp_path):
    """Skipping the 'alias' branch of the twin lookup would leave the alias set without the larger font's character."""
    two = _psf2_bytes(8, 16, [(R_SMALL, "AÀ"), ([0, 0, 0x7C, 0x42, 0x7C, 0x42, 0x42, 0x7C, 0, 0, 0, 0, 0, 0, 0, 0], "B")])
    small = ff.load_psf(str(_write(tmp_path, "s.psf", two)))
    a = gg.Atlas()
    ff.build_font_atlas(a, "font:s", small, "latin1")                          # glyph 0 draws BOTH 'A' and U+00C0 inside the repertoire: an alias set
    assert len(next(iter(a.fonts["font:s"]["glyphs"].values()))["c"]) == 2
    big = _enlarged_font(tmp_path, "b.psf", R_SMALL, 2, "Z")
    b = gg.Atlas()
    ff.build_font_atlas(b, "font:b", big, "latin1")
    atlas = gg.Atlas.merge(a, b)
    im = _render_font(b.fonts["font:b"], ["ZBZB BZBZ"] * 8, cols=16, rows=8)
    res = gg.read_image(im, atlas)
    assert res["conf"][0][0] == "a" and "Z" in res["aliases"]["0,0"] and "A" in res["aliases"]["0,0"]


def test_font_mapping_includes_homoglyph_lists_same_as_and_merge_notice_them(tmp_path):
    """Same bitmaps, same repertoire characters, but a different list of OTHER code points: not identical, and a merge of two sources is refused."""
    rows2 = [0, 0, 0x7C, 0x42, 0x7C, 0x42, 0x42, 0x7C, 0, 0, 0, 0, 0, 0, 0, 0]
    (tmp_path / "ref.psf").write_bytes(_psf2_bytes(8, 16, [(R_SMALL, "AА"), (rows2, "B")]))
    (tmp_path / "less.psf").write_bytes(_psf2_bytes(8, 16, [(R_SMALL, "A"), (rows2, "B")]))
    base = gg.Atlas()
    ff.build_font_atlas(base, "font:ref", ff.load_psf(str(tmp_path / "ref.psf")), "ascii")
    ent = _entry(tmp_path, "less.psf", "font:l", same_as="font:ref")
    ent["kind"] = "psf"
    rep = bfa.build_manifest(_manifest(tmp_path, [ent]), str(tmp_path), gg.Atlas(), base)["entries"][0]
    assert rep["identical"] is False and rep["mapping_differences"] == 1 and rep["homoglyph_list_differences"] == 1 and rep["repertoire_char_differences"] == 0
    assert rep["homoglyph_lists_here_subset_of_reference"] is True and rep["subset_of_reference"] is True
    assert bfa.mapping_differences(base.fonts["font:ref"], base.fonts["font:ref"]) == ([], [], {})
    with pytest.raises(ValueError, match="another mapping|differ"):
        bfa.build_manifest(_manifest(tmp_path, [_entry(tmp_path, "ref.psf", "font:q"), _entry(tmp_path, "less.psf", "font:q")]), str(tmp_path), gg.Atlas())


def test_hazard_analysis_flags_a_new_smaller_font_doubled_by_a_larger_base_font(tmp_path):
    small = ff.load_psf(str(_write(tmp_path, "s.psf", _psf2_bytes(8, 16, [(R_SMALL, "A")]))))
    big = _enlarged_font(tmp_path, "b.psf", R_SMALL, 2, "Q")
    s_at, b_at = gg.Atlas(), gg.Atlas()
    ff.build_font_atlas(s_at, "font:small", small, "ascii")
    ff.build_font_atlas(b_at, "font:big", big, "ascii")
    ana = bfa.hazard_analysis({**s_at.fonts, **b_at.fonts}, ["font:small"], ["font:big"])["font:small"]       # big is a BASE font, small is the new one
    assert ana["scale_hazard"] is True and ana["hazard_pairs"][0]["larger"] == "font:big" and ana["hazard_pairs"][0]["differing_chars"] == 1
    k3 = _enlarged_font(tmp_path, "b3.psf", R_SMALL, 3, "Q")
    k3_at = gg.Atlas()
    ff.build_font_atlas(k3_at, "font:big3", k3, "ascii")
    ana = bfa.hazard_analysis({**s_at.fonts, **k3_at.fonts}, ["font:big3"], ["font:small"])["font:big3"]
    assert ana["scale_hazard"] is True and ana["scale"][0]["k"] == 3         # a loop restricted to k=2 would miss this


def test_psf2_table_ending_mid_entry_or_with_bad_utf8_fails_loudly(tmp_path):
    full = _psf2_bytes(8, 4, [([0xF0, 0, 0, 0xF0], "A"), ([1, 2, 3, 4], "B")])
    cut = full[:-1]                                                   # "...A\\xff" + "B" without its terminator: the second entry is cut
    p = _write(tmp_path, "cut.psf", cut)
    with pytest.raises(ValueError, match="ends inside entry"):
        ff.load_psf(str(p))
    bad = _psf2_bytes(8, 4, [([0xF0, 0, 0, 0xF0], b"A\xc3"), ([1, 2, 3, 4], "B")])
    with pytest.raises(ValueError, match="not valid UTF-8"):
        ff.load_psf(str(_write(tmp_path, "bad.psf", bad)))


def test_notice_snippet_hashes_are_computed_and_checked(tmp_path):
    import notice_hashes as nh
    text = "\n".join("line %d" % i for i in range(1, 130)) + "\n"
    assert nh.snippet_sha256(text, 5, 8) == hashlib.sha256(b"line 5\nline 6\nline 7\nline 8\n").hexdigest()
    assert nh.snippet_sha256(text, 5, 8) != nh.snippet_sha256(text, 5, 9)
    res = nh.check_text(text)
    assert not any(v[2] for v in res.values())                         # a different text matches none of the recorded real hashes
    notice = open(os.path.join(HERE, "..", "tools", "glyphgrid", "NOTICE.md"), encoding="utf-8").read()
    assert nh.check_notice(notice) == []
    wrong = notice.replace(nh.SNIPPETS["V"][2], nh.SNIPPETS["T"][2], 1)
    assert any("snippet V" in p for p in nh.check_notice(wrong))
    assert "u18" in notice and "unverified" in notice.lower()


# ----------------------------------------------------------------------------- third review: pre-cap class kept, flags, caches, PSF1, NOTICE hashes, reader pin
@pytest.mark.parametrize("policy", ["latin-only", "none", "declared-repertoire"])
@pytest.mark.parametrize("phrase", ["Hello World", "Hello", "World", "quick brown"])
def test_trust_scale_restores_h_cells_and_applies_the_same_homoglyph_policy_as_the_1to1_read(policy, phrase):
    """k=4 (2x zoom of the 16x32 screen, capped) + trust_scale must decide exactly like the uncapped k=2 reading of the same screen, for every policy."""
    base, extra = _load("fonts.atlas.json", "fonts.console-extra.atlas.json")
    atlas = gg.Atlas.merge(base, extra)
    lines = [l[:40] for l in DENSE]
    src = _render_font(extra.fonts["font:csvga-16x32"], lines)
    ref = gg.read_image(src, atlas)                                    # k=2: guard complete, nothing capped
    capped = gg.read_image(_zoom(src, 2), atlas)                       # k=4: capped
    assert ref["scale_capped"] is False and capped["scale_capped"] is True and "H" in ref["conf"][0]
    assert set(capped["conf"][0][7:18]) == {"z", "b"} and capped["scale_precap"]["0,11"] == "H"
    assert any(v == "H" for v in capped["scale_precap"].values())
    trusted = gg.trust_scale(capped)
    assert trusted["conf"] == ref["conf"] and trusted["flags"] == ref["flags"] and trusted["rows"] == ref["rows"]
    a = gg.strict_match(ref, phrase, homoglyphs=policy)
    b = gg.strict_match(capped, phrase, homoglyphs=policy, trust_scale=True)
    assert (a["matched"], a["hits"]) == (b["matched"], b["hits"])
    assert gg.strict_match(capped, phrase, homoglyphs=policy)["matched"] is False          # without the opt-in nothing is actionable
    assert gg.find_phrase(trusted, phrase, homoglyphs=policy) == gg.find_phrase(ref, phrase, homoglyphs=policy)


def _bar_font_atlas(tmp_path):
    """Synthetic 8x16 font whose glyphs DECODE: 'A', 'B', a cursor-shaped lower bar U+2581 and a rule piece U+2500."""
    rows_b = [0, 0, 0x7C, 0x42, 0x7C, 0x42, 0x42, 0x7C, 0, 0, 0, 0, 0, 0, 0, 0]
    bar = [0] * 14 + [0xFF, 0xFF]
    rule = [0] * 8 + [0xFF] + [0] * 7
    (tmp_path / "bar.psf").write_bytes(_psf2_bytes(8, 16, [(R_SMALL, "A"), (rows_b, "B"), (bar, "▁"), (rule, "─")]))
    a = gg.Atlas()
    ff.build_font_atlas(a, "font:bar", ff.load_psf(str(tmp_path / "bar.psf")), "ascii+box")
    return a


def test_capped_cells_keep_rule_and_cursor_flags_of_their_precap_class(tmp_path):
    atlas = _bar_font_atlas(tmp_path)
    ent = atlas.fonts["font:bar"]
    lines = ["ABABABABAB"] * 8 + ["─" * 8, "ABAB▁"]
    ref = gg.read_image(_render_font(ent, lines, cols=12, rows=10), atlas, grid_hints=[(8, 16)])
    assert ref["conf"][8][:4] == "FFFF" and ref["flags"][8][:4] == "rrrr" and ref["flags"][9][4] == "c"          # 1:1: rule run and cursor candidate
    z3 = gg.read_image(_zoom(_render_font(ent, lines, cols=12, rows=10), 3), atlas, grid_hints=[(8, 16)])
    assert z3["scale_unverified"] == 3 and z3["scale_capped"] is True and set(z3["conf"][0][:10]) == {"z"}
    assert z3["flags"] == ref["flags"]                                    # flags as if the cells were F
    t = gg.trust_scale(z3)
    assert t["conf"] == ref["conf"] and t["flags"] == ref["flags"]
    for phrase in ("ABAB", "────", "AB▁"):
        assert gg.strict_match(t, phrase)["matched"] == gg.strict_match(ref, phrase)["matched"]
    assert gg.strict_match(t, "\u2500\u2500\u2500\u2500")["matched"] is False     # the rule run stays refused (flag r)


def test_atlas_add_clears_derived_caches():
    big = {"cell_w": 16, "cell_h": 32, "glyphs": {}, "origin": {"kind": "font-file", "sha256": "0" * 64}}
    rows = [[0, 0, 0x18, 0x24, 0x42, 0x7E, 0x42, 0x42, 0x42, 0x42, 0, 0, 0, 0, 0, 0][i // 2] for i in range(32)]
    key16 = b"".join(int("".join("11" if v >> (7 - x) & 1 else "00" for x in range(8)), 2).to_bytes(2, "big") for v in rows)
    a = gg.Atlas({"font:big": big})
    assert a.scale_twins(2, 8, 16) == {}                                  # cached empty
    a.add("font:big", 16, 32, key16, "Z")
    twins = a.scale_twins(2, 8, 16)
    assert list(twins.values()) == [{"Z"}]


def test_psf1_unicode_table_with_an_unterminated_final_record_fails_loudly(tmp_path):
    p = tmp_path / "t.psf"
    p.write_bytes(_psf1_bytes({0: [0xFF] * 8}, mode=2, table=_u16(0x41, 0xFFFF) + _u16(0x42, 0x43)))        # second record never ends
    with pytest.raises(ValueError, match="no 0xFFFF terminator"):
        ff.load_psf(str(p))
    q = tmp_path / "odd.psf"
    q.write_bytes(_psf1_bytes({0: [0xFF] * 8}, mode=2, table=_u16(0x41, 0xFFFF) + b"\x42"))
    with pytest.raises(ValueError):
        ff.load_psf(str(q))
    ok = tmp_path / "ok.psf"
    ok.write_bytes(_psf1_bytes({0: [0xFF] * 8}, mode=2, table=_u16(0x41, 0xFFFF)))                       # a table that is simply shorter: padded, as before
    assert [g[0] for g in ff.load_psf(str(ok)).glyphs[:2]] == [[0x41], []]


def test_check_notice_rejects_any_unrecorded_sha256():
    import notice_hashes as nh
    notice = open(os.path.join(HERE, "..", "tools", "glyphgrid", "NOTICE.md"), encoding="utf-8").read()
    assert nh.check_notice(notice) == []
    extra = notice + "\nbogus `" + "ab" * 32 + "`\n"
    assert any("not a recorded one" in p for p in nh.check_notice(extra))
    assert nh.check_notice(notice + "\nprefix `" + "ab" * 8 + "...`\n") == []          # a truncated prefix is not a full hash claim


    assert any("not a recorded one" in p for p in nh.check_notice(notice + "\nUPPER `" + ("AB" * 32) + "`\n"))                   # uppercase hex is still a hash
    assert any("128-hex" in p for p in nh.check_notice(notice + "\nsha512 `" + ("ab" * 64) + "`\n"))                          # sha512-length runs
    # a recorded hash in uppercase counts as quoted; a whitelist entry that NOTICE.md no longer quotes is stale
    gone = notice.replace(nh.KNOWN_OTHER_SHA256[0], "0" * 12 + "...")
    assert any("stale whitelist" in p for p in nh.check_notice(gone))


def test_glyph_reader_zoomed_frame_then_native_frame_is_a_fresh_full_read():
    """Crossing: a 3x zoom stores NO session, so the following native crop (whose size equals the UNSCALED size of the first frame) is a full pass equal to a
    fresh read_image; with a session it came back as 'identical' with the stale capped result (scale_capped True, scale_unverified 3)."""
    base, extra = _load("fonts.atlas.json", "fonts.console-extra.atlas.json")
    crop = Image.open(os.path.join(FIX, "crop-14-dialog.png")).convert("RGB")
    assert crop.size == (480, 96)
    zoomed = _zoom(crop, 3)
    for atlas in (base, gg.Atlas.merge(base, extra)):
        reader = gg.GlyphReader(atlas, grid_hints=[(8, 16)])
        z = reader.read(zoomed)
        assert z["scale_unverified"] == 3 and z["session"]["path"] == "full"
        native = reader.read(crop)
        fresh = gg.read_image(crop, atlas, grid_hints=[(8, 16)])
        assert native["session"]["path"] == "full" and "scale_unverified" not in native and native.get("scale_capped") is None
        assert _same_result(native, fresh)
        again = reader.read(crop)                                       # and the native frame now keeps its normal session
        assert again["session"]["path"] == "identical" and _same_result(again, fresh)


def test_psf1_table_with_a_stray_trailing_byte_after_all_records_fails(tmp_path):
    table = b"".join(_u16(i, 0xFFFF) for i in range(256)) + b"\x01"
    p = tmp_path / "t.psf"
    p.write_bytes(_psf1_bytes({0: [0xFF] * 8}, mode=2, table=table))
    with pytest.raises(ValueError, match="stray byte"):
        ff.load_psf(str(p))
    p.write_bytes(_psf1_bytes({0: [0xFF] * 8}, mode=2, table=table[:-1]))      # without the stray byte: fine
    assert len(ff.load_psf(str(p)).glyphs) == 256


def test_trust_scale_fails_closed_without_the_recorded_precap_class():
    base, extra = _load("fonts.atlas.json", "fonts.console-extra.atlas.json")
    atlas = gg.Atlas.merge(base, extra)
    capped = gg.read_image(_zoom(_render_font(extra.fonts["font:csvga-16x32"], [l[:40] for l in DENSE]), 2), atlas)
    assert "z" in "".join(capped["conf"])
    gone = {k: v for k, v in capped.items() if k != "scale_precap"}
    with pytest.raises(ValueError, match="scale_precap"):
        gg.trust_scale(gone)
    with pytest.raises(ValueError, match="scale_precap"):
        gg.strict_match(gone, "Hello World", trust_scale=True)
    foreign = dict(capped, scale_precap={k: "x" for k in capped["scale_precap"]})                 # a foreign value is not F/H
    with pytest.raises(ValueError):
        gg.trust_scale(foreign)
    partial = dict(capped, scale_precap=dict(list(capped["scale_precap"].items())[:-1]))
    with pytest.raises(ValueError):
        gg.trust_scale(partial)
    assert gg.trust_scale(capped)["trust_scale"] is True and "z" in "".join(capped["conf"])


@pytest.mark.parametrize("policy,expected", [("latin-only", False), ("none", False), ("declared-repertoire", True)])
def test_capped_h_cell_with_a_multi_codepoint_alias_set_obeys_the_policy_after_trust_scale(tmp_path, policy, expected):
    """'A' also drawn as Cyrillic U+0410 and Greek U+0391 (non-Latin lookalikes): refused by latin-only and none, accepted only on declaration.  Without the stored
    alias set the restored H cell would look benign and latin-only would accept it."""
    rows_b = [0, 0, 0x7C, 0x42, 0x7C, 0x42, 0x42, 0x7C, 0, 0, 0, 0, 0, 0, 0, 0]
    (tmp_path / "h.psf").write_bytes(_psf2_bytes(8, 16, [(R_SMALL, "AАΑ"), (rows_b, "B")]))
    atlas = gg.Atlas()
    ff.build_font_atlas(atlas, "font:h", ff.load_psf(str(tmp_path / "h.psf")), "ascii")
    lines = ["AB BA AB BA AB BA"] * 8
    src = _render_font(atlas.fonts["font:h"], lines, cols=20, rows=8)
    ref = gg.read_image(src, atlas, grid_hints=[(8, 16)])
    capped = gg.read_image(_zoom(src, 3), atlas, grid_hints=[(8, 16)])
    assert ref["conf"][0][0] == "H" and capped["conf"][0][0] == "z" and capped["scale_precap"]["0,0"] == "H"
    assert capped["aliases"]["0,0"] == ref["aliases"]["0,0"] and len(ref["aliases"]["0,0"]) == 3
    assert gg.strict_match(ref, "AB", homoglyphs=policy)["matched"] is expected
    assert gg.strict_match(capped, "AB", homoglyphs=policy, trust_scale=True)["matched"] is expected
