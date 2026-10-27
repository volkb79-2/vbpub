"""Transform chain.  Spec ``name:arg,arg`` (e.g. ``nn-upscale:2``, ``scale:1.5,lanczos``, ``jpeg:60``).

Every transform is a pure function of (image, params, rng); the rng is seeded from (seed, position, name), so a chain is
deterministic.  ``geometry`` tracks where the original grid went (scale and offset in output pixels) so an evaluator can
map engine output back to truth cells; it stays exact only through the geometric transforms.
"""
import io
import random

from PIL import Image, ImageChops, ImageFilter

FILTERS = {"nearest": Image.NEAREST, "bilinear": Image.BILINEAR, "bicubic": Image.BICUBIC, "lanczos": Image.LANCZOS, "box": Image.BOX}


def _num(s):
    try:
        return int(s)
    except ValueError:
        try:
            return float(s)
        except ValueError:
            return s


def parse(spec):
    name, _, rest = spec.partition(":")
    return name, [_num(x) for x in rest.split(",")] if rest else []


def _corner(im):
    return im.getpixel((0, 0))


def _lut(fn):
    return [max(0, min(255, int(round(fn(v))))) for v in range(256)] * 3


def t_nn_upscale(im, p, rng, geo):
    k = int(p[0])
    geo.update(sx=geo["sx"] * k, sy=geo["sy"] * k, ox=geo["ox"] * k, oy=geo["oy"] * k)
    return im.resize((im.width * k, im.height * k), Image.NEAREST)


def t_scale(im, p, rng, geo):
    f, flt = float(p[0]), (p[1] if len(p) > 1 else "lanczos")
    w, h = max(1, round(im.width * f)), max(1, round(im.height * f))
    fx, fy = w / im.width, h / im.height
    geo.update(sx=geo["sx"] * fx, sy=geo["sy"] * fy, ox=geo["ox"] * fx, oy=geo["oy"] * fy)
    return im.resize((w, h), FILTERS[flt])


def t_blur(im, p, rng, geo):
    return im.filter(ImageFilter.GaussianBlur(float(p[0])))


def t_boxblur(im, p, rng, geo):
    return im.filter(ImageFilter.BoxBlur(float(p[0])))


def t_jpeg(im, p, rng, geo):
    b = io.BytesIO()
    im.save(b, "JPEG", quality=int(p[0]))
    return Image.open(io.BytesIO(b.getvalue())).convert("RGB")


def t_quant(im, p, rng, geo):
    return im.quantize(colors=int(p[0]), method=Image.MEDIANCUT, dither=Image.NONE).convert("RGB")


def t_gamma(im, p, rng, geo):
    g = float(p[0])
    return im.point(_lut(lambda v: 255 * (v / 255.0) ** g))


def t_contrast(im, p, rng, geo):
    c = float(p[0])
    return im.point(_lut(lambda v: 128 + (v - 128) * c))


def t_offset(im, p, rng, geo):
    """Shift content by (dx, dy) inside the same canvas; the exposed edge takes the colour of the top-left pixel."""
    dx, dy = int(p[0]), int(p[1] if len(p) > 1 else p[0])
    out = Image.new("RGB", im.size, _corner(im))
    out.paste(im, (dx, dy))
    geo.update(ox=geo["ox"] + dx, oy=geo["oy"] + dy)
    return out


def t_letterbox(im, p, rng, geo):
    """Add borders l,t,r,b (one value = all four) in the colour of the top-left pixel; the canvas grows."""
    l, t, r, b = (int(x) for x in (p * 4 if len(p) == 1 else p))
    out = Image.new("RGB", (im.width + l + r, im.height + t + b), _corner(im))
    out.paste(im, (l, t))
    geo.update(ox=geo["ox"] + l, oy=geo["oy"] + t)
    return out


def t_noise(im, p, rng, geo):
    """Zero-mean noise of roughly the given standard deviation (levels), triangular distribution, per channel."""
    sigma = float(p[0])
    n = im.width * im.height * 3
    a = Image.frombytes("RGB", im.size, rng.randbytes(n))
    b = Image.frombytes("RGB", im.size, rng.randbytes(n))
    d = ImageChops.subtract(a, b, 1.0, 128).point([int(round(128 + (v - 128) * sigma / 104.0)) for v in range(256)] * 3)
    return ImageChops.add(im, d, 1.0, -128)


def t_subpixel(im, p, rng, geo):
    """Horizontal shift by a fraction of a pixel (resolution 1/3 px); ``fringe`` adds ClearType-like R/B channel offsets."""
    shift = float(p[0])
    fringe = len(p) > 1 and str(p[1]) in ("fringe", "1", "true")
    w, h = im.size
    big = im.resize((w * 3, h), Image.NEAREST)
    s = int(round(shift * 3))
    r, g, b = big.split()

    def sh(ch, d):
        out = Image.new("L", big.size, ch.getpixel((0, 0)))
        out.paste(ch, (d, 0))
        return out
    if fringe:
        big = Image.merge("RGB", (sh(r, s - 1), sh(g, s), sh(b, s + 1)))
    else:
        big = Image.merge("RGB", (sh(r, s), sh(g, s), sh(b, s)))
    geo.update(ox=geo["ox"] + s / 3.0, exact=not fringe and geo["exact"])
    return big.resize((w, h), Image.BOX)


TRANSFORMS = {
    "nn-upscale": t_nn_upscale, "scale": t_scale, "blur": t_blur, "boxblur": t_boxblur, "jpeg": t_jpeg, "quant": t_quant,
    "gamma": t_gamma, "contrast": t_contrast, "offset": t_offset, "letterbox": t_letterbox, "noise": t_noise, "subpixel": t_subpixel,
}


def apply_chain(im, specs, seed=0):
    """-> (image, geometry, [{spec, name, params}]).  ``geometry``: sx, sy (output px per source px), ox, oy (px), exact."""
    geo = {"sx": 1.0, "sy": 1.0, "ox": 0.0, "oy": 0.0, "exact": True}
    log = []
    im = im.convert("RGB")
    for i, spec in enumerate(specs):
        name, params = parse(spec)
        if name not in TRANSFORMS:
            raise ValueError("unknown transform %r (known: %s)" % (name, ", ".join(sorted(TRANSFORMS))))
        rng = random.Random("%s:%d:%s" % (seed, i, name))
        im = TRANSFORMS[name](im, params, rng, geo)
        log.append({"spec": spec, "name": name, "params": params, "size": list(im.size)})
    return im, geo, log
