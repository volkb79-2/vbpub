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


# --------------------------------------------------------------------------- image loading
def load_image(src) -> Image.Image:
    if isinstance(src, Image.Image):
        im = src
    elif isinstance(src, (bytes, bytearray)):
        im = Image.open(io.BytesIO(bytes(src)))
    else:
        im = Image.open(src)
    return im.convert("RGB")


# --------------------------------------------------------------------------- scale probe
def probe_scale(im: Image.Image, soft_threshold: float = 0.05) -> dict:
    """Decide whether the capture is a crisp 1:1 bitmap.

    soft_ratio = pixels outside the 8 most common colours / pixels that are not the dominant colour.
    A crisp indexed-colour console is ~0; any resampling (bilinear, lanczos, non-integer nearest)
    creates a continuum of blended colours along every glyph edge.  ``integer_upscale`` reports a
    crisp k x nearest-neighbour upscale (every k x k block uniform), which can be undone exactly.
    """
    colors = im.getcolors(im.size[0] * im.size[1]) or []
    total = im.size[0] * im.size[1]
    colors = sorted(colors, reverse=True)
    n_colors = len(colors)
    dominant = colors[0][0] if colors else total
    non_dom = max(total - dominant, 1)
    outside = total - sum(n for n, _ in colors[:8])
    soft_ratio = outside / non_dom if non_dom else 0.0
    up = _integer_upscale(im)
    resampled = soft_ratio > soft_threshold and up == 1
    return {
        "n_colors": n_colors,
        "soft_ratio": round(soft_ratio, 5),
        "integer_upscale": up,
        "resampled": bool(resampled),
        "verdict": "resampled: exact matching unavailable" if resampled else "crisp",
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


class Atlas:
    """fonts[name] = {"cell_w","cell_h","glyphs": {hex: {"c": {char: count}, "f": [source,...]}}, "phases": [[ox,oy],...]}

    ``phases`` are the cell origins (mod cell size) at which the font was learned: extra candidates when reading."""

    def __init__(self, fonts=None):
        self.fonts = fonts or {}

    # persistence
    def to_json(self) -> str:
        return json.dumps({"format": FORMAT, "fonts": self.fonts}, indent=1, sort_keys=True, ensure_ascii=False)

    @classmethod
    def from_json(cls, text: str) -> "Atlas":
        d = json.loads(text)
        if d.get("format") != FORMAT:
            raise ValueError("not a glyphgrid atlas: %r" % d.get("format"))
        return cls(d["fonts"])

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
    def lookup(self, font: str, key: bytes):
        """-> ('exact', char) | ('conflict', [chars]) | None"""
        g = self.fonts.get(font, {}).get("glyphs", {}).get(key.hex())
        if g is None:
            return None
        if len(g["c"]) == 1:
            return "exact", next(iter(g["c"]))
        return "conflict", sorted(g["c"])

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
           return_cells: bool = False) -> dict:
    """Exact lookup of every cell.  max_hamming=0 (default): no tolerant matching at all."""
    g = ex.grid
    cands = [font] if font else atlas.fonts_for(g["cell_w"], g["cell_h"])
    fscores = {}
    for fn in cands:
        n = hit = 0
        seen = {}
        for row in ex.rows:
            for c in row:
                if c.kind == "glyph":
                    n += 1
                    k = seen.get(c.key)
                    if k is None:
                        k = seen[c.key] = atlas.lookup(fn, c.key)
                    hit += bool(k and k[0] == "exact")
        fscores[fn] = (hit, n)
    chosen = max(fscores, key=lambda f: (fscores[f][0], f)) if fscores else None
    rows, conf, unknown, cells = [], [], [], []
    memo = {}
    for row in ex.rows:
        chars, cf = [], []
        for c in row:
            ch, st, ev = " ", CONF_BLANK, None
            if c.kind == "multicolor":
                ch, st, ev = UNKNOWN_CHAR, CONF_MULTI, {"reason": "multicolor (%d colours)" % c.ncolors}
            elif c.kind == "glyph":
                key = (chosen, c.key)
                r = memo.get(key)
                if r is None:
                    r = memo[key] = _decode_glyph(atlas, chosen, c.key, max_hamming)
                ch, st, ev = r
            chars.append(ch)
            cf.append(st)
            if st in (CONF_UNKNOWN, CONF_CONFLICT, CONF_MULTI):
                unknown.append({"row": c.row, "col": c.col, "x": g["origin_x"] + c.col * g["cell_w"],
                                "y": g["origin_y"] + c.row * g["cell_h"], "status": st,
                                "hex": c.key.hex(), **(ev or {})})
            if return_cells:
                cells.append({"row": c.row, "col": c.col, "char": ch, "status": st, "hex": c.key.hex(),
                              "bg": c.bg, "fg": c.fg, **({"evidence": ev} if ev else {})})
        rows.append("".join(chars))
        conf.append("".join(cf))
    res = {"status": "ok", "grid": {k: g[k] for k in ("cell_w", "cell_h", "origin_x", "origin_y", "cols", "rows")},
           "font": chosen, "font_scores": {k: list(v) for k, v in fscores.items()}, "rows": rows, "conf": conf,
           "unknown_cells": unknown, "max_hamming": max_hamming}
    if return_cells:
        res["cells"] = cells
    return res


def _decode_glyph(atlas, font, key, max_hamming):
    if font is None:
        return UNKNOWN_CHAR, CONF_UNKNOWN, {"reason": "no atlas font for this grid"}
    r = atlas.lookup(font, key)
    if r and r[0] == "exact":
        return r[1], CONF_EXACT, None
    if r:
        return UNKNOWN_CHAR, CONF_CONFLICT, {"reason": "atlas conflict", "candidates": r[1]}
    near = atlas.nearest(font, key)
    ev = {"reason": "not in atlas"}
    if near:
        ev.update({"nearest": near[0][1], "distance": near[0][0], "nearest_hex": near[0][2]})
        if max_hamming > 0 and near[0][0] <= max_hamming:
            same = [n for n in near if n[0] == near[0][0] and n[1] != near[0][1]]
            if not same:
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
               return_cells: bool = False, grid_hints=None) -> dict:
    """image bytes/path/PIL image -> grid result.  Never raises on a bad grid: reports status."""
    im, probe, g = prepare(src, auto_unscale, grid_hints, atlas)
    if im is None:
        return {"status": "resampled", "message": "resampled: exact matching unavailable", "probe": probe,
                "rows": [], "conf": [], "unknown_cells": []}
    if g["status"] != "ok":
        return {"status": "no_grid", "message": "no fixed grid found", "probe": probe, "grid_report": g,
                "rows": [], "conf": [], "unknown_cells": []}
    ex, _ = choose_extraction(im, g, atlas, font)
    res = decode(ex, atlas, font, max_hamming, return_cells)
    res["probe"] = probe
    res["grid_report"] = {k: g[k] for k in ("length", "contrast", "candidates")}
    return res


# --------------------------------------------------------------------------- consumer helpers
def region(res: dict, row0: int, row1: int, col0: int, col1: int):
    """(text, n_unknown) of rows [row0,row1) x cols [col0,col1)."""
    text, bad = [], 0
    for r in range(row0, row1):
        text.append(res["rows"][r][col0:col1])
        bad += sum(1 for c in res["conf"][r][col0:col1] if c not in (CONF_EXACT, CONF_BLANK))
    return "\n".join(text), bad


def find_phrase(res: dict, phrase: str):
    """Occurrences of ``phrase`` on a single row built ONLY from exact/blank cells.

    Returns [{"row","col"}].  A phrase that spans an unknown cell is never reported, and the number of
    unknown cells on the matched row is returned so a consumer can demand 0 in its region.
    """
    hits = []
    for r, (s, cf) in enumerate(zip(res["rows"], res["conf"])):
        start = 0
        while True:
            i = s.find(phrase, start)
            if i < 0:
                break
            if all(c in (CONF_EXACT, CONF_BLANK) for c in cf[i:i + len(phrase)]):
                hits.append({"row": r, "col": i, "unknown_in_row": sum(1 for c in cf if c not in (CONF_EXACT, CONF_BLANK))})
            start = i + 1
    return hits
