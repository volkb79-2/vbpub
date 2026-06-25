#!/usr/bin/env python3
"""Batch-analyze multiple PIDs and print a summary report.

Usage:
    sudo python3 batch_report.py <PID> [PID ...] [--duration SEC]
    sudo python3 batch_report.py 12345 67890 --duration 20
"""
import argparse
import os
import sys
import time
import subprocess

sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'lib'))
from damon_analysis import SysfsInterface, Classifier, ReportFormatter, get_process_info

DAMO = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'venv', 'bin', 'damo')
if not os.path.isfile(DAMO):
    DAMO = 'damo'

SAMPLE_US = 100_000
AGGR_US   = 2_000_000
HOT_RATE  = 50.0
WARM_RATE = 5.0
COLD_AGE  = 30.0
IDLE_AGE  = 120.0


def fmt_bytes(b):
    for unit in ['B', 'KiB', 'MiB', 'GiB', 'TiB']:
        if abs(b) < 1024:
            return f'{b:.1f} {unit}' if unit != 'B' else f'{int(b)} B'
        b /= 1024


def analyze_one(pid: int, duration_s: int = 15):
    """Run damo, collect, classify, return dict."""
    SysfsInterface.disable_damon_stat()
    subprocess.run([DAMO, 'stop'], capture_output=True, timeout=5)
    time.sleep(0.3)

    update_us = AGGR_US * 20
    cmd = [DAMO, 'start', str(pid),
           '--monitoring_intervals', str(SAMPLE_US), str(AGGR_US), str(update_us),
           '--monitoring_nr_regions_range', '10', '1000',
           '--damos_action', 'stat',
           '--damos_access_rate', '0%', 'max',
           '--damos_sz_region', '0', 'max',
           '--damos_age', '0', 'max',
           '--damos_max_nr_snapshots', '10000']
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if r.returncode != 0:
        return {'error': f'damo start failed: {r.stderr.strip()}'}

    time.sleep(duration_s)

    s = SysfsInterface()
    s.kdamond_update_tried_regions(0)
    time.sleep(0.2)
    regions = s.read_tried_regions(0, 0, 0)

    subprocess.run([DAMO, 'stop'], capture_output=True, timeout=5)
    # Restore damon_stat
    try:
        if os.path.isfile('/sys/module/damon_stat/parameters/enabled'):
            SysfsInterface._write('/sys/module/damon_stat/parameters/enabled', 'Y')
    except OSError:
        pass

    c = Classifier(hot_access_rate_pct=HOT_RATE, warm_access_rate_pct=WARM_RATE,
                   cold_age_sec=COLD_AGE, idle_age_sec=IDLE_AGE)
    classified = c.classify_regions(regions, SAMPLE_US, AGGR_US)
    summary = c.summary(classified)
    info = get_process_info(pid)

    return {
        'pid': pid,
        'comm': info.get('comm', '?'),
        'vm_rss_kb': info.get('vm_rss_kb', 0),
        'vm_size_kb': info.get('vm_size_kb', 0),
        'duration_s': duration_s,
        'regions_raw': len(regions),
        'summary': summary,
        'classified': classified,
    }


def main():
    p = argparse.ArgumentParser(description='Batch DAMON memory analysis for multiple PIDs')
    p.add_argument('pids', type=int, nargs='+', metavar='PID', help='PIDs to analyze')
    p.add_argument('--duration', type=int, default=15,
                   help='Monitoring duration per PID in seconds (default: 15)')
    args = p.parse_args()

    if os.geteuid() != 0:
        print('ERROR: Root privileges required.', file=sys.stderr)
        sys.exit(1)

    if not SysfsInterface.is_available():
        print('ERROR: DAMON sysfs not available.', file=sys.stderr)
        sys.exit(1)

    print('=' * 72)
    print('  DAMON Memory Analysis — Batch PID Report')
    print(f'  Kernel: {os.uname().release}')
    print(f'  Intervals: {SAMPLE_US//1000}ms / {AGGR_US//1000}ms  '
          f'Duration: {args.duration}s/PID')
    print(f'  Thresholds: hot ≥{HOT_RATE}%, warm ≥{WARM_RATE}%, '
          f'cold age ≥{COLD_AGE}s, idle age ≥{IDLE_AGE}s')
    print('=' * 72)
    print()

    results = []
    for pid in args.pids:
        info = get_process_info(pid)
        if not info.get('comm'):
            print(f'  PID {pid}: not found, skipping')
            continue

        print(f'  Analyzing PID {pid} ({info["comm"]}, '
              f'RSS {info["vm_rss_kb"]} kB)...', end=' ', flush=True)
        r = analyze_one(pid, args.duration)
        if 'error' in r:
            print(f'ERROR: {r["error"]}')
            continue
        results.append(r)
        print(f'{r["regions_raw"]} regions captured')

    if not results:
        print('No results. Check that PIDs are valid and DAMON is working.')
        sys.exit(1)

    print()
    print('─' * 72)
    print(f'{"PID":>8} {"Process":<20} {"RSS":>10} '
          f'{"Hot":>10} {"Warm":>10} {"Cold":>10} {"Idle":>10}')
    print('─' * 72)

    for r in results:
        s = r['summary']
        total = sum(x['bytes'] for x in s.values())
        fields = [f'{r["pid"]:>8}', f'{r["comm"]:<20}',
                  f'{fmt_bytes(r["vm_rss_kb"]*1024):>10}']
        for cls in ['hot', 'warm', 'cold', 'idle']:
            pct = (s[cls]['bytes'] / total * 100) if total > 0 else 0
            fields.append(f'{pct:>9.1f}%')
        print('  '.join(fields))

    print('─' * 72)
    print()

    for r in results:
        active = [reg for reg in r['classified'] if reg['access_rate_pct'] > 0]
        if not active:
            continue
        print(f'PID {r["pid"]} ({r["comm"]}) — active regions (access > 0%):')
        active.sort(key=lambda x: -x['access_rate_pct'])
        for reg in active:
            addr = reg['start']
            if addr > 0x7f0000000000 and addr < 0x800000000000:
                where = 'stack'
            elif addr > 0x700000000000:
                where = 'mmap'
            elif addr < 0x100000000:
                where = 'text/data'
            else:
                where = 'heap/gap'
            print(f'  0x{addr:012x} ({where:10s})  size={fmt_bytes(reg["size_bytes"]):>10s}  '
                  f'rate={reg["access_rate_pct"]:>5.1f}%  age={reg["age_sec"]:>5.1f}s  '
                  f'class={reg["class"]}')
        print()


if __name__ == '__main__':
    main()
