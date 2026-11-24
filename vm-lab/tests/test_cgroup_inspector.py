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
UNIT_INTERFACE = "org.freedesktop.systemd1.Unit"
SCOPE_INTERFACE = "org.freedesktop.systemd1.Scope"
SLICE_INTERFACE = "org.freedesktop.systemd1.Slice"
UINT64_MAX = 18446744073709551615


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


def _patch_inspectors(monkeypatch, containers: dict[str, list[dict[str, object]]]) -> None:
    monkeypatch.setattr(
        vm_lab,
        "docker_run",
        lambda _argv, **_kwargs: SimpleNamespace(
            returncode=0, stdout="".join(f"{name}\n" for name in containers), stderr=""
        ),
    )
    monkeypatch.setattr(vm_lab, "docker_inspect", lambda name: {"Mounts": containers[name]})


def _patch_one_inspector(monkeypatch, mounts: list[dict[str, object]]) -> None:
    _patch_inspectors(monkeypatch, {"observer-id": mounts})


def _patch_bus(monkeypatch, units: dict[str, dict[tuple[str, str], str]]) -> None:
    """Fake the host system bus beneath the busctl parser.

    `units` maps a unit name to the raw busctl text of each (interface, property)
    that a correct caller may read. Any other request raises.
    """
    object_paths = {name: f"/org/freedesktop/systemd1/unit/{index}" for index, name in enumerate(units)}

    def fake_exec(_inspector: str, argv: list[str]) -> str:
        if argv[0] != "busctl":
            raise AssertionError(f"unexpected inspector command {argv!r}")
        if "GetUnit" in argv:
            unit = argv[-1]
            if unit not in object_paths:
                raise AssertionError(f"unexpected GetUnit for {unit!r}")
            return f'o "{object_paths[unit]}"'
        if "get-property" in argv:
            object_path, interface, prop = argv[-3:]
            for unit, path in object_paths.items():
                if path == object_path and (interface, prop) in units[unit]:
                    return units[unit][(interface, prop)]
        raise AssertionError(f"unexpected bus request {argv!r}")

    monkeypatch.setattr(vm_lab, "inspector_exec", fake_exec)


def _scope_properties(
    *,
    load_state: str = 's "loaded"',
    active_state: str = 's "active"',
    tasks_current: str = "t 1",
    control_group: str | None = SCOPE_PATH,
) -> dict[tuple[str, str], str]:
    return {
        (UNIT_INTERFACE, "LoadState"): load_state,
        (UNIT_INTERFACE, "ActiveState"): active_state,
        (SCOPE_INTERFACE, "TasksCurrent"): tasks_current,
        (SCOPE_INTERFACE, "ControlGroup"): f's "{control_group}"' if control_group is not None else "t 5",
    }


def _patch_scope(monkeypatch, container_id: str = CONTAINER_ID, **properties) -> None:
    _patch_bus(monkeypatch, {f"docker-{container_id}.scope": _scope_properties(**properties)})


def test_busctl_value_reads_systemd_uint64_property(monkeypatch):
    monkeypatch.setattr(vm_lab, "inspector_exec", lambda *_args: "t 1")

    assert vm_lab.busctl_value("inspector", OBJECT_PATH, SCOPE_INTERFACE, "TasksCurrent", "t") == "1"


@pytest.mark.parametrize(
    ("raw", "signature"),
    [
        ('s "5"', "t"),
        ("t 5", "s"),
        ("t 5 6", "t"),
        ('s "a" "b"', "s"),
        ("b true", "s"),
        ("t -1", "t"),
        ("", "t"),
    ],
)
def test_busctl_value_refuses_a_value_of_another_dbus_type(monkeypatch, raw, signature):
    monkeypatch.setattr(vm_lab, "inspector_exec", lambda *_args: raw)

    with pytest.raises(RunnerError, match="could not parse host systemd property"):
        vm_lab.busctl_value("inspector", OBJECT_PATH, SCOPE_INTERFACE, "TasksCurrent", signature)


def test_systemd_scope_path_resolves_loaded_docker_scope(monkeypatch):
    _patch_scope(monkeypatch)

    assert vm_lab.systemd_scope_path("inspector", CONTAINER_ID) == SCOPE_PATH


def test_inspector_requires_read_only_host_cgroup_and_dbus_mounts(monkeypatch):
    _patch_one_inspector(monkeypatch, _inspector_mounts())

    assert vm_lab.find_cgroup_inspector() == "observer-id"


def test_inspector_accepts_a_read_only_submount_and_ignores_a_sibling_path(monkeypatch):
    mounts = _inspector_mounts() + [
        {"Source": "/sys/fs/cgroup/dev.slice", "Destination": "/sys/fs/cgroup/dev.slice", "RW": False},
        {"Source": "/sys/fs/cgroup-extra", "Destination": "/sys/fs/cgroup-extra", "RW": True},
    ]
    _patch_one_inspector(monkeypatch, mounts)

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


@pytest.mark.parametrize("order", ["read-write first", "read-only first"])
def test_inspector_refuses_a_cgroup_path_mounted_both_read_write_and_read_only(monkeypatch, order):
    pair = [
        {"Source": CGROUP_SOURCE, "Destination": CGROUP_SOURCE, "RW": True},
        {"Source": CGROUP_SOURCE, "Destination": CGROUP_SOURCE, "RW": False},
    ]
    if order == "read-only first":
        pair.reverse()
    _patch_one_inspector(monkeypatch, pair + _inspector_mounts(cgroup=None))

    with pytest.raises(RunnerError, match=r"host cgroup mount /sys/fs/cgroup is read-write"):
        vm_lab.find_cgroup_inspector()


def test_inspector_refuses_a_writable_mount_beneath_the_cgroup_mount(monkeypatch):
    submount = {"Source": "/sys/fs/cgroup/dev.slice", "Destination": "/sys/fs/cgroup/dev.slice", "RW": True}
    _patch_one_inspector(monkeypatch, _inspector_mounts() + [submount])

    with pytest.raises(RunnerError, match=r"host cgroup mount /sys/fs/cgroup/dev.slice is read-write"):
        vm_lab.find_cgroup_inspector()


@pytest.mark.parametrize("state", [{}, {"RW": None}, {"RW": "false"}, {"RW": 0}])
def test_inspector_refuses_a_mount_without_a_boolean_read_only_state(monkeypatch, state):
    mounts = [
        {"Source": CGROUP_SOURCE, "Destination": CGROUP_SOURCE, **state},
        *_inspector_mounts(cgroup=None),
    ]
    _patch_one_inspector(monkeypatch, mounts)

    with pytest.raises(RunnerError, match=r"host cgroup mount /sys/fs/cgroup has no readable read-only state"):
        vm_lab.find_cgroup_inspector()


def test_inspector_refusal_lists_only_candidates_with_a_required_mount(monkeypatch):
    _patch_inspectors(
        monkeypatch,
        {
            "unrelated-id": [{"Source": "/data", "Destination": "/data", "RW": True}],
            "partial-id": _inspector_mounts(dbus=None),
        },
    )

    with pytest.raises(RunnerError, match="found 0") as refusal:
        vm_lab.find_cgroup_inspector()

    assert "partial-id" in str(refusal.value)
    assert "unrelated-id" not in str(refusal.value)


@pytest.mark.parametrize(
    "container_id",
    [
        pytest.param("a" * 12, id="short-12-hex"),
        pytest.param("a" * 65, id="65-hex"),
        pytest.param("a" * 64 + "/../x", id="64-hex-with-suffix"),
        pytest.param("A" * 64, id="uppercase-hex"),
        pytest.param("", id="empty"),
    ],
)
def test_systemd_scope_path_rejects_malformed_container_id(monkeypatch, container_id):
    _patch_scope(monkeypatch)

    with pytest.raises(RunnerError, match="invalid full container ID"):
        vm_lab.systemd_scope_path("inspector", container_id)


def test_systemd_scope_path_refuses_unloaded_scope(monkeypatch):
    _patch_scope(monkeypatch, load_state='s "not-found"')

    with pytest.raises(RunnerError, match="LoadState='not-found'; refusing unresolved units"):
        vm_lab.systemd_scope_path("inspector", CONTAINER_ID)


def test_systemd_slice_path_refuses_unloaded_slice_as_auto_created(monkeypatch):
    _patch_bus(
        monkeypatch,
        {
            "dev-background.slice": {
                (UNIT_INTERFACE, "LoadState"): 's "not-found"',
                (SLICE_INTERFACE, "ControlGroup"): 's "/dev.slice/dev-background.slice"',
            }
        },
    )

    with pytest.raises(RunnerError, match="LoadState='not-found'; refusing auto-created slices"):
        vm_lab.systemd_slice_path("inspector", "dev-background.slice")


def test_systemd_slice_path_resolves_a_loaded_slice(monkeypatch):
    _patch_bus(
        monkeypatch,
        {
            "dev-background.slice": {
                (UNIT_INTERFACE, "LoadState"): 's "loaded"',
                (SLICE_INTERFACE, "ControlGroup"): 's "/dev.slice/dev-background.slice"',
            }
        },
    )

    assert vm_lab.systemd_slice_path("inspector", "dev-background.slice") == "/dev.slice/dev-background.slice"


def test_systemd_scope_path_refuses_an_inactive_scope(monkeypatch):
    _patch_scope(monkeypatch, active_state='s "inactive"')

    with pytest.raises(RunnerError, match="ActiveState='inactive'"):
        vm_lab.systemd_scope_path("inspector", CONTAINER_ID)


def test_systemd_scope_path_refuses_scope_without_tasks(monkeypatch):
    _patch_scope(monkeypatch, tasks_current="t 0")

    with pytest.raises(RunnerError, match="has no active tasks: TasksCurrent='0'"):
        vm_lab.systemd_scope_path("inspector", CONTAINER_ID)


def test_systemd_scope_path_refuses_systemd_unreadable_task_count(monkeypatch):
    _patch_scope(monkeypatch, tasks_current=f"t {UINT64_MAX}")

    with pytest.raises(RunnerError, match=f"has unreadable TasksCurrent='{UINT64_MAX}'"):
        vm_lab.systemd_scope_path("inspector", CONTAINER_ID)


def test_systemd_scope_path_accepts_the_largest_readable_task_count(monkeypatch):
    _patch_scope(monkeypatch, tasks_current=f"t {UINT64_MAX - 1}")

    assert vm_lab.systemd_scope_path("inspector", CONTAINER_ID) == SCOPE_PATH


@pytest.mark.parametrize(
    "properties",
    [
        pytest.param({"load_state": "t 1"}, id="load-state-as-uint64"),
        pytest.param({"active_state": "t 1"}, id="active-state-as-uint64"),
        pytest.param({"tasks_current": 's "5"'}, id="tasks-as-string"),
        pytest.param({"control_group": None}, id="control-group-as-uint64"),
    ],
)
def test_systemd_scope_path_refuses_a_property_of_the_wrong_dbus_type(monkeypatch, properties):
    _patch_scope(monkeypatch, **properties)

    with pytest.raises(RunnerError, match="could not parse host systemd property"):
        vm_lab.systemd_scope_path("inspector", CONTAINER_ID)


@pytest.mark.parametrize(
    "control_group",
    [
        pytest.param("/dev.slice/../init.scope", id="parent-segment"),
        pytest.param(f"dev.slice/dev-background.slice/docker-{CONTAINER_ID}.scope", id="relative"),
    ],
)
def test_systemd_scope_path_rejects_unsafe_control_group(monkeypatch, control_group):
    _patch_scope(monkeypatch, control_group=control_group)

    with pytest.raises(RunnerError, match="unsafe cgroup path"):
        vm_lab.systemd_scope_path("inspector", CONTAINER_ID)


@pytest.mark.parametrize(
    "control_group",
    [
        pytest.param(f"/dev.slice/dev-background.slice/docker-{'b' * 64}.scope", id="other-container"),
        pytest.param("/dev.slice/dev-background.slice", id="slice-itself"),
        pytest.param(f"/dev.slice/dev-background.slice/docker-{CONTAINER_ID}.scope/child", id="descendant"),
    ],
)
def test_systemd_scope_path_refuses_a_cgroup_that_is_not_the_requested_scope(monkeypatch, control_group):
    _patch_scope(monkeypatch, control_group=control_group)

    with pytest.raises(RunnerError, match=f"does not end in docker-{CONTAINER_ID}.scope"):
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
    _patch_scope(monkeypatch)
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


@pytest.mark.parametrize(
    "control_group",
    [
        pytest.param(f"/system.slice/docker-{CONTAINER_ID}.scope", id="other-slice"),
        pytest.param(
            f"/dev.slice/dev-background.slice-evil/docker-{CONTAINER_ID}.scope",
            id="sibling-slice-with-shared-prefix",
        ),
    ],
)
def test_verify_running_container_refuses_scope_outside_selected_slice(monkeypatch, control_group):
    profile = dataclasses.replace(GENERIC_PROFILE, guest_memory_mib=2048, cpus=2)
    item = _running_container(profile)
    _patch_scope(monkeypatch, control_group=control_group)
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
