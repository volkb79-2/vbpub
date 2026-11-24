#!/usr/bin/env python3
"""P-plat-19g: judge accept-run.sh evidence (design note 4.1).

    accept-eval.py DIR [DIR ...] [--ssh-idle S --ssh-loaded S] [--refault-tol PAGES]

Per run: M = p99(U) + p99(init.scope) + W + 64M, per-unit hot-set proxy = max(active_anon + active_file) in the
window, floor suggestion = 1.25 x that, and the 4.1 acceptance criteria. Several runs: the max of each value, the worst
verdict. A DIR may hold `ssh.txt` ("<idle> <loaded>" seconds) in place of the arguments. Output: host-setup.env lines
(commented unless PASS) and one `VERDICT` line. Exit 0 PASS, 1 FAIL, 4 INCONCLUSIVE, 2 usage.
"""
from __future__ import annotations

import argparse
import csv
import datetime
import math
import sys
from pathlib import Path

MIB = 1024 ** 2
MARGIN_SLACK = 64 * MIB
UNIT_FLOORED = ("ssh.service", "docker.service", "containerd.service", "systemd-journald.service", "mdt-psi-guard.service", "cgprofiled.service")
try:  # the table the host derives from; absent when the kit is copied out of the checkout
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from mdt_capacity import DEDICATED_HOST_MEMORY_MIN_DEFAULTS as TABLE
except Exception:  # pragma: no cover - fallback keeps the evaluator standalone
    TABLE = {}


def p99(values: list[float]) -> float:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(0.99 * len(ordered)) - 1)]


def fnum(row: dict, key: str) -> float | None:
    try:
        return float(row[key])
    except (KeyError, ValueError, TypeError):
        return None


def series(rows: list[dict], key: str) -> list[float]:
    return [v for v in (fnum(r, key) for r in rows) if v is not None]


def delta(rows: list[dict], key: str) -> float:
    vals = series(rows, key)
    return vals[-1] - vals[0] if vals else 0.0


def evaluate(path: Path, ssh: tuple[float | None, float | None], refault_tol: float) -> dict:
    with (path / "samples.csv").open(newline="") as fh:
        rows = list(csv.DictReader(fh))
    win = [r for r in rows if r.get("phase") == "window"]
    if not win:
        raise SystemExit(f"{path}: no window rows in samples.csv")
    out: dict = {"dir": str(path), "criteria": [], "hot": {}}
    w = max(series(win, "W"))
    out["M"] = p99(series(win, "U")) + p99(series(win, "init_current")) + w + MARGIN_SLACK
    units = sorted({k[: -len(".active")] for k in rows[0] if k.endswith(".active")})
    for u in units:
        act = [fnum(r, f"{u}.active") for r in win]
        act = [a for a in act if a is not None]
        if act:
            out["hot"][u] = max(act)
    crit = out["criteria"]

    def add(name: str, ok: bool | None, detail: str) -> None:
        crit.append((name, "INCONCLUSIVE" if ok is None else "PASS" if ok else "FAIL", detail))

    # dev-interactive is measured only to size its MemoryMin (4.1), so it is not a refault subject
    bad = {u: delta(win, f"{u}.refault_anon") + delta(win, f"{u}.refault_file") for u in units if u != "dev-interactive.slice"}
    bad = {u: d for u, d in bad.items() if d > refault_tol}
    add("refault", not bad, "floored-unit refault delta in the window " + (", ".join(f"{u}=+{d:.0f}" for u, d in bad.items()) or f"<= {refault_tol:.0f} pages"))
    for sl in ("system", "user"):
        peak = max(series(win, f"{sl}.psi_some_avg10") or [0.0])
        add(f"{sl}.slice pressure", peak <= 1.0, f"memory.pressure some avg10 max {peak:.2f} (limit 1)")
    idle, loaded = ssh
    if idle is None or loaded is None:
        add("ssh latency", None, "no --ssh-idle/--ssh-loaded (or ssh.txt): not measured")
    else:
        add("ssh latency", loaded <= 2 * idle, f"loaded {loaded:g}s vs 2 x idle {2 * idle:g}s")
    kills = {"vmstat": delta(rows, "vmstat_oom_kill")}
    for t in ("dev", "gates", "bg"):
        kills[t] = delta(rows, f"{t}.oom") + delta(rows, f"{t}.oom_kill")
    hit = {k: v for k, v in kills.items() if v > 0}
    add("no oom", not hit, "oom/oom_kill delta " + (", ".join(f"{k}=+{v:.0f}" for k, v in hit.items()) or "0 in vmstat, dev.slice and both tiers"))
    rise = delta(win, "dev.max")
    add("load was real", rise > 0, f"dev.slice memory.events max rose by {rise:.0f} in the window" + ("" if rise > 0 else " (the load never hit the ceiling)"))
    return out


def size(n: float) -> str:
    return f"{math.ceil(n / MIB)}M"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("dirs", nargs="+", type=Path)
    ap.add_argument("--ssh-idle", type=float)
    ap.add_argument("--ssh-loaded", type=float)
    ap.add_argument("--refault-tol", type=float, default=100.0, help="pages of floored-unit refault allowed in the window (default 100)")
    try:
        args = ap.parse_args(argv)
    except SystemExit as exc:
        return 2 if exc.code else 0
    runs, verdict = [], "PASS"
    for d in args.dirs:
        ssh = (args.ssh_idle, args.ssh_loaded)
        if (d / "ssh.txt").is_file():
            parts = (d / "ssh.txt").read_text().split()
            ssh = (float(parts[0]), float(parts[1])) if len(parts) == 2 else ssh
        if not (d / "samples.csv").is_file():
            print(f"accept-eval: {d}/samples.csv missing", file=sys.stderr)
            return 2
        runs.append(evaluate(d, ssh, args.refault_tol))
    for run in runs:
        print(f"== {run['dir']}")
        for name, state, detail in run["criteria"]:
            print(f"{state:12} {name}: {detail}")
            if state == "FAIL":
                verdict = "FAIL"
            elif state == "INCONCLUSIVE" and verdict != "FAIL":
                verdict = "INCONCLUSIVE"
    margin = max(r["M"] for r in runs)
    hot = {u: max(r["hot"].get(u, 0) for r in runs) for u in runs[0]["hot"]}
    rows = [f"{u}={size(1.25 * h)}" for u, h in sorted(hot.items()) if u in UNIT_FLOORED and 1.25 * h > TABLE.get(u, 0)]
    pre = "" if verdict == "PASS" else "# "
    print("== host-setup.env" + ("" if verdict == "PASS" else f" (NOT to be applied: {verdict}; on FAIL pin DEV_HOST_MEMORY_KEEP=1280M per design note 8)"))
    for u, h in sorted(hot.items()):
        print(f"# hot set {u}: {h / MIB:.1f}M -> floor {size(1.25 * h)}" + (f" (table {TABLE[u] // MIB}M)" if u in TABLE else ""))
    print(f'{pre}DEV_HOST_MEMORY_MIN="{" ".join(rows)}"' if rows else f"{pre}DEV_HOST_MEMORY_MIN=")
    if "dev-interactive.slice" in hot:
        print(f"{pre}DEV_INTERACTIVE_MEMORY_MIN={size(1.25 * hot['dev-interactive.slice'])}")
    print(f"{pre}DEV_HOST_MEMORY_MARGIN={size(max(margin, MARGIN_SLACK))}   # M = p99(U) + p99(init.scope) + W + 64M = {margin / MIB:.1f}M")
    print(f'{pre}DEV_HOST_MEMORY_MEASURED="{datetime.datetime.now(datetime.timezone.utc):%Y-%m-%d} {" ".join(str(d) for d in args.dirs)}"')
    print(f"VERDICT {verdict}")
    return {"PASS": 0, "FAIL": 1}.get(verdict, 4)


if __name__ == "__main__":
    sys.exit(main())
