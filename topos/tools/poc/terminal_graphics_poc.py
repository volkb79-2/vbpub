#!/usr/bin/env python3
"""Side-by-side terminal drawing PoC for the topos redesign (not product code).

Draws the same two series (CPU with a gap, PSI with a stall) in every candidate
technique so the operator can compare them in their real terminals:

    python3 terminal_graphics_poc.py            # text techniques + detected image protocols
    python3 terminal_graphics_poc.py --all      # also try every image protocol regardless
    python3 terminal_graphics_poc.py --live     # redraw the text panels at 1 Hz, report CPU/frame

Stdlib only, no Textual, so what you see is the terminal and not a framework.
Over tmux add --tmux-passthrough (needs `set -g allow-passthrough on`).
"""
from __future__ import annotations

import argparse
import base64
import math
import os
import select
import struct
import sys
import time
import zlib

Series = list[float | None]
WIDTH = 60
RED, AMBER, CYAN, DIM, RESET = "\x1b[38;5;203m", "\x1b[38;5;214m", "\x1b[38;5;80m", "\x1b[2m", "\x1b[0m"


def cpu_series(n: int, phase: float = 0.0) -> Series:
    out: Series = []
    for i in range(n):
        if n * 0.62 < i < n * 0.68:
            out.append(None)  # a real sampling gap: must look different from zero
            continue
        v = 35 + 25 * math.sin((i + phase) / 5.0) + 30 * math.exp(-(((i - n * 0.4) / 6.0) ** 2))
        out.append(max(0.0, min(100.0, v)))
    return out


def psi_series(n: int, phase: float = 0.0) -> Series:
    out: Series = []
    for i in range(n):
        spike = 70 * math.exp(-(((i - n * 0.75) / 4.0) ** 2))
        out.append(max(0.0, min(100.0, 2 + 2 * math.sin((i + phase) / 3.0) + spike)))
    return out


def colour(v: float | None) -> str:
    if v is None:
        return DIM
    return RED if v >= 70 else AMBER if v >= 40 else CYAN


def block_spark(s: Series) -> str:
    bars = "▁▂▃▄▅▆▇█"
    return "".join(
        f"{DIM}·{RESET}" if v is None else f"{colour(v)}{bars[min(7, int(v / 100 * 8))]}{RESET}" for v in s
    )


def _dots(s: Series, cell_w: int, cell_h: int, rows: int) -> list[list[list[bool]]]:
    """Rasterise series as a filled-under line into rows x cols cells of cell_w x cell_h dots."""
    cols = len(s) // cell_w
    grid = [[[False] * (cell_w * cell_h) for _ in range(cols)] for _ in range(rows)]
    total_h = rows * cell_h
    for x, v in enumerate(s[: cols * cell_w]):
        if v is None:
            continue
        top = total_h - 1 - min(total_h - 1, int(v / 100 * total_h))
        for y in range(top, total_h):
            grid[y // cell_h][x // cell_w][(y % cell_h) * cell_w + x % cell_w] = True
    return grid


def braille(s: Series, rows: int = 3) -> list[str]:
    # Braille dot bit by (row, col) in a 2x4 cell.
    bit = (0x01, 0x08, 0x02, 0x10, 0x04, 0x20, 0x40, 0x80)
    lines = []
    for row in _dots(s, 2, 4, rows):
        chars = []
        for cell in row:
            mask = sum(bit[i] for i, on in enumerate(cell) if on)
            chars.append(chr(0x2800 + mask))
        lines.append(CYAN + "".join(chars) + RESET)
    return lines


def half_block(s: Series, rows: int = 3) -> list[str]:
    # Each cell has 2 vertically stacked colour pixels: works anywhere with 256 colours.
    n = len(s)
    total = rows * 2
    lines = []
    for r in range(rows):
        cells = []
        for x in range(n):
            v = s[x]
            h = 0 if v is None else int(v / 100 * total)
            top_on = total - r * 2 - 1 < h
            bot_on = total - r * 2 - 2 < h
            code = 203 if (v or 0) >= 70 else 214 if (v or 0) >= 40 else 80
            fg = f"\x1b[38;5;{code if top_on else 235}m"
            bg = f"\x1b[48;5;{code if bot_on else 235}m"
            cells.append(f"{fg}{bg}▀")
        lines.append("".join(cells) + RESET)
    return lines


def quadrant(s: Series, rows: int = 3) -> list[str]:
    # 2x2 quadrant blocks (Unicode 1.1): most fonts have them, double the horizontal resolution.
    glyph = " ▘▝▀▖▌▞▛▗▚▐▜▄▙▟█"
    lines = []
    for row in _dots(s, 2, 2, rows):
        lines.append(AMBER + "".join(glyph[sum(1 << i for i, on in enumerate(c) if on)] for c in row) + RESET)
    return lines


def octant(s: Series, rows: int = 3) -> list[str]:
    # Vertical-eighths column graph: one column per cell, 8 levels per row. Universal fonts.
    bars = " ▁▂▃▄▅▆▇█"
    lines = []
    for r in range(rows):
        cells = []
        for v in s:
            if v is None:
                cells.append(f"{DIM}·{RESET}" if r == rows - 1 else " ")
                continue
            level = v / 100 * rows
            filled = level - (rows - 1 - r)
            cells.append(f"{colour(v)}{bars[max(0, min(8, int(filled * 8)))]}{RESET}")
        lines.append("".join(cells))
    return lines


def raster(s: Series, w: int, h: int) -> list[tuple[int, int, int]]:
    px = [(24, 26, 32)] * (w * h)
    n = len(s)
    for x in range(w):
        v = s[min(n - 1, x * n // w)]
        if v is None:
            for y in range(0, h, 4):
                px[y * w + x] = (90, 90, 90)
            continue
        top = h - 1 - int(v / 100 * (h - 1))
        col = (235, 90, 80) if v >= 70 else (240, 170, 60) if v >= 40 else (70, 200, 210)
        for y in range(top, h):
            px[y * w + x] = col
    return px


def png(px: list[tuple[int, int, int]], w: int, h: int) -> bytes:
    def chunk(t: bytes, d: bytes) -> bytes:
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)

    raw = b"".join(b"\0" + bytes(c for p in px[y * w : (y + 1) * w] for c in p) for y in range(h))
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw, 6))
        + chunk(b"IEND", b"")
    )


def wrap_tmux(seq: str, on: bool) -> str:
    return "\x1bPtmux;" + seq.replace("\x1b", "\x1b\x1b") + "\x1b\\" if on else seq


def kitty_image(px: list[tuple[int, int, int]], w: int, h: int, tmux: bool) -> str:
    data = base64.b64encode(png(px, w, h)).decode()
    parts, first = [], True
    while data:
        head, data = data[:4096], data[4096:]
        keys = f"a=T,f=100,q=2,m={1 if data else 0}" if first else f"m={1 if data else 0}"
        parts.append(wrap_tmux(f"\x1b_G{keys};{head}\x1b\\", tmux))
        first = False
    return "".join(parts) + "\n"


def iterm_image(px: list[tuple[int, int, int]], w: int, h: int, tmux: bool) -> str:
    b = png(px, w, h)
    seq = f"\x1b]1337;File=inline=1;size={len(b)};width={w}px;height={h}px;preserveAspectRatio=0:{base64.b64encode(b).decode()}\x07"
    return wrap_tmux(seq, tmux) + "\n"


def sixel_image(px: list[tuple[int, int, int]], w: int, h: int, tmux: bool) -> str:
    palette: dict[tuple[int, int, int], int] = {}
    for p in px:
        palette.setdefault(p, len(palette))
    out = ['\x1bPq"1;1;%d;%d' % (w, h)]
    out += [f"#{i};2;{r * 100 // 255};{g * 100 // 255};{b * 100 // 255}" for (r, g, b), i in palette.items()]
    for band in range(0, h, 6):
        rows = []
        for colour_rgb, idx in palette.items():
            line = []
            used = False
            for x in range(w):
                bits = 0
                for dy in range(6):
                    y = band + dy
                    if y < h and px[y * w + x] == colour_rgb:
                        bits |= 1 << dy
                used = used or bits != 0
                line.append(chr(63 + bits))
            if used:
                rows.append(f"#{idx}{''.join(line)}")
        out.append("$".join(rows) + "-")
    out.append("\x1b\\")
    return wrap_tmux("".join(out), tmux) + "\n"


def probe_sixel() -> bool:
    """Ask the terminal for DA1 attributes; 4 means sixel. Safe no-op when not a tty."""
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        return False
    import termios
    import tty

    fd = sys.stdin.fileno()
    saved = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        sys.stdout.write("\x1b[c")
        sys.stdout.flush()
        reply = b""
        while select.select([fd], [], [], 0.3)[0]:
            reply += os.read(fd, 64)
            if reply.endswith(b"c"):
                break
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, saved)
    return b";4;" in reply or reply.rstrip(b"c").endswith(b";4")


def detect() -> dict[str, bool]:
    env = os.environ
    prog = env.get("TERM_PROGRAM", "")
    return {
        "kitty": bool(env.get("KITTY_WINDOW_ID")) or env.get("TERM") == "xterm-kitty" or prog in ("ghostty", "WezTerm"),
        "iterm": prog in ("iTerm.app", "WezTerm", "vscode"),
        "sixel": probe_sixel(),
    }


def text_panels(cpu: Series, psi: Series) -> str:
    half = cpu[: WIDTH // 2]
    half_p = psi[-WIDTH // 2 :]
    lines = [
        "A  block sparkline (1 row, 8 levels, per-sample colour; gap shown as ·)",
        f"   cpu {block_spark(cpu)}",
        f"   psi {block_spark(psi)}",
        "",
        "B  eighth-block columns (3 rows, 24 levels)",
        *[f"   {x}" for x in octant(cpu)],
        "",
        "C  half-block pixels (2 colour pixels per cell, 6 px high)",
        *[f"   {x}" for x in half_block(psi)],
        "",
        "D  quadrant blocks (2x2 per cell)            E  braille (2x4 per cell)",
    ]
    for q, b in zip(quadrant(cpu), braille(cpu)):
        lines.append(f"   {q}{' ' * (45 - len(cpu) // 2 - 3)}{b}")
    lines += [
        "",
        "F  one-line stacked memory bar, meters with values",
        f"   MEM {RED}████{RESET}{AMBER}██████████{RESET}{CYAN}▒▒{RESET}{DIM}░░░░░░░░{RESET}  anon 2.1G file 4.0G zsw 0.3G avail 6.4G",
        f"   CPU {RED}▇{RESET}{AMBER}▅{RESET}{CYAN}▂{RESET}{AMBER}▆{RESET}  us32 sy9 io5 st2     PSI io {block_spark(half_p[-12:])} 1.1/0.9",
    ]
    _ = half
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--all", action="store_true", help="try every image protocol, detected or not")
    ap.add_argument("--live", action="store_true", help="redraw the text panels at 1 Hz and report CPU per frame")
    ap.add_argument("--tmux-passthrough", action="store_true", help="wrap image sequences for tmux passthrough")
    args = ap.parse_args()

    if args.live:
        phase = 0.0
        sys.stdout.write("\x1b[2J")
        try:
            while True:
                t0 = time.process_time()
                frame = text_panels(cpu_series(WIDTH, phase), psi_series(WIDTH, phase))
                sys.stdout.write("\x1b[H" + frame + f"\n\n{DIM}frame cpu {(time.process_time() - t0) * 1e3:.1f} ms, Ctrl-C to quit{RESET}\x1b[K\n")
                sys.stdout.flush()
                phase += 1.0
                time.sleep(1)
        except KeyboardInterrupt:
            return 0

    found = detect()
    env = os.environ
    print(f"TERM={env.get('TERM')} TERM_PROGRAM={env.get('TERM_PROGRAM')} tmux={'yes' if env.get('TMUX') else 'no'} ssh={'yes' if env.get('SSH_TTY') else 'no'} COLORTERM={env.get('COLORTERM')}")
    print(f"detected: {found}\n")
    cpu, psi = cpu_series(WIDTH), psi_series(WIDTH)
    print(text_panels(cpu, psi))
    w, h = 360, 54
    px = raster(psi_series(120), w, h)
    tmux = args.tmux_passthrough
    for name, fn in (("kitty", kitty_image), ("iterm", iterm_image), ("sixel", sixel_image)):
        print()
        if found[name] or args.all:
            print(f"G  {name} protocol, {w}x{h} px (blank or garbage here means this terminal cannot do it):")
            sys.stdout.write(fn(px, w, h, tmux))
        else:
            print(f"G  {name} protocol: not detected, skipped (use --all to force)")
    print("\nTell the operator-facing plan which of A-G look right here, and whether any glyphs are missing or misaligned.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
