"""Shared fixtures.

Two rules the whole suite follows:

* **Never touch the real `/sys/fs/cgroup`.** Every reader in this package takes
  a root path so a fake tree can be pointed at it. A test that reads the live
  host is a test that passes on this machine and nowhere else.
* **Never sleep.** The sampler takes an injected clock and sleep function; a
  suite that waits out real intervals is a suite nobody runs.

The fake tree mirrors the real host's shape (uncapped `dev.slice` parent,
capped tier children, a protected production leaf under `wings.slice`) because
that shape is the thing the limit resolver has to get right.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lib.model import Analysis, Event, Phase, Proposal, Series  # noqa: E402


# ── fake cgroup tree ────────────────────────────────────────────────────────

def write_cgroup(base: Path, rel: str, files: Dict[str, str]) -> Path:
    """Create one cgroup directory under ``base`` with the given files."""
    path = base / rel.strip("/")
    path.mkdir(parents=True, exist_ok=True)
    for name, content in files.items():
        if not content.endswith("\n"):
            content += "\n"
        (path / name).write_text(content)
    return path


MEMSTAT_TEMPLATE = """anon {anon}
file {file}
kernel_stack 4194304
slab 33554432
sock 65536
shmem 0
file_mapped 8388608
file_dirty 0
file_writeback 0
swapcached {swapcached}
anon_thp 0
inactive_anon {inactive_anon}
active_anon {active_anon}
inactive_file 12582912
active_file 8388608
unevictable 0
pgfault {pgfault}
pgmajfault {pgmajfault}
pgscan {pgscan}
pgsteal {pgsteal}
pgactivate 1000
pgdeactivate 900
workingset_refault_anon {wra}
workingset_refault_file {wrf}
workingset_activate_anon 10
workingset_activate_file 20
pswpin {pswpin}
pswpout {pswpout}
zswpin {zswpin}
zswpout {zswpout}
zswpwb {zswpwb}
"""


def memstat(
    anon: int = 4 * 1024**3,
    file: int = 92 * 1024**2,
    swapcached: int = 0,
    pgfault: int = 1_000_000,
    pgmajfault: int = 1000,
    pgscan: int = 500_000,
    pgsteal: int = 250_000,
    wra: int = 100_000,
    wrf: int = 200_000,
    pswpin: int = 5_000,
    pswpout: int = 9_000,
    zswpin: int = 40_000,
    zswpout: int = 80_000,
    zswpwb: int = 300,
) -> str:
    return MEMSTAT_TEMPLATE.format(
        anon=anon,
        file=file,
        swapcached=swapcached,
        inactive_anon=anon // 2,
        active_anon=anon // 2,
        pgfault=pgfault,
        pgmajfault=pgmajfault,
        pgscan=pgscan,
        pgsteal=pgsteal,
        wra=wra,
        wrf=wrf,
        pswpin=pswpin,
        pswpout=pswpout,
        zswpin=zswpin,
        zswpout=zswpout,
        zswpwb=zswpwb,
    )


def psi(some10: float = 0.0, full10: float = 0.0, total: int = 0) -> str:
    return (
        f"some avg10={some10:.2f} avg60={some10:.2f} avg300={some10:.2f} total={total}\n"
        f"full avg10={full10:.2f} avg60={full10:.2f} avg300={full10:.2f} total={total // 2}"
    )


def cgroup_files(
    *,
    memory_current: int = 1024**3,
    memory_min: str = "0",
    memory_low: str = "0",
    memory_high: str = "max",
    memory_max: str = "max",
    swap_current: int = 0,
    swap_max: str = "max",
    cpu_max: str = "max 100000",
    cpu_weight: int = 100,
    io_max: str = "",
    usage_usec: int = 1_000_000,
    events_high: int = 0,
    events_max: int = 0,
    oom_kill: int = 0,
    **memstat_kwargs: int,
) -> Dict[str, str]:
    return {
        "cgroup.controllers": "cpuset cpu io memory pids",
        "cgroup.procs": "",
        "cgroup.stat": "nr_descendants 0\nnr_dying_descendants 0",
        "memory.current": str(memory_current),
        "memory.peak": str(memory_current),
        "memory.min": memory_min,
        "memory.low": memory_low,
        "memory.high": memory_high,
        "memory.max": memory_max,
        "memory.swap.current": str(swap_current),
        "memory.swap.max": swap_max,
        "memory.swap.peak": str(swap_current),
        "memory.zswap.current": "0",
        "memory.zswap.max": "max",
        "memory.zswap.writeback": "1",
        "memory.stat": memstat(**memstat_kwargs),
        "memory.events": (
            f"low 0\nhigh {events_high}\nmax {events_max}\n"
            f"oom 0\noom_kill {oom_kill}\noom_group_kill 0"
        ),
        "memory.events.local": (
            f"low 0\nhigh {events_high}\nmax {events_max}\n"
            f"oom 0\noom_kill {oom_kill}\noom_group_kill 0"
        ),
        "memory.pressure": psi(),
        "cpu.pressure": psi(),
        "io.pressure": psi(),
        "cpu.stat": (
            f"usage_usec {usage_usec}\nuser_usec {usage_usec // 2}\n"
            f"system_usec {usage_usec // 2}\nnr_periods 0\n"
            "nr_throttled 0\nthrottled_usec 0"
        ),
        "cpu.max": cpu_max,
        "cpu.weight": str(cpu_weight),
        "io.stat": "254:0 rbytes=1048576 wbytes=2097152 rios=100 wios=200 dbytes=0 dios=0",
        "io.max": io_max,
        "io.weight": "default 100",
        "pids.current": "12",
        "pids.max": "max",
        "pids.peak": "20",
    }


@pytest.fixture
def cgroup_root(tmp_path: Path) -> Path:
    """A fake cgroup v2 tree shaped like the real host.

    ``dev.slice`` caps IO but not memory, its children cap memory, and the
    production leaf under ``wings.slice`` declares protection its own container
    scope does not re-declare — which is exactly the case the effective-limit
    resolver must report differently with and without ``memory_recursiveprot``.
    """
    root = tmp_path / "cgroup"
    write_cgroup(root, "", cgroup_files(memory_current=11 * 1024**3))
    write_cgroup(
        root,
        "dev.slice",
        cgroup_files(
            memory_current=7 * 1024**3,
            io_max="254:0 rbps=1289421332 wbps=309765974 riops=54103 wiops=35217",
        ),
    )
    write_cgroup(
        root,
        "dev.slice/dev-interactive.slice",
        cgroup_files(
            memory_current=3 * 1024**3,
            memory_low=str(2 * 1024**3),
            memory_high=str(5 * 1024**3),
            memory_max=str(8 * 1024**3),
            cpu_weight=200,
        ),
    )
    write_cgroup(
        root,
        "dev.slice/dev-background.slice",
        cgroup_files(
            memory_current=4 * 1024**3,
            memory_high=str(6 * 1024**3),
            memory_max=str(8 * 1024**3),
            swap_max=str(48 * 1024**3),
            cpu_weight=20,
        ),
    )
    write_cgroup(
        root,
        "dev.slice/dev-background.slice/docker-" + "a" * 64 + ".scope",
        cgroup_files(memory_current=2 * 1024**3),
    )
    write_cgroup(root, "wings.slice", cgroup_files(
        memory_current=5 * 1024**3,
        memory_min=str(8 * 1024**3),
        memory_low=str(12 * 1024**3),
        memory_high=str(14 * 1024**3),
    ))
    write_cgroup(
        root,
        "wings.slice/wings-prod.slice",
        cgroup_files(
            memory_current=5787 * 1024**2,
            memory_min=str(5500 * 1024**2),
            memory_low=str(6 * 1024**3),
            memory_high=str(6 * 1024**3),
            memory_max=str(20 * 1024**3),
            cpu_weight=2000,
        ),
    )
    # The leaf re-declares memory.min but leaves memory.low at 0 — without
    # memory_recursiveprot the parent's 6G low protection is simply discarded.
    write_cgroup(
        root,
        "wings.slice/wings-prod.slice/docker-" + "b" * 64 + ".scope",
        cgroup_files(
            memory_current=5758 * 1024**2,
            memory_min=str(6 * 1024**3),
            memory_low="0",
            memory_max=str(20 * 1024**3),
        ),
    )
    write_cgroup(root, "system.slice", cgroup_files())
    write_cgroup(root, "user.slice", cgroup_files())
    write_cgroup(root, "init.scope", cgroup_files())
    return root


@pytest.fixture
def proc_root(tmp_path: Path) -> Path:
    """A fake /proc with meminfo, vmstat, pressure and one process."""
    root = tmp_path / "proc"
    root.mkdir()
    (root / "meminfo").write_text(
        "MemTotal:       16374620 kB\n"
        "MemFree:          630000 kB\n"
        "MemAvailable:    3080000 kB\n"
        "Buffers:           40000 kB\n"
        "Cached:          3900000 kB\n"
        "SwapTotal:      72349180 kB\n"
        "SwapFree:       53980000 kB\n"
        "Dirty:              1200 kB\n"
        "Writeback:             0 kB\n"
        "AnonPages:       7000000 kB\n"
        "Shmem:            235000 kB\n"
        "Zswap:            447880 kB\n"
        "Zswapped:        1754812 kB\n"
    )
    (root / "vmstat").write_text(
        "pgpgin 1000\npgpgout 2000\npswpin 30000\npswpout 90000\n"
        "zswpin 400000\nzswpout 800000\nzswpwb 1200\n"
        "pgscan_kswapd 500000\npgsteal_kswapd 250000\n"
        "workingset_refault_anon 1000\nworkingset_refault_file 2000\n"
    )
    (root / "loadavg").write_text("1.50 1.20 0.90 3/1200 999999\n")
    (root / "stat").write_text(
        "cpu  1000 20 3000 90000 500 0 100 0 0 0\n"
        "cpu0 500 10 1500 45000 250 0 50 0 0 0\n"
        "cpu1 500 10 1500 45000 250 0 50 0 0 0\n"
    )
    (root / "mounts").write_text(
        "proc /proc proc rw,nosuid,nodev,noexec,relatime 0 0\n"
        "cgroup2 /sys/fs/cgroup cgroup2 rw,nosuid,nodev,noexec,relatime 0 0\n"
    )
    pressure = root / "pressure"
    pressure.mkdir()
    for name in ("cpu", "memory", "io"):
        (pressure / name).write_text(psi(some10=1.5, full10=0.5, total=12345) + "\n")
    pid = root / "4242"
    pid.mkdir()
    (pid / "comm").write_text("fakeproc\n")
    (pid / "status").write_text(
        "Name:\tfakeproc\nVmRSS:\t 1048576 kB\nRssAnon:\t  900000 kB\n"
        "RssFile:\t  148576 kB\nRssShmem:\t       0 kB\nVmSwap:\t  200000 kB\n"
    )
    (pid / "io").write_text(
        "rchar: 100\nwchar: 200\nsyscr: 3\nsyscw: 4\n"
        "read_bytes: 1048576\nwrite_bytes: 2097152\ncancelled_write_bytes: 0\n"
    )
    (pid / "stat").write_text(
        "4242 (fakeproc) S 1 4242 4242 0 -1 4194304 1000 0 0 0 350 120 0 0 20 0 5 0 100 0 0 "
        + "0 " * 30
        + "\n"
    )
    (pid / "cgroup").write_text("0::/dev.slice/dev-background.slice\n")
    return root


@pytest.fixture
def mounts_recursiveprot(tmp_path: Path) -> Path:
    """A /proc whose cgroup2 mount *does* carry memory_recursiveprot."""
    root = tmp_path / "proc-rp"
    root.mkdir()
    (root / "mounts").write_text(
        "cgroup2 /sys/fs/cgroup cgroup2 "
        "rw,nosuid,nodev,noexec,relatime,nsdelegate,memory_recursiveprot 0 0\n"
    )
    return root


# ── synthetic run data ──────────────────────────────────────────────────────

def make_sample(
    seq: int,
    mono: float,
    *,
    t0: float = 1_754_325_600.0,
    cgroups: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """One sample record in the on-disk shape described by DESIGN.md §3.1."""
    return {
        "seq": seq,
        "t": t0 + mono,
        "mono": mono,
        "cg": cgroups or {},
        "host": {
            "meminfo": {"MemTotal": 16374620 * 1024, "MemAvailable": 3_000_000_000},
            "vmstat": {"pswpin": 30000 + seq * 10, "pswpout": 90000 + seq * 40},
            "loadavg": [1.5, 1.2, 0.9],
        },
    }


def cg_metrics(
    *,
    current: int,
    usage_usec: int,
    rbytes: int = 0,
    wbytes: int = 0,
    zswpin: int = 0,
    wra: int = 0,
    wrf: int = 0,
    some10: float = 0.0,
    events_high: int = 0,
) -> Dict[str, Any]:
    return {
        "mem": {"current": current, "swap_current": 0, "zswap_current": 0},
        "memstat": {
            "anon": int(current * 0.8),
            "file": int(current * 0.2),
            "zswpin": zswpin,
            "zswpout": 0,
            "pgfault": 0,
            "pgmajfault": 0,
            "pgscan": 0,
            "pgsteal": 0,
            "pswpin": 0,
            "pswpout": 0,
            "workingset_refault_anon": wra,
            "workingset_refault_file": wrf,
        },
        "memev": {"low": 0, "high": events_high, "max": 0, "oom": 0, "oom_kill": 0},
        "psi_mem": {"some_avg10": some10, "some_total": 0, "full_avg10": 0.0, "full_total": 0},
        "psi_cpu": {"some_avg10": 0.0, "some_total": 0},
        "psi_io": {"some_avg10": 0.0, "some_total": 0},
        "cpu": {"usage_usec": usage_usec, "user_usec": usage_usec // 2,
                "system_usec": usage_usec // 2, "nr_throttled": 0, "throttled_usec": 0},
        "io": {"254:0": {"rbytes": rbytes, "wbytes": wbytes, "rios": 0, "wios": 0}},
        "pids": {"current": 5},
        "cgstat": {"nr_descendants": 0, "nr_dying_descendants": 0},
    }


SUBJECT = "/dev.slice/dev-background.slice"
OBSERVER = "/wings.slice/wings-prod.slice"


@pytest.fixture
def sample_records() -> List[Dict[str, Any]]:
    """40 samples: quiet, then a memory+IO ramp, then quiet again.

    The step at t=10s is what changepoint detection is expected to find, and
    the observer's refault counter climbs only while the subject ramps — the
    correlation the tool exists to surface.
    """
    records: List[Dict[str, Any]] = []
    for seq in range(40):
        mono = seq * 1.0
        busy = 10 <= mono < 25
        current = (1 * 1024**3) + (int((mono - 10) * 200 * 1024**2) if busy else 0)
        usage = seq * (400_000 if busy else 20_000)
        rbytes = seq * (50 * 1024**2 if busy else 1024**2)
        wra = 1000 + (seq * 500 if busy else seq * 5)
        records.append(
            make_sample(
                seq,
                mono,
                cgroups={
                    SUBJECT: cg_metrics(
                        current=current,
                        usage_usec=usage,
                        rbytes=rbytes,
                        wbytes=rbytes // 2,
                        some10=8.0 if busy else 0.2,
                        events_high=3 if busy else 0,
                    ),
                    OBSERVER: cg_metrics(
                        current=5787 * 1024**2,
                        usage_usec=seq * 30_000,
                        wra=wra,
                        wrf=2000 + seq * 10,
                        zswpin=400 + (seq * 120 if busy else seq * 2),
                        some10=4.0 if busy else 0.1,
                    ),
                },
            )
        )
    return records


@pytest.fixture
def run_dir_with_data(tmp_path: Path, sample_records) -> Path:
    """A complete run directory on disk, ready for analyze/report tests."""
    run = tmp_path / "runs" / "run-20260804-120000-test"
    run.mkdir(parents=True)
    with (run / "samples.jsonl").open("w") as fh:
        for record in sample_records:
            fh.write(json.dumps(record) + "\n")
    t0 = sample_records[0]["t"]
    # NO "mono" field, deliberately — this mirrors what `phases.emit_mark`
    # actually writes. A mark may be emitted from a foreign container that has
    # no idea when this run's monotonic clock started, so it records wall-clock
    # `t` only, and the reader is responsible for resolving it against the
    # manifest's `started`. An earlier version of this fixture supplied `mono`
    # and thereby hid a crash that hit every real wrapper-mode run.
    with (run / "marks.jsonl").open("w") as fh:
        for mono, name in ((0.0, "startup"), (10.0, "job-a"), (25.0, "idle")):
            fh.write(json.dumps({"t": t0 + mono, "name": name,
                                 "kind": "phase", "source": "mark"}) + "\n")
        # Two marks at the identical instant: `cgprofile run` emits
        # "<cmd>:done" and "settle" back to back, and time.time() really does
        # return the same value for both on a fast host. Observed live.
        fh.write(json.dumps({"t": t0 + 25.0, "name": "settle",
                             "kind": "phase", "source": "wrapper"}) + "\n")
    with (run / "events.jsonl").open("w") as fh:
        fh.write(json.dumps({
            "t": t0 + 12.0, "mono": 12.0, "kind": "memory_high_breach",
            "severity": "warn", "target": SUBJECT,
            "message": "memory.high breached 3 times", "data": {"delta": 3},
        }) + "\n")
    (run / "manifest.json").write_text(json.dumps({
        "run_id": "run-20260804-120000-test",
        "started": t0,
        "ended": t0 + 39.0,
        "argv": ["cgprofile", "attach"],
        "targets": [
            {"key": "gate", "cgroup": SUBJECT, "label": "dev-background.slice",
             "kind": "slice", "role": "subject"},
            {"key": "soulmask", "cgroup": OBSERVER, "label": "soulmask",
             "kind": "slice", "role": "observer"},
        ],
        "host": {"MemTotal": 16374620 * 1024, "mount_flags": ["rw", "nosuid", "relatime"]},
        "limits": {},
    }))
    return run


@pytest.fixture
def synthetic_analysis(sample_records) -> Analysis:
    """A small but complete Analysis, for report tests that skip the pipeline."""
    t = [float(s["mono"]) for s in sample_records]

    def series(key, target, label, unit, group, values, role="subject") -> Series:
        return Series(key=key, target=target, label=label, unit=unit,
                      group=group, t=list(t), v=list(values), role=role)

    mem = [s["cg"][SUBJECT]["mem"]["current"] for s in sample_records]
    obs_mem = [s["cg"][OBSERVER]["mem"]["current"] for s in sample_records]
    psi_vals = [s["cg"][SUBJECT]["psi_mem"]["some_avg10"] for s in sample_records]
    return Analysis(
        manifest={"run_id": "run-20260804-120000-test", "started": sample_records[0]["t"],
                  "duration": 39.0},
        targets=[
            {"key": "gate", "cgroup": SUBJECT, "label": "dev-background.slice",
             "role": "subject"},
            {"key": "soulmask", "cgroup": OBSERVER, "label": "soulmask",
             "role": "observer"},
        ],
        phases=[
            Phase(name="startup", t0=0.0, t1=10.0, source="mark"),
            Phase(name="job-a", t0=10.0, t1=25.0, source="mark"),
            Phase(name="idle", t0=25.0, t1=39.0, source="mark"),
        ],
        events=[
            Event(t=sample_records[0]["t"] + 12.0, mono=12.0, kind="memory_high_breach",
                  severity="warn", target=SUBJECT,
                  message="memory.high breached 3 times", data={"delta": 3}),
        ],
        series=[
            series("mem.current", "gate", "dev-background memory.current",
                   "bytes", "memory", mem),
            series("mem.current", "soulmask", "soulmask memory.current",
                   "bytes", "memory", obs_mem, role="observer"),
            series("psi.mem.some", "gate", "dev-background memory PSI",
                   "pct", "pressure", psi_vals),
        ],
        per_phase=[{"phase": "job-a", "target": "gate", "mem_peak": max(mem)}],
        per_target=[{"target": "gate", "mem_peak": max(mem)}],
        correlations=[{"observer": "soulmask", "subject": "gate",
                       "metric": "rfd_per_s", "r": 0.91, "n": 39}],
        proposals=[
            Proposal(id="dev-slice-uncapped", severity="serious",
                     title="dev.slice has no aggregate memory cap",
                     rationale="Child tiers sum to more than host RAM.",
                     evidence=["dev-interactive 8G + dev-background 8G > 16G total"],
                     change=["systemctl set-property dev.slice MemoryMax=10G"],
                     confidence="observed"),
        ],
        host={"MemTotal": 16374620 * 1024},
        limits={},
    )
