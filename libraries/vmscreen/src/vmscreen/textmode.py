"""Pure decoding of the VGA text buffer and BIOS-data-area mode detection (no QEMU needed)."""
from __future__ import annotations

import re
import struct
from dataclasses import dataclass, field
from typing import Any

COLORS = ("black", "blue", "green", "cyan", "red", "magenta", "brown", "light-gray",
          "dark-gray", "light-blue", "light-green", "light-cyan", "light-red", "light-magenta", "yellow", "white")

# VGA glyphs for the control range (the cp437 *codec* maps these to control characters, but a text-mode
# screen shows the graphic glyph); 0x00 and 0xFF render blank.
_LOW = " ☺☻♥♦♣♠•◘○◙♂♀♪♫☼►◄↕‼¶§▬↨↑↓→←∟↔▲▼"
_CP437 = [*_LOW] + [bytes([i]).decode("cp437") for i in range(0x20, 0x7F)] + ["⌂"] + \
         [bytes([i]).decode("cp437") for i in range(0x80, 0x100)]
_CP437[0xFF] = " "
assert len(_CP437) == 256

TEXT_BDA_MODES = (0, 1, 2, 3, 7)
BDA_BASE, BDA_SIZE = 0x400, 0xA0  # BIOS data area 0x400..0x49F


@dataclass(frozen=True)
class Cell:
    char: str
    fg: str
    bg: str
    blink: bool

    def as_dict(self) -> dict[str, Any]:
        return {"c": self.char, "fg": self.fg, "bg": self.bg, "blink": self.blink}


@dataclass
class TextScreen:
    cols: int
    rows: int
    cells: list[list[Cell]] = field(repr=False)

    @property
    def lines(self) -> list[str]:
        return ["".join(c.char for c in row).rstrip() for row in self.cells]

    def text(self) -> str:
        lines = self.lines
        while lines and not lines[-1]:
            lines.pop()
        return "\n".join(lines)


def decode_attribute(attr: int, blink_mode: bool = True) -> tuple[str, str, bool]:
    """Attribute byte -> (fg, bg, blink).  Bit 7 is blink when the attribute controller is in blink mode
    (BIOS default), otherwise it is the high bit of a 16-colour background."""
    fg = COLORS[attr & 0x0F]
    if blink_mode:
        return fg, COLORS[(attr >> 4) & 0x07], bool(attr & 0x80)
    return fg, COLORS[(attr >> 4) & 0x0F], False


def decode_buffer(data: bytes, cols: int, rows: int, blink_mode: bool = True) -> TextScreen:
    need = cols * rows * 2
    if cols <= 0 or rows <= 0 or len(data) < need:
        raise ValueError(f"text buffer has {len(data)} bytes, {cols}x{rows} needs {need}")
    grid = []
    for r in range(rows):
        row = []
        for c in range(cols):
            off = (r * cols + c) * 2
            fg, bg, blink = decode_attribute(data[off + 1], blink_mode)
            row.append(Cell(_CP437[data[off]], fg, bg, blink))
        grid.append(row)
    return TextScreen(cols, rows, grid)


def png_size(png: bytes) -> tuple[int, int]:
    if png[:8] != b"\x89PNG\r\n\x1a\n" or png[12:16] != b"IHDR":
        raise ValueError("not a PNG")
    return struct.unpack(">II", png[16:24])


@dataclass
class Detection:
    ok: bool
    reason: str
    cols: int = 0
    rows: int = 0
    base: int = 0xB8000
    size: int = 0
    cell_w: int = 0
    cell_h: int = 0
    cursor: tuple[int, int] | None = None
    verified_by_screendump: bool = False

    def as_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


def detect(bda: bytes, screen_size: tuple[int, int] | None) -> Detection:
    """Decide whether the guest is in a VGA text mode we can read at 0xb8000.

    The BIOS data area (0x449 mode, 0x44A columns, 0x484 rows-1, 0x485 char height, 0x44E page offset,
    0x450 cursor) is written by the video BIOS on every mode set.  It is STALE after a guest driver
    programs the VGA registers directly (Linux fbcon/KMS, UEFI GOP), so it is cross-checked with the
    screendump size: a text screen is cols*(8|9) x rows*char_height pixels.
    """
    if len(bda) < BDA_SIZE:
        return Detection(False, f"BIOS data area short read ({len(bda)} bytes)")
    mode = bda[0x49]
    if mode not in TEXT_BDA_MODES:
        return Detection(False, f"BIOS data area video mode {mode:#x} is not a text mode")
    cols = struct.unpack_from("<H", bda, 0x4A)[0]
    rows = bda[0x84] + 1
    cell_h = struct.unpack_from("<H", bda, 0x85)[0]
    page_off = struct.unpack_from("<H", bda, 0x4E)[0]
    if not (80 <= cols <= 255 and 1 <= rows <= 100 and cell_h in (8, 14, 16)):
        return Detection(False, f"unsupported or implausible BIOS text geometry (only 80+ column modes are read; 40-column modes are rejected) {cols}x{rows}, char height {cell_h}")
    window_end = 0xB8000 if mode == 7 else 0xC0000
    base = (0xB0000 if mode == 7 else 0xB8000) + page_off
    size = cols * rows * 2
    if base + size > window_end:
        return Detection(False, f"text page at {base:#x}+{size} leaves the VGA window")
    cur = (bda[0x50], bda[0x51])
    cell_w = 0
    verified = screen_size is not None
    if screen_size is not None:
        w, h = screen_size
        widths = [x for x in (8, 9) if cols * x == w]
        if not widths or h != rows * cell_h:
            return Detection(False, f"screendump is {w}x{h}, not a {cols}x{rows} text screen "
                                    f"({cols * 9}|{cols * 8} x {rows * cell_h}): the guest is in a graphical mode "
                                    "and the BIOS data area is stale", cols, rows)
        cell_w = widths[-1]
    return Detection(True, "text mode confirmed" + (" by screendump size" if verified else " (screendump size not checked)"),
                     cols, rows, base, size, cell_w, cell_h, cur, verified)


_XP_LINE = re.compile(r"^[0-9a-fA-F]+:((?:\s+0x[0-9a-fA-F]{2})+)\s*$")


def parse_xp(output: str) -> bytes:
    """Parse HMP ``xp /Nbx ADDR`` output into bytes (fallback when the QMP file path is not shared)."""
    out = bytearray()
    for line in output.replace("\r", "").split("\n"):
        m = _XP_LINE.match(line.strip())
        if m:
            out += bytes(int(t, 16) for t in m.group(1).split())
    return bytes(out)
