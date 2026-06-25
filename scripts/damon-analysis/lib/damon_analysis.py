#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0
"""
DAMON Analysis Library — Hot/Warm/Cold Memory Classification

Provides:
- SysfsInterface  — direct /sys/kernel/mm/damon/admin control
- Classifier      — region classification (hot/warm/cold/idle)
- Monitor         — high-level monitoring session manager
- ReportFormatter — output formatting (text, JSON, CSV)

Requires: Linux 5.18+ with CONFIG_DAMON_SYSFS=y, root privileges.
"""

import os
import sys
import time
import json
import subprocess
from collections import OrderedDict
from typing import Optional, Dict, List, Tuple

SYSFS_ADMIN = '/sys/kernel/mm/damon/admin'
KDAMONDS_DIR = os.path.join(SYSFS_ADMIN, 'kdamonds')

# ────────────────────────────────────────────────────────────────────
# SysfsInterface — direct kernel communication
# ────────────────────────────────────────────────────────────────────

class SysfsInterface:
    """Low-level read/write to DAMON sysfs files."""

    @staticmethod
    def _write(path: str, value: str) -> None:
        with open(path, 'w') as f:
            f.write(str(value))

    @staticmethod
    def _read(path: str) -> str:
        with open(path, 'r') as f:
            return f.read().strip()

    @staticmethod
    def _write_int(path: str, value: int) -> None:
        SysfsInterface._write(path, str(value))

    @staticmethod
    def _read_int(path: str) -> int:
        return int(SysfsInterface._read(path))

    @staticmethod
    def is_available() -> bool:
        return os.path.isdir(KDAMONDS_DIR)

    @staticmethod
    def damon_version() -> str:
        """Infer DAMON version from available features."""
        try:
            result = subprocess.run(
                ['damo', 'report', 'sysinfo'],
                capture_output=True, text=True, timeout=10)
            for line in result.stdout.splitlines():
                if 'DAMON version:' in line:
                    return line.split(':')[1].strip()
        except Exception:
            return 'unknown'
        return 'unknown'

    # ── kdamond management ──

    @staticmethod
    def create_kdamond(idx: int = 0) -> str:
        """Ensure kdamond 'idx' exists. Returns path to its dir."""
        nr_path = os.path.join(KDAMONDS_DIR, 'nr_kdamonds')
        current = SysfsInterface._read_int(nr_path)
        if idx + 1 > current:
            SysfsInterface._write_int(nr_path, idx + 1)
        return os.path.join(KDAMONDS_DIR, str(idx))

    @staticmethod
    def kdamond_state(idx: int) -> str:
        """Read state: 'on' or 'off'."""
        return SysfsInterface._read(
            os.path.join(KDAMONDS_DIR, str(idx), 'state'))

    @staticmethod
    def kdamond_on(idx: int = 0) -> None:
        SysfsInterface._write(
            os.path.join(KDAMONDS_DIR, str(idx), 'state'), 'on')

    @staticmethod
    def kdamond_off(idx: int = 0) -> None:
        SysfsInterface._write(
            os.path.join(KDAMONDS_DIR, str(idx), 'state'), 'off')

    @staticmethod
    def kdamond_commit(idx: int = 0) -> None:
        SysfsInterface._write(
            os.path.join(KDAMONDS_DIR, str(idx), 'state'), 'commit')

    @staticmethod
    def kdamond_update_tried_regions(idx: int = 0) -> None:
        SysfsInterface._write(
            os.path.join(KDAMONDS_DIR, str(idx), 'state'),
            'update_schemes_tried_regions')

    @staticmethod
    def kdamond_update_stats(idx: int = 0) -> None:
        SysfsInterface._write(
            os.path.join(KDAMONDS_DIR, str(idx), 'state'),
            'update_schemes_stats')

    @staticmethod
    def kdamond_clear_tried_regions(idx: int = 0) -> None:
        SysfsInterface._write(
            os.path.join(KDAMONDS_DIR, str(idx), 'state'),
            'clear_schemes_tried_regions')

    @staticmethod
    def disable_damon_stat() -> bool:
        """Disable damon_stat to allow manual DAMON use. Returns True if ok."""
        path = '/sys/module/damon_stat/parameters/enabled'
        if not os.path.isfile(path):
            return True
        try:
            if SysfsInterface._read(path) != 'Y':
                return True
            SysfsInterface._write(path, 'N')
            time.sleep(0.3)
            return True
        except OSError:
            return False

    @staticmethod
    def kdamond_pid(idx: int = 0) -> int:
        return SysfsInterface._read_int(
            os.path.join(KDAMONDS_DIR, str(idx), 'pid'))

    # ── context management ──

    @staticmethod
    def _context_dir(kdamond_idx: int, ctx_idx: int) -> str:
        return os.path.join(KDAMONDS_DIR, str(kdamond_idx),
                            'contexts', str(ctx_idx))

    @staticmethod
    def create_context(kdamond_idx: int = 0, ctx_idx: int = 0) -> str:
        nr_path = os.path.join(KDAMONDS_DIR, str(kdamond_idx),
                               'contexts', 'nr_contexts')
        current = SysfsInterface._read_int(nr_path)
        if ctx_idx + 1 > current:
            SysfsInterface._write_int(nr_path, ctx_idx + 1)
        return SysfsInterface._context_dir(kdamond_idx, ctx_idx)

    @staticmethod
    def set_operations(kdamond_idx: int, ctx_idx: int, ops: str) -> None:
        SysfsInterface._write(
            os.path.join(SysfsInterface._context_dir(kdamond_idx, ctx_idx),
                         'operations'), ops)

    @staticmethod
    def set_intervals(kdamond_idx: int, ctx_idx: int,
                      sample_us: int, aggr_us: int, update_us: int) -> None:
        base = os.path.join(SysfsInterface._context_dir(kdamond_idx, ctx_idx),
                            'monitoring_attrs', 'intervals')
        SysfsInterface._write_int(os.path.join(base, 'sample_us'), sample_us)
        SysfsInterface._write_int(os.path.join(base, 'aggr_us'), aggr_us)
        SysfsInterface._write_int(os.path.join(base, 'update_us'), update_us)

    @staticmethod
    def set_nr_regions(kdamond_idx: int, ctx_idx: int,
                       min_regions: int, max_regions: int) -> None:
        base = os.path.join(SysfsInterface._context_dir(kdamond_idx, ctx_idx),
                            'monitoring_attrs', 'nr_regions')
        SysfsInterface._write_int(os.path.join(base, 'min'), min_regions)
        SysfsInterface._write_int(os.path.join(base, 'max'), max_regions)

    # ── target management ──

    @staticmethod
    def _target_dir(kdamond_idx: int, ctx_idx: int,
                    target_idx: int) -> str:
        return os.path.join(SysfsInterface._context_dir(kdamond_idx, ctx_idx),
                            'targets', str(target_idx))

    @staticmethod
    def create_target(kdamond_idx: int, ctx_idx: int,
                      target_idx: int = 0) -> str:
        nr_path = os.path.join(
            SysfsInterface._context_dir(kdamond_idx, ctx_idx),
            'targets', 'nr_targets')
        current = SysfsInterface._read_int(nr_path)
        if target_idx + 1 > current:
            SysfsInterface._write_int(nr_path, target_idx + 1)
        return SysfsInterface._target_dir(kdamond_idx, ctx_idx, target_idx)

    @staticmethod
    def set_pid_target(kdamond_idx: int, ctx_idx: int,
                       target_idx: int, pid: int) -> None:
        SysfsInterface._write_int(
            os.path.join(SysfsInterface._target_dir(
                kdamond_idx, ctx_idx, target_idx), 'pid_target'), pid)

    @staticmethod
    def set_phys_regions(kdamond_idx: int, ctx_idx: int,
                         target_idx: int,
                         regions: List[Tuple[int, int]]) -> None:
        """regions: list of (start, end) pairs."""
        base = os.path.join(SysfsInterface._target_dir(
            kdamond_idx, ctx_idx, target_idx), 'regions')
        SysfsInterface._write_int(os.path.join(base, 'nr_regions'),
                                  len(regions))
        for i, (start, end) in enumerate(regions):
            rdir = os.path.join(base, str(i))
            SysfsInterface._write_int(os.path.join(rdir, 'start'), start)
            SysfsInterface._write_int(os.path.join(rdir, 'end'), end)

    # ── scheme management (for stat/query action) ──

    @staticmethod
    def _scheme_dir(kdamond_idx: int, ctx_idx: int,
                    scheme_idx: int) -> str:
        return os.path.join(SysfsInterface._context_dir(kdamond_idx, ctx_idx),
                            'schemes', str(scheme_idx))

    @staticmethod
    def create_scheme(kdamond_idx: int, ctx_idx: int,
                      scheme_idx: int = 0) -> str:
        nr_path = os.path.join(
            SysfsInterface._context_dir(kdamond_idx, ctx_idx),
            'schemes', 'nr_schemes')
        current = SysfsInterface._read_int(nr_path)
        if scheme_idx + 1 > current:
            SysfsInterface._write_int(nr_path, scheme_idx + 1)
        return SysfsInterface._scheme_dir(kdamond_idx, ctx_idx, scheme_idx)

    @staticmethod
    def set_scheme_action(kdamond_idx: int, ctx_idx: int,
                          scheme_idx: int, action: str) -> None:
        SysfsInterface._write(
            os.path.join(SysfsInterface._scheme_dir(
                kdamond_idx, ctx_idx, scheme_idx), 'action'), action)

    @staticmethod
    def set_scheme_access_pattern(kdamond_idx: int, ctx_idx: int,
                                  scheme_idx: int,
                                  sz_min: int, sz_max: int,
                                  nr_acc_min: int, nr_acc_max: int,
                                  age_min: int, age_max: int) -> None:
        ap = os.path.join(SysfsInterface._scheme_dir(
            kdamond_idx, ctx_idx, scheme_idx), 'access_pattern')
        SysfsInterface._write_int(os.path.join(ap, 'sz', 'min'), sz_min)
        SysfsInterface._write_int(os.path.join(ap, 'sz', 'max'), sz_max)
        SysfsInterface._write_int(
            os.path.join(ap, 'nr_accesses', 'min'), nr_acc_min)
        SysfsInterface._write_int(
            os.path.join(ap, 'nr_accesses', 'max'), nr_acc_max)
        SysfsInterface._write_int(os.path.join(ap, 'age', 'min'), age_min)
        SysfsInterface._write_int(os.path.join(ap, 'age', 'max'), age_max)

    @staticmethod
    def read_tried_regions(kdamond_idx: int = 0, ctx_idx: int = 0,
                           scheme_idx: int = 0) -> List[Dict]:
        """Read tried_regions for a scheme. Returns list of region dicts."""
        base = os.path.join(
            SysfsInterface._scheme_dir(kdamond_idx, ctx_idx, scheme_idx),
            'tried_regions')
        regions = []
        if not os.path.isdir(base):
            return regions
        # Iterate over all subdirectories (kernel uses non-sequential names)
        for entry in sorted(os.listdir(base),
                            key=lambda x: (0, int(x)) if x.isdigit()
                            else (1, x)):
            rdir = os.path.join(base, entry)
            if not os.path.isdir(rdir):
                continue
            try:
                region = {
                    'start': SysfsInterface._read_int(
                        os.path.join(rdir, 'start')),
                    'end': SysfsInterface._read_int(
                        os.path.join(rdir, 'end')),
                    'nr_accesses': SysfsInterface._read_int(
                        os.path.join(rdir, 'nr_accesses')),
                    'age': SysfsInterface._read_int(
                        os.path.join(rdir, 'age')),
                }
                regions.append(region)
            except (ValueError, OSError):
                pass
        return regions

    @staticmethod
    def read_total_tried_bytes(kdamond_idx: int = 0, ctx_idx: int = 0,
                               scheme_idx: int = 0) -> int:
        path = os.path.join(
            SysfsInterface._scheme_dir(kdamond_idx, ctx_idx, scheme_idx),
            'tried_regions', 'total_bytes')
        return SysfsInterface._read_int(path)

    @staticmethod
    def read_stats(kdamond_idx: int = 0, ctx_idx: int = 0,
                   scheme_idx: int = 0) -> Dict:
        base = os.path.join(
            SysfsInterface._scheme_dir(kdamond_idx, ctx_idx, scheme_idx),
            'stats')
        stats = {}
        for name in ['nr_tried', 'sz_tried', 'nr_applied', 'sz_applied',
                      'sz_ops_filter_passed', 'qt_exceeds']:
            try:
                stats[name] = SysfsInterface._read_int(
                    os.path.join(base, name))
            except (ValueError, OSError):
                stats[name] = -1
        return stats


# ────────────────────────────────────────────────────────────────────
# Classifier — hot/warm/cold/idle classification
# ────────────────────────────────────────────────────────────────────

class Classifier:
    """Classify DAMON regions by access pattern."""

    def __init__(self,
                 hot_access_rate_pct: float = 50.0,
                 warm_access_rate_pct: float = 5.0,
                 cold_age_sec: float = 30.0,
                 idle_age_sec: float = 120.0):
        """
        Args:
            hot_access_rate_pct:  min access rate % for 'hot'  (default 50%)
            warm_access_rate_pct: min access rate % for 'warm' (default 5%)
            cold_age_sec:         min age for 'cold' (rate < warm threshold)
            idle_age_sec:         min age for 'idle' (rate = 0%)
        """
        self.hot_rate = hot_access_rate_pct
        self.warm_rate = warm_access_rate_pct
        self.cold_age_us = int(cold_age_sec * 1_000_000)
        self.idle_age_us = int(idle_age_sec * 1_000_000)

    @staticmethod
    def access_rate_pct(nr_accesses: int, max_nr_accesses: int) -> float:
        """Calculate access rate as percentage."""
        if max_nr_accesses == 0:
            return 0.0
        return (nr_accesses / max_nr_accesses) * 100.0

    def classify(self, nr_accesses: int, age_us: int,
                 max_nr_accesses: int) -> str:
        """
        Classify a region as 'hot', 'warm', 'cold', or 'idle'.

        Returns one of: 'hot', 'warm', 'cold', 'idle'
        """
        rate = self.access_rate_pct(nr_accesses, max_nr_accesses)

        if rate >= self.hot_rate:
            return 'hot'
        elif rate >= self.warm_rate:
            return 'warm'
        elif rate == 0.0 and age_us >= self.idle_age_us:
            return 'idle'
        elif age_us >= self.cold_age_us:
            return 'cold'
        else:
            # Low access rate but not yet aged enough
            return 'warm'  # transitional, still "alive"

    def temperature(self, nr_accesses: int, age_us: int,
                    max_nr_accesses: int) -> float:
        """
        Calculate access temperature (like damo's metric).

        If access rate == 0, temperature = -age_us (more negative = colder).
        Otherwise, temperature = access_rate_pct * age_us.
        """
        rate = self.access_rate_pct(nr_accesses, max_nr_accesses)
        if rate == 0.0:
            return -float(age_us)
        return rate * float(age_us)

    def classify_regions(self, regions: List[Dict],
                         sample_us: int, aggr_us: int) -> List[Dict]:
        """Classify a list of region dicts, adding 'class' and 'temperature'.

        Note: The 'age' field from tried_regions is in **aggregation intervals**,
        not microseconds. We convert age_aggr_intervals * aggr_us → age_us.
        """
        max_nr = aggr_us // sample_us if sample_us > 0 else 0
        result = []
        for r in regions:
            r = dict(r)  # copy
            # Convert age from aggregation intervals to microseconds
            age_us = r['age'] * aggr_us
            r['size_bytes'] = r['end'] - r['start']
            r['access_rate_pct'] = self.access_rate_pct(
                r['nr_accesses'], max_nr)
            r['temperature'] = self.temperature(
                r['nr_accesses'], age_us, max_nr)
            r['class'] = self.classify(
                r['nr_accesses'], age_us, max_nr)
            r['age_us'] = age_us
            r['age_sec'] = age_us / 1_000_000.0
            result.append(r)
        return result

    def summary(self, classified: List[Dict]) -> Dict:
        """Generate a summary breakdown by class."""
        summary = {'hot': {'count': 0, 'bytes': 0},
                   'warm': {'count': 0, 'bytes': 0},
                   'cold': {'count': 0, 'bytes': 0},
                   'idle': {'count': 0, 'bytes': 0}}
        for r in classified:
            cls = r['class']
            summary[cls]['count'] += 1
            summary[cls]['bytes'] += r['size_bytes']
        return summary


# ────────────────────────────────────────────────────────────────────
# Monitor — high-level monitoring session
# ────────────────────────────────────────────────────────────────────

class Monitor:
    """High-level DAMON monitoring session using damo CLI as backend.

    Uses the damo command-line tool for configure/start/stop operations
    (since damo handles all the sysfs edge cases), and direct sysfs
    reads for collecting results.

    Supports parallel kdamonds by setting ``kdamond_idx`` to different
    values for each Monitor instance.  Indices 0–N are valid after
    writing N+1 to ``nr_kdamonds``.  Each kdamond runs in its own
    kernel thread and can monitor a different target simultaneously.

    Usage for parallel analysis::

        m1 = Monitor(kdamond_idx=0)          # kdamond 0
        m2 = Monitor(kdamond_idx=1)          # kdamond 1  (must create first!)
        m1.configure_vaddr(pid=1234)
        m2.configure_paddr()

        # Create both kdamonds before starting either:
        m1.sysfs.create_kdamond(0)
        m1.sysfs.create_kdamond(1)

        m1.start()  # starts kdamond 0
        m2.start()  # starts kdamond 1  — both run in parallel
        # ... collect from both ...
        m1.stop()
        m2.stop()
    """

    def __init__(self, ops: str = 'vaddr', pid: Optional[int] = None,
                 damo_bin: str = 'damo', kdamond_idx: int = 0):
        """
        Args:
            ops: 'vaddr', 'fvaddr', or 'paddr'
            pid: target process PID (for vaddr/fvaddr)
            damo_bin: path to damo executable
            kdamond_idx: which kdamond to use (0 = default, 1+ for parallel)
        """
        self.sysfs = SysfsInterface()
        self.ops = ops
        self.pid = pid
        self.damo_bin = damo_bin
        self._kdidx = kdamond_idx
        self._ctxidx = 0
        self._scheme_idx = 0
        self._target_idx = 0
        self.sample_us = 100_000
        self.aggr_us = 2_000_000
        self._min_regions: Optional[int] = None
        self._max_regions: Optional[int] = None
        self._update_us: Optional[int] = None
        self._running = False

    def _damo(self, *args, **kwargs) -> subprocess.CompletedProcess:
        """Run a damo command."""
        cmd = [self.damo_bin] + list(args)
        timeout = kwargs.pop('timeout', 30)
        return subprocess.run(cmd, capture_output=True, text=True,
                              timeout=timeout, **kwargs)

    def configure_vaddr(self, pid: int,
                        sample_us: int = 100_000,
                        aggr_us: int = 2_000_000,
                        update_us: Optional[int] = None,
                        min_regions: Optional[int] = None,
                        max_regions: Optional[int] = None) -> None:
        """Configure for virtual address space monitoring of a PID.

        Args:
            pid: target process PID
            sample_us: sampling interval in microseconds (default 100ms)
            aggr_us: aggregation interval in microseconds (default 2s)
            update_us: update interval in microseconds (default: keep kernel default)
            min_regions: minimum number of monitoring regions (default: keep kernel default)
            max_regions: maximum number of monitoring regions (default: keep kernel default)
        """
        self.ops = 'vaddr'
        self.pid = pid
        self.sample_us = sample_us
        self.aggr_us = aggr_us
        self._min_regions = min_regions
        self._max_regions = max_regions
        self._update_us = update_us

    def configure_paddr(self,
                        sample_us: int = 100_000,
                        aggr_us: int = 2_000_000,
                        update_us: Optional[int] = None,
                        min_regions: Optional[int] = None,
                        max_regions: Optional[int] = None) -> None:
        """Configure for physical address space monitoring.

        Args:
            sample_us: sampling interval in microseconds (default 100ms)
            aggr_us: aggregation interval in microseconds (default 2s)
            update_us: update interval in microseconds (default: keep kernel default)
            min_regions: minimum number of monitoring regions (default: keep kernel default)
            max_regions: maximum number of monitoring regions (default: keep kernel default)
        """
        self.ops = 'paddr'
        self.pid = None
        self.sample_us = sample_us
        self.aggr_us = aggr_us
        self._min_regions = min_regions
        self._max_regions = max_regions
        self._update_us = update_us

    def start(self) -> None:
        """Start DAMON via damo CLI."""
        # Disable damon_stat — it blocks user-space kdamond creation
        self.sysfs.disable_damon_stat()

        # Stop any existing DAMON first
        self._damo('stop', timeout=5)

        args = ['start']

        # Target: positional PID or 'paddr'
        if self.ops == 'paddr':
            args.append('paddr')
        elif self.pid is not None:
            args.append(str(self.pid))

        # Intervals (microseconds)
        update_us = self._update_us if self._update_us is not None else self.aggr_us * 20
        args.extend(['--monitoring_intervals',
                     str(self.sample_us), str(self.aggr_us), str(update_us)])

        # Region count
        min_r = self._min_regions if self._min_regions is not None else 10
        max_r = self._max_regions if self._max_regions is not None else 1000
        args.extend(['--monitoring_nr_regions_range', str(min_r), str(max_r)])

        # Stat scheme — match all regions, keep tried_regions snapshots
        args.extend(['--damos_action', 'stat'])
        args.extend(['--damos_access_rate', '0%', 'max'])
        args.extend(['--damos_sz_region', '0', 'max'])
        args.extend(['--damos_age', '0', 'max'])
        args.extend(['--damos_max_nr_snapshots', '10000'])

        result = self._damo(*args, timeout=30)
        if result.returncode != 0:
            raise RuntimeError(f"damo start failed: {result.stderr.strip()}")

        self._running = True
        time.sleep(0.5)

    def stop(self) -> None:
        """Stop DAMON and re-enable damon_stat."""
        self._damo('stop', timeout=10)
        self._running = False
        # Restore damon_stat (we disabled it in start())
        try:
            path = '/sys/module/damon_stat/parameters/enabled'
            if os.path.isfile(path):
                SysfsInterface._write(path, 'Y')
        except OSError:
            pass

    def is_running(self) -> bool:
        """Check if kdamond is running."""
        try:
            return self.sysfs.kdamond_state(self._kdidx) == 'on'
        except Exception:
            return False

    def collect(self) -> List[Dict]:
        """Trigger update_schemes_tried_regions and return region data."""
        # Ensure per-region snapshots enabled (belt-and-suspenders for kernels
        # where --damos_max_nr_snapshots wasn't honoured at start time)
        snap_path = os.path.join(
            SysfsInterface._scheme_dir(self._kdidx, self._ctxidx, self._scheme_idx),
            'stats', 'max_nr_snapshots')
        try:
            SysfsInterface._write(snap_path, '10000')
        except OSError:
            pass
        self.sysfs.kdamond_update_tried_regions(self._kdidx)
        time.sleep(0.1)
        return self.sysfs.read_tried_regions(
            self._kdidx, self._ctxidx, self._scheme_idx)

    @property
    def max_nr_accesses(self) -> int:
        """Theoretical maximum nr_accesses for current intervals."""
        if self.sample_us == 0:
            return 0
        return self.aggr_us // self.sample_us


# ────────────────────────────────────────────────────────────────────
# ReportFormatter — output generation
# ────────────────────────────────────────────────────────────────────

class ReportFormatter:
    """Format classified regions into various output formats."""

    @staticmethod
    def human_readable(classified: List[Dict], title: str = "Memory Analysis") -> str:
        """Generate a human-readable text report."""
        lines = []
        lines.append("=" * 72)
        lines.append(f"  {title}")
        lines.append("=" * 72)
        lines.append("")

        # Summary table
        classifier = Classifier()
        summary = classifier.summary(classified)
        total_bytes = sum(s['bytes'] for s in summary.values())

        lines.append(f"{'Class':<8} {'Regions':>8} {'Size':>14} {'% of Total':>12}")
        lines.append("-" * 44)
        for cls in ['hot', 'warm', 'cold', 'idle']:
            s = summary[cls]
            pct = (s['bytes'] / total_bytes * 100) if total_bytes > 0 else 0
            size_str = ReportFormatter._fmt_bytes(s['bytes'])
            lines.append(f"{cls.upper():<8} {s['count']:>8} {size_str:>14} {pct:>11.1f}%")
        lines.append("-" * 44)
        lines.append(f"{'TOTAL':<8} {len(classified):>8} "
                     f"{ReportFormatter._fmt_bytes(total_bytes):>14}")

        lines.append("")
        lines.append("─" * 72)
        lines.append("Region Details (sorted by temperature, coldest first):")
        lines.append("─" * 72)
        lines.append(f"{'Addr Start':>20} {'Size':>12} {'Rate%':>7} "
                     f"{'Age':>10} {'Temp':>14} {'Class':>8}")
        lines.append("-" * 78)

        # Sort by temperature (coldest first)
        sorted_regions = sorted(classified, key=lambda r: r['temperature'])

        for r in sorted_regions:
            age_str = ReportFormatter._fmt_age(r.get('age_us', r['age']))
            temp_str = ReportFormatter._fmt_temp(r['temperature'])
            lines.append(
                f"0x{r['start']:016x} "
                f"{ReportFormatter._fmt_bytes(r['size_bytes']):>12} "
                f"{r['access_rate_pct']:>6.1f}% "
                f"{age_str:>10} "
                f"{temp_str:>14} "
                f"{r['class'].upper():>8}")

        return '\n'.join(lines)

    @staticmethod
    def json_report(classified: List[Dict], metadata: Dict = None) -> str:
        """Generate JSON output."""
        classifier = Classifier()
        summary = classifier.summary(classified)
        total_bytes = sum(s['bytes'] for s in summary.values())

        output = OrderedDict()
        if metadata:
            output['metadata'] = metadata
        output['summary'] = {
            cls: {
                'count': summary[cls]['count'],
                'bytes': summary[cls]['bytes'],
                'percent': round(summary[cls]['bytes'] / total_bytes * 100, 1)
                if total_bytes > 0 else 0.0
            }
            for cls in ['hot', 'warm', 'cold', 'idle']
        }
        output['total_bytes'] = total_bytes
        output['regions'] = []
        for r in sorted(classified, key=lambda x: x['temperature']):
            output['regions'].append(OrderedDict([
                ('start', r['start']),
                ('end', r['end']),
                ('size_bytes', r['size_bytes']),
                ('access_rate_pct', round(r['access_rate_pct'], 2)),
                ('age_us', r.get('age_us', r['age'])),
                ('age_sec', round(r['age_sec'], 2)),
                ('temperature', round(r['temperature'], 2)),
                ('class', r['class']),
            ]))
        return json.dumps(output, indent=2)

    @staticmethod
    def csv_report(classified: List[Dict]) -> str:
        """Generate CSV output."""
        lines = ['start,end,size_bytes,access_rate_pct,age_us,age_sec,temperature,class']
        for r in sorted(classified, key=lambda x: x['temperature']):
            lines.append(
                f"{r['start']},{r['end']},{r['size_bytes']},"
                f"{r['access_rate_pct']:.2f},{r.get('age_us', r['age'])},"
                f"{r['age_sec']:.2f},{r['temperature']:.2f},{r['class']}")
        return '\n'.join(lines)

    @staticmethod
    def _fmt_bytes(b: int) -> str:
        """Human-readable byte size."""
        for unit in ['B', 'KiB', 'MiB', 'GiB', 'TiB']:
            if abs(b) < 1024.0:
                return f"{b:.1f} {unit}" if unit != 'B' else f"{b} B"
            b /= 1024.0
        return f"{b:.1f} PiB"

    @staticmethod
    def _fmt_age(age_us: int) -> str:
        """Human-readable age."""
        if age_us < 1_000:
            return f"{age_us} µs"
        elif age_us < 1_000_000:
            return f"{age_us/1000:.1f} ms"
        elif age_us < 60_000_000:
            return f"{age_us/1_000_000:.1f} s"
        elif age_us < 3_600_000_000:
            mins = int(age_us / 60_000_000)
            secs = int((age_us % 60_000_000) / 1_000_000)
            return f"{mins}m {secs}s"
        else:
            hours = int(age_us / 3_600_000_000)
            mins = int((age_us % 3_600_000_000) / 60_000_000)
            return f"{hours}h {mins}m"

    @staticmethod
    def _fmt_temp(temp: float) -> str:
        """Human-readable temperature."""
        if abs(temp) >= 1e12:
            return f"{temp/1e12:.1f} T"
        elif abs(temp) >= 1e9:
            return f"{temp/1e9:.1f} G"
        elif abs(temp) >= 1e6:
            return f"{temp/1e6:.1f} M"
        elif abs(temp) >= 1e3:
            return f"{temp/1e3:.1f} K"
        else:
            return f"{temp:.0f}"


# ────────────────────────────────────────────────────────────────────
# Utility helpers
# ────────────────────────────────────────────────────────────────────

def get_process_info(pid: int) -> Dict:
    """Get basic process information from /proc."""
    info = {'pid': pid, 'comm': '', 'vm_size_kb': 0, 'vm_rss_kb': 0}
    try:
        with open(f'/proc/{pid}/comm', 'r') as f:
            info['comm'] = f.read().strip()
    except (OSError, IOError):
        pass
    try:
        with open(f'/proc/{pid}/status', 'r') as f:
            for line in f:
                if line.startswith('VmSize:'):
                    info['vm_size_kb'] = int(line.split()[1])
                elif line.startswith('VmRSS:'):
                    info['vm_rss_kb'] = int(line.split()[1])
    except (OSError, IOError):
        pass
    return info


def get_container_pids(container_name: str) -> List[int]:
    """Get all PIDs in a Docker/Podman container."""
    pids = []
    try:
        # Try Docker
        result = subprocess.run(
            ['docker', 'inspect', '--format', '{{.State.Pid}}',
             container_name],
            capture_output=True, text=True, timeout=10)
        if result.returncode == 0:
            container_pid = int(result.stdout.strip())
            pids = _get_child_pids(container_pid)
            if pids:
                return pids
    except Exception:
        pass

    try:
        # Try Podman
        result = subprocess.run(
            ['podman', 'inspect', '--format', '{{.State.Pid}}',
             container_name],
            capture_output=True, text=True, timeout=10)
        if result.returncode == 0:
            container_pid = int(result.stdout.strip())
            pids = _get_child_pids(container_pid)
            if pids:
                return pids
    except Exception:
        pass

    return pids


def _get_child_pids(parent_pid: int) -> List[int]:
    """Get all child PIDs recursively."""
    pids = [parent_pid]
    try:
        for entry in os.listdir('/proc'):
            if not entry.isdigit():
                continue
            try:
                with open(f'/proc/{entry}/stat', 'r') as f:
                    ppid = int(f.read().split()[3])
                if ppid == parent_pid:
                    pid = int(entry)
                    pids.extend(_get_child_pids(pid))
            except (OSError, IOError, ValueError, IndexError):
                continue
    except OSError:
        pass
    return pids
