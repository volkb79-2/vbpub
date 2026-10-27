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

import contextlib
import copy
import io
import json
import math
import os
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
CONF_SCALED = "z"   # font-exact bitmap read from a capture that was an undone k-fold upscale WITHOUT a complete larger-font guard: the character is
                    # shown but NOT actionable (not in TEXTLIKE/EXACTLIKE: find_phrase/region/strict_match refuse it; opt in with ``trust_scale``)
TEXTLIKE = (CONF_EXACT, CONF_FONT, CONF_BLANK)
EXACTLIKE = (CONF_EXACT, CONF_FONT, CONF_HOMO)
HIT = ("exact", "font", "homoglyph", "alias")
CURSOR_CHARS = "\u2581\u2582\u2583\u2584\u2588"  # cursor-ambiguous shapes: never text unless the caller says where the cursor is
FONT_MIN_SHARE = 0.9   # share of ordinary ink cells a font-FILE font must read exactly before any cell may be font-exact
PROVEN_RUNNER_UP = 0.3  # ... and no other same-size font may explain more than this share
FILL_CHAR = "\u2588"
MAX_PIXELS = 8_000_000
# Speed paths.  Every fast path below is bit-identical to its reference implementation (the original pure Python/Pillow code, kept in
# this module and selected by ``GLYPHGRID_REFERENCE=1`` or ``with reference_mode():``); tests assert the identity.  Never a behaviour switch.
_REFERENCE = os.environ.get("GLYPHGRID_REFERENCE") == "1"


@contextlib.contextmanager
def reference_mode(on: bool = True):
    """Run the unoptimised reference implementation inside the block (used by the identity tests).  NOT thread-safe: it flips a module global."""
    global _REFERENCE
    old, _REFERENCE = _REFERENCE, bool(on)
    try:
        yield
    finally:
        _REFERENCE = old


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
def _transition_masks(im: Image.Image):
    """(mh, mv): 255 where a pixel differs from its right / lower neighbour, as L images of size (W-1, H) and (W, H-1).
    Shared by the scale probe and the grid detector (both used to recompute them)."""
    W, H = im.size
    mh = Image.frombytes("L", (W - 1, H), _mask_bytes(im.crop((0, 0, W - 1, H)), im.crop((1, 0, W, H))))
    mv = Image.frombytes("L", (W, H - 1), _mask_bytes(im.crop((0, 0, W, H - 1)), im.crop((0, 1, W, H))))
    return mh, mv


def _ramp_count(a, b, c, cand) -> int:
    hi, lo = ImageChops.lighter(a, c), ImageChops.darker(a, c)
    viol = ImageChops.lighter(ImageChops.subtract(b, hi), ImageChops.subtract(lo, b))
    r_, g_, b_ = viol.split()
    viol = ImageChops.lighter(ImageChops.lighter(r_, g_), b_).point(lambda v: 0 if v else 255)
    return ImageChops.darker(cand, viol).tobytes().count(255)


def _ramp_parts(im: Image.Image, _masks=None):
    """[(ramp pixels, edge pixels)] for the horizontal and the vertical direction.  Both counts are LOCAL (a pixel's value depends on
    its 3-pixel window), which is what lets ``GlyphReader`` update them from a changed band instead of the whole frame."""
    W, H = im.size
    parts = []
    for horizontal in (True, False):
        if horizontal:
            a, b, c = im.crop((0, 0, W - 2, H)), im.crop((1, 0, W - 1, H)), im.crop((2, 0, W, H))
            size = (W - 2, H)
        else:
            a, b, c = im.crop((0, 0, W, H - 2)), im.crop((0, 1, W, H - 1)), im.crop((0, 2, W, H))
            size = (W, H - 2)
        if _masks is not None and not _REFERENCE:  # mab / mbc are two windows of the shared transition mask
            m = _masks[0] if horizontal else _masks[1]
            mab, mbc = (m.crop((0, 0, W - 2, H)), m.crop((1, 0, W - 1, H))) if horizontal else (m.crop((0, 0, W, H - 2)), m.crop((0, 1, W, H - 1)))
        else:
            mab = Image.frombytes("L", size, _mask_bytes(a, b))
            mbc = Image.frombytes("L", size, _mask_bytes(b, c))
        mac = Image.frombytes("L", size, _mask_bytes(a, c))
        if not _REFERENCE:
            # only pixels with all three masks set can be a ramp: compute the blend test inside their bounding box (a crisp console has
            # almost none), same count as the full-frame reference
            cand = ImageChops.darker(ImageChops.darker(mab, mbc), mac)
            box = cand.getbbox()
            ramp = 0 if box is None else _ramp_count(a.crop(box), b.crop(box), c.crop(box), cand.crop(box))
            parts.append((ramp, mab.tobytes().count(255)))
            continue
        # the middle pixel must lie between its neighbours in every channel (a blend), not merely differ
        hi, lo = ImageChops.lighter(a, c), ImageChops.darker(a, c)
        viol = ImageChops.lighter(ImageChops.subtract(b, hi), ImageChops.subtract(lo, b))
        r_, g_, b_ = viol.split()
        viol = ImageChops.lighter(ImageChops.lighter(r_, g_), b_).point(lambda v: 0 if v else 255)
        ramp = ImageChops.darker(ImageChops.darker(ImageChops.darker(mab, mbc), mac), viol).tobytes().count(255)
        parts.append((ramp, mab.tobytes().count(255)))
    return parts


def _ratio(parts) -> float:
    best = 0.0
    for ramp, edges in parts:
        best = max(best, ramp / max(edges, 1))
    return best


def ramp_ratio(im: Image.Image, _masks=None) -> float:
    """Share of edge pixels that are a three-level step (left != pixel != right, left != right).

    A crisp bitmap (any palette size) has almost none: an edge is a jump between two colours.  Every
    resampling filter blends the two colours into intermediate values, which shows up as ramps.  Unlike a
    colour-count test this does not penalise a legitimately colour-rich 1:1 console.  Max of the
    horizontal and vertical measure.
    """
    return _ratio(_ramp_parts(im, _masks))


def _probe_dict(colors, total: int, parts, up: int, thr: float) -> dict:
    """The ``probe_scale`` result from its measurements (colours sorted descending as (count, colour), ramp parts, integer upscale)."""
    dominant = colors[0][0] if colors else total
    soft_ratio = (total - sum(n for n, _ in colors[:8])) / max(total - dominant, 1)  # evidence only
    ramp = _ratio(parts)
    resampled = ramp > thr and up == 1
    return {
        "n_colors": len(colors),
        "soft_ratio": round(soft_ratio, 5),
        "ramp_ratio": round(ramp, 5),
        "integer_upscale": up,
        "resampled": bool(resampled),
        "verdict": "resampled: exact matching unavailable" if resampled else "crisp (1:1 NOT proven)",
    }


def probe_scale(im: Image.Image, soft_threshold: float | None = None, _masks=None, _state=None) -> dict:
    """Detect blur/resampling.  THIS CANNOT PROVE THE CAPTURE IS 1:1.

    It refuses images whose edges are blended (bilinear/bicubic/lanczos at any scale, JPEG-like noise) and
    undoes crisp integer nearest-neighbour upscales.  It does NOT catch crisp non-integer nearest scaling,
    integer upscales with an odd offset or letterbox, or a canvas that is simply a different size than the guest
    framebuffer; those are only contained by the font-consistency floor in ``decode``.  The capture path must
    deliver the intrinsic bitmap, and the consumer must verify canvas width/height against the guest
    framebuffer size independently.
    """
    thr = RAMP_THRESHOLD if soft_threshold is None else soft_threshold
    total = im.size[0] * im.size[1]
    colors = sorted(im.getcolors(total) or [], reverse=True)
    parts = _ramp_parts(im, _masks)
    up = _integer_upscale(im)
    if _state is not None:
        _state.update(colors=colors, parts=parts, thr=thr)
    return _probe_dict(colors, total, parts, up, thr)


def _integer_upscale(im: Image.Image) -> int:
    """Largest k in 2..8 such that im is exactly a nearest-neighbour k-fold upscale, else 1."""
    W, H = im.size
    for k in (8, 6, 5, 4, 3, 2):
        if W % k or H % k:
            continue
        if not _REFERENCE and not _bands_upscaled(im, k):
            continue
        small = im.resize((W // k, H // k), Image.NEAREST)
        if ImageChops.difference(small.resize((W, H), Image.NEAREST), im).getbbox() is None:
            return k
    return 1


def _bands_upscaled(im: Image.Image, k: int, n: int = 6) -> bool:
    """Necessary condition for ``im`` being an exact k-fold nearest upscale, tested on n thin full-width bands (the test is local per
    k x k block, so a failing band proves the full test fails).  Rejects a non-upscaled screen without resizing all of it."""
    W, H = im.size
    for i in range(n):
        y0 = (H // k) * i // n * k
        band = im.crop((0, y0, W, y0 + k))
        if ImageChops.difference(band.resize((W // k, 1), Image.NEAREST).resize((W, k), Image.NEAREST), band).getbbox() is not None:
            return False
    return True


def unscale(im: Image.Image, k: int) -> Image.Image:
    return im.resize((im.size[0] // k, im.size[1] // k), Image.NEAREST) if k > 1 else im


# --------------------------------------------------------------------------- grid detection
def _mask_bytes(a: Image.Image, b: Image.Image) -> bytes:
    d = ImageChops.difference(a, b)
    r, g, bl = d.split()
    return ImageChops.lighter(ImageChops.lighter(r, g), bl).point(lambda v: 255 if v else 0).tobytes()


def _roll(img: Image.Image, sx: int, sy: int) -> Image.Image:
    """out[x, y] = img[(x + sx) % W, (y + sy) % H]: ``ImageChops.offset(img, -sx, -sy)`` (wrap-around included) as two memcpy pastes
    instead of Pillow's per-pixel offset loop (~100x faster on a 1280x800 mask)."""
    W, H = img.size
    sx %= W
    sy %= H
    if not sx and not sy:
        return img
    out = Image.new(img.mode, img.size)
    for (ox, wx), (oy, wy) in ((a, b) for a in ((0, sx), (W - sx, 0)) for b in ((0, sy), (H - sy, 0))):
        pw, ph = (W - sx if ox == 0 else sx), (H - sy if oy == 0 else sy)
        if pw and ph:
            out.paste(img.crop((wx, wy, wx + pw, wy + ph)), (ox, oy))
    return out


def _remove_long_runs(mask: Image.Image, k: int, horizontal: bool) -> Image.Image:
    """Drop transition pixels that belong to straight runs >= k (box sides/edges, uniform fills)."""
    if not _REFERENCE:
        return _remove_long_runs_fast(mask, k, horizontal)
    shifts = [(i, 0) if horizontal else (0, i) for i in range(k)]
    run = mask
    for dx, dy in shifts[1:]:
        run = ImageChops.darker(run, ImageChops.offset(mask, -dx, -dy))  # pixel starts a run of length k
    grown = run
    for dx, dy in shifts[1:]:
        grown = ImageChops.lighter(grown, ImageChops.offset(run, dx, dy))
    return ImageChops.subtract(mask, grown)


def _window(img: Image.Image, k: int, horizontal: bool, op, sign: int) -> Image.Image:
    """op over img rolled by sign*0..k-1 along one axis, in O(log k) steps (min/max are idempotent, so overlapping windows are exact)."""
    roll = (lambda im, n: _roll(im, sign * n, 0)) if horizontal else (lambda im, n: _roll(im, 0, sign * n))
    cur, n = img, 1                      # cur covers a window of n shifts
    while n * 2 <= k:
        cur, n = op(cur, roll(cur, n)), n * 2
    return cur if n == k else op(cur, roll(cur, (k - n) * 1))  # windows [0,n) and [k-n,k) cover [0,k)


def _remove_long_runs_fast(mask: Image.Image, k: int, horizontal: bool) -> Image.Image:
    """Same result as the reference (including wrap-around at the borders), with log-step windows and memcpy rolls."""
    if k <= 1:
        return ImageChops.subtract(mask, mask)
    run = _window(mask, k, horizontal, ImageChops.darker, 1)       # run[p] = AND mask[p+i], i<k
    grown = _window(run, k, horizontal, ImageChops.lighter, -1)   # grown[p] = OR run[p-i], i<k
    return ImageChops.subtract(mask, grown)


def _edge_profiles(im: Image.Image, cell_hint: int = 16, _masks=None):
    """Glyph-scale transition counts.

    ex[x] = rows where px(x)!=px(x+1); ey[y] = columns where px(y)!=px(y+1); straight runs longer
    than two cells (dialog edges, rules, fills) are removed: they sit exactly ON cell boundaries and
    would hide the text gaps that locate the row phase.
    """
    W, H = im.size
    mh, mv = _masks if (_masks is not None and not _REFERENCE) else _transition_masks(im)
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
    if not _REFERENCE and im.mode == "RGB":
        return _multicolor_fraction_fast(im, p, ph, ox, oy)
    W, H = im.size
    n = bad = 0
    for r in range((H - oy) // ph):
        for c in range((W - ox) // p):
            cc = im.crop((ox + c * p, oy + r * ph, ox + (c + 1) * p, oy + (r + 1) * ph)).getcolors(p * ph)
            if len(cc) > 1:
                n += 1
                bad += len(cc) > 2
    return bad / n if n else 0.0


def _cell_strips(im, ox, oy, w, h, cols, r):
    """Row ``r`` of the grid as ONE column-major byte string: cell ``c`` is the contiguous slice [c*w*h*3:(c+1)*w*h*3].
    One crop+transpose+tobytes per text row instead of one crop+tobytes per cell; two cells are equal iff their slices are."""
    y = oy + r * h
    return im.crop((ox, y, ox + cols * w, y + h)).transpose(Image.TRANSPOSE).tobytes()


def _multicolor_fraction_fast(im, p, ph, ox, oy):
    W, H = im.size
    cols, rows = (W - ox) // p, (H - oy) // ph
    step = p * ph * 3
    memo = {}
    n = bad = 0
    for r in range(rows):
        cm = _cell_strips(im, ox, oy, p, ph, cols, r)
        for c in range(cols):
            key = cm[c * step:(c + 1) * step]
            v = memo.get(key)
            if v is None:
                k = len(im.crop((ox + c * p, oy + r * ph, ox + (c + 1) * p, oy + (r + 1) * ph)).getcolors(p * ph))
                v = memo[key] = (k > 1, k > 2)
            n += v[0]
            bad += v[1]
    return bad / n if n else 0.0


def detect_grid(im: Image.Image, min_cells: int = 12, max_length: float = 0.5, hints=None, _masks=None) -> dict:
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
    ex, ey = _edge_profiles(im, _masks=_masks)
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


def _pack_fast(raw: bytes, w: int, h: int, fg: bytes, bg: bytes) -> bytes:
    """``_pack`` for a TWO-colour RGB cell: one channel in which the colours differ decides every pixel (translate -> '0'/'1' text -> int)."""
    k = next(i for i in range(3) if fg[i] != bg[i])
    bits = raw[k::3].translate(_TT[fg[k]])
    rb = (w + 7) // 8
    pad = rb * 8 - w
    return b"".join((int(bits[y * w:(y + 1) * w], 2) << pad).to_bytes(rb, "big") for y in range(h))


_TT = [bytes.maketrans(bytes(range(256)), bytes(49 if i == v else 48 for i in range(256))) for v in range(256)]


def normalise_cell(crop: Image.Image, rank: dict):
    """-> (kind, key, bg, fg, ncolors).  kind: blank | glyph | multicolor.

    Foreground is the minority colour of a two-colour cell (so light-on-dark, dark-on-grey and
    reverse video give the same bitmap).  Exact two-way ties use the frame-wide colour frequency
    (the more common colour is background).  Cells with 3+ colours are refused (``multicolor``).
    """
    return _normalise(crop, rank)[0]


def _normalise(crop: Image.Image, rank: dict):
    """-> (normalise_cell value, tied).  ``tied``: the result used ``rank`` (frame-wide colour frequency), so it is valid for this frame only."""
    w, h = crop.size
    cols = crop.getcolors(w * h)
    if len(cols) == 1:
        return ("blank", b"", cols[0][1], None, 1), False
    if len(cols) > 2:
        cols.sort(reverse=True)
        return ("multicolor", b"", cols[0][1], None, len(cols)), False
    (n1, c1), (n2, c2) = cols
    tied = n1 == n2
    if tied:
        bgc, fgc = (c1, c2) if rank.get(c1, 0) >= rank.get(c2, 0) else (c2, c1)
    else:
        bgc, fgc = (c1, c2) if n1 > n2 else (c2, c1)
    if not _REFERENCE and crop.mode == "RGB":
        return ("glyph", _pack_fast(crop.tobytes(), w, h, bytes(fgc), bytes(bgc)), bgc, fgc, 2), tied
    return ("glyph", _pack(crop.tobytes(), w, h, bytes(fgc)), bgc, fgc, 2), tied


class Extraction:
    def __init__(self, grid, rows, size):
        self.grid, self.rows, self.size = grid, rows, size  # rows: list[list[Cell]]


def _rank(im) -> dict:
    return {c: n for n, c in (im.getcolors(im.size[0] * im.size[1]) or [])}


class _FrameCache:
    """Per-frame cell cache shared by the many extractions of one read (candidate origins, phase search): cell value by cell bytes + the
    frame-wide colour ranking (computed once).  Never reused for another frame."""
    __slots__ = ("memo", "rank")

    def __init__(self, im):
        self.memo, self.rank = {}, _rank(im)


def _value_rows(im, grid, row_indices, memo, rank):
    """Fast path of ``extract`` without building Cell objects: rows of normalise_cell values (equal cells share ONE tuple object).
    ``memo`` maps the column-major cell bytes to its value and may be shared between calls on the same image and cell size."""
    w, h, ox, oy, cols = grid["cell_w"], grid["cell_h"], grid["origin_x"], grid["origin_y"], grid["cols"]
    step = w * h * 3
    out = []
    for r in row_indices:
        if cols <= 0:
            out.append([])
            continue
        cm = _cell_strips(im, ox, oy, w, h, cols, r)
        row = []
        for c in range(cols):
            key = cm[c * step:(c + 1) * step]
            v = memo.get(key)
            if v is None:
                x, y = ox + c * w, oy + r * h
                v = memo[key] = _normalise(im.crop((x, y, x + w, y + h)), rank)[0]
            row.append(v)
        out.append(row)
    return out


def _cells(vrows, row_indices):
    return [[Cell(r, c, *v) for c, v in enumerate(vr)] for r, vr in zip(row_indices, vrows)]


def extract(im: Image.Image, grid: dict, row_indices=None, _memo=None) -> Extraction:
    """Cells of every row (or only ``row_indices``, used for fast phase search).  ``_memo``: per-FRAME cell cache shared between calls on
    the same image and cell size (the value of a tied cell depends on the frame's colour ranking, so never share it between frames)."""
    w, h, ox, oy = grid["cell_w"], grid["cell_h"], grid["origin_x"], grid["origin_y"]
    idx = list(row_indices if row_indices is not None else range(grid["rows"]))
    if not _REFERENCE and im.mode == "RGB":
        fc = _memo if _memo is not None else _FrameCache(im)
        vrows = _value_rows(im, grid, idx, fc.memo, fc.rank)
        return Extraction(grid, _cells(vrows, idx), im.size)
    memo, rank = {}, _rank(im)
    rows = []
    for r in idx:
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


def reduce_cell(key: bytes, w: int, h: int, k: int):
    """Exact inverse of a k-fold nearest-neighbour enlargement of a packed cell bitmap (row-major, MSB left): the (w//k)x(h//k) cell, or None when
    ``key`` is not such an enlargement (some k x k block is not uniform)."""
    if w % k or h % k:
        return None
    rb, nw, nh = (w + 7) // 8, w // k, h // k
    nrb = (nw + 7) // 8
    rows = [int.from_bytes(key[y * rb:(y + 1) * rb], "big") >> (rb * 8 - w) for y in range(h)]
    out = []
    for y in range(nh):
        blk = rows[y * k:(y + 1) * k]
        if any(r != blk[0] for r in blk):
            return None
        v = 0
        for x in range(nw):
            bits = (blk[0] >> (w - (x + 1) * k)) & ((1 << k) - 1)
            if bits not in (0, (1 << k) - 1):
                return None
            v = (v << 1) | (1 if bits else 0)
        out.append((v << (nrb * 8 - nw)).to_bytes(nrb, "big"))
    return b"".join(out)


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
        self._xcache.clear()     # cross-font / scale-twin caches are derived from the glyph tables
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

    def scale_twins(self, k: int, cell_w: int, cell_h: int) -> dict:
        """Bitmaps of this cell size that a font-file font of cell ``k*cell`` ALSO produces after an exact k-fold nearest-neighbour reduction:
        {hex of the reduced bitmap: {characters that font draws}}.  An image that ``probe_scale`` undoes as a crisp k-fold upscale may be a
        native k*cell screen of such a font (only its k-fold-enlarged glyphs can occur in it): reading it at the reduced size must not treat
        a bitmap as font-exact when such a font draws another character with it."""
        ck = ("twins", k, cell_w, cell_h)
        if ck in self._xcache:
            return self._xcache[ck]
        out = {}
        W, H = cell_w * k, cell_h * k
        for fn in self.fonts_for(W, H):
            f = self.fonts[fn]
            if f.get("origin", {}).get("kind") != "font-file":
                continue
            for hx, g in f["glyphs"].items():
                red = reduce_cell(bytes.fromhex(hx), W, H, k)
                if red is not None:
                    out.setdefault(red.hex(), set()).update(g["c"])
        self._xcache[ck] = out
        return out

    def scale_guard(self, k: int, cell_w: int, cell_h: int):
        """-> (twins, guard_fonts, complete) for an undone k-fold upscale.  A capture that is a k-fold enlargement of the cell may be the native
        screen of a font of ANY cell d*cell with d | k (d >= 2) shown at zoom k/d, so ``twins`` is the union of ``scale_twins(d, ...)`` over every divisor
        d >= 2 of k (k=4: 2,4; k=6: 2,3,6; k=8: 2,4,8); ``guard_fonts`` lists the font-file fonts of those cells that were checked; ``complete`` = a font
        exists for EVERY divisor.  ``complete`` is all the loaded atlas can say: it cannot establish that no other larger font exists."""
        twins, fonts, complete = {}, [], True
        for d in range(2, k + 1):
            if k % d:
                continue
            found = [fn for fn in self.fonts_for(cell_w * d, cell_h * d) if self.fonts[fn].get("origin", {}).get("kind") == "font-file"]
            fonts += found
            complete = complete and bool(found)
            for hx, chars in self.scale_twins(d, cell_w, cell_h).items():
                twins.setdefault(hx, set()).update(chars)
        return twins, sorted(fonts), complete

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
           margin: int = 2, rule_run: int = 4, cursor=None, font_min_share: float = FONT_MIN_SHARE, unscaled: int | None = None) -> dict:
    """Exact lookup of every cell.  max_hamming=0 (default): no tolerant matching at all.

    * ``font_share`` = share of ordinary (not bar/box-like) ink cells the chosen font reads exactly.  Floor: >= ``min_hits`` cells and
      >= ``min_share``, else ``status: "no_font"``.  A font-FILE font additionally needs ``font_share >= font_min_share`` (0.9) before ANY cell
      may be font-exact: between the floors the screen is ``mixed_font`` (mixed content, or another font) and every cell is UNKNOWN.
    * A font-file bitmap that another same-size font draws as a DIFFERENT character (cross-font collision, e.g. VGA vs Unifont box lines) is
      an alias set ``a`` unless the font is proven (share >= font_min_share and no other same-size font above ``PROVEN_RUNNER_UP``).
    * ``F`` font-exact (unique code point); ``H`` unique repertoire character but other code points of the font's full coverage draw the same
      bitmap (``res["aliases"]`` holds the whole set; consumers apply a homoglyph policy); ``a`` several repertoire characters share the bitmap.
    * ``k = unscaled`` (default: ``ex.grid["unscaled"]``, set by ``prepare`` when ``probe_scale`` undid a k-fold upscale; callers cannot forget it):
      a font-exact bitmap that a font-file font of cell ``d*cell`` (any divisor d >= 2 of k) draws as a DIFFERENT character (``Atlas.scale_guard``) is an
      alias set, never F/H.  ``res["scale_unverified"] = k`` and ``res["scale_guard_fonts"]`` are always set when k > 1, and unless a font exists for EVERY
      divisor the remaining F/H cells are capped to ``z`` (CONF_SCALED: shown, not actionable; ``strict_match(trust_scale=True)`` opts in).
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

    k_un = g.get("unscaled", 1) if unscaled is None else unscaled
    twins, guard_fonts, guard_complete = atlas.scale_guard(k_un, w, h) if k_un > 1 else ({}, [], True)
    scale_cap = k_un > 1 and not guard_complete
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
    # A rival font above PROVEN_RUNNER_UP blocks the proof only if it can actually change a reading on THIS screen: for a rival that is itself a
    # font file, some inked cell bitmap must be font-exact in both fonts with DIFFERENT characters.  A rival that explains many cells but draws every
    # one of them with the same character as the chosen font (e.g. a bold face sharing the digits) is not evidence for the rival: the readings are
    # the same whichever font it is.  A learned (non-file) rival keeps the plain share rule.
    blocking = []
    for k, v in fscores.items():
        if k == chosen or not v[1] or v[0] / v[1] <= PROVEN_RUNNER_UP:
            continue
        if not (chosen and atlas.fonts[k].get("origin", {}).get("kind") == "font-file" and atlas.fonts[chosen].get("origin", {}).get("kind") == "font-file"):
            blocking.append(k)
            continue
        if any(_readings_differ(atlas, chosen, k, key) for key in {c.key for row in ex.rows for c in row if c.kind == "glyph"}):
            blocking.append(k)
    proven = is_file and share >= font_min_share and not blocking
    mixed = floor_ok and is_file and share < font_min_share
    if mixed:
        floor_ok = False
    rows, conf, unknown, cells, flags = [], [], [], [], []
    memo = {}
    aliases = {}
    precap = {}     # (row, col) -> F | H for cells capped to class z

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
                        r = memo[key] = _decode_glyph(atlas, chosen, c.key, max_hamming, min_groups, margin, proven, twins.get(c.key.hex()))
                        if scale_cap and r[1] in (CONF_FONT, CONF_HOMO):
                            # keep the class the cell would have had (F or H) and its homoglyph set: ``trust_scale`` restores exactly that, and the
                            # flag pass (rule line / cursor) treats the cell as that class, so the cap never loosens any 1:1 rule
                            r = memo[key] = (r[0], CONF_SCALED, {"reason": "font-exact after an undone %d-fold upscale; no loaded font for every divisor size, so a "
                                                                          "larger font cannot be excluded" % k_un, "would_read": r[0], "precap": r[1],
                                                                 **({"aliases": r[2]["aliases"]} if r[1] == CONF_HOMO else {})})
                    ch, st = r[0], r[1]
                    if st in (CONF_ALIAS, CONF_HOMO) or (st == CONF_SCALED and "aliases" in r[2]):
                        aliases[(c.row, c.col)] = r[2]["aliases"]
                    if st == CONF_SCALED:
                        precap[(c.row, c.col)] = r[2]["precap"]
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
    # flags (rule line, cursor) are computed as if the capped cells kept their pre-cap class
    conf_eff = ["".join(precap.get((r, ci), ch) if ch == CONF_SCALED else ch for ci, ch in enumerate(cf)) for r, cf in enumerate(conf)] if precap else conf
    for r, (row, cf) in enumerate(zip(ex.rows, conf_eff)):
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
            if st in (CONF_UNKNOWN, CONF_CONFLICT, CONF_MULTI, CONF_SINGLE, CONF_FILL, CONF_ALIAS, CONF_HOMO, CONF_SCALED):
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
           "font": chosen, "font_is_file": is_file, "font_share": round(share, 4), "font_runner_up_share": round(runner_up, 4), "font_proven": bool(proven), "font_rivals_blocking": blocking,
           "font_scores": {k: list(v) for k, v in fscores.items()}, "rows": rows, "conf": conf, "flags": flags,
           "unknown_cells": unknown, "max_hamming": max_hamming, "min_groups": min_groups,
           "aliases": {"%d,%d" % k: v for k, v in aliases.items()}}
    if k_un > 1:
        res.update(scale_unverified=k_un, scale_guard_fonts=guard_fonts, scale_capped=scale_cap,
                   scale_precap={"%d,%d" % k: v for k, v in precap.items()})
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


def _readings_differ(atlas, a, b, key) -> bool:
    """Both fonts are font-exact for this bitmap and read it as different character sets."""
    ga, gb = atlas.fonts[a]["glyphs"].get(key.hex()), atlas.fonts[b]["glyphs"].get(key.hex())
    return bool(ga and gb and ga.get("font_exact") and gb.get("font_exact") and set(ga["c"]) != set(gb["c"]))


def _decode_glyph(atlas, font, key, max_hamming, min_groups=2, margin=2, proven=True, twin=None):
    if font is None:
        return UNKNOWN_CHAR, CONF_UNKNOWN, {"reason": "no atlas font for this grid"}
    r = atlas.lookup(font, key, min_groups)
    if twin and r and r[0] in ("font", "homoglyph", "alias"):
        mine = set(atlas.fonts[font]["glyphs"][key.hex()]["c"])
        if set(twin) != mine:
            return UNKNOWN_CHAR, CONF_ALIAS, {"reason": "undone k-fold upscale: a font of the k-fold larger cell draws this bitmap as another character",
                                              "aliases": sorted(mine | set(twin))}
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
def prepare(src, auto_unscale: bool = True, grid_hints=None, atlas: "Atlas | None" = None, _state=None):
    """-> (im, probe, grid_report).  Applies the scale probe; im is None when exact matching is refused.

    Without explicit ``grid_hints`` the cell sizes of the atlas fonts are tried first (a screen that
    tiles as a known font is read as that font); only if none of them fits is the grid searched freely.
    Free search is a convenience for learning: on sparse screens it can prefer a multiple of the true
    cell (e.g. 16x16 for 8x16), which is why the known sizes come first.
    """
    im = load_image(src)
    masks = _transition_masks(im) if min(im.size) >= 3 and not _REFERENCE else None
    probe = probe_scale(im, _masks=masks, _state=_state)
    k = 1
    if probe["integer_upscale"] > 1 and auto_unscale:
        k = probe["integer_upscale"]
        im = unscale(im, k)
        masks = None
    elif probe["resampled"]:
        return None, probe, None
    im, g = _prepare_grid(im, grid_hints, atlas, masks)
    g["unscaled"] = k      # carried to every Extraction (grid_with_origin copies it): decode reads the undone scale from there
    return im, probe, g


def _prepare_grid(im, grid_hints, atlas, masks=None):
    if grid_hints is None and atlas is not None and atlas.fonts:
        sizes = sorted({(f["cell_w"], f["cell_h"]) for f in atlas.fonts.values()})
        g = detect_grid(im, hints=sizes, _masks=masks)
        if g["status"] == "ok":
            return im, g
        free = detect_grid(im, _masks=masks)
        free["hinted_attempt"] = g["reasons"][:1]
        return im, free
    return im, detect_grid(im, hints=grid_hints, _masks=masks)


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
    memo = _FrameCache(im) if not _REFERENCE and im.mode == "RGB" else None
    if memo is not None:
        bestv = None
        for ox, oy in cands:  # candidate origins are scored on shared cell values; Cell objects are built for the winner only
            g2 = grid_with_origin(grid, im.size, ox, oy)
            vr = _value_rows(im, g2, range(g2["rows"]), memo.memo, memo.rank)
            hits = _count_hits_values(vr, g2, atlas, font)
            if best is None or hits > best[1]:
                best, bestv = (None, hits), (g2, vr)
        best = (Extraction(bestv[0], _cells(bestv[1], range(bestv[0]["rows"])), im.size), best[1])
    else:
        for ox, oy in cands:
            ex = extract(im, grid_with_origin(grid, im.size, ox, oy), _memo=memo)
            hits = _count_hits(ex, atlas, font)
            if best is None or hits > best[1]:
                best = (ex, hits)
    ffonts = [fn for fn in atlas.fonts_for(grid["cell_w"], grid["cell_h"])
              if atlas.fonts[fn].get("origin", {}).get("kind") == "font-file" and (font is None or fn == font)]
    if ffonts:
        best = _phase_search_fonts(im, grid, atlas, ffonts, best, time.monotonic() + max_seconds, memo)
    return best


def _phase_search_fonts(im, grid, atlas, ffonts, best, deadline=None, memo=None):
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
            e = extract(im, g, rows, _memo=memo)
            scores.append((max(_count_hits(e, atlas, fn) for fn in ffonts), ox, oy))
    scores.sort(reverse=True)
    top = scores[0]
    second = next((x for x in scores[1:] if (x[1], x[2]) != (top[1], top[2])), (0, 0, 0))
    if top[0] >= 30 and top[0] >= 2 * second[0] + 1:
        ex = extract(im, grid_with_origin(grid, im.size, top[1], top[2]), _memo=memo)
        h = _count_hits(ex, atlas, None)
        if h > best[1]:
            return ex, h
    return best


def _count_hits_values(vrows, g, atlas, font):
    """``_count_hits`` on value rows (see ``_value_rows``): every distinct cell value is looked up once per font."""
    fonts = [font] if font else atlas.fonts_for(g["cell_w"], g["cell_h"])
    cnt = {}
    for vr in vrows:
        for v in vr:
            if v[0] == "glyph":
                cnt[v[1]] = cnt.get(v[1], 0) + 1
    best = 0
    for fn in fonts:
        n = 0
        for key, k in cnt.items():
            r = atlas.lookup(fn, key)
            if r and r[0] in HIT:
                n += k
        best = max(best, n)
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
               return_cells: bool = False, grid_hints=None, _capture=None, **decode_kw) -> dict:
    """image bytes/path/PIL image -> grid result.  Never raises on a bad grid: reports status."""
    state = {}
    im, probe, g = prepare(src, auto_unscale, grid_hints, atlas, _state=state)
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
    if _capture is not None:
        _capture.update(im=im, probe=probe, ex=ex, state=state)
    return res


# --------------------------------------------------------------------------- session reader (geometry cache + dirty cells)
class _Session:
    __slots__ = ("im", "ex", "res", "hist", "parts", "thr", "tied")


class GlyphReader:
    """Reads a SEQUENCE of frames of one console (the canvas-change watcher use case) and re-does only what changed.

    ``read(frame)`` returns the same dict as ``read_image`` plus ``res["session"] = {"path": ..., "dirty_cells": n}``, where path is
      ``full``         nothing reusable: the complete pipeline ran (first frame, other size, or a fallback below);
      ``identical``    the frame equals the previous one pixel for pixel: the previous result is returned (a copy);
      ``incremental``  same size, only some text rows changed: grid geometry and origin are REUSED, the scale-probe numbers are updated
                       from the changed bands only, and only the changed cells are re-extracted; decode runs over the whole screen.
    The incremental path falls back to ``full`` (never to a guess) when: no previous frame, the frame is not RGB-crisp 1:1, more than
    ``max_dirty`` of the text rows changed, pixels outside the grid rows changed, the previous result was not ``ok``, the new frame
    would be an integer upscale or ``resampled``, the new decode is not ``ok``, or the cell is shorter than 5 px.  The safety contract is
    unchanged: every cell is still read by exact lookup, the evidence floor and font share are re-evaluated on the whole new screen, and a
    stale geometry can only make cells UNKNOWN / the screen ``no_font`` (and then triggers the full path), never produce a wrong exact cell.
    Output equality with ``read_image`` on the same frame is asserted by the tests on synthetic edits and on the real 242-frame series
    (the whole result dict except ``session`` and ``grid_report``, which describes the search that established the cached geometry).

    NOT thread-safe: an instance holds mutable per-sequence state (previous frame, cells, colour census); use one reader per sequence and
    thread.  ``reference_mode()`` is a module global and is not thread-safe either.
    """

    def __init__(self, atlas: Atlas, font: str | None = None, max_hamming: int = 0, auto_unscale: bool = True,
                 grid_hints=None, max_dirty: float = 0.25, **decode_kw):
        self.atlas, self.font, self.max_hamming, self.auto_unscale = atlas, font, max_hamming, auto_unscale
        self.grid_hints, self.max_dirty = grid_hints, max_dirty
        self.decode_kw = decode_kw
        self._st: _Session | None = None

    def reset(self):
        self._st = None

    def read(self, src) -> dict:
        im = load_image(src)
        st = self._st
        if st is not None and not _REFERENCE and im.size == st.im.size:
            res = self._incremental(im, st)
            if res is not None:
                return res
        return self._full(im)

    # -- full pipeline, remembering what the incremental path needs
    def _full(self, im) -> dict:
        cap = {}
        res = read_image(im, self.atlas, self.font, self.max_hamming, self.auto_unscale, grid_hints=self.grid_hints, _capture=cap,
                         **self.decode_kw)
        self._st = None
        # no ``integer_upscale == 1`` test: for k > 1 the stored image is the UNSCALED one, so the next raw frame never has the same size and the incremental
        # path is unreachable (removing the test changes no observable result; pinned by test_glyph_reader_stores_no_session_for_an_undone_upscale...)
        if cap and res.get("status") == "ok" and not _REFERENCE:
            ex, state = cap["ex"], cap["state"]
            g = ex.grid
            if g["cell_h"] >= 5 and cap["im"].mode == "RGB":
                st = _Session()
                st.im, st.ex = cap["im"], ex
                st.hist = {c: n for n, c in state["colors"]}
                st.parts, st.thr = list(state["parts"]), state["thr"]
                full = g["cell_w"] * g["cell_h"]
                st.tied = {(c.row, c.col) for row in ex.rows for c in row if c.kind == "glyph" and 2 * _popcount(int.from_bytes(c.key, "big")) == full}
                st.res = copy.deepcopy(res)
                self._st = st
        res["session"] = {"path": "full", "dirty_cells": None}
        return res

    # -- incremental path; None = fall back to the full pipeline
    def _incremental(self, im, st):
        W, H = im.size
        g = st.ex.grid
        w, h, ox, oy, cols, nrows = g["cell_w"], g["cell_h"], g["origin_x"], g["origin_y"], g["cols"], g["rows"]
        diff = ImageChops.difference(im, st.im)
        if diff.getbbox() is None:
            res = copy.deepcopy(st.res)
            res["session"] = {"path": "identical", "dirty_cells": 0}
            return res
        if (oy and diff.crop((0, 0, W, oy)).getbbox()) or (oy + nrows * h < H and diff.crop((0, oy + nrows * h, W, H)).getbbox()):
            return None
        dirty = {}
        for r in range(nrows):
            y = oy + r * h
            if diff.crop((0, y, W, y + h)).getbbox() is None:
                continue
            strip = diff.crop((ox, y, ox + cols * w, y + h))
            dirty[r] = [c for c in range(cols) if strip.crop((c * w, 0, (c + 1) * w, h)).getbbox()]
        if len(dirty) > self.max_dirty * nrows:
            return None
        if _integer_upscale(im) != 1:
            return None
        # the cached tiling must still compress this screen the way a fresh search demands (>= 12 non-blank cells, description length <= 0.5);
        # a sparse screen that a fresh detect_grid would call 'no fixed grid' therefore takes the full path and gets the same refusal
        cs = _colstrips(im.convert("L"), h, oy, 8)
        if not cs[0]:
            return None
        L, _, t = _mdl(cs, w, h, ox)
        if t < 12 or L > 0.5:
            return None
        # scale probe and colour census, updated from the changed bands only (every number is local to a 3-pixel window)
        parts, hist = list(st.parts), dict(st.hist)
        rs = sorted(dirty)
        bands, k = [], 0
        while k < len(rs):
            j = k
            while j + 1 < len(rs) and rs[j + 1] == rs[j] + 1:
                j += 1
            bands.append((max(oy + rs[k] * h - 2, 0), min(oy + (rs[j] + 1) * h + 2, H)))
            k = j + 1
        for ys, ye in bands:
            newc, oldc = im.crop((0, ys, W, ye)), st.im.crop((0, ys, W, ye))
            for i, ((rn, en), (ro, eo)) in enumerate(zip(_ramp_parts(newc), _ramp_parts(oldc))):
                parts[i] = (parts[i][0] + rn - ro, parts[i][1] + en - eo)
            for sign, crop in ((1, newc), (-1, oldc)):
                for n, c in crop.getcolors(W * (ye - ys)):
                    hist[c] = hist.get(c, 0) + sign * n
        hist = {c: n for c, n in hist.items() if n > 0}
        probe = _probe_dict(sorted(((n, c) for c, n in hist.items()), reverse=True), W * H, parts, 1, st.thr)
        if probe["resampled"]:
            return None
        # changed cells, then the unchanged cells whose value depended on the frame-wide colour ranking (exact ties)
        rows = [list(row) for row in st.ex.rows]
        tied = set(st.tied)
        redo = {(r, c) for r, cs in dirty.items() for c in cs}
        redo |= tied
        tied -= redo
        for r, c in sorted(redo):
            x, y = ox + c * w, oy + r * h
            v, is_tied = _normalise(im.crop((x, y, x + w, y + h)), hist)
            rows[r][c] = Cell(r, c, *v)
            if is_tied:
                tied.add((r, c))
        ex2 = Extraction(g, rows, im.size)
        kw = dict(self.decode_kw)
        kw.pop("max_seconds", None)
        res = decode(ex2, self.atlas, self.font, self.max_hamming, **kw)
        if res["status"] != "ok":
            return None
        # the geometry must still explain what changed: an ink cell in the changed area that the atlas does not know at all (stale phase,
        # foreign content) sends the frame through the full pipeline instead
        if any(res["conf"][r][c] == CONF_UNKNOWN and rows[r][c].kind == "glyph" for r, cs in dirty.items() for c in cs):
            return None
        res["probe"] = probe
        res["grid_report"] = copy.deepcopy(st.res["grid_report"])
        st.im, st.ex, st.hist, st.parts, st.tied = im, ex2, hist, parts, tied
        st.res = copy.deepcopy(res)
        res["session"] = {"path": "incremental", "dirty_cells": sum(len(v) for v in dirty.values())}
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


def trust_scale(res: dict) -> dict:
    """Explicit opt-in for a capture that was an undone integer upscale: returns a copy of ``res`` whose capped ``z`` cells get back the class they had
    before the cap (F, or H with its homoglyph set, so the caller's homoglyph policy applies exactly as at 1:1), and records the
    choice (``res["trust_scale"] = True``, ``res["scale_unverified"]`` stays).  Use it only if the capture path is known to be a browser/display zoom of a
    native 1:1 screen (the cap exists because the loaded atlas cannot exclude a larger native font)."""
    out = dict(res)
    pre = res.get("scale_precap", {})
    out["conf"] = ["".join(pre.get("%d,%d" % (r, ci), CONF_FONT) if ch == CONF_SCALED else ch for ci, ch in enumerate(row)) for r, row in enumerate(res.get("conf", []))]
    out["trust_scale"] = True
    return out


def strict_match(res: dict, phrase: str, region=None, cursor=None, homoglyphs: str = "latin-only", min_font_share: float = FONT_MIN_SHARE,
                 trust_scale: bool = False) -> dict:
    """THE decision function for a keystroke consumer.  matched = at least one occurrence whose phrase cells AND both boundary cells are
    F/e/b (homoglyph cells per policy), unflagged, not alias/single/fill/unknown, the screen status is ok and, for a font-file font,
    font_share >= ``min_font_share``.  ``region`` = (row0, row1, col0, col1) restricts the search; ``cursor`` = (row, col) is excluded.
    Returns {"matched", "hits", "candidates" (every textual occurrence with the reasons it failed), "reasons"}."""
    reasons = []
    if res.get("status") != "ok":
        return {"matched": False, "hits": [], "candidates": [], "reasons": ["screen status %r: %s" % (res.get("status"), res.get("message", ""))]}
    if res.get("font_is_file") and res.get("font_share", 0.0) < min_font_share:
        reasons.append("font_share %.3f < %.2f" % (res["font_share"], min_font_share))
    if res.get("scale_capped"):
        if trust_scale:
            res = globals()["trust_scale"](res)
        else:
            reasons.append("capture was an undone %d-fold upscale without a complete larger-font guard: cells are class 'z' (not actionable); "
                           "pass trust_scale=True only if the capture path is a known zoom of a native 1:1 screen" % res["scale_unverified"])
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
    out = {"matched": bool(hits), "hits": hits, "candidates": cands, "reasons": reasons}
    if res.get("scale_unverified"):
        out.update(scale_unverified=res["scale_unverified"], trust_scale=bool(res.get("trust_scale")))
    return out
