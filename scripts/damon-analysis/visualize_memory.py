#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0
"""
visualize_memory.py — Generate visual representations of DAMON analysis results.

Reads JSON output from analyze_process.py or analyze_container.py
and produces:
  - ASCII heatmap (terminal)
  - Hot/cold distribution bar chart (terminal)
  - PNG heatmap (requires matplotlib, optional)

Usage:
    python3 visualize_memory.py <input.json> [options]

Options:
    --format FORMAT     Output format: ascii, chart, png (default: all)
    --output PATH       Output file path (for PNG)
    --title TEXT        Chart title
"""

import argparse
import json
import os
import sys
from collections import OrderedDict
from typing import List, Dict

# Add lib to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'lib'))
from damon_analysis import ReportFormatter


def parse_args():
    p = argparse.ArgumentParser(
        description='Visualize DAMON analysis results')
    p.add_argument('input_file', help='JSON output from analyze_*.py')
    p.add_argument('--format', choices=['ascii', 'chart', 'png', 'all'],
                   default='all', help='Output format')
    p.add_argument('--output', type=str, help='Output file path (for PNG)')
    p.add_argument('--title', type=str, default='Memory Access Analysis')
    p.add_argument('--timeseries', action='store_true',
                   help='Input is JSONL (one snapshot per line) — show time-series')
    return p.parse_args()


def ascii_heatmap(regions: List[Dict], bins: int = 80) -> str:
    """Generate a simple ASCII heatmap."""
    if not regions:
        return "(no data)"

    # Find address range
    all_starts = [r['start'] for r in regions]
    all_ends = [r['end'] for r in regions]
    min_addr = min(all_starts)
    max_addr = max(all_ends)
    total_range = max_addr - min_addr
    if total_range == 0:
        total_range = 1

    # Temperature range
    temps = [r.get('temperature', 0) for r in regions]
    min_temp = min(temps)
    max_temp = max(temps)
    temp_range = max_temp - min_temp
    if temp_range == 0:
        temp_range = 1

    # Create heatmap bins
    chars = ['0', '1', '2', '3', '4', '5', '6', '7', '8', '9']
    heatmap = [' '] * bins

    for r in regions:
        start_bin = int((r['start'] - min_addr) / total_range * bins)
        end_bin = int((r['end'] - min_addr) / total_range * bins)
        end_bin = max(end_bin, start_bin + 1)
        end_bin = min(end_bin, bins)

        # Map temperature to char (0=cold, 9=hot)
        temp = r.get('temperature', 0)
        char_idx = int((temp - min_temp) / temp_range * 9)
        char_idx = max(0, min(9, char_idx))
        ch = chars[char_idx]

        for i in range(start_bin, min(end_bin, bins)):
            heatmap[i] = ch

    line = ''.join(heatmap)
    lines = []
    lines.append(f"# Heatmap ({bins} columns, {ReportFormatter._fmt_bytes(total_range / bins)} per column)")
    lines.append(f"# Temperature range: {ReportFormatter._fmt_temp(min_temp)} .. {ReportFormatter._fmt_temp(max_temp)}")
    lines.append(f"# 0=coldest, 9=hottest")
    lines.append(line)
    return '\n'.join(lines)


def distribution_chart(regions: List[Dict]) -> str:
    """Generate a bar chart showing hot/warm/cold/idle distribution."""
    from collections import Counter
    import math

    classes = Counter(r.get('class', 'unknown') for r in regions)
    total_bytes = sum(r.get('size_bytes', 0) for r in regions)

    class_order = ['hot', 'warm', 'cold', 'idle']
    max_bar_width = 40

    lines = []
    lines.append("Memory Class Distribution")
    lines.append("=" * 50)

    for cls in class_order:
        count = classes.get(cls, 0)
        cls_regions = [r for r in regions if r.get('class') == cls]
        cls_bytes = sum(r.get('size_bytes', 0) for r in cls_regions)
        pct = (cls_bytes / total_bytes * 100) if total_bytes > 0 else 0

        bar_width = int(pct / 100 * max_bar_width)
        bar = '█' * bar_width + '░' * (max_bar_width - bar_width)

        lines.append(f"  {cls.upper():<6} │{bar}│ {pct:5.1f}%  "
                     f"({count:>3} regions, {ReportFormatter._fmt_bytes(cls_bytes)})")

    lines.append("=" * 50)
    lines.append(f"  TOTAL: {len(regions)} regions, "
                 f"{ReportFormatter._fmt_bytes(total_bytes)}")
    return '\n'.join(lines)


def size_class_chart(regions: List[Dict]) -> str:
    """Generate a size-class breakdown chart."""
    size_classes = OrderedDict([
        ('4 KiB', (0, 4 * 1024)),
        ('16 KiB', (4 * 1024, 16 * 1024)),
        ('64 KiB', (16 * 1024, 64 * 1024)),
        ('256 KiB', (64 * 1024, 256 * 1024)),
        ('1 MiB', (256 * 1024, 1024 * 1024)),
        ('4 MiB', (1024 * 1024, 4 * 1024 * 1024)),
        ('16 MiB', (4 * 1024 * 1024, 16 * 1024 * 1024)),
        ('64 MiB', (16 * 1024 * 1024, 64 * 1024 * 1024)),
        ('256 MiB', (64 * 1024 * 1024, 256 * 1024 * 1024)),
        ('1 GiB+', (256 * 1024 * 1024, float('inf'))),
    ])

    total = sum(r.get('size_bytes', 0) for r in regions)
    max_bar_width = 30

    lines = []
    lines.append("Region Size Distribution")
    lines.append("=" * 50)

    for label, (lo, hi) in size_classes.items():
        matching = [r for r in regions if lo <= r.get('size_bytes', 0) < hi]
        count = len(matching)
        bytes_sum = sum(r.get('size_bytes', 0) for r in matching)
        pct = (bytes_sum / total * 100) if total > 0 else 0

        if count == 0:
            continue

        bar_width = int(pct / 100 * max_bar_width)
        bar = '▓' * bar_width + ' ' * (max_bar_width - bar_width)

        lines.append(f"  {label:<8} │{bar}│ {pct:5.1f}%  "
                     f"({count:>3} regions, {ReportFormatter._fmt_bytes(bytes_sum)})")

    lines.append("=" * 50)
    return '\n'.join(lines)


def generate_png(regions: List[Dict], output_path: str, title: str) -> None:
    """Generate a PNG heatmap using matplotlib (if available)."""
    try:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        import numpy as np
    except ImportError:
        print("ERROR: matplotlib not available. Install with: "
              "pip install matplotlib", file=sys.stderr)
        return

    if not regions:
        print("WARNING: No regions to plot.", file=sys.stderr)
        return

    # Extract data
    starts = np.array([r['start'] for r in regions])
    sizes = np.array([r.get('size_bytes', 0) for r in regions])
    temps = np.array([r.get('temperature', 0) for r in regions])

    # Normalize temperatures to 0-1
    t_min, t_max = temps.min(), temps.max()
    if t_max > t_min:
        temps_norm = (temps - t_min) / (t_max - t_min)
    else:
        temps_norm = np.zeros_like(temps)

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 6))
    fig.suptitle(title, fontsize=14)

    # Plot 1: Temperature vs Address
    scatter = ax1.scatter(starts, temps, c=temps_norm, cmap='RdYlBu_r',
                          s=np.clip(sizes / max(sizes) * 200, 10, 200),
                          alpha=0.7, edgecolors='none')
    ax1.set_xlabel('Address')
    ax1.set_ylabel('Temperature')
    ax1.set_title('Memory Region Temperatures')
    ax1.ticklabel_format(style='plain', axis='x')
    plt.colorbar(scatter, ax=ax1, label='Temperature (normalized)')

    # Plot 2: Size distribution by class
    classes = {'hot': 0, 'warm': 0, 'cold': 0, 'idle': 0}
    for r in regions:
        cls = r.get('class', 'idle')
        classes[cls] += r.get('size_bytes', 0)

    cls_labels = list(classes.keys())
    cls_sizes = [classes[c] for c in cls_labels]
    cls_colors = ['#ff4444', '#ffaa00', '#4488ff', '#888888']

    ax2.bar(cls_labels, [s / (1024**2) for s in cls_sizes], color=cls_colors)
    ax2.set_ylabel('Size (MiB)')
    ax2.set_title('Memory Distribution by Class')
    for i, v in enumerate(cls_sizes):
        ax2.text(i, v / (1024**2) + 0.5, ReportFormatter._fmt_bytes(v),
                 ha='center', fontsize=8)

    plt.tight_layout()
    plt.savefig(output_path, dpi=150)
    plt.close()
    print(f"[*] PNG saved to {output_path}", file=sys.stderr)


def timeseries_ascii(snapshots: list) -> str:
    """ASCII time-series chart: one bar row per snapshot."""
    if not snapshots:
        return "(no data)"

    bar_width = 30
    lines = []
    lines.append("Time-Series Memory Classification")
    lines.append("=" * 80)
    lines.append(f"  {'Time':>7}  {'HOT':>8}  {'WARM':>8}  {'COLD':>8}  "
                 f"{'IDLE':>8}  {'RSS':>7}  bar (hot=█ warm=▓ cold=░ idle=·)")
    lines.append("-" * 80)

    max_total = max((s.get('total_bytes', 0) or
                     sum(s['summary'][c]['bytes'] for c in ['hot', 'warm', 'cold', 'idle']))
                    for s in snapshots) or 1

    for snap in snapshots:
        elapsed = snap.get('elapsed_sec', 0)
        m, s_rem = divmod(int(elapsed), 60)
        t_str = f"{m}:{s_rem:02d}"

        summary = snap.get('summary', {})
        hot  = summary.get('hot',  {}).get('bytes', 0)
        warm = summary.get('warm', {}).get('bytes', 0)
        cold = summary.get('cold', {}).get('bytes', 0)
        idle = summary.get('idle', {}).get('bytes', 0)
        rss_mb = snap.get('vm_rss_kb', 0) / 1024

        def seg(n, ch):
            w = int(n / max_total * bar_width)
            return ch * w

        bar = seg(hot, '█') + seg(warm, '▓') + seg(cold, '░') + seg(idle, '·')
        bar = bar[:bar_width].ljust(bar_width)

        lines.append(f"  {t_str:>7}  "
                     f"{hot/(1024**2):>7.0f}M  "
                     f"{warm/(1024**2):>7.0f}M  "
                     f"{cold/(1024**2):>7.0f}M  "
                     f"{idle/(1024**2):>7.0f}M  "
                     f"{rss_mb:>6.0f}M  {bar}")

    lines.append("=" * 80)
    lines.append(f"  {len(snapshots)} snapshots  "
                 f"span {snapshots[-1].get('elapsed_sec', 0):.0f}s")
    return '\n'.join(lines)


def generate_timeseries_png(snapshots: list, output_path: str, title: str) -> None:
    """Stacked area chart of hot/warm/cold/idle bytes over time."""
    try:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        import numpy as np
    except ImportError:
        print("ERROR: matplotlib not available. pip install matplotlib",
              file=sys.stderr)
        return

    if not snapshots:
        print("WARNING: No snapshots to plot.", file=sys.stderr)
        return

    times = [s.get('elapsed_sec', 0) for s in snapshots]
    hot   = [s['summary'].get('hot',  {}).get('bytes', 0) / (1024**2) for s in snapshots]
    warm  = [s['summary'].get('warm', {}).get('bytes', 0) / (1024**2) for s in snapshots]
    cold  = [s['summary'].get('cold', {}).get('bytes', 0) / (1024**2) for s in snapshots]
    idle  = [s['summary'].get('idle', {}).get('bytes', 0) / (1024**2) for s in snapshots]
    rss   = [s.get('vm_rss_kb', 0) / 1024 for s in snapshots]

    fig, ax = plt.subplots(figsize=(14, 6))
    fig.suptitle(title, fontsize=13)

    ax.stackplot(times, hot, warm, cold, idle,
                 labels=['Hot', 'Warm', 'Cold', 'Idle'],
                 colors=['#ff4444', '#ffaa00', '#4488ff', '#aaaaaa'],
                 alpha=0.85)
    ax.plot(times, rss, 'k--', linewidth=1.5, label='RSS total', alpha=0.6)

    ax.set_xlabel('Elapsed time (s)')
    ax.set_ylabel('Memory (MiB)')
    ax.set_title('Hot/Warm/Cold/Idle Memory Over Time')
    ax.legend(loc='upper left')
    ax.grid(True, alpha=0.3)

    plt.tight_layout()
    plt.savefig(output_path, dpi=150)
    plt.close()
    print(f"[*] PNG saved to {output_path}", file=sys.stderr)


def main():
    args = parse_args()

    if args.timeseries:
        # JSONL input: one snapshot per line
        snapshots = []
        with open(args.input_file, 'r') as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        snapshots.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
        if not snapshots:
            print("ERROR: No snapshots found in JSONL file.", file=sys.stderr)
            sys.exit(1)

        title = args.title or f"Time-Series: {snapshots[0].get('comm', '?')}"
        if args.format in ('ascii', 'chart', 'all'):
            print(timeseries_ascii(snapshots))
        if args.format in ('png', 'all'):
            output_path = args.output or args.input_file.replace('.jsonl', '.png')
            generate_timeseries_png(snapshots, output_path, title)
        return

    # Load data
    with open(args.input_file, 'r') as f:
        data = json.load(f)

    regions = data.get('regions', [])
    if not regions:
        print("ERROR: No regions found in input file.", file=sys.stderr)
        sys.exit(1)

    metadata = data.get('metadata', {})

    if args.format in ('ascii', 'all'):
        print(ascii_heatmap(regions))
        print()

    if args.format in ('chart', 'all'):
        print(distribution_chart(regions))
        print()
        print(size_class_chart(regions))

    if args.format in ('png', 'all'):
        output_path = args.output or args.input_file.replace('.json', '.png')
        generate_png(regions, output_path, args.title)


if __name__ == '__main__':
    main()
