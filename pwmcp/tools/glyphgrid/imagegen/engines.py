"""OCR engines behind one interface: ``engine.read(png_bytes) -> Reading``.

A Reading is a status plus a list of emissions ``(x0, y0, x1, y1, ch)`` in INPUT-image pixels; ``ch`` is a confident
character (``" "`` = confident blank) or ``None`` (the engine declined: UNKNOWN).  Missing optional dependencies FAIL by
default (``PWMCP_ALLOW_MISSING_TEST_DEPS=1`` turns that into a clean skip), like pwmcp/tests/_testdeps.py.
"""
import io
import os
import shutil
import subprocess
import tempfile

from PIL import Image

from . import _PARENT  # noqa: F401
import glyphgrid as gg  # noqa: E402

ALLOW_VAR = "PWMCP_ALLOW_MISSING_TEST_DEPS"


class EngineMissing(Exception):
    """A required optional dependency is absent and the developer did not opt in to skipping."""


class EngineSkipped(Exception):
    """Absent, and PWMCP_ALLOW_MISSING_TEST_DEPS=1: skip cleanly."""


def missing(what, reason):
    msg = ("%s is not available (%s). Missing dependencies FAIL by default; set %s=1 to skip this engine." % (what, reason, ALLOW_VAR))
    if os.environ.get(ALLOW_VAR) == "1":
        raise EngineSkipped(msg)
    raise EngineMissing(msg)


class Reading:
    def __init__(self, status, emissions, note=""):
        self.status, self.emissions, self.note = status, emissions, note


class GlyphGridEngine:
    name = "glyph-grid"

    def __init__(self, atlas):
        self.atlas = atlas if isinstance(atlas, gg.Atlas) else gg.Atlas.load(atlas)

    def read(self, png, truth=None):
        res = gg.read_image(io.BytesIO(png) if isinstance(png, bytes) else png, self.atlas)
        return self._emit(res)

    def _emit(self, res):
        if res["status"] != "ok":
            return Reading(res["status"], [], res.get("message", ""))
        g, k = res["grid"], (res.get("probe") or {}).get("integer_upscale", 1) or 1
        cw, ch = g["cell_w"], g["cell_h"]
        em = []
        for r, row in enumerate(res["rows"]):
            for c, chv in enumerate(row):
                st = res["conf"][r][c]
                x0, y0 = (g["origin_x"] + c * cw) * k, (g["origin_y"] + r * ch) * k
                if st in gg.EXACTLIKE:
                    out = chv
                elif st == gg.CONF_BLANK:
                    out = " "
                else:
                    out = None
                em.append((x0, y0, x0 + cw * k, y0 + ch * k, out))
        return Reading("ok", em)


class OracleGridEngine(GlyphGridEngine):
    """glyph-grid's cell matcher with grid detection and the scale probe REMOVED: the true grid comes from the ground truth
    (only when the geometry is an integer scale and offset), the image is point-sampled back to 1:1 and decoded with the public
    ``extract``/``decode``.  An upper bound for the matcher alone; the gap to ``glyph-grid`` is the cost of grid detection."""

    name = "glyph-grid-oracle"

    def read(self, png, truth=None):
        if truth is None:
            raise ValueError("the oracle engine needs the ground truth")
        geo, sc, fo = truth["geometry"], truth["screen"], truth["font"]
        k = geo["sx"]
        if geo["sx"] != geo["sy"] or k != int(k) or geo["ox"] % k or geo["oy"] % k:
            return Reading("oracle_n/a", [], "non-integer geometry")
        k = int(k)
        im = Image.open(io.BytesIO(png)).convert("RGB")
        if k > 1:
            im = im.resize((im.width // k, im.height // k), Image.NEAREST)  # point sample: the crisp case undoes exactly
        ox, oy = int(geo["ox"]) // k, int(geo["oy"]) // k
        grid = {"cell_w": fo["cell_w"], "cell_h": fo["cell_h"], "origin_x": ox, "origin_y": oy,
                "cols": min(sc["cols"], (im.width - ox) // fo["cell_w"]), "rows": min(sc["rows"], (im.height - oy) // fo["cell_h"]), "status": "ok"}
        res = gg.decode(gg.extract(im, grid), self.atlas)
        res["grid"], res["probe"] = grid, {"integer_upscale": k}
        return self._emit(res)


class TesseractEngine:
    """Tesseract via pytesseract or the CLI.  Words are spread over columns by their boxes; every emitted character is
    treated as CONFIDENT (that is the measurement: how often is it confidently wrong).  ``prescale`` is the usual
    Lanczos enlargement fed to Tesseract (its tiny-text accuracy is poor without one); boxes are mapped back."""

    name = "tesseract"

    def __init__(self, prescale=3, psm=6, lang="eng"):
        self.prescale, self.psm, self.lang = prescale, psm, lang
        self.binary = shutil.which("tesseract")
        if self.binary is None:
            missing("the `tesseract` binary", "install tesseract-ocr; the OCR sidecar image has it")

    def _run_tsv(self, im):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "in.png")
            im.save(p)
            cp = subprocess.run(["nice", "-n", "10", self.binary, p, "stdout", "-l", self.lang, "--psm", str(self.psm),
                                 "-c", "preserve_interword_spaces=1", "tsv"], capture_output=True, text=True, timeout=120)
        if cp.returncode != 0:
            raise RuntimeError("tesseract failed: %s" % cp.stderr.strip()[:300])
        return cp.stdout

    def read(self, png, truth=None):
        im = Image.open(io.BytesIO(png)).convert("RGB")
        s = self.prescale
        big = im.resize((im.width * s, im.height * s), Image.LANCZOS) if s > 1 else im
        return Reading("ok", parse_tsv(self._run_tsv(big), s))


def parse_tsv(tsv, scale=1):
    """Tesseract TSV -> emissions: each character of a word gets an equal slice of the word box (columns are inferred, not seen)."""
    em = []
    lines = tsv.splitlines()
    for line in lines[1:]:
        f = line.split("\t")
        if len(f) < 12 or f[0] != "5" or not f[11].strip():
            continue
        left, top, w, h = (int(x) / scale for x in f[6:10])
        word = f[11].strip()
        for i, chv in enumerate(word):
            em.append((left + w * i / len(word), top, left + w * (i + 1) / len(word), top + h, chv))
    return em


def make(name, atlas=None, **kw):
    if name in ("glyph-grid", "glyphgrid"):
        from .fonts import DEFAULT_ATLAS
        return GlyphGridEngine(atlas or DEFAULT_ATLAS)
    if name == "glyph-grid-oracle":
        from .fonts import DEFAULT_ATLAS
        return OracleGridEngine(atlas or DEFAULT_ATLAS)
    if name == "tesseract":
        return TesseractEngine(**kw)
    raise ValueError("unknown engine %r (glyph-grid | tesseract)" % name)
