"""The sampler client, the adapter to frames, the source and the source choice.

Everything runs against ``sampler_fake.FakeSampler`` (the wire protocol over a Unix socket).
Timeouts are the client's own short ones; nothing asserts how long anything took.
"""
from __future__ import annotations

from dataclasses import replace

import pytest
from sampler_fake import DOCKER_ID, T0_MS, FakeSampler, build_current, un, v

from topos.config import SamplerConfig, ToposConfig, load
from topos.sampler_adapter import SamplerOverlay, cgroup_metrics, convert_current, describe_event
from topos.sampler_client import (
    SamplerClient,
    SamplerProtocolError,
    SamplerRemoteError,
    SamplerTimeout,
    SamplerUnavailable,
)
from topos.sampler_source import (
    SamplerSource,
    SourceUnavailable,
    choose_source,
    local_namespace_note,
)
from topos.ui.columns import CellCtx, cn
from topos.ui.columns import REGISTRY as COLUMNS
from topos.ui.rows import build_rows


@pytest.fixture
def fake(tmp_path):
    server = FakeSampler(tmp_path / "s.sock").start()
    yield server
    server.stop()


def _config(path, **kw) -> ToposConfig:
    cfg = ToposConfig(sampler=SamplerConfig(socket_path=path, timeout_s=kw.get("timeout_s", 0.5)))
    return replace(cfg, cgroup_root=kw["cgroup_root"]) if "cgroup_root" in kw else cfg


# ── client ────────────────────────────────────────────────────────────


def test_every_request_roundtrips_with_its_id_and_remembers_the_epoch(fake) -> None:
    client = SamplerClient(fake.path, timeout_s=1.0)
    hello = client.hello()
    assert hello["protocol"] == 1 and hello["limits"]["max_response_bytes"] == client.max_response_bytes
    assert client.current(prefix="cg:/system.slice", procs=False)["tick"] == 1
    assert client.series("cg:/", "psi.cpu.some.avg10", window_s=60)["points"]
    assert client.events(window_s=300)["events"] == []
    assert client.status()["timing"]["tick_overruns"] == 2
    assert client.epoch == "e1"
    sent = [r for r in fake.requests]
    assert [r["req"] for r in sent] == ["hello", "current", "series", "events", "status"]
    assert sent[1]["prefix"] == "cg:/system.slice" and sent[1]["procs"] is False
    assert len({r["id"] for r in sent}) == 5  # ids are distinct and echoed (a mismatch would have raised)
    client.close()


def test_epoch_change_is_detected_and_reported_once(fake) -> None:
    client = SamplerClient(fake.path, timeout_s=1.0)
    client.hello()
    assert client.take_epoch_changes() == []
    fake.epoch = "e2"
    client.current()
    changes = client.take_epoch_changes()
    assert [(c.previous, c.current) for c in changes] == [("e1", "e2")]
    assert client.take_epoch_changes() == []
    client.close()


def test_a_remote_error_is_typed_and_the_connection_stays_usable(fake) -> None:
    client = SamplerClient(fake.path, timeout_s=1.0)
    fake.remote_error["series"] = "unknown_metric"
    with pytest.raises(SamplerRemoteError) as err:
        client.series("cg:/", "nope")
    assert err.value.code == "unknown_metric"
    assert client.status()["ok"] is True
    client.close()


def test_a_slow_server_times_out_instead_of_blocking(fake) -> None:
    fake.delay_s = 2.0
    client = SamplerClient(fake.path, timeout_s=0.15)
    with pytest.raises(SamplerTimeout):
        client.hello()
    client.close()


def test_missing_socket_and_a_server_that_hangs_up_are_unavailable(tmp_path, fake) -> None:
    with pytest.raises(SamplerUnavailable):
        SamplerClient(tmp_path / "nothing.sock", timeout_s=0.2).hello()
    fake.drop = True
    with pytest.raises(SamplerUnavailable):
        SamplerClient(fake.path, timeout_s=0.5).hello()


def test_reads_are_bounded_by_bytes(fake) -> None:
    fake.raw_reply = b"x" * 70_000  # never a newline
    client = SamplerClient(fake.path, timeout_s=1.0, max_response_bytes=8)
    with pytest.raises(SamplerProtocolError, match="bound"):
        client.hello()


@pytest.mark.parametrize(
    "reply",
    [b"not json\n", b"[1,2]\n", b'{"ok":true,"protocol":2,"id":1}\n', b'{"ok":true,"protocol":1,"id":999}\n'],
)
def test_malformed_answers_are_protocol_errors(fake, reply) -> None:
    fake.raw_reply = reply
    with pytest.raises(SamplerProtocolError):
        SamplerClient(fake.path, timeout_s=1.0).hello()


def test_a_connection_the_server_closed_while_idle_is_replaced_once(tmp_path) -> None:
    path = tmp_path / "s.sock"
    first = FakeSampler(path).start()
    client = SamplerClient(path, timeout_s=1.0)
    client.hello()
    first.stop()
    path.unlink()
    second = FakeSampler(path).start()
    try:
        assert client.current()["tick"] == 1
    finally:
        client.close()
        second.stop()


# ── adapter: states are never numbers ─────────────────────────────────


def test_not_applicable_and_unavailable_never_become_numbers() -> None:
    cur = build_current()
    m = cgroup_metrics(cur["entities"]["cg:/system.slice/db.service"])
    assert m["io_r_bps"].v is None and m["io_r_bps"].src == "unavail_kernel"  # first_sample
    assert m["io_cost_usage_per_s"].v is None  # not_applicable (io.cost off)
    assert m["majflt_per_s"].v == 0  # a real zero stays a number
    sys_m = cgroup_metrics(cur["entities"]["cg:/system.slice"])
    assert sys_m["mem_max"].src == "unlimited" and sys_m["mem_max"].v is None  # -1 is "max", not a number
    assert sys_m["swap_max"].v == 0 and sys_m["swap_max"].src == "exact"  # swap.max 0 is a real limit
    assert sys_m["cpu_pct"].v == pytest.approx(200.0)
    assert m["rf_d_per_s"].v == 20  # refault_anon 30 minus zswap-served 10
    perm = cgroup_metrics({"mem.current": un("permission_denied")})
    assert perm["ram"].src == "unavail_perm" and perm["ram"].v is None


def test_cells_tell_unset_na_and_zero_apart_for_the_new_columns() -> None:
    frame = convert_current(build_current())
    sys_ef, db_ef = frame.entities["system.slice"], frame.entities["system.slice/db.service"]
    assert COLUMNS["swap_max"].cell(CellCtx(sys_ef))[0] == "0"  # swap off for the cgroup
    assert COLUMNS["swap_max"].cell(CellCtx(db_ef))[0] == "·"  # max: not set
    assert COLUMNS["iocost"].cell(CellCtx(sys_ef))[0] == "15%"  # 150000 usec/s of a device-second
    assert COLUMNS["iocost"].cell(CellCtx(db_ef))[0] == "n/a"  # io.cost not applicable here
    assert COLUMNS["maj"].cell(CellCtx(sys_ef))[0] == "120"
    assert COLUMNS["maj"].cell(CellCtx(db_ef))[0] == "0"
    assert COLUMNS["io_r"].cell(CellCtx(db_ef))[0] == "n/a"
    assert COLUMNS["origin"].cell(CellCtx(db_ef))[0] == "n/a"  # no governance source: unknown, not "unset"
    assert COLUMNS["blk"].cell(CellCtx(db_ef, blk=None))[0] == "n/a"
    assert cn(db_ef, "pids_max") == 4096.0
    assert cn(frame.entities[""], "pids_max") == "na"  # the root has no pids.max: n/a


def test_the_frame_is_a_tree_keyed_like_the_collector() -> None:
    frame = convert_current(build_current())
    assert set(frame.entities) == {"", "system.slice", "system.slice/db.service", "docker.slice", f"docker.slice/docker-{DOCKER_ID}.scope"}
    assert frame.entities[""].entity.parent is None and frame.entities[""].entity.kind == "root"
    assert frame.entities["system.slice/db.service"].entity.parent == "system.slice"
    assert frame.entities["system.slice/db.service"].entity.kind == "service"
    assert frame.ts == (T0_MS + 1000) / 1000.0 and frame.interval_s == 1.0
    assert frame.host["host_mem_total"].v == 16e9
    assert frame.host["host_zswap_ratio"].v == pytest.approx(3.0)
    assert "host_uptime_s" not in frame.host  # the protocol has no uptime: absent, so n/a on screen


def test_host_extras_keep_states_apart() -> None:
    x = convert_current(build_current()).host_meta["sampler"]
    assert x["cpus"][2] is None and x["cpus"][0] == pytest.approx([0.5, 0.1, 0.05, 0.0])  # first_sample CPU: no bar
    assert x["zswap"] == {"in": 12.0, "out": 7.0, "wb": 3.0, "rej": None}  # zswpwb is a vm counter; rejects need debugfs: n/a, not 0
    assert x["swap_io"] == {"in": 0.0, "out": 25 * 4096.0}
    assert x["ksm"] == {"state": "off"} and x["iocost"] == {"state": "off"}
    assert x["memstack"]["free"] == 7e9
    assert x["sampler"]["overruns"] == 2 and x["sampler"]["skipped"] == 1 and x["sampler"]["cpu_pct"] == pytest.approx(1.0)
    assert x["scanned"] is True and x["d_state"]["system.slice/db.service"] == 2
    frame = convert_current(build_current(), status={"self": {"cpu_pct_recent_60s": v(1.25)}, "throttle": {"nr_throttled": v(4)}})
    assert frame.host_meta["sampler"]["sampler"]["cpu_pct"] == 1.25 and frame.host_meta["sampler"]["sampler"]["throttled"] == 4


def test_block_devices_drop_partitions_and_tell_idle_from_unknown() -> None:
    devs = {d["name"]: d for d in convert_current(build_current()).host_meta["block_devices"]}
    assert "vda1" not in devs  # the whole disk reports it
    assert devs["vda"]["await_ms"] == 4.5 and devs["vda"]["read_bps"] == 2000 * 512.0
    assert devs["vdb"]["await_ms"] == "idle"  # no_activity_in_interval: nothing to wait for


def test_docker_names_come_from_the_enrich_hook_and_a_failing_hook_costs_only_names() -> None:
    from topos.model import DockerMeta

    def enrich(entities):
        key = f"docker.slice/docker-{DOCKER_ID}.scope"
        entities[key].docker = DockerMeta(DOCKER_ID[:12], DOCKER_ID, "web", "img")
        return entities

    assert convert_current(build_current(), enrich=enrich).entities[f"docker.slice/docker-{DOCKER_ID}.scope"].entity.docker.name == "web"

    def boom(_entities):
        raise RuntimeError("docker is down")

    assert len(convert_current(build_current(), enrich=boom).entities) == 5


# ── overlay: processes, BLK, hard events, notes ───────────────────────


def test_process_rows_keep_unknown_cpu_unknown_and_blk_counts_every_blocked_task() -> None:
    frame = convert_current(build_current())
    overlay = SamplerOverlay("/x.sock")
    procs = overlay.processes(frame)
    first = {p.pid: p for p in procs["system.slice"]}[103]
    assert first.cpu is None and first.io_bps is None and not first.hot
    assert overlay.blocked(frame)["system.slice/db.service"] == 2
    assert overlay.counts(frame) == (4, 3)
    assert overlay.per_cpu(frame)[2] is None


def test_only_hot_or_blocked_processes_float_under_their_cgroup_blocked_first() -> None:
    frame = convert_current(build_current())
    overlay = SamplerOverlay()
    rows = build_rows(frame, procs=overlay.processes(frame), expanded={"", "system.slice", "system.slice/db.service"}).rows
    procs = [r.proc.comm for r in rows if r.kind == "proc"]
    assert procs == ["dbwriter", "burner"]  # sleeper (0.1%) and firstseen (unknown) do not float


def test_process_rows_are_capped_per_cgroup() -> None:
    cur = build_current()
    cur["procs"] = [
        {"pid": 500 + i, "start": i, "comm": f"hot{i}", "state": "R", "why": "cpu", "cgroup": "cg:/system.slice",
         "cpu_pct": v(50.0 + i), "io_read_bps": v(0), "io_write_bps": v(0)}
        for i in range(9)
    ]
    frame = convert_current(cur)
    rows = build_rows(frame, procs=SamplerOverlay().processes(frame), expanded={"", "system.slice"}).rows
    shown = [r.proc.comm for r in rows if r.kind == "proc"]
    assert shown == ["hot8", "hot7", "hot6", "hot5", "hot4"]


def test_hard_events_feed_the_why_line_only_while_recent() -> None:
    oom = {"ts_ms": T0_MS + 990_000, "type": "oom_kill", "entity": "cg:/system.slice/db.service",
           "detail": {"delta": 1, "total": 3}}
    old = {"ts_ms": T0_MS - 600_000, "type": "oom_kill", "entity": "cg:/x", "detail": {}}
    down = {"ts_ms": T0_MS + 999_000, "type": "psi_cross", "entity": "cg:/",
            "detail": {"resource": "io", "avg10": 1.0, "threshold": 25, "direction": "down"}}
    cur = build_current(tick=1000)
    overlay = SamplerOverlay()
    why = overlay.why(convert_current(cur, events=[old, oom, down]))
    assert why.startswith("OOM kill in /system.slice/db.service (+1, total 3)") and "10s ago" in why
    assert overlay.why(convert_current(cur, events=[old, down])) is None  # stale, and PSI coming down is no alarm
    assert overlay.why(convert_current(cur, events=None)) is None
    up = {**down, "detail": {**down["detail"], "direction": "up"}}
    assert "io pressure crossed 25%" in describe_event(up, 1.0)
    assert "vanished" in describe_event({"ts_ms": 0, "type": "cgroup_vanished", "entity": "cg:/a"}, 0.0)
    assert "memory.high" in describe_event({"ts_ms": 0, "type": "limit_changed", "entity": "cg:/a", "detail": {"file": "memory.high", "old": "1", "new": "2"}}, 0.0)


def test_notes_say_namespaced_truncated_and_restarted_plainly() -> None:
    overlay = SamplerOverlay("/run/topos/sampler.sock")
    hello = {"cgroup_view": {"mode": "namespaced", "because": "self_at_cgroup_root"}}
    frame = convert_current(build_current(truncated=True), hello=hello, epoch_changed_ts=T0_MS / 1000 + 1.0,
                            socket_path="/run/topos/sampler.sock")
    notes = " | ".join(overlay.notes(frame))
    assert "NAMESPACED cgroup view (self_at_cgroup_root)" in notes and "/run/topos/sampler.sock" in notes
    assert "TRUNCATED" in notes and "sampler restarted" in notes
    host = convert_current(build_current(), hello={"cgroup_view": {"mode": "host"}})
    assert overlay.notes(host) == []


# ── source ────────────────────────────────────────────────────────────


def _take(source, n):
    it = iter(source)
    try:
        return [next(it) for _ in range(n)]
    finally:
        it.close()


def test_the_source_backfills_sparklines_before_the_first_frame_and_skips_repeated_ticks(fake) -> None:
    source = SamplerSource(SamplerClient(fake.path, timeout_s=1.0), poll_s=0.01)
    it = iter(source)
    first = next(it)
    assert [round(v) for _ts, v in source.backfill["host_psi_cpu_some_avg10"]] == [1, 2, 3, 4, 5]
    assert first.ts == (T0_MS + 1000) / 1000.0
    fake.advance = True
    second = next(it)  # tick 1 again is skipped, the next real tick comes through
    assert second.ts > first.ts
    it.close()


def test_epoch_change_reloads_history_and_marks_the_frame(fake) -> None:
    source = SamplerSource(SamplerClient(fake.path, timeout_s=1.0), poll_s=0.01)
    it = iter(source)
    first = next(it)
    assert first.host_meta["sampler"]["epoch_changed_ts"] is None
    fake.epoch, fake.tick = "e2", 1
    fake.series_points = [[T0_MS + 500, 9.0]]
    after = next(it)
    assert after.host_meta["sampler"]["epoch_changed_ts"] == after.ts
    assert source.backfill["host_psi_cpu_some_avg10"] == [((T0_MS + 500) / 1000.0, 9.0)]
    assert "sampler restarted" in SamplerOverlay().notes(after)[0]
    it.close()


def test_a_sampler_that_stops_ticking_is_an_error_not_a_frozen_screen(fake) -> None:
    ticks = iter(range(0, 10_000, 3))
    source = SamplerSource(SamplerClient(fake.path, timeout_s=1.0), poll_s=0.01, clock=lambda: float(next(ticks)))
    it = iter(source)
    next(it)
    with pytest.raises(SamplerTimeout, match="no new tick"):
        next(it)


def test_a_dead_sampler_surfaces_as_an_error_from_the_iterator(tmp_path) -> None:
    source = SamplerSource(SamplerClient(tmp_path / "gone.sock", timeout_s=0.2))
    with pytest.raises(SamplerUnavailable):
        next(iter(source))
    assert source.retry_delay(0) < source.retry_delay(5) <= 10.0


def test_status_and_events_failures_do_not_break_the_view(fake) -> None:
    fake.remote_error.update(status="store_disabled", events="store_disabled", series="store_disabled")
    frame = _take(SamplerSource(SamplerClient(fake.path, timeout_s=1.0), poll_s=0.01), 1)[0]
    assert frame.host_meta["sampler"]["events"] == []
    assert frame.host_meta["sampler"]["sampler"]["cpu_pct"] == pytest.approx(1.0)  # from cpu_ticks, not status


# ── choosing the source ───────────────────────────────────────────────


def test_auto_uses_the_sampler_when_it_answers_hello(fake) -> None:
    choice = choose_source("auto", _config(fake.path))
    assert choice.kind == "sampler" and choice.label == "SAMPLER" and choice.note == ""
    assert choice.source is not None and choice.overlay is choice.source.overlay


def test_auto_falls_back_to_local_and_says_why(tmp_path) -> None:
    gone = tmp_path / "gone.sock"
    choice = choose_source("auto", _config(gone), proc_root=tmp_path / "noproc")
    assert choice.kind == "local" and choice.label == "LOCAL" and choice.source is None
    assert "sampler unavailable" in choice.note and "gone.sock" in choice.note and "showing local collection" in choice.note


def test_explicit_sampler_fails_closed(tmp_path) -> None:
    with pytest.raises(SourceUnavailable, match="not available"):
        choose_source("sampler", _config(tmp_path / "gone.sock"))
    with pytest.raises(SourceUnavailable, match="--cgroup-root"):
        choose_source("sampler", _config(tmp_path / "gone.sock"), local_forced="--cgroup-root")


def test_explicit_local_never_probes_and_a_forced_local_skips_the_sampler(fake) -> None:
    assert choose_source("local", _config(fake.path)).kind == "local"
    forced = choose_source("auto", _config(fake.path), local_forced="--slice")
    assert forced.kind == "local" and "--slice" in forced.note
    assert fake.requests == []


def test_a_silent_sampler_is_treated_as_unavailable_in_auto(fake) -> None:
    fake.delay_s = 2.0
    choice = choose_source("auto", _config(fake.path, timeout_s=0.15), probe_timeout_s=0.15)
    assert choice.kind == "local" and "sampler unavailable" in choice.note


def test_the_local_collector_says_when_it_only_sees_its_own_namespace(tmp_path) -> None:
    proc = tmp_path / "proc"
    (proc / "self").mkdir(parents=True)
    root = tmp_path / "cg"
    root.mkdir()
    (proc / "self" / "cgroup").write_text("0::/\n")
    note = local_namespace_note(root, proc, "/run/topos/sampler.sock")
    assert note is not None and "own cgroup namespace" in note and "/run/topos/sampler.sock" in note
    (root / "system.slice").mkdir()  # a real host root has it
    assert local_namespace_note(root, proc) is None
    (root / "system.slice").rmdir()
    (proc / "self" / "cgroup").write_text("0::/user.slice/x\n")
    assert local_namespace_note(root, proc) is None
    assert local_namespace_note(root, tmp_path / "missing") is None
    choice = choose_source("local", _config(tmp_path / "x.sock", cgroup_root=root), proc_root=proc)
    assert choice.note == ""  # not namespaced any more


def test_config_socket_path_has_the_documented_default_and_can_be_set(tmp_path) -> None:
    assert str(ToposConfig().sampler.socket_path) == "/run/topos/sampler.sock"
    path = tmp_path / "c.toml"
    path.write_text('[sampler]\nsocket_path = "/tmp/x.sock"\ntimeout_s = 0.5\n')
    cfg = load(path)
    assert str(cfg.sampler.socket_path) == "/tmp/x.sock" and cfg.sampler.timeout_s == 0.5


# ── command line ──────────────────────────────────────────────────────


def test_profile_flag_is_rejected_with_a_clear_message(capsys) -> None:
    from topos.cli import main

    assert main(["--profile", "balanced"]) == 2
    err = capsys.readouterr().err
    assert "--profile was removed" in err and "columns_file" in err


def test_explicit_sampler_source_fails_closed_on_the_command_line(tmp_path, capsys) -> None:
    from topos.cli import main

    cfg = tmp_path / "c.toml"
    cfg.write_text(f'[sampler]\nsocket_path = "{tmp_path}/none.sock"\n')
    assert main(["--config", str(cfg), "--source", "sampler"]) == 2
    assert "not available" in capsys.readouterr().err
    assert main(["--config", str(cfg), "--source", "sampler", "--once", "--json"]) == 2
    assert "interactive live view only" in capsys.readouterr().err
    assert main(["--config", str(cfg), "--source", "sampler", "--cgroup-root", str(tmp_path)]) == 2
    assert "--cgroup-root" in capsys.readouterr().err
