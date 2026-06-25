#!/usr/bin/env python3
"""Batch-analyze multiple PIDs and print a summary report."""
import os, sys, time, json, subprocess
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'lib'))
from damon_analysis import SysfsInterface, Classifier, ReportFormatter, get_process_info
from collections import OrderedDict

DAMO = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'venv', 'bin', 'damo')
SAMPLE_US = 100_000       # 100 ms
AGGR_US   = 2_000_000     # 2 s
HOT_RATE  = 50.0
WARM_RATE = 5.0
COLD_AGE  = 30.0
IDLE_AGE  = 120.0

PIDS = [1931, 1173, 1978, 30637]  # VSC extension, server, pty, reasonix

def fmt_bytes(b):
    for unit in ['B','KiB','MiB','GiB','TiB']:
        if abs(b) < 1024: return f'{b:.1f} {unit}' if unit != 'B' else f'{int(b)} B'
        b /= 1024

def analyze_one(pid: int, duration_s: int = 15):
    """Run damo, collect, classify, return dict."""
    # Stop any existing
    subprocess.run([DAMO, 'stop'], capture_output=True, timeout=5)
    time.sleep(0.5)

    # Start monitoring
    cmd = [DAMO, 'start', '--target_pid', str(pid),
           '-s', f'{SAMPLE_US // 1000}ms',
           '-a', f'{AGGR_US // 1000}ms',
           '--damos_action', 'stat',
           '--damos_access_rate', '0%', 'max',
           '--damos_sz_region', '0', 'max',
           '--damos_age', '0', 'max']
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if r.returncode != 0:
        return {'error': f'damo start failed: {r.stderr.strip()}'}

    # Wait for data
    time.sleep(duration_s)

    # Collect
    s = SysfsInterface()
    s.kdamond_update_tried_regions(0)
    time.sleep(0.2)
    regions = s.read_tried_regions(0, 0, 0)

    # Stop
    subprocess.run([DAMO, 'stop'], capture_output=True, timeout=5)

    # Classify
    c = Classifier(hot_access_rate_pct=HOT_RATE, warm_access_rate_pct=WARM_RATE,
                   cold_age_sec=COLD_AGE, idle_age_sec=IDLE_AGE)
    classified = c.classify_regions(regions, SAMPLE_US, AGGR_US)
    summary = c.summary(classified)

    # Process info
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
    print('=' * 72)
    print('  DAMON Memory Analysis — VSC SSH Connection Processes')
    print(f'  Kernel: {os.uname().release}')
    print(f'  Intervals: {SAMPLE_US//1000}ms / {AGGR_US//1000}ms')
    print(f'  Thresholds: hot ≥{HOT_RATE}%, warm ≥{WARM_RATE}%, cold age ≥{COLD_AGE}s, idle age ≥{IDLE_AGE}s')
    print('=' * 72)
    print()

    results = []
    for pid in PIDS:
        info = get_process_info(pid)
        if not info.get('comm'):
            print(f'  PID {pid}: not found, skipping')
            continue

        duration = 12 if pid != 30637 else 20  # longer for reasonix
        print(f'  Analyzing PID {pid} ({info["comm"]}, RSS {info["vm_rss_kb"]} kB)...', end=' ', flush=True)
        r = analyze_one(pid, duration)
        if 'error' in r:
            print(f'ERROR: {r["error"]}')
            continue
        results.append(r)
        print(f'{r["regions_raw"]} regions captured')

    # ── Summary table ──
    print()
    print('─' * 72)
    print(f'{"PID":>8} {"Process":<20} {"RSS":>10} {"Hot":>10} {"Warm":>10} {"Cold":>10} {"Idle":>10}')
    print('─' * 72)

    for r in results:
        s = r['summary']
        total = sum(x['bytes'] for x in s.values())
        fields = [f'{r["pid"]:>8}', f'{r["comm"]:<20}', f'{fmt_bytes(r["vm_rss_kb"]*1024):>10}']
        for cls in ['hot','warm','cold','idle']:
            pct = (s[cls]['bytes'] / total * 100) if total > 0 else 0
            fields.append(f'{pct:>9.1f}%')
        print('  '.join(fields))

    print('─' * 72)
    print()
    print('NOTES:')
    print('  - "Warm" dominates because most virtual address space is unmapped gaps.')
    print('  - Focus on active regions (access rate > 0%) for real memory access patterns.')
    print('  - Stack-area regions (0x7ffe...) with non-zero access = active thread stacks.')
    print('  - Heap/mmap regions with non-zero access = actively used memory.')
    print()

    # ── Active region details ──
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
