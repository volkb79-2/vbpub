"""The unit resource: fake-root planning, guards, runtime actions and revert."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from augeaswrap import File, PlanRefused, PostAction, Problem, Request, StalePlan, Unit
from augeaswrap import _fs, engine as engine_module, unit as unit_module
from augeaswrap.engine import apply, journal, load_request, plan, request_to_toml, revert
from augeaswrap import transactions as transactions_module
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
        if isinstance(outcome, subprocess.CompletedProcess):
            return outcome
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
    owned_paths = {target, *target.parents}

    def inspect(path: Path):
        info = original(path)
        if path in owned_paths:
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
    resource = Unit(name="example.service", state="present", text=service_text(), mode="0644", intent="authoritative", enable=True, on_change="restart")
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

    restored_create = revert(result.txn, root=root)
    assert restored_create.txn
    assert restored_create.actions == (
        "unit stop example.service: skipped(root)",
        "unit disable example.service: skipped(root)",
        "daemon-reload: skipped(root)",
    )
    assert not target.exists()

    target.write_text(service_text(), encoding="utf-8")
    target.chmod(0o644)
    absent = Unit(name="example.service", state="absent")
    assert [action["name"] for action in plan(Request("remove", (absent,)), root=root).as_dict()["post_actions"]] == [
        "unit stop example.service",
        "unit disable example.service",
        "daemon-reload",
        "unit reset-failed example.service",
    ]
    removed = apply(Request("remove", (absent,)), root=root)
    assert not target.exists()
    assert journal(removed.txn, root=root)["status"] == "done"
    restored = revert(removed.txn, root=root)
    assert target.read_text(encoding="utf-8") == service_text()
    assert "daemon-reload: skipped(root)" in restored.actions


def test_crlf_unit_text_is_preserved_exactly(world):
    root, _executable, _commands = world
    text = service_text().replace("\n", "\r\n")
    result = apply(
        Request(
            "preserve CRLF",
            (
                Unit(
                    name="example.service",
                    state="present",
                    text=text,
                    mode="0644",
                    intent="authoritative",
                    enable=False,
                    on_change="none",
                ),
            ),
        ),
        root=root,
    )
    assert result.txn
    assert (root / "etc/systemd/system/example.service").read_bytes() == text.encode("utf-8")


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
                    intent="authoritative",
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


def test_revert_restores_non_utf8_preimage_bytes_exactly(world):
    root, _executable, commands = world
    target = root / "etc/systemd/system/example.service"
    target.parent.mkdir(parents=True)
    old_bytes = b"\xff\xfe[Service]\nExecStart=/usr/bin/example\n"
    target.write_bytes(old_bytes)
    target.chmod(0o600)

    result = apply(
        Request(
            "replace opaque bytes",
            (Unit(name="example.service", state="present", text=service_text(), mode="0644", intent="authoritative", enable=False, on_change="none"),),
        ),
        root=root,
    )
    assert target.read_bytes() == service_text().encode("utf-8")
    assert revert(result.txn, root=root).txn
    assert target.read_bytes() == old_bytes
    assert target.stat().st_mode & 0o777 == 0o600


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
        intent="authoritative",
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
    dropin = Unit(name="example.service", state="present", dropin="50-host.conf", text="[Service]\nNice=1\n", mode="0644", intent="fallback", on_change="none")
    planned = plan(Request("drop-in", (dropin,)), root=root)
    assert any("verify: skipped (base unit not found" in detail for detail in planned.changes[0].details)
    result = apply(planned)
    assert result.txn
    assert commands.calls == []


def test_missing_systemd_analyze_is_reported_and_only_verify_is_skipped(world, monkeypatch):
    root, _executable, commands = world
    monkeypatch.setattr(unit_module.shutil, "which", lambda _name: None)
    resource = Unit(name="example.service", state="present", text=service_text(), mode="0644", intent="authoritative", enable=False, on_change="none", preflight=("/usr/bin/preflight",))
    planned = plan(Request("without analyzer", (resource,)), root=root)
    assert any("verify: skipped (systemd-analyze is not available in PATH" in detail for detail in planned.changes[0].details)
    assert commands.calls == [("/usr/bin/preflight",)]


def test_staged_verify_includes_existing_dependency_units(world, tmp_path):
    root, _executable, commands = world
    dependency = root / "run/systemd/system/network.target"
    dependency.parent.mkdir(parents=True)
    dependency.write_text("[Unit]\nDescription=Network\nAfter=basic.target\n", encoding="utf-8")
    transitive = root / "etc/systemd/system/basic.target"
    transitive.parent.mkdir(parents=True)
    transitive.write_text("[Unit]\nDescription=Basic\n", encoding="utf-8")
    unrelated_vendor_unit = root / "usr/lib/systemd/system/other.target"
    unrelated_vendor_unit.parent.mkdir(parents=True, exist_ok=True)
    unrelated_vendor_unit.write_text("[Unit]\nDescription=Other vendor target\n", encoding="utf-8")
    resource = Unit(
        name="example.service",
        state="present",
        text="[Unit]\nAfter=network.target\n[Service]\nExecStart=/usr/bin/example\n",
        mode="0644",
        intent="authoritative",
        enable=False,
        on_change="none",
    )
    planned = plan(Request("dependency staging", (resource,)), root=root)
    resource.stage(SimpleNamespace(root=root, stage_dir=tmp_path / "staging"), planned.changes[0])
    verify = next(command for command in reversed(commands.calls) if command[0] == "systemd-analyze")
    stage_root = Path(verify[1].removeprefix("--root="))
    assert (stage_root / "run/systemd/system/network.target").read_text(encoding="utf-8") == "[Unit]\nDescription=Network\nAfter=basic.target\n"
    assert (stage_root / "etc/systemd/system/basic.target").read_text(encoding="utf-8") == "[Unit]\nDescription=Basic\n"
    assert (stage_root / "usr/lib/systemd/system/other.target").read_text(encoding="utf-8") == "[Unit]\nDescription=Other vendor target\n"


def test_staged_verify_creates_executable_stubs_for_exec_targets(world, tmp_path, monkeypatch):
    root, _executable, commands = world
    helper = make_executable(root, "/usr/bin/helper")
    root_owned(monkeypatch, helper)
    text = "[Service]\nExecStartPre=/usr/bin/helper\nExecStart=/usr/bin/example\n"
    resource = Unit(
        name="example.service",
        state="present",
        text=text,
        mode="0644",
        intent="authoritative",
        enable=False,
        on_change="none",
    )
    monkeypatch.setattr(unit_module.shutil, "which", lambda _name: "/usr/bin/systemd-analyze")
    planned = plan(Request("stub exec targets", (resource,)), root=root)
    original_copy = unit_module._copy_sources
    original_command = commands.__call__
    checked: list[str] = []

    def copy_with_existing_exec_target(selected_root, stage_root, sources):
        original_copy(selected_root, stage_root, sources)
        existing = stage_root / "usr/bin/example"
        existing.parent.mkdir(parents=True, exist_ok=True)
        existing.write_bytes(b"old staged content")
        existing.chmod(0o644)

    def fake_analyzer(argv, timeout):
        command = tuple(argv)
        if command[0] == "systemd-analyze":
            stage_root = Path(command[1].removeprefix("--root="))
            for executable in ("usr/bin/helper", "usr/bin/example"):
                target = stage_root / executable
                assert target.is_file()
                assert target.read_bytes() == b""
                assert target.stat().st_mode & 0o777 == 0o755
                checked.append(executable)
        return original_command(command, timeout)

    monkeypatch.setattr(unit_module, "_copy_sources", copy_with_existing_exec_target)
    monkeypatch.setattr(unit_module, "_run_command", fake_analyzer)
    resource.stage(SimpleNamespace(root=root, stage_dir=tmp_path / "staging"), planned.changes[0])
    assert set(checked) == {"usr/bin/example", "usr/bin/helper"}


def test_staged_verify_stubs_exec_targets_from_base_unit_for_dropin(world, monkeypatch):
    root, _executable, commands = world
    base = root / "usr/lib/systemd/system/example.service"
    base.parent.mkdir(parents=True)
    # The malformed second line is deliberately best-effort scanner input:
    # systemd-analyze owns validation of discovered vendor source text.
    base.write_text(
        "[Unit]\nDescription=Vendor example\n[Service]\n"
        "ExecStart=/usr/bin/example\nExecStart=relative\n"
        "ExecStop=/../../tmp/escape\n",
        encoding="utf-8",
    )
    resource = Unit(
        name="example.service",
        state="present",
        dropin="50-extra.conf",
        text="[Service]\nEnvironment=EXAMPLE=1\n",
        mode="0644",
        intent="authoritative",
        on_change="none",
    )
    monkeypatch.setattr(unit_module.shutil, "which", lambda _name: "/usr/bin/systemd-analyze")
    original_command = commands.__call__
    observed: list[Path] = []

    def fake_analyzer(argv, timeout):
        command = tuple(argv)
        if command[0] == "systemd-analyze":
            stage_root = Path(command[1].removeprefix("--root="))
            executable = stage_root / "usr/bin/example"
            assert executable.is_file()
            assert executable.read_bytes() == b""
            assert executable.stat().st_mode & 0o777 == 0o755
            assert not (stage_root.parent.parent / "tmp/escape").exists()
            observed.append(executable)
        return original_command(command, timeout)

    monkeypatch.setattr(unit_module, "_run_command", fake_analyzer)
    planned = plan(Request("drop-in over vendor unit", (resource,)), root=root)
    assert planned.has_changes
    assert len(observed) == 1


def test_source_exec_stub_scanner_skips_nonpaths_and_unstaged_sources(tmp_path):
    stage_root = tmp_path / "stage"
    stage_root.mkdir()

    unit_module._stage_source_exec_stubs(
        stage_root,
        [
            {"path": None},
            {"path": "/usr/lib/systemd/system/missing.service"},
        ],
    )


@pytest.mark.skipif(shutil.which("systemd-analyze") is None, reason="systemd-analyze is not installed")
def test_real_analyzer_verifies_dropin_over_vendor_unit_in_scratch_root(world, monkeypatch):
    root, _executable, _commands = world
    base = root / "usr/lib/systemd/system/example.service"
    base.parent.mkdir(parents=True)
    base.write_text(
        "[Unit]\nDescription=Vendor example\nDefaultDependencies=no\n"
        "[Service]\nType=oneshot\n"
        "ExecStart=/usr/bin/example\n",
        encoding="utf-8",
    )
    resource = Unit(
        name="example.service",
        state="present",
        dropin="50-extra.conf",
        text="[Service]\nEnvironment=EXAMPLE=1\n",
        mode="0644",
        intent="authoritative",
        on_change="none",
    )

    def run_real_analyzer(argv, timeout):
        result = subprocess.run(
            list(argv),
            check=False,
            capture_output=True,
            stdin=subprocess.DEVNULL,
            text=True,
            timeout=timeout,
        )
        if result.returncode:
            diagnostic = (result.stderr or result.stdout).strip()
            raise _UnitCommandError(
                f"exit {result.returncode}: {diagnostic or 'no diagnostic'}",
                returncode=result.returncode,
                stdout=result.stdout,
                stderr=result.stderr,
            )
        return result

    monkeypatch.setattr(unit_module, "_run_command", run_real_analyzer)
    planned = plan(Request("real vendor drop-in verify", (resource,)), root=root)
    assert planned.has_changes


@pytest.mark.parametrize(
    ("command", "prefix"),
    [
        ("//home/a/p", "/home"),
        ("//tmp/p", "/tmp"),
        ("///var/tmp/p", "/var/tmp"),
    ],
)
def test_double_leading_slash_cannot_bypass_exec_forbidden_prefix(world, monkeypatch, command, prefix):
    root, _executable, _commands = world
    target = make_executable(root, "/" + command.lstrip("/"))
    root_owned(monkeypatch, target)
    resource = Unit(
        name="example.service",
        state="present",
        text=service_text(command),
        mode="0644",
        intent="authoritative",
        enable=False,
        on_change="none",
    )
    with pytest.raises(PlanRefused, match=f"refusing executable below {prefix}"):
        plan(Request("double slash", (resource,)), root=root)


def test_changed_dependency_sources_verify_and_revert_as_one_transaction(world):
    root, _executable, _commands = world
    dependency_path = root / "etc/systemd/system/network.target"
    dependency_path.parent.mkdir(parents=True)
    dependency_before = "[Unit]\nDescription=Network before\n"
    dependency_path.write_text(dependency_before, encoding="utf-8")
    dependency_path.chmod(0o644)
    service_path = root / "etc/systemd/system/example.service"
    service_before = "[Unit]\nDescription=Example before\nAfter=network.target\n[Service]\nExecStart=/usr/bin/example\n"
    service_path.write_text(service_before, encoding="utf-8")
    service_path.chmod(0o644)
    request = Request(
        "update related units",
        (
            Unit(
                name="example.service",
                state="present",
                text=service_before.replace("Example before", "Example after"),
                mode="0644",
                intent="authoritative",
                enable=False,
                on_change="none",
            ),
            Unit(
                name="network.target",
                state="present",
                text="[Unit]\nDescription=Network after\n",
                mode="0644",
                intent="authoritative",
                enable=False,
                on_change="none",
            ),
        ),
    )
    applied = apply(request, root=root)
    assert applied.txn
    assert "Example after" in service_path.read_text(encoding="utf-8")
    assert "Network after" in dependency_path.read_text(encoding="utf-8")
    assert revert(applied.txn, root=root).txn
    assert service_path.read_text(encoding="utf-8") == service_before
    assert dependency_path.read_text(encoding="utf-8") == dependency_before


def test_staged_verify_preserves_masking_dropin_symlinks(world, tmp_path):
    root, _executable, commands = world
    base = root / "usr/lib/systemd/system/example.service"
    base.parent.mkdir(parents=True)
    base.write_text("[Service]\nExecStart=/usr/bin/example\n", encoding="utf-8")
    mask_dir = base.parent / "example.service.d"
    mask_dir.mkdir()
    (mask_dir / "30-mask.conf").symlink_to("/dev/null")
    resource = Unit(
        name="example.service",
        state="present",
        dropin="50-local.conf",
        text="[Service]\nNice=1\n",
        mode="0644",
        intent="fallback",
        on_change="none",
    )
    planned = plan(Request("masked source", (resource,)), root=root)
    resource.stage(SimpleNamespace(root=root, stage_dir=tmp_path / "staging"), planned.changes[0])
    verify = next(command for command in reversed(commands.calls) if command[0] == "systemd-analyze")
    stage_root = Path(verify[1].removeprefix("--root="))
    staged_mask = stage_root / "usr/lib/systemd/system/example.service.d/30-mask.conf"
    assert staged_mask.is_symlink() and os.readlink(staged_mask) == "../../../../../dev/null"


def test_a_changed_socket_restarts_but_a_noop_socket_does_not(world):
    root, _executable, commands = world
    socket_path = root / "etc/systemd/system/example.socket"
    socket_path.parent.mkdir(parents=True)
    old_text = "[Socket]\nListenStream=/run/example.sock\n"
    socket_path.write_text(old_text, encoding="utf-8")
    socket = Unit(name="example.socket", state="present", text=old_text, mode="0644", intent="authoritative", enable=True, on_change="restart")
    service = Unit(name="example.service", state="present", text=service_text(), mode="0644", intent="authoritative", enable=False, on_change="none")
    planned = plan(Request("both", (socket, service)), root=root)
    assert [action["name"] for action in planned.as_dict()["post_actions"]] == ["daemon-reload"]
    socket_new = Unit(name="example.socket", state="present", text=old_text + "FileDescriptorName=example\n", mode="0644", intent="authoritative", enable=False, on_change="restart")
    assert "unit restart example.socket" in [
        action["name"] for action in plan(Request("socket", (socket_new,)), root=root).as_dict()["post_actions"]
    ]
    socket_path.chmod(0o600)
    metadata_only = Unit(name="example.socket", state="present", text=old_text, mode="0640", intent="authoritative", enable=False, on_change="restart")
    assert "unit restart example.socket" not in [
        action["name"] for action in plan(Request("metadata", (metadata_only,)), root=root).as_dict()["post_actions"]
    ]
    dropin = root / "etc/systemd/system/example.socket.d/50-socket.conf"
    dropin.parent.mkdir(parents=True)
    dropin.write_text("[Socket]\nBacklog=16\n", encoding="utf-8")
    dropin.chmod(0o600)
    metadata_dropin = Unit(name="example.socket", state="present", dropin="50-socket.conf", text="[Socket]\nBacklog=16\n", mode="0640", intent="authoritative", on_change="restart")
    assert "unit restart example.socket" not in [
        action["name"] for action in plan(Request("metadata dropin", (metadata_dropin,)), root=root).as_dict()["post_actions"]
    ]


def test_socket_present_idempotent_absent_and_revert(world):
    root, _executable, commands = world
    socket_text = "[Socket]\nListenStream=/run/example.sock\n"
    socket = Unit(name="example.socket", state="present", text=socket_text, mode="0644", intent="authoritative", enable=True, on_change="restart")
    target = root / "etc/systemd/system/example.socket"
    first = apply(Request("create socket", (socket,)), root=root)
    assert target.read_text(encoding="utf-8") == socket_text
    assert not plan(Request("same socket", (socket,)), root=root).has_changes
    assert revert(first.txn, root=root).actions == (
        "unit stop example.socket: skipped(root)",
        "unit disable example.socket: skipped(root)",
        "daemon-reload: skipped(root)",
    )
    assert not target.exists()

    second = apply(Request("recreate socket", (socket,)), root=root)
    assert second.txn
    absent = apply(Request("remove socket", (Unit(name="example.socket", state="absent"),)), root=root)
    assert absent.changed == ("unit:/etc/systemd/system/example.socket",)
    assert not target.exists()
    assert absent.txn
    restored = revert(absent.txn, root=root)
    assert target.read_text(encoding="utf-8") == socket_text
    assert restored.actions == ("daemon-reload: skipped(root)", "unit reset-failed example.socket: skipped(root)")


@pytest.mark.parametrize(
    ("kind", "message"),
    [
        ("missing", "executable path component is missing"),
        ("not-executable", "executable bit is not set"),
        ("owner", "path component is not root-owned"),
        ("writable-component", "group/other-writable component"),
        ("tmp", "refusing executable below /tmp"),
        ("group-writable", "group/other-writable component"),
    ],
)
def test_exec_check_refusals(tmp_path, commands, monkeypatch, kind, message):
    root = fake_root(tmp_path)
    command = "/usr/bin/example"
    target = make_executable(root, command)
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
    elif kind == "owner":
        original = unit_module._lstat

        def wrong_owner(path):
            info = original(path)
            if path == target:
                return SimpleNamespace(st_mode=info.st_mode, st_uid=os.geteuid() + 1, st_gid=info.st_gid)
            return info

        monkeypatch.setattr(unit_module, "_lstat", wrong_owner)
    resource = Unit(name="example.service", state="present", text=service_text(command), mode="0644", intent="authoritative", enable=False, on_change="none")
    with pytest.raises(Exception, match=message):
        plan(Request("bad exec", (resource,)), root=root)


def test_verify_failure_aborts_before_a_managed_write(world, monkeypatch):
    root, _executable, commands = world
    resource = Unit(name="example.service", state="present", text=service_text(), mode="0644", intent="authoritative", enable=False, on_change="none")
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


def test_commit_rechecks_managed_unit_path_after_staging(world, tmp_path, monkeypatch):
    root, _executable, _commands = world
    resource = Unit(
        name="example.service",
        state="present",
        text="[Unit]\nDescription=Example\n",
        mode="0644",
        intent="authoritative",
        enable=False,
        on_change="none",
    )
    planned = plan(Request("recheck unit path", (resource,)), root=root)
    context = SimpleNamespace(root=root, stage_dir=tmp_path / "staging")
    staged = resource.stage(context, planned.changes[0])
    calls: list[tuple[Path, str]] = []
    original_validate = unit_module._validate_unit_path
    original_atomic_write = _fs.atomic_write

    def track_validation(selected_root, path):
        calls.append((selected_root, path))
        return original_validate(selected_root, path)

    def write_then_make_group_writable(selected_root, path, data, mode, **kwargs):
        result = original_atomic_write(selected_root, path, data, mode, **kwargs)
        path.chmod(path.stat().st_mode | 0o020)
        return result

    monkeypatch.setattr(unit_module, "_validate_unit_path", track_validation)
    monkeypatch.setattr(_fs, "atomic_write", write_then_make_group_writable)
    # Keep the later observe() from supplying a second, unrelated path check if
    # the explicit post-write validation in commit is removed.
    monkeypatch.setattr(unit_module, "_observe_unit", lambda *_args: {})
    with pytest.raises(PlanRefused, match="group/other-writable"):
        resource.commit(context, staged)
    assert calls == [(root, resource.path), (root, resource.path)]
    target = root / resource.path.lstrip("/")
    assert target.read_bytes() == staged.data
    assert target.stat().st_mode & 0o020


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
        intent="authoritative",
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
    resource = Unit(name="example.service", state="present", text=service_text(), mode="0644", intent="authoritative", enable=False, on_change=on_change)
    names = [action["name"] for action in plan(Request("change", (resource,)), root=root).as_dict()["post_actions"]]
    assert names == ["daemon-reload"] + ([runtime_name] if runtime_name else [])


def test_pending_records_the_action_in_the_journal_without_running_it(world):
    root, _executable, commands = world
    resource = Unit(name="example.service", state="present", text=service_text(), mode="0644", intent="authoritative", enable=False, on_change="pending", preflight=("/usr/bin/preflight",))
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
    assert restored.pending == ()
    assert [item["name"] for item in journal(restored.txn, root=root)["actions"]] == [
        "unit stop example.service",
        "unit disable example.service",
        "daemon-reload",
    ]


def test_daemon_reload_is_declared_once_for_multiple_changed_units(world):
    root, _executable, commands = world
    one = Unit(name="one.service", state="present", text=service_text(), mode="0644", intent="authoritative", enable=False, on_change="none")
    two = Unit(name="two.service", state="present", text=service_text(), mode="0644", intent="authoritative", enable=False, on_change="none")
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
    original_action = transactions_module._run_post_action

    def record_unlink(managed_root, path):
        events.append("delete")
        return original_unlink(managed_root, path)

    def record_action(managed_root, action):
        if action.argv[1] in {"stop", "disable"}:
            events.append(action.argv[1])
        return original_action(managed_root, action)

    monkeypatch.setattr(transactions_module, "_run_post_action", record_action)
    monkeypatch.setattr(unit_module._fs, "unlink_and_sync", record_unlink)
    resource = Unit(name="example.service", state="absent")
    planned = plan(Request("remove", (resource,)), root=root)
    apply(planned)
    assert events == ["stop", "disable", "delete"]
    assert not target.exists()


def test_failure_after_commit_is_journaled(world):
    root, _executable, commands = world
    resource = Unit(name="example.service", state="present", text=service_text(), mode="0644", intent="authoritative", enable=False, on_change="none")
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


def test_absent_unit_substeps_are_journaled_before_failure(world, monkeypatch):
    root, _executable, _commands = world
    target = root / "etc/systemd/system/example.service"
    target.parent.mkdir(parents=True)
    target.write_text(service_text(), encoding="utf-8")
    executed = []

    def action(_root, post_action):
        executed.append(post_action.argv[1])
        if post_action.argv[1] == "disable":
            raise RuntimeError("disable failed")
        return f"{post_action.name}: done"

    monkeypatch.setattr(transactions_module, "_run_post_action", action)
    from augeaswrap import ApplyFailed

    with pytest.raises(ApplyFailed) as caught:
        apply(Request("remove", (Unit(name="example.service", state="absent"),)), root=root)
    record = journal(caught.value.txn, root=root)
    assert executed == ["stop", "disable"]
    assert [step["status"] for step in record["steps"][:2]] == ["done", "failed"]
    assert [action["status"] for action in record["actions"]] == ["done", "failed", "not-run", "not-run"]
    assert target.exists()


def test_interrupted_absent_unit_commit_records_postimage_and_reverts(tmp_path, monkeypatch):
    root = fake_root(tmp_path)
    target = root / "etc/systemd/system/example.service"
    first_target = root / "etc/systemd/system/first.service"
    target.parent.mkdir(parents=True)
    target.write_text(service_text(), encoding="utf-8")
    target.chmod(0o644)
    executable = make_executable(root, "/usr/bin/example")
    root_owned(monkeypatch, executable)
    monkeypatch.setattr(unit_module.shutil, "which", lambda _name: None)
    original_observe = Unit.observe
    interrupted = False

    def interrupt_after_unlink(resource, context):
        nonlocal interrupted
        observed = original_observe(resource, context)
        if resource.name == "example.service" and resource.state == "absent" and not target.exists() and not interrupted:
            interrupted = True
            raise KeyboardInterrupt
        return observed

    monkeypatch.setattr(Unit, "observe", interrupt_after_unlink)

    def mutate_first_during_stop(_root, action):
        if action.argv[1] == "stop":
            first_target.write_text("[Unit]\nDescription=Updated by stop\n", encoding="utf-8")
        return f"{action.name}: done"

    monkeypatch.setattr(transactions_module, "_run_post_action", mutate_first_during_stop)
    request = Request(
        "interrupt during removal",
        (
            Unit(
                name="first.service",
                state="present",
                text="[Unit]\nDescription=First\n",
                mode="0644",
                intent="authoritative",
                enable=False,
                on_change="none",
            ),
            Unit(name="example.service", state="absent"),
        ),
    )
    with pytest.raises(KeyboardInterrupt):
        apply(request, root=root)

    record = journal(root=root)[0]
    assert record["status"] == "interrupted"
    assert record["post_images"]["unit:/etc/systemd/system/example.service"]
    assert record["post_images"]["unit:/etc/systemd/system/first.service"]
    assert "Updated by stop" in first_target.read_text(encoding="utf-8")
    assert not target.exists()
    reverted = revert(record["id"], root=root)
    assert reverted.txn
    assert target.read_text(encoding="utf-8") == service_text()
    assert not first_target.exists()


def test_unit_stale_revert_and_later_transaction_refusals(world):
    root, _executable, commands = world
    resource = Unit(name="example.service", state="present", text=service_text(description="One"), mode="0644", intent="authoritative", enable=False, on_change="none")
    first = apply(Request("one", (resource,)), root=root)
    target = root / "etc/systemd/system/example.service"
    target.write_text(service_text(description="Drift"), encoding="utf-8")
    with pytest.raises(Exception, match="changed outside Augeaswrap"):
        revert(first.txn, root=root)
    target.write_text(service_text(description="One"), encoding="utf-8")
    apply(
        Request(
            "two",
            (Unit(name="example.service", state="present", text=service_text(description="Two"), mode="0644", intent="authoritative", enable=False, on_change="none"),),
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
    dropin = Unit(name="example.service", state="present", dropin="50-a.conf", text="[Service]\nNice=1\n", mode="0644", intent="authoritative", on_change="none")
    planned = plan(Request("drop-in", (dropin,)), root=root)
    base.write_text(service_text(description="Changed"), encoding="utf-8")
    with pytest.raises(Exception, match="changed after plan"):
        apply(planned)


def test_loader_round_trip_and_strict_shape_validation(world):
    root, _executable, commands = world
    resources = (
        Unit(name="example.service", state="present", text=service_text(), mode="0644", intent="authoritative", enable=True, on_change="restart", preflight=("/usr/bin/preflight",)),
        Unit(name="example.service", state="present", dropin="50-a.conf", text="[Service]\nNice=1\n", mode="0644", intent="fallback", on_change="pending"),
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
    with pytest.raises(ValueError, match="missing unit field"):
        load_request({"augeaswrap": 1, "writer": "w", "unit": [{"name": "x.service", "state": "present", "text": "x", "mode": "0644", "enable": False, "on_change": "none"}]})


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"name": "bad", "state": "absent"}, "known systemd suffix"),
        ({"name": "bad/service", "state": "absent"}, "path separator"),
        ({"name": "../bad.service", "state": "absent"}, "path separator"),
        ({"name": "-bad.service", "state": "absent"}, "begin with a dash"),
        ({"name": "bad$name.service", "state": "absent"}, "unsupported character"),
        ({"name": "x.service", "state": "maybe"}, "unit state"),
        ({"name": "x.service", "state": "present", "text": "x", "mode": "0644", "enable": True, "on_change": "restart"}, "intent is required"),
        ({"name": "x.service", "state": "present", "text": "x", "mode": "0644", "intent": "authoritative", "enable": True}, "on_change"),
        ({"name": "x.service", "state": "present", "text": "x", "mode": "0644", "intent": "authoritative", "enable": 1, "on_change": "none"}, "enable"),
        ({"name": "x.service", "state": "present", "dropin": "50-x.conf", "text": "x", "mode": "0644", "on_change": "none"}, "intent is required"),
        ({"name": "x.service", "state": "absent", "text": "x"}, "must not specify"),
        ({"name": "x.service", "state": "absent", "intent": "authoritative"}, "must not specify intent"),
        ({"name": "x.service", "state": "present", "dropin": "../x.conf", "text": "x", "mode": "0644", "intent": "authoritative", "on_change": "none"}, "path separator"),
        ({"name": "x.service", "state": "present", "dropin": "x", "text": "x", "mode": "0644", "intent": "authoritative", "on_change": "none"}, "conf basename"),
        ({"name": "x.service", "state": "present", "dropin": "50-x.conf", "text": "x", "mode": "0644", "intent": "authoritative", "enable": True, "on_change": "none"}, "enable"),
        ({"name": "x.service", "state": "absent", "dropin": "50-x.conf"}, "requires on_change"),
        ({"name": "x.service", "state": "absent", "dropin": "50-x.conf", "on_change": "none", "intent": "fallback"}, "forbids intent"),
        ({"name": "x.service", "state": "present", "text": "x", "mode": "0644", "intent": "authoritative", "enable": False, "on_change": "bad"}, "on_change"),
        ({"name": "x.service", "state": "present", "text": "x", "mode": "0644", "intent": "unknown", "enable": False, "on_change": "none"}, "intent"),
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
        ("[Service]\nExecStart=/usr/bin/%h\n", "not expanded"),
        ("[Service]\nExecStart=/usr/bin/$BINARY\n", "not expanded"),
    ]:
        resource = Unit(name="example.service", state="present", text=text, mode="0644", intent="authoritative", enable=False, on_change="none")
        with pytest.raises(Exception, match=message):
            plan(Request("bad", (resource,)), root=root)


def test_exec_path_is_normalized_before_forbidden_prefix_checks(world):
    root, _executable, _commands = world
    resource = Unit(name="example.service", state="present", text=service_text("/usr/../home/x"), mode="0644", intent="authoritative", enable=False, on_change="none")
    with pytest.raises(PlanRefused, match="must not contain '..'"):
        plan(Request("traversal", (resource,)), root=root)

    forbidden = Unit(name="home.service", state="present", text=service_text("/home/x"), mode="0644", intent="authoritative", enable=False, on_change="none")
    with pytest.raises(PlanRefused, match="refusing executable below /home"):
        plan(Request("forbidden prefix", (forbidden,)), root=root)


def test_symlinked_exec_path_is_refused_after_root_containment_check(world, monkeypatch):
    root, _executable, _commands = world
    target = root / "usr/bin/example"
    home_exec = make_executable(root, "/home/example")
    target.unlink()
    target.symlink_to(home_exec)
    root_owned(monkeypatch, home_exec)
    resource = Unit(name="example.service", state="present", text=service_text(), mode="0644", intent="authoritative", enable=False, on_change="none")
    with pytest.raises(PlanRefused, match="symlink component"):
        plan(Request("symlink exec", (resource,)), root=root)


def test_exec_symlink_resolution_cannot_leave_selected_root(world):
    root, _executable, _commands = world
    target = root / "usr/bin/example"
    target.unlink()
    target.symlink_to("/../../etc/passwd")
    resource = Unit(name="example.service", state="present", text=service_text(), mode="0644", intent="authoritative", enable=False, on_change="none")
    with pytest.raises(PlanRefused, match="resolves outside the selected root|cannot resolve executable path"):
        plan(Request("escaping symlink", (resource,)), root=root)


def test_unit_text_rejects_nul_and_enforces_utf8_byte_limit():
    base = {"name": "example.service", "state": "present", "mode": "0644", "intent": "authoritative", "enable": False, "on_change": "none"}
    with pytest.raises(ValueError, match="must not contain NUL"):
        Unit(**base, text="[Unit]\x00\n")
    with pytest.raises(ValueError, match="must not contain NUL"):
        Unit(name="example.service", state="present", dropin="50-host.conf", text="[Unit]\x00\n", mode="0644", intent="fallback", on_change="none")
    with pytest.raises(ValueError, match="1048576-byte limit"):
        Unit(**base, text="x" * (unit_module._UNIT_TEXT_LIMIT + 1))
    with pytest.raises(ValueError, match="1048576-byte limit"):
        Unit(
            name="example.service",
            state="present",
            dropin="50-host.conf",
            text="x" * (unit_module._UNIT_TEXT_LIMIT + 1),
            mode="0644",
            intent="fallback",
            on_change="none",
        )
    assert Unit(**base, text="x" * unit_module._UNIT_TEXT_LIMIT).text is not None
    assert Unit(**base, text="é" * (unit_module._UNIT_TEXT_LIMIT // 2)).text is not None
    with pytest.raises(ValueError, match="1048576-byte limit"):
        Unit(**base, text="é" * (unit_module._UNIT_TEXT_LIMIT // 2 + 1))


def test_unit_mode_refuses_group_other_write_and_special_bits():
    for mode, message in [("0664", "group/other-writable"), ("4755", "special permission bits")]:
        with pytest.raises(ValueError, match=message):
            Unit(name="example.service", state="present", text="[Unit]\n", mode=mode, intent="authoritative", enable=False, on_change="none")


def test_template_unit_names_are_supported_with_case_sensitive_suffixes():
    assert Unit(name="worker@.service", state="absent").name == "worker@.service"
    with pytest.raises(ValueError, match="known systemd suffix"):
        Unit(name="worker@.SERVICE", state="absent")


def test_existing_managed_unit_directory_chain_must_be_safe(world):
    root, _executable, _commands = world
    target = root / "etc/systemd/system/example.service"
    target.parent.mkdir(parents=True)
    target.write_text(service_text(), encoding="utf-8")
    target.chmod(0o644)
    target.parent.chmod(0o775)
    resource = Unit(name="example.service", state="present", text=service_text(description="New"), mode="0644", intent="authoritative", enable=False, on_change="none")
    with pytest.raises(PlanRefused, match="directory is group/other-writable"):
        plan(Request("unsafe parent", (resource,)), root=root)


def test_existing_managed_unit_must_have_expected_owner(world, monkeypatch):
    root, _executable, _commands = world
    target = root / "etc/systemd/system/example.service"
    target.parent.mkdir(parents=True)
    target.write_text(service_text(), encoding="utf-8")
    target.chmod(0o644)
    original = unit_module._lstat

    def wrong_owner(path):
        info = original(path)
        if path == target:
            return SimpleNamespace(st_mode=info.st_mode, st_uid=os.geteuid() + 1, st_gid=info.st_gid)
        return info

    monkeypatch.setattr(unit_module, "_lstat", wrong_owner)
    resource = Unit(name="example.service", state="present", text=service_text(description="New"), mode="0644", intent="authoritative", enable=False, on_change="none")
    with pytest.raises(PlanRefused, match="expected uid"):
        plan(Request("wrong owner", (resource,)), root=root)


def test_existing_managed_unit_parent_must_have_expected_owner(world, monkeypatch):
    root, _executable, _commands = world
    target = root / "etc/systemd/system/example.service"
    target.parent.mkdir(parents=True)
    target.write_text(service_text(), encoding="utf-8")
    target.chmod(0o644)
    parent = target.parent
    original = unit_module._lstat

    def wrong_parent_owner(path):
        info = original(path)
        if path == parent:
            return SimpleNamespace(st_mode=info.st_mode, st_uid=os.geteuid() + 1, st_gid=info.st_gid)
        return info

    monkeypatch.setattr(unit_module, "_lstat", wrong_parent_owner)
    resource = Unit(name="example.service", state="present", text=service_text(description="New"), mode="0644", intent="authoritative", enable=False, on_change="none")
    with pytest.raises(PlanRefused, match="expected uid"):
        plan(Request("wrong parent owner", (resource,)), root=root)


@pytest.mark.parametrize(
    ("part", "bits", "message"),
    [
        ("file", 0o020, "group/other-writable"),
        ("file", 0o4000, "special permission bits"),
        ("directory", 0o020, "group/other-writable"),
        ("directory", 0o2000, "special permission bits"),
    ],
)
def test_existing_managed_unit_file_and_directories_refuse_unsafe_modes(world, monkeypatch, part, bits, message):
    root, _executable, _commands = world
    target = root / "etc/systemd/system/example.service"
    target.parent.mkdir(parents=True)
    target.write_text(service_text(), encoding="utf-8")
    target.chmod(0o644)
    unsafe_path = target if part == "file" else target.parent
    original = unit_module._lstat

    def unsafe_mode(path):
        info = original(path)
        if path == unsafe_path:
            return SimpleNamespace(st_mode=info.st_mode | bits, st_uid=info.st_uid, st_gid=info.st_gid)
        return info

    monkeypatch.setattr(unit_module, "_lstat", unsafe_mode)
    resource = Unit(name="example.service", state="present", text=service_text(description="New"), mode="0644", intent="authoritative", enable=False, on_change="none")
    with pytest.raises(PlanRefused, match=message):
        plan(Request("unsafe mode", (resource,)), root=root)


def test_symlinked_unit_target_is_refused_by_the_fake_root(world):
    root, _executable, commands = world
    target = root / "etc/systemd/system/example.service"
    target.parent.mkdir(parents=True)
    elsewhere = root / "outside"
    elsewhere.write_text("x", encoding="utf-8")
    target.symlink_to(elsewhere)
    resource = Unit(name="example.service", state="present", text=service_text(), mode="0644", intent="authoritative", enable=False, on_change="none")
    with pytest.raises(Exception, match="symlink"):
        plan(Request("symlink", (resource,)), root=root)


def test_symlinked_unit_parent_directory_is_refused_by_the_fake_root(world):
    root, _executable, _commands = world
    (root / "etc/systemd").mkdir(parents=True)
    elsewhere = root / "elsewhere"
    elsewhere.mkdir()
    (root / "etc/systemd/system").symlink_to(elsewhere, target_is_directory=True)
    resource = Unit(name="example.service", state="present", text=service_text(), mode="0644", intent="authoritative", enable=False, on_change="none")
    with pytest.raises(Exception, match="symlink"):
        plan(Request("symlink parent", (resource,)), root=root)


def test_invalid_restored_preimage_refuses_before_restore(world):
    root, _executable, commands = world
    result = apply(
        Request(
            "one",
            (Unit(name="example.service", state="present", text=service_text(), mode="0644", intent="authoritative", enable=False, on_change="none"),),
        ),
        root=root,
    )
    pre = next((root / "var/lib/augeaswrap/txn" / result.txn / "pre").glob("*.json"))
    record = json.loads(pre.read_text(encoding="utf-8"))
    record["exists"] = "maybe"
    pre.write_text(json.dumps(record), encoding="utf-8")
    with pytest.raises(Exception, match="exists flag"):
        revert(result.txn, root=root)


def test_shadow_check_captures_layers_without_generic_directive_analysis(monkeypatch, tmp_path):
    resource = Unit(name="example.service", state="absent")
    observed = []
    monkeypatch.setattr(unit_module.shadow, "observe_layers", lambda root, target_class: observed.append(target_class))

    assert unit_module.shadow_check(resource, tmp_path) == []
    assert observed[0].directories == tuple(
        f"{directory}/example.service.d" for directory in unit_module._UNIT_SEARCH_DIRS
    )


def test_registered_shadow_hook_runs_during_planning(tmp_path, monkeypatch):
    spec = engine_module.kind_spec("unit")
    assert spec is not None and spec.shadow_check is unit_module.shadow_check
    calls = []

    def refusing(resource, root):
        calls.append((resource, root))
        return [Problem("unit.shadow", "blocked by a test shadow")]

    original = engine_module.kind_spec
    monkeypatch.setattr(
        engine_module,
        "kind_spec",
        lambda kind: replace(spec, shadow_check=refusing) if kind == "unit" else original(kind),
    )
    root = fake_root(tmp_path)
    with pytest.raises(PlanRefused, match="blocked by a test shadow"):
        plan(Request("shadow", (Unit(name="example.service", state="absent"),)), root=root)
    assert len(calls) == 1 and isinstance(calls[0][0], Unit) and calls[0][1] == root


@pytest.mark.parametrize("invalid_result", [(), [object()]])
def test_registered_shadow_hook_rejects_invalid_return_values(tmp_path, monkeypatch, invalid_result):
    spec = engine_module.kind_spec("unit")
    assert spec is not None
    original = engine_module.kind_spec
    monkeypatch.setattr(
        engine_module,
        "kind_spec",
        lambda kind: replace(spec, shadow_check=lambda _resource, _root: invalid_result)
        if kind == "unit"
        else original(kind),
    )
    with pytest.raises(PlanRefused, match="shadow_check must return list"):
        plan(Request("bad shadow hook", (Unit(name="example.service", state="absent"),)), root=fake_root(tmp_path))


def test_preflight_success_is_recorded_with_its_bounded_timeout(world):
    root, _executable, commands = world
    preflight = ("/usr/bin/preflight", "--ok")
    resource = Unit(
        name="example.service",
        state="present",
        text=service_text(),
        mode="0644",
        intent="authoritative",
        enable=False,
        on_change="none",
        preflight=preflight,
    )
    apply(Request("validated", (resource,)), root=root)
    assert preflight in commands.calls
    assert commands.timeouts[commands.calls.index(preflight)] == 30.0


def test_owner_and_group_are_resolved_and_preserved(world):
    root, _executable, commands = world
    assert unit_module._unit_owner_ids(Path("/")) == (0, 0)
    target = root / "etc/systemd/system/example.service"
    target.parent.mkdir(parents=True)
    target.write_text("old", encoding="utf-8")
    target.chmod(0o600)
    resource = Unit(name="example.service", state="present", text=service_text(), mode="0644", intent="authoritative", enable=False, on_change="none")
    planned = plan(Request("metadata", (resource,)), root=root)
    desired = planned.changes[0].after["target"]
    assert desired["uid"] == target.stat().st_uid and desired["gid"] == target.stat().st_gid


@pytest.mark.parametrize(
    ("live_status", "want_enabled", "expected_action"),
    [
        ("disabled", True, "unit enable example.service"),
        ("enabled", True, None),
        ("enabled", False, "unit disable example.service"),
        ("disabled", False, None),
    ],
)
def test_enable_is_reconciled_as_declared_state(world, monkeypatch, live_status, want_enabled, expected_action):
    root, _executable, commands = world
    target = root / "etc/systemd/system/example.service"
    target.parent.mkdir(parents=True)
    target.write_text(service_text(), encoding="utf-8")
    target.chmod(0o644)
    monkeypatch.setattr(unit_module, "_is_live_root", lambda _root: True)
    is_enabled = ("systemctl", "is-enabled", "--", "example.service")
    commands.outcomes[is_enabled] = subprocess.CompletedProcess(
        is_enabled,
        0 if live_status == "enabled" else 1,
        live_status + "\n",
        "",
    )
    resource = Unit(
        name="example.service",
        state="present",
        text=service_text(),
        mode="0644",
        intent="authoritative",
        enable=want_enabled,
        on_change="none",
    )
    planned = plan(Request("enable state", (resource,)), root=root)
    actions = [item["name"] for item in planned.as_dict()["post_actions"]]
    assert actions == ([expected_action] if expected_action else [])
    assert is_enabled in commands.calls


def test_live_enablement_postimage_tracks_post_action_state(world, monkeypatch):
    root, _executable, commands = world
    target = root / "etc/systemd/system/example.service"
    target.parent.mkdir(parents=True)
    target.write_text(service_text(), encoding="utf-8")
    target.chmod(0o644)
    status = {"value": "disabled"}
    is_enabled = ("systemctl", "is-enabled", "--", "example.service")

    def run_command(argv, timeout):
        command = tuple(argv)
        commands.calls.append(command)
        commands.timeouts.append(timeout)
        if command == is_enabled:
            value = status["value"]
            return subprocess.CompletedProcess(command, 0 if value == "enabled" else 1, value + "\n", "")
        return subprocess.CompletedProcess(command, 0, "", "")

    def run_action(_root, action):
        if action.argv[1] == "enable":
            status["value"] = "enabled"
        elif action.argv[1] == "disable":
            status["value"] = "disabled"
        return f"{action.name}: done"

    monkeypatch.setattr(unit_module, "_is_live_root", lambda _root: True)
    monkeypatch.setattr(unit_module, "_run_command", run_command)
    monkeypatch.setattr(transactions_module, "_run_post_action", run_action)
    resource = Unit(
        name="example.service",
        state="present",
        text=service_text(),
        mode="0644",
        intent="authoritative",
        enable=True,
        on_change="none",
    )
    created = apply(Request("reconcile enabled state", (resource,)), root=root)
    assert created.actions == ("unit enable example.service: done",)
    assert status["value"] == "enabled"

    restored = revert(created.txn, root=root)
    assert restored.actions == ("unit disable example.service: done",)
    assert status["value"] == "disabled"


@pytest.mark.parametrize(
    ("status", "returncode", "expected"),
    [
        ("enabled", 0, True),
        ("enabled-runtime", 0, True),
        ("disabled", 1, False),
        ("linked", 1, False),
        ("masked", 1, False),
        ("static", 0, False),
        ("not-found", 4, False),
    ],
)
def test_unit_enabled_classifies_systemctl_status(world, monkeypatch, status, returncode, expected):
    _root, _executable, commands = world
    monkeypatch.setattr(unit_module, "_is_live_root", lambda _root: True)
    argv = ("systemctl", "is-enabled", "--", "example.service")
    commands.outcomes[argv] = subprocess.CompletedProcess(argv, returncode, status + "\n", "")
    assert unit_module._unit_enabled(Path("/"), "example.service") is expected


def test_unit_enabled_refuses_unexpected_and_inconsistent_statuses(world, monkeypatch):
    _root, _executable, commands = world
    monkeypatch.setattr(unit_module, "_is_live_root", lambda _root: True)
    argv = ("systemctl", "is-enabled", "--", "example.service")
    commands.outcomes[argv] = subprocess.CompletedProcess(argv, 1, "mystery\n", "")
    with pytest.raises(_UnitCommandError, match="unexpected systemctl is-enabled result"):
        unit_module._unit_enabled(Path("/"), "example.service")
    commands.outcomes[argv] = subprocess.CompletedProcess(argv, 1, "enabled\n", "")
    with pytest.raises(_UnitCommandError, match="inconsistent systemctl is-enabled result"):
        unit_module._unit_enabled(Path("/"), "example.service")
    commands.outcomes[argv] = subprocess.CompletedProcess(argv, 0, "", "")
    with pytest.raises(_UnitCommandError, match="unexpected systemctl is-enabled result"):
        unit_module._unit_enabled(Path("/"), "example.service")


@pytest.mark.parametrize("returncode", [1, 4])
def test_missing_live_unit_with_empty_is_enabled_output_plans_enable(world, monkeypatch, returncode):
    root, _executable, commands = world
    monkeypatch.setattr(unit_module, "_is_live_root", lambda _root: True)
    is_enabled = ("systemctl", "is-enabled", "--", "new.service")
    original_command = commands.__call__

    def command(argv, timeout):
        if tuple(argv) == is_enabled:
            raise _UnitCommandError(f"exit {returncode}: no diagnostic", returncode=returncode, stdout="")
        return original_command(argv, timeout)

    monkeypatch.setattr(unit_module, "_run_command", command)
    resource = Unit(
        name="new.service",
        state="present",
        text="[Unit]\nDescription=New unit\n",
        mode="0644",
        intent="authoritative",
        enable=True,
        on_change="none",
    )
    actions = plan(Request("new live unit", (resource,)), root=root).as_dict()["post_actions"]
    assert [item["argv"] for item in actions if item["name"] == "unit enable new.service"] == [
        ["systemctl", "enable", "--", "new.service"]
    ]


def test_absent_unit_desired_enablement_is_false_on_a_live_root(monkeypatch):
    monkeypatch.setattr(unit_module, "_is_live_root", lambda _root: True)
    resource = Unit(name="old.service", state="absent")
    desired = resource._desired(
        SimpleNamespace(root=Path("/")),
        {"target": {"exists": False, "fingerprint": "absent"}, "enabled": None, "sources": []},
    )
    assert desired["enabled"] is False


def test_live_enablement_reconcile_action_has_option_terminator(world, monkeypatch):
    root, _executable, commands = world
    monkeypatch.setattr(unit_module, "_is_live_root", lambda _root: True)
    is_enabled = ("systemctl", "is-enabled", "--", "example.service")
    commands.outcomes[is_enabled] = subprocess.CompletedProcess(is_enabled, 1, "disabled\n", "")
    resource = Unit(
        name="example.service",
        state="present",
        text=service_text(),
        mode="0644",
        intent="authoritative",
        enable=True,
        on_change="none",
    )
    actions = plan(Request("enable argv", (resource,)), root=root).as_dict()["post_actions"]
    assert [item["argv"] for item in actions if item["name"] == "unit enable example.service"] == [
        ["systemctl", "enable", "--", "example.service"]
    ]


def test_unit_verification_compares_post_commit_enablement(monkeypatch):
    resource = Unit(
        name="example.service",
        state="present",
        text="[Unit]\n",
        mode="0644",
        intent="authoritative",
        enable=True,
        on_change="none",
    )
    address = resource.addresses()[0]
    target = {"exists": True, "sha256": "same", "mode": 0o644, "uid": 0, "gid": 0}
    expected = {
        "path": resource.path,
        "target": target,
        "enabled": True,
        "exec_paths": [],
        "sources": [],
    }
    actual = {**expected, "enabled": False}
    staged = unit_module._StagedUnit(True, b"", 0o644, 0, 0, expected, True, resource.name, resource.path, None)
    monkeypatch.setattr(Unit, "observe", lambda _self, _ctx: {address: actual})
    problems = resource.verify(SimpleNamespace(expected={address: staged}))
    assert [problem.code for problem in problems] == ["unit.verify.failed"]


def test_all_systemctl_unit_arguments_are_option_terminated(world):
    root, _executable, _commands = world
    resource = Unit(name="example.service", state="present", text=service_text(), mode="0644", intent="authoritative", enable=True, on_change="restart")
    action_argv = [item["argv"] for item in plan(Request("argv", (resource,)), root=root).as_dict()["post_actions"]]
    assert action_argv == [
        ["systemctl", "daemon-reload"],
        ["systemctl", "enable", "--", "example.service"],
        ["systemctl", "restart", "--", "example.service"],
    ]
    existing = root / "etc/systemd/system/example.service"
    existing.parent.mkdir(parents=True)
    existing.write_text(service_text(), encoding="utf-8")
    existing.chmod(0o644)
    absent = Unit(name="example.service", state="absent")
    removal_argv = [item["argv"] for item in plan(Request("remove", (absent,)), root=root).as_dict()["post_actions"]]
    assert removal_argv == [
        ["systemctl", "stop", "--", "example.service"],
        ["systemctl", "disable", "--", "example.service"],
        ["systemctl", "daemon-reload"],
        ["systemctl", "reset-failed", "--", "example.service"],
    ]


def test_post_action_validation_rejects_bad_pending_value():
    from augeaswrap import PostAction

    with pytest.raises(ValueError, match="pending flag"):
        PostAction("x", ("true",), 1, pending=1)


def test_conflicting_pending_declarations_are_refused():
    from augeaswrap import PostAction

    first = SimpleNamespace(kind="fake", post_actions=lambda _changes: [PostAction("same", ("true",), 1, pending=True, phase="runtime")])
    second = SimpleNamespace(kind="fake", post_actions=lambda _changes: [PostAction("same", ("true",), 1, pending=False, phase="runtime")])
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


def test_command_runner_forwards_the_requested_timeout(monkeypatch):
    captured = {}

    def run(argv, **kwargs):
        captured.update(kwargs)
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(unit_module.subprocess, "run", run)
    unit_module._run_command(("bounded",), 7.5)
    assert captured["timeout"] == 7.5


def test_source_inventory_ignores_non_conf_and_target_dropin(world):
    root, _executable, commands = world
    directory = root / "etc/systemd/system/example.service.d"
    directory.mkdir(parents=True)
    (directory / "README").write_text("not a drop-in", encoding="utf-8")
    (directory / "50-target.conf").write_text("target", encoding="utf-8")
    inventory = unit_module._source_inventory(root, "example.service", "/etc/systemd/system/example.service.d/50-target.conf", "50-target.conf")
    assert inventory == []


def test_a_unit_dropin_layer_change_after_plan_is_stale(world, monkeypatch):
    root, _executable, _commands = world
    monkeypatch.setattr(unit_module, "_source_inventory", lambda *_args: [])
    layer = root / "usr/lib/systemd/system/example.service.d/10-vendor.conf"
    layer.parent.mkdir(parents=True)
    layer.write_text("[Service]\nEnvironment=MODE=one\n", encoding="utf-8")
    resource = Unit(
        name="example.service",
        state="present",
        text=service_text(),
        mode="0644",
        intent="fallback",
        enable=False,
        on_change="none",
    )

    planned = plan(Request("stale unit layers", (resource,)), root=root)
    layer.write_text("[Service]\nEnvironment=MODE=two\n", encoding="utf-8")
    with pytest.raises(StalePlan, match="changed after plan"):
        apply(planned)


def test_unit_render_shows_a_unified_diff(world):
    root, _executable, commands = world
    target = root / "etc/systemd/system/example.service"
    target.parent.mkdir(parents=True)
    target.write_text("old\n", encoding="utf-8")
    resource = Unit(name="example.service", state="present", text="new\n", mode="0644", intent="authoritative", enable=False, on_change="none")
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
    root_owned(monkeypatch, target)
    resource = Unit(name="example.service", state="present", text=service_text(command), mode="0644", intent="authoritative", enable=False, on_change="none")
    with pytest.raises(Exception, match="not a regular file"):
        plan(Request("directory", (resource,)), root=root)

    missing = root / "usr/bin/error"
    original = unit_module._lstat

    def inspect(path):
        if path == missing:
            raise OSError("inspection failed")
        return original(path)

    monkeypatch.setattr(unit_module, "_lstat", inspect)
    resource = Unit(name="error.service", state="present", text=service_text("/usr/bin/error"), mode="0644", intent="authoritative", enable=False, on_change="none")
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
        ({"name": "x.service", "state": "present", "text": "x", "dropin": "x.conf", "mode": "0644", "intent": "fallback", "on_change": None}, "on_change"),
        ({"name": "x.service", "state": "absent", "dropin": "x.conf", "text": "x", "on_change": "none"}, "must not specify"),
        ({"name": "x.service", "state": "absent", "dropin": "x.conf", "on_change": "none", "preflight": ("x",)}, "takes no preflight"),
        ({"name": "x.service", "state": "present", "text": "x", "mode": "0644", "intent": "authoritative", "enable": False, "on_change": "none", "preflight": []}, "non-empty argv"),
        ({"name": "x.service", "state": "present", "text": "x", "mode": "0644", "intent": "authoritative", "enable": False, "on_change": "none", "preflight": [""]}, "non-empty strings"),
    ],
)
def test_additional_constructor_refusals(kwargs, message):
    with pytest.raises(ValueError, match=message):
        Unit(**kwargs)


def test_owner_resolution_failure_is_a_named_plan_refusal(world):
    root, _executable, commands = world
    resource = Unit(name="example.service", state="present", text=service_text(), mode="0644", intent="authoritative", owner="no-such-user", enable=False, on_change="none")
    with pytest.raises(Exception, match="unknown user"):
        plan(Request("owner", (resource,)), root=root)


def test_private_protocol_guards_report_expected_and_mismatch_problems(world, monkeypatch):
    root, _executable, commands = world
    resource = Unit(name="example.service", state="present", text=service_text(), mode="0644", intent="authoritative", enable=False, on_change="none")
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
    resource = Unit(name="example.service", state="present", text=service_text(), mode="0644", intent="authoritative", enable=False, on_change="none")
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

    def disappear(managed_root, absolute, *, allow_symlink=False):
        if absolute.endswith("50-child.conf"):
            return None
        return original(managed_root, absolute, allow_symlink=allow_symlink)

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


def test_unit_text_rejects_unpaired_surrogates():
    with pytest.raises(ValueError, match="valid UTF-8"):
        Unit(
            name="example.service",
            state="present",
            text="\ud800",
            mode="0644",
            intent="authoritative",
            enable=False,
            on_change="none",
        )


@pytest.mark.parametrize(
    ("directory", "mode", "message"),
    [
        (True, unit_module.stat.S_IFREG | 0o755, "component is not a directory"),
        (False, unit_module.stat.S_IFDIR | 0o755, "not a regular file"),
    ],
)
def test_managed_unit_metadata_refuses_wrong_file_types(tmp_path, directory, mode, message):
    with pytest.raises(PlanRefused, match=message):
        unit_module._safe_unit_metadata(
            SimpleNamespace(st_mode=mode, st_uid=os.geteuid()),
            expected_uid=os.geteuid(),
            path=tmp_path / "unit",
            directory=directory,
        )


def test_live_enablement_parses_expected_command_errors_and_refuses_other_codes(world, monkeypatch):
    monkeypatch.setattr(unit_module, "_is_live_root", lambda _root: True)

    def expected_disabled(_argv, _timeout, returncode):
        raise _UnitCommandError("disabled", returncode=returncode, stdout="disabled\n")

    for returncode in (1, 4):
        monkeypatch.setattr(
            unit_module,
            "_run_command",
            lambda argv, timeout, code=returncode: expected_disabled(argv, timeout, code),
        )
        assert unit_module._unit_enabled(Path("/"), "example.service") is False

    monkeypatch.setattr(
        unit_module,
        "_run_command",
        lambda _argv, _timeout: (_ for _ in ()).throw(_UnitCommandError("io", returncode=3, stdout="disabled\n")),
    )
    with pytest.raises(_UnitCommandError, match="io"):
        unit_module._unit_enabled(Path("/"), "example.service")


def test_public_unit_signatures_cover_non_mapping_observations():
    assert unit_module._target_signature(None) == (False,)
    assert unit_module._verification_signature(None) == (False,)
    assert unit_module._verification_signature({"fingerprint": "opaque"}) == ("fingerprint", "opaque")


def test_unit_verification_signature_includes_exec_observations():
    value = {
        "path": "/etc/systemd/system/example.service",
        "target": {"exists": True, "sha256": "unit", "mode": 0o644, "uid": 0, "gid": 0},
        "enabled": False,
        "exec_paths": [{"path": "/usr/bin/example", "mode": 0o755, "uid": 0, "gid": 0}],
    }
    changed_exec = {**value, "exec_paths": [{"path": "/usr/bin/example", "mode": 0o700, "uid": 0, "gid": 0}]}

    # Exec observations are managed verification state; source inventories are
    # validation-only inputs and deliberately do not participate here.
    assert unit_module._verification_signature(value) != unit_module._verification_signature(changed_exec)


def test_unit_verification_signature_excludes_validation_only_inventories():
    value = {
        "path": "/etc/systemd/system/example.service",
        "target": {"exists": True, "sha256": "unit", "mode": 0o644, "uid": 0, "gid": 0},
        "enabled": False,
        "exec_paths": [],
        "sources": [{"path": "/usr/lib/systemd/system/example.service", "sha256": "old"}],
        "layers": {"files": {"/usr/lib/systemd/system/example.service.d/10-vendor.conf": "b2xk"}},
    }
    changed_inventory = {
        **value,
        "sources": [{"path": "/usr/lib/systemd/system/example.service", "sha256": "new"}],
        "layers": {"files": {"/usr/lib/systemd/system/example.service.d/10-vendor.conf": "bmV3"}},
    }

    assert unit_module._verification_signature(value) == unit_module._verification_signature(changed_inventory)


def test_unit_plan_fingerprint_includes_validation_layer_snapshot():
    value = {
        "path": "/etc/systemd/system/example.service",
        "target": {"exists": False, "fingerprint": "absent"},
        "enabled": None,
        "exec_paths": [],
        "sources": [],
        "layers": {"files": {}, "links": {}, "directories": {"/etc/systemd/system/example.service.d": None}},
    }
    changed_layers = {
        **value,
        "layers": {"files": {}, "links": {}, "directories": {"/etc/systemd/system/example.service.d": []}},
    }

    assert unit_module._combined_fingerprint(value) != unit_module._combined_fingerprint(changed_layers)


def test_unit_observation_and_shadow_hook_use_per_unit_dropin_layers(monkeypatch, tmp_path):
    root = fake_root(tmp_path)
    resource = Unit(name="example.service", state="absent")
    seen = []

    def observe_layers(_root, target_class):
        seen.append(target_class)
        return {"files": {}, "links": {}, "directories": {}}

    monkeypatch.setattr(unit_module.shadow, "observe_layers", observe_layers)
    observed = resource.observe(SimpleNamespace(root=root))[resource.addresses()[0]]
    assert observed["layers"] == {"files": {}, "links": {}, "directories": {}}
    assert unit_module.shadow_check(resource, root) == []

    expected_directories = tuple(f"{directory}/example.service.d" for directory in unit_module._UNIT_SEARCH_DIRS)
    assert [item.name for item in seen] == ["unit:example.service", "unit:example.service"]
    assert all(item.directories == expected_directories for item in seen)


def test_interleaved_unit_and_other_kind_tables_are_refused(tmp_path):
    interleaved = tmp_path / "interleaved.toml"
    interleaved.write_text(
        'augeaswrap = 1\nwriter = "mixed"\n'
        '[[unit]]\nname = "one.service"\nstate = "absent"\n'
        '[[file]]\npath = "/etc/example.conf"\nstate = "absent"\n'
        '[[unit]]\nname = "two.service"\nstate = "absent"\n',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="interleaves 'unit'"):
        load_request(interleaved)

    request = Request(
        "mixed kind order",
        (
            Unit(name="one.service", state="absent"),
            File("/etc/example.conf", "absent"),
            Unit(name="two.service", state="absent"),
        ),
    )
    with pytest.raises(ValueError, match="cannot serialize interleaved 'unit'"):
        request_to_toml(request)


def test_virtual_path_resolution_handles_dots_parent_and_symlink_limits(tmp_path):
    root = fake_root(tmp_path)
    target = root / "usr/bin/example"
    target.parent.mkdir(parents=True)
    target.write_text("x", encoding="utf-8")
    link = root / "usr/lib/example-link"
    link.parent.mkdir(parents=True)
    link.symlink_to("./../bin/example")
    assert unit_module._resolve_below_root(root, "/usr/lib/example-link") == target
    assert unit_module._resolve_below_root(root, "/usr/./bin/example") == target
    assert unit_module._resolve_below_root(root, "usr/bin/example") == target
    with pytest.raises(ValueError, match="escapes selected root"):
        unit_module._resolve_below_root(root, "/../../outside")

    for index in range(41):
        (root / f"link-{index}").symlink_to(f"link-{index + 1}")
    (root / "link-41").symlink_to("/usr/bin/example")
    with pytest.raises(ValueError, match="too many symlinks"):
        unit_module._resolve_below_root(root, "/link-0")


def test_exec_containment_guard_refuses_an_outside_resolver_result(tmp_path, monkeypatch):
    root = fake_root(tmp_path)
    monkeypatch.setattr(unit_module, "_resolve_below_root", lambda _root, _path: tmp_path / "outside")
    with pytest.raises(PlanRefused, match="outside the selected root"):
        unit_module._inspect_exec(root, "/usr/bin/example", "/etc/systemd/system/example.service")


def test_source_file_refuses_unapproved_and_broken_symlinks(tmp_path):
    root = fake_root(tmp_path)
    source = root / "usr/lib/systemd/system/example.service"
    source.parent.mkdir(parents=True)
    source.write_text("[Unit]\n", encoding="utf-8")
    alias = source.with_name("alias.service")
    alias.symlink_to("./example.service")
    with pytest.raises(_fs.PathRefusal, match="symlinked unit source"):
        unit_module._source_file(root, "/usr/lib/systemd/system/alias.service")
    observed = unit_module._source_file(root, "/usr/lib/systemd/system/alias.service", allow_symlink=True)
    assert observed is not None and observed["source_path"] == "/usr/lib/systemd/system/example.service"

    broken = source.with_name("broken.service")
    broken.symlink_to("missing.service")
    with pytest.raises(ValueError, match="broken or recursive"):
        unit_module._source_file(root, "/usr/lib/systemd/system/broken.service", allow_symlink=True)
    directory_alias = source.with_name("directory.service")
    directory_target = source.parent / "target-dir"
    directory_target.mkdir()
    directory_alias.symlink_to("target-dir")
    with pytest.raises(ValueError, match="does not resolve to a regular file"):
        unit_module._source_file(root, "/usr/lib/systemd/system/directory.service", allow_symlink=True)
    fifo = source.with_name("fifo.service")
    os.mkfifo(fifo)
    with pytest.raises(ValueError, match="not a regular file"):
        unit_module._source_file(root, "/usr/lib/systemd/system/fifo.service")


def test_unit_dependency_parser_fails_closed_on_invalid_encoding_and_quotes():
    assert unit_module._unit_references(b"Requires=\xff.service\n") == set()
    with pytest.raises(ValueError, match="cannot parse unit dependency list"):
        unit_module._unit_references(b'Requires="unterminated.service\n')
    assert unit_module._unit_references(b"Requires=not-a-unit.invalid\n") == set()
    with pytest.raises(ValueError, match="no source path"):
        unit_module._source_bytes(Path("/tmp"), {})


def test_unit_search_inventory_handles_alias_cycles_and_missing_dependencies(tmp_path):
    root = fake_root(tmp_path)
    vendor = root / "usr/lib/systemd/system"
    vendor.mkdir(parents=True)
    (vendor / "example.service").write_text("[Unit]\nRequires=dep.service\n", encoding="utf-8")
    (vendor / "dep.service").write_text("[Unit]\nRequires=example.service dep.service\n", encoding="utf-8")
    (vendor / "alias.service").symlink_to("./example.service")
    (vendor / "broken.service").symlink_to("missing.service")
    (vendor / "directory.service").mkdir()

    sources = unit_module._source_inventory(
        root,
        "requested.service",
        "/etc/systemd/system/requested.service",
        None,
        "[Unit]\nRequires=missing.target requested.service\n",
    )
    by_path = {source["path"]: source for source in sources}
    assert "/usr/lib/systemd/system/alias.service" in by_path
    assert by_path["/usr/lib/systemd/system/alias.service"]["source_path"] == "/usr/lib/systemd/system/example.service"
    assert "/usr/lib/systemd/system/example.service" in by_path
    assert "/usr/lib/systemd/system/dep.service" in by_path
    assert "/usr/lib/systemd/system/broken.service" not in by_path
    assert "/usr/lib/systemd/system/directory.service" not in by_path

    skip_base = tmp_path / "skip-path"
    skip_base.mkdir()
    skip_root = fake_root(skip_base)
    skip_vendor = skip_root / "usr/lib/systemd/system"
    skip_vendor.mkdir(parents=True)
    (skip_vendor / "same.service").write_text("[Unit]\n", encoding="utf-8")
    skipped = unit_module._source_inventory(
        skip_root,
        "same.service",
        "/usr/lib/systemd/system/same.service",
        None,
    )
    assert skipped == []

    with pytest.raises(ValueError, match="broken or recursive"):
        unit_module._source_inventory(
            root,
            "requested.service",
            "/etc/systemd/system/requested.service",
            None,
            "[Unit]\nRequires=broken.service\n",
        )


def test_local_lib_static_unit_tree_is_staged(tmp_path):
    root = fake_root(tmp_path)
    local = root / "usr/local/lib/systemd/system"
    local.mkdir(parents=True)
    (local / "local-helper.service").write_text("[Unit]\nDescription=Local helper\n", encoding="utf-8")
    sources = unit_module._source_inventory(
        root,
        "requested.service",
        "/etc/systemd/system/requested.service",
        None,
    )
    stage = tmp_path / "stage"
    stage.mkdir()
    unit_module._copy_sources(root, stage, sources)
    assert (stage / "usr/local/lib/systemd/system/local-helper.service").read_text(encoding="utf-8") == (
        "[Unit]\nDescription=Local helper\n"
    )


def test_dropin_text_references_add_dependency_unit_to_verify_tree(tmp_path):
    root = fake_root(tmp_path)
    vendor = root / "usr/lib/systemd/system"
    vendor.mkdir(parents=True)
    (vendor / "requested.service").write_text("[Unit]\nDescription=Requested\n", encoding="utf-8")
    runtime = root / "run/systemd/system"
    runtime.mkdir(parents=True)
    (runtime / "dependency.service").write_text("[Unit]\nDescription=Dependency\n", encoding="utf-8")

    sources = unit_module._source_inventory(
        root,
        "requested.service",
        "/etc/systemd/system/requested.service.d/50-local.conf",
        "50-local.conf",
        "[Unit]\nRequires=dependency.service\n",
    )
    assert "/run/systemd/system/dependency.service" in {source["path"] for source in sources}


def test_dropins_of_referenced_dependency_units_are_discovered(tmp_path):
    root = fake_root(tmp_path)
    vendor = root / "usr/lib/systemd/system"
    vendor.mkdir(parents=True)
    (vendor / "requested.service").write_text("[Unit]\nRequires=dependency.service\n", encoding="utf-8")
    (vendor / "dependency.service").write_text("[Unit]\nDescription=Dependency\n", encoding="utf-8")
    dropins = root / "etc/systemd/system/dependency.service.d"
    dropins.mkdir(parents=True)
    (dropins / "20-local.conf").write_text("[Unit]\nRequires=extra.target\n", encoding="utf-8")
    local = root / "usr/local/lib/systemd/system"
    local.mkdir(parents=True)
    (local / "extra.target").write_text("[Unit]\nDescription=Extra\n", encoding="utf-8")

    sources = unit_module._source_inventory(
        root,
        "requested.service",
        "/etc/systemd/system/requested.service",
        None,
    )
    staged_paths = {source["path"] for source in sources}
    assert "/etc/systemd/system/dependency.service.d/20-local.conf" in staged_paths
    assert "/usr/local/lib/systemd/system/extra.target" in staged_paths


def test_unit_search_inventory_skips_entries_removed_after_listing(tmp_path, monkeypatch):
    root = fake_root(tmp_path)
    vendor = root / "usr/lib/systemd/system"
    vendor.mkdir(parents=True)
    gone = vendor / "gone.service"
    gone.write_text("[Unit]\n", encoding="utf-8")
    (vendor / "not-a-file.service").mkdir()
    os.mkfifo(vendor / "special.service")
    original = Path.lstat

    def disappearing(path):
        if path == gone:
            raise FileNotFoundError(path)
        return original(path)

    monkeypatch.setattr(Path, "lstat", disappearing)
    assert unit_module._source_inventory(root, "requested.service", "/etc/systemd/system/requested.service", None) == []


def test_unit_search_inventory_rejects_non_directory_search_paths(tmp_path, monkeypatch):
    root = fake_root(tmp_path)
    directory = root / "usr/lib/systemd/system"
    directory.parent.mkdir(parents=True)
    directory.write_text("not a directory", encoding="utf-8")
    monkeypatch.setattr(unit_module._fs, "safe_components", lambda _root, _path: None)
    with pytest.raises(ValueError, match="search path is not a directory"):
        unit_module._directory_entries(root, "/usr/lib/systemd/system")


def test_source_staging_detects_races_and_recreates_existing_aliases(tmp_path):
    root = fake_root(tmp_path)
    vendor = root / "usr/lib/systemd/system"
    vendor.mkdir(parents=True)
    actual = vendor / "actual.service"
    actual.write_text("[Unit]\n", encoding="utf-8")
    alias = vendor / "alias.service"
    alias.symlink_to("actual.service")
    (vendor / "masked.service").symlink_to("/dev/null")
    sources = unit_module._source_inventory(root, "requested.service", "/etc/systemd/system/requested.service", None)
    stage = tmp_path / "stage"
    stage.mkdir()
    unit_module._copy_sources(root, stage, sources)
    unit_module._copy_sources(root, stage, sources)
    assert (stage / "usr/lib/systemd/system/alias.service").is_symlink()

    target_source = next(source for source in sources if source["path"] == "/usr/lib/systemd/system/actual.service")
    actual.write_text("[Unit]\nDescription=changed\n", encoding="utf-8")
    race_stage = tmp_path / "stage-race"
    race_stage.mkdir()
    with pytest.raises(ValueError, match="changed while staging"):
        unit_module._copy_sources(root, race_stage, [target_source])
    missing_target_stage = tmp_path / "stage-missing-target"
    missing_target_stage.mkdir()
    with pytest.raises(ValueError, match="symlink target was not staged"):
        unit_module._copy_sources(
            root,
            missing_target_stage,
            [{"path": "/usr/lib/systemd/system/alias.service", "link_target": "/missing.service", "masked": False}],
        )


def test_live_unit_desired_state_refuses_nonroot_owner_without_host_reads(monkeypatch):
    monkeypatch.setattr(unit_module, "_unit_owner_ids", lambda _root: (0, 0))
    resource = Unit(
        name="example.service",
        state="present",
        text="[Unit]\nDescription=No executable paths\n",
        mode="0644",
        owner=1000,
        intent="authoritative",
        enable=False,
        on_change="none",
    )
    with pytest.raises(PlanRefused, match="must be root-owned"):
        resource._desired(SimpleNamespace(root=Path("/")), {"target": {"exists": False}, "sources": []})


def test_runtime_action_ignores_unchanged_target_and_restore_handles_no_changes():
    resource = Unit(name="example.service", state="present", text="[Unit]\n", mode="0644", intent="authoritative", enable=False, on_change="restart")
    signature = {"target": {"exists": True, "sha256": "same", "mode": 0o644, "uid": 0, "gid": 0}}
    change = unit_module.Change("unit", resource.addresses()[0], "change", signature, signature, resource)
    assert resource._runtime_change_applies([change]) is False
    restored = unit_module._RestoredUnit(
        "/etc/systemd/system/example.service",
        {"exists": False, "enabled": False, "fingerprint": "absent"},
        "example.service",
        None,
    )
    assert restored.post_actions([]) == []
    no_file_change = unit_module.Change(
        "unit", restored.addresses()[0], "change", signature, signature, restored
    )
    actions = restored.post_actions([no_file_change])
    assert [action.argv[1] for action in actions] == ["stop", "disable"]


def test_restore_systemctl_argv_terminates_every_unit_operand():
    name = "example.service"
    path = f"/etc/systemd/system/{name}"
    before = {
        "target": {"exists": True, "sha256": "current"},
        "enabled": False,
    }
    after_absent = {"target": {"exists": False, "sha256": None}, "enabled": False}
    absent = unit_module._RestoredUnit(
        path,
        {"exists": False, "enabled": False, "fingerprint": "absent"},
        name,
        None,
    )
    absent_change = unit_module.Change("unit", absent.addresses()[0], "change", before, after_absent, absent)
    assert [action.argv for action in absent.post_actions([absent_change])] == [
        ("systemctl", "stop", "--", name),
        ("systemctl", "disable", "--", name),
        ("systemctl", "daemon-reload"),
    ]

    original_actions = tuple(
        PostAction(f"recorded {verb}", ("systemctl", verb, "--", name), 30.0, phase="runtime")
        for verb in ("restart", "reload", "reset-failed")
    )
    present = unit_module._RestoredUnit(
        path,
        {"exists": True, "enabled": True, "sha256": "saved", "data_b64": "W1VuaXRdCg==", "mode": 0o644, "uid": 0, "gid": 0},
        name,
        None,
        original_actions,
    )
    after_present = {"target": {"exists": True, "sha256": "saved"}, "enabled": False}
    present_change = unit_module.Change("unit", present.addresses()[0], "change", before, after_present, present)
    assert [action.argv for action in present.post_actions([present_change])] == [
        ("systemctl", "daemon-reload"),
        ("systemctl", "enable", "--", name),
        ("systemctl", "restart", "--", name),
        ("systemctl", "reload", "--", name),
        ("systemctl", "reset-failed", "--", name),
    ]


def test_dropin_with_no_runtime_policy_only_reloads(world):
    root, _executable, _commands = world
    dropin = Unit(
        name="example.service",
        state="present",
        dropin="50-no-runtime.conf",
        text="[Service]\nNice=2\n",
        mode="0644",
        intent="fallback",
        on_change="none",
    )
    actions = plan(Request("drop-in without runtime", (dropin,)), root=root).as_dict()["post_actions"]
    assert [item["name"] for item in actions] == ["daemon-reload"]


def test_unit_snapshot_and_restore_reject_invalid_enablement():
    resource = Unit(name="example.service", state="absent")
    change = unit_module.Change("unit", resource.addresses()[0], "change", {}, {}, resource)
    with pytest.raises(ValueError, match="invalid enabled state"):
        unit_module._unit_snapshot(change, {"target": {"exists": False}, "enabled": "yes"})
    record = {"path": resource.path, "exists": False, "enabled": "yes"}
    with pytest.raises(ValueError, match="enabled state must be a boolean or null"):
        unit_module._unit_restore(resource.addresses()[0], record, ())


def test_post_action_validation_rejects_unknown_phase_and_before_commit_pending():
    with pytest.raises(ValueError, match="phase is not a registered"):
        PostAction("x", ("true",), 1, phase="unregistered")
    with pytest.raises(ValueError, match="cannot be pending"):
        PostAction("x", ("true",), 1, pending=True, phase="before_commit")


@pytest.mark.parametrize("argv", [("systemctl", "not-a-verb"), ("augeaswrap", "not-endpoints")])
def test_unknown_system_action_forms_require_an_explicit_phase(argv):
    with pytest.raises(ValueError, match="needs an explicit phase"):
        engine_module._action_phase(PostAction("unknown", argv, 1))


def test_exec_command_keeps_an_unquoted_hash_in_the_command_token():
    assert unit_module._exec_command(
        "ExecStart=/usr/bin/example#suffix",
        "/etc/systemd/system/example.service",
    ) == "/usr/bin/example#suffix"


def test_exec_command_removes_shell_quotes_from_the_command_token():
    assert unit_module._exec_command(
        "ExecStart='/usr/bin/example' --flag",
        "/etc/systemd/system/example.service",
    ) == "/usr/bin/example"


def test_absent_unit_does_not_schedule_a_second_runtime_disable():
    resource = Unit(name="example.service", state="absent")
    before = {"target": {"exists": True, "sha256": "before"}, "enabled": True}
    after = {"target": {"exists": False, "fingerprint": "absent"}, "enabled": False}
    change = unit_module.Change(
        "unit", resource.addresses()[0], "change", before, after, resource
    )

    actions = resource.post_actions([change])

    assert [(action.argv, action.phase) for action in actions] == [
        (("systemctl", "stop", "--", "example.service"), "before_commit"),
        (("systemctl", "disable", "--", "example.service"), "before_commit"),
        (("systemctl", "daemon-reload"), "daemon_reload"),
        (("systemctl", "reset-failed", "--", "example.service"), "runtime"),
    ]


def test_unit_restore_rejects_an_address_without_the_canonical_path():
    with pytest.raises(ValueError, match="pre-image path does not match"):
        unit_module._unit_restore(
            "unit:example.service",
            {"path": "example.service", "exists": False},
            (),
        )
