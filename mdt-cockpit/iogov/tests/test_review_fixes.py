"""Regression tests for the Opus review of 2db1712aa (H1-H5, M1-M6, L1-L8). Each one fails on the old code; see the commit message
for the per-fix evidence."""

from __future__ import annotations

import errno
import json
import os
import random
import stat
import threading
import time
import tracemalloc

import pytest
from conftest import make_env, write_file

import iogov.bench as bench_mod
import iogov.rawread as rawread
from iogov import covariates as cov
from iogov import safeio
from iogov.bench import over_4k, seq_read, seq_write_phase, tick_reads
from iogov.budget import DailyBudget
from iogov.cli import main
from iogov.collectors import Collector, ContainerResolver, passive_summary
from iogov.config import ConfigError, MiB, from_mapping, load
from iogov.gating import RunGuard
from iogov.journal import Journal, prune, read_records
from iogov.rawread import AlignedBuffer, RawReader, RawReadError
from iogov.recognizer import Engine, Window, analyze_lane, points_of, recognize
from iogov.report import analyze
from iogov.runner import annotate, build, run_bench_cmd, run_tick
from iogov.scratch import PREFIX, Scratch

D = 86400.0


def records(world):
    return list(read_records(str(world.state / "journal")))


def state(world):
    return json.load(open(world.state / "state.json"))


# ===== H1: crash-safe budget, next_bench persisted first, failure backoff and disable ================================


def test_h1_failing_bench_does_not_retry_every_15_minutes_and_budget_holds(world, monkeypatch):
    cfg = world.config(max_write_bytes_per_day=5 * MiB)
    real_sync, real_pwrite = os.fdatasync, os.pwrite
    wrote = {"b": 0}
    monkeypatch.setattr(os, "pwrite", lambda fd, buf, off: (wrote.__setitem__("b", wrote["b"] + len(buf)), real_pwrite(fd, buf, off))[1])

    def flaky(fd):
        if wrote["b"] >= 1 * MiB:
            raise OSError(errno.EIO, "simulated flush error")
        return real_sync(fd)

    monkeypatch.setattr(os, "fdatasync", flaky)
    per_attempt = []
    for attempt in range(6):
        env, clock = make_env(seed=attempt)
        clock.t += attempt * 900  # the 15-minute timer fires again, same UTC day
        before = wrote["b"]
        try:
            run_bench_cmd(build(cfg, env))
        except OSError:
            pass
        per_attempt.append(wrote["b"] - before)
    assert per_attempt[0] > 0 and per_attempt[1:] == [0] * 5  # one attempt, then backed off
    assert wrote["b"] <= cfg.max_write_bytes_per_day
    st = state(world)
    assert st["bench_fail"]["n"] == 1 and st["next_bench"] > make_env()[1].t + 900 * 6


def test_h1_next_bench_and_budget_are_persisted_before_any_io(world, monkeypatch):
    env, _ = make_env()
    cfg = world.config()
    seen = {}
    real = os.pwrite

    def spy(fd, buf, off):
        if "snap" not in seen:  # the first byte the bench writes: what is already on disk?
            seen["snap"] = state(world)
        return real(fd, buf, off)

    monkeypatch.setattr(os, "pwrite", spy)
    run_bench_cmd(build(cfg, env), force=True)
    assert seen["snap"]["next_bench"] > env.wall() and seen["snap"]["budget"]["write_bytes"] >= 1 * MiB  # reserved up front


def test_h1_a_hard_crash_keeps_the_reservation_charged(world, monkeypatch):
    env, _ = make_env()
    cfg = world.config()

    def die(*a, **k):
        raise SystemExit("killed")

    monkeypatch.setattr("iogov.runner.run_bench", die)
    with pytest.raises(SystemExit):
        run_bench_cmd(build(cfg, env), force=True)
    st = state(world)
    assert st["next_bench"] > env.wall() and st["bench_fail"]["n"] == 1


def test_h1_reservation_is_refunded_to_actual_use(world):
    env, _ = make_env()
    cfg = world.config()
    run_tick(build(cfg, env))
    st = state(world)
    reads = [r for r in records(world) if r["type"] == "run_end"][0]["io"]["read_bytes"]
    assert st["budget"]["read_bytes"] == reads  # not the worst-case reservation


def test_h1_consecutive_crashes_disable_the_bench_until_a_reset(world, monkeypatch):
    env, clock = make_env()
    cfg = world.config(bench_max_failures=2)
    boom = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
    real = bench_mod.over_4k
    monkeypatch.setattr(bench_mod, "over_4k", boom)
    for _ in range(2):
        run_bench_cmd(build(cfg, env), force=True)
        clock.t += 10
    assert state(world)["bench_fail"]["disabled_at"] is not None
    n = len(records(world))
    run_bench_cmd(build(cfg, env), force=True)
    new = records(world)[n:]
    assert [r["type"] for r in new] == ["skip"] and new[0]["reasons"][0].startswith("bench_disabled")
    monkeypatch.setattr(bench_mod, "over_4k", real)
    clock.t += 10
    assert annotate(build(cfg, env), "operator checked", "baseline_reset")
    clock.t += 10
    run_bench_cmd(build(cfg, env), force=True)
    assert [r for r in records(world) if r["type"] == "bench"][-1]["status"] == "ok"
    assert state(world)["bench_fail"]["n"] == 0


# ===== H2: bounded memory, retention, slim records ==================================================================


def synth_journal(world, days: int, ticks_per_day: int = 24):
    j = Journal(str(world.state / "journal"), lambda: 0.0)
    os.makedirs(j.directory, mode=0o700, exist_ok=True)
    t0 = 20000 * D
    handles = {}
    for day in range(days):
        for k in range(ticks_per_day):
            t = t0 + day * D + k * (D / ticks_per_day)
            lat = 500 if day < 30 else 2000
            probe = {"v": 1, "ts": "x", "t": t, "type": "probe", "lane": "r_cold", "device": "vda", "quiet": True, "n": 12, "zero": 0,
                     "lat_us": [lat] * 16, "hist": [0] * 24, "direct": True, "epoch": "e", "ctx": {"a": 1}}
            passive = {"v": 1, "ts": "x", "t": t + 1, "type": "passive", "devices": {"vda": {"w_bps": 1e6}}, "pressure": None}
            path = j.path_for(t)
            if path not in handles:
                handles[path] = open(path, "a")
            handles[path].write(json.dumps(probe) + "\n" + json.dumps(passive) + "\n")
    for h in handles.values():
        h.close()
    return t0 + days * D + 1


def test_h2_analyze_streams_a_200_day_journal_in_bounded_memory(world):
    now = synth_journal(world, 200)
    cfg = world.config()
    tracemalloc.start()
    t = time.time()
    text = analyze(cfg, str(world.state / "journal"), now=now, plots=False)
    peak = tracemalloc.get_traced_memory()[1]
    tracemalloc.stop()
    assert peak < 6 * MiB, peak  # the whole journal is ~9 600 records; the old code held all of them (tens of MiB)
    assert time.time() - t < 30
    assert "r_cold@vda" in text and "ALARM_DOWN" in text


def test_h2_prune_removes_old_monthly_files_and_never_the_current_month(world):
    d = world.state / "journal"
    d.mkdir(parents=True)
    now = 1_790_000_000.0  # 2026-09
    for name in ("journal-2024-01.jsonl", "journal-2025-12.jsonl", "journal-2026-08.jsonl", "journal-2026-09.jsonl"):
        (d / name).write_text("x" * 1000)
    assert prune(str(d), now, 400, 10**9, dry_run=True) == ["journal-2024-01.jsonl"]
    assert (d / "journal-2024-01.jsonl").exists()  # dry run
    assert prune(str(d), now, 400, 10**9) == ["journal-2024-01.jsonl"]
    assert prune(str(d), now, 10**6, 1500) == ["journal-2025-12.jsonl", "journal-2026-08.jsonl"]  # size cap: oldest first
    assert (d / "journal-2026-09.jsonl").exists()


def test_h2_prune_command_and_documented_defaults(world, capsys):
    cfg_path = world.root / "c.toml"
    cfg_path.write_text(f'state_dir = "{world.state}"\n')
    d = world.state / "journal"
    d.mkdir(parents=True, mode=0o700)
    (d / "journal-2001-01.jsonl").write_text("{}\n")
    assert main(["--config", str(cfg_path), "prune", "--dry-run"]) == 0
    assert "would remove: journal-2001-01.jsonl" in capsys.readouterr().out
    c = from_mapping({})
    assert c.journal_max_age_days == 400.0 and c.journal_max_bytes == 512 * MiB


def test_h2_covariates_and_settings_are_recorded_on_change_or_hourly_not_every_tick(world):
    env, clock = make_env()
    cfg = world.config(data_paths=[str(world.root)])
    for _ in range(3):
        run_tick(build(cfg, env))
        clock.t += 301
    starts = [r for r in records(world) if r["type"] == "run_start"]
    assert [bool(s.get("covariates")) for s in starts] == [True, False, False]
    write_file(world.sys / "class/block/vda/queue/nr_requests", "8\n")
    run_tick(build(cfg, env))
    assert records(world)[-1] or True
    starts = [r for r in records(world) if r["type"] == "run_start"]
    assert starts[-1].get("covariates")  # the settings epoch changed: recorded again
    clock.t += 3700
    run_tick(build(cfg, env))
    assert [r for r in records(world) if r["type"] == "run_start"][-1].get("covariates")  # hourly


def test_h2_aggregates_are_persisted_and_analyze_agrees_with_the_runner(world):
    env, clock = make_env()
    cfg = world.config()
    j_cfg = cfg
    for day in range(9):
        for k in range(4):
            run_tick(build(cfg, env))
            clock.t += 3600 * 6 - 1
    persisted = json.load(open(world.state / "agg.json"))
    assert persisted["v"] == 1 and persisted["lanes"]
    from iogov.report import replay
    eng, _ = replay(j_cfg, str(world.state / "journal"), now=clock.t)
    replayed = eng.to_dict()
    for lane, data in persisted["lanes"].items():  # same code path -> same state
        for key in ("phase", "reference_log2", "s_dn", "s_up", "windows_seen"):
            assert replayed["lanes"][lane][key] == data[key], (lane, key)


# ===== H3: no symlink / TOCTOU, directory hygiene, identity ==========================================================


def test_h3_scratch_name_swapped_after_create_never_writes_through_the_symlink(world, monkeypatch):
    victim = world.root / "victim.img"
    victim.write_bytes(b"\xAA" * (4 * MiB))
    before = victim.read_bytes()
    real_open = os.open

    def racing_open(path, flags, mode=0o777, **kw):
        fd = real_open(path, flags, mode, **kw)
        if str(path).endswith(".dat") and flags & os.O_EXCL:  # right after the exclusive create the attacker swaps the name
            os.rename(path, str(path) + ".moved")
            os.symlink(victim, path)
        return fd

    monkeypatch.setattr(os, "open", racing_open)
    env, _ = make_env()
    run_bench_cmd(build(world.config(), env), force=True)
    assert victim.read_bytes() == before
    bench = [r for r in records(world) if r["type"] == "bench"][-1]
    assert "unsafe_path" in bench["abort"]


def test_h3_scratch_is_used_through_one_descriptor_never_reopened_by_name(world, monkeypatch):
    env, _ = make_env()
    opened = []
    real = os.open
    monkeypatch.setattr(os, "open", lambda p, f, *a, **k: (opened.append(str(p)), real(p, f, *a, **k))[1])
    run_bench_cmd(build(world.config(), env), force=True)
    assert len([p for p in opened if p.endswith(".dat")]) == 1


def test_h3_symlinked_or_loose_directories_and_files_are_refused(world, capsys):
    env, _ = make_env()
    cfg = world.config()
    real_dir = world.root / "elsewhere"
    real_dir.mkdir(mode=0o700)
    os.symlink(real_dir, world.state)
    assert run_tick(build(cfg, env)) == 2 and "not a real directory" in capsys.readouterr().err
    os.unlink(world.state)
    world.state.mkdir(mode=0o770)
    os.chmod(world.state, 0o770)
    assert run_tick(build(cfg, env)) == 2 and "group/world writable" in capsys.readouterr().err
    os.chmod(world.state, 0o700)
    (world.state / "run.lock").write_text("")
    os.chmod(world.state / "run.lock", 0o666)
    assert run_tick(build(cfg, env)) == 2 and "unsafe mode" in capsys.readouterr().err


def test_h3_journal_and_state_files_that_are_symlinks_are_not_followed(world, capsys):
    env, _ = make_env()
    cfg = world.config()
    victim = world.root / "victim.txt"
    victim.write_text("keep")
    jdir = world.state / "journal"
    jdir.mkdir(parents=True, mode=0o700)
    j = Journal(str(jdir), env.wall)
    os.symlink(victim, j.path_for(env.wall()))
    with pytest.raises(safeio.UnsafePath):
        j.append("note", text="x")
    assert victim.read_text() == "keep"
    os.unlink(j.path_for(env.wall()))
    os.symlink(victim, world.state / "state.json")
    assert run_tick(build(cfg, env)) == 2
    assert victim.read_text() == "keep"


def test_h3_state_temp_file_is_exclusive_nofollow_with_a_random_name(world, monkeypatch):
    seen = []
    real = os.open
    monkeypatch.setattr(os, "open", lambda p, f, *a, **k: (seen.append((str(p), f)), real(p, f, *a, **k))[1])
    world.state.mkdir(mode=0o700)
    safeio.save_json(str(world.state / "state.json"), {"a": 1}, lambda n: os.urandom(n).hex())
    safeio.save_json(str(world.state / "state.json"), {"a": 2}, lambda n: os.urandom(n).hex())
    tmps = [(p, f) for p, f in seen if p.endswith(".tmp")]
    assert len(tmps) == 2 and tmps[0][0] != tmps[1][0]
    assert all(f & os.O_EXCL and f & os.O_NOFOLLOW for _, f in tmps)


def test_h3_root_is_refused_unless_allowed_and_then_needs_root_owned_dirs(world, monkeypatch, capsys):
    env, _ = make_env()
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    assert run_tick(build(world.config(), env)) == 2 and "refusing to run as root" in capsys.readouterr().err
    assert not world.state.exists()
    # allowed, but the (test) directories belong to another uid than 0: refused, nothing written
    assert run_tick(build(world.config(), env, allow_root=True)) == 2 and "not owned by the running user" in capsys.readouterr().err
    assert main(["--allow-root", "--config", str(world.root / "none.toml"), "config"]) == 2  # config errors stay errors


# ===== H4 / H5 / L6: persisted baselines, closed windows, false-alarm budget =====================================


def probe_rec(t, lat, epoch="e"):
    return {"type": "probe", "t": t, "lane": "r_cold", "device": "vda", "quiet": True, "n": 12, "lat_us": [lat] * 12, "epoch": epoch}


def test_h4_a_4x_degradation_from_day_30_is_still_reported_at_day_200_without_spurious_baselines(world):
    cfg = world.config()
    t0 = 20000 * D
    eng = Engine(cfg, keep_events=True)
    kinds, ratio_at = [], {}
    for day in range(200):
        lat = 500 if day < 30 else 2000
        eng = Engine(cfg, json.loads(json.dumps(eng.to_dict())), keep_events=False)  # a restart every day: state is persisted, not re-derived
        for h in range(24):
            eng.feed(probe_rec(t0 + day * D + h * 3600, lat))
        eng.close_up_to(t0 + (day + 1) * D + 1)
        kinds += [e["kind"] for e in eng.drain()]
        if day in (100, 199):
            st = eng.lanes["r_cold@vda"]
            ratio_at[day] = (st.display_phase, round(st.ratio(), 2))
    assert kinds.count("baseline_established") == 1 and kinds.count("shift_detected") == 1
    assert ratio_at[100] == ratio_at[199] == ("ALARM_DOWN", 0.25)


def test_h4_baseline_reset_notes_never_expire(world):
    cfg = world.config()
    t0 = 20000 * D
    eng = Engine(cfg, keep_events=True)
    for day in range(40):
        for h in range(24):
            eng.feed(probe_rec(t0 + day * D + h * 3600, 500 if day < 10 else 2000))
        if day == 20:
            eng.add_reset(t0 + day * D + 100)
        eng.close_up_to(t0 + (day + 1) * D + 1)
        eng = Engine(cfg, json.loads(json.dumps(eng.to_dict())), keep_events=True)
    st = eng.lanes["r_cold@vda"]
    assert st.phase == "OK" and st.reference == pytest.approx(1e6 / 2000, rel=0.05)  # the operator's reset is still in force at day 40


def test_h5_a_partial_day_never_produces_a_verdict(world):
    cfg = world.config(confirm_windows=2)
    t0 = 20000 * D
    eng = Engine(cfg, keep_events=True)
    for day in range(5):
        for i in range(288):
            eng.feed(probe_rec(t0 + day * D + i * 300, 500))
    for day in (5, 6):
        for i in range(288):
            eng.feed(probe_rec(t0 + day * D + i * 300, 2000))
    for i in range(3):  # the first 15 minutes of day 7 are bad, the day is nowhere near over
        eng.feed(probe_rec(t0 + 7 * D + i * 300, 2000))
    eng.close_up_to(t0 + 7 * D + 900)
    st = eng.lanes["r_cold@vda"]
    w0 = 20000
    assert st.last_valid_window == w0 + 6 and all(r.start <= w0 + 6 for r in st.rows)  # window 7 is open and was not evaluated
    assert w0 + 7 not in {e["evidence"]["window_start"] for e in eng.drain()}
    eng.close_up_to(t0 + 8 * D)
    assert eng.lanes["r_cold@vda"].last_valid_window == w0 + 7


def test_l6_false_alarm_budget_at_the_documented_noise_levels():
    cfg = from_mapping({})
    gens = {
        "gauss 0.3": lambda r: 2 ** r.gauss(0, 0.3),
        "gauss 0.5": lambda r: 2 ** r.gauss(0, 0.5),
        "15% slow days (3x) on sigma 0.2": lambda r: 2 ** r.gauss(0, 0.2) / (3 if r.random() < 0.15 else 1),
    }
    for name, gen in gens.items():
        alarms = 0
        for seed in range(300):
            rng = random.Random(seed)
            wins = {i: Window(i, 1, [340e6 * gen(rng)], set()) for i in range(365)}
            alarms += sum(1 for e in analyze_lane("w_fresh_qd4", wins, [], cfg, D).events if e["kind"] == "shift_detected")
        assert alarms / 300 <= 1.0, (name, alarms / 300)  # documented budget: <= 1 false shift per lane-year


def test_l6_windows_missing_between_bad_windows_break_the_streak():
    cfg = from_mapping({})
    levels = {i: Window(i, 1, [340e6], set()) for i in range(8)}
    for i in (9, 12, 15, 18, 21):  # bad every third day, nothing in between: never three ADJACENT bad windows
        levels[i] = Window(i, 1, [340e6 / 20], set())
    res = analyze_lane("w_fresh_qd4", levels, [], cfg, D)
    assert not [e for e in res.events if e["kind"] == "shift_detected"]


def test_l6_settings_change_is_flagged_after_a_baseline_reset_too():
    cfg = from_mapping({})
    wins = {i: Window(i, 1, [340e6], {"a"}) for i in range(8)}
    wins.update({i: Window(i, 1, [340e6 / 20], {"b"}) for i in range(8, 16)})
    wins.update({i: Window(i, 1, [340e6 / 400], {"c"}) for i in range(16, 24)})
    res = analyze_lane("w_fresh_qd4", wins, [(8 + 6) * D], cfg, D)  # reset at day 14: reference = the epoch-b regime
    late = [e for e in res.events if e["kind"] == "shift_detected" and e["evidence"]["window_start"] > 16]
    assert late and late[0]["evidence"]["confounded"] is True


def test_l7_a_capped_lane_reports_a_lower_bound():
    cfg = from_mapping({})
    wins = {i: Window(i, 1, [256 * MiB], set(), capped=1, records=1) for i in range(8)}
    wins.update({i: Window(i, 1, [256 * MiB / 20], set()) for i in range(8, 14)})
    ev = [e for e in analyze_lane("w_fresh_qd4", wins, [], cfg, D).events if e["kind"] == "shift_detected"][0]
    assert "by at least" in ev["text"] and ev["evidence"]["factor_is_lower_bound"] is True


def test_l4_samples_that_did_not_bypass_the_page_cache_are_not_evidence():
    assert points_of({"type": "probe", "quiet": True, "direct": False, "lane": "r_cold", "device": "vda", "n": 1, "lat_us": [5]}) == []
    assert points_of({"type": "probe", "quiet": True, "direct": True, "lane": "r_cold", "device": "vda", "n": 1, "lat_us": [5]})


# ===== M1: locks ====================================================================================================


def test_m1_a_second_run_never_overlaps_and_state_is_not_clobbered(world):
    cfg = world.config()
    env1, _ = make_env(1)
    env2, _ = make_env(2)
    rt_tick, rt_bench = build(cfg, env2), build(cfg, env1)  # both timers fire at the same moment
    run_bench_cmd(rt_bench)
    nb = state(world)["next_bench"]
    run_tick(rt_tick)  # the tick finishes last: it must not overwrite what the bench persisted
    st = state(world)
    assert st["next_bench"] == nb and st["budget"]["write_bytes"] > 0
    assert not build(cfg, make_env(3)[0]) is None
    from iogov.runner import bench_due
    rt = build(cfg, make_env(3)[0])
    rt.state = state(world)
    assert not bench_due(rt)


def test_m1_run_lock_excludes_tick_and_bench(world):
    env, _ = make_env()
    cfg = world.config()
    world.state.mkdir(mode=0o700)
    lock = safeio.FileLock(str(world.state / "run.lock"))
    assert lock.acquire()
    assert run_tick(build(cfg, env)) == 0 and run_bench_cmd(build(cfg, env), force=True) == 0
    assert not (world.state / "journal").exists() or not records(world)
    lock.release()
    run_tick(build(cfg, env))
    assert [r for r in records(world) if r["type"] == "probe"]


def test_m1_concurrent_runs_lose_no_budget_updates(world):
    cfg = world.config()
    errors = []

    def one(seed):
        try:
            for _ in range(3):
                env, _ = make_env(seed)
                run_tick(build(cfg, env))
        except Exception as exc:  # pragma: no cover
            errors.append(exc)

    threads = [threading.Thread(target=one, args=(i,)) for i in range(4)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert not errors
    ran = [r for r in records(world) if r["type"] == "run_end" and r["status"] == "ok"]
    assert 1 <= len(ran) <= 12
    assert state(world)["budget"]["read_bytes"] == sum(r["io"]["read_bytes"] for r in ran)  # no lost update
    for line in open(next((world.state / "journal").glob("*.jsonl"))):
        json.loads(line)  # no interleaved/torn lines


# ===== M2: hard clamps, config file ownership ======================================================================


def test_m2_every_safety_limit_is_clamped_and_reported():
    c = from_mapping({
        "max_read_bytes_per_day": 10**15, "max_write_bytes_per_day": 10**15, "write_bps_max": 1e15, "read_bps_max": 1e15,
        "sync_every_bytes": 1024 * MiB, "read_max_attempts": 10**6, "abort_latency_ms": 1e9, "pressure_recheck_every_ops": 10**9,
        "tick_pressure_some_max": 100.0, "bench_pressure_some_max": 100.0, "loadavg_per_cpu_max": 1e6, "flush_limit_s": 1e6,
        "block_bytes": 1, "seq_read_block": 100, "sync4k_samples": 0, "read_samples_per_tick": 0, "qd_low": 0,
    })
    assert c.max_read_bytes_per_day <= 256 * MiB and c.max_write_bytes_per_day <= 2 * 1024 * MiB
    assert c.write_bps_max <= 512 * MiB and c.sync_every_bytes <= 8 * MiB and c.read_max_attempts <= 128
    assert c.abort_latency_ms <= 1000 and c.pressure_recheck_every_ops <= 256 and c.flush_limit_s <= 10
    assert c.tick_pressure_some_max <= 25 and c.bench_pressure_some_max <= 5 and c.loadavg_per_cpu_max <= 4
    assert c.block_bytes == 4096 and c.seq_read_block == 4096 and c.sync4k_samples >= 1 and c.read_samples_per_tick >= 1 and c.qd_low >= 1
    assert len(c.clamped) >= 15


def test_m2_read_block_bytes_is_gone_because_it_never_changed_the_read_size():
    with pytest.raises(ConfigError, match="unknown"):
        from_mapping({"read_block_bytes": 8192})


def test_m2_config_files_writable_by_others_are_refused(tmp_path):
    p = tmp_path / "iogov.toml"
    p.write_text("read_iops_max = 5.0\n")
    os.chmod(p, 0o644)
    assert load(str(p)).read_iops_max == 5.0
    os.chmod(p, 0o666)
    with pytest.raises(ConfigError, match="refusing a config file"):
        load(str(p))


# ===== M3: flush watchdog, adaptive flush size, guarded offset probes =================================================


def test_m3_a_slow_flush_ends_the_lane_as_slow_flush(world, monkeypatch):
    env, clock = make_env()
    cfg = world.config(flush_limit_s=1.0)
    real = os.fdatasync
    monkeypatch.setattr(os, "fdatasync", lambda fd: (real(fd), setattr(clock, "t", clock.t + 5))[0])
    guard = RunGuard(cfg, Collector(cfg, [], env), env, True)
    with Scratch(cfg, env, 1 * MiB) as sc:
        lane = seq_write_phase(sc, cfg, env, guard, DailyBudget({}, cfg, env.wall), 0, 1 * MiB, 1)
    assert lane["aborted"] == "slow_flush" and lane["syncs"] == 0


def test_m3_the_watchdog_raises_the_stop_flag_while_a_flush_is_still_blocked(world, monkeypatch):
    env, _ = make_env()
    cfg = world.config(flush_limit_s=0.05)
    monkeypatch.setattr(os, "fdatasync", lambda fd: time.sleep(0.4))
    bench_mod.timed_flush(0, cfg, env)  # (the fake monotonic clock saw no delay, so no lane abort: only the real-time watchdog fired)
    assert env.stop.reason == "slow_flush"


def test_m3_flush_size_adapts_to_the_measured_write_rate(world):
    env, clock = make_env()
    cfg = world.config(abort_latency_ms=1000.0, phase_wall_s=120.0)
    guard = RunGuard(cfg, Collector(cfg, [], env), env, True)
    with Scratch(cfg, env, 2 * MiB) as sc:
        fast = seq_write_phase(sc, cfg, env, guard, DailyBudget({}, cfg, env.wall), 0, 1 * MiB, 1)
        clock.step = 0.2  # every chunk now takes 200 ms: ~320 KiB/s
        slow = seq_write_phase(sc, cfg, env, guard, DailyBudget({}, cfg, env.wall), 1 * MiB, 1 * MiB, 1)
    assert fast["syncs"] == 2 and slow["syncs"] >= 5  # a flush must stay near 250 ms of data


def test_m3_every_offset_probe_read_in_seq_read_is_guarded(world):
    env, clock = make_env()
    cfg = world.config(abort_latency_ms=50.0)
    clock.step = 0.5
    guard = RunGuard(cfg, Collector(cfg, [], env), env, True)
    with RawReader(str(world.disk), env, allow_regular=True) as r:
        lane = seq_read(r, cfg, env, guard, DailyBudget({}, cfg, env.wall))
    assert lane["aborted"].startswith("slow_io") and lane["bytes"] == 0


# ===== M4: surviving mutants ========================================================================================


def test_m4_the_4k_lane_syncs_every_sample_and_aborts_on_a_slow_io(world, monkeypatch):
    env, clock = make_env()
    cfg = world.config(sync4k_samples=16, abort_latency_ms=50.0)
    guard = RunGuard(cfg, Collector(cfg, [], env), env, True)
    syncs = []
    real = os.fdatasync
    monkeypatch.setattr(os, "fdatasync", lambda fd: (syncs.append(fd), real(fd))[1])
    with Scratch(cfg, env, 1 * MiB) as sc:
        lane = over_4k(sc, cfg, env, guard, DailyBudget({}, cfg, env.wall), 256 * 1024)
        assert lane["n"] == 16 and len(syncs) == 16 and lane["aborted"] is None
        clock.step = 0.5
        slow = over_4k(sc, cfg, env, guard, DailyBudget({}, cfg, env.wall), 256 * 1024)
    assert slow["aborted"].startswith("slow_io") and slow["n"] == 1


def test_m4_the_sequential_phase_really_calls_fdatasync(world, monkeypatch):
    env, _ = make_env()
    cfg = world.config()
    guard = RunGuard(cfg, Collector(cfg, [], env), env, True)
    syncs = []
    real = os.fdatasync
    monkeypatch.setattr(os, "fdatasync", lambda fd: (syncs.append(fd), real(fd))[1])
    with Scratch(cfg, env, 1 * MiB) as sc:
        lane = seq_write_phase(sc, cfg, env, guard, DailyBudget({}, cfg, env.wall), 0, 1 * MiB, 4)
    assert lane["syncs"] == len(syncs) >= 2


@pytest.mark.parametrize("qd", [1, 4])
def test_m4_write_phases_check_the_guard_before_every_chunk(world, qd):
    env, _ = make_env()
    cfg = world.config()
    guard = RunGuard(cfg, Collector(cfg, [], env), env, True)
    calls = {"n": 0}
    real = os.pwrite

    def spy(fd, buf, off):
        calls["n"] += 1
        if calls["n"] == 2:
            env.stop.set("signal:SIGTERM")
        return real(fd, buf, off)

    os.pwrite = spy
    try:
        with Scratch(cfg, env, 2 * MiB) as sc:
            lane = seq_write_phase(sc, cfg, env, guard, DailyBudget({}, cfg, env.wall), 0, 1 * MiB, qd)
    finally:
        os.pwrite = real
    assert lane["aborted"] == "signal:SIGTERM" and lane["bytes"] <= 64 * 1024 * (2 + qd)


def test_m4_recovery_needs_two_consecutive_normal_windows_not_one():
    cfg = from_mapping({})
    base, bad = 340e6, 340e6 / 20
    levels = [base] * 8 + [bad] * 6 + [base, bad, bad, bad, bad] + [base, base, base]
    wins = {i: Window(i, 1, [v], set()) for i, v in enumerate(levels)}
    res = analyze_lane("w_fresh_qd4", wins, [], cfg, D)
    cleared = [e["evidence"]["window_start"] for e in res.events if e["kind"] == "shift_cleared"]
    assert cleared == [len(levels) - 2]  # one lucky window at index 14 did not clear it; two in a row at the end did


def test_m4_witness_blocks_that_became_zero_are_retired(world):
    env, _ = make_env()
    cfg = world.config(read_samples_per_tick=4, witness_reads=2)
    st = {"devices": {"vda": {"n_blocks": 4096, "stride": 1, "start": 0, "counter": 0, "witness": [10, 11, 12]}}}
    world.make_disk(16 * MiB, zero_ranges=[(10 * 4096, 11 * 4096)])  # block 10 is now zero
    with RawReader(str(world.disk), env, allow_regular=True) as r:
        guard = RunGuard(cfg, Collector(cfg, [], env), env, False)
        tick_reads(r, cfg, env, guard, DailyBudget({}, cfg, env.wall), st)
    assert 10 not in st["devices"]["vda"]["witness"]


def test_m4_seq_read_only_starts_on_a_non_zero_block(world):
    cfg = world.config(seq_read_bytes=512 * 1024, seq_read_block=64 * 1024)
    world.make_disk(16 * MiB, zero_ranges=[(0, 8 * MiB)])
    for seed in range(25):
        env, _ = make_env(seed)
        guard = RunGuard(cfg, Collector(cfg, [], env), env, True)
        with RawReader(str(world.disk), env, allow_regular=True) as r:
            lane = seq_read(r, cfg, env, guard, DailyBudget({}, cfg, env.wall))
        assert lane["valid"] is True or lane["aborted"] == "no_nonzero_offset_found", (seed, lane)
        if lane["valid"]:
            assert lane["nonzero_fraction"] == 1.0 and lane["offset_block"] * cfg.seq_read_block >= 8 * MiB


# ===== M5: unit hardening =============================================================================================


def test_m5_service_templates_carry_the_review_hardening():
    from pathlib import Path
    for name in ("iogov-tick", "iogov-bench", "iogov-watch"):
        text = (Path(__file__).resolve().parents[1] / "units" / f"{name}.service.in").read_text()
        for needle in ("ProtectKernelTunables=yes", "ProtectControlGroups=yes", "CapabilityBoundingSet=\n", "PrivateNetwork=yes",
                       "SystemCallFilter=@system-service", "StateDirectory=iogov", "UMask=0077"):
            assert needle in text, (name, needle)


# ===== M6: container resolution ======================================================================================


def test_m6_resolution_is_cached_and_survives_non_dict_json(world, monkeypatch):
    import glob as glob_mod
    cdir = world.root / "docker"
    cid = "a" * 64
    write_file(cdir / cid / "config.v2.json", json.dumps({"Name": "/game", "Config": {"Env": ["SECRET=1"]}}))
    write_file(cdir / ("b" * 64) / "config.v2.json", "[1, 2, 3]")  # not a dict
    write_file(cdir / ("c" * 64) / "config.v2.json", '"a string"')
    os.makedirs(world.cg / f"system.slice/docker-{cid}.scope")
    cfg = world.config(containers=["game", "nope"], docker_containers_dir=str(cdir))
    env, clock = make_env()
    res = ContainerResolver(cfg, env.mono)
    calls = {"n": 0}
    real = glob_mod.glob
    monkeypatch.setattr(glob_mod, "glob", lambda *a, **k: (calls.__setitem__("n", calls["n"] + 1), real(*a, **k))[1])
    out = res.resolve(cfg.containers)
    assert out["game"].endswith(f"docker-{cid}.scope") and out["nope"] is None
    first = calls["n"]
    for _ in range(50):  # 50 ticks / seconds of `watch`
        clock.t += 0.5
        res.resolve(cfg.containers)
    assert calls["n"] - first <= 2  # not 50 x tree walks


def test_m6_explicit_cgroup_paths_need_no_config_v2_json(world):
    cfg = world.config(containers=["game"], container_cgroups={"game": str(world.cg / "game.slice")}, docker_containers_dir=str(world.root / "nodocker"))
    env, _ = make_env()
    assert ContainerResolver(cfg, env.mono).resolve(["game"]) == {"game": str(world.cg / "game.slice")}


def test_m6_reparse_only_when_the_file_changes(world):
    cdir = world.root / "docker"
    cid = "d" * 64
    f = cdir / cid / "config.v2.json"
    write_file(f, json.dumps({"Name": "/one"}))
    cfg = world.config(containers=["one", "two"], docker_containers_dir=str(cdir))
    env, clock = make_env()
    res = ContainerResolver(cfg, env.mono, rescan_s=10.0)
    res.resolve(["one"])
    assert res._parsed[str(f)][1] == "one"
    write_file(f, json.dumps({"Name": "/two"}))
    os.utime(f, (time.time() + 5, time.time() + 5))
    clock.t += 11
    res.resolve(["two"])
    assert res._parsed[str(f)][1] == "two"


# ===== L1..L5, L8 ======================================================================================================


def test_l1_a_short_pwrite_aborts_the_lane_without_merging(world, monkeypatch):
    env, _ = make_env()
    cfg = world.config()
    guard = RunGuard(cfg, Collector(cfg, [], env), env, True)
    real = os.pwrite
    monkeypatch.setattr(os, "pwrite", lambda fd, buf, off: (real(fd, buf, off), len(buf) - 1)[1])  # the kernel reports a short write
    with Scratch(cfg, env, 1 * MiB) as sc:
        lane = seq_write_phase(sc, cfg, env, guard, DailyBudget({}, cfg, env.wall), 0, 512 * 1024, 1)
        lane4 = over_4k(sc, cfg, env, guard, DailyBudget({}, cfg, env.wall), 256 * 1024)
    assert lane["aborted"].startswith("short_write") and lane4["aborted"].startswith("short_write")


def test_l1_oversized_provider_file_is_hashed_and_bad_snapshot_values_tolerated(world):
    big = {"used_bytes": 5, "total_bytes": 10, "blob": "x" * 10000, "snapshot_created": "yesterday"}
    write_file(world.root / "prov.json", json.dumps(big))
    cfg = world.config(provider_signal_file=str(world.root / "prov.json"))
    p = cov.provider_signal(cfg, time.time)
    assert p["truncated"] is True and len(json.dumps(p)) < 600 and "blob" not in p
    c = {"fill": {}, "fstrim": {"last_run": time.time()}, "provider": {"snapshot_created": "yesterday", "used_bytes": "x"}, "mounts": {}}
    codes = {f["code"] for f in cov.prevention(cfg, c, time.time)}
    assert "snapshot_unreadable" in codes


def test_l2_a_wall_clock_that_jumped_backwards_cannot_freeze_the_bench(world):
    env, _ = make_env()
    cfg = world.config()
    world.state.mkdir(mode=0o700)
    write_file(world.state / "state.json", json.dumps({"next_bench": env.wall() + 400 * D}))
    os.chmod(world.state / "state.json", 0o600)
    run_bench_cmd(build(cfg, env))
    assert [r for r in records(world) if r["type"] == "bench"]


def test_l3_stale_scratch_is_removed_even_with_a_kill_switch_and_odd_names_do_not_crash(world):
    env, _ = make_env()
    cfg = world.config()
    world.scratch.mkdir(mode=0o700)
    stale = world.scratch / (PREFIX + "0123456789ab.dat")
    stale.write_bytes(b"x")
    (world.scratch / (PREFIX + "adirectory")).mkdir()
    os.symlink("/etc/hostname", world.scratch / (PREFIX + "alink.dat"))
    (world.root / "etc").mkdir()
    (world.root / "etc/disabled").write_text("")
    run_tick(build(cfg, env))
    assert not stale.exists() and (world.scratch / (PREFIX + "adirectory")).is_dir() and os.path.islink(world.scratch / (PREFIX + "alink.dat"))
    (world.root / "etc/disabled").unlink()
    run_bench_cmd(build(cfg, env), force=True)  # the directory with our prefix must not crash the next bench
    assert [r for r in records(world) if r["type"] == "bench"][-1]["status"] == "ok"


def test_l4_o_direct_refusal_on_a_block_device_is_never_downgraded_to_buffered_reads(world, monkeypatch):
    env, _ = make_env()
    real = os.open
    opened = []

    def refuse(path, flags, *a, **k):
        opened.append(flags)
        if flags & os.O_DIRECT:
            raise OSError(errno.EINVAL, "no direct")
        return real(path, flags, *a, **k)

    monkeypatch.setattr(os, "open", refuse)
    monkeypatch.setattr(rawread.stat, "S_ISBLK", lambda m: True)  # pretend the node is a block device
    monkeypatch.setattr(rawread.stat, "S_ISREG", lambda m: False)
    with pytest.raises(RawReadError, match="lane unsupported"):
        RawReader(str(world.disk), env).open()
    assert len(opened) == 1 and opened[0] & os.O_DIRECT  # no second, buffered open


def test_l5_the_content_check_makes_no_copy_of_the_data():
    buf = AlignedBuffer(2 * MiB)
    buf.mm[:] = b"\x01" * (2 * MiB)
    tracemalloc.start()
    assert buf.nonzero() is True
    buf.wipe()
    assert buf.nonzero() is False
    peak = tracemalloc.get_traced_memory()[1]
    tracemalloc.stop()
    assert peak < 256 * 1024  # the old int.from_bytes(mm[:n]) made a 2 MiB copy (and an int of the same size) every time


def test_l8_our_own_probe_slice_is_not_reported_as_another_writer(world):
    env, clock = make_env()
    for name in ("iogov-probe.slice", "system.slice"):
        write_file(world.cg / name / "io.stat", "8:0 rbytes=0 wbytes=0 rios=0 wios=0\n")
    cfg = world.config()
    col = Collector(cfg, build(cfg, env).disks, env)
    a = col.snapshot()
    clock.t += 10
    for name in ("iogov-probe.slice", "system.slice"):
        write_file(world.cg / name / "io.stat", "8:0 rbytes=0 wbytes=90000000 rios=0 wios=9\n")
    s = passive_summary(a, col.snapshot())
    assert list(s["others_writing"]) == ["system.slice"]
