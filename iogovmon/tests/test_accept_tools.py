"""The acceptance tooling in `tools/accept/` (stats.py, refprobe.py, synth_shift_check.py) and the IGM-22 minors of stage.py.

Host-free: temp dirs, fake /proc trees and a fake subprocess boundary only. Nothing here needs root, fio, systemd or a real block device, so every
test runs in the r0-r1 lane. `tools/` is outside the coverage scope (`--cov=iogovmon`); these tests are behaviour checks, not coverage.
"""

from __future__ import annotations

import json
import random
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import mock

import _acceptload
from _acceptload import ACCEPT_DIR, FIXTURES
from test_accept_stage import make_stage

stage = _acceptload.load("stage.py")
refprobe = _acceptload.load("refprobe.py")
synth = _acceptload.load("synth_shift_check.py")

POST_STAGE = "post-stage (recommend-only, to 11-24)"


# ---- stats.py: per-phase percentiles on a tiny synthetic extraction --------------------------------------------------------------------------------
def run_stats(tmp_path):
    """Lay the flat fixture files out as the unpacked acceptance tarball (a `var/` dir would be gitignored, so the tree is built here)."""
    root = tmp_path / "extracted"
    acc, jdir = root / "root" / "iogovmon-accept2", root / "var" / "lib" / "iogovmon" / "journal"
    (acc / "refprobe").mkdir(parents=True)
    jdir.mkdir(parents=True)
    shutil.copy(FIXTURES / "stats" / "stage-phases.json", acc)
    shutil.copy(FIXTURES / "stats" / "cow-20261118.csv", acc / "refprobe")
    shutil.copy(FIXTURES / "stats" / "journal-2026-11.jsonl", jdir)
    res = subprocess.run([sys.executable, "-I", str(ACCEPT_DIR / "stats.py"), str(root)], capture_output=True, text=True, timeout=120)
    assert res.returncode == 0, res.stderr
    return res.stdout


def tables(out):
    """{(block, name): {phase: [cells after the phase]}} from the markdown output; block = first word of the `## ` heading."""
    found, block, name = {}, None, None
    for line in out.splitlines():
        if line.startswith("## "):
            block, name = line[3:].split(" ")[0], None
        elif line.startswith("### "):
            name = line[4:]
            found[(block, name)] = {}
        elif line.startswith("| ") and name is not None:
            cells = [c.strip() for c in line.strip("|").split("|")]
            if cells[0] != "phase" and not cells[0].startswith("-"):
                found[(block, name)][cells[0]] = cells[1:]
    return found


def test_stats_percentiles_per_phase_and_phase_attribution(tmp_path):
    out = run_stats(tmp_path)
    t = tables(out)
    cold = t[("Latency", "r_cold")]
    assert cold["A0-baseline"] == ["5", "300", "480", "496"]  # 100..500 us: p50 300, interpolated p95 480, p99 496
    assert cold["B1-fill"] == ["2", "1500", "1950", "1990"]
    assert cold[POST_STAGE] == ["1", "7", "7", "7"]  # after the last phase end: the open-ended post-stage phase
    assert t[("Latency", "r_witness")]["A0-baseline"] == ["3", "50", "50", "50"]
    assert "999999" not in out  # a sample before the first phase belongs to no phase and is dropped


def test_stats_bench_lanes_count_valid_lanes_only(tmp_path):
    t = tables(run_stats(tmp_path))
    assert t[("Bench", "r_seq")]["A0-baseline"] == ["1", "100.0", "100.0", "100.0"]  # rate_bps / MiB
    assert t[("Bench", "w_fresh_qd4")]["A0-baseline"] == ["1", "200.0", "200.0", "200.0"]  # rate_write_bps / MiB
    assert t[("Latency", "w_over_4k")]["A0-baseline"] == ["3", "20", "29", "30"]  # the bench lane's own lat_us sample
    assert ("Bench", "r_bad") not in t and ("Latency", "r_bad") not in t  # valid=false is excluded


def test_stats_yield_and_refprobe_tables(tmp_path):
    out = run_stats(tmp_path)
    assert "| A0-baseline | 5 | 10 | 50.0% |" in out  # r_cold non-zero yield: 5 of 10 attempts (the yield table has no `###` sub-heading)
    assert "| B1-fill | 2 | 10 | 20.0% |" in out
    t = tables(out)
    assert t[("Reference", "cow/read-hot")]["A0-baseline"] == ["2", "20", "29", "30"]  # the errno != 0 row (latency 999) is dropped
    assert t[("Reference", "cow/wr_sync")]["B1-fill"] == ["1", "5000", "5000", "5000"]


# ---- refprobe.py: host-free parts (CSV writer, deletion allowlist, fill-file scan) -----------------------------------------------------------------
def test_refprobe_csv_one_file_per_fs_and_day_with_header_and_cap(tmp_path, monkeypatch):
    csv = refprobe.Csv(tmp_path / "data", flush_s=3600)
    ts = 1_790_000_000 * 10**9
    csv.add([(ts, "cow", "read-hot", 0, 12, 0), (ts, "root", "wr_sync", 4096, 900, 0)])
    csv.flush(force=True)
    files = sorted(p.name for p in (tmp_path / "data").glob("*.csv"))
    assert [re.fullmatch(r"(cow|root)-\d{8}\.csv", n) is not None for n in files] == [True, True]
    cow = next(p for p in (tmp_path / "data").glob("cow-*.csv")).read_text().splitlines()
    assert cow == ["ts_utc_ns,fs,op,offset,latency_us,errno", f"{ts},cow,read-hot,0,12,0"]
    size = sum(p.stat().st_size for p in (tmp_path / "data").glob("*.csv"))
    monkeypatch.setattr(refprobe, "CAP_BYTES", 1)  # the data dir is over the cap now: further rows are dropped, never written
    csv.add([(ts, "cow", "read-hot", 8192, 13, 0)])
    csv.flush(force=True)
    assert csv.capped and csv.rows == []
    assert sum(p.stat().st_size for p in (tmp_path / "data").glob("*.csv")) == size


def test_refprobe_removes_only_allowed_probe_dirs(tmp_path, monkeypatch):
    monkeypatch.setattr(refprobe, "ALLOWED_PARENTS", {str(tmp_path)})
    ok_dir, wrong_name, outside = tmp_path / "iogov-refprobe-x", tmp_path / "precious", tmp_path / "sub" / "iogov-refprobe"
    for d in (ok_dir, wrong_name, outside):
        d.mkdir(parents=True)
        (d / "probe.bin").write_bytes(b"x")
    link = tmp_path / "iogov-refprobe-link"
    link.symlink_to(wrong_name, target_is_directory=True)
    assert refprobe.remove_probe_dir(str(ok_dir)) is True and not ok_dir.exists()
    for refused in (wrong_name, outside, link):
        assert refprobe.remove_probe_dir(str(refused)) is False
    assert (wrong_name / "probe.bin").exists() and (outside / "probe.bin").exists() and link.is_symlink()


def test_refprobe_fill_scan_takes_regular_files_of_a_block_or_more(tmp_path):
    fill = tmp_path / "fill"
    (fill / "churn").mkdir(parents=True)
    (fill / "big-000.bin").write_bytes(b"x" * 8192)
    (fill / "churn" / "f.dat").write_bytes(b"x" * 4096)
    (fill / "tiny").write_bytes(b"x" * 100)  # below one block: no read offset exists
    (fill / "link").symlink_to(fill / "big-000.bin")  # not a regular file: never read through a link
    probe = refprobe.FillProbe({"root": str(fill)}, rescan_s=60.0)
    probe.rescan()
    assert sorted((name, Path(p).name, size) for name, p, size, _cum in probe.files) == [("root", "big-000.bin", 8192), ("root", "f.dat", 4096)]
    assert probe.files[-1][3] == 8192 + 4096  # cumulative weight, used to pick files in proportion to their size
    (tmp_path / "linked").symlink_to(fill, target_is_directory=True)
    linked = refprobe.FillProbe({"root": str(tmp_path / "linked")}, rescan_s=60.0)
    linked.rescan()
    assert linked.files == []  # a fill dir that is a symlink is not followed


# ---- synth_shift_check.py: the synthetic journal it feeds the recognizer -----------------------------------------------------------------------------
def template(pool, nonzero=1.0):
    def rec(lane):
        return {"v": 1, "type": "probe", "lane": lane, "device": "vda", "quiet": True, "direct": True, "hist": [], "t": 0.0}
    return {lane: {"rec": rec(lane), "pool": list(pool), "nonzero": nonzero, "last_t": 0.0} for lane in synth.LANES}


def hour_aligned(t):
    return int(t // 3600) * 3600


def test_synth_percentile_and_transforms():
    assert synth.pct([1, 2, 3, 4, 5], 50) == 3 and synth.pct([10, 20, 30, 40], 0) == 10 and synth.pct([10, 20, 30, 40], 100) == 40
    assert synth.transform("median", 3.0, 500)(100) == 300
    assert synth.transform("none", 1, 500)(100) == 100
    tail = synth.transform("tail", 8.0, 500)
    assert tail(499) == 499 and tail(500) == 4000  # only the top decile of the baseline distribution is stretched


def test_synth_median_shift_moves_only_the_changed_windows_of_r_cold():
    tmpl = template([1000] * 50)
    t_first = 1_790_000_000 // 3600 * 3600
    recs, rows = synth.build_journal({"kind": "median", "factor": 3.0}, tmpl, 128, random.Random(1), t_first)
    assert [r["phase"] for r in rows] == ["baseline"] * synth.N_BASE + ["changed"] * synth.N_BAD + ["recovery"] * synth.N_REC
    assert [r["p50"] for r in rows] == [1000] * synth.N_BASE + [3000] * synth.N_BAD + [1000] * synth.N_REC
    n_windows = synth.N_BASE + synth.N_BAD + synth.N_REC
    assert len(recs) == n_windows * synth.TICKS * len(synth.LANES) + len(synth.LANES)  # + the trailing tick that closes the last window
    for r in recs[:-len(synth.LANES)]:  # every tick sits inside its own UTC-hour window (a tick must never straddle the boundary)
        k = (hour_aligned(r["t"]) - t_first) // 3600
        assert 0 <= k < n_windows
        if r["lane"] == "r_witness":
            assert r["lat_us"] == [1000, 1000]  # the witness lane is the unchanged control inside the same run


def test_synth_tail_only_shift_leaves_the_median_flat_and_moves_p99():
    tmpl = template([100] * 80 + [1000] * 20)
    t_first = 1_790_000_000 // 3600 * 3600
    _recs, rows = synth.build_journal({"kind": "tail", "factor": 8.0}, tmpl, 128, random.Random(3), t_first)
    changed, base = rows[synth.N_BASE], rows[0]
    assert (base["p50"], base["p99"]) == (100, 1000)
    assert (changed["p50"], changed["p99"]) == (100, 8000)  # the tail moved 8x, the median did not (what the recognizer cannot see, IGM-20)


def test_synth_scenario_config_points_state_into_the_scenario_dir(tmp_path, monkeypatch):
    base = tmp_path / "cond.toml"
    base.write_text('window_hours = 1.0\nstate_dir = "/live/state"\nscratch_dir = "/live/scratch"\nread_max_attempts = 128\n')
    monkeypatch.setattr(synth, "CONFIG_V2", base)
    cfg = synth.write_scenario(tmp_path / "root", "scn", [{"a": 1}, {"b": 2}])
    d = tmp_path / "root" / "scn"
    assert cfg.read_text().splitlines() == ['window_hours = 1.0', f'state_dir = "{d}"', f'scratch_dir = "{d}/scratch"', 'read_max_attempts = 128']
    assert [json.loads(x) for x in (d / "journal" / "journal-synth.jsonl").read_text().splitlines()] == [{"a": 1}, {"b": 2}]
    assert "/live/" not in cfg.read_text()  # the live state dir is never named in a scenario config
    assert synth.evaluate({}, "r_cold@vda", 0)["valid_windows"] == 0  # a lane that never produced a window is reported, not a crash


# ---- stage.py IGM-22 minors: (a) timeouts, (b) orphan match, (c) file allowlist, (d) churn mount check -------------------------------------------------
def new_stage():
    tmp = tempfile.TemporaryDirectory()
    return tmp, make_stage(tmp.name), Path(tmp.name)


def test_igm22a_every_systemctl_style_helper_survives_a_hung_command():
    tmp, S, work = new_stage()
    seen = []

    def hang(argv, **kw):
        seen.append(kw.get("timeout"))
        raise subprocess.TimeoutExpired(argv, kw.get("timeout"))

    with tmp, mock.patch.object(stage.subprocess, "run", hang):
        marker = work / "fstrim-timer-was-active"
        S.fstrim_stop()  # `systemctl is-active` hangs: not proven active, so nothing is stopped and no marker is left
        assert not marker.exists()
        marker.write_text("x\n")
        assert S.fstrim_restore() is False and marker.exists()  # `systemctl start` hangs: reported failed, the marker stays so cleanup retries
        assert S.throttle_reset() is False  # a hung reset is a failed reset, never a silent success
        S.nodiscard_on()  # findmnt hangs: no discard option is seen, nothing is remounted, nothing raises
    assert seen and all(t is not None and t > 0 for t in seen)  # no helper command can wait forever


def fake_proc(root, procs):
    for pid, argv in procs.items():
        d = root / str(pid)
        d.mkdir(parents=True)
        (d / "cmdline").write_bytes(b"\0".join(a.encode() for a in argv) + b"\0")
    (root / "self").mkdir()  # a non-numeric entry is ignored


def test_igm22b_orphan_match_is_by_path_component_not_substring():
    assert stage.names_path("--filename=/srv/cow/iogov-fill/big-000.bin", "/srv/cow/iogov-fill")
    assert stage.names_path("--filename=/other/a.bin:/srv/cow/iogov-fill/b.bin", "/srv/cow/iogov-fill")  # fio file list
    assert stage.names_path("/srv/cow/iogov-fill", "/srv/cow/iogov-fill")
    for other in ("--filename=/srv/cow/iogov-fill2/x", "--filename=/data/srv/cow/iogov-fill/x", "/srv/cow/iogov-fill-keep/x", "--size=1G", ""):
        assert not stage.names_path(other, "/srv/cow/iogov-fill")
    needles = ["/srv/cow/iogov-fill", "/var/tmp/iogov-fill"]
    assert stage.orphan_kind(["fio", "--filename=/srv/cow/iogov-fill/big-000.bin"], needles) == "fio"
    assert stage.orphan_kind(["python3", "stage.py", "--churn-worker", "--dir", "/var/tmp/iogov-fill/churn"], needles) == "worker"
    assert stage.orphan_kind(["sh", "-c", "fio --filename=/srv/cow/iogov-fill/a"], needles) is None  # a shell that merely mentions a path
    assert stage.orphan_kind(["python3", "other.py", "--dir", "/var/tmp/iogov-fill/churn"], needles) is None  # not our churn worker
    assert stage.orphan_kind([], needles) is None


def test_igm22b_kill_orphans_kills_only_real_writers_of_our_paths(tmp_path):
    tmp, S, work = new_stage()
    procs = {
        100001: ["fio", "--name=fill", "--filename=/srv/cow/iogov-fill/big-000.bin", "--size=1G"],
        100002: ["fio", "--filename=/srv/cow/iogov-fill2/big.bin"],          # a sibling dir whose name merely starts the same
        100003: ["fio", "--filename=/data/srv/cow/iogov-fill/x"],            # our path as the tail of another path
        100004: ["python3", "stage.py", "--churn-worker", "--dir", "/var/tmp/iogov-fill/churn", "--deadline", "5"],
        100005: ["python3", "stage.py", "--churn-worker", "--dir", "/var/tmp/iogov-fill-keep/churn"],
        100006: ["sh", "-c", "fio --filename=/srv/cow/iogov-fill/a"],
        100007: ["fio", str(work / "fio-churn.ini")],
        100008: ["fio", "--filename=/var/tmp/iogov-load.bin"],
        100009: ["fio", "--filename=/var/tmp/iogov-load.bin.bak"],
        100010: ["python3", "other.py", "/var/tmp/iogov-fill/churn"],
    }
    fake_proc(tmp_path, procs)
    killed = []
    with tmp, mock.patch.object(stage.os, "kill", lambda pid, sig: killed.append(pid)):
        S.kill_orphans(proc=str(tmp_path))
    assert sorted(killed) == [100001, 100004, 100007, 100008]


def test_igm22c_files_outside_the_allowlist_are_never_deleted_and_the_run_refuses_to_start(tmp_path, monkeypatch, capsys):
    for ok in ("/var/tmp/iogov-load.bin", "/srv/cow/iogov-probe.bin"):
        assert stage.allowed_file(ok)
    for bad in ("/var/tmp/other.bin", "/var/tmp/sub/iogov-x.bin", "/etc/iogov-x", "/var/tmp/../etc/iogov-x", "/", "/var/tmp"):
        assert not stage.allowed_file(bad), bad
    assert stage.allowed_fill_dir("/srv/cow/iogov-fill") and stage.allowed_fill_dir("/var/tmp/iogov-fill/")
    assert not stage.allowed_fill_dir("/srv/cow") and not stage.allowed_fill_dir("/home/iogov-fill") and not stage.allowed_fill_dir("/srv/cow/x/iogov-fill")

    tmp, S, work = new_stage()
    victim = work / "precious.db"
    victim.write_text("keep me")
    with tmp:
        assert S.bad_paths() == []  # the shipped example config passes its own allowlists
        S.files_allowed, S.allowed_dirs = {str(victim)}, set()
        assert S.bad_paths() == [str(victim)]
        S.rm_created("test")
        assert victim.read_text() == "keep me" and str(victim) in S.rm_failed
        assert S.cleanup("normal") is False and victim.exists()  # cleanup reports the skipped path as a failure, still deletes nothing

    victim2 = tmp_path / "precious2.db"
    victim2.write_text("keep me too")
    cfg_text = (ACCEPT_DIR / "stage.example.toml").read_text().replace("/root/iogovmon-accept2", str(tmp_path))
    cfg_file = tmp_path / "bad.toml"
    cfg_file.write_text(cfg_text.replace('"/var/tmp/iogov-load.bin"', f'"{victim2}"'))
    monkeypatch.setattr(sys, "argv", ["stage.py", "--config", str(cfg_file), "--run", "--t0", "2026-11-18T10:00:00Z"])
    assert stage.main() == 2  # refused before anything is written, spawned or deleted
    assert "outside the deletion allowlists" in capsys.readouterr().err and victim2.read_text() == "keep me too"


def test_igm22d_churn_burst_refuses_without_the_mount_check():
    tmp, S, work = new_stage()
    spawned = []

    class Done:
        returncode = 0

        def poll(self):
            return 0

    S.spawn = lambda argv, tag: spawned.append(tag) or Done()
    S.kill_children = lambda: None
    S.root_dir, S.cow_dir = str(work / "root" / "iogov-fill"), str(work / "cow" / "iogov-fill")
    with tmp:
        S.mounts_ok = lambda: False  # /srv/cow is not a separate mount: the 4k overwrite file would load the root filesystem
        S.churn_burst("B3-c1-burst", 1_790_000_000.0, 5)
        assert spawned == [] and not (work / "root").exists() and not (work / "cow").exists()  # no writer, no directory
        assert S.limit_hit == {"B3-c1-burst": ["mount check failed"]}  # recorded in the phase table like a refused fill
        S.mounts_ok = lambda: True
        S.churn_burst("B3-c2-burst", 1_790_000_000.0, 5)
        assert spawned == ["churn-worker", "churn-fio-overwrite"] and (work / "cow" / "iogov-fill").is_dir()
