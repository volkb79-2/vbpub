"""Stdlib PNG decoding and the blank/ink cross-check of a decoded VGA text buffer against the screendump.

The check proves that the cells the buffer says are blank are blank on screen and the cells it says have a visible glyph
carry more than one colour.  That catches a shifted/stale text page and a graphical mode of the same pixel size.  It does
NOT prove which glyph is drawn (that rests on the default-cp437-font assumption, see sources.ASSUMPTIONS).
"""
from __future__ import annotations

import struct
import zlib
from typing import Any

_BPP = {0: 1, 2: 3, 4: 2, 6: 4}
BLANK_CODES = (0x00, 0x20, 0xFF)
UNIFORM_GLYPHS = (0xDB,)  # full block: a uniform cell although the glyph is not blank


def decode_png(data: bytes) -> tuple[int, int, int, list[bytearray]]:
    """-> (width, height, bytes_per_pixel, rows).  8-bit non-interlaced gray/gray+alpha/RGB/RGBA only (QEMU writes RGB)."""
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("not a PNG")
    pos, idat, hdr = 8, [], None
    while pos + 8 <= len(data):
        n, tag = struct.unpack(">I4s", data[pos:pos + 8])
        body = data[pos + 8:pos + 8 + n]
        if tag == b"IHDR":
            hdr = struct.unpack(">IIBBBBB", body)
        elif tag == b"IDAT":
            idat.append(body)
        elif tag == b"IEND":
            break
        pos += 12 + n
    if hdr is None:
        raise ValueError("PNG without IHDR")
    w, h, depth, ctype, _, _, interlace = hdr
    if depth != 8 or interlace or ctype not in _BPP:
        raise ValueError(f"unsupported PNG (bit depth {depth}, colour type {ctype}, interlace {interlace})")
    bpp = _BPP[ctype]
    stride = w * bpp
    raw = zlib.decompress(b"".join(idat))
    if len(raw) < h * (stride + 1):
        raise ValueError("truncated PNG pixel data")
    rows: list[bytearray] = []
    prev = bytearray(stride)
    for y in range(h):
        f = raw[y * (stride + 1)]
        line = bytearray(raw[y * (stride + 1) + 1:(y + 1) * (stride + 1)])
        if f == 1:
            for i in range(bpp, stride):
                line[i] = (line[i] + line[i - bpp]) & 255
        elif f == 2:
            for i in range(stride):
                line[i] = (line[i] + prev[i]) & 255
        elif f == 3:
            for i in range(stride):
                left = line[i - bpp] if i >= bpp else 0
                line[i] = (line[i] + ((left + prev[i]) >> 1)) & 255
        elif f == 4:
            for i in range(stride):
                a = line[i - bpp] if i >= bpp else 0
                c = prev[i - bpp] if i >= bpp else 0
                b = prev[i]
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                line[i] = (line[i] + (a if pa <= pb and pa <= pc else b if pb <= pc else c)) & 255
        elif f != 0:
            raise ValueError(f"bad PNG filter {f}")
        rows.append(line)
        prev = line
    return w, h, bpp, rows


def cell_has_ink(rows: list[bytearray], bpp: int, x0: int, y0: int, cw: int, ch: int) -> bool:
    """True when the cell's pixels are not all one colour."""
    first = bytes(rows[y0][x0 * bpp:(x0 + 1) * bpp])
    want = first * cw
    return any(bytes(rows[y][x0 * bpp:(x0 + cw) * bpp]) != want for y in range(y0, y0 + ch))


def cursor_shaped(rows: list[bytearray], bpp: int, x0: int, y0: int, cw: int, ch: int) -> bool:
    """Is the cell's non-background ink exactly one filled rectangle on the LAST TWO scanlines (14-15 of 16, 12-13 of 14, 6-7 of 8:
    the standard underline cursor) spanning the cell width (or width - 1 in a 9-dot cell)?  Background = the cell's top-left pixel.
    The cursor colour is the attribute's fg, but the DAC palette is not readable through QMP, so colour is not checked.  Glyphs
    such as ``_`` (one scanline), the lower-half block (eight) or a thicker bar do not match; a two-scanline bar still cannot be
    told from the cursor, which is why the cell is reported as UNCERTAIN, never as verified."""
    bg = bytes(rows[y0][x0 * bpp:(x0 + 1) * bpp])
    xs: list[int] = []
    ys: list[int] = []
    count = 0
    for y in range(y0, y0 + ch):
        for x in range(x0, x0 + cw):
            if bytes(rows[y][x * bpp:(x + 1) * bpp]) != bg:
                xs.append(x)
                ys.append(y)
                count += 1
    if not count:
        return False
    w, h = max(xs) - min(xs) + 1, max(ys) - min(ys) + 1
    return count == w * h and w >= cw - 1 and min(ys) - y0 == ch - 2 and max(ys) - y0 == ch - 1


def verify_ink(png: bytes, cols: int, nrows: int, cell_w: int, cell_h: int, buf: bytes, cursor: tuple[int, int] | None,
               blink_mode: bool) -> dict[str, Any]:
    """Compare predicted blank/ink per cell with the screendump.  ``ok`` is True only with zero mismatches over >= 1 compared cell,
    False on a mismatch, None when the PNG cannot be decoded (then the text is unverified, not wrong)."""
    out: dict[str, Any] = {"method": "ink-map", "ok": None}
    try:
        w, h, bpp, rows = decode_png(png)
    except (ValueError, zlib.error) as exc:
        out["reason"] = f"screendump not decodable: {exc}"
        return out
    if (w, h) != (cols * cell_w, nrows * cell_h):
        out.update(ok=False, reason=f"screendump {w}x{h} does not match {cols}x{nrows} cells of {cell_w}x{cell_h}")
        return out
    compared = skipped = 0
    mismatches: list[dict[str, Any]] = []
    for r in range(nrows):
        for c in range(cols):
            code, attr = buf[(r * cols + c) * 2], buf[(r * cols + c) * 2 + 1]
            fg, bg = attr & 0x0F, (attr >> 4) & (0x07 if blink_mode else 0x0F)
            if code in UNIFORM_GLYPHS or (blink_mode and attr & 0x80) or (cursor is not None and (c, r) == tuple(cursor)):
                skipped += 1
                continue
            expected = code not in BLANK_CODES and fg != bg
            seen = cell_has_ink(rows, bpp, c * cell_w, r * cell_h, cell_w, cell_h)
            compared += 1
            if expected != seen:
                mismatches.append({"row": r, "col": c, "expected_ink": expected, "seen_ink": seen})
    # Linux moves the cursor through the CRTC without touching the BIOS data area, and QEMU draws it: ONE cell that is blank in the
    # buffer but carries a cursor-shaped block is most likely that stale cursor, not a wrong page: it is NOT counted as verified
    # (uncertain_cells), the rest of the screen is.  Anything else stays strict.
    if len(mismatches) == 1 and not mismatches[0]["expected_ink"] and mismatches[0]["seen_ink"] and cursor_shaped(
            rows, bpp, mismatches[0]["col"] * cell_w, mismatches[0]["row"] * cell_h, cell_w, cell_h):
        out["uncertain_cells"] = [{"row": mismatches[0]["row"], "col": mismatches[0]["col"],
                                   "reason": "cursor-shaped ink (two bottom scanlines) in a cell the buffer says is blank: "
                                             "the stale cursor, or a glyph of that shape; the buffer character cannot be vouched for"}]
        mismatches = []
    out.update(compared=compared, skipped=skipped, mismatched=len(mismatches), mismatches=mismatches[:5], ok=not mismatches and compared > 0)
    if compared == 0:
        out["reason"] = "no comparable cells"
    elif mismatches:
        out["reason"] = f"{len(mismatches)} of {compared} cells disagree with the screendump (stale or shifted text page, or a graphical mode)"
    return out
