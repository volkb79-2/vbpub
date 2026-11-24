"""Host cgroup inspection does not require the host PID namespace."""

from __future__ import annotations

import dataclasses
from types import SimpleNamespace

import pytest

from vm_lab import GENERIC_PROFILE, RunnerError
from vm_lab import core as vm_lab


CONTAINER_ID = "a" * 64
OBJECT_PATH = "/org/freedesktop/systemd1/unit/docker_2dexample_2escope"
SCOPE_PATH = f"/dev.slice/dev-background.slice/docker-{CONTAINER_ID}.scope"
CGROUP_SOURCE = "/sys/fs/cgroup"
DBUS_SOURCE = "/run/dbus/system_bus_socket"


def _inspector_mounts(
    *,
    cgroup: str | None = "read-only",
    dbus: str | None = "read-only",
) -> list[dict[str, object]]:
    mounts: list[dict[str, object]] = []
    if cgroup is not None:
        mounts.append(
            {
                "Source": CGROUP_SOURCE,
                "Destination": CGROUP_SOURCE,
                "RW": cgroup == "read-write",
            }
        )
    if dbus is not None:
        mounts.append(
            {
                "Source": DBUS_SOURCE,
                "Destination": DBUS_SOURCE,
                "RW": dbus == "read-write",
            }
        )
    return mounts


def _patch_one_inspector(monkeypatch, mounts: list[dict[str, object]]) -> None:
    monkeypatch.setattr(
        vm_lab,
        "docker_run",
        lambda _argv, **_kwargs: SimpleNamespace(returncode=0, stdout="observer-id\n", stderr=""),
    )
    monkeypatch.setattr(vm_lab, "docker_inspect", lambda _cid: {"Mounts": mounts})


def _patch_scope_properties(
    monkeypatch,
    *,
    load_state: str = "loaded",
    active_state: str = "active",
    tasks_current: str = "1",
    control_group: str = SCOPE_PATH,
) -> None:
    properties = {
        "LoadState": load_state,
        "ActiveState": active_state,
        "TasksCurrent": tasks_current,
        "ControlGroup": control_group,
    }
    monkeypatch.setattr(vm_lab, "inspector_exec", lambda *_args: f'o "{OBJECT_PATH}"')
    monkeypatch.setattr(
        vm_lab,
        "busctl_value",
        lambda _inspector, _path, _interface, prop: properties[prop],
    )


def test_busctl_value_reads_systemd_uint64_property(monkeypatch):
    monkeypatch.setattr(vm_lab, "inspector_exec", lambda *_args: "t 1")

    assert vm_lab.busctl_value("inspector", OBJECT_PATH, "org.freedesktop.systemd1.Scope", "TasksCurrent") == "1"


def test_systemd_scope_path_resolves_loaded_docker_scope(monkeypatch):
    _patch_scope_properties(monkeypatch)

    assert vm_lab.systemd_scope_path("inspector", CONTAINER_ID) == SCOPE_PATH


def test_inspector_requires_read_only_host_cgroup_and_dbus_mounts(monkeypatch):
    _patch_one_inspector(monkeypatch, _inspector_mounts())

    assert vm_lab.find_cgroup_inspector() == "observer-id"


def test_inspector_refuses_a_writable_cgroup_mount(monkeypatch):
    _patch_one_inspector(monkeypatch, _inspector_mounts(cgroup="read-write"))

    with pytest.raises(
        RunnerError,
        match=r"host cgroup mount /sys/fs/cgroup is read-write; it must be read-only",
    ):
        vm_lab.find_cgroup_inspector()


def test_inspector_refuses_a_writable_dbus_mount(monkeypatch):
    _patch_one_inspector(monkeypatch, _inspector_mounts(dbus="read-write"))

    with pytest.raises(
        RunnerError,
        match=r"system D-Bus mount /run/dbus/system_bus_socket is read-write; it must be read-only",
    ):
        vm_lab.find_cgroup_inspector()


def test_inspector_refuses_a_missing_cgroup_mount(monkeypatch):
    _patch_one_inspector(monkeypatch, _inspector_mounts(cgroup=None))

    with pytest.raises(RunnerError, match=r"host cgroup mount /sys/fs/cgroup is missing"):
        vm_lab.find_cgroup_inspector()


def test_inspector_refuses_a_missing_dbus_mount(monkeypatch):
    _patch_one_inspector(monkeypatch, _inspector_mounts(dbus=None))

    with pytest.raises(
        RunnerError,
        match=r"system D-Bus mount /run/dbus/system_bus_socket is missing",
    ):
        vm_lab.find_cgroup_inspector()


def test_systemd_scope_path_rejects_malformed_container_id(monkeypatch):
    _patch_scope_properties(monkeypatch)

    with pytest.raises(RunnerError, match="invalid full container ID"):
        vm_lab.systemd_scope_path("inspector", "short-id")


def test_systemd_scope_path_refuses_unloaded_scope(monkeypatch):
    _patch_scope_properties(monkeypatch, load_state="not-found")

    with pytest.raises(RunnerError, match="LoadState='not-found'"):
        vm_lab.systemd_scope_path("inspector", CONTAINER_ID)


def test_systemd_scope_path_refuses_an_inactive_scope(monkeypatch):
    _patch_scope_properties(monkeypatch, active_state="inactive")

    with pytest.raises(RunnerError, match="ActiveState='inactive'"):
        vm_lab.systemd_scope_path("inspector", CONTAINER_ID)


def test_systemd_scope_path_refuses_scope_without_tasks(monkeypatch):
    _patch_scope_properties(monkeypatch, tasks_current="0")

    with pytest.raises(RunnerError, match="has no active tasks: TasksCurrent='0'"):
        vm_lab.systemd_scope_path("inspector", CONTAINER_ID)


def test_systemd_scope_path_refuses_unreadable_tasks(monkeypatch):
    _patch_scope_properties(monkeypatch, tasks_current="unreadable")

    with pytest.raises(RunnerError, match="has unreadable TasksCurrent='unreadable'"):
        vm_lab.systemd_scope_path("inspector", CONTAINER_ID)


def test_systemd_scope_path_rejects_unsafe_control_group(monkeypatch):
    _patch_scope_properties(monkeypatch, control_group="/dev.slice/../init.scope")

    with pytest.raises(RunnerError, match="unsafe cgroup path"):
        vm_lab.systemd_scope_path("inspector", CONTAINER_ID)


def _running_container(profile):
    return {
        "Id": CONTAINER_ID,
        "Config": {"Labels": {profile.vm_label: profile.label_value, profile.state_label: "state-hash"}},
        "HostConfig": {
            "CgroupParent": "dev-background.slice",
            "NanoCpus": profile.nano_cpus,
            "Memory": profile.container_memory_bytes,
            "MemorySwap": profile.container_memory_bytes,
        },
        "NetworkSettings": {"Networks": {"zbm-test": {}}},
        "State": {"Running": True, "Pid": 4321},
    }


def test_verify_running_container_checks_systemd_scope_and_resource_limits(monkeypatch):
    profile = dataclasses.replace(GENERIC_PROFILE, guest_memory_mib=2048, cpus=2)
    item = _running_container(profile)
    monkeypatch.setattr(vm_lab, "docker_inspect", lambda _name: item)
    monkeypatch.setattr(vm_lab, "systemd_scope_path", lambda _inspector, _id: SCOPE_PATH)
    monkeypatch.setattr(
        vm_lab,
        "cgroup_levels",
        lambda _inspector, path: [
            {
                "path": path,
                "memory_max": profile.container_memory_bytes,
                "cpu_max": 2.0,
            }
        ],
    )

    result = vm_lab.verify_running_container(
        profile,
        "guest",
        "state-hash",
        "zbm-test",
        "dev-background.slice",
        "inspector",
        "/dev.slice/dev-background.slice",
    )

    assert result["host_cgroup"] == SCOPE_PATH
    assert result["host_pid"] == 4321
    assert result["memory_max_bytes"] == profile.container_memory_bytes


def test_verify_running_container_refuses_scope_outside_selected_slice(monkeypatch):
    profile = dataclasses.replace(GENERIC_PROFILE, guest_memory_mib=2048, cpus=2)
    item = _running_container(profile)
    _patch_scope_properties(monkeypatch, control_group="/system.slice/docker.scope")
    monkeypatch.setattr(vm_lab, "docker_inspect", lambda _name: item)
    monkeypatch.setattr(
        vm_lab,
        "cgroup_levels",
        lambda _inspector, path: [
            {
                "path": path,
                "memory_max": profile.container_memory_bytes,
                "cpu_max": 2.0,
            }
        ],
    )

    with pytest.raises(RunnerError, match="outside selected slice"):
        vm_lab.verify_running_container(
            profile,
            "guest",
            "state-hash",
            "zbm-test",
            "dev-background.slice",
            "inspector",
            "/dev.slice/dev-background.slice",
        )
