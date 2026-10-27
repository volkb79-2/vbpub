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
    assert only_small["conf"][0][0] == "F" and only_small["rows"][0][0] == "A"              # without the larger font loaded the wrong F is unavoidable (documented)
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
