#!/usr/bin/env python3
"""Soulmask host monitor — cgroup memory (zswap pressure, tmpfs slices, disk
swap) plus a per-server RCON `fps` column (game-thread tick rate).

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

RCON `fps` column: sampled over ONE persistent RCON connection per server,
held open for the life of this process (see `soulmask_rcon.py`'s module
docstring for why a fresh connection per sample would be wrong — every RCON
connection ends with the server logging a benign-but-noisy
"Receive error: SE_EWOULDBLOCK" on close, confirmed live). RCON needs root +
`nsenter`; if either is unavailable, or the relay child errors, `fps` shows
'—' for that server without affecting any other column — RCON is a
nice-to-have next to this file's real job of memory monitoring, never a
reason to stall or crash it. Disable entirely with --no-rcon.

Run with --help (works standalone, no root/docker needed) or --legend (same
text, printed at startup right before the live table) for the full
column-by-column guide, including how these per-cgroup numbers relate to
htop's per-process RES/CODE/SHR view.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import select
import shutil
import signal
import subprocess
import sys
import textwrap
import time
from datetime import datetime
from pathlib import Path

RCON_ENGINE = Path(__file__).resolve().parent / "soulmask_rcon.py"
RCON_CONNECT_TIMEOUT_S = 5.0
RCON_POLL_TIMEOUT_S = 2.0
RCON_RESPAWN_BACKOFF_S = 30.0
_FPS_RE = re.compile(r"Average FPS:\s*([\d.]+)")

# How often the monitor rechecks which WSServer containers exist, while it
# already has at least one healthy server (see discover_live_servers /
# rescan_servers). Decoupled from --interval (which can be sub-second) since
# a rescan is a `docker ps` plus one `docker top` per running container on
# the host — on a host with many unrelated containers that's real overhead
# to pay every sample tick just to notice a new Soulmask server. A missed
# server only means a wait of up to this long before it shows up; a server
# that disappears is still caught immediately by the per-sample
# FileNotFoundError path in run(), not gated on this interval.
RESCAN_INTERVAL_S = 30.0

TMPFS0_CG = "/sys/fs/cgroup/soulmask_tmpfs.slice/soulmask_tmpfs-ZSwapMax0.slice"  # incompressible (pak, MemoryZSwapMax=0)
TMPFS1_CG = "/sys/fs/cgroup/soulmask_tmpfs.slice/soulmask_tmpfs-ZSwapMax1.slice"  # compressible (binaries, Steam, libs, zswap-eligible)
PROC_ROOT = "/proc"
LEGEND_WIDTH = 160

# The full column-by-column guide used to be duplicated here as a second,
# hand-formatted copy of what's now in LEGEND_SECTIONS below (rendered via
# legend_for_width(), shown by both --help and --legend) — two texts that
# could silently drift apart, which is exactly how the old copy ended up
# describing columns ("p_RAM", "p_z", "p_disk"...) that no longer exist
# anywhere in the code (a pre-tmpfs-split "PAK" naming leftover). Removed in
# favor of one source of truth: run --help or --legend for the real thing.

GAME_COLUMNS = (("ram", "RAM", 5), ("anon", "anon", 5), ("file", "file", 5),
                ("zpool", "zpool", 6), ("zeq", "ratio", 6), ("rfz", "rfz/s", 7),
                ("rfd", "rfd/s", 7), ("rff", "rff/s", 7))
KSM_COLUMNS = (("kmerge", "merge", 6),
               ("kzero", "zero", 5), ("kprofit", "profit", 7))
RCON_COLUMNS = (("fps", "fps", 5),)
KSM_HOST_COLUMNS = (("kfull", "Kfull/s", 8), ("kcow", "Kcow/s", 7),
                    ("kswp", "Kswp/s", 7))
TMPFS0_COLUMNS = (("t0ram", "T0_RAM", 6), ("t0z", "T0_z", 6),
                  ("t0disk", "T0_disk", 7), ("t0rfz", "T0_rfz/s", 8),
                  ("t0rfd", "T0_rfd/s", 8))
TMPFS1_COLUMNS = (("t1ram", "T1_RAM", 6), ("t1z", "T1_z", 6),
                  ("t1disk", "T1_disk", 7), ("t1rfz", "T1_rfz/s", 8),
                  ("t1rfd", "T1_rfd/s", 8))
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
    ("Per-server RCON columns", (
        ("`fps`", "Average server tick rate from RCON `ServerFPS`, read over one persistent connection held open for this monitor's whole run (not reconnected per sample). `—` means RCON is currently unavailable for that server — missing root/nsenter, a bad `RCON_PASSWORD`, or the relay child backing off after a failure. Disable entirely with `--no-rcon`."),
    )),
    ("Shared host KSM columns (between server and TMPFS blocks)", (
        ("`ΔK_full/s`", "Derived `Δfull_scans / Δt`: completed full KSM passes per second. It is shown with one decimal place; `0.2/s` means one completed pass about every five seconds. `—` means no previous valid sample or a counter reset."),
        ("`K_cow/s`", "Derived rate from the host-wide `cow_ksm` counter in `/proc/vmstat`: copy-on-write events involving KSM pages per second."),
        ("`K_swp/s`", "Derived rate from `/proc/vmstat` `ksm_swpin_copy`: KSM-related swap-in copies per second."),
    )),
    ("Shared TMPFS ZSwapMax0 columns (`T0_*` — incompressible pak/IO-store files, MemoryZSwapMax=0; may be absent)", (
        ("`T0_RAM`", "`soulmask_tmpfs-ZSwapMax0.slice` `memory.current`: tmpfs pages resident in RAM (shmem)."),
        ("`T0_z`", "`memory.zswap.current`: bytes compressed in zswap for this slice. Should stay near 0 here — `MemoryZSwapMax=0` is meant to bypass zswap for this slice."),
        ("`T0_disk`", "`max(0, memory.swap.current − zswapped − swapcached)`: pages actually on real disk swap. Non-zero means zswap was full or bypassed when these pages were evicted."),
        ("`T0_rfz/s`", "`Δzswpin / Δt`: zswap refaults/s on this slice (expected ≈0, since this slice targets a zswap bypass)."),
        ("`T0_rfd/s`", "`max(0, Δworkingset_refault_anon − Δzswpin) / Δt`: disk refaults/s on this slice."),
    )),
    ("Shared TMPFS ZSwapMax1 columns (`T1_*` — compressible binaries, Steam runtime, libraries; zswap-eligible; may be absent)", (
        ("`T1_RAM`", "`soulmask_tmpfs-ZSwapMax1.slice` `memory.current`: tmpfs pages resident in RAM (shmem)."),
        ("`T1_z`", "`memory.zswap.current`: bytes compressed in zswap for this slice."),
        ("`T1_disk`", "`max(0, memory.swap.current − zswapped − swapcached)`: pages actually on real disk swap."),
        ("`T1_rfz/s`", "`Δzswpin / Δt`: zswap refaults/s on this slice."),
        ("`T1_rfd/s`", "`max(0, Δworkingset_refault_anon − Δzswpin) / Δt`: disk refaults/s on this slice."),
    )),
    ("Multi-instance selection, appearing/disappearing, rates, and resets", (
        ("`-c`/`--container`", "Selects one Soulmask WSServer container by server-UUID prefix, container-ID prefix, or any substring of the container name (Wings names each container after its server UUID). Without it, every running WSServer container is monitored. The selector is honoured every time the monitored set is rechecked."),
        ("appearing / disappearing", "The set of monitored servers is rechecked periodically while running (not only reactively): a new WSServer container is picked up within one rescan interval, and a container that stops is dropped without disturbing any other server's row that same tick. A server that is UNCHANGED across a rescan keeps its existing RCON connection and rate-tracking state untouched — a rescan never forces a reconnect."),
        ("WSServer process restart", "If a server's container keeps running but its WSServer process itself restarts with a new PID, that is detected on the next rescan and the affected server's RCON relay reconnects (its memory/cgroup rate tracking is unaffected, since the cgroup itself did not change)."),
        ("first sample / `—`", "The first sample after start (or right after a server is added, or after its cgroup is recreated) prints `—` for every rate column — there is no previous reading yet to diff against."),
        ("counter reset", "A tracked counter reading LOWER than its previous value means the cgroup/container was recreated and its counters reset to 0. This is detected, printed as `—` for that one sample, and resynced silently — it will never print a bogus negative or huge rate."),
    )),
    ("System, controls, and notation", (
        ("`disk_sw`", "`max(0, /proc/swaps Used − SwapCached − zswap_stored_pages × 4096)`: host-wide disk swap, not Soulmask-only. A trailing `*` means zswap debugfs was unreadable and the value is an overestimate."),
        ("cgroup controls", "Startup inventory shows each server's actual `memory.min`, `memory.low`, `memory.high`, `memory.max`, `cpu.weight`, `io.bfq.weight`, and `memory.zswap.writeback`. They are re-read silently every sample; drift prints the old, new, and complete current values."),
        ("`—`", "Unavailable rate: first sample, counter reset, or an absent TMPFS slice."),
        ("`?`", "Unknown or unavailable value: PID/process file could not be found or read, `CONFIG_KSM`/a field is unavailable, or process-exit timing prevented a read. Numeric `0` is a successful read of zero."),
        ("table vs. JSON names", "The live table uses compact abbreviated headers (e.g. `rfz/s`, `zpool`, `ratio` — see the header row printed at startup and every 40 rows for the exact spelling). `--json` uses longer, explicit field names instead (e.g. `rf_z_per_s`, `zpool_bytes`); the ratio itself is not precomputed in JSON — derive it from `zeq_bytes / zpool_bytes` the same way the table's `ratio` column does."),
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


def fmt_fps(v) -> str:
    return DASH if v is None else f"{v:.1f}"


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


def env_of(cid: str, key: str) -> str:
    """One env var from a container's config (RCON_PORT/RCON_PASSWORD,
    injected by Wings) — same pattern used by exec-soulmask-rcon.py."""
    try:
        r = subprocess.run(
            ["docker", "inspect", "-f", "{{range .Config.Env}}{{println .}}{{end}}", cid],
            capture_output=True, text=True, timeout=10)
    except subprocess.TimeoutExpired:
        return ""
    if r.returncode != 0:
        return ""
    prefix = key + "="
    for line in r.stdout.splitlines():
        if line.startswith(prefix):
            return line[len(prefix):]
    return ""


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


def _build_server_record(cid: str, name: str):
    """Resolve one candidate WSServer container into a server record, or
    None if its cgroup can't be resolved right now (container mid-teardown
    — or, confirmed in this project's own devcontainer test environment, a
    sandbox that doesn't share the real Docker host's PID/cgroup
    namespaces at all, the same limitation already documented for nsenter
    in exec-soulmask-rcon.py). Shared by find_game_cgroups (bootstrap) and
    discover_live_servers (ongoing rescans) so both agree on what counts as
    a valid server."""
    cg = container_cgroup_path(cid)
    if not cg or not os.path.isdir(cg):
        return None
    return {
        "cid": cid,
        "name": name,
        "uuid": name,
        "pid": container_server_pid(cid, cg),
        "metrics_cgroup": cg,
        "slice": server_slice_path(cg),
    }


def find_game_cgroups(selector=None, poll_s: float = 2) -> list:
    """Poll docker for WSServer-Linux-Shipping containers (optionally
    narrowed by -c/--container). Blocks (printing a wait message once)
    until at least one appears. Returns a list of server records, each with
    the container scope used for metrics and the Wings slice used for control
    verification.

    This is the BOOTSTRAP/total-loss path only — it deliberately blocks.
    Once at least one server is running, run() switches to the non-blocking
    discover_live_servers()/rescan_servers() pair to notice further servers
    appearing or disappearing without ever stalling an already-healthy
    monitor loop."""
    sel_msg = f" matching -c '{selector}'" if selector else ""
    waited = False
    while True:
        servers = []
        for cid, name in list_wsserver_containers(selector):
            record = _build_server_record(cid, name)
            if record is not None:
                servers.append(record)
        if servers:
            servers = sort_servers(servers)
            if waited:
                note("found Soulmask server(s): " + ", ".join(
                    f"{s['uuid']} ({s['slice']})" for s in servers))
            return servers
        if not waited:
            note(f"waiting for Soulmask container (WSServer-Linux-Shipping){sel_msg}... "
                 "Ctrl-C to abort")
            waited = True
        time.sleep(poll_s)


def discover_live_servers(selector=None) -> list:
    """Non-blocking snapshot of currently-live WSServer containers, as plain
    [{'cid','name','pid','metrics_cgroup','slice'}] records (no 'uuid',
    'tracker', 'rcon', etc. — this is raw discovery input for
    rescan_servers(), not a server record on its own). Never blocks and
    never raises for "nothing found" — returns an empty list."""
    out = []
    for cid, name in list_wsserver_containers(selector):
        record = _build_server_record(cid, name)
        if record is not None:
            out.append(record)
    return out


def rescan_servers(servers: list, live: list, rcon_enabled: bool) -> tuple[list, bool]:
    """Diff `live` (from discover_live_servers) against the currently
    tracked `servers`, updating `servers` incrementally in place where
    possible. Returns (updated_servers, changed).

    The one rule that matters most here: a server present in both lists is
    NEVER rebuilt. Same dict, same RconRelay, same RateTracker — an
    untouched server's persistent RCON connection and rate-tracking
    baseline survive every rescan. That's the entire point of this
    function existing separately from "just call find_game_cgroups()
    again": that call always throws away and rebuilds everything, which
    silently defeated the persistent-connection work this monitor's RCON
    integration depends on.

    A server whose WSServer PID changed (process restarted, container
    didn't) has its pid updated in place — this is what makes RconRelay's
    own pid-change handling in poll_fps() actually reachable, and also
    fixes a real (if minor) staleness bug: without this, KSM stats would
    keep being read from a since-exited (or worse, since-reused) PID
    forever after any in-container process restart.
    """
    live_by_cid = {r["cid"]: r for r in live}
    changed = False

    still_alive = []
    for server in servers:
        entry = live_by_cid.get(server["cid"])
        if entry is None:
            note(f"Soulmask server gone: {server['uuid']} — dropping from monitor")
            rcon = server.get("rcon")
            if rcon is not None:
                rcon.close()
            changed = True
            continue
        if entry["pid"] != server["pid"]:
            note(f"Soulmask server {server['uuid']}: WSServer process restarted "
                 f"(pid {server['pid']} -> {entry['pid']})")
            server["pid"] = entry["pid"]
            changed = True
        still_alive.append(server)
    servers = still_alive

    tracked_cids = {server["cid"] for server in servers}
    for cid, entry in live_by_cid.items():
        if cid in tracked_cids:
            continue
        server = {
            "cid": entry["cid"], "name": entry["name"], "uuid": entry["name"],
            "pid": entry["pid"], "metrics_cgroup": entry["metrics_cgroup"],
            "slice": entry["slice"],
            "tracker": RateTracker(["wra", "zswpin", "wrf"]),
            "rcon": RconRelay(entry["cid"], entry["pid"]) if rcon_enabled else None,
        }
        initialize_server_controls([server])
        servers.append(server)
        note(f"Soulmask server appeared: {entry['name']} — adding to monitor")
        changed = True

    if changed:
        servers = sort_servers(servers)
    return servers, changed


def sample_all_servers(servers: list, ts: float) -> list:
    """Sample every server INDEPENDENTLY: one server's cgroup disappearing
    (FileNotFoundError — container removed/restarted between rescans) drops
    only that server (closing its RconRelay) and must never prevent any
    other, still-healthy server from being sampled and rendered this same
    tick. Returns the possibly-shorter list of servers that sampled
    successfully, each with 'sample'/'rates' populated.

    This replaces the old design, where ALL servers' sampling shared one
    try/except around the whole loop — a single server's disappearance
    silently skipped that tick's output for every OTHER server too."""
    alive = []
    for server in servers:
        try:
            g = sample_game(server["metrics_cgroup"], server["slice"], server.get("pid"))
        except FileNotFoundError:
            note(f"Soulmask cgroup disappeared for {server['uuid']} "
                 "(container removed/restarted?) — dropping from monitor")
            rcon = server.get("rcon")
            if rcon is not None:
                rcon.close()
            continue
        report_control_drift(server, g["controls"])
        rates = server["tracker"].update(
            ts, {"wra": g["wra"], "zswpin": g["zswpin"], "wrf": g["wrf"]})
        rf_z, rf_d = split_rates(rates)
        g["fps"] = server["rcon"].poll_fps(ts, server.get("pid")) if server.get("rcon") else None
        server["sample"] = g
        server["rates"] = {"rfz": rf_z, "rfd": rf_d, "rff": rates.get("wrf")}
        alive.append(server)
    return alive


def _container_cmdline(pid: int | None) -> str:
    """Return the WSServer process command line from /proc/<pid>/cmdline."""
    if pid is None:
        return ""
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            return f.read().replace(b"\0", b" ").decode(errors="replace").strip()
    except (FileNotFoundError, PermissionError):
        return ""

def server_role(server: dict) -> tuple[str, str]:
    """Return (role, label) for a WSServer container.
    role is 'main', 'client', or 'standalone'. Label is e.g. 'MAIN', 'CLIENT', ''.
    Reads the actual game process cmdline from /proc/<pid>/cmdline."""
    cmdline = _container_cmdline(server.get("pid"))
    if "-mainserverport" in cmdline:
        return ("main", "MAIN")
    if "-clientserverconnect" in cmdline:
        return ("client", "CLIENT")
    return ("standalone", "")

def sort_servers(servers: list) -> list:
    """Re-order servers so main comes first, then client, then others.
    This ensures server1 = main in the table output."""
    def sort_key(s):
        role, _ = server_role(s)
        return {"main": 0, "client": 1}.get(role, 2)
    servers.sort(key=sort_key)
    for s in servers:
        _, label = server_role(s)
        if label:
            s["role_label"] = label
    return servers


# ─── RCON (ServerFPS) ──────────────────────────────────────────────────────────

class RconRelay:
    """One persistent `soulmask_rcon.py --relay` child per server, reused for
    this monitor's whole run so ServerFPS polling doesn't pay a fresh
    connect+auth (and the server's own SE_EWOULDBLOCK-on-close log line —
    confirmed live, see soulmask_rcon.py) on every sample.

    Never raises: any failure (no root/nsenter, bad password, dead child,
    a hung reply) is swallowed and reported as fps=None, with a bounded
    respawn backoff so a persistently-broken RCON connection can't turn into
    a respawn-every-tick loop. `poll_fps` never blocks longer than
    RCON_POLL_TIMEOUT_S — a stuck RCON reply must never stall memory
    sampling, which is this file's actual job.
    """

    def __init__(self, cid: str, pid: int | None):
        self.cid = cid
        self.pid = pid
        self.proc: subprocess.Popen | None = None
        self.last_attempt = 0.0
        self.last_error: str | None = None

    def _readline(self, timeout: float) -> str | None:
        if self.proc is None or self.proc.stdout is None:
            return None
        ready, _, _ = select.select([self.proc.stdout], [], [], timeout)
        if not ready:
            return None
        return self.proc.stdout.readline()

    def _kill(self) -> None:
        proc, self.proc = self.proc, None
        if proc is None:
            return
        try:
            proc.terminate()
            proc.wait(timeout=2)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

    def _spawn(self, ts: float) -> bool:
        self.last_attempt = ts
        if self.pid is None:
            self.last_error = "WSServer PID unknown"
            return False
        port = env_of(self.cid, "RCON_PORT") or "19000"
        password = env_of(self.cid, "RCON_PASSWORD")
        if not password:
            self.last_error = "RCON_PASSWORD empty on container env"
            return False
        argv = ["nsenter", f"--net=/proc/{self.pid}/ns/net", "--",
                sys.executable, str(RCON_ENGINE),
                "--port", port, "--password", password, "--relay"]
        try:
            self.proc = subprocess.Popen(
                argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, text=True, bufsize=1)
        except OSError as e:
            self.last_error = f"spawn failed: {e}"
            self.proc = None
            return False

        line = self._readline(RCON_CONNECT_TIMEOUT_S)
        if not line:
            self.last_error = "relay did not respond to connect"
            self._kill()
            return False
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            self.last_error = f"unexpected relay output: {line.strip()!r}"
            self._kill()
            return False
        if not event.get("ok"):
            self.last_error = event.get("error", "connect failed")
            self._kill()
            return False
        self.last_error = None
        return True

    def poll_fps(self, ts: float, pid: int | None) -> float | None:
        if pid != self.pid:
            # Container restarted under us — the old netns is gone. Reset
            # last_attempt too: the backoff below must not apply the OLD
            # pid's timer to a respawn against a brand new pid.
            self._kill()
            self.pid = pid
            self.last_attempt = 0.0

        if self.proc is None or self.proc.poll() is not None:
            self.proc = None
            if ts - self.last_attempt < RCON_RESPAWN_BACKOFF_S:
                return None
            if not self._spawn(ts):
                return None

        try:
            self.proc.stdin.write("ServerFPS\n")
            self.proc.stdin.flush()
        except (BrokenPipeError, OSError) as e:
            self.last_error = str(e)
            self._kill()
            return None

        line = self._readline(RCON_POLL_TIMEOUT_S)
        if not line:
            self.last_error = "no reply from relay (hung or dead)"
            self._kill()
            return None
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            self.last_error = f"unexpected relay output: {line.strip()!r}"
            return None
        if not event.get("ok"):
            self.last_error = event.get("error", "command failed")
            self._kill()
            return None
        match = _FPS_RE.search(event.get("reply", ""))
        return float(match.group(1)) if match else None

    def close(self) -> None:
        self._kill()


def attach_rcon(servers: list, enabled: bool) -> None:
    for server in servers:
        server["rcon"] = RconRelay(server["cid"], server["pid"]) if enabled else None


def close_rcon(servers: list) -> None:
    for server in servers:
        rcon = server.get("rcon")
        if rcon is not None:
            rcon.close()


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


def sample_tmpfs(cg_path):
    """Read memory stats for a tmpfs cgroup slice. Returns None if absent."""
    if not os.path.isdir(cg_path):
        return None
    try:
        stat = read_stat(os.path.join(cg_path, "memory.stat"))
        ram = read_int(os.path.join(cg_path, "memory.current"))
        zpool = read_int(os.path.join(cg_path, "memory.zswap.current"))
        swap_cur = read_int(os.path.join(cg_path, "memory.swap.current"))
        band = read_band(cg_path)
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
    See LEGEND_SECTIONS 'disk_sw' (--help / --legend) for the full derivation."""
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


def _group_width(columns, prefix=""):
    """Total character width of a column group, including inter-column spaces."""
    if not columns:
        return 0
    return sum(max(w, len(prefix + label)) for _, label, w in columns) + len(columns) - 1


def _pad_center(text: str, width: int) -> str:
    """Center text in a field of given width."""
    if len(text) >= width:
        return text[:width]
    left = (width - len(text)) // 2
    return " " * left + text + " " * (width - len(text) - left)


def table_format(server_count: int, wide: bool) -> str:
    game_columns = GAME_COLUMNS if wide else tuple(c for c in GAME_COLUMNS if c[0] != "file")
    groups = ["{ts:<8}"]
    for index in range(server_count):
        # Combine game + KSM + RCON columns into one group (no separator)
        prefix = f"s{index + 1}_"
        groups.append(_column_group(game_columns, prefix) + " " +
                      _column_group(KSM_COLUMNS, prefix) + " " +
                      _column_group(RCON_COLUMNS, prefix))
    groups.append(_column_group(KSM_HOST_COLUMNS))
    groups.append(_column_group(TMPFS0_COLUMNS))
    groups.append(_column_group(TMPFS1_COLUMNS))
    groups.append("{disk_sw}")
    return " | ".join(groups)


def header_lines(server_count: int, wide: bool, servers=None):
    """Return (row1, row2, dash) for a two-row table header.
    Row1: group labels with cgroup config values + KSM status.
    Row2: short column names."""
    game_columns = GAME_COLUMNS if wide else tuple(c for c in GAME_COLUMNS if c[0] != "file")
    SEP = " | "

    def _ksm_status_str(server):
        if server is None:
            return ""
        ksm = server.get("ksm") or {}
        # Build abbreviated KSM status: merge_pages/zero_pages +/-profit
        k_merge = ksm.get("ksm_merging_pages") or 0
        k_zero = ksm.get("ksm_zero_pages") or 0
        k_profit = ksm.get("ksm_process_profit")
        try:
            k_profit_int = int(k_profit)
            profit_str = f"+{_fmt_bytes(k_profit_int)}" if k_profit_int >= 0 else f"{_fmt_bytes(k_profit_int)}"
        except (ValueError, TypeError):
            profit_str = ""
        return f"KSM:m={k_merge}z={k_zero}{profit_str}"

    def _fmt_bytes(b):
        """Format bytes as human readable."""
        if b is None: return "?"
        b = int(b)
        if abs(b) >= 1073741824: return f"{b/1073741824:.0f}G"
        if abs(b) >= 1048576: return f"{b/1048576:.0f}M"
        if abs(b) >= 1024: return f"{b/1024:.0f}K"
        return str(b)

    # ── build group info: (row1_text, [(columns, prefix), ...], total_width, row2_fmt_str) ──
    group_info = []  # each entry: (row1_label, [(col_defs_tuple, prefix), ...], width, row2_fmt)
    # time
    group_info.append(("time", [((("ts", "time", 8),), "")], 8, "{ts:<8}"))

    for index in range(server_count):
        server = servers[index] if servers and index < len(servers) else None
        controls = server["controls"] if server else {}
        role_label = server.get("role_label", "") if server else ""
        role_str = f" ({role_label})" if role_label else ""

        def cv(k):
            return fmt_control_value(k, controls.get(k, "?"))
        ksm_str = _ksm_status_str(server)
        ctrl_str = (f"min={cv('min')} low={cv('low')} high={cv('high')} "
                    f"max={cv('max')} cpu={cv('cpu')} io={cv('bfq')} {ksm_str}")
        label = f"S{index + 1}{role_str}: {ctrl_str}".strip()

        # Combined game + KSM + RCON columns
        prefix = f"s{index + 1}_"
        gw = _group_width(game_columns, prefix)
        kw = _group_width(KSM_COLUMNS, prefix)
        rw = _group_width(RCON_COLUMNS, prefix)
        total_w = gw + 1 + kw + 1 + rw  # 1 space between each sub-group
        row2_fmt = (_column_group(game_columns, prefix) + " " +
                    _column_group(KSM_COLUMNS, prefix) + " " +
                    _column_group(RCON_COLUMNS, prefix))
        group_info.append((label,
            [(game_columns, prefix), (KSM_COLUMNS, prefix), (RCON_COLUMNS, prefix)],
            total_w, row2_fmt))

    # KSM host group
    khw = _group_width(KSM_HOST_COLUMNS)
    kh_fmt = _column_group(KSM_HOST_COLUMNS)
    group_info.append(("KSM host", [(KSM_HOST_COLUMNS, "")], khw, kh_fmt))

    # TMPFS0 group
    t0_band = read_band(TMPFS0_CG) if os.path.isdir(TMPFS0_CG) else {}
    t0_label = f"T0 (pak) min={fmt_band_value(t0_band.get('min','?'))}"
    t0w = _group_width(TMPFS0_COLUMNS)
    t0_fmt = _column_group(TMPFS0_COLUMNS)
    group_info.append((t0_label, [(TMPFS0_COLUMNS, "")], t0w, t0_fmt))

    # TMPFS1 group
    t1_band = read_band(TMPFS1_CG) if os.path.isdir(TMPFS1_CG) else {}
    t1_label = f"T1 (cmpr) min={fmt_band_value(t1_band.get('min','?'))}"
    t1w = _group_width(TMPFS1_COLUMNS)
    t1_fmt = _column_group(TMPFS1_COLUMNS)
    group_info.append((t1_label, [(TMPFS1_COLUMNS, "")], t1w, t1_fmt))

    # disk_sw
    group_info.append(("swap", [((("disk_sw", "disk_sw", 7),), "")], 7, "{disk_sw:<7}"))

    # ── build row1 and row2 ────────────────────────────────────────────────
    row1_parts = []
    row2_parts = []
    for r1_text, sub_groups, width, r2_fmt in group_info:
        row1_parts.append(_pad_center(r1_text, width))
        # Build r2 dict from all sub-groups
        r2 = {}
        for cols, prefix in sub_groups:
            for key, label, _ in cols:
                r2[f"{prefix}{key}"] = label
        row2_parts.append(r2_fmt.format(**r2))

    row1 = " | ".join(row1_parts)
    row2 = " | ".join(row2_parts)
    dash = "-" * max(len(row1), len(row2))
    return row1, row2, dash


def print_server_inventory(servers, output):
    for index, server in enumerate(servers, start=1):
        controls = server["controls"]
        print(f"  SERVER {index}: UUID {server['uuid']}", file=output)
        print(f"    container: {server['cid']} ({server['name']})", file=output)
        role_label = server.get("role_label", "")
        role_str = f" ({role_label})" if role_label else ""
        print(f"    slice:     {server['slice']}{role_str}", file=output)
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
    wings_cg = "/sys/fs/cgroup/wings.slice"
    if os.path.isdir(wings_cg):
        w_band = read_band(wings_cg)
        print(f"  wings.slice   {band_str(w_band)}")
    print()
    print_server_inventory(servers, sys.stdout)
    print_ksm_inventory(servers, global_ksm, sys.stdout)
    parent_cg = "/sys/fs/cgroup/soulmask_tmpfs.slice"
    if os.path.isdir(parent_cg):
        p_band = read_band(parent_cg)
        print(f"  TMPFS parent (soulmask_tmpfs.slice)   min={fmt_band_value(p_band['min'])}")
    for label, cg_path in [("  ZSwapMax0 (pak)", TMPFS0_CG),
                              ("  ZSwapMax1 (compressible)", TMPFS1_CG)]:
        if os.path.isdir(cg_path):
            t_band = read_band(cg_path)
            print(f"  {label}   {band_str(t_band)}  {writeback_label(t_band['writeback'])}")
        else:
            print(f"  {label}   (slice not present)")
    print()
    if args.legend:
        print_startup_legend(sys.stdout)
        print()
    print("  Applied server cgroup controls above are re-read every sample; a note is")
    print("  printed on stderr only when they drift." +
          ("" if args.wide else "  ('file' column: --wide or --json.)"))
    print()
    row1, row2, dash = header_lines(len(servers), args.wide, servers)
    print(row1)
    print(row2)
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
        "fps": g.get("fps"),
    }


def table_row(servers, global_ksm, tmpfs0, tmpfs0_rf_z, tmpfs0_rf_d, tmpfs1, tmpfs1_rf_z, tmpfs1_rf_d, disk_sw, disk_sw_degraded, wide):
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
            f"s{index}_kmerge": fmt_pages(ksm.get("ksm_merging_pages")),
            f"s{index}_kzero": fmt_pages(ksm.get("ksm_zero_pages")),
            f"s{index}_kprofit": fmt_signed_bytes(ksm.get("ksm_process_profit")),
            f"s{index}_fps": fmt_fps(g.get("fps")),
    })
    values.update({
        "kfull": fmt_rate_fraction(global_ksm.get("full_scans_per_s")),
        "kcow": fmt_rate(global_ksm.get("cow_ksm_per_s")),
        "kswp": fmt_rate(global_ksm.get("ksm_swpin_copy_per_s")),
    })
    for tmpfs_key, t_data, t_rf_z, t_rf_d in [
            ("t0", tmpfs0, tmpfs0_rf_z, tmpfs0_rf_d),
            ("t1", tmpfs1, tmpfs1_rf_z, tmpfs1_rf_d),
    ]:
        if t_data is not None:
            values.update({
                f"{tmpfs_key}ram": fmt_mb(t_data["ram"]),
                f"{tmpfs_key}z": fmt_mb(t_data["zpool"]),
                f"{tmpfs_key}disk": fmt_mb(t_data["disk"]),
                f"{tmpfs_key}rfz": fmt_rate(t_rf_z),
                f"{tmpfs_key}rfd": fmt_rate(t_rf_d),
            })
        else:
            values.update({
                f"{tmpfs_key}ram": DASH, f"{tmpfs_key}z": DASH,
                f"{tmpfs_key}disk": DASH, f"{tmpfs_key}rfz": DASH,
                f"{tmpfs_key}rfd": DASH,
            })
    values.update({
        "disk_sw": fmt_mb(disk_sw) + ("*" if disk_sw_degraded else ""),
    })
    return table_format(len(servers), wide).format(**values)


def run(args):
    servers = find_game_cgroups(args.container)
    initialize_server_controls(servers)
    attach_rcon(servers, not args.no_rcon)
    global_ksm = read_ksm_global()
    for server in servers:
        server["tracker"] = RateTracker(["wra", "zswpin", "wrf"])
    ksm_rate_tracker = NumericCounterRateTracker(KSM_RATE_COUNTERS)
    tmpfs0_tracker = RateTracker(["wra", "zswpin"])
    tmpfs1_tracker = RateTracker(["wra", "zswpin"])
    tmpfs0_was_present = False
    tmpfs1_was_present = False
    last_tmpfs0_band = None
    last_tmpfs1_band = None
    row_i = 0
    header_needed = False
    next_rescan_ts = time.time() + RESCAN_INTERVAL_S

    if not args.json:
        print_intro(args, servers, global_ksm)
    else:
        note(f"found {len(servers)} Soulmask server(s):")
        print_server_inventory(servers, sys.stderr)
        print_ksm_inventory(servers, global_ksm, sys.stderr)
        if args.legend:
            print_startup_legend(sys.stderr)

    try:
        while True:
            ts = time.time()

            # Non-blocking periodic membership recheck: pick up newly
            # appeared servers, drop ones docker no longer lists, and
            # notice a WSServer PID change on an otherwise-unchanged
            # server. An UNCHANGED server's dict — and therefore its
            # RconRelay/RateTracker — is never touched here; see
            # rescan_servers()'s docstring for why that matters.
            if ts >= next_rescan_ts:
                live = discover_live_servers(args.container)
                servers, changed = rescan_servers(servers, live, not args.no_rcon)
                if changed:
                    header_needed = True
                next_rescan_ts = ts + RESCAN_INTERVAL_S

            if not servers:
                # Every server is gone, not just one — this is the same
                # "nothing to monitor" state as a cold start, so reuse the
                # same blocking bootstrap rather than spinning on an empty
                # table. Immediate (not gated on RESCAN_INTERVAL_S): there
                # is nothing else useful for this loop to do in the
                # meantime anyway.
                note("all Soulmask servers gone — waiting for one to (re)appear...")
                servers = find_game_cgroups(args.container)
                initialize_server_controls(servers)
                attach_rcon(servers, not args.no_rcon)
                for server in servers:
                    server["tracker"] = RateTracker(["wra", "zswpin", "wrf"])
                ksm_rate_tracker.reset()
                global_ksm = read_ksm_global()
                header_needed = True
                next_rescan_ts = time.time() + RESCAN_INTERVAL_S
                continue

            # Each server is sampled in isolation — one disappearing here
            # (its cgroup vanished between rescans) drops only that server
            # and never blocks this tick's row for anyone else.
            sampled = sample_all_servers(servers, ts)
            if len(sampled) != len(servers):
                header_needed = True
            servers = sampled

            if not servers:
                # The server(s) we had all disappeared reactively, mid-tick
                # (rather than being caught by the proactive rescan above).
                # Skip rendering this tick; the top-of-loop "all gone"
                # branch picks it up on the very next iteration.
                continue

            global_ksm = read_ksm_global()
            global_ksm = add_ksm_rates(
                global_ksm, ksm_rate_tracker.update(ts, global_ksm))
            tmpfs0 = sample_tmpfs(TMPFS0_CG)
            if tmpfs0 is None:
                tmpfs0_tracker.reset()
                tmpfs0_was_present = False
                tmpfs0_rf_z = tmpfs0_rf_d = None
            else:
                if not tmpfs0_was_present:
                    tmpfs0_tracker.reset()
                tmpfs0_was_present = True
                t0_rates = tmpfs0_tracker.update(ts, {"wra": tmpfs0["wra"], "zswpin": tmpfs0["zswpin"]})
                tmpfs0_rf_z, tmpfs0_rf_d = split_rates(t0_rates)

            tmpfs1 = sample_tmpfs(TMPFS1_CG)
            if tmpfs1 is None:
                tmpfs1_tracker.reset()
                tmpfs1_was_present = False
                tmpfs1_rf_z = tmpfs1_rf_d = None
            else:
                if not tmpfs1_was_present:
                    tmpfs1_tracker.reset()
                tmpfs1_was_present = True
                t1_rates = tmpfs1_tracker.update(ts, {"wra": tmpfs1["wra"], "zswpin": tmpfs1["zswpin"]})
                tmpfs1_rf_z, tmpfs1_rf_d = split_rates(t1_rates)

            disk_sw, disk_sw_degraded = disk_swap_bytes()

            if not args.json:
                if tmpfs0 is not None:
                    if last_tmpfs0_band is not None and last_tmpfs0_band != tmpfs0["band"]:
                        note(f"T0 band changed: {band_str(last_tmpfs0_band)}  ->  {band_str(tmpfs0['band'])}")
                    last_tmpfs0_band = tmpfs0["band"]
                else:
                    last_tmpfs0_band = None
                if tmpfs1 is not None:
                    if last_tmpfs1_band is not None and last_tmpfs1_band != tmpfs1["band"]:
                        note(f"T1 band changed: {band_str(last_tmpfs1_band)}  ->  {band_str(tmpfs1['band'])}")
                    last_tmpfs1_band = tmpfs1["band"]
                else:
                    last_tmpfs1_band = None

                if header_needed or (row_i and row_i % 40 == 0):
                    row1, row2, dash = header_lines(len(servers), args.wide, servers)
                    print(row1)
                    print(row2)
                    print(dash)
                    header_needed = False

                print(table_row(servers, global_ksm, tmpfs0, tmpfs0_rf_z, tmpfs0_rf_d, tmpfs1, tmpfs1_rf_z, tmpfs1_rf_d, disk_sw, disk_sw_degraded,
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
                    "tmpfs_zswapmax0": None if tmpfs0 is None else {
                        "cgroup": TMPFS0_CG,
                        "ram_bytes": tmpfs0["ram"], "zpool_bytes": tmpfs0["zpool"],
                        "disk_bytes": tmpfs0["disk"],
                        "rf_z_per_s": tmpfs0_rf_z, "rf_d_per_s": tmpfs0_rf_d,
                        "memory_min": tmpfs0["band"]["min"], "memory_high": tmpfs0["band"]["high"],
                        "zswap_writeback": tmpfs0["band"]["writeback"],
                    },
                    "tmpfs_zswapmax1": None if tmpfs1 is None else {
                        "cgroup": TMPFS1_CG,
                        "ram_bytes": tmpfs1["ram"], "zpool_bytes": tmpfs1["zpool"],
                        "disk_bytes": tmpfs1["disk"],
                        "rf_z_per_s": tmpfs1_rf_z, "rf_d_per_s": tmpfs1_rf_d,
                        "memory_min": tmpfs1["band"]["min"], "memory_high": tmpfs1["band"]["high"],
                        "zswap_writeback": tmpfs1["band"]["writeback"],
                    },
                    "disk_sw_bytes": disk_sw,
                    "disk_sw_estimated": disk_sw_degraded,
                }
                if len(games) == 1:
                    obj["game"] = games[0]
                print(json.dumps(obj), flush=True)

            row_i += 1
            time.sleep(args.interval)
    finally:
        close_rcon(servers)


# ─── CLI ───────────────────────────────────────────────────────────────────────

def _interval_type(s):
    try:
        v = float(s)
    except ValueError:
        raise argparse.ArgumentTypeError(f"invalid interval {s!r} — must be a number")
    if v <= 0:
        raise argparse.ArgumentTypeError("interval must be > 0")
    return v


def help_description() -> str:
    """-h/--help's full text: works standalone (no root/docker/running
    instance needed — argparse handles -h before main()'s root/docker
    checks) and pulls the column-by-column guide from the SAME
    LEGEND_SECTIONS/legend_for_width() that --legend prints at startup, so
    the two can never drift apart the way the old COLUMN_GUIDE docstring
    copy eventually did (see its removal note above)."""
    intro = (
        "Soulmask host monitor — cgroup memory (zswap pressure, tmpfs slices,\n"
        "disk swap) plus a per-server RCON 'fps' column (game-thread tick rate).\n"
        "\n"
        "Every number below is a PER-CGROUP metric (memory.stat, memory.zswap.current,\n"
        "memory.swap.current for the whole container), not a per-process number like\n"
        "top/htop/free/ps show. The most important consequence: this file's 'RAM'\n"
        "column (memory.current) is typically LARGER than top's RES for the same\n"
        "workload, because memory.current also counts kernel/slab structures and the\n"
        "compressed zswap pool that RES never includes — see 'How these columns\n"
        "relate to htop's process view' below for the full mapping.\n"
    )
    return intro + "\n" + legend_for_width()


def parse_args(argv=None):
    class WideRawDescriptionHelpFormatter(argparse.RawDescriptionHelpFormatter):
        def __init__(self, prog):
            super().__init__(prog, max_help_position=36, width=LEGEND_WIDTH)

    p = argparse.ArgumentParser(
        prog="soulmask-monitor.py",
        description=help_description(),
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
    p.add_argument("--no-rcon", action="store_true",
                    help="skip the RCON 'fps' column entirely (no nsenter/RCON calls at all)")
    return p.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)

    if os.geteuid() != 0:
        die("must run as root (sudo) — reading cgroup memory.stat and the zswap debugfs "
            "counters requires root.")
    if not shutil.which("docker"):
        die("docker not found in PATH.")

    # SIGTERM (systemd stop, etc.) must also drain into run()'s try/finally so
    # RCON relay children get terminated explicitly rather than orphaned.
    signal.signal(signal.SIGTERM, lambda signum, frame: sys.exit(0))

    try:
        run(args)
    except KeyboardInterrupt:
        print(file=sys.stderr)
        note("stopped.")
        sys.exit(0)


if __name__ == "__main__":
    main()
