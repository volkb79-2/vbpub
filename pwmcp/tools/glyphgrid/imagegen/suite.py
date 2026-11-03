"""The standard matrix: fonts x screens x transform chains -> a directory of PNG + JSON pairs (scratch, never committed)."""
import json
import os

from .fonts import FontUnavailable, available_ids, load_font
from .render import write

SCREENS = ("alphabet", "installer", "random")
# (name, chain).  Ordered from lossless to hostile; every chain is deterministic from the seed.
TRANSFORMS = [
    ("clean", []),
    ("nn-upscale:2", ["nn-upscale:2"]),
    ("nn-upscale:3", ["nn-upscale:3"]),
    ("offset:1,1", ["offset:1,1"]),
    ("letterbox:1", ["letterbox:1"]),
    ("scale:1.5,lanczos", ["scale:1.5,lanczos"]),
    ("scale:0.75,bilinear", ["scale:0.75,bilinear"]),
    ("scale:1.25,bicubic", ["scale:1.25,bicubic"]),
    ("blur:0.8", ["blur:0.8"]),
    ("boxblur:1", ["boxblur:1"]),
    ("jpeg:90", ["jpeg:90"]),
    ("jpeg:50", ["jpeg:50"]),
    ("quant:16", ["quant:16"]),
    ("gamma:1.4", ["gamma:1.4"]),
    ("contrast:0.6", ["contrast:0.6"]),
    ("noise:8", ["noise:8"]),
    ("subpixel:0.33", ["subpixel:0.33"]),
    ("subpixel:0.33,fringe", ["subpixel:0.33,fringe"]),
    ("nn-upscale:2+jpeg:75", ["nn-upscale:2", "jpeg:75"]),
    ("nn-upscale:2+blur:0.8", ["nn-upscale:2", "blur:0.8"]),
]


def build_suite(outdir, fonts=None, screens=SCREENS, transforms=None, seed=1, atlas_path=None, progress=None):
    fonts = fonts or available_ids(atlas_path)
    chains = transforms if transforms is not None else [c for _, c in TRANSFORMS]
    index, skipped = [], []
    for fid in fonts:
        try:
            font = load_font(fid, atlas_path)
        except FontUnavailable as e:
            skipped.append({"font": fid, "reason": str(e)})
            continue
        for sc in screens:
            for chain in chains:
                base = write(outdir, font, sc, chain, seed)
                index.append(os.path.basename(base))
                if progress:
                    progress(base)
    with open(os.path.join(outdir, "suite.json"), "w", encoding="utf-8") as f:
        json.dump({"seed": seed, "fonts": fonts, "screens": list(screens), "images": index, "skipped_fonts": skipped}, f, indent=1)
    return index, skipped
