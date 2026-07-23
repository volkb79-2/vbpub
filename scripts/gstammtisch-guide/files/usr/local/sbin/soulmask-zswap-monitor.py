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

Run with --legend for the full column-by-column guide.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import textwrap
import time
from datetime import datetime

PAK_CG = "/sys/fs/cgroup/soulmask.slice/soulmask-paks.slice"
PROC_ROOT = "/proc"
LEGEND_WIDTH = 160

COLUMN_GUIDE = """
Column guide
============

GAME cgroup (each selected Soulmask WSServer-Linux-Shipping container):

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
  z_ratio  zswapped / z_pool          uncompressed-equivalent bytes divided
                                       by compressed bytes. `2.74x` means
                                       2.74 original bytes per compressed
                                       byte. `—` means the pool is empty.
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
                                       means needed file pages are repeatedly
                                       evicted and later faulted back in
                                       (often the game binary's own code).
                                       This is the
                                       swappiness-validation signal
                                       (MEASUREMENTS.md M5): rf_f/s ≈ 0
                                       with modest rf_z/s and rf_d/s ≈ 0
                                       confirms swappiness=100 is the
                                       right trade for this host.

  KSM       /proc/<pid>/ksm_stat for the WSServer process:
            KSM = opt-in/mergeable status (`on`, `any`, `vma`, `off`),
            k_merge = pages currently in KSM merging, k_zero = pages mapped
            to the kernel zero page, k_profit = approximate process profit.
            The startup inventory also prints rmap items, full merge state,
            host-wide KSM profit/scans, and `cow_ksm`/`ksm_swpin_copy` event
            counters. KSM merges anonymous pages only; it never deduplicates
            file-backed page cache.

  KSM host rates
            pages_scanned is a cumulative ksmd counter. scan/s is the
            monitor's derived Δpages_scanned / Δt rate; it is not a native
            per-second kernel counter. full_scans is the cumulative number of
            completed full KSM scans. cow/s and swpin/s are derived rates for
            the corresponding host-wide KSM COW and swap-in-copy counters.

  KSM status and '?'
            `on` means both ksm_merge_any and ksm_mergeable are `yes`;
            `any` means the process opted in via PR_SET_MEMORY_MERGE;
            `vma` means at least one VMA is mergeable; `off` means neither
            is active. `?` means UNKNOWN/UNAVAILABLE, never zero: the PID
            was not found, /proc/<pid>/ksm_stat could not be read, CONFIG_KSM
            or the field is unavailable, or permission/process-exit timing
            prevented a read. A numeric `0` is a successful read of zero.

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
container after its server UUID). By default the monitor follows EVERY
container running WSServer-Linux-Shipping. Use -c/--container with a
server-UUID prefix, container-id prefix, or any substring of the container
name to monitor only one. The selector is also honoured when re-discovering
after a container restart. --json output includes a `games` array with every
selected container (and retains the singular `game` object when only one is
selected).

Applied cgroup controls (GAME min/low/high/max, CPU weight, BFQ I/O weight):
  The controls are read from each server's Wings slice at startup and once
  every sample thereafter. Reads are silent. If a value drifts, a stderr note
  names the changed value and prints the complete current control set for
  direct comparison. The shared PAK slice continues to be sampled separately.
  Inventory names are the actual cgroup files: memory.min, memory.low,
  memory.high, memory.max, cpu.weight, io.bfq.weight, and
  memory.zswap.writeback. Memory values are rendered as G/M; the raw values
  remain available in --json.

Row layout and math
===================
  Each row is: time | S1 GAME + S1 KSM | S2 GAME + S2 KSM | shared KSM |
  shared PAK | disk_sw. With -c/--container there may be only one server
  block. `—` is an intentionally unavailable rate (first sample, counter
  reset, or absent PAK), whereas `?` is an unavailable state/value read.

  z_eq / z_pool                  approximate uncompressed-to-compressed
                                 zswap ratio (when z_pool > 0).
  rf_z/s = Δzswpin / Δt          anonymous pages refaulted from zswap.
  rf_d/s = max(0, Δwra−Δzswpin) / Δt
                                 anonymous pages refaulted from disk swap.
  rf_f/s = Δworkingset_refault_file / Δt
                                 file-cache pages refaulted from storage.
  p_disk = max(0, swap_current − zswapped − swapcached)
                                 shared PAK pages actually on disk swap.
  disk_sw = max(0, swaps_used − SwapCached − zswap_stored_pages×4096)
                                 host-wide disk swap, not Soulmask-only.
  KSM saved pages ≈ ksm_merging_pages + ksm_zero_pages; process profit is
  the kernel's approximate saved bytes minus KSM metadata overhead. The
  k_rmap count is that metadata; a high k_rmap/(merged pages) ratio means
  scanning effort is likely not paying for itself.
  scan/s = Δpages_scanned / Δt; pages_scanned is cumulative and the kernel
  exposes no native rate. full_scans counts completed full scans. cow/s and
  swpin/s use the same delta-over-time calculation for KSM COW and swap-in
  copy counters.

  `*` after disk_sw means debugfs zswap counters were unreadable, so the
  value is an overestimate. Rates are rounded to whole pages/second. JSON
  uses the same names in `games[]` and adds raw KSM/cgroup values plus the
  `ksm_global` object.

KSM process information:
  Each server block also shows the process's KSM opt-in/mergeable state,
  merged pages, zero-page merges, and process profit. The startup inventory
  includes the full `/proc/<pid>/ksm_stat` view, host KSM counters, and
  actionable suggestions when KSM is disabled, not opted in, unprofitable, or
  showing an excessive rmap-to-merged ratio.

Rates and resets:
  - The first sample after start (or after any reconnect) prints '-' for
    every rate column — there is no previous reading yet to diff against.
  - A negative delta (a counter reading lower than the previous sample)
    means the cgroup/container was recreated and its counters reset to 0.
    This is detected, printed as '-' for that one sample, and resynced
    silently — it will never print a bogus negative or huge rate.
"""

GAME_COLUMNS = (("ram", "RAM", 6), ("anon", "anon", 6), ("file", "file", 6),
                ("zpool", "z_pool", 7), ("zeq", "z_ratio", 7), ("rfz", "rf_z/s", 8),
                ("rfd", "rf_d/s", 8), ("rff", "rf_f/s", 8))
KSM_COLUMNS = (("ksm", "KSM", 5), ("kmerge", "k_merge", 8),
               ("kzero", "k_zero", 7), ("kprofit", "k_profit", 9))
KSM_HOST_COLUMNS = (("kfull", "ΔK_full/s", 10), ("kcow", "K_cow/s", 8),
                    ("kswp", "K_swp/s", 8))
PAK_COLUMNS = (("pram", "p_RAM", 6), ("pz", "p_z", 6), ("pdisk", "p_disk", 7),
               ("prfz", "p_rfz/s", 8), ("prfd", "p_rfd/s", 8))
CONTROL_COLUMNS = (("min", "memory.min"), ("low", "memory.low"),
                   ("high", "memory.high"), ("max", "memory.max"),
                   ("cpu", "cpu.weight"), ("bfq", "io.bfq.weight"),
                   ("writeback", "memory.zswap.writeback"))
KSM_PROCESS_FIELDS = ("ksm_rmap_items", "ksm_zero_pages", "ksm_merging_pages",
                      "ksm_process_profit", "ksm_merge_any", "ksm_mergeable")
KSM_GLOBAL_FIELDS = ("run", "advisor_mode", "use_zero_pages", "pages_shared",
                     "pages_sharing", "ksm_zero_pages", "general_profit",
                     "pages_scanned", "full_scans", "cow_ksm", "ksm_swpin_copy")
KSM_RATE_COUNTERS = ("pages_scanned", "full_scans", "cow_ksm", "ksm_swpin_copy")
DASH = "—"  # em dash, matches the previous script's '—' placeholder


LEGEND_SECTIONS = (
    ("Per-server GAME columns", (
        ("`RAM`", "`memory.current`: total bytes charged to the server cgroup, including anonymous memory, file cache, kernel structures, and compressed zswap storage."),
        ("`anon`", "`memory.stat` `anon`: resident anonymous RAM such as heap and stack. It excludes pages currently stored in zswap."),
        ("`file`", "`memory.stat` `file`: charged file cache, executable/library mappings, mmap data, and tmpfs/shmem. Shown with `--wide`; always present in JSON."),
        ("`z_pool`", "`memory.zswap.current`: compressed bytes currently held in zswap."),
        ("`z_ratio`", "`z_eq / z_pool`: uncompressed-equivalent bytes divided by compressed bytes. For example, `2.74x` means 2.74 bytes of original data are represented by each compressed byte. `—` means the pool is empty, so no ratio exists."),
        ("`rf_z/s`", "`Δzswpin / Δt`: anonymous pages refaulted from zswap. This is RAM-speed decompression and is normally much cheaper than disk I/O."),
        ("`rf_d/s`", "`max(0, Δworkingset_refault_anon − Δzswpin) / Δt`: anonymous pages refaulted from real disk swap. Sustained non-zero values are the lag signal."),
        ("`rf_f/s`", "`Δworkingset_refault_file / Δt`: file-cache pages refaulted from storage. Every refault is disk I/O. A sustained value means needed file pages are repeatedly being evicted and later faulted back in; this counter observes the refault, not when the eviction happened."),
    )),
    ("Per-server KSM columns", (
        ("`KSM`", "Process KSM state from `/proc/<pid>/ksm_stat`: `on` means both `ksm_merge_any` and `ksm_mergeable` are `yes`; `any` means process-wide opt-in; `vma` means at least one mergeable VMA; `off` means neither is active."),
        ("`k_merge`", "Current `ksm_merging_pages` for the inferred WSServer PID: pages participating in KSM merging."),
        ("`k_zero`", "Current `ksm_zero_pages`: pages mapped to the kernel's shared zero page through KSM."),
        ("`k_profit`", "Current `ksm_process_profit`: the kernel's approximate bytes saved by this process's KSM mappings minus KSM metadata overhead. Negative means the metadata cost is larger."),
    )),
    ("Shared host KSM columns (between server and PAK blocks)", (
        ("`ΔK_full/s`", "Derived `Δfull_scans / Δt`: completed full KSM passes per second. It is shown with one decimal place; `0.2/s` means one completed pass about every five seconds. `—` means no previous valid sample or a counter reset."),
        ("`K_cow/s`", "Derived rate from the host-wide `cow_ksm` counter in `/proc/vmstat`: copy-on-write events involving KSM pages per second."),
        ("`K_swp/s`", "Derived rate from `/proc/vmstat` `ksm_swpin_copy`: KSM-related swap-in copies per second."),
    )),
    ("Shared PAK columns", (
        ("`p_RAM`", "PAK cgroup `memory.current`: resident RAM used by the pak/DLC ramdisk and its source file cache."),
        ("`p_z`", "PAK cgroup `memory.zswap.current`: pak bytes compressed in zswap."),
        ("`p_disk`", "`max(0, memory.swap.current − zswapped − swapcached)`: pak pages actually on disk swap."),
        ("`p_rfz/s`", "PAK `Δzswpin / Δt`: pak pages refaulted from zswap per second."),
        ("`p_rfd/s`", "PAK `max(0, Δworkingset_refault_anon − Δzswpin) / Δt`: pak anonymous pages refaulted from disk swap per second."),
    )),
    ("System, controls, and notation", (
        ("`disk_sw`", "`max(0, /proc/swaps Used − SwapCached − zswap_stored_pages × 4096)`: host-wide disk swap, not Soulmask-only. A trailing `*` means zswap debugfs was unreadable and the value is an overestimate."),
        ("cgroup controls", "Startup inventory shows each server's actual `memory.min`, `memory.low`, `memory.high`, `memory.max`, `cpu.weight`, `io.bfq.weight`, and `memory.zswap.writeback`. They are re-read silently every sample; drift prints the old, new, and complete current values."),
        ("`—`", "Unavailable rate: first sample, counter reset, or absent PAK."),
        ("`?`", "Unknown or unavailable value: PID/process file could not be found or read, `CONFIG_KSM`/a field is unavailable, or process-exit timing prevented a read. Numeric `0` is a successful read of zero."),
        ("JSON", "`--json` emits one object per sample. Per-server values are in `games[]`; host KSM values are in `ksm_global`. With one server, the compatibility field `game` is also present."),
    )),
)


def legend_for_width(width: int = LEGEND_WIDTH) -> str:
    """Render the detailed legend as aligned value/explanation columns."""
    output = ["Legend", "======", ""]
    for heading, items in LEGEND_SECTIONS:
        output.extend((heading, "-" * len(heading)))
        value_width = max(len(value) for value, _ in items)
        continuation = " " * (value_width + 3)
        for value, explanation in items:
            prefix = f"{value:<{value_width}}   "
            output.extend(textwrap.wrap(
                explanation, width=max(1, width - len(prefix)),
                initial_indent=prefix, subsequent_indent=continuation,
                break_long_words=False, break_on_hyphens=False,
            ) or [prefix.rstrip()])
        output.append("")
    return "\n".join(output).rstrip()


# ─── low-level reads ──────────────────────────────────────────────────────────

def die(msg: str, code: int = 1) -> None:
    print(f"[monitor:ERROR] {msg}", file=sys.stderr)
    sys.exit(code)


def note(msg: str) -> None:
    print(f"[monitor] {msg}", file=sys.stderr, flush=True)


def note_block(lines) -> None:
    """Print a diagnostic block with every rendered line <= LEGEND_WIDTH."""
    prefix_width = len("[monitor] ")
    width = LEGEND_WIDTH - prefix_width
    for line in lines:
        wrapped = textwrap.wrap(
            line, width=width, break_long_words=True, break_on_hyphens=False,
        ) or [""]
        for part in wrapped:
            note(part)


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


def read_controls(cg_path: str) -> dict:
    """Read the resource controls applied to one server's cgroup.

    The Wings per-server slice owns these values. Returning '?' for a missing
    file keeps the monitor useful across kernel/systemd variants and makes a
    disappearing control visible in the same drift comparison as any other
    change.
    """
    controls = {}
    for key, filename in CONTROL_COLUMNS:
        try:
            controls[key] = read_text(os.path.join(cg_path, filename))
        except (FileNotFoundError, PermissionError):
            controls[key] = "?"
    return controls


def read_process_ksm(pid) -> dict:
    """Read /proc/<pid>/ksm_stat, returning '?' when unavailable."""
    values = {key: "?" for key in KSM_PROCESS_FIELDS}
    values["pid"] = str(pid) if pid is not None else "?"
    if pid is None:
        return values
    try:
        with open(os.path.join(PROC_ROOT, str(pid), "ksm_stat")) as f:
            for line in f:
                parts = line.split()
                if len(parts) >= 2:
                    key = parts[0].rstrip(":")
                    if key in values:
                        values[key] = parts[1]
    except (FileNotFoundError, PermissionError):
        pass
    return values


def read_ksm_global() -> dict:
    """Read host-wide KSM counters and the KSM COW/swap-in event counters."""
    values = {key: "?" for key in KSM_GLOBAL_FIELDS}
    for key in KSM_GLOBAL_FIELDS:
        path = os.path.join(PROC_ROOT, "vmstat") if key in ("cow_ksm", "ksm_swpin_copy") \
            else f"/sys/kernel/mm/ksm/{key}"
        try:
            if path == os.path.join(PROC_ROOT, "vmstat"):
                with open(path) as f:
                    for line in f:
                        parts = line.split()
                        if len(parts) == 2 and parts[0] == key:
                            values[key] = parts[1]
                            break
            else:
                values[key] = read_text(path)
        except (FileNotFoundError, PermissionError):
            pass
    return values


def int_value(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def fmt_pages(value) -> str:
    number = int_value(value)
    return "?" if number is None else str(number)


def fmt_signed_bytes(value) -> str:
    number = int_value(value)
    if number is None:
        return "?"
    sign = "+" if number > 0 else ""
    absolute = abs(number)
    if absolute >= 1073741824:
        rendered = f"{absolute / 1073741824:.1f}G"
    elif absolute >= 1048576:
        rendered = f"{absolute / 1048576:.1f}M"
    elif absolute >= 1024:
        rendered = f"{absolute / 1024:.1f}K"
    else:
        rendered = f"{absolute}B"
    return ("-" if number < 0 else sign) + rendered


def ksm_status(ksm: dict) -> str:
    merge_any = ksm.get("ksm_merge_any")
    mergeable = ksm.get("ksm_mergeable")
    if merge_any == "yes" and mergeable == "yes":
        return "on"
    if merge_any == "yes":
        return "any"
    if mergeable == "yes":
        return "vma"
    if merge_any == "?" and mergeable == "?":
        return "?"
    return "off"


def ksm_process_str(ksm: dict) -> str:
    return (f"pid={ksm.get('pid', '?')} status={ksm_status(ksm)} "
            f"merge_any={ksm.get('ksm_merge_any', '?')} "
            f"mergeable={ksm.get('ksm_mergeable', '?')} "
            f"merging={fmt_pages(ksm.get('ksm_merging_pages'))}p "
            f"zero={fmt_pages(ksm.get('ksm_zero_pages'))}p "
            f"rmap={fmt_pages(ksm.get('ksm_rmap_items'))} "
            f"profit={fmt_signed_bytes(ksm.get('ksm_process_profit'))}")


def ksm_global_str(ksm: dict) -> str:
    return (f"run={ksm.get('run', '?')} advisor={ksm.get('advisor_mode', '?')} "
            f"zero_pages={ksm.get('use_zero_pages', '?')} "
            f"shared={ksm.get('pages_shared', '?')} "
            f"sharing={ksm.get('pages_sharing', '?')} "
            f"ksm_zero={ksm.get('ksm_zero_pages', '?')} "
            f"profit={fmt_signed_bytes(ksm.get('general_profit'))} "
            f"scanned={ksm.get('pages_scanned', '?')} "
            f"scan/s={fmt_rate(ksm.get('pages_scanned_per_s'))} "
            f"full_scans={ksm.get('full_scans', '?')} "
            f"full/s={fmt_rate_fraction(ksm.get('full_scans_per_s'))} "
            f"cow={ksm.get('cow_ksm', '?')} "
            f"cow/s={fmt_rate(ksm.get('cow_ksm_per_s'))} "
            f"swpin_copy={ksm.get('ksm_swpin_copy', '?')} "
            f"swpin/s={fmt_rate(ksm.get('ksm_swpin_copy_per_s'))}")


def ksm_suggestions(servers, global_ksm: dict) -> list[str]:
    suggestions = []
    if global_ksm.get("run") not in ("1", "?"):
        suggestions.append("KSM is stopped: enable /sys/kernel/mm/ksm/run=1 if deduplication is intended.")
    if global_ksm.get("run") == "?":
        suggestions.append("KSM sysfs is unavailable: verify CONFIG_KSM and /sys/kernel/mm/ksm.")
    if global_ksm.get("use_zero_pages") == "0":
        suggestions.append("Consider /sys/kernel/mm/ksm/use_zero_pages=1 to deduplicate zero-filled pages.")

    scanned = int_value(global_ksm.get("pages_scanned"))
    profit = int_value(global_ksm.get("general_profit"))
    if scanned and profit is not None and profit <= 0:
        suggestions.append("Host-wide KSM profit is non-positive after scanning; compare with KSM disabled.")
    if int_value(global_ksm.get("cow_ksm")):
        suggestions.append("KSM COW events are non-zero; watch their rate because writes to merged pages pay a copy cost.")
    if int_value(global_ksm.get("ksm_swpin_copy")):
        suggestions.append("KSM swap-in copies are non-zero; correlate with disk refaults before increasing KSM scope.")

    for server in servers:
        ksm = server.get("ksm", {})
        if ksm_status(ksm) == "?":
            suggestions.append(
                f"{server['uuid']}: /proc/{ksm.get('pid', '?')}/ksm_stat is unavailable; "
                "verify CONFIG_KSM, procfs access, and the server PID.")
        if ksm_status(ksm) == "off":
            suggestions.append(
                f"{server['uuid']}: process is not KSM-opted-in; add PR_SET_MEMORY_MERGE=1 "
                "or the LD_PRELOAD opt-in shim before allocations if this workload is a candidate.")
        merged = int_value(ksm.get("ksm_merging_pages")) or 0
        zero = int_value(ksm.get("ksm_zero_pages")) or 0
        rmap = int_value(ksm.get("ksm_rmap_items"))
        process_profit = int_value(ksm.get("ksm_process_profit"))
        if merged + zero == 0 and ksm_status(ksm) in ("on", "any", "vma"):
            suggestions.append(
                f"{server['uuid']}: opted in but no pages are merged yet; wait for scans, "
                "then verify that the two servers have genuinely identical anonymous data.")
        if merged + zero and process_profit is not None and process_profit <= 0:
            suggestions.append(
                f"{server['uuid']}: process KSM profit is non-positive; narrow the mergeable "
                "ranges or remove the opt-in if this persists.")
        if rmap is not None and merged + zero and rmap / (merged + zero) > 64:
            suggestions.append(
                f"{server['uuid']}: KSM rmap/merged ratio exceeds 64; narrow MADV_MERGEABLE "
                "coverage or disable opt-in for this process.")
    return suggestions


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


def fmt_control_value(key: str, value: str) -> str:
    if key in ("min", "low", "high", "max"):
        return fmt_band_value(value)
    return value


def fmt_rate(v) -> str:
    if v is None:
        return DASH
    return f"{int(round(v))}/s"


def fmt_rate_fraction(v) -> str:
    if v is None:
        return DASH
    return f"{v:.1f}/s"


def fmt_zswap_ratio(z_eq, z_pool) -> str:
    uncompressed = int_value(z_eq)
    compressed = int_value(z_pool)
    if uncompressed is None or compressed is None:
        return "?"
    if compressed <= 0:
        return DASH
    return f"{uncompressed / compressed:.2f}x"


def band_str(band: dict) -> str:
    return (f"min={fmt_band_value(band['min'])} "
            f"high={fmt_band_value(band['high'])} "
            f"writeback={band['writeback']}")


def controls_str(controls: dict) -> str:
    return " ".join(
        f"{label}={fmt_control_value(key, controls.get(key, '?'))}"
        for key, label in (("min", "memory.min"), ("low", "memory.low"),
                           ("high", "memory.high"), ("max", "memory.max"),
                           ("cpu", "cpu.weight"), ("bfq", "io.bfq.weight"),
                           ("writeback", "memory.zswap.writeback"))
    )


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
        with open(os.path.join(PROC_ROOT, str(pid), "cgroup")) as f:
            for line in f:
                line = line.strip()
                if line.startswith("0::"):
                    rel = line.split(":", 2)[2]
                    return "/sys/fs/cgroup" + rel
    except FileNotFoundError:
        pass

    # Fallback when the init process disappeared between docker inspect and
    # this read. The container ID is still enough to locate its Docker scope.
    scope_prefix = f"docker-{cid}"
    for root, dirs, _ in os.walk("/sys/fs/cgroup"):
        for directory in dirs:
            if directory.startswith(scope_prefix) and directory.endswith(".scope"):
                return os.path.join(root, directory)
    return None


def container_server_pid(cid: str, cgroup_path: str | None = None):
    """Find WSServer's host PID from the known container cgroup.

    ``docker top`` is intentionally not used for the PID: its displayed PID
    can be namespace/tooling dependent. The cgroup scope contains the host
    PIDs in cgroup.procs; inspect their command lines and select the game
    process from there.
    """
    cgroup_path = cgroup_path or container_cgroup_path(cid)
    if not cgroup_path or not os.path.isdir(cgroup_path):
        return None

    pids: set[int] = set()
    for root, _, files in os.walk(cgroup_path):
        if "cgroup.procs" not in files:
            continue
        try:
            with open(os.path.join(root, "cgroup.procs")) as f:
                pids.update(int(value) for value in f.read().split())
        except (FileNotFoundError, PermissionError, ValueError):
            continue

    for pid in sorted(pids):
        try:
            with open(os.path.join(PROC_ROOT, str(pid), "cmdline"), "rb") as f:
                cmdline = f.read().replace(b"\0", b" ").decode(errors="replace")
        except (FileNotFoundError, PermissionError):
            continue
        if "WSServer-Linux-Shipping" in cmdline:
            return pid
    return None


def server_slice_path(cgroup_path: str) -> str:
    """Return the nearest Wings per-server slice for a container cgroup.

    A current cgroup path looks like
    ``/wings.slice/wings-<dashless-uuid>.slice/docker-<cid>.scope``. The
    fallback to the container scope keeps the monitor compatible with the
    retired layout, where the resource controls were applied directly to the
    Docker scope.
    """
    prefix = "/sys/fs/cgroup"
    if not cgroup_path.startswith(prefix):
        return cgroup_path
    rel_parts = cgroup_path[len(prefix):].strip("/").split("/")
    for index in range(len(rel_parts) - 1, -1, -1):
        component = rel_parts[index]
        if component.startswith("wings-") and component.endswith(".slice"):
            return prefix + "/" + "/".join(rel_parts[:index + 1])
    return cgroup_path


def selector_matches(selector: str, cid: str, name: str) -> bool:
    """Wings names containers by server UUID, so a UUID prefix is a name
    prefix — covered by the substring test. Also accept container-id
    prefixes."""
    return cid.startswith(selector) or selector in name


def list_wsserver_containers(selector=None) -> list:
    """[(cid, name)] of running WSServer containers, optionally narrowed by
    the -c selector. The game PID is resolved from the cgroup afterward."""
    out = []
    for cid, name in docker_ps():
        if selector is not None and not selector_matches(selector, cid, name):
            continue
        if container_has_wsserver(cid):
            out.append((cid, name))
    return out


def find_game_cgroups(selector=None, poll_s: float = 2) -> list:
    """Poll docker for WSServer-Linux-Shipping containers (optionally
    narrowed by -c/--container). Blocks (printing a wait message once)
    until at least one appears. Returns a list of server records, each with
    the container scope used for metrics and the Wings slice used for control
    verification."""
    sel_msg = f" matching -c '{selector}'" if selector else ""
    waited = False
    while True:
        cands = list_wsserver_containers(selector)
        servers = []
        for cid, name in cands:
            cg = container_cgroup_path(cid)
            if cg and os.path.isdir(cg):
                servers.append({
                    "cid": cid,
                    "name": name,
                    "uuid": name,
                    "pid": container_server_pid(cid, cg),
                    "metrics_cgroup": cg,
                    "slice": server_slice_path(cg),
                })
        if servers:
            if waited:
                note("found Soulmask server(s): " + ", ".join(
                    f"{s['uuid']} ({s['slice']})" for s in servers))
            return servers
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


class NumericCounterRateTracker:
    """Derive rates from counters that may be unavailable on some kernels."""

    def __init__(self, keys):
        self.keys = keys
        self.prev = None
        self.prev_ts = None

    def reset(self):
        self.prev = None
        self.prev_ts = None

    def update(self, ts: float, counters: dict) -> dict:
        current = {key: int_value(counters.get(key)) for key in self.keys}
        rates = {key: None for key in self.keys}
        if self.prev is not None and self.prev_ts is not None:
            dt = ts - self.prev_ts
            if (dt > 0 and all(current[key] is not None for key in self.keys)
                    and all(self.prev[key] is not None for key in self.keys)
                    and not any(current[key] < self.prev[key] for key in self.keys)):
                for key in self.keys:
                    rates[key] = (current[key] - self.prev[key]) / dt
        self.prev = current
        self.prev_ts = ts
        return rates


def add_ksm_rates(ksm: dict, rates: dict) -> dict:
    enriched = dict(ksm)
    for key in KSM_RATE_COUNTERS:
        enriched[f"{key}_per_s"] = rates.get(key)
    return enriched


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

def sample_game(cg_path: str, controls_path: str | None = None, pid=None) -> dict:
    stat = read_stat(os.path.join(cg_path, "memory.stat"))
    ram = read_int(os.path.join(cg_path, "memory.current"))
    zpool = read_int(os.path.join(cg_path, "memory.zswap.current"))
    controls = read_controls(controls_path or cg_path)
    process_ksm = read_process_ksm(pid)
    return {
        "ram": ram,
        "anon": stat.get("anon", 0),
        "file": stat.get("file", 0),
        "zpool": zpool,
        "zeq": stat.get("zswapped", 0),
        "wra": stat.get("workingset_refault_anon", 0),
        "wrf": stat.get("workingset_refault_file", 0),
        "zswpin": stat.get("zswpin", 0),
        "controls": controls,
        "ksm": process_ksm,
        # Keep the old band-shaped view for the JSON compatibility fields and
        # the pak/game writeback explanations.
        "band": {key: controls[key] for key in ("min", "high", "writeback")},
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

def _column_group(columns, prefix=""):
    return " ".join(
        f"{{{prefix}{key}:<{max(width, len(prefix + label))}}}"
        for key, label, width in columns
    )


def table_format(server_count: int, wide: bool) -> str:
    game_columns = GAME_COLUMNS if wide else tuple(c for c in GAME_COLUMNS if c[0] != "file")
    groups = ["{ts:<8}"]
    for index in range(server_count):
        groups.append(_column_group(game_columns, f"s{index + 1}_"))
        groups.append(_column_group(KSM_COLUMNS, f"s{index + 1}_"))
    groups.append(_column_group(KSM_HOST_COLUMNS))
    groups.append(_column_group(PAK_COLUMNS))
    groups.append("{disk_sw}")
    return " | ".join(groups)


def header_lines(server_count: int, wide: bool):
    names = {"ts": "time"}
    game_columns = GAME_COLUMNS if wide else tuple(c for c in GAME_COLUMNS if c[0] != "file")
    for index in range(server_count):
        for key, label, _ in game_columns:
            names[f"s{index + 1}_{key}"] = f"S{index + 1}_{label}"
        for key, label, _ in KSM_COLUMNS:
            names[f"s{index + 1}_{key}"] = f"S{index + 1}_{label}"
    for key, label, _ in KSM_HOST_COLUMNS:
        names[key] = label
    for key, label, _ in PAK_COLUMNS:
        names[key] = label
    names["disk_sw"] = "disk_sw"
    fmt = table_format(server_count, wide)
    head = fmt.format(**names)
    return head, "-" * len(head)


def print_server_inventory(servers, output):
    for index, server in enumerate(servers, start=1):
        controls = server["controls"]
        print(f"  SERVER {index}: UUID {server['uuid']}", file=output)
        print(f"    container: {server['cid']} ({server['name']})", file=output)
        print(f"    slice:     {server['slice']}", file=output)
        print(f"    applied:   {controls_str(controls)}", file=output)
        print(f"    KSM:       {ksm_process_str(server['ksm'])}", file=output)


def initialize_server_controls(servers):
    for server in servers:
        server["controls"] = read_controls(server["slice"])
        server["last_controls"] = dict(server["controls"])
        server["ksm"] = read_process_ksm(server.get("pid"))
        server["last_ksm"] = dict(server["ksm"])


def report_control_drift(server, controls):
    previous = server.get("last_controls")
    if previous is not None:
        changed = []
        for key, label in CONTROL_COLUMNS:
            old = previous.get(key, "?")
            new = controls.get(key, "?")
            if old != new:
                changed.append(f"{label} {fmt_control_value(key, old)} -> "
                              f"{fmt_control_value(key, new)}")
        if changed:
            note_block([
                f"[{server['uuid']}] cgroup drift on {server['slice']}:",
                f"  changed: {'; '.join(changed)}",
                f"  current: {controls_str(controls)}",
            ])
    server["last_controls"] = dict(controls)


def print_ksm_inventory(servers, global_ksm, output):
    print(f"  KSM host:   {ksm_global_str(global_ksm)}", file=output)
    suggestions = ksm_suggestions(servers, global_ksm)
    if suggestions:
        print("  KSM suggestions:", file=output)
        for suggestion in suggestions:
            print(f"    - {suggestion}", file=output)
    else:
        print("  KSM suggestions: no immediate action from current counters.", file=output)


def print_startup_legend(output):
    print(legend_for_width(), file=output)


def print_intro(args, servers, global_ksm):
    print(f"Soulmask memory monitor — Ctrl-C to stop   (interval: {args.interval:g}s)")
    print()
    print_server_inventory(servers, sys.stdout)
    print_ksm_inventory(servers, global_ksm, sys.stdout)
    if os.path.isdir(PAK_CG):
        p_band = read_band(PAK_CG)
        print(f"  PAK   {band_str(p_band)}  {writeback_label(p_band['writeback'])}")
    else:
        print("  PAK   (pak slice not present)")
    print()
    if args.legend:
        print_startup_legend(sys.stdout)
        print()
    print("  Applied server cgroup controls above are re-read every sample; a note is")
    print("  printed on stderr only when they drift." +
          ("" if args.wide else "  ('file' column: --wide or --json.)"))
    print()
    head, dash = header_lines(len(servers), args.wide)
    print(head)
    print(dash)


def server_json(server, g, rf_z, rf_d, rf_f):
    controls = g["controls"]
    return {
        "container_id": server["cid"],
        "container_name": server["name"],
        "uuid": server["uuid"],
        "pid": server["pid"],
        "cgroup": server["metrics_cgroup"],
        "slice": server["slice"],
        "ram_bytes": g["ram"], "anon_bytes": g["anon"], "file_bytes": g["file"],
        "zpool_bytes": g["zpool"], "zeq_bytes": g["zeq"],
        "rf_z_per_s": rf_z, "rf_d_per_s": rf_d, "rf_f_per_s": rf_f,
        "memory_min": controls["min"], "memory_low": controls["low"],
        "memory_high": controls["high"], "memory_max": controls["max"],
        "cpu_weight": controls["cpu"], "io_bfq_weight": controls["bfq"],
        "zswap_writeback": controls["writeback"],
        "ksm": g["ksm"],
    }


def table_row(servers, global_ksm, pak, p_rf_z, p_rf_d, disk_sw, disk_sw_degraded, wide):
    values = {"ts": now_hms()}
    game_columns = GAME_COLUMNS if wide else tuple(c for c in GAME_COLUMNS if c[0] != "file")
    for index, server in enumerate(servers, start=1):
        g = server["sample"]
        rates = server["rates"]
        for key, _, _ in game_columns:
            if key == "ram":
                value = fmt_mb(g["ram"])
            elif key == "anon":
                value = fmt_mb(g["anon"])
            elif key == "file":
                value = fmt_mb(g["file"])
            elif key == "zpool":
                value = fmt_mb(g["zpool"])
            elif key == "zeq":
                value = fmt_zswap_ratio(g["zeq"], g["zpool"])
            else:
                value = fmt_rate(rates[key])
            values[f"s{index}_{key}"] = value
        ksm = g["ksm"]
        values.update({
            f"s{index}_ksm": ksm_status(ksm),
            f"s{index}_kmerge": fmt_pages(ksm.get("ksm_merging_pages")),
            f"s{index}_kzero": fmt_pages(ksm.get("ksm_zero_pages")),
            f"s{index}_kprofit": fmt_signed_bytes(ksm.get("ksm_process_profit")),
    })
    values.update({
        "kfull": fmt_rate_fraction(global_ksm.get("full_scans_per_s")),
        "kcow": fmt_rate(global_ksm.get("cow_ksm_per_s")),
        "kswp": fmt_rate(global_ksm.get("ksm_swpin_copy_per_s")),
    })
    values.update({
        "pram": fmt_mb(pak["ram"]) if pak else DASH,
        "pz": fmt_mb(pak["zpool"]) if pak else DASH,
        "pdisk": fmt_mb(pak["disk"]) if pak else DASH,
        "prfz": fmt_rate(p_rf_z) if pak else DASH,
        "prfd": fmt_rate(p_rf_d) if pak else DASH,
        "disk_sw": fmt_mb(disk_sw) + ("*" if disk_sw_degraded else ""),
    })
    return table_format(len(servers), wide).format(**values)


def run(args):
    servers = find_game_cgroups(args.container)
    initialize_server_controls(servers)
    global_ksm = read_ksm_global()
    for server in servers:
        server["tracker"] = RateTracker(["wra", "zswpin", "wrf"])
    ksm_rate_tracker = NumericCounterRateTracker(KSM_RATE_COUNTERS)
    pak_tracker = RateTracker(["wra", "zswpin"])
    pak_was_present = False
    last_pak_band = None
    row_i = 0
    header_needed = False

    if not args.json:
        print_intro(args, servers, global_ksm)
    else:
        note(f"found {len(servers)} Soulmask server(s):")
        print_server_inventory(servers, sys.stderr)
        print_ksm_inventory(servers, global_ksm, sys.stderr)
        if args.legend:
            print_startup_legend(sys.stderr)

    while True:
        ts = time.time()
        try:
            for server in servers:
                g = sample_game(server["metrics_cgroup"], server["slice"], server.get("pid"))
                report_control_drift(server, g["controls"])
                rates = server["tracker"].update(
                    ts, {"wra": g["wra"], "zswpin": g["zswpin"], "wrf": g["wrf"]})
                rf_z, rf_d = split_rates(rates)
                server["sample"] = g
                server["rates"] = {"rfz": rf_z, "rfd": rf_d, "rff": rates.get("wrf")}
        except FileNotFoundError:
            note("Soulmask cgroup disappeared (container restarted?) — re-discovering all servers...")
            servers = find_game_cgroups(args.container)
            initialize_server_controls(servers)
            for server in servers:
                server["tracker"] = RateTracker(["wra", "zswpin", "wrf"])
            ksm_rate_tracker.reset()
            global_ksm = read_ksm_global()
            header_needed = True
            continue

        global_ksm = read_ksm_global()
        global_ksm = add_ksm_rates(
            global_ksm, ksm_rate_tracker.update(ts, global_ksm))
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
            if p is not None:
                if last_pak_band is not None and last_pak_band != p["band"]:
                    note(f"PAK band changed: {band_str(last_pak_band)}  ->  {band_str(p['band'])}")
                last_pak_band = p["band"]
            else:
                last_pak_band = None

            if header_needed or (row_i and row_i % 20 == 0):
                head, dash = header_lines(len(servers), args.wide)
                print(head)
                print(dash)
                header_needed = False

            print(table_row(servers, global_ksm, p, p_rf_z, p_rf_d, disk_sw, disk_sw_degraded,
                            args.wide), flush=True)
        else:
            games = [server_json(server, server["sample"], server["rates"]["rfz"],
                                 server["rates"]["rfd"], server["rates"]["rff"])
                     for server in servers]
            obj = {
                "ts": now_iso(),
                "epoch": ts,
                "interval_s": args.interval,
                "games": games,
                "ksm_global": global_ksm,
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
            if len(games) == 1:
                obj["game"] = games[0]
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
    class WideRawDescriptionHelpFormatter(argparse.RawDescriptionHelpFormatter):
        def __init__(self, prog):
            super().__init__(prog, max_help_position=36, width=LEGEND_WIDTH)

    p = argparse.ArgumentParser(
        prog="soulmask-zswap-monitor.py",
        description="Soulmask cgroup memory monitor — zswap pressure, pak slice, disk swap.",
        formatter_class=WideRawDescriptionHelpFormatter,
    )
    p.add_argument("interval", nargs="?", default=5.0, type=_interval_type,
                    help="seconds between samples (default: 5)")
    p.add_argument("--json", action="store_true",
                    help="emit one JSON object per sample on stdout instead of the table")
    p.add_argument("--wide", action="store_true",
                    help="add the 'file' column (memory.stat 'file') to the table "
                         "(always included in --json)")
    p.add_argument("--legend", action="store_true",
                    help="print the detailed column legend at startup")
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
