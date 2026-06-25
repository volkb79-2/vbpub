"""Shared fixtures and helpers for damon-analysis tests."""
import os
import sys
import pytest

# Make the scripts/damon-analysis directory importable
SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIB_DIR = os.path.join(SCRIPTS_DIR, 'lib')
for d in (LIB_DIR, SCRIPTS_DIR):
    if d not in sys.path:
        sys.path.insert(0, d)

DAMON_SYSFS = '/sys/kernel/mm/damon/admin/kdamonds'

requires_root = pytest.mark.skipif(
    os.geteuid() != 0,
    reason="requires root (run with sudo)"
)

requires_damon = pytest.mark.skipif(
    not os.path.isdir(DAMON_SYSFS),
    reason="DAMON sysfs not available"
)

requires_root_and_damon = pytest.mark.skipif(
    os.geteuid() != 0 or not os.path.isdir(DAMON_SYSFS),
    reason="requires root + DAMON sysfs"
)


# ---------------------------------------------------------------------------
# Shared region fixtures
# ---------------------------------------------------------------------------

def make_region(start=0x1000, size=4096, nr_accesses=10, age=5,
                max_nr=20, aggr_us=2_000_000):
    """Build a raw region dict as returned by SysfsInterface.read_tried_regions()."""
    return {
        'start': start,
        'end': start + size,
        'nr_accesses': nr_accesses,
        'age': age,
    }


@pytest.fixture
def raw_regions():
    """A small set of raw regions covering hot / warm / cold / idle cases."""
    return [
        make_region(0x10000, 4096,   nr_accesses=18, age=1),   # hot  (18/20 = 90%)
        make_region(0x20000, 8192,   nr_accesses=2,  age=3),   # warm (2/20 = 10%)
        make_region(0x30000, 16384,  nr_accesses=0,  age=20),  # cold (0%, age=20*2s=40s)
        make_region(0x40000, 32768,  nr_accesses=0,  age=70),  # idle (0%, age=70*2s=140s)
        make_region(0x50000, 65536,  nr_accesses=12, age=2),   # hot  (12/20 = 60%)
    ]


@pytest.fixture
def classified_regions(raw_regions):
    """Raw regions after classification (sample_us=100000, aggr_us=2000000)."""
    from damon_analysis import Classifier
    c = Classifier(hot_access_rate_pct=50.0, warm_access_rate_pct=5.0,
                   cold_age_sec=30.0, idle_age_sec=120.0)
    return c.classify_regions(raw_regions, sample_us=100_000, aggr_us=2_000_000)
