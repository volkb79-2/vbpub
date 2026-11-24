#!/usr/bin/env python3
"""P-plat-19g: judge accept-run.sh evidence (design note 4.1).

    accept-eval.py DIR [DIR ...] [--ssh-idle S --ssh-loaded S] [--refault-tol PAGES]

Per run: M = p99(U) + p99(init.scope) + W + 64M, per-unit hot-set proxy = max(active_anon + active_file) in the window,
floor suggestion 1.25 x that, and the 4.1 criteria. Missing data (column, series, probe, manifest) is INCONCLUSIVE and names
itself, never a pass. A DIR may hold `ssh.txt` ("<idle> <loaded>" seconds) instead of the arguments. Several runs: max of each
value, worst verdict. The proposed host-setup.env lines are checked through mdt_capacity's own derivation (install.sh's limits)
and printed commented out unless PASS. Exit 0 PASS, 1 FAIL, 4 INCONCLUSIVE, 2 usage or crash.
"""
from __future__ import annotations

import argparse
import csv
import datetime
import math
import sys
from pathlib import Path

MIB = 1024 ** 2
REQUIRED_UNITS = {"ssh.service", "docker.service", "containerd.service", "systemd-journald.service", "user.slice"}
FLOORED = ("ssh.service", "docker.service", "containerd.service", "systemd-journald.service", "mdt-psi-guard.service", "cgprofiled.service")
try:  # absent when the kit is copied out of the checkout: the install.sh-limit check is then skipped, with a note
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    import mdt_capacity as cap
except Exception:  # pragma: no cover
    cap = None


class Inconclusive(Exception):
    pass


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


def read_env(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if path.is_file():
        for line in path.read_text().splitlines():
            k, sep, v = line.partition("=")
            if sep:
                out[k.strip()] = v.strip().strip("\"'")
    return out


def evaluate(path: Path, ssh: tuple[float | None, float | None], tol: float) -> dict:
    with (path / "samples.csv").open(newline="") as fh:
        rows = list(csv.DictReader(fh))
    win = [r for r in rows if r.get("phase") == "window"]
    if not win:
        raise Inconclusive(f"{path}: no window rows in samples.csv")
    man = read_env(path / "manifest.env")
    out: dict = {"dir": str(path), "criteria": [], "hot": {}, "man": man, "base": read_env(path / "host-setup.env.snapshot")}
    for key in ("U", "init_current", "W"):
        if not series(win, key):
            raise Inconclusive(f"{path}: column {key} has no data in the window")
    out["M"] = p99(series(win, "U")) + p99(series(win, "init_current")) + max(series(win, "W")) + 64 * MIB
    units = sorted({k[: -len(".active")] for k in rows[0] if k.endswith(".active")})
    present = [u for u in units if series(win, f"{u}.active")]
    out["hot"] = {u: max(series(win, f"{u}.active")) for u in present}
    crit = out["criteria"]

    def add(name: str, ok: bool | None, detail: str) -> None:
        crit.append((name, "INCONCLUSIVE" if ok is None else "PASS" if ok else "FAIL", detail))

    def missing(cols: list[str]) -> list[str]:
        return [c for c in cols if not series(win, c)]

    absent = sorted(REQUIRED_UNITS & set(units) - set(present)) + sorted(REQUIRED_UNITS - set(units))
    # dev-interactive is measured only to size its MemoryMin (4.1), so it is not a refault subject
    rcols = [f"{u}.refault_{k}" for u in present if u != "dev-interactive.slice" for k in ("anon", "file")]
    if absent or missing(rcols):
        add("refault", None, "no data for " + ", ".join(absent + missing(rcols)))
    else:
        bad = {u: delta(win, f"{u}.refault_anon") + delta(win, f"{u}.refault_file") for u in present if u != "dev-interactive.slice"}
        bad = {u: d for u, d in bad.items() if d > tol}
        add("refault", not bad, "floored-unit refault delta in the window " + (", ".join(f"{u}=+{d:.0f}" for u, d in bad.items()) or f"<= {tol:.0f} pages"))
    for sl in ("system", "user"):
        col = f"{sl}.psi_some_avg10"
        peak = max(series(win, col), default=None)
        add(f"{sl}.slice pressure", None if peak is None else peak <= 1.0, f"no data in column {col}" if peak is None else f"memory.pressure some avg10 max {peak:.2f} (limit 1)")
    idle, loaded = ssh
    add("ssh latency", None if idle is None or loaded is None else loaded <= 2 * idle,
        "no --ssh-idle/--ssh-loaded (or ssh.txt): not measured" if idle is None or loaded is None else f"loaded {loaded:g}s vs 2 x idle {2 * idle:g}s")
    ocols = ["vmstat_oom_kill"] + [f"{t}.{k}" for t in ("dev", "gates", "bg") for k in ("oom", "oom_kill")]
    if missing(ocols):
        add("no oom", None, "no data for " + ", ".join(missing(ocols)))
    else:
        kills = {"vmstat": delta(rows, "vmstat_oom_kill")}
        kills.update({t: delta(rows, f"{t}.oom") + delta(rows, f"{t}.oom_kill") for t in ("dev", "gates", "bg")})
        hit = {k: v for k, v in kills.items() if v > 0}
        add("no oom", not hit, "oom/oom_kill delta " + (", ".join(f"{k}=+{v:.0f}" for k, v in hit.items()) or "0 in vmstat, dev.slice and both tiers"))
    # The load was real: dev.slice's LOCAL max events rose (memory.events is hierarchical and counts the probes' own limits) or
    # dev.slice sat at its ceiling on most window ticks, both probes still ran at the end, and swap writeback reached the disk.
    if missing(["dev.max_local", "dev.current", "dev.mem_max"]):
        add("load was real", None, "no data for " + ", ".join(missing(["dev.max_local", "dev.current", "dev.mem_max"])))
    else:
        busy = 0
        for a, b in zip(win, win[1:]):
            cur, top = fnum(b, "dev.current"), fnum(b, "dev.mem_max")
            busy += (fnum(b, "dev.max_local") or 0) > (fnum(a, "dev.max_local") or 0) or bool(cur and top and cur >= 0.95 * top)
        rise, frac = delta(win, "dev.max_local"), busy / max(1, len(win) - 1)
        alive = [man.get(f"probe_{p}_end") == "true false" for p in ("gates", "bg")]
        add("load was real", rise > 0 and frac >= 0.5 and all(alive) or None,
            f"dev.slice local max events +{rise:.0f}, rising or at the ceiling on {frac:.0%} of window ticks; probes at the end: gates={man.get('probe_gates_end', 'unknown')}, bg={man.get('probe_bg_end', 'unknown')}")
    pout = delta(win, "pswpout") if series(win, "pswpout") else None
    add("disk swap exercised", None if pout is None else pout > 0 or None,
        "no pswpout data" if pout is None else f"pswpout +{pout:.0f} pages, zswpout +{delta(win, 'zswpout'):.0f}, zswpwb +{delta(win, 'zswpwb'):.0f} in the window (zswap alone absorbed it if pswpout is 0)")
    if man.get("kernel_oom_lines", "0") not in ("0", ""):
        add("kernel oom log", None, f"{man['kernel_oom_lines']} kernel OOM line(s) in kernel-oom.txt: attribute them (probe or another container) before judging")
    return out


def size(n: float) -> str:
    return f"{math.ceil(n / MIB)}M"


def proposal(runs: list[dict], rows: list[str], margin: float, inter: float | None) -> tuple[list[str], str | None, int | None, int | None]:
    """The env lines, a refusal text when mdt_capacity (install.sh's own derivation) would refuse them, the proposed
    host keep K in bytes (floors + the measured margin with any KEEP pin of the snapshot dropped, clamped to never be
    below the tested K) and the tested K (both None when they could not be derived)."""
    base, man = runs[0]["base"], runs[0]["man"]
    notes: list[str] = []
    if cap is None or "mem_total" not in man:
        return ["# note: mdt_capacity or mem_total missing: the install.sh limits were NOT checked"], None, None, None
    mt, st = int(man["mem_total"]), int(man.get("swap_total", 0))
    try:
        tested = cap.dedicated_memory(base, mem_total=mt, swap_total=st)
        new = dict(base, DEV_HOST_MEMORY_MARGIN=size(margin), DEV_INTERACTIVE_MEMORY_LOW=base.get("DEV_INTERACTIVE_MEMORY_LOW") or "2.5G")
        if rows:
            new["DEV_HOST_MEMORY_MIN"] = " ".join(rows)
        if inter:
            new["DEV_INTERACTIVE_MEMORY_MIN"] = size(inter)
        new.pop("DEV_HOST_MEMORY_KEEP", None)   # a KEEP pin in the snapshot would win over floors + margin: the proposal is the unpinned K
        got = cap.dedicated_memory(new, mem_total=mt, swap_total=st)
    except cap.CapacityError as exc:
        return notes, str(exc), None, None
    # Never below the tested K, whatever the verdict (P-plat-19h r2 N1): under a pin the tested K was the pin, not floors +
    # tested margin, so floors + the measured margin can come out lower; a lower K is untested.
    pin = (base.get("DEV_HOST_MEMORY_KEEP") or "").strip()
    clamped = got.keep < tested.keep
    final_keep, final_max = (tested.keep, tested.maximum) if clamped else (got.keep, got.maximum)
    if pin and clamped:
        notes.append(f"# tested at the pin DEV_HOST_MEMORY_KEEP={pin}; floors + margin alone give {got.keep / MIB:.0f}M, below the tested K: keep the pin")
    elif pin:
        notes.append(f"# tested at the pin DEV_HOST_MEMORY_KEEP={pin}; the proposed K below is floors + margin without it: remove the pin to apply these lines")
    elif clamped:
        notes.append(f"# floors + margin alone give {got.keep / MIB:.0f}M, below the tested K: the tested K stands")
    notes.append(f"# K {tested.keep / MIB:.0f}M tested -> {final_keep / MIB:.0f}M proposed, dev.slice MemoryMax {tested.maximum / MIB:.0f}M -> {final_max / MIB:.0f}M")
    if final_keep > tested.keep:
        notes.append("# K rose: re-run once at the new K (raise and repeat, design note 4.1)")
    return notes, None, final_keep, tested.keep


def main(argv: list[str] | None = None) -> int:
    try:
        return _main(argv)
    except Exception as exc:  # a crash is never a verdict about the host
        print(f"accept-eval: internal error {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


def _main(argv: list[str] | None) -> int:
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
        if not (d / "samples.csv").is_file():
            print(f"accept-eval: {d}/samples.csv missing", file=sys.stderr)
            return 2
        ssh = (args.ssh_idle, args.ssh_loaded)
        if (d / "ssh.txt").is_file():
            idle, loaded = (float(x) for x in (d / "ssh.txt").read_text().split())
            ssh = (idle, loaded)
        try:
            runs.append(evaluate(d, ssh, args.refault_tol))
        except Inconclusive as exc:
            print(exc)
            print("VERDICT INCONCLUSIVE")
            return 4
    for run in runs:
        print(f"== {run['dir']}")
        for name, state, detail in run["criteria"]:
            print(f"{state:12} {name}: {detail}")
            if state == "FAIL":
                verdict = "FAIL"
            elif state == "INCONCLUSIVE" and verdict != "FAIL":
                verdict = "INCONCLUSIVE"
    hot: dict[str, float] = {}
    for r in runs:
        for u, h in r["hot"].items():
            hot[u] = max(hot.get(u, 0), h)
    table = dict(cap.DEDICATED_HOST_MEMORY_MIN_DEFAULTS) if cap else {}
    rows = [f"{u}={size(1.25 * h)}" for u, h in sorted(hot.items()) if u in FLOORED and 1.25 * h > table.get(u, 0)]
    tested_margin = (cap.DEDICATED_MEMORY_MARGIN_DEFAULT if cap else 0)
    if cap and runs[0]["base"].get("DEV_HOST_MEMORY_MARGIN"):
        tested_margin = cap.parse_size_bytes(runs[0]["base"]["DEV_HOST_MEMORY_MARGIN"]) or tested_margin
    measured = max(r["M"] for r in runs)
    margin = max(measured, tested_margin, 64 * MIB)   # never below the margin the run was made at: a lower K is untested
    low = (cap.parse_size_bytes(runs[0]["base"].get("DEV_INTERACTIVE_MEMORY_LOW") or "2.5G") or 0) if cap else 0
    inter = None
    if "dev-interactive.slice" in hot:
        want, floor = 1.25 * hot["dev-interactive.slice"], 512 * MIB
        inter = min(max(want, floor), low) if low else max(want, floor)
    notes, refused, keep, tested_keep = proposal(runs, rows, margin, inter)
    if refused:
        print(f"INCONCLUSIVE proposal: install.sh's derivation would refuse these values: {refused}")
        verdict = "FAIL" if verdict == "FAIL" else "INCONCLUSIVE"
    if cap is None:   # table, tested margin and Low would all be 0: the lines could not be checked, so no PASS
        print("INCONCLUSIVE proposal: mdt_capacity is not importable (kit copied out of the checkout); the env lines are NOT checked against install.sh")
        verdict = "FAIL" if verdict == "FAIL" else "INCONCLUSIVE"
    pre = "" if verdict == "PASS" else "# "
    # P-plat-19h: the pin advice names the proposed K (floors + measured margin, snapshot pin dropped), never a fixed 1280M
    if keep and tested_keep and keep > tested_keep:
        pin = f"DEV_HOST_MEMORY_KEEP={size(keep)}, the proposed K (floors + measured margin, never below the tested one)"
    elif keep:   # the measurement gives no K above the one that was tested (e.g. a pin): no number, never a lower one
        pin = f"DEV_HOST_MEMORY_KEEP above the tested {size(tested_keep)} (design note 4.1); the measurement does not give a value"
    else:
        pin = "DEV_HOST_MEMORY_KEEP at the proposed K (not derivable here: see the notes below)"
    print("== host-setup.env" + ("" if verdict == "PASS" else f" (NOT to be applied: {verdict}; on FAIL pin {pin}, design note 8)"))
    for u, h in sorted(hot.items()):
        print(f"# hot set {u}: {h / MIB:.1f}M -> floor {size(1.25 * h)}" + (f" (table {table[u] // MIB}M)" if u in table else ""))
    for note in notes:
        print(note)
    print(f'{pre}DEV_HOST_MEMORY_MIN="{" ".join(rows)}"' if rows else f"{pre}DEV_HOST_MEMORY_MIN=")
    if inter:
        print(f"{pre}DEV_INTERACTIVE_MEMORY_MIN={size(inter)}" + ("   # clamped to DEV_INTERACTIVE_MEMORY_LOW" if low and 1.25 * hot["dev-interactive.slice"] > low else ""))
    print(f"{pre}DEV_HOST_MEMORY_MARGIN={size(margin)}   # measured M = p99(U) + p99(init.scope) + W + 64M = {measured / MIB:.1f}M")
    print(f'{pre}DEV_HOST_MEMORY_MEASURED="{datetime.datetime.now(datetime.timezone.utc):%Y-%m-%d} {" ".join(str(d) for d in args.dirs)}"')
    print(f"VERDICT {verdict}")
    return {"PASS": 0, "FAIL": 1}.get(verdict, 4)


if __name__ == "__main__":
    sys.exit(main())
