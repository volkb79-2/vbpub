#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0
"""
damon_cli.py — Unified CLI for DAMON memory analysis (Python-native, no bash).

Replaces run_analysis.sh with proper error handling, logging, and debuggability.

PREREQUISITES (see also ``damon_cli.py requirements``):
    - Linux 5.18+ with CONFIG_DAMON_SYSFS=y
    - Root privileges (sudo)
    - damo Python package (pip install damo) — auto-detected from ./venv/bin/damo
    - The shared library: lib/damon_analysis.py (same directory)

QUICK SETUP on a fresh system:
    apt-get install -y python3-venv
    python3 -m venv venv
    venv/bin/pip install damo
    sudo venv/bin/python3 damon_cli.py requirements   # verify everything
    sudo venv/bin/python3 damon_cli.py diagnose        # full system check

Usage:
    sudo python3 damon_cli.py diagnose
    sudo python3 damon_cli.py requirements
    sudo python3 damon_cli.py profile-pid <PID> [--duration SEC] [--output json|text]
    sudo python3 damon_cli.py profile-container <NAME> [--duration SEC]
    sudo python3 damon_cli.py profile-system [--duration SEC]
    sudo python3 damon_cli.py classify <PID> [--duration SEC] [--output json|text|csv]
    sudo python3 damon_cli.py auto-reclaim on [--min-age SEC] [--quota-sz SIZE]
    sudo python3 damon_cli.py auto-lru-sort on [--hot-thres PERMIL] [--cold-age SEC]
    sudo python3 damon_cli.py monitor-pid <PID>

For detailed help on each subcommand:
    python3 damon_cli.py <subcommand> --help
"""

import argparse
import logging
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional, Dict, List

# ── path setup ──────────────────────────────────────────────────────
SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR / 'lib'))

from damon_analysis import (
    SysfsInterface, Classifier, Monitor, ReportFormatter,
    get_process_info,
)

# ── constants ───────────────────────────────────────────────────────
DAMO_BIN = str(SCRIPT_DIR / 'venv' / 'bin' / 'damo')
if not os.path.isfile(DAMO_BIN):
    DAMO_BIN = 'damo'  # fallback to PATH

OUTPUT_DIR = SCRIPT_DIR / 'output'
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

SYSFS_KDAMONDS = '/sys/kernel/mm/damon/admin/kdamonds'
SYSFS_DAMON_STAT_ENABLED = '/sys/module/damon_stat/parameters/enabled'
SYSFS_DAMON_RECLAIM = '/sys/module/damon_reclaim/parameters'
SYSFS_DAMON_LRU_SORT = '/sys/module/damon_lru_sort/parameters'

LOG = logging.getLogger('damon_cli')

# ── helpers ─────────────────────────────────────────────────────────

def die(msg: str, code: int = 1):
    """Print error and exit."""
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(code)


def require_root():
    if os.geteuid() != 0:
        die("Root privileges required. Run with sudo.")


def check_damon_sysfs():
    if not os.path.isdir(SYSFS_KDAMONDS):
        die(
            "DAMON sysfs not available at "
            f"{SYSFS_KDAMONDS}\n"
            "Check: grep CONFIG_DAMON_SYSFS /boot/config-$(uname -r)"
        )


def sysfs_read(path: str) -> Optional[str]:
    """Safely read a sysfs file, return None on failure."""
    try:
        with open(path) as f:
            return f.read().strip()
    except (OSError, IOError) as e:
        LOG.debug("sysfs_read(%s) → %s", path, e)
        return None


def sysfs_write(path: str, value: str) -> bool:
    """Safely write to a sysfs file, return success."""
    try:
        with open(path, 'w') as f:
            f.write(str(value))
        return True
    except (OSError, IOError) as e:
        LOG.debug("sysfs_write(%s, %s) → %s", path, value, e)
        return False


def damo(*args, timeout: int = 30) -> subprocess.CompletedProcess:
    """Run a damo command, return CompletedProcess or raise."""
    cmd = [DAMO_BIN] + list(args)
    LOG.debug("damo: %s", ' '.join(cmd))
    return subprocess.run(
        cmd, capture_output=True, text=True, timeout=timeout
    )


def check_damon_stat() -> Optional[str]:
    """Return 'enabled', 'disabled', or None if module not loaded."""
    if not os.path.isfile(SYSFS_DAMON_STAT_ENABLED):
        return None
    val = sysfs_read(SYSFS_DAMON_STAT_ENABLED)
    if val == 'Y':
        return 'enabled'
    elif val == 'N':
        return 'disabled'
    return 'unknown'


def disable_damon_stat() -> bool:
    """Disable damon_stat so manual DAMON use works. Returns success."""
    if check_damon_stat() != 'enabled':
        return True  # already disabled or not present
    LOG.info("Disabling damon_stat (it occupies the DAMON kdamond)")
    ok = sysfs_write(SYSFS_DAMON_STAT_ENABLED, 'N')
    if ok:
        time.sleep(0.3)
    return ok


def human_bytes(b: int) -> str:
    """Format byte count for humans."""
    for unit in ['B', 'KiB', 'MiB', 'GiB', 'TiB']:
        if abs(b) < 1024.0:
            return f"{b:.1f} {unit}" if unit != 'B' else f"{b} B"
        b /= 1024.0
    return f"{b:.1f} PiB"


def human_int(n: int) -> str:
    """Format integer with commas."""
    return f"{n:,}"


def parse_duration(s: str) -> int:
    """Parse a duration string like '120', '2m', '1h' into seconds."""
    s = s.strip().lower()
    multipliers = {'s': 1, 'm': 60, 'h': 3600}
    for suffix, mult in multipliers.items():
        if s.endswith(suffix):
            try:
                return int(float(s[:-1]) * mult)
            except ValueError:
                pass
    try:
        return int(float(s))
    except ValueError:
        raise argparse.ArgumentTypeError(f"Invalid duration: {s}")


def parse_size(s: str) -> int:
    """Parse a size string like '512M', '1G', '128MiB' into bytes."""
    s = s.strip().upper().replace('IB', '')
    units = {'B': 1, 'K': 1024, 'M': 1024**2, 'G': 1024**3, 'T': 1024**4}
    for suffix, mult in sorted(units.items(), key=lambda x: -len(x[0])):
        if s.endswith(suffix):
            try:
                return int(float(s[:-len(suffix)]) * mult)
            except ValueError:
                pass
    try:
        return int(s)
    except ValueError:
        raise argparse.ArgumentTypeError(f"Invalid size: {s}")


# ── subcommand: requirements ────────────────────────────────────────

def cmd_requirements(args):
    """Check installation prerequisites — non-root friendly."""
    print("=== DAMON CLI — Installation Prerequisites ===\n")

    all_ok = True

    def check(what, ok, ok_detail='', fail_detail=''):
        nonlocal all_ok
        mark = '✓' if ok else '✗'
        print(f"  {mark} {what}")
        if ok and ok_detail:
            print(f"     {ok_detail}")
        if not ok:
            if fail_detail:
                print(f"     {fail_detail}")
            all_ok = False

    # Python version
    py_ver = sys.version_info
    check(f"Python ≥ 3.9", py_ver >= (3, 9),
          ok_detail=f"found {py_ver.major}.{py_ver.minor}.{py_ver.micro}")

    # damo binary
    damo_found = os.path.isfile(DAMO_BIN) or shutil.which('damo') is not None
    check(f"damo installed", damo_found,
          ok_detail=str(DAMO_BIN),
          fail_detail=f"looked at {DAMO_BIN} — run: pip install damo")

    # lib/damon_analysis.py
    lib_path = SCRIPT_DIR / 'lib' / 'damon_analysis.py'
    check(f"Shared library", lib_path.is_file(),
          ok_detail=str(lib_path),
          fail_detail=f"missing — expected at {lib_path}")

    # Root check (informational)
    is_root = os.geteuid() == 0
    check(f"Root privileges", is_root,
          ok_detail="yes",
          fail_detail="not root — use sudo for 'start', 'classify', etc.")

    # Kernel config (read-only, doesn't require root)
    config_path = Path(f"/boot/config-{os.uname().release}")
    if config_path.is_file():
        try:
            cfg = config_path.read_text()
            for flag in ['CONFIG_DAMON=y', 'CONFIG_DAMON_SYSFS=y',
                          'CONFIG_DAMON_VADDR=y', 'CONFIG_DAMON_PADDR=y']:
                check(flag, flag in cfg)
        except OSError:
            check("Kernel config readable", False, fail_detail=str(config_path))
    else:
        check("Kernel config", False,
              fail_detail=f"{config_path} not found — is /boot mounted?")

    # sysfs available
    sysfs_ok = os.path.isdir(SYSFS_KDAMONDS)
    check("DAMON sysfs mounted", sysfs_ok,
          fail_detail="missing /sys/kernel/mm/damon/admin/kdamonds" if not sysfs_ok else "")

    # damon_stat
    if os.path.isfile(SYSFS_DAMON_STAT_ENABLED):
        try:
            ds = sysfs_read(SYSFS_DAMON_STAT_ENABLED)
            check("damon_stat module", True, ok_detail=f"loaded (enabled={ds})")
        except Exception:
            check("damon_stat module", True, ok_detail="loaded")
    else:
        check("damon_stat module", True, ok_detail="not loaded (ok)")

    # daemon tools (optional)
    has_docker = shutil.which('docker') is not None
    has_podman = shutil.which('podman') is not None
    has_perf = shutil.which('perf') is not None
    check("Docker or Podman (for containers)", has_docker or has_podman,
          ok_detail="available" if (has_docker or has_podman) else "",
          fail_detail="install docker.io or podman for container analysis")
    check("perf (for tracepoint recording)", has_perf,
          ok_detail="available",
          fail_detail="optional — only needed for 'damo record' full tracing")

    print()
    if all_ok:
        print("All prerequisites satisfied.")
        print("Run 'sudo damon_cli.py diagnose' for full system details.")
    else:
        print("Some prerequisites are missing. See above for details.")
        print("\nQuick install on Debian/Ubuntu:")
        print("  apt-get install -y python3-venv linux-perf")
        print("  python3 -m venv venv")
        print("  venv/bin/pip install damo")
        print("  # Reboot if CONFIG_DAMON_*=y is missing from kernel config")


# ── subcommand: diagnose ────────────────────────────────────────────

def cmd_diagnose(args):
    """Print system DAMON readiness report."""
    print("=== DAMON System Diagnostics ===\n")

    # Kernel
    print(f"Kernel: {os.uname().release}\n")

    # Config
    print("--- DAMON Kernel Config ---")
    config_path = f"/boot/config-{os.uname().release}"
    if os.path.isfile(config_path):
        try:
            with open(config_path) as f:
                for line in f:
                    if line.startswith('CONFIG_DAMON'):
                        print(f"  {line.rstrip()}")
        except OSError:
            print("  (could not read config)")
    else:
        print(f"  Config not found at {config_path}")
    print()

    # sysfs
    print("--- DAMON sysfs ---")
    if os.path.isdir(SYSFS_KDAMONDS):
        print("  sysfs: AVAILABLE at /sys/kernel/mm/damon/admin/")
        nr = sysfs_read(os.path.join(SYSFS_KDAMONDS, 'nr_kdamonds'))
        print(f"  nr_kdamonds: {nr}")
    else:
        print("  sysfs: MISSING — DAMON control unavailable")
    print()

    # damo
    print("--- damo Tool ---")
    if os.path.isfile(DAMO_BIN) and os.access(DAMO_BIN, os.X_OK):
        try:
            result = damo('report', 'sysinfo', timeout=10)
            if result.returncode == 0:
                print(result.stdout)
            else:
                print(f"  damo found but report failed: {result.stderr}")
        except Exception as e:
            print(f"  damo found but invocation failed: {e}")
    else:
        print(f"  damo not found at {DAMO_BIN}")
    print()

    # Modules
    print("--- DAMON Kernel Modules ---")
    for mod_name, mod_path in [
        ('damon_reclaim', SYSFS_DAMON_RECLAIM),
        ('damon_lru_sort', SYSFS_DAMON_LRU_SORT),
        ('damon_stat', '/sys/module/damon_stat/parameters'),
    ]:
        if os.path.isdir(mod_path):
            print(f"  {mod_name}: LOADED")
            enabled = sysfs_read(os.path.join(mod_path, 'enabled'))
            print(f"    enabled: {enabled}")
            if mod_name == 'damon_stat' and enabled == 'Y':
                print("    ⚠ WARNING: damon_stat occupies the DAMON kdamond.")
                print("      Disable it before manual DAMON use with:")
                print("      sudo python3 damon_cli.py damon-stat off")
        else:
            print(f"  {mod_name}: not loaded")
    print()

    # Memory
    print("--- System Memory ---")
    try:
        meminfo = {}
        with open('/proc/meminfo') as f:
            for line in f:
                parts = line.split(':')
                if len(parts) >= 2:
                    meminfo[parts[0].strip()] = parts[1].strip()
        memtotal = int(meminfo.get('MemTotal', '0').split()[0]) * 1024
        memavail = int(meminfo.get('MemAvailable', '0').split()[0]) * 1024
        swaptotal = int(meminfo.get('SwapTotal', '0').split()[0]) * 1024
        swapfree = int(meminfo.get('SwapFree', '0').split()[0]) * 1024
        zswap = meminfo.get('Zswap', '0 kB')
        print(f"  RAM: {human_bytes(memtotal)} total, {human_bytes(memavail)} available")
        print(f"  Swap: {human_bytes(swaptotal)} total, {human_bytes(swapfree)} free")
        print(f"  Zswap: {zswap}")
    except Exception as e:
        print(f"  (error reading meminfo: {e})")
    print()

    # Top processes
    print("--- Top 10 Processes by RSS ---")
    try:
        result = subprocess.run(
            ['ps', 'aux', '--sort=-%mem'],
            capture_output=True, text=True, timeout=10
        )
        lines = result.stdout.splitlines()
        print(lines[0])  # header
        for line in lines[1:11]:
            parts = line.split(None, 10)
            if len(parts) >= 11:
                print(f"  PID {parts[1]:>6s}  RSS {parts[5]:>6s}  {parts[10]}")
    except Exception as e:
        print(f"  (error running ps: {e})")
    print()

    print("=== End Diagnostics ===")


# ── subcommand: profile-pid ─────────────────────────────────────────

def cmd_profile_pid(args):
    """Profile a process with DAMON."""
    require_root()
    check_damon_sysfs()
    disable_damon_stat()

    # Forward to analyze_process.py
    script = str(SCRIPT_DIR / 'analyze_process.py')
    cmd = [sys.executable, script, str(args.pid)]
    if args.duration:
        cmd.extend(['--duration', str(args.duration)])
    if args.output:
        cmd.extend(['--output', args.output])
    if args.output_file:
        cmd.extend(['--output-file', args.output_file])
    if args.sample_us:
        cmd.extend(['--sample-us', str(args.sample_us)])
    if args.aggr_us:
        cmd.extend(['--aggr-us', str(args.aggr_us)])
    if args.hot_rate:
        cmd.extend(['--hot-rate', str(args.hot_rate)])
    if args.warm_rate:
        cmd.extend(['--warm-rate', str(args.warm_rate)])
    if args.cold_age:
        cmd.extend(['--cold-age', str(args.cold_age)])
    if args.idle_age:
        cmd.extend(['--idle-age', str(args.idle_age)])

    LOG.info("Running: %s", ' '.join(cmd))
    subprocess.run(cmd)


# ── subcommand: profile-container ───────────────────────────────────

def cmd_profile_container(args):
    """Profile a container with DAMON."""
    require_root()
    check_damon_sysfs()
    disable_damon_stat()

    script = str(SCRIPT_DIR / 'analyze_container.py')
    cmd = [sys.executable, script, args.container]
    if args.duration:
        cmd.extend(['--duration', str(args.duration)])
    if args.output:
        cmd.extend(['--output', args.output])
    if args.output_file:
        cmd.extend(['--output-file', args.output_file])
    if args.mode:
        cmd.extend(['--mode', args.mode])
    if args.cgroup_path:
        cmd.extend(['--cgroup-path', args.cgroup_path])

    LOG.info("Running: %s", ' '.join(cmd))
    subprocess.run(cmd)


# ── subcommand: profile-system ──────────────────────────────────────

def cmd_profile_system(args):
    """System-wide physical memory profile."""
    require_root()
    check_damon_sysfs()
    disable_damon_stat()

    duration = args.duration or 60
    timestamp = time.strftime('%Y%m%d_%H%M%S')
    output = args.output_file or str(OUTPUT_DIR / f'system_{timestamp}.json')

    print(f"[*] System-wide physical memory profile ({duration}s)...", file=sys.stderr)

    # Start DAMON on physical address space
    try:
        result = damo('start', 'paddr', '-s', '100ms', '-a', '2s', timeout=15)
        if result.returncode != 0:
            die(f"Failed to start DAMON: {result.stderr}")
    except subprocess.TimeoutExpired:
        die("damo start timed out")

    print(f"[*] Monitoring started, collecting for {duration}s...", file=sys.stderr)
    time.sleep(duration)

    # Record a snapshot
    try:
        damo('record', '--snapshot', '0', '1', '--output_file', output, timeout=15)
    except Exception as e:
        LOG.warning("Snapshot recording failed: %s", e)

    # Stop
    try:
        damo('stop', timeout=10)
    except Exception:
        pass

    print(f"[*] System profile saved to: {output}", file=sys.stderr)

    # Try to show a text summary
    try:
        result = damo('report', 'access', '--input_file', output, timeout=10)
        if result.returncode == 0:
            print(result.stdout)
    except Exception:
        print(f"[*] Raw data in {output} — use 'damo report access --input_file {output}'",
              file=sys.stderr)


# ── subcommand: classify ────────────────────────────────────────────

def cmd_classify(args):
    """Classify process memory hot/warm/cold."""
    require_root()
    check_damon_sysfs()
    disable_damon_stat()

    script = str(SCRIPT_DIR / 'analyze_process.py')
    cmd = [sys.executable, script, str(args.pid)]
    if args.duration:
        cmd.extend(['--duration', str(args.duration)])
    if args.output:
        cmd.extend(['--output', args.output])
    if args.output_file:
        cmd.extend(['--output-file', args.output_file])
    if args.hot_rate:
        cmd.extend(['--hot-rate', str(args.hot_rate)])
    if args.warm_rate:
        cmd.extend(['--warm-rate', str(args.warm_rate)])
    if args.cold_age:
        cmd.extend(['--cold-age', str(args.cold_age)])
    if args.idle_age:
        cmd.extend(['--idle-age', str(args.idle_age)])
    if args.sample_us:
        cmd.extend(['--sample-us', str(args.sample_us)])
    if args.aggr_us:
        cmd.extend(['--aggr-us', str(args.aggr_us)])
    if args.update_us:
        cmd.extend(['--update-us', str(args.update_us)])
    if args.min_regions is not None:
        cmd.extend(['--min-regions', str(args.min_regions)])
    if args.max_regions is not None:
        cmd.extend(['--max-regions', str(args.max_regions)])

    LOG.info("Running: %s", ' '.join(cmd))
    subprocess.run(cmd)


# ── subcommand: damon-stat ──────────────────────────────────────────

def cmd_damon_stat(args):
    """Control damon_stat module."""
    require_root()

    if args.action == 'status':
        status = check_damon_stat()
        if status is None:
            print("damon_stat: not loaded")
        else:
            print(f"damon_stat: {status}")
        return

    if not os.path.isfile(SYSFS_DAMON_STAT_ENABLED):
        die("damon_stat module not loaded (CONFIG_DAMON_STAT not built or module not inserted)")

    if args.action == 'off':
        ok = disable_damon_stat()
        if ok:
            print("damon_stat: disabled")
        else:
            die("Failed to disable damon_stat")
    elif args.action == 'on':
        ok = sysfs_write(SYSFS_DAMON_STAT_ENABLED, 'Y')
        if ok:
            print("damon_stat: enabled")
        else:
            die("Failed to enable damon_stat")


# ── subcommand: auto-reclaim ────────────────────────────────────────

def cmd_auto_reclaim(args):
    """Control DAMON_RECLAIM."""
    require_root()

    params_dir = SYSFS_DAMON_RECLAIM

    if args.action == 'status':
        print("--- DAMON_RECLAIM Status ---")
        if not os.path.isdir(params_dir):
            print("  Module not loaded. Try: modprobe damon_reclaim")
            return
        for param in [
            'enabled', 'min_age', 'quota_ms', 'quota_sz',
            'quota_reset_interval_ms',
            'wmarks_high', 'wmarks_mid', 'wmarks_low',
            'nr_reclaim_tried_regions', 'bytes_reclaim_tried_regions',
            'nr_reclaimed_regions', 'bytes_reclaimed_regions',
            'nr_quota_exceeds', 'kdamond_pid',
        ]:
            val = sysfs_read(os.path.join(params_dir, param))
            # Format size params
            if param in ('quota_sz', 'bytes_reclaim_tried_regions',
                         'bytes_reclaimed_regions'):
                try:
                    val = f"{val} ({human_bytes(int(val))})"
                except (ValueError, TypeError):
                    pass
            elif param == 'min_age':
                try:
                    val = f"{val} ({int(val)/1e6:.0f}s)"
                except (ValueError, TypeError):
                    pass
            print(f"  {param}: {val}")
        return

    # Ensure module loaded
    if not os.path.isdir(params_dir):
        try:
            subprocess.run(['modprobe', 'damon_reclaim'],
                           capture_output=True, timeout=10, check=True)
            time.sleep(0.3)
        except subprocess.CalledProcessError as e:
            die(f"Cannot load damon_reclaim module: {e.stderr}")

    if args.action == 'on':
        min_age_us = int((args.min_age or 120) * 1_000_000)
        quota_sz = args.quota_sz or 128 * 1024 * 1024  # 128 MiB
        quota_ms = args.quota_ms or 10

        print(f"[*] Enabling DAMON_RECLAIM...", file=sys.stderr)
        print(f"    min_age: {min_age_us}µs ({args.min_age or 120}s)", file=sys.stderr)
        print(f"    quota_sz: {human_bytes(quota_sz)}", file=sys.stderr)
        print(f"    quota_ms: {quota_ms}ms", file=sys.stderr)

        sysfs_write(os.path.join(params_dir, 'min_age'), str(min_age_us))
        sysfs_write(os.path.join(params_dir, 'quota_sz'), str(quota_sz))
        sysfs_write(os.path.join(params_dir, 'quota_ms'), str(quota_ms))
        sysfs_write(os.path.join(params_dir, 'quota_reset_interval_ms'), '1000')
        ok = sysfs_write(os.path.join(params_dir, 'enabled'), 'Y')
        if ok:
            print("[*] DAMON_RECLAIM enabled.", file=sys.stderr)
        else:
            die("Failed to enable DAMON_RECLAIM")

    elif args.action == 'off':
        ok = sysfs_write(os.path.join(params_dir, 'enabled'), 'N')
        if ok:
            print("[*] DAMON_RECLAIM disabled.", file=sys.stderr)
        else:
            die("Failed to disable DAMON_RECLAIM")


# ── subcommand: auto-lru-sort ───────────────────────────────────────

def cmd_auto_lru_sort(args):
    """Control DAMON_LRU_SORT."""
    require_root()

    params_dir = SYSFS_DAMON_LRU_SORT

    if args.action == 'status':
        print("--- DAMON_LRU_SORT Status ---")
        if not os.path.isdir(params_dir):
            print("  Module not loaded. Try: modprobe damon_lru_sort")
            return
        for param in [
            'enabled', 'hot_thres_access_freq', 'cold_min_age',
            'quota_ms', 'quota_reset_interval_ms',
            'wmarks_high', 'wmarks_mid', 'wmarks_low',
            'nr_lru_sort_tried_hot_regions', 'bytes_lru_sort_tried_hot_regions',
            'nr_lru_sorted_hot_regions', 'bytes_lru_sorted_hot_regions',
            'nr_lru_sort_tried_cold_regions', 'bytes_lru_sort_tried_cold_regions',
            'nr_lru_sorted_cold_regions', 'bytes_lru_sorted_cold_regions',
            'kdamond_pid',
        ]:
            val = sysfs_read(os.path.join(params_dir, param))
            if param == 'cold_min_age':
                try:
                    val = f"{val} ({int(val)/1e6:.0f}s)"
                except (ValueError, TypeError):
                    pass
            elif param.startswith('bytes_'):
                try:
                    val = f"{val} ({human_bytes(int(val))})"
                except (ValueError, TypeError):
                    pass
            print(f"  {param}: {val}")
        return

    if not os.path.isdir(params_dir):
        try:
            subprocess.run(['modprobe', 'damon_lru_sort'],
                           capture_output=True, timeout=10, check=True)
            time.sleep(0.3)
        except subprocess.CalledProcessError as e:
            die(f"Cannot load damon_lru_sort module: {e.stderr}")

    if args.action == 'on':
        hot_thres = args.hot_thres or 500  # permil (50%)
        cold_age_us = int((args.cold_age or 120) * 1_000_000)

        print(f"[*] Enabling DAMON_LRU_SORT...", file=sys.stderr)
        print(f"    hot_thres_access_freq: {hot_thres}‰", file=sys.stderr)
        print(f"    cold_min_age: {cold_age_us}µs ({args.cold_age or 120}s)", file=sys.stderr)

        sysfs_write(os.path.join(params_dir, 'hot_thres_access_freq'), str(hot_thres))
        sysfs_write(os.path.join(params_dir, 'cold_min_age'), str(cold_age_us))
        sysfs_write(os.path.join(params_dir, 'quota_ms'), '10')
        sysfs_write(os.path.join(params_dir, 'quota_reset_interval_ms'), '1000')
        ok = sysfs_write(os.path.join(params_dir, 'enabled'), 'Y')
        if ok:
            print("[*] DAMON_LRU_SORT enabled.", file=sys.stderr)
        else:
            die("Failed to enable DAMON_LRU_SORT")

    elif args.action == 'off':
        ok = sysfs_write(os.path.join(params_dir, 'enabled'), 'N')
        if ok:
            print("[*] DAMON_LRU_SORT disabled.", file=sys.stderr)
        else:
            die("Failed to disable DAMON_LRU_SORT")


# ── subcommand: monitor-pid ─────────────────────────────────────────

def cmd_monitor_pid(args):
    """Live monitoring dashboard for a PID."""
    require_root()
    check_damon_sysfs()
    disable_damon_stat()

    pid = args.pid
    print(f"[*] Starting live monitoring for PID {pid}...", file=sys.stderr)
    print("[*] Press Ctrl+C to stop.\n", file=sys.stderr)

    # Start damo
    try:
        result = damo('start', '--target_pid', str(pid),
                       '-s', '100ms', '-a', '2s', timeout=15)
        if result.returncode != 0:
            die(f"Failed to start DAMON: {result.stderr}")
    except subprocess.TimeoutExpired:
        die("damo start timed out")

    # Cleanup on exit
    def cleanup():
        try:
            damo('stop', timeout=10)
        except Exception:
            pass
        print("\n[*] Stopped.", file=sys.stderr)

    # Register cleanup for normal exit AND signals
    import atexit
    atexit.register(cleanup)

    def sig_handler(sig, frame):
        sys.exit(0)

    signal.signal(signal.SIGINT, sig_handler)
    signal.signal(signal.SIGTERM, sig_handler)

    time.sleep(2)
    try:
        while True:
            # Clear screen
            print("\033[2J\033[H", end='')
            print(f"=== Live Memory Access Pattern — PID {pid} ===")
            print(f"Time: {time.strftime('%Y-%m-%d %H:%M:%S')}")
            print()

            try:
                result = damo('report', 'access', timeout=10)
                if result.returncode == 0:
                    print(result.stdout)
                else:
                    print("(waiting for data...)")
            except Exception:
                print("(waiting for data...)")

            print()
            print("--- Press Ctrl+C to stop ---")
            time.sleep(5)
    except (KeyboardInterrupt, SystemExit):
        pass


# ── main entry point ────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description='DAMON memory analysis CLI (Python-native)',
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument('--verbose', '-v', action='store_true',
                        help='Enable debug logging')
    parser.add_argument('--debug', action='store_true',
                        help='Show full tracebacks on error')

    sub = parser.add_subparsers(dest='command', title='commands')

    # ── diagnose ──
    p_diag = sub.add_parser('diagnose', help='System DAMON readiness check')

    # ── profile-pid ──
    p_ppid = sub.add_parser('profile-pid', help='Profile a process')
    p_ppid.add_argument('pid', type=int, help='Target PID')
    p_ppid.add_argument('--duration', type=float,
                        help='Monitoring duration in seconds (default: 60)')
    p_ppid.add_argument('--output', choices=['text', 'json', 'csv'],
                        help='Output format')
    p_ppid.add_argument('--output-file', help='Write output to file')
    p_ppid.add_argument('--sample-us', type=int, help='Sampling interval (µs)')
    p_ppid.add_argument('--aggr-us', type=int, help='Aggregation interval (µs)')
    p_ppid.add_argument('--update-us', type=int, help='Update interval (µs)')
    p_ppid.add_argument('--hot-rate', type=float, help='Hot threshold access %%')
    p_ppid.add_argument('--warm-rate', type=float, help='Warm threshold access %%')
    p_ppid.add_argument('--cold-age', type=float, help='Cold threshold age (seconds)')
    p_ppid.add_argument('--idle-age', type=float, help='Idle threshold age (seconds)')
    p_ppid.add_argument('--min-regions', type=int,
                        help='Minimum monitoring regions')
    p_ppid.add_argument('--max-regions', type=int,
                        help='Maximum monitoring regions')

    # ── profile-container ──
    p_pcon = sub.add_parser('profile-container',
                             help='Profile a Docker/Podman container')
    p_pcon.add_argument('container', help='Container name or ID')
    p_pcon.add_argument('--duration', type=float, help='Monitoring duration (seconds)')
    p_pcon.add_argument('--output', choices=['text', 'json', 'csv'])
    p_pcon.add_argument('--output-file')
    p_pcon.add_argument('--mode', choices=['process', 'physical'])
    p_pcon.add_argument('--cgroup-path', help='Explicit cgroup path')

    # ── profile-system ──
    p_psys = sub.add_parser('profile-system',
                             help='System-wide physical memory profile')
    p_psys.add_argument('--duration', type=float,
                        help='Monitoring duration in seconds (default: 60)')
    p_psys.add_argument('--output-file', help='Output file path')

    # ── classify ──
    p_cls = sub.add_parser('classify',
                            help='Classify memory hot/warm/cold')
    p_cls.add_argument('pid', type=int, help='Target PID')
    p_cls.add_argument('--duration', type=float, help='Monitoring duration (seconds)')
    p_cls.add_argument('--output', choices=['text', 'json', 'csv'])
    p_cls.add_argument('--output-file')
    p_cls.add_argument('--hot-rate', type=float)
    p_cls.add_argument('--warm-rate', type=float)
    p_cls.add_argument('--cold-age', type=float)
    p_cls.add_argument('--idle-age', type=float)
    p_cls.add_argument('--sample-us', type=int,
                        help='Sampling interval in µs (default: 100000)')
    p_cls.add_argument('--aggr-us', type=int,
                        help='Aggregation interval in µs (default: 2000000)')
    p_cls.add_argument('--update-us', type=int,
                        help='Update interval in µs (default: kernel default)')
    p_cls.add_argument('--min-regions', type=int,
                        help='Minimum monitoring regions (default: kernel default)')
    p_cls.add_argument('--max-regions', type=int,
                        help='Maximum monitoring regions (default: kernel default)')

    # ── damon-stat ──
    p_ds = sub.add_parser('damon-stat',
                           help='Control damon_stat module (on/off/status)')
    p_ds.add_argument('action', nargs='?', default='status',
                       choices=['on', 'off', 'status'])

    # ── auto-reclaim ──
    p_ar = sub.add_parser('auto-reclaim',
                           help='Control DAMON_RECLAIM')
    p_ar.add_argument('action', nargs='?', default='status',
                       choices=['on', 'off', 'status'])
    p_ar.add_argument('--min-age', type=int, help='Cold threshold (seconds)')
    p_ar.add_argument('--quota-sz', type=parse_size,
                       help='Size quota (e.g., 512M, 1G)')
    p_ar.add_argument('--quota-ms', type=int, help='Time quota (ms)')

    # ── auto-lru-sort ──
    p_al = sub.add_parser('auto-lru-sort',
                           help='Control DAMON_LRU_SORT')
    p_al.add_argument('action', nargs='?', default='status',
                       choices=['on', 'off', 'status'])
    p_al.add_argument('--hot-thres', type=int,
                       help='Hot threshold in permil (e.g., 500 = 50%%)')
    p_al.add_argument('--cold-age', type=int,
                       help='Cold threshold (seconds)')

    # ── monitor-pid ──
    p_mp = sub.add_parser('monitor-pid',
                           help='Live monitoring dashboard')
    p_mp.add_argument('pid', type=int, help='Target PID')

    # ── requirements ──
    p_req = sub.add_parser('requirements',
                            help='Check installation prerequisites')

    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()

    # Setup logging
    if args.verbose or args.debug:
        logging.basicConfig(
            level=logging.DEBUG,
            format='%(asctime)s %(levelname)-8s %(name)s %(message)s',
            datefmt='%H:%M:%S',
        )

    if not args.command:
        parser.print_help()
        sys.exit(1)

    try:
        dispatch = {
            'diagnose': cmd_diagnose,
            'requirements': cmd_requirements,
            'profile-pid': cmd_profile_pid,
            'profile-container': cmd_profile_container,
            'profile-system': cmd_profile_system,
            'classify': cmd_classify,
            'damon-stat': cmd_damon_stat,
            'auto-reclaim': cmd_auto_reclaim,
            'auto-lru-sort': cmd_auto_lru_sort,
            'monitor-pid': cmd_monitor_pid,
        }
        func = dispatch[args.command]
        func(args)
    except Exception as e:
        if args.debug:
            raise
        die(str(e))


if __name__ == '__main__':
    main()
