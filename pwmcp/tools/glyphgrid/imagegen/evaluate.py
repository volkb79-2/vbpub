"""Score an engine's Reading against the exact ground truth, per cell.

Truth cells: ink cell = non-space char.  Not scored: cursor cells, and cells not fully inside the content still visible after the
transforms (``geometry.clip``: ``offset`` crops the far edge).  Ambiguous cells (flag A: the font draws another repertoire
character identically, or its fg/bg complement, or a blank for a solid cell) are scored like every other ink cell; they are also
counted in ``ambiguous`` / ``wrong_ambiguous`` so a confident answer there (necessarily a guess) is visible as its own class.
 answered        ink cells for which the engine emitted any confident character (including a confident blank)
 exact           engine char == truth
 unknown         engine declined or emitted nothing
 wrong           engine emitted a different char for an ink cell, a blank for an ink cell, a non-blank where the truth is blank, or a
                 non-blank outside the grid (``spurious``).  wrong / answered is the figure to read, never "wrong out of all cells".
Emissions: ``(x0, y0, x1, y1, ch[, conf])``; ``ch`` None = declined.  A declined emission never conflicts with a confident one;
two different confident emissions in one cell are a wrong answer.
"""
import glob
import json
import os

COUNTS = ("images", "answered_images", "refused", "ink", "answered", "exact", "unknown", "wrong", "spurious", "ambiguous",
          "wrong_ambiguous", "cropped")
EPS = 1e-6


def _inside(clip, x0, y0, x1, y1):
    return x0 >= clip[0] - EPS and y0 >= clip[1] - EPS and x1 <= clip[2] + EPS and y1 <= clip[3] + EPS


def score(truth, reading):
    """-> (counts, wrong cells [dict row, col, truth, got, flags, conf], confidences {'exact': [...], 'wrong': [...]})."""
    geo, sc = truth["geometry"], truth["screen"]
    fw, fh = truth["font"]["cell_w"], truth["font"]["cell_h"]
    cw, ch = fw * geo["sx"], fh * geo["sy"]
    clip = geo.get("clip") or [0, 0, 1e18, 1e18]
    n = dict.fromkeys(COUNTS, 0)
    n["images"] = 1
    n["refused"] = 0 if reading.status == "ok" else 1
    cells, conf = {}, {}
    wrong, confs = [], {"exact": [], "wrong": []}
    for em in reading.emissions:
        x0, y0, x1, y1, out = em[:5]
        cf = em[5] if len(em) > 5 else None
        c = int(((x0 + x1) / 2 - geo["ox"]) // cw)
        r = int(((y0 + y1) / 2 - geo["oy"]) // ch)
        if out is None:
            continue  # declined: nothing was said, so nothing can conflict
        if not (0 <= r < sc["rows"] and 0 <= c < sc["cols"]):
            if out != " ":
                n["spurious"] += 1
                n["wrong"] += 1
                if cf is not None:
                    confs["wrong"].append(cf)
            continue
        prev = cells.get((r, c))
        cells[(r, c)] = out if prev is None or prev == out else "\0"  # disagreeing confident emissions -> wrong below
        if cf is not None:
            conf[(r, c)] = cf
    answered_any = False
    for r, row in enumerate(sc["cells"]):
        for c, (tch, _fg, _bg, flags) in enumerate(row):
            if "C" in flags:
                continue
            x0, y0 = geo["ox"] + c * cw, geo["oy"] + r * ch
            if not _inside(clip, x0, y0, x0 + cw, y0 + ch):
                n["cropped"] += 1
                continue
            got = cells.get((r, c))
            if tch == " ":
                if got not in (None, " "):
                    n["wrong"] += 1
                    n["spurious"] += 1
                    if (r, c) in conf:
                        confs["wrong"].append(conf[(r, c)])
                continue
            amb = "A" in flags
            n["ink"] += 1
            n["ambiguous"] += amb
            if got is not None:
                n["answered"] += 1
                answered_any = True
            if got is None:
                n["unknown"] += 1
            elif got == tch:
                n["exact"] += 1
                if (r, c) in conf:
                    confs["exact"].append(conf[(r, c)])
            else:
                n["wrong"] += 1
                n["wrong_ambiguous"] += amb
                wrong.append({"row": r, "col": c, "truth": tch, "got": got, "flags": flags, "conf": conf.get((r, c))})
                if (r, c) in conf:
                    confs["wrong"].append(conf[(r, c)])
    n["answered_images"] = 1 if answered_any else 0
    return n, wrong, confs


def load_suite(directory):
    out = []
    for jp in sorted(glob.glob(os.path.join(directory, "*.json"))):
        if os.path.basename(jp) == "suite.json":
            continue
        with open(jp, encoding="utf-8") as f:
            t = json.load(f)
        if t.get("format", "").startswith("imagegen-truth/"):
            out.append((jp[:-5] + ".png", t))
    return out


def evaluate(directory, engine, group="label"):
    """-> ({group value: counts}, details list, confidences).  ``group``: label | font | screen."""
    agg, details, confs = {}, [], {"exact": [], "wrong": []}
    for png_path, t in load_suite(directory):
        with open(png_path, "rb") as f:
            reading = engine.read(f.read(), t)
        n, wrong, cf = score(t, reading)
        key = {"label": t["label"], "font": t["font"]["id"], "screen": t["screen"]["name"].split("-")[0]}[group]
        a = agg.setdefault(key, dict.fromkeys(COUNTS, 0))
        for k in COUNTS:
            a[k] += n[k]
        for k in confs:
            confs[k] += cf[k]
        details.append({"image": os.path.basename(png_path), "status": reading.status, "counts": n, "wrong_examples": wrong[:8]})
    return agg, details, confs


def pct(a, b):
    return "%.1f%%" % (100.0 * a / b) if b else "n/a"


def markdown(agg, title="group"):
    rows = ["| %s | images (answered / refused) | ink cells | answered cells | exact (of ink) | unknown (of ink) | wrong-confident | wrong per answered |" % title,
            "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for k, a in agg.items():
        rows.append("| `%s` | %d (%d / %d) | %d | %d | %s | %s | %d (spurious %d, on ambiguous cells %d) | %s |" % (
            k, a["images"], a["answered_images"], a["refused"], a["ink"], a["answered"], pct(a["exact"], a["ink"]),
            pct(a["unknown"], a["ink"]), a["wrong"], a["spurious"], a["wrong_ambiguous"], pct(a["wrong"], a["answered"] + a["spurious"])))
    return "\n".join(rows)


def conf_summary(confs):
    def q(v):
        if not v:
            return "none"
        v = sorted(v)
        return "n=%d min %.0f median %.0f max %.0f" % (len(v), v[0], v[len(v) // 2], v[-1])
    return "engine confidence of exact cells: %s; of wrong cells: %s" % (q(confs["exact"]), q(confs["wrong"]))
