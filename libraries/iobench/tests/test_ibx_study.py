"""Fake-fio and replay coverage for the standalone IBX study controller."""
from __future__ import annotations

import json
import re
import runpy
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from studies.ibx import driver

GIB = driver.GIB
HEALTHY = {
    "psi": {"io": {"some": {"avg10": 0.1}}, "memory": {"some": {"avg10": 0.1}}},
    "meminfo": {"MemAvailable": 16 * GIB}, "free_bytes": 16 * GIB,
    "diskstats": {"writes_completed": 3, "sectors_written": 20, "flushes_completed": 1,
                   "discards_completed": 2, "sectors_discarded": 4},
}


class FakeFio:
    """Model fio's key stop semantics: time_based ignores io_size; otherwise io_size bounds bytes."""

    rate_bps = 1024**2

    def __init__(self):
        self.calls = []

    def run(self, job_path: Path, max_runtime=None):
        text = job_path.read_text(encoding="utf-8")
        options = {}
        for line in text.splitlines():
            if "=" in line and not line.startswith("["):
                key, value = line.split("=", 1)
                options[key] = value
        runtime = int(options["runtime"])
        block = int(options["bs"])
        rw = options["rw"]
        io_size = int(options["io_size"]) if "io_size" in options else None
        timed = options.get("time_based") == "1"
        runtime = min(runtime, max_runtime) if max_runtime is not None else runtime
        if timed:
            done = self.rate_bps * runtime
        elif io_size is not None:
            done = min(io_size, self.rate_bps * runtime)
        else:
            done = self.rate_bps * runtime
        duration = float(runtime) if timed else min(float(runtime), done / self.rate_bps)
        stat_name = "write" if rw in ("write", "randwrite") else "read"
        stats = {"io_bytes": done, "bw_bytes": done / max(duration, 0.001),
                 "iops": done / block / max(duration, 0.001),
                 "clat_ns": {"percentile": {"95.000000": 100_000.0, "99.000000": 120_000.0}}}
        payload = {"jobs": [{stat_name: stats}]}
        prefix = Path(options["write_bw_log"])
        prefix.parent.mkdir(parents=True, exist_ok=True)
        (Path(f"{prefix}_bw.1.log")).write_text("0,1024\n1000,1024\n", encoding="utf-8")
        (Path(f"{prefix}_iops.1.log")).write_text("0,100\n1000,100\n", encoding="utf-8")
        self.calls.append({"options": options, "bytes": done, "duration": duration})
        return payload, duration


class FakeHost:
    def __init__(self, *, psi_io=0.1, psi_mem=0.1, available=16 * GIB, free=16 * GIB):
        self.fio = FakeFio()
        self.snapshot = {"io_cost": {"enable": 0}, "cgroups": []}
        self.psi_io = psi_io
        self.psi_mem = psi_mem
        self.available = available
        self.free = free
        self.discards = []
        self.fsync_starts = []
        self.fsync_stops = []
        self.quiet_checked = 0
        self.clock = 0.0

    def is_root(self):
        return True

    def assert_quiet_window(self):
        self.quiet_checked += 1

    def validate_target(self, target, kind, offset, size, device):
        self.validated_target = (target, kind, offset, size, device)

    def io_control_snapshot(self):
        return self.snapshot

    def trim_state(self, device):
        return {"supported": True, "discard_max_bytes": 1024**3}

    def discard(self, target, kind, offset, size, timeout=None, sample=None):
        self.discards.append((target, kind, offset, size, timeout))
        if sample is not None:
            sample()
        return 0.01

    def now(self):
        return self.clock

    def sleep(self, seconds):
        self.clock += seconds

    def sample(self, device, out, target=None):
        return {**HEALTHY, "device": device,
                "psi": {"io": {"some": {"avg10": self.psi_io}},
                        "memory": {"some": {"avg10": self.psi_mem}}},
                "meminfo": {"MemAvailable": self.available}, "free_bytes": self.free}

    def wait_with_samples(self, seconds, sample):
        for _ in range(seconds):
            sample()
            self.clock += 1

    def execute_fio(self, job_path, runtime_s, sample):
        payload, duration = self.fio.run(job_path, max_runtime=runtime_s)
        elapsed = 0.0
        while elapsed < duration:
            sample()
            step = min(1, duration - elapsed)
            self.clock += step
            elapsed += step
        return payload, duration

    def read_fio_logs(self, prefix):
        return driver.LocalHost().read_fio_logs(prefix)

    def start_fsync(self, out):
        record = ("owned-collector", out / "fsync-bpf")
        record[1].mkdir(parents=True, exist_ok=True)
        self.fsync_starts.append(record)
        return record

    def stop_fsync(self, collector):
        self.fsync_stops.append(collector)


class InterruptOnceHost(FakeHost):
    def __init__(self):
        super().__init__()
        self.interrupted = False

    def execute_fio(self, job_path, runtime_s, sample):
        payload, duration = super().execute_fio(job_path, runtime_s, sample)
        options = self.fio.calls[-1]["options"]
        if not self.interrupted and options["rw"] in ("write", "randwrite"):
            self.interrupted = True
            raise driver.StudyError("simulated interruption after a partial write")
        return payload, duration


def run_set(tmp_path, name, *, host=None, target_type=None, target="/dev/fake", offset=0,
            size=24 * GIB, fsync_cell=None):
    return driver.run_study(name, target=target, device="vda5", out=tmp_path,
                            target_kind=target_type, offset=offset, size=size,
                            io_psi_max=20, mem_psi_max=20, min_memavailable=2 * GIB,
                            min_free=4 * GIB, host=host or FakeHost(), fsync_cell=fsync_cell,
                            quiet_window_confirmed=True)


def test_fio_writer_is_volume_bounded_and_fake_models_time_based_io_size(tmp_path):
    writer = driver.Job("bounded", "write", 1024**2, 32, 10, "wbps", 4096)
    text = driver.render_job(writer, target="/dev/fake", offset=0, size=GIB,
                             log_prefix=str(tmp_path / "logs" / "one"))
    assert "direct=1" in text
    assert "io_size=4096" in text
    assert "time_based=1" not in text
    path = tmp_path / "bounded.fio"
    path.write_text(text, encoding="utf-8")
    fake = FakeFio()
    payload, _ = fake.run(path)
    assert payload["jobs"][0]["write"]["io_bytes"] == 4096

    opts = text + "time_based=1\nio_size=4096\n"
    timed = tmp_path / "timed.fio"
    timed.write_text(opts, encoding="utf-8")
    timed_payload, _ = fake.run(timed)
    assert timed_payload["jobs"][0]["write"]["io_bytes"] == 10 * FakeFio.rate_bps


def test_profile_cells_match_nested_family_and_total_write_caps():
    veryquick = driver.rung_jobs("veryquick")
    quick = driver.rung_jobs("quick")
    standard = driver.rung_jobs("standard")
    assert "seq4k-coarse" not in {job.name for job in veryquick}
    assert not {"seq4k-read", "seq4k-write"} & {job.name for job in veryquick}
    assert "fresh4k-write" not in {job.name for job in veryquick}
    assert "fresh4k-write" in {job.name for job in quick}
    assert "rewrite-pass-3" in {job.name for job in standard}
    assert sum(job.write_cap for job in veryquick if job.writes) <= 15 * 10**9
    assert sum(job.write_cap for job in quick if job.writes) <= 26 * 10**9
    assert sum(job.write_cap for job in standard if job.writes) <= 100 * 10**9
    assert all(job.write_cap > 0 for job in standard if job.writes)


def test_run_resume_monitors_and_records_one_json_per_fio_cell(tmp_path):
    host = FakeHost()
    first = run_set(tmp_path, "raw-t0", host=host)
    files = sorted((tmp_path / "cells").glob("*.json"))
    assert first["results"] == len(files) == len(host.fio.calls) + 3
    assert len(host.discards) == 7
    assert host.quiet_checked == 1
    assert all("time_based=1" not in job["options"] or "io_size" not in job["options"]
               for job in host.fio.calls if job["options"]["rw"] in ("write", "randwrite"))
    assert all("io_size" in job["options"] for job in host.fio.calls
               if job["options"]["rw"] in ("write", "randwrite"))
    state = json.loads((tmp_path / driver.STATE_NAME).read_text(encoding="utf-8"))
    assert state["write_bytes"] == first["write_bytes"]
    assert state["io_control_runs"]["raw-t0"]["before"] == state["io_control_runs"]["raw-t0"]["after"] == host.snapshot
    fio_record = next(json.loads(path.read_text(encoding="utf-8")) for path in files
                      if '"fio"' in path.read_text(encoding="utf-8"))
    assert set(fio_record) >= {
        "fio", "fio_series", "estimates", "io_size", "write_bytes", "io_bytes"}
    read_record = next(json.loads(path.read_text(encoding="utf-8")) for path in files
                       if json.loads(path.read_text(encoding="utf-8")).get("number") == "rbps")
    assert read_record["io_bytes"] > 0 and read_record["write_bytes"] == 0
    monitor = [json.loads(line) for line in (tmp_path / "monitor.jsonl").read_text().splitlines()]
    assert monitor and {"flushes_completed", "discards_completed"} <= set(monitor[0]["diskstats"])
    calls = len(host.fio.calls)
    cached_rate_read = next(path for path in files
                            if json.loads(path.read_text(encoding="utf-8")).get("cell") == "rate-read-q1")
    cached_payload = json.loads(cached_rate_read.read_text(encoding="utf-8"))
    cached_payload.pop("estimates")
    driver.write_json(cached_rate_read, cached_payload)
    resumed = run_set(tmp_path, "raw-t0", host=host)
    assert resumed["results"] == calls + 3
    assert len(host.fio.calls) == calls
    assert len(host.discards) == 7
    host.snapshot = {"changed": True}
    with pytest.raises(driver.StudyError, match="snapshot changed since the previous attempt"):
        run_set(tmp_path, "raw-t0", host=host)
    state = json.loads((tmp_path / driver.STATE_NAME).read_text(encoding="utf-8"))
    state["io_control_runs"]["raw-t0"] = "malformed"
    driver.write_json(tmp_path / driver.STATE_NAME, state)
    with pytest.raises(driver.StudyError, match="snapshot history is malformed"):
        run_set(tmp_path, "raw-t0", host=host)


def test_interrupted_cell_keeps_write_and_active_allowances_on_resume(tmp_path):
    host = InterruptOnceHost()
    with pytest.raises(driver.StudyError, match="simulated interruption"):
        run_set(tmp_path, "raw-t0", host=host)
    state_path = tmp_path / driver.STATE_NAME
    state = json.loads(state_path.read_text(encoding="utf-8"))
    writer_key = "raw|t0|standard|seq1m-write"
    writer_cap = next(job.write_cap for job in driver.rung_jobs("standard")
                      if job.name == "seq1m-write")
    assert state["write_bytes"] == writer_cap
    assert state["write_reservations"][writer_key] == [writer_cap]
    assert state["active_reservations"][writer_key] == [
        driver.RUNG["standard"]["burst_s"] + driver.FIO_DRAIN_RESERVE_S]
    resumed = run_set(tmp_path, "raw-t0", host=host)
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert resumed["write_bytes"] == state["write_bytes"]
    assert state["write_reservations"][writer_key] == [writer_cap]
    assert state["active_reservations"][writer_key] == [
        driver.RUNG["standard"]["burst_s"] + driver.FIO_DRAIN_RESERVE_S]


def test_reservation_helpers_replace_caps_with_actuals_and_fail_closed():
    state = driver.empty_state(driver.dt.datetime.now(driver.dt.UTC).isoformat(),
                               "raw", "/dev/vda5", "vda5")
    driver.reserve_write(state, "cell", 100)
    driver.settle_write(state, "cell", 60)
    assert state["write_bytes"] == 60 and state["write_reservations"] == {}
    driver.settle_write(state, "other", 10)
    with pytest.raises(driver.StudyError, match="reserved cell cap"):
        driver.reserve_write(state, "bad", 5)
        driver.settle_write(state, "bad", 6)
    driver.reserve_active(state, "cell", 30)
    driver.settle_active(state, "cell", 20)
    driver.settle_active(state, "other", 5)
    assert state["active_seconds"] == 25 and state["active_reservations"] == {}
    with pytest.raises(driver.StudyError, match="reserved active-time allowance"):
        driver.reserve_active(state, "bad", 2)
        driver.settle_active(state, "bad", 3)


def test_fsync_option_wraps_only_the_named_cell_and_file_run_does_not_discard(tmp_path):
    host = FakeHost()
    run_set(tmp_path, "file-ext4", host=host, target_type="ext4-file",
            target="/mnt/target.dat", size=24 * GIB, fsync_cell="quick:seq1m-write")
    assert len(host.fsync_starts) == len(host.fsync_stops) == 1
    assert len(host.discards) == 2
    wrapped = [json.loads(path.read_text()) for path in (tmp_path / "cells").glob("*.json")
               if json.loads(path.read_text()).get("fsync")]
    assert len(wrapped) == 1
    assert wrapped[0]["cell"] == "seq1m-write"
    with pytest.raises(driver.StudyError, match="RUNG:CELL"):
        run_set(tmp_path / "invalid", "file-ext4", host=FakeHost(), target_type="ext4-file",
                target="/mnt/target.dat", fsync_cell="unknown")


def test_part_c_uses_region_c_adaptive_wait_and_bounded_writes(tmp_path):
    host = FakeHost()
    run_set(tmp_path, "part-c", host=host, offset=24 * GIB, size=8 * GIB)
    assert ("/dev/fake", "raw", 24 * GIB, 8 * GIB, pytest.approx(3 * 60 * 60 - 60)) in host.discards
    jobs = [job["options"] for job in host.fio.calls]
    assert len(jobs) == 14
    assert all("io_size" in row for row in jobs if row["rw"] in ("write", "randwrite"))
    assert all("time_based" not in row for row in jobs if row["rw"] in ("write", "randwrite"))
    assert sum(1 for row in jobs if row.get("runtime") == "600") == 5
    assert sum(int(row["io_size"]) for row in jobs if row.get("rw") == "write") >= 36 * GIB
    recovery_job = next(job for job in driver.rung_jobs("quick", part_c=True)
                        if job.name == "c-recover10-seq")
    assert recovery_job.write_cap == 4 * GIB


def test_part_c_positive_replay_and_repeat_gate(tmp_path):
    assert driver.part_c_assessment(tmp_path) == {"status": "incomplete", "confirmed": False}
    cells = tmp_path / "cells"
    cells.mkdir()
    rows = [
        ("c-pass1-seq", "c_seq_bps", 100.0), ("c-pass4-seq", "c_seq_bps", 60.0),
        ("c-pass1-rand", "c_rand_iops", 100.0), ("c-idle-probe", "idle_iops", 70.0),
        ("c-idle-seq", "c_seq_bps", 70.0),
        ("c-recover10-rand", "c_recovery_iops", 95.0),
        ("c-recover10-seq", "c_recovery_bps", 95.0), ("rate", "wbps", 100.0),
        ("rate", "wbps", 110.0), ("rate", "wrandiops", 100.0),
        ("rate", "wrandiops", 200.0),
    ]
    for index, (cell, number, value) in enumerate(rows):
        row = {"target_type": "raw", "slot": "t1h" if number not in ("wrandiops", "wbps") else f"t{index}h",
               "rung": "part-c" if number not in ("wrandiops", "wbps") else "quick",
               "number": number, "cell": cell, "value": value}
        (cells / f"{index}.json").write_text(json.dumps(row), encoding="utf-8")
    result = driver.part_c_assessment(tmp_path)
    assert result["confirmed"] is True
    assert result["raw_quick_spread"] == pytest.approx(0.1)
    assert result["rewrite_threshold"] == pytest.approx(0.7)
    assert result["rewrite_below_threshold"] and result["idle_stays_low"] and result["recovered_by_10m"]
    pass4_path = next(path for path in cells.glob("*.json")
                      if json.loads(path.read_text())["cell"] == "c-pass4-seq")
    pass4 = json.loads(pass4_path.read_text())
    pass4["value"] = 73
    pass4_path.write_text(json.dumps(pass4), encoding="utf-8")
    assert driver.part_c_assessment(tmp_path)["rewrite_below_threshold"] is False
    pass4["value"] = 60
    pass4_path.write_text(json.dumps(pass4), encoding="utf-8")
    repeat_root = tmp_path / "repeat"
    repeat_cells = repeat_root / "cells"
    repeat_cells.mkdir(parents=True)
    for index, path in enumerate(cells.glob("*.json")):
        (repeat_cells / f"{index}.json").write_text(path.read_text(), encoding="utf-8")
    run_set(repeat_root, "part-c-repeat", host=FakeHost(), offset=24 * GIB, size=8 * GIB)
    repeated = [json.loads(path.read_text()) for path in repeat_cells.glob("*.json")]
    assert any(row.get("cell_set") == "part-c-repeat" and row.get("slot") == "t4h"
               for row in repeated)
    idle_seq = json.loads((cells / "4.json").read_text())
    idle_seq["value"] = 95.0
    (cells / "4.json").write_text(json.dumps(idle_seq), encoding="utf-8")
    assert driver.part_c_assessment(tmp_path)["confirmed"] is False
    with pytest.raises(driver.StudyError, match="requires the first"):
        run_set(tmp_path / "empty", "part-c-repeat", host=FakeHost(), offset=24 * GIB, size=8 * GIB)


def test_optional_file_standard_requires_borderline_quick_replay(tmp_path):
    host = FakeHost()
    run_set(tmp_path, "raw-t0", host=host)
    run_set(tmp_path, "file-ext4", host=host, target_type="ext4-file", target="/mnt/target.dat")
    assert not driver.file_standard_needed(tmp_path, "ext4-file")
    cells = tmp_path / "cells"
    decoded = [(path, json.loads(path.read_text())) for path in cells.glob("*.json")]
    raw_standard = next(row for _, row in decoded if row.get("target_type") == "raw" and
                        row.get("rung") == "standard" and row.get("number") == "rbps")
    file_quick = next(path for path, row in decoded if row.get("target_type") == "ext4-file" and
                      row.get("rung") == "quick" and row.get("number") == "rbps")
    file_row = json.loads(file_quick.read_text())
    file_row["value"] = raw_standard["value"] * 0.5
    file_quick.write_text(json.dumps(file_row), encoding="utf-8")
    assert driver.file_standard_needed(tmp_path, "ext4-file")


def test_local_trim_state_reads_explicit_sysfs_capability(tmp_path):
    path = tmp_path / "class/block/vda5/queue/discard_max_bytes"
    path.parent.mkdir(parents=True)
    path.write_text("4096\n", encoding="utf-8")
    host = driver.LocalHost(sys=tmp_path)
    assert host.trim_state("vda5") == {"supported": True, "discard_max_bytes": 4096}
    path.write_text("0\n", encoding="utf-8")
    assert host.trim_state("vda5")["supported"] is False
    path.write_text("bad\n", encoding="utf-8")
    with pytest.raises(driver.StudyError, match="malformed"):
        host.trim_state("vda5")
    with pytest.raises(driver.StudyError, match="cannot read"):
        host.trim_state("missing")


def test_local_target_validation_matches_monitored_device(tmp_path, monkeypatch):
    target = tmp_path / "target.dat"
    target.write_bytes(b"x" * 4096)
    dev = tmp_path / "sys/class/block/vda5/dev"
    dev.parent.mkdir(parents=True)
    dev.write_text("8:5\n", encoding="utf-8")
    host = driver.LocalHost(sys=tmp_path / "sys")
    with pytest.raises(driver.StudyError, match="exactly covering the requested extent"):
        host.validate_target(str(target), "ext4-file", 4096, 4096, "vda5")
    link = tmp_path / "link.dat"
    link.symlink_to(target)
    with pytest.raises(driver.StudyError, match="must not be a symlink"):
        host.validate_target(str(link), "ext4-file", 0, 4096, "vda5")
    def find_ext4(args, **kwargs):
        return SimpleNamespace(returncode=0, stdout="ext4 8:5\n", stderr="")
    monkeypatch.setattr(driver.subprocess, "run", find_ext4)
    host.validate_target(str(target), "ext4-file", 0, 4096, "vda5")
    monkeypatch.setattr(driver.subprocess, "run", lambda *args, **kwargs:
        SimpleNamespace(returncode=0, stdout="btrfs 8:6\n", stderr=""))
    with pytest.raises(driver.StudyError, match="does not match monitored"):
        host.validate_target(str(target), "btrfs-file", 0, 4096, "vda5")
    monkeypatch.setattr(driver.stat, "S_ISBLK", lambda mode: True)
    dev.write_text("0:0\n", encoding="utf-8")
    def unmounted_and_sized(args, **kwargs):
        if args[0] == "findmnt":
            return SimpleNamespace(returncode=1, stdout="", stderr="")
        return SimpleNamespace(returncode=0, stdout=f"{GIB}\n", stderr="")
    monkeypatch.setattr(driver.subprocess, "run", unmounted_and_sized)
    host.validate_target(str(target), "raw", 0, 4096, "vda5")
    def no_size(args, **kwargs):
        if args[0] == "findmnt":
            return SimpleNamespace(returncode=1, stdout="", stderr="")
        return SimpleNamespace(returncode=1, stdout="", stderr="no block size")
    monkeypatch.setattr(driver.subprocess, "run", no_size)
    with pytest.raises(driver.StudyError, match="raw target size"):
        host.validate_target(str(target), "raw", 0, 4096, "vda5")
    monkeypatch.setattr(driver.subprocess, "run", lambda *args, **kwargs:
        SimpleNamespace(returncode=0, stdout="/mnt/study\n", stderr=""))
    with pytest.raises(driver.StudyError, match="is mounted"):
        host.validate_target(str(target), "raw", 0, 4096, "vda5")
    monkeypatch.setattr(driver.subprocess, "run", lambda *args, **kwargs:
        SimpleNamespace(returncode=2, stdout="", stderr="findmnt failed"))
    with pytest.raises(driver.StudyError, match="cannot establish whether"):
        host.validate_target(str(target), "raw", 0, 4096, "vda5")
    monkeypatch.setattr(driver.subprocess, "run", lambda *args, **kwargs:
        SimpleNamespace(returncode=0, stdout="btrfs 8:5\n", stderr=""))
    with pytest.raises(driver.StudyError, match="must be on ext4"):
        host.validate_target(str(target), "ext4-file", 0, 4096, "vda5")
    dev.write_text("bad\n", encoding="utf-8")
    with pytest.raises(driver.StudyError, match="device number.*malformed"):
        host.validate_target(str(target), "ext4-file", 0, 4096, "vda5")
    with pytest.raises(driver.StudyError, match="file target must be on ext4"):
        dev.write_text("8:5\n", encoding="utf-8")
        monkeypatch.setattr(driver.subprocess, "run", lambda *args, **kwargs:
            SimpleNamespace(returncode=1, stdout="", stderr="not mounted"))
        host.validate_target(str(target), "ext4-file", 0, 4096, "vda5")


def test_local_quiet_window_and_fsync_wrapper_are_mocked(tmp_path, monkeypatch):
    host = driver.LocalHost()
    quiet = [SimpleNamespace(returncode=0, stdout="loaded\ninactive\n", stderr="") for _ in range(3)]
    monkeypatch.setattr(driver.subprocess, "run", lambda *args, **kwargs: quiet.pop(0))
    host.assert_quiet_window()
    active = [SimpleNamespace(returncode=0, stdout="loaded\nactive\n", stderr="")]
    monkeypatch.setattr(driver.subprocess, "run", lambda *args, **kwargs: active.pop(0))
    with pytest.raises(driver.StudyError, match="is active"):
        host.assert_quiet_window()
    unknown = [SimpleNamespace(returncode=0, stdout="weird\ninactive\n", stderr="")]
    monkeypatch.setattr(driver.subprocess, "run", lambda *args, **kwargs: unknown.pop(0))
    with pytest.raises(driver.StudyError, match="state is unknown"):
        host.assert_quiet_window()
    failed = [SimpleNamespace(returncode=1, stdout="", stderr="")]
    monkeypatch.setattr(driver.subprocess, "run", lambda *args, **kwargs: failed.pop(0))
    with pytest.raises(driver.StudyError, match="cannot verify"):
        host.assert_quiet_window()

    proc = tmp_path / "proc/self/cgroup"
    proc.parent.mkdir(parents=True)
    proc.write_text("0::/system.slice/docker-" + "a" * 64 + ".scope\n", encoding="utf-8")
    host = driver.LocalHost(proc=tmp_path / "proc")
    assert host.current_container_id() == "a" * 64
    proc.write_text("0::/system.slice/other.scope\n", encoding="utf-8")
    with pytest.raises(driver.StudyError, match="Docker cgroup identity"):
        host.current_container_id()
    proc.write_text("0::/system.slice/docker-" + "a" * 64 + ".scope\n", encoding="utf-8")
    monkeypatch.delenv("CGROUP_PARENT_DEV_BACKGROUND", raising=False)
    with pytest.raises(driver.StudyError, match="requires CGROUP_PARENT"):
        host.start_fsync(tmp_path)
    monkeypatch.setenv("CGROUP_PARENT_DEV_BACKGROUND", "dev-background.slice")
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        if str(argv[0]).endswith("mdt-cockpit/scripts/iomon-bpf/run.sh"):
            Path(argv[argv.index("--out") + 1]).mkdir(parents=True, exist_ok=True)
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if argv[0] == "systemctl":
            return SimpleNamespace(returncode=0, stdout="loaded\n", stderr="")
        if argv[0] == "docker" and argv[1] == "inspect":
            return SimpleNamespace(returncode=0, stdout="true\n", stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(driver.subprocess, "run", run)
    collector = host.start_fsync(tmp_path)
    assert collector[0].startswith("iomon-bpf-ibx-")
    host.stop_fsync(collector)
    assert (collector[1] / "STOP-BPF").is_file()
    assert any(call[0] == "docker" and call[1] == "stop" for call in calls)


def test_local_fsync_refuses_unloaded_slice_and_cleans_timed_out_collector(tmp_path, monkeypatch):
    proc = tmp_path / "proc/self/cgroup"
    proc.parent.mkdir(parents=True)
    proc.write_text("0::/system.slice/docker-" + "b" * 64 + ".scope\n", encoding="utf-8")
    host = driver.LocalHost(proc=tmp_path / "proc")
    monkeypatch.setenv("CGROUP_PARENT_DEV_BACKGROUND", "missing.slice")
    monkeypatch.setattr(driver.subprocess, "run", lambda *args, **kwargs:
        SimpleNamespace(returncode=0, stdout="not-found\n", stderr=""))
    with pytest.raises(driver.StudyError, match="not a loaded"):
        host.start_fsync(tmp_path)

    monkeypatch.setattr(driver.subprocess, "run", lambda *args, **kwargs:
        SimpleNamespace(returncode=0, stdout="loaded\n", stderr="") if args[0][0] == "systemctl" else
        SimpleNamespace(returncode=0, stdout="false\n", stderr=""))
    clock = [0.0]
    host.now = lambda: clock[0]
    host.sleep = lambda seconds: clock.__setitem__(0, clock[0] + seconds)
    with pytest.raises(driver.StudyError, match="did not start"):
        host.start_fsync(tmp_path)


def test_local_discard_wait_logs_sample_and_root_helpers_are_mocked(tmp_path, monkeypatch):
    host = driver.LocalHost()
    calls = []
    clock = [0.0]
    host.now = lambda: clock[0]
    host.sleep = lambda seconds: clock.__setitem__(0, clock[0] + seconds)
    processes = [FakeProcess(finish_after=0), FakeProcess(finish_after=1)]
    monkeypatch.setattr(driver.subprocess, "Popen", lambda argv, **kwargs:
        calls.append((argv, kwargs)) or processes.pop(0))
    host.discard("/dev/vda5", "raw", 10, 20, timeout=30)
    host.discard("/mnt/file", "ext4-file", 0, 10)
    assert calls[0][0] == ["blkdiscard", "-o", "10", "-l", "20", "/dev/vda5"]
    assert calls[1][0] == ["fallocate", "-p", "-o", "0", "-l", "10", "/mnt/file"]
    assert calls[0][1]["stdout"] == driver.subprocess.PIPE and calls[0][1]["close_fds"]
    timed = FakeProcess(finish_after=1)
    processes.append(timed)
    samples = []
    host.discard("/dev/vda5", "raw", 0, 1, timeout=3, sample=lambda: samples.append(clock[0]))
    assert samples == [1.0]
    processes.append(FakeProcess(finish_after=None, wait_timeouts=1))
    with pytest.raises(driver.StudyError, match="remaining active-I/O"):
        host.discard("/dev/vda5", "raw", 0, 1, timeout=1)
    assert processes == []
    assert calls[-1][0] == ["blkdiscard", "-o", "0", "-l", "1", "/dev/vda5"]
    with pytest.raises(driver.StudyError, match="remaining active-I/O"):
        host.discard("/dev/vda5", "raw", 0, 1, timeout=0)
    processes.append(FakeProcess(finish_after=0, returncode=1))
    with pytest.raises(driver.StudyError, match="discard failed"):
        host.discard("/dev/vda5", "raw", 0, 1)

    interrupted = FakeProcess(finish_after=None, wait_timeouts=1)
    processes.append(interrupted)
    with pytest.raises(driver.StudyError, match="monitor stop"):
        host.discard("/dev/vda5", "raw", 0, 1, sample=lambda: (_ for _ in ()).throw(
            driver.StudyError("monitor stop")))
    assert interrupted.signals == [driver.signal.SIGTERM, driver.signal.SIGKILL]

    clock = [0.0]
    host.now = lambda: clock[0]
    host.sleep = lambda seconds: clock.__setitem__(0, clock[0] + seconds)
    samples = []
    host.wait_with_samples(3, lambda: samples.append(clock[0]))
    assert samples == [0.0, 1.0, 2.0]
    prefix = tmp_path / "fio" / "cell"
    prefix.parent.mkdir()
    Path(f"{prefix}_bw.1.log").write_text("0,2\n", encoding="utf-8")
    assert host.read_fio_logs(prefix)["bw"][0]["value"] == 2048
    assert host.read_fio_logs(tmp_path / "missing") == {"bw": [], "iops": []}
    monkeypatch.setattr(driver, "monitor_sample", lambda device, proc: {"device": device})
    sampled = host.sample("vda5", tmp_path)
    assert sampled["device"] == "vda5" and sampled["free_bytes"] > 0
    sampled_target = host.sample("vda5", tmp_path, str(tmp_path))
    assert sampled_target["target_free_bytes"] == sampled_target["output_free_bytes"]
    monkeypatch.setattr(driver.os, "geteuid", lambda: 0)
    assert host.is_root()
    monkeypatch.setattr(driver.os, "geteuid", lambda: 1000)
    assert host.is_root() is False


class FakeProcess:
    def __init__(self, *, finish_after=1, returncode=0, output='{"jobs": []}', wait_timeouts=0):
        self.finish_after = finish_after
        self.desired_returncode = returncode
        self.output = output
        self.wait_timeouts = wait_timeouts
        self.poll_count = 0
        self.returncode = None
        self.signals = []
        self.waits = 0

    def poll(self):
        self.poll_count += 1
        if self.returncode is not None:
            return self.returncode
        if self.finish_after is not None and self.poll_count > self.finish_after:
            self.returncode = self.desired_returncode
        return self.returncode

    def send_signal(self, value):
        self.signals.append(value)
        if value == driver.signal.SIGTERM or self.wait_timeouts == 0:
            self.returncode = 0

    def kill(self):
        self.signals.append(driver.signal.SIGKILL)
        self.returncode = -driver.signal.SIGKILL

    def wait(self, timeout=None):
        self.waits += 1
        if self.wait_timeouts:
            self.wait_timeouts -= 1
            raise subprocess.TimeoutExpired("fio", timeout)
        self.returncode = self.returncode or 0
        return self.returncode

    def communicate(self):
        return self.output, "fake stderr"


def test_local_execute_fio_completion_timeout_errors_and_cleanup(tmp_path, monkeypatch):
    host = driver.LocalHost()
    clock = [0.0]
    host.now = lambda: clock[0]
    host.sleep = lambda seconds: clock.__setitem__(0, clock[0] + seconds)
    process = FakeProcess()
    monkeypatch.setattr(driver.subprocess, "Popen", lambda *args, **kwargs: process)
    samples = []
    payload, duration = host.execute_fio(tmp_path / "job.fio", 5, lambda: samples.append(1))
    assert payload == {"jobs": []} and duration == 1 and len(samples) == 1

    clock[0] = 0
    timed = FakeProcess(finish_after=None)
    monkeypatch.setattr(driver.subprocess, "Popen", lambda *args, **kwargs: timed)
    payload, duration = host.execute_fio(tmp_path / "job.fio", 1, lambda: None)
    assert payload == {"jobs": []} and duration == 1
    assert timed.signals == [driver.signal.SIGINT]

    clock[0] = 0
    drain = FakeProcess(finish_after=None, wait_timeouts=1)
    monkeypatch.setattr(driver.subprocess, "Popen", lambda *args, **kwargs: drain)
    host.execute_fio(tmp_path / "job.fio", 1, lambda: None)
    assert drain.signals == [driver.signal.SIGINT, driver.signal.SIGTERM]

    failed = FakeProcess(finish_after=0, returncode=2)
    monkeypatch.setattr(driver.subprocess, "Popen", lambda *args, **kwargs: failed)
    with pytest.raises(driver.StudyError, match="fio failed with exit 2"):
        host.execute_fio(tmp_path / "job.fio", 5, lambda: None)
    malformed = FakeProcess(output="not json")
    monkeypatch.setattr(driver.subprocess, "Popen", lambda *args, **kwargs: malformed)
    with pytest.raises(driver.StudyError, match="invalid JSON"):
        host.execute_fio(tmp_path / "job.fio", 5, lambda: None)
    interrupted = FakeProcess(finish_after=None)
    monkeypatch.setattr(driver.subprocess, "Popen", lambda *args, **kwargs: interrupted)
    with pytest.raises(driver.StudyError, match="monitor stop"):
        host.execute_fio(tmp_path / "job.fio", 5,
                         lambda: (_ for _ in ()).throw(driver.StudyError("monitor stop")))
    assert interrupted.signals == [driver.signal.SIGTERM]
    finished_during_sample = FakeProcess(finish_after=1)
    monkeypatch.setattr(driver.subprocess, "Popen", lambda *args, **kwargs: finished_during_sample)
    with pytest.raises(driver.StudyError, match="monitor stop"):
        host.execute_fio(tmp_path / "job.fio", 5,
                         lambda: (_ for _ in ()).throw(driver.StudyError("monitor stop")))
    assert finished_during_sample.signals == []


def test_run_guards_and_stop_thresholds_are_fail_closed(tmp_path):
    host = FakeHost()
    with pytest.raises(driver.StudyError, match="root"):
        driver.run_study("raw-t0", target="/dev/fake", device="vda5", out=tmp_path / "a",
                         target_kind="raw", offset=0, size=24 * GIB, io_psi_max=20,
                         mem_psi_max=20, min_memavailable=GIB, min_free=GIB,
                         host=SimpleNamespace(is_root=lambda: False), quiet_window_confirmed=True)
    with pytest.raises(driver.StudyError, match="confirm-quiet"):
        driver.run_study("raw-t0", target="/dev/fake", device="vda5", out=tmp_path / "b",
                         target_kind="raw", offset=0, size=24 * GIB, io_psi_max=20,
                         mem_psi_max=20, min_memavailable=GIB, min_free=GIB, host=host)
    with pytest.raises(driver.StudyError, match="device a sysfs"):
        run_set(tmp_path / "c", "raw-t0", host=host, target="relative", size=GIB)
    with pytest.raises(driver.StudyError, match="extent"):
        run_set(tmp_path / "d", "raw-t0", host=host, offset=31 * GIB, size=2 * GIB)
    with pytest.raises(driver.StudyError, match="PSI limits"):
        driver.run_study("raw-t0", target="/dev/fake", device="vda5", out=tmp_path / "e",
                         target_kind="raw", offset=0, size=24 * GIB, io_psi_max=0,
                         mem_psi_max=20, min_memavailable=GIB, min_free=GIB, host=host,
                         quiet_window_confirmed=True)
    with pytest.raises(driver.StudyError, match="PSI limits"):
        driver.run_study("raw-t0", target="/dev/fake", device="vda5", out=tmp_path / "nan",
                         target_kind="raw", offset=0, size=24 * GIB, io_psi_max=float("nan"),
                         mem_psi_max=20, min_memavailable=GIB, min_free=GIB, host=host,
                         quiet_window_confirmed=True)
    with pytest.raises(driver.StudyError, match="memory and free-space floors"):
        driver.run_study("raw-t0", target="/dev/fake", device="vda5", out=tmp_path / "floor",
                         target_kind="raw", offset=0, size=24 * GIB, io_psi_max=20,
                         mem_psi_max=20, min_memavailable=0, min_free=GIB, host=host,
                         quiet_window_confirmed=True)
    healthy = {**HEALTHY, "free_bytes": 8 * GIB}
    assert driver.stop_reason(healthy, min_memavailable=GIB, min_free=4 * GIB) is None
    assert "incomplete" in driver.stop_reason({}, min_memavailable=GIB, min_free=GIB)
    lowmem = {**HEALTHY, "meminfo": {"MemAvailable": 1}}
    assert "MemAvailable" in driver.stop_reason(lowmem, min_memavailable=GIB, min_free=1)
    lowfree = {**HEALTHY, "free_bytes": 1}
    assert "free space" in driver.stop_reason(lowfree, min_memavailable=1, min_free=GIB)
    assert "malformed" in driver.stop_reason({**HEALTHY, "free_bytes": "1"},
                                               min_memavailable=1, min_free=1)
    assert "malformed" in driver.stop_reason({**HEALTHY, "psi": {"io": {"some": {"avg10": -1}},
                                        "memory": {"some": {"avg10": 0.1}}}},
                                               min_memavailable=1, min_free=1)
    assert "malformed" in driver.stop_reason({**HEALTHY, "psi": {"io": {"some": {"avg10": float("nan")}},
                                        "memory": {"some": {"avg10": 0.1}}}},
                                               min_memavailable=1, min_free=1)


def test_preflight_and_memory_or_free_space_stops_happen_before_fio_or_discard(tmp_path):
    lowfree = FakeHost(free=1)
    with pytest.raises(driver.StudyError, match="free space"):
        run_set(tmp_path / "free", "raw-t0", host=lowfree)
    assert lowfree.discards == [] and lowfree.fio.calls == []
    lowmem = FakeHost(available=1)
    with pytest.raises(driver.StudyError, match="MemAvailable"):
        run_set(tmp_path / "memory", "raw-t0", host=lowmem)
    assert lowmem.discards == [] and lowmem.fio.calls == []
    highmempsi = FakeHost(psi_mem=50)
    with pytest.raises(driver.StudyError, match="memory PSI exceeded"):
        run_set(tmp_path / "psi-memory", "file-ext4", host=highmempsi,
                target_type="ext4-file", target="/mnt/file")


def test_elapsed_active_and_write_meters_refuse_before_starting_more_work(tmp_path):
    now = driver.dt.datetime.now(driver.dt.timezone.utc)
    out = tmp_path / "budget"
    out.mkdir()
    state = driver.empty_state(now.isoformat(), "raw", "/dev/fake", "vda5")
    state["write_bytes"] = driver.WRITE_CAP - 1
    driver.write_json(out / driver.STATE_NAME, state)
    host = FakeHost()
    with pytest.raises(driver.StudyError, match="remaining write budget"):
        run_set(out, "raw-t0", host=host)
    assert host.discards == [] and host.fio.calls == []

    state["write_bytes"] = 0
    state["active_seconds"] = driver.ACTIVE_CAP_S
    driver.write_json(out / driver.STATE_NAME, state)
    with pytest.raises(driver.StudyError, match="active-time"):
        run_set(out, "raw-t0", host=host)
    state["active_seconds"] = 0
    state["started_utc"] = (now - driver.dt.timedelta(seconds=driver.ELAPSED_CAP_S + 1)).isoformat()
    driver.write_json(out / driver.STATE_NAME, state)
    with pytest.raises(driver.StudyError, match="12 hour elapsed"):
        run_set(out, "raw-t0", host=host)


def test_stop_rules_psi_three_sample_policy_is_applied_and_logged(tmp_path):
    host = FakeHost(psi_io=50)
    # A small cell-set fixture avoids a long fake schedule while exercising the controller's monitor guard.
    original = driver.CELL_SETS["file-ext4"]
    driver.CELL_SETS["file-ext4"] = ("ext4-file", ("veryquick",), "t9h")
    calls = {"n": 0}

    def three_samples(job_path, runtime_s, sample):
        calls["n"] += 1
        with pytest.raises(driver.StudyError, match="three consecutive"):
            sample()
            sample()
            sample()
        raise driver.StudyError("fake fio stopped by monitor")

    host.execute_fio = three_samples
    try:
        with pytest.raises(driver.StudyError, match="fake fio stopped"):
            run_set(tmp_path, "file-ext4", host=host, target_type="ext4-file", target="/tmp/target")
    finally:
        driver.CELL_SETS["file-ext4"] = original
    assert calls["n"] == 1
    rows = [json.loads(line) for line in (tmp_path / "monitor.jsonl").read_text().splitlines()]
    assert len(rows) == 3


def test_schedule_adaptive_rules_and_plan_rendering(capsys):
    plan = driver.plan_document()
    assert plan["meter"]["base_schedule_bytes"] + plan["meter"]["adaptive_reserve_bytes"] == driver.WRITE_CAP
    assert plan["meter"]["base_schedule_bytes"] == driver.base_schedule_write_cap()
    assert driver.CELL_SETS["raw-t0"][1] == ("standard", "quick", "veryquick")
    assert driver.CELL_SETS["raw-t3h"][1] == ("veryquick", "quick")
    assert driver.CELL_SETS["raw-t6h"][1] == ("quick", "veryquick", "standard")
    assert driver.CELL_SETS["raw-t9h"][1] == ("veryquick", "quick")
    driver.print_plan()
    output = capsys.readouterr().out
    assert "500 GB" in output and "t0+6h" in output
    assert driver.adaptive_repeat_action(0.12, 0.05, 1) == "proven"
    assert driver.adaptive_repeat_action(0.02, 0.05, 1) == "repeat-once"
    assert driver.adaptive_repeat_action(0.02, 0.05, 2) == "no-effect"
    assert driver.adaptive_repeat_action(0.08, 0.05, 1) == "repeat"
    assert driver.adaptive_repeat_action(0.08, 0.05, 3) == "small"
    assert driver.noise_band([1.0, 1.02]) == 0.05
    assert driver.noise_band([100, 130]) == pytest.approx(0.30)
    assert driver.noise_band([100, 110], latency=True) == pytest.approx(0.10)
    assert driver.needs_extra_repeat([100, 100, 100, 130], 100)
    assert not driver.needs_extra_repeat([100, 100, 100, 101], 100)
    assert not driver.needs_extra_repeat([100, 101, 100], 100)
    assert not driver.needs_extra_repeat([100, 100, 100, 130], 0)
    with pytest.raises(driver.StudyError, match="positive noise"):
        driver.adaptive_repeat_action(0.1, 0, 1)
    with pytest.raises(driver.StudyError, match="completed run"):
        driver.adaptive_repeat_action(0.1, 0.1, 0)


def test_plan_reads_existing_meter_and_refuses_malformed_state(tmp_path):
    state_path = tmp_path / driver.STATE_NAME
    state = driver.empty_state(driver.dt.datetime.now(driver.dt.timezone.utc).isoformat(),
                               "raw", "/dev/vda5", "vda5")
    state["write_bytes"] = 123
    state["active_seconds"] = 12.5
    driver.write_json(state_path, state)
    plan = driver.plan_document(state_path)
    assert plan["meter"]["used_bytes"] == 123
    assert plan["time"]["active_seconds_used"] == 12.5
    driver.write_json(state_path, {"schema": 9})
    with pytest.raises(driver.StudyError, match="cannot be used"):
        driver.plan_document(state_path)
    for malformed in ({"schema": driver.SCHEMA, "write_bytes": True, "active_seconds": 0},
                      {"schema": driver.SCHEMA, "write_bytes": 0, "active_seconds": float("nan")}):
        driver.write_json(state_path, malformed)
        with pytest.raises(driver.StudyError, match="cannot be used"):
            driver.plan_document(state_path)
    state_path.write_text("{", encoding="utf-8")
    with pytest.raises(driver.StudyError, match="unreadable"):
        driver.plan_document(state_path)


def test_recorded_data_replay_analyze_rung_tables(tmp_path):
    cells = tmp_path / "cells"
    cells.mkdir()
    fixture = driver.HERE / "fixtures/replay.jsonl"
    for index, line in enumerate(fixture.read_text(encoding="utf-8").splitlines()):
        (cells / f"{index:02}.json").write_text(line + "\n", encoding="utf-8")
    report = driver.analysis(tmp_path)
    raw = report["tables"]["raw"]["rbps"]
    assert raw["V"] == pytest.approx(0.20)
    assert raw["cells"]["quick"]["e"] == pytest.approx(0.10)
    assert raw["cells"]["quick"]["run_values"][0] == {
        "slot": "t0", "value": 105.0, "x_ref": 100.0}
    assert report["tables"]["raw"]["wrandiops"]["T"] == "±50%"
    assert report["tables"]["raw"]["write_iops_cap"]["T"] == "-30/+20%"
    assert report["tables"]["raw"]["read_iops_cap"]["C"] is None
    assert report["tables"]["raw"]["rewrite_ratio"]["C"] is None
    random_rw = report["tables"]["raw"]["read_write_rand_iops_ratio"]
    assert random_rw["cells"]["quick"]["e"] == pytest.approx(0)
    assert random_rw["cells"]["veryquick"]["verdict"] == "—"
    rand_seq = report["tables"]["raw"]["read_rand_seq_iops_ratio"]
    assert rand_seq["cells"]["quick"]["e"] == pytest.approx(0.10)
    assert rand_seq["cells"]["veryquick"]["verdict"] == "—"
    ext4 = report["tables"]["ext4-file"]["rbps"]
    assert ext4["file_bias_vs_raw"]["quick"] == pytest.approx(-0.25)
    assert ext4["file_bias_raw_spread"]["quick"] == pytest.approx(120 / 105 - 1)
    markdown = driver.render_analysis(report)
    assert "usable for:" in markdown
    assert "not usable for:" in markdown
    assert "btrfs-file" in markdown
    assert driver.adaptive_raw_repeat(tmp_path, "quick", 4) == (False, False)
    assert driver.adaptive_raw_repeat(tmp_path, "veryquick", 4) == (False, False)
    with pytest.raises(driver.StudyError, match="needs 4 completed"):
        run_set(tmp_path / "empty", "raw-t12h", host=FakeHost())


def test_load_results_skips_ratio_without_both_values_or_nonzero_denominator(tmp_path):
    cells = tmp_path / "cells"
    cells.mkdir()
    rows = (
        {"cell": "seq1m-read", "number": "rbps", "slot": "t0", "value": 100.0},
        {"cell": "seq1m-write", "number": "wbps", "slot": "t1h", "value": 50.0},
        {"cell": "seq1m-read", "number": "rbps", "slot": "t2h", "value": 100.0},
        {"cell": "seq1m-write", "number": "wbps", "slot": "t2h", "value": 0.0},
    )
    for index, row in enumerate(rows):
        cells.joinpath(f"{index}.json").write_text(json.dumps({
            **row, "target_type": "raw", "rung": "quick"}), encoding="utf-8")
    assert not any(row["number"] == "read_write_bps_ratio" for row in driver.load_results(tmp_path))


def test_analysis_refuses_missing_and_corrupt_recordings(tmp_path):
    with pytest.raises(driver.StudyError, match="no recorded"):
        driver.analysis(tmp_path)
    cells = tmp_path / "cells"
    cells.mkdir()
    (cells / "broken.json").write_text("{", encoding="utf-8")
    with pytest.raises(driver.StudyError, match="invalid cell result"):
        driver.analysis(tmp_path)
    (cells / "broken.json").write_text(json.dumps({"target_type": "raw", "slot": "t0",
        "rung": "quick", "number": "rbps", "cell": "read", "value": "fast"}), encoding="utf-8")
    with pytest.raises(driver.StudyError, match="malformed measurement"):
        driver.analysis(tmp_path)
    (cells / "broken.json").write_text(json.dumps({"target_type": "raw", "slot": "t0",
        "rung": "quick", "number": "rbps", "cell": "", "value": 1}), encoding="utf-8")
    with pytest.raises(driver.StudyError, match="malformed identity"):
        driver.analysis(tmp_path)


def test_adaptive_action_uses_raw_standard_control_noise(tmp_path):
    cells = tmp_path / "cells"
    cells.mkdir()
    rows = [("standard", "t0", 100.0), ("standard", "t6h", 100.0),
            ("quick", "t0", 100.0), ("quick", "t3h", 100.0),
            ("quick", "t6h", 100.0), ("quick", "t9h", 110.0)]
    for index, (rung, slot, value) in enumerate(rows):
        row = {"target_type": "raw", "slot": slot, "rung": rung, "number": "rbps",
               "cell": "seq1m-read", "value": value}
        (cells / f"{index}.json").write_text(json.dumps(row), encoding="utf-8")
    result = driver.analysis(tmp_path)["tables"]["raw"]["rbps"]["cells"]["quick"]
    assert result["e"] == pytest.approx(0.1)
    assert result["repeat_action"] == "verdict-fixed"


def test_adaptive_repeat_requires_complete_slots_and_repeats_moving_error(tmp_path):
    host = FakeHost()
    for cell_set in ("raw-t0", "raw-t3h", "raw-t6h", "raw-t9h"):
        run_set(tmp_path, cell_set, host=host)
    assert driver.adaptive_raw_repeat(tmp_path, "quick", 4) == (True, False)
    cells = tmp_path / "cells"
    decoded = [(path, json.loads(path.read_text())) for path in cells.glob("*.json")]
    fourth = next(path for path, row in decoded if row.get("target_type") == "raw" and
                  row.get("slot") == "t9h" and row.get("rung") == "quick" and
                  row.get("number") == "rbps")
    row = json.loads(fourth.read_text())
    row["value"] *= 1.3
    fourth.write_text(json.dumps(row), encoding="utf-8")
    assert driver.adaptive_raw_repeat(tmp_path, "quick", 4) == (True, True)
    run_set(tmp_path, "raw-t12h", host=host)
    decoded = [(path, json.loads(path.read_text())) for path in cells.glob("*.json")]
    fifth = next(path for path, row in decoded if row.get("target_type") == "raw" and
                 row.get("slot") == "t12h" and row.get("rung") == "quick" and
                 row.get("number") == "rbps")
    row = json.loads(fifth.read_text())
    row["value"] *= 1.5
    fifth.write_text(json.dumps(row), encoding="utf-8")
    assert driver.adaptive_raw_repeat(tmp_path, "quick", 5) == (True, True)
    run_set(tmp_path, "raw-t12h2", host=host)


def write_cgroup(root: Path, *, io_max="", io_weight="default 100\n8:0 100\n", io_latency="",
                 qos="enable=0\n", model=""):
    for directory in (root, root / "bench"):
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "io.max").write_text(io_max, encoding="utf-8")
        (directory / "io.weight").write_text(io_weight, encoding="utf-8")
        (directory / "io.latency").write_text(io_latency, encoding="utf-8")
    (root / "io.cost.qos").write_text(qos, encoding="utf-8")
    (root / "io.cost.model").write_text(model, encoding="utf-8")


def test_io_control_snapshot_checks_full_cgroup_chain_and_iocost(tmp_path, monkeypatch):
    root = tmp_path / "cg"
    write_cgroup(root, io_max="\n8:0 rbps=max wbps=max\n",
                 io_weight="default 100\n  \n8:0 100\n")
    snapshot = driver.io_control_snapshot(cgroup_root=root, cgroup_text="0::/bench\n")
    assert [row["path"] for row in snapshot["cgroups"]] == [str(root), str(root / "bench")]
    assert snapshot["io_cost"]["io.cost.qos"] == "enable=0"
    assert driver.cgroup_path("0::/bench\n") == "/bench"
    assert driver._io_max_active("8:0 rbps=max wbps=max\n") is False
    assert driver._io_max_active("") is False
    assert driver._io_max_active("\n") is False
    assert driver._io_max_active("8:0 rbps=100\n") is True
    with pytest.raises(driver.StudyError, match="malformed io.max"):
        driver._io_max_active("badline\n")
    with pytest.raises(driver.StudyError, match="malformed io.max setting"):
        driver._io_max_active("8:0 rbps\n")
    with pytest.raises(driver.StudyError, match="cannot resolve"):
        driver.cgroup_path("1:name=systemd:/foo\n")
    with pytest.raises(driver.StudyError, match="cannot resolve"):
        driver.cgroup_path("0::/../escape\n")

    original_read_text = Path.read_text

    def proc_cgroup(path, *args, **kwargs):
        if path == Path("/proc/self/cgroup"):
            return "0::/bench\n"
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", proc_cgroup)
    assert driver.io_control_snapshot(cgroup_root=root)["io_cost"]["io.cost.qos"] == "enable=0"


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"io_max": "8:0 rbps=100\n"}, "active io.max"),
        ({"io_max": "8:0\n"}, "malformed io.max line"),
        ({"io_max": "8:0 foo=max\n"}, "unknown or malformed io.max"),
        ({"io_weight": "default 200\n"}, "non-default io.weight"),
        ({"io_weight": "bad\n"}, "malformed io.weight"),
        ({"io_latency": "8:0 target=1000\n"}, "active io.latency"),
        ({"qos": "enable=1\n"}, "qos is enabled"),
        ({"qos": "enable=0 enable=0\n"}, "must explicitly report enable=0"),
        ({"model": "8:0 ctrl=user\n"}, "model is configured"),
    ],
)
def test_io_control_snapshot_refuses_active_or_unknown_controls(tmp_path, kwargs, message):
    root = tmp_path / "cg"
    write_cgroup(root, **kwargs)
    with pytest.raises(driver.StudyError, match=message):
        driver.io_control_snapshot(cgroup_root=root, cgroup_text="0::/bench\n")


def test_io_control_snapshot_refuses_missing_files_and_nonzero_qos_with_disabled_flag(tmp_path):
    root = tmp_path / "cg"
    write_cgroup(root, qos="")
    with pytest.raises(driver.StudyError, match="must explicitly report enable=0"):
        driver.io_control_snapshot(cgroup_root=root, cgroup_text="0::/bench\n")
    (root / "bench/io.latency").unlink()
    with pytest.raises(driver.StudyError, match="cannot read required"):
        driver.io_control_snapshot(cgroup_root=root, cgroup_text="0::/bench\n")
    write_cgroup(root, qos="enable=0 enable=1\n")
    with pytest.raises(driver.StudyError, match="qos is enabled"):
        driver.io_control_snapshot(cgroup_root=root, cgroup_text="0::/bench\n")
    write_cgroup(root, io_weight="8:0 100\n")
    with pytest.raises(driver.StudyError, match="no explicit default"):
        driver.io_control_snapshot(cgroup_root=root, cgroup_text="0::/bench\n")
    write_cgroup(root)
    (root / "io.cost.model").unlink()
    with pytest.raises(driver.StudyError, match="io.cost state.*missing"):
        driver.io_control_snapshot(cgroup_root=root, cgroup_text="0::/bench\n")


def test_state_schema_resume_identity_and_timestamp_guards(tmp_path):
    path = tmp_path / driver.STATE_NAME
    initial = driver.load_state(path, "raw", "/dev/vda5", "vda5")
    assert initial["write_bytes"] == 0 and initial["cells"] == {}
    driver.write_json(path, initial)
    resumed = driver.load_state(path, "raw", "/dev/vda5", "vda5")
    assert resumed["targets"]["raw"] == {"target": "/dev/vda5", "device": "vda5"}
    with pytest.raises(driver.StudyError, match="target identity changed"):
        driver.load_state(path, "raw", "/dev/other", "vda5")
    with pytest.raises(driver.StudyError, match="unsupported"):
        driver.write_json(path, {"schema": 9})
        driver.load_state(path, "raw", "/dev/vda5", "vda5")
    driver.write_json(path, {"schema": True})
    with pytest.raises(driver.StudyError, match="unsupported"):
        driver.load_state(path, "raw", "/dev/vda5", "vda5")
    with pytest.raises(driver.StudyError, match="missing required"):
        driver.write_json(path, {"schema": driver.SCHEMA})
        driver.load_state(path, "raw", "/dev/vda5", "vda5")
    malformed = {"schema": driver.SCHEMA, "started_utc": "bad", "write_bytes": 0,
                 "active_seconds": 0, "targets": {}, "cells": {}}
    driver.write_json(path, malformed)
    with pytest.raises(driver.StudyError, match="timestamp is malformed"):
        driver.state_elapsed(driver.load_state(path, "raw", "/dev/vda5", "vda5"))
    naive = {**malformed, "started_utc": "2026-11-10T00:00:00"}
    with pytest.raises(driver.StudyError, match="must include a timezone"):
        driver.state_elapsed(naive)
    future = {**malformed, "started_utc": (driver.dt.datetime.now(driver.dt.timezone.utc) +
                                             driver.dt.timedelta(hours=1)).isoformat()}
    with pytest.raises(driver.StudyError, match="in the future"):
        driver.state_elapsed(future)
    malformed_counts = {**malformed,
                        "started_utc": driver.dt.datetime.now(driver.dt.timezone.utc).isoformat(),
                        "write_bytes": True}
    driver.write_json(path, malformed_counts)
    with pytest.raises(driver.StudyError, match="fields are malformed"):
        driver.load_state(path, "raw", "/dev/vda5", "vda5")
    malformed_active = {**malformed_counts, "write_bytes": 0, "active_seconds": True}
    driver.write_json(path, malformed_active)
    with pytest.raises(driver.StudyError, match="fields are malformed"):
        driver.load_state(path, "raw", "/dev/vda5", "vda5")
    malformed_reservations = {**malformed, "started_utc": driver.dt.datetime.now(driver.dt.UTC).isoformat(),
                              "write_bytes": 1, "active_seconds": 0,
                              "targets": {"raw": {"target": "/dev/vda5", "device": "vda5"}},
                              "cells": {}, "write_reservations": {"cell": [2]}}
    driver.write_json(path, malformed_reservations)
    with pytest.raises(driver.StudyError, match="exceed the write meter"):
        driver.load_state(path, "raw", "/dev/vda5", "vda5")
    malformed_target = {**malformed_reservations, "write_bytes": 0, "write_reservations": {},
                        "targets": {"ext4-file": {"target": "/mnt/file", "device": "sdb"}}}
    driver.write_json(path, malformed_target)
    with pytest.raises(driver.StudyError, match="share the requested backing device"):
        driver.load_state(path, "raw", "/dev/vda5", "vda5")
    corrupt = tmp_path / "corrupt.json"
    corrupt.write_text("{", encoding="utf-8")
    with pytest.raises(driver.StudyError, match="unreadable"):
        driver.load_state(corrupt, "raw", "/dev/vda5", "vda5")


def test_target_and_cellset_validation_and_writer_cap_allocator():
    assert driver.parse_run_cellset("file-ext4", None) == ("ext4-file", ("quick", "veryquick"), "t9h")
    assert driver.parse_run_cellset("part-c-repeat", None) == ("raw", ("part-c",), "t4h")
    with pytest.raises(driver.StudyError, match="requires target type"):
        driver.parse_run_cellset("file-ext4", "btrfs-file")
    with pytest.raises(driver.StudyError, match="unknown cell-set"):
        driver.parse_run_cellset("unknown", None)
    with pytest.raises(driver.StudyError, match="unknown rung"):
        driver.rung_jobs("thorough")
    assert driver.bound_write_caps([driver.Job("read", "read", 4096, 1, 1, "r")], GIB)[0].write_cap == 0
    capped = driver.bound_write_caps([driver.Job("a", "write", 4096, 1, 1, "w", 8),
                                      driver.Job("b", "write", 4096, 1, 1, "w", 8)], 10)
    assert [job.write_cap for job in capped] == [8, 2]
    shared = driver.bound_write_caps([driver.Job("a", "write", 4096, 1, 1, "w"),
                                      driver.Job("b", "write", 4096, 1, 1, "w")], 10)
    assert sum(job.write_cap for job in shared) == 10
    assert driver.bound_write_caps([driver.Job("a", "write", 4096, 1, 1, "w", 1)], 10)[0].write_cap == 1
    with pytest.raises(driver.StudyError, match="zero-duration cells"):
        driver.bound_write_caps([driver.Job("zero", "write", 4096, 1, 0, "w")], 10)


def test_run_refuses_a_target_extent_outside_its_assigned_region(tmp_path):
    host = FakeHost()
    with pytest.raises(driver.StudyError, match="assigned study region"):
        run_set(tmp_path, "raw-t0", host=host, offset=24 * GIB, size=8 * GIB)
    assert getattr(host, "validated_target", None) is None
    with pytest.raises(driver.StudyError, match="assigned study region"):
        run_set(tmp_path / "part-c", "part-c", host=host, offset=0, size=24 * GIB)


def test_render_job_path_guard_and_reader_modes(tmp_path, monkeypatch):
    read = driver.Job("read", "read", 4096, 1, 5, "rrandiops")
    rendered = driver.render_job(read, target="/dev/vda5", offset=0, size=GIB,
                                 log_prefix=str(tmp_path / "log"))
    assert "time_based=1" in rendered and "io_size=" not in rendered and "direct=1" in rendered
    rate = driver.render_job(read, target="/dev/vda5", offset=0, size=GIB,
                             log_prefix=str(tmp_path / "log"), rate_iops=100)
    assert "rate_iops=100" in rate
    with pytest.raises(driver.StudyError, match="unsafe path"):
        driver.render_job(read, target="/dev/bad path", offset=0, size=GIB,
                          log_prefix=str(tmp_path / "log"))
    with pytest.raises(driver.StudyError, match="unsafe fio template value"):
        driver.render_job(read, target="/dev/vda5\n", offset=0, size=GIB,
                          log_prefix=str(tmp_path / "log"))
    malformed = tmp_path / "bad.fio"
    malformed.write_text("[job]\ndirect=1\ntime_based=1\nio_size=4096\n", encoding="utf-8")
    monkeypatch.setattr(driver, "JOB_TEMPLATE", malformed)
    writer = driver.Job("write", "write", 4096, 1, 1, "wbps", 1024)
    with pytest.raises(driver.StudyError, match="not volume bounded"):
        driver.render_job(writer, target="/dev/vda5", offset=0, size=GIB,
                          log_prefix=str(tmp_path / "writer"))


def test_parse_fio_result_refuses_shape_missing_stats_percentiles_and_overwrite(tmp_path):
    job = driver.Job("write", "write", 4096, 1, 5, "wbps", 100)
    good = {"jobs": [{"write": {"io_bytes": 50, "bw_bytes": 10, "iops": 2}}]}
    assert driver.parse_fio_values(good, job) == (10.0, 50)
    assert driver.parse_fio_values({"jobs": [{"read": {"io_bytes": 50, "bw_bytes": 10,
                                                           "iops": 2}}]},
                                   driver.Job("read", "read", 4096, 1, 5, "rrandiops")) == (2.0, 50)
    latency = driver.Job("latency", "read", 4096, 1, 5, "rlat")
    assert driver.parse_fio_values({"jobs": [{"read": {"io_bytes": 1,
        "clat_ns": {"percentile": {"99.000000": 20}}}}]}, latency) == (20.0, 1)
    percentiles = {"jobs": [{"read": {"io_bytes": 1, "clat_ns": {"percentile": {
        "95.000000": 19, "99.000000": 29}}}}]}
    assert driver.parse_fio_values(percentiles, latency) == (19.0, 1)
    assert driver.parse_fio_values(percentiles,
        driver.Job("under-load", "read", 4096, 32, 5, "latency_under_load_read")) == (29.0, 1)
    with pytest.raises(driver.StudyError, match="exactly one"):
        driver.parse_fio_values({"jobs": []}, job)
    with pytest.raises(driver.StudyError, match="expected direction"):
        driver.parse_fio_values({"jobs": [{"read": {}}]}, job)
    with pytest.raises(driver.StudyError, match="more than"):
        driver.parse_fio_values({"jobs": [{"write": {"io_bytes": 101}}]}, job)
    with pytest.raises(driver.StudyError, match="malformed io_bytes"):
        driver.parse_fio_values({"jobs": [{"write": {"io_bytes": True}}]}, job)
    with pytest.raises(driver.StudyError, match="no clat_ns"):
        driver.parse_fio_values({"jobs": [{"read": {"io_bytes": 1, "clat_ns": {}}}]}, latency)
    with pytest.raises(driver.StudyError, match="malformed latency"):
        driver.parse_fio_values({"jobs": [{"read": {"io_bytes": 1,
            "clat_ns": {"percentile": {"95.000000": float("nan")}}}}]}, latency)
    with pytest.raises(driver.StudyError, match="malformed IOPS"):
        driver.parse_fio_values({"jobs": [{"read": {"io_bytes": 1, "iops": float("inf")}}]},
                                driver.Job("read", "read", 4096, 1, 5, "rrandiops"))
    with pytest.raises(driver.StudyError, match="malformed bandwidth"):
        driver.parse_fio_values({"jobs": [{"write": {"io_bytes": 1, "bw_bytes": -1}}]}, job)
    driver.write_json(tmp_path / "a.json", {"ok": True})
    driver.append_jsonl(tmp_path / "a.jsonl", {"n": 1})
    assert json.loads((tmp_path / "a.json").read_text()) == {"ok": True}
    assert json.loads((tmp_path / "a.jsonl").read_text()) == {"n": 1}


def test_parse_and_reduction_helpers_cover_kernel_counters_and_bad_inputs(tmp_path):
    row = "8 0 vda 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18\n"
    parsed = driver.parse_diskstats("short\n8 0 bad x 0 0 0 0 0 0 0 0 0 0\n" + row)
    assert parsed["vda"]["discards_completed"] == 12
    assert parsed["vda"]["flushes_completed"] == 16
    assert driver.parse_psi("\ninvalid nope\nfull avg60=3\nsome avg10=1.25 avg60=2 total=4\n") == {
        "some": {"avg10": 1.25, "avg60": 2.0, "total": 4.0}}
    with pytest.raises(driver.StudyError, match="MemAvailable"):
        driver.parse_meminfo("not a meminfo row\nMemTotal: 10 kB\n")
    assert driver.parse_meminfo("MemTotal: 100 kB\nMemAvailable: 50 kB\n")["MemAvailable"] == 50 * 1024
    assert driver.parse_fio_log("head\n0,2\ninvalid,value\n3,4,5\n", bandwidth=True)[0]["value"] == 2048
    assert len(driver.parse_fio_log("0,3\n", bandwidth=False)) == 1
    assert driver.summarize_series({"bw": []})["bw"]["steady_tail"] is None
    assert driver.median([]) is None
    assert driver.median([1, 3]) == 2
    assert driver.spread([]) is None
    assert driver.spread([0, 1]) is None
    assert driver.spread([2, 4]) == 1
    sample = tmp_path / "proc"
    (sample / "pressure").mkdir(parents=True)
    (sample / "diskstats").write_text(row, encoding="utf-8")
    (sample / "pressure/io").write_text("some avg10=0.1 total=1\n", encoding="utf-8")
    (sample / "pressure/memory").write_text("some avg10=0.2 total=2\n", encoding="utf-8")
    (sample / "meminfo").write_text("MemTotal: 100 kB\nMemAvailable: 50 kB\n", encoding="utf-8")
    assert driver.monitor_sample("vda", proc=sample)["diskstats"]["flushes_completed"] == 16
    with pytest.raises(driver.StudyError, match="no row"):
        driver.monitor_sample("missing", proc=sample)
    (sample / "diskstats").write_text("8 0 vda 1 2 3 4 5 6 7 8 9 10 11 12\n", encoding="utf-8")
    with pytest.raises(driver.StudyError, match="lacks discard/flush"):
        driver.monitor_sample("vda", proc=sample)


def test_size_parsing_and_cli_modes(tmp_path, capsys, monkeypatch):
    assert driver.parse_size("24GiB") == 24 * GIB
    assert driver.parse_size("1MB") == 1_000_000
    assert driver.parse_size("0B") == 0
    with pytest.raises(driver.argparse.ArgumentTypeError):
        driver.parse_size("-1B")
    assert driver.main(["plan", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["limits"]["write_bytes"] == driver.WRITE_CAP
    recorded = tmp_path / "replay"
    cells = recorded / "cells"
    cells.mkdir(parents=True)
    row = {"target_type": "raw", "slot": "t0", "rung": "standard", "number": "rbps",
           "cell": "seq1m-read", "value": 10}
    (cells / "one.json").write_text(json.dumps(row), encoding="utf-8")
    dest = tmp_path / "analysis.md"
    assert driver.main(["analyze", str(recorded), "--output", str(dest)]) == 0
    assert "## raw" in dest.read_text(encoding="utf-8")
    assert driver.main(["analyze", str(recorded), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["tables"]["raw"]["rbps"]
    monkeypatch.setattr(driver, "run_study", lambda *args, **kwargs: {"cell_set": "raw-t0"})
    assert driver.main(["run", "raw-t0", "--target", "/dev/fake", "--device", "vda5",
                        "--offset", "0B", "--size", "24GiB", "--out", str(tmp_path),
                        "--max-io-psi", "20", "--max-memory-psi", "20", "--min-memavailable", "1GiB",
                        "--min-free", "1GiB", "--confirm-quiet-window"]) == 0
    assert json.loads(capsys.readouterr().out)["cell_set"] == "raw-t0"
    monkeypatch.setattr(driver, "run_study", lambda *args, **kwargs: (_ for _ in ()).throw(driver.StudyError("refused")))
    assert driver.main(["run", "raw-t0", "--target", "/dev/fake", "--device", "vda5",
                        "--offset", "0B", "--size", "24GiB", "--out", str(tmp_path),
                        "--max-io-psi", "20", "--max-memory-psi", "20", "--min-memavailable", "1GiB",
                        "--min-free", "1GiB", "--confirm-quiet-window"]) == 2
    assert "refused" in capsys.readouterr().err


def test_module_entrypoint_executes_plan(monkeypatch, capsys):
    monkeypatch.setattr(driver.sys, "argv", [str(driver.HERE / "driver.py"), "plan", "--json"])
    with pytest.raises(SystemExit) as exit_info:
        runpy.run_path(str(driver.HERE / "driver.py"), run_name="__main__")
    assert exit_info.value.code == 0
    assert json.loads(capsys.readouterr().out)["meter"]["used_bytes"] == 0


def test_state_reservation_and_meter_guards(tmp_path):
    now = driver.dt.datetime.now(driver.dt.UTC).isoformat()
    base = driver.empty_state(now, "raw", "/dev/vda5", "vda5")
    invalid_states = [
        ({"write_reservations": []}, "write reservations are malformed"),
        ({"write_reservations": {"cell": []}}, "write reservations are malformed"),
        ({"write_reservations": {"cell": [True]}, "write_bytes": 1},
         "write reservations are malformed"),
        ({"write_reservations": {"cell": [2]}, "write_bytes": 1},
         "exceed the write meter"),
        ({"active_reservations": []}, "active-time reservations are malformed"),
        ({"active_reservations": {"cell": [False]}, "active_seconds": 1},
         "active-time reservations are malformed"),
        ({"active_reservations": {"cell": [2]}, "active_seconds": 1},
         "exceed the active-time meter"),
        ({"targets": {"other": {"target": "", "device": "vda5"}}},
         "target identity is malformed"),
    ]
    path = tmp_path / driver.STATE_NAME
    for update, message in invalid_states:
        state = {**base, **update}
        driver.write_json(path, state)
        with pytest.raises(driver.StudyError, match=message):
            driver.load_state(path, "raw", "/dev/vda5", "vda5")

    with pytest.raises(driver.StudyError, match="write reservations are malformed"):
        driver.reserve_write({"write_reservations": []}, "cell", 1)
    with pytest.raises(driver.StudyError, match="write reservations are malformed"):
        driver.reserve_write({"write_reservations": {"cell": [True]}}, "cell", 1)
    with pytest.raises(driver.StudyError, match="recorded fio writes are malformed"):
        driver.settle_write({"write_bytes": 0}, "cell", True)
    state = {"write_bytes": 0, "write_reservations": {"cell": []}}
    with pytest.raises(driver.StudyError, match="write reservations are malformed"):
        driver.settle_write(state, "cell", 0)
    state = {"write_bytes": 0}
    driver.settle_write(state, "cell", 5)
    assert state["write_bytes"] == 5

    with pytest.raises(driver.StudyError, match="active-time reservations are malformed"):
        driver.reserve_active({"active_reservations": []}, "cell", 1)
    with pytest.raises(driver.StudyError, match="active-time reservations are malformed"):
        driver.reserve_active({"active_reservations": {"cell": [True]}}, "cell", 1)
    with pytest.raises(driver.StudyError, match="active-time reservations are malformed"):
        driver.settle_active({"active_reservations": {"cell": []}}, "cell", 0)
    with pytest.raises(driver.StudyError, match="active-time reservations are malformed"):
        driver.settle_active({"active_reservations": {}}, "cell", float("nan"))


def test_host_target_and_fsync_failure_edges(tmp_path, monkeypatch):
    host_api = driver.LocalHost()
    monkeypatch.setattr(driver, "io_control_snapshot", lambda: {"guarded": True})
    assert host_api.io_control_snapshot() == {"guarded": True}
    monkeypatch.setattr(driver.time, "monotonic", lambda: 12.5)
    sleeps = []
    monkeypatch.setattr(driver.time, "sleep", lambda seconds: sleeps.append(seconds))
    assert host_api.now() == 12.5
    host_api.sleep(0.25)
    assert sleeps == [0.25]

    target = tmp_path / "file"
    target.write_bytes(b"x" * 4096)
    host = driver.LocalHost(sys=tmp_path / "missing-sys")
    with pytest.raises(driver.StudyError, match="target cannot be inspected"):
        host.validate_target(str(tmp_path / "absent"), "ext4-file", 0, 4096, "vda5")
    with pytest.raises(driver.StudyError, match="must be a block device"):
        host.validate_target(str(target), "raw", 0, 4096, "vda5")
    with pytest.raises(driver.StudyError, match="cannot map diskstats device"):
        host.validate_target(str(target), "ext4-file", 0, 4096, "vda5")

    dev = tmp_path / "sys/class/block/vda5/dev"
    dev.parent.mkdir(parents=True)
    dev.write_text("8:5\n", encoding="utf-8")
    monkeypatch.setattr(driver.stat, "S_ISBLK", lambda mode: True)

    def too_small(argv, **kwargs):
        if argv[0] == "findmnt":
            return SimpleNamespace(returncode=1, stdout="", stderr="")
        return SimpleNamespace(returncode=0, stdout="1024\n", stderr="")

    monkeypatch.setattr(driver.subprocess, "run", too_small)
    with pytest.raises(driver.StudyError, match="smaller than the requested extent"):
        host.validate_target(str(target), "raw", 0, 4096, "vda5")

    proc_cgroup = tmp_path / "proc/self/cgroup"
    proc_cgroup.parent.mkdir(parents=True)
    proc_cgroup.write_text("0::/system.slice/docker-" + "c" * 64 + ".scope\n", encoding="utf-8")
    fsync_host = driver.LocalHost(proc=tmp_path / "proc")
    monkeypatch.setenv("CGROUP_PARENT_DEV_BACKGROUND", "dev-background.slice")
    monkeypatch.setattr(driver, "REPO", tmp_path)
    monkeypatch.setattr(driver.subprocess, "run", lambda *args, **kwargs:
        SimpleNamespace(returncode=0, stdout="loaded\n", stderr=""))
    with pytest.raises(driver.StudyError, match="wrapper is missing"):
        fsync_host.start_fsync(tmp_path)
    bpf_out = tmp_path / "owned-output"
    bpf_out.mkdir()
    monkeypatch.setattr(driver.subprocess, "run", lambda *args, **kwargs:
        SimpleNamespace(returncode=1, stdout="", stderr="stop rejected"))
    with pytest.raises(driver.StudyError, match="stop failed.*stop rejected"):
        fsync_host.stop_fsync(("owned-collector", bpf_out))


def test_run_study_snapshot_history_optional_standard_and_repeat_edges(tmp_path):
    def state_with(snapshots):
        state = driver.empty_state(driver.dt.datetime.now(driver.dt.UTC).isoformat(),
                                   "ext4-file", "/mnt/file", "vda5")
        state["io_control_runs"] = snapshots
        return state

    for index, (snapshots, message) in enumerate([
            ([], "snapshot history is malformed"),
            ({"file-ext4": "bad"}, "snapshot history is malformed"),
            ({"file-ext4": {"before": None}}, "snapshot history is malformed"),
            ({"file-ext4": {"before": {}, "after": []}}, "snapshot history is malformed")]):
        out = tmp_path / f"snapshot-{index}"
        out.mkdir()
        driver.write_json(out / driver.STATE_NAME, state_with(snapshots))
        with pytest.raises(driver.StudyError, match=message):
            run_set(out, "file-ext4", host=FakeHost(), target_type="ext4-file", target="/mnt/file")

    host = FakeHost()
    run_set(tmp_path / "optional", "raw-t0", host=host)
    run_set(tmp_path / "optional", "file-ext4", host=host,
            target_type="ext4-file", target="/mnt/file")
    with pytest.raises(driver.StudyError, match="standard run is not needed"):
        run_set(tmp_path / "optional", "file-ext4-standard", host=host,
                target_type="ext4-file", target="/mnt/file")
    with pytest.raises(driver.StudyError, match="quick and veryquick results must complete"):
        driver.file_standard_needed(tmp_path / "empty", "ext4-file")
    only_files = tmp_path / "file-only"
    run_set(only_files, "file-ext4", host=FakeHost(), target_type="ext4-file", target="/mnt/file")
    with pytest.raises(driver.StudyError, match="quick results are required"):
        driver.file_standard_needed(only_files, "ext4-file")

    repeat_root = tmp_path / "repeat"
    cells = repeat_root / "cells"
    cells.mkdir(parents=True)
    index = 0

    def record(rung, slots):
        nonlocal index
        for slot in slots:
            for job in driver.rung_jobs(rung):
                row = {"target_type": "raw", "slot": slot, "rung": rung,
                       "number": job.value, "cell": job.name,
                       "value": 0.0 if rung == "standard" and job.value == "rbps" else 100.0}
                (cells / f"{index}.json").write_text(json.dumps(row), encoding="utf-8")
                index += 1
            trim = {"target_type": "raw", "slot": slot, "rung": rung,
                    "number": "trim_state", "cell": "trim-state", "value": 1}
            (cells / f"{index}.json").write_text(json.dumps(trim), encoding="utf-8")
            index += 1

    record("standard", ("t0", "t6h"))
    prior_slots = ("t0", "t3h", "t6h", "t9h")
    record("quick", prior_slots)
    record("veryquick", prior_slots)
    assert driver.adaptive_raw_repeat(repeat_root, "quick", 4) == (True, False)
    with pytest.raises(driver.StudyError, match="skipped:.*20 percent"):
        run_set(repeat_root, "raw-t12h", host=FakeHost())
    with pytest.raises(driver.StudyError, match="needs 5 completed"):
        run_set(repeat_root, "raw-t12h2", host=FakeHost())


def test_resume_refuses_broken_links_and_adopts_valid_orphan_results(tmp_path, monkeypatch):
    jobs = [
        driver.Job("rate-read-q32", "randread", 4096, 32, 1, "read_iops_cap"),
        driver.Job("rate-write-q32", "randwrite", 4096, 32, 1, "write_iops_cap", 100),
    ]
    monkeypatch.setitem(driver.CELL_SETS, "file-ext4", ("ext4-file", ("quick",), "t9h"))
    monkeypatch.setattr(driver, "rung_jobs", lambda rung, part_c=False: jobs)
    read_key = driver.cell_key("ext4-file", "t9h", "quick", jobs[0])
    write_key = driver.cell_key("ext4-file", "t9h", "quick", jobs[1])
    trim_job = driver.Job("trim-state", "read", 4096, 1, 0, "trim_state")
    trim_key = driver.cell_key("ext4-file", "t9h", "quick", trim_job)

    def save_state(out, cells=None):
        out.mkdir(parents=True, exist_ok=True)
        state = driver.empty_state(driver.dt.datetime.now(driver.dt.UTC).isoformat(),
                                   "ext4-file", "/mnt/file", "vda5")
        state["cells"] = cells or {}
        driver.write_json(out / driver.STATE_NAME, state)
        return out / "cells"

    expected_read = re.sub(r"[^A-Za-z0-9_.-]", "_", read_key) + ".json"
    expected_write = re.sub(r"[^A-Za-z0-9_.-]", "_", write_key) + ".json"

    out = tmp_path / "missing-cell"
    save_state(out, {read_key: expected_read})
    with pytest.raises(driver.StudyError, match="points to missing result"):
        run_set(out, "file-ext4", host=FakeHost(), target_type="ext4-file", target="/mnt/file")

    out = tmp_path / "wrong-cell"
    cells = save_state(out, {read_key: expected_read})
    cells.mkdir(exist_ok=True)
    driver.write_json(cells / expected_read, {"cell_key": "wrong", "value": 1})
    with pytest.raises(driver.StudyError, match="does not match planned cell"):
        run_set(out, "file-ext4", host=FakeHost(), target_type="ext4-file", target="/mnt/file")

    out = tmp_path / "missing-trim"
    save_state(out, {trim_key: re.sub(r"[^A-Za-z0-9_.-]", "_", trim_key) + ".json"})
    with pytest.raises(driver.StudyError, match="points to missing trim result"):
        run_set(out, "file-ext4", host=FakeHost(), target_type="ext4-file", target="/mnt/file")

    def orphan(out, key, name, *, write_bytes=0, duration_s=0, cell_key=None):
        cells = save_state(out)
        cells.mkdir(exist_ok=True)
        driver.write_json(cells / name, {"cell_key": key if cell_key is None else cell_key,
            "value": 100, "write_bytes": write_bytes, "duration_s": duration_s,
            "estimates": {"bw": {"early_10s": 99}}})

    out = tmp_path / "orphan-wrong"
    orphan(out, read_key, expected_read, cell_key="wrong")
    with pytest.raises(driver.StudyError, match="orphan result does not match"):
        run_set(out, "file-ext4", host=FakeHost(), target_type="ext4-file", target="/mnt/file")
    out = tmp_path / "orphan-bytes"
    orphan(out, read_key, expected_read, write_bytes=True)
    with pytest.raises(driver.StudyError, match="malformed write bytes"):
        run_set(out, "file-ext4", host=FakeHost(), target_type="ext4-file", target="/mnt/file")
    out = tmp_path / "orphan-duration"
    orphan(out, read_key, expected_read, duration_s=True)
    with pytest.raises(driver.StudyError, match="malformed duration"):
        run_set(out, "file-ext4", host=FakeHost(), target_type="ext4-file", target="/mnt/file")

    out = tmp_path / "orphan-valid"
    cells = save_state(out)
    cells.mkdir(exist_ok=True)
    for key, name, byte_count in ((read_key, expected_read, 0), (write_key, expected_write, 50)):
        record = {"cell_key": key, "value": 100, "write_bytes": byte_count, "duration_s": 0}
        if key == write_key:
            record["estimates"] = {"bw": {"early_10s": 99}}
        driver.write_json(cells / name, record)
    host = FakeHost()
    outcome = run_set(out, "file-ext4", host=host, target_type="ext4-file", target="/mnt/file")
    state = json.loads((out / driver.STATE_NAME).read_text(encoding="utf-8"))
    assert outcome["results"] == 3 and state["write_bytes"] == 50
    assert host.fio.calls == []


def test_run_study_remaining_active_and_elapsed_limits_stop_before_fio(tmp_path, monkeypatch):
    read_job = driver.Job("one-read", "read", 4096, 1, 1, "rbps", wait_before_s=1)
    monkeypatch.setitem(driver.CELL_SETS, "file-ext4", ("ext4-file", ("quick",), "t9h"))
    monkeypatch.setattr(driver, "rung_jobs", lambda rung, part_c=False: [read_job])
    monkeypatch.setattr(driver, "state_elapsed", lambda state: 0.0)

    def make_state(out, *, active=0.0, writes=0):
        out.mkdir(parents=True, exist_ok=True)
        state = driver.empty_state(driver.dt.datetime.now(driver.dt.UTC).isoformat(),
                                   "ext4-file", "/mnt/file", "vda5")
        state["active_seconds"] = active
        state["write_bytes"] = writes
        driver.write_json(out / driver.STATE_NAME, state)
        return out

    for name, active, message in (
            ("cell-active", driver.ACTIVE_CAP_S - driver.FIO_DRAIN_RESERVE_S / 2, "active-I/O cap"),
            ("raw-discard", driver.ACTIVE_CAP_S - driver.FIO_DRAIN_RESERVE_S / 2, "cap would be exceeded by discard")):
        out = tmp_path / name
        make_state(out, active=active)
        with pytest.raises(driver.StudyError, match=message):
            run_set(out, "raw-t0" if name == "raw-discard" else "file-ext4", host=FakeHost(),
                    target_type=None if name == "raw-discard" else "ext4-file",
                    target="/dev/fake" if name == "raw-discard" else "/mnt/file")

    elapsed = tmp_path / "cell-elapsed"
    make_state(elapsed)
    monkeypatch.setattr(driver, "state_elapsed",
                        lambda state: driver.ELAPSED_CAP_S - driver.FIO_DRAIN_RESERVE_S / 2)
    with pytest.raises(driver.StudyError, match="12 hour elapsed study limit"):
        run_set(elapsed, "file-ext4", host=FakeHost(), target_type="ext4-file", target="/mnt/file")

    invalid_writer = driver.Job("no-cap", "write", 4096, 1, 1, "wbps", 0)
    monkeypatch.setattr(driver, "state_elapsed", lambda state: 0.0)
    monkeypatch.setattr(driver, "rung_jobs", lambda rung, part_c=False: [invalid_writer])
    with pytest.raises(driver.StudyError, match="has no io_size"):
        run_set(tmp_path / "no-cap", "file-ext4", host=FakeHost(),
                target_type="ext4-file", target="/mnt/file")
    monkeypatch.setattr(driver, "rung_jobs", lambda rung, part_c=False: [read_job])

    def wait_case(name, elapsed_after_wait, message):
        out = make_state(tmp_path / name)
        calls = {"n": 0}

        def elapsed(state):
            calls["n"] += 1
            if calls["n"] <= 2:
                return 0.0
            if calls["n"] == 3 and message.startswith("before"):
                return driver.ELAPSED_CAP_S - 1
            if calls["n"] >= 4:
                return elapsed_after_wait
            return 0.0

        monkeypatch.setattr(driver, "state_elapsed", elapsed)
        with pytest.raises(driver.StudyError, match=message):
            run_set(out, "file-ext4", host=FakeHost(), target_type="ext4-file", target="/mnt/file")

    wait_case("wait-before", 0.0, "before required idle wait")
    wait_case("wait-after", driver.ELAPSED_CAP_S + 1, "12 hour elapsed study limit reached")
    wait_case("runtime-zero", driver.ELAPSED_CAP_S - driver.FIO_DRAIN_RESERVE_S / 2,
              "active-I/O or 12 hour elapsed")

    class LongFio(FakeHost):
        def execute_fio(self, job_path, runtime_s, sample):
            payload, _duration = self.fio.run(job_path, max_runtime=runtime_s)
            return payload, runtime_s + driver.FIO_DRAIN_RESERVE_S + 1

    monkeypatch.setattr(driver, "state_elapsed", lambda state: 0.0)
    with pytest.raises(driver.StudyError, match="fio duration is outside"):
        run_set(tmp_path / "duration", "file-ext4", host=LongFio(),
                target_type="ext4-file", target="/mnt/file")

    c_job = driver.Job("c-pass1-seq", "write", 1024**2, 32, 1, "c_seq_bps", 10,
                       extent_bytes=8 * GIB, discard_before=True)
    monkeypatch.setattr(driver, "rung_jobs", lambda rung, part_c=False: [c_job])

    class FullTimeoutDiscard(FakeHost):
        def discard(self, target, kind, offset, size, timeout=None, sample=None):
            return timeout

    with pytest.raises(driver.StudyError, match="active-I/O cap would be exceeded"):
        run_set(tmp_path / "post-discard-cap", "part-c", host=FullTimeoutDiscard(),
                offset=24 * GIB, size=8 * GIB)

    load_job = driver.Job("load-read-30", "randread", 4096, 32, 1,
                          "latency_under_load_read_30", rate_fraction=0.3)
    monkeypatch.setitem(driver.CELL_SETS, "file-ext4", ("ext4-file", ("standard",), "t9h"))
    monkeypatch.setattr(driver, "rung_jobs", lambda rung, part_c=False: [load_job])
    with pytest.raises(driver.StudyError, match="lacks its preceding QD32 rate"):
        run_set(tmp_path / "load-without-rate", "file-ext4", host=FakeHost(),
                target_type="ext4-file", target="/mnt/file")

    class ChangedSnapshot(FakeHost):
        def __init__(self):
            super().__init__()
            self.snapshot_number = 0

        def io_control_snapshot(self):
            self.snapshot_number += 1
            return {"number": self.snapshot_number}

    with pytest.raises(driver.StudyError, match="changed during the study"):
        run_set(tmp_path / "changed-snapshot", "file-ext4", host=ChangedSnapshot(),
                target_type="ext4-file", target="/mnt/file")


def test_analysis_markdown_covers_special_rows_and_reason_lines(tmp_path):
    spec = driver.TOLERANCES["rbps"]
    assert driver.verdict({"e": None}, spec) == "—"
    assert driver.verdict({"e": 0.75, "min_deviation": 0.6, "max_deviation": 0.75}, spec) == "⚠"
    assert driver.verdict({"e": 1.2, "min_deviation": 0.6, "max_deviation": 1.2}, spec) == "✗"
    assert driver._reference_value({"slot": "t0"}, [], "rbps") is None
    no_reference = driver._row_error([{"slot": "t0", "value": 10}], [], "rbps", spec)
    assert no_reference["e"] is None and no_reference["verdict"] == "—"

    def cell(*, error=None, bias=None, sample_spread=None, verdict="—", **extra):
        return {"e": error, "bias": bias, "spread": sample_spread, "verdict": verdict, **extra}

    row = {"T": "±10%", "C_text": "none", "T_low": -0.1, "T_high": 0.1,
           "V": 0.5, "reason": "measured", "file_bias_vs_raw": {"quick": 0.2, "standard": 0.1},
           "file_bias_raw_spread": {"quick": None, "standard": 0.2},
           "label": "example number", "cells": {
               "veryquick": cell(verdict="—"),
               "quick": cell(error=0.2, bias=0.2, sample_spread=0.3, verdict="⚠"),
               "standard": cell(bias=0.0, sample_spread=0.0, verdict="✓", is_reference=True),
           }}
    result = {"tables": {"raw": {
        "rbps": row,
        "burst_64gb": {**row, "V": None, "file_bias_vs_raw": None,
            "cells": {"veryquick": cell(verdict="—"), "quick": cell(verdict="—"),
                      "standard": cell(verdict="✗", visible=False)}},
        "trim_state": {**row, "V": None, "file_bias_vs_raw": None,
            "cells": {"veryquick": cell(verdict="✓", value=False), "quick": cell(verdict="—"),
                      "standard": cell(verdict="—")}},
        "discard_recovery": {**row, "V": None, "file_bias_vs_raw": None,
            "cells": {"veryquick": cell(verdict="—"), "quick": cell(verdict="✗", value=0.5),
                      "standard": cell(verdict="—", value=None)}},
    }}, "part_c": {"status": "not reproduced", "rewrite_ratio": 0.5,
        "rewrite_threshold": 0.7, "raw_quick_spread": None,
        "idle_ratio": None, "recovery_ratio": None}}
    rendered = driver.render_analysis(result)
    assert "moving target dominates" in rendered
    assert "file bias: quick +20.0% vs raw at same slot" in rendered
    assert "standard +10.0% vs raw at same slot (raw band 20.0%)" in rendered
    assert "not visible ✗" in rendered and "discard unsupported ✓" in rendered
    assert "recovery" in rendered and "Pass-4/pass-1: 50.0%" in rendered
    assert "Idle probe: —" in rendered and "discard recovery at +10m: —" in rendered

    bad_root = tmp_path / "analysis-missing-field"
    bad_root.mkdir()
    (bad_root / "cells").mkdir()
    (bad_root / "cells/bad.json").write_text(json.dumps({"target_type": "raw"}), encoding="utf-8")
    with pytest.raises(driver.StudyError, match="missing required fields"):
        driver.load_results(bad_root)
