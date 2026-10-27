"""Regression tests for the second (Fable) review of 7f38a7743. Each one fails on that commit.

R1  `analyze`/`status` run by an operator or root (files owned by the service user) reported "0 records" / crashed; unreadable
    journal files were dropped silently.
R2  the daily budget under-counted bytes written after a midnight rollover.
R3  the 4 KiB durable-write lane's fdatasync was not under the flush watchdog (the design note said every flush is).
R4  the raw reader verified the block-device property by name (stat) only, not through the descriptor it opened.
R5  a gated or backed-off tick dropped the passive snapshot pair: no evidence from exactly the moments that matter.
R6  `watch` episodes ignored `memory.pressure`: a swap/reclaim-driven stall of the protected workload was invisible or mislabelled.
R7  dead config keys and aliases.
R8  docs disagreed with code (README install modes, "errors" vs clamping, hard-cap numbers).
"""

from __future__ import annotations

import json
import os
import re
import stat
import time
from pathlib import Path

import pytest
from conftest import make_env, write_file

import iogov.collectors as collectors
import iogov.config as config_mod
import iogov.recognizer as recognizer
from iogov import safeio
from iogov.bench import over_4k
from iogov.budget import DailyBudget
from iogov.config import MiB, from_mapping, ConfigError
from iogov.episodes import EpisodeDetector, watch
from iogov.gating import RunGuard
from iogov.journal import ReadStats, read_records
from iogov.rawread import AlignedBuffer, RawReader, RawReadError
from iogov.report import analyze, status
from iogov.runner import build, run_tick
from iogov.scratch import Scratch

PKG = Path(__file__).resolve().parents[1]


def records(world):
    return list(read_records(str(world.state / "journal")))


# ===== R1: read-only reports work for another uid, and name what they could not read ================================


def test_r1_analyze_and_status_work_for_an_operator_uid(world, monkeypatch):
    env, _ = make_env()
    cfg = world.config()
    run_tick(build(cfg, env))
    n = len(records(world))
    assert n > 0
    monkeypatch.setattr(safeio, "euid", lambda: os.geteuid() + 1)  # an operator (or root) reads the service user's files
    stats = ReadStats()
    assert len(list(read_records(cfg.journal_dir(), stats))) == n and not stats.unreadable
    text = analyze(cfg, cfg.journal_dir())
    assert text.startswith(f"iogov analyze: {n} records")
    out = status(cfg, env.wall)  # loads state.json and agg.json read-only
    assert "last tick:" in out and "never" not in out.splitlines()[2].split(";")[0]


def test_r1_unreadable_journal_files_are_named_not_silently_dropped(world, tmp_path):
    env, _ = make_env()
    cfg = world.config()
    run_tick(build(cfg, env))
    target = tmp_path / "elsewhere.jsonl"
    target.write_text("")
    (world.state / "journal" / "journal-2020-01.jsonl").symlink_to(target)
    stats = ReadStats()
    n = len(list(read_records(cfg.journal_dir(), stats)))
    assert n > 0 and len(stats.unreadable) == 1 and "journal-2020-01.jsonl" in stats.unreadable[0] and stats.skipped()
    text = analyze(cfg, cfg.journal_dir())
    assert "UNREADABLE journal-2020-01.jsonl" in text
    assert json.loads(analyze(cfg, cfg.journal_dir(), as_json=True))["read"]["unreadable"]
    assert "UNREADABLE journal file" in status(cfg, env.wall)


def test_r1_any_owner_is_only_for_read_only_opens(tmp_path):
    p = str(tmp_path / "f")
    with pytest.raises(ValueError):
        safeio.open_file(p, os.O_WRONLY | os.O_CREAT, any_owner=True)
    assert not os.path.exists(p)
    fd = safeio.open_file(p, os.O_WRONLY | os.O_CREAT, 0o600)
    os.close(fd)
    fd = safeio.open_file(p, os.O_RDONLY, any_owner=True)
    os.close(fd)


# ===== R2: budget rollover ==========================================================================================


def test_r2_bytes_written_after_midnight_count_against_the_new_day():
    cfg = from_mapping({})
    st: dict = {}
    t = [1_790_000_000.0 - 1_790_000_000.0 % 86400 + 86399.0]
    b = DailyBudget(st, cfg, lambda: t[0])
    b.reserve("write", 4 * MiB)
    t[0] += 2.0  # the UTC day changed while the run was in progress
    b.charge("write", 3 * MiB, ops=3)
    b.finish()
    assert st["budget"]["write_bytes"] == 3 * MiB and st["budget"]["write_ops"] == 3
    b2 = DailyBudget(st, cfg, lambda: t[0])
    assert b2.remaining("write") == cfg.max_write_bytes_per_day - 3 * MiB


# ===== R3: the 4 KiB lane's flush is under the watchdog ============================================================


def test_r3_the_4k_lane_flush_runs_under_the_watchdog(world, monkeypatch):
    env, clock = make_env()
    cfg = world.config(flush_limit_s=0.05, abort_latency_ms=1000.0, sync4k_samples=16)  # abort_latency alone would NOT stop this
    real = os.fdatasync

    def slow(fd):
        time.sleep(0.12)
        return real(fd)

    monkeypatch.setattr(os, "fdatasync", slow)
    guard = RunGuard(cfg, build(cfg, env).collector, env, strict_pressure=True)
    budget = DailyBudget({}, cfg, env.wall)
    with Scratch(cfg, env, 1 * MiB) as sc:
        buf = AlignedBuffer(65536)  # the scratch file is O_DIRECT: page-aligned buffer
        buf.mm[:] = b"\x01" * 65536
        os.pwrite(sc.fd, memoryview(buf.mm), 0)
        buf.close()
        lane = over_4k(sc, cfg, env, guard, budget, 65536)
    assert lane["aborted"] == "slow_flush" and env.stop.reason == "slow_flush" and lane["n"] <= 1


def test_r3_timed_flush_is_the_only_fdatasync_call_site():
    src = (PKG / "bench.py").read_text()
    assert src.count("os.fdatasync(") == 1 and "def timed_flush" in src


# ===== R4: the raw reader verifies the descriptor ================================================================


def test_r4_raw_reader_refuses_a_descriptor_that_is_not_a_block_device(world, monkeypatch):
    env, _ = make_env()
    real_stat = os.stat
    lying = os.stat_result((stat.S_IFBLK | 0o600,) + tuple(real_stat(world.disk))[1:])

    def stat_lies(path, *a, **k):  # the NAME claims a block device; what gets opened is a regular file
        return lying if str(path) == str(world.disk) else real_stat(path, *a, **k)

    monkeypatch.setattr(os, "stat", stat_lies)
    with pytest.raises(RawReadError, match="descriptor"):
        RawReader(str(world.disk), env, allow_regular=False).open()
    assert not [fd for fd in range(3, 64) if _is_open(fd) and _same(fd, world.disk)]  # nothing left open


def _is_open(fd: int) -> bool:
    try:
        os.fstat(fd)
        return True
    except OSError:
        return False


def _same(fd: int, path) -> bool:
    try:
        a, b = os.fstat(fd), os.stat(path)
        return (a.st_dev, a.st_ino) == (b.st_dev, b.st_ino)
    except OSError:
        return False


# ===== R5: a gated tick keeps the passive evidence, never probe IO ==================================================


def test_r5_gated_and_backed_off_ticks_keep_passive_evidence_without_probe_io(world, monkeypatch):
    env, clock = make_env()
    cfg = world.config()
    write_file(world.proc / "pressure/memory", "some avg10=37.10 avg60=21.00 avg300=24.00 total=10\nfull avg10=17.60 avg60=8.00 avg300=6.00 total=5\n")
    counters = {"pswpin": 1000, "pswpout": 2000, "pgmajfault": 50, "zswpin": 7, "zswpout": 9, "workingset_refault_anon": 3}
    n_snap = {"n": 0}

    def vmstat_text():
        n_snap["n"] += 1
        bump = 100 * n_snap["n"]
        return "".join(f"{k} {v + bump}\n" for k, v in counters.items()) + "nr_free_pages 1\n"

    write_file(world.proc / "vmstat", vmstat_text())
    world.set_pressure(40.0, 5.0)
    reads = []
    monkeypatch.setattr(os, "preadv", lambda *a, **k: reads.append(a) or 0)
    clock.on_sleep = lambda s: write_file(world.proc / "vmstat", vmstat_text())
    run_tick(build(cfg, env))  # gated
    clock.t += 10
    world.set_pressure(0.0, 0.0)
    run_tick(build(cfg, env))  # calm again but still inside the backoff window
    recs = records(world)
    passive = [r for r in recs if r["type"] == "passive"]
    assert len(passive) == 2 and all(r["gated"] for r in passive)
    assert passive[0]["gate_reasons"][0].startswith("io_pressure_high") and passive[1]["gate_reasons"] == []
    assert passive[0]["pressure_memory"]["some"]["avg10"] == 37.1 and passive[0]["pressure_memory"]["full"]["avg10"] == 17.6
    rates = passive[0]["vmstat_per_s"]
    assert set(rates) == set(counters) and rates["pswpin"] > 0
    assert not reads and not [r for r in recs if r["type"] in ("probe", "run_start", "run_end")]
    skips = [r for r in recs if r["type"] == "skip"]
    assert len(skips) == 1 and skips[0]["ctx"]["memory_some_avg10"] == 37.1


# ===== R6: memory pressure in episodes =============================================================================


def test_r6_episode_triggers_on_memory_stall_and_labels_the_driver(world):
    cfg = world.config(episode_pre_s=2, episode_quiet_s=3.0, watch_interval_s=1.0)
    det = EpisodeDetector(cfg)
    assert det.is_distress(0.0, 0.0, 0, mem_some=cfg.episode_some_pct) and not det.is_distress(0.0, 0.0, 0, mem_some=0.0)
    out = []
    for t in range(20):
        mem = 60.0 if 5 <= t < 9 else 0.0
        io = 10.0 if 5 <= t < 9 else 0.0
        ep = det.feed(float(t), io, 0.0, 0, 0.0, mem_some=mem, mem_full=mem / 2)
        if ep:
            out.append(ep)
    assert len(out) == 1
    e = out[0]
    assert e["driver"] == "memory" and e["peak_mem_some_pct"] == 60.0 and e["peak_mem_full_pct"] == 30.0 and e["peak_some_pct"] == 10.0
    assert e["series_columns"][-2:] == ["mem_some_pct", "mem_full_pct"] and all(len(row) == 7 for row in e["series"])
    # the old call signature (no memory samples) still works and labels an io-only episode as io
    det2 = EpisodeDetector(cfg)
    eps = [det2.feed(float(t), 30.0 if 3 <= t < 6 else 0.0, 0.0, 0, 0.0) for t in range(15)]
    assert [x["driver"] for x in eps if x] == ["io"]


def test_r6_watch_reads_memory_pressure_of_the_protected_cgroups(world):
    env, clock = make_env()
    cfg = world.config(episode_quiet_s=2.0, watch_interval_s=1.0, episode_pre_s=2)
    total = {"v": 0}

    def tick(_s):
        n = len(clock.slept)
        if 3 <= n <= 6:
            total["v"] += 700_000  # 70 % of each second stalled on memory reclaim; io.pressure stays flat
        write_file(world.cg / "game.slice/memory.pressure",
                   f"some avg10=0 avg60=0 avg300=0 total={total['v']}\nfull avg10=0 avg60=0 avg300=0 total={total['v'] // 2}\n")

    clock.on_sleep = tick
    clock.step = 1e-6
    assert watch(build(cfg, env), max_seconds=20) == 1
    ev = [r for r in records(world) if r["type"] == "harm"][0]["evidence"]
    assert ev["driver"] == "memory" and ev["peak_mem_some_pct"] > 60 and ev["peak_some_pct"] == 0
    text = analyze(cfg, cfg.journal_dir())
    assert "driver memory" in text and "'memory': 1" in text


# ===== R7: dead code ===============================================================================================


def test_r7_dead_config_keys_and_aliases_are_gone():
    for key in ("lookback_days", "recognize_every_s"):
        with pytest.raises(ConfigError, match="unknown"):
            from_mapping({key: 1})
    assert not hasattr(config_mod, "HARD") and not hasattr(recognizer, "LaneResult") and not hasattr(collectors, "resolve_containers")


# ===== R8: docs agree with code =====================================================================================


def test_r8_readme_and_design_note_agree_with_the_code():
    readme = (PKG / "README.md").read_text()
    design = (PKG.parent / "docs" / "IO-GOVERNOR-V1-DESIGN.md").read_text()
    assert "are errors" not in readme and "clamped" in readme  # over-limit values are clamped, not rejected
    assert re.search(r"install -d -o iogov -g iogov -m 700 /var/lib/iogov /var/lib/iogov/scratch", readme)  # the scratch dir MUST be 0700
    assert re.search(r"install -d -o root -g root -m 755 /etc/iogov", readme)  # never owned by the service user
    assert "iogov-tick.timer` first" in readme and "bench --force" in readme  # the first bench is a manual, idle-window step
    hard = config_mod.HARD_MAX
    assert f"{hard['region_hard_cap'] // (1024 * 1024 * 1024)} GiB" in design and "region_hard_cap" in design
    assert f"{hard['max_read_bytes_per_day'] // (1024 * 1024)} MiB" in design and f"{int(hard['sync4k_iops_max'])}/s" in design
    for needle in ("memory.pressure", "pressure_memory", "vmstat_per_s", "gated", "driver", "UNREADABLE", "any owner"):
        assert needle in design, needle
    example = (PKG / "iogov.toml.example").read_text()
    assert "memory.pressure" in example
