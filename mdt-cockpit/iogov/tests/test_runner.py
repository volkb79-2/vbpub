"""End to end on fakes: tick, bench, gating skips, dry run, kill switch, signal cleanup, schema of everything emitted."""

from __future__ import annotations

import hashlib
import json
import os

import pytest
from conftest import make_env

from iogov.cli import main
from iogov.journal import read_records, validate_record
from iogov.runner import build, cleanup, plan_text, run_bench_cmd, run_tick
from iogov.scratch import PREFIX


def records(world):
    return list(read_records(str(world.state / "journal")))


def sha(path) -> str:
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


def test_tick_journals_probes_passive_and_never_modifies_the_device(world):
    env, clock = make_env()
    cfg = world.config()
    before = (sha(world.disk), os.stat(world.disk).st_mtime_ns)
    assert run_tick(build(cfg, env)) == 0
    after = (sha(world.disk), os.stat(world.disk).st_mtime_ns)
    assert before == after
    recs = records(world)
    types = [r["type"] for r in recs]
    assert types[0] == "run_start" and types[-1] == "run_end"
    assert "passive" in types
    lanes = {r["lane"]: r for r in recs if r["type"] == "probe"}
    assert set(lanes) == {"r_cold", "r_witness"}
    assert lanes["r_cold"]["n"] == 6 and lanes["r_cold"]["quiet"] is True and lanes["r_cold"]["device"] == "vda"
    assert all(validate_record(r) == [] for r in recs)
    assert all(r["v"] == 1 for r in recs)


def test_second_tick_reads_witnesses_and_the_walk_continues(world):
    env, clock = make_env()
    cfg = world.config()
    run_tick(build(cfg, env))
    run_tick(build(cfg, env))
    probes = [r for r in records(world) if r["type"] == "probe"]
    witness = [r for r in probes if r["lane"] == "r_witness"]
    assert witness[0]["n"] == 0 and witness[1]["n"] == 2
    state = json.load(open(world.state / "state.json"))
    assert state["devices"]["vda"]["counter"] >= 12


def test_zero_blocks_are_counted_and_excluded(world):
    env, _ = make_env()
    world.make_disk(16 * 1024 * 1024, zero_ranges=[(0, 8 * 1024 * 1024)])
    cfg = world.config(read_samples_per_tick=8, read_max_attempts=24)
    run_tick(build(cfg, env))
    rec = next(r for r in records(world) if r["type"] == "probe" and r["lane"] == "r_cold")
    assert rec["zero"] > 0
    assert rec["n"] + rec["zero"] == rec["attempts"] - rec["errors"]


def test_busy_host_skips_without_any_probe_io_and_journals_once(world):
    env, clock = make_env()
    cfg = world.config(skip_passive_interval_s=60.0)  # ticks below are 700 s apart: every one is due for a passive snapshot
    world.set_pressure(40.0, 5.0)
    for _ in range(3):
        run_tick(build(cfg, env))
        clock.t += 700  # past the (growing) backoff but inside skip_log_interval_s
    recs = records(world)
    skips = [r for r in recs if r["type"] == "skip"]
    assert len(skips) == 1 and skips[0]["reasons"][0].startswith("io_pressure_high")  # deduplicated within skip_log_interval_s
    # the passive evidence of the busy moment is kept on every gated tick (no probe record, no run_start: no probe IO happened)
    passive = [r for r in recs if r["type"] == "passive"]
    assert len(passive) == 3 and all(r["gated"] and r["gate_reasons"] for r in passive)
    assert not [r for r in recs if r["type"] in ("probe", "run_start", "run_end")]


def test_backoff_blocks_the_next_runs_until_it_expires(world):
    env, clock = make_env()
    cfg = world.config()
    world.set_pressure(40.0, 0.0)
    run_tick(build(cfg, env))
    world.set_pressure(0.0, 0.0)
    run_tick(build(cfg, env))  # still inside the backoff window: no run
    assert not [r for r in records(world) if r["type"] == "probe"]
    clock.t += cfg.backoff_base_s + 1
    run_tick(build(cfg, env))
    assert [r for r in records(world) if r["type"] == "probe"]


def test_kill_switch_flag_file_stops_everything(world):
    env, _ = make_env()
    cfg = world.config()
    (world.root / "etc").mkdir()
    (world.root / "etc/disabled").write_text("")
    run_tick(build(cfg, env))
    run_bench_cmd(build(cfg, env), force=True)
    recs = records(world)
    assert {r["type"] for r in recs} == {"skip"}
    assert all("kill_switch" in r["reasons"][0] for r in recs)
    assert not world.scratch.exists() or not any(world.scratch.glob(PREFIX + "*"))


def test_kill_switch_appearing_mid_run_aborts_at_the_next_pressure_recheck(world):
    env, clock = make_env()
    cfg = world.config(pressure_recheck_every_ops=2, read_samples_per_tick=40, read_max_attempts=40)
    clock.on_sleep = None
    ks = world.root / "etc/disabled"
    ks.parent.mkdir()
    calls = {"n": 0}
    orig = os.preadv

    def spy(fd, bufs, off):
        calls["n"] += 1
        if calls["n"] == 5:
            ks.write_text("")
        return orig(fd, bufs, off)

    os.preadv = spy
    try:
        run_tick(build(cfg, env))
    finally:
        os.preadv = orig
    end = [r for r in records(world) if r["type"] == "run_end"][0]
    assert end["status"] == "aborted" and "kill_switch" in end["abort"]
    assert calls["n"] < 12


def test_bench_runs_all_lanes_and_removes_its_file(world):
    env, _ = make_env()
    cfg = world.config()
    assert run_bench_cmd(build(cfg, env), force=True) == 0
    bench = next(r for r in records(world) if r["type"] == "bench")
    assert bench["status"] == "ok"
    assert set(bench["lanes"]) == {"w_fresh_qd1", "w_fresh_qd4", "w_over_4k", "r_seq"}
    assert all(l["valid"] for l in bench["lanes"].values())
    assert bench["region"]["mode"] == "file" and bench["region"]["probe_bytes"] == 1024 * 1024
    assert not [n for n in os.listdir(world.scratch) if n.startswith(PREFIX)]
    assert all(validate_record(r) == [] for r in records(world))


def test_bench_is_a_noop_until_due(world):
    env, clock = make_env()
    cfg = world.config()
    run_bench_cmd(build(cfg, env), force=True)
    n = len(records(world))
    run_bench_cmd(build(cfg, env))
    assert len(records(world)) == n
    clock.t += cfg.bench_interval_h * 3600 * 1.2
    run_bench_cmd(build(cfg, env))
    assert len(records(world)) > n


def test_bench_requires_calm_unlike_tick(world):
    env, _ = make_env()
    cfg = world.config()
    world.set_pressure(3.0, 0.0)  # fine for a tick (limit 10), not for the bench (limit 1)
    run_tick(build(cfg, env))
    run_bench_cmd(build(cfg, env), force=True)
    recs = records(world)
    assert any(r["type"] == "probe" for r in recs)
    skip = next(r for r in recs if r["type"] == "skip")
    assert skip["kind"] == "bench"


def test_bench_skips_when_the_device_has_requests_in_flight_or_the_game_is_stalled(world):
    env, clock = make_env()
    cfg = world.config()
    world.set_inflight(3)
    run_bench_cmd(build(cfg, env), force=True)
    world.set_inflight(0)
    clock.t += 10_000
    world.set_cgroup_pressure("game.slice", 7.0)
    run_bench_cmd(build(cfg, env), force=True)
    reasons = [r["reasons"][0].split(":")[0] for r in records(world) if r["type"] == "skip"]
    assert reasons == ["device_busy", "protected_pressure"]


def test_sigterm_during_bench_cleans_up_and_journals_the_abort(world):
    env, clock = make_env()
    cfg = world.config()
    calls = {"n": 0}
    orig = os.pwrite

    def spy(fd, data, off):
        calls["n"] += 1
        if calls["n"] == 3:
            env.stop.set("signal:SIGTERM")  # what the real handler does
        return orig(fd, data, off)

    os.pwrite = spy
    try:
        run_bench_cmd(build(cfg, env), force=True)
    finally:
        os.pwrite = orig
    bench = next(r for r in records(world) if r["type"] == "bench")
    assert bench["status"] == "aborted" and bench["abort"] == "signal:SIGTERM"
    assert not [n for n in os.listdir(world.scratch) if n.startswith(PREFIX)]


def test_a_crash_inside_the_bench_is_an_abort_that_removes_the_scratch_file_and_saves_state(world, monkeypatch):
    env, _ = make_env()
    cfg = world.config()
    import iogov.bench as bench_mod

    def boom(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(bench_mod, "over_4k", boom)
    assert run_bench_cmd(build(cfg, env), force=True) == 0
    bench = next(r for r in records(world) if r["type"] == "bench")
    assert bench["status"] == "aborted" and bench["abort"].startswith("error:RuntimeError")
    assert not [n for n in os.listdir(world.scratch) if n.startswith(PREFIX)]
    assert json.load(open(world.state / "state.json"))["bench_fail"]["n"] == 1


def test_stale_scratch_file_from_a_killed_run_is_removed(world):
    env, _ = make_env()
    cfg = world.config()
    world.scratch.mkdir(mode=0o700)
    stale = world.scratch / (PREFIX + "deadbeef0123.dat")
    stale.write_bytes(b"x" * 4096)
    assert cleanup(cfg) == [stale.name]
    stale.write_bytes(b"x")
    run_bench_cmd(build(cfg, env), force=True)
    assert not stale.exists()


def test_dry_run_prints_the_plan_and_touches_nothing(world, capsys):
    cfg_path = world.root / "iogov.toml"
    cfg_path.write_text(f'state_dir = "{world.state}"\nscratch_dir = "{world.scratch}"\n'
                        f'sysfs_root = "{world.sys}"\nprocfs_root = "{world.proc}"\ncgroup_root = "{world.cg}"\n'
                        f'dev_root = "{world.dev}"\ndevices = ["{world.disk}"]\nallow_regular_files_as_devices = true\n'
                        f'scratch_fs_allowed = []\nkill_switch_file = "{world.root}/etc/disabled"\n')
    before = sha(world.disk)
    opened = []
    orig = os.open

    def spy(path, flags, *a, **k):
        opened.append(str(path))
        return orig(path, flags, *a, **k)

    os.open = spy
    try:
        for argv in (["plan"], ["tick", "--dry-run"], ["bench", "--dry-run"]):
            assert main(["--config", str(cfg_path)] + argv) == 0
    finally:
        os.open = orig
    out = capsys.readouterr().out
    assert "dry run" in out and "changes no cap" in out
    assert not world.state.exists() and not world.scratch.exists()
    assert not [p for p in opened if p.startswith(str(world.dev))]
    assert sha(world.disk) == before


def test_plan_text_lists_devices_budget_and_gates(world):
    env, _ = make_env()
    text = plan_text(build(world.config(), env, load=False))
    for needle in ("devices:", "vda", "budget:", "write_bytes_per_day", "gates:", "bench"):
        assert needle in text


def test_every_emitted_record_has_a_schema_version_and_validates(world):
    env, clock = make_env()
    cfg = world.config()
    run_tick(build(cfg, env))
    run_bench_cmd(build(cfg, env), force=True)
    main_args = ["--config", "/nonexistent"]  # config errors are exit 2
    assert main(main_args + ["status"]) == 2
    raw = []
    for f in sorted((world.state / "journal").glob("journal-*.jsonl")):
        raw += [json.loads(l) for l in open(f)]
    assert raw and all(r["v"] == 1 and not validate_record(r) for r in raw)
