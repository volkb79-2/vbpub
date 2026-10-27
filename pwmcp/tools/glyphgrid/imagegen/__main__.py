"""CLI:  python -m imagegen {generate,suite,atlas,eval} ...   (run from pwmcp/tools/glyphgrid or with it on PYTHONPATH)"""
import argparse
import json
import os
import sys

from .engines import EngineMissing, EngineSkipped, make
from .evaluate import conf_summary, evaluate, markdown
from .fonts import BITMAP_IDS, DEFAULT_ATLAS
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
    atlas = build_atlas(a.font or list(BITMAP_IDS))
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
    agg, details, confs = evaluate(a.suite, eng, a.by)
    print("engine: %s%s" % (eng.name, "  atlas: %s" % (a.atlas or DEFAULT_ATLAS) if eng.name.startswith("glyph-grid") else ""))
    print(markdown(agg, a.by))
    if confs["exact"] or confs["wrong"]:
        print(conf_summary(confs))
    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump({"engine": eng.name, "by": a.by, "groups": agg, "images": details}, f, indent=1, ensure_ascii=False)
    return 0


def cmd_read(a):
    eng = make(a.engine, a.atlas)
    with open(a.image, "rb") as f:
        rd = eng.read(f.read(), json.load(open(a.truth)) if a.truth else None)
    print("status:", rd.status, rd.note)
    if rd.emissions:
        rows = {}
        for e in rd.emissions:
            rows.setdefault(round(e[1]), []).append((e[0], e[4] if e[4] is not None else "\ufffd"))
        for y in sorted(rows):
            print("".join(c for _, c in sorted(rows[y])))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="imagegen", description=__doc__)
    sp = ap.add_subparsers(dest="cmd", required=True)
    g = sp.add_parser("generate", help="one image + truth")
    g.add_argument("--font", required=True, help="vga-8x16 | unifont-16 | edk2-8x19 | vector-16 (DejaVu Mono else Pillow bundled) | dejavu-mono-16 | pillow-default-16 | ttf:<path>:<px>")
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
    r = sp.add_parser("read", help="read one image with an engine and print status + text (reproducer for known defects)")
    r.add_argument("image")
    r.add_argument("--engine", default="glyph-grid", choices=("glyph-grid", "glyph-grid-oracle"))
    r.add_argument("--atlas")
    r.add_argument("--truth", help="truth JSON (required by the oracle engine)")
    r.set_defaults(fn=cmd_read)
    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    raise SystemExit(main())
