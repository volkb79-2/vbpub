#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0
"""
analyze_container.py — DAMON-based container memory hot/warm/cold analysis

Supports Docker and Podman containers.

Usage:
    sudo python3 analyze_container.py <CONTAINER_NAME|CONTAINER_ID> [options]

Options:
    --duration SEC       Monitoring duration in seconds (default: 120)
    --sample-us US       Sampling interval in µs (default: 100000 = 100ms)
    --aggr-us US         Aggregation interval in µs (default: 2000000 = 2s)
    --hot-rate PCT       Hot threshold access rate % (default: 50)
    --warm-rate PCT      Warm threshold access rate % (default: 5)
    --cold-age SEC       Cold threshold age in seconds (default: 30)
    --idle-age SEC       Idle threshold age in seconds (default: 120)
    --output FORMAT      Output format: text, json, csv (default: text)
    --output-file PATH   Write output to file (default: stdout)
    --mode MODE          Analysis mode: process|physical (default: process)
    --cgroup-path PATH   Explicit cgroup path for --mode physical

Examples:
    sudo python3 analyze_container.py my-container
    sudo python3 analyze_container.py my-container --duration 300 --output json
    sudo python3 analyze_container.py my-container --mode physical
    sudo python3 analyze_container.py my-container --output-file report.json
"""

import argparse
import json
import os
import sys
import time
import signal
import subprocess
from collections import OrderedDict
from typing import List, Dict, Optional

sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'lib'))
from damon_analysis import (SysfsInterface, Classifier, Monitor,
                            ReportFormatter, get_process_info,
                            get_container_pids)

running = True


def signal_handler(sig, frame):
    global running
    running = False


def parse_args():
    p = argparse.ArgumentParser(
        description='DAMON container memory hot/warm/cold analysis')
    p.add_argument('container', help='Container name or ID')
    p.add_argument('--duration', type=float, default=120.0,
                   help='Monitoring duration in seconds (default: 120)')
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
    p.add_argument('--mode', choices=['process', 'physical'],
                   default='process',
                   help='Analysis mode: process (per-process vaddr) or '
                        'physical (system-wide paddr with memcg filter)')
    p.add_argument('--cgroup-path', type=str,
                   help='Explicit cgroup path (needed for --mode physical)')
    p.add_argument('--min-regions', type=int, default=10)
    p.add_argument('--max-regions', type=int, default=1000)
    return p.parse_args()


def find_container_cgroup(container_name: str) -> Optional[str]:
    """Find the cgroup path for a container (Docker/Podman)."""
    # Try Docker
    try:
        result = subprocess.run(
            ['docker', 'inspect', '--format', '{{.HostConfig.CgroupParent}}',
             container_name],
            capture_output=True, text=True, timeout=10)
        if result.returncode == 0:
            # Docker containers live under /sys/fs/cgroup/<subsystem>/docker/<id>
            id_result = subprocess.run(
                ['docker', 'inspect', '--format', '{{.Id}}', container_name],
                capture_output=True, text=True, timeout=10)
            if id_result.returncode == 0:
                cid = id_result.stdout.strip()
                return f'/docker/{cid}'
    except Exception:
        pass

    # Try Podman
    try:
        result = subprocess.run(
            ['podman', 'inspect', '--format', '{{.Id}}', container_name],
            capture_output=True, text=True, timeout=10)
        if result.returncode == 0:
            cid = result.stdout.strip()
            return f'/machine.slice/libpod-{cid}.scope'
    except Exception:
        pass

    return None


def analyze_process_mode(container_name: str, args, classifier, formatter):
    """Monitor each process in the container via vaddr."""
    pids = get_container_pids(container_name)
    if not pids:
        print(f"ERROR: No processes found in container '{container_name}'",
              file=sys.stderr)
        sys.exit(1)

    print(f"[*] Found {len(pids)} processes in container "
          f"'{container_name}'", file=sys.stderr)
    for pid in pids:
        info = get_process_info(pid)
        if info.get('comm'):
            print(f"    PID {pid}: {info['comm']} "
                  f"(VmRSS: {info['vm_rss_kb']} kB)", file=sys.stderr)

    all_results = []

    for pid in pids:
        info = get_process_info(pid)
        if not info.get('comm'):
            continue
        if info.get('vm_rss_kb', 0) < 100:  # skip tiny processes
            continue

        print(f"\n[*] Analyzing PID {pid} ({info['comm']})...",
              file=sys.stderr)

        monitor = Monitor()
        monitor.configure_vaddr(
            pid=pid,
            sample_us=args.sample_us,
            aggr_us=args.aggr_us,
            min_regions=args.min_regions,
            max_regions=args.max_regions)

        try:
            monitor.start()
            # Warm-up: 3 aggregation periods
            warmup = (monitor.aggr_us / 1_000_000) * 3
            time.sleep(max(warmup, 3.0))

            # Collect data
            regions = monitor.collect()
            classified = classifier.classify_regions(
                regions, monitor.sample_us, monitor.aggr_us)

            for r in classified:
                r['pid'] = pid
                r['comm'] = info['comm']
            all_results.extend(classified)

        finally:
            monitor.stop()
            time.sleep(0.5)

    return all_results


def analyze_physical_mode(container_name: str, args, classifier, formatter):
    """Monitor physical address space with memcg filter."""
    cgroup_path = args.cgroup_path
    if not cgroup_path:
        cgroup_path = find_container_cgroup(container_name)

    if not cgroup_path:
        print("ERROR: Could not determine cgroup path for container. "
              "Use --cgroup-path to specify it explicitly.",
              file=sys.stderr)
        print("Example: --cgroup-path /docker/<container_id>",
              file=sys.stderr)
        sys.exit(1)

    print(f"[*] Cgroup path: {cgroup_path}", file=sys.stderr)

    # For physical mode with memcg filter, we need damo or direct sysfs
    # with ops_filters. Let's use damo for this since filter setup via
    # sysfs directly is verbose.
    print("[*] Using damo for physical address monitoring with memcg filter",
          file=sys.stderr)

    # Build damo command
    damo_bin = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), 'venv', 'bin', 'damo')
    if not os.path.exists(damo_bin):
        damo_bin = 'damo'  # fall back to PATH

    try:
        # Start damo with paddr + memcg filter + stat action
        cmd = [
            damo_bin, 'start', 'paddr',
            '--damos_action', 'stat',
            '--damos_filter', f'reject none memcg {cgroup_path}',
            '-s', f'{args.sample_us // 1000}ms',
            '-a', f'{args.aggr_us // 1000}ms',
        ]
        subprocess.run(cmd, check=True, timeout=30)
        time.sleep(1)

        # Wait for monitoring data
        warmup = (args.aggr_us / 1_000_000) * 3
        time.sleep(max(warmup, 3.0))

        # Collect via tried_regions
        sysfs = SysfsInterface()
        sysfs.kdamond_update_tried_regions(0)
        time.sleep(0.2)
        regions = sysfs.read_tried_regions(0, 0, 0)

        classified = classifier.classify_regions(
            regions, args.sample_us, args.aggr_us)

        return classified

    except subprocess.CalledProcessError as e:
        print(f"ERROR: damo failed: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        # Always stop
        try:
            subprocess.run([damo_bin, 'stop'], timeout=10)
        except Exception:
            pass


def main():
    global running
    args = parse_args()
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    if not SysfsInterface.is_available():
        print("ERROR: DAMON sysfs not available.", file=sys.stderr)
        sys.exit(1)

    if os.geteuid() != 0:
        print("ERROR: Root privileges required.", file=sys.stderr)
        sys.exit(1)

    classifier = Classifier(
        hot_access_rate_pct=args.hot_rate,
        warm_access_rate_pct=args.warm_rate,
        cold_age_sec=args.cold_age,
        idle_age_sec=args.idle_age)
    formatter = ReportFormatter()

    print(f"[*] Container analysis: '{args.container}'", file=sys.stderr)
    print(f"    Mode: {args.mode}, Duration: {args.duration}s",
          file=sys.stderr)
    print(f"    Intervals: sample={args.sample_us}µs, "
          f"aggr={args.aggr_us}µs", file=sys.stderr)

    if args.mode == 'process':
        classified = analyze_process_mode(args.container, args, classifier,
                                          formatter)
        metadata = {
            'container': args.container,
            'mode': 'process',
            'sample_us': args.sample_us,
            'aggr_us': args.aggr_us,
        }
    else:
        classified = analyze_physical_mode(args.container, args, classifier,
                                           formatter)
        metadata = {
            'container': args.container,
            'mode': 'physical',
            'sample_us': args.sample_us,
            'aggr_us': args.aggr_us,
        }

    # Aggregate summary across all processes (for process mode)
    if args.mode == 'process':
        summary = classifier.summary(classified)
        total_bytes = sum(s['bytes'] for s in summary.values())
    else:
        summary = classifier.summary(classified)
        total_bytes = sum(s['bytes'] for s in summary.values())

    # Generate output
    if args.output == 'json':
        output = formatter.json_report(classified, metadata)
    elif args.output == 'csv':
        output = formatter.csv_report(classified)
    else:
        output = formatter.human_readable(
            classified,
            title=f"Container: {args.container} (mode: {args.mode})")

    if args.output_file:
        with open(args.output_file, 'w') as f:
            f.write(output + '\n')
        print(f"[*] Report written to {args.output_file}", file=sys.stderr)
    else:
        print(output)

    # Print summary to stderr
    print(f"\n[*] Summary:", file=sys.stderr)
    for cls in ['hot', 'warm', 'cold', 'idle']:
        s = summary[cls]
        pct = (s['bytes'] / total_bytes * 100) if total_bytes > 0 else 0
        print(f"    {cls.upper():<6}: {s['count']:>4} regions, "
              f"{ReportFormatter._fmt_bytes(s['bytes']):>12} "
              f"({pct:.1f}%)", file=sys.stderr)


if __name__ == '__main__':
    main()
