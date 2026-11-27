#!/usr/bin/env python3
"""P-plat-19m: judge the acceptance squeeze run's evidence (DESIGN-ACCEPT-KIT-V2.md 3, 4, 5, 6).

    accept-eval.py DIR [DIR ...]                 the run's verdict, the DAMON floors, M and the proposed K
    accept-eval.py --live-step DIR --step N      one step's verdict into DIR/steps/N.env (accept-run.sh's ladder)

Per step every 3 criterion is PASS/FAIL/INCONCLUSIVE and missing data names itself -- never a pass (v1's rule).
`cause=` carries the ladder's decision: **only `load_not_real` continues it** (2.5); missing hard data stops it.
Refaults are INFO only (3.4). The run's verdict derives the DAMON floors (hotset.csv, x1.10, 4.3, and printed only
when this run proved them), re-checks M over the **deepest passing** step (5) and applies the ONE K rule
`K_proposed = max(F_new + M, k_last_pass)`. A FAIL is a step-0 FAIL and prints **no K at all**: pinning the K that
just failed is the one advice that must never appear, and `1280M` appears nowhere. A v1 evidence directory (no
`kit_version=2`) is refused with exit 2, because v2 criteria on v1 data would turn a missing squeeze into a pass.
Exit 0 PASS, 1 FAIL, 4 INCONCLUSIVE, 2 usage, a crash or a v1 directory.
"""
from __future__ import annotations

import argparse
import csv
import datetime
import json
import math
import sys
from pathlib import Path

MIB = 1024 ** 2
FLOOR_FACTOR = 1.10             # 4.3: one page per region per 400 ms plus merge/split churn
SQUEEZE_TOLERANCE = 128 * MIB   # 3.5: dev.slice/memory.current >= its own max - 128M
PRESSURE_LIMIT = 1.0            # 3.1: some and full avg10 on all three slices
LATENCY_SLACK = 0.5             # 3.2/3.3: max(2 x the step-0 p95, the step-0 p95 + 0.5 s)
MEM_AVAILABLE_FLOOR = 128 * MIB  # 2.4's guard, judged per step as well
SLICES = ("system", "user", "cgprofile")   # the col prefix; the design's table names the cgroups
LIVENESS = ("sshd", "journald", "dockerd", "containerd", "cgprofiled", "psi_guard")
# 3.3: two probes' pass rule is NOT latency, so the latency rule must never be applied to them (H2). `psi_guard`
# passes on "active + its ticks advance" (a tick counter, never a stopwatch; its recorded seconds are its own probe
# duration and carry no verdict), and `cgprofiled` in its `degraded` form (CLI absent -> `systemctl is-active`) is
# INFO by design. Judging either against a step-0 baseline turned a healthy daemon into a fabricated step-3/4 FAIL.
LATENCY_EXEMPT = ("psi_guard", "cgprofiled")
FLOORED = ("ssh.service", "docker.service", "containerd.service", "systemd-journald.service",
           "mdt-psi-guard.service", "cgprofiled.service")
NOT_REFALTED = ("dev-interactive.slice",)   # measured only to size its MemoryMin (4.2), never refault-judged
try:  # absent when the kit is copied out of the checkout: the install.sh-limit check is then reported, never faked
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    import mdt_capacity as cap
except Exception:  # pragma: no cover
    cap = None


class Inconclusive(Exception):
    pass


class V1Evidence(Exception):
    pass


# ---------- small readers ----------

def pctl(values: list[float], q: float) -> float:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(q * len(ordered)) - 1)]


def p95(values: list[float]) -> float:
    return pctl(values, 0.95)


def p99(values: list[float]) -> float:
    return pctl(values, 0.99)


def fnum(row: dict, key: str) -> float | None:
    try:
        return float(row[key])
    except (KeyError, ValueError, TypeError):
        return None


def series(rows: list[dict], key: str) -> list[float]:
    return [v for v in (fnum(r, key) for r in rows) if v is not None]


def delta(rows: list[dict], key: str) -> float | None:
    vals = series(rows, key)
    return vals[-1] - vals[0] if len(vals) >= 2 else None


def read_env(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if path.is_file():
        for line in path.read_text().splitlines():
            k, sep, v = line.partition("=")
            if sep:
                out[k.strip()] = v.strip().strip("\"'")
    return out


def read_csv(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    with path.open(newline="") as fh:
        return list(csv.DictReader(fh))


def size(n: float) -> str:
    return f"{math.ceil(n / MIB)}M"


def rows_of(rows: list[dict], step: int, phase: str | None = None) -> list[dict]:
    out = [r for r in rows if str(r.get("step", "")) == str(step)]
    return [r for r in out if r.get("phase") == phase] if phase else out


def probes_of(probes: list[dict], step: int, name: str | None = None) -> list[dict]:
    out = [p for p in probes if str(p.get("step", "")) == str(step)]
    return [p for p in out if p.get("probe") == name] if name else out


def seconds_of(probes: list[dict], step: int, name: str) -> list[float]:
    vals = []
    for p in probes_of(probes, step, name):
        try:
            vals.append(float(p.get("seconds", "")))
        except (TypeError, ValueError):
            continue
    return vals


def detail_int(probes: list[dict], step: int, name: str, key: str) -> int | None:
    for p in reversed(probes_of(probes, step, name)):
        for token in (p.get("detail") or "").split():
            if token.startswith(f"{key}="):
                tail = token[len(key) + 1:]
                return int(tail) if tail.isdigit() else None
    return None


def ssh_base(probes: list[dict]) -> float | None:
    """3.2: the max of 3 ssh samples at step 0 before any raise, its slow sample included."""
    vals = seconds_of(probes, 0, "ssh")[:3]
    return max(vals) if vals else None


def step_base(probes: list[dict]) -> dict[str, float | None]:
    """3.3: the step-0 p95 of each liveness probe is that probe's latency baseline."""
    return {name: (vals and p95(vals) or None) for name, vals in
            ((n, seconds_of(probes, 0, n)) for n in LIVENESS)}


def over_limit(vals: list[float], base: float | None, timeout: bool) -> tuple[bool, str]:
    """3.2/3.3's contention-agnostic rule: >= 2 samples over the limit, or any timeout, or the median over."""
    if base is None:
        return timeout, ""
    limit = max(2 * base, base + LATENCY_SLACK)
    slow = [v for v in vals if v > limit]
    med = sorted(vals)[(len(vals) - 1) // 2]
    bad = len(slow) >= 2 or timeout or med > limit
    return bad, f"p95 {p95(vals):.2f}s<= {limit:.2f}s (base {base:.2f}s, {len(slow)}/{len(vals)} over, median {med:.2f}s)"


# ---------- the per-step judge (3) ----------

def judge_step(rows: list[dict], probes: list[dict], step: int) -> tuple[str, str, str]:
    """One step's 3 criteria over its judged window. Returns (verdict, cause, detail)."""
    win = rows_of(rows, step, "judge")
    if not win:
        return "INCONCLUSIVE", "missing:samples", f"no judged rows for step {step} in samples"
    states: dict[str, str] = {}
    detail: dict[str, str] = {}
    causes: dict[str, str] = {}

    def put(name: str, state: str, text: str, cause: str = "") -> None:
        states[name] = state
        detail[name] = text
        if cause:
            causes[name] = cause

    # 3.1 pressure: system.slice, user.slice, cgprofile.slice -- some AND full. The root rows stay INFO (2.4).
    bad, ptext, gone = [], [], None
    for sl in SLICES:
        for mode in ("some", "full"):
            col = f"{sl}.psi_{mode}_avg10"
            vals = series(win, col)
            if not vals:
                gone = col
                break
            peak = max(vals)
            ptext.append(f"{sl}.{mode} {peak:.2f}")
            if peak > PRESSURE_LIMIT:
                bad.append(f"{sl}.{mode} {peak:.2f} > {PRESSURE_LIMIT:.2f}")
        if gone:
            break
    if gone:
        put("pressure", "INCONCLUSIVE", f"no data in column {gone}")
    else:
        put("pressure", "FAIL" if bad else "PASS", "; ".join(bad) or "psi " + "/".join(ptext))

    # 3.2 ssh: the p95 of 10 samples; step 0 sets the baseline (max of its first 3 samples)
    ssh_vals = seconds_of(probes, step, "ssh")
    ssh_to = any(p.get("rc") == "timeout" for p in probes_of(probes, step, "ssh"))
    base = ssh_base(probes)
    if not ssh_vals:
        put("ssh", "INCONCLUSIVE", f"no ssh samples for step {step} in probes")
    elif step == 0 or base is None:
        put("ssh", "PASS" if not ssh_to else "FAIL",
            f"ssh p95 {p95(ssh_vals):.2f}s (step 0 sets the baseline)" + (" (a sample hit the timeout)" if ssh_to else ""))
    else:
        bad_ssh, text = over_limit(ssh_vals, base, ssh_to)
        if ssh_to:
            text += " (a sample hit the timeout)"
        put("ssh", "FAIL" if bad_ssh else "PASS", "ssh " + text)

    # 3.3 liveness: rc 0 in every sample, the same latency rule, and containerd's NRestarts unchanged
    bases = step_base(probes)
    seen, bad_l, unmeasured, absent = [], [], [], []
    restarts0 = detail_int(probes, 0, "containerd", "nrestarts")
    for name in LIVENESS:
        prows = probes_of(probes, step, name)
        if not prows:
            absent.append(name)   # 3.5/L-i: a probe that did not run is missing data, never a pass and never a FAIL
            continue
        if name == "cgprofiled" and all("state=degraded" in (p.get("detail") or "").split() for p in prows):
            continue  # CLI absent: its systemctl/socket observation is INFO only, including a recorded timeout
        seen.append(name)
        rcs = [p.get("rc") for p in prows]
        if "unmeasurable" in rcs:
            unmeasured.append(name)   # 3.3/L-i: the probe could not measure -> missing data, never a pass
            continue
        if any(rc != "0" for rc in rcs):
            bad_l.append(f"{name} rc {next(rc for rc in rcs if rc != '0')}")
            continue
        if name == "containerd":
            now = detail_int(probes, step, "containerd", "nrestarts")
            if now is None or restarts0 is None:
                unmeasured.append(name)  # empty/invalid NRestarts is missing, never an invented zero or a pass
                continue
            if now > restarts0:
                bad_l.append(f"containerd restarted ({restarts0} -> {now})")
                continue
        vals = seconds_of(probes, step, name)
        if step > 0 and vals and name not in LATENCY_EXEMPT:
            bad_one, _ = over_limit(vals, bases.get(name), False)
            if bad_one:
                bad_l.append(f"{name} latency over its step-0 baseline")
    if not seen:
        put("probes", "INCONCLUSIVE", f"no liveness rows for step {step} in probes")
    elif absent or unmeasured:
        put("probes", "INCONCLUSIVE",
            f"missing data: " + ", ".join(absent + unmeasured) + " (a probe that did not run is never a pass)")
    else:
        put("probes", "FAIL" if bad_l else "PASS",
            f"probes {len(seen) - len(bad_l)}/{len(LIVENESS)} passed" + (f" ({'; '.join(bad_l)})" if bad_l else ""))

    # no oom (2.4's global counter plus both load tiers)
    kill_cols = {"vmstat": "vmstat_oom_kill", "dev": "dev.oom", "gates": "gates.oom", "bg": "bg.oom"}
    hits, missing = {}, []
    for lbl, col in kill_cols.items():
        d = delta(rows, col)
        if d is None:
            missing.append(col)
        elif d > 0:
            hits[lbl] = d
    if missing:
        put("no oom", "INCONCLUSIVE", "no data in " + ", ".join(missing))
    else:
        put("no oom", "FAIL" if hits else "PASS",
            "oom/oom_kill delta " + (", ".join(f"{k}=+{v:.0f}" for k, v in hits.items())
                                     or "0 in vmstat, dev.slice and both tiers"))

    # 3.5 load was real: at the ceiling on >= 80 % of the judged ticks AND MemFree <= 2 x W on >= 50 %. It is the
    # only INCONCLUSIVE cause that continues the ladder, and such a step can never be k_last_pass.
    need = ("dev.current", "dev.mem_max", "mem_free", "W")
    gone = [c for c in need if not series(win, c)]
    if gone:
        put("load was real", "INCONCLUSIVE", "no data for " + ", ".join(gone), cause="missing")
    else:
        at_top = sum(1 for r in win
                     if (cur := fnum(r, "dev.current")) is not None
                     and (top := fnum(r, "dev.mem_max")) is not None and cur >= top - SQUEEZE_TOLERANCE)
        low_free = sum(1 for r in win
                       if (free := fnum(r, "mem_free")) is not None
                       and (w := fnum(r, "W")) is not None and free <= 2 * w)
        frac, ffrac = at_top / len(win), low_free / len(win)
        ok = frac >= 0.8 and ffrac >= 0.5
        put("load was real", "PASS" if ok else "INCONCLUSIVE",
            f"dev.slice at its ceiling on {frac:.0%} of the judged ticks, MemFree <= 2 x W on {ffrac:.0%}"
            + ("" if ok else " (needs >= 80 % and >= 50 %: the step is not load-bearing)"),
            cause="" if ok else "load_not_real")

    # 2.4's MemAvailable guard is a criterion: a breach in the window is a FAIL, not a missing value
    avail = series(win, "mem_available")
    if avail and min(avail) < MEM_AVAILABLE_FLOOR:
        put("mem available", "FAIL", f"MemAvailable fell to {min(avail) / MIB:.0f}M (floor 128M)")

    fails = [k for k, v in states.items() if v == "FAIL"]
    unknown = [k for k, v in states.items() if v == "INCONCLUSIVE"]
    if fails:
        verdict, cause = "FAIL", fails[0].replace(" ", "_")
    elif unknown:
        verdict = "INCONCLUSIVE"
        if unknown == ["load was real"] and causes.get("load was real") == "load_not_real":
            cause = "load_not_real"     # 2.5: the ONLY cause that continues the ladder
        else:
            cause = "missing:" + unknown[0]   # missing hard data stops it (L-i) -- a load row without data is missing
    else:
        verdict, cause = "PASS", ""
    text = " ".join(f"{k} {states.get(k, '?')}: {v};" for k, v in detail.items())
    return verdict, cause, f"load {states.get('load was real', '?')}: {detail.get('load was real', '')}; {text}".strip()


def write_step_env(evidence: Path, step: int, verdict: str, cause: str, text: str) -> None:
    steps = evidence / "steps"
    steps.mkdir(parents=True, exist_ok=True)
    clean = text.replace("\n", " ").replace("\r", " ")
    (steps / f"{step}.env").write_text(f"verdict={verdict}\ncause={cause}\ndetail={clean}\n")


def live_step(evidence: Path, step: int) -> int:
    verdict, cause, text = judge_step(read_csv(evidence / "samples.csv"), read_csv(evidence / "probes.csv"), step)
    write_step_env(evidence, step, verdict, cause, text)
    print(f"accept-eval: step {step} {verdict} cause={cause or '<none>'} {text}")
    return 0


# ---------- the run's verdict (4.3, 5, 6) ----------

def installed_floors(base: dict[str, str]) -> dict[str, int]:
    rows = dict(cap.DEDICATED_HOST_MEMORY_MIN_DEFAULTS) if cap else {}
    raw = (base.get("DEV_HOST_MEMORY_MIN") or "").strip()
    if cap and raw and raw != "-":
        for row in raw.split():
            unit, _, val = row.partition("=")
            parsed = cap.parse_size_bytes(val)
            if parsed:
                rows[unit] = parsed
    return rows


def topology_dropped_steps(evidence: Path) -> dict[str, set[int]]:
    """A per-unit topology drop is evidence that the step's floor was not fully measured."""
    path = evidence / "hotset-topology.jsonl"
    if not path.is_file():
        return {}
    dropped: dict[str, set[int]] = {}
    try:
        with path.open(encoding="utf-8") as fh:
            for line_number, line in enumerate(fh, 1):
                try:
                    record = json.loads(line)
                except ValueError as exc:
                    raise Inconclusive(f"{path}:{line_number} is not valid topology evidence: {exc}") from exc
                if not isinstance(record, dict) or record.get("kind") != "dropped":
                    continue
                unit, step = record.get("unit"), record.get("step")
                if isinstance(unit, str) and isinstance(step, int):
                    dropped.setdefault(unit, set()).add(step)
    except OSError as exc:
        raise Inconclusive(f"cannot read topology evidence {path}: {exc}") from exc
    return dropped


def topology_drop_reason(evidence: Path, unit: str, passing: set[int]) -> str | None:
    path = evidence / "hotset-topology.jsonl"
    if not path.is_file():
        return None
    details = []
    try:
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                record = json.loads(line)
                if (isinstance(record, dict) and record.get("kind") == "dropped" and record.get("unit") == unit
                        and isinstance(record.get("step"), int) and record.get("step") in passing
                        and isinstance(record.get("reason"), str)):
                    details.append((record["step"], record["reason"]))
    except (OSError, ValueError) as exc:
        raise Inconclusive(f"cannot read topology drop reason from {path}: {exc}") from exc
    if not details:
        return None
    step, reason = sorted(details)[0]
    return f"dropped at step {step}: {reason}"


def damon_floors(evidence: Path, installed: dict[str, int], passing: set[int]) -> dict[str, dict]:
    """4.3: H_unit = the p95 of the one-apply values in the judged window, max over the passing steps; floor =
    ceil(1.10 x H_unit). A floor >= the installed row that passed every step with it in force is **proved**; one
    genuinely below it was never in force and stays a candidate."""
    rows = read_csv(evidence / "hotset.csv")
    dropped_steps = topology_dropped_steps(evidence)
    out: dict[str, dict] = {}
    for unit in sorted({r.get("unit", "") for r in rows if r.get("unit")}):
        hot, steps = 0.0, []
        for step in sorted(passing):
            vals = []
            for r in rows:
                if str(r.get("step")) == str(step) and r.get("unit") == unit:
                    try:
                        vals.append(float(r["sz_filter_passed_one_apply"]))
                    except (KeyError, TypeError, ValueError):
                        continue
            if vals:
                hot, steps = max(hot, p95(vals)), steps + [step]
        if not steps:
            continue
        floor = (int(hot) * 11 + 9) // 10   # ceil(1.10 x H) in integer bytes: no float ulp (400M -> 440M, not 441M)
        measured_all_passes = bool(passing) and set(steps) == passing
        no_dropped_pass = not (dropped_steps.get(unit, set()) & passing)
        was_refused = hotset_refusal(evidence, unit) is not None
        out[unit] = {"hot": hot, "floor": floor, "steps": steps, "installed": installed.get(unit, 0),
                     "proved": measured_all_passes and no_dropped_pass and not was_refused
                     and floor >= installed.get(unit, 0)}
    return out


def margin(rows: list[dict], step: int) -> float | None:
    """5: M = p99(U) + p99(init.scope) + W + 64M over the step's judged window, never below 64M."""
    win = rows_of(rows, step, "judge")
    u, i, w = series(win, "U"), series(win, "init_current"), series(win, "W")
    if not u or not i or not w:
        return None
    return max(p99(u) + p99(i) + max(w) + 64 * MIB, 64 * MIB)


def hotset_refusal(evidence: Path, unit: str) -> str | None:
    marker = evidence / "hotset-refused"
    if marker.is_file() and unit in marker.read_text().split():
        prefix = f"damon_measure=failed:{unit}:"
        for line in (evidence / "manifest.env").read_text().splitlines() if (evidence / "manifest.env").is_file() else []:
            if line.startswith(prefix):
                return line[len(prefix):] or "DAMON measurement refused"
        return "sz_ops_filter_passed 0"
    return None


def read_steps(evidence: Path) -> dict[int, dict[str, str]]:
    out: dict[int, dict[str, str]] = {}
    for f in sorted((evidence / "steps").glob("*.env")):
        if f.stem.isdigit():
            out[int(f.stem)] = read_env(f)
    return out


def run_verdict(run: dict) -> str:
    """Only a measured pass or the sole continuing load_not_real result can support a run verdict."""
    if 0 in run["steps"] and run["steps"][0].get("verdict") == "FAIL":
        return "FAIL"
    if any(st.get("verdict") == "INCONCLUSIVE" and st.get("cause") != "load_not_real"
           for st in run["steps"].values()):
        return "INCONCLUSIVE"
    if run["passed"]:
        return "PASS"
    return "INCONCLUSIVE"


def merged_floors(run: dict) -> dict[str, int]:
    """5: the installed rows with every PROVED raise applied. A candidate row is genuinely below its installed one
    (4.3), so it never enters -- F_new can only rise, and a floor this run never exercised never lowers K."""
    merged = dict(run["installed"])
    for unit, v in run["floors"].items():
        if v["proved"]:
            merged[unit] = max(merged.get(unit, 0), v["floor"])
    return merged


def proposal(run: dict) -> tuple[str | None, int | None, int | None, int | None]:
    """5: F_new = `host_floors` over the INSTALLED rows with the PROVED raises applied (a candidate row never enters
    it, so F_new >= F_installed); K_proposed = max(F_new + M, k_last_pass). The KEEP pin is dropped from the rows
    because K is derived again. Returns (refusal text or None, K, F_new, k_last_pass)."""
    if cap is None:
        return "mdt_capacity is not importable (the kit was copied out of the checkout)", None, None, None
    man, base, m = run["man"], run["base"], run["m"]
    if m is None:
        return "no M: the deepest passing step has no U/init_current/W data", None, None, None
    if not man.get("mem_total", "").isdigit():
        return "manifest.env has no mem_total: F_new cannot be derived", None, None, None
    rows = dict(base)
    merged = merged_floors(run)
    rows["DEV_HOST_MEMORY_MIN"] = " ".join(f"{u}={size(v)}" for u, v in sorted(merged.items()))
    rows["DEV_HOST_MEMORY_MARGIN"] = str(int(m))   # F_new + the MEASURED M; the printed margin is keep - F_new
    rows.pop("DEV_HOST_MEMORY_KEEP", None)
    try:
        got = cap.dedicated_memory(rows, mem_total=int(man["mem_total"]), swap_total=int(man.get("swap_total", 0) or 0))
    except Exception as exc:
        return f"install.sh's own derivation would refuse these rows: {exc}", None, None, None
    k_last = man.get("k_last_pass", "").strip()
    tested = int(k_last) if k_last.isdigit() else run["tested_k"]
    keep = got.keep
    if tested is not None and keep < tested:
        keep = tested
    return None, keep, got.floors.floors, tested


def evaluate(evidence: Path, tested_k: int | None) -> dict:
    man = read_env(evidence / "manifest.env")
    if man.get("kit_version") != "2":
        raise V1Evidence(str(evidence))
    rows = read_csv(evidence / "samples.csv")
    steps = read_steps(evidence)
    if not steps:
        raise Inconclusive(f"{evidence}: no steps/*.env: the judge wrote no per-step verdict")
    base = read_env(evidence / "host-setup.env.snapshot")
    run: dict = {"dir": evidence, "man": man, "base": base, "rows": rows, "steps": steps,
                 "passed": sorted(n for n, s in steps.items() if s.get("verdict") == "PASS"),
                 "failed": sorted(n for n, s in steps.items() if s.get("verdict") == "FAIL"),
                 "installed": installed_floors(base), "tested_k": tested_k}
    run["floors"] = damon_floors(evidence, run["installed"], set(run["passed"]))
    run["m_squeeze"] = margin(rows, max(run["passed"])) if run["passed"] else None
    run["m_unpressured"] = margin(rows, 0)
    vals = [v for v in (run["m_squeeze"], run["m_unpressured"]) if v is not None]
    run["m"] = max(vals) if vals else None
    return run


def refault_info(run: dict) -> list[str]:
    """3.4: informational only -- one INFO line per step with a rising refault (the evidence that warm pages were
    paged out, which is the intended result of a squeeze)."""
    lines = []
    for step in sorted(run["steps"]):
        win = rows_of(run["rows"], step, "judge") or rows_of(run["rows"], step)
        hits = []
        for unit in sorted(run["installed"]):
            if unit in NOT_REFALTED:
                continue
            a, f = delta(win, f"{unit}.refault_anon"), delta(win, f"{unit}.refault_file")
            if a is None and f is None:
                continue
            total = (a or 0) + (f or 0)
            if total > 0:
                hits.append(f"{unit} +{total:.0f}")
        if hits:
            lines.append(f"INFO refault (informational, pages): {', '.join(hits)} (step {step})")
    return lines


def print_run(run: dict) -> str:
    man = run["man"]
    k0 = int(man["k_tested"]) if man.get("k_tested", "").isdigit() else None
    step_bytes = int(man["step_bytes"]) if man.get("step_bytes", "").isdigit() else 128 * MIB
    for n, st in sorted(run["steps"].items()):
        state = st.get("verdict", "INCONCLUSIVE")
        k = f"K={size(k0 - n * step_bytes)}" if k0 is not None and state in ("PASS", "FAIL") else ""
        print(f"STEP {n}{' ' + k if k else ''} {state} {st.get('detail', '')}")
    verdict = run_verdict(run)
    if verdict == "FAIL":
        who = f"K0={size(k0)}" if k0 is not None else "K0"
        print(f"FAIL: step 0 failed at the live configuration. pin DEV_HOST_MEMORY_KEEP above the tested {who} "
              "(step 0 failed at the live configuration); this run measures no value")
        print("VERDICT FAIL")
        print("== host-setup.env (NOT to be applied: FAIL; no K is printed at all on a FAIL)")
        return verdict
    if verdict == "INCONCLUSIVE":
        print("INCONCLUSIVE: the ladder stopped with unmeasured data; no K is proposed")
        print("VERDICT INCONCLUSIVE")
        print("== host-setup.env (NOT to be applied: INCONCLUSIVE)")
        return verdict
    if run["passed"] and run["failed"]:
        last, first = max(run["passed"]), max(run["failed"])
        kp, kf = man.get("k_last_pass", ""), man.get("k_first_fail", "")
        cause = run["steps"].get(first, {}).get("cause", "")
        print(f"SQUEEZE last pass K={size(int(kp)) if kp.isdigit() else kp} (step {last}), first fail "
              f"K={size(int(kf)) if kf.isdigit() else kf} (step {first}): stopped by the {cause} criterion")
    for line in refault_info(run):
        print(line)
    for unit in sorted(run["floors"]):
        v = run["floors"][unit]
        if v["proved"]:
            print(f"FLOOR {unit}={size(v['floor'])} source=damon:{v['hot'] / MIB:.1f}M x{FLOOR_FACTOR:.2f} "
                  f"tested=every step")
        else:
            reason = topology_drop_reason(Path(run["dir"]), unit, set(run["passed"]))
            untested = f"untested: {reason}" if reason else "untested"
            print(f"FLOOR {unit}={size(v['floor'])} source=damon candidate (installed {size(v['installed'])}, {untested})")
    for unit in FLOORED:
        if unit in run["floors"]:
            continue
        why = hotset_refusal(run["dir"], unit)
        if why:
            detail = f"DAMON refused: {why}"
        else:
            dropped = topology_drop_reason(Path(run["dir"]), unit, set(run["passed"]))
            detail = f"untested: {dropped}" if dropped else "no DAMON data"
        print(f"FLOOR {unit}={size(run['installed'].get(unit, 0))} source=table candidate "
              f"(installed {size(run['installed'].get(unit, 0))}, {detail})")
    refused, keep, f_new, tested = proposal(run)
    if refused or keep is None or f_new is None:
        print(f"INCONCLUSIVE proposal: {refused}")
        print("VERDICT INCONCLUSIVE")
        return "INCONCLUSIVE"
    m_s, m_u = run["m_squeeze"], run["m_unpressured"]
    if m_s is not None and m_u is not None:
        note = " differ by >= 25 %: the larger is taken" if m_u and abs(m_s - m_u) >= 0.25 * m_u else ""
        print(f"M measured={m_s / MIB:.0f}M (deepest passing step {max(run['passed'])}) "
              f"unpressured={m_u / MIB:.0f}M set={run['m'] / MIB:.0f}M{note}")
    print(f"K proposed={size(keep)} = F {size(f_new)} + M {size(keep - f_new)}"
          + (f" (>= k_last_pass {size(tested)})" if tested is not None and keep >= tested else ""))
    print(f"VERDICT {verdict}")
    pre = "" if verdict == "PASS" else "# "
    merged = merged_floors(run)
    rows_txt = " ".join(f"{u}={size(v)}" for u, v in sorted(merged.items()))
    stamp = f"{datetime.datetime.now(datetime.timezone.utc):%Y-%m-%d}"
    print("== host-setup.env" + ("" if verdict == "PASS" else f" (NOT to be applied: {verdict})"))
    print(f'{pre}DEV_HOST_MEMORY_MIN="{rows_txt}"')
    print(f"{pre}DEV_HOST_MEMORY_MARGIN={size(keep - f_new)}   # K proposed - F_new; M itself is {run['m'] / MIB:.0f}M")
    print(f'{pre}DEV_HOST_MEMORY_MEASURED="{stamp} {run["dir"]}"')
    return verdict


def v1_refusal(evidence: Path) -> int:
    print(f"accept-eval: {evidence} has no kit_version=2 in manifest.env: this is a v1 acceptance directory. v2 "
          "criteria applied to v1 data would turn a missing squeeze into a pass, so it is refused; re-run the v2 kit.")
    return 2


def main(argv: list[str] | None = None) -> int:
    try:
        return _main(argv)
    except V1Evidence as exc:
        return v1_refusal(Path(str(exc)))
    except Inconclusive as exc:
        print(exc)
        print("VERDICT INCONCLUSIVE")
        return 4
    except SystemExit:
        raise
    except Exception as exc:   # a crash is never a verdict about the host
        print(f"accept-eval: internal error {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


def _main(argv: list[str] | None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("dirs", nargs="*", type=Path)
    ap.add_argument("--live-step", type=Path, help="judge one step of this run into DIR/steps/N.env")
    ap.add_argument("--step", type=int, default=0, help="the step --live-step judges (default 0)")
    try:
        args = ap.parse_args(argv)
    except SystemExit as exc:
        return 2 if exc.code else 0
    if args.live_step is not None:
        if not (args.live_step / "samples.csv").is_file():
            print(f"accept-eval: {args.live_step}/samples.csv missing", file=sys.stderr)
            return 2
        return live_step(args.live_step, args.step)
    if not args.dirs:
        print("accept-eval: no evidence directory and no --live-step", file=sys.stderr)
        return 2
    verdict, rc = "PASS", 0
    for d in args.dirs:
        if not (d / "samples.csv").is_file():
            print(f"accept-eval: {d}/samples.csv missing", file=sys.stderr)
            return 2
        v = print_run(evaluate(d, None))
        if v == "FAIL":
            verdict, rc = "FAIL", 1
        elif v == "INCONCLUSIVE" and verdict != "FAIL":
            verdict, rc = "INCONCLUSIVE", 4
    return rc


if __name__ == "__main__":
    sys.exit(main())
