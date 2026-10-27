"""CLI:  python -m imagegen {generate,suite,atlas,eval} ...   (run from pwmcp/tools/glyphgrid or with it on PYTHONPATH)"""
import argparse
import json
import os
import sys

from .engines import EngineMissing, EngineSkipped, make
from .evaluate import evaluate, markdown
from .fonts import DEFAULT_ATLAS, available_ids
from .render import write
from .suite import SCREENS, build_suite


def cmd_generate(a):
    base = write(a.out, a.font, a.screen, a.transform, a.seed)
    print(base + ".png")
    print(base + ".json")
    return 0


def cmd_suite(a):
    fonts = a.font or None
    n, skipped = build_suite(a.out, fonts, a.screen or SCREENS, None, a.seed, progress=None)
    print("wrote %d images to %s" % (len(n), a.out))
    for s in skipped:
        print("SKIPPED font %(font)s: %(reason)s" % s)
    return 0


def cmd_atlas(a):
    from .atlasbuild import build_atlas
    atlas = build_atlas(a.font or [f for f in available_ids() if "dejavu" not in f])
    atlas.save(a.out)
    print("wrote", a.out)
    return 0


def cmd_eval(a):
    try:
        eng = make(a.engine, a.atlas, prescale=a.prescale) if a.engine == "tesseract" else make(a.engine, a.atlas)
    except EngineSkipped as e:
        print("SKIP:", e)
        return 0
    except EngineMissing as e:
        print("FAIL:", e, file=sys.stderr)
        return 2
    agg, details = evaluate(a.suite, eng, a.by)
    print("engine: %s%s" % (eng.name, "  atlas: %s" % (a.atlas or DEFAULT_ATLAS) if eng.name.startswith("glyph-grid") else ""))
    print(markdown(agg, a.by))
    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump({"engine": eng.name, "by": a.by, "groups": agg, "images": details}, f, indent=1, ensure_ascii=False)
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="imagegen", description=__doc__)
    sp = ap.add_subparsers(dest="cmd", required=True)
    g = sp.add_parser("generate", help="one image + truth")
    g.add_argument("--font", required=True, help="vga-8x16 | unifont-16 | edk2-8x19 | dejavu-mono-16 | ttf:<path>:<px>")
    g.add_argument("--screen", required=True, help="alphabet | installer | random")
    g.add_argument("--transform", action="append", default=[], help="name:args, repeatable, applied in order")
    g.add_argument("--seed", type=int, default=0)
    g.add_argument("-o", "--out", required=True)
    g.set_defaults(fn=cmd_generate)
    s = sp.add_parser("suite", help="standard matrix: fonts x screens x transforms")
    s.add_argument("--font", action="append")
    s.add_argument("--screen", action="append")
    s.add_argument("--seed", type=int, default=1)
    s.add_argument("-o", "--out", required=True)
    s.set_defaults(fn=cmd_suite)
    t = sp.add_parser("atlas", help="learn a glyphgrid atlas from generator output (bitmap fonts)")
    t.add_argument("--font", action="append")
    t.add_argument("-o", "--out", required=True)
    t.set_defaults(fn=cmd_atlas)
    e = sp.add_parser("eval", help="run an engine over a suite")
    e.add_argument("--suite", required=True)
    e.add_argument("--engine", required=True, choices=("glyph-grid", "glyph-grid-oracle", "tesseract"))
    e.add_argument("--atlas", help="glyph-grid atlas (default: the committed font-exact fixture)")
    e.add_argument("--by", default="label", choices=("label", "font", "screen"))
    e.add_argument("--prescale", type=int, default=3, help="tesseract: Lanczos enlargement before OCR")
    e.add_argument("--json", help="write full per-image results")
    e.set_defaults(fn=cmd_eval)
    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    raise SystemExit(main())
