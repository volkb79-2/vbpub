"""Screen -> image, transform chain, PNG bytes + exact ground truth."""
import hashlib
import io
import json
import os
import re

from PIL import Image

from . import FORMAT
from .fonts import load_font
from .screens import build
from .transforms import apply_chain


def env():
    """Versions the PNG/JPEG bytes depend on (Pillow and its zlib/libjpeg): same bytes are guaranteed only within one environment."""
    from PIL import __version__, features
    return {"pillow": __version__, "zlib": features.version("zlib"), "libjpeg": features.version("jpg")}


def hexcol(c):
    return "#%02x%02x%02x" % tuple(c)


def render_screen(font, screen):
    cw, ch = font.cell_w, font.cell_h
    im = Image.new("RGB", (screen.cols * cw, screen.rows * ch))
    memo = {}
    for r, row in enumerate(screen.cells):
        for c, cell in enumerate(row):
            mask = font.draw(cell.ch)
            if screen.cursor == (r, c):
                mask = mask.copy()
                mask.paste(255, (0, ch - 3, cw, ch - 1))  # underline cursor, 2 scan lines, in the cell's fg
            k = (cell.ch, cell.fg, cell.bg, screen.cursor == (r, c))
            tile = memo.get(k)
            if tile is None:
                tile = memo[k] = Image.composite(Image.new("RGB", (cw, ch), cell.fg), Image.new("RGB", (cw, ch), cell.bg), mask)
            im.paste(tile, (c * cw, r * ch))
    return im


def label(specs):
    if not specs:
        return "clean"
    return "+".join(re.sub(r"[^A-Za-z0-9.\-]+", "_", s.replace(":", "-")) for s in specs)


def png_bytes(im):
    b = io.BytesIO()
    im.save(b, "PNG", compress_level=6)  # no metadata, no timestamps: byte-deterministic
    return b.getvalue()


def truth_for(font, screen, specs, seed, geo, log, base_size, size, png):
    cells = []
    for r, row in enumerate(screen.cells):
        cells.append([[c.ch, hexcol(c.fg), hexcol(c.bg), c.flags + ("C" if screen.cursor == (r, i) else "")] for i, c in enumerate(row)])
    return {
        "format": FORMAT, "env": env(), "seed": seed, "label": label(specs), "font": font.describe(),
        "screen": {"name": screen.name, "cols": screen.cols, "rows": screen.rows,
                   "cursor": list(screen.cursor) if screen.cursor else None, "text": screen.text_rows(), "cells": cells},
        "transforms": log, "geometry": geo,
        "image": {"base_size": list(base_size), "size": list(size), "png_sha256": hashlib.sha256(png).hexdigest()},
    }


def generate(font, screen_name, specs=(), seed=0):
    """font: id or CellFont.  -> (png_bytes, truth dict).  Same arguments -> same bytes."""
    if isinstance(font, str):
        font = load_font(font)
    screen = build(screen_name, font, seed)
    base = render_screen(font, screen)
    im, geo, log = apply_chain(base, list(specs), seed, fill=screen.bg)
    png = png_bytes(im)
    return png, truth_for(font, screen, list(specs), seed, geo, log, base.size, im.size, png)


def stem(font_id, screen_name, specs, seed):
    return "%s__%s__%s__s%s" % (re.sub(r"[^A-Za-z0-9.\-]+", "_", font_id), screen_name, label(specs), seed)


def write(outdir, font, screen_name, specs=(), seed=0):
    os.makedirs(outdir, exist_ok=True)
    png, truth = generate(font, screen_name, specs, seed)
    base = os.path.join(outdir, stem(truth["font"]["id"], truth["screen"]["name"], specs, seed))
    with open(base + ".png", "wb") as f:
        f.write(png)
    with open(base + ".json", "w", encoding="utf-8") as f:
        json.dump(truth, f, ensure_ascii=False, indent=None, separators=(",", ":"), sort_keys=True)
    return base
