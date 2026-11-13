"""Fake-fio and replay coverage for the standalone IBX study controller."""
from __future__ import annotations

import errno
import builtins
import json
import os
import re
import runpy
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from studies.ibx import driver

GIB = driver.GIB
FAKE_IDENTITY = driver.TargetIdentity(32 * GIB, "f00d-1234")
HEALTHY = {
    "psi": {"io": {"some": {"avg10": 0.1}, "full": {"avg10": 0.0}},
            "memory": {"some": {"avg10": 0.1}, "full": {"avg10": 0.1}}},
    "meminfo": {"MemAvailable": 16 * GIB}, "free_bytes": 16 * GIB,
    "diskstats": {"writes_completed": 3, "sectors_written": 20, "flushes_completed": 1,
                   "discards_completed": 2, "sectors_discarded": 4},
}


class FakeFio:
    """Model fio's key stop semantics: time_based ignores io_size; otherwise io_size bounds bytes."""

    rate_bps = 1024**2
    bw_log_value = 1024

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
        bw_log_value = self.bw_log_value
        (Path(f"{prefix}_bw.1.log")).write_text(
            f"0,{bw_log_value}\n1000,{bw_log_value}\n", encoding="utf-8")
        (Path(f"{prefix}_iops.1.log")).write_text("0,100\n1000,100\n", encoding="utf-8")
        self.calls.append({"options": options, "bytes": done, "duration": duration})
        return payload, duration


class FakeHost:
    def __init__(self, *, psi_io=0.1, psi_mem=0.1, psi_mem_full=None,
                 available=16 * GIB, free=16 * GIB):
        self.fio = FakeFio()
        self.snapshot = {"io_cost": {"enable": 0}, "cgroups": []}
        self.psi_io = psi_io
        self.psi_mem = psi_mem
        self.psi_mem_full = psi_mem if psi_mem_full is None else psi_mem_full
        self.available = available
        self.free = free
        self.discards = []
        self.fsync_starts = []
        self.fsync_stops = []
        self.quiet_checked = 0
        self.clock = 0.0
        self.liveness_latency = 0.01
        self.liveness_responsive = True
        self.liveness_samples = []
        self.liveness_calls = 0
        self.events = []
        self.system_disk = {"hostname": "fixture-host", "target_disk": "vda",
                            "root_fs_devices": ["vda3"], "system_mounts_on_disk": [],
                            "sibling_swap_devices": [], "target_partuuid": FAKE_IDENTITY.partuuid,
                            "root_disk_shared": False, "system_disk_acknowledgement": None,
                            "acknowledgement_format": "fixture"}

    def is_root(self):
        return True

    def assert_quiet_window(self):
        self.quiet_checked += 1

    def validate_target(self, target, kind, offset, size, device, confirmation=None, *,
                        allow_system_disk_partition=False, system_disk_ack=None,
                        derive_system_disk_ack=False):
        self.validated_target = (target, kind, offset, size, device)
        self.validation_args = {"allow_system_disk_partition": allow_system_disk_partition,
                                "system_disk_ack": system_disk_ack,
                                "derive_system_disk_ack": derive_system_disk_ack}
        if kind == "raw":
            if confirmation != FAKE_IDENTITY:
                raise driver.StudyError("fake raw identity does not match acknowledgement")
            if self.system_disk["root_disk_shared"]:
                if not allow_system_disk_partition and not derive_system_disk_ack:
                    raise driver.StudyError(
                        "raw target is on the root disk or contains a running system filesystem")
                if (not derive_system_disk_ack and
                        system_disk_ack != self.system_disk["system_disk_acknowledgement"]):
                    raise driver.StudyError("system-disk acknowledgement does not match current host facts")
            elif (allow_system_disk_partition or system_disk_ack is not None) and not derive_system_disk_ack:
                raise driver.StudyError("system-disk acknowledgement applies only to a target on the running system disk")
            return driver.TargetValidation(FAKE_IDENTITY, dict(self.system_disk))
        return driver.TargetValidation(None)

    def io_control_snapshot(self):
        return self.snapshot

    def trim_state(self, device):
        return {"supported": True, "discard_max_bytes": 1024**3}

    def discard(self, target, kind, offset, size, timeout=None, sample=None):
        self.events.append("discard")
        self.discards.append((target, kind, offset, size, timeout))
        if sample is not None:
            sample()
        return 0.01

    def now(self):
        return self.clock

    def sleep(self, seconds):
        self.clock += seconds

    def sample(self, device, out, target=None):
        self.events.append("sample")
        return {**HEALTHY, "device": device,
                "psi": {"io": {"some": {"avg10": self.psi_io}},
                        "memory": {"some": {"avg10": self.psi_mem},
                                   "full": {"avg10": self.psi_mem_full}}},
                "meminfo": {"MemAvailable": self.available}, "free_bytes": self.free}

    def liveness_probe(self, max_latency_s):
        self.events.append("liveness")
        self.liveness_calls += 1
        sample = self.liveness_samples.pop(0) if self.liveness_samples else {
            "responsive": self.liveness_responsive, "latency_seconds": self.liveness_latency,
            "components": {"root_fsync_seconds": self.liveness_latency / 3,
                           "sshd_seconds": self.liveness_latency / 3,
                           "cockpit_seconds": self.liveness_latency / 3}}
        self.clock += float(sample.get("clock_advance_seconds", sample.get("latency_seconds", 0)))
        return {key: value for key, value in sample.items() if key != "clock_advance_seconds"}

    def wait_with_samples(self, seconds, sample):
        for _ in range(seconds):
            sample()
            self.clock += 1

    def execute_fio(self, job_path, runtime_s, sample):
        self.events.append("fio")
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
            size=24 * GIB, fsync_cell=None, dry_run=False,
            allow_system_disk_partition=False, system_disk_ack=None,
            probe_consecutive_samples=3, max_probe_latency_s=1.0,
            probe_interval_s=driver.DEFAULT_LIVENESS_PROBE_INTERVAL_S):
    return driver.run_study(name, target=target, device="vda5", out=tmp_path,
                            target_kind=target_type, offset=offset, size=size,
                            mem_psi_max=20, min_memavailable=2 * GIB,
                            min_free=4 * GIB, max_probe_latency_s=max_probe_latency_s,
                            probe_consecutive_samples=probe_consecutive_samples,
                            probe_interval_s=probe_interval_s,
                            host=host or FakeHost(), fsync_cell=fsync_cell,
                            quiet_window_confirmed=True,
                            confirm_target=f"{target}:{FAKE_IDENTITY.size_bytes}:{FAKE_IDENTITY.partuuid}"
                            if target_type in (None, "raw") else None,
                            allow_system_disk_partition=allow_system_disk_partition,
                            system_disk_ack=system_disk_ack, dry_run=dry_run)


def execute_liveness_script(*, banner=b"SSH-2.0-fixture\r\n", systemctl_state="active",
                            systemctl_status=0, docker_status=0, fsync_error=False):
    printed = []
    calls = []
    addresses = []
    clock = iter((0.0, 0.1, 0.2, 0.3, 0.4, 0.5))

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def settimeout(self, _timeout):
            return None

        def recv(self, _count):
            return banner

    def command(args, **_kwargs):
        calls.append(args)
        if args[:2] == ["systemctl", "show"]:
            return SimpleNamespace(returncode=systemctl_status, stdout=systemctl_state, stderr="")
        return SimpleNamespace(returncode=docker_status, stdout="", stderr="")

    fake_modules = {
        "json": json,
        "os": SimpleNamespace(write=lambda _fd, _value: None,
                               fsync=lambda _fd: (_ for _ in ()).throw(OSError("fsync failed"))
                               if fsync_error else None,
                               close=lambda _fd: None, unlink=lambda _path: None),
        "socket": SimpleNamespace(create_connection=lambda address, timeout:
                                   (addresses.append((address, timeout)) or Connection())),
        "subprocess": SimpleNamespace(run=command),
        "sys": SimpleNamespace(argv=["probe", "1", driver.LIVENESS_ROOT_DIRECTORY,
                                     driver.LIVENESS_SSH_HOST, str(driver.LIVENESS_SSH_PORT),
                                     driver.LIVENESS_COCKPIT_UNIT,
                                     driver.LIVENESS_COCKPIT_CONTAINER]),
        "tempfile": SimpleNamespace(mkstemp=lambda **_kwargs: (7, "/root/.ibx-liveness-test")),
        "time": SimpleNamespace(monotonic=lambda: next(clock)),
    }
    builtins_map = vars(builtins).copy()
    builtins_map["__import__"] = lambda name, *_args, **_kwargs: fake_modules[name]
    builtins_map["print"] = printed.append
    status = 0
    try:
        exec(driver.LIVENESS_PROBE_SCRIPT, {"__builtins__": builtins_map})
    except SystemExit as exc:
        status = exc.code
    return status, json.loads(printed[0]), calls, addresses


def seed_raw_t6h_evidence(out, *, quick=(100.0, 100.0), veryquick=(100.0, 100.0)):
    out.mkdir(parents=True)
    cells = out / "cells"
    cells.mkdir()
    for job in driver.rung_jobs("standard"):
        number = job.value
        value = 120_000.0 if number.startswith("latency_under_load_") else 100.0
        row = {"target_type": "raw", "slot": "t0", "rung": "standard",
               "cell": job.name, "number": number, "value": value}
        if job.name in ("rate-read-q32", "rate-write-q32"):
            direction = "read" if job.name == "rate-read-q32" else "write"
            row["fio"] = {"jobs": [{direction: {"clat_ns": {
                "percentile": {"99.000000": 120_000.0}}}}]}
        driver.write_json(cells / f"standard-{job.name}.json", row)
    for rung, values in (("quick", quick), ("veryquick", veryquick)):
        for slot, value in zip(("t0", "t3h"), values, strict=True):
            for job in driver.rung_jobs(rung):
                row = {
                    "target_type": "raw", "slot": slot, "rung": rung,
                    "cell": job.name,
                    "number": job.value,
                    "value": (value if job.name == "seq1m-read" else
                              120_000.0 if job.value.startswith("latency_under_load_") else 100.0),
                }
                if job.name in ("rate-read-q32", "rate-write-q32"):
                    direction = "read" if job.name == "rate-read-q32" else "write"
                    row["fio"] = {"jobs": [{direction: {"clat_ns": {
                        "percentile": {"99.000000": 120_000.0}}}}]}
                driver.write_json(cells / f"{rung}-{slot}-{job.name}.json", row)
            driver.write_json(cells / f"{rung}-{slot}-trim-state.json", {
                "target_type": "raw", "slot": slot, "rung": rung,
                "cell": "trim-state", "number": "trim_state", "value": 1,
            })
    state = driver.empty_state(driver.dt.datetime.now(driver.dt.UTC).isoformat(),
                               "raw", "/dev/fake", "vda5", FAKE_IDENTITY)
    driver.write_json(out / driver.STATE_NAME, state)


def test_fio_writer_is_volume_bounded_and_fake_models_time_based_io_size(tmp_path):
    writer = driver.Job("bounded", "write", 1024**2, 32, 10, "wbps", 4096)
    text = driver.render_job(writer, target="/dev/fake", offset=0, size=GIB,
                             log_prefix=str(tmp_path / "logs" / "one"))
    assert "direct=1" in text
    assert "io_size=4096" in text
    assert "time_based=1" not in text
    path = tmp_path / "bounded.fio"
    path.write_text(text, encoding="utf-8")
    (tmp_path / "logs").mkdir()
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
    canary = {job.name: job for job in quick}
    assert canary["fresh4k-write"].value == "canary_iops"
    assert canary["discard-recovery4k-write"].value == "discard_recovery_iops"
    assert canary["fresh4k-write"].write_cap > 0
    assert canary["discard-recovery4k-write"].write_cap > 0
    assert canary["fresh4k-write"].bs == canary["discard-recovery4k-write"].bs == 4096
    assert "rewrite-pass-3" in {job.name for job in standard}
    assert sum(job.write_cap for job in veryquick if job.writes) <= 15 * 10**9
    assert sum(job.write_cap for job in quick if job.writes) <= 26 * 10**9
    assert sum(job.write_cap for job in standard if job.writes) <= 100 * 10**9
    assert all(job.write_cap > 0 for job in standard if job.writes)
    for rung in ("veryquick", "quick", "standard"):
        assert all(job.read_written_extent for job in driver.rung_jobs(rung)
                   if job.rw in ("read", "randread"))


def test_run_resume_monitors_and_records_one_json_per_fio_cell(tmp_path):
    host = FakeHost()
    ack = "fixture-host:vda:vda3:f00d-1234"
    host.system_disk.update({"root_disk_shared": True, "system_mounts_on_disk": ["/", "/boot"],
                             "sibling_swap_devices": ["vda6"],
                             "system_disk_acknowledgement": ack})
    # Exercise a fill that reaches its write cap so read-extent checks cover
    # the maximum written extent for each rung.
    host.fio.rate_bps = 100 * GIB
    host.fio.bw_log_value = 1_000_000_000
    first = run_set(tmp_path, "raw-t0", host=host, allow_system_disk_partition=True,
                    system_disk_ack=ack)
    files = sorted((tmp_path / "cells").glob("*.json"))
    assert first["results"] == len(files) == len(host.fio.calls) + 3
    assert (tmp_path / "fio-logs").is_dir()
    assert len(host.discards) == 7
    assert host.quiet_checked == 1
    assert all("time_based=1" not in job["options"] or "io_size" not in job["options"]
               for job in host.fio.calls if job["options"]["rw"] in ("write", "randwrite"))
    assert all("io_size" in job["options"] for job in host.fio.calls
               if job["options"]["rw"] in ("write", "randwrite"))
    state = json.loads((tmp_path / driver.STATE_NAME).read_text(encoding="utf-8"))
    assert state["write_bytes"] == first["write_bytes"]
    assert first["run_manifest"] == state["run_manifest"]
    planned = driver.plan_document(tmp_path / driver.STATE_NAME)
    assert planned["run_manifest"] == state["run_manifest"]
    assert "--system-disk-ack" in planned["risk_note"]
    assert state["run_manifest"]["target_safety"]["raw"]["root_disk_shared"] is True
    assert state["run_manifest"]["stop_policy"]["io_psi"].startswith("advisory")
    assert state["io_control_runs"]["raw-t0"]["before"] == state["io_control_runs"]["raw-t0"]["after"] == host.snapshot
    fio_record = next(json.loads(path.read_text(encoding="utf-8")) for path in files
                      if '"fio"' in path.read_text(encoding="utf-8"))
    assert set(fio_record) >= {
        "fio", "fio_series", "estimates", "io_size", "write_bytes", "io_bytes", "run_manifest"}
    read_record = next(json.loads(path.read_text(encoding="utf-8")) for path in files
                       if json.loads(path.read_text(encoding="utf-8")).get("number") == "rbps")
    assert read_record["io_bytes"] > 0 and read_record["write_bytes"] == 0
    fill_records = [json.loads(path.read_text(encoding="utf-8")) for path in files
                    if json.loads(path.read_text(encoding="utf-8")).get("cell") == "fill"]
    filled_by_rung = {row["rung"]: row["write_bytes"] for row in fill_records}
    read_records = [json.loads(path.read_text(encoding="utf-8")) for path in files
                    if json.loads(path.read_text(encoding="utf-8")).get("read_extent_bytes") is not None]
    assert read_records
    assert all(row["read_extent_bytes"] == filled_by_rung[row["rung"]] for row in read_records)
    assert all(f"size={filled_by_rung[row['rung']]}" in
               (tmp_path / "jobs" / f"{row['cell_key'].replace('|', '_')}.fio").read_text()
               for row in read_records)
    monitor = [json.loads(line) for line in (tmp_path / "monitor.jsonl").read_text().splitlines()]
    assert monitor and {"flushes_completed", "discards_completed"} <= set(monitor[0]["diskstats"])
    assert monitor[0]["host_liveness"]["limit_seconds"] == 1.0
    report = driver.analysis(tmp_path)
    assert report["run_manifest"] == state["run_manifest"]
    rendered_report = driver.render_analysis(report)
    assert "Raw target risk: **acknowledged system-disk run**" in rendered_report
    assert f"System-disk drift-check token: `{ack}`" in rendered_report
    assert "sibling swap device(s): `vda6`" in rendered_report
    assert "D-IBX-002" in rendered_report
    assert "I/O PSI is recorded as advisory" in rendered_report
    calls = len(host.fio.calls)
    cached_rate_read = next(path for path in files
                            if json.loads(path.read_text(encoding="utf-8")).get("cell") == "rate-read-q1")
    cached_payload = json.loads(cached_rate_read.read_text(encoding="utf-8"))
    cached_payload.pop("estimates")
    driver.write_json(cached_rate_read, cached_payload)
    resumed = run_set(tmp_path, "raw-t0", host=host, allow_system_disk_partition=True,
                      system_disk_ack=ack)
    assert resumed["results"] == calls + 3
    assert len(host.fio.calls) == calls
    assert len(host.discards) == 7
    host.snapshot = {"changed": True}
    with pytest.raises(driver.StudyError, match="snapshot changed since the previous attempt"):
        run_set(tmp_path, "raw-t0", host=host, allow_system_disk_partition=True,
                system_disk_ack=ack)
    state = json.loads((tmp_path / driver.STATE_NAME).read_text(encoding="utf-8"))
    state["io_control_runs"]["raw-t0"] = "malformed"
    driver.write_json(tmp_path / driver.STATE_NAME, state)
    with pytest.raises(driver.StudyError, match="snapshot history is malformed"):
        run_set(tmp_path, "raw-t0", host=host, allow_system_disk_partition=True,
                system_disk_ack=ack)


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
                               "raw", "/dev/vda5", "vda5", FAKE_IDENTITY)
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


def test_part_c_positive_replay_and_repeat_gate(tmp_path, monkeypatch):
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
    monkeypatch.setattr(driver, "DROPPED_ADAPTIVE_REPEAT_CELL_SETS", ())
    run_set(repeat_root, "part-c-repeat", host=FakeHost(), offset=24 * GIB, size=8 * GIB)
    repeated = [json.loads(path.read_text()) for path in repeat_cells.glob("*.json")]
    assert any(row.get("cell_set") == "part-c-repeat" and row.get("slot") == "t4h"
               for row in repeated)
    idle_seq = json.loads((cells / "4.json").read_text())
    idle_seq["value"] = 95.0
    (cells / "4.json").write_text(json.dumps(idle_seq), encoding="utf-8")
    assert driver.part_c_assessment(tmp_path)["confirmed"] is False
    host = FakeHost()
    skipped = run_set(tmp_path, "part-c-repeat", host=host, offset=24 * GIB, size=8 * GIB)
    assert skipped["skipped"] is True and "did not confirm" in skipped["reason"]
    assert host.discards == [] and host.fio.calls == []
    assert not (tmp_path / driver.STATE_NAME).exists()
    empty = tmp_path / "empty"
    with pytest.raises(driver.StudyError, match="requires the first.*complete"):
        run_set(empty, "part-c-repeat", host=FakeHost(), offset=24 * GIB, size=8 * GIB)


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


def _trim_state_fixture(tmp_path):
    disk = tmp_path / "devices/block/vda"
    partition = disk / "vda5"
    (disk / "queue").mkdir(parents=True)
    partition.mkdir()
    (partition / "partition").write_text("5\n", encoding="utf-8")
    class_block = tmp_path / "class/block"
    class_block.mkdir(parents=True)
    (class_block / "vda").symlink_to(disk)
    (class_block / "vda5").symlink_to(partition)
    (disk / "queue/discard_max_bytes").write_text("4096\n", encoding="utf-8")
    return driver.LocalHost(sys=tmp_path), disk, partition


def test_local_trim_state_reads_explicit_sysfs_capability(tmp_path):
    host, disk, _partition = _trim_state_fixture(tmp_path)
    path = disk / "queue/discard_max_bytes"
    assert host.trim_state("vda5") == {"supported": True, "discard_max_bytes": 4096}
    assert host.trim_state("vda") == {"supported": True, "discard_max_bytes": 4096}
    path.write_text("0\n", encoding="utf-8")
    assert host.trim_state("vda5")["supported"] is False
    path.write_text("bad\n", encoding="utf-8")
    with pytest.raises(driver.StudyError, match="malformed"):
        host.trim_state("vda5")
    path.write_bytes(b"\xff")
    with pytest.raises(driver.StudyError, match="cannot read discard support for vda5"):
        host.trim_state("vda5")
    with pytest.raises(driver.StudyError, match="cannot resolve sysfs block device"):
        host.trim_state("missing")
    path.unlink()
    with pytest.raises(driver.StudyError, match="cannot read discard support for vda5"):
        host.trim_state("vda5")


def test_local_trim_state_refuses_non_directory_sysfs_target(tmp_path):
    target = tmp_path / "devices/block/not-a-directory"
    target.parent.mkdir(parents=True)
    target.write_text("not a block device", encoding="utf-8")
    class_block = tmp_path / "class/block"
    class_block.mkdir(parents=True)
    (class_block / "not-a-directory").symlink_to(target)
    host = driver.LocalHost(sys=tmp_path)
    with pytest.raises(driver.StudyError, match="sysfs block device not-a-directory is not a directory"):
        host.trim_state("not-a-directory")


def test_local_trim_state_refuses_unreadable_partition_identity(tmp_path, monkeypatch):
    host, _disk, partition = _trim_state_fixture(tmp_path)
    marker = partition / "partition"
    original_stat = Path.stat

    def unreadable(candidate, *args, **kwargs):
        if candidate == marker:
            raise PermissionError("fixture denies partition stat")
        return original_stat(candidate, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", unreadable)
    with pytest.raises(driver.StudyError, match="cannot inspect partition identity for vda5"):
        host.trim_state("vda5")


def test_local_trim_state_refuses_non_regular_partition_identity(tmp_path):
    host, _disk, partition = _trim_state_fixture(tmp_path)
    marker = partition / "partition"
    marker.unlink()
    marker.mkdir()
    with pytest.raises(driver.StudyError, match="partition identity for vda5 is malformed"):
        host.trim_state("vda5")


def _raw_target_fixture(tmp_path, monkeypatch, *, root_devno="8:99"):
    target = tmp_path / "dev/vda5"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"fake block node")
    sysroot = tmp_path / "sys"
    disk = sysroot / "devices/block/vda"
    part = disk / "vda5"
    sibling = disk / "vda1"
    root_disk = sysroot / "devices/block/root-disk"
    for path in (disk, part, sibling, root_disk):
        (path / "holders").mkdir(parents=True, exist_ok=True)
    (disk / "dev").write_text("8:0\n", encoding="utf-8")
    (part / "dev").write_text("0:0\n", encoding="utf-8")
    (part / "partition").write_text("5\n", encoding="utf-8")
    (sibling / "dev").write_text("8:1\n", encoding="utf-8")
    (sibling / "partition").write_text("1\n", encoding="utf-8")
    (root_disk / "dev").write_text("8:99\n", encoding="utf-8")
    dev_block = sysroot / "dev/block"
    dev_block.mkdir(parents=True)
    for devno, path in (("0:0", part), ("8:0", disk), ("8:1", sibling), ("8:99", root_disk)):
        (dev_block / devno).symlink_to(path)
    class_block = sysroot / "class/block"
    class_block.mkdir(parents=True)
    (class_block / "vda5").symlink_to(part)
    (class_block / "vda").symlink_to(disk)
    (class_block / "root-disk").symlink_to(root_disk)
    proc = tmp_path / "proc"
    for namespace in ("self", "1"):
        mount_dir = proc / namespace
        mount_dir.mkdir(parents=True)
        (mount_dir / "mountinfo").write_text(
            f"36 25 {root_devno} / / rw,relatime - ext4 /dev/root rw\n", encoding="utf-8")
    (proc / "swaps").write_text("Filename Type Size Used Priority\n", encoding="utf-8")

    def command(args, **kwargs):
        if args[0] == "blockdev":
            return SimpleNamespace(returncode=0, stdout=f"{FAKE_IDENTITY.size_bytes}\n", stderr="")
        if args[0] == "blkid":
            return SimpleNamespace(returncode=0, stdout=f"{FAKE_IDENTITY.partuuid}\n", stderr="")
        return SimpleNamespace(returncode=1, stdout="", stderr="unsupported fake command")

    monkeypatch.setattr(driver.stat, "S_ISBLK", lambda mode: True)
    real_open, real_close = driver.os.open, driver.os.close
    raw_target = str(target.resolve())

    def raw_open(path, *args, **kwargs):
        if os.path.realpath(os.fspath(path)) == raw_target:
            return 123
        return real_open(path, *args, **kwargs)

    def raw_close(fd):
        if fd == 123:
            return None
        return real_close(fd)

    monkeypatch.setattr(driver.os, "open", raw_open)
    monkeypatch.setattr(driver.os, "close", raw_close)
    monkeypatch.setattr(driver.subprocess, "run", command)
    return driver.LocalHost(proc=proc, sys=sysroot), target, part, sibling


def test_localhost_sysfs_mount_btrfs_and_swap_refusal_edges(tmp_path, monkeypatch):
    host = driver.LocalHost(proc=tmp_path / "proc", sys=tmp_path / "sys")
    with pytest.raises(driver.StudyError, match="cannot resolve sysfs block device"):
        host._sys_device("8:99")
    with pytest.raises(driver.StudyError, match="cannot read sysfs device number"):
        host._read_devno(tmp_path / "no-device-number")
    malformed = tmp_path / "malformed-device-number"
    malformed.mkdir()
    (malformed / "dev").write_text("x:y\n", encoding="utf-8")
    with pytest.raises(driver.StudyError, match="device number.*malformed"):
        host._read_devno(malformed)
    with pytest.raises(driver.StudyError, match="cannot inspect sysfs holders"):
        host._holders(tmp_path / "no-holders")
    unresolved = tmp_path / "unresolved-holder"
    (unresolved / "holders").mkdir(parents=True)
    (unresolved / "holders/missing").touch()
    with pytest.raises(driver.StudyError, match="cannot resolve holder missing"):
        host._holders(unresolved)

    disk = tmp_path / "sys/devices/block/vda"
    (disk / "holders").mkdir(parents=True)
    (disk / "dev").write_text("8:0\n", encoding="utf-8")
    (disk / "holders/vda").symlink_to(disk)
    class_block = tmp_path / "sys/class/block"
    class_block.mkdir(parents=True)
    (class_block / "vda").symlink_to(disk)
    assert host._holder_closure([disk, disk]) == {"8:0"}

    with pytest.raises(driver.StudyError, match="cannot read mount table"):
        host._mount_rows(tmp_path / "missing-mountinfo")
    malformed_mount = tmp_path / "malformed-mountinfo"
    malformed_mount.write_text("not a mount row\n", encoding="utf-8")
    with pytest.raises(driver.StudyError, match="mount table.*malformed"):
        host._mount_rows(malformed_mount)

    monkeypatch.setattr(driver.subprocess, "run", lambda *args, **kwargs:
                        SimpleNamespace(returncode=0, stdout="ext4 uuid nope\n", stderr=""))
    with pytest.raises(driver.StudyError, match="device number.*malformed"):
        host._findmnt_facts("/mnt/data")
    with pytest.raises(driver.StudyError, match="cannot establish btrfs UUID"):
        host._btrfs_members("not-a-uuid!", "/mnt/data")
    with pytest.raises(driver.StudyError, match="cannot resolve btrfs backing members"):
        host._btrfs_members("abc123", "/mnt/data")
    empty_devices = tmp_path / "sys/fs/btrfs/abc123/devices"
    empty_devices.mkdir(parents=True)
    with pytest.raises(driver.StudyError, match="btrfs backing members are missing"):
        host._btrfs_members("abc123", "/mnt/data")
    (empty_devices / "missing-member").symlink_to(tmp_path / "absent-member")
    with pytest.raises(driver.StudyError, match="cannot resolve btrfs member"):
        host._btrfs_members("abc123", "/mnt/data")
    (empty_devices / "missing-member").unlink()

    monkeypatch.setattr(host, "_findmnt_facts", lambda target: ("ext4", "uuid", "8:1"))
    with pytest.raises(driver.StudyError, match="filesystem identity changed"):
        host._mount_devnos({"fstype": "btrfs", "mountpoint": "/mnt/data", "devno": "8:1"})
    member = tmp_path / "sys/devices/block/member"
    member.mkdir(parents=True)
    (member / "dev").write_text("8:5\n", encoding="utf-8")
    (empty_devices / "member0").symlink_to(member)
    monkeypatch.setattr(host, "_findmnt_facts", lambda target: ("btrfs", "abc123", "0:0"))
    assert host._mount_devnos({"fstype": "btrfs", "mountpoint": "/mnt/data", "devno": "0:0"}) == {"8:5"}
    assert host._mount_devnos({"fstype": "ext4", "mountpoint": "/mnt/data", "devno": "8:1"}) == {"8:1"}

    with pytest.raises(driver.StudyError, match="cannot establish active swap devices"):
        host._swap_devnos()
    swaps = tmp_path / "proc/swaps"
    swaps.parent.mkdir(parents=True)
    swap_file = tmp_path / "swapfile"
    swap_file.touch()
    swaps.write_text(f"Filename Type Size Used Priority\n\n{swap_file} file 1 0 -2\n",
                     encoding="utf-8")
    monkeypatch.setattr(host, "_findmnt_facts", lambda target: ("ext4", "uuid", "8:1"))
    assert host._swap_devnos() == {f"{driver.os.major(swap_file.stat().st_dev)}:{driver.os.minor(swap_file.stat().st_dev)}"}
    swaps.write_text(f"Filename Type Size Used Priority\n{tmp_path / 'missing-swap'} partition 1 0 -2\n",
                     encoding="utf-8")
    with pytest.raises(driver.StudyError, match="cannot inspect active swap target"):
        host._swap_devnos()
    swaps.write_text(f"Filename Type Size Used Priority\n{swap_file} file 1 0 -2\n",
                     encoding="utf-8")
    monkeypatch.setattr(host, "_findmnt_facts", lambda target: ("btrfs", "abc123", "8:1"))
    assert "8:5" in host._swap_devnos()
    monkeypatch.setattr(driver.stat, "S_ISBLK", lambda mode: True)
    assert "0:0" in host._swap_devnos()


def test_local_ext4_target_requires_exact_file_extent_and_mount_identity(tmp_path, monkeypatch):
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
        return SimpleNamespace(returncode=0, stdout="ext4 1234abcd 8:5\n", stderr="")
    monkeypatch.setattr(driver.subprocess, "run", find_ext4)
    host.validate_target(str(target), "ext4-file", 0, 4096, "vda5")

    monkeypatch.setattr(driver.subprocess, "run", lambda *args, **kwargs:
        SimpleNamespace(returncode=0, stdout="btrfs 1234abcd 8:5\n", stderr=""))
    with pytest.raises(driver.StudyError, match="must be on ext4"):
        host.validate_target(str(target), "ext4-file", 0, 4096, "vda5")
    dev.write_text("bad\n", encoding="utf-8")
    with pytest.raises(driver.StudyError, match="device number.*malformed"):
        host.validate_target(str(target), "ext4-file", 0, 4096, "vda5")
    with pytest.raises(driver.StudyError, match="cannot establish filesystem identity"):
        dev.write_text("8:5\n", encoding="utf-8")
        monkeypatch.setattr(driver.subprocess, "run", lambda *args, **kwargs:
            SimpleNamespace(returncode=1, stdout="", stderr="not mounted"))
        host.validate_target(str(target), "ext4-file", 0, 4096, "vda5")


def _btrfs_target_fixture(tmp_path, monkeypatch):
    target = tmp_path / "target.dat"
    target.write_bytes(b"x" * 4096)
    sysroot = tmp_path / "sys"
    monitored = sysroot / "class/block/vda5/dev"
    monitored.parent.mkdir(parents=True)
    monitored.write_text("8:5\n", encoding="utf-8")
    uuid = "11111111-2222-3333-4444-555555555555"
    member = sysroot / "devices/block/vda/vda6"
    (member / "holders").mkdir(parents=True)
    (member / "dev").write_text("8:5\n", encoding="utf-8")
    devices = sysroot / "fs/btrfs" / uuid / "devices"
    devices.mkdir(parents=True)
    (devices / "1").symlink_to(member)
    monkeypatch.setattr(driver.subprocess, "run", lambda *args, **kwargs:
        SimpleNamespace(returncode=0, stdout=f"btrfs {uuid} 0:52\n", stderr=""))
    return driver.LocalHost(sys=sysroot), target, uuid, devices, member


def test_btrfs_target_resolves_anonymous_mount_device_to_sysfs_member(tmp_path, monkeypatch):
    host, target, _, _, _ = _btrfs_target_fixture(tmp_path, monkeypatch)
    host.validate_target(str(target), "btrfs-file", 0, 4096, "vda5")


def test_btrfs_target_refuses_member_that_differs_from_monitored_device(tmp_path, monkeypatch):
    host, target, _, _, member = _btrfs_target_fixture(tmp_path, monkeypatch)
    (member / "dev").write_text("8:6\n", encoding="utf-8")
    with pytest.raises(driver.StudyError, match="does not match monitored"):
        host.validate_target(str(target), "btrfs-file", 0, 4096, "vda5")


def test_btrfs_target_refuses_multiple_sysfs_members(tmp_path, monkeypatch):
    host, target, _, devices, _ = _btrfs_target_fixture(tmp_path, monkeypatch)
    second = tmp_path / "sys/devices/block/vda/vda7"
    (second / "holders").mkdir(parents=True)
    (second / "dev").write_text("8:7\n", encoding="utf-8")
    (devices / "2").symlink_to(second)
    with pytest.raises(driver.StudyError, match="single-device btrfs"):
        host.validate_target(str(target), "btrfs-file", 0, 4096, "vda5")


def test_btrfs_target_refuses_unreadable_member_list(tmp_path, monkeypatch):
    host, target, _, _, _ = _btrfs_target_fixture(tmp_path, monkeypatch)
    device_dir = tmp_path / "sys/fs/btrfs/11111111-2222-3333-4444-555555555555/devices"
    (device_dir / "1").unlink()
    with pytest.raises(driver.StudyError, match="backing members are missing"):
        host.validate_target(str(target), "btrfs-file", 0, 4096, "vda5")


def test_raw_target_refuses_missing_acknowledgement(tmp_path, monkeypatch):
    host, target, _, _ = _raw_target_fixture(tmp_path, monkeypatch)
    with pytest.raises(driver.StudyError, match="--confirm-target"):
        host.validate_target(str(target), "raw", 0, 4096, "vda5")


def test_raw_target_refuses_non_block_device(tmp_path, monkeypatch):
    host, target, _, _ = _raw_target_fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(driver.stat, "S_ISBLK", lambda mode: False)
    with pytest.raises(driver.StudyError, match="must be a block device"):
        host.validate_target(str(target), "raw", 0, 4096, "vda5", confirmation=FAKE_IDENTITY)


def test_target_confirmation_accepts_exact_identity():
    text = f"/dev/vda5:{FAKE_IDENTITY.size_bytes}:{FAKE_IDENTITY.partuuid}"
    assert driver.parse_target_confirmation(text, "/dev/vda5") == FAKE_IDENTITY
    with pytest.raises(driver.StudyError, match="--confirm-target"):
        driver.parse_target_confirmation(None, "/dev/vda5")


def test_target_confirmation_refuses_path_mismatch():
    with pytest.raises(driver.StudyError, match="exactly match PATH"):
        driver.parse_target_confirmation(
            f"/dev/vda:{FAKE_IDENTITY.size_bytes}:{FAKE_IDENTITY.partuuid}", "/dev/vda5")


def test_target_confirmation_refuses_invalid_size():
    with pytest.raises(driver.StudyError, match="exactly match PATH"):
        driver.parse_target_confirmation(f"/dev/vda5:0:{FAKE_IDENTITY.partuuid}", "/dev/vda5")


def test_target_confirmation_refuses_invalid_partuuid():
    with pytest.raises(driver.StudyError, match="exactly match PATH"):
        driver.parse_target_confirmation(f"/dev/vda5:{FAKE_IDENTITY.size_bytes}:unknown", "/dev/vda5")


def test_system_disk_ack_derivation_reads_facts_and_requires_shared_system_disk():
    host = FakeHost()
    host.system_disk.update({"root_disk_shared": True,
                             "system_disk_acknowledgement": "fixture-host:vda:vda3:f00d-1234"})
    ack = driver.derive_system_disk_ack(
        "/dev/fake", "vda5", f"/dev/fake:{FAKE_IDENTITY.size_bytes}:{FAKE_IDENTITY.partuuid}", host)
    assert ack == "fixture-host:vda:vda3:f00d-1234"
    assert host.validation_args["allow_system_disk_partition"] is True
    assert host.validation_args["derive_system_disk_ack"] is True
    host.system_disk["root_disk_shared"] = False
    with pytest.raises(driver.StudyError, match="does not share the running system disk"):
        driver.derive_system_disk_ack(
            "/dev/fake", "vda5", f"/dev/fake:{FAKE_IDENTITY.size_bytes}:{FAKE_IDENTITY.partuuid}", host)
    host.system_disk["root_disk_shared"] = True
    host.validate_target = lambda *args, **kwargs: driver.TargetValidation(
        driver.TargetIdentity(FAKE_IDENTITY.size_bytes + 1, FAKE_IDENTITY.partuuid),
        dict(host.system_disk))
    with pytest.raises(driver.StudyError, match="does not share the running system disk"):
        driver.derive_system_disk_ack(
            "/dev/fake", "vda5", f"/dev/fake:{FAKE_IDENTITY.size_bytes}:{FAKE_IDENTITY.partuuid}", host)
    host.validate_target = lambda *args, **kwargs: driver.TargetValidation(FAKE_IDENTITY, None)
    with pytest.raises(driver.StudyError, match="does not share the running system disk"):
        driver.derive_system_disk_ack(
            "/dev/fake", "vda5", f"/dev/fake:{FAKE_IDENTITY.size_bytes}:{FAKE_IDENTITY.partuuid}", host)
    host.validate_target = lambda *args, **kwargs: driver.TargetValidation(
        FAKE_IDENTITY, {"root_disk_shared": True})
    with pytest.raises(driver.StudyError, match="cannot derive system-disk acknowledgement"):
        driver.derive_system_disk_ack(
            "/dev/fake", "vda5", f"/dev/fake:{FAKE_IDENTITY.size_bytes}:{FAKE_IDENTITY.partuuid}", host)
    unprivileged = FakeHost()
    unprivileged.is_root = lambda: False
    with pytest.raises(driver.StudyError, match="requires root"):
        driver.derive_system_disk_ack(
            "/dev/fake", "vda5", f"/dev/fake:{FAKE_IDENTITY.size_bytes}:{FAKE_IDENTITY.partuuid}",
            unprivileged)


def test_system_disk_ack_cli_prints_fresh_acknowledgement(tmp_path, capsys, monkeypatch):
    host = FakeHost()
    host.system_disk.update({"root_disk_shared": True,
                             "system_disk_acknowledgement": "fixture-host:vda:vda3:f00d-1234"})
    monkeypatch.setattr(driver, "LocalHost", lambda: host)
    assert driver.main(["system-disk-ack", "--target", "/dev/fake", "--device", "vda5",
                        "--confirm-target",
                        f"/dev/fake:{FAKE_IDENTITY.size_bytes}:{FAKE_IDENTITY.partuuid}"]) == 0
    assert capsys.readouterr().out == "fixture-host:vda:vda3:f00d-1234\n"


def test_raw_target_accepts_exact_acknowledged_identity(tmp_path, monkeypatch):
    host, target, _, _ = _raw_target_fixture(tmp_path, monkeypatch)
    accepted = host.validate_target(str(target), "raw", 0, 4096, "vda5", confirmation=FAKE_IDENTITY)
    assert accepted.identity == FAKE_IDENTITY


def test_raw_target_refuses_device_number_that_differs_from_diskstats_device(tmp_path, monkeypatch):
    host, target, part, _ = _raw_target_fixture(tmp_path, monkeypatch)
    (part / "dev").write_text("8:5\n", encoding="utf-8")
    with pytest.raises(driver.StudyError, match="does not match monitored device"):
        host.validate_target(str(target), "raw", 0, 4096, "vda5", confirmation=FAKE_IDENTITY)


def test_raw_target_refuses_size_mismatch(tmp_path, monkeypatch):
    host, target, _, _ = _raw_target_fixture(tmp_path, monkeypatch, root_devno="8:1")
    acknowledgement = driver.system_disk_acknowledgement(
        driver.socket.gethostname(), "vda", ["vda1"], FAKE_IDENTITY.partuuid)
    with pytest.raises(driver.StudyError, match="raw target size does not match"):
        host.validate_target(str(target), "raw", 0, 4096, "vda5",
                             confirmation=driver.TargetIdentity(GIB + 1, FAKE_IDENTITY.partuuid),
                             allow_system_disk_partition=True, system_disk_ack=acknowledgement)


def test_raw_target_refuses_replaced_partuuid(tmp_path, monkeypatch):
    host, target, _, _ = _raw_target_fixture(tmp_path, monkeypatch, root_devno="8:1")
    def replaced_partition(args, **kwargs):
        if args[0] == "blkid":
            return SimpleNamespace(returncode=0, stdout="beef-9999\n", stderr="")
        return SimpleNamespace(returncode=0, stdout=f"{FAKE_IDENTITY.size_bytes}\n", stderr="")
    monkeypatch.setattr(driver.subprocess, "run", replaced_partition)
    acknowledgement = driver.system_disk_acknowledgement(
        driver.socket.gethostname(), "vda", ["vda1"], "beef-9999")
    with pytest.raises(driver.StudyError, match="PARTUUID does not match"):
        host.validate_target(str(target), "raw", 0, 4096, "vda5", confirmation=FAKE_IDENTITY,
                             allow_system_disk_partition=True, system_disk_ack=acknowledgement)


def test_raw_target_refuses_unprovable_device_identity_and_partition_topology(tmp_path, monkeypatch):
    def valid_command(args, **kwargs):
        if args[0] == "blockdev":
            return SimpleNamespace(returncode=0, stdout=f"{FAKE_IDENTITY.size_bytes}\n", stderr="")
        if args[0] == "blkid":
            return SimpleNamespace(returncode=0, stdout=f"{FAKE_IDENTITY.partuuid}\n", stderr="")
        return SimpleNamespace(returncode=1, stdout="", stderr="unsupported")

    host, target, _, _ = _raw_target_fixture(tmp_path / "size", monkeypatch)
    monkeypatch.setattr(driver.subprocess, "run", lambda *args, **kwargs:
                        SimpleNamespace(returncode=1, stdout="", stderr="unreadable"))
    with pytest.raises(driver.StudyError, match="cannot establish raw target size"):
        host.validate_target(str(target), "raw", 0, 4096, "vda5", confirmation=FAKE_IDENTITY)

    host, target, _, _ = _raw_target_fixture(tmp_path / "blkid-io", monkeypatch)
    def blkid_io_error(args, **kwargs):
        if args[0] == "blkid":
            raise OSError("blkid unavailable")
        return valid_command(args, **kwargs)
    monkeypatch.setattr(driver.subprocess, "run", blkid_io_error)
    with pytest.raises(driver.StudyError, match="cannot establish raw target PARTUUID"):
        host.validate_target(str(target), "raw", 0, 4096, "vda5", confirmation=FAKE_IDENTITY)

    host, target, _, _ = _raw_target_fixture(tmp_path / "blkid-empty", monkeypatch)
    def empty_partuuid(args, **kwargs):
        if args[0] == "blkid":
            return SimpleNamespace(returncode=2, stdout="", stderr="no PARTUUID")
        return valid_command(args, **kwargs)
    monkeypatch.setattr(driver.subprocess, "run", empty_partuuid)
    with pytest.raises(driver.StudyError, match="cannot establish raw target PARTUUID"):
        host.validate_target(str(target), "raw", 0, 4096, "vda5", confirmation=FAKE_IDENTITY)

    host, target, _, _ = _raw_target_fixture(tmp_path / "device-map", monkeypatch)
    (host.sys / "class/block/vda5").unlink()
    monkeypatch.setattr(driver.subprocess, "run", valid_command)
    with pytest.raises(driver.StudyError, match="cannot map diskstats device"):
        host.validate_target(str(target), "raw", 0, 4096, "vda5", confirmation=FAKE_IDENTITY)

    host, target, _, _ = _raw_target_fixture(tmp_path / "partitions", monkeypatch)
    disk_sys = host.sys / "devices/block/vda"
    original_iterdir = Path.iterdir
    def fail_disk_listing(path):
        if path == disk_sys:
            raise OSError("partition listing denied")
        return original_iterdir(path)
    monkeypatch.setattr(Path, "iterdir", fail_disk_listing)
    monkeypatch.setattr(driver.subprocess, "run", valid_command)
    with pytest.raises(driver.StudyError, match="cannot inspect partitions of raw target disk"):
        host.validate_target(str(target), "raw", 0, 4096, "vda5", confirmation=FAKE_IDENTITY)

    monkeypatch.setattr(Path, "iterdir", original_iterdir)
    host, target, _, _ = _raw_target_fixture(tmp_path / "root-map", monkeypatch)
    (host.sys / "dev/block/8:99").unlink()
    monkeypatch.setattr(driver.subprocess, "run", valid_command)
    with pytest.raises(driver.StudyError, match="running system's root block device"):
        host.validate_target(str(target), "raw", 0, 4096, "vda5", confirmation=FAKE_IDENTITY)


def test_raw_target_refuses_whole_disk_with_partitions(tmp_path, monkeypatch):
    host, target, _, _ = _raw_target_fixture(tmp_path, monkeypatch)
    disk = host.sys / "devices/block/vda"
    link = host.sys / "dev/block/0:0"
    link.unlink()
    link.symlink_to(disk)
    with pytest.raises(driver.StudyError, match="whole disks are refused"):
        host.validate_target(str(target), "raw", 0, 4096, "vda5", confirmation=FAKE_IDENTITY,
                             allow_system_disk_partition=True, system_disk_ack="fixture-drift-token")


def test_raw_target_refuses_mounted_partition(tmp_path, monkeypatch):
    host, target, part, _ = _raw_target_fixture(tmp_path, monkeypatch, root_devno="8:1")
    mount_line = f"37 25 0:0 / /mnt/study rw,relatime - ext4 /dev/vda5 rw\n"
    expected = driver.system_disk_acknowledgement(
        driver.socket.gethostname(), "vda", ["vda1"], FAKE_IDENTITY.partuuid)
    for namespace in ("self", "1"):
        (host.proc / namespace / "mountinfo").write_text(
            (host.proc / namespace / "mountinfo").read_text(encoding="utf-8") + mount_line,
            encoding="utf-8")
    with pytest.raises(driver.StudyError, match="partition is mounted"):
        host.validate_target(str(target), "raw", 0, 4096, "vda5", confirmation=FAKE_IDENTITY,
                             allow_system_disk_partition=True, system_disk_ack=expected)


def test_raw_target_refuses_swap_active_partition(tmp_path, monkeypatch):
    host, target, _, _ = _raw_target_fixture(tmp_path, monkeypatch)
    for namespace in ("self", "1"):
        (host.proc / namespace / "mountinfo").write_text(
            f"36 25 8:99 / / rw,relatime - ext4 /dev/root rw\n", encoding="utf-8")
    (host.proc / "swaps").write_text(f"Filename Type Size Used Priority\n{target} partition 1 0 -2\n",
                                      encoding="utf-8")
    with pytest.raises(driver.StudyError, match="swap-active"):
        host.validate_target(str(target), "raw", 0, 4096, "vda5", confirmation=FAKE_IDENTITY)


def test_raw_target_refuses_partition_with_holders(tmp_path, monkeypatch):
    host, target, part, _ = _raw_target_fixture(tmp_path, monkeypatch, root_devno="8:1")
    holder = host.sys / "devices/block/dm-0"
    (holder / "holders").mkdir(parents=True)
    (holder / "dev").write_text("253:0\n", encoding="utf-8")
    (host.sys / "class/block/dm-0").symlink_to(holder)
    (part / "holders/dm-0").symlink_to(holder)
    assert host._holders(part) == {"253:0"}
    acknowledgement = driver.system_disk_acknowledgement(
        driver.socket.gethostname(), "vda", ["vda1"], FAKE_IDENTITY.partuuid)
    with pytest.raises(driver.StudyError, match="active holders"):
        host.validate_target(str(target), "raw", 0, 4096, "vda5", confirmation=FAKE_IDENTITY,
                             allow_system_disk_partition=True, system_disk_ack=acknowledgement)


def test_raw_target_refuses_active_exclusive_claim(tmp_path, monkeypatch):
    host, target, _, _ = _raw_target_fixture(tmp_path, monkeypatch, root_devno="8:1")
    real_open = os.open

    def busy_open(target_path, flags, *args, **kwargs):
        if os.path.realpath(os.fspath(target_path)) != str(target.resolve()):
            return real_open(target_path, flags, *args, **kwargs)
        if flags & driver.os.O_EXCL:
            raise OSError(errno.EBUSY, "device is held")
        return 123

    monkeypatch.setattr(driver.os, "open", busy_open)
    acknowledgement = driver.system_disk_acknowledgement(
        driver.socket.gethostname(), "vda", ["vda1"], FAKE_IDENTITY.partuuid)
    with pytest.raises(driver.StudyError, match="active exclusive holder"):
        host.validate_target(str(target), "raw", 0, 4096, "vda5", confirmation=FAKE_IDENTITY,
                             allow_system_disk_partition=True, system_disk_ack=acknowledgement)


def test_raw_target_refuses_when_exclusive_probe_cannot_be_established(tmp_path, monkeypatch):
    host, target, _, _ = _raw_target_fixture(tmp_path, monkeypatch)

    real_open = os.open

    def denied_open(target_path, flags, *args, **kwargs):
        if os.path.realpath(os.fspath(target_path)) != str(target.resolve()):
            return real_open(target_path, flags, *args, **kwargs)
        raise OSError(errno.EACCES, "permission denied")

    monkeypatch.setattr(driver.os, "open", denied_open)
    with pytest.raises(driver.StudyError, match="cannot verify raw target is free"):
        host.validate_target(str(target), "raw", 0, 4096, "vda5", confirmation=FAKE_IDENTITY)


def test_raw_target_refuses_when_exclusive_probe_cannot_be_closed(tmp_path, monkeypatch):
    host, target, _, _ = _raw_target_fixture(tmp_path, monkeypatch)
    real_close = os.close

    def failed_close(fd):
        if fd != 123:
            return real_close(fd)
        raise OSError(errno.EIO, "close failed")

    monkeypatch.setattr(driver.os, "close", failed_close)
    with pytest.raises(driver.StudyError, match="cannot release raw target exclusive probe"):
        host.validate_target(str(target), "raw", 0, 4096, "vda5", confirmation=FAKE_IDENTITY)


def test_raw_target_refuses_root_system_disk(tmp_path, monkeypatch):
    host, target, _, _ = _raw_target_fixture(tmp_path, monkeypatch, root_devno="8:1")
    with pytest.raises(driver.StudyError) as raised:
        host.validate_target(str(target), "raw", 0, 4096, "vda5", confirmation=FAKE_IDENTITY)
    assert str(raised.value) == "raw target is on the root disk or contains a running system filesystem"


@pytest.mark.parametrize(("hostname", "disk", "roots", "partuuid"), [
    (None, "vda", ["vda1"], FAKE_IDENTITY.partuuid),
    ("host", "vda/name", ["vda1"], FAKE_IDENTITY.partuuid),
    ("host", None, ["vda1"], FAKE_IDENTITY.partuuid),
    ("host", "vda", [], FAKE_IDENTITY.partuuid),
    ("host", "vda", ["vda/1"], FAKE_IDENTITY.partuuid),
    ("host", "vda", [1], FAKE_IDENTITY.partuuid),
    ("host", "vda", ["vda1"], "not-a-partuuid"),
    ("host", "vda", ["vda1"], None),
])
def test_system_disk_ack_formatter_rejects_malformed_host_facts(hostname, disk, roots, partuuid):
    with pytest.raises(driver.StudyError, match="malformed host facts"):
        driver.system_disk_acknowledgement(hostname, disk, roots, partuuid)


def test_raw_target_allows_system_disk_only_with_exact_per_run_ack_and_records_swap_risk(
        tmp_path, monkeypatch):
    host, target, _, _ = _raw_target_fixture(tmp_path, monkeypatch, root_devno="8:1")
    swapfile = tmp_path / "swapfile"
    swapfile.touch()
    (host.proc / "swaps").write_text(
        f"Filename Type Size Used Priority\n{swapfile} file 1 0 -2\n", encoding="utf-8")
    original_stat = Path.stat

    def stat_with_sibling_swap(path, *args, **kwargs):
        if path == swapfile:
            return SimpleNamespace(st_mode=driver.stat.S_IFBLK, st_rdev=os.makedev(8, 1),
                                   st_dev=os.makedev(8, 1))
        return original_stat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", stat_with_sibling_swap)
    expected = driver.system_disk_acknowledgement(
        driver.socket.gethostname(), "vda", ["vda1"], FAKE_IDENTITY.partuuid)
    with pytest.raises(driver.StudyError, match="does not match current host facts"):
        host.validate_target(str(target), "raw", 0, 4096, "vda5", confirmation=FAKE_IDENTITY,
                             allow_system_disk_partition=True, system_disk_ack="constant-ack")
    accepted = host.validate_target(str(target), "raw", 0, 4096, "vda5", confirmation=FAKE_IDENTITY,
                                    allow_system_disk_partition=True, system_disk_ack=expected)
    assert accepted.identity == FAKE_IDENTITY
    assert accepted.system_disk["root_disk_shared"] is True
    assert accepted.system_disk["root_fs_devices"] == ["vda1"]
    assert accepted.system_disk["sibling_swap_devices"] == ["vda1"]
    assert accepted.system_disk["system_disk_acknowledgement"] == expected
    derived = host.validate_target(str(target), "raw", 0, 4096, "vda5",
                                   confirmation=FAKE_IDENTITY, allow_system_disk_partition=True,
                                   derive_system_disk_ack=True)
    assert derived.system_disk["system_disk_acknowledgement"] == expected


def test_raw_target_system_disk_opt_in_does_not_allow_target_partition_swap(tmp_path, monkeypatch):
    host, target, _, _ = _raw_target_fixture(tmp_path, monkeypatch, root_devno="8:1")
    (host.proc / "swaps").write_text(
        f"Filename Type Size Used Priority\n{target} partition 1 0 -2\n", encoding="utf-8")
    expected = driver.system_disk_acknowledgement(
        driver.socket.gethostname(), "vda", ["vda1"], FAKE_IDENTITY.partuuid)
    with pytest.raises(driver.StudyError, match="swap-active"):
        host.validate_target(str(target), "raw", 0, 4096, "vda5", confirmation=FAKE_IDENTITY,
                             allow_system_disk_partition=True, system_disk_ack=expected)


def test_raw_target_system_disk_opt_in_does_not_allow_whole_disk_swap_or_holder(
        tmp_path, monkeypatch):
    host, target, _, _ = _raw_target_fixture(tmp_path, monkeypatch, root_devno="8:1")
    swapfile = tmp_path / "swapfile"
    swapfile.touch()
    (host.proc / "swaps").write_text(
        f"Filename Type Size Used Priority\n{swapfile} partition 1 0 -2\n", encoding="utf-8")
    original_stat = Path.stat

    def stat_with_whole_disk_swap(path, *args, **kwargs):
        if path == swapfile:
            return SimpleNamespace(st_mode=driver.stat.S_IFBLK, st_rdev=os.makedev(8, 0),
                                   st_dev=os.makedev(8, 0))
        return original_stat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", stat_with_whole_disk_swap)
    expected = driver.system_disk_acknowledgement(
        driver.socket.gethostname(), "vda", ["vda1"], FAKE_IDENTITY.partuuid)
    with pytest.raises(driver.StudyError, match="swap-active"):
        host.validate_target(str(target), "raw", 0, 4096, "vda5", confirmation=FAKE_IDENTITY,
                             allow_system_disk_partition=True, system_disk_ack=expected)

    monkeypatch.setattr(Path, "stat", original_stat)
    (host.proc / "swaps").write_text("Filename Type Size Used Priority\n", encoding="utf-8")
    holder_host, holder_target, _, _ = _raw_target_fixture(
        tmp_path / "holder", monkeypatch, root_devno="8:99")
    (holder_host.sys / "devices/block/vda/holders/root-disk").symlink_to(
        holder_host.sys / "devices/block/root-disk")
    holder_ack = driver.system_disk_acknowledgement(
        driver.socket.gethostname(), "vda", ["root-disk"], FAKE_IDENTITY.partuuid)
    with pytest.raises(driver.StudyError) as raised:
        holder_host.validate_target(str(holder_target), "raw", 0, 4096, "vda5",
                                    confirmation=FAKE_IDENTITY, allow_system_disk_partition=True,
                                    system_disk_ack=holder_ack)
    assert str(raised.value) == "raw target is on the root disk or contains a running system filesystem"


def test_raw_target_refuses_opt_in_for_a_target_on_a_separate_disk(tmp_path, monkeypatch):
    host, target, _, _ = _raw_target_fixture(tmp_path, monkeypatch)
    with pytest.raises(driver.StudyError, match="applies only to a target on the running system disk"):
        host.validate_target(str(target), "raw", 0, 4096, "vda5", confirmation=FAKE_IDENTITY,
                             allow_system_disk_partition=True, system_disk_ack="any")


def test_raw_target_refuses_root_disk_reached_through_whole_disk_holder(tmp_path, monkeypatch):
    host, target, _, _ = _raw_target_fixture(tmp_path, monkeypatch, root_devno="8:99")
    disk = host.sys / "devices/block/vda"
    root_disk = host.sys / "devices/block/root-disk"
    (disk / "holders/root-disk").symlink_to(root_disk)
    with pytest.raises(driver.StudyError, match="root disk or contains"):
        host.validate_target(str(target), "raw", 0, 4096, "vda5", confirmation=FAKE_IDENTITY)


@pytest.mark.parametrize("mountpoint", ("/boot", "/srv/cow"))
def test_raw_target_refuses_disk_containing_system_mount(tmp_path, monkeypatch, mountpoint):
    host, target, _, _ = _raw_target_fixture(tmp_path, monkeypatch, root_devno="8:99")
    system_mount = f"37 25 8:1 / {mountpoint} rw,relatime - ext4 /dev/vda1 rw\n"
    for namespace in ("self", "1"):
        path = host.proc / namespace / "mountinfo"
        path.write_text(path.read_text(encoding="utf-8") + system_mount, encoding="utf-8")
    with pytest.raises(driver.StudyError, match="root disk or contains"):
        host.validate_target(str(target), "raw", 0, 4096, "vda5", confirmation=FAKE_IDENTITY)


def test_raw_target_refuses_unprovable_mount_namespace(tmp_path, monkeypatch):
    host, target, _, _ = _raw_target_fixture(tmp_path / "second", monkeypatch)
    (host.proc / "1/mountinfo").write_text(
        "36 25 8:0 / /mnt/hidden rw,relatime - ext4 /dev/vda rw\n", encoding="utf-8")
    with pytest.raises(driver.StudyError, match="mount state across PID 1"):
        host.validate_target(str(target), "raw", 0, 4096, "vda5", confirmation=FAKE_IDENTITY)


def _acknowledged_validation(host, target, acknowledgement=None):
    expected = acknowledgement or driver.system_disk_acknowledgement(
        driver.socket.gethostname(), "vda", ["vda1"], FAKE_IDENTITY.partuuid)
    return host.validate_target(str(target), "raw", 0, 4096, "vda5", confirmation=FAKE_IDENTITY,
                                allow_system_disk_partition=True, system_disk_ack=expected)


def _add_mount_row(host, row):
    for namespace in ("self", "1"):
        path = host.proc / namespace / "mountinfo"
        path.write_text(path.read_text(encoding="utf-8") + row, encoding="utf-8")


def test_system_disk_opt_in_still_refuses_a_swap_alias_of_the_target(tmp_path, monkeypatch):
    host, target, _, _ = _raw_target_fixture(tmp_path, monkeypatch, root_devno="8:1")
    alias_dir = tmp_path / "dev/disk/by-uuid"
    alias_dir.mkdir(parents=True)
    alias = alias_dir / "swap-alias"
    alias.symlink_to(target)
    (host.proc / "swaps").write_text(
        f"Filename Type Size Used Priority\n{alias} partition 1 0 -2\n", encoding="utf-8")
    with pytest.raises(driver.StudyError, match="swap-active"):
        _acknowledged_validation(host, target)


@pytest.mark.parametrize("mountpoint", ("/boot", "/boot/efi", "/usr", "/var", "/etc", "/srv/cow/sub"))
def test_system_disk_opt_in_still_refuses_system_mounts_on_the_target(tmp_path, monkeypatch, mountpoint):
    host, target, _, _ = _raw_target_fixture(tmp_path, monkeypatch, root_devno="8:1")
    _add_mount_row(host, f"37 25 0:0 / {mountpoint} rw,relatime - ext4 /dev/vda5 rw\n")
    with pytest.raises(driver.StudyError, match="partition is mounted"):
        _acknowledged_validation(host, target)


def test_system_disk_opt_in_still_refuses_a_target_that_is_a_btrfs_member_of_a_mount(
        tmp_path, monkeypatch):
    host, target, part, _ = _raw_target_fixture(tmp_path, monkeypatch, root_devno="8:1")
    _add_mount_row(host, "37 25 0:77 / /srv/cow rw,relatime - btrfs /dev/vda1 rw\n")
    devices = host.sys / "fs/btrfs/abcd-1234/devices"
    devices.mkdir(parents=True)
    (devices / "vda5").symlink_to(part)
    (devices / "vda1").symlink_to(host.sys / "devices/block/vda/vda1")
    base_command = driver.subprocess.run

    def command(args, **kwargs):
        if args[0] == "findmnt":
            return SimpleNamespace(returncode=0, stdout="btrfs abcd-1234 0:77\n", stderr="")
        return base_command(args, **kwargs)

    monkeypatch.setattr(driver.subprocess, "run", command)
    with pytest.raises(driver.StudyError, match="partition is mounted"):
        _acknowledged_validation(host, target)


def test_system_disk_acknowledgement_is_bound_to_the_current_hostname(tmp_path, monkeypatch):
    host, target, _, _ = _raw_target_fixture(tmp_path, monkeypatch, root_devno="8:1")
    replayed = driver.system_disk_acknowledgement(
        driver.socket.gethostname(), "vda", ["vda1"], FAKE_IDENTITY.partuuid)
    assert _acknowledged_validation(host, target, replayed).identity == FAKE_IDENTITY
    monkeypatch.setattr(driver.socket, "gethostname", lambda: "another-host")
    with pytest.raises(driver.StudyError, match="does not match current host facts"):
        _acknowledged_validation(host, target, replayed)


def test_system_disk_opt_in_does_not_follow_a_target_symlink_or_hidden_mount(tmp_path, monkeypatch):
    host, target, _, _ = _raw_target_fixture(tmp_path, monkeypatch, root_devno="8:1")
    alias = tmp_path / "dev/alias"
    alias.symlink_to(target)
    with pytest.raises(driver.StudyError, match="must not be a symlink"):
        _acknowledged_validation(host, alias)
    init_mounts = host.proc / "1/mountinfo"
    init_mounts.write_text(init_mounts.read_text(encoding="utf-8") +
                           "37 25 0:0 / /mnt/hidden rw,relatime - ext4 /dev/vda5 rw\n",
                           encoding="utf-8")
    with pytest.raises(driver.StudyError, match="mount state across PID 1"):
        _acknowledged_validation(host, target)


def test_sibling_swap_on_a_separate_disk_still_refuses_without_the_opt_in(tmp_path, monkeypatch):
    host, target, _, _ = _raw_target_fixture(tmp_path, monkeypatch, root_devno="8:99")
    sibling_swap = tmp_path / "sibling-swap"
    sibling_swap.touch()
    (host.proc / "swaps").write_text(
        f"Filename Type Size Used Priority\n{sibling_swap} partition 1 0 -2\n", encoding="utf-8")
    original_stat = Path.stat

    def stat_with_sibling_swap(path, *args, **kwargs):
        if path == sibling_swap:
            return SimpleNamespace(st_mode=driver.stat.S_IFBLK, st_rdev=os.makedev(8, 1),
                                   st_dev=os.makedev(8, 1))
        return original_stat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", stat_with_sibling_swap)
    with pytest.raises(driver.StudyError, match="swap-active"):
        host.validate_target(str(target), "raw", 0, 4096, "vda5", confirmation=FAKE_IDENTITY)


def test_local_quiet_window_and_fsync_wrapper_are_mocked(tmp_path, monkeypatch):
    host = driver.LocalHost()
    quiet = [SimpleNamespace(returncode=0, stdout="loaded\ninactive\n", stderr="") for _ in range(3)]
    quiet.append(SimpleNamespace(returncode=0, stdout="not-found\ninactive\n", stderr=""))
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


def test_local_liveness_probe_reports_fake_child_latency_and_failures(monkeypatch):
    host = driver.LocalHost()
    components = {"root_fsync_seconds": 0.1, "sshd_seconds": 0.1, "cockpit_seconds": 0.1}
    clock = [0.0, 0.35]
    monkeypatch.setattr(driver.time, "monotonic", lambda: clock.pop(0))
    calls = []
    def successful_probe(*args, **kwargs):
        calls.append(args[0])
        return SimpleNamespace(returncode=0, stdout=json.dumps({
            "responsive": True, "components": components}), stderr="")
    monkeypatch.setattr(driver.subprocess, "run", successful_probe)
    passed = host.liveness_probe(1)
    assert passed == {"responsive": True, "latency_seconds": 0.35,
                      "components": components, "error": None}
    assert calls[0][0] == "systemd-run"
    assert "--slice=system.slice" in calls[0] and "--wait" in calls[0] and "--pipe" in calls[0]
    assert "--collect" in calls[0] and "--property=RuntimeMaxSec=1" in calls[0]
    assert calls[0][-6:] == ["1", driver.LIVENESS_ROOT_DIRECTORY, driver.LIVENESS_SSH_HOST,
                             str(driver.LIVENESS_SSH_PORT), driver.LIVENESS_COCKPIT_UNIT,
                             driver.LIVENESS_COCKPIT_CONTAINER]

    def launched_unit(argv):
        option = next(arg for arg in argv if arg.startswith("--unit="))
        return option.removeprefix("--unit=")

    clock[:] = [0.0, 2.0, 2.0, 2.2]
    host = driver.LocalHost()
    timeout_calls = []
    launched = []
    def timed_out(*args, **kwargs):
        timeout_calls.append(args[0])
        if args[0][0] == "systemd-run":
            assert kwargs["timeout"] == 2
            launched.append(launched_unit(args[0]))
            raise driver.subprocess.TimeoutExpired(args[0], kwargs["timeout"])
        assert args[0] == ["systemctl", "is-active", launched[-1]]
        return SimpleNamespace(returncode=0, stdout="active", stderr="")
    monkeypatch.setattr(driver.subprocess, "run", timed_out)
    timeout = host.liveness_probe(1)
    assert timeout["responsive"] is False and timeout["latency_seconds"] == 2.0
    assert "timed out" in timeout["error"]
    still_active = host.liveness_probe(1)
    assert "not been confirmed stopped" in still_active["error"]
    assert timeout_calls[-1][:2] == ["systemctl", "is-active"]

    for stopped_state, returncode in (("inactive", 3), ("failed", 3), ("inactive", 1)):
        clock[:] = [2.0, 2.2]
        host = driver.LocalHost()
        host._liveness_probe_stalled = True
        host._liveness_probe_stalled_unit = "ibx-liveness-prior.service"

        def completed_after_timeout(*args, _state=stopped_state, _returncode=returncode, **kwargs):
            if args[0][:2] == ["systemctl", "is-active"]:
                assert args[0] == ["systemctl", "is-active", "ibx-liveness-prior.service"]
                return SimpleNamespace(returncode=_returncode, stdout=_state, stderr="")
            return SimpleNamespace(returncode=0, stdout=json.dumps({
                "responsive": True, "components": components}), stderr="")

        monkeypatch.setattr(driver.subprocess, "run", completed_after_timeout)
        recovered = host.liveness_probe(1)
        assert recovered["responsive"] is True
        assert host._liveness_probe_stalled is False

    for status_error in (OSError("systemctl unavailable"),
                         driver.subprocess.TimeoutExpired(["systemctl", "is-active"], 1)):
        clock[:] = [0.0, 2.0]
        host = driver.LocalHost()
        launched = []

        def status_unavailable(*args, _error=status_error, _launched=launched, **kwargs):
            if args[0][0] == "systemd-run":
                _launched.append(launched_unit(args[0]))
                raise driver.subprocess.TimeoutExpired(args[0], kwargs["timeout"])
            assert args[0] == ["systemctl", "is-active", _launched[-1]]
            raise _error

        monkeypatch.setattr(driver.subprocess, "run", status_unavailable)
        host.liveness_probe(1)
        unresolved = host.liveness_probe(1)
        assert unresolved["responsive"] is False
        assert "not been confirmed stopped" in unresolved["error"]

    clock[:] = [0.0, 2.0]
    host = driver.LocalHost()
    launched = []
    def unrecognized_status(*args, **kwargs):
        if args[0][0] == "systemd-run":
            launched.append(launched_unit(args[0]))
            raise driver.subprocess.TimeoutExpired(args[0], kwargs["timeout"])
        assert args[0] == ["systemctl", "is-active", launched[-1]]
        return SimpleNamespace(returncode=0, stdout="unexpected-state", stderr="")
    monkeypatch.setattr(driver.subprocess, "run", unrecognized_status)
    host.liveness_probe(1)
    unresolved = host.liveness_probe(1)
    assert unresolved["responsive"] is False
    assert "not been confirmed stopped" in unresolved["error"]

    for returncode, state in ((3, "deactivating"), (3, ""), (0, "inactive"),
                              (3, "unknown")):
        clock[:] = [0.0]
        host = driver.LocalHost()
        host._liveness_probe_stalled = True
        host._liveness_probe_stalled_unit = "ibx-liveness-prior.service"

        def not_confirmed_stopped(*args, _returncode=returncode, _state=state, **kwargs):
            if args[0][0] == "systemd-run":
                pytest.fail("an unconfirmed prior probe allowed another liveness probe")
            assert args[0] == ["systemctl", "is-active", "ibx-liveness-prior.service"]
            return SimpleNamespace(returncode=_returncode, stdout=_state, stderr="")

        monkeypatch.setattr(driver.subprocess, "run", not_confirmed_stopped)
        unresolved = host.liveness_probe(1)
        assert unresolved["responsive"] is False
        assert "not been confirmed stopped" in unresolved["error"]

    clock[:] = [0.0, 2.0, 2.0, 2.2]
    host = driver.LocalHost()
    probe_runs = [0]
    launched = []

    def unit_collected(*args, **kwargs):
        if args[0][:2] == ["systemctl", "is-active"]:
            assert args[0] == ["systemctl", "is-active", launched[-1]]
            return SimpleNamespace(returncode=4, stdout="unknown", stderr="")
        launched.append(launched_unit(args[0]))
        probe_runs[0] += 1
        if probe_runs[0] == 1:
            raise driver.subprocess.TimeoutExpired(args[0], kwargs["timeout"])
        return SimpleNamespace(returncode=0, stdout=json.dumps({
            "responsive": True, "components": components}), stderr="")

    monkeypatch.setattr(driver.subprocess, "run", unit_collected)
    host.liveness_probe(1)
    collected = host.liveness_probe(1)
    assert collected["responsive"] is True
    assert host._liveness_probe_stalled is False

    clock[:] = [0.0, 0.5]
    host = driver.LocalHost()
    def timed_out_before_limit(*args, **kwargs):
        raise driver.subprocess.TimeoutExpired(args[0], kwargs["timeout"])
    monkeypatch.setattr(driver.subprocess, "run", timed_out_before_limit)
    timeout_before_limit = host.liveness_probe(1)
    assert timeout_before_limit["latency_seconds"] == 1

    clock[:] = [2.0, 2.3]
    host = driver.LocalHost()
    monkeypatch.setattr(driver.subprocess, "run", lambda *args, **kwargs:
                        (_ for _ in ()).throw(OSError("cannot start probe")))
    unavailable = host.liveness_probe(1)
    assert unavailable["responsive"] is False and unavailable["latency_seconds"] == pytest.approx(0.3)
    assert "cannot start" in unavailable["error"]

    clock[:] = [0.0, 0.2]
    host = driver.LocalHost()
    monkeypatch.setattr(driver.subprocess, "run", lambda *args, **kwargs:
                        SimpleNamespace(returncode=0, stdout="not-json", stderr=""))
    malformed = host.liveness_probe(1)
    assert malformed["responsive"] is False and malformed["error"] == "probe returned malformed output"

    for payload in ({"responsive": "yes", "components": {}},
                    {"responsive": True, "components": []}):
        host = driver.LocalHost()
        clock[:] = [0.0, 0.2]
        monkeypatch.setattr(driver.subprocess, "run", lambda *args, payload=payload, **kwargs:
                            SimpleNamespace(returncode=0, stdout=json.dumps(payload), stderr=""))
        malformed = host.liveness_probe(1)
        assert malformed["responsive"] is False
        assert malformed["error"] == "probe returned malformed output"

    clock[:] = [0.0, 0.2]
    host = driver.LocalHost()
    monkeypatch.setattr(driver.subprocess, "run", lambda *args, **kwargs:
                        SimpleNamespace(returncode=0, stdout=json.dumps({
                            "responsive": False, "components": {}, "error": "sshd down"}), stderr=""))
    refused = host.liveness_probe(1)
    assert refused["responsive"] is False and refused["error"] == "sshd down"


def test_liveness_script_executes_fake_fsync_banner_and_cockpit_checks():
    status, payload, calls, addresses = execute_liveness_script()
    assert status == 0 and payload["responsive"] is True
    assert set(payload["components"]) == {"root_fsync_seconds", "sshd_seconds", "cockpit_seconds"}
    assert addresses == [((driver.LIVENESS_SSH_HOST, driver.LIVENESS_SSH_PORT), 1.0)]
    assert calls == [["systemctl", "show", driver.LIVENESS_COCKPIT_UNIT,
                      "--property=ActiveState", "--value"],
                     ["docker", "exec", driver.LIVENESS_COCKPIT_CONTAINER, "true"]]

    for kwargs, error_fragment in (
            ({"banner": b"not-ssh"}, "SSH protocol banner"),
            ({"systemctl_status": 1, "systemctl_state": "inactive"}, "not active"),
            ({"docker_status": 1}, "did not respond to exec"),
            ({"fsync_error": True}, "fsync failed")):
        status, payload, _calls, _addresses = execute_liveness_script(**kwargs)
        assert status == 1 and payload["responsive"] is False
        assert error_fragment in payload["error"]


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
                         target_kind="raw", offset=0, size=24 * GIB, mem_psi_max=20,
                         min_memavailable=GIB, min_free=GIB, max_probe_latency_s=1,
                         probe_consecutive_samples=3,
                         host=SimpleNamespace(is_root=lambda: False), quiet_window_confirmed=True)
    with pytest.raises(driver.StudyError, match="confirm-quiet"):
        driver.run_study("raw-t0", target="/dev/fake", device="vda5", out=tmp_path / "b",
                         target_kind="raw", offset=0, size=24 * GIB, mem_psi_max=20,
                         min_memavailable=GIB, min_free=GIB, max_probe_latency_s=1,
                         probe_consecutive_samples=3, host=host)
    with pytest.raises(driver.StudyError, match="device a sysfs"):
        run_set(tmp_path / "c", "raw-t0", host=host, target="relative", size=GIB)
    with pytest.raises(driver.StudyError, match="extent"):
        run_set(tmp_path / "d", "raw-t0", host=host, offset=31 * GIB, size=2 * GIB)
    with pytest.raises(driver.StudyError, match="memory PSI full limit"):
        driver.run_study("raw-t0", target="/dev/fake", device="vda5", out=tmp_path / "e",
                         target_kind="raw", offset=0, size=24 * GIB, mem_psi_max=0,
                         min_memavailable=GIB, min_free=GIB, max_probe_latency_s=1,
                         probe_consecutive_samples=3, host=host,
                         quiet_window_confirmed=True)
    with pytest.raises(driver.StudyError, match="memory PSI full limit"):
        driver.run_study("raw-t0", target="/dev/fake", device="vda5", out=tmp_path / "nan",
                         target_kind="raw", offset=0, size=24 * GIB, mem_psi_max=float("nan"),
                         min_memavailable=GIB, min_free=GIB, max_probe_latency_s=1,
                         probe_consecutive_samples=3, host=host,
                         quiet_window_confirmed=True)
    with pytest.raises(driver.StudyError, match="maximum host-liveness probe latency"):
        driver.run_study("raw-t0", target="/dev/fake", device="vda5", out=tmp_path / "probe-limit",
                         target_kind="raw", offset=0, size=24 * GIB, mem_psi_max=20,
                         min_memavailable=GIB, min_free=GIB, max_probe_latency_s=0,
                         probe_consecutive_samples=3, host=host, quiet_window_confirmed=True)
    with pytest.raises(driver.StudyError, match="consecutive-sample count"):
        driver.run_study("raw-t0", target="/dev/fake", device="vda5", out=tmp_path / "probe-count",
                         target_kind="raw", offset=0, size=24 * GIB, mem_psi_max=20,
                         min_memavailable=GIB, min_free=GIB, max_probe_latency_s=1,
                         probe_consecutive_samples=0, host=host, quiet_window_confirmed=True)
    with pytest.raises(driver.StudyError, match="memory and free-space floors"):
        driver.run_study("raw-t0", target="/dev/fake", device="vda5", out=tmp_path / "floor",
                         target_kind="raw", offset=0, size=24 * GIB, mem_psi_max=20,
                         min_memavailable=0, min_free=GIB, max_probe_latency_s=1,
                         probe_consecutive_samples=3, host=host,
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
    assert "malformed" in driver.stop_reason({**HEALTHY, "psi": {"memory": {"full": {"avg10": -1}}}},
                                               min_memavailable=1, min_free=1)
    assert "malformed" in driver.stop_reason({**HEALTHY, "psi": {"memory": {"full": {"avg10": float("nan")}}}},
                                               min_memavailable=1, min_free=1)
    for malformed_psi in (True, "100", 101):
        sample = {**HEALTHY, "psi": {"memory": {"full": {"avg10": malformed_psi}}}}
        assert "malformed" in driver.stop_reason(sample, min_memavailable=1, min_free=1)
    assert "malformed" in driver.stop_reason({**HEALTHY, "meminfo": {"MemAvailable": True}},
                                               min_memavailable=1, min_free=1)
    assert "malformed" in driver.stop_reason({**HEALTHY, "free_bytes": True},
                                               min_memavailable=1, min_free=1)
    high_memory_some = {**HEALTHY,
                        "psi": {"io": {"some": {"avg10": 99}},
                                "memory": {"some": {"avg10": 99},
                                           "full": {"avg10": 0.1}}}}
    assert driver.stop_reason(high_memory_some, min_memavailable=1, min_free=1) is None
    assert driver.stop_reason({**HEALTHY, "psi": {
        "io": {"some": {"avg10": 100}}, "memory": HEALTHY["psi"]["memory"]}},
                              min_memavailable=1, min_free=1) is None


def test_preflight_and_memory_or_free_space_stops_happen_before_fio_or_discard(tmp_path):
    lowfree = FakeHost(free=1)
    with pytest.raises(driver.StudyError, match="free space"):
        run_set(tmp_path / "free", "raw-t0", host=lowfree)
    assert lowfree.discards == [] and lowfree.fio.calls == []
    lowmem = FakeHost(available=1)
    with pytest.raises(driver.StudyError, match="MemAvailable"):
        run_set(tmp_path / "memory", "raw-t0", host=lowmem)
    assert lowmem.discards == [] and lowmem.fio.calls == []
    highmempsi = FakeHost(psi_mem=0.1, psi_mem_full=50)
    with pytest.raises(driver.StudyError, match="memory PSI full exceeded"):
        run_set(tmp_path / "psi-memory", "raw-t0", host=highmempsi, dry_run=True)
    assert highmempsi.discards == [] and highmempsi.fio.calls == []


def test_liveness_stop_uses_fake_latency_source_and_clock_for_n_consecutive_samples(tmp_path):
    host = FakeHost(psi_io=99)
    host.liveness_samples = [
        {"responsive": True, "latency_seconds": 0.1, "components": {"root_fsync_seconds": 0.1}},
        {"responsive": True, "latency_seconds": 2.0, "components": {"root_fsync_seconds": 2.0}},
        {"responsive": True, "latency_seconds": 2.1, "components": {"root_fsync_seconds": 2.1}},
        {"responsive": True, "latency_seconds": 2.2, "components": {"root_fsync_seconds": 2.2}},
    ]
    original = driver.CELL_SETS["file-ext4"]
    driver.CELL_SETS["file-ext4"] = ("ext4-file", ("veryquick",), "t9h")

    def samples_until_stop(job_path, runtime_s, sample):
        with pytest.raises(driver.StudyError, match="3 consecutive samples"):
            for _ in range(4):
                host.clock += 1
                sample()
        raise driver.StudyError("fake fio stopped by monitor")

    host.execute_fio = samples_until_stop
    try:
        with pytest.raises(driver.StudyError, match="fake fio stopped"):
            run_set(tmp_path, "file-ext4", host=host, target_type="ext4-file", target="/tmp/target",
                    probe_interval_s=1)
    finally:
        driver.CELL_SETS["file-ext4"] = original
    rows = [json.loads(line) for line in (tmp_path / "monitor.jsonl").read_text().splitlines()]
    assert len(rows) == 4
    assert [row["host_liveness"]["latency_seconds"] for row in rows] == [0.1, 2.0, 2.1, 2.2]
    assert host.clock == pytest.approx(9.4)
    assert all(row["psi"]["io"]["some"]["avg10"] == 99 for row in rows)


def test_liveness_probe_failure_stops_at_configured_sample_count(tmp_path):
    host = FakeHost()
    host.liveness_responsive = False
    with pytest.raises(driver.StudyError, match="initial host-liveness probe"):
        run_set(tmp_path, "file-ext4", host=host, target_type="ext4-file", target="/tmp/target",
                probe_consecutive_samples=1)
    assert host.fio.calls == [] and host.discards == []
    row = json.loads((tmp_path / "monitor.jsonl").read_text().splitlines()[0])
    assert row["host_liveness"]["responsive"] is False


def test_liveness_stop_counts_only_consecutive_slow_samples(tmp_path):
    host = FakeHost()
    host.liveness_samples = [
        {"responsive": True, "latency_seconds": 0.1, "components": {}},
        {"responsive": True, "latency_seconds": 2.0, "components": {}},
        {"responsive": True, "latency_seconds": 0.1, "components": {}},
        {"responsive": True, "latency_seconds": 2.1, "components": {}},
        {"responsive": True, "latency_seconds": 2.2, "components": {}},
        {"responsive": True, "latency_seconds": 2.3, "components": {}},
    ]
    original = driver.CELL_SETS["file-ext4"]
    driver.CELL_SETS["file-ext4"] = ("ext4-file", ("veryquick",), "t9h")

    def samples_until_stop(job_path, runtime_s, sample):
        with pytest.raises(driver.StudyError, match="3 consecutive samples"):
            for _ in range(6):
                host.clock += 1
                sample()
        raise driver.StudyError("fake fio stopped by monitor")

    host.execute_fio = samples_until_stop
    try:
        with pytest.raises(driver.StudyError, match="fake fio stopped"):
            run_set(tmp_path, "file-ext4", host=host, target_type="ext4-file", target="/tmp/target",
                    probe_interval_s=1)
    finally:
        driver.CELL_SETS["file-ext4"] = original
    rows = [json.loads(line) for line in (tmp_path / "monitor.jsonl").read_text().splitlines()]
    assert [row["host_liveness"]["latency_seconds"] for row in rows] == [0.1, 2.0, 0.1, 2.1, 2.2, 2.3]


def test_elapsed_active_and_write_meters_refuse_before_starting_more_work(tmp_path):
    now = driver.dt.datetime.now(driver.dt.timezone.utc)
    out = tmp_path / "budget"
    out.mkdir()
    state = driver.empty_state(now.isoformat(), "raw", "/dev/fake", "vda5", FAKE_IDENTITY)
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
    host = FakeHost(psi_io=50, psi_mem=0.1, psi_mem_full=50)
    # A small cell-set fixture avoids a long fake schedule while exercising the controller's monitor guard.
    original = driver.CELL_SETS["file-ext4"]
    driver.CELL_SETS["file-ext4"] = ("ext4-file", ("veryquick",), "t9h")
    calls = {"n": 0}

    def three_samples(job_path, runtime_s, sample):
        calls["n"] += 1
        with pytest.raises(driver.StudyError, match="memory PSI full exceeded threshold for three"):
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


def test_schedule_adaptive_rules_and_plan_rendering(capsys, monkeypatch):
    plan = driver.plan_document()
    assert plan["meter"]["base_schedule_bytes"] + plan["meter"]["adaptive_reserve_bytes"] <= driver.WRITE_CAP
    assert plan["meter"]["base_schedule_bytes"] == driver.base_schedule_write_cap()
    assert driver.CELL_SETS["raw-t0"][1] == ("standard", "quick", "veryquick")
    assert driver.CELL_SETS["raw-t3h"][1] == ("veryquick", "quick")
    assert driver.CELL_SETS["raw-t6h"][1] == ("quick", "veryquick", "standard")
    assert driver.CELL_SETS["raw-t9h"][1] == ("veryquick", "quick")
    driver.print_plan()
    output = capsys.readouterr().out
    assert f"{driver.WRITE_CAP / driver.GB:.2f} GB writes" in output and "t0+6h" in output
    assert "adaptive reserve 0.00 GB" in output
    assert "operator's explicit --allow-system-disk-partition consent" in output
    assert plan["limits"]["liveness_probe_interval_seconds"] == driver.DEFAULT_LIVENESS_PROBE_INTERVAL_S
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
    monkeypatch.setattr(driver, "WRITE_CAP", driver.BASE_SCHEDULE_WRITE_CAP - 1)
    with pytest.raises(driver.StudyError, match="maximum base schedule requires"):
        driver.schedule_adaptive_repeats()
    monkeypatch.setattr(driver, "WRITE_CAP", driver.BASE_SCHEDULE_WRITE_CAP +
                        driver.adaptive_repeat_candidate_write_cap())
    assert driver.schedule_adaptive_repeats() == driver.ADAPTIVE_REPEAT_CELL_SETS


def test_ibx_cap_is_pinned_in_user_documentation():
    documents = (
        driver.REPO / "libraries/iobench/README.md",
        driver.HERE / "README.md",
        driver.REPO / "libraries/iobench/docs/DESIGN-GUIDE.md",
        driver.REPO / "libraries/iobench/docs/CONSUMERS.md",
    )
    assert driver.WRITE_CAP_GB == 500
    for path in documents:
        content = path.read_text(encoding="utf-8")
        assert f"{driver.WRITE_CAP_GB} GB decimal" in content
        assert "626.25 GB" not in content


def test_t6_standard_is_omitted_when_quick_errors_are_far_from_boundary(tmp_path):
    out = tmp_path / "stable"
    seed_raw_t6h_evidence(out)
    assert not driver.raw_standard_needed(out)
    run_set(out, "raw-t6h", host=FakeHost())
    rows = [json.loads(path.read_text()) for path in (out / "cells").glob("*.json")]
    assert not any(row.get("slot") == "t6h" and row.get("rung") == "standard" for row in rows)


def test_t6_standard_runs_when_quick_error_is_near_tolerance_boundary(tmp_path):
    out = tmp_path / "borderline"
    seed_raw_t6h_evidence(out, quick=(140.0, 100.0))
    assert driver.raw_standard_needed(out)
    run_set(out, "raw-t6h", host=FakeHost())
    rows = [json.loads(path.read_text()) for path in (out / "cells").glob("*.json")]
    assert any(row.get("slot") == "t6h" and row.get("rung") == "standard" for row in rows)


def test_t6_standard_runs_when_quick_movement_needs_second_reference_point(tmp_path):
    out = tmp_path / "moving-target"
    seed_raw_t6h_evidence(out, quick=(70.0, 130.0))
    assert driver.raw_standard_needed(out)
    run_set(out, "raw-t6h", host=FakeHost())
    rows = [json.loads(path.read_text()) for path in (out / "cells").glob("*.json")]
    assert any(row.get("slot") == "t6h" and row.get("rung") == "standard" for row in rows)


def test_real_schedule_keeps_base_caps_and_drops_unfunded_adaptive_repeats(tmp_path):
    assert driver.WRITE_CAP_GB == 500
    assert driver.WRITE_CAP == driver.WRITE_CAP_GB * driver.GB == 500_000_000_000
    assert driver.BASE_SCHEDULE_WRITE_CAP == driver.base_schedule_write_cap() == 495_123_688_448
    assert driver.BASE_SCHEDULE_WRITE_CAP <= driver.WRITE_CAP
    assert driver.WRITE_CAP - driver.BASE_SCHEDULE_WRITE_CAP == 4_876_311_552

    part_repeat_cap = driver.cell_set_write_cap("part-c-repeat")
    raw_repeat_cap = driver.cell_set_write_cap("raw-t12h")
    assert part_repeat_cap == 49_123_688_448
    assert raw_repeat_cap == driver.cell_set_write_cap("raw-t12h2") == 41 * driver.GB
    assert driver.adaptive_repeat_candidate_write_cap() == part_repeat_cap + 2 * raw_repeat_cap
    assert driver.SCHEDULED_ADAPTIVE_REPEAT_CELL_SETS == ()
    assert driver.DROPPED_ADAPTIVE_REPEAT_CELL_SETS == (
        "part-c-repeat", "raw-t12h", "raw-t12h2")
    assert driver.ADAPTIVE_RESERVE_WRITE_CAP == driver.adaptive_reserve_write_cap() == 0
    assert driver.BASE_SCHEDULE_WRITE_CAP + driver.ADAPTIVE_RESERVE_WRITE_CAP <= driver.WRITE_CAP
    assert min(part_repeat_cap, raw_repeat_cap) > driver.WRITE_CAP - driver.BASE_SCHEDULE_WRITE_CAP
    assert driver.cell_set_write_cap("file-ext4-standard") == 100 * driver.GB
    assert driver.cell_set_write_cap("file-btrfs-standard") == 100 * driver.GB
    assert driver.file_standard_write_cap() == 100 * driver.GB
    empty = {"cells": {}}
    assert (driver.pending_cell_set_write_cap("part-c", out=tmp_path, state=empty)
            == driver.cell_set_write_cap("part-c"))
    assert driver.required_remainder_write_cap(
        "file-btrfs-standard", out=tmp_path, state=empty) == 0
    with pytest.raises(driver.StudyError, match="unknown cell-set"):
        driver.cell_set_write_cap("unknown-set")

    for cell_set, offset, size in (
            ("part-c-repeat", 24 * GIB, 8 * GIB),
            ("raw-t12h", 0, 24 * GIB), ("raw-t12h2", 0, 24 * GIB)):
        out = tmp_path / cell_set
        host = FakeHost()
        result = run_set(out, cell_set, host=host, offset=offset, size=size)
        assert result["skipped"] is True and "500 GB write cap" in result["reason"]
        assert getattr(host, "validated_target", None) is None
        assert host.fio.calls == [] and host.discards == []
        assert not out.exists()


def test_complete_maximum_base_schedule_fits_under_500_gb(tmp_path, monkeypatch):
    out = tmp_path / "maximum-base"
    host = FakeHost()
    host.fio.rate_bps = 100 * GIB
    host.fio.bw_log_value = 1_000_000_000
    monkeypatch.setattr(driver, "raw_standard_needed", lambda *_: True)
    for cell_set, kwargs in (
            ("raw-t0", {}),
            ("part-c", {"offset": 24 * GIB, "size": 8 * GIB}),
            ("raw-t3h", {}), ("raw-t6h", {}), ("raw-t9h", {}),
            ("file-ext4", {"target_type": "ext4-file", "target": "/mnt/ext4.dat"}),
            ("file-btrfs", {"target_type": "btrfs-file", "target": "/mnt/btrfs.dat"})):
        run_set(out, cell_set, host=host, **kwargs)
    state = json.loads((out / driver.STATE_NAME).read_text(encoding="utf-8"))
    assert state["write_bytes"] == driver.BASE_SCHEDULE_WRITE_CAP
    assert state["write_bytes"] <= driver.WRITE_CAP
    assert state["write_reservations"] == {}


def test_optional_file_standard_skips_when_its_full_cap_does_not_fit(tmp_path, monkeypatch):
    out = tmp_path / "file-standard"
    out.mkdir()
    state = driver.empty_state(driver.dt.datetime.now(driver.dt.timezone.utc).isoformat(),
                               "ext4-file", "/mnt/target.dat", "vda5", None)
    state["write_bytes"] = driver.BASE_SCHEDULE_WRITE_CAP
    driver.write_json(out / driver.STATE_NAME, state)
    monkeypatch.setattr(driver, "file_standard_needed", lambda *_: True)
    host = FakeHost()
    result = run_set(out, "file-ext4-standard", host=host,
                     target_type="ext4-file", target="/mnt/target.dat")
    assert result["skipped"] is True and "full file-standard cap" in result["reason"]
    assert host.validated_target == ("/mnt/target.dat", "ext4-file", 0, 24 * GIB, "vda5")
    assert host.fio.calls == [] and host.discards == []
    assert not (out / "jobs").exists() and not (out / "fio-logs").exists()


def test_optional_file_standard_reserves_required_file_btrfs_remainder(tmp_path, monkeypatch):
    out = tmp_path / "required-remainder"
    out.mkdir()
    state = driver.empty_state(driver.dt.datetime.now(driver.dt.UTC).isoformat(),
                               "ext4-file", "/mnt/target.dat", "vda5", None)
    state["write_bytes"] = 400 * driver.GB
    driver.write_json(out / driver.STATE_NAME, state)
    monkeypatch.setattr(driver, "file_standard_needed", lambda *_: True)

    host = FakeHost()
    standard = run_set(out, "file-ext4-standard", host=host,
                       target_type="ext4-file", target="/mnt/target.dat")
    assert standard["skipped"] is True
    assert standard["reason"] == (
        "the full file-standard cap and 41000000000 bytes required by cell-sets "
        "still ahead do not fit in the remaining 500 GB write budget")
    assert host.fio.calls == [] and host.discards == []
    assert json.loads((out / driver.STATE_NAME).read_text())["write_bytes"] == 400 * driver.GB

    required = run_set(out, "file-btrfs", host=host,
                       target_type="btrfs-file", target="/mnt/target.dat")
    assert required.get("skipped") is not True
    state = json.loads((out / driver.STATE_NAME).read_text())
    assert state["write_bytes"] <= driver.WRITE_CAP


def test_optional_file_standard_reserves_only_unpaid_required_remainder(tmp_path, monkeypatch):
    out = tmp_path / "partially-paid-required-remainder"
    cells = out / "cells"
    cells.mkdir(parents=True)
    state = driver.empty_state(driver.dt.datetime.now(driver.dt.UTC).isoformat(),
                               "ext4-file", "/mnt/target.dat", "vda5", None)
    for index, job in enumerate(driver.rung_jobs("quick")):
        key = driver.cell_key("btrfs-file", "t9h", "quick", job)
        filename = re.sub(r"[^A-Za-z0-9_.-]", "_", key) + ".json"
        if index == 0:
            driver.write_json(cells / filename, {})
        else:
            state["cells"][key] = filename
    state["write_bytes"] = 375 * driver.GB
    driver.write_json(out / driver.STATE_NAME, state)
    monkeypatch.setattr(driver, "file_standard_needed", lambda *_: True)

    host = FakeHost()
    standard = run_set(out, "file-ext4-standard", host=host,
                       target_type="ext4-file", target="/mnt/target.dat")
    assert standard.get("skipped") is not True
    assert json.loads((out / driver.STATE_NAME).read_text())["write_bytes"] <= driver.WRITE_CAP


def test_plan_reads_existing_meter_and_refuses_malformed_state(tmp_path, capsys):
    state_path = tmp_path / driver.STATE_NAME
    state = driver.empty_state(driver.dt.datetime.now(driver.dt.timezone.utc).isoformat(),
                               "raw", "/dev/vda5", "vda5", FAKE_IDENTITY)
    state["write_bytes"] = 123
    state["active_seconds"] = 12.5
    driver.write_json(state_path, state)
    driver.print_plan(state_path=state_path)
    assert "system-disk facts: not recorded yet" in capsys.readouterr().out
    state["run_manifest"]["target_safety"]["raw"] = {
        "hostname": "fixture-host", "target_disk": "vda", "root_fs_devices": ["vda3"],
        "system_mounts_on_disk": ["/", "/boot"], "sibling_swap_devices": ["vda6"],
        "target_partuuid": FAKE_IDENTITY.partuuid,
        "system_disk_acknowledgement": "fixture-host:vda:vda3:f00d-1234",
    }
    driver.write_json(state_path, state)
    plan = driver.plan_document(state_path)
    assert plan["meter"]["used_bytes"] == 123
    assert plan["time"]["active_seconds_used"] == 12.5
    driver.print_plan(state_path=state_path)
    plan_output = capsys.readouterr().out
    assert "Recorded raw target facts: host=fixture-host; disk=vda" in plan_output
    assert "system mount(s)=/,/boot; sibling swap device(s)=vda6" in plan_output
    assert "system-disk drift-check token=fixture-host:vda:vda3:f00d-1234" in plan_output
    driver.write_json(state_path, {"schema": 9})
    with pytest.raises(driver.StudyError, match="cannot be used"):
        driver.plan_document(state_path)
    for malformed in ({"schema": driver.STATE_SCHEMA, "write_bytes": True, "active_seconds": 0},
                      {"schema": driver.STATE_SCHEMA, "write_bytes": 0, "active_seconds": float("nan")}):
        driver.write_json(state_path, malformed)
        with pytest.raises(driver.StudyError, match="cannot be used"):
            driver.plan_document(state_path)
    state_path.write_text("{", encoding="utf-8")
    with pytest.raises(driver.StudyError, match="unreadable"):
        driver.plan_document(state_path)

    malformed = driver.empty_state(driver.dt.datetime.now(driver.dt.UTC).isoformat(),
                                   "raw", "/dev/vda5", "vda5", FAKE_IDENTITY)
    malformed["run_manifest"] = {"schema": driver.RUN_MANIFEST_SCHEMA}
    driver.write_json(state_path, malformed)
    with pytest.raises(driver.StudyError, match="run manifest is malformed"):
        driver.plan_document(state_path)
    malformed["run_manifest"] = {"schema": driver.RUN_MANIFEST_SCHEMA,
                                 "stop_policy": "bad", "target_safety": {}}
    driver.write_json(state_path, malformed)
    with pytest.raises(driver.StudyError, match="run manifest is malformed"):
        driver.plan_document(state_path)


def test_recorded_data_replay_analyze_rung_tables(tmp_path, monkeypatch):
    cells = tmp_path / "cells"
    cells.mkdir()
    fixture_paths = (driver.HERE / "fixtures/replay.jsonl",
                     driver.HERE / "fixtures/replay-file-sequential.jsonl")
    fixture_rows = [json.loads(line) for fixture in fixture_paths
                    for line in fixture.read_text(encoding="utf-8").splitlines()]
    assert fixture_rows
    for row in fixture_rows:
        assert row["schema"] == driver.SCHEMA
        assert row["started_utc"] < row["completed_utc"] == row["recorded_utc"]
        assert {"write_bytes_before", "write_cap_bytes", "active_seconds_before"} <= set(row["meter"])
        assert row["meter"]["write_cap_bytes"] == driver.WRITE_CAP
        assert "target_identity" in row and row["target"] and row["device"]
        assert row["provenance"]["kind"] == "recorded-shape-fixture"
        assert "not a host observation" in row["provenance"]["source"]
        assert isinstance(row["fio"].get("jobs"), list) and len(row["fio"]["jobs"]) == 1
    for index, row in enumerate(fixture_rows):
        (cells / f"{index:02}.json").write_text(json.dumps(row) + "\n", encoding="utf-8")
    report = driver.analysis(tmp_path)
    raw = report["tables"]["raw"]["rbps"]
    assert raw["V"] == pytest.approx(0.20)
    assert raw["cells"]["quick"]["e"] == pytest.approx(0.10)
    assert raw["cells"]["quick"]["run_values"][0] == {
        "slot": "t0", "value": 550000000.0, "x_ref": 500000000.0}
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
    assert report["tables"]["raw"]["discard_recovery"]["T"] == "±30%"
    assert report["tables"]["raw"]["discard_recovery"]["cells"]["quick"]["e"] is not None
    standard_canary = [row for row in fixture_rows if row["target_type"] == "raw" and
                       row["slot"] == "t0" and row["rung"] == "standard"]
    fresh = next(row for row in standard_canary if row["cell"] == "fresh4k-write")
    recovered = next(row for row in standard_canary if row["cell"] == "discard-recovery4k-write")
    recovery_ratio = next(row for row in driver.load_results(tmp_path)
                          if row["target_type"] == "raw" and row["slot"] == "t0" and
                          row["rung"] == "standard" and row["number"] == "discard_recovery")
    assert recovery_ratio["value"] == pytest.approx(recovered["value"] / fresh["value"])
    quick_ratio = next(row for row in driver.load_results(tmp_path)
                       if row["target_type"] == "raw" and row["slot"] == "t0" and
                       row["rung"] == "quick" and row["number"] == "discard_recovery")
    assert report["tables"]["raw"]["discard_recovery"]["cells"]["quick"]["e"] == pytest.approx(
        abs(quick_ratio["value"] / recovery_ratio["value"] - 1))
    assert all(report["tables"]["raw"][f"latency_under_load_{direction}_{pct}"]["cells"][rung]["e"]
               is not None
               for direction in ("read", "write") for pct in (30, 60, 90)
               for rung in ("veryquick", "quick", "standard"))
    proxy_rows = [row for row in driver.load_results(tmp_path)
                  if row.get("cell") == "derived:latency_under_load_read_30:qd32-p99-proxy"]
    assert proxy_rows and proxy_rows[0]["value"] == 210000
    assert proxy_rows[0]["latency_proxy"] == "QD32 p99"
    ext4 = report["tables"]["ext4-file"]["rbps"]
    assert ext4["file_bias_vs_raw"]["quick"] == pytest.approx(-0.25)
    assert ext4["file_bias_raw_spread"]["quick"] == pytest.approx(628571428.5714285 / 550000000 - 1)
    markdown = driver.render_analysis(report)
    assert "usable for:" in markdown
    assert "not usable for:" in markdown
    assert "btrfs-file" in markdown
    assert "quick (default)" in markdown
    assert "D-IBX-001" in markdown
    monkeypatch.setattr(driver, "DROPPED_ADAPTIVE_REPEAT_CELL_SETS", ())
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


def test_render_analysis_reports_separate_disk_context_and_non_mapping_policy():
    result = {"tables": {}, "part_c": {}, "study_decisions": {},
              "run_manifest": {"stop_policy": [], "target_safety": {"raw": {
                  "hostname": "fixture-host", "target_disk": "vdb",
                  "root_fs_devices": ["vda3"], "target_partuuid": "f00d-1234",
                  "root_disk_shared": False, "system_mounts_on_disk": [],
                  "sibling_swap_devices": [], "system_disk_acknowledgement": None}}}}
    rendered = driver.render_analysis(result)
    assert "Raw target risk: target disk was separate from the running system disk" in rendered
    assert "Host: `fixture-host`; target disk: `vdb`" in rendered
    assert "System-disk drift-check token: `not required`" in rendered
    assert "Stop policy:" not in rendered


def test_render_analysis_reports_missing_system_disk_facts():
    rendered = driver.render_analysis({"tables": {}, "part_c": {}, "study_decisions": {},
                                       "run_manifest": {"stop_policy": {}, "target_safety": {}}})
    assert "Raw target system-disk facts were not recorded in this manifest." in rendered


def test_liveness_probe_validation_refuses_incomplete_and_malformed_data():
    valid = {"responsive": True, "latency_seconds": 0.1, "components": {}}
    assert driver.validate_liveness_probe(valid)["responsive"] is True
    invalid = (None, {}, {**valid, "responsive": "yes"}, {**valid, "latency_seconds": True},
               {**valid, "latency_seconds": "0.1"}, {**valid, "latency_seconds": float("nan")},
               {**valid, "latency_seconds": -0.1}, {**valid, "components": []})
    for probe in invalid:
        with pytest.raises(driver.StudyError, match="probe data is incomplete or malformed"):
            driver.validate_liveness_probe(probe)


def test_run_refuses_malformed_liveness_sample_before_target_io(tmp_path):
    host = FakeHost()
    host.liveness_samples = [{"responsive": "yes", "latency_seconds": 0.1, "components": {}}]
    with pytest.raises(driver.StudyError, match="probe data is incomplete or malformed"):
        run_set(tmp_path, "raw-t0", host=host)
    assert host.discards == [] and host.fio.calls == []


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


def test_adaptive_repeat_requires_complete_slots_and_repeats_moving_error(tmp_path, monkeypatch):
    monkeypatch.setattr(driver, "DROPPED_ADAPTIVE_REPEAT_CELL_SETS", ())
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


def materialize_real_cgroup_layout(tmp_path):
    fixture_path = Path(__file__).parent / "fixtures/ibx-real-cgroup-layout.json"
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    root = tmp_path / "cg"
    root.mkdir()
    for relative, controls in fixture["groups"].items():
        directory = root if relative == "." else root / relative
        directory.mkdir(parents=True, exist_ok=True)
        for name, contents in controls.items():
            path = directory / name
            if contents is None:
                assert not path.exists()
            else:
                path.write_text(contents, encoding="utf-8")
    return root, fixture


def test_io_control_snapshot_accepts_the_recorded_host_layout(tmp_path, monkeypatch):
    root, fixture = materialize_real_cgroup_layout(tmp_path)
    assert fixture["provenance"]["kernel"] == "7.2.12"
    assert {source["sha256"] for source in fixture["provenance"]["sources"]} == {
        "b0ff2a77f3b056d8a6da3aff5a2976d126eb2dc18668aa0fe09ab47aaedce9ad",
        "cd4519778ee1a7baf0276b6caef0847550fb904df19d138a68637e9022b42a43",
    }
    snapshot = driver.io_control_snapshot(cgroup_root=root, cgroup_text=fixture["cgroup_text"])
    assert [row["path"] for row in snapshot["cgroups"]] == [str(root), str(root / "system.slice")]
    assert snapshot["cgroups"][0]["files"] == {
        "io.max": None, "io.weight": None, "io.latency": None}
    assert snapshot["cgroups"][1]["files"] == {
        "io.max": "", "io.weight": "default 100", "io.latency": None}
    assert "io_latency_peers" not in snapshot
    assert snapshot["io_cost"]["io.cost.qos"].startswith("254:0 enable=0 ctrl=auto")
    assert snapshot["io_cost"]["io.cost.model"].startswith("254:0 ctrl=auto model=linear")
    for row in snapshot["cgroups"]:
        io_max = row["files"]["io.max"]
        if io_max is not None:
            assert driver._io_max_active(io_max, Path(row["path"])) is False
        io_weight = row["files"]["io.weight"]
        if io_weight is not None:
            assert driver._validate_io_weight_inactive(io_weight, Path(row["path"])) is None
    assert driver._io_max_active("8:0 rbps=max wbps=max\n") is False
    assert driver._io_max_active("") is False
    assert driver._io_max_active("\n") is False
    assert driver._io_max_active("8:0 rbps=100\n") is True
    assert driver.cgroup_path("0::/bench\n") == "/bench"
    with pytest.raises(driver.StudyError, match="malformed io.max"):
        driver._io_max_active("badline\n")
    with pytest.raises(driver.StudyError, match="malformed io.max setting"):
        driver._io_max_active("8:0 rbps\n")
    with pytest.raises(driver.StudyError, match="cannot resolve"):
        driver.cgroup_path("1:name=systemd:/foo\n")
    with pytest.raises(driver.StudyError, match="cannot resolve"):
        driver.cgroup_path("0::/../escape\n")
    appearing_control = root / "system.slice/io.max"
    appearing_control.write_text("254:0 rbps=max\n", encoding="utf-8")
    changed_snapshot = driver.io_control_snapshot(cgroup_root=root, cgroup_text=fixture["cgroup_text"])
    assert snapshot != changed_snapshot
    appearing_control.unlink()
    transient_peer = root / "cgprofile.slice"
    (transient_peer / "io.weight").unlink()
    transient_peer.rmdir()
    assert driver.io_control_snapshot(cgroup_root=root, cgroup_text=fixture["cgroup_text"]) == snapshot
    transient_peer.mkdir()
    (transient_peer / "io.latency").write_text("\n", encoding="utf-8")
    assert driver.io_control_snapshot(cgroup_root=root, cgroup_text=fixture["cgroup_text"]) == snapshot

    original_read_text = Path.read_text

    def proc_cgroup(path, *args, **kwargs):
        if path == Path("/proc/self/cgroup"):
            return fixture["cgroup_text"]
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", proc_cgroup)
    assert driver.io_control_snapshot(cgroup_root=root)["io_cost"] == snapshot["io_cost"]


def test_io_control_snapshot_accepts_root_cgroup_membership(tmp_path):
    root, _fixture = materialize_real_cgroup_layout(tmp_path)
    snapshot = driver.io_control_snapshot(cgroup_root=root, cgroup_text="0::/\n")
    assert [row["path"] for row in snapshot["cgroups"]] == [str(root)]
    assert snapshot["cgroups"][0]["files"] == {
        "io.max": None, "io.weight": None, "io.latency": None}


def test_io_control_snapshot_accepts_present_but_inactive_controls(tmp_path):
    root, fixture = materialize_real_cgroup_layout(tmp_path)
    benchmark = root / "system.slice/study.service"
    benchmark.mkdir()
    (benchmark / "io.max").write_text(
        "254:0 rbps=max wbps=max riops=max wiops=max\n", encoding="utf-8")
    (benchmark / "io.weight").write_text("default 100\n\n254:0 100\n", encoding="utf-8")
    (benchmark / "io.latency").write_text("\n", encoding="utf-8")
    snapshot = driver.io_control_snapshot(
        cgroup_root=root, cgroup_text="0::/system.slice/study.service\n")
    assert snapshot["cgroups"][-1]["files"] == {
        "io.max": "254:0 rbps=max wbps=max riops=max wiops=max",
        "io.weight": "default 100\n\n254:0 100",
        "io.latency": "",
    }
    assert snapshot["io_cost"] == {
        name: fixture["groups"]["."][name].strip()
        for name in ("io.cost.qos", "io.cost.model")
    }


@pytest.mark.parametrize(
    ("relative", "filename", "contents", "message"),
    [
        ("system.slice", "io.max", "254:0 rbps=100\n", "active io.max limit"),
        ("system.slice", "io.weight", "default 50\n", "non-default io.weight"),
        ("system.slice", "io.latency", "254:0 target=1000\n", "active io.latency"),
        ("cgprofile.slice", "io.latency", "254:0 target=1000\n", "active io.latency"),
        (".", "io.cost.qos", "254:0 enable=1 ctrl=auto\n", "active io.cost.qos limit"),
        (".", "io.cost.model", "254:0 ctrl=user model=linear rbps=1\n", "custom io.cost.model"),
    ],
)
def test_io_control_snapshot_refuses_each_active_limit_by_filename(
        tmp_path, relative, filename, contents, message):
    root, fixture = materialize_real_cgroup_layout(tmp_path)
    directory = root if relative == "." else root / relative
    (directory / filename).write_text(contents, encoding="utf-8")
    with pytest.raises(driver.StudyError, match=message) as caught:
        driver.io_control_snapshot(cgroup_root=root, cgroup_text=fixture["cgroup_text"])
    assert filename in str(caught.value)


@pytest.mark.parametrize(
    ("relative", "filename", "contents", "message"),
    [
        ("system.slice", "io.max", "8:0\n", "malformed io.max line"),
        ("system.slice", "io.max", "8:0 foo=max\n", "unknown or malformed io.max control"),
        ("system.slice", "io.max", "8:0 rbps=fast\n", "unknown or malformed io.max control"),
        ("system.slice", "io.max", "8:0 rbps=max rbps=max\n", "unknown or malformed io.max control"),
        ("system.slice", "io.weight", "bad\n", "malformed io.weight"),
        ("system.slice", "io.weight", "8:0 100\n", "no explicit default 100"),
        ("system.slice", "io.weight", "default 100\ndefault 100\n", "duplicate default"),
        ("system.slice", "io.weight", "default many\n", "malformed io.weight"),
        ("system.slice", "io.weight", "default 100\n8:0 100\n8:0 100\n", "duplicate device row"),
        ("system.slice", "io.weight", "default 100\n8:0 many\n", "malformed io.weight"),
        (".", "io.cost.qos", "254:0 enable=maybe\n", "no valid enable value"),
        (".", "io.cost.qos", "bad\n", "malformed io.cost.qos"),
        (".", "io.cost.qos", "254:0 mystery=1\n", "unknown or malformed io.cost.qos"),
        (".", "io.cost.qos", "254:0 enable\n", "malformed io.cost.qos"),
        (".", "io.cost.qos", "254:0 enable=0 enable=0\n", "unknown or malformed io.cost.qos"),
        (".", "io.cost.qos", "254:0 enable=0\n254:0 enable=0\n", "malformed io.cost.qos"),
        (".", "io.cost.qos", "254:0 enable=0 ctrl=broken\n", "malformed io.cost.qos controller"),
        (".", "io.cost.qos", "254:0 enable=0 rlat=fast\n", "malformed io.cost.qos value"),
        (".", "io.cost.qos", "254:0 enable=0 rpct=101\n", "malformed io.cost.qos value"),
        (".", "io.cost.qos", "254:0 enable=0 min=0\n", "malformed io.cost.qos value"),
        (".", "io.cost.qos", "254:0 enable=0 max=10001\n", "malformed io.cost.qos value"),
        (".", "io.cost.model", "254:0 ctrl=auto model=linear mystery=1\n", "unknown or malformed io.cost.model"),
        (".", "io.cost.model", "254:0 ctrl=auto model=linear rbps=fast\n", "malformed io.cost.model value"),
    ],
)
def test_io_control_snapshot_refuses_weird_control_values(
        tmp_path, relative, filename, contents, message):
    root, fixture = materialize_real_cgroup_layout(tmp_path)
    directory = root if relative == "." else root / relative
    (directory / filename).write_text(contents, encoding="utf-8")
    with pytest.raises(driver.StudyError, match=message) as caught:
        driver.io_control_snapshot(cgroup_root=root, cgroup_text=fixture["cgroup_text"])
    assert filename in str(caught.value)


def test_io_control_snapshot_refuses_existing_unreadable_io_max(tmp_path, monkeypatch):
    root, fixture = materialize_real_cgroup_layout(tmp_path)
    path = root / "system.slice/io.max"
    path.write_text("", encoding="utf-8")
    original_read_text = Path.read_text

    def unreadable(candidate, *args, **kwargs):
        if candidate == path:
            raise PermissionError("fixture denies read")
        return original_read_text(candidate, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", unreadable)
    with pytest.raises(driver.StudyError, match="cannot read existing cgroup IO control.*io.max"):
        driver.io_control_snapshot(cgroup_root=root, cgroup_text=fixture["cgroup_text"])


def test_io_control_snapshot_refuses_control_path_that_cannot_be_inspected(tmp_path, monkeypatch):
    root, fixture = materialize_real_cgroup_layout(tmp_path)
    path = root / "system.slice/io.max"
    path.write_text("", encoding="utf-8")
    original_lstat = Path.lstat

    def inaccessible(candidate):
        if candidate == path:
            raise PermissionError("fixture denies stat")
        return original_lstat(candidate)

    monkeypatch.setattr(Path, "lstat", inaccessible)
    with pytest.raises(driver.StudyError, match="cannot inspect existing cgroup IO control.*io.max"):
        driver.io_control_snapshot(cgroup_root=root, cgroup_text=fixture["cgroup_text"])


def test_absent_io_control_requires_an_enumerable_parent(tmp_path):
    with pytest.raises(driver.StudyError, match="cannot enumerate cgroup IO control parent"):
        driver._read_optional_control(tmp_path / "missing/io.max")


def test_io_control_snapshot_refuses_unreadable_cgroup_membership(tmp_path, monkeypatch):
    root, _fixture = materialize_real_cgroup_layout(tmp_path)
    original_read_text = Path.read_text

    def unreadable(candidate, *args, **kwargs):
        if candidate == Path("/proc/self/cgroup"):
            raise PermissionError("fixture denies membership read")
        return original_read_text(candidate, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", unreadable)
    with pytest.raises(driver.StudyError, match="cannot read unified cgroup membership.*proc/self/cgroup"):
        driver.io_control_snapshot(cgroup_root=root)


def test_io_control_snapshot_refuses_cgroup_that_cannot_be_enumerated(tmp_path):
    root, fixture = materialize_real_cgroup_layout(tmp_path)
    (root / "system.slice/io.max").unlink()
    (root / "system.slice/io.weight").unlink()
    (root / "system.slice").rmdir()
    with pytest.raises(driver.StudyError, match="cannot enumerate benchmark cgroup.*system.slice"):
        driver.io_control_snapshot(cgroup_root=root, cgroup_text=fixture["cgroup_text"])


def test_io_control_snapshot_refuses_unenumerable_latency_peers(tmp_path, monkeypatch):
    root, fixture = materialize_real_cgroup_layout(tmp_path)
    original_iterdir = Path.iterdir

    def unavailable_peers(path):
        if path == root:
            raise PermissionError("fixture denies cgroup enumeration")
        return original_iterdir(path)

    monkeypatch.setattr(Path, "iterdir", unavailable_peers)
    with pytest.raises(driver.StudyError, match="cannot enumerate benchmark cgroup peers"):
        driver.io_control_snapshot(cgroup_root=root, cgroup_text=fixture["cgroup_text"])


def test_io_control_snapshot_refuses_unreadable_latency_peer_entry(tmp_path, monkeypatch):
    root, fixture = materialize_real_cgroup_layout(tmp_path)
    peer = root / "cgprofile.slice"
    original_stat = Path.stat

    def inaccessible(candidate, *args, **kwargs):
        if candidate == peer:
            raise PermissionError("fixture denies peer stat")
        return original_stat(candidate, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", inaccessible)
    with pytest.raises(driver.StudyError, match="cannot enumerate benchmark cgroup peers"):
        driver.io_control_snapshot(cgroup_root=root, cgroup_text=fixture["cgroup_text"])


def test_io_control_snapshot_ignores_peer_removed_after_parent_listing(tmp_path, monkeypatch):
    root, fixture = materialize_real_cgroup_layout(tmp_path)
    removed_peer = root / "cgprofile.slice"
    original_stat = Path.stat

    def removed(candidate, *args, **kwargs):
        if candidate == removed_peer:
            raise FileNotFoundError("fixture removed peer after readdir")
        return original_stat(candidate, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", removed)
    snapshot = driver.io_control_snapshot(cgroup_root=root, cgroup_text=fixture["cgroup_text"])
    assert [row["path"] for row in snapshot["cgroups"]] == [str(root), str(root / "system.slice")]


def test_io_control_snapshot_ignores_peer_removed_before_latency_read(tmp_path, monkeypatch):
    root, fixture = materialize_real_cgroup_layout(tmp_path)
    peer = root / "cgprofile.slice"
    latency = peer / "io.latency"
    original_stat = Path.stat
    original_lstat = Path.lstat
    peer_stat_count = 0

    def disappearing_stat(candidate, *args, **kwargs):
        nonlocal peer_stat_count
        if candidate == peer:
            peer_stat_count += 1
            if peer_stat_count > 1:
                raise FileNotFoundError("fixture removed peer after readdir")
        return original_stat(candidate, *args, **kwargs)

    def disappearing_lstat(candidate, *args, **kwargs):
        if candidate == latency:
            raise FileNotFoundError("fixture removed peer control after stat")
        return original_lstat(candidate, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", disappearing_stat)
    monkeypatch.setattr(Path, "lstat", disappearing_lstat)
    snapshot = driver.io_control_snapshot(cgroup_root=root, cgroup_text=fixture["cgroup_text"])
    assert [row["path"] for row in snapshot["cgroups"]] == [str(root), str(root / "system.slice")]


def test_io_control_snapshot_ignores_peer_control_removed_during_read(tmp_path, monkeypatch):
    root, fixture = materialize_real_cgroup_layout(tmp_path)
    latency = root / "cgprofile.slice/io.latency"
    latency.write_text("\n", encoding="utf-8")
    original_read_text = Path.read_text

    def removed_read(candidate, *args, **kwargs):
        if candidate == latency:
            raise FileNotFoundError("fixture removed peer control during read")
        return original_read_text(candidate, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", removed_read)
    snapshot = driver.io_control_snapshot(cgroup_root=root, cgroup_text=fixture["cgroup_text"])
    assert [row["path"] for row in snapshot["cgroups"]] == [str(root), str(root / "system.slice")]


def test_io_control_snapshot_refuses_latency_peer_missing_from_parent_listing(tmp_path, monkeypatch):
    root, fixture = materialize_real_cgroup_layout(tmp_path)
    original_iterdir = Path.iterdir

    def omit_benchmark_peer(path):
        entries = list(original_iterdir(path))
        if path == root:
            entries = [entry for entry in entries if entry != root / "system.slice"]
        return iter(entries)

    monkeypatch.setattr(Path, "iterdir", omit_benchmark_peer)
    with pytest.raises(driver.StudyError, match="system.slice is not a child cgroup"):
        driver.io_control_snapshot(cgroup_root=root, cgroup_text=fixture["cgroup_text"])


def test_io_control_snapshot_refuses_non_directory_cgroup_path(tmp_path):
    root, fixture = materialize_real_cgroup_layout(tmp_path)
    (root / "system.slice/io.max").unlink()
    (root / "system.slice/io.weight").unlink()
    (root / "system.slice").rmdir()
    (root / "system.slice").write_text("not a cgroup", encoding="utf-8")
    with pytest.raises(driver.StudyError, match="cannot enumerate benchmark cgroup.*not a directory"):
        driver.io_control_snapshot(cgroup_root=root, cgroup_text=fixture["cgroup_text"])


def test_io_control_snapshot_treats_absent_cost_controls_as_no_setting(tmp_path):
    root, fixture = materialize_real_cgroup_layout(tmp_path)
    (root / "io.cost.qos").unlink()
    (root / "io.cost.model").unlink()
    snapshot = driver.io_control_snapshot(cgroup_root=root, cgroup_text=fixture["cgroup_text"])
    assert snapshot["io_cost"] == {"io.cost.qos": None, "io.cost.model": None}


def test_io_control_snapshot_treats_empty_cost_files_as_no_setting(tmp_path):
    root, fixture = materialize_real_cgroup_layout(tmp_path)
    (root / "io.cost.qos").write_text(
        "254:0 enable=0\n\n254:1 enable=0\n", encoding="utf-8")
    (root / "io.cost.model").write_text("\n", encoding="utf-8")
    snapshot = driver.io_control_snapshot(cgroup_root=root, cgroup_text=fixture["cgroup_text"])
    assert snapshot["io_cost"] == {
        "io.cost.qos": "254:0 enable=0\n\n254:1 enable=0", "io.cost.model": ""}


def test_state_schema_resume_identity_and_timestamp_guards(tmp_path):
    path = tmp_path / driver.STATE_NAME
    with pytest.raises(driver.StudyError, match="requires size and PARTUUID"):
        driver.empty_state(driver.dt.datetime.now(driver.dt.UTC).isoformat(),
                           "raw", "/dev/vda5", "vda5")
    initial = driver.load_state(path, "raw", "/dev/vda5", "vda5", FAKE_IDENTITY)
    assert initial["write_bytes"] == 0 and initial["cells"] == {}
    driver.write_json(path, initial)
    path.write_text("{", encoding="utf-8")
    with pytest.raises(driver.StudyError, match="study state is unreadable"):
        driver.load_state(path, "raw", "/dev/vda5", "vda5", FAKE_IDENTITY)
    driver.write_json(path, initial)
    resumed = driver.load_state(path, "raw", "/dev/vda5", "vda5", FAKE_IDENTITY)
    assert resumed["targets"]["raw"] == {"target": "/dev/vda5", "device": "vda5",
                                          **FAKE_IDENTITY.as_json()}
    with pytest.raises(driver.StudyError, match="requires current size and PARTUUID"):
        driver.load_state(path, "raw", "/dev/vda5", "vda5")
    malformed_identity = json.loads(json.dumps(initial))
    malformed_identity["targets"]["raw"]["size_bytes"] = 0
    driver.write_json(path, malformed_identity)
    with pytest.raises(driver.StudyError, match="raw target size/PARTUUID identity"):
        driver.load_state(path, "raw", "/dev/vda5", "vda5", FAKE_IDENTITY)
    driver.write_json(path, initial)
    with pytest.raises(driver.StudyError, match="target identity changed"):
        driver.load_state(path, "raw", "/dev/other", "vda5", FAKE_IDENTITY)
    with pytest.raises(driver.StudyError, match="target identity changed"):
        driver.load_state(path, "raw", "/dev/vda5", "vda5",
                          driver.TargetIdentity(FAKE_IDENTITY.size_bytes, "beef-9999"))
    with pytest.raises(driver.StudyError, match="target identity changed"):
        driver.load_state(path, "raw", "/dev/vda5", "vda5",
                          driver.TargetIdentity(FAKE_IDENTITY.size_bytes + 1,
                                                FAKE_IDENTITY.partuuid))
    with pytest.raises(driver.StudyError, match="do not share the requested backing device"):
        driver.load_state(path, "raw", "/dev/vda5", "vdb5", FAKE_IDENTITY)
    with pytest.raises(driver.StudyError, match="unsupported"):
        driver.write_json(path, {"schema": 9})
        driver.load_state(path, "raw", "/dev/vda5", "vda5", FAKE_IDENTITY)
    driver.write_json(path, {"schema": True})
    with pytest.raises(driver.StudyError, match="unsupported"):
        driver.load_state(path, "raw", "/dev/vda5", "vda5", FAKE_IDENTITY)
    with pytest.raises(driver.StudyError, match="missing required"):
        driver.write_json(path, {"schema": driver.STATE_SCHEMA})
        driver.load_state(path, "raw", "/dev/vda5", "vda5", FAKE_IDENTITY)
    for bad_manifest in (None, {"schema": 2, "stop_policy": None, "target_safety": {}},
                         {"schema": True, "stop_policy": None, "target_safety": {}},
                         {"schema": driver.RUN_MANIFEST_SCHEMA, "stop_policy": None,
                          "target_safety": []},
                         {"schema": driver.RUN_MANIFEST_SCHEMA, "stop_policy": "bad",
                          "target_safety": {}}):
        malformed_manifest = {**initial, "run_manifest": bad_manifest}
        driver.write_json(path, malformed_manifest)
        with pytest.raises(driver.StudyError, match="run manifest is malformed"):
            driver.load_state(path, "raw", "/dev/vda5", "vda5", FAKE_IDENTITY)
    malformed = {"schema": driver.STATE_SCHEMA, "started_utc": "bad", "write_bytes": 0,
                 "active_seconds": 0, "targets": {}, "cells": {},
                 "run_manifest": {"schema": driver.RUN_MANIFEST_SCHEMA,
                                  "stop_policy": None, "target_safety": {},
                                  "target_safety_attempts": {}}}
    driver.write_json(path, malformed)
    with pytest.raises(driver.StudyError, match="timestamp is malformed"):
        driver.state_elapsed(driver.load_state(path, "raw", "/dev/vda5", "vda5", FAKE_IDENTITY))
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
        driver.load_state(path, "raw", "/dev/vda5", "vda5", FAKE_IDENTITY)
    malformed_active = {**malformed_counts, "write_bytes": 0, "active_seconds": True}
    driver.write_json(path, malformed_active)
    with pytest.raises(driver.StudyError, match="fields are malformed"):
        driver.load_state(path, "raw", "/dev/vda5", "vda5", FAKE_IDENTITY)
    malformed_reservations = {**malformed, "started_utc": driver.dt.datetime.now(driver.dt.UTC).isoformat(),
                              "write_bytes": 1, "active_seconds": 0,
                              "targets": {"raw": {"target": "/dev/vda5", "device": "vda5"}},
                              "cells": {}, "write_reservations": {"cell": [2]}}
    driver.write_json(path, malformed_reservations)
    with pytest.raises(driver.StudyError, match="exceed the write meter"):
        driver.load_state(path, "raw", "/dev/vda5", "vda5", FAKE_IDENTITY)
    malformed_target = {**malformed_reservations, "write_bytes": 0, "write_reservations": {},
                        "targets": {"ext4-file": {"target": "/mnt/file", "device": "sdb"}}}
    driver.write_json(path, malformed_target)
    with pytest.raises(driver.StudyError, match="share the requested backing device"):
        driver.load_state(path, "raw", "/dev/vda5", "vda5", FAKE_IDENTITY)
    corrupt = tmp_path / "corrupt.json"
    corrupt.write_text("{", encoding="utf-8")
    with pytest.raises(driver.StudyError, match="unreadable"):
        driver.load_state(corrupt, "raw", "/dev/vda5", "vda5", FAKE_IDENTITY)


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


def test_run_dry_run_validates_and_renders_without_writing(tmp_path):
    host = FakeHost()
    out = tmp_path / "not-created"
    result = run_set(out, "raw-t0", host=host, dry_run=True)
    assert result["dry_run"] is True and result["jobs_rendered"] > 0
    assert result["host_liveness_preflight"]["responsive"] is True
    assert not out.exists()
    assert host.quiet_checked == 0 and host.liveness_calls == 1
    assert host.discards == [] and host.fio.calls == []


def test_stop_policy_documentation_maps_manifest_fields_to_run_options(tmp_path):
    readme = " ".join((driver.HERE / "README.md").read_text(encoding="utf-8").split())
    manifest = run_set(tmp_path / "dry-run", "raw-t0", host=FakeHost(), dry_run=True)["run_manifest"]
    policy = manifest["stop_policy"]
    mapping = {
        "memory_psi_full_avg10_max_percent": "--max-memory-psi",
        "min_memavailable_bytes": "--min-memavailable",
        "min_free_bytes": "--min-free",
        "max_probe_latency_seconds": "--max-probe-latency-seconds",
        "probe_consecutive_samples": "--probe-consecutive-samples",
        "probe_interval_seconds": "--probe-interval-seconds",
    }
    assert set(mapping) == set(policy) - {"io_psi"}
    parser = driver.build_parser()
    run_parser = next(action.choices["run"] for action in parser._actions
                      if "run" in (getattr(action, "choices", None) or {}))
    option_strings = run_parser._option_string_actions
    for policy_key, option in mapping.items():
        assert f"`{policy_key}` to `{option}`" in readme
        assert option in option_strings


def test_acceptance_runbook_pins_the_expected_host_before_proceeding(tmp_path):
    acceptance = (driver.HERE / "ACCEPTANCE.md").read_text(encoding="utf-8")
    match = re.search(r"(?ms)^assert_expected_host\(\) \{\n.*?^\}", acceptance)
    assert match is not None
    bash_scripts = re.findall(r"(?ms)^```bash\n(.*?)^```", acceptance)
    call_sites = [line.strip() for script in bash_scripts for line in script.splitlines()
                  if line.strip() == "assert_expected_host"]
    assert len(call_sites) == 3
    preflight = acceptance.split("## 1. Target-read-only inventory", 1)[0]
    preflight_script = re.search(r"(?ms)^```bash\n(.*?)^```", preflight)
    assert preflight_script is not None
    preflight_call = re.search(r"(?m)^assert_expected_host$", preflight_script.group(1))
    assert preflight_call is not None
    assert preflight_call.start() < preflight_script.group(1).index("host-escape -- lsblk")
    assert 'host-escape -- systemctl is-active "$UNKNOWN_UNIT"' in preflight_script.group(1)
    assert preflight_script.group(1).index('systemctl is-active "$UNKNOWN_UNIT"') < \
        preflight_script.group(1).index("host-escape -- lsblk")
    probe_start_marker = 'UNKNOWN_UNIT="ibx-acceptance-unknown-${BASHPID}.service"'
    probe_end_marker = (
        "printf 'missing-unit systemd status: %s (%s)\\n' "
        '"$UNKNOWN_STATE" "$UNKNOWN_STATUS"'
    )
    probe_start = preflight_script.group(1).index(probe_start_marker)
    probe_end = preflight_script.group(1).index(probe_end_marker, probe_start) + len(probe_end_marker)
    missing_unit_probe = preflight_script.group(1)[probe_start:probe_end]
    assert missing_unit_probe.startswith(probe_start_marker)
    assert missing_unit_probe.endswith(probe_end_marker)
    raw_idle = re.search(r"(?ms)^assert_raw_idle\(\) \{\n(.*?)^\}", acceptance)
    assert raw_idle is not None
    assert raw_idle.group(1).lstrip().startswith("assert_expected_host")
    btrfs_section = acceptance.split("## 2. Btrfs file and real fio-log acceptance", 1)[1].split(
        "## Rollback", 1)[0]
    btrfs_script = re.search(r"(?ms)^```bash\n(.*?)^```", btrfs_section)
    assert btrfs_script is not None
    btrfs_call = re.search(r"(?m)^assert_raw_idle$", btrfs_script.group(1))
    assert btrfs_call is not None
    assert btrfs_call.start() < btrfs_script.group(1).index('host-escape -- mkfs.btrfs -K "$RAW"')
    rollback_section = acceptance.split("## Rollback", 1)[1]
    rollback_script = re.search(r"(?ms)^```bash\n(.*?)^```", rollback_section)
    assert rollback_script is not None
    rollback = rollback_script.group(1)
    assert rollback.index("assert_expected_host") < rollback.index('host-escape -- umount "$MOUNT"')
    assert rollback.index('host-escape -- umount "$MOUNT"') < rollback.index("assert_raw_idle")
    assert rollback.index("assert_raw_idle") < rollback.index('host-escape -- wipefs')
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    host_escape = fake_bin / "host-escape"
    host_escape.write_text(
        "#!/bin/sh\n"
        "[ \"$1\" = -- ] || exit 96\n"
        "shift\n"
        "if [ \"$1\" = systemctl ] && [ \"$2\" = is-active ]; then\n"
        "  printf '%s\\n' \"$FAKE_UNIT_STATE\"\n"
        "  exit \"$FAKE_UNIT_STATUS\"\n"
        "fi\n"
        "[ \"$1\" = hostname ] && [ \"$2\" = -s ] || exit 97\n"
        "printf '%s\\n' \"$FAKE_ACTUAL_HOST\"\n",
        encoding="utf-8",
    )
    host_escape.chmod(0o755)
    probe_script = missing_unit_probe

    def invoke_probe(status, state):
        env = {"PATH": f"{fake_bin}:{os.defpath}", "FAKE_UNIT_STATUS": str(status),
               "FAKE_UNIT_STATE": state}
        return subprocess.run(["bash", "-euo", "pipefail", "-c", probe_script], env=env,
                              check=False, capture_output=True, text=True)

    for status, state in ((4, "unknown"), (4, "inactive"), (3, "inactive"), (1, "failed")):
        accepted = invoke_probe(status, state)
        assert accepted.returncode == 0, (status, state, accepted.stderr)
        assert f"missing-unit systemd status: {state} ({status})" in accepted.stdout
    for status, state in ((0, "inactive"), (0, "active"), (4, ""), (3, "weird")):
        refused = invoke_probe(status, state)
        assert refused.returncode != 0, (status, state, refused.stdout)
        assert "unexpected" in refused.stderr
    assert 'case "$UNKNOWN_STATUS:$UNKNOWN_STATE" in' in missing_unit_probe
    assert "4:unknown) ;;" in missing_unit_probe
    assert "*:inactive|*:failed)" in missing_unit_probe
    assert "((UNKNOWN_STATUS != 0))" in missing_unit_probe

    helper_and_call = f"{match.group(0)}\nassert_expected_host"

    def invoke(*, actual_host, expected_host=None):
        env = {"PATH": f"{fake_bin}:{os.defpath}", "FAKE_ACTUAL_HOST": actual_host}
        if expected_host is not None:
            env["IBX_EXPECTED_HOST"] = expected_host
        return subprocess.run(
            ["bash", "-euo", "pipefail", "-c", helper_and_call],
            env=env,
            check=False,
            capture_output=True,
            text=True,
        )

    assert invoke(actual_host="compute-a", expected_host="compute-a").returncode == 0
    mismatch = invoke(actual_host="compute-b", expected_host="compute-a")
    assert mismatch.returncode != 0 and "expected host compute-a" in mismatch.stderr
    missing = invoke(actual_host="compute-a")
    assert missing.returncode != 0 and "set the exact short hostname" in missing.stderr


def test_acceptance_refusal_helper_continues_and_reports_a_failing_summary():
    acceptance = (driver.HERE / "ACCEPTANCE.md").read_text(encoding="utf-8")
    section = acceptance.split("## 1. Target-read-only inventory", 1)[1].split(
        "## 2. Btrfs file and real fio-log acceptance", 1)[0]
    script_match = re.search(r"(?ms)^```bash\n(.*?)^```", section)
    assert script_match is not None
    expect_refusal = re.search(r"(?ms)^expect_refusal\(\) \{\n.*?^\}", script_match.group(1))
    record_check = re.search(r"(?ms)^record_check\(\) \{\n.*?^\}", script_match.group(1))
    acknowledged_dry_run = re.search(
        r"(?ms)^check_acknowledged_dry_run\(\) \{\n.*?^\}", script_match.group(1))
    whole_disk_refusal = re.search(
        r"(?ms)^check_whole_disk_refusal\(\) \{\n.*?^\}", script_match.group(1))
    summary_tail = re.search(
        r"(?ms)^printf 'SECTION 1 summary:.*?^printf 'SECTION1-PASS\\n'\n", script_match.group(1))
    assert (expect_refusal is not None and record_check is not None and
            acknowledged_dry_run is not None and whole_disk_refusal is not None)
    assert summary_tail is not None
    assert "set +e" not in expect_refusal.group(0) and "set -e" not in expect_refusal.group(0)
    assert "SECTION 1 summary:" in script_match.group(1)
    shell = f"""set -euo pipefail
CHECK_COUNT=0
FAIL_COUNT=0
{expect_refusal.group(0)}
{record_check.group(0)}
{acknowledged_dry_run.group(0)}
{whole_disk_refusal.group(0)}
RAW=/dev/vda5
IBX_TARGET_CONFIRMATION=/dev/vda5:34359738368:00000000-0000-0000-0000-000000000000
current_system_disk_ack() {{ printf 'compute-a:vda:vda3:partuuid\\n'; }}
raw_dry_run() {{
  if [[ "$1" == /dev/vda ]]; then
    printf '%s\\n' 'raw target must be a dedicated partition; whole disks are refused'
    return 2
  fi
  printf '%s\\n' '{{"dry_run": true}}'
}}
host-escape() {{ printf '34359738368\\n'; }}
fail_command() {{ printf 'unexpected command failure\\n'; return 1; }}
pass_command() {{ return 0; }}
record_check 'acknowledged dry-run' check_acknowledged_dry_run
record_check 'whole-disk refusal' check_whole_disk_refusal
record_check 'unexpected refusal' expect_refusal 'expected refusal text' fail_command
record_check 'following check' pass_command
{summary_tail.group(0)}
"""
    result = subprocess.run(["bash", "-c", shell], check=False, capture_output=True, text=True)
    assert result.returncode == 1
    assert "FAIL check: unexpected refusal" in result.stderr
    assert "PASS check: acknowledged dry-run" in result.stdout
    assert "PASS check: whole-disk refusal" in result.stdout
    assert "PASS check: following check" in result.stdout
    assert "SECTION 1 summary: 4 checks, 1 failures" in result.stdout
    assert "SECTION1-FAIL" in result.stdout and "SECTION1-PASS" not in result.stdout


def test_ibx_runbooks_pin_host_before_driver_runs_and_raw_transitions():
    readme = (driver.HERE / "README.md").read_text(encoding="utf-8")
    bash_scripts = re.findall(r"(?ms)^```bash\n(.*?)^```", readme)
    script = next(script for script in bash_scripts if "current_system_disk_ack() {" in script)
    assert ': "${IBX_EXPECTED_HOST:?set the exact short hostname from approved run authorization}"' in script
    expected_host = re.search(r"(?ms)^assert_expected_host\(\) \{\n(.*?)^\}", script)
    assert expected_host is not None
    assert "host-escape -- hostname -s" in expected_host.group(1)
    assert '[[ $actual_host == "$IBX_EXPECTED_HOST" ]]' in expected_host.group(1)
    target_helper = re.search(r"(?ms)^assert_study_host_and_target\(\) \{\n(.*?)^\}", script)
    assert target_helper is not None
    assert target_helper.group(1).lstrip().startswith(
        "assert_expected_host || return 1\n  confirm_study_target || return 1")
    acceptance = (driver.HERE / "ACCEPTANCE.md").read_text(encoding="utf-8")
    acceptance_host = re.search(r"(?ms)^assert_expected_host\(\) \{\n(.*?)^\}", acceptance)
    assert acceptance_host is not None and expected_host.group(0) == acceptance_host.group(0)

    consumers = (driver.REPO / "libraries/iobench/docs/CONSUMERS.md").read_text(encoding="utf-8")
    consumer_scripts = re.findall(r"(?ms)^```bash\n(.*?)^```", consumers)
    consumer_script = next(script for script in consumer_scripts
                           if "current_system_disk_ack() {" in script)
    consumer_host = re.search(r"(?ms)^assert_expected_host\(\) \{\n(.*?)^\}", consumer_script)
    assert consumer_host is not None and consumer_host.group(0) == acceptance_host.group(0)
    consumer_ack = re.search(r"(?ms)^current_system_disk_ack\(\) \{\n(.*?)^\}", consumer_script)
    assert consumer_ack is not None
    assert consumer_ack.group(1).lstrip().startswith("assert_expected_host || return 1\n")
    consumer_run = re.search(r"(?ms)^run_study\(\) \{\n(.*?)^\}", consumer_script)
    assert consumer_run is not None
    assert consumer_run.group(1).lstrip().startswith("assert_expected_host || return 1\n")
    consumer_without_helper = consumer_script.replace(consumer_run.group(0), "", 1)
    assert 'host-escape -- python3 "$STUDY" run ' not in consumer_without_helper
    assert len(re.findall(r"(?m)^run_study ", consumer_without_helper)) == 2
    assert re.search(r'(?m)^assert_expected_host \|\| exit 1\npython3 "\$STUDY" plan.*\|\| exit 1$',
                     consumer_without_helper)

    ack_helper = re.search(r"(?ms)^current_system_disk_ack\(\) \{\n(.*?)^\}", script)
    assert ack_helper is not None
    assert ack_helper.group(1).lstrip().startswith("assert_study_host_and_target || return 1\n")
    run_helper = re.search(r"(?ms)^run_study\(\) \{\n(.*?)^\}", script)
    assert run_helper is not None
    assert run_helper.group(1).lstrip().startswith("assert_expected_host || return 1\n")
    without_helper = script.replace(run_helper.group(0), "", 1)
    assert 'host-escape -- python3 "$STUDY" run ' not in without_helper
    assert len(re.findall(r"(?m)^run_study ", without_helper)) >= 10
    assert re.search(r'(?m)^assert_study_host_and_target \|\| exit 1\nhost-escape -- python3 "\$STUDY" plan.*\|\| exit 1$',
                     without_helper)

    def assert_run_commands_fail_fast(source, minimum):
        source_lines = source.splitlines()
        count = 0
        for start, line in enumerate(source_lines):
            if not line.startswith("run_study "):
                continue
            end = start
            while source_lines[end].endswith(chr(92)):
                end += 1
            assert source_lines[end].endswith("|| exit 1")
            count += 1
        assert count >= minimum

    assert_run_commands_fail_fast(consumer_without_helper, 2)
    assert_run_commands_fail_fast(without_helper, 10)

    transition = re.search(r"(?ms)^study_transition\(\) \{\n(.*?)^\}", script)
    assert transition is not None
    assert transition.group(1).lstrip().startswith(
        "assert_study_host_and_target || return 1\n  host-escape -- \"$@\"")
    # Invocation grammar requires a command token after whitespace; the function
    # definition ends with "() {" and cannot be mistaken for a call.
    transitions = re.findall(r"(?m)^study_transition ([A-Za-z0-9_.-]+(?: .*)?)$", script)
    assert [line.split(maxsplit=1)[0] for line in transitions] == [
        "mkfs.ext4", "mkdir", "mount", "fallocate", "umount", "wipefs",
        "mkfs.btrfs", "mount", "fallocate", "umount", "wipefs", "blkdiscard",
    ]
    assert all(line.endswith("|| exit 1") for line in transitions)
    assert not re.search(r"(?m)^host-escape -- (mkfs|mkdir|mount|fallocate|umount|wipefs|blkdiscard)", script)


def test_dry_run_skips_active_timer_guard_while_live_run_still_requires_quiet_window(tmp_path):
    class ActiveTimerHost(FakeHost):
        def assert_quiet_window(self):
            self.quiet_checked += 1
            raise driver.StudyError("quiet-window timer fstrim.timer is active")

    dry_host = ActiveTimerHost()
    result = run_set(tmp_path / "dry", "raw-t0", host=dry_host, dry_run=True)
    assert result["dry_run"] is True
    assert dry_host.quiet_checked == 0 and dry_host.liveness_calls == 1

    live_host = ActiveTimerHost()
    with pytest.raises(driver.StudyError, match="timer fstrim.timer is active"):
        run_set(tmp_path / "live", "raw-t0", host=live_host)
    assert live_host.quiet_checked == 1 and live_host.discards == [] and live_host.fio.calls == []


def test_first_liveness_probe_is_required_before_live_io_and_dry_run_acceptance(tmp_path):
    failed = {"responsive": False, "latency_seconds": 0.1, "components": {}, "error": "sshd down"}
    live_host = FakeHost()
    live_host.liveness_samples = [failed]
    with pytest.raises(driver.StudyError, match="initial host-liveness probe"):
        run_set(tmp_path / "live", "raw-t0", host=live_host)
    assert live_host.discards == [] and live_host.fio.calls == []
    assert live_host.events.index("liveness") < next(
        (index for index, event in enumerate(live_host.events) if event == "discard"), len(live_host.events))

    dry_host = FakeHost()
    dry_host.liveness_samples = [failed]
    with pytest.raises(driver.StudyError, match="initial host-liveness probe"):
        run_set(tmp_path / "dry", "raw-t0", host=dry_host, dry_run=True)
    assert not (tmp_path / "dry").exists()
    assert dry_host.discards == [] and dry_host.fio.calls == [] and dry_host.quiet_checked == 0


@pytest.mark.parametrize("interval", (True, "10", 0, -1, 61, float("nan")))
def test_liveness_probe_interval_must_be_a_finite_bounded_number(tmp_path, interval):
    with pytest.raises(driver.StudyError, match="probe interval must be finite"):
        run_set(tmp_path, "raw-t0", dry_run=True, probe_interval_s=interval)


def test_run_dry_run_refuses_bad_output_and_monitor_samples(tmp_path, monkeypatch):
    host = FakeHost()
    out_file = tmp_path / "not-a-directory"
    out_file.write_text("occupied", encoding="utf-8")
    with pytest.raises(driver.StudyError, match="exists and is not a directory"):
        run_set(out_file, "raw-t0", host=host, dry_run=True)
    assert host.discards == [] and host.fio.calls == []

    host = FakeHost()
    original_is_dir = Path.is_dir
    monkeypatch.setattr(Path, "is_dir", lambda path: False)
    with pytest.raises(driver.StudyError, match="existing output filesystem"):
        run_set(tmp_path / "no-existing-parent", "raw-t0", host=host, dry_run=True)
    monkeypatch.setattr(Path, "is_dir", original_is_dir)

    low_space = FakeHost(free=0)
    with pytest.raises(driver.StudyError, match="free space fell below"):
        run_set(tmp_path / "low-space", "raw-t0", host=low_space, dry_run=True)
    high_io = FakeHost(psi_io=99)
    result = run_set(tmp_path / "high-io", "raw-t0", host=high_io, dry_run=True)
    assert result["dry_run"] is True and result["run_manifest"]["stop_policy"]["io_psi"].startswith("advisory")
    assert high_io.quiet_checked == 0 and high_io.discards == [] and high_io.fio.calls == []

    high_memory_full = FakeHost(psi_mem=0.1, psi_mem_full=99)
    with pytest.raises(driver.StudyError, match="memory PSI full exceeded threshold"):
        run_set(tmp_path / "high-memory-full", "raw-t0", host=high_memory_full, dry_run=True)
    assert high_memory_full.discards == [] and high_memory_full.fio.calls == []
    assert low_space.discards == [] and low_space.fio.calls == []


def test_run_rejects_file_confirmation_and_unmatched_validated_identity(tmp_path):
    host = FakeHost()
    with pytest.raises(driver.StudyError, match="applies only to raw"):
        driver.run_study("file-ext4", target="/mnt/ext4.dat", device="vda5", out=tmp_path,
                         target_kind="ext4-file", offset=0, size=24 * GIB,
                         mem_psi_max=20, min_memavailable=2 * GIB,
                         min_free=4 * GIB, max_probe_latency_s=1,
                         probe_consecutive_samples=3, host=host, quiet_window_confirmed=True,
                         confirm_target=f"/dev/fake:{FAKE_IDENTITY.size_bytes}:{FAKE_IDENTITY.partuuid}")
    host = FakeHost()
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(host, "validate_target", lambda *args, **kwargs:
                        driver.TargetValidation(
                            driver.TargetIdentity(FAKE_IDENTITY.size_bytes + 1, FAKE_IDENTITY.partuuid),
                            dict(host.system_disk)))
    try:
        with pytest.raises(driver.StudyError, match="validated raw target identity"):
            run_set(tmp_path / "wrong-identity", "raw-t0", host=host)
    finally:
        monkeypatch.undo()


def test_system_disk_opt_in_requires_both_flags_and_raw_target(tmp_path):
    ack = "fixture-host:vda:vda3:f00d-1234"
    with pytest.raises(driver.StudyError, match="must be supplied together"):
        run_set(tmp_path / "missing-ack", "raw-t0", allow_system_disk_partition=True)
    with pytest.raises(driver.StudyError, match="must be supplied together"):
        run_set(tmp_path / "missing-flag", "raw-t0", system_disk_ack=ack)
    with pytest.raises(driver.StudyError, match="applies only to raw partition runs"):
        run_set(tmp_path / "file-target", "file-ext4", target_type="ext4-file",
                target="/mnt/target.dat", allow_system_disk_partition=True, system_disk_ack=ack)


def test_system_disk_ack_is_refused_for_a_separate_target_disk(tmp_path):
    ack = "fixture-host:vda:vda3:f00d-1234"
    with pytest.raises(driver.StudyError, match="applies only to a target on the running system disk"):
        run_set(tmp_path / "separate-disk", "raw-t0", allow_system_disk_partition=True,
                system_disk_ack=ack)


def test_resumed_cell_target_identity_is_rechecked(tmp_path):
    host = FakeHost()
    run_set(tmp_path, "raw-t0", host=host)
    result_path = next(path for path in (tmp_path / "cells").glob("*.json")
                       if json.loads(path.read_text(encoding="utf-8")).get("cell") not in
                       (None, "trim-state"))
    result = json.loads(result_path.read_text(encoding="utf-8"))
    result["target_identity"] = {"size_bytes": FAKE_IDENTITY.size_bytes + 1,
                                  "partuuid": FAKE_IDENTITY.partuuid}
    driver.write_json(result_path, result)
    with pytest.raises(driver.StudyError, match="cell result target identity changed"):
        run_set(tmp_path, "raw-t0", host=host)


def test_resume_refuses_changed_stable_identity_and_stop_policy(tmp_path):
    host = FakeHost()
    run_set(tmp_path / "system-facts", "raw-t0", host=host)
    host.system_disk["root_fs_devices"] = ["vda4"]
    with pytest.raises(driver.StudyError, match="stable system-disk target identity changed"):
        run_set(tmp_path / "system-facts", "raw-t0", host=host)

    host = FakeHost()
    root = tmp_path / "policy"
    run_set(root, "raw-t0", host=host)
    with pytest.raises(driver.StudyError, match="stop policy changed.*preserve.*meters.*Recorded stop policy.*new --out directory resets"):
        run_set(root, "raw-t0", host=host, max_probe_latency_s=2.0)


def test_resume_records_changed_mount_and_swap_context_per_attempt(tmp_path):
    host = FakeHost()
    host.system_disk.update({"root_disk_shared": True,
                             "system_mounts_on_disk": ["/", "/boot"],
                             "sibling_swap_devices": ["vda6"],
                             "system_disk_acknowledgement": "fixture-host:vda:vda3:f00d-1234"})
    root = tmp_path / "attempt-history"
    run_set(root, "raw-t0", host=host, allow_system_disk_partition=True,
            system_disk_ack=host.system_disk["system_disk_acknowledgement"])
    host.system_disk["system_mounts_on_disk"] = ["/", "/boot", "/srv/cow"]
    host.system_disk["sibling_swap_devices"] = ["vda6", "vda7"]
    resumed = run_set(root, "raw-t0", host=host, allow_system_disk_partition=True,
                      system_disk_ack=host.system_disk["system_disk_acknowledgement"])
    attempts = resumed["run_manifest"]["target_safety_attempts"]["raw"]
    assert len(attempts) == 2
    assert attempts[0]["facts"]["system_mounts_on_disk"] == ["/", "/boot"]
    assert attempts[1]["facts"]["system_mounts_on_disk"] == ["/", "/boot", "/srv/cow"]
    assert attempts[1]["facts"]["sibling_swap_devices"] == ["vda6", "vda7"]


@pytest.mark.parametrize("bad_history", ([], {"raw": "bad"}))
def test_run_refuses_malformed_system_disk_attempt_history(tmp_path, monkeypatch, bad_history):
    state = driver.empty_state(driver.dt.datetime.now(driver.dt.UTC).isoformat(),
                               "raw", "/dev/fake", "vda5", FAKE_IDENTITY)
    state["run_manifest"]["target_safety_attempts"] = bad_history
    monkeypatch.setattr(driver, "load_state", lambda *_args, **_kwargs: state)
    with pytest.raises(driver.StudyError, match="system-disk target-safety history is malformed"):
        run_set(tmp_path, "raw-t0")


def test_load_run_manifest_requires_complete_safety_and_stop_context(tmp_path):
    root = tmp_path / "manifest"
    root.mkdir()
    assert driver.load_run_manifest(root) is None
    state_path = root / driver.STATE_NAME
    state_path.write_text("{", encoding="utf-8")
    with pytest.raises(driver.StudyError, match="unreadable"):
        driver.load_run_manifest(root)
    state = driver.empty_state(driver.dt.datetime.now(driver.dt.UTC).isoformat(),
                               "raw", "/dev/fake", "vda5", FAKE_IDENTITY)
    state["run_manifest"]["stop_policy"] = {}
    driver.write_json(state_path, state)
    assert driver.load_run_manifest(root) == state["run_manifest"]
    malformed_states = [
        {**state, "schema": True},
        {**state, "run_manifest": None},
        {**state, "run_manifest": {"schema": 2, "stop_policy": {}, "target_safety": {}}},
        {**state, "run_manifest": {"schema": True, "stop_policy": {}, "target_safety": {}}},
        {**state, "run_manifest": {"schema": driver.RUN_MANIFEST_SCHEMA,
                                    "stop_policy": {}, "target_safety": []}},
        {**state, "run_manifest": {"schema": driver.RUN_MANIFEST_SCHEMA,
                                    "stop_policy": {}, "target_safety": {},
                                    "target_safety_attempts": []}},
        {**state, "run_manifest": {"schema": driver.RUN_MANIFEST_SCHEMA,
                                    "stop_policy": None, "target_safety": {}}},
    ]
    for malformed in malformed_states:
        driver.write_json(state_path, malformed)
        with pytest.raises(driver.StudyError, match="run manifest is missing or malformed"):
            driver.load_run_manifest(root)


def test_run_refuses_read_when_no_written_extent_was_recorded(tmp_path):
    class EmptyFillHost(FakeHost):
        def execute_fio(self, job_path, runtime_s, sample):
            if job_path.name.startswith("raw_t0_standard_fill"):
                return {"jobs": [{"write": {"io_bytes": 0, "bw_bytes": 0, "iops": 0}}]}, 0
            return super().execute_fio(job_path, runtime_s, sample)

    host = EmptyFillHost()
    with pytest.raises(driver.StudyError, match="has no recorded written extent"):
        run_set(tmp_path, "raw-t0", host=host)
    assert host.discards and host.fio.calls


def test_load_results_refuses_qd32_without_recorded_p99(tmp_path):
    cells = tmp_path / "cells"
    cells.mkdir()
    driver.write_json(cells / "bad-q32.json", {
        "target_type": "raw", "slot": "t0", "rung": "quick", "cell": "rate-read-q32",
        "number": "rate_read_q32", "value": 100,
        "fio": {"jobs": [{"read": {"clat_ns": {"percentile": {}}}}]},
    })
    with pytest.raises(driver.StudyError, match="missing a valid fio p99"):
        driver.load_results(tmp_path)


def test_raw_standard_rule_refuses_absent_and_incomplete_evidence(tmp_path):
    cells = tmp_path / "empty" / "cells"
    cells.mkdir(parents=True)
    assert driver.raw_standard_needed(cells.parent)
    driver.write_json(cells / "standard.json", {
        "target_type": "raw", "slot": "t0", "rung": "standard",
        "cell": "seq1m-read", "number": "rbps", "value": 100,
    })
    driver.write_json(cells / "quick-seq.json", {
        "target_type": "raw", "slot": "t0", "rung": "quick",
        "cell": "seq1m-read", "number": "rbps", "value": 100,
    })
    assert driver.raw_standard_needed(cells.parent)


def test_raw_standard_rule_skips_zero_boundaries_and_detects_moving_metric(tmp_path, monkeypatch):
    out = tmp_path / "zero-boundary"
    seed_raw_t6h_evidence(out)
    monkeypatch.setitem(driver.TOLERANCES, "coverage_zero", {
        "label": "coverage", "T": "0", "low": 0.0, "high": 0.0,
        "C": None, "visible": ("quick",), "why": "coverage"})
    cells = out / "cells"
    driver.write_json(cells / "standard-coverage-zero.json", {
        "target_type": "raw", "slot": "t0", "rung": "standard",
        "cell": "coverage-zero-reference", "number": "coverage_zero", "value": 1.0,
    })
    for rung in ("quick", "veryquick"):
        for slot, value in (("t0", 1.0), ("t3h", 2.0)):
            driver.write_json(cells / f"{rung}-{slot}-coverage-zero.json", {
                "target_type": "raw", "slot": slot, "rung": rung,
                "cell": f"coverage-zero-{slot}", "number": "coverage_zero", "value": value,
            })
    assert driver.raw_standard_needed(out)


def test_first_discard_waits_for_first_job_render(tmp_path, monkeypatch):
    host = FakeHost()
    original = driver.render_job
    monkeypatch.setattr(driver, "render_job", lambda job, **kwargs:
                        (_ for _ in ()).throw(driver.StudyError("render rejected"))
                        if job.name == "seq1m-write" else original(job, **kwargs))
    out = tmp_path / "render-refusal"
    with pytest.raises(driver.StudyError, match="render rejected"):
        run_set(out, "raw-t0", host=host)
    assert not out.exists() and host.discards == [] and host.fio.calls == []


def test_run_refuses_relative_output_before_discard(tmp_path):
    host = FakeHost()
    with pytest.raises(driver.StudyError, match="--out must be an absolute path"):
        run_set(Path("relative-out"), "raw-t0", host=host)
    assert host.discards == [] and host.fio.calls == []


def test_first_job_uses_its_prevalidated_render_after_discard(tmp_path, monkeypatch):
    host = FakeHost()
    original = driver.render_job
    original_discard = host.discard
    calls = []
    first_job_path = tmp_path / "jobs" / (re.sub(
        r"[^A-Za-z0-9_.-]", "_",
        driver.cell_key("raw", "t0", "standard", driver.rung_jobs("standard")[0])) + ".fio")
    checked_before_discard = []

    def record_render(job, **kwargs):
        calls.append((job.name, bool(host.discards)))
        return original(job, **kwargs)

    def check_prepared_job_before_discard(*args, **kwargs):
        assert first_job_path.is_file()
        assert "[seq1m-write]" in first_job_path.read_text(encoding="utf-8")
        checked_before_discard.append(True)
        return original_discard(*args, **kwargs)

    monkeypatch.setattr(driver, "render_job", record_render)
    monkeypatch.setattr(host, "discard", check_prepared_job_before_discard)
    run_set(tmp_path, "raw-t0", host=host)
    first_job_calls = [discarded for name, discarded in calls if name == "seq1m-write"]
    assert len(first_job_calls) == 3 and not any(first_job_calls)
    assert host.discards and checked_before_discard


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
    assert driver.parse_psi("\ninvalid nope\nfull avg10=3 avg60=4\n"
                            "some avg10=1.25 avg60=2 total=4\nbad avg10=nan\n") == {
        "full": {"avg10": 3.0, "avg60": 4.0},
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
    (sample / "pressure/io").write_text("some avg10=99 total=1\nfull avg10=100 total=1\n", encoding="utf-8")
    (sample / "pressure/memory").write_text("some avg10=0.2 total=2\nfull avg10=0.01 total=2\n", encoding="utf-8")
    (sample / "meminfo").write_text("MemTotal: 100 kB\nMemAvailable: 50 kB\n", encoding="utf-8")
    monitor = driver.monitor_sample("vda", proc=sample)
    assert monitor["diskstats"]["flushes_completed"] == 16
    assert monitor["psi"]["io"]["some"]["avg10"] == 99
    assert monitor["psi"]["memory"]["full"]["avg10"] == 0.01
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
                        "--max-memory-psi", "20", "--min-memavailable", "1GiB",
                        "--min-free", "1GiB", "--max-probe-latency-seconds", "1",
                        "--probe-consecutive-samples", "3", "--confirm-quiet-window"]) == 0
    assert json.loads(capsys.readouterr().out)["cell_set"] == "raw-t0"
    monkeypatch.setattr(driver, "run_study", lambda *args, **kwargs: (_ for _ in ()).throw(driver.StudyError("refused")))
    assert driver.main(["run", "raw-t0", "--target", "/dev/fake", "--device", "vda5",
                        "--offset", "0B", "--size", "24GiB", "--out", str(tmp_path),
                        "--max-memory-psi", "20", "--min-memavailable", "1GiB",
                        "--min-free", "1GiB", "--max-probe-latency-seconds", "1",
                        "--probe-consecutive-samples", "3", "--confirm-quiet-window"]) == 2
    assert "refused" in capsys.readouterr().err


def test_module_entrypoint_executes_plan(monkeypatch, capsys):
    monkeypatch.setattr(driver.sys, "argv", [str(driver.HERE / "driver.py"), "plan", "--json"])
    with pytest.raises(SystemExit) as exit_info:
        runpy.run_path(str(driver.HERE / "driver.py"), run_name="__main__")
    assert exit_info.value.code == 0
    assert json.loads(capsys.readouterr().out)["meter"]["used_bytes"] == 0


def test_state_reservation_and_meter_guards(tmp_path):
    now = driver.dt.datetime.now(driver.dt.UTC).isoformat()
    base = driver.empty_state(now, "raw", "/dev/vda5", "vda5", FAKE_IDENTITY)
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
            driver.load_state(path, "raw", "/dev/vda5", "vda5", FAKE_IDENTITY)

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


def test_run_study_snapshot_history_optional_standard_and_repeat_edges(tmp_path, monkeypatch):
    monkeypatch.setattr(driver, "DROPPED_ADAPTIVE_REPEAT_CELL_SETS", ())
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
    skipped = run_set(tmp_path / "optional", "file-ext4-standard", host=host,
                      target_type="ext4-file", target="/mnt/file")
    assert skipped["skipped"] is True and "not needed" in skipped["reason"]
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
                if job.name in ("rate-read-q32", "rate-write-q32"):
                    direction = "read" if job.name == "rate-read-q32" else "write"
                    row["fio"] = {"jobs": [{direction: {"clat_ns": {"percentile": {
                        "95.000000": 90_000, "99.000000": 100_000}}}}]}
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
    skipped = run_set(repeat_root, "raw-t12h", host=FakeHost())
    assert skipped["skipped"] is True and "20 percent" in skipped["reason"]
    with pytest.raises(driver.StudyError, match="needs 5 completed"):
        run_set(repeat_root, "raw-t12h2", host=FakeHost())


def test_resume_refuses_broken_links_and_adopts_valid_orphan_results(tmp_path, monkeypatch):
    jobs = [
        driver.Job("rate-read-q32", "randread", 4096, 32, 1, "read_iops_cap"),
        driver.Job("rate-write-q32", "randwrite", 4096, 32, 1, "write_iops_cap", 100),
        driver.Job("other-read", "read", 4096, 1, 1, "other_metric"),
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
    other_key = driver.cell_key("ext4-file", "t9h", "quick", jobs[2])
    expected_other = re.sub(r"[^A-Za-z0-9_.-]", "_", other_key) + ".json"

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
    for key, name, byte_count in ((read_key, expected_read, 0), (write_key, expected_write, 50),
                                  (other_key, expected_other, 0)):
        record = {"cell_key": key, "value": 100, "write_bytes": byte_count, "duration_s": 0}
        if key == write_key:
            record["estimates"] = {"bw": {"early_10s": 99}}
        driver.write_json(cells / name, record)
    host = FakeHost()
    outcome = run_set(out, "file-ext4", host=host, target_type="ext4-file", target="/mnt/file")
    state = json.loads((out / driver.STATE_NAME).read_text(encoding="utf-8"))
    assert outcome["results"] == 4 and state["write_bytes"] == 50
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
