#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0
"""
web_report.py — Interactive Flask dashboard for DAMON time-series data.

Reads JSONL output from damon_cli.py timeseries-pid and serves an HTML
dashboard with Chart.js visualizations, live polling, and time-lapse animation.

Usage:
    python3 web_report.py --file <path.jsonl> [--port 8080] [--watch]
"""

import sys, os
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_VENV_SITE = os.path.join(_SCRIPT_DIR, 'venv', 'lib')
for _d in os.listdir(_VENV_SITE):
    _sp = os.path.join(_VENV_SITE, _d, 'site-packages')
    if os.path.isdir(_sp):
        sys.path.insert(0, _sp)
        break

import argparse
import json
import time
from pathlib import Path
from flask import Flask, jsonify, render_template_string

app = Flask(__name__)

# Global state
g_data = {
    'snapshots': [],
    'file_path': '',
    'file_mtime': 0,
}

HTML_TEMPLATE = '''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>gstammtisch DAMON Monitor</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js@4/dist/chart.umd.min.js"></script>
    <style>
        * { box-sizing: border-box; }
        body {
            margin: 0;
            padding: 16px;
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: #111827;
            color: #f9fafb;
        }
        .header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 24px;
            border-bottom: 1px solid #374151;
            padding-bottom: 16px;
        }
        .header h1 {
            margin: 0;
            font-size: 24px;
        }
        .header-subtitle {
            font-size: 13px;
            color: #9ca3af;
        }
        .status-badge {
            padding: 6px 12px;
            border-radius: 4px;
            font-size: 12px;
            font-weight: 500;
        }
        .status-live {
            background: #065f46;
            color: #10b981;
        }
        .status-done {
            background: #374151;
            color: #d1d5db;
        }
        .summary-bar {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 16px;
            margin-bottom: 24px;
            padding: 16px;
            background: #1f2937;
            border-radius: 6px;
        }
        .summary-item {
            display: flex;
            flex-direction: column;
        }
        .summary-label {
            font-size: 11px;
            color: #9ca3af;
            text-transform: uppercase;
            margin-bottom: 4px;
        }
        .summary-value {
            font-size: 18px;
            font-weight: 600;
            color: #f9fafb;
        }
        .charts-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(400px, 1fr));
            gap: 16px;
            margin-bottom: 24px;
        }
        .chart-card {
            background: #1f2937;
            border-radius: 6px;
            padding: 16px;
        }
        .chart-card h3 {
            margin: 0 0 12px 0;
            font-size: 14px;
            color: #e5e7eb;
        }
        .chart-container {
            position: relative;
            height: 250px;
        }
        .controls {
            background: #1f2937;
            border-radius: 6px;
            padding: 16px;
            margin-bottom: 24px;
        }
        .control-row {
            display: flex;
            gap: 12px;
            align-items: center;
            flex-wrap: wrap;
            margin-bottom: 12px;
        }
        .control-row:last-child {
            margin-bottom: 0;
        }
        button {
            padding: 8px 16px;
            background: #4f46e5;
            color: #f9fafb;
            border: none;
            border-radius: 4px;
            cursor: pointer;
            font-size: 13px;
            font-weight: 500;
        }
        button:hover {
            background: #4338ca;
        }
        button.secondary {
            background: #374151;
        }
        button.secondary:hover {
            background: #4b5563;
        }
        .speed-select, .scrubber {
            padding: 6px 12px;
            background: #374151;
            color: #f9fafb;
            border: 1px solid #4b5563;
            border-radius: 4px;
            font-size: 13px;
            cursor: pointer;
        }
        .scrubber {
            flex-grow: 1;
            max-width: 400px;
            padding: 4px;
        }
        .scrubber-info {
            font-size: 12px;
            color: #9ca3af;
            min-width: 120px;
        }
        .file-info {
            font-size: 11px;
            color: #6b7280;
            margin-top: 8px;
        }
        @media (max-width: 768px) {
            .charts-grid {
                grid-template-columns: 1fr;
            }
            .summary-bar {
                grid-template-columns: 1fr;
            }
        }
    </style>
</head>
<body>
    <div class="header">
        <div>
            <h1>gstammtisch DAMON Monitor</h1>
            <div class="header-subtitle" id="subtitle">loading...</div>
        </div>
        <div class="status-badge" id="status-badge">LIVE ●</div>
    </div>

    <div class="summary-bar">
        <div class="summary-item">
            <div class="summary-label">Peak Hot+Warm</div>
            <div class="summary-value" id="summary-peak">— GiB</div>
        </div>
        <div class="summary-item">
            <div class="summary-label">Steady-State Hot+Warm</div>
            <div class="summary-value" id="summary-steady">— GiB</div>
        </div>
        <div class="summary-item">
            <div class="summary-label">Peak CPU %</div>
            <div class="summary-value" id="summary-cpu">—%</div>
        </div>
        <div class="summary-item">
            <div class="summary-label">Avg Read IO</div>
            <div class="summary-value" id="summary-read">—</div>
        </div>
        <div class="summary-item">
            <div class="summary-label">Avg Write IO</div>
            <div class="summary-value" id="summary-write">—</div>
        </div>
    </div>

    <div class="controls">
        <div class="control-row">
            <button id="btn-play" onclick="togglePlay()">▶ Play</button>
            <button class="secondary" onclick="resetAnimation()">⏮ Reset</button>
            <label>Speed:
                <select class="speed-select" id="speed-select" onchange="setSpeed()">
                    <option value="5">1×</option>
                    <option value="25">5×</option>
                    <option value="100">20×</option>
                </select>
            </label>
        </div>
        <div class="control-row">
            <input type="range" class="scrubber" id="scrubber" min="0" max="30" value="0" oninput="scrubberChanged()">
            <div class="scrubber-info"><span id="time-display">0:00</span> snap <span id="snap-display">0</span>/<span id="snap-total">0</span></div>
        </div>
        <div class="file-info" id="file-info">File: —</div>
    </div>

    <div class="charts-grid">
        <div class="chart-card">
            <h3>Memory Breakdown</h3>
            <div class="chart-container">
                <canvas id="chart-memory"></canvas>
            </div>
        </div>
        <div class="chart-card">
            <h3>RSS + Swap</h3>
            <div class="chart-container">
                <canvas id="chart-rss"></canvas>
            </div>
        </div>
        <div class="chart-card">
            <h3>CPU %</h3>
            <div class="chart-container">
                <canvas id="chart-cpu"></canvas>
            </div>
        </div>
        <div class="chart-card">
            <h3>Disk IO (bytes/s)</h3>
            <div class="chart-container">
                <canvas id="chart-io"></canvas>
            </div>
        </div>
    </div>

    <script>
        window.allSnapshots = [];
        let currentFrame = 0;
        let playing = false;
        let speedFps = 5;

        const charts = {};

        function initCharts() {
            const memCtx = document.getElementById('chart-memory').getContext('2d');
            charts.memory = new Chart(memCtx, {
                type: 'line',
                data: { labels: [], datasets: [] },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    interaction: { mode: 'index', intersect: false },
                    plugins: { legend: { labels: { color: '#9ca3af' } } },
                    scales: {
                        y: { stacked: true, ticks: { color: '#9ca3af' }, grid: { color: '#374151' } },
                        x: { ticks: { color: '#9ca3af' }, grid: { color: '#374151' } }
                    }
                }
            });

            const rssCtx = document.getElementById('chart-rss').getContext('2d');
            charts.rss = new Chart(rssCtx, {
                type: 'line',
                data: { labels: [], datasets: [] },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    interaction: { mode: 'index', intersect: false },
                    plugins: { legend: { labels: { color: '#9ca3af' } } },
                    scales: {
                        y: { ticks: { color: '#9ca3af' }, grid: { color: '#374151' } },
                        x: { ticks: { color: '#9ca3af' }, grid: { color: '#374151' } }
                    }
                }
            });

            const cpuCtx = document.getElementById('chart-cpu').getContext('2d');
            charts.cpu = new Chart(cpuCtx, {
                type: 'line',
                data: { labels: [], datasets: [] },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    interaction: { mode: 'index', intersect: false },
                    plugins: { legend: { display: false } },
                    scales: {
                        y: { max: 100, ticks: { color: '#9ca3af' }, grid: { color: '#374151' } },
                        x: { ticks: { color: '#9ca3af' }, grid: { color: '#374151' } }
                    }
                }
            });

            const ioCtx = document.getElementById('chart-io').getContext('2d');
            charts.io = new Chart(ioCtx, {
                type: 'bar',
                data: { labels: [], datasets: [] },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    interaction: { mode: 'index', intersect: false },
                    plugins: { legend: { labels: { color: '#9ca3af' } } },
                    scales: {
                        x: { stacked: true, ticks: { color: '#9ca3af' }, grid: { color: '#374151' } },
                        y: { stacked: true, ticks: { color: '#9ca3af' }, grid: { color: '#374151' } }
                    }
                }
            });
        }

        function formatBytes(bytes) {
            if (bytes < 1024) return bytes + ' B';
            if (bytes < 1024**2) return (bytes/1024).toFixed(1) + ' KiB';
            if (bytes < 1024**3) return (bytes/1024**2).toFixed(1) + ' MiB';
            return (bytes/1024**3).toFixed(1) + ' GiB';
        }

        function formatTime(seconds) {
            const m = Math.floor(seconds / 60);
            const s = Math.floor(seconds % 60);
            return `${m}:${s.toString().padStart(2, '0')}`;
        }

        function updateAllCharts(snapshots) {
            if (!snapshots.length) return;

            const times = snapshots.map(s => formatTime(s.elapsed_sec));

            // Memory breakdown
            const hot  = snapshots.map(s => (s.summary.hot.bytes / (2**30)));
            const warm = snapshots.map(s => (s.summary.warm.bytes / (2**30)));
            const cold = snapshots.map(s => (s.summary.cold.bytes / (2**30)));
            const idle = snapshots.map(s => (s.summary.idle.bytes / (2**30)));

            charts.memory.data.labels = times;
            charts.memory.data.datasets = [
                { label: 'Idle', data: idle, borderColor: '#6b7280', fill: true, stack: 'mem', backgroundColor: 'rgba(107,114,128,0.3)' },
                { label: 'Cold', data: cold, borderColor: '#3b82f6', fill: true, stack: 'mem', backgroundColor: 'rgba(59,130,246,0.3)' },
                { label: 'Warm', data: warm, borderColor: '#f97316', fill: true, stack: 'mem', backgroundColor: 'rgba(249,115,22,0.3)' },
                { label: 'Hot',  data: hot,  borderColor: '#ef4444', fill: true, stack: 'mem', backgroundColor: 'rgba(239,68,68,0.3)' }
            ];
            charts.memory.options.scales.y.title = { display: true, text: 'GiB', color: '#9ca3af' };
            charts.memory.update();

            // RSS + Swap
            const rss  = snapshots.map(s => (s.vm_rss_kb / (1024**2)));
            const swap = snapshots.map(s => (s.vm_swap_kb / (1024**2)));
            const hasSwap = swap.some(v => v > 0);

            charts.rss.data.labels = times;
            charts.rss.data.datasets = [
                { label: 'RSS', data: rss, borderColor: '#22c55e', fill: false, tension: 0.1 }
            ];
            if (hasSwap) {
                charts.rss.data.datasets.push({ label: 'VmSwap', data: swap, borderColor: '#a855f7', fill: false, tension: 0.1 });
            }
            charts.rss.update();

            // CPU %
            const cpu = snapshots.map(s => s.cpu_pct !== null ? s.cpu_pct : null);
            charts.cpu.data.labels = times;
            charts.cpu.data.datasets = [
                { label: 'CPU %', data: cpu, borderColor: '#06b6d4', fill: true, backgroundColor: 'rgba(6,182,212,0.2)', tension: 0.1, spanGaps: false }
            ];
            charts.cpu.update();

            // IO
            const ioRead  = snapshots.map(s => (s.io_read_bps || 0));
            const ioWrite = snapshots.map(s => (s.io_write_bps || 0));
            charts.io.data.labels = times;
            charts.io.data.datasets = [
                { label: 'Read', data: ioRead, backgroundColor: '#14b8a6' },
                { label: 'Write', data: ioWrite, backgroundColor: '#f59e0b' }
            ];
            charts.io.update();
        }

        function updateSummary(snapshots) {
            if (!snapshots.length) return;

            // Peak hot+warm
            let peakHotWarm = 0;
            for (const s of snapshots) {
                const hw = (s.summary.hot.bytes + s.summary.warm.bytes) / (2**30);
                peakHotWarm = Math.max(peakHotWarm, hw);
            }

            // Steady-state: median of last 30%
            const steadyStart = Math.floor(snapshots.length * 0.7);
            let steadyValues = [];
            for (let i = steadyStart; i < snapshots.length; i++) {
                const hw = (snapshots[i].summary.hot.bytes + snapshots[i].summary.warm.bytes) / (2**30);
                steadyValues.push(hw);
            }
            steadyValues.sort((a, b) => a - b);
            const steadyHotWarm = steadyValues.length > 0 ? steadyValues[Math.floor(steadyValues.length / 2)] : 0;

            // Peak CPU
            let peakCpu = 0;
            for (const s of snapshots) {
                if (s.cpu_pct !== null) peakCpu = Math.max(peakCpu, s.cpu_pct);
            }

            // Avg IO (skip first 3 snapshots)
            let avgRead = 0, avgWrite = 0, count = 0;
            for (let i = 3; i < snapshots.length; i++) {
                if (snapshots[i].io_read_bps > 0 || snapshots[i].io_write_bps > 0) {
                    avgRead += snapshots[i].io_read_bps;
                    avgWrite += snapshots[i].io_write_bps;
                    count++;
                }
            }
            if (count > 0) {
                avgRead /= count;
                avgWrite /= count;
            }

            document.getElementById('summary-peak').textContent = peakHotWarm.toFixed(2) + ' GiB';
            document.getElementById('summary-steady').textContent = steadyHotWarm.toFixed(2) + ' GiB';
            document.getElementById('summary-cpu').textContent = peakCpu.toFixed(1) + '%';
            document.getElementById('summary-read').textContent = formatBytes(avgRead);
            document.getElementById('summary-write').textContent = formatBytes(avgWrite);
        }

        function setFrame(n) {
            currentFrame = Math.max(0, Math.min(n, window.allSnapshots.length));
            const slice = window.allSnapshots.slice(0, currentFrame);
            updateAllCharts(slice);
            updateSummary(slice);

            document.getElementById('scrubber').value = currentFrame;
            document.getElementById('time-display').textContent = currentFrame > 0 ? formatTime(window.allSnapshots[currentFrame - 1].elapsed_sec) : '0:00';
            document.getElementById('snap-display').textContent = currentFrame;
            document.getElementById('snap-total').textContent = window.allSnapshots.length;
        }

        function togglePlay() {
            if (playing) {
                playing = false;
                document.getElementById('btn-play').textContent = '▶ Play';
            } else {
                playing = true;
                currentFrame = 0;
                document.getElementById('btn-play').textContent = '⏸ Pause';
                animTick();
            }
        }

        function resetAnimation() {
            playing = false;
            currentFrame = 0;
            document.getElementById('btn-play').textContent = '▶ Play';
            setFrame(0);
        }

        function setSpeed() {
            const val = document.getElementById('speed-select').value;
            speedFps = parseInt(val);
        }

        function animTick() {
            if (!playing) return;
            currentFrame = Math.min(currentFrame + 1, window.allSnapshots.length);
            setFrame(currentFrame);
            if (currentFrame < window.allSnapshots.length) {
                setTimeout(animTick, 1000 / speedFps);
            } else {
                playing = false;
                document.getElementById('btn-play').textContent = '▶ Play';
            }
        }

        function scrubberChanged() {
            currentFrame = parseInt(document.getElementById('scrubber').value);
            setFrame(currentFrame);
            playing = false;
            document.getElementById('btn-play').textContent = '▶ Play';
        }

        async function loadData() {
            try {
                const resp = await fetch('/api/data');
                const data = await resp.json();
                window.allSnapshots = data.snapshots || [];
                document.getElementById('snap-total').textContent = window.allSnapshots.length;
                document.getElementById('scrubber').max = window.allSnapshots.length;

                const info = window.allSnapshots.length > 0 ? window.allSnapshots[0] : {};
                document.getElementById('subtitle').textContent = `${info.comm || '?'} (PID ${info.pid || '?'}) • ${data.file}`;
                document.getElementById('file-info').textContent = `File: ${data.file}`;

                const badge = data.done ? 'Complete' : 'LIVE ●';
                const badgeClass = data.done ? 'status-done' : 'status-live';
                document.getElementById('status-badge').textContent = badge;
                document.getElementById('status-badge').className = 'status-badge ' + badgeClass;

                if (currentFrame === 0) {
                    setFrame(window.allSnapshots.length);
                }

                if (!data.done) {
                    setTimeout(loadData, 5000);
                }
            } catch (e) {
                console.error('Error loading data:', e);
                setTimeout(loadData, 5000);
            }
        }

        document.addEventListener('DOMContentLoaded', () => {
            initCharts();
            loadData();
        });
    </script>
</body>
</html>
'''

def load_snapshots(file_path):
    """Load all snapshots from JSONL file."""
    snapshots = []
    try:
        with open(file_path, 'r') as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        snapshots.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
    except OSError as e:
        print(f"[!] Error reading {file_path}: {e}", file=sys.stderr)
    return snapshots

def is_file_done(file_path, threshold_sec=15):
    """Check if file is no longer being written (mtime unchanged for >threshold_sec)."""
    try:
        current_mtime = os.path.getmtime(file_path)
        elapsed = time.time() - current_mtime
        return elapsed > threshold_sec
    except OSError:
        return True

@app.route('/')
def index():
    return render_template_string(HTML_TEMPLATE)

@app.route('/api/data')
def api_data():
    """Return all snapshot data and status."""
    snapshots = load_snapshots(g_data['file_path'])
    done = is_file_done(g_data['file_path'])

    return jsonify({
        'snapshots': snapshots,
        'done': done,
        'file': g_data['file_path'],
        'snapshot_count': len(snapshots)
    })

@app.route('/api/status')
def api_status():
    """Lightweight poll for snapshot count and done status."""
    try:
        with open(g_data['file_path'], 'r') as f:
            count = sum(1 for line in f if line.strip())
    except OSError:
        count = 0

    done = is_file_done(g_data['file_path'])
    return jsonify({'snapshot_count': count, 'done': done})

def find_latest_output():
    """Find latest JSONL in output/ directory."""
    output_dir = Path(_SCRIPT_DIR) / 'output'
    if not output_dir.exists():
        return None

    jsonl_files = list(output_dir.glob('ts_pid*.jsonl'))
    if not jsonl_files:
        return None

    return str(max(jsonl_files, key=lambda p: p.stat().st_mtime))

def main():
    parser = argparse.ArgumentParser(description='DAMON web dashboard')
    parser.add_argument('--file', type=str, help='Path to JSONL file')
    parser.add_argument('--port', type=int, default=8080, help='Port (default: 8080)')
    parser.add_argument('--watch', action='store_true', help='Watch for new data')
    args = parser.parse_args()

    file_path = args.file
    if not file_path:
        file_path = find_latest_output()
        if not file_path:
            print("ERROR: No JSONL file found. Use --file or run timeseries-pid first.", file=sys.stderr)
            sys.exit(1)

    file_path = str(Path(file_path).resolve())
    if not os.path.isfile(file_path):
        print(f"ERROR: File not found: {file_path}", file=sys.stderr)
        sys.exit(1)

    g_data['file_path'] = file_path

    url = f'http://localhost:{args.port}/'
    print(f"[*] Dashboard: {url}", file=sys.stderr)
    print(f"    Ctrl+C to stop.", file=sys.stderr)

    try:
        app.run(host='127.0.0.1', port=args.port, debug=False)
    except KeyboardInterrupt:
        print("\n[*] Stopped.", file=sys.stderr)

if __name__ == '__main__':
    main()
