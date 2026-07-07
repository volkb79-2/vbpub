#!/usr/bin/env python3
"""Soulmask cgroup memory monitor — zswap pressure, pak slice, disk swap.

WHY THIS EXISTS — splitting refault sources
--------------------------------------------
Per-cgroup `memory.stat` exposes two counters that *look* like they should
tell you whether the game is stalling on RAM-speed zswap decompression or on
millisecond-scale real disk I/O, but neither one does that on its own:

  workingset_refault_anon  — counts ALL anonymous-page refaults: pages that
                              were evicted and are now being faulted back in,
                              whether they came from the zswap compressed
                              pool (microsecond decompress) or were written
                              through to the real swap device on disk
                              (millisecond I/O). It does not distinguish.

  pgmajfault                — counts ALL major faults: the same anon
                              refaults above PLUS file-backed major faults
                              (executable/mmap'd file pages not in page
                              cache). It is neither anon-only nor
                              zswap-only.

Because `memory.zswap.writeback` is usually left enabled and most cold pages
never actually reach the real device, both counters tend to move in
lockstep — which is exactly why operators watching the previous (bash)
version of this monitor kept seeing `rflt/s == mflt/s` and could not tell
"is this the harmless zswap path, or the lag-inducing disk path?"

The fix: `memory.stat` also exposes `zswpin` — pages actually decompressed
FROM zswap. That lets us split the aggregate:

  rf_z/s (zswap refault rate) = Δzswpin / Δt
                                 (~microseconds/page — healthy, expected)

  rf_d/s (disk refault rate)  = max(0, Δworkingset_refault_anon − Δzswpin) / Δt
                                 (~milliseconds/page — 1000x+ slower;
                                  THIS is the column that predicts in-game lag)

A third refault stream is tracked separately: `workingset_refault_file`
(rf_f/s) — FILE-cache refaults. Every one of those is a disk read (there is
no zswap for file pages), and a sustained rate means the kernel is dropping
needed file pages (often the game binary's own code). This is the
swappiness-validation signal from MEASUREMENTS.md M5.

Run with --help for the full column-by-column guide.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime

PAK_CG = "/sys/fs/cgroup/soulmask.slice/soulmask-paks.slice"

COLUMN_GUIDE = """
Column guide
============

GAME cgroup (the selected Soulmask WSServer-Linux-Shipping container):

  RAM      memory.current             physical RAM used by the cgroup: anon
                                       + file cache + kernel structures +
                                       the zswap compressed pool itself.
  anon     memory.stat 'anon'         resident anonymous RAM only (live
                                       heap/stack pages not reclaimed) —
                                       excludes the zswap pool.
  file     memory.stat 'file'         page cache charged to the cgroup:
           [table: --wide only;        binary/library text, mmap'd data
            --json: always]            files, and tmpfs/shmem. Evicting
                                       these is "free" for the kernel but
                                       re-reading them always costs a real
                                       disk read — watch rf_f/s.
  z_pool   memory.zswap.current       compressed bytes currently held in
                                       the zswap pool.
  z_eq     memory.stat 'zswapped'     uncompressed-equivalent size of the
                                       cold pages sitting in zswap.
                                       True compression ratio = z_eq / z_pool.
                                       (NOT memory.swap.current — that also
                                       counts swapcached pages still
                                       resident in RAM.)
  rf_z/s   Δzswpin / Δt               zswap refaults/s: pages decompressed
                                       FROM ZSWAP (RAM-speed, microseconds/
                                       page). Rises during area loads and
                                       decays — normal and healthy.
  rf_d/s   Δ(workingset_refault_anon
             − zswpin) / Δt           disk refaults/s: anon pages faulted
                                       back in from the REAL swap device
                                       (milliseconds/page). Sustained >0 is
                                       the column that predicts in-game lag.
  rf_f/s   Δworkingset_refault_file
             / Δt                     file-cache refaults/s: pages evicted
                                       from page cache and re-read from
                                       their backing file. EVERY file
                                       refault is a disk read — there is no
                                       zswap for file pages. Sustained >0
                                       means the kernel is dropping needed
                                       file pages (often the game binary's
                                       own executable code). This is the
                                       swappiness-validation signal
                                       (MEASUREMENTS.md M5): rf_f/s ≈ 0
                                       with modest rf_z/s and rf_d/s ≈ 0
                                       confirms swappiness=100 is the
                                       right trade for this host.

PAK cgroup (soulmask-paks.slice — pak/DLC file ramdisk; may be absent):

  p_RAM    pak memory.current         pak pages resident in RAM (ramdisk
                                       shmem + evictable source file cache).
  p_z      pak memory.zswap.current   pak bytes compressed in zswap.
  p_disk   pak memory.swap.current −
           memory.stat 'zswapped' −
           memory.stat 'swapcached'   pak pages actually on the real disk,
                                       clamped >= 0. >0 means zswap was
                                       full or writeback was on when these
                                       pages were evicted.
  p_rfz/s  Δzswpin / Δt (pak)         pak zswap refaults/s.
  p_rfd/s  Δ(workingset_refault_anon
             − zswpin) / Δt (pak)     pak disk refaults/s.

SYSTEM-WIDE:

  disk_sw  /proc/swaps 'Used' total
           − /proc/meminfo SwapCached
           − zswap stored_pages*4096
             (root/debugfs only)      total pages on REAL disk swap across
                                       the whole host (every cgroup, not
                                       just Soulmask), clamped >= 0.
                                       /proc/swaps 'Used' counts pages with
                                       an allocated swap slot even when the
                                       data currently lives in zswap (RAM)
                                       or in SwapCached (RAM) — both must be
                                       subtracted or disk_sw is wildly
                                       inflated. If /sys/kernel/debug/zswap
                                       is unreadable, the zswap term is
                                       dropped and the value is an
                                       overestimate — marked with a
                                       trailing '*'.

How these columns relate to htop's process view
================================================
htop's M_VIRT / RES / SHR / CODE / DATA are PER-PROCESS numbers, read from
/proc/<pid>/status. Every column above is PER-CGROUP: it aggregates ALL
processes in the container PLUS kernel memory and tmpfs/page cache charged
to the cgroup. The mapping:

  RAM (memory.current)   = anon + file (page cache incl. charged tmpfs)
                           + kernel structures + the cgroup's compressed
                           zswap pool. It is therefore typically LARGER
                           than the sum of the processes' RES: RES never
                           counts kernel structures or the zswap pool, and
                           summing RES across processes double-counts
                           shared pages.

  anon (memory.stat)     ≈ Σ private anonymous resident memory of the
                           cgroup's processes ≈ the DATA-ish portion of
                           htop's RES (heap + stack). htop RES additionally
                           contains file-backed pages — binary text and
                           shared libraries — which in cgroup terms are
                           our 'file' (overlapping htop's CODE and SHR).

  file (memory.stat)     ≈ the file-backed part of RES (CODE/SHR overlap)
                           PLUS page cache the processes touched that is
                           charged to this cgroup even when no process
                           currently maps it, PLUS charged tmpfs.

  z_pool / z_eq / zswpin and the zswap-vs-disk refault split (rf_z/s vs
  rf_d/s): NO process-level tool exposes these. Nothing in htop, free,
  vmstat, or docker stats can show how much of a workload sits compressed
  in zswap, or whether a refault was served from zswap (µs) or the disk
  (ms). Per-cgroup memory.stat is the ONLY source — that is the reason
  this monitor exists.

Multi-instance selection (-c/--container)
=========================================
More than one Soulmask server can run on this host (Wings names each
container after its server UUID). By default the monitor picks the FIRST
container running WSServer-Linux-Shipping and prints a notice listing any
others it ignored. Use -c/--container with a server-UUID prefix,
container-id prefix, or any substring of the container name to select a
specific one. The selector is also honoured when re-discovering after a
container restart. --json output always includes the selected container's
id and name.

Live band banner (GAME/PAK memory.min, memory.high, memory.zswap.writeback):
  Re-read from the cgroup EVERY sample, not just once at startup, because
  setup-cgroups.sh / the cgroup watcher can change these live while this
  monitor keeps running. A note is printed (stderr) whenever any of them
  changes.

Rates and resets:
  - The first sample after start (or after any reconnect) prints '-' for
    every rate column — there is no previous reading yet to diff against.
  - A negative delta (a counter reading lower than the previous sample)
    means the cgroup/container was recreated and its counters reset to 0.
    This is detected, printed as '-' for that one sample, and resynced
    silently — it will never print a bogus negative or huge rate.
"""

FMT_DEFAULT = ("{ts:<8} | {ram:<6} {anon:<6} {zpool:<7} {zeq:<7} {rfz:<8} {rfd:<8} {rff:<8} "
               "| {pram:<6} {pz:<6} {pdisk:<7} {prfz:<8} {prfd:<8} | {disk_sw}")
FMT_WIDE = ("{ts:<8} | {ram:<6} {anon:<6} {file:<6} {zpool:<7} {zeq:<7} {rfz:<8} {rfd:<8} {rff:<8} "
            "| {pram:<6} {pz:<6} {pdisk:<7} {prfz:<8} {prfd:<8} | {disk_sw}")
HEADER_NAMES = dict(ts="time", ram="RAM", anon="anon", file="file", zpool="z_pool", zeq="z_eq",
                    rfz="rf_z/s", rfd="rf_d/s", rff="rf_f/s", pram="p_RAM", pz="p_z",
                    pdisk="p_disk", prfz="p_rfz/s", prfd="p_rfd/s", disk_sw="disk_sw")
DASH = "—"  # em dash, matches the previous script's '—' placeholder


# ─── low-level reads ──────────────────────────────────────────────────────────

def die(msg: str, code: int = 1) -> None:
    print(f"[monitor:ERROR] {msg}", file=sys.stderr)
    sys.exit(code)


def note(msg: str) -> None:
    print(f"[monitor] {msg}", file=sys.stderr, flush=True)


def now_hms() -> str:
    return datetime.now().strftime("%H:%M:%S")


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def read_text(path: str) -> str:
    with open(path, "r") as f:
        return f.read().strip()


def read_int(path: str, default: int = 0) -> int:
    try:
        return int(read_text(path))
    except ValueError:
        return default


def read_stat(path: str) -> dict:
    out = {}
    with open(path, "r") as f:
        for line in f:
            parts = line.split()
            if len(parts) == 2:
                try:
                    out[parts[0]] = int(parts[1])
                except ValueError:
                    continue
    return out


def read_band(cg_path: str) -> dict:
    def rd(name):
        try:
            return read_text(os.path.join(cg_path, name))
        except FileNotFoundError:
            return "?"

    return {
        "min": rd("memory.min"),
        "high": rd("memory.high"),
        "writeback": rd("memory.zswap.writeback"),
    }


def fmt_mb(byte_val) -> str:
    if byte_val is None:
        return DASH
    return f"{int(byte_val) // 1048576}M"


def fmt_band_value(v: str) -> str:
    if v in ("max", "?"):
        return v
    try:
        b = int(v)
    except ValueError:
        return str(v)
    if b == 0:
        return "0"
    if b % 1073741824 == 0:
        return f"{b // 1073741824}G"
    return f"{b // 1048576}M"


def fmt_rate(v) -> str:
    if v is None:
        return DASH
    return f"{int(round(v))}/s"


def band_str(band: dict) -> str:
    return (f"min={fmt_band_value(band['min'])} "
            f"high={fmt_band_value(band['high'])} "
            f"writeback={band['writeback']}")


def writeback_label(v: str) -> str:
    if v == "0":
        return "(writeback=0 - cold pages stay in zswap, never reach real disk)"
    if v == "1":
        return "(writeback=1 - cold pages MAY be written through to real disk under pressure)"
    return "(writeback=? unknown)"


# ─── docker / cgroup discovery ────────────────────────────────────────────────

def docker_ps() -> list:
    """Returns [(cid, name)] for all running containers."""
    try:
        r = subprocess.run(["docker", "ps", "--format", "{{.ID}}\t{{.Names}}"],
                            capture_output=True, text=True, timeout=10)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return []
    if r.returncode != 0:
        return []
    out = []
    for line in r.stdout.splitlines():
        parts = line.split("\t", 1)
        if len(parts) == 2:
            out.append((parts[0], parts[1]))
    return out


def container_has_wsserver(cid: str) -> bool:
    try:
        r = subprocess.run(["docker", "top", cid], capture_output=True, text=True, timeout=10)
    except subprocess.TimeoutExpired:
        return False
    return r.returncode == 0 and "WSServer-Linux-Shipping" in r.stdout


def container_cgroup_path(cid: str):
    try:
        r = subprocess.run(["docker", "inspect", "-f", "{{.State.Pid}}", cid],
                            capture_output=True, text=True, timeout=10)
    except subprocess.TimeoutExpired:
        return None
    if r.returncode != 0:
        return None
    pid = r.stdout.strip()
    if not pid or pid == "0":
        return None
    try:
        with open(f"/proc/{pid}/cgroup") as f:
            for line in f:
                line = line.strip()
                if line.startswith("0::"):
                    rel = line.split(":", 2)[2]
                    return "/sys/fs/cgroup" + rel
    except FileNotFoundError:
        return None
    return None


def selector_matches(selector: str, cid: str, name: str) -> bool:
    """Wings names containers by server UUID, so a UUID prefix is a name
    prefix — covered by the substring test. Also accept container-id
    prefixes."""
    return cid.startswith(selector) or selector in name


def list_wsserver_containers(selector=None) -> list:
    """[(cid, name)] of running containers whose process tree contains
    WSServer-Linux-Shipping, optionally narrowed by the -c selector."""
    out = []
    for cid, name in docker_ps():
        if selector is not None and not selector_matches(selector, cid, name):
            continue
        if container_has_wsserver(cid):
            out.append((cid, name))
    return out


def find_game_cgroup(selector=None, poll_s: float = 2) -> tuple:
    """Poll docker for WSServer-Linux-Shipping containers (optionally
    narrowed by -c/--container). Blocks (printing a wait message once)
    until one appears. Returns (cid, name, cgroup_path). When multiple
    candidates exist, picks the first and prints a notice listing the
    others."""
    sel_msg = f" matching -c '{selector}'" if selector else ""
    waited = False
    while True:
        cands = list_wsserver_containers(selector)
        for cid, name in cands:
            cg = container_cgroup_path(cid)
            if cg and os.path.isdir(cg):
                others = [f"{c} ({n})" for c, n in cands if c != cid]
                if others:
                    note(f"NOTICE: multiple WSServer containers{sel_msg} — monitoring "
                         f"{cid} ({name}); ignoring: {', '.join(others)}. "
                         "Select one with -c/--container.")
                if waited:
                    note(f"found Soulmask container {cid} ({name}) -> {cg}")
                return cid, name, cg
        if not waited:
            note(f"waiting for Soulmask container (WSServer-Linux-Shipping){sel_msg}... "
                 "Ctrl-C to abort")
            waited = True
        time.sleep(poll_s)


# ─── rate tracking (handles counter resets on container restart) ─────────────

class RateTracker:
    """Tracks monotonic counters and returns per-second rates.
    If ANY tracked counter goes backwards (cgroup/container recreated,
    counters reset to 0), every rate is reported as None for that sample
    and the tracker resyncs to the new baseline silently."""

    def __init__(self, keys):
        self.keys = keys
        self.prev = None
        self.prev_ts = None

    def reset(self):
        self.prev = None
        self.prev_ts = None

    def update(self, ts: float, counters: dict) -> dict:
        rates = {k: None for k in self.keys}
        if self.prev is not None and self.prev_ts is not None:
            dt = ts - self.prev_ts
            if dt > 0:
                reset_detected = any(counters.get(k, 0) < self.prev.get(k, 0) for k in self.keys)
                if not reset_detected:
                    for k in self.keys:
                        rates[k] = (counters.get(k, 0) - self.prev.get(k, 0)) / dt
        self.prev = dict(counters)
        self.prev_ts = ts
        return rates


def split_rates(rates: dict):
    r_wra = rates.get("wra")
    r_zin = rates.get("zswpin")
    if r_wra is None or r_zin is None:
        return None, None
    rf_z = r_zin
    rf_d = r_wra - r_zin
    if rf_d < 0:
        rf_d = 0.0
    return rf_z, rf_d


# ─── per-cgroup sampling ──────────────────────────────────────────────────────

def sample_game(cg_path: str) -> dict:
    stat = read_stat(os.path.join(cg_path, "memory.stat"))
    ram = read_int(os.path.join(cg_path, "memory.current"))
    zpool = read_int(os.path.join(cg_path, "memory.zswap.current"))
    return {
        "ram": ram,
        "anon": stat.get("anon", 0),
        "file": stat.get("file", 0),
        "zpool": zpool,
        "zeq": stat.get("zswapped", 0),
        "wra": stat.get("workingset_refault_anon", 0),
        "wrf": stat.get("workingset_refault_file", 0),
        "zswpin": stat.get("zswpin", 0),
        "band": read_band(cg_path),
    }


def sample_pak():
    if not os.path.isdir(PAK_CG):
        return None
    try:
        stat = read_stat(os.path.join(PAK_CG, "memory.stat"))
        ram = read_int(os.path.join(PAK_CG, "memory.current"))
        zpool = read_int(os.path.join(PAK_CG, "memory.zswap.current"))
        swap_cur = read_int(os.path.join(PAK_CG, "memory.swap.current"))
        band = read_band(PAK_CG)
    except FileNotFoundError:
        return None
    zeq = stat.get("zswapped", 0)
    swapcached = stat.get("swapcached", 0)
    disk = swap_cur - zeq - swapcached
    if disk < 0:
        disk = 0
    return {
        "ram": ram,
        "zpool": zpool,
        "disk": disk,
        "wra": stat.get("workingset_refault_anon", 0),
        "zswpin": stat.get("zswpin", 0),
        "band": band,
    }


def disk_swap_bytes():
    """System-wide pages actually on the real disk swap device(s).
    See COLUMN_GUIDE 'disk_sw' for the full derivation."""
    used_kib = 0
    try:
        with open("/proc/swaps") as f:
            next(f, None)  # header line
            for line in f:
                parts = line.split()
                if len(parts) >= 4:
                    try:
                        used_kib += int(parts[3])
                    except ValueError:
                        continue
    except FileNotFoundError:
        pass

    swapcached_kib = 0
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("SwapCached:"):
                    swapcached_kib = int(line.split()[1])
                    break
    except FileNotFoundError:
        pass

    zswap_kib = None
    try:
        with open("/sys/kernel/debug/zswap/stored_pages") as f:
            zswap_kib = int(f.read().strip()) * 4
    except (FileNotFoundError, PermissionError, ValueError):
        zswap_kib = None

    disk_kib = used_kib - swapcached_kib - (zswap_kib or 0)
    if disk_kib < 0:
        disk_kib = 0
    return disk_kib * 1024, zswap_kib is None


# ─── output ────────────────────────────────────────────────────────────────────

def header_lines(fmt: str):
    head = fmt.format(**HEADER_NAMES)
    return head, "-" * len(head)


def print_intro(args, cid, name, game_cg):
    print(f"Soulmask memory monitor — Ctrl-C to stop   (interval: {args.interval:g}s)")
    print()
    print(f"  GAME container: {cid} ({name})")
    print(f"  GAME cgroup:    {game_cg}")
    g_band = read_band(game_cg)
    print(f"  GAME  {band_str(g_band)}  {writeback_label(g_band['writeback'])}")
    if os.path.isdir(PAK_CG):
        p_band = read_band(PAK_CG)
        print(f"  PAK   {band_str(p_band)}  {writeback_label(p_band['writeback'])}")
    else:
        print("  PAK   (pak slice not present)")
    print()
    print("  Run with --help for the full column guide (why rf_z/s and rf_d/s are split,")
    print("  what rf_f/s validates, the htop mapping, every formula, disk_sw derivation).")
    print("  memory.min/high/writeback above are re-read every sample; a note is printed")
    print("  here on stderr when they change." +
          ("" if args.wide else "  ('file' column: --wide or --json.)"))
    print()
    head, dash = header_lines(FMT_WIDE if args.wide else FMT_DEFAULT)
    print(head)
    print(dash)


def run(args):
    cid, cname, game_cg = find_game_cgroup(args.container)
    game_tracker = RateTracker(["wra", "zswpin", "wrf"])
    pak_tracker = RateTracker(["wra", "zswpin"])
    pak_was_present = False
    last_game_band = None
    last_pak_band = None
    row_i = 0
    fmt = FMT_WIDE if args.wide else FMT_DEFAULT

    if not args.json:
        print_intro(args, cid, cname, game_cg)

    while True:
        ts = time.time()
        try:
            g = sample_game(game_cg)
        except FileNotFoundError:
            note("Soulmask cgroup disappeared (container restarted?) — re-discovering...")
            cid, cname, game_cg = find_game_cgroup(args.container)
            game_tracker.reset()
            last_game_band = None
            continue

        g_rates = game_tracker.update(ts, {"wra": g["wra"], "zswpin": g["zswpin"], "wrf": g["wrf"]})
        rf_z, rf_d = split_rates(g_rates)
        rf_f = g_rates.get("wrf")

        p = sample_pak()
        if p is None:
            pak_tracker.reset()
            pak_was_present = False
            p_rf_z = p_rf_d = None
        else:
            if not pak_was_present:
                pak_tracker.reset()
            pak_was_present = True
            p_rates = pak_tracker.update(ts, {"wra": p["wra"], "zswpin": p["zswpin"]})
            p_rf_z, p_rf_d = split_rates(p_rates)

        disk_sw, disk_sw_degraded = disk_swap_bytes()

        if not args.json:
            if last_game_band is not None and last_game_band != g["band"]:
                note(f"GAME band changed: {band_str(last_game_band)}  ->  {band_str(g['band'])}")
            last_game_band = g["band"]
            if p is not None:
                if last_pak_band is not None and last_pak_band != p["band"]:
                    note(f"PAK band changed: {band_str(last_pak_band)}  ->  {band_str(p['band'])}")
                last_pak_band = p["band"]
            else:
                last_pak_band = None

            if row_i and row_i % 20 == 0:
                head, dash = header_lines(fmt)
                print(head)
                print(dash)

            print(fmt.format(
                ts=now_hms(),
                ram=fmt_mb(g["ram"]), anon=fmt_mb(g["anon"]), file=fmt_mb(g["file"]),
                zpool=fmt_mb(g["zpool"]), zeq=fmt_mb(g["zeq"]),
                rfz=fmt_rate(rf_z), rfd=fmt_rate(rf_d), rff=fmt_rate(rf_f),
                pram=fmt_mb(p["ram"]) if p else DASH,
                pz=fmt_mb(p["zpool"]) if p else DASH,
                pdisk=fmt_mb(p["disk"]) if p else DASH,
                prfz=fmt_rate(p_rf_z) if p else DASH,
                prfd=fmt_rate(p_rf_d) if p else DASH,
                disk_sw=fmt_mb(disk_sw) + ("*" if disk_sw_degraded else ""),
            ), flush=True)
        else:
            obj = {
                "ts": now_iso(),
                "epoch": ts,
                "interval_s": args.interval,
                "game": {
                    "container_id": cid,
                    "container_name": cname,
                    "cgroup": game_cg,
                    "ram_bytes": g["ram"], "anon_bytes": g["anon"], "file_bytes": g["file"],
                    "zpool_bytes": g["zpool"], "zeq_bytes": g["zeq"],
                    "rf_z_per_s": rf_z, "rf_d_per_s": rf_d, "rf_f_per_s": rf_f,
                    "memory_min": g["band"]["min"], "memory_high": g["band"]["high"],
                    "zswap_writeback": g["band"]["writeback"],
                },
                "pak": None if p is None else {
                    "cgroup": PAK_CG,
                    "ram_bytes": p["ram"], "zpool_bytes": p["zpool"], "disk_bytes": p["disk"],
                    "rf_z_per_s": p_rf_z, "rf_d_per_s": p_rf_d,
                    "memory_min": p["band"]["min"], "memory_high": p["band"]["high"],
                    "zswap_writeback": p["band"]["writeback"],
                },
                "disk_sw_bytes": disk_sw,
                "disk_sw_estimated": disk_sw_degraded,
            }
            print(json.dumps(obj), flush=True)

        row_i += 1
        time.sleep(args.interval)


# ─── CLI ───────────────────────────────────────────────────────────────────────

def _interval_type(s):
    try:
        v = float(s)
    except ValueError:
        raise argparse.ArgumentTypeError(f"invalid interval {s!r} — must be a number")
    if v <= 0:
        raise argparse.ArgumentTypeError("interval must be > 0")
    return v


def parse_args(argv=None):
    p = argparse.ArgumentParser(
        prog="soulmask-zswap-monitor.py",
        description="Soulmask cgroup memory monitor — zswap pressure, pak slice, disk swap.",
        epilog=COLUMN_GUIDE,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("interval", nargs="?", default=5.0, type=_interval_type,
                    help="seconds between samples (default: 5)")
    p.add_argument("--json", action="store_true",
                    help="emit one JSON object per sample on stdout instead of the table")
    p.add_argument("--wide", action="store_true",
                    help="add the 'file' column (memory.stat 'file') to the table "
                         "(always included in --json)")
    p.add_argument("-c", "--container", metavar="SELECTOR",
                    help="select which WSServer container to monitor when several run: "
                         "server-UUID prefix, container-id prefix, or any substring of "
                         "the container name (Wings names containers by server UUID)")
    return p.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)

    if os.geteuid() != 0:
        die("must run as root (sudo) — reading cgroup memory.stat and the zswap debugfs "
            "counters requires root.")
    if not shutil.which("docker"):
        die("docker not found in PATH.")

    try:
        run(args)
    except KeyboardInterrupt:
        print(file=sys.stderr)
        note("stopped.")
        sys.exit(0)


if __name__ == "__main__":
    main()
