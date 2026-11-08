"""The unit resource: fake-root planning, guards, runtime actions and revert."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from augeaswrap import Request, Unit
from augeaswrap import _fs, engine as engine_module, unit as unit_module
from augeaswrap.engine import apply, journal, load_request, plan, request_to_toml, revert
from augeaswrap.unit import _UnitCommandError


class Commands:
    """Record argv and return canned command results."""

    def __init__(self):
        self.calls: list[tuple[str, ...]] = []
        self.timeouts: list[float] = []
        self.outcomes: dict[tuple[str, ...], object] = {}

    def __call__(self, argv, timeout):
        command = tuple(argv)
        self.calls.append(command)
        self.timeouts.append(timeout)
        outcome = self.outcomes.get(command, 0)
        if isinstance(outcome, BaseException):
            raise outcome
        return subprocess.CompletedProcess(command, int(outcome), "", "" if int(outcome) == 0 else "canned failure")


def fake_root(tmp_path: Path) -> Path:
    root = tmp_path / "root"
    root.mkdir()
    return root


def make_executable(root: Path, command: str) -> Path:
    target = root / command.lstrip("/")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("#!/bin/sh\n", encoding="utf-8")
    target.chmod(0o755)
    return target


def root_owned(monkeypatch: pytest.MonkeyPatch, target: Path) -> None:
    original = unit_module._lstat

    def inspect(path: Path):
        info = original(path)
        if path == target:
            return SimpleNamespace(st_mode=info.st_mode, st_uid=0, st_gid=info.st_gid)
        return info

    monkeypatch.setattr(unit_module, "_lstat", inspect)


def service_text(command: str = "/usr/bin/example", description: str = "Example") -> str:
    return f"[Unit]\nDescription={description}\n[Service]\nExecStart={command}\n"


@pytest.fixture
def commands(monkeypatch):
    recorder = Commands()
    monkeypatch.setattr(unit_module, "_run_command", recorder)
    return recorder


@pytest.fixture
def world(tmp_path, commands, monkeypatch):
    root = fake_root(tmp_path)
    executable = make_executable(root, "/usr/bin/example")
    root_owned(monkeypatch, executable)
    return root, executable, commands


def test_present_absent_idempotent_and_revert_for_a_whole_unit(world):
    root, _executable, commands = world
    resource = Unit(name="example.service", state="present", text=service_text(), mode="0644", enable=True, on_change="restart")
    request = Request("unit writer", (resource,))

    planned = plan(request, root=root)
    assert planned.has_changes
    assert [action["name"] for action in planned.as_dict()["post_actions"]] == [
        "daemon-reload",
        "unit enable example.service",
        "unit restart example.service",
    ]
    assert commands.calls[0][0] == "systemd-analyze"
    assert commands.timeouts[0] == 30.0

    result = apply(planned)
    target = root / "etc/systemd/system/example.service"
    assert target.read_text(encoding="utf-8") == service_text()
    assert target.stat().st_mode & 0o777 == 0o644
    assert result.txn and result.changed == ("unit:/etc/systemd/system/example.service",)

    assert not plan(request, root=root).has_changes
    assert [entry["id"] for entry in journal(root=root)] == [result.txn]
    assert {entry["status"] for entry in journal(result.txn, root=root)["actions"]} == {"skipped(root)"}

    assert revert(result.txn, root=root).txn
    assert not target.exists()

    target.write_text(service_text(), encoding="utf-8")
    target.chmod(0o644)
    absent = Unit(name="example.service", state="absent")
    assert [action["name"] for action in plan(Request("remove", (absent,)), root=root).as_dict()["post_actions"]] == [
        "daemon-reload",
        "unit reset-failed example.service",
    ]
    removed = apply(Request("remove", (absent,)), root=root)
    assert not target.exists()
    assert journal(removed.txn, root=root)["status"] == "done"
    restored = revert(removed.txn, root=root)
    assert target.read_text(encoding="utf-8") == service_text()
    assert "daemon-reload: skipped(root)" in restored.actions


def test_changed_unit_revert_restores_exact_bytes_and_replays_actions(world):
    root, _executable, commands = world
    target = root / "etc/systemd/system/example.service"
    target.parent.mkdir(parents=True)
    target.write_text(service_text(description="Before"), encoding="utf-8")
    target.chmod(0o600)

    result = apply(
        Request(
            "change",
            (
                Unit(
                    name="example.service",
                    state="present",
                    text=service_text(description="After"),
                    mode="0640",
                    on_change="reload",
                    enable=False,
                ),
            ),
        ),
        root=root,
    )
    assert target.read_text(encoding="utf-8") == service_text(description="After")
    assert target.stat().st_mode & 0o777 == 0o640
    restored = revert(result.txn, root=root)
    assert target.read_text(encoding="utf-8") == service_text(description="Before")
    assert target.stat().st_mode & 0o777 == 0o600
    assert restored.actions == ("daemon-reload: skipped(root)", "unit reload example.service: skipped(root)")


def test_dropin_present_absent_and_revert_with_a_local_base_unit(world):
    root, _executable, commands = world
    base = root / "etc/systemd/system/example.service"
    base.parent.mkdir(parents=True)
    base.write_text(service_text(), encoding="utf-8")
    dropin = Unit(
        name="example.service",
        state="present",
        dropin="50-extra.conf",
        text="[Service]\nNice=5\n",
        mode="0644",
        on_change="restart",
    )
    created = apply(Request("drop-in", (dropin,)), root=root)
    target = root / "etc/systemd/system/example.service.d/50-extra.conf"
    assert target.read_text(encoding="utf-8") == "[Service]\nNice=5\n"
    assert [action["name"] for action in plan(Request("drop-in", (dropin,)), root=root).as_dict()["post_actions"]] == []
    assert revert(created.txn, root=root).txn
    assert not target.exists()

    absent = Unit(name="example.service", state="absent", dropin="50-extra.conf", on_change="reload")
    assert [action["name"] for action in plan(Request("remove", (absent,)), root=root).as_dict()["post_actions"]] == []
    assert apply(Request("remove", (absent,)), root=root).txn is None


def test_dropin_absent_only_reloads_then_applies_on_change(world):
    root, _executable, commands = world
    target = root / "etc/systemd/system/example.service.d/50-extra.conf"
    target.parent.mkdir(parents=True)
    target.write_text("old", encoding="utf-8")
    resource = Unit(name="example.service", state="absent", dropin="50-extra.conf", on_change="restart")
    planned = plan(Request("remove", (resource,)), root=root)
    assert [action["name"] for action in planned.as_dict()["post_actions"]] == [
        "daemon-reload",
        "unit restart example.service",
    ]
    result = apply(planned)
    assert not target.exists()
    assert result.changed == ("unit:/etc/systemd/system/example.service.d/50-extra.conf",)


def test_dropin_of_a_host_only_unit_skips_verify_but_still_validates(world):
    root, _executable, commands = world
    # No /etc/systemd/system/example.service exists: it is a host-only unit.
    dropin = Unit(name="example.service", state="present", dropin="50-host.conf", text="[Service]\nNice=1\n", mode="0644", on_change="none")
    result = apply(Request("drop-in", (dropin,)), root=root)
    assert result.txn
    assert commands.calls == []


def test_a_changed_socket_restarts_but_a_noop_socket_does_not(world):
    root, _executable, commands = world
    socket_path = root / "etc/systemd/system/example.socket"
    socket_path.parent.mkdir(parents=True)
    old_text = "[Socket]\nListenStream=/run/example.sock\n"
    socket_path.write_text(old_text, encoding="utf-8")
    socket = Unit(name="example.socket", state="present", text=old_text, mode="0644", enable=True, on_change="restart")
    service = Unit(name="example.service", state="present", text=service_text(), mode="0644", enable=False, on_change="none")
    planned = plan(Request("both", (socket, service)), root=root)
    assert [action["name"] for action in planned.as_dict()["post_actions"]] == ["daemon-reload"]
    socket_new = Unit(name="example.socket", state="present", text=old_text + "FileDescriptorName=example\n", mode="0644", enable=False, on_change="restart")
    assert "unit restart example.socket" in [
        action["name"] for action in plan(Request("socket", (socket_new,)), root=root).as_dict()["post_actions"]
    ]
    socket_path.chmod(0o600)
    metadata_only = Unit(name="example.socket", state="present", text=old_text, mode="0640", enable=False, on_change="restart")
    assert "unit restart example.socket" not in [
        action["name"] for action in plan(Request("metadata", (metadata_only,)), root=root).as_dict()["post_actions"]
    ]
    dropin = root / "etc/systemd/system/example.socket.d/50-socket.conf"
    dropin.parent.mkdir(parents=True)
    dropin.write_text("[Socket]\nBacklog=16\n", encoding="utf-8")
    dropin.chmod(0o600)
    metadata_dropin = Unit(name="example.socket", state="present", dropin="50-socket.conf", text="[Socket]\nBacklog=16\n", mode="0640", on_change="restart")
    assert "unit restart example.socket" not in [
        action["name"] for action in plan(Request("metadata dropin", (metadata_dropin,)), root=root).as_dict()["post_actions"]
    ]


@pytest.mark.parametrize(
    ("kind", "message"),
    [
        ("missing", "executable path component is missing"),
        ("not-executable", "executable bit is not set"),
        ("owner", "executable is not root-owned"),
        ("writable-component", "group/other-writable component"),
        ("tmp", "refusing executable below /tmp"),
        ("group-writable", "group/other-writable component"),
    ],
)
def test_exec_check_refusals(tmp_path, commands, monkeypatch, kind, message):
    root = fake_root(tmp_path)
    command = "/usr/bin/example"
    target = make_executable(root, command)
    if kind != "owner":
        root_owned(monkeypatch, target)
    if kind == "missing":
        target.unlink()
    elif kind == "not-executable":
        target.chmod(0o644)
    elif kind == "writable-component":
        target.parent.chmod(0o775)
    elif kind == "tmp":
        command = "/tmp/example"
        target = make_executable(root, command)
        root_owned(monkeypatch, target)
    elif kind == "group-writable":
        target.chmod(0o755 | 0o020)
    resource = Unit(name="example.service", state="present", text=service_text(command), mode="0644", enable=False, on_change="none")
    with pytest.raises(Exception, match=message):
        plan(Request("bad exec", (resource,)), root=root)


def test_verify_failure_aborts_before_a_managed_write(world, monkeypatch):
    root, _executable, commands = world
    resource = Unit(name="example.service", state="present", text=service_text(), mode="0644", enable=False, on_change="none")
    original = commands.__call__

    def fail_verify(argv, timeout):
        if tuple(argv)[0] == "systemd-analyze":
            commands.calls.append(tuple(argv))
            commands.timeouts.append(timeout)
            raise _UnitCommandError("exit 2: invalid unit")
        return original(argv, timeout)

    monkeypatch.setattr(unit_module, "_run_command", fail_verify)
    with pytest.raises(Exception, match="systemd-analyze verify failed"):
        plan(Request("bad unit", (resource,)), root=root)
    assert not (root / "etc/systemd/system/example.service").exists()


@pytest.mark.parametrize("mode", ["failure", "timeout"])
def test_preflight_failure_and_timeout_abort_before_write(world, mode):
    root, _executable, commands = world
    preflight = ("/usr/bin/preflight", "--check")
    if mode == "failure":
        commands.outcomes[preflight] = _UnitCommandError("exit 1: bad config")
    else:
        commands.outcomes[preflight] = subprocess.TimeoutExpired(preflight, 30)
    resource = Unit(
        name="example.service",
        state="present",
        text=service_text(),
        mode="0644",
        enable=False,
        on_change="none",
        preflight=preflight,
    )
    with pytest.raises(Exception, match=f"preflight {'failed' if mode == 'failure' else 'timed out'}"):
        plan(Request("bad preflight", (resource,)), root=root)
    assert commands.timeouts[-1] == 30.0
    assert not (root / "etc/systemd/system/example.service").exists()


@pytest.mark.parametrize(
    ("on_change", "runtime_name"),
    [("none", None), ("restart", "unit restart example.service"), ("reload", "unit reload example.service")],
)
def test_on_change_variants(world, on_change, runtime_name):
    root, _executable, commands = world
    resource = Unit(name="example.service", state="present", text=service_text(), mode="0644", enable=False, on_change=on_change)
    names = [action["name"] for action in plan(Request("change", (resource,)), root=root).as_dict()["post_actions"]]
    assert names == ["daemon-reload"] + ([runtime_name] if runtime_name else [])


def test_pending_records_the_action_in_the_journal_without_running_it(world):
    root, _executable, commands = world
    resource = Unit(name="example.service", state="present", text=service_text(), mode="0644", enable=False, on_change="pending", preflight=("/usr/bin/preflight",))
    planned = plan(Request("pending", (resource,)), root=root)
    pending_plan = [item for item in planned.as_dict()["post_actions"] if item["name"] == "unit restart example.service"]
    assert pending_plan[0]["pending"] is True and pending_plan[0]["status"] == "pending"
    result = apply(planned)
    assert result.pending == ("unit restart example.service",)
    action = next(item for item in journal(result.txn, root=root)["actions"] if item["name"] == "unit restart example.service")
    assert action["status"] == "pending" and action["pending"] is True
    assert action["result"] == "unit restart example.service: recorded pending"
    assert commands.calls[-1] == ("/usr/bin/preflight",)
    restored = revert(result.txn, root=root)
    assert not (root / "etc/systemd/system/example.service").exists()
    assert restored.pending == ("unit restart example.service",)
    restored_action = next(
        item for item in journal(restored.txn, root=root)["actions"] if item["name"] == "unit restart example.service"
    )
    assert restored_action["status"] == "pending" and restored_action["pending"] is True


def test_daemon_reload_is_declared_once_for_multiple_changed_units(world):
    root, _executable, commands = world
    one = Unit(name="one.service", state="present", text=service_text(), mode="0644", enable=False, on_change="none")
    two = Unit(name="two.service", state="present", text=service_text(), mode="0644", enable=False, on_change="none")
    planned = plan(Request("two units", (one, two)), root=root)
    actions = planned.as_dict()["post_actions"]
    assert [item["name"] for item in actions] == ["daemon-reload"]
    result = apply(planned)
    assert [item["name"] for item in journal(result.txn, root=root)["actions"]] == ["daemon-reload"]


def test_absent_unit_stops_and_disables_before_delete(tmp_path, commands, monkeypatch):
    root = fake_root(tmp_path)
    target = root / "etc/systemd/system/example.service"
    target.parent.mkdir(parents=True)
    target.write_text(service_text(), encoding="utf-8")
    events: list[str] = []
    original_unlink = unit_module._fs.unlink_and_sync

    def record_unlink(managed_root, path):
        events.append("delete")
        return original_unlink(managed_root, path)

    monkeypatch.setattr(unit_module, "_is_live_root", lambda path: True)
    monkeypatch.setattr(unit_module._fs, "unlink_and_sync", record_unlink)
    resource = Unit(name="example.service", state="absent")
    planned = plan(Request("remove", (resource,)), root=root)
    apply(planned)
    assert [command[:2] for command in commands.calls] == [("systemctl", "stop"), ("systemctl", "disable")]
    assert events == ["delete"]
    assert not target.exists()


def test_failure_after_commit_is_journaled(world):
    root, _executable, commands = world
    resource = Unit(name="example.service", state="present", text=service_text(), mode="0644", enable=False, on_change="none")
    from augeaswrap import ApplyFailed

    with pytest.raises(ApplyFailed) as caught:
        apply(
            Request("failure", (resource,)),
            root=root,
            fail_step=lambda name: "boom" if "post-action daemon-reload" in name else None,
        )
    value = journal(caught.value.txn, root=root)
    assert value["status"] == "failed"
    assert value["steps"][0]["status"] == "done"
    assert value["steps"][1]["status"] == "failed"
    assert value["failed"]["reason"] == "boom"
    assert (root / "etc/systemd/system/example.service").exists()


def test_unit_stale_revert_and_later_transaction_refusals(world):
    root, _executable, commands = world
    resource = Unit(name="example.service", state="present", text=service_text(description="One"), mode="0644", enable=False, on_change="none")
    first = apply(Request("one", (resource,)), root=root)
    target = root / "etc/systemd/system/example.service"
    target.write_text(service_text(description="Drift"), encoding="utf-8")
    with pytest.raises(Exception, match="changed outside Augeaswrap"):
        revert(first.txn, root=root)
    target.write_text(service_text(description="One"), encoding="utf-8")
    apply(
        Request(
            "two",
            (Unit(name="example.service", state="present", text=service_text(description="Two"), mode="0644", enable=False, on_change="none"),),
        ),
        root=root,
    )
    target.write_text(service_text(description="One"), encoding="utf-8")
    with pytest.raises(Exception, match="touched by later transaction"):
        revert(first.txn, root=root)


def test_changing_a_verify_source_makes_the_plan_stale(world):
    root, _executable, commands = world
    base = root / "etc/systemd/system/example.service"
    base.parent.mkdir(parents=True)
    base.write_text(service_text(), encoding="utf-8")
    dropin = Unit(name="example.service", state="present", dropin="50-a.conf", text="[Service]\nNice=1\n", mode="0644", on_change="none")
    planned = plan(Request("drop-in", (dropin,)), root=root)
    base.write_text(service_text(description="Changed"), encoding="utf-8")
    with pytest.raises(Exception, match="changed after plan"):
        apply(planned)


def test_loader_round_trip_and_strict_shape_validation(world):
    root, _executable, commands = world
    resources = (
        Unit(name="example.service", state="present", text=service_text(), mode="0644", enable=True, on_change="restart", preflight=("/usr/bin/preflight",)),
        Unit(name="example.service", state="present", dropin="50-a.conf", text="[Service]\nNice=1\n", mode="0644", on_change="pending"),
        Unit(name="example.service", state="absent", dropin="50-b.conf", on_change="reload"),
        Unit(name="old.service", state="absent"),
    )
    request = Request("round trip", resources)
    path = root.parent / "request.toml"
    path.write_text(request.to_toml(), encoding="utf-8")
    parsed = load_request(path)
    assert parsed == request
    assert parsed.to_toml() == request_to_toml(request)
    assert "[[unit]]" in request.to_toml()

    with pytest.raises(ValueError, match="unknown unit key"):
        load_request({"augeaswrap": 1, "writer": "w", "unit": [{"name": "x.service", "state": "present", "unknown": 1}]})
    with pytest.raises(ValueError, match="missing unit field"):
        load_request({"augeaswrap": 1, "writer": "w", "unit": [{"name": "x.service", "state": "present"}]})


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"name": "bad", "state": "absent"}, "known systemd suffix"),
        ({"name": "bad/service", "state": "absent"}, "path separator"),
        ({"name": "../bad.service", "state": "absent"}, "path separator"),
        ({"name": "bad$name.service", "state": "absent"}, "unsupported character"),
        ({"name": "x.service", "state": "maybe"}, "unit state"),
        ({"name": "x.service", "state": "present", "text": "x", "mode": "0644", "enable": True}, "on_change"),
        ({"name": "x.service", "state": "present", "text": "x", "mode": "0644", "enable": 1, "on_change": "none"}, "enable"),
        ({"name": "x.service", "state": "absent", "text": "x"}, "must not specify"),
        ({"name": "x.service", "state": "present", "dropin": "../x.conf", "text": "x", "mode": "0644", "on_change": "none"}, "path separator"),
        ({"name": "x.service", "state": "present", "dropin": "x", "text": "x", "mode": "0644", "on_change": "none"}, "conf basename"),
        ({"name": "x.service", "state": "present", "dropin": "50-x.conf", "text": "x", "mode": "0644", "enable": True, "on_change": "none"}, "enable"),
        ({"name": "x.service", "state": "absent", "dropin": "50-x.conf"}, "requires on_change"),
        ({"name": "x.service", "state": "present", "text": "x", "mode": "0644", "enable": False, "on_change": "bad"}, "on_change"),
    ],
)
def test_constructor_refusals(kwargs, message):
    with pytest.raises(ValueError, match=message):
        Unit(**kwargs)


def test_exec_parser_prefixes_and_relative_or_malformed_refusals(world):
    root, _executable, commands = world
    assert unit_module._exec_paths(
        "[Service]\n"
        "ExecStart=-@:+!/usr/bin/example --flag\n"
        "ExecReload=/usr/bin/example reload\n"
        "# ExecStop=/usr/bin/ignored\n",
        "/etc/systemd/system/example.service",
    ) == ("/usr/bin/example",)
    for text, message in [
        ("[Service]\nExecStart=relative\n", "must be absolute"),
        ("[Service]\nExecStart='/unterminated\n", "cannot parse"),
        ("[Service]\nExecStart=-\n", "empty executable"),
    ]:
        resource = Unit(name="example.service", state="present", text=text, mode="0644", enable=False, on_change="none")
        with pytest.raises(Exception, match=message):
            plan(Request("bad", (resource,)), root=root)


def test_symlinked_unit_target_is_refused_by_the_fake_root(world):
    root, _executable, commands = world
    target = root / "etc/systemd/system/example.service"
    target.parent.mkdir(parents=True)
    elsewhere = root / "outside"
    elsewhere.write_text("x", encoding="utf-8")
    target.symlink_to(elsewhere)
    resource = Unit(name="example.service", state="present", text=service_text(), mode="0644", enable=False, on_change="none")
    with pytest.raises(Exception, match="symlink"):
        plan(Request("symlink", (resource,)), root=root)


def test_invalid_restored_preimage_refuses_before_restore(world):
    root, _executable, commands = world
    result = apply(
        Request(
            "one",
            (Unit(name="example.service", state="present", text=service_text(), mode="0644", enable=False, on_change="none"),),
        ),
        root=root,
    )
    pre = next((root / "var/lib/augeaswrap/txn" / result.txn / "pre").glob("*.json"))
    record = json.loads(pre.read_text(encoding="utf-8"))
    record["exists"] = "maybe"
    pre.write_text(json.dumps(record), encoding="utf-8")
    with pytest.raises(Exception, match="exists flag"):
        revert(result.txn, root=root)


def test_shadow_check_is_a_documented_noop_seam():
    assert unit_module.shadow_check(object()) == []


def test_preflight_success_is_recorded_with_its_bounded_timeout(world):
    root, _executable, commands = world
    preflight = ("/usr/bin/preflight", "--ok")
    resource = Unit(
        name="example.service",
        state="present",
        text=service_text(),
        mode="0644",
        enable=False,
        on_change="none",
        preflight=preflight,
    )
    apply(Request("validated", (resource,)), root=root)
    assert preflight in commands.calls
    assert commands.timeouts[commands.calls.index(preflight)] == 30.0


def test_owner_and_group_are_resolved_and_preserved(world):
    root, _executable, commands = world
    target = root / "etc/systemd/system/example.service"
    target.parent.mkdir(parents=True)
    target.write_text("old", encoding="utf-8")
    target.chmod(0o600)
    resource = Unit(name="example.service", state="present", text=service_text(), mode="0644", enable=False, on_change="none")
    planned = plan(Request("metadata", (resource,)), root=root)
    desired = planned.changes[0].after["target"]
    assert desired["uid"] == target.stat().st_uid and desired["gid"] == target.stat().st_gid


def test_post_action_validation_rejects_bad_pending_value():
    from augeaswrap import PostAction

    with pytest.raises(ValueError, match="pending flag"):
        PostAction("x", ("true",), 1, pending=1)


def test_conflicting_pending_declarations_are_refused():
    from augeaswrap import PostAction

    first = SimpleNamespace(kind="fake", post_actions=lambda _changes: [PostAction("same", ("true",), 1, pending=True)])
    second = SimpleNamespace(kind="fake", post_actions=lambda _changes: [PostAction("same", ("true",), 1, pending=False)])
    changes = [
        unit_module.Change("fake", "fake:a", "change", {}, {}, first),
        unit_module.Change("fake", "fake:b", "change", {}, {}, second),
    ]
    with pytest.raises(ValueError, match="conflicting pending declarations"):
        engine_module._post_action_bindings(changes)


def test_command_seam_reports_oserror_and_nonzero(tmp_path, monkeypatch):
    commands = Commands()
    monkeypatch.setattr(unit_module.subprocess, "run", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("missing")))
    with pytest.raises(_UnitCommandError, match="cannot run"):
        unit_module._run_command(("missing",), 1)

    def failed(*_args, **_kwargs):
        return subprocess.CompletedProcess(("bad",), 3, "stdout", "first line\nsecond")

    monkeypatch.setattr(unit_module.subprocess, "run", failed)
    with pytest.raises(_UnitCommandError, match="first line"):
        unit_module._run_command(("bad",), 1)


def test_command_seam_reports_timeout(monkeypatch):
    def timeout(*_args, **_kwargs):
        raise subprocess.TimeoutExpired(("slow",), 1)

    monkeypatch.setattr(unit_module.subprocess, "run", timeout)
    with pytest.raises(unit_module._UnitTimeout, match="timed out"):
        unit_module._run_command(("slow",), 1)


def test_source_inventory_ignores_non_conf_and_target_dropin(world):
    root, _executable, commands = world
    directory = root / "etc/systemd/system/example.service.d"
    directory.mkdir(parents=True)
    (directory / "README").write_text("not a drop-in", encoding="utf-8")
    (directory / "50-target.conf").write_text("target", encoding="utf-8")
    inventory = unit_module._source_inventory(root, "example.service", "/etc/systemd/system/example.service.d/50-target.conf", "50-target.conf")
    assert inventory == []


def test_unit_render_shows_a_unified_diff(world):
    root, _executable, commands = world
    target = root / "etc/systemd/system/example.service"
    target.parent.mkdir(parents=True)
    target.write_text("old\n", encoding="utf-8")
    resource = Unit(name="example.service", state="present", text="new\n", mode="0644", enable=False, on_change="none")
    rendered = plan(Request("diff", (resource,)), root=root).render()
    assert "-old" in rendered and "+new" in rendered


def test_command_runner_success_returns_the_completed_process(monkeypatch):
    completed = subprocess.CompletedProcess(("ok",), 0, "out", "")
    monkeypatch.setattr(unit_module.subprocess, "run", lambda *_args, **_kwargs: completed)
    assert unit_module._run_command(("ok",), 5) is completed


def test_non_regular_exec_and_exec_lstat_error_are_refused(tmp_path, commands, monkeypatch):
    root = fake_root(tmp_path)
    command = "/usr/bin/not-regular"
    target = root / command.lstrip("/")
    target.mkdir(parents=True)
    target.chmod(0o555)
    resource = Unit(name="example.service", state="present", text=service_text(command), mode="0644", enable=False, on_change="none")
    with pytest.raises(Exception, match="not a regular file"):
        plan(Request("directory", (resource,)), root=root)

    missing = root / "usr/bin/error"
    original = unit_module._lstat

    def inspect(path):
        if path == missing:
            raise OSError("inspection failed")
        return original(path)

    monkeypatch.setattr(unit_module, "_lstat", inspect)
    resource = Unit(name="error.service", state="present", text=service_text("/usr/bin/error"), mode="0644", enable=False, on_change="none")
    with pytest.raises(Exception, match="cannot inspect executable"):
        plan(Request("io", (resource,)), root=root)


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"name": None, "state": "absent"}, "non-empty string"),
        ({"name": "x.service", "state": "present", "text": None, "mode": "0644", "enable": False, "on_change": "none"}, "text is required"),
        ({"name": "x.service", "state": "present", "text": "x", "mode": None, "enable": False, "on_change": "none"}, "mode is required"),
        ({"name": "x.service", "state": "absent", "enable": False}, "must not specify"),
        ({"name": "x.service", "state": "present", "dropin": "", "text": "x", "mode": "0644", "on_change": "none"}, "non-empty string"),
        ({"name": "x.service", "state": "present", "dropin": "x$.conf", "text": "x", "mode": "0644", "on_change": "none"}, "unsupported character"),
        ({"name": "x.service", "state": "present", "text": None, "dropin": "x.conf", "mode": "0644", "on_change": "none"}, "text is required"),
        ({"name": "x.service", "state": "present", "text": "x", "dropin": "x.conf", "mode": None, "on_change": "none"}, "mode is required"),
        ({"name": "x.service", "state": "present", "text": "x", "dropin": "x.conf", "mode": "0644", "on_change": None}, "on_change"),
        ({"name": "x.service", "state": "absent", "dropin": "x.conf", "text": "x", "on_change": "none"}, "must not specify"),
        ({"name": "x.service", "state": "absent", "dropin": "x.conf", "on_change": "none", "preflight": ("x",)}, "takes no preflight"),
        ({"name": "x.service", "state": "present", "text": "x", "mode": "0644", "enable": False, "on_change": "none", "preflight": []}, "non-empty argv"),
        ({"name": "x.service", "state": "present", "text": "x", "mode": "0644", "enable": False, "on_change": "none", "preflight": [""]}, "non-empty strings"),
    ],
)
def test_additional_constructor_refusals(kwargs, message):
    with pytest.raises(ValueError, match=message):
        Unit(**kwargs)


def test_owner_resolution_failure_is_a_named_plan_refusal(world):
    root, _executable, commands = world
    resource = Unit(name="example.service", state="present", text=service_text(), mode="0644", owner="no-such-user", enable=False, on_change="none")
    with pytest.raises(Exception, match="unknown user"):
        plan(Request("owner", (resource,)), root=root)


def test_private_protocol_guards_report_expected_and_mismatch_problems(world, monkeypatch):
    root, _executable, commands = world
    resource = Unit(name="example.service", state="present", text=service_text(), mode="0644", enable=False, on_change="none")
    address = resource.addresses()[0]
    assert resource.verify(SimpleNamespace(expected={}))[0].code == "unit.verify.expected"
    assert resource.post_actions([]) == []
    with pytest.raises(ValueError, match="observation must be a mapping"):
        resource.diff(SimpleNamespace(root=root), {address: "not-a-mapping"})
    with pytest.raises(TypeError, match="staged image"):
        resource.commit(SimpleNamespace(root=root), object())
    expected = SimpleNamespace(expected={address: unit_module._StagedUnit(False, None, None, None, None, {"fingerprint": "expected"}, True, resource.name, resource.path, None)})
    monkeypatch.setattr(Unit, "observe", lambda _self, _ctx: (_ for _ in ()).throw(_fs.PathRefusal("bad", kind="type", path=address)))
    assert resource.verify(expected)[0].code == "unit.verify.failed"
    monkeypatch.setattr(Unit, "observe", lambda _self, _ctx: {address: {"fingerprint": "wrong"}})
    assert resource.verify(expected)[0].code == "unit.verify.failed"


def test_restore_private_protocol_guards_and_diff_refusal(world):
    root, _executable, commands = world
    image = {"exists": False, "fingerprint": "absent"}
    restored = unit_module._RestoredUnit("/etc/systemd/system/example.service", image, "example.service", None)
    address = restored.addresses()[0]
    with pytest.raises(ValueError, match="restored unit observation"):
        restored.diff(SimpleNamespace(root=root), {address: "bad"})
    with pytest.raises(TypeError, match="staged image"):
        restored.commit(SimpleNamespace(root=root), object())
    assert restored.verify(SimpleNamespace(expected={}))[0].code == "revert.verify.expected"
    expected = SimpleNamespace(root=root, expected={address: unit_module._StagedUnit(False, None, None, None, None, {"fingerprint": "expected"}, True, "example.service", "/etc/systemd/system/example.service", None)})
    assert restored.verify(expected)[0].code == "revert.verify.failed"


def test_restore_verify_observation_failure_is_reported(world, monkeypatch):
    root, _executable, commands = world
    restored = unit_module._RestoredUnit(
        "/etc/systemd/system/example.service",
        {"exists": False, "fingerprint": "absent"},
        "example.service",
        None,
    )
    monkeypatch.setattr(unit_module._RestoredUnit, "observe", lambda _self, _ctx: (_ for _ in ()).throw(_fs.PathRefusal("bad", kind="type", path=restored.path)))
    expected = SimpleNamespace(expected={restored.addresses()[0]: unit_module._StagedUnit(False, None, None, None, None, {}, True, "example.service", restored.path, None)})
    assert restored.verify(expected)[0].code == "revert.verify.failed"


def test_snapshot_input_validation_and_source_race_guard(world, monkeypatch):
    root, _executable, commands = world
    resource = Unit(name="example.service", state="present", text=service_text(), mode="0644", enable=False, on_change="none")
    change = unit_module.Change("unit", resource.addresses()[0], "change", {}, {}, resource)
    with pytest.raises(ValueError, match="observation must be a mapping"):
        unit_module._unit_snapshot(change, "bad")
    with pytest.raises(ValueError, match="no target image"):
        unit_module._unit_snapshot(change, {})

    directory = root / "etc/systemd/system/example.service.d"
    directory.mkdir(parents=True)
    (directory / "50-child.conf").write_text("child", encoding="utf-8")
    inventory = unit_module._source_inventory(root, "example.service", "/etc/systemd/system/example.service", None)
    assert [item["path"].rsplit("/", 1)[-1] for item in inventory] == ["50-child.conf"]
    original = unit_module._source_file

    def disappear(managed_root, absolute):
        if absolute.endswith("50-child.conf"):
            return None
        return original(managed_root, absolute)

    monkeypatch.setattr(unit_module, "_source_file", disappear)
    assert unit_module._source_inventory(root, "example.service", "/etc/systemd/system/example.service", None) == []


def test_restore_record_validation_refusals():
    actions = ()
    cases = [
        ("file:/etc/systemd/system/x.service", {"path": "/etc/systemd/system/x.service", "exists": False}, "does not match"),
        ("unit:/etc/systemd/system/x.service", {"path": "/wrong", "exists": False}, "does not match"),
        ("unit:/etc/systemd/system/x.service.d/sub/child.conf", {"exists": False}, "not a unit or drop-in"),
        ("unit:/etc/systemd/system/x.service", {"path": "/etc/systemd/system/x.service", "exists": True, "mode": True, "uid": 0, "gid": 0, "sha256": "", "data_b64": ""}, "invalid metadata"),
        ("unit:/etc/systemd/system/x.service", {"path": "/etc/systemd/system/x.service", "exists": True, "mode": 0o644, "uid": 0, "gid": 0, "sha256": "0" * 64, "data_b64": "eA=="}, "digest"),
        ("unit:/etc/systemd/system/x.service", {"path": "/etc/systemd/system/x.service", "exists": True, "mode": 0o644, "uid": 0, "gid": 0, "sha256": __import__("hashlib").sha256(b"\xff").hexdigest(), "data_b64": "/w=="}, "not UTF-8"),
    ]
    for address, record, message in cases:
        with pytest.raises(ValueError, match=message):
            unit_module._unit_restore(address, record, actions)


def test_values_helper_rejects_non_unit():
    with pytest.raises(ValueError, match="no unit TOML form"):
        unit_module._unit_values(object())


def test_restore_render_branches():
    resource = unit_module._RestoredUnit(
        "/etc/systemd/system/example.service",
        {"exists": True, "sha256": "x", "data_b64": "bmV3Cg==", "mode": 0o644, "uid": 0, "gid": 0},
        "example.service",
        None,
    )
    change = unit_module.Change("unit", resource.addresses()[0], "change", {}, {"target": {"exists": True}}, resource)
    rendered = unit_module._render_unit(change, SimpleNamespace(observed={}, root="/"))
    assert "+new" in rendered

    absent = unit_module._RestoredUnit(
        "/etc/systemd/system/example.service",
        {"exists": False},
        "example.service",
        None,
    )
    absent_change = unit_module.Change("unit", absent.addresses()[0], "change", {}, {"target": {"exists": False}}, absent)
    old_target = {"exists": True, "sha256": "x", "data_b64": "b2xkCg==", "mode": 0o644, "uid": 0, "gid": 0}
    observed = {absent.addresses()[0]: {"path": absent.path, "target": old_target, "exec_paths": [], "sources": []}}
    assert "-old" in unit_module._render_unit(absent_change, SimpleNamespace(observed=observed))
