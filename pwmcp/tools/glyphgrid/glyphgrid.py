"""glyphgrid: deterministic glyph-grid matching for fixed-font console screenshots.

Pure function image bytes -> grid result.  Python stdlib + Pillow only.

Pipeline
  1. ``probe_scale``  refuse resampled/blurred captures (exact matching is not possible there).
  2. ``detect_grid``  find cell size and origin without being told (or report no fixed grid).
  3. ``extract``      split into cells, normalise every cell to a 1-bit foreground bitmap
                      (works for light-on-dark and dark-on-light, reverse video included).
  4. ``decode``       exact dictionary lookup in an ``Atlas``.  UNKNOWN is never a guess:
                      nearest candidate + Hamming distance are evidence, not output.

Safety contract (three evidence classes, see README): a cell is TEXT only if it is font-exact ``F`` (character
from an authoritative font file, font identity proven by ``font_share``), learned-multi-source ``e`` or blank ``b``,
carries no flag (rule-line / cursor), is not an alias set and, for a homoglyph cell ``H``, passes the explicit homoglyph policy.
Everything else (``s`` single-source, ``u``, ``a``, ``x``, ``f`` fill, ``m``) is indeterminate.  ``max_hamming``
(tolerant matching) is OFF by default.  The scale probe cannot prove a capture is 1:1.
"""
from __future__ import annotations

import io
import json
import math
import time
import unicodedata
from collections import Counter

from PIL import Image, ImageChops

UNKNOWN_CHAR = "�"
FORMAT = "glyphgrid-atlas/1"

# confidence letters used in the per-row ``conf`` strings
CONF_EXACT, CONF_BLANK, CONF_UNKNOWN, CONF_NEAREST, CONF_CONFLICT, CONF_MULTI = "e", "b", "u", "n", "x", "m"
CONF_FONT = "F"     # exact, character taken from an authoritative FONT FILE (not from screenshots)
CONF_HOMO = "H"     # font-exact glyph whose repertoire character is unique but other code points draw the same bitmap
CONF_ALIAS = "a"    # font-exact bitmap shared by several characters of the repertoire: ambiguity set, no pick
CONF_SINGLE = "s"   # bitmap known, but confirmed by fewer than ``min_groups`` independent screens: NOT exact
CONF_FILL = "f"     # uniform cell whose colour differs from the text background next to it (solid block, fill)
TEXTLIKE = (CONF_EXACT, CONF_FONT, CONF_BLANK)
EXACTLIKE = (CONF_EXACT, CONF_FONT, CONF_HOMO)
HIT = ("exact", "font", "homoglyph", "alias")
CURSOR_CHARS = "\u2581\u2582\u2583\u2584\u2588"  # cursor-ambiguous shapes: never text unless the caller says where the cursor is
FONT_MIN_SHARE = 0.9   # share of ordinary ink cells a font-FILE font must read exactly before any cell may be font-exact
PROVEN_RUNNER_UP = 0.3  # ... and no other same-size font may explain more than this share
FILL_CHAR = "\u2588"
MAX_PIXELS = 8_000_000
RAMP_THRESHOLD = 0.04  # calibrated on the even-numbered corpus frames, tested on the odd ones (eval --probe)


# --------------------------------------------------------------------------- image loading
def load_image(src, max_pixels: int = MAX_PIXELS) -> Image.Image:
    if isinstance(src, Image.Image):
        im = src
    elif isinstance(src, (bytes, bytearray)):
        im = Image.open(io.BytesIO(bytes(src)))
    else:
        im = Image.open(src)
    if im.size[0] * im.size[1] > max_pixels:
        raise ValueError("image too large for glyphgrid: %dx%d = %d px > %d px limit (a console capture is "
                         "normally <= 1920x1200; pass max_pixels to raise)" % (im.size[0], im.size[1], im.size[0] * im.size[1], max_pixels))
    return im.convert("RGB")


# --------------------------------------------------------------------------- scale probe
def ramp_ratio(im: Image.Image) -> float:
    """Share of edge pixels that are a three-level step (left != pixel != right, left != right).

    A crisp bitmap (any palette size) has almost none: an edge is a jump between two colours.  Every
    resampling filter blends the two colours into intermediate values, which shows up as ramps.  Unlike a
    colour-count test this does not penalise a legitimately colour-rich 1:1 console.  Max of the
    horizontal and vertical measure.
    """
    W, H = im.size
    best = 0.0
    for horizontal in (True, False):
        if horizontal:
            a, b, c = im.crop((0, 0, W - 2, H)), im.crop((1, 0, W - 1, H)), im.crop((2, 0, W, H))
            size = (W - 2, H)
        else:
            a, b, c = im.crop((0, 0, W, H - 2)), im.crop((0, 1, W, H - 1)), im.crop((0, 2, W, H))
            size = (W, H - 2)
        mab = Image.frombytes("L", size, _mask_bytes(a, b))
        mbc = Image.frombytes("L", size, _mask_bytes(b, c))
        mac = Image.frombytes("L", size, _mask_bytes(a, c))
        # the middle pixel must lie between its neighbours in every channel (a blend), not merely differ
        hi, lo = ImageChops.lighter(a, c), ImageChops.darker(a, c)
        viol = ImageChops.lighter(ImageChops.subtract(b, hi), ImageChops.subtract(lo, b))
        r_, g_, b_ = viol.split()
        viol = ImageChops.lighter(ImageChops.lighter(r_, g_), b_).point(lambda v: 0 if v else 255)
        ramp = ImageChops.darker(ImageChops.darker(ImageChops.darker(mab, mbc), mac), viol).tobytes().count(255)
        edges = mab.tobytes().count(255)
        best = max(best, ramp / max(edges, 1))
    return best


def probe_scale(im: Image.Image, soft_threshold: float | None = None) -> dict:
    """Detect blur/resampling.  THIS CANNOT PROVE THE CAPTURE IS 1:1.

    It refuses images whose edges are blended (bilinear/bicubic/lanczos at any scale, JPEG-like noise) and
    undoes crisp integer nearest-neighbour upscales.  It does NOT catch crisp non-integer nearest scaling,
    integer upscales with an odd offset or letterbox, or a canvas that is simply a different size than the guest
    framebuffer; those are only contained by the font-consistency floor in ``decode``.  The capture path must
    deliver the intrinsic bitmap, and the consumer must verify canvas width/height against the guest
    framebuffer size independently.
    """
    thr = RAMP_THRESHOLD if soft_threshold is None else soft_threshold
    colors = sorted(im.getcolors(im.size[0] * im.size[1]) or [], reverse=True)
    total = im.size[0] * im.size[1]
    dominant = colors[0][0] if colors else total
    soft_ratio = (total - sum(n for n, _ in colors[:8])) / max(total - dominant, 1)  # evidence only
    ramp = ramp_ratio(im)
    up = _integer_upscale(im)
    resampled = ramp > thr and up == 1
    return {
        "n_colors": len(colors),
        "soft_ratio": round(soft_ratio, 5),
        "ramp_ratio": round(ramp, 5),
        "integer_upscale": up,
        "resampled": bool(resampled),
        "verdict": "resampled: exact matching unavailable" if resampled else "crisp (1:1 NOT proven)",
    }


def _integer_upscale(im: Image.Image) -> int:
    """Largest k in 2..8 such that im is exactly a nearest-neighbour k-fold upscale, else 1."""
    W, H = im.size
    for k in (8, 6, 5, 4, 3, 2):
        if W % k or H % k:
            continue
        small = im.resize((W // k, H // k), Image.NEAREST)
        if ImageChops.difference(small.resize((W, H), Image.NEAREST), im).getbbox() is None:
            return k
    return 1


def unscale(im: Image.Image, k: int) -> Image.Image:
    return im.resize((im.size[0] // k, im.size[1] // k), Image.NEAREST) if k > 1 else im


# --------------------------------------------------------------------------- grid detection
def _mask_bytes(a: Image.Image, b: Image.Image) -> bytes:
    d = ImageChops.difference(a, b)
    r, g, bl = d.split()
    return ImageChops.lighter(ImageChops.lighter(r, g), bl).point(lambda v: 255 if v else 0).tobytes()


def _remove_long_runs(mask: Image.Image, k: int, horizontal: bool) -> Image.Image:
    """Drop transition pixels that belong to straight runs >= k (box sides/edges, uniform fills)."""
    shifts = [(i, 0) if horizontal else (0, i) for i in range(k)]
    run = mask
    for dx, dy in shifts[1:]:
        run = ImageChops.darker(run, ImageChops.offset(mask, -dx, -dy))  # pixel starts a run of length k
    grown = run
    for dx, dy in shifts[1:]:
        grown = ImageChops.lighter(grown, ImageChops.offset(run, dx, dy))
    return ImageChops.subtract(mask, grown)


def _edge_profiles(im: Image.Image, cell_hint: int = 16):
    """Glyph-scale transition counts.

    ex[x] = rows where px(x)!=px(x+1); ey[y] = columns where px(y)!=px(y+1); straight runs longer
    than two cells (dialog edges, rules, fills) are removed: they sit exactly ON cell boundaries and
    would hide the text gaps that locate the row phase.
    """
    W, H = im.size
    mh = Image.frombytes("L", (W - 1, H), _mask_bytes(im.crop((0, 0, W - 1, H)), im.crop((1, 0, W, H))))
    mv = Image.frombytes("L", (W, H - 1), _mask_bytes(im.crop((0, 0, W, H - 1)), im.crop((0, 1, W, H))))
    mh = _remove_long_runs(mh, 2 * cell_hint + 1, False)
    mv = _remove_long_runs(mv, 2 * 8 + 1, True)
    d = mh.tobytes()
    ex = [d[x::W - 1].count(255) for x in range(W - 1)]
    d = mv.tobytes()
    ey = [d[y * W:(y + 1) * W].count(255) for y in range(H - 1)]
    return ex, ey


def _cap(e):
    nz = sorted(v for v in e if v)
    if not nz:
        return list(e)
    cap = 3 * nz[len(nz) // 2]
    return [min(v, cap) for v in e]


def _fold(e, p):
    s = [0.0] * p
    c = [0] * p
    for i, v in enumerate(e):
        s[i % p] += v
        c[i % p] += 1
    return [s[i] / c[i] if c[i] else 0.0 for i in range(p)], c


def _fold_score(e, p):
    n = len(e)
    mu = sum(e) / n
    var = sum((v - mu) ** 2 for v in e) / n
    if var == 0:
        return 0.0
    m, c = _fold(e, p)
    return sum(cc * (x - mu) ** 2 for x, cc in zip(m, c)) / n / var


def _row_pitch_candidates(ey):
    """Row pitches worth testing: the 3 best fold periods in 8..48 plus their divisors >= 8.

    The fold variance alone cannot separate a pitch from its multiples on sparse screens, so this is
    only a candidate generator; the compression test in ``detect_grid`` decides.
    """
    e = _cap(ey)
    sc = {p: _fold_score(e, p) for p in range(8, 41)}
    top = sorted(sc, key=lambda p: -sc[p])[:3]
    cands = set()
    for p in top:
        cands.add(p)
        cands.update(d for d in range(8, p) if p % d == 0)
    return sorted(cands), e


def _colstrips(imL, ph, oy, maxn):
    """Densest text-line strips of height ``ph`` as column-major byte strings (one slice per cell)."""
    W, H = imL.size
    res = []
    for r in range((H - oy) // ph):
        s = imL.crop((0, oy + r * ph, W, oy + (r + 1) * ph))
        c = s.getcolors(256)
        if c and len(c) > 1:
            res.append((sum(n for n, _ in c) - max(n for n, _ in c), s.transpose(Image.TRANSPOSE).tobytes()))
    res.sort(key=lambda t: -t[0])
    return [b for _, b in res[:maxn]], W


def _mdl(cs, p, ph, phase):
    """(L, D, T): description length per pixel of the non-blank cells under this tiling.

    D distinct non-blank cell bitmaps (dictionary cost D*p*ph bits) and T cell references
    (T*log2(D) bits).  A true glyph grid compresses (letters repeat); a picture does not.
    """
    strips, W = cs
    seen = set()
    tot = 0
    area = p * ph
    for cb in strips:
        for x in range(phase, W - p + 1, p):
            c = cb[x * ph:(x + p) * ph]
            if c.count(c[0]) == area:
                continue
            tot += 1
            seen.add(c)
    if not tot:
        return 9.0, 0, 0
    d = len(seen)
    return (d * area + tot * math.log2(max(d, 2))) / (tot * area), d, tot


def _multicolor_fraction(im, p, ph, ox, oy):
    """Share of non-blank cells (whole screen) holding 3+ colours under the tiling."""
    W, H = im.size
    n = bad = 0
    for r in range((H - oy) // ph):
        for c in range((W - ox) // p):
            cc = im.crop((ox + c * p, oy + r * ph, ox + (c + 1) * p, oy + (r + 1) * ph)).getcolors(p * ph)
            if len(cc) > 1:
                n += 1
                bad += len(cc) > 2
    return bad / n if n else 0.0


def detect_grid(im: Image.Image, min_cells: int = 12, max_length: float = 0.5, hints=None) -> dict:
    """Find (cell_w, cell_h, origin) automatically, or report 'no fixed grid found'.

    Candidate row pitches come from the vertical transition profile; for each, every column pitch
    6..16 and every phase is scored by minimum description length (``_mdl``): the tiling under which
    the non-blank cells repeat most wins, and it is accepted only if it compresses the screen
    (L <= max_length, i.e. cells genuinely repeat).  A photograph or logo gives L ~ 1 -> 'no fixed grid'.
    ``hints`` = [(cell_w, cell_h), ...] restricts the search (a consumer that knows its console size).
    Shifts that only move blank columns/rows tie exactly; they are all reported in ``candidates``
    and ``decode`` picks among them by atlas evidence.
    """
    W, H = im.size
    out = {"status": "no_grid", "reasons": [], "cell_w": None, "cell_h": None, "origin_x": None,
           "origin_y": None, "cols": 0, "rows": 0, "length": None, "contrast": None, "candidates": []}
    ex, ey = _edge_profiles(im)
    if sum(ey) == 0:
        out["reasons"].append("uniform image")
        return out
    if hints:
        pcands = sorted({h for _, h in hints})
        ecap = _cap(ey)
        pxs = sorted({w for w, _ in hints})
    else:
        pcands, ecap = _row_pitch_candidates(ey)
        pxs = list(range(6, 17))
    imL = im.convert("L")
    ycmap = {}
    results = {}  # (ph, px) -> best L over the sampled phases
    for ph in pcands:
        m, _ = _fold(ecap, ph)
        mn, mx = min(m), max(m)
        ycands = sorted(((b + 1) % ph) for b in range(ph) if m[b] <= mn + 0.12 * (mx - mn)) or [0]
        ycmap[ph] = ycands
        sample = sorted({ycands[0], ycands[len(ycands) // 2], ycands[-1]})
        for oy in sample:
            cs = _colstrips(imL, ph, oy, 8)
            if not cs[0]:
                continue
            for px in pxs:
                if hints and (px, ph) not in [tuple(h) for h in hints]:
                    continue
                for f in range(px):
                    L, d, t = _mdl(cs, px, ph, f)
                    if t >= min_cells and L < results.get((ph, px), (9,))[0]:
                        results[(ph, px)] = (L, f, oy)
    if not results:
        out["reasons"].append("too few non-blank cells for any tiling (< %d)" % min_cells)
        return out
    ranked = sorted(results.items(), key=lambda kv: kv[1][0])
    best_L = ranked[0][1][0]
    median = sorted(v[0] for v in results.values())[len(results) // 2]
    (ph_px, bv) = ranked[0]
    for (k2, v2) in ranked[1:]:
        # a sub-tiling of the true cell (8x8 halves of 8x16 glyphs) is trivially at least as
        # compressible: a tiling whose cell is an integer multiple of the winner and nearly as good
        # (within 10%) is the real cell
        if k2[0] % ph_px[0] == 0 and k2[1] % ph_px[1] == 0 and v2[0] <= 1.1 * bv[0] and (k2 != ph_px):
            ph_px, bv = k2, v2
    ph, px = ph_px
    out["length"] = round(bv[0], 4)
    out["contrast"] = round(bv[0] / median, 4)
    if bv[0] > max_length:
        out["reasons"].append("no fixed grid found (best tiling %dx%d: description length %.3f > %.2f)" % (px, ph, bv[0], max_length))
        return out
    # refine over EVERY origin of this pitch; keep all within 8% of the best, nearest to (0,0) first
    scored = {}
    for oy in ycmap[ph]:
        cs = _colstrips(imL, ph, oy, 14)
        if not cs[0]:
            continue
        for ox in range(px):
            scored[(ox, oy)] = _mdl(cs, px, ph, ox)[0]
    lo = min(scored.values())
    mxf, _ = _fold(_cap(ex), px)
    lo_x, hi_x = min(mxf), max(mxf)
    xcands = {(b + 1) % px for b in range(px) if mxf[b] <= lo_x + 0.25 * (hi_x - lo_x)}
    ties = [c for c, l in scored.items() if l <= lo * 1.08 + 1e-9 and c[0] in xcands] or \
           [c for c, l in scored.items() if l <= lo * 1.08 + 1e-9]
    ties = sorted(ties, key=lambda c: (min(c[0], px - c[0]) + min(c[1], ph - c[1]), scored[c], c))
    if (0, 0) not in ties:
        ties.append((0, 0))  # framebuffer consoles start at the screen origin: always a candidate
    ox, oy = ties[0]
    mc = _multicolor_fraction(im, px, ph, ox, oy)
    out["multicolor_fraction"] = round(mc, 4)
    if mc > 0.25:
        out["reasons"].append("no fixed grid found (%.0f%% of non-blank cells have 3+ colours: antialiased or graphical)" % (100 * mc))
        return out
    out.update({"status": "ok", "cell_w": px, "cell_h": ph, "origin_x": ox, "origin_y": oy,
                "cols": (W - ox) // px, "rows": (H - oy) // ph, "candidates": [list(c) for c in ties[:16]]})
    return out


def grid_with_origin(grid: dict, im_size, ox: int, oy: int) -> dict:
    g = dict(grid)
    g["origin_x"], g["origin_y"] = ox, oy
    g["cols"] = (im_size[0] - ox) // g["cell_w"]
    g["rows"] = (im_size[1] - oy) // g["cell_h"]
    return g


# --------------------------------------------------------------------------- cell extraction
class Cell:
    __slots__ = ("row", "col", "kind", "key", "bg", "fg", "ncolors")

    def __init__(self, row, col, kind, key, bg, fg, ncolors):
        self.row, self.col, self.kind, self.key, self.bg, self.fg, self.ncolors = row, col, kind, key, bg, fg, ncolors


def _pack(raw: bytes, w: int, h: int, fg: bytes) -> bytes:
    rb = (w + 7) // 8
    out = bytearray()
    for y in range(h):
        acc = 0
        base = y * w * 3
        for x in range(w):
            i = base + 3 * x
            acc = (acc << 1) | (1 if raw[i:i + 3] == fg else 0)
        out += (acc << (rb * 8 - w)).to_bytes(rb, "big")
    return bytes(out)


def normalise_cell(crop: Image.Image, rank: dict):
    """-> (kind, key, bg, fg, ncolors).  kind: blank | glyph | multicolor.

    Foreground is the minority colour of a two-colour cell (so light-on-dark, dark-on-grey and
    reverse video give the same bitmap).  Exact two-way ties use the frame-wide colour frequency
    (the more common colour is background).  Cells with 3+ colours are refused (``multicolor``).
    """
    w, h = crop.size
    cols = crop.getcolors(w * h)
    if len(cols) == 1:
        return "blank", b"", cols[0][1], None, 1
    if len(cols) > 2:
        cols.sort(reverse=True)
        return "multicolor", b"", cols[0][1], None, len(cols)
    (n1, c1), (n2, c2) = cols
    if n1 == n2:
        bgc, fgc = (c1, c2) if rank.get(c1, 0) >= rank.get(c2, 0) else (c2, c1)
    else:
        bgc, fgc = (c1, c2) if n1 > n2 else (c2, c1)
    return "glyph", _pack(crop.tobytes(), w, h, bytes(fgc)), bgc, fgc, 2


class Extraction:
    def __init__(self, grid, rows, size):
        self.grid, self.rows, self.size = grid, rows, size  # rows: list[list[Cell]]


def extract(im: Image.Image, grid: dict, row_indices=None) -> Extraction:
    """Cells of every row (or only ``row_indices``, used for fast phase search)."""
    w, h, ox, oy = grid["cell_w"], grid["cell_h"], grid["origin_x"], grid["origin_y"]
    rank = {c: n for n, c in (im.getcolors(im.size[0] * im.size[1]) or [])}
    memo = {}
    rows = []
    for r in (row_indices if row_indices is not None else range(grid["rows"])):
        row = []
        for c in range(grid["cols"]):
            x, y = ox + c * w, oy + r * h
            crop = im.crop((x, y, x + w, y + h))
            raw = crop.tobytes()
            v = memo.get(raw)
            if v is None:
                v = memo[raw] = normalise_cell(crop, rank)
            row.append(Cell(r, c, *v))
        rows.append(row)
    return Extraction(grid, rows, im.size)


# --------------------------------------------------------------------------- atlas
def _popcount(x: int) -> int:
    return x.bit_count() if hasattr(x, "bit_count") else bin(x).count("1")


def boxlike(key: bytes, w: int, h: int) -> bool:
    """Ink in the top/bottom pixel row or a row spanning the full width: box-drawing rules/corners and
    underscores/bars connect to their neighbours; ordinary text glyphs keep a margin."""
    rb = (w + 7) // 8
    rows = [int.from_bytes(key[y * rb:(y + 1) * rb], "big") >> (rb * 8 - w) for y in range(h)]
    left, right = 1 << (w - 1), 1
    if rows[0] or rows[-1] or any((r & left) and (r & right) for r in rows):
        return True
    return barlike(key, w, h)


def barlike(key: bytes, w: int, h: int) -> bool:
    """A horizontal bar of at most two pixel rows spanning (nearly) the cell: '_', '-', '=' style rules, cursors."""
    rb = (w + 7) // 8
    ink = [r for r in (int.from_bytes(key[y * rb:(y + 1) * rb], "big") >> (rb * 8 - w) for y in range(h)) if r]
    return bool(ink) and len(ink) <= 2 and max(bin(r).count("1") for r in ink) >= w - 2


class Atlas:
    """fonts[name] = {"cell_w","cell_h","glyphs": {hex: {"c": {char: count}, "f": [source,...]}}, "phases": [[ox,oy],...]}

    ``phases`` are the cell origins (mod cell size) at which the font was learned: extra candidates when reading.
    ``groups`` maps a source (frame id) to a screen group: near-duplicate frames share a group, so one
    screen photographed twice is ONE piece of evidence (see ``lookup(min_groups=)``)."""

    def __init__(self, fonts=None, groups=None):
        self.fonts = fonts or {}
        self.groups = groups or {}
        self.contradictions = []
        self._xcache = {}

    # persistence
    def to_json(self) -> str:
        d = {"format": FORMAT, "fonts": self.fonts}
        if self.groups:
            d["groups"] = self.groups
        return json.dumps(d, indent=1, sort_keys=True, ensure_ascii=False)

    @classmethod
    def from_json(cls, text: str) -> "Atlas":
        try:
            d = json.loads(text)
        except ValueError as e:
            raise ValueError("glyphgrid atlas is not valid JSON: %s" % e)
        if not isinstance(d, dict) or d.get("format") != FORMAT:
            raise ValueError("not a glyphgrid atlas (expected format %r, got %r)" % (FORMAT, d.get("format") if isinstance(d, dict) else type(d).__name__))
        fonts = d.get("fonts")
        if not isinstance(fonts, dict) or not fonts:
            raise ValueError("glyphgrid atlas: 'fonts' must be a non-empty object")
        for name, f in fonts.items():
            where = "font %r" % name
            if not isinstance(f, dict):
                raise ValueError("glyphgrid atlas: %s must be an object" % where)
            w, h = f.get("cell_w"), f.get("cell_h")
            if not (isinstance(w, int) and isinstance(h, int) and 1 <= w <= 64 and 1 <= h <= 128):
                raise ValueError("glyphgrid atlas: %s has invalid cell_w/cell_h %r/%r" % (where, w, h))
            glyphs = f.get("glyphs")
            if not isinstance(glyphs, dict):
                raise ValueError("glyphgrid atlas: %s 'glyphs' must be an object" % where)
            want = 2 * ((w + 7) // 8) * h
            origin = f.get("origin")
            if origin is not None and (not isinstance(origin, dict) or origin.get("kind") not in ("font-file",)):
                raise ValueError("glyphgrid atlas: %s has unknown origin.kind %r" % (where, (origin or {}).get("kind") if isinstance(origin, dict) else origin))
            for k, g in glyphs.items():
                if g.get("font_exact") is not None:
                    sha = (origin or {}).get("sha256", "")
                    if g["font_exact"] is not True or not (isinstance(sha, str) and len(sha) == 64 and all(c in "0123456789abcdef" for c in sha)):
                        raise ValueError("glyphgrid atlas: %s glyph %s is font_exact but the font has no origin.sha256 provenance" % (where, k))
                    if "x" in g and not (isinstance(g["x"], list) and all(isinstance(v, int) and 0 <= v < 0x110000 for v in g["x"])):
                        raise ValueError("glyphgrid atlas: %s glyph %s has an invalid homoglyph list 'x'" % (where, k))
                try:
                    int(k, 16)
                except ValueError:
                    raise ValueError("glyphgrid atlas: %s bitmap key %r is not hex" % (where, k))
                if len(k) != want:
                    raise ValueError("glyphgrid atlas: %s bitmap key %r has %d hex digits, a %dx%d cell needs %d" % (where, k, len(k), w, h, want))
                if not isinstance(g, dict) or not isinstance(g.get("c"), dict) or not g["c"] or \
                        not all(isinstance(ch, str) and isinstance(n, int) and n > 0 for ch, n in g["c"].items()):
                    raise ValueError("glyphgrid atlas: %s glyph %s needs a non-empty 'c' {char: count}" % (where, k))
            for ph in f.get("phases", []):
                if not (isinstance(ph, list) and len(ph) == 2 and all(isinstance(v, int) for v in ph)):
                    raise ValueError("glyphgrid atlas: %s has invalid phase %r" % (where, ph))
        groups = d.get("groups", {})
        if not isinstance(groups, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in groups.items()):
            raise ValueError("glyphgrid atlas: 'groups' must map source -> group name")
        return cls(fonts, groups)

    def save(self, path):
        with open(path, "w", encoding="utf-8") as f:
            f.write(self.to_json())

    @classmethod
    def load(cls, path) -> "Atlas":
        with open(path, encoding="utf-8") as f:
            return cls.from_json(f.read())

    # learning
    def add(self, font: str, cell_w: int, cell_h: int, key: bytes, char: str, source: str = "", phase=None):
        f = self.fonts.setdefault(font, {"cell_w": cell_w, "cell_h": cell_h, "glyphs": {}})
        if phase is not None:
            ph = [phase[0] % cell_w, phase[1] % cell_h]
            if ph not in f.setdefault("phases", []):
                f["phases"].append(ph)
        g = f["glyphs"].setdefault(key.hex(), {"c": {}, "f": []})
        g["c"][char] = g["c"].get(char, 0) + 1
        if source and source not in g["f"]:
            g["f"].append(source)

    def conflicts(self):
        return [(fn, k, g) for fn, f in self.fonts.items() for k, g in f["glyphs"].items()
                if len(g["c"]) > 1 and not g.get("font_exact")]

    # lookup
    @classmethod
    def merge(cls, *atlases: "Atlas") -> "Atlas":
        """Union of atlases with distinct font names, then CROSS-CHECK: a learned glyph that sits in a font-file font of the same cell size
        (the learned font shares >= 50% of its bitmaps with it, i.e. it is the same font) but with a different character is contradicted by the
        font file: it is demoted (reads as conflict) and listed in ``merged.contradictions``.  This would have caught the '-' / '_'
        transcript error automatically."""
        fonts, groups = {}, {}
        for a in atlases:
            for name, f in a.fonts.items():
                if name in fonts:
                    raise ValueError("font name %r occurs in two atlases" % name)
                fonts[name] = f
            groups.update(a.groups)
        out = cls(fonts, groups)
        out.contradictions = []
        ffiles = {n: f for n, f in fonts.items() if f.get("origin", {}).get("kind") == "font-file"}
        for ln, lf in fonts.items():
            if ln in ffiles:
                continue
            for fn, ff_ in ffiles.items():
                if (ff_["cell_w"], ff_["cell_h"]) != (lf["cell_w"], lf["cell_h"]) or not lf["glyphs"]:
                    continue
                shared = sum(1 for k in lf["glyphs"] if k in ff_["glyphs"])
                if shared < 0.5 * len(lf["glyphs"]):
                    continue
                for k, g in lf["glyphs"].items():
                    fg = ff_["glyphs"].get(k)
                    allowed = set(fg["c"]) | {chr(c) for c in fg.get("x", [])} if fg else None
                    if fg and not set(g["c"]) <= allowed and not g.get("demoted"):
                        g["demoted"] = "contradicted by %s" % fn
                        out.contradictions.append({"learned_font": ln, "font_file": fn, "hex": k, "learned": sorted(g["c"]), "font_file_chars": sorted(fg["c"])})
        return out

    def cross_font_chars(self, font: str, key: bytes):
        """Characters that OTHER font-file fonts of the same cell size draw with this exact bitmap, if they differ from ``font``'s reading
        (e.g. VGA vertical line vs Unifont heavy vertical line).  Non-empty = the bitmap alone cannot tell which font/character it is."""
        ck = (font, key)
        if ck in self._xcache:
            return self._xcache[ck]
        me = self.fonts.get(font, {})
        mine = me.get("glyphs", {}).get(key.hex())
        out = set()
        if mine is not None and me.get("origin", {}).get("kind") == "font-file":
            mine_chars = set(mine["c"])
            for fn, f in self.fonts.items():
                if fn != font and f.get("origin", {}).get("kind") == "font-file" and (f["cell_w"], f["cell_h"]) == (me["cell_w"], me["cell_h"]):
                    o = f["glyphs"].get(key.hex())
                    if o is not None and set(o["c"]) != mine_chars:
                        out |= set(o["c"])
        self._xcache[ck] = sorted(out)
        return self._xcache[ck]

    def n_groups(self, font: str, key: bytes) -> int:
        """Independent screens (distinct groups; an ungrouped source is its own group) that confirm this bitmap."""
        g = self.fonts.get(font, {}).get("glyphs", {}).get(key.hex())
        return len({self.groups.get(src, src) for src in g["f"]}) if g else 0

    def lookup(self, font: str, key: bytes, min_groups: int = 1):
        """-> ('exact', char) | ('single', char) | ('conflict', [chars]) | None

        'single': the label exists but fewer than ``min_groups`` independent screen groups confirm it."""
        g = self.fonts.get(font, {}).get("glyphs", {}).get(key.hex())
        if g is None:
            return None
        if g.get("demoted"):
            return "conflict", sorted(g["c"])
        if g.get("font_exact"):  # verified by the font file, not by screenshots: no group rule, aliases stay a set
            extra = [chr(c) for c in g.get("x", [])]
            if len(g["c"]) > 1:
                return "alias", sorted(g["c"]) + extra
            ch = next(iter(g["c"]))
            return ("homoglyph", ch, extra) if extra else ("font", ch)
        if len(g["c"]) > 1:
            return "conflict", sorted(g["c"])
        ch = next(iter(g["c"]))
        if min_groups > 1 and self.n_groups(font, key) < min_groups:
            return "single", ch
        return "exact", ch

    def min_pairwise_distance(self, font: str) -> int:
        """Smallest Hamming distance between two glyphs of different characters (tolerant-match safety margin)."""
        items = [(int(k, 16), next(iter(g["c"])) if len(g["c"]) == 1 else "/".join(sorted(g["c"])))
                 for k, g in self.fonts.get(font, {}).get("glyphs", {}).items()]
        best = 10 ** 9
        for i, (a, ca) in enumerate(items):
            for b, cb in items[i + 1:]:
                if ca != cb:
                    best = min(best, _popcount(a ^ b))
        return best

    def fonts_for(self, cell_w, cell_h):
        return [n for n, f in self.fonts.items() if f["cell_w"] == cell_w and f["cell_h"] == cell_h]

    def nearest(self, font: str, key: bytes, limit: int = 3):
        """Evidence only: [(distance, char, hex)] best first over glyphs of ``font``."""
        v = int.from_bytes(key, "big")
        best = []
        for k, g in self.fonts.get(font, {}).get("glyphs", {}).items():
            d = _popcount(v ^ int(k, 16))
            ch = next(iter(g["c"])) if len(g["c"]) == 1 else "/".join(sorted(g["c"]))
            best.append((d, ch, k))
        best.sort()
        return best[:limit]


# --------------------------------------------------------------------------- decode
def decode(ex: Extraction, atlas: Atlas, font: str | None = None, max_hamming: int = 0,
           return_cells: bool = False, min_groups: int = 2, min_hits: int = 8, min_share: float = 0.25,
           margin: int = 2, rule_run: int = 4, cursor=None, font_min_share: float = FONT_MIN_SHARE) -> dict:
    """Exact lookup of every cell.  max_hamming=0 (default): no tolerant matching at all.

    * ``font_share`` = share of ordinary (not bar/box-like) ink cells the chosen font reads exactly.  Floor: >= ``min_hits`` cells and
      >= ``min_share``, else ``status: "no_font"``.  A font-FILE font additionally needs ``font_share >= font_min_share`` (0.9) before ANY cell
      may be font-exact: between the floors the screen is ``mixed_font`` (mixed content, or another font) and every cell is UNKNOWN.
    * A font-file bitmap that another same-size font draws as a DIFFERENT character (cross-font collision, e.g. VGA vs Unifont box lines) is
      an alias set ``a`` unless the font is proven (share >= font_min_share and no other same-size font above ``PROVEN_RUNNER_UP``).
    * ``F`` font-exact (unique code point); ``H`` unique repertoire character but other code points of the font's full coverage draw the same
      bitmap (``res["aliases"]`` holds the whole set; consumers apply a homoglyph policy); ``a`` several repertoire characters share the bitmap.
    * ``min_groups``: a LEARNED glyph is 'exact' only if >= this many independent screen groups confirm it, else 's'.
    * flags (``res["flags"]``): 'r' rule-line, 'c' cursor candidate: cells reading a cursor-shaped glyph (lower blocks, full block) always, a
      bar-like last inked cell of a row, or exactly the cell given by ``cursor=(row, col)`` (then the shape heuristic is off).
    * 'f' fill: uniform cells next to text on a different background, and the whole run of such uniform cells.
    """
    g = ex.grid
    w, h = g["cell_w"], g["cell_h"]
    boxmemo = {}

    def isbox(key):
        v = boxmemo.get(key)
        if v is None:
            v = boxmemo[key] = boxlike(key, w, h)
        return v

    cands = [font] if font else atlas.fonts_for(w, h)
    fscores = {}
    for fn in cands:
        n = hit = 0
        seen = {}
        for row in ex.rows:
            for c in row:
                if c.kind == "glyph" and not isbox(c.key):
                    n += 1
                    k = seen.get(c.key)
                    if k is None:
                        k = seen[c.key] = atlas.lookup(fn, c.key)
                    hit += bool(k and k[0] in HIT)
        fscores[fn] = (hit, n)
    chosen = max(fscores, key=lambda f: (fscores[f][0], f)) if fscores else None
    share = (fscores[chosen][0] / fscores[chosen][1]) if chosen and fscores[chosen][1] else 0.0
    floor_ok = bool(chosen) and fscores[chosen][0] >= min_hits and fscores[chosen][0] >= min_share * fscores[chosen][1]
    is_file = bool(chosen) and atlas.fonts[chosen].get("origin", {}).get("kind") == "font-file"
    runner_up = max([(v[0] / v[1] if v[1] else 0.0) for k, v in fscores.items() if k != chosen] or [0.0])
    proven = is_file and share >= font_min_share and runner_up <= PROVEN_RUNNER_UP
    mixed = floor_ok and is_file and share < font_min_share
    if mixed:
        floor_ok = False
    rows, conf, unknown, cells, flags = [], [], [], [], []
    memo = {}
    aliases = {}

    def neighbour_text_bg_differs(row, ci, c):
        for j in (ci - 1, ci + 1):
            if 0 <= j < len(row) and row[j].kind == "glyph" and row[j].bg != c.bg and not isbox(row[j].key):
                return True
        return False

    for row in ex.rows:
        chars, cf = [], []
        for ci, c in enumerate(row):
            ch, st = " ", CONF_BLANK
            if c.kind == "multicolor":
                ch, st = UNKNOWN_CHAR, CONF_MULTI
            elif c.kind == "glyph":
                if not floor_ok:
                    ch, st = UNKNOWN_CHAR, CONF_UNKNOWN
                else:
                    key = (chosen, c.key)
                    r = memo.get(key)
                    if r is None:
                        r = memo[key] = _decode_glyph(atlas, chosen, c.key, max_hamming, min_groups, margin, proven)
                    ch, st = r[0], r[1]
                    if st in (CONF_ALIAS, CONF_HOMO):
                        aliases[(c.row, c.col)] = r[2]["aliases"]
            elif neighbour_text_bg_differs(row, ci, c):
                ch, st = FILL_CHAR, CONF_FILL
            chars.append(ch)
            cf.append(st)
        # fill runs: the interior of a run of same-colour uniform cells next to a fill cell is fill throughout
        for rng_ in (range(len(row)), range(len(row) - 1, -1, -1)):
            for ci in rng_:
                if cf[ci] == CONF_BLANK and row[ci].kind == "blank":
                    for j in (ci - 1, ci + 1):
                        if 0 <= j < len(row) and cf[j] == CONF_FILL and row[j].bg == row[ci].bg:
                            cf[ci], chars[ci] = CONF_FILL, FILL_CHAR
        rows.append("".join(chars))
        conf.append("".join(cf))
    iscur = lambda st, ch: st in EXACTLIKE and ch in CURSOR_CHARS  # noqa: E731
    for r, (row, cf) in enumerate(zip(ex.rows, conf)):
        fl = ["."] * len(row)
        ci = 0
        while ci < len(row):
            c = row[ci]
            if cf[ci] in EXACTLIKE and c.kind == "glyph" and isbox(c.key):
                cj = ci
                while cj + 1 < len(row) and row[cj + 1].kind == "glyph" and row[cj + 1].key == c.key and cf[cj + 1] in EXACTLIKE:
                    cj += 1
                if cj - ci + 1 >= rule_run:
                    left = row[ci - 1] if ci > 0 else None
                    right = row[cj + 1] if cj + 1 < len(row) else None
                    if not any(n is not None and n.kind == "glyph" and not isbox(n.key) for n in (left, right)):
                        for k in range(ci, cj + 1):
                            fl[k] = "r"
                ci = cj + 1
            else:
                ci += 1
        if cursor is not None:
            if cursor[0] == r and 0 <= cursor[1] < len(row) and cf[cursor[1]] != CONF_BLANK:
                fl[cursor[1]] = "c"
        else:
            for ci, c in enumerate(row):
                if iscur(cf[ci], rows[r][ci]) and fl[ci] == ".":
                    fl[ci] = "c"
            inked = [i for i, x in enumerate(cf) if x != CONF_BLANK]
            if inked and cf[inked[-1]] in EXACTLIKE and row[inked[-1]].kind == "glyph" and barlike(row[inked[-1]].key, w, h) and fl[inked[-1]] == ".":
                fl[inked[-1]] = "c"
        flags.append("".join(fl))
    for r, row in enumerate(ex.rows):
        for ci, c in enumerate(row):
            st = conf[r][ci]
            if st in (CONF_UNKNOWN, CONF_CONFLICT, CONF_MULTI, CONF_SINGLE, CONF_FILL, CONF_ALIAS, CONF_HOMO):
                if c.kind == "glyph" and floor_ok:
                    ev = memo[(chosen, c.key)][2]
                elif c.kind == "multicolor":
                    ev = {"reason": "multicolor (%d colours)" % c.ncolors}
                elif c.kind == "glyph":
                    ev = {"reason": "mixed_font: font_share %.3f < %.2f" % (share, font_min_share) if mixed else "no_font"}
                else:
                    ev = {"reason": "fill", "colour": c.bg}
                unknown.append({"row": c.row, "col": c.col, "x": g["origin_x"] + c.col * w, "y": g["origin_y"] + c.row * h,
                                "status": st, "hex": c.key.hex(), **(ev or {})})
            if return_cells:
                cells.append({"row": c.row, "col": c.col, "char": rows[r][ci], "status": st, "flag": flags[r][ci],
                              "hex": c.key.hex(), "bg": c.bg, "fg": c.fg})
    has_glyph = any(c.kind == "glyph" for row in ex.rows for c in row)
    status = "ok"
    if has_glyph and not floor_ok:
        status = "mixed_font" if mixed else "no_font"
    res = {"status": status,
           "grid": {k: g[k] for k in ("cell_w", "cell_h", "origin_x", "origin_y", "cols", "rows")},
           "font": chosen, "font_is_file": is_file, "font_share": round(share, 4), "font_runner_up_share": round(runner_up, 4), "font_proven": bool(proven),
           "font_scores": {k: list(v) for k, v in fscores.items()}, "rows": rows, "conf": conf, "flags": flags,
           "unknown_cells": unknown, "max_hamming": max_hamming, "min_groups": min_groups,
           "aliases": {"%d,%d" % k: v for k, v in aliases.items()}}
    if status != "ok":
        res["message"] = ("no atlas font reads this screen (need >= %d ordinary cells and >= %d%% of them): every cell UNKNOWN" % (min_hits, int(min_share * 100))
                          if status == "no_font" else
                          "font_share %.3f is below %.2f: mixed or foreign content, no cell may be font-exact; every cell UNKNOWN" % (share, font_min_share))
    if max_hamming > 0 and chosen:
        res["atlas_min_distance"] = atlas.min_pairwise_distance(chosen)
        res["tolerance_margin"] = margin
        if max_hamming * 2 >= res["atlas_min_distance"]:
            res["warning"] = "max_hamming %d is >= half the atlas minimum pairwise distance %d: different glyphs can be confused" % (max_hamming, res["atlas_min_distance"])
    if return_cells:
        res["cells"] = cells
    return res


def _decode_glyph(atlas, font, key, max_hamming, min_groups=2, margin=2, proven=True):
    if font is None:
        return UNKNOWN_CHAR, CONF_UNKNOWN, {"reason": "no atlas font for this grid"}
    r = atlas.lookup(font, key, min_groups)
    if r and r[0] in ("font", "homoglyph"):
        other = atlas.cross_font_chars(font, key)
        if other and not proven:
            return UNKNOWN_CHAR, CONF_ALIAS, {"reason": "bitmap drawn as a different character by another font of this cell size; font not proven",
                                              "aliases": sorted(set([r[1]] + list(other)))}
    if r and r[0] == "exact":
        return r[1], CONF_EXACT, None
    if r and r[0] == "font":
        return r[1], CONF_FONT, None
    if r and r[0] == "homoglyph":
        return r[1], CONF_HOMO, {"reason": "other code points draw this bitmap too", "aliases": [r[1]] + r[2]}
    if r and r[0] == "alias":
        return UNKNOWN_CHAR, CONF_ALIAS, {"reason": "font-exact bitmap shared by several characters (ambiguity set)", "aliases": r[1]}
    if r and r[0] == "single":
        return UNKNOWN_CHAR, CONF_SINGLE, {"reason": "single-source: confirmed by %d independent screen group(s), need %d" % (atlas.n_groups(font, key), min_groups),
                                           "would_read": r[1]}
    if r:
        return UNKNOWN_CHAR, CONF_CONFLICT, {"reason": "atlas conflict", "candidates": r[1]}
    near = atlas.nearest(font, key, 6)
    ev = {"reason": "not in atlas"}
    if near:
        ev.update({"nearest": near[0][1], "distance": near[0][0], "nearest_hex": near[0][2]})
        if max_hamming > 0 and near[0][0] <= max_hamming:
            other = [n for n in near if n[1] != near[0][1]]
            if not other or other[0][0] >= near[0][0] + margin:
                return near[0][1], CONF_NEAREST, ev
    return UNKNOWN_CHAR, CONF_UNKNOWN, ev


# --------------------------------------------------------------------------- top level
def prepare(src, auto_unscale: bool = True, grid_hints=None, atlas: "Atlas | None" = None):
    """-> (im, probe, grid_report).  Applies the scale probe; im is None when exact matching is refused.

    Without explicit ``grid_hints`` the cell sizes of the atlas fonts are tried first (a screen that
    tiles as a known font is read as that font); only if none of them fits is the grid searched freely.
    Free search is a convenience for learning: on sparse screens it can prefer a multiple of the true
    cell (e.g. 16x16 for 8x16), which is why the known sizes come first.
    """
    im = load_image(src)
    probe = probe_scale(im)
    if probe["integer_upscale"] > 1 and auto_unscale:
        im = unscale(im, probe["integer_upscale"])
    elif probe["resampled"]:
        return None, probe, None
    if grid_hints is None and atlas is not None and atlas.fonts:
        sizes = sorted({(f["cell_w"], f["cell_h"]) for f in atlas.fonts.values()})
        g = detect_grid(im, hints=sizes)
        if g["status"] == "ok":
            return im, probe, g
        free = detect_grid(im)
        free["hinted_attempt"] = g["reasons"][:1]
        return im, probe, free
    return im, probe, detect_grid(im, hints=grid_hints)


def choose_extraction(im, grid: dict, atlas: Atlas, font: str | None = None, max_seconds: float = 4.0):
    """Try every candidate origin; keep the one with most exact atlas hits.  -> (Extraction, hits)."""
    best = None
    cands = grid["candidates"] or [[grid["origin_x"], grid["origin_y"]]]
    cands = [list(c) for c in cands[:8]]
    if [0, 0] in [list(c) for c in grid["candidates"]] and [0, 0] not in cands:
        cands.append([0, 0])
    # origins at which the atlas fonts were learned (a font whose glyphs sit at an offset inside the cell,
    # or a crop whose blank-run differs, would otherwise never offer that phase)
    for fn in atlas.fonts_for(grid["cell_w"], grid["cell_h"]):
        for ph in atlas.fonts[fn].get("phases", []):
            ph = [ph[0] % grid["cell_w"], ph[1] % grid["cell_h"]]
            if ph not in cands:
                cands.append(ph)
    for ox, oy in cands:
        ex = extract(im, grid_with_origin(grid, im.size, ox, oy))
        hits = _count_hits(ex, atlas, font)
        if best is None or hits > best[1]:
            best = (ex, hits)
    ffonts = [fn for fn in atlas.fonts_for(grid["cell_w"], grid["cell_h"])
              if atlas.fonts[fn].get("origin", {}).get("kind") == "font-file" and (font is None or fn == font)]
    if ffonts:
        best = _phase_search_fonts(im, grid, atlas, ffonts, best, time.monotonic() + max_seconds)
    return best


def _phase_search_fonts(im, grid, atlas, ffonts, best, deadline=None):
    """Font-FILE atlases carry no learned phase: try every cell origin on a few dense rows and accept the winner only
    with a large, unambiguous margin (thousands of exact glyph hits cannot happen by chance at a wrong phase)."""
    ex0 = best[0]
    n_all = sum(1 for row in ex0.rows for c in row if c.kind == "glyph")
    if best[1] >= 0.6 * max(n_all, 1):
        return best
    dense = sorted(range(len(ex0.rows)), key=lambda r: -sum(1 for c in ex0.rows[r] if c.kind == "glyph"))[:6]
    rows = sorted({min(max(r + d, 0), grid["rows"] - 1) for r in dense for d in (-1, 0, 1)})
    scores = []
    for oy in range(grid["cell_h"]):
        for ox in range(grid["cell_w"]):
            if deadline is not None and time.monotonic() > deadline:  # budget: a hopeless input must not cost seconds per megapixel
                return best
            g = grid_with_origin(grid, im.size, ox, oy)
            e = extract(im, g, rows)
            scores.append((max(_count_hits(e, atlas, fn) for fn in ffonts), ox, oy))
    scores.sort(reverse=True)
    top = scores[0]
    second = next((x for x in scores[1:] if (x[1], x[2]) != (top[1], top[2])), (0, 0, 0))
    if top[0] >= 30 and top[0] >= 2 * second[0] + 1:
        ex = extract(im, grid_with_origin(grid, im.size, top[1], top[2]))
        h = _count_hits(ex, atlas, None)
        if h > best[1]:
            return ex, h
    return best


def _count_hits(ex, atlas, font):
    g = ex.grid
    fonts = [font] if font else atlas.fonts_for(g["cell_w"], g["cell_h"])
    best, seen = 0, {}
    for fn in fonts:
        n = 0
        for row in ex.rows:
            for c in row:
                if c.kind == "glyph":
                    k = (fn, c.key)
                    r = seen.get(k)
                    if r is None:
                        r = seen[k] = atlas.lookup(fn, c.key)
                    n += bool(r and r[0] in HIT)
        best = max(best, n)
    return best


def read_image(src, atlas: Atlas, font: str | None = None, max_hamming: int = 0, auto_unscale: bool = True,
               return_cells: bool = False, grid_hints=None, **decode_kw) -> dict:
    """image bytes/path/PIL image -> grid result.  Never raises on a bad grid: reports status."""
    im, probe, g = prepare(src, auto_unscale, grid_hints, atlas)
    if im is None:
        return {"status": "resampled", "message": "resampled: exact matching unavailable", "probe": probe,
                "rows": [], "conf": [], "unknown_cells": []}
    if g["status"] != "ok":
        return {"status": "no_grid", "message": "no fixed grid found", "probe": probe, "grid_report": g,
                "rows": [], "conf": [], "unknown_cells": []}
    ex, _ = choose_extraction(im, g, atlas, font, decode_kw.pop("max_seconds", 4.0))
    res = decode(ex, atlas, font, max_hamming, return_cells, **decode_kw)
    res["probe"] = probe
    res["grid_report"] = {k: g[k] for k in ("length", "contrast", "candidates")}
    return res


# --------------------------------------------------------------------------- consumer helpers
HOMOGLYPH_POLICIES = ("latin-only", "none", "declared-repertoire")


def _benign_latin(ch: str, extra) -> bool:
    """Every other code point drawing this bitmap is a Latin lookalike of ``ch`` (accented variant, Kelvin sign, hyphen class)."""
    for e in extra:
        if ch == "-" and e in ("­", "‐", "‑"):
            continue
        d = unicodedata.normalize("NFKD", e)
        if d[:1] == ch and all(unicodedata.category(x) == "Mn" for x in d[1:]):
            continue
        return False
    return True


def _cell_state(res, r, c, exclude_flagged=True, homoglyphs="latin-only", cursor=None):
    """'ok' (may be relied on) | 'ambiguous' (readable but not unique) | 'bad'."""
    if homoglyphs not in HOMOGLYPH_POLICIES:
        raise ValueError("homoglyphs must be one of %r" % (HOMOGLYPH_POLICIES,))
    if cursor is not None and (r, c) == tuple(cursor):
        return "bad"
    if exclude_flagged and res.get("flags") and res["flags"][r][c] != ".":
        return "bad"
    st = res["conf"][r][c]
    if st in TEXTLIKE:
        return "ok"
    if st == CONF_HOMO:
        extra = res["aliases"].get("%d,%d" % (r, c), [])[1:]
        ch = res["rows"][r][c]
        if homoglyphs == "declared-repertoire" or (homoglyphs == "latin-only" and _benign_latin(ch, extra)):
            return "ok"
        return "ambiguous"
    if st == CONF_ALIAS:
        return "ambiguous"
    return "bad"


def _ok_cell(res, r, c, exclude_flagged, homoglyphs="latin-only"):
    return _cell_state(res, r, c, exclude_flagged, homoglyphs) == "ok"


def region(res: dict, row0: int, row1: int, col0: int, col1: int, exclude_flagged: bool = True, homoglyphs: str = "latin-only"):
    """(text, n_bad) of rows [row0,row1) x cols [col0,col1); n_bad counts every cell that may not be relied on."""
    text, bad = [], 0
    for r in range(row0, row1):
        text.append(res["rows"][r][col0:col1])
        bad += sum(1 for c in range(col0, min(col1, len(res["rows"][r]))) if not _ok_cell(res, r, c, exclude_flagged, homoglyphs))
    return "\n".join(text), bad


def _boundary_ok(res, r, c, exclude_flagged, homoglyphs, cursor):
    """A word boundary is the row edge or a READABLE non-alphanumeric cell (text-like state, not flagged); unknown/alias/single/fill/cursor
    neighbours are NOT boundaries."""
    if c < 0 or c >= len(res["rows"][r]):
        return True
    if _cell_state(res, r, c, exclude_flagged, homoglyphs, cursor) != "ok":
        return False
    return not res["rows"][r][c].isalnum()


def _occurrences(res, phrase, region_=None):
    rows = range(len(res["rows"])) if region_ is None else range(region_[0], region_[1])
    for r in rows:
        s = res["rows"][r]
        start = 0
        while True:
            i = s.find(phrase, start)
            if i < 0:
                break
            start = i + 1
            if region_ is not None and not (region_[2] <= i and i + len(phrase) <= region_[3]):
                continue
            yield r, i


def find_phrase(res: dict, phrase: str, exclude_flagged: bool = True, word_boundary: bool | None = None, homoglyphs: str = "latin-only"):
    """Plain (unique) occurrences of ``phrase``: every phrase cell is F/e/b without flag, homoglyph cells pass the policy.
    ``word_boundary`` (default: on when the phrase starts/ends with an alphanumeric) requires a readable non-alphanumeric
    neighbour or the row edge.  Ambiguous matches are NEVER returned here: see ``find_phrase_ambiguous``.
    Returns [{"row","col","unknown_in_row"}]."""
    if word_boundary is None:
        word_boundary = phrase[:1].isalnum() or phrase[-1:].isalnum()
    hits = []
    for r, i in _occurrences(res, phrase):
        j = i + len(phrase)
        if not all(_cell_state(res, r, c, exclude_flagged, homoglyphs) == "ok" for c in range(i, j)):
            continue
        if word_boundary:
            if phrase[0].isalnum() and not _boundary_ok(res, r, i - 1, exclude_flagged, homoglyphs, None):
                continue
            if phrase[-1].isalnum() and not _boundary_ok(res, r, j, exclude_flagged, homoglyphs, None):
                continue
        hits.append({"row": r, "col": i, "unknown_in_row": sum(1 for c in range(len(res["rows"][r])) if _cell_state(res, r, c, exclude_flagged, homoglyphs) != "ok")})
    return hits


def find_phrase_ambiguous(res: dict, phrase: str, exclude_flagged: bool = True, homoglyphs: str = "latin-only"):
    """Matches that are readable but NOT unique: some cells are alias sets (``a``, the phrase character is in the set) or homoglyph cells
    refused by the policy.  A keystroke decision must not rely on these."""
    out = []
    for r in range(len(res["rows"])):
        s = res["rows"][r]
        for i in range(0, len(s) - len(phrase) + 1):
            amb, ok = [], True
            for k, ch in enumerate(phrase):
                c = i + k
                state = _cell_state(res, r, c, exclude_flagged, homoglyphs)
                if state == "ok":
                    ok = s[c] == ch
                elif state == "ambiguous" and ch in res["aliases"].get("%d,%d" % (r, c), []):
                    amb.append((r, c))
                else:
                    ok = False
                if not ok:
                    break
            if ok and amb:
                out.append({"row": r, "col": i, "ambiguous": True, "alias_cells": amb})
    return out


def strict_match(res: dict, phrase: str, region=None, cursor=None, homoglyphs: str = "latin-only", min_font_share: float = FONT_MIN_SHARE) -> dict:
    """THE decision function for a keystroke consumer.  matched = at least one occurrence whose phrase cells AND both boundary cells are
    F/e/b (homoglyph cells per policy), unflagged, not alias/single/fill/unknown, the screen status is ok and, for a font-file font,
    font_share >= ``min_font_share``.  ``region`` = (row0, row1, col0, col1) restricts the search; ``cursor`` = (row, col) is excluded.
    Returns {"matched", "hits", "candidates" (every textual occurrence with the reasons it failed), "reasons"}."""
    reasons = []
    if res.get("status") != "ok":
        return {"matched": False, "hits": [], "candidates": [], "reasons": ["screen status %r: %s" % (res.get("status"), res.get("message", ""))]}
    if res.get("font_is_file") and res.get("font_share", 0.0) < min_font_share:
        reasons.append("font_share %.3f < %.2f" % (res["font_share"], min_font_share))
    hits, cands = [], []
    for r, i in _occurrences(res, phrase, region):
        j = i + len(phrase)
        why = []
        for c in range(i, j):
            st = _cell_state(res, r, c, True, homoglyphs, cursor)
            if st != "ok":
                why.append("cell %d,%d %s (conf %r flag %r)" % (r, c, st, res["conf"][r][c], res["flags"][r][c]))
        for c in (i - 1, j):
            if 0 <= c < len(res["rows"][r]) and not _boundary_ok(res, r, c, True, homoglyphs, cursor):
                why.append("boundary cell %d,%d not readable text/space (conf %r)" % (r, c, res["conf"][r][c]))
        if res.get("font_share") is not None and reasons:
            why.extend(reasons)
        cands.append({"row": r, "col": i, "reasons": why})
        if not why:
            hits.append({"row": r, "col": i})
    if not cands:
        reasons.append("phrase not present in the read text")
    return {"matched": bool(hits), "hits": hits, "candidates": cands, "reasons": reasons}
