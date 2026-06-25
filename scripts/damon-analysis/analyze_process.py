#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0
"""
analyze_process.py — DAMON-based process memory hot/warm/cold analysis

Usage:
    sudo python3 analyze_process.py <PID> [options]
    sudo python3 analyze_process.py --command "myapp --flag" [options]

Options:
    --duration SEC       Monitoring duration in seconds (default: 60)
    --sample-us US       Sampling interval in µs (default: 100000 = 100ms)
    --aggr-us US         Aggregation interval in µs (default: 2000000 = 2s)
    --hot-rate PCT       Hot threshold access rate % (default: 50)
    --warm-rate PCT      Warm threshold access rate % (default: 5)
    --cold-age SEC       Cold threshold age in seconds (default: 30)
    --idle-age SEC       Idle threshold age in seconds (default: 120)
    --output FORMAT      Output format: text, json, csv (default: text)
    --output-file PATH   Write output to file (default: stdout)
    --interval SEC       Snapshot interval in seconds (default: 10)
    --continuous         Run continuously, printing snapshots

Examples:
    sudo python3 analyze_process.py 12345
    sudo python3 analyze_process.py 12345 --duration 120 --output json
    sudo python3 analyze_process.py 12345 --continuous --interval 5
"""

import argparse
import os
import sys
import time
import signal

# Add lib to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'lib'))
from damon_analysis import (SysfsInterface, Classifier, Monitor,
                            ReportFormatter, get_process_info)

# Auto-detect damo binary
_DAMO_BIN = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         'venv', 'bin', 'damo')
if not os.path.exists(_DAMO_BIN):
    _DAMO_BIN = 'damo'  # fall back to PATH

running = True


def signal_handler(sig, frame):
    global running
    running = False


def parse_args():
    p = argparse.ArgumentParser(
        description='DAMON process memory hot/warm/cold analysis')
    p.add_argument('target', nargs='?',
                   help='PID or command string to run and monitor')
    p.add_argument('--duration', type=float, default=60.0,
                   help='Monitoring duration in seconds (default: 60)')
    p.add_argument('--sample-us', type=int, default=100_000,
                   help='Sampling interval in µs (default: 100000)')
    p.add_argument('--aggr-us', type=int, default=2_000_000,
                   help='Aggregation interval in µs (default: 2000000)')
    p.add_argument('--hot-rate', type=float, default=50.0,
                   help='Hot threshold access rate %% (default: 50)')
    p.add_argument('--warm-rate', type=float, default=5.0,
                   help='Warm threshold access rate %% (default: 5)')
    p.add_argument('--cold-age', type=float, default=30.0,
                   help='Cold threshold age in seconds (default: 30)')
    p.add_argument('--idle-age', type=float, default=120.0,
                   help='Idle threshold age in seconds (default: 120)')
    p.add_argument('--output', choices=['text', 'json', 'csv'],
                   default='text', help='Output format (default: text)')
    p.add_argument('--output-file', type=str,
                   help='Write output to file (default: stdout)')
    p.add_argument('--interval', type=float, default=10.0,
                   help='Snapshot interval in seconds (default: 10)')
    p.add_argument('--continuous', action='store_true',
                   help='Run continuously, printing snapshots')
    p.add_argument('--min-regions', type=int, default=10)
    p.add_argument('--max-regions', type=int, default=1000)
    p.add_argument('--update-us', type=int,
                   help='Update interval in µs (default: kernel default)')
    p.add_argument('--ops', choices=['vaddr', 'paddr'], default='vaddr',
                   help='Operations set (default: vaddr)')
    return p.parse_args()


def get_pid(target: str) -> int:
    """Resolve target to a PID."""
    # Check if it's already a PID
    if target.isdigit():
        return int(target)

    # It's a command — run it in background
    import subprocess
    proc = subprocess.Popen(target, shell=True)
    return proc.pid


def run_analysis(monitor: Monitor, classifier: Classifier,
                 formatter: ReportFormatter, args, metadata: dict) -> None:
    """Run one monitoring cycle and print results."""

    # Start monitoring
    monitor.start()
    time.sleep(0.5)

    if not monitor.is_running():
        print("ERROR: Failed to start DAMON monitoring. Check kernel config.",
              file=sys.stderr)
        sys.exit(1)

    # Wait for data accumulation
    wait_time = max(args.aggr_us / 1_000_000 * 2, args.interval)
    time.sleep(wait_time)

    # Collect and classify
    regions = monitor.collect()
    if not regions:
        print("WARNING: No regions collected. "
              "Try longer intervals or check if process has mapped memory.",
              file=sys.stderr)

    classified = classifier.classify_regions(
        regions, monitor.sample_us, monitor.aggr_us)

    # Generate output
    if args.output == 'json':
        output = formatter.json_report(classified, metadata)
    elif args.output == 'csv':
        output = formatter.csv_report(classified)
    else:
        info = get_process_info(metadata.get('pid', 0)) if metadata else {}
        proc_name = info.get('comm', 'unknown')
        output = formatter.human_readable(
            classified,
            title=f"Process: {proc_name} (PID: {metadata.get('pid', 'N/A')})")

    # Write output
    if args.output_file:
        with open(args.output_file, 'w') as f:
            f.write(output + '\n')
    else:
        print(output)

    return classified


def main():
    global running
    args = parse_args()
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # Check availability
    if not SysfsInterface.is_available():
        print("ERROR: DAMON sysfs not available at "
              f"{os.path.join('/sys/kernel/mm/damon/admin/kdamonds')}",
              file=sys.stderr)
        print("Check: grep CONFIG_DAMON_SYSFS /boot/config-$(uname -r)",
              file=sys.stderr)
        sys.exit(1)

    if os.geteuid() != 0:
        print("ERROR: Root privileges required.", file=sys.stderr)
        sys.exit(1)

    # Resolve PID
    if not args.target:
        print("ERROR: Target PID or command required.", file=sys.stderr)
        sys.exit(1)

    pid = get_pid(args.target)
    proc_info = get_process_info(pid)
    if not proc_info.get('comm'):
        print(f"ERROR: Process {pid} not found.", file=sys.stderr)
        sys.exit(1)

    print(f"[*] Monitoring PID {pid} ({proc_info['comm']}) "
          f"for {args.duration}s...", file=sys.stderr)
    print(f"    Intervals: sample={args.sample_us}µs, "
          f"aggr={args.aggr_us}µs", file=sys.stderr)
    print(f"    Classification: hot ≥{args.hot_rate}%, "
          f"warm ≥{args.warm_rate}%, cold age ≥{args.cold_age}s, "
          f"idle age ≥{args.idle_age}s", file=sys.stderr)

    # Initialize
    classifier = Classifier(
        hot_access_rate_pct=args.hot_rate,
        warm_access_rate_pct=args.warm_rate,
        cold_age_sec=args.cold_age,
        idle_age_sec=args.idle_age)
    formatter = ReportFormatter()
    metadata = {
        'pid': pid,
        'comm': proc_info.get('comm', 'unknown'),
        'vm_size_kb': proc_info.get('vm_size_kb', 0),
        'vm_rss_kb': proc_info.get('vm_rss_kb', 0),
        'sample_us': args.sample_us,
        'aggr_us': args.aggr_us,
    }

    if args.continuous:
        # Continuous mode
        monitor = Monitor(damo_bin=_DAMO_BIN)
        monitor.configure_vaddr(
            pid=pid,
            sample_us=args.sample_us,
            aggr_us=args.aggr_us,
            min_regions=args.min_regions,
            max_regions=args.max_regions,
            update_us=args.update_us)

        snapshot_num = 0
        start_time = time.time()
        try:
            monitor.start()
            while running and (time.time() - start_time < args.duration
                               if args.duration > 0 else True):
                time.sleep(args.interval)
                regions = monitor.collect()
                classified = classifier.classify_regions(
                    regions, monitor.sample_us, monitor.aggr_us)

                snapshot_num += 1
                meta = dict(metadata)
                meta['snapshot'] = snapshot_num
                meta['elapsed_sec'] = round(time.time() - start_time, 1)

                if args.output == 'json':
                    output = formatter.json_report(classified, meta)
                elif args.output == 'csv':
                    output = formatter.csv_report(classified)
                else:
                    output = formatter.human_readable(
                        classified,
                        title=f"Snapshot #{snapshot_num} "
                              f"(t={meta['elapsed_sec']}s)")

                if args.output_file:
                    mode = 'a' if snapshot_num > 1 else 'w'
                    with open(args.output_file, mode) as f:
                        f.write(output + '\n')
                else:
                    print(output)
        finally:
            monitor.stop()

    else:
        # Single snapshot mode
        monitor = Monitor(damo_bin=_DAMO_BIN)
        monitor.configure_vaddr(
            pid=pid,
            sample_us=args.sample_us,
            aggr_us=args.aggr_us,
            min_regions=args.min_regions,
            max_regions=args.max_regions,
            update_us=args.update_us)

        try:
            # Let monitoring run for the specified duration
            warmup = max(args.aggr_us / 1_000_000 * 3, 5.0)
            first_wait = min(warmup, args.duration * 0.3)
            monitor.start()
            time.sleep(first_wait)

            # For the remaining time, periodically collect but keep running
            end_time = time.time() + args.duration - first_wait
            last_collect = 0
            while time.time() < end_time and running:
                time.sleep(min(args.interval, end_time - time.time()))
                if time.time() - last_collect >= args.interval:
                    monitor.collect()  # update tried regions
                    last_collect = time.time()

            # Final collect for result
            regions = monitor.collect()
            classified = classifier.classify_regions(
                regions, monitor.sample_us, monitor.aggr_us)
            run_analysis(monitor, classifier, formatter, args, metadata)
        finally:
            monitor.stop()


if __name__ == '__main__':
    main()
