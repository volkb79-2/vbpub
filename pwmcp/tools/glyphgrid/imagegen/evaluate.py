"""Score an engine's Reading against the exact ground truth, per cell.

Truth cells: ink cell = non-space char.  Cursor cells are not scored.  Ambiguous ink cells (font draws another repertoire
character identically) are excluded from the accuracy denominator but a wrong confident char there still counts.
 exact           engine char == truth (ink cell)
 unknown         engine declined (or emitted nothing) for an ink cell
 wrong_confident engine emitted a different char for an ink cell, a blank for an ink cell, a non-blank where the truth is
                 blank, or any non-blank outside the grid (spurious)
"""
import glob
import json
import os

COUNTS = ("images", "refused", "ink", "exact", "unknown", "wrong", "spurious", "ambiguous")


def score(truth, reading):
    geo, sc = truth["geometry"], truth["screen"]
    cw, ch = truth["font"]["cell_w"] * geo["sx"], truth["font"]["cell_h"] * geo["sy"]
    cells = {}
    n = dict.fromkeys(COUNTS, 0)
    n["images"] = 1
    if reading.status != "ok":
        n["refused"] = 1
    wrong_cells = []
    for (x0, y0, x1, y1, out) in reading.emissions:
        c = int(((x0 + x1) / 2 - geo["ox"]) // cw)
        r = int(((y0 + y1) / 2 - geo["oy"]) // ch)
        if not (0 <= r < sc["rows"] and 0 <= c < sc["cols"]):
            if out not in (None, " "):
                n["spurious"] += 1
                n["wrong"] += 1
            continue
        prev = cells.get((r, c), "")
        cells[(r, c)] = out if prev == "" or prev == out else "\0"  # two emissions disagreeing -> wrong below
    for r, row in enumerate(sc["cells"]):
        for c, (tch, _fg, _bg, flags) in enumerate(row):
            if "C" in flags:
                continue
            got = cells.get((r, c))  # None: nothing / declined
            if tch == " ":
                if got not in (None, " "):
                    n["wrong"] += 1
                    n["spurious"] += 1
                continue
            amb = "A" in flags
            if amb:
                n["ambiguous"] += 1
            else:
                n["ink"] += 1
            if got is None:
                n["unknown"] += 0 if amb else 1
            elif got == tch:
                n["exact"] += 0 if amb else 1
            else:
                n["wrong"] += 1
                wrong_cells.append((r, c, tch, got))
    return n, wrong_cells


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
    """-> {group value: counts dict}, details list.  ``group``: label | font | screen."""
    agg, details = {}, []
    for png_path, t in load_suite(directory):
        with open(png_path, "rb") as f:
            reading = engine.read(f.read(), t)
        n, wrong = score(t, reading)
        key = {"label": t["label"], "font": t["font"]["id"], "screen": t["screen"]["name"].split("-")[0]}[group]
        a = agg.setdefault(key, dict.fromkeys(COUNTS, 0))
        for k in COUNTS:
            a[k] += n[k]
        details.append({"image": os.path.basename(png_path), "status": reading.status, "counts": n,
                        "wrong_examples": [{"row": r, "col": c, "truth": tc, "got": g} for r, c, tc, g in wrong[:8]]})
    return agg, details


def pct(a, b):
    return "%.1f%%" % (100.0 * a / b) if b else "n/a"


def markdown(agg, title="group"):
    rows = ["| %s | images | refused | ink cells | exact | unknown | wrong-confident (spurious) |" % title, "|---|---:|---:|---:|---:|---:|---:|"]
    for k, a in agg.items():
        rows.append("| `%s` | %d | %d | %d | %s | %s | %d (%d) |" % (k, a["images"], a["refused"], a["ink"], pct(a["exact"], a["ink"]),
                                                                  pct(a["unknown"], a["ink"]), a["wrong"], a["spurious"]))
    return "\n".join(rows)
