"""Learn a glyphgrid atlas FROM GENERATOR OUTPUT (generated clean pages + their exact truth), not from the font files.

Every page is one source = one group; a bitmap reads 'exact' only when >= 2 groups confirm it, so each font is rendered as an
alphabet page in two colour schemes plus the installer and two random pages (the same glyph in 2+ separately named pages).
These groups come from one generator, so they are NOT independent evidence in the glyphgrid-doctrine sense: the atlas is a
measurement fixture, never a keystroke-grade one.  Bitmaps shared by several characters become atlas conflicts (UNKNOWN).
"""
from . import _PARENT  # noqa: F401
import glyphgrid as gg  # noqa: E402

from .fonts import load_font
from .render import render_screen
from .screens import alphabet, installer, random_text


def learn_pages(atlas, font):
    pages = [("alphabet:" + s, alphabet(font, s)) for s in ("grey-on-black", "white-on-blue", "black-on-grey")]
    pages += [("installer", installer(font)), ("random:101", random_text(font, 101)), ("random:102", random_text(font, 102))]
    name = "gen:" + font.id
    for src, screen in pages:
        im = render_screen(font, screen)
        grid = {"cell_w": font.cell_w, "cell_h": font.cell_h, "origin_x": 0, "origin_y": 0, "cols": screen.cols, "rows": screen.rows, "status": "ok"}
        ex = gg.extract(im, grid)
        for r, row in enumerate(ex.rows):
            for c, cell in enumerate(row):
                ch = screen.cells[r][c].ch
                if cell.kind == "glyph" and ch != " " and screen.cursor != (r, c):
                    atlas.add(name, font.cell_w, font.cell_h, cell.key, ch, source=src)
    return len(pages)


def build_atlas(font_ids, atlas_path=None):
    atlas = gg.Atlas()
    for fid in font_ids:
        font = load_font(fid, atlas_path)
        if font.kind != "bitmap":
            raise ValueError("%s is a vector font: anti-aliased cells have no 1-bit atlas" % fid)
        learn_pages(atlas, font)
    return atlas
