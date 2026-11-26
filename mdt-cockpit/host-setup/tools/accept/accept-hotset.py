#!/usr/bin/env python3
"""P-plat-19m: the DAMON hot-set driver of the acceptance kit (DESIGN-ACCEPT-KIT-V2.md 4).

One paddr kdamond, one `stat` scheme per **leaf** memcg, two AND-ed ops-filters per scheme:

* `ops_filters/0`: `type memcg`, `memcg_path <leaf>`, `matching N`, `allow N` -- rejects every page that is
  not the leaf's, so the young filter below never runs on (or mkold's) another cgroup's pages;
* `ops_filters/1`: `type young`, `matching Y`, `allow Y` -- the leaf's young pages pass; the leaf's old pages
  match nothing and fall to the reverse of the last decision, which after an allow filter is a reject.

DAMOS decides on the **first** filter a page matches (`DAMON-GUIDE.md:278-279`), so the chain is an AND, not
the OR that "both filters allow" would make it. The per-unit number is Sigma `tried_regions/*/sz_filter_passed`
after one `update_schemes_tried_regions`; `refresh_ms` is 0 because the kit drives the updates itself and the
kernel's own periodic refresh would alias the scheme's 8 s apply interval (r2 N5).

    accept-hotset.py setup                 --evidence DIR --state FILE --units "u=rel ..." [--admin DIR] ...
    accept-hotset.py refresh-schemes --step N      (same args, plus the running kdamond from the WAL)
    accept-hotset.py sample          --step N      (same args)
    accept-hotset.py off|on                        (same args)

`setup` refuses (exit 3, nothing written) when DAMON_STAT is not `N`, when a kdamond is already `on`, when the
kernel lacks `paddr`/`stats/sz_ops_filter_passed`/the `ops_filters` chain, or when a live cgprofile session
multiplexes kdamonds (4.4). It appends `kdamond=<idx>` to the WAL **before** it writes `on`, because the restore
reads that index from the WAL only.
Exit 0 ok, 3 refused, 2 usage or crash. Test hooks: `--fixture` (a JSON per-unit sample source, no sysfs),
`--damon-stat PATH`, `--cgroup DIR`, `--lib DIR`, `--proc DIR`.
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import shutil
import subprocess
import sys

MIB = 1024 ** 2
HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_LIB = os.path.normpath(os.path.join(HERE, "..", "..", "..", "..", "scripts", "damon-analysis", "lib"))
SAMPLE_US, AGGR_US, UPDATE_US = 400_000, 8_000_000, 8_000_000
MIN_REGIONS, MAX_REGIONS = 10, 1000
MAX_U32 = 2 ** 32 - 1
MAX_SZ = 2 ** 63 - 1
HOTSET_COLUMNS = ("utc", "step", "unit", "memcg_leaves", "sz_filter_passed_one_apply", "sz_tried", "regions_hot_bytes_info")
SELF_TEST_CURRENT = MIB   # above this, sz_filter_passed 0 is a refused floor, never "no hot pages" (4.1)


class Refusal(Exception):
    pass


def say(msg: str) -> None:
    print(f"accept-hotset: {msg}")


def note(msg: str) -> None:
    print(f"accept-hotset: NOTE {msg}")


def read_int(path: str, default: int | None = None) -> int:
    try:
        with open(path) as fh:
            return int(fh.read().strip())
    except (OSError, ValueError):
        if default is None:
            raise
        return default


def write_text(path: str, value: object) -> None:
    try:
        with open(path, "w") as fh:
            fh.write(f"{value}\n")
    except OSError as exc:
        raise Refusal(f"cannot write {path}: {exc}") from exc


def read_lines(path: str) -> list[str]:
    try:
        with open(path) as fh:
            return fh.read().splitlines()
    except OSError:
        return []


def parse_units(raw: str) -> list[tuple[str, str]]:
    out = []
    for item in raw.split():
        unit, sep, rel = item.partition("=")
        if not sep or not unit or not rel:
            raise Refusal(f"--units entry {item!r} is not unit=relpath")
        out.append((unit, rel))
    return out


def leaf_cgroups(cg: str, rel: str) -> list[str]:
    """The leaf memcgs of one unit. A service is its own leaf; a slice is every descendant cgroup that has memory
    accounting and no child that has it -- the slice's own directory reads ~0 (the memcg filter matches the folio's
    memcg id exactly, never its descendants, 4.2)."""
    base = os.path.join(cg, rel)
    if not os.path.isdir(base):
        return []
    leaves = []
    for root, _dirs, files in os.walk(base):
        if "memory.current" not in files:
            continue
        if any(os.path.isfile(os.path.join(root, d, "memory.current")) for d in os.listdir(root)):
            continue   # a slice or an intermediate cgroup: measured as its leaves, never as itself
        leaves.append(os.path.relpath(root, cg))
    return leaves


def hot_leaves(units: list[tuple[str, str]], cg: str) -> list[tuple[str, str]]:
    return [(unit, leaf) for unit, rel in units for leaf in leaf_cgroups(cg, rel)]


def unit_current(cg: str, entries: list[tuple[str, str]], unit: str) -> int:
    return sum(read_int(os.path.join(cg, leaf, "memory.current"), 0) for u, leaf in entries if u == unit)


def resolve_kdamond(args) -> int:
    if args.kdamond is not None:
        return args.kdamond
    for line in reversed(read_lines(args.state or "")):   # the WAL line setup wrote before it wrote `on`
        if line.startswith("kdamond=") and line[8:].strip().isdigit():
            return int(line[8:].strip())
    return 0


def damon_stat_state(path: str) -> str:
    if not os.path.isfile(path):
        return "absent"
    try:
        with open(path) as fh:
            raw = fh.read().strip()
    except OSError as exc:
        raise Refusal(f"cannot read {path}: {exc}; whether the kernel's damon_stat owns DAMON is unknown") from exc
    if raw == "N":
        return "N"
    if raw == "Y":
        raise Refusal(
            f"DAMON_STAT is Y at {path}: it owns the kernel-wide DAMON interface and every manual kdamond fails "
            "EBUSY. Refusing, nothing written; units/mdt-damon-stat.conf (DAMON_STAT_DISABLE_DEFAULT) and `install.sh "
            "--require-damon-disable` are the host policy that turns it off, never this kit")
    raise Refusal(f"{path} holds {raw!r}, which is neither N nor Y; refusing rather than guessing")


def cgprofile_sessions() -> None:
    """4.4's second conflict: a live cgprofile session multiplexes kdamonds. The CLI's absence is a NOTE."""
    exe = shutil.which("cgprofilectl")
    if exe is None:
        note("cgprofilectl is not installed: the kdamond multiplexing check is skipped (the CLI's absence is never a refusal)")
        return
    try:
        r = subprocess.run([exe, "--json", "status"], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError) as exc:
        raise Refusal(f"cgprofilectl --json status could not run ({exc}); whether a cgprofile session is live is unknown") from exc
    if r.returncode != 0:
        raise Refusal(f"cgprofilectl --json status exited {r.returncode}; whether a cgprofile session is live is unknown")
    match = re.search(r'"sessions_live"\s*:\s*"?(\d+)"?', r.stdout)
    if match is None:
        note("cgprofilectl --json status names no sessions_live count: a live session cannot be ruled out from its output")
        return
    if int(match.group(1)) != 0:
        raise Refusal(f"cgprofilectl reports sessions_live={match.group(1)}: a live cgprofile session holds kdamonds")


def set_ops_filters(scheme_dir: str, leaf_abs: str) -> None:
    """The AND chain of 4.1. `nr_filters` goes first because that write is what creates the filter directories."""
    ops = os.path.join(scheme_dir, "ops_filters")
    if not os.path.isdir(ops):
        raise Refusal(f"{ops} is missing: this kernel has no ops_filters, so no per-leaf hot set is measurable")
    write_text(os.path.join(ops, "nr_filters"), 2)
    for idx in ("0", "1"):
        if not os.path.isfile(os.path.join(ops, idx, "allow")):
            raise Refusal(f"{ops}/{idx}/allow is missing after nr_filters=2: no ops-filter chain on this kernel")
    write_text(os.path.join(ops, "0", "type"), "memcg")
    write_text(os.path.join(ops, "0", "memcg_path"), leaf_abs)
    write_text(os.path.join(ops, "0", "matching"), "N")
    write_text(os.path.join(ops, "0", "allow"), "N")
    write_text(os.path.join(ops, "1", "type"), "young")
    write_text(os.path.join(ops, "1", "matching"), "Y")
    write_text(os.path.join(ops, "1", "allow"), "Y")


def mem_total_bytes(proc: str) -> int:
    for line in read_lines(os.path.join(proc, "meminfo")):
        if line.startswith("MemTotal:"):
            try:
                return int(line.split()[1]) * 1024
            except (IndexError, ValueError):
                break
    raise Refusal(f"no MemTotal in {proc}/meminfo: the paddr target cannot be sized")


def load_lib(path: str, admin: str):
    sys.path.insert(0, path)
    try:
        import damon_analysis as da
    except Exception as exc:   # an unimportable lib is a refusal, never a fallback (4.1 "never end - start")
        raise Refusal(f"scripts/damon-analysis/lib is not importable from {path} ({exc}); an accept checkout "
                      "needs mdt-cockpit/ AND scripts/damon-analysis/lib/") from exc
    da.SYSFS_ADMIN = admin
    da.KDAMONDS_DIR = os.path.join(admin, "kdamonds")
    return da


def select_kdamond(admin: str, state: str) -> int:
    """4.1's one kdamond: any `on` kdamond refuses, nr_kdamonds=0 writes 1 and uses index 0, else the highest
    index (off) is reused and overwritten. The index goes to the WAL BEFORE `on` (the restore reads it from there)."""
    nrd = os.path.join(admin, "kdamonds")
    nr = read_int(os.path.join(nrd, "nr_kdamonds"), -1)
    if nr < 0:
        raise Refusal(f"{nrd}/nr_kdamonds is unreadable: the DAMON sysfs interface is absent or not root-owned")
    for idx in range(nr):
        st = read_lines(os.path.join(nrd, str(idx), "state"))
        if not st:
            raise Refusal(f"{nrd}/{idx}/state is unreadable")
        if st[0].strip() == "on":
            raise Refusal(f"kdamond {idx} is already on: the kit runs one kdamond and never multiplexes")
    if nr == 0:
        write_text(os.path.join(nrd, "nr_kdamonds"), 1)
        idx = 0
    else:
        idx = nr - 1
    try:
        with open(state, "a") as fh:
            fh.write(f"kdamond={idx}\n")
    except OSError as exc:
        raise Refusal(f"cannot append kdamond={idx} to {state}: {exc}") from exc
    if not os.path.isdir(os.path.join(nrd, str(idx))):
        raise Refusal(f"kdamond {idx} does not exist after nr_kdamonds={nr}")
    return idx


def configure(da, admin: str, kd: int, entries: list[tuple[str, str]], cg: str, mem_total: int, commit: bool) -> None:
    ctx = 0
    da.SysfsInterface.create_context(kd, ctx)
    da.SysfsInterface.set_operations(kd, ctx, "paddr")
    da.SysfsInterface.set_intervals(kd, ctx, SAMPLE_US, AGGR_US, UPDATE_US)
    da.SysfsInterface.set_nr_regions(kd, ctx, MIN_REGIONS, MAX_REGIONS)
    da.SysfsInterface.set_nr_targets(kd, ctx, 1)   # recreates the target array: the schemes below are configured after it
    regions = os.path.join(admin, "kdamonds", str(kd), "contexts", str(ctx), "targets", "0", "regions")
    write_text(os.path.join(regions, "nr_regions"), 1)   # the kernel creates regions/0 on that write
    for idx in ("0",):
        if not os.path.isdir(os.path.join(regions, idx)):
            raise Refusal(f"{regions}/{idx} does not exist after nr_regions=1: this kernel does not expose physical regions")
    write_text(os.path.join(regions, "0", "start"), 0)
    write_text(os.path.join(regions, "0", "end"), mem_total)
    schemes = os.path.join(admin, "kdamonds", str(kd), "contexts", str(ctx), "schemes")
    write_text(os.path.join(schemes, "nr_schemes"), len(entries))
    for i, (unit, leaf) in enumerate(entries):
        sdir = da.SysfsInterface.create_scheme(kd, ctx, i)
        if not os.path.isdir(sdir):
            raise Refusal(f"scheme {i} ({unit} {leaf}) was not created under {schemes}")
        if read_int(os.path.join(sdir, "stats", "sz_ops_filter_passed"), -1) < 0:
            raise Refusal(f"{sdir}/stats/sz_ops_filter_passed is unreadable: this kernel cannot report the filter-passed "
                          "bytes, and the regions-based end-start number is the machine's hot set, never a leaf's (4.1)")
        write_text(os.path.join(sdir, "action"), "stat")
        da.SysfsInterface.set_scheme_access_pattern(kd, ctx, i, 0, MAX_SZ, 0, MAX_U32, 0, MAX_U32)
        set_ops_filters(sdir, os.path.join(cg, leaf))
    write_text(os.path.join(admin, "kdamonds", str(kd), "refresh_ms"), 0)   # N5: no periodic refresh aliasing aggr_us
    if commit:
        da.SysfsInterface.kdamond_commit(kd)


def entries_or_refuse(units: list[tuple[str, str]], cg: str) -> list[tuple[str, str]]:
    entries = hot_leaves(units, cg)
    if not entries:
        raise Refusal(f"no leaf memcg under {cg} for the units given: a slice is measured as its leaves (4.2)")
    return entries


def setup(args) -> int:
    status = damon_stat_state(args.damon_stat)
    cgprofile_sessions()
    units = parse_units(args.units)
    if args.fixture:   # no sysfs at all: the fixture supplies the samples, the WAL still needs its index
        print(f"damon_stat_enabled={status}")
        print("kdamond=fake")
        return 0
    da = load_lib(args.lib, args.admin)
    if not da.SysfsInterface.is_available():
        raise Refusal(f"{args.admin}/kdamonds is absent: DAMON sysfs is unavailable")
    idx = select_kdamond(args.admin, args.state)
    entries = entries_or_refuse(units, args.cgroup)
    # `contexts/0` is created only by writing `1` to contexts/nr_contexts.
    # A fresh kdamond has nr_contexts=0, so avail_operations is not readable
    # until this write has happened.  Keep this before every read below
    # contexts/0; the WAL already records the kdamond before the write.
    da.SysfsInterface.create_context(idx, 0)
    avail = read_lines(os.path.join(args.admin, "kdamonds", str(idx), "contexts", "0", "avail_operations"))
    if not avail:
        raise Refusal(f"kdamonds/{idx}/contexts/0/avail_operations is unreadable")
    if "paddr" not in avail[0].split():
        raise Refusal(f"avail_operations [{avail[0].strip()}] has no paddr: this kernel cannot monitor physical regions")
    configure(da, args.admin, idx, entries, args.cgroup, mem_total_bytes(args.proc), commit=False)
    da.SysfsInterface.kdamond_on(idx)
    st = read_lines(os.path.join(args.admin, "kdamonds", str(idx), "state"))
    if not st or st[0].strip() != "on":
        raise Refusal(f"kdamond {idx} did not start (state {st[0].strip() if st else 'unreadable'!r})")
    say(f"kdamond {idx}: {len(entries)} leaf schemes, paddr, refresh_ms 0, {SAMPLE_US // 1000}ms/{AGGR_US // 1000}s")
    print(f"damon_stat_enabled={status}")
    print(f"kdamond={idx}")
    return 0


def refresh(args) -> int:
    if args.fixture:
        return 0
    units = parse_units(args.units)
    cg = args.cgroup
    entries = entries_or_refuse(units, cg)
    da = load_lib(args.lib, args.admin)
    kd = resolve_kdamond(args)
    for idx in range(len(entries)):   # a scheme changed mid-run, and the commit applies the new leaf list
        da.SysfsInterface.set_scheme_action(kd, 0, idx, "stat")
    configure(da, args.admin, kd, entries, cg, mem_total_bytes(args.proc), commit=True)
    say(f"kdamond {kd}: {len(entries)} leaf schemes re-enumerated for step {args.step}")
    return 0


def append_rows(path: str, rows: list[tuple[str, dict]], step: int) -> None:
    new = not os.path.isfile(path)
    utc = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        with open(path, "a") as fh:
            if new:
                fh.write(",".join(HOTSET_COLUMNS) + "\n")
            for unit, vals in rows:
                fh.write(f"{utc},{step},{unit},{vals['leaves']},{vals['passed']},{vals['tried']},{vals['hot']}\n")
    except OSError as exc:
        raise Refusal(f"cannot append to {path}: {exc}") from exc


def record_refusal(evidence: str, unit: str, why: str) -> None:
    """One `damon_measure=failed:<unit>:<why>` in manifest.env, once per unit (4.1's self-test)."""
    marker = os.path.join(evidence, "hotset-refused")
    if unit in read_lines(marker):
        return
    try:
        with open(marker, "a") as fh:
            fh.write(f"{unit}\n")
        with open(os.path.join(evidence, "manifest.env"), "a") as fh:
            fh.write(f"damon_measure=failed:{unit}:{why}\n")
    except OSError as exc:
        note(f"cannot record the refused floor of {unit}: {exc}")


def fixture_rows(args, units: list[tuple[str, str]]) -> list[tuple[str, dict]]:
    try:
        with open(args.fixture) as fh:
            data = json.load(fh)
    except (OSError, ValueError) as exc:
        raise Refusal(f"--fixture {args.fixture} is not readable JSON: {exc}") from exc
    table = data.get("units", {})
    rows, refused = [], []
    for unit, _rel in units:
        vals = table.get(unit)
        if vals is None:
            rows.append((unit, {"leaves": 0, "passed": 0, "tried": 0, "hot": 0}))
            continue
        row = {"leaves": int(vals.get("memcg_leaves", 1)), "passed": int(vals.get("per_apply", 0)),
               "tried": int(vals.get("sz_tried", 0)), "hot": int(vals.get("regions_hot_bytes", 0))}
        rows.append((unit, row))
        if row["passed"] == 0 and int(vals.get("current", 0)) > SELF_TEST_CURRENT:
            refused.append((unit, f"sz_filter_passed 0 with memory.current {int(vals.get('current', 0))}"))
    for unit, why in refused:
        record_refusal(args.evidence, unit, why)
        note(f"{unit}: {why}; that unit's floor is refused, never read as no hot pages (4.1's self-test)")
    return rows


def sample(args) -> int:
    units = parse_units(args.units)
    if args.fixture:
        append_rows(os.path.join(args.evidence, "hotset.csv"), fixture_rows(args, units), args.step)
        return 0
    da = load_lib(args.lib, args.admin)
    kd, cg = resolve_kdamond(args), args.cgroup
    entries = entries_or_refuse(units, cg)
    da.SysfsInterface.kdamond_update_tried_regions(kd)   # exactly one apply per sample
    per: dict[str, dict] = {}
    for i, (unit, _leaf) in enumerate(entries):
        sdir = da.SysfsInterface._scheme_dir(kd, 0, i)
        tried = os.path.join(sdir, "tried_regions")
        passed = hot = 0
        names = sorted(os.listdir(tried)) if os.path.isdir(tried) else []
        for name in names:
            rdir = os.path.join(tried, name)
            if not os.path.isdir(rdir):
                continue
            passed += read_int(os.path.join(rdir, "sz_filter_passed"), 0)
            hot += read_int(os.path.join(rdir, "end"), 0) - read_int(os.path.join(rdir, "start"), 0)
        stats = da.SysfsInterface.read_stats(kd, 0, i)
        acc = per.setdefault(unit, {"leaves": 0, "passed": 0, "tried": 0, "hot": 0})
        acc["leaves"] += 1
        acc["passed"] += passed
        acc["tried"] += max(0, stats.get("sz_tried", 0))
        acc["hot"] += hot
    rows = [(unit, acc) for unit, acc in sorted(per.items())]
    for unit, _acc in rows:
        current = unit_current(cg, entries, unit)
        if per[unit]["passed"] == 0 and current > SELF_TEST_CURRENT:
            record_refusal(args.evidence, unit, f"sz_filter_passed 0 with memory.current {current}")
            note(f"{unit}: sz_filter_passed is 0 with memory.current {current}; that unit's floor is refused, "
                 "never read as no hot pages (4.1's self-test)")
    append_rows(os.path.join(args.evidence, "hotset.csv"), rows, args.step)
    say(f"step {args.step}: {len(rows)} units, {sum(r['passed'] for _u, r in rows)} filtered bytes in one apply")
    return 0


def kdamond_state_cmd(args, value: str) -> int:
    if args.fixture:
        say(f"kdamond fake -> {value}")
        return 0
    kd = resolve_kdamond(args)
    path = os.path.join(args.admin, "kdamonds", str(kd), "state")
    write_text(path, value)
    st = read_lines(path)
    if not st or st[0].strip() != value:
        raise Refusal(f"kdamond {kd} state {st[0].strip() if st else 'unreadable'!r} != {value!r} after the write")
    say(f"kdamond {kd} -> {value}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("command", choices=("setup", "refresh-schemes", "sample", "off", "on"))
    ap.add_argument("--evidence", required=True)
    ap.add_argument("--state", help="the WAL; setup appends kdamond=<idx> to it, the other commands read it")
    ap.add_argument("--units", default="")
    ap.add_argument("--admin", default="/sys/kernel/mm/damon/admin")
    ap.add_argument("--cgroup", default="/sys/fs/cgroup")
    ap.add_argument("--proc", default="/proc")
    ap.add_argument("--lib", default=DEFAULT_LIB)
    ap.add_argument("--damon-stat", default="/sys/module/damon_stat/parameters/enabled")
    ap.add_argument("--kdamond", type=int, help="override the index the WAL records")
    ap.add_argument("--step", type=int, default=0)
    ap.add_argument("--fixture")
    try:
        args = ap.parse_args(argv)
        os.makedirs(args.evidence, exist_ok=True)
        if args.command == "setup" and not args.state:
            raise Refusal("setup needs --state: the WAL gets kdamond=<idx> before `on`")
        handler = {"setup": setup, "refresh-schemes": refresh, "sample": sample,
                   "off": lambda a: kdamond_state_cmd(a, "off"),
                   "on": lambda a: kdamond_state_cmd(a, "on")}[args.command]
        return handler(args)
    except Refusal as exc:
        print(f"accept-hotset: {exc}", file=sys.stderr)
        return 3
    except OSError as exc:   # a missing or unreadable sysfs file is a refusal, never an internal error
        print(f"accept-hotset: {exc.filename or 'sysfs'}: {exc.strerror or exc}; refusing, nothing done", file=sys.stderr)
        return 3
    except SystemExit as exc:   # argparse's usage exit
        return 2 if exc.code else 0
    except Exception as exc:    # a crash is never a measurement
        print(f"accept-hotset: internal error {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
