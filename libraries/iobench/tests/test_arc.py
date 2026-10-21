"""IOB-ARC: architecture rules and the FakeHost/FakeFio estate."""
import ast
import json
import pathlib
import subprocess
import sys

import pytest

import iobench
from iobench import devio, testing
from iobench.host import HostError, RunResult, TargetRef, check_argv, check_sysfs_path
from iobench.testing import (GIB, Bucket, CacheModel, DirScenario, FakeCrash, FakeDevice, FakeHost, Scenario,
                             TailViolation, mixture, percentile_us, standard_host)

from helpers import cached_host, fill, job, run_job, target_ref

SRC = pathlib.Path(iobench.__file__).parent
CORE = sorted(p for p in SRC.glob("*.py") if p.name not in ("cli.py",))


def test_arc_01_core_imports_stdlib_only():
    code = (
        "import sys, importlib\n"
        "sys.path.insert(0, %r)\n"
        "std = set(sys.stdlib_module_names) | {'iobench'}\n"
        "class Guard:\n"
        "    def find_spec(self, name, path=None, target=None):\n"
        "        top = name.split('.')[0]\n"
        "        if top not in std:\n"
        "            raise ImportError('non-stdlib import: ' + name)\n"
        "sys.meta_path.insert(0, Guard())\n"
        "for m in %r:\n"
        "    importlib.import_module('iobench.' + m)\n"
    ) % (str(SRC.parent), [p.stem for p in CORE if p.stem != "__main__"])
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    bad = code.replace("for m in", "import numpy\nfor m in")
    assert subprocess.run([sys.executable, "-c", bad], capture_output=True).returncode != 0


def test_arc_01_cli_extended_only_in_cli():
    for path in SRC.glob("*.py"):
        tree = ast.parse(path.read_text())
        mods = [n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)]
        mods += [a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names]
        uses = any(m.split(".")[0] == "cli_extended" for m in mods)
        assert uses == (path.name == "cli.py"), path.name


EFFECT_CALLS = {"open", "exit"}
EFFECT_MODULES = {"subprocess", "time", "random", "uuid", "secrets", "shutil", "socket", "signal", "fcntl", "tempfile"}
OS_PURE = {"path", "fspath", "sep", "linesep"}


def effect_violations(source: str) -> list[str]:
    out = []
    for n in ast.walk(ast.parse(source)):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in EFFECT_CALLS:
            out.append(f"call {n.func.id}")
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and isinstance(n.func.value, ast.Name):
            if n.func.value.id in EFFECT_MODULES:
                out.append(f"call {n.func.value.id}.{n.func.attr}")
            if n.func.value.id == "sys" and n.func.attr == "exit":
                out.append("call sys.exit")
        if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name) and n.value.id == "os" and n.attr not in OS_PURE:
            out.append(f"os.{n.attr}")
        if isinstance(n, (ast.Import, ast.ImportFrom)):
            names = [a.name for a in n.names] if isinstance(n, ast.Import) else [n.module or ""]
            out += [f"import {m}" for m in names if m.split(".")[0] in EFFECT_MODULES]
    return out


def test_arc_02_no_direct_effects():
    allow = {"host.py", "cli.py", "__main__.py"}
    for path in SRC.glob("*.py"):
        if path.name in allow:
            continue
        assert effect_violations(path.read_text()) == [], path.name
    # controlled wrong: injecting open( into a core module turns the scan red
    assert effect_violations("def f():\n    open('/x')\n") == ["call open"]
    assert effect_violations("import subprocess\nsubprocess.run([])") != []
    assert effect_violations("import os\nos.kill(1, 2)") != []
    assert effect_violations("import os\nos.path.join('a')") == []
    assert effect_violations("import sys\nsys.exit(1)") == ["call sys.exit"]


def read_hist(res):
    return res.hist


def test_arc_03_fakefio_scenarios():
    # plateau + noise-free: flat series, parser agrees with the knob
    host = standard_host(Scenario(read=DirScenario(iops_max=21600)))
    host.devices["/dev/vda7"].written.add(0, 8 * GIB)
    res = run_job(host, job("randread"))
    assert res.stats.iops == pytest.approx(21600 * 0.0 + res.stats.iops)
    assert all(abs(v - res.series[0][1]) < 1 for _, v in res.series)
    assert len(res.series) == 15 and res.series[0][0] == 2.0
    # flat cap: byte cap binds every size (bytes flat), iops cap binds iops
    cap = standard_host(Scenario(read=DirScenario(bps_cap_rand=14e6, bps_cap_seq=14e6)))
    for bs in (4096, 1 << 20):
        r = run_job(cap, job("randread", bs=bs))
        assert r.stats.bw_bps == pytest.approx(14e6, rel=0.01)
    seq = run_job(cap, job("read", bs=1 << 20, qd=16, metric="bw"))
    assert seq.series[0][1] == pytest.approx(14e6, rel=0.001)
    iops_cap = standard_host(Scenario(read=DirScenario(iops_cap=3000)))
    assert run_job(iops_cap, job("randread", bs=1 << 20)).stats.iops == pytest.approx(3000, rel=0.01)
    # burst-then-throttle (token bucket): 2 GB/s for ~2 s then 14 MiB/s
    sc = Scenario(write=DirScenario(bucket=Bucket(2e9, 14.68e6, 2.0), iops_max=1e6, c_byte_ns=0.25))
    bursty = standard_host(sc)
    r = run_job(bursty, job("write", bs=1 << 20, qd=16, metric="bw", rt=30, avg=1000, size=8 * GIB))
    assert r.series[0][1] == pytest.approx(2e9, rel=0.02) and r.series[1][1] == pytest.approx(2e9, rel=0.02)
    assert r.series[3][1] == pytest.approx(14.68e6, rel=0.02)
    # a low demand refills the bucket instead of draining it
    sc.write.bucket.credit = 0.0
    assert sc.write.bucket.capacity == pytest.approx(2.0 * (2e9 - 14.68e6))
    run_job(bursty, job("write", bs=4096, rt=30, name="idle", size=8 * GIB, rate=100))
    assert sc.write.bucket.credit > 0
    # slow disk with byte metering, fresh-block penalty on never-written space
    pen = standard_host(Scenario(write=DirScenario(iops_max=71584, fresh_penalty=19.9, fresh_penalty_seq=2.0)))
    pen.devices["/dev/vda7"].written.add(0, GIB)
    rewrite = run_job(pen, job("randwrite", off=0)).stats.iops
    fresh = run_job(pen, job("randwrite", off=2 * GIB, name="f")).stats.iops
    assert rewrite / fresh == pytest.approx(19.9, rel=0.01)
    seq_fresh = run_job(pen, job("write", off=4 * GIB, bs=1 << 20, qd=16, metric="bw", name="s")).stats.bw_bps
    seq_rewrite = run_job(pen, job("write", off=0, bs=1 << 20, qd=16, metric="bw", name="s2")).stats.bw_bps
    assert seq_rewrite / seq_fresh == pytest.approx(2.0, rel=0.01)
    # moving ceiling: rate multiplier by clock; noise is deterministic and bounded
    moving = standard_host(Scenario(ceiling=lambda t: 1.0 if t < testing.FakeHost().now() + 15 else 0.5, noise_cv=0.05))
    moving.devices["/dev/vda7"].written.add(0, GIB)
    r = run_job(moving, job("randread", rt=30))
    assert r.series[-1][1] < 0.6 * r.series[0][1]
    again = standard_host(Scenario(ceiling=lambda t: 1.0 if t < testing.FakeHost().now() + 15 else 0.5, noise_cv=0.05))
    again.devices["/dev/vda7"].written.add(0, GIB)
    assert run_job(again, job("randread", rt=30)).series == r.series
    # byte cap truncated by io_size: a short series
    cut = standard_host(Scenario(write=DirScenario(bps_cap_seq=10e6)))
    r = run_job(cut, job("write", bs=1 << 20, qd=16, metric="bw", rt=60, io=50_000_000, size=GIB))
    assert r.stats.io_bytes == pytest.approx(50_000_000, rel=0.01) and r.stats.runtime_s == pytest.approx(5.0)
    assert len(r.series) == 3
    # crash at stage N
    crash = standard_host()
    crash.crash_after = 1
    run_job(crash, job("randread", name="a"))
    with pytest.raises(FakeCrash):
        run_job(crash, job("randread", name="b"))
    # qd table and QD1 latency-driven rate
    qd = standard_host(Scenario(read=DirScenario(qd_iops={1: 3000, 8: 23700, 32: 23900})))
    assert run_job(qd, job("randread", qd=8)).stats.iops == pytest.approx(23700 / 23900 * run_job(qd, job("randread", qd=32, name="b")).stats.iops, rel=0.001)
    q1 = run_job(standard_host(), job("randread", qd=1, rt=10))
    assert q1.stats.iops == pytest.approx(1e6 / 300, rel=0.01)
    # rate-limited load: achieved <= offered, latency grows with load
    lat = standard_host(Scenario(read=DirScenario(iops_max=20000)))
    p99 = []
    for load in (0.1, 0.9):
        r = run_job(lat, job("randread", rate=int(20000 * load), name=f"l{load}"))
        assert r.stats.iops <= 20000 * load * 1.001
        p99.append(r.stats.pct_us["99.000000"])
    assert p99[1] > 2 * p99[0]


def test_arc_03_histogram_logs_and_percentiles():
    sc = Scenario(read=DirScenario(iops_max=9800, lat_us=760, lat_hit_us=110, lat_hit2_us=300))
    host = standard_host(sc)
    host.devices["/dev/vda7"].cache = CacheModel(100, 92000, tier2_capacity_gib=200, tier2_iops=20000)
    host.devices["/dev/vda7"].written.add(0, 400 * GIB)
    host.devices["/dev/vda7"].cache.write_seq(0, 300 * GIB)
    res = run_job(host, job("randread", size=150 * GIB, qd=1, rt=10, hist=True))
    assert len(res.hist) == 10 and len(res.hist[0][1]) == devio.HIST_BINS
    tot = [sum(c[i] for _, c in res.hist) for i in range(devio.HIST_BINS)]
    low = sum(tot[i] for i in range(devio.HIST_BINS) if devio.bin_value_us(i) < 200)
    high = sum(tot[i] for i in range(devio.HIST_BINS) if devio.bin_value_us(i) > 600)
    assert low > 0 and high > 0  # a hit mode and a miss mode
    assert res.stats.pct_us["50.000000"] > 0
    w = mixture([(1.0, 100.0), (0.0, 5000.0)], 0.12)
    assert percentile_us(w, 50) == pytest.approx(100, rel=0.1)
    assert percentile_us({5: 1.0}, 100) == devio.bin_value_us(5)


def test_arc_03_cache_content_is_history_dependent():
    # write-allocating fill of [0, 345 GiB): the cache holds the LAST-written 265 GiB
    host = cached_host(capacity=265)
    fill(host, 345)
    rates = {}
    for ws in (16, 64, 128, 256, 345):
        rates[ws] = run_job(host, job("randread", size=ws * GIB, name=f"r{ws}")).stats.iops
    assert rates[64] == pytest.approx(9800, rel=0.01)  # the front is cold: the miss rate
    assert rates[345] == pytest.approx(31240, rel=0.02)
    assert rates[16] < rates[128] < rates[256] < rates[345]  # inverted ladder
    # closed formula C/ws would predict a DECREASING ladder: not reproduced
    formula = {ws: 1 / ((min(1, 265 / ws)) / 92000 + (1 - min(1, 265 / ws)) / 9800) for ws in rates}
    assert formula[16] > formula[345] and rates[16] < rates[345]
    # the same job after a different fill order gives a different hit ratio
    other = cached_host(capacity=265)
    for start in (200, 0):  # fill the back first, then the front
        n = 145 * GIB if start == 200 else 200 * GIB
        j = job("write", off=start * GIB, size=n, bs=1 << 20, qd=16, rt=300, io=n, metric="bw", name=f"f{start}")
        other.scenario.write.bps_cap_seq = 1.36e9
        run_job(other, j)
    a = run_job(other, job("randread", size=256 * GIB, name="x")).stats.iops
    assert a != pytest.approx(rates[256], rel=0.05)
    # read-allocate, empty cache: the ladder decays as the working set outgrows the cache
    ra = cached_host(capacity=265, write_allocate=False, read_allocate=True)
    ra.devices["/dev/vda7"].written.add(0, 400 * GIB)
    r1 = run_job(ra, job("randread", size=GIB, rt=120, name="a", qd=32)).stats.iops
    assert r1 > 3 * 9800
    no_alloc = cached_host(capacity=265, write_allocate=False)
    no_alloc.devices["/dev/vda7"].written.add(0, 400 * GIB)
    assert run_job(no_alloc, job("randread", size=GIB)).stats.iops == pytest.approx(9800, rel=0.01)
    no_alloc.devices["/dev/vda7"].cache.write_random(0, GIB, 1e6)
    assert not no_alloc.devices["/dev/vda7"].cache.order
    wa = cached_host(capacity=2)
    run_job(wa, job("randwrite", size=4 * GIB, rt=60, name="rw"))
    assert 0 < sum(wa.devices["/dev/vda7"].cache.order.values()) <= 2.0 + 1e-9
    no_alloc.devices["/dev/vda7"].cache.write_seq(0, GIB)
    assert not no_alloc.devices["/dev/vda7"].cache.order


def test_arc_03_two_tier_cache_rates():
    sc = Scenario(read=DirScenario(iops_max=9800, lat_us=760, lat_hit_us=110))
    host = standard_host(sc)
    dev = host.devices["/dev/vda7"]
    dev.cache = CacheModel(40, 92000, tier2_capacity_gib=300, tier2_iops=15000)
    dev.written.add(0, 400 * GIB)
    dev.cache.write_seq(0, 345 * GIB)
    h1, h2 = dev.cache.hit_fractions(0, 345 * GIB)
    assert h1 == pytest.approx(40 / 345, rel=0.01) and h2 == pytest.approx(260 / 345, rel=0.01)
    assert dev.cache.hit_fractions(0, 32 * GIB) == (0.0, 0.0)  # outside both tiers


def test_arc_04_fake_clock_events_and_signals():
    h = FakeHost()
    fired = []
    h.at(h.now() + 5, lambda: fired.append(h.now()))
    h.sleep(3)
    assert not fired
    h.sleep(3)
    assert fired == [h.now() - 1]
    assert h.token() == "a1b2" and h.getenv("X") is None
    h.env["X"] = "1"
    assert h.getenv("X") == "1"
    assert h.signal_pending() is None
    h.deliver_signal("SIGTERM")
    assert h.signal_pending() == "SIGTERM"
    h.install_signals()
    assert h.signals_installed
    other = FakeHost(lock_registry=h.locks)
    assert h.lock("/run/iobench/lock") and h.lock("/run/iobench/lock")
    assert not other.lock("/run/iobench/lock")
    h.unlock()
    h.unlock()
    assert other.lock("/run/iobench/lock")


def test_arc_05_fakehost_filesystem_api():
    h = standard_host()
    h.add_disk("sdb", 8, GIB, model="M", serial="S", wwn="W")
    assert h.read_text("/sys/class/block/sdb/wwid") == "W\n"
    h.write_atomic("/run/iobench/a.json", "{}", 0o600)
    assert h.read_text("/run/iobench/a.json") == "{}" and h.modes["/run/iobench/a.json"] == 0o600
    h.append_line("/run/iobench/l", "x")
    h.append_line("/run/iobench/l", "y\n")
    assert h.read_text("/run/iobench/l") == "x\ny\n"
    assert h.exists("/run/iobench") and h.exists("/run/iobench/l") and not h.exists("/nope")
    assert h.listdir("/run/iobench") == ["a.json", "l"]
    with pytest.raises(FileNotFoundError):
        h.listdir("/nope")
    with pytest.raises(FileNotFoundError):
        h.read_text("/nope")
    h.remove("/run/iobench/l")
    h.remove("/run/iobench/l")
    assert h.stat("/run/iobench/l") is None
    assert h.stat("/run/iobench/a.json").size == 2 and h.stat("/run/iobench").is_dir
    h.symlinks["/link"] = "/run/iobench/a.json"
    assert h.stat("/link").is_symlink
    h.nlinks["/run/iobench/a.json"] = 2
    assert h.stat("/run/iobench/a.json").nlink == 2
    h.makedirs("/x/y/z")
    assert h.stat("/x/y/z").is_dir
    assert h.statfs("/bench/work") == (30 * GIB, 20 * GIB)
    h.consume("/bench/work", GIB)
    assert h.statfs("/bench")[1] == 19 * GIB and h.statfs("/etc")[1] == 20 * GIB
    assert h.realpath("/sys/class/block/vda7").endswith("/vda/vda7")
    assert h.devno("/dev/vda7") == "254:7" and h.devno("/nope") is None


def test_arc_05_fakehost_sysfs_and_forbidden():
    h = standard_host()
    q = "/sys/class/block/vda/queue/nr_requests"
    h.write_sysfs(q, "16")
    assert h.read_text(q) == "16\n" and h.write_log == [(q, "16")]
    h.sysfs_swallow.add(q)
    h.write_sysfs(q, "8")
    assert h.read_text(q) == "16\n"
    h.sysfs_reject.add(q)
    with pytest.raises(OSError):
        h.write_sysfs(q, "4")
    with pytest.raises(FileNotFoundError):
        h.write_sysfs("/sys/nothing", "1")
    with pytest.raises(HostError):
        h.write_sysfs("/sys/fs/cgroup/wings.slice/io.max", "8:0 rbps=1")
    for bad in (["fstrim", "/"], ["systemctl", "set-property", "x", "IOWeight=1"], ["systemctl", "daemon-reload"],
                ["mount", "a", "b"], ["dd", "of=/dev/vda7"], ["systemd-run", "-pIOWeight=3"]):
        with pytest.raises(HostError):
            check_argv(bad)
    check_argv(["wipefs", "--no-act", "/dev/x"])
    check_sysfs_path("/proc/sys/vm/dirty_bytes")
    with pytest.raises(HostError):
        h.run(["rm", "-rf", "/"])
    assert h.run(["fio", "--version"]).stdout.startswith("fio-")
    h.containers = ["a", "b"]
    assert h.run(devio.docker_ps_argv()).stdout == "a\nb\n"
    h.active_units.add("wings")
    assert h.run(devio.unit_active_argv("wings")).returncode == 0
    assert h.run(devio.unit_active_argv("other")).returncode == 3
    assert [a[0] for a in h.argv_log][-1] == "systemctl"


def test_arc_05_fakehost_device_commands():
    h = standard_host()
    ref = target_ref(h)
    assert h.run(devio.blkid_argv("/dev/vda7")).stdout == f"PART_ENTRY_UUID={ref.partuuid}\n"
    assert h.run(devio.blkid_argv("/dev/vda3")).stdout.count("TYPE=ext4") == 1
    h.devices["/dev/vda7"].partuuid = None
    assert h.run(devio.blkid_argv("/dev/vda7")).returncode == 2
    h.devices["/dev/vda7"].partuuid = ref.partuuid
    assert "ext4" in h.run(devio.wipefs_check_argv("/dev/vda3")).stdout
    h.put("/proc/swaps", "Filename Type Size Used Priority\n/dev/vda4 partition 1 0 -2\n")
    assert h.swap_lines() == ["/dev/vda4"] and "/dev/vda4" in h.run(devio.swapon_show_argv()).stdout
    # preallocate a file target
    r = h.run(devio.fallocate_argv("/bench/t.img", 4 * GIB))
    assert r.returncode == 0 and h.statfs("/bench")[1] == 16 * GIB
    assert h.devices["/bench/t.img"].kind == "file" and h.stat("/bench/t.img").size == 4 * GIB
    assert h.run(devio.fallocate_argv("/bench/big.img", 100 * GIB)).returncode == 1
    # marker and discard
    h.write_marker(ref, "{}")
    assert h.read_marker(ref) == "{}"
    assert h.discard_range(devio.blkdiscard_argv("/dev/vda7", 0, 4096), ref, 0, 4096).returncode == 0
    h.devices["/dev/vda7"].discard_supported = False
    assert h.discard_range(devio.blkdiscard_argv("/dev/vda7", 0, 4096), ref, 0, 4096).returncode == 1
    # identity drift is refused by every re-check
    import dataclasses
    for change in ({"size_bytes": 1}, {"devno": "9:9"}, {"partuuid": "zzz"}):
        with pytest.raises(HostError):
            h._verify(dataclasses.replace(ref, **change))
    with pytest.raises(HostError):
        h._verify(dataclasses.replace(ref, path="/dev/nope"))
    fref = target_ref(h, "/bench/t.img")
    h._verify(fref)
    with pytest.raises(HostError):
        h._verify(dataclasses.replace(fref, inode=1))


def test_arc_05_fakehost_writer_rules():
    h = standard_host()
    ref = target_ref(h)
    j = job("randwrite", size=GIB)
    with pytest.raises(HostError, match="write_range"):
        h.spawn(j.argv())
    with pytest.raises(HostError, match="extent"):
        h.write_range(j.argv(), ref, 0, GIB // 2)
    h.write_range(j.argv(), ref, 0, GIB).wait()
    assert h.devices["/dev/vda7"].written.covers(0, GIB)
    # killing a sequential writer records the shorter extent
    h.scenario.write.bps_cap_seq = 100e6
    s = job("write", off=GIB, size=2 * GIB, bs=1 << 20, qd=16, rt=100, io=2 * GIB, metric="bw", name="s")
    handle = h.write_range(s.argv(), ref, GIB, 2 * GIB)
    assert handle.poll() is None
    h.sleep(10)
    handle.kill()
    handle.kill()
    assert handle.poll() == -9 and handle.wait().returncode == -9
    ext = h.devices["/dev/vda7"].written
    assert ext.covers(GIB, GIB + 100_000_000) and not ext.covers(GIB, 2 * GIB)
    # a finished job polls to its return code
    done = h.write_range(j.with_(name="k").argv(), ref, 0, GIB)
    h.sleep(31)
    assert done.poll() == 0 and done.poll() == 0
    # an unwritten read is recorded (the zero-page trap) and confinement of reads
    h.spawn(job("randread", off=10 * GIB, name="u").argv()).wait()
    assert h.devices["/dev/vda7"].unwritten_reads == [(10 * GIB, GIB)]
    with pytest.raises(TailViolation):
        h.spawn(job("randread", off=h.devices["/dev/vda7"].size - 4096, size=4096, name="t").argv())


def test_tgt_03_device_model_tail_rules():
    dev = FakeDevice("/dev/x", 10 * GIB)
    dev.check_range(0, GIB, "fio")
    dev.check_range(dev.tail_start, 4096, "marker")
    for kind in ("fio", "discard", "fill"):
        with pytest.raises(TailViolation):
            dev.check_range(dev.tail_start - 4096, 8192, kind)
    with pytest.raises(TailViolation):  # a marker rewrite leaving the tail
        dev.check_range(dev.tail_start - 4096, 8192, "marker")
    with pytest.raises(TailViolation):
        dev.check_range(dev.tail_start, 2 * 1048576, "marker")


def test_arc_05_workdir_load_and_probe_models():
    h = standard_host()
    h.put("/proc/sys/vm/dirty_bytes", "0\n")
    h.put("/sys/class/block/vda/queue/nr_requests", "64\n")
    h.sweep_model = lambda knobs: testing.SweepOutcome(probe_p99_us=1000.0 * knobs["nr_requests"], drain_bps=20e6)
    load = devio.FioJob("load", "/bench/w/f0", "write", 1 << 20, 4, 60, 0, GIB, None, direct=False, prefix="/run/iobench/load")
    handle = h.spawn(load.argv())
    h.sleep(30)
    stats = h._diskstats().split()
    assert int(stats[9]) * 512 == pytest.approx(20e6 * 30)
    assert "Dirty" in h._meminfo() and int(h._meminfo().split("Dirty:")[1].split()[0]) > 0
    handle.wait()
    h.sleep(100)
    assert "Dirty: 0 kB" in h._meminfo()
    fs = load.with_(name="fs", fsync=16, rw="randwrite", bs=4096)
    res = devio.collect(h, fs, h.spawn(fs.argv()).wait(), 0.0)
    assert res.stats.iops == pytest.approx(800, rel=0.01)
    unit = devio.probe_unit_argv("u", "iobench.slice", 130, "pass", ["/bench/w", "/run/iobench/probe.out", "60", "0.5"])
    h.spawn(unit).wait()
    lats = sorted(float(x) for x in h.read_text("/run/iobench/probe.out").split())
    assert len(lats) == 120 and lats[118] == 64000.0
    with pytest.raises(HostError):
        h.spawn(["systemd-run", "python3", "-c", "x", "a", "b", "60", "0.5"])
    assert h.knobs()["nr_requests"] == 64
    assert h.mounts and "254:5" in h.read_text("/proc/self/mountinfo")
    h.add_mount("254:9", "/only-self", "/dev/x", ns="self")
    assert "/only-self" in h.read_text("/proc/self/mountinfo") and "/only-self" not in h.read_text("/proc/1/mountinfo")
    h.add_holder("/dev/vda7", "dm-0", 253, 0, GIB)
    assert h.listdir(h.realpath("/sys/dev/block/254:7") + "/holders") == ["dm-0"]
    h.set_fs("/mnt", GIB, GIB)
    assert h.statfs("/mnt") == (GIB, GIB)
    assert not (h.odirect_unsupported or not h.probe_odirect("/bench"))
    h.odirect_unsupported.add("/bench")
    assert not h.probe_odirect("/bench/x") and h.probe_odirect("/mnt")


# ---------------------------------------------------------------------------------------------------------
# a full FakeHost run
# ---------------------------------------------------------------------------------------------------------
from helpers import INVERSION, calibrate, make_host, matrix  # noqa: E402


def test_arc_04_deterministic_result():
    import iobench.testing as T

    def run(token):
        host = make_host(cache=INVERSION)
        host.token = lambda: token
        calibrate(host)
        return host.read_text("/var/lib/iobench/result.json"), host.read_text("/var/lib/iobench/calibration.json")

    a, b = run("a1b2"), run("a1b2")
    assert a == b
    c = run("ffff")
    assert c != a and all(x[0].replace("ffff", "a1b2") == x[1] for x in zip(c, a))  # a varying token changes the ids and nothing else
    assert json.loads(a[0])["run"]["id"].endswith("-a1b2")


def test_arc_05_builders_only():
    host = make_host(cache=INVERSION)
    calibrate(host)
    host.sweep_model = lambda k: testing.SweepOutcome()
    matrix(host)
    seen = {a[0] for a in host.argv_log}
    assert {"fio", "blkid", "wipefs", "blkdiscard", "docker"} <= seen
    for argv in host.argv_log:
        assert devio.is_allowed_argv(argv), argv
    assert "systemd-run" in seen
    # file-target builders
    fhost = standard_host()
    from iobench import guard
    guard.init_file(fhost, "/bench/t.img", size=2 << 30, own=True, expect_fs_uuid="22222222-2222-2222-2222-222222222222",
                    confirm_quiet_window=False, allow_large_root_target=False, created="2026-10-21T03:00:00Z")
    assert all(devio.is_allowed_argv(a) for a in fhost.argv_log) and ["fallocate", "-l"] == fhost.argv_log[-1][:2]


FORBIDDEN_WORDS = ("fstrim", "mkfs", "mount", "umount", "dd", "set-property", "daemon-reload")


def argv_strings(source: str) -> list[str]:
    out = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, (ast.List, ast.Tuple)):
            out += [e.value for e in node.elts if isinstance(e, ast.Constant) and isinstance(e.value, str)]
    return out


def test_arc_05_no_forbidden_argv_exists():
    for path in SRC.glob("*.py"):
        if path.name in ("host.py", "__main__.py"):  # host.py is where the refusals live
            continue
        words = argv_strings(path.read_text())
        for w in FORBIDDEN_WORDS:
            assert w not in words, (path.name, w)
        assert not any(x.startswith("mkfs") for x in words), path.name
    assert "fstrim" in argv_strings("x = ['fstrim', '/']")  # the scan can go red
