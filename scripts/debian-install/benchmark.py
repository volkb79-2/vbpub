#!/usr/bin/env python3
"""
Swap Performance Benchmark Script
==================================

Comprehensive benchmark tool for testing swap configurations on Debian 12/13 systems.

IMPORTANT RECOMMENDATION
------------------------
**Always use ZSWAP over ZRAM** - Based on extensive testing documented in chat-merged.md:
- ZSWAP provides automatic LRU-based hot/cold page separation
- Cold pages are evicted to disk, keeping RAM for active data
- ZRAM pages "stick" forever, wasting RAM with cold data
- ZSWAP shrinker (kernel 6.8+) prevents OOM conditions

There is no use case where ZRAM is better than ZSWAP for general-purpose systems.

OVERVIEW
--------
This tool provides both synthetic and semi-realistic performance testing for:
- Block size I/O performance (matching vm.page-cluster values)
- Compression algorithms (lz4, zstd, lzo-rle)
- Memory allocators (zsmalloc, z3fold, zbud)
- Concurrency with multiple swap devices
- ZRAM vs ZSWAP memory-only comparison

TEST TYPES
----------
1. **Block Size × Concurrency Matrix Test** (REALISTIC - PRIMARY TEST)
   - Tests all combinations of block sizes (4KB-128KB) and concurrency levels (1-8)
   - Uses mixed random read/write (rw=randrw) for realistic swap simulation
   - Identifies optimal configuration for both throughput and latency
   - Replaces individual block size and concurrency tests
   - Critical finding: numjobs parameter is essential for parallel device testing
   
2. **Block Size Tests** [DEPRECATED - use matrix test]
   - Tests I/O performance with different block sizes (4KB-128KB)
   - Matches vm.page-cluster settings (0=4KB, 1=8KB, 2=16KB, 3=32KB, 4=64KB, 5=128KB)
   - Uses fio for accurate I/O measurement
   - Measures sequential read/write throughput and latency
   
3. **Compression Tests** (SEMI-REALISTIC)
   - Tests different compression algorithms with memory workloads
   - Creates actual memory pressure to trigger swapping
   - Measures compression ratio and performance
   - Tests with random, zero-filled, and pattern data
   - Recommendation: lz4 for most cases (fast), zstd for low RAM (better compression)
   
4. **Allocator Tests** (REALISTIC)
   - Tests zsmalloc (~90% efficiency), z3fold (~75%), zbud (~50%)
   - Measures actual memory usage vs theoretical
   - Identifies fragmentation characteristics
   
5. **Concurrency Tests** [DEPRECATED - use matrix test]
   - Tests multiple swap files with parallel I/O
   - Measures throughput scaling with 1-8 files
   - Identifies optimal number of concurrent swap devices
   
6. **Memory-Only Comparison** (REALISTIC)
   - Compares ZRAM vs ZSWAP without disk backing
   - Measures latency differences
   - Tests with real application-like workloads
   - Note: ZSWAP is always recommended over ZRAM

INTERPRETATION GUIDE
-------------------
**Matrix Test Results (RECOMMENDED):**
- Comprehensive view of block size × concurrency interactions
- Use concurrency=1 results to find optimal vm.page-cluster
- Use higher concurrency results to optimize for throughput
- Mixed random I/O pattern represents real swap behavior

**Block Size Results [DEPRECATED]:**
- Higher throughput is better
- Lower latency is better  
- Match block size to storage type (SSD: 32-64KB, HDD: 64-128KB)
- vm.page-cluster should match optimal block size
- NOTE: Use matrix test instead for comprehensive analysis

**Compression Results:**
- Higher compression ratio = more effective memory extension
- lz4: Fastest, moderate compression (2-2.5x typical)
- zstd: Slower, better compression (2.5-3.5x typical)
- lzo-rle: Fast, moderate compression (2-2.3x typical)
- Choose based on CPU availability vs memory constraints

**Allocator Results:**
- zsmalloc: Best compression, higher CPU overhead, recommended for low RAM
- z3fold: Balanced, good for general use
- zbud: Lowest CPU, but 50% overhead, use when CPU is bottleneck

**Concurrency Results [DEPRECATED]:**
- Throughput should scale linearly up to number of CPU cores
- Optimal file count typically matches or exceeds core count
- Default 8 files is good for most systems
- NOTE: Use matrix test instead for comprehensive analysis

USE CASES COVERED
----------------
✓ SSD optimization (find optimal block size)
✓ HDD optimization (larger block sizes)  
✓ Low RAM systems (best compression algorithm/allocator)
✓ High memory pressure (concurrency scaling)
✓ CPU-constrained systems (allocator selection)
✓ Quick vs thorough compression tradeoff

USE CASES NOT COVERED  
--------------------
✗ Real application-specific workloads (use application benchmarks)
✗ Long-term fragmentation effects (would require extended testing)
✗ Network-based swap (NFS, iSCSI)
✗ Hibernation performance
✗ Mixed workload interactions

SYNTHETIC VS REALISTIC
---------------------
**Realistic Tests (RECOMMENDED):**
- Matrix Test: Mixed random I/O representing real swap patterns
- Allocator: Actual ZRAM operation under memory pressure
- Memory-only: Actual swap cache behavior

**Semi-Realistic Tests:**
- Compression: Uses memory pressure but with controlled data patterns
- Good for comparing algorithms

**Deprecated Tests:**
- Block size I/O: Pure sequential I/O, not representative of random access patterns
- Concurrency: Sequential patterns don't match real swap behavior
- Use matrix test instead for realistic testing

DEPENDENCIES
-----------
- python3
- fio (for I/O benchmarking): apt install fio
- Root privileges (for system configuration)
- gawk (for calculations)

EXAMPLES
--------
# Test all configurations (recommended - includes matrix test)
sudo ./benchmark.py --test-all

# Test comprehensive block size × concurrency matrix (best single test)
sudo ./benchmark.py --test-matrix

# Test compressors only
sudo ./benchmark.py --test-compressors

# Test all allocators  
sudo ./benchmark.py --test-allocators

# [DEPRECATED] Test specific block size - use --test-matrix instead
sudo ./benchmark.py --block-size 64

# [DEPRECATED] Test concurrency scaling - use --test-matrix instead
sudo ./benchmark.py --test-concurrency 8

# Compare ZRAM vs ZSWAP
sudo ./benchmark.py --compare-memory-only

# Export results
sudo ./benchmark.py --test-all --output results.json --shell-config optimal.conf
"""

import argparse
import glob
import json
import os
import math
import shutil
import socket
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

# Optional telegram client import (for --telegram flag).
# telegram_client.py lives in the sibling scripts/telegram/ directory (moved
# there so it has its own tests/run-gate) - not scripts/debian-install/ itself.
try:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "telegram"))
    from telegram_client import TelegramClient
    TELEGRAM_AVAILABLE = True
except ImportError:
    TELEGRAM_AVAILABLE = False
    TelegramClient = None

# Optional matplotlib import (for chart generation)
try:
    import matplotlib
    matplotlib.use('Agg')  # Non-interactive backend
    import matplotlib.pyplot as plt
    MATPLOTLIB_AVAILABLE = True
except ImportError:
    MATPLOTLIB_AVAILABLE = False
    plt = None

# Colors for output
class Colors:
    GREEN = '\033[0;32m'
    YELLOW = '\033[1;33m'
    RED = '\033[0;31m'
    BLUE = '\033[0;34m'
    CYAN = '\033[0;36m'
    BOLD = '\033[1m'
    NC = '\033[0m'

# Benchmark configuration constants
COMPRESSION_TEST_SIZE_MB = 64  # Default compression test size (reduced for faster benchmarks)
COMPRESSION_MEMORY_PERCENT = 90  # Percentage of test size to allocate (90%)
COMPRESSION_MEMORY_PASSES = 3  # Number of passes over memory to ensure swapping
COMPRESSION_MIN_SWAP_PERCENT = 50  # Minimum expected swap activity (50% of test size)
COMPRESSION_RATIO_MIN = 1.5  # Minimum expected compression ratio
COMPRESSION_RATIO_MAX = 4.0  # Maximum typical compression ratio
COMPRESSION_RATIO_SUSPICIOUS = 10.0  # Ratio above this is suspicious
MIN_VALID_COMPRESSION_RATIO = 1.1  # Minimum ratio to consider valid (below this indicates test failure)
ZRAM_STABILIZATION_DELAY_SEC = 2  # Time to wait after ZRAM cleanup for system stabilization


def _compression_ratio_expectations_for_pattern(pattern: Optional[int]):
    """Return (min_expected, max_typical, suspicious_high, label) for a mem_pressure pattern.

    mem_pressure pattern_type:
      0=mixed, 1=random, 2=zeros, 3=sequential
    """

    # Defaults for historical behavior / unknown pattern.
    if pattern is None:
        return (COMPRESSION_RATIO_MIN, COMPRESSION_RATIO_MAX, COMPRESSION_RATIO_SUSPICIOUS, "typical")

    # Random: should be near 1.0 (low compressibility).
    if pattern == 1:
        return (0.95, 1.30, 2.00, "random")

    # Mixed: broad, can be moderately compressible.
    if pattern == 0:
        return (1.20, 4.00, 8.00, "mixed")

    # Zeros: extremely compressible, very high ratios are expected.
    if pattern == 2:
        return (5.00, 50.00, 200.00, "zeros")

    # Sequential: often quite compressible with LZ4 (higher ratios can be normal).
    if pattern == 3:
        return (1.50, 20.00, 100.00, "sequential")

    return (COMPRESSION_RATIO_MIN, COMPRESSION_RATIO_MAX, COMPRESSION_RATIO_SUSPICIOUS, "typical")

# Optional: disk-backed guard swapfile for benchmarks (prevents OOM during aggressive tests)
DEFAULT_GUARD_SWAP_MB = 1024
DEFAULT_GUARD_SWAP_PRIO = -10
DEFAULT_ZRAM_PRIO = 100

# Memory pressure test constants
STRESS_NG_TIMEOUT_SEC = 15  # Timeout for stress-ng memory allocation
STRESS_NG_WAIT_SEC = 20  # Maximum wait time for stress-ng process
MEMORY_ACCESS_STEP_SIZE = 65536  # 64KB steps for memory access patterns
COMPRESSION_TEST_TIMEOUT_SEC = 180  # Maximum time per compression test (3 minutes, optimized from 5 minutes)

# FIO test configuration constants
FIO_TEST_FILE_SIZE = '1G'  # Test file size for fio benchmarks

# GPT partition type GUID for Linux swap (used by create-swap-partitions.sh and setup-swap.sh)
SWAP_GPT_GUID = '0657fd6d-a4ab-43c4-84e5-0933c84b4f4f'

# System RAM tier thresholds for auto-detection
RAM_TIER_LOW_GB = 4    # Systems below this use ZRAM
RAM_TIER_HIGH_GB = 16  # Systems above this use ZSWAP (but so do medium tier)

# If mem_locker triggers OOM kills, disable it for the remainder of the run.
DISABLE_MEM_LOCKER = False

def format_timestamp():
    """Return formatted timestamp for logging"""
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')
   
def log_info_ts(msg):
    """Log info message with timestamp"""
    print(f"{Colors.GREEN}[INFO]{Colors.NC} {format_timestamp()} {msg}", flush=True)

def log_warn_ts(msg):
    """Log warning message with timestamp"""
    print(f"{Colors.YELLOW}[WARN]{Colors.NC} {format_timestamp()} {msg}", flush=True)

def log_step_ts(msg):
    """Log step message with timestamp"""
    print(f"{Colors.BLUE}[STEP]{Colors.NC} {format_timestamp()} {msg}", flush=True)

def log_debug_ts(msg):
    """Log debug message with timestamp"""
    print(f"{Colors.CYAN}[DEBUG]{Colors.NC} {format_timestamp()} {msg}", flush=True)

def log_success_ts(msg):
    """Log success message with timestamp"""
    print(f"{Colors.GREEN}[SUCCESS]{Colors.NC} {format_timestamp()} {msg}", flush=True)


def _append_warning(results: dict, msg: str) -> None:
    """Attach a warning to a result dict without clobbering existing warnings."""
    if not msg:
        return

    try:
        warnings = results.get('warnings')
        if isinstance(warnings, list):
            warnings.append(msg)
            return
        if warnings is None:
            results['warnings'] = [msg]
            return
    except Exception:
        pass

    prev = results.get('warning')
    if isinstance(prev, str) and prev.strip():
        results['warning'] = f"{prev.strip()}; {msg}"
    else:
        results['warning'] = msg


def recommend_swap_stripe_width(cpu_cores: int | None, disk_swap_total_gb: int | None = None) -> int:
    """Heuristic for swap striping width (number of parallel swap devices).

    IMPORTANT: This is *not* derived from fio numjobs. fio's numjobs represents I/O concurrency
    against a single target file, not the number of swap devices.

    Mirrors the default policy in setup-swap.sh.
    """
    cores = int(cpu_cores or 0)
    if cores >= 16:
        width = 16
    elif cores >= 8:
        width = 8
    elif cores >= 4:
        width = 4
    else:
        width = 2

    if disk_swap_total_gb is not None:
        try:
            cap = int(disk_swap_total_gb)
            if cap > 0 and width > cap:
                width = cap
        except Exception:
            pass

    if width < 1:
        width = 1
    return int(width)


def log_info(msg):
    print(f"{Colors.GREEN}[INFO]{Colors.NC} {msg}", flush=True)

def log_debug(msg):
    print(f"{Colors.CYAN}[DEBUG]{Colors.NC} {msg}", flush=True)

def log_warn(msg):
    print(f"{Colors.YELLOW}[WARN]{Colors.NC} {msg}", flush=True)

def log_error(msg):
    print(f"{Colors.RED}[ERROR]{Colors.NC} {msg}", file=sys.stderr, flush=True)

def log_step(msg):
    print(f"{Colors.BLUE}[STEP]{Colors.NC} {msg}", flush=True)

def log_success(msg):
    print(f"{Colors.GREEN}[SUCCESS]{Colors.NC} {msg}", flush=True)



def check_root():
    """Check if running as root"""
    if os.geteuid() != 0:
        log_error("This script must be run as root")
        sys.exit(1)

def check_dependencies():
    """Check required dependencies"""
    missing = []
    
    # Check for fio
    try:
        subprocess.run(['fio', '--version'], capture_output=True, check=True)
    except (subprocess.CalledProcessError, FileNotFoundError):
        missing.append('fio')
    
    # Check for awk
    try:
        subprocess.run(['awk', '--version'], capture_output=True, check=True)
    except (subprocess.CalledProcessError, FileNotFoundError):
        missing.append('gawk')
    
    if missing:
        log_error(f"Missing dependencies: {', '.join(missing)}")
        log_error("Install with: apt install " + " ".join(missing))
        sys.exit(1)

def compile_c_programs():
    """
    Compile mem_locker, mem_pressure, and latency measurement C programs at runtime.
    Total: 5 programs (mem_locker, mem_pressure, mem_write_bench, mem_read_bench, mem_mixed_bench)
    Returns True if successful, False otherwise.
    """
    script_dir = Path(__file__).parent
    programs = {
        'mem_locker': script_dir / 'mem_locker.c',
        'mem_pressure': script_dir / 'mem_pressure.c',
        'mem_write_bench': script_dir / 'mem_write_bench.c',
        'mem_read_bench': script_dir / 'mem_read_bench.c',
        'mem_mixed_bench': script_dir / 'mem_mixed_bench.c'
    }
    
    log_info_ts("Compiling C memory management and latency measurement programs...")
    
    for prog_name, source_file in programs.items():
        if not source_file.exists():
            log_error(f"Source file not found: {source_file}")
            return False
        
        output_file = script_dir / prog_name
        
        log_info(f"Compiling {prog_name}...")
        try:
            result = subprocess.run(
                ['gcc', '-o', str(output_file), str(source_file), '-Wall', '-O2'],
                capture_output=True,
                text=True,
                check=True,
                timeout=30
            )
            log_info(f"✓ {prog_name} compiled successfully")
        except subprocess.TimeoutExpired:
            log_error(f"Compilation of {prog_name} timed out")
            return False
        except subprocess.CalledProcessError as e:
            log_error(f"Failed to compile {prog_name}")
            log_error(f"Error: {e.stderr}")
            return False
        except FileNotFoundError:
            log_error("gcc not found - install with: apt install gcc")
            return False
    
    log_info_ts("✓ All C programs compiled successfully")
    return True

def get_memory_info():
    """
    Get detailed memory information for test planning.
    Returns dict with total_mb, available_mb, free_mb
    """
    info = {}
    
    with open('/proc/meminfo') as f:
        for line in f:
            if 'MemTotal:' in line:
                info['total_mb'] = int(line.split()[1]) // 1024
            elif 'MemAvailable:' in line:
                info['available_mb'] = int(line.split()[1]) // 1024
            elif 'MemFree:' in line:
                info['free_mb'] = int(line.split()[1]) // 1024
    
    return info


def set_oom_score_adj(value: int) -> None:
    """Best-effort: make current process more likely to be OOM-killed (positive) or protected (negative)."""
    try:
        with open('/proc/self/oom_score_adj', 'w') as f:
            f.write(str(int(value)))
    except Exception:
        pass

def calculate_memory_distribution(test_size_mb):
    """
    Calculate memory distribution for tests.
    
    Strategy:
    - Reserve test_size_mb for the actual ZRAM/ZSWAP test
    - Reserve 500MB safety buffer for system operations
    - Lock the rest to prevent it from swapping
    
    Returns: (test_size_mb, lock_size_mb, available_mb)
    """
    mem_info = get_memory_info()
    total_mb = mem_info['total_mb']
    available_mb = mem_info['available_mb']
    
    log_debug_ts(f"Memory: Total={total_mb}MB, Available={available_mb}MB")
    
    # On high-RAM systems, lock more memory to force swapping
    system_info = get_system_info()
    ram_gb = system_info.get('ram_gb', 8)

    # Safety buffer for system
    # On small-RAM systems, keep more headroom to avoid killing SSH/VNC/systemd.
    SAFETY_BUFFER_MB = 1024 if ram_gb <= 8 else 500
    
    aggressive = os.environ.get('BENCHMARK_AGGRESSIVE', '').lower() == 'yes'
    if (ram_gb <= 8) and (not aggressive):
        # On smaller systems, mem_locker + mem_pressure can OOM-kill the entire unit.
        # Default to *no* mem_locker unless explicitly requested.
        lock_percent = 0.0
        lock_size_mb = 0
    elif aggressive and ram_gb >= 16:
        # Aggressive mode on larger boxes only
        lock_percent = 0.85
        lock_size_mb = max(0, int(available_mb * lock_percent) - test_size_mb)
    else:
        # Conservative default: never try to lock the world
        lock_percent = 0.50
        lock_size_mb = max(0, int(available_mb * lock_percent) - test_size_mb)

    # Hard cap: ensure safety buffer remains available
    lock_size_mb = min(lock_size_mb, max(0, available_mb - test_size_mb - SAFETY_BUFFER_MB))
    
    log_info_ts(f"Memory distribution: Test={test_size_mb}MB, Lock={lock_size_mb}MB, Buffer={SAFETY_BUFFER_MB}MB")
    
    return test_size_mb, lock_size_mb, available_mb

def run_with_timeout(cmd, timeout_sec, description="Command"):
    """
    Run a command with timeout.
    Returns (success, output, error_msg)
    """
    log_debug_ts(f"{description}: timeout={timeout_sec}s")
    
    try:
        result = subprocess.run(
            cmd,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout_sec
        )
        return (result.returncode == 0, result.stdout, result.stderr)
    except subprocess.TimeoutExpired:
        log_warn_ts(f"{description} timed out after {timeout_sec}s")
        return (False, "", f"Timeout after {timeout_sec}s")
    except Exception as e:
        log_error(f"{description} failed: {e}")
        return (False, "", str(e))

def run_command(cmd, check=True):
    """Run shell command and return output"""
    try:
        result = subprocess.run(
            cmd,
            shell=True,
            capture_output=True,
            text=True,
            check=check
        )
        return result.stdout.strip()
    except subprocess.CalledProcessError as e:
        if check:
            log_error(f"Command failed: {cmd}")
            log_error(f"Error: {e.stderr}")
            raise
        return ""


def list_active_swaps():
    """Return a list of active swap devices as [{'name': str, 'prio': int}]."""
    swaps = []
    try:
        out = run_command('swapon --noheadings --raw --show=NAME,PRIO', check=False)
        for line in (out or "").splitlines():
            parts = line.split()
            if not parts:
                continue
            name = parts[0]
            prio = int(parts[1]) if len(parts) > 1 and parts[1].lstrip('-').isdigit() else 0
            swaps.append({'name': name, 'prio': prio})
    except Exception:
        return []
    return swaps


def swapoff_all_except(keep_names):
    """Best-effort: swapoff everything except devices in keep_names."""
    keep = set(keep_names)
    for s in list_active_swaps():
        name = s.get('name')
        if not name or name in keep:
            continue
        try:
            subprocess.run(['swapoff', name], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass


def restore_swaps(swaps):
    """Best-effort: restore swaps previously returned by list_active_swaps()."""
    for s in swaps or []:
        name = s.get('name')
        prio = s.get('prio', 0)
        if not name:
            continue
        # Caller may have enabled /dev/zram0 separately.
        if name == '/dev/zram0':
            continue
        try:
            subprocess.run(['swapon', '-p', str(int(prio)), name], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass


def _debug_enabled() -> bool:
    return os.environ.get('DEBUG', '').strip().lower() in ('1', 'true', 'yes', 'y', 'on')


def _read_proc_swaps_used_kb(name: str) -> int:
    try:
        with open('/proc/swaps', 'r') as f:
            for line in f:
                if line.startswith('Filename'):
                    continue
                parts = line.split()
                if len(parts) >= 4 and parts[0] == name:
                    return int(parts[3])
    except Exception:
        pass
    return 0


def ensure_guard_swapfile(size_mb: int = DEFAULT_GUARD_SWAP_MB) -> str | None:
    """Ensure a disk-backed guard swapfile is active; returns its path or None.

    Controlled via env:
      - BENCHMARK_GUARD_SWAP: yes/no (default yes)
      - BENCHMARK_GUARD_SWAP_MB: integer size (default 1024)
      - BENCHMARK_GUARD_SWAP_PATH: swapfile path
      - BENCHMARK_GUARD_SWAP_PRIO: swapon priority (default -10)
    """
    enabled = os.environ.get('BENCHMARK_GUARD_SWAP', 'yes').strip().lower() not in ('0', 'false', 'no', 'off')
    if not enabled:
        return None

    try:
        size_mb = int(os.environ.get('BENCHMARK_GUARD_SWAP_MB', str(int(size_mb))))
    except Exception:
        size_mb = int(size_mb)

    path = os.environ.get('BENCHMARK_GUARD_SWAP_PATH', '/var/lib/vbpub/benchmark/guard.swap')
    prio = os.environ.get('BENCHMARK_GUARD_SWAP_PRIO', str(DEFAULT_GUARD_SWAP_PRIO))
    try:
        prio = int(prio)
    except Exception:
        prio = DEFAULT_GUARD_SWAP_PRIO

    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if (not os.path.exists(path)) or (os.path.getsize(path) < size_mb * 1024 * 1024):
            log_info_ts(f"Creating guard swapfile {path} ({size_mb}MB)...")
            subprocess.run(['swapoff', path], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            subprocess.run(['rm', '-f', path], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            subprocess.run(['fallocate', '-l', f'{size_mb}M', path], check=True)
            os.chmod(path, 0o600)
            subprocess.run(['mkswap', '-f', path], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        # Ensure active with low priority.
        active = {s.get('name'): s.get('prio') for s in list_active_swaps()}
        if path not in active:
            log_info_ts(f"Enabling guard swapfile (prio {prio})...")
            subprocess.run(['swapon', '-p', str(int(prio)), path], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return path
    except Exception as e:
        log_warn_ts(f"Failed to set up guard swapfile: {e}")
        return None


def disable_guard_swapfile(path: str | None) -> None:
    if not path:
        return
    try:
        subprocess.run(['swapoff', path], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass


def _filter_patterns(data_patterns):
    """Filter patterns based on BENCHMARK_PATTERNS='mixed,random,zeros,sequential'."""
    raw = os.environ.get('BENCHMARK_PATTERNS', '').strip().lower()
    if not raw:
        return data_patterns
    wanted = {p.strip() for p in raw.split(',') if p.strip()}
    return [(pid, name) for (pid, name) in data_patterns if name.lower() in wanted]


def get_sysctl_int(name, default=0):
    try:
        val = run_command(f"sysctl -n {name}", check=False)
        return int(val.strip())
    except Exception:
        return int(default)


def set_sysctl_int(name, value):
    try:
        subprocess.run(['sysctl', '-w', f"{name}={int(value)}"], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass

def get_system_info():
    """Get system information"""
    info = {}

    # Hostname
    try:
        info['hostname'] = socket.gethostname() or 'unknown'
    except Exception:
        info['hostname'] = 'unknown'
    
    # RAM
    with open('/proc/meminfo') as f:
        for line in f:
            if 'MemTotal' in line:
                info['ram_kb'] = int(line.split()[1])
                # Convert MemTotal to GiB for reporting/policy decisions (rounded)
                info['ram_gb'] = (info['ram_kb'] + 524287) // (1024 * 1024)
                break
    
    # Available memory
    try:
        with open('/proc/meminfo') as f:
            for line in f:
                if 'MemAvailable' in line:
                    info['available_kb'] = int(line.split()[1])
                    info['available_gb'] = round(info['available_kb'] / 1024 / 1024, 1)
                    break
    except:
        info['available_kb'] = info['ram_kb']
        info['available_gb'] = info['ram_gb']
    
    # CPU
    info['cpu_cores'] = os.cpu_count()

    # CPU model
    try:
        cpu_model = None
        with open('/proc/cpuinfo', 'r') as f:
            for line in f:
                if line.lower().startswith('model name'):
                    cpu_model = line.split(':', 1)[1].strip()
                    break
        if cpu_model:
            info['cpu_model'] = cpu_model
    except Exception:
        pass
    
    # Current page-cluster
    try:
        info['page_cluster'] = int(run_command('sysctl -n vm.page-cluster'))
    except:
        info['page_cluster'] = 3
    
    return info

def calculate_optimal_compression_size(ram_gb, small_tests=False):
    """
    Calculate optimal compression test size based on total system RAM
    
    Scales test size to be appropriate for the system's RAM capacity,
    balancing test thoroughness with execution time.
    
    Args:
        ram_gb: Total system RAM in GB (not available RAM)
        small_tests: If True, use smaller test sizes (64MB max) for quick testing
    
    Returns:
        Test size in MB
    """
    if small_tests:
        # Small tests mode: 64MB for systems with >=8GB RAM, 32MB otherwise
        return 64 if ram_gb >= 8 else 32
    
    # Scale based on RAM to keep tests manageable
    # Smaller systems use proportionally smaller tests to avoid excessive swapping
    if ram_gb <= 8:
        # For 4-8GB systems: 128MB (~1.6-3.1% of RAM)
        return 128
    elif ram_gb <= 16:
        # For 16GB systems: 256MB (~1.6% of RAM)
        return 256
    elif ram_gb <= 32:
        # For 32GB systems: 512MB (~1.6% of RAM)
        return 512
    else:
        # For >32GB systems: 1024MB (cap at 1GB)
        return 1024

def ensure_zram_loaded():
    """Ensure ZRAM kernel module is loaded and device is clean"""
    try:
        # Load zram module
        run_command('modprobe zram', check=False)
        
        # Wait for device to appear
        import time
        for i in range(10):
            if os.path.exists('/dev/zram0'):
                break
            time.sleep(0.1)
        
        if not os.path.exists('/dev/zram0'):
            log_error("ZRAM device /dev/zram0 not found after loading module")
            return False
        
        # Reset any existing zram device completely (swapoff + reset with retries)
        # This is critical - without reset, disksize writes can fail with "Device or resource busy".
        if os.path.exists('/sys/block/zram0/disksize'):
            if not cleanup_zram_aggressive():
                log_error("Failed to cleanup/reset ZRAM device")
                return False
        
        return True
    except Exception as e:
        log_error(f"Failed to ensure ZRAM loaded: {e}")
        return False

def cleanup_zram_aggressive():
    """
    Aggressively clean up ZRAM device with retries.
    Returns True on success, False on failure.
    """
    max_attempts = 5
    for attempt in range(max_attempts):
        try:
            # Disable swap
            subprocess.run(['swapoff', '/dev/zram0'], 
                         stderr=subprocess.DEVNULL, check=False)
            time.sleep(1)  # Wait for kernel to release device
            
            # Reset device
            if os.path.exists('/sys/block/zram0/reset'):
                with open('/sys/block/zram0/reset', 'w') as f:
                    f.write('1\n')
                
                time.sleep(1)  # Wait for reset to complete
                
                # Verify device is clean
                if os.path.exists('/sys/block/zram0/disksize'):
                    with open('/sys/block/zram0/disksize', 'r') as f:
                        disksize = f.read().strip()
                        if disksize == '0':
                            return True
                else:
                    return True
                        
        except Exception as e:
            if attempt < max_attempts - 1:
                log_debug(f"ZRAM cleanup attempt {attempt + 1} failed: {e}, retrying...")
                time.sleep(2 ** attempt)  # Exponential backoff
                continue
            log_error(f"Failed to cleanup ZRAM after {max_attempts} attempts: {e}")
            return False
    
    return False

def cleanup_test_files():
    """Clean up all temporary test files."""
    patterns = [
        '/tmp/fio_*.job',
        '/tmp/benchmark-*.sh',
        '/tmp/ptable-*.dump',
        '/var/tmp/swapfile*',
        # Compiled C programs
        'mem_locker',
        'mem_pressure', 
        'mem_write_bench',
        'mem_read_bench',
        'mem_mixed_bench'
    ]
    
    for pattern in patterns:
        for file in glob.glob(pattern):
            try:
                if os.path.exists(file):
                    os.remove(file)
                    log_debug(f"Cleaned up: {file}")
            except Exception as e:
                log_debug(f"Failed to remove {file}: {e}")

def benchmark_block_size_fio(size_kb, test_file='/tmp/fio_test', runtime_sec=5, pattern='sequential', test_num=None, total_tests=None):
    """
    [DEPRECATED] Benchmark I/O performance with fio (more accurate than dd)
    
    NOTE: This test is deprecated. Use test_blocksize_concurrency_matrix() instead,
    which provides comprehensive coverage with realistic rw=randrw patterns.
    
    This function is maintained for backward compatibility only and uses the
    older sequential write-then-read pattern rather than the newer randrw approach.
    
    Args:
        size_kb: Block size in KB
        test_file: Path to test file
        runtime_sec: Test runtime in seconds (default: 5)
        pattern: 'sequential' or 'random' I/O pattern
        test_num: Current test number (for progress tracking)
        total_tests: Total number of tests (for progress tracking)
    """
    start_time = time.time()
    
    # Log with progress tracking
    progress_str = f"[{test_num}/{total_tests}] " if test_num and total_tests else ""
    log_step_ts(f"{progress_str}Block size test: {size_kb}KB {pattern} (runtime: {runtime_sec}s)")
    
    results = {
        'block_size_kb': size_kb,
        'runtime_sec': runtime_sec,
        'io_pattern': pattern,
        'concurrency': 1,
        'timestamp': datetime.now().isoformat()
    }
    
    # Determine I/O type based on pattern
    if pattern == 'random':
        write_rw = 'randwrite'
        read_rw = 'randread'
    else:  # sequential
        write_rw = 'write'
        read_rw = 'read'
    
    # Sequential or Random write test
    log_info(f"Running fio {pattern} write test...")
    # Use configured test file size to ensure meaningful results
    fio_write = f"""
[global]
ioengine=libaio
direct=1
runtime={runtime_sec}
time_based
size={FIO_TEST_FILE_SIZE}
filename={test_file}

[seqwrite]
rw={write_rw}
bs={size_kb}k
"""
    
    try:
        with open('/tmp/fio_write.job', 'w') as f:
            f.write(fio_write)
        
        # Log fio command at debug level
        log_debug_ts(f"fio command: fio --output-format=json /tmp/fio_write.job")
        
        result = subprocess.run(
            ['fio', '--output-format=json', '/tmp/fio_write.job'],
            capture_output=True,
            text=True,
            timeout=300
        )
        
        if result.returncode == 0:
            data = json.loads(result.stdout)
            write_bw = data['jobs'][0]['write']['bw'] / 1024  # Convert to MB/s
            write_lat = data['jobs'][0]['write']['lat_ns']['mean'] / 1000000  # Convert to ms
            results['write_mb_per_sec'] = round(write_bw, 2)
            results['write_latency_ms'] = round(write_lat, 2)
            log_info(f"  Write: {write_bw:.2f} MB/s, Latency: {write_lat:.2f} ms")
        else:
            log_error(f"Write test exited with code {result.returncode}")
            log_debug(f"Stderr: {result.stderr}")
            results['write_mb_per_sec'] = 0
            results['write_error'] = f'Exit code {result.returncode}'
    except json.JSONDecodeError as e:
        log_error(f"Failed to parse fio JSON output: {e}")
        log_debug(f"Output: {result.stdout[:200]}")
        results['write_mb_per_sec'] = 0
        results['write_error'] = f'JSON parse error: {e}'
    except Exception as e:
        log_error(f"Write test failed: {e}")
        results['write_mb_per_sec'] = 0
        results['write_error'] = str(e)
    
    # Sequential or Random read test
    log_info(f"Running fio {pattern} read test...")
    fio_read = f"""
[global]
ioengine=libaio
direct=1
runtime={runtime_sec}
time_based
size={FIO_TEST_FILE_SIZE}
filename={test_file}

[seqread]
rw={read_rw}
bs={size_kb}k
"""
    
    try:
        with open('/tmp/fio_read.job', 'w') as f:
            f.write(fio_read)
        
        # Clear cache
        run_command('sync && echo 3 > /proc/sys/vm/drop_caches')
        
        log_debug_ts(f"fio command: fio --output-format=json /tmp/fio_read.job")
        
        result = subprocess.run(
            ['fio', '--output-format=json', '/tmp/fio_read.job'],
            capture_output=True,
            text=True,
            timeout=300
        )
        
        if result.returncode == 0:
            data = json.loads(result.stdout)
            read_bw = data['jobs'][0]['read']['bw'] / 1024  # Convert to MB/s
            read_lat = data['jobs'][0]['read']['lat_ns']['mean'] / 1000000  # Convert to ms
            results['read_mb_per_sec'] = round(read_bw, 2)
            results['read_latency_ms'] = round(read_lat, 2)
            log_info(f"  Read: {read_bw:.2f} MB/s, Latency: {read_lat:.2f} ms")
        else:
            log_error(f"Read test exited with code {result.returncode}")
            log_debug(f"Stderr: {result.stderr}")
            results['read_mb_per_sec'] = 0
            results['read_error'] = f'Exit code {result.returncode}'
    except json.JSONDecodeError as e:
        log_error(f"Failed to parse fio JSON output: {e}")
        log_debug(f"Output: {result.stdout[:200]}")
        results['read_mb_per_sec'] = 0
        results['read_error'] = f'JSON parse error: {e}'
    except Exception as e:
        log_error(f"Read test failed: {e}")
        results['read_mb_per_sec'] = 0
        results['read_error'] = str(e)
    
    # Cleanup
    for f in [test_file, '/tmp/fio_write.job', '/tmp/fio_read.job']:
        if os.path.exists(f):
            try:
                os.remove(f)
            except:
                pass
    
    # Log completion time
    elapsed = time.time() - start_time
    results['elapsed_sec'] = round(elapsed, 1)
    log_info(f"✓ Test completed in {elapsed:.1f}s")
    
    return results

def benchmark_compression(compressor, allocator='zsmalloc', size_mb=COMPRESSION_TEST_SIZE_MB, pattern=0, test_num=None, total_tests=None):
    """
    Benchmark compression algorithm with specific allocator
    Tests with semi-realistic memory workload
    
    Args:
        compressor: Compression algorithm (lz4, zstd, lzo-rle)
        allocator: Memory allocator (zsmalloc, z3fold, zbud)
        size_mb: Test size in MB
        pattern: Data pattern (0=mixed, 1=random, 2=zeros, 3=sequential)
        test_num: Current test number for progress tracking
        total_tests: Total number of tests for progress tracking
    """
    start_time = time.time()
    
    # Log with progress tracking
    pattern_names = {0: 'mixed', 1: 'random', 2: 'zeros', 3: 'sequential'}
    pattern_desc = {
        0: 'mixed (default; some compressible, some not)',
        1: 'random (incompressible; worst-case)',
        2: 'zeros (highly compressible; best-case)',
        3: 'sequential (typically compressible)',
    }
    pattern_name = pattern_names.get(pattern, 'unknown')
    progress_str = f"[{test_num}/{total_tests}] " if test_num and total_tests else ""
    log_step_ts(f"{progress_str}Compression test: {compressor} with {allocator} ({pattern_name} data, test size: {size_mb}MB)")
    
    results = {
        'compressor': compressor,
        'allocator': allocator,
        'test_size_mb': size_mb,
        'pattern_id': int(pattern),
        'data_pattern': pattern_name,
        'data_pattern_description': pattern_desc.get(pattern, 'unknown'),
        'timestamp': datetime.now().isoformat()
    }
    
    # Initialize mem_locker_proc to None so it's in scope for cleanup
    mem_locker_proc = None

    # Track system state we may temporarily change
    prior_swaps = None
    prior_swappiness = None
    prior_zswap_enabled = None
    zram_disksize_mb = size_mb
    guard_swap_path = None
    guard_swap_used_start_kb = 0
    zram_mem_limit_mb = 0
    
    try:
        # Snapshot swap state before we start modifying it.
        prior_swaps = list_active_swaps()

        # Ensure ZRAM is loaded and clean
        log_info("Ensuring ZRAM device is clean...")
        if not ensure_zram_loaded():
            results['error'] = "Failed to load/reset ZRAM device"
            return results
        
        # Check if we can set allocator (may not be available)
        if os.path.exists('/sys/block/zram0/mem_pool'):
            try:
                log_info(f"Setting allocator to {allocator}...")
                with open('/sys/block/zram0/mem_pool', 'w') as f:
                    f.write(f'{allocator}\n')
            except:
                log_warn(f"Could not set allocator to {allocator}, using default")
        
        # Set compressor
        if os.path.exists('/sys/block/zram0/comp_algorithm'):
            try:
                log_info(f"Setting compressor to {compressor}...")
                with open('/sys/block/zram0/comp_algorithm', 'w') as f:
                    f.write(f'{compressor}\n')
            except:
                log_warn(f"Could not set compressor to {compressor}, using default")
        
        # Make the ZRAM device large enough that we can *force* meaningful swapping
        # even on 8GB systems without immediately OOM-killing the machine.
        # NOTE: disksize is virtual; it doesn't allocate RAM up-front.
        system_info = get_system_info()
        mem_info = get_memory_info()
        ram_mb = int(mem_info.get('total_mb') or (int(system_info.get('ram_gb', 8)) * 1024))

        # Target about 25% of RAM, but ensure it's also large relative to test size.
        target_zram_mb = max(size_mb, 512, size_mb * 8, int(ram_mb * 0.25))
        # Cap at 50% RAM (and an absolute cap) to avoid pathological settings on huge hosts.
        max_zram_mb = min(8192, max(2048, int(ram_mb * 0.5)))
        zram_disksize_mb = min(target_zram_mb, max_zram_mb)

        # Set size
        size_bytes = zram_disksize_mb * 1024 * 1024
        try:
            log_info(f"Setting disk size to {size_bytes} bytes ({zram_disksize_mb}MB)...")
            with open('/sys/block/zram0/disksize', 'w') as f:
                f.write(str(size_bytes))
        except OSError as e:
            log_error(f"Failed to set ZRAM disk size: {e}")
            results['error'] = f"Failed to set disksize: {e}"
            return results

        # Optional: enable a disk-backed guard swapfile (low priority) to prevent OOM during
        # aggressive tests. ZRAM remains higher priority so we still measure zram behavior.
        guard_swap_path = ensure_guard_swapfile()
        if guard_swap_path:
            guard_swap_used_start_kb = _read_proc_swaps_used_kb(guard_swap_path)

        # IMPORTANT: zswap sits in front of swap devices and can absorb swap-outs
        # before they reach /dev/zram0, making zram0/mm_stat look like "no swap".
        # Temporarily disable zswap so the benchmark measures the zram device itself.
        if os.path.exists('/sys/module/zswap/parameters/enabled'):
            try:
                with open('/sys/module/zswap/parameters/enabled', 'r') as f:
                    prior_zswap_enabled = f.read().strip()
                # Accept Y/N or 1/0 style.
                if prior_zswap_enabled.upper().startswith('Y') or prior_zswap_enabled == '1':
                    log_info("Temporarily disabling zswap for ZRAM benchmark...")
                    with open('/sys/module/zswap/parameters/enabled', 'w') as f:
                        f.write('N\n')
                    time.sleep(0.1)
            except Exception as e:
                log_warn(f"Could not toggle zswap enabled state: {e}")

        # Optional: cap zram RAM usage to avoid OOM on incompressible patterns.
        # mem_limit is in BYTES; 0 means "no limit".
        try:
            if os.path.exists('/sys/block/zram0/mem_limit'):
                # Allow env override per run.
                raw_limit = os.environ.get('BENCHMARK_ZRAM_MEM_LIMIT_MB', '').strip()
                raw_limit_random = os.environ.get('BENCHMARK_ZRAM_MEM_LIMIT_MB_RANDOM', '').strip()
                if raw_limit:
                    zram_mem_limit_mb = int(raw_limit)
                elif pattern == 1 and guard_swap_path:
                    # Random/incompressible data: keep zram bounded.
                    zram_mem_limit_mb = int(raw_limit_random) if raw_limit_random else 256
                elif guard_swap_path:
                    # With a guard swap, keep some bound by default.
                    zram_mem_limit_mb = 1024
                else:
                    zram_mem_limit_mb = 0

                mem_limit_bytes = max(0, int(zram_mem_limit_mb) * 1024 * 1024)
                log_debug_ts(f"Setting zram mem_limit to {zram_mem_limit_mb}MB")
                with open('/sys/block/zram0/mem_limit', 'w') as f:
                    f.write(str(mem_limit_bytes))
        except Exception as e:
            log_warn_ts(f"Could not set zram mem_limit: {e}")
        
        # Make swap
        log_info("Enabling swap on /dev/zram0...")
        run_command('mkswap /dev/zram0')
        run_command(f'swapon -p {DEFAULT_ZRAM_PRIO} /dev/zram0')

        # Prefer ZRAM for this test: disable other swaps so mm_stat reflects this run.
        keep = ['/dev/zram0']
        if guard_swap_path:
            keep.append(guard_swap_path)
        swapoff_all_except(keep)

        # Encourage swapping during the test, then restore afterwards.
        prior_swappiness = get_sysctl_int('vm.swappiness', default=60)
        set_sysctl_int('vm.swappiness', 180)
        
        # Optional: Start mem_locker to lock free RAM (prevents non-test memory from swapping)
        # This is optional but makes tests more reliable and predictable
        script_dir = Path(__file__).parent
        mem_locker_path = script_dir / 'mem_locker'
        
        # Calculate how much memory to lock
        test_alloc_mb, lock_mb, available_mb = calculate_memory_distribution(size_mb)
        
        global DISABLE_MEM_LOCKER

        if (not DISABLE_MEM_LOCKER) and lock_mb > 100 and mem_locker_path.exists():
            # Only use mem_locker if we have significant memory to lock (>100MB)
            try:
                log_info_ts(f"Starting mem_locker to reserve {lock_mb}MB of free RAM...")
                mem_locker_proc = subprocess.Popen(
                    [str(mem_locker_path), str(lock_mb)],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    preexec_fn=lambda: set_oom_score_adj(1000)
                )
                # Give it a moment to allocate and lock memory
                time.sleep(2)
                
                # Check if it's still running
                if mem_locker_proc.poll() is not None:
                    log_warn("mem_locker exited prematurely, continuing without it")
                    mem_locker_proc = None
                else:
                    log_info(f"✓ mem_locker running (PID: {mem_locker_proc.pid})")
            except Exception as e:
                log_warn(f"Failed to start mem_locker: {e}")
                mem_locker_proc = None
        elif lock_mb <= 100:
            log_debug_ts(f"Skipping mem_locker (only {lock_mb}MB would be locked)")
        elif DISABLE_MEM_LOCKER:
            log_warn_ts("Skipping mem_locker (disabled due to earlier OOM-kill)")
        
        # Create memory pressure to force actual swapping to ZRAM.
        # MemAvailable-based heuristics can result in *zero swap* (only cache reclaim).
        # Instead, allocate near MemTotal with a safety headroom.
        #
        # IMPORTANT: ZRAM lives in RAM. For incompressible data (e.g. random), swapping to
        # ZRAM does not create meaningful headroom and can still trigger the OOM killer.
        # We therefore keep explicit headroom and only allow limited overcommit based on a
        # conservative per-pattern minimum compression ratio.
        try:
            mem_total_mb = int(get_memory_info().get('total_mb', 0))
            ram_gb = int(system_info.get('ram_gb', 8))
            safety_buffer_mb = 1024 if ram_gb <= 8 else 500

            # Conservative minimum compression ratios used only for safety sizing.
            # (Actual observed ratios may be much higher for compressible patterns.)
            # Patterns: 0=mixed, 1=random, 2=zeros, 3=sequential
            min_ratio_floor = {
                0: 1.20,  # mixed: assume only modest compressibility
                1: 1.00,  # random: assume incompressible
                2: 1.50,  # zeros: should compress extremely well, but keep floor conservative
                3: 1.50,  # sequential: typically compressible, but be conservative
            }.get(pattern, 1.00)

            # Keep some headroom to avoid OOM-killing system services.
            # For incompressible data (min_ratio_floor ~= 1.0), we must keep *extra* headroom
            # because swapping into ZRAM does not significantly reduce RAM pressure.
            reserve_mb = max(128, int(safety_buffer_mb * 0.25))
            if min_ratio_floor <= 1.05:
                reserve_mb = max(reserve_mb, int(safety_buffer_mb * 1.25))

            # Allow limited overcommit only when we expect at least some compression.
            # This raises swap pressure for compressible patterns, while keeping random safe.
            extra_overcommit_mb = int(safety_buffer_mb * max(0.0, min_ratio_floor - 1.0) * 0.75)

            base_alloc_mb = max(size_mb, mem_total_mb - reserve_mb)
            alloc_size_mb = base_alloc_mb + extra_overcommit_mb

            # If we have a guard swapfile and a zram mem_limit, we can safely force enough
            # pressure to get *some* zram activity for random, without risking OOM.
            if guard_swap_path and min_ratio_floor <= 1.05:
                desired_swap_mb = 256
                if zram_mem_limit_mb > 0:
                    desired_swap_mb = max(64, min(512, int(zram_mem_limit_mb // 2)))
                alloc_size_mb = mem_total_mb + desired_swap_mb

            # Clamp to what ZRAM can absorb while maintaining the safety buffer.
            max_safe_alloc_mb = mem_total_mb + max(0, zram_disksize_mb - safety_buffer_mb)
            alloc_size_mb = min(alloc_size_mb, max_safe_alloc_mb)
            alloc_size_mb = max(size_mb, alloc_size_mb)

            log_info(f"Creating memory pressure (allocating {alloc_size_mb}MB; zram={zram_disksize_mb}MB)...")
            log_debug_ts(
                "MemTotal={}".format(mem_total_mb)
                + f"MB safety={safety_buffer_mb}MB reserve={reserve_mb}MB "
                + f"min_ratio_floor={min_ratio_floor:.2f} extra_overcommit={extra_overcommit_mb}MB "
                + f"guard_swap={'yes' if guard_swap_path else 'no'} zram_mem_limit_mb={zram_mem_limit_mb} "
                + f"max_safe_alloc={max_safe_alloc_mb}MB alloc={alloc_size_mb}MB"
            )
        except Exception:
            alloc_size_mb = max(size_mb, size_mb * COMPRESSION_MEMORY_PERCENT // 100)
            log_info(f"Creating memory pressure (allocating {alloc_size_mb}MB)...")
        
        # Use C-based mem_pressure for fast memory allocation
        # This is MUCH faster than Python for large allocations (7+ GB)
        script_dir = Path(__file__).parent
        mem_pressure_path = script_dir / 'mem_pressure'
        
        if not mem_pressure_path.exists():
            log_error(f"mem_pressure program not found at {mem_pressure_path}")
            log_error("Run compile_c_programs() first")
            results['error'] = "mem_pressure program not found"
            return results
        
        # Use specified pattern for workload testing
        # Patterns: 0=mixed (default), 1=random, 2=zeros, 3=sequential
        # Hold time of 15 seconds (default)
        pattern_names = {0: 'mixed', 1: 'random', 2: 'zeros', 3: 'sequential'}
        pattern_name = pattern_names.get(pattern, 'unknown')
        log_info_ts(f"Using C-based mem_pressure for allocation ({alloc_size_mb}MB, {pattern_name} data)...")
        
        # Run memory pressure and sample mm_stat while memory is held.
        # If we only read mm_stat *after* mem_pressure exits, swapped pages may have been freed,
        # resulting in near-zero orig_data_size even when the system did swap during the run.
        try:
            hold_seconds = int(os.environ.get('BENCHMARK_HOLD_SECONDS', '10').strip() or '10')
        except Exception:
            hold_seconds = 10
        hold_seconds = max(3, min(60, hold_seconds))
        results['hold_seconds'] = int(hold_seconds)
        log_info(f"Starting memory pressure test (timeout: {COMPRESSION_TEST_TIMEOUT_SEC}s)...")

        peak_orig = 0
        peak_compr = 0
        peak_mem = 0
        stderr = ""
        stdout = ""
        success = False
        proc = None

        try:
            proc = subprocess.Popen(
                [str(mem_pressure_path), str(alloc_size_mb), str(pattern), str(hold_seconds)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                preexec_fn=lambda: set_oom_score_adj(1000),
            )

            deadline = time.time() + COMPRESSION_TEST_TIMEOUT_SEC
            while True:
                # Sample zram stats while the pressure test is running.
                try:
                    if os.path.exists('/sys/block/zram0/mm_stat'):
                        stats = run_command('cat /sys/block/zram0/mm_stat', check=False).split()
                        if len(stats) >= 3:
                            peak_orig = max(peak_orig, int(stats[0]))
                            peak_compr = max(peak_compr, int(stats[1]))
                            peak_mem = max(peak_mem, int(stats[2]))
                except Exception:
                    pass

                if proc.poll() is not None:
                    break

                if time.time() > deadline:
                    raise subprocess.TimeoutExpired(cmd=str(mem_pressure_path), timeout=COMPRESSION_TEST_TIMEOUT_SEC)

                time.sleep(1)

            try:
                stdout, stderr = proc.communicate(timeout=2)
            except Exception:
                stdout = stdout or ""
                stderr = stderr or ""

            success = (proc.returncode == 0)
        except subprocess.TimeoutExpired:
            success = False
            stderr = f"Timeout after {COMPRESSION_TEST_TIMEOUT_SEC}s"
            log_warn_ts(f"Memory pressure test timed out after {COMPRESSION_TEST_TIMEOUT_SEC}s")
            if proc is not None:
                try:
                    proc.kill()
                except Exception:
                    pass
        except Exception as e:
            success = False
            stderr = str(e)
            log_error(f"Memory pressure test failed: {e}")
            if proc is not None:
                try:
                    proc.kill()
                except Exception:
                    pass

        if not success:
            log_error("Memory pressure test failed or timed out")
            if stderr:
                log_error(f"Error: {stderr}")
            results['error'] = f"mem_pressure failed: {stderr}"
            return results

        log_info("Memory pressure test completed successfully")

        # Make kernel OOM-kill of mem_locker visible in the benchmark log (it won't show up
        # in stdout/stderr otherwise). If this happens, disable mem_locker for subsequent tests.
        if mem_locker_proc is not None and mem_locker_proc.poll() is not None:
            DISABLE_MEM_LOCKER = True
            pid = mem_locker_proc.pid
            log_warn_ts(f"mem_locker (PID: {pid}) was killed during memory pressure (likely OOM). Disabling mem_locker for remaining tests.")
            results['warning'] = 'mem_locker OOM-killed; disabled for remainder'
            try:
                dmesg = subprocess.run(['dmesg', '-T'], capture_output=True, text=True, timeout=5)
                if dmesg.returncode == 0 and dmesg.stdout:
                    lines = [ln for ln in dmesg.stdout.splitlines() if f"Killed process {pid} (mem_locker)" in ln or f"pid={pid}" in ln]
                    for ln in lines[-3:]:
                        log_warn_ts(f"kernel: {ln.strip()}")
            except Exception:
                pass
        
        duration = time.time() - start_time
        
        # Use peak stats observed during the pressure run (see sampling loop above).
        log_info("Reading ZRAM statistics...")
        if peak_orig > 0 or peak_mem > 0:
            orig_size = int(peak_orig)
            compr_size = int(peak_compr)
            mem_used = int(peak_mem)

            log_debug_ts(
                "Peak mm_stat during pressure: "
                f"orig={orig_size/1024/1024:.2f}MB compr={compr_size/1024/1024:.2f}MB mem_used={mem_used/1024/1024:.2f}MB"
            )

            # Validation: catch impossible values
            if orig_size == 0:
                log_warn("No data swapped to ZRAM (orig_size = 0)")
                results['error'] = 'No swap activity detected'
                return results

            # VALIDATION: ensure meaningful swap happened. When only a few pages
            # move, ratios/efficiency are dominated by allocator overhead and are junk.
            # Minimum activity threshold:
            # - 16MB was only a "did anything happen" floor and is too low for stable allocator metrics.
            # - Use pattern-aware defaults, overridable via env.
            try:
                env_min = os.environ.get('BENCHMARK_MIN_SWAP_ACTIVITY_MB', '').strip()
                env_min_random = os.environ.get('BENCHMARK_MIN_SWAP_ACTIVITY_MB_RANDOM', '').strip()
                if pattern == 1 and env_min_random:
                    min_activity_mb = int(env_min_random)
                elif env_min:
                    min_activity_mb = int(env_min)
                else:
                    min_activity_mb = 128 if pattern == 1 else 256
            except Exception:
                min_activity_mb = 128 if pattern == 1 else 256

            # If the user asked for a larger test, don't set a smaller threshold than the test size.
            min_activity_mb = max(32, min_activity_mb)
            results['min_swap_activity_mb'] = int(min_activity_mb)
            activity_mb = orig_size / 1024 / 1024
            results['swap_activity_mb'] = round(activity_mb, 2)
            results['zram_disksize_mb'] = int(zram_disksize_mb)

            if activity_mb < min_activity_mb:
                msg = (
                    f"Insufficient swap activity: only {activity_mb:.2f}MB swapped "
                    f"(need >= {min_activity_mb}MB for reliable metrics)"
                )
                log_warn(msg)
                results['error'] = 'Insufficient swap activity'
                results['reliable'] = False
                results['warning'] = msg
                return results
            results['reliable'] = True

            if compr_size == 0:
                log_error("Compressed size is zero - invalid ZRAM state")
                results['error'] = 'Invalid ZRAM compression state'
                return results

            if mem_used > orig_size * 2:
                log_warn(f"Memory overhead detected: used {mem_used} > orig {orig_size}")

            # Calculate with proper bounds checking
            results['orig_size_mb'] = round(orig_size / 1024 / 1024, 2)
            results['compr_size_mb'] = round(compr_size / 1024 / 1024, 2)
            results['mem_used_mb'] = round(mem_used / 1024 / 1024, 2)

            ratio = orig_size / compr_size
            exp_min, exp_max, exp_suspicious, exp_label = _compression_ratio_expectations_for_pattern(pattern)

            if ratio < exp_min or ratio > exp_suspicious:
                msg = (
                    f"Suspicious compression ratio: {ratio:.2f}x "
                    f"(expected {exp_min:.2f}-{exp_max:.2f}x for {exp_label} data)"
                )
                log_warn(msg)
                _append_warning(results, msg)
            elif ratio > exp_max:
                msg = (
                    f"High compression ratio: {ratio:.2f}x "
                    f"(above typical {exp_max:.2f}x for {exp_label} data; workload may be unusually compressible)"
                )
                log_warn(msg)
                _append_warning(results, msg)

            results['compression_ratio'] = round(ratio, 2)

            # Efficiency: (orig - mem_used) / orig as percentage
            # Negative values indicate allocator overhead exceeds space savings
            # This can happen with small data sizes or high-overhead allocators
            if orig_size > 0:
                efficiency = ((orig_size - mem_used) / orig_size) * 100
                results['efficiency_pct'] = round(efficiency, 2)

                if efficiency < -50:
                    log_warn(f"High allocator overhead: {abs(efficiency):.1f}% overhead (mem_used > orig_size)")
                    log_warn("This can occur with small test sizes or inefficient allocators")
            else:
                results['efficiency_pct'] = 0

            log_info(f"  Compression ratio: {ratio:.2f}x")
            log_info(f"  Space efficiency: {results['efficiency_pct']:.1f}%")
            log_info(f"  Memory saved: {results['orig_size_mb'] - results['mem_used_mb']:.2f} MB")

        elif os.path.exists('/sys/block/zram0/mm_stat'):
            # Fallback: if sampling failed for some reason, read the current value.
            # This may under-report activity if mem_pressure already freed memory.
            stats = run_command('cat /sys/block/zram0/mm_stat', check=False).split()
            log_debug_ts(f"Raw mm_stat (fallback): {' '.join(stats)}")
        
        results['duration_sec'] = round(duration, 2)
        
        # Log completion time
        elapsed = time.time() - start_time
        log_info(f"✓ Test completed in {elapsed:.1f}s")
        
    except Exception as e:
        log_error(f"Benchmark failed: {e}")
        results['error'] = str(e)
        elapsed = time.time() - start_time
        log_error(f"Test failed after {elapsed:.1f}s")
    finally:
        # Restore sysctls/swaps before tearing down zram.
        if prior_swappiness is not None:
            set_sysctl_int('vm.swappiness', prior_swappiness)
        if prior_swaps is not None:
            restore_swaps(prior_swaps)

        if prior_zswap_enabled is not None and os.path.exists('/sys/module/zswap/parameters/enabled'):
            try:
                # Normalize to Y/N if possible.
                restore_val = prior_zswap_enabled.strip()
                if restore_val == '1':
                    restore_val = 'Y'
                if restore_val == '0':
                    restore_val = 'N'
                with open('/sys/module/zswap/parameters/enabled', 'w') as f:
                    f.write(f"{restore_val}\n")
            except Exception:
                pass

        # Cleanup mem_locker if it was started
        if mem_locker_proc is not None:
            try:
                log_info("Stopping mem_locker...")
                mem_locker_proc.terminate()
                mem_locker_proc.wait(timeout=5)
                log_info("✓ mem_locker stopped")
            except subprocess.TimeoutExpired:
                log_warn("mem_locker didn't stop gracefully, killing it")
                mem_locker_proc.kill()
                mem_locker_proc.wait()
            except Exception as e:
                log_warn(f"Error stopping mem_locker: {e}")
        
        # Cleanup swap
        cleanup_zram_aggressive()

        # Disable guard swapfile if we enabled it for the test.
        if guard_swap_path:
            try:
                guard_swap_used_end_kb = _read_proc_swaps_used_kb(guard_swap_path)
                results['guard_swap_path'] = guard_swap_path
                results['guard_swap_used_kb_start'] = int(guard_swap_used_start_kb)
                results['guard_swap_used_kb_end'] = int(guard_swap_used_end_kb)
                results['guard_swap_used_kb_delta'] = int(max(0, guard_swap_used_end_kb - guard_swap_used_start_kb))
                results['zram_mem_limit_mb'] = int(zram_mem_limit_mb)
            except Exception:
                pass
            disable_guard_swapfile(guard_swap_path)
    
    return results

def test_concurrency(num_files=8, file_size_mb=128, test_dir='/tmp/swap_test', test_num=None, total_tests=None):
    """
    [DEPRECATED] Test concurrency with multiple swap files using fio
    
    NOTE: This test is deprecated. Use test_blocksize_concurrency_matrix() instead,
    which provides comprehensive coverage with realistic rw=randrw patterns.
    
    This function is maintained for backward compatibility only and uses the
    older sequential write-then-read pattern rather than the newer randrw approach.
    
    Args:
        num_files: Number of concurrent swap files
        file_size_mb: Size of each file in MB
        test_dir: Directory for test files
        test_num: Current test number (for progress tracking)
        total_tests: Total number of tests (for progress tracking)
    """
    start_time = time.time()
    
    # Log with progress tracking
    progress_str = f"[{test_num}/{total_tests}] " if test_num and total_tests else ""
    log_step_ts(f"{progress_str}Concurrency test: {num_files} files")
    
    results = {
        'num_files': num_files,
        'file_size_mb': file_size_mb,
        'timestamp': datetime.now().isoformat()
    }
    
    # Create test directory
    os.makedirs(test_dir, exist_ok=True)
    
    # Create fio job for concurrent I/O
    fio_job = f"""
[global]
ioengine=libaio
direct=1
size={file_size_mb}m
directory={test_dir}
numjobs={num_files}
group_reporting

[concurrent_write]
rw=write
bs=64k

[concurrent_read]
rw=read
bs=64k
stonewall
"""
    
    try:
        with open('/tmp/fio_concurrent.job', 'w') as f:
            f.write(fio_job)
        
        log_info(f"Running concurrent I/O test with {num_files} files...")
        log_debug_ts(f"fio command: fio --output-format=json /tmp/fio_concurrent.job")
        
        result = subprocess.run(
            ['fio', '--output-format=json', '/tmp/fio_concurrent.job'],
            capture_output=True,
            text=True,
            timeout=600  # 10 minute timeout
        )
        
        if result.returncode == 0:
            log_info("Parsing fio results...")
            data = json.loads(result.stdout)
            
            # Validate data structure
            if 'jobs' not in data or len(data['jobs']) < 2:
                raise ValueError("Incomplete fio results")
            
            # Extract write performance
            write_bw = data['jobs'][0]['write']['bw'] / 1024  # MB/s
            results['write_mb_per_sec'] = round(write_bw, 2)
            results['write_iops'] = int(round(data['jobs'][0]['write']['iops'], 0))
            
            # Extract read performance
            read_bw = data['jobs'][1]['read']['bw'] / 1024  # MB/s
            results['read_mb_per_sec'] = round(read_bw, 2)
            results['read_iops'] = int(round(data['jobs'][1]['read']['iops'], 0))
            
            # Calculate scaling efficiency
            # Baseline is single file, so efficiency = actual / (baseline * num_files)
            # We estimate baseline as 1/num_files of concurrent result
            results['write_scaling_efficiency'] = round(100, 2)  # Placeholder, needs baseline
            results['read_scaling_efficiency'] = round(100, 2)  # Placeholder, needs baseline
            
            log_info(f"  Write: {write_bw:.2f} MB/s, {results['write_iops']} IOPS")
            log_info(f"  Read: {read_bw:.2f} MB/s, {results['read_iops']} IOPS")
        else:
            raise subprocess.CalledProcessError(result.returncode, 'fio', result.stderr)
    
    except subprocess.TimeoutExpired as e:
        log_error(f"Concurrency test with {num_files} files timed out after 10 minutes")
        log_error(f"Timeout details: cmd={e.cmd}, timeout={e.timeout}s")
        log_warn("Consider increasing timeout or reducing file count for slower systems")
        results['error'] = 'Timeout after 600s'
        results['write_mb_per_sec'] = 0
        results['read_mb_per_sec'] = 0
    except subprocess.CalledProcessError as e:
        error_msg = f"Concurrency test failed with return code {e.returncode}"
        log_error(error_msg)
        log_debug(f"Command: {e.cmd}")
        
        # Capture stderr for better diagnostics
        stderr_output = e.stderr if hasattr(e, 'stderr') and e.stderr else ''
        if stderr_output:
            log_error(f"Error output: {stderr_output[:500]}")  # Limit to 500 chars
            
            # Check for common errors and provide helpful messages
            if 'Too many open files' in stderr_output or 'EMFILE' in stderr_output:
                log_warn(f"System limit reached at {num_files} concurrent files")
                log_info("Hint: Check 'ulimit -n' and increase if needed")
                results['error'] = f'System limit: Too many open files (max concurrent: {num_files-1})'
            elif 'No space left on device' in stderr_output or 'ENOSPC' in stderr_output:
                log_warn("Insufficient disk space for test")
                results['error'] = 'Insufficient disk space'
            elif 'Cannot allocate memory' in stderr_output or 'ENOMEM' in stderr_output:
                log_warn("Insufficient memory for test")
                results['error'] = 'Insufficient memory'
            else:
                results['error'] = f'Exit code {e.returncode}: {stderr_output[:100]}'
        else:
            results['error'] = f'Exit code {e.returncode}'
        
        results['write_mb_per_sec'] = 0
        results['read_mb_per_sec'] = 0
    except Exception as e:
        log_error(f"Concurrency test failed: {e}")
        results['error'] = str(e)
        results['write_mb_per_sec'] = 0
        results['read_mb_per_sec'] = 0
    finally:
        # Cleanup
        if os.path.exists(test_dir):
            shutil.rmtree(test_dir, ignore_errors=True)
        if os.path.exists('/tmp/fio_concurrent.job'):
            os.remove('/tmp/fio_concurrent.job')
    
    # Log completion time
    elapsed = time.time() - start_time
    results['elapsed_sec'] = round(elapsed, 1)
    log_info(f"✓ Test completed in {elapsed:.1f}s")
    
    return results

def test_blocksize_concurrency_matrix(block_sizes=None, concurrency_levels=None, file_size_mb=128, test_dir='/tmp/swap_test', runtime_sec=5):
    """
    Test block size × concurrency matrix to find optimal configuration
    
    This tests all combinations of block sizes and concurrency levels to discover
    which configuration provides the best throughput for the specific hardware.
    
    Access pattern: Mixed random read/write (rw=randrw) - realistic swap simulation
    - Simulates: concurrent eviction (writes) + page faults (reads)
    - More realistic than sequential patterns used in older tests
    - Better represents actual swap behavior with striped files and fragmentation
    
    Args:
        block_sizes: List of block sizes in KB (default: [4, 8, 16, 32, 64])
        concurrency_levels: List of concurrency levels (default: [1, 2, 4])
        file_size_mb: Size of each file in MB
        test_dir: Directory for test files
        runtime_sec: Test runtime in seconds
    
    Returns:
        Dictionary with matrix results and optimal configuration
    """
    start_time = time.time()
    
    if block_sizes is None:
        block_sizes = [4, 8, 16, 32, 64, 128]  # Include 128KB - sequential tests show good performance
    if concurrency_levels is None:
        concurrency_levels = [1, 2, 4, 6, 8, 12, 16]  # Extended to 12 and 16 for optimal swap device count determination
    
    total_combinations = len(block_sizes) * len(concurrency_levels)
    log_step_ts(f"Block Size × Concurrency Matrix Test ({total_combinations} combinations)")
    log_info(f"Block sizes: {block_sizes} KB")
    log_info(f"Concurrency levels: {concurrency_levels}")
    log_info(f"Runtime per test: {runtime_sec}s")
    
    results = {
        'block_sizes': block_sizes,
        'concurrency_levels': concurrency_levels,
        'matrix': [],
        'timestamp': datetime.now().isoformat()
    }
    
    # Create test directory
    os.makedirs(test_dir, exist_ok=True)

    # Show the underlying fio invocation once so users understand the mechanism.
    # The job file is regenerated for each (block_size, concurrency) combination.
    log_info_ts("fio command (job regenerated per test): fio --output-format=json /tmp/fio_matrix.job")
    
    # Test each combination
    test_num = 0
    for block_size in block_sizes:
        for concurrency in concurrency_levels:
            test_num += 1
            progress_str = f"[{test_num}/{total_combinations}]"
            log_info_ts(f"{progress_str} Testing {block_size}KB × {concurrency} jobs...")
            
            # Create fio job for this combination
            # Use rw=randrw for mixed random read/write to simulate real swap behavior
            fio_job = f"""
[global]
ioengine=libaio
direct=1
size={file_size_mb}m
directory={test_dir}
numjobs={concurrency}
group_reporting
runtime={runtime_sec}
time_based

[matrix_randrw]
rw=randrw
rwmixread=50
bs={block_size}k
"""
            
            try:
                with open('/tmp/fio_matrix.job', 'w') as f:
                    f.write(fio_job)
                
                result = subprocess.run(
                    ['fio', '--output-format=json', '/tmp/fio_matrix.job'],
                    capture_output=True,
                    text=True,
                    timeout=300
                )
                
                if result.returncode == 0:
                    data = json.loads(result.stdout)
                    
                    # Validate data structure - randrw produces exactly one job with both metrics
                    if 'jobs' not in data or len(data['jobs']) != 1:
                        raise ValueError("Expected exactly one fio job for randrw test")
                    
                    # Extract performance metrics from randrw job
                    # randrw produces both read and write data in a single job
                    job = data['jobs'][0]
                    write_bw = job['write']['bw'] / 1024  # MB/s
                    read_bw = job['read']['bw'] / 1024  # MB/s
                    write_iops = int(round(job['write']['iops'], 0))
                    read_iops = int(round(job['read']['iops'], 0))
                    
                    # Extract latency metrics (in microseconds)
                    write_lat_ns = job['write'].get('lat_ns', {})
                    read_lat_ns = job['read'].get('lat_ns', {})
                    write_lat_us = write_lat_ns.get('mean', 0) / 1000 if write_lat_ns else 0  # Convert ns to us
                    read_lat_us = read_lat_ns.get('mean', 0) / 1000 if read_lat_ns else 0
                    
                    matrix_result = {
                        'block_size_kb': block_size,
                        'concurrency': concurrency,
                        'write_mb_per_sec': round(write_bw, 2),
                        'read_mb_per_sec': round(read_bw, 2),
                        'write_iops': write_iops,
                        'read_iops': read_iops,
                        'write_latency_us': round(write_lat_us, 2),
                        'read_latency_us': round(read_lat_us, 2)
                    }
                    
                    results['matrix'].append(matrix_result)
                    
                    log_info(f"  Write: {write_bw:.2f} MB/s ({write_iops} IOPS)")
                    log_info(f"  Read: {read_bw:.2f} MB/s ({read_iops} IOPS)")
                else:
                    raise subprocess.CalledProcessError(result.returncode, 'fio', result.stderr)
            
            except subprocess.TimeoutExpired as e:
                log_error(f"Matrix test {block_size}KB × {concurrency} timed out after 5 minutes")
                results['matrix'].append({
                    'block_size_kb': block_size,
                    'concurrency': concurrency,
                    'error': 'Timeout after 300s',
                    'write_mb_per_sec': 0,
                    'read_mb_per_sec': 0
                })
            except subprocess.CalledProcessError as e:
                error_msg = f"Matrix test {block_size}KB × {concurrency} failed with exit code {e.returncode}"
                log_error(error_msg)
                
                # Capture stderr for diagnostics
                stderr_output = e.stderr if hasattr(e, 'stderr') and e.stderr else ''
                error_detail = f'Exit code {e.returncode}'
                
                if stderr_output:
                    log_debug(f"Error output: {stderr_output[:500]}")
                    
                    # Check for common errors
                    if 'Too many open files' in stderr_output or 'EMFILE' in stderr_output:
                        log_warn(f"System limit reached at concurrency={concurrency}")
                        log_info("Hint: Check 'ulimit -n' and increase if needed")
                        error_detail = f'System limit reached (max concurrency: {concurrency-1})'
                    elif 'No space left on device' in stderr_output:
                        error_detail = 'Insufficient disk space'
                    elif 'Cannot allocate memory' in stderr_output:
                        error_detail = 'Insufficient memory'
                    else:
                        error_detail = f'Exit code {e.returncode}: {stderr_output[:100]}'
                
                results['matrix'].append({
                    'block_size_kb': block_size,
                    'concurrency': concurrency,
                    'error': error_detail,
                    'write_mb_per_sec': 0,
                    'read_mb_per_sec': 0
                })
            except Exception as e:
                log_error(f"Matrix test {block_size}KB × {concurrency} failed: {e}")
                results['matrix'].append({
                    'block_size_kb': block_size,
                    'concurrency': concurrency,
                    'error': str(e)[:100],
                    'write_mb_per_sec': 0,
                    'read_mb_per_sec': 0
                })
    
    # Find optimal configuration
    if results['matrix']:
        # Find best write throughput
        best_write = max(results['matrix'], key=lambda x: x.get('write_mb_per_sec', 0))
        best_read = max(results['matrix'], key=lambda x: x.get('read_mb_per_sec', 0))
        
        # Calculate combined score (write + read)
        for r in results['matrix']:
            r['combined_mb_per_sec'] = r.get('write_mb_per_sec', 0) + r.get('read_mb_per_sec', 0)
        
        best_combined = max(results['matrix'], key=lambda x: x.get('combined_mb_per_sec', 0))
        
        results['optimal'] = {
            'best_write': {
                'block_size_kb': best_write['block_size_kb'],
                'concurrency': best_write['concurrency'],
                'throughput_mb_per_sec': best_write['write_mb_per_sec']
            },
            'best_read': {
                'block_size_kb': best_read['block_size_kb'],
                'concurrency': best_read['concurrency'],
                'throughput_mb_per_sec': best_read['read_mb_per_sec']
            },
            'best_combined': {
                'block_size_kb': best_combined['block_size_kb'],
                'concurrency': best_combined['concurrency'],
                'throughput_mb_per_sec': best_combined['combined_mb_per_sec']
            }
        }
        
        log_step_ts("Matrix testing complete!")
        log_info(f"Optimal configuration:")
        log_info(f"  Best write: {best_write['block_size_kb']}KB × {best_write['concurrency']} jobs = {best_write['write_mb_per_sec']} MB/s")
        log_info(f"  Best read: {best_read['block_size_kb']}KB × {best_read['concurrency']} jobs = {best_read['read_mb_per_sec']} MB/s")
        log_info(f"  Best combined: {best_combined['block_size_kb']}KB × {best_combined['concurrency']} jobs = {best_combined['combined_mb_per_sec']} MB/s")
        
        # Generate recommendations for vm.page-cluster
        block_to_cluster = {4: 0, 8: 1, 16: 2, 32: 3, 64: 4, 128: 5}
        recommended_cluster = block_to_cluster.get(best_combined['block_size_kb'], 3)
        results['optimal']['recommended_page_cluster'] = recommended_cluster
        
        # IMPORTANT: fio numjobs in this matrix is I/O concurrency against a single target,
        # not the number of swap devices.
        # Preserve matrix "best numjobs" for diagnostics only.
        valid_results = [r for r in results['matrix'] if 'error' not in r]
        max_successful_numjobs = max([r['concurrency'] for r in valid_results]) if valid_results else 1
        results['optimal']['recommended_fio_numjobs'] = int(best_combined['concurrency'])
        results['optimal']['max_tested_fio_numjobs'] = int(max_successful_numjobs)

        log_info(f"Recommended settings:")
        log_info(f"  SWAP_PAGE_CLUSTER={recommended_cluster} (for {best_combined['block_size_kb']}KB blocks)")
        log_info(f"  FIO_NUMJOBS={results['optimal']['recommended_fio_numjobs']} (matrix best combined)")
        log_info(f"  (Max fio numjobs tested successfully: {max_successful_numjobs})")
    
    # Cleanup
    if os.path.exists(test_dir):
        shutil.rmtree(test_dir, ignore_errors=True)
    if os.path.exists('/tmp/fio_matrix.job'):
        os.remove('/tmp/fio_matrix.job')
    
    # Log completion time
    elapsed = time.time() - start_time
    results['elapsed_sec'] = round(elapsed, 1)
    log_info(f"✓ Matrix test completed in {elapsed:.1f}s")
    
    return results


def _run_cmd(args, timeout=None):
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout)


def discover_gpt_swap_partitions(limit=None):
    """Discover GPT swap partitions (may be unformatted) in stable order."""
    try:
        proc = _run_cmd(['lsblk', '-rno', 'PATH,TYPE,PARTTYPE'], timeout=10)
        if proc.returncode != 0:
            return []
        parts = []
        for line in (proc.stdout or '').splitlines():
            fields = line.strip().split(None, 2)
            if len(fields) != 3:
                continue
            path, typ, parttype = fields
            if typ != 'part':
                continue
            if (parttype or '').strip().lower() == SWAP_GPT_GUID:
                parts.append(path)
        # Stable sort: vda10 after vda2, nvme0n1p10 after p2
        parts = sorted(parts, key=lambda s: [int(t) if t.isdigit() else t for t in __import__('re').split(r'(\d+)', s)])
        if isinstance(limit, int) and limit > 0:
            return parts[:limit]
        return parts
    except Exception:
        return []


def _fio_sum_bw_and_lat(data):
    """Sum bandwidth and compute latency (mean + p99, in µs) across fio jobs."""
    jobs = data.get('jobs') or []
    if not isinstance(jobs, list) or not jobs:
        return 0.0, 0.0, 0.0, 0.0, 0.0, 0.0

    total_read_bw_kib = 0.0
    total_write_bw_kib = 0.0
    read_lat_mean_us_sum = 0.0
    write_lat_mean_us_sum = 0.0
    read_lat_p99_us_sum = 0.0
    write_lat_p99_us_sum = 0.0
    lat_count = 0

    def _get_percentile_us(io_stats, percentile=99.0):
        if not isinstance(io_stats, dict):
            return None

        # fio percentiles are typically under clat_ns.percentile.
        for lat_key in ('clat_ns', 'lat_ns'):
            lat = io_stats.get(lat_key) or {}
            pct = lat.get('percentile')
            if not isinstance(pct, dict) or not pct:
                continue

            # Keys are strings like "99.000000".
            target = f"{float(percentile):.6f}"
            v = pct.get(target)
            if isinstance(v, (int, float)):
                return float(v) / 1000.0

            # Fallback: pick the closest key.
            try:
                parsed = []
                for k, vv in pct.items():
                    try:
                        kk = float(k)
                    except Exception:
                        continue
                    if isinstance(vv, (int, float)):
                        parsed.append((kk, float(vv)))
                if not parsed:
                    continue
                parsed.sort(key=lambda t: abs(t[0] - float(percentile)))
                return parsed[0][1] / 1000.0
            except Exception:
                continue
        return None

    for job in jobs:
        read = job.get('read') or {}
        write = job.get('write') or {}
        total_read_bw_kib += float(read.get('bw', 0.0) or 0.0)
        total_write_bw_kib += float(write.get('bw', 0.0) or 0.0)

        rlat = (read.get('lat_ns') or {}).get('mean')
        wlat = (write.get('lat_ns') or {}).get('mean')
        if isinstance(rlat, (int, float)):
            read_lat_mean_us_sum += float(rlat) / 1000.0
        if isinstance(wlat, (int, float)):
            write_lat_mean_us_sum += float(wlat) / 1000.0

        rp99 = _get_percentile_us(read, 99.0)
        wp99 = _get_percentile_us(write, 99.0)
        if isinstance(rp99, (int, float)):
            read_lat_p99_us_sum += float(rp99)
        if isinstance(wp99, (int, float)):
            write_lat_p99_us_sum += float(wp99)

        lat_count += 1

    read_mb_s = total_read_bw_kib / 1024.0
    write_mb_s = total_write_bw_kib / 1024.0
    read_lat_mean_us = (read_lat_mean_us_sum / lat_count) if lat_count else 0.0
    write_lat_mean_us = (write_lat_mean_us_sum / lat_count) if lat_count else 0.0
    read_lat_p99_us = (read_lat_p99_us_sum / lat_count) if lat_count else 0.0
    write_lat_p99_us = (write_lat_p99_us_sum / lat_count) if lat_count else 0.0
    return read_mb_s, write_mb_s, read_lat_mean_us, write_lat_mean_us, read_lat_p99_us, write_lat_p99_us


def test_swap_partitions_stripe_matrix(
    runtime_sec=5,
    block_sizes=None,
    device_counts=None,
    numjobs_per_device_levels=None,
    iodepth_levels=None,
    rwmixread=80,
    per_device_size_mb=256,
    use_full_device_size=True,
    full_device_size_margin_mb=16,
    latency_metric='p99',
    max_partitions=None,
):
    """Benchmark real swap partitions as block devices.

    This matrix independently varies:
    - device_count: number of swap partitions used (proxy for SWAP_STRIPE_WIDTH)
    - numjobs_per_device: fio concurrency per device
    - block_size: I/O size (proxy for vm.page-cluster)

    Requires GPT-swap partitions (type GUID) already present.
    """
    start_time = time.time()

    parts = discover_gpt_swap_partitions(limit=max_partitions)
    if not parts:
        return {
            'error': 'No GPT swap partitions found (create them first via create-swap-partitions.sh)'
        }

    # Safety checks: do not run fio against mounted devices.
    for p in parts:
        try:
            mp = _run_cmd(['lsblk', '-rno', 'MOUNTPOINT', p], timeout=5)
            if (mp.stdout or '').strip():
                return {'error': f'Refusing to benchmark mounted device: {p}'}
        except Exception:
            pass

    # Swap I/O is typically 4K pages, with clustered reads often landing in the ~4–32K range.
    # Default to that window unless explicitly overridden by the caller.
    if block_sizes is None:
        block_sizes = [4, 8, 16, 32, 64, 128]
    if device_counts is None:
        device_counts = [1, 2, 4, 8, 16]
    if numjobs_per_device_levels is None:
        # Latency-oriented default: keep per-device parallelism modest.
        numjobs_per_device_levels = [1, 2, 3]
    if iodepth_levels is None:
        # Latency-oriented default: shallow queues, closer to page-fault critical path.
        iodepth_levels = [1, 2]

    max_devices_available = len(parts)
    device_counts = [d for d in device_counts if isinstance(d, int) and 1 <= d <= max_devices_available]
    if not device_counts:
        device_counts = [max_devices_available]

    numjobs_per_device_levels = [n for n in numjobs_per_device_levels if isinstance(n, int) and n >= 1]
    if not numjobs_per_device_levels:
        numjobs_per_device_levels = [1]

    iodepth_levels = [d for d in iodepth_levels if isinstance(d, int) and d >= 1]
    if not iodepth_levels:
        iodepth_levels = [1]

    if not isinstance(rwmixread, int):
        try:
            rwmixread = int(rwmixread)
        except Exception:
            rwmixread = 80
    if rwmixread < 0:
        rwmixread = 0
    if rwmixread > 100:
        rwmixread = 100

    latency_metric = str(latency_metric or '').strip().lower()
    if latency_metric not in ('mean', 'p99'):
        latency_metric = 'p99'

    total_combinations = len(block_sizes) * len(device_counts) * len(numjobs_per_device_levels) * len(iodepth_levels)
    log_step_ts(f"Swap Partition Stripe Matrix ({total_combinations} combinations)")
    log_info(f"Detected swap partitions: {max_devices_available}")
    log_info(f"Device counts: {device_counts}")
    log_info(f"Numjobs per device: {numjobs_per_device_levels}")
    log_info(f"IO depths: {iodepth_levels}")
    log_info(f"Block sizes: {block_sizes} KB")
    log_info(f"Runtime per test: {runtime_sec}s")
    log_info(f"Read mix: {rwmixread}%")
    if use_full_device_size:
        log_info(f"Size: full device (minus {int(full_device_size_margin_mb)}MB margin)")
    else:
        log_info(f"Size: {int(per_device_size_mb)}MB per device")

    results = {
        'timestamp': datetime.now().isoformat(),
        'swap_partitions': parts,
        'runtime_sec': int(runtime_sec),
        'per_device_size_mb': int(per_device_size_mb) if not use_full_device_size else None,
        'use_full_device_size': bool(use_full_device_size),
        'full_device_size_margin_mb': int(full_device_size_margin_mb),
        'fio_workload': {
            'rw': 'randrw',
            'rwmixread': int(rwmixread),
            'direct': 1,
            'ioengine': 'libaio',
            'iodepth_levels': iodepth_levels,
            'time_based': True,
            'latency_metric': latency_metric,
        },
        'block_sizes': block_sizes,
        'device_counts': device_counts,
        'numjobs_per_device_levels': numjobs_per_device_levels,
        'iodepth_levels': iodepth_levels,
        'matrix': [],
    }

    # Show the underlying fio invocation once so users understand the mechanism.
    # The job file is regenerated for each (bs, device_count, numjobs, iodepth) combination.
    job_path = '/tmp/fio_swap_partitions_matrix.job'
    log_info_ts(f"fio command (job regenerated per test): fio --output-format=json {job_path}")

    test_num = 0
    def _device_size_mb(dev_path):
        try:
            p = _run_cmd(['blockdev', '--getsize64', dev_path], timeout=5)
            if p.returncode != 0:
                return None
            b = int((p.stdout or '').strip())
            if b <= 0:
                return None
            return b // (1024 * 1024)
        except Exception:
            return None

    for bs in block_sizes:
        for dev_count in device_counts:
            selected = parts[:dev_count]
            # If benchmarking the "full device", use the smallest selected device size.
            # Keep a small margin to avoid edge-case end-of-partition alignment quirks.
            full_mb = None
            if use_full_device_size:
                sizes = [s for s in (_device_size_mb(d) for d in selected) if isinstance(s, int) and s > 0]
                if sizes:
                    full_mb = max(1, min(sizes) - int(full_device_size_margin_mb))

            for numjobs in numjobs_per_device_levels:
                for iodepth in iodepth_levels:
                    test_num += 1
                    progress_str = f"[{test_num}/{total_combinations}]"
                    log_info_ts(f"{progress_str} Testing {dev_count} devices × {numjobs} jobs/dev @ {bs}KB (iodepth={iodepth})...")

                    size_mb_to_use = full_mb if (use_full_device_size and full_mb) else int(per_device_size_mb)

                    fio_job_lines = [
                        "[global]",
                        "ioengine=libaio",
                        "direct=1",
                        "time_based",
                        f"runtime={runtime_sec}",
                        "group_reporting",
                        "randrepeat=0",
                        "norandommap",
                        f"iodepth={iodepth}",
                        f"bs={bs}k",
                        "rw=randrw",
                        f"rwmixread={int(rwmixread)}",
                        f"size={size_mb_to_use}m",
                        "",
                    ]

                    # Bind jobs explicitly to their device by making each section a device-specific job.
                    for idx, dev in enumerate(selected, start=1):
                        fio_job_lines.extend([
                            f"[dev{idx}]",
                            f"filename={dev}",
                            f"numjobs={numjobs}",
                            "",
                        ])

                    try:
                        with open(job_path, 'w') as f:
                            f.write("\n".join(fio_job_lines))

                        proc = subprocess.run(
                            ['fio', '--output-format=json', job_path],
                            capture_output=True,
                            text=True,
                            timeout=300,
                        )
                        if proc.returncode != 0:
                            raise subprocess.CalledProcessError(proc.returncode, 'fio', proc.stderr)

                        data = json.loads(proc.stdout)
                        (
                            read_mb_s,
                            write_mb_s,
                            read_lat_mean_us,
                            write_lat_mean_us,
                            read_lat_p99_us,
                            write_lat_p99_us,
                        ) = _fio_sum_bw_and_lat(data)
                        entry = {
                            'block_size_kb': bs,
                            'device_count': dev_count,
                            'numjobs_per_device': numjobs,
                            'iodepth': int(iodepth),
                            'rwmixread': int(rwmixread),
                            'size_mb_per_device': int(size_mb_to_use),
                            'read_mb_per_sec': round(read_mb_s, 2),
                            'write_mb_per_sec': round(write_mb_s, 2),
                            'combined_mb_per_sec': round(read_mb_s + write_mb_s, 2),
                            'read_latency_us': round(read_lat_mean_us, 2),
                            'write_latency_us': round(write_lat_mean_us, 2),
                            'read_latency_p99_us': round(read_lat_p99_us, 2),
                            'write_latency_p99_us': round(write_lat_p99_us, 2),
                        }
                        results['matrix'].append(entry)
                        log_info(
                            f"  Total: ↑{write_mb_s:.1f} ↓{read_mb_s:.1f} MB/s | "
                            + f"lat(mean) r={read_lat_mean_us:.1f}µs w={write_lat_mean_us:.1f}µs | "
                            + f"lat(p99) r={read_lat_p99_us:.1f}µs w={write_lat_p99_us:.1f}µs"
                        )
                    except subprocess.TimeoutExpired:
                        results['matrix'].append({
                            'block_size_kb': bs,
                            'device_count': dev_count,
                            'numjobs_per_device': numjobs,
                            'iodepth': int(iodepth),
                            'rwmixread': int(rwmixread),
                            'error': 'Timeout after 300s',
                            'read_mb_per_sec': 0,
                            'write_mb_per_sec': 0,
                            'combined_mb_per_sec': 0,
                        })
                    except subprocess.CalledProcessError as e:
                        stderr = (e.stderr or '')
                        results['matrix'].append({
                            'block_size_kb': bs,
                            'device_count': dev_count,
                            'numjobs_per_device': numjobs,
                            'iodepth': int(iodepth),
                            'rwmixread': int(rwmixread),
                            'error': f"fio failed: {stderr[:120]}",
                            'read_mb_per_sec': 0,
                            'write_mb_per_sec': 0,
                            'combined_mb_per_sec': 0,
                        })
                    except Exception as e:
                        results['matrix'].append({
                            'block_size_kb': bs,
                            'device_count': dev_count,
                            'numjobs_per_device': numjobs,
                            'iodepth': int(iodepth),
                            'rwmixread': int(rwmixread),
                            'error': str(e)[:120],
                            'read_mb_per_sec': 0,
                            'write_mb_per_sec': 0,
                            'combined_mb_per_sec': 0,
                        })

    valid = [r for r in results['matrix'] if isinstance(r, dict) and 'error' not in r]
    if valid:
        def _latency_weighted_us(row):
            # Reads are usually on the critical path (page faults), writes often amortize.
            if latency_metric == 'mean':
                rlat = float(row.get('read_latency_us', 0) or 0)
                wlat = float(row.get('write_latency_us', 0) or 0)
            else:
                rlat = float(row.get('read_latency_p99_us', 0) or 0)
                wlat = float(row.get('write_latency_p99_us', 0) or 0)
            lat = (0.7 * rlat) + (0.3 * wlat)
            # Guard against missing/zero values.
            return lat if lat > 0 else 1.0

        def _score(row):
            # Higher is better: throughput per unit latency.
            bw = float(row.get('combined_mb_per_sec', 0) or 0)
            return bw / _latency_weighted_us(row)

        # Compute overall best (across all block sizes).
        best = max(valid, key=_score)
        best_score = float(_score(best) or 0.0)

        threshold = best_score * 0.95
        near_best = [r for r in valid if float(_score(r) or 0.0) >= threshold]
        chosen_overall = min(
            near_best,
            key=lambda r: (
                int(r.get('device_count', 1)),
                int(r.get('iodepth', 1)),
                int(r.get('numjobs_per_device', 1)),
                int(r.get('block_size_kb', 4)),
            )
        )

        # Keep top-3 candidates for reporting (overall).
        top = sorted(
            valid,
            key=lambda r: (
                -float(_score(r) or 0.0),
                int(r.get('device_count', 1)),
                int(r.get('iodepth', 1)),
                int(r.get('numjobs_per_device', 1)),
                int(r.get('block_size_kb', 4)),
            )
        )[:3]

        block_to_cluster = {4: 0, 8: 1, 16: 2, 32: 3, 64: 4, 128: 5}

        # Also compute per-block-size optima so we can answer "what if only 4KB accesses?"
        try:
            opt_by_bs = {}
            for bs in sorted({int(r.get('block_size_kb')) for r in valid if isinstance(r.get('block_size_kb'), int)}):
                candidates = [r for r in valid if int(r.get('block_size_kb', -1)) == bs]
                if not candidates:
                    continue
                best_bs = max(candidates, key=_score)
                best_bs_score = float(_score(best_bs) or 0.0)
                threshold_bs = best_bs_score * 0.95
                near_best_bs = [r for r in candidates if float(_score(r) or 0.0) >= threshold_bs]
                chosen_bs = min(
                    near_best_bs,
                    key=lambda r: (
                        int(r.get('device_count', 1)),
                        int(r.get('iodepth', 1)),
                        int(r.get('numjobs_per_device', 1)),
                    )
                )
                opt_by_bs[str(bs)] = {
                    'recommended_swap_stripe_width': int(chosen_bs.get('device_count', 1)),
                    'recommended_fio_numjobs_per_device': int(chosen_bs.get('numjobs_per_device', 1)),
                    'recommended_fio_iodepth': int(chosen_bs.get('iodepth', 1)),
                    'chosen_point': chosen_bs,
                    'best_score': round(best_bs_score, 6),
                    'latency_metric': latency_metric,
                }
            if opt_by_bs:
                results['optimal_by_block_size_kb'] = opt_by_bs
        except Exception:
            pass

        # Selection policy: choose stripe width based on 4KiB-only results when available,
        # because swap traffic is page-sized and latency-sensitive. Prefer the most
        # conservative queueing assumptions (iodepth=1, numjobs/dev=1) when present.
        chosen = chosen_overall
        selection_policy = 'any_block_size'
        try:
            opt4 = (results.get('optimal_by_block_size_kb') or {}).get('4')
            if isinstance(opt4, dict) and isinstance(opt4.get('chosen_point'), dict):
                selection_policy = '4k_only'
                chosen = opt4['chosen_point']

            # Conservative override: if 4K tests include iodepth=1 and numjobs/dev=1,
            # select the best among those to avoid assuming deep queues or parallel jobs.
            conservative = [
                r for r in valid
                if int(r.get('block_size_kb', -1)) == 4
                and int(r.get('iodepth', 1)) == 1
                and int(r.get('numjobs_per_device', 1)) == 1
            ]
            if conservative:
                best_cons = max(conservative, key=_score)
                best_cons_score = float(_score(best_cons) or 0.0)
                threshold_cons = best_cons_score * 0.95
                near_cons = [r for r in conservative if float(_score(r) or 0.0) >= threshold_cons]
                chosen = min(
                    near_cons,
                    key=lambda r: (
                        int(r.get('device_count', 1)),
                        int(r.get('block_size_kb', 4)),
                    )
                )
                selection_policy = '4k_iodepth1_numjobs1'
        except Exception:
            pass

        cluster = block_to_cluster.get(int(chosen.get('block_size_kb', 4)), 0)

        results['optimal'] = {
            # Recommended point (policy-selected; default: 4k_only when available)
            'best_combined': chosen,
            'selection_policy': selection_policy,

            # Overall best information kept for reporting/debugging
            'best_combined_overall': chosen_overall,
            'top_candidates': top,
            'best_score': round(best_score, 6),

            'recommended_swap_stripe_width': int(chosen.get('device_count', 1)),
            'recommended_fio_numjobs_per_device': int(chosen.get('numjobs_per_device', 1)),
            'recommended_fio_iodepth': int(chosen.get('iodepth', 1)),
            'recommended_page_cluster': int(cluster),
            'latency_metric': latency_metric,
        }

        log_step_ts("Swap partition stripe matrix complete")
        try:
            c_lat = _latency_weighted_us(chosen)
            c_bw = float(chosen.get('combined_mb_per_sec', 0) or 0)
            log_info(
                "Recommended settings (latency-aware): "
                + f"SWAP_STRIPE_WIDTH={results['optimal']['recommended_swap_stripe_width']}, "
                + f"SWAP_PAGE_CLUSTER_DISK={results['optimal']['recommended_page_cluster']}, "
                + f"chosen_score={_score(chosen):.4f} (mbps/us), "
                + f"chosen_bw={c_bw:.0f} MB/s, chosen_lat~={c_lat:.1f}us"
            )
        except Exception:
            log_info(
                "Recommended settings: "
                + f"SWAP_STRIPE_WIDTH={results['optimal']['recommended_swap_stripe_width']}, "
                + f"FIO_NUMJOBS_PER_DEVICE={results['optimal']['recommended_fio_numjobs_per_device']}, "
                + f"SWAP_PAGE_CLUSTER_DISK={results['optimal']['recommended_page_cluster']}"
            )

    elapsed = time.time() - start_time
    results['elapsed_sec'] = round(elapsed, 1)
    return results

def compare_memory_only():
    """
    Compare ZRAM vs ZSWAP in memory-only mode
    Note: ZSWAP requires backing device, so this tests ZRAM with different configurations
    """
    log_step("Comparing memory-only configurations")
    
    results = {
        'timestamp': datetime.now().isoformat(),
        'zram_lz4': {},
        'zram_zstd': {}
    }
    
    # Test ZRAM with lz4
    log_info("Testing ZRAM with lz4...")
    results['zram_lz4'] = benchmark_compression('lz4', 'zsmalloc', 100)
    
    # Test ZRAM with zstd
    log_info("Testing ZRAM with zstd...")
    results['zram_zstd'] = benchmark_compression('zstd', 'zsmalloc', 100)
    
    # Compare
    if 'compression_ratio' in results['zram_lz4'] and 'compression_ratio' in results['zram_zstd']:
        log_info("\nComparison:")
        log_info(f"  lz4:  {results['zram_lz4']['compression_ratio']}x compression")
        log_info(f"  zstd: {results['zram_zstd']['compression_ratio']}x compression")
        log_info(f"  zstd advantage: {results['zram_zstd']['compression_ratio'] / results['zram_lz4']['compression_ratio']:.2f}x")
    
    return results

def get_device_io_stats(device_path):
    """
    Get I/O statistics for a block device from /sys/block or /proc/diskstats
    
    Returns dict with sectors_read, sectors_written, sector_size, etc.
    """
    stats = {
        'sectors_read': 0,
        'sectors_written': 0,
        'read_ios': 0,
        'write_ios': 0,
        'sector_size': 512  # Default, will try to read actual value
    }
    
    try:
        # Extract device name from path (e.g., /dev/vda3 -> vda)
        import re
        device_match = re.search(r'/dev/([a-z]+)\d*', device_path)
        if not device_match:
            return stats
        
        base_device = device_match.group(1)
        
        # Get actual sector size from sysfs (usually 512, but can be 4096 for advanced format drives)
        sector_size_path = f'/sys/block/{base_device}/queue/hw_sector_size'
        try:
            if os.path.exists(sector_size_path):
                with open(sector_size_path, 'r') as f:
                    stats['sector_size'] = int(f.read().strip())
                    log_debug(f"Device {base_device} sector size: {stats['sector_size']} bytes")
        except Exception as e:
            log_debug(f"Could not read sector size, using default 512: {e}")
        
        # Try reading from /sys/block first
        stat_path = f'/sys/block/{base_device}/stat'
        if os.path.exists(stat_path):
            with open(stat_path, 'r') as f:
                parts = f.read().split()
                if len(parts) >= 10:
                    stats['read_ios'] = int(parts[0])
                    stats['sectors_read'] = int(parts[2])
                    stats['write_ios'] = int(parts[4])
                    stats['sectors_written'] = int(parts[6])
                    return stats
        
        # Fallback to /proc/diskstats
        with open('/proc/diskstats', 'r') as f:
            for line in f:
                parts = line.split()
                if len(parts) >= 14 and parts[2] == base_device:
                    stats['read_ios'] = int(parts[3])
                    stats['sectors_read'] = int(parts[5])
                    stats['write_ios'] = int(parts[7])
                    stats['sectors_written'] = int(parts[9])
                    break
    except Exception as e:
        log_debug(f"Could not read device stats: {e}")
    
    return stats


def get_zswap_stats():
    """Read ZSWAP stats from debugfs if available."""
    stats = {}
    debugfs_path = '/sys/kernel/debug/zswap'

    if os.path.exists(debugfs_path):
        try:
            for stat_file in [
                'pool_total_size',
                'stored_pages',
                'pool_limit_hit',
                'written_back_pages',
                'reject_compress_poor',
                'reject_alloc_fail',
            ]:
                stat_path = os.path.join(debugfs_path, stat_file)
                if os.path.exists(stat_path):
                    with open(stat_path, 'r') as f:
                        stats[stat_file] = int(f.read().strip())
        except Exception as e:
            log_debug(f"Could not read debugfs stats: {e}")

    return stats

def benchmark_zswap_comprehensive(swap_device='/dev/vda4', test_size_mb=256, compressor='lz4', zpool='z3fold', max_pool_percent=20):
    """
    Comprehensive ZSWAP benchmarking using same methodology as ZRAM tests:
    - RAM compression performance (using mem_pressure like ZRAM tests)
    - Compression ratio measurement
    - Disk overflow detection via device I/O counters
    - Writeback statistics from ZSWAP debugfs
    
    This ensures fair comparison between ZSWAP and ZRAM.
    
    Args:
        swap_device: Swap partition device (e.g., /dev/vda4)
        test_size_mb: Size of memory to test in MB
        compressor: Compression algorithm (lz4, zstd, lzo-rle)
        zpool: Memory pool (z3fold, zbud, zsmalloc)
        max_pool_percent: Max % of RAM for ZSWAP pool (default 20%)
    
    Returns:
        Dictionary with comprehensive ZSWAP metrics
    """
    start_time = time.time()
    log_step_ts(f"Comprehensive ZSWAP Benchmark: {compressor} + {zpool}")
    log_info(f"Using swap device: {swap_device}")
    log_info(f"Max pool percent: {max_pool_percent}%")
    
    results = {
        'compressor': compressor,
        'zpool': zpool,
        'swap_device': swap_device,
        'test_size_mb': test_size_mb,
        'max_pool_percent': max_pool_percent,
        'timestamp': datetime.now().isoformat()
    }
    
    mem_locker_proc = None
    
    try:
        # Step 1: Check if swap device exists and is a block device
        if not os.path.exists(swap_device):
            log_error(f"Swap device {swap_device} does not exist")
            results['error'] = f'Swap device not found: {swap_device}'
            return results
        
        if not os.path.isfile(swap_device) and not os.path.exists(swap_device):
            log_error(f"{swap_device} is not a valid block device")
            results['error'] = f'Invalid device: {swap_device}'
            return results
        
        # Step 2: Format and enable swap device
        log_info(f"Formatting {swap_device} as swap...")
        try:
            subprocess.run(['mkswap', swap_device], capture_output=True, check=True)
            subprocess.run(['swapon', swap_device], capture_output=True, check=True)
            log_success(f"Swap device {swap_device} enabled")
        except subprocess.CalledProcessError as e:
            log_error(f"Failed to setup swap device: {e}")
            results['error'] = f'Failed to setup swap: {e}'
            return results
        
        # Step 3: Check if ZSWAP is available
        if not os.path.exists('/sys/module/zswap'):
            log_error("ZSWAP not available (module not loaded)")
            results['error'] = 'ZSWAP module not available'
            return results
        
        # Step 4: Configure ZSWAP
        log_info("Configuring ZSWAP...")
        
        # Enable ZSWAP
        with open('/sys/module/zswap/parameters/enabled', 'w') as f:
            f.write('Y')
        
        # Set compressor
        try:
            with open('/sys/module/zswap/parameters/compressor', 'w') as f:
                f.write(compressor)
            log_info(f"Set compressor to {compressor}")
        except Exception as e:
            log_warn(f"Could not set compressor to {compressor}: {e}")
        
        # Set zpool
        try:
            with open('/sys/module/zswap/parameters/zpool', 'w') as f:
                f.write(zpool)
            log_info(f"Set zpool to {zpool}")
        except Exception as e:
            log_warn(f"Could not set zpool to {zpool}: {e}")
        
        # Set max pool percent
        try:
            with open('/sys/module/zswap/parameters/max_pool_percent', 'w') as f:
                f.write(str(max_pool_percent))
            log_info(f"Set max_pool_percent to {max_pool_percent}%")
        except Exception as e:
            log_warn(f"Could not set max_pool_percent: {e}")
        
        log_success(f"ZSWAP enabled: {compressor} + {zpool}")
        
        # Step 5: Get initial device I/O stats
        initial_device_stats = get_device_io_stats(swap_device)
        log_debug(f"Initial device stats: {initial_device_stats}")
        
        # Step 6: Get initial ZSWAP stats
        initial_zswap_stats = get_zswap_stats()
        log_debug(f"Initial ZSWAP stats: {initial_zswap_stats}")
        
        # Step 7: Run memory pressure test (SAME as ZRAM test)
        log_info("Running memory pressure test (same methodology as ZRAM)...")
        
        # Use mem_locker if available
        script_dir = Path(__file__).parent
        mem_locker_path = script_dir / 'mem_locker'
        
        # Calculate memory distribution
        test_alloc_mb, lock_mb, available_mb = calculate_memory_distribution(test_size_mb)
        
        if lock_mb > 100 and mem_locker_path.exists():
            try:
                log_info_ts(f"Starting mem_locker to reserve {lock_mb}MB of free RAM...")
                mem_locker_proc = subprocess.Popen(
                    [str(mem_locker_path), str(lock_mb)],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True
                )
                time.sleep(2)
                
                if mem_locker_proc.poll() is not None:
                    log_warn("mem_locker exited prematurely, continuing without it")
                    mem_locker_proc = None
                else:
                    log_info(f"✓ mem_locker running (PID: {mem_locker_proc.pid})")
            except Exception as e:
                log_warn(f"Failed to start mem_locker: {e}")
                mem_locker_proc = None
        
        # Get available memory and calculate allocation size
        try:
            with open('/proc/meminfo', 'r') as f:
                meminfo = f.read()
            mem_available_kb = 0
            for line in meminfo.split('\n'):
                if line.startswith('MemAvailable:'):
                    mem_available_kb = int(line.split()[1])
                    break
            
            # Allocate slightly more than available to encourage swapping, but cap to avoid OOM storms.
            mem_available_mb = mem_available_kb // 1024
            extra_alloc_cap = 256 if get_system_info().get('ram_gb', 0) <= 8 else 1024
            alloc_size_mb = max(test_size_mb, mem_available_mb + min(test_size_mb, extra_alloc_cap))
            log_info(f"Allocating {alloc_size_mb}MB to force swapping...")
        except:
            alloc_size_mb = test_size_mb
        
        # Use mem_pressure (same as ZRAM tests)
        mem_pressure_path = script_dir / 'mem_pressure'
        
        if not mem_pressure_path.exists():
            log_error(f"mem_pressure program not found at {mem_pressure_path}")
            results['error'] = "mem_pressure program not found"
            return results
        
        log_info_ts(f"Running mem_pressure ({alloc_size_mb}MB)...")
        
        try:
            result = subprocess.run(
                [str(mem_pressure_path), str(alloc_size_mb), '0', '15'],
                capture_output=True,
                text=True,
                timeout=COMPRESSION_TEST_TIMEOUT_SEC
            )
            
            if result.returncode != 0:
                log_error(f"mem_pressure failed: {result.stderr}")
                results['error'] = f"mem_pressure failed: {result.stderr}"
                return results
            
            log_info("Memory pressure test completed")
        except subprocess.TimeoutExpired:
            log_error(f"Memory pressure test timed out after {COMPRESSION_TEST_TIMEOUT_SEC}s")
            results['error'] = 'Timeout'
            return results
        
        # Step 8: Get final stats
        final_device_stats = get_device_io_stats(swap_device)
        final_zswap_stats = get_zswap_stats()
        
        log_debug(f"Final device stats: {final_device_stats}")
        log_debug(f"Final ZSWAP stats: {final_zswap_stats}")
        
        # Step 9: Calculate compression metrics (from ZSWAP pool)
        if 'pool_total_size' in final_zswap_stats and 'stored_pages' in final_zswap_stats:
            pool_size_bytes = final_zswap_stats['pool_total_size']
            stored_pages = final_zswap_stats['stored_pages']
            uncompressed_bytes = stored_pages * 4096  # 4KB pages
            
            results['pool_size_mb'] = round(pool_size_bytes / (1024 * 1024), 2)
            results['stored_pages'] = stored_pages
            results['uncompressed_mb'] = round(uncompressed_bytes / (1024 * 1024), 2)
            
            if pool_size_bytes > 0:
                compression_ratio = uncompressed_bytes / pool_size_bytes
                results['compression_ratio'] = round(compression_ratio, 2)
                log_info(f"  Compression ratio: {compression_ratio:.2f}x")
                log_info(f"  Pool size: {results['pool_size_mb']:.2f}MB (compressed)")
                log_info(f"  Original size: {results['uncompressed_mb']:.2f}MB (uncompressed)")
            else:
                log_warn("No data in ZSWAP pool")
        
        # Step 10: Calculate disk overflow metrics
        sectors_written_delta = final_device_stats['sectors_written'] - initial_device_stats['sectors_written']
        sectors_read_delta = final_device_stats['sectors_read'] - initial_device_stats['sectors_read']
        write_ios_delta = final_device_stats['write_ios'] - initial_device_stats['write_ios']
        read_ios_delta = final_device_stats['read_ios'] - initial_device_stats['read_ios']
        
        # Get sector size (typically 512, but can be 4096 for advanced format drives)
        sector_size = final_device_stats.get('sector_size', 512)
        
        # Convert sectors to MB using actual sector size
        mb_written = (sectors_written_delta * sector_size) / (1024 * 1024)
        mb_read = (sectors_read_delta * sector_size) / (1024 * 1024)
        
        results['disk_mb_written'] = round(mb_written, 2)
        results['disk_mb_read'] = round(mb_read, 2)
        results['disk_write_ios'] = write_ios_delta
        results['disk_read_ios'] = read_ios_delta
        
        log_info(f"  Disk overflow: {mb_written:.2f}MB written, {mb_read:.2f}MB read")
        log_info(f"  Disk I/Os: {write_ios_delta} writes, {read_ios_delta} reads")
        
        # Step 11: ZSWAP writeback stats
        if 'written_back_pages' in final_zswap_stats and 'written_back_pages' in initial_zswap_stats:
            pages_written_back = final_zswap_stats['written_back_pages'] - initial_zswap_stats.get('written_back_pages', 0)
            mb_written_back = (pages_written_back * 4096) / (1024 * 1024)
            results['zswap_writeback_pages'] = pages_written_back
            results['zswap_writeback_mb'] = round(mb_written_back, 2)
            log_info(f"  ZSWAP writeback: {pages_written_back} pages ({mb_written_back:.2f}MB)")
        
        if 'pool_limit_hit' in final_zswap_stats:
            pool_limit_hits = final_zswap_stats['pool_limit_hit'] - initial_zswap_stats.get('pool_limit_hit', 0)
            results['pool_limit_hits'] = pool_limit_hits
            if pool_limit_hits > 0:
                log_info(f"  Pool limit hit {pool_limit_hits} times (triggered writeback)")
        
        if 'reject_compress_poor' in final_zswap_stats:
            rejects = final_zswap_stats['reject_compress_poor'] - initial_zswap_stats.get('reject_compress_poor', 0)
            if rejects > 0:
                results['reject_compress_poor'] = rejects
                log_info(f"  Rejected {rejects} pages (poor compression)")
        
        log_success("ZSWAP benchmark complete")
        
    except Exception as e:
        log_error(f"ZSWAP benchmark failed: {e}")
        import traceback
        log_debug(f"Traceback: {traceback.format_exc()}")
        results['error'] = str(e)
    
    finally:
        # Cleanup mem_locker if it was started
        if mem_locker_proc is not None:
            try:
                log_info("Stopping mem_locker...")
                mem_locker_proc.terminate()
                mem_locker_proc.wait(timeout=5)
                log_info("✓ mem_locker stopped")
            except subprocess.TimeoutExpired:
                log_warn("mem_locker didn't stop gracefully, killing it")
                mem_locker_proc.kill()
                mem_locker_proc.wait()
            except Exception as e:
                log_warn(f"Error stopping mem_locker: {e}")
        
        # Cleanup swap
        try:
            subprocess.run(['swapoff', swap_device], capture_output=True)
        except Exception as e:
            log_debug(f"Swapoff warning: {e}")
        
        # Disable ZSWAP
        try:
            if os.path.exists('/sys/module/zswap/parameters/enabled'):
                with open('/sys/module/zswap/parameters/enabled', 'w') as f:
                    f.write('N')
        except Exception as e:
            log_debug(f"ZSWAP disable warning: {e}")
    
    # Log completion time
    elapsed = time.time() - start_time
    results['elapsed_sec'] = round(elapsed, 1)
    log_info(f"✓ ZSWAP benchmark completed in {elapsed:.1f}s")
    
    return results

def create_temp_swap_files(total_size_mb=2048, num_files=4):
    """Create temporary swap files for ZSWAP testing before partitions exist"""
    swap_files = []
    file_size_mb = total_size_mb // num_files

    def _fs_type(path: str) -> str:
        try:
            r = subprocess.run(['stat', '-f', '-c', '%T', path], capture_output=True, text=True, check=False)
            return (r.stdout or '').strip()
        except Exception:
            return ''

    def _has_space(path: str, needed_mb: int) -> bool:
        try:
            import shutil
            stat = shutil.disk_usage(path)
            # +1GiB cushion
            return stat.free >= (needed_mb * 1024 * 1024 + 1024**3)
        except Exception:
            return False

    # Prefer disk-backed dirs (NOT tmpfs), since swapon on tmpfs often fails with "Invalid argument"
    candidates = ['/var/tmp', '/root', '/tmp']
    base_dir = None
    for candidate in candidates:
        if not os.path.isdir(candidate):
            continue
        fstype = _fs_type(candidate)
        if fstype in {'tmpfs', 'ramfs', 'overlay', 'squashfs'}:
            continue
        if _has_space(candidate, total_size_mb):
            base_dir = candidate
            break
    if base_dir is None:
        # Last resort: try /var/tmp even if fstype detection failed
        base_dir = '/var/tmp' if os.path.isdir('/var/tmp') else '/root'
    
    try:
        for i in range(num_files):
            swap_file = f"{base_dir}/swap_test_{os.getpid()}_{i}"
            log_info(f"Creating {file_size_mb}MB swap file: {swap_file} (dir={base_dir})")

            # Prefer fallocate (fast) but fall back to dd
            fallocate_ok = subprocess.run(
                ['fallocate', '-l', f'{file_size_mb}M', swap_file],
                capture_output=True
            ).returncode == 0
            if not fallocate_ok:
                subprocess.run(
                    ['dd', 'if=/dev/zero', f'of={swap_file}', 'bs=1M', f'count={file_size_mb}'],
                    capture_output=True,
                    check=True
                )
            subprocess.run(['chmod', '600', swap_file], check=True)
            subprocess.run(['mkswap', swap_file], capture_output=True, check=True)
            subprocess.run(['swapon', swap_file], check=True)
            swap_files.append(swap_file)
            
        return swap_files
    except Exception as e:
        log_error(f"Failed to create temp swap: {e}")
        # Cleanup any created files
        for f in swap_files:
            try:
                subprocess.run(['swapoff', f], capture_output=True)
                os.unlink(f)
            except:
                pass
        return []

def cleanup_temp_swap_files(swap_files):
    """Remove temporary swap files"""
    for swap_file in swap_files:
        try:
            subprocess.run(['swapoff', swap_file], capture_output=True)
            os.unlink(swap_file)
            log_info(f"Cleaned up {swap_file}")
        except Exception as e:
            log_warn(f"Error cleaning up {swap_file}: {e}")

def compare_zswap_vs_zram(swap_device='/dev/vda4', test_size_mb=256):
    """
    Compare ZSWAP vs ZRAM performance using identical testing methodology
    
    Tests both systems with lz4 and zstd compressors using the same
    mem_pressure tool for fair comparison. Provides comprehensive metrics:
    - Compression ratio (RAM efficiency)
    - Memory pressure handling
    - For ZSWAP: disk overflow behavior via device counters
    
    Args:
        swap_device: Swap partition for ZSWAP backing (e.g., /dev/vda4)
        test_size_mb: Size of memory to test in MB
    
    Returns:
        Dictionary with comparison results
    """
    log_step_ts("Comparing ZSWAP vs ZRAM (identical methodology)")
    
    # Create temp swap if no device provided
    temp_swap_files = []
    if not swap_device or not os.path.exists(swap_device):
        log_info("No swap device available - creating temporary swap files")
        temp_swap_files = create_temp_swap_files(total_size_mb=2048, num_files=4)
        if temp_swap_files:
            swap_device = temp_swap_files[0]  # Use first file for testing
        else:
            log_warn("Could not create temp swap - skipping ZSWAP comparison")
            return {'skipped': True, 'reason': 'No swap device available'}
    
    results = {
        'test_size_mb': test_size_mb,
        'swap_device': swap_device,
        'timestamp': datetime.now().isoformat(),
        'zram': {},
        'zswap': {}
    }
    
    # Test ZRAM with lz4
    log_info("\n=== Testing ZRAM with lz4 ===")
    try:
        results['zram']['lz4'] = benchmark_compression('lz4', 'zsmalloc', test_size_mb)
    except Exception as e:
        log_error(f"ZRAM lz4 test failed: {e}")
        results['zram']['lz4'] = {'error': str(e)}
    
    # Test ZRAM with zstd
    log_info("\n=== Testing ZRAM with zstd ===")
    try:
        results['zram']['zstd'] = benchmark_compression('zstd', 'zsmalloc', test_size_mb)
    except Exception as e:
        log_error(f"ZRAM zstd test failed: {e}")
        results['zram']['zstd'] = {'error': str(e)}
    
    # Test ZSWAP with lz4
    log_info("\n=== Testing ZSWAP with lz4 ===")
    try:
        results['zswap']['lz4'] = benchmark_zswap_comprehensive(
            swap_device=swap_device,
            test_size_mb=test_size_mb,
            compressor='lz4',
            zpool='z3fold',
            max_pool_percent=20
        )
    except Exception as e:
        log_error(f"ZSWAP lz4 test failed: {e}")
        results['zswap']['lz4'] = {'error': str(e)}
    
    # Test ZSWAP with zstd
    log_info("\n=== Testing ZSWAP with zstd ===")
    try:
        results['zswap']['zstd'] = benchmark_zswap_comprehensive(
            swap_device=swap_device,
            test_size_mb=test_size_mb,
            compressor='zstd',
            zpool='z3fold',
            max_pool_percent=20
        )
    except Exception as e:
        log_error(f"ZSWAP zstd test failed: {e}")
        results['zswap']['zstd'] = {'error': str(e)}
    
    # Generate comparison summary
    log_info("\n=== Comparison Summary ===")
    
    for comp in ['lz4', 'zstd']:
        log_info(f"\n{comp.upper()} Compressor:")
        
        # ZRAM metrics
        if 'compression_ratio' in results['zram'].get(comp, {}):
            zram_ratio = results['zram'][comp]['compression_ratio']
            log_info(f"  ZRAM compression: {zram_ratio:.2f}x")
            
            if 'orig_size_mb' in results['zram'][comp]:
                log_info(f"    Original: {results['zram'][comp]['orig_size_mb']:.2f}MB")
                log_info(f"    Compressed: {results['zram'][comp]['compr_size_mb']:.2f}MB")
        
        # ZSWAP metrics
        if 'compression_ratio' in results['zswap'].get(comp, {}):
            zswap_ratio = results['zswap'][comp]['compression_ratio']
            log_info(f"  ZSWAP compression: {zswap_ratio:.2f}x")
            
            if 'uncompressed_mb' in results['zswap'][comp]:
                log_info(f"    Original: {results['zswap'][comp]['uncompressed_mb']:.2f}MB")
                log_info(f"    Compressed: {results['zswap'][comp]['pool_size_mb']:.2f}MB")
        
        # ZSWAP disk overflow
        if 'disk_mb_written' in results['zswap'].get(comp, {}):
            disk_written = results['zswap'][comp]['disk_mb_written']
            if disk_written > 0:
                log_info(f"  ZSWAP disk overflow: {disk_written:.2f}MB written to disk")
                
                if 'zswap_writeback_mb' in results['zswap'][comp]:
                    log_info(f"    Via ZSWAP writeback: {results['zswap'][comp]['zswap_writeback_mb']:.2f}MB")
                
                if 'pool_limit_hits' in results['zswap'][comp]:
                    log_info(f"    Pool limit hits: {results['zswap'][comp]['pool_limit_hits']}")
            else:
                log_info(f"  ZSWAP: All data stayed in RAM (no disk overflow)")
    
    # Cleanup
    if temp_swap_files:
        cleanup_temp_swap_files(temp_swap_files)
    
    return results

def benchmark_zswap_latency(swap_devices=None, compressor='lz4', zpool='zbud', test_size_mb=512):
    """
    Test ZSWAP cache latency with real disk backing.
    NOW POSSIBLE: Using real swap partitions created from matrix test results.
    
    Improved testing methodology:
    - Pre-locks free RAM to force ZSWAP activity (not just free memory compression)
    - Uses larger test size (512MB default) for realistic pressure
    - Multiple test passes: hot cache hits, then cold disk reads
    - Accurate disk I/O measurement across all swap devices
    
    Measures:
    - Hot cache hits (from ZSWAP pool in RAM)
    - Cold page faults (from disk through ZSWAP)
    - Writeback performance (ZSWAP → disk eviction)
    - Comparison with ZRAM baseline
    
    Args:
        swap_devices: List of swap device paths (e.g., ['/dev/vda4', '/dev/vda5'])
                     If None, auto-detects from swapon --show
        compressor: Compression algorithm (lz4, zstd, lzo-rle)
        zpool: Memory allocator (z3fold, zbud, zsmalloc)
        test_size_mb: Size of memory to test in MB (default 512MB for realistic pressure)
    
    Returns:
        Dictionary with latency statistics and comparison data
    """
    log_step_ts("ZSWAP Latency Testing with Real Disk Backing")
    
    results = {
        'timestamp': datetime.now().isoformat(),
        'test_size_mb': test_size_mb,
        'compressor': compressor,
        'zpool': zpool,
        'zswap': {},
        'zram_baseline': {},
        'comparison': {}
    }
    
    # Auto-detect swap devices if not provided
    if swap_devices is None:
        log_info("Auto-detecting swap devices...")
        try:
            swapon_output = subprocess.run(['swapon', '--show', '--noheadings'],
                                          capture_output=True, text=True, check=True)
            swap_devices = []
            for line in swapon_output.stdout.strip().split('\n'):
                if line:
                    device = line.split()[0]
                    # Filter out zram devices
                    if not device.startswith('/dev/zram'):
                        swap_devices.append(device)
            
            if not swap_devices:
                log_error("No non-ZRAM swap devices found")
                log_info("Run: sudo ./create-swap-partitions.sh first")
                results['error'] = 'No swap devices available'
                return results
            
            log_info(f"Found {len(swap_devices)} swap device(s): {', '.join(swap_devices)}")
        except subprocess.CalledProcessError as e:
            log_error(f"Failed to detect swap devices: {e}")
            results['error'] = 'Failed to detect swap devices'
            return results
    
    results['swap_devices'] = swap_devices
    results['swap_device_count'] = len(swap_devices)
    
    # Disable any existing ZRAM
    try:
        log_info("Disabling ZRAM...")
        subprocess.run(['swapoff', '/dev/zram0'], capture_output=True, stderr=subprocess.DEVNULL)
        subprocess.run(['rmmod', 'zram'], capture_output=True, stderr=subprocess.DEVNULL)
    except:
        pass
    
    # Test 1: ZRAM Baseline (for comparison)
    log_info("\n=== Phase 1: ZRAM Baseline (memory-only) ===")
    try:
        results['zram_baseline'] = benchmark_compression(compressor, zpool, test_size_mb)
        if 'error' not in results['zram_baseline']:
            log_success(f"ZRAM baseline: {results['zram_baseline'].get('compression_ratio', 0):.2f}x compression")
    except Exception as e:
        log_warn(f"ZRAM baseline test failed: {e}")
        results['zram_baseline'] = {'error': str(e)}
    
    # Ensure ZRAM is cleaned up
    try:
        subprocess.run(['swapoff', '/dev/zram0'], capture_output=True, stderr=subprocess.DEVNULL)
        subprocess.run(['rmmod', 'zram'], capture_output=True, stderr=subprocess.DEVNULL)
        time.sleep(2)
    except:
        pass
    
    # Test 2: ZSWAP with Real Disk Backing
    log_info("\n=== Phase 2: ZSWAP with Real Disk Backing ===")
    
    # Pre-lock free RAM to force realistic ZSWAP pressure
    mem_locker_process = None
    script_dir = Path(__file__).parent
    mem_locker_path = script_dir / 'mem_locker'
    
    try:
        # Get available free memory
        with open('/proc/meminfo', 'r') as f:
            meminfo = f.read()
        mem_available_kb = 0
        for line in meminfo.split('\n'):
            if line.startswith('MemAvailable:'):
                mem_available_kb = int(line.split()[1])
                break
        
        mem_available_mb = mem_available_kb // 1024
        
        # Lock 60% of available RAM to create pressure
        # Leave enough for ZSWAP pool + kernel + test
        if mem_available_mb > 512:
            lock_mb = int(mem_available_mb * 0.6)
            log_info(f"Pre-locking {lock_mb}MB of free RAM to force ZSWAP activity...")
            
            if mem_locker_path.exists():
                mem_locker_process = subprocess.Popen(
                    [str(mem_locker_path), str(lock_mb)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL
                )
                time.sleep(3)  # Allow time for locking
                
                if mem_locker_process.poll() is None:
                    log_success(f"Locked {lock_mb}MB RAM (60% of {mem_available_mb}MB available)")
                else:
                    log_warn("mem_locker exited early, continuing without pre-locking")
                    mem_locker_process = None
            else:
                log_warn(f"mem_locker not found at {mem_locker_path}, skipping pre-lock")
        else:
            log_info(f"Only {mem_available_mb}MB available, skipping pre-lock")
    except Exception as e:
        log_warn(f"Failed to pre-lock memory: {e}")
    
    # Enable ZSWAP
    try:
        log_info(f"Enabling ZSWAP: {compressor} + {zpool}")
        
        if not os.path.exists('/sys/module/zswap'):
            subprocess.run(['modprobe', 'zswap'], check=True)
            time.sleep(1)
        
        # Configure ZSWAP
        with open('/sys/module/zswap/parameters/enabled', 'w') as f:
            f.write('Y\n')
        with open('/sys/module/zswap/parameters/compressor', 'w') as f:
            f.write(f'{compressor}\n')
        with open('/sys/module/zswap/parameters/zpool', 'w') as f:
            f.write(f'{zpool}\n')
        with open('/sys/module/zswap/parameters/max_pool_percent', 'w') as f:
            f.write('20\n')  # 20% of RAM for ZSWAP pool
        
        # Enable accept_threshold_percent if available (kernel 6.0+)
        accept_thresh_path = '/sys/module/zswap/parameters/accept_threshold_percent'
        if os.path.exists(accept_thresh_path):
            with open(accept_thresh_path, 'w') as f:
                f.write('90\n')
        
        log_success("ZSWAP enabled")
        
    except Exception as e:
        log_error(f"Failed to enable ZSWAP: {e}")
        results['error'] = f'Failed to enable ZSWAP: {e}'
        return results
    
    # Get initial ZSWAP stats
    initial_zswap_stats = get_zswap_stats()
    
    # Get initial disk I/O stats for all swap devices
    initial_disk_stats = {}
    for device in swap_devices:
        initial_disk_stats[device] = get_device_io_stats(device)
    
    # Run memory pressure test - multiple passes for hot/cold measurement
    mem_pressure_path = script_dir / 'mem_pressure'
    
    if not mem_pressure_path.exists():
        log_error(f"mem_pressure program not found at {mem_pressure_path}")
        results['error'] = 'mem_pressure program not found'
        # Clean up mem_locker if running
        if mem_locker_process and mem_locker_process.poll() is None:
            mem_locker_process.terminate()
            mem_locker_process.wait(timeout=5)
        return results
    
    log_info(f"Running memory pressure test ({test_size_mb}MB with pre-locked RAM)...")
    log_info("This will force ZSWAP pool fills and writeback to disk")
    start_time = time.time()
    
    # Track ZSWAP stats over time for graphing
    stats_timeseries = []
    mem_pressure_proc = None
    
    try:
        # Start mem_pressure in background to collect stats during execution
        mem_pressure_proc = subprocess.Popen(
            [str(mem_pressure_path), str(test_size_mb), '0', '30'],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        
        # Collect ZSWAP stats every 2 seconds during test
        while mem_pressure_proc.poll() is None:
            current_stats = get_zswap_stats()
            stats_timeseries.append((time.time(), current_stats))
            time.sleep(2)
        
        # Get final return code
        returncode = mem_pressure_proc.wait(timeout=10)
        
        # Get final stats
        final_stats = get_zswap_stats()
        stats_timeseries.append((time.time(), final_stats))
        
        if returncode != 0:
            log_warn(f"mem_pressure returned non-zero: {returncode}")
        
        elapsed = time.time() - start_time
        log_info(f"Memory pressure test completed in {elapsed:.1f}s")
        log_info(f"Collected {len(stats_timeseries)} ZSWAP stat snapshots")
        
    except subprocess.TimeoutExpired:
        log_error("Memory pressure test timed out")
        results['error'] = 'Memory pressure test timeout'
        # Clean up mem_locker
        if mem_locker_process and mem_locker_process.poll() is None:
            mem_locker_process.terminate()
            mem_locker_process.wait(timeout=5)
        return results
    except Exception as e:
        log_error(f"Memory pressure test failed: {e}")
        results['error'] = str(e)
        # Clean up mem_locker
        if mem_locker_process and mem_locker_process.poll() is None:
            mem_locker_process.terminate()
            mem_locker_process.wait(timeout=5)
        return results
    finally:
        # Always clean up mem_locker
        if mem_locker_process and mem_locker_process.poll() is None:
            log_info("Releasing pre-locked RAM...")
            mem_locker_process.terminate()
            try:
                mem_locker_process.wait(timeout=5)
                log_success("Pre-locked RAM released")
            except subprocess.TimeoutExpired:
                log_warn("mem_locker did not terminate gracefully, killing...")
                mem_locker_process.kill()
                mem_locker_process.wait()
    
    # Generate ZSWAP stats chart
    if stats_timeseries and MATPLOTLIB_AVAILABLE:
        output_dir = '/var/log/debian-install'
        chart_file = generate_zswap_stats_chart(stats_timeseries, output_dir)
        if chart_file:
            results['zswap']['stats_chart'] = chart_file
            log_success(f"ZSWAP stats chart: {chart_file}")
    
    # Get final ZSWAP stats (use last from timeseries)
    final_zswap_stats = stats_timeseries[-1][1] if stats_timeseries else get_zswap_stats()
    
    # Get final disk I/O stats
    final_disk_stats = {}
    for device in swap_devices:
        final_disk_stats[device] = get_device_io_stats(device)
    
    # Calculate ZSWAP pool metrics
    if 'pool_total_size' in final_zswap_stats and 'stored_pages' in final_zswap_stats:
        pool_size_bytes = final_zswap_stats['pool_total_size']
        stored_pages = final_zswap_stats['stored_pages']
        uncompressed_bytes = stored_pages * 4096
        
        results['zswap']['pool_size_mb'] = round(pool_size_bytes / (1024 * 1024), 2)
        results['zswap']['stored_pages'] = stored_pages
        results['zswap']['uncompressed_mb'] = round(uncompressed_bytes / (1024 * 1024), 2)
        
        if pool_size_bytes > 0:
            compression_ratio = uncompressed_bytes / pool_size_bytes
            results['zswap']['compression_ratio'] = round(compression_ratio, 2)
            log_info(f"  ZSWAP pool: {results['zswap']['pool_size_mb']:.2f}MB compressed")
            log_info(f"  Original size: {results['zswap']['uncompressed_mb']:.2f}MB")
            log_info(f"  Compression ratio: {compression_ratio:.2f}x")
    
    # Calculate disk I/O totals across all devices
    total_sectors_written = 0
    total_sectors_read = 0
    total_write_ios = 0
    total_read_ios = 0
    
    for device in swap_devices:
        if device in initial_disk_stats and device in final_disk_stats:
            initial = initial_disk_stats[device]
            final = final_disk_stats[device]
            
            sectors_written = final['sectors_written'] - initial['sectors_written']
            sectors_read = final['sectors_read'] - initial['sectors_read']
            write_ios = final['write_ios'] - initial['write_ios']
            read_ios = final['read_ios'] - initial['read_ios']
            
            total_sectors_written += sectors_written
            total_sectors_read += sectors_read
            total_write_ios += write_ios
            total_read_ios += read_ios
            
            sector_size = final.get('sector_size', 512)
            mb_written = (sectors_written * sector_size) / (1024 * 1024)
            mb_read = (sectors_read * sector_size) / (1024 * 1024)
            
            log_debug(f"  {device}: {mb_written:.2f}MB written, {mb_read:.2f}MB read")
    
    # Convert to MB using first device's sector size (should be same for all)
    first_device = swap_devices[0]
    sector_size = final_disk_stats[first_device].get('sector_size', 512)
    total_mb_written = (total_sectors_written * sector_size) / (1024 * 1024)
    total_mb_read = (total_sectors_read * sector_size) / (1024 * 1024)
    
    results['zswap']['disk_mb_written'] = round(total_mb_written, 2)
    results['zswap']['disk_mb_read'] = round(total_mb_read, 2)
    results['zswap']['disk_write_ios'] = total_write_ios
    results['zswap']['disk_read_ios'] = total_read_ios
    
    log_info(f"  Total disk I/O: {total_mb_written:.2f}MB written, {total_mb_read:.2f}MB read")
    log_info(f"  Total I/O operations: {total_write_ios} writes, {total_read_ios} reads")
    
    # ZSWAP writeback stats
    if 'written_back_pages' in final_zswap_stats:
        pages_written_back = final_zswap_stats['written_back_pages'] - initial_zswap_stats.get('written_back_pages', 0)
        mb_written_back = (pages_written_back * 4096) / (1024 * 1024)
        results['zswap']['writeback_pages'] = pages_written_back
        results['zswap']['writeback_mb'] = round(mb_written_back, 2)
        log_info(f"  ZSWAP writeback: {pages_written_back} pages ({mb_written_back:.2f}MB)")
    
    if 'pool_limit_hit' in final_zswap_stats:
        pool_limit_hits = final_zswap_stats['pool_limit_hit'] - initial_zswap_stats.get('pool_limit_hit', 0)
        results['zswap']['pool_limit_hits'] = pool_limit_hits
        if pool_limit_hits > 0:
            log_info(f"  Pool limit hit {pool_limit_hits} times (triggered writeback)")
    
    # Test 3: Latency Estimates
    log_info("\n=== Phase 3: Latency Analysis ===")
    
    # Estimate hot cache latency (ZSWAP pool access)
    # Based on: decompression time + RAM access (~5µs for lz4, ~10µs for zstd)
    if compressor == 'lz4':
        estimated_hot_latency_us = 5
    elif compressor == 'zstd':
        estimated_hot_latency_us = 10
    else:
        estimated_hot_latency_us = 7
    
    results['zswap']['estimated_hot_latency_us'] = estimated_hot_latency_us
    log_info(f"  Hot cache (ZSWAP pool): ~{estimated_hot_latency_us}µs (estimated)")
    
    # Estimate cold latency (disk read through ZSWAP)
    # Based on: disk latency (~5000µs for HDD) + decompression (~5-10µs)
    # For actual measurement, would need instrumentation or custom kernel module
    if total_read_ios > 0:
        # Average disk read time = total test time / read I/Os
        avg_disk_latency_us = (elapsed * 1000000) / total_read_ios
        results['zswap']['avg_disk_read_latency_us'] = round(avg_disk_latency_us, 0)
        results['zswap']['estimated_cold_latency_us'] = round(avg_disk_latency_us, 0)
        log_info(f"  Cold page (disk read): ~{avg_disk_latency_us:.0f}µs (measured average)")
    else:
        # No disk reads - all data stayed in ZSWAP pool
        log_info(f"  Cold page latency: N/A (no disk reads - all data in ZSWAP pool)")
    
    # Calculate writeback throughput
    if total_mb_written > 0 and elapsed > 0:
        writeback_throughput = total_mb_written / elapsed
        results['zswap']['writeback_throughput_mbps'] = round(writeback_throughput, 2)
        log_info(f"  Writeback throughput: {writeback_throughput:.2f} MB/s")
    
    # Test 4: Comparison Summary
    log_info("\n=== Phase 4: ZSWAP vs ZRAM Comparison ===")
    
    zram_ratio = results['zram_baseline'].get('compression_ratio', 0)
    zswap_ratio = results['zswap'].get('compression_ratio', 0)
    
    if zram_ratio > 0 and zswap_ratio > 0:
        log_info(f"  ZRAM compression: {zram_ratio:.2f}x")
        log_info(f"  ZSWAP compression: {zswap_ratio:.2f}x")
        
        # Store comparison
        results['comparison']['zram_compression_ratio'] = zram_ratio
        results['comparison']['zswap_compression_ratio'] = zswap_ratio
        results['comparison']['compression_ratio_diff'] = round(abs(zram_ratio - zswap_ratio), 2)
    
    # Compare hot latency
    # ZRAM: ~5µs (from previous tests)
    # ZSWAP hot: ~5-10µs
    results['comparison']['zram_hot_latency_us'] = 5  # Typical from benchmark_compression
    results['comparison']['zswap_hot_latency_us'] = estimated_hot_latency_us
    results['comparison']['hot_latency_overhead_us'] = estimated_hot_latency_us - 5

    if 'avg_disk_read_latency_us' in results['zswap']:
        zswap_cold_latency_us = results['zswap']['avg_disk_read_latency_us']
        results['comparison']['zswap_cold_latency_us'] = zswap_cold_latency_us
        results['comparison']['cold_latency_overhead_us'] = round(zswap_cold_latency_us - estimated_hot_latency_us, 0)
    
    log_info(f"  ZRAM hot access: ~5µs (memory-only)")
    log_info(f"  ZSWAP hot access: ~{estimated_hot_latency_us}µs (similar, but LRU managed)")
    
    # Key difference: ZSWAP has disk overflow, ZRAM doesn't
    if total_mb_written > 0:
        log_info(f"  ZSWAP disk overflow: {total_mb_written:.2f}MB written to disk")
        log_info(f"  ZRAM disk overflow: N/A (would cause OOM)")
        results['comparison']['has_disk_overflow'] = True
        results['comparison']['disk_overflow_mb'] = total_mb_written
    else:
        log_info(f"  ZSWAP: All data stayed in RAM (pool not full)")
        results['comparison']['has_disk_overflow'] = False
    
    log_success("ZSWAP latency testing complete")
    
    return results

def benchmark_write_latency(compressor, allocator, test_size_mb=100, pattern=0, test_num=None, total_tests=None):
    """
    Measure page write (swap-out) latency.
    
    Process:
    1. Setup ZRAM/ZSWAP with specified compressor/allocator
    2. Run mem_write_bench with specified data pattern
    3. Collect latency statistics
    
    Args:
        compressor: Compression algorithm (lz4, zstd, lzo-rle)
        allocator: Memory allocator (zsmalloc, z3fold, zbud)
        test_size_mb: Size of memory to test in MB
        pattern: Data pattern (0=mixed, 1=random, 2=zeros, 3=sequential)
        test_num: Current test number (for progress tracking)
        total_tests: Total number of tests (for progress tracking)
    
    Returns:
        Dictionary with latency statistics
    """
    start_time = time.time()
    
    progress_str = f"[{test_num}/{total_tests}] " if test_num and total_tests else ""
    log_step_ts(f"{progress_str}Write latency test: {compressor} + {allocator} (pattern={pattern})")
    
    results = {
        'compressor': compressor,
        'allocator': allocator,
        'test_size_mb': test_size_mb,
        'pattern': pattern,
        'timestamp': datetime.now().isoformat()
    }
    
    try:
        # Ensure ZRAM is loaded and clean
        if not ensure_zram_loaded():
            results['error'] = "Failed to load/reset ZRAM device"
            return results
        
        # Set allocator
        if os.path.exists('/sys/block/zram0/mem_pool'):
            try:
                with open('/sys/block/zram0/mem_pool', 'w') as f:
                    f.write(f'{allocator}\n')
            except:
                log_warn(f"Could not set allocator to {allocator}")
        
        # Set compressor
        if os.path.exists('/sys/block/zram0/comp_algorithm'):
            try:
                with open('/sys/block/zram0/comp_algorithm', 'w') as f:
                    f.write(f'{compressor}\n')
            except:
                log_warn(f"Could not set compressor to {compressor}")
        
        # Set ZRAM size
        size_bytes = test_size_mb * 1024 * 1024
        try:
            with open('/sys/block/zram0/disksize', 'w') as f:
                f.write(str(size_bytes))
        except OSError as e:
            log_error(f"Failed to set ZRAM disk size: {e}")
            results['error'] = f"Failed to set disksize: {e}"
            return results
        
        # Enable swap
        run_command('mkswap /dev/zram0')
        run_command('swapon -p 100 /dev/zram0')
        
        # Run mem_write_bench
        script_dir = Path(__file__).parent
        mem_write_bench_path = script_dir / 'mem_write_bench'
        
        if not mem_write_bench_path.exists():
            results['error'] = "mem_write_bench executable not found. Run compilation first or check if the C program built successfully."
            return results
        
        log_info(f"Running mem_write_bench ({test_size_mb}MB, pattern={pattern})...")
        result = subprocess.run(
            [str(mem_write_bench_path), str(test_size_mb), str(pattern)],
            capture_output=True,
            text=True,
            timeout=300
        )
        
        if result.returncode == 0:
            # Parse JSON output
            try:
                bench_results = json.loads(result.stdout)
                results.update(bench_results)
                log_info(f"  Write latency: avg={bench_results.get('avg_write_us', 0):.2f}µs, "
                        f"p95={bench_results.get('p95_write_us', 0):.2f}µs, "
                        f"p99={bench_results.get('p99_write_us', 0):.2f}µs")
            except json.JSONDecodeError as e:
                log_error(f"Failed to parse mem_write_bench output: {e}")
                results['error'] = f"JSON parse error: {e}"
        else:
            log_error(f"mem_write_bench failed with code {result.returncode}")
            log_debug(f"Stderr: {result.stderr}")
            results['error'] = f"Exit code {result.returncode}"
    
    except subprocess.TimeoutExpired:
        log_error("Write latency test timed out")
        results['error'] = "Timeout"
    except Exception as e:
        log_error(f"Write latency test failed: {e}")
        results['error'] = str(e)
    finally:
        # Cleanup
        cleanup_zram_aggressive()
    
    elapsed = time.time() - start_time
    results['elapsed_sec'] = round(elapsed, 1)
    log_info(f"✓ Test completed in {elapsed:.1f}s")
    
    return results

def benchmark_read_latency(compressor, allocator, test_size_mb=100, access_pattern=0, test_num=None, total_tests=None):
    """
    Measure page read (page fault + decompress) latency.
    
    Process:
    1. Setup ZRAM/ZSWAP
    2. Run mem_read_bench with specified access pattern
    3. Measure page fault latency
    
    Args:
        compressor: Compression algorithm
        allocator: Memory allocator
        test_size_mb: Size of memory to test in MB
        access_pattern: Access pattern (0=sequential, 1=random, 2=stride)
        test_num: Current test number (for progress tracking)
        total_tests: Total number of tests (for progress tracking)
    
    Returns:
        Dictionary with latency statistics
    """
    start_time = time.time()
    
    pattern_names = ["sequential", "random", "stride"]
    pattern_name = pattern_names[access_pattern] if 0 <= access_pattern <= 2 else "unknown"
    
    progress_str = f"[{test_num}/{total_tests}] " if test_num and total_tests else ""
    log_step_ts(f"{progress_str}Read latency test: {compressor} + {allocator} ({pattern_name})")
    
    results = {
        'compressor': compressor,
        'allocator': allocator,
        'test_size_mb': test_size_mb,
        'access_pattern': pattern_name,
        'timestamp': datetime.now().isoformat()
    }
    
    try:
        # Ensure ZRAM is loaded and clean
        if not ensure_zram_loaded():
            results['error'] = "Failed to load/reset ZRAM device"
            return results
        
        # Set allocator
        if os.path.exists('/sys/block/zram0/mem_pool'):
            try:
                with open('/sys/block/zram0/mem_pool', 'w') as f:
                    f.write(f'{allocator}\n')
            except:
                log_warn(f"Could not set allocator to {allocator}")
        
        # Set compressor
        if os.path.exists('/sys/block/zram0/comp_algorithm'):
            try:
                with open('/sys/block/zram0/comp_algorithm', 'w') as f:
                    f.write(f'{compressor}\n')
            except:
                log_warn(f"Could not set compressor to {compressor}")
        
        # Set ZRAM size
        size_bytes = test_size_mb * 1024 * 1024
        try:
            with open('/sys/block/zram0/disksize', 'w') as f:
                f.write(str(size_bytes))
        except OSError as e:
            log_error(f"Failed to set ZRAM disk size: {e}")
            results['error'] = f"Failed to set disksize: {e}"
            return results
        
        # Enable swap
        run_command('mkswap /dev/zram0')
        run_command('swapon -p 100 /dev/zram0')
        
        # Run mem_read_bench
        script_dir = Path(__file__).parent
        mem_read_bench_path = script_dir / 'mem_read_bench'
        
        if not mem_read_bench_path.exists():
            results['error'] = "mem_read_bench executable not found. Run compilation first or check if the C program built successfully."
            return results
        
        log_info(f"Running mem_read_bench ({test_size_mb}MB, {pattern_name})...")
        result = subprocess.run(
            [str(mem_read_bench_path), str(test_size_mb), str(access_pattern)],
            capture_output=True,
            text=True,
            timeout=300
        )
        
        if result.returncode == 0:
            # Parse JSON output
            try:
                bench_results = json.loads(result.stdout)
                results.update(bench_results)
                log_info(f"  Read latency: avg={bench_results.get('avg_read_us', 0):.2f}µs, "
                        f"p95={bench_results.get('p95_read_us', 0):.2f}µs, "
                        f"p99={bench_results.get('p99_read_us', 0):.2f}µs")
            except json.JSONDecodeError as e:
                log_error(f"Failed to parse mem_read_bench output: {e}")
                results['error'] = f"JSON parse error: {e}"
        else:
            log_error(f"mem_read_bench failed with code {result.returncode}")
            log_debug(f"Stderr: {result.stderr}")
            results['error'] = f"Exit code {result.returncode}"
    
    except subprocess.TimeoutExpired:
        log_error("Read latency test timed out")
        results['error'] = "Timeout"
    except Exception as e:
        log_error(f"Read latency test failed: {e}")
        results['error'] = str(e)
    finally:
        # Cleanup
        cleanup_zram_aggressive()
    
    elapsed = time.time() - start_time
    results['elapsed_sec'] = round(elapsed, 1)
    log_info(f"✓ Test completed in {elapsed:.1f}s")
    
    return results

def benchmark_native_ram_baseline(test_size_mb=100):
    """
    Measure native RAM access latency (no swap).
    
    Provides baseline for comparison - this is the "ideal" performance target.
    Measures pure RAM read/write speed without any swap or compression overhead.
    
    Args:
        test_size_mb: Size of memory to test in MB
    
    Returns:
        Dictionary with baseline read/write latency in nanoseconds
    """
    log_step_ts("Native RAM baseline test (no swap)")
    
    results = {
        'type': 'native_ram',
        'test_size_mb': test_size_mb,
        'timestamp': datetime.now().isoformat()
    }
    
    try:
        # Run a simple memory access benchmark without any swap
        # We'll use a minimal C-like approach via Python for simplicity
        import array
        import time
        
        size_bytes = test_size_mb * 1024 * 1024
        
        log_info(f"Testing native RAM access ({test_size_mb}MB)...")
        
        # Write test
        log_info("Measuring write speed...")
        data = bytearray(size_bytes)
        
        write_start = time.time()
        for i in range(0, size_bytes, 4096):
            data[i] = (i % 256)
        write_end = time.time()
        
        write_ns_per_page = ((write_end - write_start) * 1e9) / (size_bytes / 4096)
        
        # Read test
        log_info("Measuring read speed...")
        dummy = 0
        read_start = time.time()
        for i in range(0, size_bytes, 4096):
            dummy += data[i]
        read_end = time.time()
        
        read_ns_per_page = ((read_end - read_start) * 1e9) / (size_bytes / 4096)
        
        # Calculate bandwidth
        write_time = write_end - write_start
        read_time = read_end - read_start
        write_gb_per_sec = (size_bytes / (1024**3)) / write_time if write_time > 0 else 0
        read_gb_per_sec = (size_bytes / (1024**3)) / read_time if read_time > 0 else 0
        
        results['read_ns'] = round(read_ns_per_page, 2)
        results['write_ns'] = round(write_ns_per_page, 2)
        results['read_gb_per_sec'] = round(read_gb_per_sec, 2)
        results['write_gb_per_sec'] = round(write_gb_per_sec, 2)
        
        log_info(f"  Native RAM read: {read_ns_per_page:.0f} ns/page ({read_gb_per_sec:.2f} GB/s)")
        log_info(f"  Native RAM write: {write_ns_per_page:.0f} ns/page ({write_gb_per_sec:.2f} GB/s)")
        log_info(f"✓ Baseline established")
        
    except Exception as e:
        log_error(f"Baseline test failed: {e}")
        results['error'] = str(e)
    
    return results

def benchmark_latency_comparison(test_size_mb=100):
    """
    Comprehensive comparison of latency across all configurations.
    
    Tests matrix:
    - Baseline: Native RAM (no swap)
    - ZRAM: lz4/zstd × zsmalloc/zbud (4 combinations for efficiency)
    - Access patterns: sequential/random for reads
    - Operations: read/write
    
    Args:
        test_size_mb: Size of memory to test in MB
    
    Returns:
        Dictionary with comprehensive latency comparison
    """
    log_step_ts("Comprehensive latency comparison")
    
    results = {
        'timestamp': datetime.now().isoformat(),
        'test_size_mb': test_size_mb,
        'baseline': {},
        'write_latency': [],
        'read_latency': []
    }
    
    # 1. Baseline: Native RAM
    log_info("\n=== Phase 1: Native RAM Baseline ===")
    results['baseline'] = benchmark_native_ram_baseline(test_size_mb)
    
    # 2. Write latency tests - reduced to top performers only
    # Test best speed (lz4) and best compression (zstd) with most common allocators
    log_info("\n=== Phase 2: Write Latency Tests (Top Performers) ===")
    write_configs = [
        ('lz4', 'zsmalloc'),   # Fast compressor, best allocator
        ('zstd', 'zsmalloc'),  # Best compression, best allocator
        ('lz4', 'z3fold'),     # Fast compressor, alternative allocator
    ]
    
    for i, (comp, alloc) in enumerate(write_configs, 1):
        result = benchmark_write_latency(comp, alloc, test_size_mb, pattern=0,
                                        test_num=i, total_tests=len(write_configs))
        results['write_latency'].append(result)
    
    # 3. Read latency tests - reduced to top performers only
    log_info("\n=== Phase 3: Read Latency Tests (Top Performers) ===")
    read_configs = [
        ('lz4', 'zsmalloc', 0),   # Fast, sequential
        ('zstd', 'zsmalloc', 0),  # Best compression, sequential
        ('lz4', 'zsmalloc', 1),   # Fast, random
    ]
    
    for i, (comp, alloc, pattern) in enumerate(read_configs, 1):
        result = benchmark_read_latency(comp, alloc, test_size_mb, pattern,
                                       test_num=i, total_tests=len(read_configs))
        results['read_latency'].append(result)
    
    # 4. Generate comparison summary
    log_info("\n=== Latency Comparison Summary ===")
    
    if 'read_ns' in results['baseline']:
        baseline_read_ns = results['baseline']['read_ns']
        baseline_write_ns = results['baseline']['write_ns']
        
        log_info(f"Baseline (Native RAM):")
        log_info(f"  Read:  {baseline_read_ns:.0f} ns/page")
        log_info(f"  Write: {baseline_write_ns:.0f} ns/page")
        log_info("")
        
        # Compare write latencies
        for result in results['write_latency']:
            if 'avg_write_us' in result and 'error' not in result:
                avg_us = result['avg_write_us']
                avg_ns = avg_us * 1000
                slowdown = avg_ns / baseline_write_ns if baseline_write_ns > 0 else 0
                log_info(f"{result['compressor']:8s} + {result['allocator']:8s} write: "
                        f"{avg_us:7.2f}µs ({slowdown:.0f}x slower than RAM)")
        
        log_info("")
        
        # Compare read latencies
        for result in results['read_latency']:
            if 'avg_read_us' in result and 'error' not in result:
                avg_us = result['avg_read_us']
                avg_ns = avg_us * 1000
                slowdown = avg_ns / baseline_read_ns if baseline_read_ns > 0 else 0
                pattern = result.get('access_pattern', 'unknown')
                log_info(f"{result['compressor']:8s} + {result['allocator']:8s} read ({pattern:10s}): "
                        f"{avg_us:7.2f}µs ({slowdown:.0f}x slower than RAM)")
    
    log_info("\n✓ Latency comparison complete")
    
    return results

def export_shell_config(results, output_file):
    """Export optimal configuration as shell script"""
    log_step(f"Exporting configuration to {output_file}")
    
    with open(output_file, 'w') as f:
        f.write("# Swap Configuration from Benchmark\n")
        f.write(f"# Generated: {datetime.now().isoformat()}\n")
        f.write("# Based on comprehensive testing documented in chat-merged.md\n\n")
        
        # Find best block size from matrix test (preferred) or old block_sizes test (fallback)
        page_cluster_written = False
        
        if 'matrix' in results and isinstance(results['matrix'], dict):
            # Extract from matrix test results
            matrix_data = results['matrix'].get('matrix', [])
            if matrix_data:
                # Find test with best combined throughput
                best_matrix = max(matrix_data,
                                key=lambda x: x.get('write_mb_per_sec', 0) + x.get('read_mb_per_sec', 0))
                
                # Map block size to page-cluster
                block_to_cluster = {4: 0, 8: 1, 16: 2, 32: 3, 64: 4, 128: 5}
                cluster_disk_optimal = block_to_cluster.get(best_matrix.get('block_size_kb'), 0)
                
                # CRITICAL: Matrix test finds optimal DISK block size, NOT ZSWAP readahead
                # ZSWAP is a RAM cache - no seek cost, readahead wastes bandwidth
                # ALWAYS use page-cluster=0 for ZSWAP systems (see chat-merged.md section 2.2)
                cluster_zswap = 0

                f.write(
                    f"# Matrix test result: {best_matrix.get('block_size_kb')}KB "
                    f"× {best_matrix.get('concurrency')} jobs\n"
                )
                f.write(f"# Disk I/O throughput: "
                       f"{best_matrix.get('write_mb_per_sec', 0) + best_matrix.get('read_mb_per_sec', 0):.0f} MB/s\n")
                f.write(f"# Optimal DISK page-cluster: {cluster_disk_optimal} ({best_matrix.get('block_size_kb')}KB blocks)\n")
                f.write(f"#\n")
                f.write(f"# IMPORTANT: For ZSWAP (RAM cache), page-cluster MUST be 0\n")
                f.write(f"# - ZSWAP caches individual 4KB pages, no seek cost\n")
                f.write(f"# - Readahead wastes memory bandwidth and cache space\n")
                f.write(f"# - See chat-merged.md lines 168-179 for rationale\n")
                f.write(f"#\n")
                f.write(f"SWAP_PAGE_CLUSTER={cluster_zswap}  # Always 0 for ZSWAP\n")
                f.write(f"SWAP_PAGE_CLUSTER_DISK={cluster_disk_optimal}  # Use this if disk-only swap (no ZSWAP)\n\n")
                page_cluster_written = True
        
        # Fallback to deprecated block_sizes test
        if not page_cluster_written and 'block_sizes' in results and results['block_sizes']:
            best_block = max(results['block_sizes'], 
                           key=lambda x: x.get('read_mb_per_sec', 0) + x.get('write_mb_per_sec', 0))
            # Map block size to page-cluster
            block_to_cluster = {4: 0, 8: 1, 16: 2, 32: 3, 64: 4, 128: 5}
            cluster = block_to_cluster.get(best_block['block_size_kb'], 3)
            f.write(f"# Best block size: {best_block['block_size_kb']}KB\n")
            f.write(f"# (Read: {best_block.get('read_mb_per_sec', 0)} MB/s, ")
            f.write(f"Write: {best_block.get('write_mb_per_sec', 0)} MB/s)\n")
            f.write(f"# Optimal page-cluster value: vm.page-cluster={cluster}\n")
            f.write(f"SWAP_PAGE_CLUSTER={cluster}\n\n")
        
        # Find best compressor (ignore failed/unreliable runs)
        if 'compressors' in results and results['compressors']:
            candidates = [
                r for r in results['compressors']
                if isinstance(r, dict)
                and 'error' not in r
                and r.get('reliable', True)
                and isinstance(r.get('compression_ratio'), (int, float))
            ]
            best_comp = max(candidates, key=lambda x: x.get('compression_ratio', 0), default=None)
            if best_comp:
                f.write(f"# Best compressor: {best_comp['compressor']}\n")
                f.write(f"# (Compression ratio: {best_comp.get('compression_ratio', 0)}x)\n")
                f.write(f"ZSWAP_COMPRESSOR={best_comp['compressor']}\n")
                f.write(f"ZRAM_COMPRESSOR={best_comp['compressor']}\n\n")
            else:
                f.write("# Compressor benchmarks were unreliable; using safe default\n")
                f.write("ZSWAP_COMPRESSOR=lz4\n")
                f.write("ZRAM_COMPRESSOR=lz4\n\n")
        
        # Best allocator (ignore failed/unreliable runs; prefer mixed/default pattern when available)
        if 'allocators' in results and results['allocators']:
            candidates = [
                r for r in results['allocators']
                if isinstance(r, dict)
                and 'error' not in r
                and r.get('reliable', True)
                and isinstance(r.get('efficiency_pct'), (int, float))
            ]
            mixed = [
                r for r in candidates
                if (r.get('pattern_id') == 0) or (str(r.get('data_pattern', '')).lower() == 'mixed')
            ]
            best_alloc = max((mixed or candidates), key=lambda x: x.get('efficiency_pct', 0), default=None)
            if best_alloc:
                f.write(f"# Best allocator: {best_alloc['allocator']}\n")
                f.write(f"# (Efficiency: {best_alloc.get('efficiency_pct', 0)}%)\n")
                f.write(f"# NOTE: zbud often works better with ZSWAP (z3fold can fail to load)\n")
                f.write(f"ZRAM_ALLOCATOR={best_alloc['allocator']}\n\n")
            else:
                f.write("# Allocator benchmarks were unreliable; using safe default\n")
                f.write("ZRAM_ALLOCATOR=zsmalloc\n\n")
        
        # Optimal stripe width
        # IMPORTANT: Do not derive SWAP_STRIPE_WIDTH from the single-target matrix fio numjobs.
        stripe_width = None
        try:
            sm = results.get('swap_partitions_matrix')
            if isinstance(sm, dict):
                opt = sm.get('optimal') or {}
                v = opt.get('recommended_swap_stripe_width')
                if isinstance(v, int) and v > 0:
                    stripe_width = v
        except Exception:
            stripe_width = None

        if stripe_width is None and 'concurrency' in results and results['concurrency']:
            best_concur = max(
                results['concurrency'],
                key=lambda x: x.get('write_mb_per_sec', 0) + x.get('read_mb_per_sec', 0),
            )
            stripe_width = best_concur.get('num_files')
            if stripe_width:
                f.write(f"# Optimal swap stripe width (deprecated concurrency test): {stripe_width}\n")
                f.write(f"# (Write: {best_concur.get('write_mb_per_sec', 0)} MB/s, ")
                f.write(f"Read: {best_concur.get('read_mb_per_sec', 0)} MB/s)\n")

        if isinstance(stripe_width, int) and stripe_width > 0:
            f.write(f"# Optimal swap stripe width (devices): {stripe_width}\n")
            f.write(f"SWAP_STRIPE_WIDTH={stripe_width}\n")
        else:
            # Let setup-swap.sh choose/auto-detect if we didn't measure it.
            f.write("# SWAP_STRIPE_WIDTH not measured; let setup-swap.sh auto-detect\n")
            f.write("SWAP_STRIPE_WIDTH=auto\n")
    
    log_info(f"Configuration saved to {output_file}")

def generate_benchmark_summary_report(results, output_file):
    """Generate human-readable benchmark summary report"""
    log_step(f"Generating benchmark summary report: {output_file}")
    
    system_info = results.get('system_info', {})
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    
    with open(output_file, 'w') as f:
        f.write("=" * 68 + "\n")
        f.write("SWAP PERFORMANCE BENCHMARK REPORT\n")
        f.write(f"System: {system_info.get('hostname', 'unknown')} - ")
        f.write(f"{system_info.get('ram_gb', '?')}GB RAM, ")
        f.write(f"{system_info.get('cpu_cores', '?')} CPU cores")
        if 'cpu_model' in system_info:
            f.write(f", {system_info['cpu_model']}")
        f.write(f"\nDate: {timestamp}\n")
        f.write("=" * 68 + "\n\n")
        
        # Optimal configuration section
        f.write("OPTIMAL CONFIGURATION\n")
        f.write("-" * 68 + "\n")
        
        # Prefer matrix-derived block size (modern path)
        matrix_opt = None
        if isinstance(results.get('matrix'), dict):
            matrix_opt = (results['matrix'].get('optimal') or {})

        if isinstance(matrix_opt, dict) and isinstance((matrix_opt.get('best_combined') or {}).get('block_size_kb'), int):
            best = matrix_opt['best_combined']
            block_to_cluster = {4: 0, 8: 1, 16: 2, 32: 3, 64: 4, 128: 5}
            cluster = block_to_cluster.get(best['block_size_kb'], 3)
            f.write(f"✓ Page Cluster:        {cluster} ({best['block_size_kb']}KB blocks; matrix best)\n")
        elif 'block_sizes' in results and results['block_sizes']:
            best_block = max(
                results['block_sizes'],
                key=lambda x: x.get('read_mb_per_sec', 0) + x.get('write_mb_per_sec', 0),
            )
            block_to_cluster = {4: 0, 8: 1, 16: 2, 32: 3, 64: 4, 128: 5}
            cluster = block_to_cluster.get(best_block['block_size_kb'], 3)
            f.write(f"✓ Page Cluster:        {cluster} ({best_block['block_size_kb']}KB blocks)\n")
        
        if 'compressors' in results and results['compressors']:
            best_comp = max(results['compressors'], 
                          key=lambda x: x.get('compression_ratio', 0))
            f.write(f"✓ Compressor:          {best_comp['compressor']} ")
            f.write(f"({best_comp.get('compression_ratio', 0):.2f}x compression ratio)\n")
        
        if 'allocators' in results and results['allocators']:
            candidates = [
                r for r in results['allocators']
                if isinstance(r, dict)
                and 'error' not in r
                and r.get('reliable', True)
                and isinstance(r.get('efficiency_pct'), (int, float))
            ]
            mixed = [
                r for r in candidates
                if (r.get('pattern_id') == 0) or (str(r.get('data_pattern', '')).lower() == 'mixed')
            ]
            best_alloc = max((mixed or candidates), key=lambda x: x.get('efficiency_pct', 0), default=None)
            if best_alloc:
                f.write(f"✓ Allocator:           {best_alloc['allocator']} ")
                f.write(f"({best_alloc.get('efficiency_pct', 0):.1f}% efficiency)\n")
        
        # Matrix "concurrency" is fio numjobs (I/O concurrency), not swap-device count.
        if isinstance(matrix_opt, dict) and isinstance((matrix_opt.get('best_combined') or {}).get('concurrency'), int):
            numjobs = matrix_opt['best_combined']['concurrency']
            f.write(f"✓ Matrix Best:         {numjobs} fio jobs (numjobs; matrix best)\n")

        stripe = None
        spm = results.get('swap_partitions_matrix')
        if isinstance(spm, dict):
            opt = spm.get('optimal') or {}
            v = opt.get('recommended_swap_stripe_width')
            if isinstance(v, int) and v > 0:
                stripe = v
        if isinstance(stripe, int) and stripe > 0:
            f.write(f"✓ Stripe Width:        {stripe} swap devices (measured)\n")
        else:
            f.write("✓ Stripe Width:        auto (not measured)\n")
        
        f.write("\n")
        
        # Performance highlights
        f.write("PERFORMANCE HIGHLIGHTS\n")
        f.write("-" * 68 + "\n")
        
        if 'block_sizes' in results and results['block_sizes']:
            best_block = max(results['block_sizes'], 
                           key=lambda x: x.get('read_mb_per_sec', 0))
            f.write(f"• Best Block Size:     {best_block['block_size_kb']}KB ")
            f.write(f"({best_block.get('read_mb_per_sec', 0):.0f} MB/s read)\n")
        
        if 'compressors' in results and results['compressors']:
            best_comp = max(results['compressors'], 
                          key=lambda x: x.get('compression_ratio', 0))
            space_eff = (1 - 1/best_comp.get('compression_ratio', 1)) * 100
            f.write(f"• Best Compressor:     {best_comp['compressor']} ")
            f.write(f"({space_eff:.1f}% space efficiency)\n")
        
        if 'concurrency' in results and results['concurrency']:
            best_concur = max(results['concurrency'], 
                            key=lambda x: x.get('write_mb_per_sec', 0))
            f.write(f"• Best Concurrency:    {best_concur['num_files']} files ")
            f.write(f"({best_concur.get('write_mb_per_sec', 0):.0f} MB/s write)\n")
        
        # Latency comparison
        if 'latency' in results:
            latency = results['latency']
            if 'ram_baseline' in latency and 'zram_lz4' in latency:
                ram_lat = latency['ram_baseline'].get('avg_latency_ns', 0)
                zram_lat = latency['zram_lz4'].get('write_latency_us', 0) * 1000
                if ram_lat > 0 and zram_lat > 0:
                    slowdown = zram_lat / ram_lat
                    f.write(f"• RAM Access:          {ram_lat:.0f} ns/page (baseline)\n")
                    f.write(f"• ZRAM Latency:        {zram_lat/1000:.2f} µs ({slowdown:.0f}x slower than RAM)\n")
        
        f.write("\n")
        
        # Detailed results summary
        f.write("DETAILED RESULTS\n")
        f.write("-" * 68 + "\n")
        
        if 'block_sizes' in results and results['block_sizes']:
            f.write("Block Size Performance:\n")
            for bs in results['block_sizes'][:5]:  # Top 5
                f.write(f"  {bs['block_size_kb']:3d}KB: ")
                f.write(f"R={bs.get('read_mb_per_sec', 0):6.0f} MB/s, ")
                f.write(f"W={bs.get('write_mb_per_sec', 0):6.0f} MB/s\n")
            f.write("\n")
        
        if 'compressors' in results and results['compressors']:
            f.write("Compression Performance:\n")
            for comp in results['compressors']:
                f.write(f"  {comp['compressor']:8s}: ")
                f.write(f"{comp.get('compression_ratio', 0):.2f}x compression\n")
            f.write("\n")
        
        # Recommendations
        f.write("RECOMMENDATIONS\n")
        f.write("-" * 68 + "\n")
        
        if 'block_sizes' in results and results['block_sizes']:
            best_block = max(results['block_sizes'], 
                           key=lambda x: x.get('read_mb_per_sec', 0) + x.get('write_mb_per_sec', 0))
            block_to_cluster = {4: 0, 8: 1, 16: 2, 32: 3, 64: 4, 128: 5}
            cluster = block_to_cluster.get(best_block['block_size_kb'], 3)
            f.write(f"1. Use vm.page-cluster={cluster} for optimal I/O performance\n")
        
        if 'compressors' in results and results['compressors']:
            best_comp = max(results['compressors'], 
                          key=lambda x: x.get('compression_ratio', 0))
            f.write(f"2. Enable ZSWAP with {best_comp['compressor']} compressor for best compression\n")
        
        if 'concurrency' in results and results['concurrency']:
            best_concur = max(results['concurrency'], 
                            key=lambda x: x.get('write_mb_per_sec', 0) + x.get('read_mb_per_sec', 0))
            f.write(f"3. Configure {best_concur['num_files']} parallel swap devices for maximum throughput\n")
        
        if 'compressors' in results and results['compressors']:
            best_comp = max(results['compressors'], 
                          key=lambda x: x.get('compression_ratio', 0))
            f.write(f"4. Expected memory extension: {best_comp.get('compression_ratio', 0):.1f}x with {best_comp['compressor']} compression\n")
        
        f.write("\n")
        f.write("=" * 68 + "\n")
    
    log_info(f"✓ Benchmark summary report saved to {output_file}")

def generate_swap_config_report(results, output_file):
    """Generate human-readable swap configuration decisions report"""
    log_step(f"Generating swap configuration report: {output_file}")
    
    system_info = results.get('system_info', {})
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    ram_gb = system_info.get('ram_gb', 0)
    
    with open(output_file, 'w') as f:
        f.write("=" * 68 + "\n")
        f.write("SWAP CONFIGURATION DECISIONS\n")
        f.write(f"System: {system_info.get('hostname', 'unknown')} - ")
        f.write(f"{ram_gb}GB RAM\n")
        f.write(f"Date: {timestamp}\n")
        f.write("=" * 68 + "\n\n")
        
        # Auto-detection results
        f.write("AUTO-DETECTION RESULTS\n")
        f.write("-" * 68 + "\n")
        
        # Determine RAM solution based on system RAM
        if ram_gb < RAM_TIER_LOW_GB:
            ram_solution = "zram"
            reason = f"low RAM system (<{RAM_TIER_LOW_GB}GB)"
        elif ram_gb < RAM_TIER_HIGH_GB:
            ram_solution = "zswap"
            reason = f"medium RAM system ({RAM_TIER_LOW_GB}-{RAM_TIER_HIGH_GB}GB)"
        else:
            ram_solution = "zswap"
            reason = f"high RAM system (>{RAM_TIER_HIGH_GB}GB)"
        
        f.write(f"RAM Solution:    {ram_solution} ({reason})\n")
        f.write(f"Backing Type:    files_in_root (SSD with adequate space)\n")
        f.write(f"RAM Swap:        {int(ram_gb * 0.5)}GB (50% of RAM)\n")
        f.write(f"Disk Swap:       {int(ram_gb * 1.0)}GB (overflow protection)\n")
        
        stripe_width = None
        spm = results.get('swap_partitions_matrix')
        if isinstance(spm, dict):
            opt = spm.get('optimal') or {}
            v = opt.get('recommended_swap_stripe_width')
            if isinstance(v, int) and v > 0:
                stripe_width = v
        if stripe_width:
            f.write(f"Stripe Width:    {stripe_width} devices (measured)\n")
        else:
            f.write("Stripe Width:    auto (not measured)\n")
        
        f.write("\n")
        
        # Rationale
        f.write("RATIONALE\n")
        f.write("-" * 68 + "\n")
        f.write(f"• Selected {ram_solution.upper()}: System has {ram_gb}GB RAM ({reason})\n")
        
        if ram_solution == "zswap":
            f.write("• ZSWAP advantages: Lower overhead, good compression\n")
        else:
            f.write("• ZRAM advantages: Better compression, simpler setup\n")
        
        f.write("• Disk backing required: Prevents OOM situations\n")
        
        if stripe_width:
            f.write(f"• {stripe_width} swap devices: Derived from swap-partition stripe benchmark\n")
        
        f.write("\n")
        
        # Applied configuration
        f.write("APPLIED CONFIGURATION\n")
        f.write("-" * 68 + "\n")
        
        if 'compressors' in results and results['compressors']:
            best_comp = max(results['compressors'], 
                          key=lambda x: x.get('compression_ratio', 0))
            f.write(f"Compressor:      {best_comp['compressor']}\n")
            f.write(f"Compression:     {best_comp.get('compression_ratio', 0):.2f}x ratio\n")
        
        if 'allocators' in results and results['allocators']:
            best_alloc = max(results['allocators'], 
                           key=lambda x: x.get('efficiency_pct', 0))
            f.write(f"Allocator:       {best_alloc['allocator']}\n")
        
        if 'block_sizes' in results and results['block_sizes']:
            best_block = max(results['block_sizes'], 
                           key=lambda x: x.get('read_mb_per_sec', 0) + x.get('write_mb_per_sec', 0))
            block_to_cluster = {4: 0, 8: 1, 16: 2, 32: 3, 64: 4, 128: 5}
            cluster = block_to_cluster.get(best_block['block_size_kb'], 3)
            f.write(f"Page Cluster:    vm.page-cluster={cluster}\n")
        
        f.write("\n")
        
        # Warnings
        f.write("WARNINGS\n")
        f.write("-" * 68 + "\n")
        
        warnings_found = False
        if 'compressors' in results:
            for comp in results['compressors']:
                if isinstance(comp.get('warnings'), list) and comp['warnings']:
                    for w in comp['warnings']:
                        f.write(f"⚠ {w}\n")
                        warnings_found = True
                elif 'warning' in comp and comp.get('warning'):
                    f.write(f"⚠ {comp['warning']}\n")
                    warnings_found = True
        
        if not warnings_found:
            f.write("No warnings\n")
        
        f.write("\n")
        f.write("=" * 68 + "\n")
    
    log_info(f"✓ Swap configuration report saved to {output_file}")

def generate_matrix_heatmaps(matrix_results, output_dir='/var/log/debian-install'):
    """
    Generate visualizations for matrix test results.

    Note: Heatmaps were removed because they tend to be hard to read in Telegram
    and add little compared to line charts.

    Generates:
    1. Line charts: Throughput vs Block Size (for each concurrency)
    2. Line charts: Throughput vs Concurrency (for each block size)
    3. (If latency data available) Line charts: Latency vs Block Size
    4. (If latency data available) Line charts: Latency vs Concurrency
    """
    if not MATPLOTLIB_AVAILABLE:
        log_warn("matplotlib not available - skipping matrix chart generation")
        return None
    
    try:
        import numpy as np
    except ImportError:
        log_warn("numpy not available - skipping matrix chart generation")
        return None
    
    os.makedirs(output_dir, exist_ok=True)
    chart_files = []
    timestamp = datetime.now().strftime('%Y%m%d-%H%M%S')
    
    # Use globally imported plt (already checked via MATPLOTLIB_AVAILABLE)
    block_sizes = matrix_results['block_sizes']
    concurrency_levels = matrix_results['concurrency_levels'] 
    
    # Extract data into 2D arrays
    write_data = np.zeros((len(concurrency_levels), len(block_sizes)))
    read_data = np.zeros((len(concurrency_levels), len(block_sizes)))
    write_lat_data = np.zeros((len(concurrency_levels), len(block_sizes)))
    read_lat_data = np.zeros((len(concurrency_levels), len(block_sizes)))
    
    for result in matrix_results['matrix']:
        if 'error' in result:
            continue
        bi = block_sizes.index(result['block_size_kb'])
        ci = concurrency_levels.index(result['concurrency'])
        write_data[ci, bi] = result.get('write_mb_per_sec', 0)
        read_data[ci, bi] = result.get('read_mb_per_sec', 0)
        write_lat_data[ci, bi] = result.get('write_latency_us', 0)
        read_lat_data[ci, bi] = result.get('read_latency_us', 0)
    
    try:
        # Chart 1: Line chart - Throughput vs Block Size (for each concurrency level)
        fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(16, 6))
        
        for ci, concurrency in enumerate(concurrency_levels):
            write_vals = write_data[ci, :]
            read_vals = read_data[ci, :]
            ax1.plot(block_sizes, write_vals, 'o-', label=f'Concurrency {concurrency}', linewidth=2, markersize=6)
            ax2.plot(block_sizes, read_vals, 's-', label=f'Concurrency {concurrency}', linewidth=2, markersize=6)
        
        ax1.set_xlabel('Block Size (KB)', fontsize=12)
        ax1.set_ylabel('Write Throughput (MB/s)', fontsize=12)
        ax1.set_title('Write Throughput vs Block Size', fontsize=14, fontweight='bold')
        ax1.legend(fontsize=10)
        ax1.grid(True, alpha=0.3)
        ax1.set_xscale('log', base=2)
        
        ax2.set_xlabel('Block Size (KB)', fontsize=12)
        ax2.set_ylabel('Read Throughput (MB/s)', fontsize=12)
        ax2.set_title('Read Throughput vs Block Size', fontsize=14, fontweight='bold')
        ax2.legend(fontsize=10)
        ax2.grid(True, alpha=0.3)
        ax2.set_xscale('log', base=2)
        
        plt.tight_layout()
        output_file = os.path.join(output_dir, f'matrix-throughput-vs-blocksize-{timestamp}.png')
        plt.savefig(output_file, dpi=150, bbox_inches='tight')
        plt.close()
        chart_files.append(output_file)
        log_info(f"Generated throughput vs block size chart: {output_file}")
        
        # Chart 2: Line chart - Throughput vs Concurrency (for each block size)
        fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(16, 6))
        
        for bi, block_size in enumerate(block_sizes):
            write_vals = write_data[:, bi]
            read_vals = read_data[:, bi]
            ax1.plot(concurrency_levels, write_vals, 'o-', label=f'{block_size} KB', linewidth=2, markersize=6)
            ax2.plot(concurrency_levels, read_vals, 's-', label=f'{block_size} KB', linewidth=2, markersize=6)
        
        ax1.set_xlabel('Concurrency Level', fontsize=12)
        ax1.set_ylabel('Write Throughput (MB/s)', fontsize=12)
        ax1.set_title('Write Throughput vs Concurrency', fontsize=14, fontweight='bold')
        ax1.legend(fontsize=10)
        ax1.grid(True, alpha=0.3)
        
        ax2.set_xlabel('Concurrency Level', fontsize=12)
        ax2.set_ylabel('Read Throughput (MB/s)', fontsize=12)
        ax2.set_title('Read Throughput vs Concurrency', fontsize=14, fontweight='bold')
        ax2.legend(fontsize=10)
        ax2.grid(True, alpha=0.3)
        
        plt.tight_layout()
        output_file = os.path.join(output_dir, f'matrix-throughput-vs-concurrency-{timestamp}.png')
        plt.savefig(output_file, dpi=150, bbox_inches='tight')
        plt.close()
        chart_files.append(output_file)
        log_info(f"Generated throughput vs concurrency chart: {output_file}")
        
        # Optional latency line charts (if latency data available)
        has_latency = np.any(write_lat_data > 0) or np.any(read_lat_data > 0)
        if has_latency:
            # Chart 3: Line chart - Latency vs Block Size (for each concurrency level)
            fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(16, 6))
            
            for ci, concurrency in enumerate(concurrency_levels):
                write_lat_vals = write_lat_data[ci, :]
                read_lat_vals = read_lat_data[ci, :]
                ax1.plot(block_sizes, write_lat_vals, 'o-', label=f'Concurrency {concurrency}', linewidth=2, markersize=6)
                ax2.plot(block_sizes, read_lat_vals, 's-', label=f'Concurrency {concurrency}', linewidth=2, markersize=6)
            
            ax1.set_xlabel('Block Size (KB)', fontsize=12)
            ax1.set_ylabel('Write Latency (µs)', fontsize=12)
            ax1.set_title('Write Latency vs Block Size', fontsize=14, fontweight='bold')
            ax1.legend(fontsize=10)
            ax1.grid(True, alpha=0.3)
            ax1.set_xscale('log', base=2)
            
            ax2.set_xlabel('Block Size (KB)', fontsize=12)
            ax2.set_ylabel('Read Latency (µs)', fontsize=12)
            ax2.set_title('Read Latency vs Block Size', fontsize=14, fontweight='bold')
            ax2.legend(fontsize=10)
            ax2.grid(True, alpha=0.3)
            ax2.set_xscale('log', base=2)
            
            plt.tight_layout()
            output_file = os.path.join(output_dir, f'matrix-latency-vs-blocksize-{timestamp}.png')
            plt.savefig(output_file, dpi=150, bbox_inches='tight')
            plt.close()
            chart_files.append(output_file)
            log_info(f"Generated latency vs block size chart: {output_file}")
            
            # Chart 4: Line chart - Latency vs Concurrency (for each block size)
            fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(16, 6))
            
            for bi, block_size in enumerate(block_sizes):
                write_lat_vals = write_lat_data[:, bi]
                read_lat_vals = read_lat_data[:, bi]
                ax1.plot(concurrency_levels, write_lat_vals, 'o-', label=f'{block_size} KB', linewidth=2, markersize=6)
                ax2.plot(concurrency_levels, read_lat_vals, 's-', label=f'{block_size} KB', linewidth=2, markersize=6)
            
            ax1.set_xlabel('Concurrency Level', fontsize=12)
            ax1.set_ylabel('Write Latency (µs)', fontsize=12)
            ax1.set_title('Write Latency vs Concurrency', fontsize=14, fontweight='bold')
            ax1.legend(fontsize=10)
            ax1.grid(True, alpha=0.3)
            
            ax2.set_xlabel('Concurrency Level', fontsize=12)
            ax2.set_ylabel('Read Latency (µs)', fontsize=12)
            ax2.set_title('Read Latency vs Concurrency', fontsize=14, fontweight='bold')
            ax2.legend(fontsize=10)
            ax2.grid(True, alpha=0.3)
            
            plt.tight_layout()
            output_file = os.path.join(output_dir, f'matrix-latency-vs-concurrency-{timestamp}.png')
            plt.savefig(output_file, dpi=150, bbox_inches='tight')
            plt.close()
            chart_files.append(output_file)
            log_info(f"Generated latency vs concurrency chart: {output_file}")
        
        # Return list of all generated chart files
        return chart_files
        
    except Exception as e:
        log_error(f"Failed to generate matrix charts: {e}")
        import traceback
        log_debug(traceback.format_exc())
        return chart_files if chart_files else None

def generate_zswap_stats_chart(stats_timeseries, output_dir='/var/log/debian-install'):
    """
    Generate time-series chart for ZSWAP statistics
    
    Args:
        stats_timeseries: List of tuples [(timestamp, stats_dict), ...]
        output_dir: Directory to save PNG file
    
    Returns:
        Path to generated chart file or None if matplotlib not available
    """
    if not MATPLOTLIB_AVAILABLE:
        return None
        
    try:
        import matplotlib.pyplot as plt
        
        if not stats_timeseries or len(stats_timeseries) < 2:
            log_warning("Insufficient data points for ZSWAP stats chart")
            return None
        
        # Extract time and metrics
        timestamps = [t for t, _ in stats_timeseries]
        base_time = timestamps[0]
        time_seconds = [(t - base_time) for t in timestamps]
        
        pool_sizes = [s.get('pool_total_size', 0) / (1024**2) for _, s in stats_timeseries]  # MB
        stored_pages = [s.get('stored_pages', 0) for _, s in stats_timeseries]
        writebacks = [s.get('written_back_pages', 0) for _, s in stats_timeseries]
        limit_hits = [s.get('pool_limit_hit', 0) for _, s in stats_timeseries]
        reject_compress = [s.get('reject_compress_poor', 0) for _, s in stats_timeseries]
        reject_alloc = [s.get('reject_alloc_fail', 0) for _, s in stats_timeseries]
        
        # Create figure with subplots
        fig, axes = plt.subplots(3, 2, figsize=(16, 12))
        
        # Chart 1: Pool Size over time
        axes[0, 0].plot(time_seconds, pool_sizes, 'b-', linewidth=2)
        axes[0, 0].set_xlabel('Time (seconds)', fontsize=11)
        axes[0, 0].set_ylabel('Pool Size (MB)', fontsize=11)
        axes[0, 0].set_title('ZSWAP Pool Size Over Time', fontsize=12, fontweight='bold')
        axes[0, 0].grid(True, alpha=0.3)
        axes[0, 0].fill_between(time_seconds, pool_sizes, alpha=0.3)
        
        # Chart 2: Stored Pages over time
        axes[0, 1].plot(time_seconds, stored_pages, 'g-', linewidth=2)
        axes[0, 1].set_xlabel('Time (seconds)', fontsize=11)
        axes[0, 1].set_ylabel('Pages', fontsize=11)
        axes[0, 1].set_title('Stored Pages Over Time', fontsize=12, fontweight='bold')
        axes[0, 1].grid(True, alpha=0.3)
        axes[0, 1].fill_between(time_seconds, stored_pages, alpha=0.3, color='green')
        
        # Chart 3: Writebacks over time
        axes[1, 0].plot(time_seconds, writebacks, 'r-', linewidth=2)
        axes[1, 0].set_xlabel('Time (seconds)', fontsize=11)
        axes[1, 0].set_ylabel('Written Back Pages', fontsize=11)
        axes[1, 0].set_title('ZSWAP Writebacks Over Time', fontsize=12, fontweight='bold')
        axes[1, 0].grid(True, alpha=0.3)
        axes[1, 0].fill_between(time_seconds, writebacks, alpha=0.3, color='red')
        
        # Chart 4: Pool Limit Hits over time
        axes[1, 1].plot(time_seconds, limit_hits, 'orange', linewidth=2)
        axes[1, 1].set_xlabel('Time (seconds)', fontsize=11)
        axes[1, 1].set_ylabel('Pool Limit Hits', fontsize=11)
        axes[1, 1].set_title('Pool Limit Hits Over Time', fontsize=12, fontweight='bold')
        axes[1, 1].grid(True, alpha=0.3)
        axes[1, 1].fill_between(time_seconds, limit_hits, alpha=0.3, color='orange')
        
        # Chart 5: Compression Rejects over time
        axes[2, 0].plot(time_seconds, reject_compress, 'm-', linewidth=2, label='Poor Compression')
        axes[2, 0].set_xlabel('Time (seconds)', fontsize=11)
        axes[2, 0].set_ylabel('Rejected (Poor Compression)', fontsize=11)
        axes[2, 0].set_title('Compression Rejects Over Time', fontsize=12, fontweight='bold')
        axes[2, 0].grid(True, alpha=0.3)
        axes[2, 0].fill_between(time_seconds, reject_compress, alpha=0.3, color='magenta')
        
        # Chart 6: Allocation Failures over time
        axes[2, 1].plot(time_seconds, reject_alloc, 'darkred', linewidth=2)
        axes[2, 1].set_xlabel('Time (seconds)', fontsize=11)
        axes[2, 1].set_ylabel('Allocation Failures', fontsize=11)
        axes[2, 1].set_title('Allocation Failures Over Time', fontsize=12, fontweight='bold')
        axes[2, 1].grid(True, alpha=0.3)
        axes[2, 1].fill_between(time_seconds, reject_alloc, alpha=0.3, color='darkred')
        
        plt.tight_layout()
        
        timestamp = time.strftime("%Y%m%d-%H%M%S")
        output_file = f'{output_dir}/zswap-stats-timeseries-{timestamp}.png'
        plt.savefig(output_file, dpi=150, bbox_inches='tight')
        plt.close()
        
        log_info(f"Generated ZSWAP stats chart: {output_file}")
        return output_file
        
    except Exception as e:
        log_error(f"Failed to generate ZSWAP stats chart: {e}")
        import traceback
        log_debug(traceback.format_exc())
        return None

def generate_charts(results, output_dir='/var/log/debian-install', webp=True):
    """
    Generate matplotlib charts for benchmark results
    
    Creates PNG (or WebP) charts for:
    1. Block size vs Throughput (read/write, sequential/random)
    2. Block size vs Latency
    3. Concurrency vs Throughput scaling
    4. Compression ratio comparison
    
    Args:
        results: Benchmark results dictionary
        output_dir: Directory to save PNG files
        webp: If True, convert PNG to WebP format (smaller file size)
    
    Returns:
        List of generated chart file paths
    """
    if not MATPLOTLIB_AVAILABLE:
        log_warn("matplotlib not available - skipping chart generation")
        log_info("Install with: apt install python3-matplotlib")
        return []
    
    os.makedirs(output_dir, exist_ok=True)
    chart_files = []
    timestamp = datetime.now().strftime('%Y%m%d-%H%M%S')
    
    try:
        # Chart 1: Block Size vs Throughput
        if 'block_sizes' in results and results['block_sizes']:
            fig, ax = plt.subplots(figsize=(10, 6))
            
            block_sizes = [b['block_size_kb'] for b in results['block_sizes']]
            seq_write = [b.get('write_mb_per_sec', 0) for b in results['block_sizes']]
            seq_read = [b.get('read_mb_per_sec', 0) for b in results['block_sizes']]
            
            ax.plot(block_sizes, seq_write, 'o-', label='Sequential Write', linewidth=2, markersize=8)
            ax.plot(block_sizes, seq_read, 's-', label='Sequential Read', linewidth=2, markersize=8)
            
            # Add random I/O if available
            has_random = any(b.get('rand_write_mb_per_sec', 0) > 0 for b in results['block_sizes'])
            if has_random:
                rand_write = [b.get('rand_write_mb_per_sec', 0) for b in results['block_sizes']]
                rand_read = [b.get('rand_read_mb_per_sec', 0) for b in results['block_sizes']]
                ax.plot(block_sizes, rand_write, '^--', label='Random Write', linewidth=2, markersize=8, alpha=0.7)
                ax.plot(block_sizes, rand_read, 'v--', label='Random Read', linewidth=2, markersize=8, alpha=0.7)
            
            ax.set_xlabel('Block Size (KB)', fontsize=12)
            ax.set_ylabel('Throughput (MB/s)', fontsize=12)
            
            # Add subtitle with test parameters
            title = 'Block Size vs Throughput'
            if results['block_sizes']:
                first_test = results['block_sizes'][0]
                test_params = []
                if 'concurrency' in first_test:
                    test_params.append(f"Concurrency: {first_test['concurrency']}")
                if 'runtime_sec' in first_test:
                    test_params.append(f"Duration: {first_test['runtime_sec']}s")
                if 'io_pattern' in first_test:
                    test_params.append(f"Pattern: {first_test['io_pattern']}")
                if test_params:
                    subtitle = ' | '.join(test_params)
                    ax.set_title(f'{title}\n{subtitle}', fontsize=14, fontweight='bold')
                else:
                    ax.set_title(title, fontsize=14, fontweight='bold')
            else:
                ax.set_title(title, fontsize=14, fontweight='bold')
            
            ax.legend(fontsize=10)
            ax.grid(True, alpha=0.3)
            ax.set_xscale('log', base=2)
            
            chart_file = f"{output_dir}/benchmark-throughput-{timestamp}.png"
            plt.tight_layout()
            plt.savefig(chart_file, dpi=150)
            plt.close()
            chart_files.append(chart_file)
            log_info(f"Generated throughput chart: {chart_file}")
        
        # Chart 2: Block Size vs Latency
        if 'block_sizes' in results and results['block_sizes']:
            has_latency = any(b.get('write_latency_ms', 0) > 0 or b.get('read_latency_ms', 0) > 0 for b in results['block_sizes'])
            if has_latency:
                fig, ax = plt.subplots(figsize=(10, 6))
                
                block_sizes = [b['block_size_kb'] for b in results['block_sizes']]
                write_lat = [b.get('write_latency_ms', 0) for b in results['block_sizes']]
                read_lat = [b.get('read_latency_ms', 0) for b in results['block_sizes']]
                
                ax.plot(block_sizes, write_lat, 'o-', label='Write Latency', linewidth=2, markersize=8)
                ax.plot(block_sizes, read_lat, 's-', label='Read Latency', linewidth=2, markersize=8)
                
                ax.set_xlabel('Block Size (KB)', fontsize=12)
                ax.set_ylabel('Latency (ms)', fontsize=12)
                ax.set_title('Block Size vs Latency', fontsize=14, fontweight='bold')
                ax.legend(fontsize=10)
                ax.grid(True, alpha=0.3)
                ax.set_xscale('log', base=2)
                
                chart_file = f"{output_dir}/benchmark-latency-{timestamp}.png"
                plt.tight_layout()
                plt.savefig(chart_file, dpi=150)
                plt.close()
                chart_files.append(chart_file)
                log_info(f"Generated latency chart: {chart_file}")
        
        # Chart 3: Concurrency vs Throughput Scaling
        if 'concurrency' in results and results['concurrency']:
            fig, ax = plt.subplots(figsize=(10, 6))
            
            # Filter out error results
            valid_concur = [c for c in results['concurrency'] if 'error' not in c]
            if valid_concur:
                num_files = [c['num_files'] for c in valid_concur]
                write_throughput = [c.get('write_mb_per_sec', 0) for c in valid_concur]
                read_throughput = [c.get('read_mb_per_sec', 0) for c in valid_concur]
                total_throughput = [w + r for w, r in zip(write_throughput, read_throughput)]
                
                ax.plot(num_files, write_throughput, 'o-', label='Write', linewidth=2, markersize=8)
                ax.plot(num_files, read_throughput, 's-', label='Read', linewidth=2, markersize=8)
                ax.plot(num_files, total_throughput, '^-', label='Total', linewidth=2, markersize=8)
                
                # Add ideal linear scaling reference
                if num_files and total_throughput:
                    ideal_scaling = [total_throughput[0] * (n / num_files[0]) for n in num_files]
                    ax.plot(num_files, ideal_scaling, '--', color='gray', label='Ideal Linear', alpha=0.5)
                
                ax.set_xlabel('Number of Concurrent Files', fontsize=12)
                ax.set_ylabel('Throughput (MB/s)', fontsize=12)
                ax.set_title('Concurrency Scaling', fontsize=14, fontweight='bold')
                ax.legend(fontsize=10)
                ax.grid(True, alpha=0.3)
                
                chart_file = f"{output_dir}/benchmark-concurrency-{timestamp}.png"
                plt.tight_layout()
                plt.savefig(chart_file, dpi=150)
                plt.close()
                chart_files.append(chart_file)
                log_info(f"Generated concurrency chart: {chart_file}")
        
        # Chart 4: Compression Ratio Comparison
        if 'compressors' in results and results['compressors']:
            fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 6))
            
            # Filter out error results
            valid_comp = [c for c in results['compressors'] if 'error' not in c and c.get('compression_ratio', 0) > 0]
            if valid_comp:
                compressors = [c['compressor'] for c in valid_comp]
                ratios = [c.get('compression_ratio', 0) for c in valid_comp]
                efficiency = [c.get('efficiency_pct', 0) for c in valid_comp]
                
                # Bar chart for compression ratios
                bars = ax1.bar(compressors, ratios, color=['#3498db', '#e74c3c', '#2ecc71'][:len(compressors)])
                ax1.set_ylabel('Compression Ratio (x)', fontsize=12)
                ax1.set_title('Compression Ratio Comparison', fontsize=12, fontweight='bold')
                ax1.grid(True, alpha=0.3, axis='y')
                
                # Add value labels on bars
                for bar in bars:
                    height = bar.get_height()
                    ax1.text(bar.get_x() + bar.get_width()/2., height,
                            f'{height:.1f}x', ha='center', va='bottom', fontsize=10)
                
                # Bar chart for efficiency
                bars2 = ax2.bar(compressors, efficiency, color=['#3498db', '#e74c3c', '#2ecc71'][:len(compressors)])
                ax2.set_ylabel('Space Efficiency (%)', fontsize=12)
                ax2.set_title('Space Efficiency Comparison', fontsize=12, fontweight='bold')
                ax2.grid(True, alpha=0.3, axis='y')
                ax2.axhline(y=0, color='black', linestyle='-', linewidth=0.5)
                
                # Add value labels on bars
                for bar in bars2:
                    height = bar.get_height()
                    ax2.text(bar.get_x() + bar.get_width()/2., height,
                            f'{height:.0f}%', ha='center', va='bottom' if height > 0 else 'top', fontsize=10)
                
                chart_file = f"{output_dir}/benchmark-compression-{timestamp}.png"
                plt.tight_layout()
                plt.savefig(chart_file, dpi=150)
                plt.close()
                chart_files.append(chart_file)
                log_info(f"Generated compression chart: {chart_file}")

        # Swap partition striping matrix charts (real partitions)
        try:
            spm = results.get('swap_partitions_matrix')
            if isinstance(spm, dict) and 'error' not in spm and isinstance(spm.get('matrix'), list) and spm.get('matrix'):
                # Helper functions local to chart generator.
                def _lat_w_us(row):
                    metric = (spm.get('optimal') or {}).get('latency_metric') or (spm.get('fio_workload') or {}).get('latency_metric') or 'p99'
                    metric = str(metric).lower()
                    if metric == 'mean':
                        rlat = float(row.get('read_latency_us', 0) or 0)
                        wlat = float(row.get('write_latency_us', 0) or 0)
                    else:
                        rlat = float(row.get('read_latency_p99_us', 0) or 0)
                        wlat = float(row.get('write_latency_p99_us', 0) or 0)
                    lat = (0.7 * rlat) + (0.3 * wlat)
                    return lat if lat > 0 else 1.0

                def _score(row):
                    bw = float(row.get('combined_mb_per_sec', 0) or 0)
                    return bw / _lat_w_us(row)

                valid_rows = [r for r in spm['matrix'] if isinstance(r, dict) and 'error' not in r]
                if valid_rows:
                    metric = (spm.get('optimal') or {}).get('latency_metric') or (spm.get('fio_workload') or {}).get('latency_metric') or 'p99'
                    mix = (spm.get('fio_workload') or {}).get('rwmixread', '?')

                    # Group rows by (device_count, numjobs_per_device, iodepth).
                    grouped = {}
                    for r in valid_rows:
                        bs = r.get('block_size_kb')
                        dc = r.get('device_count')
                        nj = r.get('numjobs_per_device')
                        depth = r.get('iodepth')
                        if not all(isinstance(x, int) for x in (bs, dc, nj, depth)):
                            continue
                        grouped.setdefault((dc, nj, depth), []).append(r)

                    device_counts = sorted({k[0] for k in grouped.keys()})

                    chosen = (spm.get('optimal') or {}).get('best_combined') or {}
                    try:
                        chosen_dc = int(chosen.get('device_count'))
                        chosen_bs = int(chosen.get('block_size_kb'))
                        chosen_nj = int(chosen.get('numjobs_per_device', 1) or 1)
                        chosen_depth = int(chosen.get('iodepth', 1) or 1)
                    except Exception:
                        chosen_dc = chosen_bs = chosen_nj = chosen_depth = None

                    # Raw-data charts: x=blocksize, left y=throughput, right y=latency.
                    # One chart per device_count to keep it readable.
                    for dc in device_counts:
                        series_keys = sorted({(nj, depth) for (kdc, nj, depth) in grouped.keys() if kdc == dc})
                        if not series_keys:
                            continue

                        fig, ax1 = plt.subplots(figsize=(12, 6))
                        ax2 = ax1.twinx()

                        cmap = plt.get_cmap('tab10')
                        for i, (nj, depth) in enumerate(series_keys):
                            rows = grouped.get((dc, nj, depth), [])
                            # Build a map so we only keep one point per block size.
                            by_bs = {}
                            for r in rows:
                                bs = r.get('block_size_kb')
                                if not isinstance(bs, int):
                                    continue
                                # Keep the higher-throughput run if duplicates exist.
                                cur = by_bs.get(bs)
                                if cur is None or float(r.get('combined_mb_per_sec', 0) or 0) > float(cur.get('combined_mb_per_sec', 0) or 0):
                                    by_bs[bs] = r

                            xs = sorted(by_bs.keys())
                            if not xs:
                                continue
                            ys_bw = [float(by_bs[bs].get('combined_mb_per_sec', 0) or 0) for bs in xs]
                            ys_lat = [_lat_w_us(by_bs[bs]) for bs in xs]

                            color = cmap(i % 10)
                            label = f"jobs/dev={nj}, iodepth={depth}"
                            ax1.plot(xs, ys_bw, marker='o', linewidth=2, color=color, label=label)
                            ax2.plot(xs, ys_lat, marker='x', linestyle='--', linewidth=1.5, color=color, label='_nolegend_')

                            # Highlight the chosen point if it belongs to this series.
                            if chosen_dc == dc and chosen_bs in by_bs and chosen_nj == nj and chosen_depth == depth:
                                try:
                                    y_star = float(by_bs[chosen_bs].get('combined_mb_per_sec', 0) or 0)
                                    ax1.plot([chosen_bs], [y_star], marker='*', markersize=18,
                                             color='gold', markeredgecolor='black', zorder=10)
                                    ax1.annotate(
                                        "chosen",
                                        xy=(chosen_bs, y_star),
                                        xytext=(8, 10),
                                        textcoords='offset points',
                                        fontsize=9,
                                        bbox=dict(boxstyle='round,pad=0.25', fc='white', alpha=0.7),
                                    )
                                except Exception:
                                    pass

                        ax1.set_xlabel('Block size (KB)', fontsize=12)
                        ax1.set_ylabel('Throughput (MB/s; higher is better)', fontsize=12)
                        ax2.set_ylabel(f"Weighted latency (µs; {metric}; lower is better)", fontsize=12)

                        # Exponential/log2 x-axis to make powers-of-two block sizes readable.
                        try:
                            ax1.set_xscale('log', base=2)
                            ax1.set_xticks(xs)
                            ax1.set_xticklabels([f"2^{int(round(math.log2(x)))}" for x in xs])
                        except Exception:
                            pass
                        ax1.set_title(
                            f"Swap Partition Stripe Matrix (devices={dc}, rwmixread={mix}%)\n"
                            + "Solid=throughput (left axis), dashed=latency (right axis)",
                            fontsize=14,
                            fontweight='bold',
                        )
                        ax1.grid(True, alpha=0.3)
                        ax1.legend(fontsize=9, loc='upper left')

                        chart_file = f"{output_dir}/swap-partitions-matrix-raw-dc{dc}-{timestamp}.png"
                        plt.tight_layout()
                        plt.savefig(chart_file, dpi=150, bbox_inches='tight')
                        plt.close()
                        chart_files.append(chart_file)
                        log_info(f"Generated swap matrix raw chart: {chart_file}")

                    # Focus chart: only 4KB blocksize; show throughput+latency for combinations
                    # of (devices, jobs/dev). Render one chart per iodepth to keep it readable.
                    bs_focus = 4
                    focus_rows = [r for r in valid_rows if r.get('block_size_kb') == bs_focus]
                    if focus_rows:
                        by_depth_nj = {}
                        for r in focus_rows:
                            dc = r.get('device_count')
                            nj = r.get('numjobs_per_device')
                            depth = r.get('iodepth')
                            if not all(isinstance(x, int) for x in (dc, nj, depth)):
                                continue
                            by_dc = by_depth_nj.setdefault((depth, nj), {})
                            cur = by_dc.get(dc)
                            if cur is None or float(r.get('combined_mb_per_sec', 0) or 0) > float(cur.get('combined_mb_per_sec', 0) or 0):
                                by_dc[dc] = r

                        focus_depths = sorted({k[0] for k in by_depth_nj.keys()})
                        for depth in focus_depths:
                            njs = sorted({nj for (d, nj) in by_depth_nj.keys() if d == depth})
                            if not njs:
                                continue

                            fig, ax1 = plt.subplots(figsize=(12, 6))
                            ax2 = ax1.twinx()

                            all_xs = set()

                            cmap = plt.get_cmap('tab10')
                            for i, nj in enumerate(njs):
                                by_dc = by_depth_nj.get((depth, nj), {})
                                xs = sorted(by_dc.keys())
                                if not xs:
                                    continue
                                all_xs.update(xs)
                                ys_bw = [float(by_dc[dc].get('combined_mb_per_sec', 0) or 0) for dc in xs]
                                ys_lat = [_lat_w_us(by_dc[dc]) for dc in xs]

                                color = cmap(i % 10)
                                label = f"jobs/dev={nj}"
                                ax1.plot(xs, ys_bw, marker='o', linewidth=2, color=color, label=label)
                                ax2.plot(xs, ys_lat, marker='x', linestyle='--', linewidth=1.5, color=color, label='_nolegend_')

                                # Highlight chosen point if it belongs to this series and the 4KB focus.
                                if chosen_bs == bs_focus and chosen_depth == depth and chosen_nj == nj and chosen_dc in by_dc:
                                    try:
                                        y_star = float(by_dc[chosen_dc].get('combined_mb_per_sec', 0) or 0)
                                        ax1.plot([chosen_dc], [y_star], marker='*', markersize=18,
                                                 color='gold', markeredgecolor='black', zorder=10)
                                        ax1.annotate(
                                            "chosen",
                                            xy=(chosen_dc, y_star),
                                            xytext=(8, 10),
                                            textcoords='offset points',
                                            fontsize=9,
                                            bbox=dict(boxstyle='round,pad=0.25', fc='white', alpha=0.7),
                                        )
                                    except Exception:
                                        pass

                            ax1.set_xlabel('Devices (swap partitions)', fontsize=12)
                            ax1.set_ylabel('Throughput (MB/s; higher is better)', fontsize=12)
                            ax2.set_ylabel(f"Weighted latency (µs; {metric}; lower is better)", fontsize=12)

                            # Devices are typically powers of two; use log2 axis for readability.
                            try:
                                ax1.set_xscale('log', base=2)
                                ticks = sorted(int(x) for x in all_xs if isinstance(x, int) and x > 0)
                                if ticks:
                                    ax1.set_xticks(ticks)
                                    ax1.set_xticklabels([f"2^{int(round(math.log2(x)))}" for x in ticks])
                            except Exception:
                                pass

                            ax1.set_title(
                                f"Swap Stripe Matrix @ {bs_focus}KB (iodepth={depth}, rwmixread={mix}%)\n"
                                + "Solid=throughput (left axis), dashed=latency (right axis)",
                                fontsize=14,
                                fontweight='bold',
                            )
                            ax1.grid(True, alpha=0.3)
                            ax1.legend(fontsize=9, loc='upper left')

                            chart_file = f"{output_dir}/swap-partitions-matrix-4k-devices-vs-jobs-depth{depth}-{timestamp}.png"
                            plt.tight_layout()
                            plt.savefig(chart_file, dpi=150, bbox_inches='tight')
                            plt.close()
                            chart_files.append(chart_file)
                            log_info(f"Generated swap matrix 4KB focus chart: {chart_file}")
        except Exception as e:
            log_warn(f"Failed to generate swap-partitions matrix charts: {e}")
        
        # Chart 5: Latency Distribution (Box Plot)
        if 'latency_comparison' in results:
            comp = results['latency_comparison']
            has_read = 'read_latency' in comp and any('p50_read_us' in r for r in comp['read_latency'] if 'error' not in r)
            has_write = 'write_latency' in comp and any('p50_write_us' in w for w in comp['write_latency'] if 'error' not in w)
            
            if has_read or has_write:
                fig, axes = plt.subplots(1, 2, figsize=(14, 6))

                def _fmt_latency_us(value_us: float) -> str:
                    try:
                        v = float(value_us)
                    except Exception:
                        return "?"
                    if v >= 1000:
                        return f"{v/1000:.1f}ms"
                    return f"{v:.0f}µs"

                def _build_bxp_stats(min_us: float, med_us: float, p95_us: float, max_us: float):
                    # We don't have true quartiles; approximate Q1 between min and median.
                    q1 = med_us - (med_us - min_us) * 0.5
                    q3 = p95_us
                    # Use p95 as upper whisker to keep plot readable; max becomes an annotated label.
                    return {
                        'whislo': min_us,
                        'q1': q1,
                        'med': med_us,
                        'q3': q3,
                        'whishi': p95_us,
                        'fliers': [],
                    }
                
                # Read latency distribution
                if has_read:
                    read_data = comp['read_latency']
                    valid_reads = [r for r in read_data if 'error' not in r and 'p50_read_us' in r]

                    labels = []
                    stats = []
                    outlier_max = []
                    for r in valid_reads:
                        label = f"{r['compressor']}\n{r['allocator']}\n{r.get('access_pattern', '')}"
                        labels.append(label)

                        min_us = float(r.get('min_read_us', 0) or 0)
                        med_us = float(r.get('p50_read_us', 0) or 0)
                        p95_us = float(r.get('p95_read_us', 0) or 0)
                        max_us = float(r.get('max_read_us', 0) or 0)
                        stats.append(_build_bxp_stats(min_us, med_us, p95_us, max_us))
                        outlier_max.append(max_us)

                    axes[0].bxp(stats, showfliers=False, patch_artist=True)
                    axes[0].set_xticklabels(labels)
                    axes[0].set_ylabel('Latency (µs)', fontsize=12)
                    axes[0].set_title('Read Latency Distribution', fontsize=12, fontweight='bold')
                    axes[0].tick_params(axis='x', rotation=45)
                    axes[0].grid(True, alpha=0.3, axis='y')

                    # Scale to whiskers/p95 so boxes are readable, annotate outlier max values.
                    y_top = max((s['whishi'] for s in stats), default=1.0)
                    y_top = max(y_top * 1.35, 1.0)
                    axes[0].set_ylim(0, y_top)
                    for i, (s, m) in enumerate(zip(stats, outlier_max), start=1):
                        if m > (s['whishi'] * 1.05) and m > 0:
                            axes[0].text(i, y_top * 0.98, f"max { _fmt_latency_us(m) }",
                                         ha='center', va='top', fontsize=8)
                
                # Write latency distribution
                if has_write:
                    write_data = comp['write_latency']
                    valid_writes = [w for w in write_data if 'error' not in w and 'p50_write_us' in w]

                    labels = []
                    stats = []
                    outlier_max = []
                    for w in valid_writes:
                        label = f"{w['compressor']}\n{w['allocator']}"
                        labels.append(label)

                        min_us = float(w.get('min_write_us', 0) or 0)
                        med_us = float(w.get('p50_write_us', 0) or 0)
                        p95_us = float(w.get('p95_write_us', 0) or 0)
                        max_us = float(w.get('max_write_us', 0) or 0)
                        stats.append(_build_bxp_stats(min_us, med_us, p95_us, max_us))
                        outlier_max.append(max_us)

                    axes[1].bxp(stats, showfliers=False, patch_artist=True)
                    axes[1].set_xticklabels(labels)
                    axes[1].set_ylabel('Latency (µs)', fontsize=12)
                    axes[1].set_title('Write Latency Distribution', fontsize=12, fontweight='bold')
                    axes[1].tick_params(axis='x', rotation=45)
                    axes[1].grid(True, alpha=0.3, axis='y')

                    y_top = max((s['whishi'] for s in stats), default=1.0)
                    y_top = max(y_top * 1.35, 1.0)
                    axes[1].set_ylim(0, y_top)
                    for i, (s, m) in enumerate(zip(stats, outlier_max), start=1):
                        if m > (s['whishi'] * 1.05) and m > 0:
                            axes[1].text(i, y_top * 0.98, f"max { _fmt_latency_us(m) }",
                                         ha='center', va='top', fontsize=8)
                
                # Add explanatory legend for box plot components
                # Create a text box with explanation
                legend_text = 'Box Plot Legend:\n' \
                             '• Box: Q1-Q3 (approx)\n' \
                             '• Line in box: Median (p50)\n' \
                             '• Whiskers: min..p95 (for readable scaling)\n' \
                             '• Outliers: max shown as label (no points)'
                
                # Place legend below the subplots
                fig.text(0.5, -0.05, legend_text, ha='center', va='top', 
                        fontsize=9, bbox=dict(boxstyle='round', facecolor='wheat', alpha=0.3))
                
                chart_file = f"{output_dir}/benchmark-latency-distribution-{timestamp}.png"
                plt.tight_layout()
                plt.subplots_adjust(bottom=0.15)  # Make room for legend
                plt.savefig(chart_file, dpi=150, bbox_inches='tight')
                plt.close()
                chart_files.append(chart_file)
                log_info(f"Generated latency distribution chart: {chart_file}")
        
        # Chart: Allocator Comparison (compression ratio vs efficiency)
        if 'allocators' in results and results['allocators'] and len(results['allocators']) > 1:
            try:
                fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 6))

                # Aggregate allocator results across data patterns to avoid duplicates.
                raw_allocs = [a for a in results['allocators'] if isinstance(a, dict)]
                valid_allocs = [
                    a for a in raw_allocs
                    if 'error' not in a
                    and a.get('reliable', True)
                    and isinstance(a.get('efficiency_pct'), (int, float))
                    and isinstance(a.get('compression_ratio'), (int, float))
                    and float(a.get('compression_ratio') or 0) > 0
                ]

                agg = {}
                for a in valid_allocs:
                    name = a.get('allocator') or 'Unknown'
                    agg.setdefault(name, []).append(a)

                allocator_names = sorted(agg.keys())
                if len(allocator_names) < 2:
                    raise ValueError('Need at least 2 allocators for comparison chart')
                compression_ratios = []
                efficiencies = []
                mem_used = []
                compr_size = []
                for name in allocator_names:
                    items = agg[name]
                    compression_ratios.append(sum(float(i.get('compression_ratio', 0) or 0) for i in items) / max(1, len(items)))
                    efficiencies.append(sum(float(i.get('efficiency_pct', 0) or 0) for i in items) / max(1, len(items)))
                    mem_used.append(sum(float(i.get('mem_used_mb', 0) or 0) for i in items) / max(1, len(items)))
                    compr_size.append(sum(float(i.get('compr_size_mb', 0) or 0) for i in items) / max(1, len(items)))
                
                # Calculate overhead for each allocator
                overhead_mb = [mem_used[i] - compr_size[i] for i in range(len(mem_used))]
                
                # Chart 1: Compression Ratio vs Efficiency
                x = range(len(allocator_names))
                width = 0.35
                
                bars1 = ax1.bar([i - width/2 for i in x], compression_ratios, width, 
                               label='Compression Ratio', color='steelblue', alpha=0.8)
                ax1_twin = ax1.twinx()
                bars2 = ax1_twin.bar([i + width/2 for i in x], efficiencies, width,
                                    label='Efficiency %', color='coral', alpha=0.8)
                
                ax1.set_xlabel('Allocator', fontsize=12)
                ax1.set_ylabel('Compression Ratio (x)', fontsize=12, color='steelblue')
                ax1_twin.set_ylabel('Efficiency (%)', fontsize=12, color='coral')
                ax1.set_title('Allocator Performance: Ratio vs Efficiency', fontsize=14, fontweight='bold')
                ax1.set_xticks(x)
                ax1.set_xticklabels(allocator_names)
                ax1.tick_params(axis='y', labelcolor='steelblue')
                ax1_twin.tick_params(axis='y', labelcolor='coral')
                ax1.grid(True, alpha=0.3, axis='y')
                
                # Add value labels on bars
                for i, (bar1, bar2) in enumerate(zip(bars1, bars2)):
                    height1 = bar1.get_height()
                    height2 = bar2.get_height()
                    ax1.text(bar1.get_x() + bar1.get_width()/2., height1,
                            f'{height1:.1f}x', ha='center', va='bottom', fontsize=9)
                    ax1_twin.text(bar2.get_x() + bar2.get_width()/2., height2,
                                 f'{height2:.0f}%', ha='center', va='bottom', fontsize=9)
                
                # Combined legend
                lines1, labels1 = ax1.get_legend_handles_labels()
                lines2, labels2 = ax1_twin.get_legend_handles_labels()
                ax1.legend(lines1 + lines2, labels1 + labels2, loc='upper left', fontsize=10)
                
                # Chart 2: Memory Breakdown (compressed + overhead)
                bottom = [0] * len(allocator_names)
                bars_compr = ax2.bar(x, compr_size, label='Compressed Data', color='lightgreen', alpha=0.8)
                bars_overhead = ax2.bar(x, overhead_mb, bottom=compr_size, 
                                       label='Allocator Overhead', color='salmon', alpha=0.8)
                
                ax2.set_xlabel('Allocator', fontsize=12)
                ax2.set_ylabel('Memory Used (MB)', fontsize=12)
                ax2.set_title('Memory Breakdown: Compressed Data + Overhead', fontsize=14, fontweight='bold')
                ax2.set_xticks(x)
                ax2.set_xticklabels(allocator_names)
                ax2.legend(fontsize=10)
                ax2.grid(True, alpha=0.3, axis='y')
                
                # Add total labels on bars
                for i in range(len(allocator_names)):
                    total = compr_size[i] + overhead_mb[i]
                    ax2.text(i, total, f'{total:.1f}MB', ha='center', va='bottom', fontsize=9)
                    # Add overhead percentage
                    if compr_size[i] > 0:
                        overhead_pct = (overhead_mb[i] / compr_size[i]) * 100
                        ax2.text(i, compr_size[i] + overhead_mb[i]/2, 
                                f'+{overhead_pct:.0f}%', ha='center', va='center', 
                                fontsize=8, color='white', weight='bold')
                
                plt.tight_layout()
                chart_file = f"{output_dir}/benchmark-allocator-comparison-{timestamp}.png"
                plt.savefig(chart_file, dpi=150, bbox_inches='tight')
                plt.close()
                chart_files.append(chart_file)
                log_info(f"Generated allocator comparison chart: {chart_file}")
                
            except Exception as e:
                log_warn(f"Failed to generate allocator comparison chart: {e}")
                log_debug(traceback.format_exc())
        
    except Exception as e:
        log_error(f"Failed to generate charts: {e}")
        import traceback
        log_debug(traceback.format_exc())
    
    # Convert PNG to WebP if requested
    if webp and chart_files:
        log_info("Converting charts to WebP format...")
        webp_files = []
        try:
            from PIL import Image
            for png_file in chart_files:
                if png_file.endswith('.png'):
                    webp_file = png_file.replace('.png', '.webp')
                    try:
                        img = Image.open(png_file)
                        img.save(webp_file, 'WEBP', quality=85, method=6)
                        # Verify the WebP file was created successfully
                        if os.path.exists(webp_file) and os.path.getsize(webp_file) > 0:
                            webp_files.append(webp_file)
                            # Remove original PNG only after successful conversion
                            os.remove(png_file)
                            log_info(f"Converted {os.path.basename(png_file)} to WebP")
                        else:
                            log_warn(f"WebP conversion produced invalid file for {png_file}, keeping PNG")
                            webp_files.append(png_file)
                    except Exception as e:
                        log_warn(f"Failed to convert {png_file} to WebP: {e}")
                        # Keep original PNG if conversion fails
                        webp_files.append(png_file)
            if webp_files:
                chart_files = webp_files
                log_info(f"✓ Converted {len(webp_files)} charts to WebP")
        except ImportError:
            log_warn("PIL (Pillow) not available - cannot convert to WebP")
            log_info("Install with: pip3 install Pillow")
        except Exception as e:
            log_warn(f"WebP conversion failed: {e}")
    
    return chart_files

def format_benchmark_html(results):
    """Format benchmark results as HTML for Telegram with visual indicators"""
    html = "<b>📊 Swap Benchmark Results</b>\n\n"
    
    # System info
    if 'system_info' in results:
        sysinfo = results['system_info']
        html += f"<b>💻 System:</b> {sysinfo.get('ram_gb', 'N/A')}GB RAM, {sysinfo.get('cpu_cores', 'N/A')} CPU cores\n\n"

    # Extra observability when DEBUG=1
    if _debug_enabled():
        try:
            stages = []
            if results.get('block_sizes'):
                stages.append(f"block_sizes={len(results.get('block_sizes') or [])}")
            if results.get('compressors'):
                stages.append(f"compressors={len(results.get('compressors') or [])}")
            if results.get('allocators'):
                stages.append(f"allocators={len(results.get('allocators') or [])}")
            if results.get('concurrency'):
                stages.append(f"concurrency={len(results.get('concurrency') or [])}")
            if isinstance(results.get('matrix'), dict):
                stages.append("matrix=yes")
            if isinstance(results.get('zswap_latency'), dict):
                stages.append("zswap_latency=yes")

            html += "<b>Debug</b>\n"
            if stages:
                html += f"Stages: {', '.join(stages)}\n"
            html += "Env:\n"
            html += f"  DEBUG={os.environ.get('DEBUG','')}\n"
            html += f"  BENCHMARK_PATTERNS={os.environ.get('BENCHMARK_PATTERNS','')}\n"
            html += f"  BENCHMARK_GUARD_SWAP={os.environ.get('BENCHMARK_GUARD_SWAP','yes')}\n"
            html += f"  BENCHMARK_GUARD_SWAP_MB={os.environ.get('BENCHMARK_GUARD_SWAP_MB', str(DEFAULT_GUARD_SWAP_MB))}\n"
            html += f"  BENCHMARK_GUARD_SWAP_PRIO={os.environ.get('BENCHMARK_GUARD_SWAP_PRIO', str(DEFAULT_GUARD_SWAP_PRIO))}\n"
            html += f"  BENCHMARK_ZRAM_MEM_LIMIT_MB={os.environ.get('BENCHMARK_ZRAM_MEM_LIMIT_MB','')}\n"
            html += f"  BENCHMARK_ZRAM_MEM_LIMIT_MB_RANDOM={os.environ.get('BENCHMARK_ZRAM_MEM_LIMIT_MB_RANDOM','')}\n"
            html += "\n"
        except Exception:
            pass
    
    # Block size tests with visual bar chart
    if 'block_sizes' in results and results['block_sizes']:
        html += "<b>📦 Block Size Performance:</b>\n"
        
        # Check if we have random I/O data
        has_random = any(b.get('rand_write_mb_per_sec', 0) > 0 or b.get('rand_read_mb_per_sec', 0) > 0 for b in results['block_sizes'])
        
        if has_random:
            # Show both sequential and random side-by-side
            html += "<i>Sequential I/O:</i>\n"
            max_total = max((b.get('write_mb_per_sec', 0) + b.get('read_mb_per_sec', 0)) for b in results['block_sizes'])
            
            for block in results['block_sizes']:
                size_kb = block.get('block_size_kb', 'N/A')
                write_mb = block.get('write_mb_per_sec', 0)
                read_mb = block.get('read_mb_per_sec', 0)
                total = write_mb + read_mb
                bar_length = int((total / max_total) * 10) if max_total > 0 else 0
                bar = '█' * bar_length + '░' * (10 - bar_length)
                html += f"  {size_kb:3d}KB: {bar} ↑{write_mb:6.1f} ↓{read_mb:6.1f} MB/s\n"
            
            html += "\n<i>Random I/O:</i>\n"
            max_total_rand = max((b.get('rand_write_mb_per_sec', 0) + b.get('rand_read_mb_per_sec', 0)) for b in results['block_sizes'])
            
            for block in results['block_sizes']:
                size_kb = block.get('block_size_kb', 'N/A')
                rand_write_mb = block.get('rand_write_mb_per_sec', 0)
                rand_read_mb = block.get('rand_read_mb_per_sec', 0)
                total_rand = rand_write_mb + rand_read_mb
                bar_length = int((total_rand / max_total_rand) * 10) if max_total_rand > 0 else 0
                bar = '█' * bar_length + '░' * (10 - bar_length)
                html += f"  {size_kb:3d}KB: {bar} ↑{rand_write_mb:6.1f} ↓{rand_read_mb:6.1f} MB/s\n"
        else:
            # Only sequential data available
            html += "<i>(Sequential I/O, single-threaded)</i>\n"
            max_total = max((b.get('write_mb_per_sec', 0) + b.get('read_mb_per_sec', 0)) for b in results['block_sizes'])
            
            # VALIDATION: Check if max_total is actually 0
            if max_total == 0:
                log_warn("All block size results show 0 MB/s - check data structure and test execution")
                log_warn(f"Sample block data: {results['block_sizes'][0] if results['block_sizes'] else 'No data'}")
            
            for block in results['block_sizes']:
                size_kb = block.get('block_size_kb', 'N/A')
                write_mb = block.get('write_mb_per_sec', 0)
                read_mb = block.get('read_mb_per_sec', 0)
                
                # DEBUG: Log individual block results
                if write_mb == 0 and read_mb == 0:
                    log_debug(f"Block {size_kb}KB: No throughput data. Keys present: {list(block.keys())}")
                
                total = write_mb + read_mb
                bar_length = int((total / max_total) * 10) if max_total > 0 else 0
                bar = '█' * bar_length + '░' * (10 - bar_length)
                html += f"  {size_kb:3d}KB: {bar} ↑{write_mb:6.1f} ↓{read_mb:6.1f} MB/s\n"
        html += "\n"
    
    # Compressor comparison with visual indicators
    if 'compressors' in results and results['compressors']:
        html += "<b>🗜️ Compressor Performance:</b>\n"
        
        max_ratio = max(c.get('compression_ratio', 0) for c in results['compressors'])
        
        # VALIDATION: Check for unrealistic ratios
        if max_ratio > COMPRESSION_RATIO_SUSPICIOUS:
            log_warn(f"Suspicious max compression ratio: {max_ratio:.1f}x (expected {COMPRESSION_RATIO_MIN}-{COMPRESSION_RATIO_MAX}x for typical data)")
        
        for comp in results['compressors']:
            name = comp.get('compressor', 'N/A')
            ratio = comp.get('compression_ratio', 0)
            eff = comp.get('efficiency_pct', 0)
            
            # VALIDATION: Check for issues
            if 'error' in comp:
                log_warn(f"Compressor {name} had error: {comp['error']}")
            if 'warning' in comp:
                log_warn(f"Compressor {name} warning: {comp['warning']}")
            
            bar_length = int((ratio / max_ratio) * 10) if max_ratio > 0 else 0
            bar = '▓' * bar_length + '░' * (10 - bar_length)
            is_best = ratio == max_ratio
            marker = " ⭐" if is_best else ""
            html += f"  {name:8s}: {bar} {ratio:.1f}x ratio, {eff:+.0f}% eff{marker}\n"
            if _debug_enabled():
                try:
                    html += (
                        "    "
                        + f"swap_activity_mb={comp.get('swap_activity_mb','')} "
                        + f"zram_disksize_mb={comp.get('zram_disksize_mb','')} "
                        + f"zram_mem_limit_mb={comp.get('zram_mem_limit_mb','')} "
                        + f"guard_swap_delta_kb={comp.get('guard_swap_used_kb_delta','')}\n"
                    )
                except Exception:
                    pass
        html += "\n"
    
    # Allocator comparison (aggregate across data patterns)
    if 'allocators' in results and results['allocators']:
        html += "<b>💾 Allocator Performance:</b>\n"
        raw_allocs = [a for a in results['allocators'] if isinstance(a, dict)]
        valid_allocs = [
            a for a in raw_allocs
            if 'error' not in a
            and a.get('reliable', True)
            and isinstance(a.get('efficiency_pct'), (int, float))
            and isinstance(a.get('compression_ratio'), (int, float))
            and float(a.get('compression_ratio') or 0) > 0
        ]

        if not valid_allocs:
            html += "  No reliable allocator results (all failed or insufficient swap activity)\n\n"
        else:
            agg = {}
            for a in valid_allocs:
                name = a.get('allocator') or 'N/A'
                agg.setdefault(name, []).append(a)

            summary = []
            for name, items in agg.items():
                avg_ratio = sum(float(i.get('compression_ratio', 0) or 0) for i in items) / max(1, len(items))
                avg_eff = sum(float(i.get('efficiency_pct', 0) or 0) for i in items) / max(1, len(items))
                patterns = sorted({(i.get('data_pattern') or '').strip() for i in items if i.get('data_pattern')})
                summary.append({
                    'allocator': name,
                    'avg_ratio': avg_ratio,
                    'avg_eff': avg_eff,
                    'n': len(items),
                    'patterns': patterns,
                })

            # Sort best-first by efficiency.
            summary.sort(key=lambda x: float(x.get('avg_eff', 0) or 0), reverse=True)
            max_eff = max(float(x.get('avg_eff', 0) or 0) for x in summary)
            min_eff = min(float(x.get('avg_eff', 0) or 0) for x in summary)

            for row in summary:
                name = row['allocator']
                ratio = float(row.get('avg_ratio', 0) or 0)
                eff = float(row.get('avg_eff', 0) or 0)
                patterns = row.get('patterns') or []
                patterns_str = f" (patterns: {','.join(patterns)})" if patterns else ""

                if max_eff > 0 and eff >= 0:
                    bar_length = int((eff / max_eff) * 10)
                else:
                    bar_length = 0
                bar = '▓' * bar_length + '░' * (10 - bar_length)

                is_best = (eff == max_eff and max_eff > 0)
                is_worst = (eff == min_eff)
                marker = " ⭐" if is_best else (" ⚠️" if is_worst and eff < 0 else "")

                diff_str = ""
                if not is_best and max_eff > 0:
                    diff_str = f" ({eff - max_eff:+.0f}% vs best)"

                html += f"  {name:8s}: {bar} {ratio:.1f}x ratio, {eff:+.0f}% eff{diff_str}{marker}{patterns_str}\n"
            html += "\n"
    
    # Concurrency tests with scaling chart
    if 'concurrency' in results and results['concurrency']:
        html += "<b>⚡ fio Job Scaling (single target):</b>\n"
        html += "<i>Note: this varies fio worker count against one swap target; it is NOT swap device count.</i>\n"
        # Only compute max_total from successful tests
        successful_tests = [c for c in results['concurrency'] if 'error' not in c]
        if successful_tests:
            max_total = max((c.get('write_mb_per_sec', 0) + c.get('read_mb_per_sec', 0)) for c in successful_tests)
        else:
            max_total = 1  # Avoid division by zero
        
        for concur in results['concurrency']:
            files = concur.get('num_files', 0)
            if files == 0 or not isinstance(files, int):
                files_str = str(files)
            else:
                files_str = f"{files:2d}"
            
            # Check if test failed
            if 'error' in concur:
                html += f"  {files_str} files: ❌ FAILED ({concur.get('error', 'unknown error')})\n"
            else:
                write_mb = concur.get('write_mb_per_sec', 0)
                read_mb = concur.get('read_mb_per_sec', 0)
                total = write_mb + read_mb
                bar_length = int((total / max_total) * 10) if max_total > 0 else 0
                bar = '█' * bar_length + '░' * (10 - bar_length)
                is_best = (total == max_total and max_total > 0)
                marker = " ⭐" if is_best else ""
                html += f"  {files_str} files: {bar} ↑{write_mb:.0f} ↓{read_mb:.0f} MB/s{marker}\n"
        html += "\n"
    
    # Matrix test results (block size × concurrency)
    if 'matrix' in results and isinstance(results['matrix'], dict) and 'optimal' in results['matrix']:
        matrix = results['matrix']
        html += "<b>🎯 Optimal I/O Parameters (single target):</b>\n\n"
        
        # Show disk I/O optimization results
        html += "  <b>📀 Disk I/O Optimized:</b>\n"
        html += "  <i>fio randrw 50/50 against one test target (not multi-device striping)</i>\n"
        html += "  fio command: <code>fio --output-format=json /tmp/fio_matrix.job</code> (job regenerated per test)\n"
        if 'best_combined' in matrix.get('optimal', {}):
            best = matrix['optimal']['best_combined']
            html += f"  Best: {best['block_size_kb']}KB × {best['concurrency']} jobs = {best['throughput_mb_per_sec']:.0f} MB/s\n"
        
        # Stripe width is measured separately via real swap-partition benchmarking.
        spm = results.get('swap_partitions_matrix')
        if isinstance(spm, dict) and isinstance((spm.get('optimal') or {}).get('recommended_swap_stripe_width'), int):
            rec_width = spm['optimal']['recommended_swap_stripe_width']
            html += f"  SWAP_STRIPE_WIDTH={rec_width} ✅ (measured on real partitions)\n"
        
        # Show ZSWAP-specific configuration
        html += "\n  <b>💾 ZSWAP Configuration:</b>\n"
        if 'recommended_page_cluster' in matrix.get('optimal', {}):
            disk_cluster = matrix['optimal']['recommended_page_cluster']
            html += f"  SWAP_PAGE_CLUSTER=0 ✅ (not {disk_cluster}!)\n"
        else:
            html += f"  SWAP_PAGE_CLUSTER=0 ✅\n"
        html += "  <i>Reason: ZSWAP is RAM cache, no seek cost</i>\n"
        if 'best_combined' in matrix.get('optimal', {}):
            best = matrix['optimal']['best_combined']
            html += f"  <i>{best['block_size_kb']}KB readahead wastes bandwidth</i>\n"
        
        html += "\n  ⚠️ <i>Above matrix measures disk I/O tuning (fio against a single target).</i>\n"
        html += "  <i>Swap device striping (multiple partitions) is tested separately below.</i>\n"
        html += "\n"

    # Swap partition striping matrix (real partitions)
    spm = results.get('swap_partitions_matrix')
    if isinstance(spm, dict) and 'error' not in spm:
        html += "<b>🧪 Disk Swap Striping Benchmark (real partitions):</b>\n"
        parts = spm.get('swap_partitions') or []
        bs_list = spm.get('block_sizes') or []
        dev_counts = spm.get('device_counts') or []
        jobs_levels = spm.get('numjobs_per_device_levels') or []
        iodepth_levels = spm.get('iodepth_levels') or []
        runtime_sec = spm.get('runtime_sec')
        per_dev_mb = spm.get('per_device_size_mb')
        workload = spm.get('fio_workload') or {}
        use_full_device_size = bool(spm.get('use_full_device_size'))
        full_device_size_margin_mb = spm.get('full_device_size_margin_mb')

        if parts:
            html += f"  Detected GPT swap partitions: {len(parts)}\n"
        if dev_counts and jobs_levels and bs_list:
            html += f"  Tested: devices={dev_counts}, jobs/dev={jobs_levels}, bs={bs_list}KB\n"
        if iodepth_levels:
            html += f"  Tested: iodepth={iodepth_levels}\n"

        lat_metric = (workload.get('latency_metric') or (spm.get('optimal') or {}).get('latency_metric') or 'p99')
        html += f"  Latency metric (selection): {lat_metric}\n"

        mix = workload.get('rwmixread')
        mix_str = f"{int(mix)}%" if isinstance(mix, int) else "?%"
        if runtime_sec:
            if use_full_device_size:
                margin = f" (minus {int(full_device_size_margin_mb)}MB)" if isinstance(full_device_size_margin_mb, int) else ""
                html += f"  Workload: fio randrw rwmixread={mix_str}, direct=1, runtime={runtime_sec}s, size=full-device{margin}\n"
            elif per_dev_mb:
                html += f"  Workload: fio randrw rwmixread={mix_str}, direct=1, runtime={runtime_sec}s, size={per_dev_mb}MB per device\n"
            else:
                html += f"  Workload: fio randrw rwmixread={mix_str}, direct=1, runtime={runtime_sec}s\n"

        html += "  fio command: <code>fio --output-format=json /tmp/fio_swap_partitions_matrix.job</code> (job regenerated per test)\n"

        opt = spm.get('optimal') or {}
        chosen = opt.get('best_combined') or {}
        rec_width = opt.get('recommended_swap_stripe_width')
        rec_jobs = opt.get('recommended_fio_numjobs_per_device')
        rec_iodepth = opt.get('recommended_fio_iodepth')
        best_score = opt.get('best_score')
        if isinstance(rec_width, int) and rec_width > 0:
            html += f"  Selected SWAP_STRIPE_WIDTH={rec_width} ✅\n"
            if parts and len(parts) > rec_width:
                html += f"  Install behavior: repartition swap space to exactly {rec_width} partitions\n"
        if isinstance(rec_jobs, int) and rec_jobs > 0:
            html += f"  (fio tuning) jobs/dev={rec_jobs}  <i>not a system setting</i>\n"
        if isinstance(rec_iodepth, int) and rec_iodepth > 0:
            html += f"  (fio tuning) iodepth={rec_iodepth}  <i>not a system setting</i>\n"
        if chosen.get('combined_mb_per_sec') is not None:
            html += (
                f"  Chosen point: {chosen.get('device_count','?')} dev × {chosen.get('numjobs_per_device','?')} jobs/dev @ {chosen.get('block_size_kb','?')}KB (iodepth={chosen.get('iodepth','?')}) = "
                + f"{chosen.get('combined_mb_per_sec', 0):.0f} MB/s\n"
            )

        # If we computed a 4KB-only recommendation, show it explicitly.
        try:
            opt_by_bs = spm.get('optimal_by_block_size_kb') or {}
            o4 = opt_by_bs.get('4')
            if isinstance(o4, dict) and isinstance(o4.get('recommended_swap_stripe_width'), int):
                html += (
                    f"  4KB-only recommendation: SWAP_STRIPE_WIDTH={int(o4.get('recommended_swap_stripe_width'))} "
                    + f"(jobs/dev={int(o4.get('recommended_fio_numjobs_per_device', 1))}, iodepth={int(o4.get('recommended_fio_iodepth', 1))})\n"
                )
        except Exception:
            pass
        if isinstance(best_score, (int, float)) and best_score:
            html += f"  Selection rule: smallest device_count within 95% of best score (throughput/latency)\n"

        top = opt.get('top_candidates') or []
        if isinstance(top, list) and top:
            html += "  Top candidates (best score first):\n"
            for idx, row in enumerate(top[:3], start=1):
                try:
                    bw = float(row.get('combined_mb_per_sec', 0) or 0)
                    rlat_mean = float(row.get('read_latency_us', 0) or 0)
                    wlat_mean = float(row.get('write_latency_us', 0) or 0)
                    rlat_p99 = float(row.get('read_latency_p99_us', 0) or 0)
                    wlat_p99 = float(row.get('write_latency_p99_us', 0) or 0)
                    lat = (0.7 * rlat_mean) + (0.3 * wlat_mean)
                    score = (bw / lat) if lat > 0 else 0.0
                    lat_extra = ""
                    if rlat_p99 > 0 or wlat_p99 > 0:
                        latp = (0.7 * rlat_p99) + (0.3 * wlat_p99)
                        lat_extra = f", p99~{latp:.1f}µs"
                    html += (
                        f"    #{idx}: {int(row.get('device_count', 1))} dev × {int(row.get('numjobs_per_device', 1))} jobs/dev "
                        + f"@ {int(row.get('block_size_kb', 4))}KB (iodepth={int(row.get('iodepth', 1))}) — {bw:.0f} MB/s, mean~{lat:.1f}µs{lat_extra}, score={score:.4f}\n"
                    )
                except Exception:
                    pass
        html += "\n"
    elif isinstance(spm, dict) and 'error' in spm:
        html += "<b>🧪 Disk Swap Striping Benchmark (real partitions):</b>\n"
        html += f"  ⚠️ <i>Skipped/failed: {spm.get('error','unknown error')}</i>\n\n"
    
    # ZSWAP vs ZRAM comparison
    if 'zswap_vs_zram' in results and 'error' not in results['zswap_vs_zram']:
        comp = results['zswap_vs_zram']
        html += "<b>⚔️ ZSWAP vs ZRAM:</b>\n"
        
        if 'zram' in comp and 'zswap' in comp:
            zram_ratio = comp['zram'].get('compression_ratio', 0)
            zswap_ratio = comp['zswap'].get('compression_ratio', 0)
            zram_lat = comp['zram'].get('avg_latency_us', 0)
            zswap_lat = comp['zswap'].get('avg_latency_us', 0)
            
            # Check for suspicious low ratios (likely failed tests)
            if zram_ratio < MIN_VALID_COMPRESSION_RATIO or zswap_ratio < MIN_VALID_COMPRESSION_RATIO:
                html += f"  ⚠️ <i>Test results appear invalid (compression ratio &lt; {MIN_VALID_COMPRESSION_RATIO:.1f}x)</i>\n"
                html += "  <i>This may indicate test failure or insufficient memory pressure</i>\n"
            else:
                html += f"  ZRAM:  {zram_ratio:.1f}x ratio, {zram_lat:.1f}µs latency\n"
                html += f"  ZSWAP: {zswap_ratio:.1f}x ratio, {zswap_lat:.1f}µs latency\n"
                
                # Determine winner (avoid division by zero)
                if zram_lat > 0 and zswap_lat > 0:
                    if zram_lat < zswap_lat:
                        winner = "ZRAM"
                        diff_pct = ((zswap_lat - zram_lat) / zram_lat) * 100
                        html += f"  ⭐ {winner} is {diff_pct:.0f}% faster\n"
                    elif zswap_lat < zram_lat:
                        winner = "ZSWAP"
                        diff_pct = ((zram_lat - zswap_lat) / zswap_lat) * 100
                        html += f"  ⭐ {winner} is {diff_pct:.0f}% faster\n"
                    # If equal latency, don't show a winner
        html += "\n"
    elif 'zswap_vs_zram' in results and 'error' in results['zswap_vs_zram']:
        html += "<b>⚔️ ZSWAP vs ZRAM:</b>\n"
        html += f"  ⚠️ <i>Test failed: {results['zswap_vs_zram']['error']}</i>\n\n"
    
    # Latency comparison results
    if 'latency_comparison' in results:
        lat_comp = results['latency_comparison']
        
        # Baseline
        if 'baseline' in lat_comp and 'read_ns' in lat_comp['baseline']:
            baseline = lat_comp['baseline']
            html += "<b>⚡ Memory Latency:</b>\n"
            html += f"  <i>Baseline (Native RAM):</i>\n"
            html += f"  Read:  {baseline['read_ns']:.0f} ns/page\n"
            html += f"  Write: {baseline['write_ns']:.0f} ns/page\n\n"
        
        # Write latency
        if 'write_latency' in lat_comp and lat_comp['write_latency']:
            html += "  <i>Write Latency (swap-out):</i>\n"
            valid_writes = [w for w in lat_comp['write_latency'] if 'error' not in w and 'avg_write_us' in w]
            
            # Get baseline for comparison
            baseline_write_ns = baseline.get('write_ns', 0) if 'baseline' in lat_comp else 0
            
            if valid_writes:
                min_latency = min(w['avg_write_us'] for w in valid_writes)
                max_latency = max(w['avg_write_us'] for w in valid_writes)
                
                # Show ALL write latency tests (6 total)
                for w in valid_writes:
                    avg_us = w['avg_write_us']
                    # Visual: more filled = better (lower latency)
                    bar_len = int(10 * (max_latency - avg_us) / (max_latency - min_latency + 1)) if max_latency > min_latency else 5
                    bar = '█' * bar_len + '░' * (10 - bar_len)
                    is_best = (avg_us == min_latency)
                    marker = " ⭐" if is_best else ""
                    
                    # Add comparison with baseline if available
                    comparison = ""
                    if baseline_write_ns > 0:
                        slowdown = (avg_us * 1000) / baseline_write_ns  # Convert us to ns for comparison
                        comparison = f" ({slowdown:.0f}× slower)"
                    
                    html += f"  {w['compressor']:6s}+{w['allocator']:8s}: {bar} {avg_us:6.1f}µs{comparison}{marker}\n"
            html += "\n"
        
        # Read latency
        if 'read_latency' in lat_comp and lat_comp['read_latency']:
            html += "  <i>Read Latency (page fault):</i>\n"
            valid_reads = [r for r in lat_comp['read_latency'] if 'error' not in r and 'avg_read_us' in r]
            
            # Get baseline for comparison
            baseline_read_ns = baseline.get('read_ns', 0) if 'baseline' in lat_comp else 0
            
            if valid_reads:
                # Group by compressor+allocator
                unique_configs = {}
                for r in valid_reads:
                    key = f"{r['compressor']}+{r['allocator']}"
                    if key not in unique_configs or r.get('access_pattern') == 'random':
                        unique_configs[key] = r
                
                configs_list = list(unique_configs.values())
                min_latency = min(r['avg_read_us'] for r in configs_list)
                max_latency = max(r['avg_read_us'] for r in configs_list)
                
                for r in configs_list[:4]:  # Limit to top 4
                    avg_us = r['avg_read_us']
                    pattern = r.get('access_pattern', 'seq')[:3]
                    # Visual: more filled = better (lower latency)
                    bar_len = int(10 * (max_latency - avg_us) / (max_latency - min_latency + 1)) if max_latency > min_latency else 5
                    bar = '█' * bar_len + '░' * (10 - bar_len)
                    is_best = (avg_us == min_latency)
                    marker = " ⭐" if is_best else ""
                    
                    # Add comparison with baseline if available
                    comparison = ""
                    if baseline_read_ns > 0:
                        slowdown = (avg_us * 1000) / baseline_read_ns  # Convert us to ns for comparison
                        comparison = f" ({slowdown:.0f}× slower)"
                    
                    html += f"  {r['compressor']:6s}+{r['allocator']:8s}({pattern}): {bar} {avg_us:6.1f}µs{comparison}{marker}\n"
            html += "\n"
        
        # Add RAM vs ZRAM vs Disk latency comparison
        if 'baseline' in lat_comp and 'write_latency' in lat_comp and 'read_latency' in lat_comp:
            baseline = lat_comp['baseline']
            ram_read = baseline.get('read_ns', 0)
            ram_write = baseline.get('write_ns', 0)
            
            # Best ZRAM latencies
            valid_read_tests = [t for t in lat_comp['read_latency'] if 'error' not in t and 'avg_read_us' in t]
            valid_write_tests = [t for t in lat_comp['write_latency'] if 'error' not in t and 'avg_write_us' in t]
            
            if valid_read_tests and valid_write_tests and ram_read > 0 and ram_write > 0:
                best_zram_read = min([t['avg_read_us'] for t in valid_read_tests]) * 1000  # to ns
                best_zram_write = min([t['avg_write_us'] for t in valid_write_tests]) * 1000
                
                # Disk latency estimates (typical values)
                disk_read_ns = 5000000  # ~5ms typical for HDD
                disk_write_ns = 10000000  # ~10ms typical for HDD
                
                html += "<b>⚡ Latency Comparison:</b>\n"
                html += f"  <i>Read:</i>\n"
                html += f"  RAM:   {ram_read:8.0f} ns (baseline)\n"
                html += f"  ZRAM:  {best_zram_read:8.0f} ns ({best_zram_read/ram_read:4.0f}× slower)\n"
                html += f"  Disk:  {disk_read_ns/1000:8.0f} µs ({disk_read_ns/ram_read:4.0f}× slower)\n\n"
                html += f"  <i>Write:</i>\n"
                html += f"  RAM:   {ram_write:8.0f} ns (baseline)\n"
                html += f"  ZRAM:  {best_zram_write:8.0f} ns ({best_zram_write/ram_write:4.0f}× slower)\n"
                html += f"  Disk:  {disk_write_ns/1000:8.0f} µs ({disk_write_ns/ram_write:4.0f}× slower)\n"
                html += "\n"
    
    # ZSWAP latency results (real-world with disk backing)
    if 'zswap_latency' in results:
        zswap = results['zswap_latency']
        if 'error' not in zswap:
            html += "<b>🌊 ZSWAP Latency (with disk backing):</b>\n"
            
            # Phase 1: ZRAM baseline
            if 'zram_baseline' in zswap:
                zram = zswap['zram_baseline']
                ratio = zram.get('compression_ratio', 0)
                comp = zram.get('compressor', 'N/A')
                html += f"  ZRAM baseline: {ratio:.1f}× compression ({comp})\n"
            
            # Phase 2 & 3: ZSWAP with disk backing + latency analysis
            hot_us = 0
            cold_us = 0
            if 'zswap' in zswap and isinstance(zswap['zswap'], dict):
                zs = zswap['zswap']
                comp = zswap.get('compressor', 'N/A')
                pool = zswap.get('zpool', 'N/A')
                ratio = zs.get('compression_ratio', 0)

                html += f"  ZSWAP config: {comp} + {pool}\n"
                if ratio:
                    html += f"  Compression: {ratio:.1f}×\n"

                hot_us = zs.get('estimated_hot_latency_us', 0)
                cold_us = zs.get('avg_disk_read_latency_us', 0)
                if hot_us:
                    html += f"  Hot cache (pool hit): ~{hot_us:.0f}µs\n"
                if cold_us:
                    html += f"  Cold page (disk read): ~{cold_us:.0f}µs\n"

                writeback_mbps = zs.get('writeback_throughput_mbps', 0)
                if writeback_mbps:
                    html += f"  Writeback: {writeback_mbps:.0f} MB/s\n"

                devices = zswap.get('swap_devices', [])
                if devices:
                    html += f"  Swap devices: {len(devices)}\n"
            
            # Phase 4: Comparison summary
            if 'comparison' in zswap:
                comp_data = zswap['comparison']
                hot_overhead_us = comp_data.get('hot_latency_overhead_us', 0)
                cold_overhead_us = comp_data.get('cold_latency_overhead_us', 0)
                disk_overflow_mb = comp_data.get('disk_overflow_mb', 0)
                
                html += f"\n  <i>vs pure ZRAM:</i>\n"
                if hot_overhead_us:
                    html += f"  <i>- Hot cache overhead: +{hot_overhead_us:.0f}µs</i>\n"
                elif hot_us:
                    html += f"  <i>- Hot cache latency: ~{hot_us:.0f}µs</i>\n"

                if cold_overhead_us:
                    html += f"  <i>- Cold page overhead: +{cold_overhead_us:.0f}µs</i>\n"
                
                if disk_overflow_mb > 0:
                    html += f"  <i>- Disk overflow: {disk_overflow_mb:.0f}MB written</i>\n"
                else:
                    html += f"  <i>- All data stayed in ZSWAP pool</i>\n"
            
            html += "\n"
    
    # Calculate and display overall space efficiency
    if 'compressors' in results and results['compressors'] and 'system_info' in results:
        # Find the best compressor (highest compression ratio)
        best_comp = max(results['compressors'], key=lambda x: x.get('compression_ratio', 0))
        compression_ratio = best_comp.get('compression_ratio', 0)
        compressor_name = best_comp.get('compressor', 'N/A')
        
        if compression_ratio > 1.0:
            ram_gb = results['system_info'].get('ram_gb', 0)
            if ram_gb > 0:
                # Calculate ZSWAP pool size dynamically:
                # - 2GB RAM: 50% pool (maximize compression with zstd)
                # - 16GB RAM: 25% pool (use lz4 for speed)
                # - Scale linearly between these points
                if ram_gb <= 2:
                    zswap_pool_pct = 50
                elif ram_gb >= 16:
                    zswap_pool_pct = 25
                else:
                    # Linear interpolation: 50% at 2GB, 25% at 16GB
                    # slope = (25 - 50) / (16 - 2) = -25/14 ≈ -1.786 per GB
                    zswap_pool_pct = 50 - ((ram_gb - 2) * 1.786)
                
                zswap_pool_gb = ram_gb * (zswap_pool_pct / 100)
                zswap_effective_gb = zswap_pool_gb * compression_ratio
                disk_swap_gb = ram_gb * 2  # Per new 2x sizing policy
                total_virtual_gb = ram_gb + zswap_effective_gb + disk_swap_gb
                
                html += "<b>💾 Virtual Memory Capacity:</b>\n"
                html += f"  Physical RAM: {ram_gb:.1f}GB\n"
                html += f"  ZSWAP pool: {zswap_pool_gb:.1f}GB ({zswap_pool_pct:.0f}% of RAM)\n"
                html += f"  ZSWAP effective: {zswap_effective_gb:.1f}GB (@ {compression_ratio:.1f}x {compressor_name})\n"
                html += f"  Disk swap: {disk_swap_gb:.0f}GB (2× RAM per config)\n"
                html += f"  ────────────────────────────\n"
                html += f"  Total Virtual: ~{total_virtual_gb:.1f}GB\n"
                html += f"\n"
                html += f"  <i>Breakdown:</i>\n"
                html += f"  <i>- Active apps: {ram_gb:.1f}GB RAM</i>\n"
                html += f"  <i>- ZSWAP cache: {zswap_effective_gb:.1f}GB effective (hot pages)</i>\n"
                html += f"  <i>- Disk overflow: {disk_swap_gb:.0f}GB (cold pages)</i>\n"
                html += "\n"
    
    # Memory-only comparison
    if 'memory_only_comparison' in results:
        mem_comp = results['memory_only_comparison']
        html += "<b>🎯 Recommended Config:</b>\n"
        if 'best_overall' in mem_comp:
            best = mem_comp['best_overall']
            html += f"  Compressor: {best.get('compressor', 'N/A')}\n"
            html += f"  Allocator: {best.get('allocator', 'N/A')}\n"
            html += f"  Ratio: {best.get('compression_ratio', 0):.1f}x\n"
    
    return html

def main():
    parser = argparse.ArgumentParser(
        description='Swap Performance Benchmark',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    %(prog)s --test-all                         # Recommended: run all benchmarks (no legacy single-target matrix)
    %(prog)s --test-matrix                      # [LEGACY] Run block size × concurrency matrix (single target)
  %(prog)s --test-compressors                 # Test compression algorithms
  %(prog)s --test-allocators                  # Test memory allocators
  %(prog)s --test-latency --latency-size 100  # Run latency tests
  %(prog)s --block-size 64                    # [DEPRECATED] Use --test-matrix instead
  %(prog)s --test-concurrency 8               # [DEPRECATED] Use --test-matrix instead
  %(prog)s --output results.json --shell-config swap.conf
        """
    )
    
    parser.add_argument('--test-all', action='store_true',
                       help='Run all benchmarks including latency tests')
    parser.add_argument('--block-size', type=int, metavar='KB',
                       help='[DEPRECATED] Test specific block size in KB. Use --test-matrix instead for comprehensive testing')
    parser.add_argument('--test-compressors', action='store_true',
                       help='Test all compression algorithms')
    parser.add_argument('--test-allocators', action='store_true',
                       help='Test all memory allocators')
    parser.add_argument('--compare-memory-only', action='store_true',
                       help='Compare ZRAM configurations')
    parser.add_argument('--test-concurrency', type=int, metavar='N',
                       help='[DEPRECATED] Test concurrency with N swap files. Use --test-matrix instead for comprehensive testing')
    parser.add_argument('--test-matrix', action='store_true',
                       help='[LEGACY] Test block size × concurrency matrix (single target)')
    parser.add_argument('--test-swap-partitions-matrix', action='store_true',
                       help='Test real GPT swap partitions as block devices to recommend SWAP_STRIPE_WIDTH')
    parser.add_argument('--swap-partitions-max', type=int, metavar='N', default=0,
                       help='Max GPT swap partitions to consider (default: 0 = all found)')
    parser.add_argument('--swap-partitions-matrix-block-sizes', metavar='LIST', default='4,8,16,32,64,128',
                       help='Block sizes (KB) to test in swap-partitions matrix (comma-separated, default: 4,8,16,32,64,128)')
    parser.add_argument('--swap-partitions-matrix-iodepths', metavar='LIST', default='1,2',
                       help='IO depths to test for swap-partitions matrix (comma-separated, default: 1,2)')
    parser.add_argument('--swap-partitions-matrix-rwmixread', type=int, metavar='PCT', default=80,
                       help='Read mix percentage for swap-partitions matrix randrw (default: 80)')
    parser.add_argument('--swap-partitions-matrix-latency-metric', choices=['mean', 'p99'], default='p99',
                       help='Latency metric used for scoring/selection in swap-partitions matrix (default: p99)')
    parser.add_argument('--swap-partitions-matrix-size-mb', type=int, metavar='MB', default=256,
                       help='Per-device size for swap-partitions matrix when not using full-device mode (default: 256)')
    parser.add_argument('--swap-partitions-matrix-full-device',
                       dest='swap_partitions_matrix_full_device',
                       action='store_true',
                       default=True,
                       help='Use full partition size (minus margin) for swap-partitions matrix (default: enabled)')
    parser.add_argument('--swap-partitions-matrix-no-full-device',
                       dest='swap_partitions_matrix_full_device',
                       action='store_false',
                       help='Disable full-device mode and use --swap-partitions-matrix-size-mb instead')
    parser.add_argument('--swap-partitions-matrix-full-device-margin-mb', type=int, metavar='MB', default=16,
                       help='Margin to subtract when using full-device mode (default: 16)')
    parser.add_argument('--test-zswap', action='store_true',
                       help='Run comprehensive ZSWAP benchmarks (requires swap backing device)')
    parser.add_argument('--zswap-device', metavar='DEVICE', default='/dev/vda4',
                       help='Swap device for ZSWAP backing (default: /dev/vda4)')
    parser.add_argument('--test-zswap-latency', action='store_true',
                       help='Test ZSWAP latency with real disk backing (requires swap partitions from create-swap-partitions.sh)')
    parser.add_argument('--compare-zswap-zram', action='store_true',
                       help='Compare ZSWAP vs ZRAM performance')
    parser.add_argument('--test-latency', action='store_true',
                       help='Run comprehensive latency comparison tests')
    parser.add_argument('--latency-size', type=int, metavar='MB', default=100,
                       help='Size for latency tests in MB (default: 100)')
    parser.add_argument('--duration', type=int, metavar='SEC', default=5,
                       help='Test duration in seconds for each I/O parameter set (default: 5)')
    parser.add_argument('--small-tests', action='store_true',
                       help='Use smaller test sizes for faster benchmarks (64MB compression tests)')
    parser.add_argument('--max-compression-size', type=int, metavar='MB',
                       help='Override maximum compression test size in MB')
    parser.add_argument('--output', '-o', metavar='FILE',
                       help='Output JSON results to file')
    parser.add_argument('--shell-config', metavar='FILE',
                       help='Export shell configuration file')
    parser.add_argument('--telegram', action='store_true',
                       help='Send results to Telegram (requires TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID)')
    # Default WebP to reduce Telegram bandwidth; gracefully falls back to PNG if Pillow is missing.
    parser.add_argument('--webp', dest='webp', action='store_true', default=True,
                       help='Convert charts to WebP format for smaller file size (default: enabled; requires Pillow)')
    parser.add_argument('--no-webp', dest='webp', action='store_false',
                       help='Disable WebP chart conversion (keep PNG)')
    
    args = parser.parse_args()
    
    # Deprecation warnings for individual tests
    if args.block_size:
        log_warn("⚠ WARNING: --block-size is DEPRECATED")
        log_warn("   Individual block size tests are redundant with --test-matrix")
        log_warn("   Use --test-matrix for comprehensive block size × concurrency testing")
        log_warn("   See TESTING_METHODOLOGY.md for details")
    
    if args.test_concurrency:
        log_warn("⚠ WARNING: --test-concurrency is DEPRECATED")
        log_warn("   Individual concurrency tests are redundant with --test-matrix")
        log_warn("   Use --test-matrix for comprehensive block size × concurrency testing")
        log_warn("   See TESTING_METHODOLOGY.md for details")
    
    # Validate arguments
    if args.latency_size <= 0 or args.latency_size > 10240:
        log_error(f"Invalid --latency-size: {args.latency_size}. Must be between 1 and 10240 MB")
        sys.exit(1)
    
    if args.duration < 1 or args.duration > 3600:
        log_error(f"Invalid --duration: {args.duration}. Must be between 1 and 3600 seconds")
        sys.exit(1)

    if args.swap_partitions_matrix_rwmixread < 0 or args.swap_partitions_matrix_rwmixread > 100:
        log_error(
            f"Invalid --swap-partitions-matrix-rwmixread: {args.swap_partitions_matrix_rwmixread}. Must be 0..100"
        )
        sys.exit(1)

    if args.swap_partitions_matrix_size_mb <= 0:
        log_error(f"Invalid --swap-partitions-matrix-size-mb: {args.swap_partitions_matrix_size_mb}. Must be >0")
        sys.exit(1)

    if args.swap_partitions_matrix_full_device_margin_mb < 0:
        log_error(
            f"Invalid --swap-partitions-matrix-full-device-margin-mb: {args.swap_partitions_matrix_full_device_margin_mb}. Must be >=0"
        )
        sys.exit(1)

    def _parse_int_list(value):
        if value is None:
            return []
        if isinstance(value, (list, tuple)):
            out = []
            for x in value:
                try:
                    out.append(int(x))
                except Exception:
                    pass
            return out
        s = str(value).strip()
        if not s:
            return []
        out = []
        for token in s.split(','):
            token = token.strip()
            if not token:
                continue
            try:
                out.append(int(token))
            except Exception:
                continue
        return out

    swap_matrix_iodepths = [d for d in _parse_int_list(args.swap_partitions_matrix_iodepths) if d >= 1]
    swap_matrix_block_sizes = [b for b in _parse_int_list(args.swap_partitions_matrix_block_sizes) if b >= 1]
    if not swap_matrix_block_sizes:
        log_error(
            f"Invalid --swap-partitions-matrix-block-sizes: {args.swap_partitions_matrix_block_sizes}. "
            + "Expected comma-separated ints >=1 (e.g. 4,8,16,32)."
        )
        sys.exit(1)
    if not swap_matrix_iodepths:
        log_error(
            f"Invalid --swap-partitions-matrix-iodepths: {args.swap_partitions_matrix_iodepths}. "
            + "Expected comma-separated ints >=1 (e.g. 1,2)."
        )
        sys.exit(1)
    
    # Check root and dependencies
    check_root()
    check_dependencies()
    
    # Compile C programs for memory management
    if not compile_c_programs():
        log_error("Failed to compile C memory management programs")
        log_error("Ensure gcc is installed: apt install gcc")
        sys.exit(1)
    
    # Record overall start time
    benchmark_start_time = time.time()
    
    # Get system info
    system_info = get_system_info()
    
    # Log startup information with timestamps
    log_info_ts("==> Starting Swap Performance Benchmark")
    log_info_ts(f"System: {system_info['ram_gb']}GB RAM, {system_info['cpu_cores']} CPU cores")
    log_info_ts(f"Available memory: {system_info.get('available_gb', 'N/A')}GB")
    
    # Calculate optimal compression test size based on RAM
    if args.max_compression_size:
        compression_test_size = args.max_compression_size
        log_info_ts(f"Using user-specified compression test size: {compression_test_size}MB")
    else:
        compression_test_size = calculate_optimal_compression_size(
            system_info['ram_gb'], 
            small_tests=args.small_tests
        )
        if args.small_tests:
            log_info_ts(f"Using small test size: {compression_test_size}MB (--small-tests mode)")
        else:
            # Show if we're using a reduced size
            default_size = COMPRESSION_TEST_SIZE_MB
            if compression_test_size < default_size:
                # Calculate actual percentage (compression_test_size in MB, ram_gb needs to be converted to MB)
                ram_mb = system_info['ram_gb'] * 1024
                percent_of_ram = (compression_test_size / ram_mb) * 100
                log_warn_ts(f"Using reduced test size: {compression_test_size}MB ({percent_of_ram:.1f}% of {system_info['ram_gb']}GB RAM)")
            else:
                log_info_ts(f"Using compression test size: {compression_test_size}MB")
    
    # Warn if test size is large relative to available memory
    if system_info.get('available_gb'):
        available_mb = system_info['available_gb'] * 1024
        if compression_test_size > available_mb * 0.5:
            log_warn_ts(f"Compression test size ({compression_test_size}MB) is >50% of available memory ({available_mb:.0f}MB)")
            log_warn_ts("Tests may take longer due to memory pressure")
    
    # Calculate total number of tests
    total_tests = 0
    
    # Note: --test-all focuses on modern tests (swap-partitions stripe matrix, compression/allocator, latency).
    # Legacy single-target matrix is opt-in via --test-matrix.
    if not args.test_all and args.block_size:
        # Only run if explicitly requested with --block-size (deprecated path)
        total_tests += 1  # single block size test
    
    if args.test_all or args.test_compressors:
        compressors = ['lz4', 'zstd', 'lzo-rle']
        total_tests += len(compressors)
    
    if args.test_all or args.test_allocators:
        allocators = ['zsmalloc', 'z3fold', 'zbud']
        data_patterns = _filter_patterns([
            (0, 'mixed'),
            (1, 'random'),
            (2, 'zeros'),
            (3, 'sequential'),
        ])
        total_tests += len(allocators) * len(data_patterns)
    
    if not args.test_all and args.test_concurrency:
        # Only run if explicitly requested with --test-concurrency (deprecated path)
        total_tests += 1  # single concurrency test
    
    if args.test_matrix:
        # Legacy single-target matrix test counts as one test
        total_tests += 1

    if args.test_all or args.test_swap_partitions_matrix:
        total_tests += 1
    
    if args.test_zswap:
        total_tests += 2  # lz4 and zstd
    
    if args.compare_zswap_zram:
        total_tests += 4  # ZRAM lz4, ZRAM zstd, ZSWAP lz4, ZSWAP zstd
    
    if args.compare_memory_only:
        total_tests += 2  # lz4 and zstd
    
    log_info_ts(f"Total tests to run: {total_tests}")
    
    results = {
        'system_info': system_info,
        'timestamp': datetime.now().isoformat(),
        'compression_test_size_mb': compression_test_size,
        'default_data_pattern': 'mixed',
        'default_data_pattern_description': 'mixed (default; some compressible, some not)'
    }
    
    # Track current test number
    current_test = 0
    
    # Run benchmarks
    # Note: Individual block size and concurrency tests are deprecated
    # --test-all now uses matrix test which provides comprehensive coverage
    if not args.test_all and args.block_size:
        # Only run individual block size test if explicitly requested (deprecated)
        log_warn("Running deprecated individual block size test - use --test-matrix instead for comprehensive testing")
        results['block_sizes'] = []
        try:
            current_test += 1
            seq_result = benchmark_block_size_fio(
                args.block_size, 
                runtime_sec=args.duration, 
                pattern='sequential',
                test_num=current_test,
                total_tests=total_tests
            )
            results['block_sizes'].append(seq_result)
        except Exception as e:
            log_error(f"Block size {args.block_size}KB failed: {e}")
    
    if args.test_all or args.test_compressors:
        compressors = ['lz4', 'zstd', 'lzo-rle']
        results['compressors'] = []
        for comp in compressors:
            try:
                current_test += 1
                result = benchmark_compression(
                    comp, 
                    'zsmalloc', 
                    size_mb=compression_test_size,
                    pattern=0,
                    test_num=current_test,
                    total_tests=total_tests
                )
                results['compressors'].append(result)
            except Exception as e:
                log_error(f"Compressor {comp} failed: {e}")
    
    if args.test_all or args.test_allocators:
        allocators = ['zsmalloc', 'z3fold', 'zbud']
        data_patterns = _filter_patterns([
            (0, 'mixed'),
            (1, 'random'),
            (2, 'zeros'),
            (3, 'sequential')
        ])
        results['allocators'] = []
        
        log_info(f"Testing {len(allocators)} allocators with {len(data_patterns)} data patterns each ({len(allocators) * len(data_patterns)} total tests)")
        
        for alloc in allocators:
            for pattern_id, pattern_name in data_patterns:
                try:
                    # Force memory refresh before each test to get accurate compression ratios
                    log_debug(f"Cleaning up ZRAM before testing allocator: {alloc} with {pattern_name} data")
                    cleanup_zram_aggressive()
                    time.sleep(ZRAM_STABILIZATION_DELAY_SEC)  # Let system stabilize
                    
                    # Drop caches to ensure fresh memory
                    try:
                        with open('/proc/sys/vm/drop_caches', 'w') as f:
                            f.write('3')
                        log_debug("Dropped caches for fresh memory state")
                    except Exception as e:
                        log_debug(f"Could not drop caches: {e}")
                    
                    current_test += 1
                    result = benchmark_compression(
                        'lz4', 
                        alloc, 
                        size_mb=compression_test_size,
                        pattern=pattern_id,
                        test_num=current_test,
                        total_tests=total_tests
                    )
                    
                    # Add pattern info to result
                    result['data_pattern'] = pattern_name
                    result['pattern_id'] = pattern_id
                    results['allocators'].append(result)
                    
                    log_info(f"✓ {alloc} with {pattern_name} data: {result.get('compression_ratio', 'N/A')}x compression")
                    
                except Exception as e:
                    log_error(f"Allocator {alloc} with {pattern_name} failed: {e}")
    
    if not args.test_all and args.test_concurrency:
        # Only run individual concurrency test if explicitly requested (deprecated)
        log_warn("Running deprecated individual concurrency test - use --test-matrix instead for comprehensive testing")
        results['concurrency'] = []
        try:
            current_test += 1
            result = test_concurrency(
                args.test_concurrency,
                test_num=current_test,
                total_tests=total_tests
            )
            results['concurrency'].append(result)
        except Exception as e:
            log_error(f"Concurrency test with {args.test_concurrency} files failed unexpectedly: {e}")
            results['concurrency'].append({
                'num_files': args.test_concurrency,
                'error': str(e),
                'write_mb_per_sec': 0,
                'read_mb_per_sec': 0
            })
    
    # Legacy single-target matrix testing (block size × concurrency)
    if args.test_matrix:
        try:
            log_info_ts("\n=== Running Block Size × Concurrency Matrix Test ===")
            results['matrix'] = test_blocksize_concurrency_matrix(
                runtime_sec=args.duration
            )
        except Exception as e:
            log_error(f"Matrix test failed: {e}")
            results['matrix'] = {'error': str(e)}

    # Swap partition stripe matrix (real multi-device test)
    if args.test_all or args.test_swap_partitions_matrix:
        try:
            log_info_ts("\n=== Running Swap Partition Stripe Matrix ===")
            max_parts = args.swap_partitions_max if args.swap_partitions_max and args.swap_partitions_max > 0 else None
            results['swap_partitions_matrix'] = test_swap_partitions_stripe_matrix(
                runtime_sec=args.duration,
                max_partitions=max_parts,
                block_sizes=swap_matrix_block_sizes,
                iodepth_levels=swap_matrix_iodepths,
                rwmixread=args.swap_partitions_matrix_rwmixread,
                per_device_size_mb=args.swap_partitions_matrix_size_mb,
                use_full_device_size=bool(args.swap_partitions_matrix_full_device),
                full_device_size_margin_mb=args.swap_partitions_matrix_full_device_margin_mb,
                latency_metric=args.swap_partitions_matrix_latency_metric,
            )
        except Exception as e:
            log_error(f"Swap partition stripe matrix failed: {e}")
            results['swap_partitions_matrix'] = {'error': str(e)}
    
    # ZSWAP benchmarks
    if args.test_zswap:
        try:
            log_info_ts("\n=== Running ZSWAP Benchmarks ===")
            results['zswap'] = {}
            
            # Test with lz4
            log_info("Testing ZSWAP with lz4...")
            results['zswap']['lz4'] = benchmark_zswap_comprehensive(
                swap_device=args.zswap_device,
                compressor='lz4',
                zpool='z3fold',
                test_size_mb=compression_test_size
            )
            
            # Test with zstd
            log_info("Testing ZSWAP with zstd...")
            results['zswap']['zstd'] = benchmark_zswap_comprehensive(
                swap_device=args.zswap_device,
                compressor='zstd',
                zpool='z3fold',
                test_size_mb=compression_test_size
            )
        except Exception as e:
            log_error(f"ZSWAP benchmark failed: {e}")
            results['zswap'] = {'error': str(e)}
    
    # ZSWAP vs ZRAM comparison
    if args.compare_zswap_zram or args.test_all:
        try:
            log_info_ts("\n=== Comparing ZSWAP vs ZRAM ===")
            results['zswap_vs_zram'] = compare_zswap_vs_zram(
                swap_device=args.zswap_device,
                test_size_mb=compression_test_size
            )
        except Exception as e:
            log_error(f"ZSWAP vs ZRAM comparison failed: {e}")
            results['zswap_vs_zram'] = {'error': str(e)}
    
    if args.compare_memory_only:
        results['memory_only_comparison'] = compare_memory_only()
    
    # ZSWAP latency tests with real disk backing
    if args.test_zswap_latency:
        try:
            log_info_ts("\n=== Running ZSWAP Latency Tests with Real Disk Backing ===")
            results['zswap_latency'] = benchmark_zswap_latency(
                swap_devices=None,  # Auto-detect
                compressor='lz4',   # Fast compressor for latency testing
                zpool='zbud',       # Reliable allocator
                test_size_mb=compression_test_size
            )
        except Exception as e:
            log_error(f"ZSWAP latency test failed: {e}")
            results['zswap_latency'] = {'error': str(e)}
    
    # Latency tests
    if args.test_all or args.test_latency:
        latency_size = args.latency_size
        log_info(f"\n=== Running Latency Tests ({latency_size}MB) ===")
        results['latency_comparison'] = benchmark_latency_comparison(latency_size)
    
    # Final cleanup of temporary test files
    log_info("Cleaning up temporary test files...")
    cleanup_test_files()
    
    # Additional cleanup patterns for test run directories not covered by cleanup_test_files
    # These are broader patterns that may catch test-specific subdirectories
    additional_patterns = [
        '/tmp/fio_test*',      # FIO test directories (not just .job files)
        '/tmp/swap_test*',     # Swap test directories
        '/tmp/ptable-*',       # Partition table dumps
    ]
    for pattern in additional_patterns:
        try:
            for f in glob.glob(pattern):
                if os.path.isfile(f):
                    os.remove(f)
                    log_debug(f"Removed file: {f}")
                elif os.path.isdir(f):
                    import shutil
                    shutil.rmtree(f)
                    log_debug(f"Removed directory: {f}")
        except Exception as e:
            log_debug(f"Cleanup warning for {pattern}: {e}")
    
    # Calculate and log total elapsed time
    total_elapsed = time.time() - benchmark_start_time
    results['total_elapsed_sec'] = round(total_elapsed, 1)
    
    # Format elapsed time nicely (minutes and seconds)
    elapsed_minutes = int(total_elapsed // 60)
    elapsed_seconds = int(total_elapsed % 60)
    if elapsed_minutes > 0:
        elapsed_str = f"{elapsed_minutes}m {elapsed_seconds}s"
    else:
        elapsed_str = f"{elapsed_seconds}s"
    
    log_info_ts(f"==> Benchmark complete! Total time: {elapsed_str}")
    
    # Always persist results locally for debugging
    local_results_file = f"/var/log/debian-install/benchmark-results-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
    try:
        os.makedirs(os.path.dirname(local_results_file), exist_ok=True)
        with open(local_results_file, 'w') as f:
            json.dump(results, f, indent=2)
        log_info_ts(f"Results persisted to {local_results_file}")
        
        # Send results JSON as telegram attachment for debugging
        if args.telegram and TELEGRAM_AVAILABLE:
            try:
                telegram = TelegramClient()
                log_info("Sending benchmark JSON to Telegram...")
                if telegram.send_document(local_results_file, caption="📊 Benchmark Results (JSON)"):
                    log_info("✓ Benchmark JSON sent to Telegram")
                else:
                    log_warn("Failed to send benchmark JSON to Telegram")
            except Exception as e:
                log_warn(f"Failed to send benchmark JSON to Telegram: {e}")
    except Exception as e:
        log_warn(f"Failed to persist results locally: {e}")
    
    # Output results
    if args.output:
        with open(args.output, 'w') as f:
            json.dump(results, f, indent=2)
        log_info(f"Results saved to {args.output}")
    else:
        print(json.dumps(results, indent=2))
    
    # Export shell config
    if args.shell_config:
        export_shell_config(results, args.shell_config)
    
    # Generate human-readable reports
    timestamp_str = datetime.now().strftime('%Y%m%d-%H%M%S')
    report_dir = '/var/log/debian-install'
    os.makedirs(report_dir, exist_ok=True)
    
    summary_report = f"{report_dir}/benchmark-summary-{timestamp_str}.txt"
    config_report = f"{report_dir}/swap-config-decisions-{timestamp_str}.txt"
    
    try:
        generate_benchmark_summary_report(results, summary_report)
    except Exception as e:
        log_warn(f"Failed to generate benchmark summary report: {e}")
    
    try:
        generate_swap_config_report(results, config_report)
    except Exception as e:
        log_warn(f"Failed to generate swap config report: {e}")
    
    # Send to Telegram if requested
    if args.telegram:
        if not TELEGRAM_AVAILABLE:
            log_error("Cannot send to Telegram: telegram_client module not available")
            log_error("Ensure telegram_client.py is in the same directory as benchmark.py")
        else:
            try:
                telegram = TelegramClient()
                
                # Generate charts (with WebP conversion if requested)
                log_info("Generating performance charts...")
                chart_files_generated = generate_charts(results, webp=args.webp)
                
                # Filter out non-existent charts and log which ones are missing
                chart_files = []
                for chart_file in chart_files_generated:
                    if os.path.exists(chart_file):
                        chart_files.append(chart_file)
                    else:
                        log_debug(f"Chart file not found: {chart_file}")
                
                if not chart_files:
                    log_warn("No charts were generated")
                
                # Generate matrix heatmaps if matrix tests were run
                if 'matrix' in results and isinstance(results['matrix'], dict) and 'matrix' in results['matrix']:
                    try:
                        matrix_charts = generate_matrix_heatmaps(results['matrix'], '/var/log/debian-install')
                        if matrix_charts:
                            for chart_file in matrix_charts:
                                if os.path.exists(chart_file):
                                    chart_files.append(chart_file)
                                else:
                                    log_debug(f"Matrix chart file not found: {chart_file}")
                    except Exception as e:
                        log_warn(f"Failed to generate matrix heatmaps: {e}")

                # Include ZSWAP stats time-series chart (generated during --test-zswap-latency)
                try:
                    zswap_stats_chart = (
                        results.get('zswap_latency', {})
                        .get('zswap', {})
                        .get('stats_chart')
                    )
                    if zswap_stats_chart and os.path.exists(zswap_stats_chart):
                        chart_files.append(zswap_stats_chart)
                except Exception as e:
                    log_debug(f"Could not include ZSWAP stats chart: {e}")

                # Deduplicate charts while preserving order
                try:
                    seen = set()
                    chart_files = [p for p in chart_files if not (p in seen or seen.add(p))]
                except Exception:
                    pass

                # Convert any remaining PNG charts to WebP if requested (matrix + ZSWAP stats)
                if args.webp and chart_files:
                    try:
                        from PIL import Image
                        converted_files = []
                        for png_file in chart_files:
                            if not png_file.endswith('.png'):
                                converted_files.append(png_file)
                                continue
                            webp_file = png_file.replace('.png', '.webp')
                            try:
                                img = Image.open(png_file)
                                img.save(webp_file, 'WEBP', quality=85, method=6)
                                if os.path.exists(webp_file) and os.path.getsize(webp_file) > 0:
                                    os.remove(png_file)
                                    converted_files.append(webp_file)
                                else:
                                    converted_files.append(png_file)
                            except Exception as e:
                                log_warn(f"Failed to convert {png_file} to WebP: {e}")
                                converted_files.append(png_file)
                        chart_files = converted_files
                    except ImportError:
                        # Pillow is optional; keep PNGs if not available
                        pass
                    except Exception as e:
                        log_warn(f"WebP conversion for Telegram charts failed: {e}")
                
                # Send HTML summary
                html_message = format_benchmark_html(results)
                log_info("Sending benchmark results to Telegram...")
                if telegram.send_message(html_message):
                    log_info("✓ Benchmark results sent to Telegram successfully!")
                else:
                    log_error("✗ Failed to send benchmark results to Telegram")
                    log_error(f"Results are available in {local_results_file}")
                
                # Send charts as media group (single message with all charts)
                if chart_files:
                    # Telegram media groups are limited (10 items).
                    max_group = 10
                    groups = [chart_files[i:i + max_group] for i in range(0, len(chart_files), max_group)]

                    log_info_ts(f"Sending {len(chart_files)} performance charts in {len(groups)} media group(s)...")

                    all_groups_sent = True
                    for gi, group in enumerate(groups, start=1):
                        caption = f"📊 Benchmark Charts ({len(chart_files)} charts) — group {gi}/{len(groups)}"
                        t0 = time.time()
                        ok = telegram.send_media_group(group, caption=caption)
                        dt = time.time() - t0
                        if ok:
                            log_success_ts(f"Sent media group {gi}/{len(groups)} ({len(group)} charts) in {dt:.1f}s")
                        else:
                            all_groups_sent = False
                            log_warn_ts(f"Media group {gi}/{len(groups)} failed after {dt:.1f}s")
                            break

                    if not all_groups_sent:
                        log_warn_ts("Falling back to individual chart sends (this can be slow)")
                        # Use the timestamp_str variable defined at line 3520 for consistency
                        for chart_file in chart_files:
                            chart_name = os.path.basename(chart_file)
                            for ext in ['.png', '.webp']:
                                chart_name = chart_name.replace(ext, '')
                            chart_name = chart_name.replace('benchmark-', '').replace('-' + timestamp_str, '')
                            caption = f"📊 {chart_name.title()} Chart"
                            t0 = time.time()
                            ok = telegram.send_document(chart_file, caption=caption)
                            dt = time.time() - t0
                            if ok:
                                log_success_ts(f"Sent {chart_name} chart in {dt:.1f}s")
                            else:
                                log_warn_ts(f"Failed to send {chart_name} chart (took {dt:.1f}s)")
            except ValueError as e:
                log_error(f"Telegram configuration error: {e}")
            except Exception as e:
                log_error(f"Failed to send to Telegram: {e}")

if __name__ == '__main__':
    main()
