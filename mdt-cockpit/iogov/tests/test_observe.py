"""Gating/kill switches, journal schema, config, device detection, collectors, covariates, prevention, episodes, who-else-writes."""

from __future__ import annotations

import json
import os
import time

import pytest
from conftest import make_env, write_file

from iogov import covariates as cov
from iogov.cli import main
from iogov.collectors import (Collector, cgroup_rates, parse_block_stat, parse_io_stat, parse_pressure, passive_summary, settings_epoch)
from iogov.config import ConfigError, from_mapping, load
from iogov.devices import detect, stable_node, whole_disks_of, disks_for_path
from iogov.env import StopRequested
from iogov.episodes import EpisodeDetector, watch
from iogov.gating import Backoff, Gate, RunGuard, kill_switch_reason
from iogov.journal import Journal, ReadStats, load_state, read_records, save_state, validate_record
from iogov.report import analyze, harm_summary, status
from iogov.runner import build, run_tick


# --- config ---------------------------------------------------------------------------------------------------


def test_config_rejects_unknown_keys_wrong_types_and_values_above_hard_ceilings():
    with pytest.raises(ConfigError, match="unknown"):
        from_mapping({"nonsense": 1})
    with pytest.raises(ConfigError, match="expected"):
        from_mapping({"read_iops_max": "fast"})
    with pytest.raises(ConfigError, match="hard ceiling"):
        from_mapping({"qd_high": 64})
    with pytest.raises(ConfigError, match="multiple of 4096"):
        from_mapping({"block_bytes": 5000})
    assert from_mapping({"region_default": 0}).region_default == 0


def test_config_loads_toml_and_the_shipped_example(tmp_path):
    from _testdeps import optional_module
    optional_module("tomllib", "python >= 3.11 is required to read the config file")
    here = os.path.dirname(os.path.dirname(__file__))
    cfg = load(os.path.join(here, "iogov.toml.example"))
    assert cfg.read_iops_max == 20.0 and cfg.region_default == 512 * 1024 * 1024
    with pytest.raises(ConfigError):
        load(str(tmp_path / "missing.toml"))


# --- kill switches, gating, backoff -----------------------------------------------------------------------------


def test_every_kill_switch_form_is_honoured(world, tmp_path):
    cfg = world.config()
    assert kill_switch_reason(cfg, {}) is None
    assert kill_switch_reason(world.config(enabled=False), {}).startswith("config")
    assert kill_switch_reason(cfg, {"IOGOV_DISABLE": "1"}).startswith("env")
    flag = world.root / "etc/disabled"
    flag.parent.mkdir()
    flag.write_text("")
    assert kill_switch_reason(cfg, {}).startswith("flag-file")
    flag.unlink()
    (world.state).mkdir()
    (world.state / "DISABLED").write_text("")
    assert kill_switch_reason(cfg, {}).startswith("flag-file")


def test_gate_tags_quiet_and_reports_reasons(world):
    env, _ = make_env()
    cfg = world.config()
    g = Gate(cfg, Collector(cfg, build(cfg, env).disks, env), env)
    ok = g.check("bench")
    assert ok.ok and ok.quiet and ok.ctx["inflight"] == 0
    world.set_pressure(2.0, 0.0)
    r = g.check("bench")
    assert not r.ok and r.reasons[0].startswith("io_pressure_high")
    assert g.check("tick").ok  # a tick tolerates 2%
    world.set_pressure(0.0, 0.5)
    assert any(x.startswith("io_pressure_full") for x in g.check("bench").reasons)
    world.set_pressure(0.0, 0.0)
    write_file(world.proc / "loadavg", "99.0 99.0 99.0 1/1 1\n")
    cfg2 = world.config(loadavg_per_cpu_max=1.5)
    assert any(x.startswith("load_high") for x in Gate(cfg2, Collector(cfg2, [], env), env).check("tick").reasons)


def test_governed_writer_blocks_the_bench_but_tags_ticks_contended(world):
    env, clock = make_env()
    cfg = world.config()
    col = Collector(cfg, build(cfg, env).disks, env)
    n = {"i": 0}

    def grow(_s):
        n["i"] += 1
        write_file(world.cg / "dev.slice/io.stat", f"8:0 rbytes=0 wbytes={n['i'] * 100 * 1024 * 1024} rios=0 wios={n['i']} dbytes=0 dios=0\n")

    clock.on_sleep = grow
    gate = Gate(cfg, col, env)
    assert any(x.startswith("governed_writing") for x in gate.check("bench").reasons)
    t = gate.check("tick")
    assert t.ok and t.quiet is False


def test_guard_stops_on_pressure_rise_and_on_the_stop_flag(world):
    env, _ = make_env()
    cfg = world.config(pressure_recheck_every_ops=1)
    guard = RunGuard(cfg, Collector(cfg, [], env), env, strict_pressure=True)
    guard.check()
    world.set_pressure(30.0, 0.0)
    with pytest.raises(StopRequested, match="io_pressure_rose"):
        guard.check()
    world.set_pressure(0.0, 0.0)
    env.stop.set("signal:SIGTERM")
    with pytest.raises(StopRequested, match="SIGTERM"):
        guard.check()


def test_backoff_doubles_to_a_cap_and_clears(world):
    env, clock = make_env()
    cfg = world.config()
    b = Backoff({}, cfg, env.wall, "tick")
    delays = [b.hit() for _ in range(6)]
    assert delays[:3] == [300, 600, 1200] and delays[-1] == cfg.backoff_max_s
    assert b.blocked() > 0
    b.clear()
    assert b.blocked() == 0


# --- journal --------------------------------------------------------------------------------------------------------


def test_journal_is_append_only_monthly_and_tolerates_damage(tmp_path):
    env, clock = make_env()
    j = Journal(str(tmp_path / "j"), env.wall, run_id="abc")
    j.append("note", text="one")
    clock.t += 40 * 86400
    j.append("note", text="two")
    files = sorted(os.listdir(tmp_path / "j"))
    assert len(files) == 2 and all(f.startswith("journal-") for f in files)
    first = tmp_path / "j" / files[0]
    size = first.stat().st_size
    j.append("note", text="three")  # a later month: the old file is never rewritten
    assert first.stat().st_size == size
    with open(first, "a") as fh:
        fh.write('{"v": 1, "ty')  # torn last line
        fh.write('\n{"v": 99, "type": "note", "t": 1, "ts": "x", "text": "future"}\n')
        fh.write('{"v": 1, "type": "nonsense", "t": 1, "ts": "x"}\n')
    stats = ReadStats()
    recs = list(read_records(str(tmp_path / "j"), stats))
    assert [r["text"] for r in recs] == ["one", "two", "three"]
    assert (stats.torn, stats.unknown_version, stats.invalid) == (1, 1, 1)


def test_journal_refuses_invalid_and_oversized_records(tmp_path):
    env, _ = make_env()
    j = Journal(str(tmp_path), env.wall)
    with pytest.raises(ValueError, match="missing"):
        j.append("probe", lane="r_cold")
    with pytest.raises(ValueError, match="unknown type"):
        j.append("mystery")
    with pytest.raises(ValueError, match="exceeds"):
        j.append("note", text="x" * 60000)
    assert validate_record({"v": 2}) != []


def test_schema_documents_every_record_type_with_its_required_fields():
    from iogov.journal import REQUIRED
    assert set(REQUIRED) == {"run_start", "run_end", "skip", "probe", "bench", "passive", "recommendation", "note", "harm"}


def test_state_file_is_replaced_atomically(tmp_path):
    p = str(tmp_path / "s" / "state.json")
    save_state(p, {"a": 1})
    save_state(p, {"a": 2})
    assert load_state(p) == {"a": 2} and os.listdir(tmp_path / "s") == ["state.json"]
    assert load_state(str(tmp_path / "none")) == {}


# --- devices ------------------------------------------------------------------------------------------------------


def make_sys(root):
    sysfs = root / "sys"
    (sysfs / "devices/virtual").mkdir(parents=True)
    for name in ("vda", "vdb"):
        write_file(sysfs / f"devices/{name}/size", "2048\n")
        (sysfs / "class/block").mkdir(parents=True, exist_ok=True)
        os.symlink(f"../../devices/{name}", sysfs / f"class/block/{name}")
    write_file(sysfs / "devices/vda/vda3/partition", "3\n")
    os.symlink("../../devices/vda/vda3", sysfs / "class/block/vda3")
    write_file(sysfs / "devices/dm-0/size", "1\n")
    os.symlink("../../devices/dm-0", sysfs / "class/block/dm-0")
    (sysfs / "devices/dm-0/slaves").mkdir()
    os.symlink("../../vda/vda3", sysfs / "devices/dm-0/slaves/vda3")
    os.symlink("../../vdb", sysfs / "devices/dm-0/slaves/vdb")
    return sysfs


def test_partitions_and_device_mapper_resolve_to_whole_disks(tmp_path):
    sysfs = make_sys(tmp_path)
    assert whole_disks_of(str(sysfs), "vda3") == {"vda"}
    assert whole_disks_of(str(sysfs), "dm-0") == {"vda", "vdb"}
    assert whole_disks_of(str(sysfs), "vdb") == {"vdb"}


def test_data_path_detection_goes_through_st_dev(tmp_path, world):
    sysfs = make_sys(tmp_path / "fake")
    st = os.stat(world.root)
    os.makedirs(sysfs / "dev/block", exist_ok=True)
    os.symlink("../../class/block/dm-0", sysfs / "dev/block" / f"{os.major(st.st_dev)}:{os.minor(st.st_dev)}")
    assert disks_for_path(str(sysfs), str(world.root)) == {"vda", "vdb"}
    cfg = from_mapping({"sysfs_root": str(sysfs), "dev_root": str(tmp_path / "dev"), "data_paths": [str(world.root)]})
    disks, notes = detect(cfg)
    assert [d.kname for d in disks] == ["vda", "vdb"] and notes == []


def test_detection_excludes_virtual_devices_and_says_when_nothing_is_configured(tmp_path):
    cfg = from_mapping({"sysfs_root": str(tmp_path), "dev_root": str(tmp_path), "devices": ["/dev/loop3", "/dev/zram0", "/dev/vda"]})
    disks, notes = detect(cfg)
    assert [d.kname for d in disks] == ["vda"] and sum("excluded" in n for n in notes) == 2
    assert "nothing to probe" in detect(from_mapping({}))[1][0]


def test_stable_node_prefers_by_id(tmp_path):
    dev = tmp_path / "dev"
    (dev / "disk/by-id").mkdir(parents=True)
    (dev / "vda").write_text("")
    os.symlink("../../vda", dev / "disk/by-id/virtio-ABC")
    os.symlink("../../vda", dev / "disk/by-id/virtio-ABC-part1")
    assert stable_node(str(dev), "vda").endswith("by-id/virtio-ABC")


# --- collectors -------------------------------------------------------------------------------------------------------


def test_parsers_and_missing_files_are_not_errors(world):
    p = parse_pressure("some avg10=1.50 avg60=0.10 avg300=0.00 total=99\nfull avg10=0.25 avg60=0 avg300=0 total=5\n")
    assert p["some"]["avg10"] == 1.5 and p["full"]["total"] == 5
    assert parse_pressure("") is None and parse_block_stat("1 2 x") is None
    assert parse_io_stat("8:0 rbytes=10 wbytes=20 rios=1 wios=2\n")["8:0"]["wbytes"] == 20
    env, _ = make_env()
    cfg = world.config(procfs_root=str(world.root / "nope"), governed_cgroups=[str(world.root / "nocg")])
    snap = Collector(cfg, [], env).snapshot()
    assert snap["pressure"] is None and snap["cgroups"]["governed"][str(world.root / "nocg")] is None


def test_cgroup_rates_and_who_else_writes(world):
    env, clock = make_env()
    write_file(world.cg / "system.slice/io.stat", "8:0 rbytes=0 wbytes=0 rios=0 wios=0\n")
    write_file(world.cg / "user.slice/io.stat", "8:0 rbytes=0 wbytes=0 rios=0 wios=0\n")
    write_file(world.cg / "io.stat", "8:0 rbytes=0 wbytes=0 rios=0 wios=0\n")
    cfg = world.config(others_top=1)
    col = Collector(cfg, build(cfg, env).disks, env)
    a = col.snapshot()
    clock.t += 10
    write_file(world.cg / "system.slice/io.stat", "8:0 rbytes=0 wbytes=50000000 rios=0 wios=5\n")
    write_file(world.cg / "user.slice/io.stat", "8:0 rbytes=0 wbytes=1000000 rios=0 wios=1\n")
    write_file(world.cg / "io.stat", "8:0 rbytes=0 wbytes=60000000 rios=0 wios=6\n")
    b = col.snapshot()
    s = passive_summary(a, b, cfg.others_top)
    assert list(s["others_writing"]) == ["system.slice"]  # top-1, and neither governed nor protected slices appear
    assert s["others_writing"]["system.slice"]["w_bps"] == pytest.approx(5e6, rel=0.01)
    assert s["root_total_w_bps"] == pytest.approx(6e6, rel=0.01)
    assert cgroup_rates(None, b["cgroups"]["others"]["system.slice"], 1.0) is None


def test_settings_epoch_changes_with_queue_params_and_not_otherwise(world):
    env, _ = make_env()
    cfg = world.config()
    col = Collector(cfg, build(cfg, env).disks, env)
    e1 = settings_epoch(col.settings())
    assert e1 == settings_epoch(col.settings())
    write_file(world.sys / "class/block/vda/queue/nr_requests", "8\n")
    assert settings_epoch(col.settings()) != e1
    s = col.settings()
    assert s["queue"]["vda"]["scheduler"] == "mq-deadline" and "io.cost.model" in s["cgroup"]


# --- covariates and the prevention checker ---------------------------------------------------------------------------


def covs(world, **over):
    env, _ = make_env()
    cfg = world.config(data_paths=[str(world.root)], fstrim_stamp_file=str(world.root / "none"), fstrim_log_file=str(world.root / "none2"),
                       lvm_conf=str(world.root / "none3"), provider_signal_file=str(world.root / "none4"), **over)
    return cfg, env, cov.collect(cfg, build(cfg, env).disks, env.wall)


def test_covariates_record_fill_discard_provider_queue_and_prio(world):
    now = time.time()
    write_file(world.root / "fstrim.stamp", "")
    os.utime(world.root / "fstrim.stamp", (now - 3 * 86400, now - 3 * 86400))
    write_file(world.root / "fstrim.log", "/: 12.3 GiB (13200000000 bytes) trimmed on /dev/mapper/x\n")
    write_file(world.root / "lvm.conf", "devices {\n}\nissue_discards = 1\n")
    write_file(world.root / "prov.json", json.dumps({"used_bytes": 95, "total_bytes": 100, "snapshot_created": now - 3600 * 30}))
    write_file(world.proc / "mounts", f"/dev/vda1 / ext4 rw,relatime,discard 0 0\n")
    write_file(world.sys / "class/block/vda/queue/discard_max_bytes", "2147483648\n")
    write_file(world.cg / "dev.slice/io.prio.class", "no-change\n")
    cfg = world.config(data_paths=[str(world.root)], fstrim_stamp_file=str(world.root / "fstrim.stamp"),
                       fstrim_log_file=str(world.root / "fstrim.log"), lvm_conf=str(world.root / "lvm.conf"),
                       provider_signal_file=str(world.root / "prov.json"))
    env, _ = make_env()
    c = cov.collect(cfg, build(cfg, env).disks, time.time)
    assert c["fill"][str(world.root)]["used_pct"] is not None
    assert c["fstrim"]["trimmed_bytes"] == 13200000000 and c["fstrim"]["last_run"] is not None
    assert c["lvm_issue_discards"] is True and "discard" in c["mounts"][str(world.root)]["options"]
    assert c["discard_max_bytes"]["vda"] == 2147483648 and c["provider"]["used_bytes"] == 95
    assert c["io_prio"]["cgroup_io.prio.class"] is True


def test_prevention_checker_warns_on_fill_old_fstrim_snapshot_and_provider_usage(world):
    cfg, env, c = covs(world)
    wall = time.time
    c["fill"] = {"/data": {"used_pct": 72.0}}
    c["fstrim"] = {"last_run": time.time() - 20 * 86400}
    c["provider"] = {"snapshot_created": time.time() - 50 * 3600, "used_bytes": 99, "total_bytes": 100}
    c["lvm_issue_discards"] = False
    c["discard_max_bytes"] = {"vda": 0}
    codes = {f["code"] for f in cov.prevention(cfg, c, wall)}
    assert {"fill", "fstrim_old", "snapshot_age", "provider_used", "lvm_issue_discards", "no_discard_support"} <= codes


def test_prevention_checker_is_quiet_when_healthy_and_says_unknown_when_blind(world):
    cfg, env, c = covs(world)
    codes = {f["code"] for f in cov.prevention(cfg, c, time.time)}
    assert {"fstrim_unknown", "provider_unknown"} <= codes and "fill" not in codes
    c["fill"] = {"/d": {"used_pct": 40.0}}
    c["fstrim"] = {"last_run": time.time() - 86400}
    c["provider"] = {"used_bytes": 10, "total_bytes": 100}
    assert cov.prevention(cfg, c, time.time) == [] or {f["level"] for f in cov.prevention(cfg, c, time.time)} == {"info"}


def test_run_start_carries_covariates_and_status_prints_prevention(world, capsys):
    env, _ = make_env()
    cfg = world.config(data_paths=[str(world.root)])
    run_tick(build(cfg, env))
    start = [r for r in read_records(str(world.state / "journal")) if r["type"] == "run_start"][0]
    assert {"fill", "fstrim", "provider", "io_prio", "mounts"} <= set(start["covariates"])
    out = status(cfg, env.wall)
    assert "prevention checks (report only)" in out and "fstrim_unknown" in out


def test_annotate_and_action_notes_are_journalled(world, capsys):
    cfg_path = world.root / "c.toml"
    cfg_path.write_text(f'state_dir = "{world.state}"\nkill_switch_file = "{world.root}/etc/disabled"\n')
    assert main(["--config", str(cfg_path), "annotate", "provider optimise run"]) == 0
    assert main(["--config", str(cfg_path), "annotate", "--action", "nr_requests 16 -> 8"]) == 0
    assert main(["--config", str(cfg_path), "annotate", "--baseline-reset", "after optimise"]) == 0
    notes = [r for r in read_records(str(world.state / "journal")) if r["type"] == "note"]
    assert [n["kind"] for n in notes] == ["annotation", "action", "baseline_reset"]


# --- distress episodes (stopping distance) -----------------------------------------------------------------------------


def feed(det, spec):
    out = []
    for t, some in spec:
        ep = det.feed(float(t), some, 0.0, 0, 0.0)
        if ep:
            out.append(ep)
    return out


def test_episode_records_the_window_around_a_distress_spike_and_its_recovery(world):
    cfg = world.config(episode_pre_s=5, episode_quiet_s=4.0, watch_interval_s=1.0)
    det = EpisodeDetector(cfg)
    spec = [(t, 0.0) for t in range(10)] + [(10 + i, 30.0) for i in range(6)] + [(16 + i, 0.0) for i in range(8)]
    eps = feed(det, spec)
    assert len(eps) == 1
    e = eps[0]
    assert e["duration_s"] == 5.0 and e["recovery_s"] == 4.0 and e["peak_some_pct"] == 30.0 and not e["truncated"]
    rel = [row[0] for row in e["series"]]
    assert rel[0] == -5.0 and max(rel) == 9.0  # 5 s before the trigger and the quiet tail after it


def test_episode_triggers_on_dstate_and_full_and_splits_long_ones(world):
    cfg = world.config(episode_max_s=20.0, episode_quiet_s=3.0)
    det = EpisodeDetector(cfg)
    assert det.feed(0.0, 0, 0, 3, 0) is None  # D-state threads alone are distress
    out = [det.feed(float(t), 0, 0, 3, 0) for t in range(1, 40)]
    assert [o for o in out if o and o["truncated"]]
    assert det.is_distress(0, 2.0, 0) and not det.is_distress(1.0, 0.1, 1)


def test_episode_series_is_downsampled_but_keeps_the_peak(world):
    cfg = world.config(episode_max_points=20, episode_quiet_s=2.0, episode_max_s=10_000.0)
    det = EpisodeDetector(cfg)
    spec = [(t, 10.0 if t != 137 else 90.0) for t in range(300)] + [(300 + i, 0.0) for i in range(4)]
    e = feed(det, spec)[0]
    assert len(e["series"]) <= 25 and any(row[1] == 90.0 for row in e["series"])


def test_watch_journals_an_episode_from_real_pressure_files_and_does_no_device_io(world, monkeypatch):
    env, clock = make_env()
    cfg = world.config(episode_quiet_s=2.0, watch_interval_s=1.0, episode_pre_s=2)
    total = {"v": 0}

    def tick(_s):
        n = clock.slept.__len__()
        if 3 <= n <= 6:
            total["v"] += 600_000  # 60% of each second stalled
        write_file(world.cg / "game.slice/io.pressure",
                   f"some avg10=0 avg60=0 avg300=0 total={total['v']}\nfull avg10=0 avg60=0 avg300=0 total=0\n")

    clock.on_sleep = tick
    clock.step = 1e-6
    opened = []
    real = os.open
    monkeypatch.setattr(os, "open", lambda p, f, *a, **k: (opened.append(str(p)), real(p, f, *a, **k))[1])
    n = watch(build(cfg, env), max_seconds=20)
    assert n == 1
    rec = [r for r in read_records(str(world.state / "journal")) if r["type"] == "harm"][0]
    assert rec["kind"] == "episode" and rec["evidence"]["peak_some_pct"] > 50 and "settings_epoch" in rec["evidence"]
    assert not [p for p in opened if p.startswith(str(world.dev))]


def test_watch_stops_on_the_kill_switch(world):
    env, _ = make_env()
    cfg = world.config()
    (world.root / "etc").mkdir()
    (world.root / "etc/disabled").write_text("")
    assert watch(build(cfg, env), max_seconds=100) == 0


def test_slow_probe_reads_are_journalled_as_harm_observations_not_capacity(world):
    env, clock = make_env()
    cfg = world.config(canary_latency_ms=10.0, abort_latency_ms=10_000.0)
    clock.step = 0.05
    run_tick(build(cfg, env))
    recs = list(read_records(str(world.state / "journal")))
    harm = [r for r in recs if r["type"] == "harm"]
    assert harm and harm[0]["kind"] == "probe_latency" and not [r for r in recs if r["type"] == "recommendation"]


def test_analyze_keeps_harm_and_capability_sections_apart_and_shows_stopping_distance(world):
    env, clock = make_env()
    cfg = world.config()
    j = Journal(cfg.journal_dir(), env.wall)
    ev = {"start": 1.0, "end": 40.0, "duration_s": 30.0, "recovery_s": 9.0, "peak_some_pct": 41.0, "peak_full_pct": 3.0,
          "peak_dstate": 4, "series": [[0, 41.0, 3.0, 4, 5.0]], "series_columns": [], "truncated": False}
    j.append("harm", kind="episode", evidence=ev)
    clock.t += 100
    j.append("note", text="nr_requests 16 -> 8", kind="action")
    clock.t += 100
    j.append("harm", kind="episode", evidence=dict(ev, duration_s=2.0, recovery_s=1.0))
    text = analyze(cfg, cfg.journal_dir())
    assert "protected workload harmed" in text and "around annotation" in text and "max 30.0 s" in text
    assert "no qualifying windows" in text or "capability" in text
    h = harm_summary(list(read_records(cfg.journal_dir())))
    assert h["episodes"] == 2 and h["around_annotations"][0]["max_duration_after_s"] == 2.0
    assert json.loads(analyze(cfg, cfg.journal_dir(), as_json=True))["protected_harm"]["episodes"] == 2


def test_quiet_experiment_prints_a_plan_and_executes_nothing(world, capsys, monkeypatch):
    called = []
    monkeypatch.setattr(os, "system", lambda *a: called.append(a))
    assert main(["quiet-experiment", "--knob", "nr_requests", "--value", "8"]) == 0
    out = capsys.readouterr().out
    assert "printed only" in out and "Preconditions" in out and "revert" in out and "nr_requests" in out
    assert main(["quiet-experiment", "--knob", "bogus", "--value", "1"]) == 2
    assert called == []
