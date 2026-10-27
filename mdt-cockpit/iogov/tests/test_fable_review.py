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
from iogov.config import MiB, from_mapping, load, ConfigError
from iogov.episodes import EpisodeDetector, watch
from iogov.gating import RunGuard
from iogov.journal import ReadStats, read_records
from iogov.rawread import AlignedBuffer, RawReader, RawReadError
from iogov.report import analyze, status
from iogov.runner import build, cleanup, plan_text, run_bench_cmd, run_tick
from iogov.scratch import PREFIX, Scratch
import iogov.journal as journal_mod
import iogov.runner as runner_mod
import iogov.scratch as scratch_mod

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
    cfg = world.config(backoff_base_s=3600.0)  # one gate failure backs the probes off for an hour: longer than skip_passive_interval_s
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
    slept = len(clock.slept)
    run_tick(build(cfg, env))  # calm again but inside the backoff window AND inside skip_passive_interval_s: nothing at all
    assert len(clock.slept) == slept  # no gate sleep: the gate was not even read
    clock.t += cfg.skip_passive_interval_s
    run_tick(build(cfg, env))  # still backing off (the backoff grew), passive snapshot due again
    recs = records(world)
    passive = [r for r in recs if r["type"] == "passive"]
    assert len(passive) == 2 and all(r["gated"] for r in passive)
    assert passive[0]["gate_reasons"][0].startswith("io_pressure_high")
    assert passive[1]["gate_reasons"] == [f"backoff:until={int(state(world)['backoff']['tick']['until'])}"]
    assert passive[0]["pressure_memory"]["some"]["avg10"] == 37.1 and passive[0]["pressure_memory"]["full"]["avg10"] == 17.6
    rates = passive[0]["vmstat_per_s"]
    assert set(rates) == set(counters) and rates["pswpin"] > 0
    assert not reads and not [r for r in recs if r["type"] in ("probe", "run_start", "run_end")]
    skips = [r for r in recs if r["type"] == "skip"]
    assert len(skips) == 1 and skips[0]["ctx"]["memory_some_avg10"] == 37.1


def state(world) -> dict:
    return json.load(open(world.state / "state.json"))


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


def test_r7_dead_config_keys_are_deprecation_warnings_and_aliases_are_gone(world):
    cfg = from_mapping({"lookback_days": 120, "recognize_every_s": 3600.0, "read_iops_max": 5.0})  # an upgraded host's old config file
    assert cfg.read_iops_max == 5.0 and len(cfg.deprecated) == 2 and all("ignored" in d for d in cfg.deprecated)
    assert not hasattr(cfg, "lookback_days")
    with pytest.raises(ConfigError, match="unknown"):
        from_mapping({"brand_new_typo": 1})  # an unknown NEW key is still an error
    env, _ = make_env()
    text = plan_text(build(world.config(lookback_days=1), env))
    assert "DEPRECATED key lookback_days" in text
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


# ===== S1-S7: the Sonnet review of 9708c6f8a =========================================================================
# S1 FIFO/device nodes hang readers; S2 unbounded/untyped state and journal reads; S3 kill switch between the runner's check and the
# gate's; S4 backed-off ticks: reason and cost; S5 read contract; S6 hung-flush backstop; S7 removed keys as deprecation warnings.


def _finishes(fn, timeout: float = 5.0):
    """Run fn in a daemon thread; True if it returned within the timeout (a blocking open never returns)."""

    import threading
    box: dict = {}

    def run():
        try:
            box["r"] = fn()
        except BaseException as exc:  # noqa: BLE001 - the test inspects it
            box["exc"] = exc

    t = threading.Thread(target=run, daemon=True)
    t.start()
    t.join(timeout)
    return (not t.is_alive()), box


def test_s1_a_fifo_as_journal_file_cannot_hang_analyze_and_is_named(world):
    env, _ = make_env()
    cfg = world.config()
    run_tick(build(cfg, env))
    os.mkfifo(world.state / "journal" / "journal-2021-01.jsonl")
    stats = ReadStats()
    done, box = _finishes(lambda: len(list(read_records(cfg.journal_dir(), stats))))
    assert done and box["r"] > 0 and len(stats.unreadable) == 1 and "fifo" in stats.unreadable[0]
    done, box = _finishes(lambda: analyze(cfg, cfg.journal_dir()))
    assert done and "UNREADABLE journal-2021-01.jsonl" in box["r"]
    done, box = _finishes(lambda: status(cfg, env.wall))
    assert done and "UNREADABLE" in box["r"]


def test_s1_a_fifo_or_device_node_as_state_file_is_refused_before_open(world, monkeypatch):
    env, _ = make_env()
    cfg = world.config()
    run_tick(build(cfg, env))
    os.unlink(world.state / "state.json")
    os.mkfifo(world.state / "state.json")
    done, box = _finishes(lambda: status(cfg, env.wall))
    assert done and "corrupt state" in box["r"] and "fifo" in box["r"]
    # a device node (cannot be created without root: lstat is made to report one)
    real_lstat = os.lstat
    agg = str(world.state / "agg.json")
    monkeypatch.setattr(os, "lstat", lambda p, *a, **k: os.stat_result((stat.S_IFCHR | 0o600,) + tuple(real_lstat(p)[1:])) if str(p) == agg else real_lstat(p, *a, **k))
    opened = []
    real_open = os.open
    monkeypatch.setattr(os, "open", lambda p, *a, **k: (opened.append(str(p)), real_open(p, *a, **k))[1])
    with pytest.raises(safeio.UnsafePath, match="character device"):
        safeio.load_json(agg, any_owner=True, strict=True)
    assert agg not in opened  # refused BEFORE any open


def test_s1_collector_and_config_reads_never_block_on_a_fifo(world):
    env, _ = make_env()
    cfg = world.config()
    pio = world.proc / "pressure/io"
    os.unlink(pio)
    os.mkfifo(pio)
    done, box = _finishes(lambda: build(cfg, env).collector.system_pressure())
    assert done and box["r"] is None
    fifo_cfg = world.root / "cfg.toml"
    os.mkfifo(fifo_cfg)
    done, box = _finishes(lambda: load(str(fifo_cfg)))
    assert done and isinstance(box.get("exc"), ConfigError) and "fifo" in str(box["exc"])
    # the runner's own open paths too: resets.log as a FIFO is refused, not opened
    os.unlink(pio)  # (a write-open of the FIFO by the fixture would block: put the regular file back first)
    world.set_pressure(0.0, 0.0)
    run_tick(build(cfg, env))
    os.mkfifo(world.state / "resets.log")
    done, box = _finishes(lambda: run_tick(build(cfg, env)))
    assert done and box["r"] == 2  # refused cleanly (UnsafePath -> exit 2), no hang, no traceback


def test_s2_deeply_nested_or_oversized_state_files_are_reported_not_crashes(world, monkeypatch):
    env, _ = make_env()
    cfg = world.config()
    run_tick(build(cfg, env))
    (world.state / "state.json").write_text('{"a":' * 100000)
    out = status(cfg, env.wall)
    assert "corrupt state" in out and "RecursionError" in out
    (world.state / "state.json").write_text('{"next_bench": "soon", "budget": {"day": 1}, "last_tick": 5.0, "backoff": []}')
    out = status(cfg, env.wall)
    assert "fields with the wrong type ignored: next_bench, budget, backoff" in out and "last tick:" in out
    (world.state / "agg.json").write_text("null")
    assert "corrupt state" in status(cfg, env.wall)
    (world.state / "agg.json").write_text('{"v": 1, "horizon": "x", "records": 0, "resets": [], "passive_peak": {}, "lanes": 5, "open": {}}')
    out = status(cfg, env.wall)
    assert "corrupt state" in out and "agg.json" in out and "replayed from the journal" in out
    monkeypatch.setattr(safeio, "MAX_JSON_BYTES", 64)
    (world.state / "agg.json").write_text('{"v": 1, "padding": "' + "x" * 200 + '"}')
    assert "exceeds the 64 byte limit" in status(cfg, env.wall)
    assert safeio.load_json(str(world.state / "agg.json")) == {}  # the runner's non-strict view: start fresh, never crash


def test_s2_journal_reads_are_bounded_per_line_per_file_and_per_run(world, monkeypatch):
    env, _ = make_env()
    cfg = world.config()
    run_tick(build(cfg, env))
    f = next((world.state / "journal").glob("journal-*.jsonl"))
    n = len(records(world))
    monkeypatch.setattr(journal_mod, "MAX_LINE_READ", 8192)
    with open(f, "a") as fh:
        fh.write('{"v": 1, "ts": "x", "t": 1.0, "type": "note", "text": "' + "x" * 20000 + '"}\n')  # one oversized line
        fh.write('{"v": 1, "ts": "x", "t": 2.0, "type": "note", "text": "after"}\n')
        fh.write('{"a":' * 500 + "\n")  # unterminated: a plain JSONDecodeError, torn (the RecursionError case is test_t6, real limits)
    stats = ReadStats()
    got = list(read_records(cfg.journal_dir(), stats))
    assert len(got) == n + 1 and got[-1]["text"] == "after" and stats.oversized == 1 and stats.torn == 1
    monkeypatch.setattr(journal_mod, "MAX_RECORDS", 2)
    stats = ReadStats()
    assert len(list(read_records(cfg.journal_dir(), stats))) == 2 and stats.limit_hit
    assert "LIMIT: 2 records read" in analyze(cfg, cfg.journal_dir())
    monkeypatch.setattr(journal_mod, "MAX_RECORDS", 2_000_000)
    monkeypatch.setattr(journal_mod, "MAX_FILE_BYTES", 300)
    stats = ReadStats()
    assert len(list(read_records(cfg.journal_dir(), stats))) < n and stats.truncated == [f.name]
    assert f"TRUNCATED {f.name}" in analyze(cfg, cfg.journal_dir())


def test_s2_records_with_wrong_field_types_are_invalid_not_tracebacks(world):
    env, _ = make_env()
    cfg = world.config()
    run_tick(build(cfg, env))
    f = next((world.state / "journal").glob("journal-*.jsonl"))
    bad = [
        '{"v": 1, "ts": "x", "t": 1.0, "type": "skip", "kind": "tick", "reasons": 5}',
        '{"v": 1, "ts": "x", "t": "1", "type": "note", "text": "t is a string"}',
        '{"v": 1, "ts": "x", "t": 1.0, "type": "bench", "status": "ok", "region": [], "lanes": {}, "settings_epoch": "e"}',
        '{"v": 1, "ts": "x", "t": 1.0, "type": "probe", "lane": "r_cold", "device": "vda", "quiet": 1, "n": 1, "zero": 0, "lat_us": [1], "hist": [], "direct": true}',
        '{"v": true, "ts": "x", "t": 1.0, "type": "note", "text": "v is a bool"}',
    ]
    nested = [  # top level valid, nested garbage: read, but it must not crash the recognizer or the harm summary
        '{"v": 1, "ts": "x", "t": 1.0, "type": "harm", "kind": "episode", "evidence": {"duration_s": "long", "driver": 7}}',
        '{"v": 1, "ts": "x", "t": 1.0, "type": "bench", "status": "ok", "region": {}, "lanes": {"w_fresh_qd4": {"valid": true}}, "settings_epoch": "e"}',
        '{"v": 1, "ts": "x", "t": 1.0, "type": "passive", "devices": {"vda": 3}, "pressure": null}',
        '{"v": 1, "ts": "x", "t": 1.0, "type": "run_end", "kind": "tick", "status": "ok", "duration_s": 1, "io": {}, "abort": {"x": 1}}',
    ]
    with open(f, "a") as fh:
        fh.write("\n".join(bad + nested) + "\n")
    stats = ReadStats()
    got = list(read_records(cfg.journal_dir(), stats))
    assert stats.invalid == len(bad) and len([r for r in got if r["t"] == 1.0]) == len(nested)
    text = analyze(cfg, cfg.journal_dir())
    assert "1 distress episodes" in text and "driver 7" in text
    assert "last run" in status(cfg, env.wall)


def test_s3_kill_switch_between_the_runners_check_and_the_gates_is_a_clean_skip(world, monkeypatch):
    import iogov.gating as gating
    env, _ = make_env()
    cfg = world.config()
    monkeypatch.setattr(gating, "kill_switch_reason", lambda c, environ=None: "flag-file:appeared-late")  # the gate sees it, the runner did not
    assert run_tick(build(cfg, env)) == 0
    recs = records(world)
    assert [r["type"] for r in recs] == ["skip"] and recs[0]["reasons"] == ["kill_switch:flag-file:appeared-late"]
    assert state(world)["skip_logged"]["tick"]["sig"] == "kill_switch"


def test_s3_an_unexpected_exception_is_a_one_line_error_with_state_saved_and_the_lock_released(world, monkeypatch, capsys):
    env, _ = make_env()
    cfg = world.config()

    class Boom:
        def __init__(self, *a):
            pass

        def check(self, kind):
            raise RuntimeError("boom")

    monkeypatch.setattr(runner_mod, "Gate", Boom)
    assert run_tick(build(cfg, env)) == 3
    err = capsys.readouterr().err
    assert "internal error" in err and "RuntimeError: boom" in err and "Traceback" not in err
    assert (world.state / "state.json").exists()
    notes = [r for r in records(world) if r["type"] == "note" and r.get("kind") == "error"]
    assert len(notes) == 1 and "RuntimeError: boom" in notes[0]["text"]  # the gap is visible in the journal, not only on stderr
    monkeypatch.undo()
    assert run_tick(build(cfg, env)) == 0 and [r for r in records(world) if r["type"] == "probe"]  # the lock was released


def test_s4_a_backed_off_tick_is_rate_limited_and_says_backoff(world, monkeypatch):
    from iogov.collectors import Collector
    env, clock = make_env()
    cfg = world.config(backoff_base_s=3600.0)
    world.set_pressure(40.0, 0.0)
    run_tick(build(cfg, env))  # gated -> backoff (one hour)
    world.set_pressure(0.0, 0.0)
    calls = {"n": 0}
    real = Collector.snapshot
    monkeypatch.setattr(Collector, "snapshot", lambda self: (calls.__setitem__("n", calls["n"] + 1), real(self))[1])
    for _ in range(3):
        clock.t += 60
        run_tick(build(cfg, env))
    assert calls["n"] == 0 and len(clock.slept) == 1  # no gate read, no 1 s sleep while backed off and not due
    clock.t += cfg.skip_passive_interval_s
    run_tick(build(cfg, env))
    passive = [r for r in records(world) if r["type"] == "passive"]
    assert len(passive) == 2 and passive[1]["gate_reasons"] == [f"backoff:until={int(state(world)['backoff']['tick']['until'])}"]
    assert calls["n"] == 2
    assert from_mapping({"skip_passive_interval_s": 1.0}).skip_passive_interval_s == 60.0  # clamped to the hard minimum


def test_s6_units_have_a_hard_backstop_and_a_killed_bench_is_cleaned_up_by_the_next_run(world, monkeypatch):
    units = PKG / "units"
    for name, limit in (("iogov-tick.service.in", "10min"), ("iogov-bench.service.in", "30min")):
        text = (units / name).read_text()
        assert f"TimeoutStartSec={limit}" in text and re.search(r"^TimeoutStopSec=\d+$", text, re.M) and "ExecStopPost=" in text
    env, clock = make_env()
    cfg = world.config()
    # SIGKILL runs no `finally`: neither the scratch cleanup nor the budget refund happen; only what was persisted BEFORE the IO survives.
    # (The kernel drops a dead process's flock and descriptors: the stub releases exactly those and leaves the file behind.)
    monkeypatch.setattr(scratch_mod.Scratch, "_cleanup", lambda self: (self.lock.release(), os.close(self.fd)) if self.lock else None)
    monkeypatch.setattr(DailyBudget, "finish", lambda self: None)

    def killed(fd):
        raise SystemExit(137)

    monkeypatch.setattr(os, "fdatasync", killed)
    with pytest.raises(SystemExit):
        run_bench_cmd(build(cfg, env), force=True)
    st = state(world)
    leftover = [n for n in os.listdir(world.scratch) if n.startswith(PREFIX)]
    reserved = scratch_mod.plan_region(cfg, 16 * MiB).probe_bytes + cfg.sync4k_samples * 4096  # the worst case, persisted before any IO
    assert leftover and st["budget"]["write_bytes"] == reserved and st["next_bench"] > env.wall()  # the whole reservation stays charged
    assert "scratch leftovers: ['" in status(cfg, env.wall)
    monkeypatch.undo()
    assert cleanup(cfg) == leftover  # ExecStopPost path
    assert not [n for n in os.listdir(world.scratch) if n.startswith(PREFIX)]
    clock.t += 8 * 86400
    run_bench_cmd(build(cfg, env), force=True)  # the next run also starts from a clean scratch dir and records what it removed
    bench = [r for r in records(world) if r["type"] == "bench"][-1]
    assert bench["status"] == "ok" and bench["region"]["stale_removed"] == [] and state(world)["bench_fail"]["n"] == 0


# ===== T1-T9: the Sonnet review of 4e6f87f37 =========================================================================


TOKENS = {
    "IOGOV_USER": "iogov", "IOGOV_PYTHON": "/usr/bin/python3", "IOGOV_PYTHONPATH": "/opt/iogov", "IOGOV_CONFIG": "/etc/iogov/iogov.toml",
    "IOGOV_STATE_DIR": "/var/lib/iogov", "IOGOV_SCRATCH_DIR": "/var/lib/iogov/scratch", "IOGOV_KILL_SWITCH_FILE": "/etc/iogov/disabled",
    "IOGOV_DISK_NODE": "/dev/null", "IOGOV_DISK_ID_PATH": "pci-0000:00:04.0", "IOGOV_IO_WEIGHT": "50", "IOGOV_TICK_INTERVAL": "5min",
}


def render_units(into: Path) -> list[Path]:
    out = []
    for src in sorted((PKG / "units").glob("*.in")):
        text = src.read_text()
        for tok, val in TOKENS.items():
            text = text.replace(f"@{tok}@", val)
        assert not re.search(r"@IOGOV_[A-Z_]+@", text), src.name  # every real token rendered (the slice's comment mentions `@IOGOV_*@`)
        dst = into / src.name[: -len(".in")]
        dst.write_text(text)
        out.append(dst)
    return out


def test_t1_oneshot_units_bound_their_runtime_with_timeoutstartsec_not_runtimemaxsec(tmp_path):
    import subprocess
    from _testdeps import require_binary
    for name, limit in (("iogov-tick.service.in", "10min"), ("iogov-bench.service.in", "30min")):
        text = (PKG / "units" / name).read_text()
        assert "Type=oneshot" in text and f"TimeoutStartSec={limit}" in text
        assert not re.search(r"^RuntimeMaxSec=", text, re.M), f"{name}: RuntimeMaxSec= is ignored on Type=oneshot units"
    analyze_bin = require_binary("systemd-analyze", "verifies the rendered unit templates (a directive systemd ignores is a silent safety hole)")
    rendered = render_units(tmp_path)
    units = [p for p in rendered if p.suffix in (".service", ".timer", ".slice")]
    proc = subprocess.run([analyze_bin, "verify", "--man=no", *[str(p) for p in units]], cwd=tmp_path, capture_output=True, text=True, timeout=60)
    out = proc.stdout + proc.stderr
    assert proc.returncode == 0, out
    for bad in ("Ignoring", "has no effect", "Unknown key", "Unknown section", "Failed to parse"):
        assert bad not in out, out


def test_t2_a_refused_state_load_leaves_the_state_file_untouched(world, monkeypatch):
    env, _ = make_env()
    cfg = world.config()
    run_tick(build(cfg, env))
    sj = world.state / "state.json"
    good = sj.read_bytes()
    assert json.loads(good)["last_tick"]
    # (a) wrong mode
    os.chmod(sj, 0o644)
    assert run_tick(build(cfg, env)) == 2 and sj.read_bytes() == good and stat.S_IMODE(os.stat(sj).st_mode) == 0o644
    os.chmod(sj, 0o600)
    # (b) wrong owner (the running uid is made to look different)
    monkeypatch.setattr(safeio, "euid", lambda: os.geteuid() + 1)
    assert run_tick(build(cfg, env)) == 2 and sj.read_bytes() == good
    monkeypatch.undo()
    # (c) a symlink in place of the file
    target = world.root / "elsewhere.json"
    target.write_bytes(good)
    os.rename(sj, world.root / "keep.json")
    sj.symlink_to(target)
    assert run_tick(build(cfg, env)) == 2 and sj.is_symlink() and target.read_bytes() == good
    sj.unlink()
    # (d) a FIFO in place of the file
    os.mkfifo(sj)
    done, box = _finishes(lambda: run_tick(build(cfg, env)))
    assert done and box["r"] == 2 and stat.S_ISFIFO(os.lstat(sj).st_mode)
    sj.unlink()
    os.rename(world.root / "keep.json", sj)
    assert run_tick(build(cfg, env)) == 0 and json.loads(sj.read_bytes())["last_tick"] >= json.loads(good)["last_tick"]


def test_t3_a_non_utf8_resets_log_does_not_stop_the_service(world):
    env, _ = make_env()
    cfg = world.config()
    run_tick(build(cfg, env))
    p = world.state / "resets.log"
    p.write_bytes(b"\xff\xfe\n1.0\n")
    os.chmod(p, 0o600)
    rt = build(cfg, env)
    assert run_tick(rt) == 0 and rt.engine.resets == [1.0]


def test_t4_a_regular_file_swapped_for_a_fifo_between_lstat_and_open_cannot_block(world, monkeypatch):
    p = world.root / "swapped"
    p.write_text("{}")
    real_open = os.open

    def swap_then_open(path, flags, *a, **k):  # the race: the lstat has passed, now the name points at a FIFO
        if str(path) == str(p) and not stat.S_ISFIFO(os.lstat(p).st_mode):
            os.unlink(p)
            os.mkfifo(p)
        return real_open(path, flags, *a, **k)

    monkeypatch.setattr(os, "open", swap_then_open)
    done, box = _finishes(lambda: safeio.open_file(str(p), os.O_RDONLY, any_owner=True))
    assert done, "the open blocked on the FIFO: O_NONBLOCK is missing"
    assert isinstance(box.get("exc"), safeio.UnsafePath) and "not a regular file" in str(box["exc"])
    assert stat.S_ISFIFO(os.lstat(p).st_mode)  # the race really happened: the lstat saw a file, the open got a FIFO


def test_t5_read_text_caps_a_regular_file_larger_than_the_limit(tmp_path, monkeypatch):
    p = tmp_path / "big"
    p.write_bytes(b"a" * (70000) + b"\n")  # more than one 64 KiB read chunk and more than the limit
    got = []
    real_read = os.read
    monkeypatch.setattr(os, "read", lambda fd, n: (lambda b: (got.append(len(b)), b)[1])(real_read(fd, n)))
    assert safeio.read_text(str(p), limit=4096) == "a" * 4096
    assert sum(got) <= 4096 + 1  # the bytes actually pulled from the kernel are bounded too, not only the returned slice
    got.clear()
    assert safeio.read_text(str(p), limit=65536 + 100) == "a" * (65536 + 100) and sum(got) <= 65536 + 101
    got.clear()
    assert safeio.read_text(str(p)) == "a" * 70000 + "\n" and sum(got) == 70001
    assert safeio.read_text(str(tmp_path), limit=10) is None  # a directory: not a regular file, no read


def test_t6_journal_limits_at_their_real_defaults_and_a_parser_recursion_error(world):
    env, _ = make_env()
    cfg = world.config()
    run_tick(build(cfg, env))
    f = next((world.state / "journal").glob("journal-*.jsonl"))
    n = len(records(world))
    with open(f, "ab") as fh:
        fh.write(b'{"v": 1, "ts": "x", "t": 1.0, "type": "note", "text": "' + b"x" * (journal_mod.MAX_LINE_READ + 1000) + b'"}\n')
        fh.write(b"[" * 100000 + b"]" * 100000 + b"\n")  # closed, 200 KB, nested beyond the parser's recursion limit
        fh.write(b'{"v": 1, "ts": "x", "t": 2.0, "type": "note", "text": "after"}\n')
    with pytest.raises(RecursionError):
        json.loads("[" * 100000 + "]" * 100000)  # the premise of the case on this interpreter
    stats = ReadStats()
    got = list(read_records(cfg.journal_dir(), stats))
    assert len(got) == n + 1 and got[-1]["text"] == "after" and stats.oversized == 1 and stats.torn == 1


def test_t7_nan_in_persisted_numbers_never_freezes_a_schedule_or_the_rate_limit(world):
    env, clock = make_env()
    cfg = world.config(backoff_base_s=3600.0)
    world.set_pressure(40.0, 0.0)
    run_tick(build(cfg, env))  # gated: backoff + a passive record
    sj = world.state / "state.json"
    st = json.loads(sj.read_text())
    st["skip_passive_t"] = float("nan")
    st["next_bench"] = float("nan")
    sj.write_text(json.dumps(st))
    clock.t += 60
    run_tick(build(cfg, env))  # NaN last-passive time: treated as "never", so the snapshot is due again
    assert len([r for r in records(world) if r["type"] == "passive"]) == 2
    from iogov.runner import bench_due
    rt = build(cfg, env)
    rt.state = {"next_bench": float("nan")}
    assert bench_due(rt)
    from iogov.report import clean_state
    cleaned, problems = clean_state({"next_bench": float("nan"), "last_tick": float("inf"), "last_bench": 5.0})
    assert problems == ["last_tick", "next_bench"] and cleaned == {"last_bench": 5.0}
    assert "wrong type ignored: next_bench" in status(cfg, env.wall)  # the NaN next_bench written above; last_tick there is valid


def test_t8_a_symlinked_config_path_is_accepted_only_when_the_link_is_root_owned(world, monkeypatch):
    real = world.root / "real.toml"
    real.write_text('read_iops_max = 7.0\n')
    link = world.root / "link.toml"
    link.symlink_to(real)
    with pytest.raises(ConfigError, match="root-owned"):
        load(str(link))  # a symlink owned by the running user is not trusted
    real_lstat = os.lstat
    monkeypatch.setattr(os, "lstat", lambda p, *a, **k: os.stat_result((stat.S_IFLNK | 0o777, 0, 0, 1, 0, 0) + tuple(real_lstat(p)[6:])) if str(p) == str(link) else real_lstat(p, *a, **k))
    assert load(str(link)).read_iops_max == 7.0  # a root-owned link whose target passes every check
    os.chmod(real, 0o666)
    with pytest.raises(ConfigError, match="writable"):
        load(str(link))  # the target is checked like a plain file


def test_t9_journal_file_limit_counts_bytes_not_characters(world, monkeypatch):
    env, _ = make_env()
    cfg = world.config()
    (world.state / "journal").mkdir(parents=True)
    f = world.state / "journal" / "journal-2026-01.jsonl"
    line = ('{"v": 1, "ts": "x", "t": 1.0, "type": "note", "text": "' + "é" * 200 + '"}\n')  # 252 characters, 452 bytes
    f.write_text(line + line, encoding="utf-8")
    os.chmod(f, 0o600)
    monkeypatch.setattr(journal_mod, "MAX_FILE_BYTES", 700)
    stats = ReadStats()
    got = list(read_records(cfg.journal_dir(), stats))
    assert len(got) == 1 and stats.truncated == [f.name]  # 452 + 452 bytes > 700, although 504 characters < 700
