"""Regression tests for the actions/squeeze safety fix round (C-01..C-18).

Everything uses fakes and tmp_path; nothing touches cgroupfs, /sys or docker.
"""

from __future__ import annotations

import json
import signal
import subprocess
from pathlib import Path

import pytest

from topos.actions import owner_safety, squeeze
from topos.actions.execute import (
    AuditIdentity,
    ExecuteResult,
    _OwnerSafetyGate,
    execute_kill,
    execute_plan,
    execute_set_property,
)
from topos.actions.squeeze import (
    SqueezeConfig,
    check_confinement,
    recover_squeeze,
    recovery_path_for,
    run_squeeze,
)

FULL_ID = "a" * 64
IDENT = lambda: AuditIdentity(uid=0, user="root")  # noqa: E731


def _runner():
    calls: list[tuple[str, ...]] = []

    def runner(argv, *, timeout=30.0):
        calls.append(argv)
        return ExecuteResult("", "", argv, 0, "", "", "success", 0.0)

    return runner, calls


def _inspect(labels: dict[str, str] | None = None, name: str = "game"):
    payload = [{
        "Id": FULL_ID,
        "Name": "/" + name,
        "Config": {"Labels": dict(labels or {})},
    }]
    return lambda ref: payload


def _exec(kind: str, target: str, tmp_path: Path, **kw):
    runner, calls = _runner()
    result = execute_plan(
        kind,
        target,
        admin=True,
        confirm="EXECUTE",
        audit_path=tmp_path / "audit.jsonl",
        runner=runner,
        identity=IDENT,
        root_check=lambda: True,
        owner_protected_services=kw.pop("protected", ()),
        **kw,
    )
    return result, calls


# --------------------------------------------------------------------- C-01


def test_container_scope_owned_by_wings_is_refused_like_the_docker_verb(tmp_path):
    labels = {"Service": "Pterodactyl", "ContainerType": "server_process"}
    docker, _ = _exec("docker-stop", FULL_ID, tmp_path, owner_inspect=_inspect(labels))
    scope, calls = _exec(
        "systemd-stop", f"docker-{FULL_ID}.scope", tmp_path, owner_inspect=_inspect(labels)
    )
    assert docker.outcome == "refusal"
    assert scope.outcome == "refusal"
    assert "Pterodactyl" in scope.stderr
    assert calls == []


def test_container_scope_of_standalone_container_still_runs(tmp_path):
    result, calls = _exec(
        "systemd-stop", f"docker-{FULL_ID}.scope", tmp_path, owner_inspect=_inspect()
    )
    assert result.outcome == "success"
    assert calls[0][-1] == f"docker-{FULL_ID}.scope"


def test_container_scope_of_protected_container_is_refused(tmp_path):
    result, calls = _exec(
        "systemd-restart",
        f"docker-{FULL_ID}.scope",
        tmp_path,
        owner_inspect=_inspect(name="game"),
        protected=("game",),
    )
    assert result.outcome == "refusal"
    assert calls == []


def test_libpod_scope_fails_closed_when_docker_cannot_inspect_it(tmp_path):
    result, calls = _exec(
        "systemd-stop", f"libpod-{FULL_ID}.scope", tmp_path, owner_inspect=lambda ref: None
    )
    assert result.outcome == "refusal"
    assert calls == []


@pytest.mark.parametrize(
    "unit", ["docker.service", "system.slice/x.service", "key-form.service"]
)
def test_protected_services_apply_to_every_systemd_verb_in_name_and_key_forms(
    tmp_path, unit
):
    name = unit.split("/")[-1]
    protected = (unit,) if "/" in unit else (f"system.slice/{name}", name)
    for entry in protected:
        result, calls = _exec(
            "systemd-stop", name, tmp_path, protected=(entry,)
        )
        assert result.outcome == "refusal", entry
        assert calls == []


def test_unprotected_service_is_not_affected(tmp_path):
    result, calls = _exec("systemd-stop", "other.service", tmp_path, protected=("docker.service",))
    assert result.outcome == "success"


@pytest.mark.parametrize(
    "name",
    ["poweroff", "reboot", "halt", "kexec", "rescue", "emergency", "shutdown", "default"],
)
@pytest.mark.parametrize("kind", ["systemd-start", "systemd-stop", "systemd-restart"])
def test_power_and_rescue_targets_are_refused_unconditionally(tmp_path, kind, name):
    result, calls = _exec(kind, f"{name}.target", tmp_path)
    assert result.outcome == "refusal"
    assert "power/rescue" in result.stderr
    assert calls == []


def test_other_targets_are_not_denylisted(tmp_path):
    result, _ = _exec("systemd-restart", "multi-user.target", tmp_path)
    assert result.outcome == "success"


def test_systemd_kill_on_protected_scope_key_form_is_refused(tmp_path):
    runner, calls = _runner()
    scope = f"docker-{FULL_ID}.scope"
    result = execute_kill(
        "systemd-kill",
        scope,
        admin=True,
        confirm="KILL",
        audit_path=tmp_path / "audit.jsonl",
        runner=runner,
        identity=IDENT,
        root_check=lambda: True,
        protected_check=lambda kind, target: False,
        owner_protected_services=(f"system.slice/{scope}",),
        owner_inspect=_inspect(),
    )
    assert result.outcome == "refusal"
    assert calls == []


def test_systemd_kill_of_wings_scope_is_refused(tmp_path):
    runner, calls = _runner()
    result = execute_kill(
        "systemd-kill",
        f"docker-{FULL_ID}.scope",
        admin=True,
        confirm="KILL",
        audit_path=tmp_path / "audit.jsonl",
        runner=runner,
        identity=IDENT,
        root_check=lambda: True,
        protected_check=lambda kind, target: False,
        owner_protected_services=(),
        owner_inspect=_inspect({"Service": "Pterodactyl"}),
    )
    assert result.outcome == "refusal"
    assert calls == []


# --------------------------------------------------------------------- C-07


def _set_property(tmp_path, reader, **kw):
    runner, calls = _runner()
    result = execute_set_property(
        "demo.service",
        property_name="memory.high",
        property_value="1024",
        persistence="runtime",
        admin=True,
        confirm="EXECUTE",
        audit_path=tmp_path / "audit.jsonl",
        runner=runner,
        identity=IDENT,
        root_check=lambda: True,
        current_value_reader=reader,
        owner_protected_services=(),
        **kw,
    )
    return result, calls


def _records(tmp_path):
    return [json.loads(l) for l in (tmp_path / "audit.jsonl").read_text().splitlines()]


def test_expect_current_max_detects_change_away_from_max(tmp_path):
    result, calls = _set_property(tmp_path, lambda unit: "2048", expect_current="max")
    assert result.outcome == "stale"
    assert calls == []


def test_expect_current_max_matches_systemd_infinity_spelling(tmp_path):
    result, calls = _set_property(tmp_path, lambda unit: "infinity", expect_current="max")
    assert result.outcome == "success"
    assert _records(tmp_path)[-1]["fresh_current"] == "max"


def test_fresh_value_is_read_and_recorded_without_an_expectation(tmp_path):
    result, _ = _set_property(tmp_path, lambda unit: "4096")
    assert result.outcome == "success"
    assert _records(tmp_path)[-1]["fresh_current"] == "4096"


def test_unreadable_value_with_expectation_is_stale(tmp_path):
    result, calls = _set_property(tmp_path, lambda unit: None, expect_current="max")
    assert result.outcome == "stale"
    assert calls == []


# --------------------------------------------------------------------- C-09


def test_owner_inspect_uses_absolute_docker_scrubbed_env_type_and_timeout(
    monkeypatch, real_docker_inspect
):
    seen = {}

    def run(argv, **kwargs):
        seen["argv"] = argv
        seen.update(kwargs)
        return subprocess.CompletedProcess(argv, 0, stdout=b"[]", stderr=b"")

    monkeypatch.setenv("DOCKER_HOST", "tcp://elsewhere:2375")
    monkeypatch.setattr(subprocess, "run", run)
    assert owner_safety.default_owner_inspect("game") == []
    assert seen["argv"] == ["/usr/bin/docker", "inspect", "--type", "container", "--", "game"]
    assert "DOCKER_HOST" not in seen["env"]
    assert seen["timeout"] == 10.0


def test_owner_inspect_timeout_fails_closed(monkeypatch, tmp_path, real_docker_inspect):
    def run(argv, **kwargs):
        raise subprocess.TimeoutExpired(argv, 10.0)

    monkeypatch.setattr(subprocess, "run", run)
    assert owner_safety.default_owner_inspect("game") is None
    result, calls = _exec("docker-stop", "game", tmp_path, owner_inspect=owner_safety.default_owner_inspect)
    assert result.outcome == "refusal"
    assert calls == []


# --------------------------------------------------------------------- C-12


def test_bind_argv_replaces_only_the_final_element():
    gate = _OwnerSafetyGate("docker-stop", "stop", None, None)
    gate.full_id = FULL_ID
    assert gate.bind_argv(("/usr/bin/docker", "stop", "stop")) == (
        "/usr/bin/docker",
        "stop",
        FULL_ID,
    )
    gate = _OwnerSafetyGate("docker-kill", "TERM", None, None)
    gate.full_id = FULL_ID
    assert gate.bind_argv(("/usr/bin/docker", "kill", "--signal", "TERM", "TERM")) == (
        "/usr/bin/docker",
        "kill",
        "--signal",
        "TERM",
        FULL_ID,
    )


# --------------------------------------------------------------------- C-18


def test_audit_record_after_partial_write_starts_on_a_fresh_line(tmp_path):
    audit = tmp_path / "audit.jsonl"
    audit.write_text('{"partial": ')  # simulated ENOSPC leftover, no newline
    audit.chmod(0o600)
    result, _ = _exec("docker-stop", "game", tmp_path, owner_inspect=_inspect())
    assert result.outcome == "success"
    lines = audit.read_text().splitlines()
    assert lines[0] == '{"partial": '
    assert [json.loads(l)["stage"] for l in lines[1:]] == ["pre", "post"]


# --------------------------------------------------------------------- squeeze


def _cfg(tmp_path: Path, **kw) -> SqueezeConfig:
    values = dict(
        target="/cg/worker.scope",
        step=1,
        delay=0.0,
        floor=1,
        start=2,
        relax_to="max",
        psi_some_limit=10.0,
        psi_full_limit=5.0,
        rf_limit=200,
        force=False,
        log_path=tmp_path / "squeeze.jsonl",
        audit_path=tmp_path / "audit.jsonl",
        admin=True,
        confirm="SQUEEZE",
        recovery_path=tmp_path / "recovery" / "w.json",
    )
    values.update(kw)
    return SqueezeConfig(**values)


class _NullAudit:
    def record(self, **kwargs):
        pass


def _readers():
    return dict(
        cgroup_int_reader=lambda t, f: {"memory.current": 10, "memory.min": 0}.get(f, 0),
        cgroup_flat_kv_reader=lambda t, f: {},
        cgroup_pressure_reader=lambda t, f: {"some": {"avg10": 0.0}, "full": {"avg10": 0.0}},
        clock=lambda: 1.0,
        auditor=_NullAudit(),
        cgroup_text_reader=lambda t, f: "8589934592",
    )


def test_failed_restore_is_never_reported_as_restored(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(squeeze.time, "sleep", lambda s: None)
    writes: list[str] = []

    def writer(path, filename, value):
        writes.append(value)
        if value == "max":
            raise OSError("EINVAL")

    result = run_squeeze(_cfg(tmp_path), cgroup_writer=writer, **_readers())
    assert result.stop_reason == "restore_failed"
    assert result.restored_to == ""
    assert writes.count("max") == 2  # one retry
    err = capsys.readouterr().err
    assert "FAILED" in err and "still 1" in err
    # recovery record kept for --recover
    assert (tmp_path / "recovery" / "w.json").exists()


def test_cli_exits_nonzero_with_loud_message_on_restore_failure(tmp_path, monkeypatch, capsys):
    from topos.cli import _main_squeeze

    monkeypatch.setattr(squeeze.time, "sleep", lambda s: None)
    result = squeeze.SqueezeResult(
        "restore_failed", 1, 1, _cfg(tmp_path), (), "", "restore FAILED stuck at 1"
    )
    monkeypatch.setattr("topos.actions.squeeze.run_squeeze_gated", lambda c: result)
    assert _main_squeeze(["--target", "/cg/x"]) == 3
    assert "restore FAILED stuck at 1" in capsys.readouterr().err


def test_recovery_file_is_written_before_first_write_and_removed_on_clean_restore(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(squeeze.time, "sleep", lambda s: None)
    rec = tmp_path / "recovery" / "w.json"
    first_write_saw: list[dict] = []

    def writer(path, filename, value):
        if not first_write_saw:
            first_write_saw.append(json.loads(rec.read_text()))

    result = run_squeeze(_cfg(tmp_path), cgroup_writer=writer, **_readers())
    assert result.stop_reason == "floor"
    assert first_write_saw == [
        {"target": "/cg/worker.scope", "original": "8589934592", "relax_to": "max"}
    ]
    assert not rec.exists()


def test_existing_recovery_file_blocks_a_second_squeeze(tmp_path):
    rec = tmp_path / "recovery" / "w.json"
    rec.parent.mkdir()
    rec.write_text("{}")
    writes: list[str] = []
    result = run_squeeze(
        _cfg(tmp_path), cgroup_writer=lambda p, f, v: writes.append(v), **_readers()
    )
    assert result.stop_reason == "error"
    assert "recovery" in result.error
    assert writes == []


def test_sighup_mid_run_restores_and_still_writes_the_end_audit(tmp_path, monkeypatch):
    calls = {"n": 0}
    handlers: dict[int, object] = {}
    writes: list[str] = []
    audits: list[str] = []

    class Audit:
        def record(self, **kw):
            audits.append(kw["kind"])

    def fake_signal(signum, handler):
        old = handlers.get(signum, signal.SIG_DFL)
        handlers[signum] = handler
        return old

    def sleep(_):
        calls["n"] += 1
        if calls["n"] == 2:
            handlers[signal.SIGHUP](signal.SIGHUP, None)

    monkeypatch.setattr(squeeze.time, "sleep", sleep)
    monkeypatch.setattr(squeeze.signal, "signal", fake_signal)
    readers = _readers()
    readers["auditor"] = Audit()
    result = run_squeeze(
        _cfg(tmp_path, start=10, floor=1),
        cgroup_writer=lambda p, f, v: writes.append(v),
        **readers,
    )
    assert result.stop_reason == "terminated"
    assert result.exit_signal == signal.SIGHUP
    assert writes[-1] == "max"
    assert audits == ["squeeze", "squeeze-end"]
    assert not (tmp_path / "recovery" / "w.json").exists()


def test_recover_restores_original_and_removes_record(tmp_path):
    rec_dir = tmp_path / "recovery"
    cg = tmp_path / "cgroup"
    cg.mkdir()
    (cg / "memory.high").write_text("1\n")
    rec = recovery_path_for(str(cg), rec_dir)
    rec_dir.mkdir()
    rec.write_text(json.dumps({"target": str(cg), "original": "4096", "relax_to": "max"}))
    writes: list[tuple[str, str]] = []
    out = recover_squeeze(
        None,
        recovery_dir=rec_dir,
        cgroup_root=str(tmp_path),
        writer=lambda p, f, v: writes.append((p, v)),
    )
    assert [o.ok for o in out] == [True]
    assert writes == [(str(cg), "4096")]
    assert not rec.exists()


def test_recover_failure_keeps_the_record(tmp_path):
    rec_dir = tmp_path / "recovery"
    rec_dir.mkdir()
    cg = tmp_path / "cgroup"
    cg.mkdir()
    (cg / "memory.high").write_text("1\n")
    rec = recovery_path_for(str(cg), rec_dir)
    rec.write_text(json.dumps({"target": str(cg), "original": None, "relax_to": "max"}))

    def writer(p, f, v):
        raise OSError("busy")

    out = recover_squeeze(str(cg), recovery_dir=rec_dir, cgroup_root=str(tmp_path), writer=writer)
    assert [o.ok for o in out] == [False]
    assert rec.exists()


def test_confinement_requires_path_under_root_with_regular_memory_high(tmp_path):
    root = tmp_path / "cgroup"
    good = root / "a.slice"
    good.mkdir(parents=True)
    (good / "memory.high").write_text("max\n")
    assert check_confinement(str(good), str(root)) is None

    assert check_confinement(str(tmp_path), str(root)) is not None  # outside
    assert check_confinement(str(root), str(root)) is not None  # the root itself
    nofile = root / "b.slice"
    nofile.mkdir()
    assert "no memory.high" in check_confinement(str(nofile), str(root))
    link = root / "link"
    link.symlink_to(tmp_path)
    assert check_confinement(str(link), str(root)) is not None  # escapes via symlink
    asdir = root / "c.slice"
    (asdir / "memory.high").mkdir(parents=True)
    assert "regular" in check_confinement(str(asdir), str(root))


def test_run_squeeze_with_production_writer_refuses_target_outside_cgroup_root(tmp_path):
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    result = run_squeeze(
        _cfg(tmp_path, target=str(outside), cgroup_root=str(tmp_path / "cgroup")),
        **_readers(),
    )
    assert result.stop_reason == "error"
    assert not (outside / "memory.high").exists()


# --------------------------------------------------------------- C-10 / C-11

from conftest import fixture_root  # noqa: E402
from topos.config import DamonConfig  # noqa: E402
from topos.damon import control  # noqa: E402

_GAME_KEY = "system.slice/docker-" + "a" * 64 + ".scope"


def _damon_tree(tmp_path: Path) -> Path:
    root = tmp_path / "kdamonds"
    root.mkdir()
    (root / "nr_kdamonds").write_text("1\n")
    (root / "0").mkdir()
    (root / "0" / "state").write_text("off\n")
    return root


def _start(tmp_path: Path, damon_root: Path, state_dir: Path):
    return control.start_entity_session(
        _GAME_KEY,
        cgroup_root=fixture_root() / "cgroupfs" / "gstammtisch",
        damon_root=damon_root,
        state_dir=state_dir,
        config=DamonConfig(),
        confirmed_text=control.APPROVAL_TEXT,
        require_root=False,
    )


def test_teardown_writes_nr_contexts_zero_and_never_removes_the_contexts_tree(tmp_path):
    damon_root = _damon_tree(tmp_path)
    state_dir = tmp_path / "state"
    _start(tmp_path, damon_root, state_dir)
    attr = damon_root / "0" / "contexts" / "0" / "operations"
    assert attr.exists()
    assert control.stop_owned_sessions(
        damon_root=damon_root, state_dir=state_dir, all_mine=True, require_root=False
    ) == 1
    # sysfs attribute files cannot be unlinked, so teardown must leave them alone
    assert attr.exists()
    assert (damon_root / "0" / "contexts" / "nr_contexts").read_text().strip() == "0"
    assert (damon_root / "0" / "state").read_text().strip() == "off"


def test_marker_is_written_before_the_sysfs_writes(tmp_path, monkeypatch):
    damon_root = _damon_tree(tmp_path)
    state_dir = tmp_path / "state"
    seen: list[bool] = []
    real = control._write_sysfs

    def spy(path, value):
        seen.append((state_dir / "damon" / "kdamond-0.json").exists())
        real(path, value)

    monkeypatch.setattr(control, "_write_sysfs", spy)
    _start(tmp_path, damon_root, state_dir)
    assert seen and all(seen)


def test_failed_sysfs_write_leaves_a_marker_so_the_kdamond_can_be_stopped(tmp_path, monkeypatch):
    damon_root = _damon_tree(tmp_path)
    state_dir = tmp_path / "state"

    def boom(path, value):
        raise OSError("sysfs write failed")

    monkeypatch.setattr(control, "_write_sysfs", boom)
    with pytest.raises(OSError):
        _start(tmp_path, damon_root, state_dir)
    assert (state_dir / "damon" / "kdamond-0.json").exists()


def test_markers_record_boot_id_and_other_boot_markers_are_ignored_and_removed(
    tmp_path, monkeypatch
):
    damon_root = _damon_tree(tmp_path)
    state_dir = tmp_path / "state"
    monkeypatch.setattr(control, "_current_boot_id", lambda: "boot-A")
    session = _start(tmp_path, damon_root, state_dir)
    assert json.loads(session.marker_path.read_text())["boot_id"] == "boot-A"
    assert control.owned_markers(state_dir) == [session.marker_path]

    monkeypatch.setattr(control, "_current_boot_id", lambda: "boot-B")
    assert control.owned_markers(state_dir) == []
    assert not session.marker_path.exists()
    # the stale marker no longer blocks a new session on the same slot
    (damon_root / "0" / "state").write_text("off\n")  # the old kdamond died with the boot
    assert _start(tmp_path, damon_root, state_dir).kdamond_idx == 0


def test_legacy_marker_without_boot_id_is_kept(tmp_path, monkeypatch):
    monkeypatch.setattr(control, "_current_boot_id", lambda: "boot-B")
    marker = tmp_path / "state" / "damon" / "kdamond-0.json"
    marker.parent.mkdir(parents=True)
    marker.write_text(json.dumps({"owner": "topos", "kdamond_idx": 0}))
    assert control.owned_markers(tmp_path / "state") == [marker]


# --------------------------------------------------------------------- C-13


def _docker_fixture(tmp_path: Path, lines: list[bytes]) -> Path:
    d = tmp_path / "fx" / "containers" / ("c" * 64)
    d.mkdir(parents=True)
    (d / ("c" * 64 + "-json.log")).write_bytes(b"".join(lines))
    return tmp_path / "fx"


def _read_docker(fx: Path, **kw):
    from topos.inspect_files.reader import build_inspect_read

    return build_inspect_read(
        "docker-json-log",
        "c" * 64,
        inspect_files=True,
        admin=True,
        fixture_root=fx,
        is_root=lambda: True,
        **kw,
    )


def test_docker_log_read_returns_the_tail_not_the_head(tmp_path):
    lines = [f"line-{i:05d}\n".encode() for i in range(5000)]
    fx = _docker_fixture(tmp_path, lines)
    result = _read_docker(fx, max_bytes=1000, max_lines=5000)
    out = result.content.splitlines()
    assert out[-1] == "line-04999"
    assert "line-00000" not in result.content
    assert out[0].startswith("line-")  # whole lines only, partial first line dropped
    assert len(out[0]) == len("line-00000")
    assert result.truncated_bytes is True


def test_docker_log_tail_honours_max_lines(tmp_path):
    fx = _docker_fixture(tmp_path, [f"l{i}\n".encode() for i in range(10)])
    result = _read_docker(fx, max_lines=3)
    assert result.content.splitlines() == ["l7", "l8", "l9"]
    assert result.truncated_lines is True


def test_small_docker_log_is_returned_whole(tmp_path):
    fx = _docker_fixture(tmp_path, [b"a\n", b"b\n"])
    result = _read_docker(fx)
    assert result.content == "a\nb\n" or result.content == "a\nb"
    assert not result.truncated_bytes and not result.truncated_lines


# --------------------------------------------------------------------- C-14


def test_journald_snapshot_honours_the_callers_max_bytes():
    import sys

    from topos.inspect_files import reader

    child = "import sys\nwhile True:\n    sys.stdout.buffer.write(b'y' * 4096)\n    sys.stdout.flush()"
    real = reader._default_journald_runner

    def runner(argv, *, timeout, max_bytes=65536):
        return real((sys.executable, "-c", child), timeout=30, max_bytes=max_bytes)

    # unit tests inject nothing into the production path, so drive the runner via argv rewrite
    original = reader._default_journald_runner
    reader._default_journald_runner = runner  # type: ignore[assignment]
    try:
        result = reader._run_journald_snapshot("ssh.service", max_lines=100000, max_bytes=500)
    finally:
        reader._default_journald_runner = original
    assert len(result.content.encode()) <= 500
    assert result.truncated_bytes is True


# --------------------------------------------------------------------- C-15


def test_cgroup_target_allows_systemd_escaped_backslash_names():
    from topos.inspect_files.catalog import _normalise_cgroup_target

    name = "system.slice/system-systemd\\x2dfsck.slice"
    assert _normalise_cgroup_target(name) == name
    with pytest.raises(ValueError):
        _normalise_cgroup_target("system.slice/../x")


# --------------------------------------------------------------------- C-16


def test_kill_preview_refuses_protected_and_invalid_targets_like_execute():
    from topos.actions.kill_ops import build_kill_preview

    with pytest.raises(ValueError, match="protected"):
        build_kill_preview("docker-kill", "game", protected_check=lambda k, t: True)
    with pytest.raises(ValueError):
        build_kill_preview("docker-kill", "a/b", protected_check=lambda k, t: False)
    plan = build_kill_preview("docker-kill", "game", protected_check=lambda k, t: False)
    assert plan.argv[-1] == "game"


# --------------------------------------------------------------------- C-08


def test_container_selector_resolves_to_container_id_for_docker_kinds(monkeypatch):
    from topos import cli

    key = f"system.slice/docker-{FULL_ID}.scope"
    monkeypatch.setattr(cli, "_resolve_container_target", lambda name: key)
    for kind in ("docker-stop", "docker-kill", "docker-json-log"):
        assert cli._resolve_mutual_exclusive_target(None, "game", "action", kind) == FULL_ID
    # cgroup-files and no-kind callers keep the cgroup key
    assert cli._resolve_mutual_exclusive_target(None, "game", "inspect-files", "cgroup-files") == key
    assert cli._resolve_mutual_exclusive_target(None, "game", "squeeze") == key


def test_container_id_passes_the_docker_target_validators():
    from topos.actions.catalog import ActionKind, validate_target
    from topos.inspect_files.catalog import _validate_docker_target

    validate_target(ActionKind.DOCKER_STOP, FULL_ID)
    _validate_docker_target(FULL_ID)
