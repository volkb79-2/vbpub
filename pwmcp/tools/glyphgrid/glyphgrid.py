"""glyphgrid: deterministic glyph-grid matching for fixed-font console screenshots.

Pure function image bytes -> grid result.  Python stdlib + Pillow only.

Pipeline
  1. ``probe_scale``  refuse resampled/blurred captures (exact matching is not possible there).
  2. ``detect_grid``  find cell size and origin without being told (or report no fixed grid).
  3. ``extract``      split into cells, normalise every cell to a 1-bit foreground bitmap
                      (works for light-on-dark and dark-on-light, reverse video included).
  4. ``decode``       exact dictionary lookup in an ``Atlas``.  UNKNOWN is never a guess:
                      nearest candidate + Hamming distance are evidence, not output.

Safety contract: only cells with status ``exact`` (and blank cells) are text; everything else is
indeterminate.  ``max_hamming`` (tolerant matching) is OFF by default.
"""
from __future__ import annotations

import io
import json
import math
from collections import Counter

from PIL import Image, ImageChops

UNKNOWN_CHAR = "�"
FORMAT = "glyphgrid-atlas/1"

# confidence letters used in the per-row ``conf`` strings
CONF_EXACT, CONF_BLANK, CONF_UNKNOWN, CONF_NEAREST, CONF_CONFLICT, CONF_MULTI = "e", "b", "u", "n", "x", "m"
CONF_SINGLE = "s"   # bitmap known, but confirmed by fewer than ``min_groups`` independent screens: NOT exact
CONF_FILL = "f"     # uniform cell whose colour differs from the text background next to it (solid block, fill)
TEXTLIKE = (CONF_EXACT, CONF_BLANK)
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
    ink = [r for r in rows if r]
    # a horizontal bar of at most two pixel rows spanning (nearly) the cell: '_', '-', '=' style rules
    return bool(ink) and len(ink) <= 2 and max(bin(r).count("1") for r in ink) >= w - 2


class Atlas:
    """fonts[name] = {"cell_w","cell_h","glyphs": {hex: {"c": {char: count}, "f": [source,...]}}, "phases": [[ox,oy],...]}

    ``phases`` are the cell origins (mod cell size) at which the font was learned: extra candidates when reading.
    ``groups`` maps a source (frame id) to a screen group: near-duplicate frames share a group, so one
    screen photographed twice is ONE piece of evidence (see ``lookup(min_groups=)``)."""

    def __init__(self, fonts=None, groups=None):
        self.fonts = fonts or {}
        self.groups = groups or {}

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
            for k, g in glyphs.items():
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
        return [(fn, k, g) for fn, f in self.fonts.items() for k, g in f["glyphs"].items() if len(g["c"]) > 1]

    # lookup
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
           margin: int = 2, rule_run: int = 4, cursor=None) -> dict:
    """Exact lookup of every cell.  max_hamming=0 (default): no tolerant matching at all.

    * evidence floor: the chosen font must read at least ``min_hits`` ordinary (not bar/box-like) glyph cells
      exactly AND at least ``min_share`` of them, else ``status: "no_font"`` and every cell is UNKNOWN.
    * ``min_groups``: a glyph is 'exact' only if >= this many independent screen groups confirm it, else 's'.
    * flags (``res["flags"]``, one char per cell): 'r' rule-line (run of >= ``rule_run`` identical bar-like
      cells with no ordinary glyph beside it, e.g. a title underline of '_'), 'c' cursor candidate (the given
      ``cursor=(row, col)``, or the last inked cell of a row when it is bar-like).  Flagged cells keep 'e';
      ``find_phrase``/``region`` exclude them by default.
    * 'f' fill: a uniform cell next to a text cell with a different background (solid block, fill).
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
                    hit += bool(k and k[0] == "exact")
        fscores[fn] = (hit, n)
    chosen = max(fscores, key=lambda f: (fscores[f][0], f)) if fscores else None
    floor_ok = bool(chosen) and fscores[chosen][0] >= min_hits and fscores[chosen][0] >= min_share * fscores[chosen][1]
    rows, conf, unknown, cells, flags = [], [], [], [], []
    memo = {}

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
                        r = memo[key] = _decode_glyph(atlas, chosen, c.key, max_hamming, min_groups, margin)
                    ch, st = r[0], r[1]
            elif neighbour_text_bg_differs(row, ci, c):
                ch, st = FILL_CHAR, CONF_FILL
            chars.append(ch)
            cf.append(st)
        rows.append("".join(chars))
        conf.append("".join(cf))
    for r, (row, cf) in enumerate(zip(ex.rows, conf)):
        fl = ["."] * len(row)
        ci = 0
        while ci < len(row):
            c = row[ci]
            if cf[ci] == CONF_EXACT and c.kind == "glyph" and isbox(c.key):
                cj = ci
                while cj + 1 < len(row) and row[cj + 1].kind == "glyph" and row[cj + 1].key == c.key and cf[cj + 1] == CONF_EXACT:
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
            inked = [i for i, x in enumerate(cf) if x != CONF_BLANK]
            if inked and cf[inked[-1]] == CONF_EXACT and row[inked[-1]].kind == "glyph" and isbox(row[inked[-1]].key) and fl[inked[-1]] == ".":
                fl[inked[-1]] = "c"
        flags.append("".join(fl))
    for r, row in enumerate(ex.rows):
        for ci, c in enumerate(row):
            st = conf[r][ci]
            if st in (CONF_UNKNOWN, CONF_CONFLICT, CONF_MULTI, CONF_SINGLE, CONF_FILL):
                if c.kind == "glyph" and floor_ok:
                    ev = memo[(chosen, c.key)][2]
                elif c.kind == "multicolor":
                    ev = {"reason": "multicolor (%d colours)" % c.ncolors}
                elif c.kind == "glyph":
                    ev = {"reason": "no_font"}
                else:
                    ev = {"reason": "fill", "colour": c.bg}
                unknown.append({"row": c.row, "col": c.col, "x": g["origin_x"] + c.col * w, "y": g["origin_y"] + c.row * h,
                                "status": st, "hex": c.key.hex(), **(ev or {})})
            if return_cells:
                cells.append({"row": c.row, "col": c.col, "char": rows[r][ci], "status": st, "flag": flags[r][ci],
                              "hex": c.key.hex(), "bg": c.bg, "fg": c.fg})
    has_glyph = any(c.kind == "glyph" for row in ex.rows for c in row)
    res = {"status": "no_font" if (has_glyph and not floor_ok) else "ok",
           "grid": {k: g[k] for k in ("cell_w", "cell_h", "origin_x", "origin_y", "cols", "rows")},
           "font": chosen, "font_scores": {k: list(v) for k, v in fscores.items()}, "rows": rows, "conf": conf, "flags": flags,
           "unknown_cells": unknown, "max_hamming": max_hamming, "min_groups": min_groups}
    if res["status"] == "no_font":
        res["message"] = "no atlas font reads this screen (need >= %d ordinary cells and >= %d%% of them): every cell UNKNOWN" % (min_hits, int(min_share * 100))
    if max_hamming > 0 and chosen:
        res["atlas_min_distance"] = atlas.min_pairwise_distance(chosen)
        res["tolerance_margin"] = margin
        if max_hamming * 2 >= res["atlas_min_distance"]:
            res["warning"] = "max_hamming %d is >= half the atlas minimum pairwise distance %d: different glyphs can be confused" % (max_hamming, res["atlas_min_distance"])
    if return_cells:
        res["cells"] = cells
    return res


def _decode_glyph(atlas, font, key, max_hamming, min_groups=2, margin=2):
    if font is None:
        return UNKNOWN_CHAR, CONF_UNKNOWN, {"reason": "no atlas font for this grid"}
    r = atlas.lookup(font, key, min_groups)
    if r and r[0] == "exact":
        return r[1], CONF_EXACT, None
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


def choose_extraction(im, grid: dict, atlas: Atlas, font: str | None = None):
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
                    n += bool(r and r[0] == "exact")
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
    ex, _ = choose_extraction(im, g, atlas, font)
    res = decode(ex, atlas, font, max_hamming, return_cells, **decode_kw)
    res["probe"] = probe
    res["grid_report"] = {k: g[k] for k in ("length", "contrast", "candidates")}
    return res


# --------------------------------------------------------------------------- consumer helpers
def _ok_cell(res, r, c, exclude_flagged):
    if res["conf"][r][c] not in TEXTLIKE:
        return False
    return not (exclude_flagged and res.get("flags") and res["flags"][r][c] != ".")


def region(res: dict, row0: int, row1: int, col0: int, col1: int, exclude_flagged: bool = True):
    """(text, n_bad) of rows [row0,row1) x cols [col0,col1); n_bad counts unknown/single/fill/flagged cells."""
    text, bad = [], 0
    for r in range(row0, row1):
        text.append(res["rows"][r][col0:col1])
        bad += sum(1 for c in range(col0, min(col1, len(res["rows"][r]))) if not _ok_cell(res, r, c, exclude_flagged))
    return "\n".join(text), bad


def find_phrase(res: dict, phrase: str, exclude_flagged: bool = True, word_boundary: bool | None = None):
    """Occurrences of ``phrase`` on a single row built ONLY from exact/blank cells.

    Cells flagged rule-line ('r') or cursor ('c') are excluded by default; 'fill', 'single-source' and unknown
    cells never match.  ``word_boundary`` (default: on when the phrase starts/ends with an alphanumeric)
    rejects a match glued to a neighbouring alphanumeric.  Returns [{"row","col","unknown_in_row"}].
    """
    if word_boundary is None:
        word_boundary = phrase[:1].isalnum() or phrase[-1:].isalnum()
    hits = []
    for r, s in enumerate(res["rows"]):
        start = 0
        while True:
            i = s.find(phrase, start)
            if i < 0:
                break
            start = i + 1
            j = i + len(phrase)
            if not all(_ok_cell(res, r, c, exclude_flagged) for c in range(i, j)):
                continue
            if word_boundary:
                if phrase[0].isalnum() and i > 0 and s[i - 1].isalnum():
                    continue
                if phrase[-1].isalnum() and j < len(s) and s[j].isalnum():
                    continue
            hits.append({"row": r, "col": i, "unknown_in_row": sum(1 for c in range(len(s)) if not _ok_cell(res, r, c, exclude_flagged))})
    return hits
