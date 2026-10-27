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
    assert rep_all["entries"][1]["same_size_relations"]["font:x"]["shared_bitmaps"] >= 1 and rep["entries"][0]["atlas_font"] == "font:y"
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
