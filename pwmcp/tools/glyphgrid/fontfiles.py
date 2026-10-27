"""Stdlib loaders for AUTHORITATIVE bitmap-font sources and generation of font-exact glyphgrid atlas fonts.

Console text is drawn from known bitmap fonts.  Instead of learning glyph->character pairs from
human-transcribed screenshots, read the font file (or the kernel's built-in font table) and render every
glyph into a cell: the character of a screenshot cell then comes from the FONT, not from a transcript.

Loaders: PSF1/PSF2 (console fonts, optional gzip, with unicode table), GRUB PF2, GNU Unifont .hex, BDF,
and raw 256-glyph tables (the Linux kernel's built-in ``font_vga_8x16`` found inside a decompressed vmlinux).
Every loader returns a ``FontFile``; ``build_font_atlas`` renders it into the atlas with provenance
(file name and sha256) and the declared repertoire.  Characters that share ONE bitmap inside the repertoire are
recorded as an alias set (``"c"`` holds every character), never resolved to one pick.
"""
from __future__ import annotations

import gzip
import hashlib
import os
import re
import struct

import glyphgrid as gg


class FontFile:
    def __init__(self, name, path, sha256, cell_w, cell_h, glyphs, ascent=None, descent=None, note=""):
        self.name, self.path, self.sha256 = name, path, sha256
        self.cell_w, self.cell_h = cell_w, cell_h
        self.glyphs = glyphs  # list of (codepoints:list[int], rows:list[int], w, h, xoff, yoff) ; rows are ints, MSB = left
        self.ascent, self.descent, self.note = ascent, descent, note


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read(path):
    data = open(path, "rb").read()
    if data[:2] == b"\x1f\x8b":
        data = gzip.decompress(data)
    return data


# ----------------------------------------------------------------------------- PSF
def load_psf(path) -> FontFile:
    raw = open(path, "rb").read()
    data = gzip.decompress(raw) if raw[:2] == b"\x1f\x8b" else raw
    glyphs = []
    if data[:2] == b"\x36\x04":  # PSF1
        mode, h = data[2], data[3]
        n = 512 if mode & 1 else 256
        w, off, hastab = 8, 4, bool(mode & 6)
        bitmaps = [data[off + i * h:off + (i + 1) * h] for i in range(n)]
        table = data[off + n * h:]
        maps = []
        if hastab:
            for entry in table.split(b"\xff\xff")[:n]:
                cps = [int.from_bytes(entry[i:i + 2], "little") for i in range(0, len(entry) - 1, 2)
                       if entry[i:i + 2] not in (b"\xfe\xff",)]
                maps.append(cps)
        else:
            maps = [[i] for i in range(n)]
    elif data[:4] == b"\x72\xb5\x4a\x86":  # PSF2
        hdrsize, flags, n, bpg, h, w = struct.unpack_from("<6I", data, 8)
        bitmaps = [data[hdrsize + i * bpg:hdrsize + (i + 1) * bpg] for i in range(n)]
        maps = []
        if flags & 1:
            tab = data[hdrsize + n * bpg:]
            for entry in tab.split(b"\xff")[:n]:
                seq = entry.split(b"\xfe")[0]  # only the single code points (ignore combining sequences)
                maps.append([ord(c) for c in seq.decode("utf-8", "replace")])
        else:
            maps = [[i] for i in range(n)]
    else:
        raise ValueError("not a PSF font: %s" % path)
    rb = (w + 7) // 8
    for bm, cps in zip(bitmaps, maps):
        rows = [int.from_bytes(bm[y * rb:(y + 1) * rb], "big") >> (rb * 8 - w) for y in range(h)]
        glyphs.append((cps, rows, w, h, 0, 0))
    return FontFile(os.path.basename(path), path, _sha(raw), w, h, glyphs)


# ----------------------------------------------------------------------------- raw table (kernel font_vga_8x16)
# IBM PC glyph indices 0x00-0x1F and 0x7F are symbols (the cp437 codec returns control characters there)
_SYM = "\u0000\u263a\u263b\u2665\u2666\u2663\u2660\u2022\u25d8\u25cb\u25d9\u2642\u2640\u266a\u266b\u263c" \
       "\u25ba\u25c4\u2195\u203c\u00b6\u00a7\u25ac\u21a8\u2191\u2193\u2192\u2190\u221f\u2194\u25b2\u25bc"
CP437 = _SYM + bytes(range(32, 127)).decode("ascii") + "\u2302" + bytes(range(128, 256)).decode("cp437")


def load_raw_table(data, offset, n=256, w=8, h=16, name=None, note="", charmap="cp437", unimap=None, path=None) -> FontFile:
    """A table of n glyphs of h bytes at ``offset`` (e.g. the kernel's ``fontdata_8x16`` inside vmlinux).
    ``data`` is bytes or a path.  Glyph index -> code points: ``unimap`` (a list of code-point tuples per glyph, e.g. the kernel's
    default unicode map from ``kernel_unimap``: it makes glyph 0x41 also draw U+00C0-U+00C3) or, without it, 'cp437'
    (indices 0-31 are the CP437 symbols)."""
    if isinstance(data, (bytes, bytearray)):
        raw, path = bytes(data), path or "<bytes>"
    else:
        raw, path = open(data, "rb").read(), data
    glyphs = []
    for i in range(n):
        bm = raw[offset + i * h:offset + (i + 1) * h]
        cps = list(unimap[i]) if unimap is not None else ([ord(CP437[i])] if charmap == "cp437" else [i])
        glyphs.append((cps, list(bm), w, h, 0, 0))
    return FontFile(name or "raw-%x" % offset, path, _sha(raw), w, h, glyphs,
                    note=note or "raw %d-glyph table at 0x%x of %s" % (n, offset, os.path.basename(path)))


def kernel_unimap(blob: bytes):
    """The Linux console's DEFAULT unicode map (``dfont_unicount`` + ``dfont_unitable`` in drivers/tty/vt/consolemap.c) parsed
    out of a decompressed kernel: list of 256 tuples of code points.  Needed because the kernel draws several code points with the
    same glyph (A <- A-grave A-acute A-circumflex A-tilde, C <- copyright sign, K <- Kelvin sign, '-' <- soft hyphen ...)."""
    for m in re.finditer(rb"[\x01-\x0c]{256}", blob):
        o = m.start()
        counts = blob[o:o + 256]
        if counts[0x41] < 3:
            continue
        total = sum(counts)
        for t in range(max(0, o - 65536), o + 65536, 2):
            if t + 2 * total > len(blob):
                break
            tab = struct.unpack_from("<%dH" % total, blob, t)
            pos, ents = 0, []
            for c in counts:
                ents.append(tuple(tab[pos:pos + c]))
                pos += c
            if 0x41 in ents[0x41] and 0x20 in ents[0x20] and 0x61 in ents[0x61] and 0x2500 in ents[0xC4]:
                return ents
    raise ValueError("kernel default unicode map not found")


# ----------------------------------------------------------------------------- GRUB PF2
def load_pf2(path) -> FontFile:
    data = open(path, "rb").read()
    if data[:4] != b"FILE":
        raise ValueError("not a PF2 font: %s" % path)
    pos = 12
    meta, chix, dat = {}, None, None
    while pos + 8 <= len(data):
        name = data[pos:pos + 4]
        ln = struct.unpack_from(">I", data, pos + 4)[0]
        body = pos + 8
        if name == b"CHIX":
            chix = data[body:body + ln]
        elif name == b"DATA":
            dat = body
            break
        else:
            meta[name.decode()] = data[body:body + ln]
        pos = body + ln
    asc = struct.unpack(">H", meta["ASCE"])[0]
    desc = struct.unpack(">H", meta["DESC"])[0]
    glyphs = []
    for i in range(0, len(chix), 9):
        cp, _flags, off = struct.unpack_from(">IBI", chix, i)
        w, h, xo, yo, dw = struct.unpack_from(">HHhhh", data, off)
        bits = data[off + 10:off + 10 + (w * h + 7) // 8]
        nbits = int.from_bytes(bits, "big")
        total = len(bits) * 8
        rows = []
        for y in range(h):
            r = 0
            for x in range(w):
                r = (r << 1) | ((nbits >> (total - 1 - (y * w + x))) & 1)
            rows.append(r)
        if cp < 0x110000:  # GRUB sets flag bits above the Unicode range for some entries: not code points
            glyphs.append(([cp], rows, w, h, xo, yo, dw))
    cw = max((g[6] for g in glyphs if g[0][0] in range(0x21, 0x7f)), default=8)
    return FontFile(os.path.basename(path), path, _sha(data), cw, asc + desc,
                    [g[:6] for g in glyphs], ascent=asc, descent=desc,
                    note="PF2 name=%r family=%r asc=%d desc=%d" % (meta.get("NAME", b"").rstrip(b"\0"), meta.get("FAMI", b"").rstrip(b"\0"), asc, desc))


# ----------------------------------------------------------------------------- Unifont .hex / BDF
def load_hex(path) -> FontFile:
    raw = open(path, "rb").read()
    glyphs = []
    for line in raw.decode("ascii").splitlines():
        m = re.match(r"([0-9A-Fa-f]{4,6}):([0-9A-Fa-f]+)$", line.strip())
        if not m:
            continue
        cp, hexd = int(m.group(1), 16), m.group(2)
        w = 8 if len(hexd) == 32 else 16
        b = bytes.fromhex(hexd)
        rb = w // 8
        glyphs.append(([cp], [int.from_bytes(b[y * rb:(y + 1) * rb], "big") for y in range(16)], w, 16, 0, 0))
    return FontFile(os.path.basename(path), path, _sha(raw), 8, 16, glyphs)


def load_bdf(path) -> FontFile:
    raw = open(path, "rb").read()
    text = raw.decode("latin-1")
    glyphs, ascent, descent = [], 0, 0
    fbb = None
    m = re.search(r"FONTBOUNDINGBOX (\d+) (\d+) (-?\d+) (-?\d+)", text)
    if m:
        fbb = tuple(map(int, m.groups()))
    m = re.search(r"FONT_ASCENT (\d+)", text)
    ascent = int(m.group(1)) if m else (fbb[1] + fbb[3] if fbb else 0)
    m = re.search(r"FONT_DESCENT (\d+)", text)
    descent = int(m.group(1)) if m else (-fbb[3] if fbb else 0)
    for blk in re.finditer(r"STARTCHAR.*?ENDCHAR", text, re.S):
        b = blk.group(0)
        enc = int(re.search(r"ENCODING (-?\d+)", b).group(1))
        if enc < 0:
            continue
        w, h, xo, yo = map(int, re.search(r"BBX (-?\d+) (-?\d+) (-?\d+) (-?\d+)", b).groups())
        hexrows = re.search(r"BITMAP\n(.*?)\nENDCHAR", b, re.S).group(1).split()
        rb = (w + 7) // 8
        rows = [int(r.ljust(rb * 2, "0"), 16) >> (rb * 8 - w) for r in hexrows]
        glyphs.append(([enc], rows, w, h, xo, yo))
    cw = fbb[0] if fbb else 8
    return FontFile(os.path.basename(path), path, _sha(raw), cw, ascent + descent, glyphs, ascent=ascent, descent=descent)


# ----------------------------------------------------------------------------- rendering into cells
def render_cell(font: FontFile, g, cell_w=None, cell_h=None) -> bytes:
    """Pack one glyph into the cell bitmap format of ``glyphgrid.extract`` (row-major, MSB-left, fg=1)."""
    cps, rows, w, h, xo, yo = g[:6]
    cw, ch = cell_w or font.cell_w, cell_h or font.cell_h
    rb = (cw + 7) // 8
    cell = [0] * ch
    if font.ascent is None:  # fixed-cell fonts (PSF/hex/raw): glyph fills its own cell, left aligned
        for y in range(min(h, ch)):
            v = rows[y]
            v = v << (cw - w) if w <= cw else v >> (w - cw)
            cell[y] = v & ((1 << cw) - 1)
    else:  # PF2/BDF: bitmap placed by xoff / baseline
        top = font.ascent - (yo + h)
        for y in range(h):
            yy = top + y
            if 0 <= yy < ch:
                sh = cw - w - xo
                v = rows[y] << sh if sh >= 0 else rows[y] >> -sh
                cell[yy] = v & ((1 << cw) - 1)
    out = bytearray()
    for v in cell:
        out += (v << (rb * 8 - cw)).to_bytes(rb, "big")
    return bytes(out)


# repertoire: the characters the consumer expects to read.  Aliases are computed INSIDE it.
REPERTOIRES = {
    "ascii": lambda cp: 0x21 <= cp <= 0x7E,
    "ascii+box": lambda cp: 0x21 <= cp <= 0x7E or 0x2500 <= cp <= 0x259F or cp in (0x25B2, 0x25BC, 0x2191, 0x2193),
    "latin1": lambda cp: 0x21 <= cp <= 0x7E or 0xA1 <= cp <= 0xFF and cp != 0xAD,
}


def build_font_atlas(atlas: "gg.Atlas", fontname: str, font: FontFile, repertoire="ascii+box", cell_w=None, cell_h=None, provenance=None):
    """Add ``font`` to ``atlas`` as font-exact entries.

    ``"c"`` = characters of the declared repertoire drawn by this bitmap (more than one = alias set); ``"x"`` = every OTHER code point of the
    font's FULL coverage that draws the same bitmap (homoglyphs outside the repertoire: Cyrillic/Greek capitals, accented Latin via the kernel
    unimap, soft hyphen ...).  A bitmap identifies a GLYPH, not a code point.  -> info dict."""
    keep = REPERTOIRES[repertoire]
    cw, ch = cell_w or font.cell_w, cell_h or font.cell_h
    f = atlas.fonts.setdefault(fontname, {"cell_w": cw, "cell_h": ch, "glyphs": {}})
    f["origin"] = {"kind": "font-file", "file": os.path.basename(font.path), "font": font.name, "sha256": font.sha256,
                   "repertoire": repertoire, "note": font.note, **(provenance or {})}
    per = {}
    for g in font.glyphs:
        key = render_cell(font, g, cw, ch)
        ent = per.setdefault(key, [set(), set()])
        for cp in g[0]:
            ent[0 if keep(cp) else 1].add(cp)
    aliases, homoglyphs = {}, {}
    for key, (chars, extra) in per.items():
        if not any(key) or not chars:
            continue
        ent = {"c": {chr(c): 1 for c in sorted(chars)}, "f": ["font-file:" + font.sha256[:12]], "font_exact": True}
        if extra:
            ent["x"] = sorted(extra)
            homoglyphs[key.hex()] = sorted(extra)
        f["glyphs"][key.hex()] = ent
        if len(chars) > 1:
            aliases[key.hex()] = sorted(chr(c) for c in chars)
    return {"glyphs": len(f["glyphs"]), "alias_sets": aliases, "homoglyph_sets": homoglyphs}


# ----------------------------------------------------------------------------- firmware / kernel sources
def decompress_bzimage(path) -> bytes:
    """x86 bzImage -> the decompressed kernel (zstd/gzip/xz payload).  Stdlib only (Python >= 3.14 for zstd)."""
    import lzma
    import zlib
    d = open(path, "rb").read()
    ss = d[0x1F1] or 4
    po, pl = struct.unpack_from("<II", d, 0x248)
    pay = d[(ss + 1) * 512 + po:(ss + 1) * 512 + po + pl]
    if pay[:4] == b"\x28\xb5\x2f\xfd":
        from compression import zstd
        return zstd.ZstdDecompressor().decompress(pay)
    if pay[:2] == b"\x1f\x8b":
        return zlib.decompress(pay, 31)
    if pay[:6] == b"\xfd7zXZ\x00":
        return lzma.decompress(pay)
    raise ValueError("unsupported bzImage payload compression")


VGA_A = bytes.fromhex("000010386cc6c6fec6c6c6c600000000")      # IBM VGA 8x16 'A'
VGA_0 = bytes.fromhex("0000386cc6c6d6d6c6c66c3800000000")      # IBM VGA 8x16 '0'


def find_vga_table(blob: bytes) -> int:
    """Offset of the 256x16-byte IBM VGA font table (the kernel's ``font_vga_8x16`` data) in a decompressed kernel."""
    i = -1
    while True:
        i = blob.find(VGA_A, i + 1)
        if i < 0:
            raise ValueError("VGA 8x16 font table not found")
        base = i - 0x41 * 16
        if base >= 0 and blob[base + 0x30 * 16:base + 0x31 * 16] == VGA_0:
            return base


def load_edk2_narrow(blob: bytes, source_path: str, source_sha256: str, where: str) -> FontFile:
    """EDK2/TianoCore ``EFI_NARROW_GLYPH`` table (the UEFI console font, 8x19): entries of
    UINT16 UnicodeWeight, UINT8 Attributes, UINT8 GlyphCol1[19] (one byte per ROW, MSB left), ascending by weight."""
    start = -1
    key = b"\x20\x00\x00" + b"\x00" * 19 + b"\x21\x00"
    i = blob.find(key)
    while i >= 0:
        # confirm a few more consecutive printable weights
        if all(struct.unpack_from("<H", blob, i + 22 * k)[0] == 0x20 + k for k in range(8)):
            start = i
            break
        i = blob.find(key, i + 1)
    if start < 0:
        raise ValueError("EFI_NARROW_GLYPH table not found")
    glyphs, pos, prev = [], start, -1
    while pos + 22 <= len(blob):
        w, attr = struct.unpack_from("<HB", blob, pos)
        if w <= prev or w == 0:
            break
        glyphs.append(([w], list(blob[pos + 3:pos + 22]), 8, 19, 0, 0))
        prev, pos = w, pos + 22
    return FontFile("edk2-narrow-glyph", source_path, source_sha256, 8, 19, glyphs,
                    note="EFI_NARROW_GLYPH table, %d entries, found at 0x%x of %s" % (len(glyphs), start, where))


def ovmf_decompressed_volumes(path):
    """Yield (offset, bytes) of LZMA-compressed firmware volumes inside an OVMF image (stdlib scan)."""
    import lzma
    data = open(path, "rb").read()
    for m in re.finditer(b"\x5d\x00\x00", data):
        try:
            out = lzma.LZMADecompressor(format=lzma.FORMAT_ALONE).decompress(data[m.start():m.start() + (16 << 20)], max_length=64 << 20)
        except Exception:
            continue
        if len(out) > 65536:
            yield m.start(), out
