"""Fonts for the generator: bitmap cell fonts (atlas / fontfiles) and a vector font (Pillow, anti-aliased)."""
import hashlib
import json
import os

from PIL import Image, ImageDraw, ImageFont

from . import _PARENT
import fontfiles as ff  # noqa: E402  (glyphgrid's own public module; _PARENT is on sys.path)

DEFAULT_ATLAS = os.path.normpath(os.path.join(_PARENT, "..", "..", "tests", "fixtures", "glyphgrid", "fonts.atlas.json"))
BITMAP_IDS = ("vga-8x16", "unifont-16", "edk2-8x19")
TTF_CANDIDATES = ("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",)


class FontUnavailable(Exception):
    pass


def _sha_file(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def _shared_sets(keys):
    by = {}
    for ch, k in keys.items():
        by.setdefault(k, set()).add(ch)
    return {k: v for k, v in by.items() if len(v) > 1}


class CellFont:
    """A fixed-cell bitmap font.  ``draw(ch)`` -> 'L' coverage mask of cell size (255 = ink, 0/255 only)."""

    kind = "bitmap"

    def __init__(self, fid, cell_w, cell_h, sha256, source, keys):
        self.id, self.cell_w, self.cell_h, self.sha256, self.source = fid, cell_w, cell_h, sha256, source
        self._set_keys(keys)

    def _set_keys(self, keys):
        self._keys = dict(keys)  # char -> bytes
        self._masks = {}
        self._shared = _shared_sets(self._keys)

    def pick(self, ch):
        """``ch`` if the font draws it, else a plain-ASCII stand-in (box drawing -> +-|, blocks -> #.:)."""
        from .screens import FALLBACK
        return ch if self.has(ch) else FALLBACK.get(ch, "?")

    def repertoire(self):
        return sorted(self._keys)

    def has(self, ch):
        return ch == " " or ch in self._keys

    def key(self, ch):
        return self._keys[ch]

    def _complement(self, k):
        rb = (self.cell_w + 7) // 8
        mask = (((1 << self.cell_w) - 1) << (rb * 8 - self.cell_w)).to_bytes(rb, "big")
        return bytes(b ^ mask[i % rb] for i, b in enumerate(k))

    def ambiguous_with(self, ch):
        """Other characters no reader can tell from ``ch``: the same bitmap (alias set), or its fg/bg COMPLEMENT (a cell reader
        takes the minority colour as ink, so polarity is lost: lower half block vs upper half block), or a blank for a solid cell."""
        k = self._keys.get(ch)
        if k is None:
            return []
        out = set(self._shared.get(k, set())) - {ch}
        if self.kind == "bitmap":
            ck = self._complement(k)
            out |= {c for c, v in self._keys.items() if v == ck and c != ch}
            if not any(ck):
                out.add(" ")  # solid cell == blank cell of the ink colour
        return sorted(out)

    def _make_mask(self, ch):
        return Image.frombytes("1", (self.cell_w, self.cell_h), self._keys[ch]).convert("L")

    def draw(self, ch):
        m = self._masks.get(ch)
        if m is None:
            m = self._make_mask(ch) if ch in self._keys else Image.new("L", (self.cell_w, self.cell_h), 0)
            self._masks[ch] = m
        return m

    def describe(self):
        return {"id": self.id, "kind": self.kind, "cell_w": self.cell_w, "cell_h": self.cell_h, "sha256": self.sha256, "source": self.source}


class VectorFont(CellFont):
    """TrueType/OTF font drawn by Pillow with anti-aliasing; each glyph is CENTRED in a cell as wide as the widest advance, so a
    proportional font works too (the vector screens test refusal / no misread, not font fidelity)."""

    kind = "vector"

    def __init__(self, fid, font, source, sha256, px):
        import math
        self._font = font
        asc, desc = font.getmetrics()
        cps = list(range(0x21, 0x7F)) + list(range(0x2500, 0x25A0))
        cw = max(1, max(int(math.ceil(font.getlength(chr(cp)))) for cp in cps if cp < 0x7F))
        super().__init__(fid, cw, asc + desc, sha256, "%s @%dpx" % (source, px), {})
        keys = {}
        for cp in cps:
            m = self._render(chr(cp))
            if m.getbbox() is not None:
                keys[chr(cp)] = m.tobytes()
        self._set_keys(keys)

    def _render(self, ch):
        m = Image.new("L", (self.cell_w, self.cell_h), 0)
        ImageDraw.Draw(m).text((self.cell_w / 2.0, 0), ch, font=self._font, fill=255, anchor="ma")
        return m

    def _make_mask(self, ch):
        return self._render(ch)


def _dejavu(px, fid=None):
    p = find_ttf()
    if p is None:
        raise FontUnavailable("DejaVu Sans Mono not found (looked in %s)" % ", ".join(TTF_CANDIDATES))
    return VectorFont(fid or "dejavu-mono-%d" % px, ImageFont.truetype(p, px), os.path.basename(p), _sha_file(p), px)


def _pillow_default(px, fid=None):
    """Pillow's bundled TrueType font (Pillow >= 10.1, FreeType compiled in): no system package needed."""
    from PIL import __version__
    try:
        f = ImageFont.load_default(size=px)
    except Exception as e:  # noqa: BLE001  (old Pillow: TypeError; no FreeType: OSError/ImportError)
        raise FontUnavailable("Pillow's bundled TrueType font is unavailable (needs Pillow >= 10.1 built with FreeType): %s" % e)
    if not isinstance(f, ImageFont.FreeTypeFont):
        raise FontUnavailable("ImageFont.load_default(size=...) did not return a TrueType font (Pillow built without FreeType?)")
    ident = "pillow-bundled:%s:%s:%s" % (__version__, f.getname()[0], f.getname()[1])
    return VectorFont(fid or "pillow-default-%d" % px, f, "Pillow %s bundled %s" % (__version__, " ".join(f.getname())),
                      hashlib.sha256(ident.encode()).hexdigest(), px)


def load_vector(px=16):
    """DejaVu Sans Mono when present, else Pillow's bundled font; raises FontUnavailable (a FAIL, not a skip, in the tests) if neither."""
    try:
        return _dejavu(px)
    except FontUnavailable as e1:
        try:
            return _pillow_default(px)
        except FontUnavailable as e2:
            raise FontUnavailable("no vector font available: %s; %s" % (e1, e2))


def from_atlas(fid, atlas_path=None):
    """Bitmap font from a font-exact glyphgrid atlas entry ``font:<fid>`` (the committed fixture by default)."""
    path = atlas_path or DEFAULT_ATLAS
    with open(path, "rb") as f:
        raw = f.read()
    ent = json.loads(raw)["fonts"].get("font:" + fid)
    if ent is None:
        raise FontUnavailable("atlas %s has no font:%s" % (path, fid))
    keys = {}
    for hx, g in ent["glyphs"].items():
        for ch in g["c"]:
            keys[ch] = bytes.fromhex(hx)
    org = ent.get("origin", {})
    return CellFont(fid, ent["cell_w"], ent["cell_h"], org.get("sha256", hashlib.sha256(raw).hexdigest()),
                    "atlas %s (font file %s)" % (os.path.basename(path), org.get("file", "?")), keys)


def from_fontfile(fid, fontfile, repertoire="ascii+box"):
    """Bitmap font from a ``fontfiles.FontFile`` (PSF/PF2/hex/BDF/raw/EDK2), via the public ``fontfiles.render_cell``."""
    keep = ff.REPERTOIRES[repertoire]
    keys = {}
    for g in fontfile.glyphs:
        k = ff.render_cell(fontfile, g)
        if not any(k):
            continue
        for cp in g[0]:
            if keep(cp):
                keys[chr(cp)] = k
    return CellFont(fid, fontfile.cell_w, fontfile.cell_h, fontfile.sha256, "font file %s" % os.path.basename(fontfile.path or fontfile.name), keys)


def find_ttf():
    for p in TTF_CANDIDATES:
        if os.path.isfile(p):
            return p
    return None


def load_font(fid, atlas_path=None):
    """``vga-8x16`` | ``unifont-16`` | ``edk2-8x19`` | ``vector-<px>`` (DejaVu Mono if present else Pillow's bundled font; the truth
    records which) | ``dejavu-mono-<px>`` | ``pillow-default-<px>`` | ``ttf:<path>:<px>``."""
    if fid in BITMAP_IDS:
        return from_atlas(fid, atlas_path)
    if fid.startswith("vector-"):
        return load_vector(int(fid.rsplit("-", 1)[1]))
    if fid.startswith("dejavu-mono-"):
        return _dejavu(int(fid.rsplit("-", 1)[1]))
    if fid.startswith("pillow-default-"):
        return _pillow_default(int(fid.rsplit("-", 1)[1]))
    if fid.startswith("ttf:"):
        p, _, px = fid[4:].rpartition(":")
        if not os.path.isfile(p):
            raise FontUnavailable("font file %s not found" % p)
        return VectorFont(fid, ImageFont.truetype(p, int(px)), os.path.basename(p), _sha_file(p), int(px))
    raise FontUnavailable("unknown font id %r" % fid)


def available_ids(atlas_path=None):
    out = []
    for fid in BITMAP_IDS + ("vector-16",):
        try:
            out.append(load_font(fid, atlas_path).id)
        except FontUnavailable:
            pass
    return out
