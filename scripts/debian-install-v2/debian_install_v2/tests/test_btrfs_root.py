"""IC-M1a: v2 on an ISO-installed, single-device btrfs root.

These are fake-boundary tests. Every argv passes HostActions validation, but
no host command runs and no real mount, subvolume, partition or filesystem is
touched.
"""
from __future__ import annotations

import json
from pathlib import Path
import shutil

import pytest

from debian_install_v2 import layout_policy
from debian_install_v2.actions import ActionError, HostActions, PlannedAction
from debian_install_v2.config import Config, ConfigError, load_config
from debian_install_v2.customscript import build_customscript_bundle
from debian_install_v2.installer import (
    INSTALL_COMPLETE_MARKER,
    ROLLBACK_STATE_COMMAND,
    HostIdentityRefusal,
    Installer,
    InstallerError,
)
from debian_install_v2.state import StateStore


COW_VOLUME = {"name": "@cow", "mount": "/srv/cow", "nocow": False}
NOCOW_VOLUME = {"name": "@cow-nocow", "mount": "/srv/cow-nocow", "nocow": True}
VALID_PARTITIONING = {
    "root": "*",
    "reserve_gb": 0,
    "data": {"kind": "subvolumes", "volumes": [COW_VOLUME]},
}
VALID_BTRFS_CONFIG = {
    "schema_version": 2,
    "fresh_install": True,
    "swap_policy": "off",
    "swap_partition_count": 0,
    "run_io_benchmark": False,
    "credential_mode": "systemd",
    "partitioning": VALID_PARTITIONING,
}
MANAGED_LIST = (
    "@os-000001-20261124-121500Z-host (running, default) "
    "kernel=6.12.0-1-amd64 created=2026-11-24T12:15:00Z reason=install os=Debian GNU/Linux\n"
)


class BoundaryActions(HostActions):
    """Scripted HostActions with an observable, validated argv boundary."""

    def __init__(self, *, outputs=None, errors=None, existing=None):
        super().__init__(dry_run=False)
        self.outputs = dict(outputs or {})
        self.errors = dict(errors or {})
        self.existing = set(existing or ())
        self.files: dict[str, str] = {}
        self.modes: dict[str, int] = {}

    def run(
        self,
        argv,
        description: str = "",
        dangerous: bool = False,
        input: str | None = None,
        timeout: float | None = None,
    ):
        self._validate(list(argv))
        key = tuple(argv)
        self.planned.append(PlannedAction(key, description or " ".join(argv), dangerous))
        if key in self.errors:
            raise self.errors[key]
        return self.outputs.get(key, "")

    def write_file(self, path: str, content: str, mode: int = 0o644) -> None:
        self.planned.append(PlannedAction(("/usr/bin/tee", path), f"write {path}", True))
        self.files[path] = content
        self.modes[path] = mode

    def remove_file(self, path: str) -> None:
        self.planned.append(PlannedAction(("/usr/bin/rm", "-f", path), f"remove {path}", True))

    def exists(self, path: str) -> bool:
        return path in self.existing


class RecordingState:
    def __init__(self, steps=None):
        self.data = {"steps": dict(steps or {})}
        self.saved: list[dict[str, object]] = []

    def load(self):
        return {"steps": dict(self.data["steps"])}

    def mark_step(self, name: str, status: str, detail: str = "") -> None:
        self.data["steps"][name] = {"status": status, "detail": detail}

    def save(self, **changes):
        self.saved.append(dict(changes))
        return {"steps": dict(self.data["steps"]), **changes}


def _btrfs_installer(
    tmp_path: Path,
    *,
    partitioning=None,
    actions=None,
    state=None,
    config_overrides=None,
) -> Installer:
    config_data = dict(VALID_BTRFS_CONFIG)
    if partitioning is not None:
        config_data["partitioning"] = partitioning
    config_data.update(config_overrides or {})
    config_data["state_dir"] = str(tmp_path / "state")
    config_data["log_dir"] = str(tmp_path / "logs")
    config = Config(**config_data)
    installer = Installer(config, actions or BoundaryActions(), inspect_host=False)
    installer.root_fs = "btrfs"
    installer.root_disk = "vda"
    installer.root_partition_path = "/dev/vda2"
    installer.root_number = 2
    installer.state = state or RecordingState()
    Path(config.state_dir).mkdir(parents=True, exist_ok=True)
    return installer


def _root_device_output(devids: tuple[str, ...] = ("1",)) -> str:
    devid_lines = "\n".join(
        f"\tdevid {devid} size 21474836480 used 1073741824 path /dev/vda{index}"
        for index, devid in enumerate(devids, start=1)
    )
    return f"Label: none  uuid: 00000000-0000-0000-0000-000000000000\n{devid_lines}\n"


def _assert_btrfs_command_allowlist(
    actions: BoundaryActions, allowed: set[str],
) -> None:
    tools = [Path(action.argv[0]).name for action in actions.planned]
    assert set(tools) <= allowed, actions.planned


# ---------------------------------------------------------------- discovery


def test_root_discovery_uses_nofsroot_and_reports_btrfs_without_subvol_suffix(tmp_path):
    findmnt_argv = ("/usr/bin/findmnt", "-n", "-o", "SOURCE,FSTYPE", "--nofsroot", "/")
    btrfs_show_argv = ("/usr/bin/btrfs", "filesystem", "show", "--raw", "/dev/vda2")
    actions = BoundaryActions(outputs={
        findmnt_argv: "/dev/vda2 btrfs\n",
        btrfs_show_argv: _root_device_output(),
    })
    installer = Installer(Config(state_dir=str(tmp_path / "state")), actions, inspect_host=False)

    assert installer._discover_root() == ("vda", "/dev/vda2", 2)
    assert installer.root_fs == "btrfs"
    assert [action.argv for action in actions.planned] == [findmnt_argv, btrfs_show_argv]


def test_without_nofsroot_a_btrfs_subvolume_suffix_is_rejected(tmp_path):
    findmnt_argv = ("/usr/bin/findmnt", "-n", "-o", "SOURCE,FSTYPE", "--nofsroot", "/")
    actions = BoundaryActions(outputs={
        findmnt_argv: "/dev/vda2[/@os-000001-20261124-121500Z-host] btrfs\n",
    })
    installer = Installer(Config(state_dir=str(tmp_path / "state")), actions, inspect_host=False)

    with pytest.raises(HostIdentityRefusal, match="subvolume suffix"):
        installer._discover_root()


def test_extra_findmnt_columns_are_malformed_not_multi_device(tmp_path):
    findmnt_argv = ("/usr/bin/findmnt", "-n", "-o", "SOURCE,FSTYPE", "--nofsroot", "/")
    actions = BoundaryActions(outputs={
        findmnt_argv: "/dev/vda2 /dev/vdb2 btrfs\n",
    })
    installer = Installer(Config(state_dir=str(tmp_path / "state")), actions, inspect_host=False)

    with pytest.raises(HostIdentityRefusal, match="root mount discovery is malformed"):
        installer._discover_root()


@pytest.mark.parametrize("devids", [(), ("1", "2")])
def test_btrfs_requires_exactly_one_backing_device(tmp_path, devids):
    findmnt_argv = ("/usr/bin/findmnt", "-n", "-o", "SOURCE,FSTYPE", "--nofsroot", "/")
    btrfs_show_argv = ("/usr/bin/btrfs", "filesystem", "show", "--raw", "/dev/vda2")
    actions = BoundaryActions(outputs={
        findmnt_argv: "/dev/vda2 btrfs\n",
        btrfs_show_argv: _root_device_output(tuple(devids)),
    })
    installer = Installer(Config(state_dir=str(tmp_path / "state")), actions, inspect_host=False)

    expected = "root_device_list_unavailable" if not devids else "root_multi_device"
    with pytest.raises(HostIdentityRefusal, match=expected):
        installer._discover_root()


def test_zfs_root_is_refused_with_root_fs_unsupported(tmp_path):
    findmnt_argv = ("/usr/bin/findmnt", "-n", "-o", "SOURCE,FSTYPE", "--nofsroot", "/")
    actions = BoundaryActions(outputs={findmnt_argv: "rpool/ROOT/debian zfs\n"})
    installer = Installer(Config(state_dir=str(tmp_path / "state")), actions, inspect_host=False)

    assert installer._discover_root() == ("", "rpool/ROOT/debian", 0)
    with pytest.raises(HostIdentityRefusal, match="root_fs_unsupported"):
        installer._validate_root_config()


# ---------------------------------------------------------- root validation


@pytest.mark.parametrize(
    ("schema_version", "partitioning", "swap_policy", "swap_count", "expected"),
    [
        (1, VALID_PARTITIONING, "off", 0, "btrfs_config_requires_schema_version_2"),
        (2, {**VALID_PARTITIONING, "root": 160}, "off", 0, "btrfs_root_resize_unsupported"),
        (2, {**VALID_PARTITIONING, "reserve_gb": 1}, "off", 0, "btrfs_reserve_unsupported"),
        (2, {**VALID_PARTITIONING, "reserve_gb": False}, "off", 0, "btrfs_reserve_unsupported"),
        (
            2,
            {
                **VALID_PARTITIONING,
                "data": {"kind": "partitions", "volumes": [
                    {"size": "*", "fs": "ext4", "mount": "/srv/x", "label": "X"},
                ]},
            },
            "off",
            0,
            "btrfs_data_kind_unsupported",
        ),
        (2, {**VALID_PARTITIONING, "data": {}}, "off", 0, "btrfs_data_kind_unsupported"),
        (2, VALID_PARTITIONING, "on", 4, "btrfs_swap_needs_p3"),
        (2, VALID_PARTITIONING, "off", 1, "btrfs_swap_needs_p3"),
    ],
)
def test_each_btrfs_layout_refusal_has_a_named_code(
    schema_version, partitioning, swap_policy, swap_count, expected
):
    with pytest.raises(layout_policy.LayoutPolicyError, match=expected):
        layout_policy.disk_spec(
            partitioning,
            root_fs="btrfs",
            schema_version=schema_version,
            swap_policy=swap_policy,
            swap_partition_count=swap_count,
        )


def test_schema_two_subvolumes_require_btrfs_and_schema_two():
    with pytest.raises(layout_policy.LayoutPolicyError, match="subvolumes_require_btrfs"):
        layout_policy.disk_spec(
            VALID_PARTITIONING,
            root_fs="ext4",
            schema_version=2,
            swap_policy="off",
            swap_partition_count=0,
        )
    with pytest.raises(layout_policy.LayoutPolicyError, match="subvolumes_require_schema_version_2"):
        layout_policy.disk_spec(
            VALID_PARTITIONING,
            schema_version=1,
            swap_policy="off",
            swap_partition_count=0,
        )


def test_ext4_keeps_positive_swap_count_and_requires_tail_swap_for_iobench():
    with pytest.raises(layout_policy.LayoutPolicyError, match="ext4_swap_partition_count_required"):
        layout_policy.disk_spec(
            {**VALID_PARTITIONING, "data": {"kind": "none"}},
            root_fs="ext4",
            schema_version=2,
            swap_policy="off",
            swap_partition_count=0,
        )
    with pytest.raises(layout_policy.LayoutPolicyError, match="io_benchmark_requires_tail_swap_span"):
        layout_policy.disk_spec(
            {**VALID_PARTITIONING, "data": {"kind": "none"}},
            root_fs="ext4",
            schema_version=2,
            swap_policy="off",
            swap_partition_count=4,
            run_io_benchmark=True,
        )
    with pytest.raises(layout_policy.LayoutPolicyError, match="io_benchmark_requires_tail_swap_span"):
        layout_policy.disk_spec(
            {"root": "*", "reserve_gb": 0, "data": {"kind": "none"}},
            root_fs="ext4",
            schema_version=2,
            swap_policy="on",
            swap_partition_count=0,
            run_io_benchmark=True,
        )


def test_other_root_filesystems_are_refused_by_name():
    with pytest.raises(layout_policy.LayoutPolicyError, match="root_fs_unsupported"):
        layout_policy.disk_spec(
            {}, root_fs="xfs", schema_version=2, swap_policy="on", swap_partition_count=4,
        )


def test_schema_one_still_loads_and_means_ext4():
    config = load_config(raw_json='{"schema_version":1}')
    assert config.schema_version == 1
    layout_policy.disk_spec(
        config.partitioning,
        root_fs="ext4",
        schema_version=config.schema_version,
        swap_policy=config.swap_policy,
        swap_partition_count=config.swap_partition_count,
    )
    with pytest.raises(layout_policy.LayoutPolicyError, match="data_kind_none_requires_schema_version_2"):
        layout_policy.disk_spec(
            {"data": {"kind": "none"}},
            schema_version=1,
            swap_policy="on",
            swap_partition_count=4,
        )


def test_btrfs_install_benchmark_keeps_its_named_refusal_without_the_legacy_key():
    with pytest.raises(layout_policy.LayoutPolicyError, match="btrfs_iobench_raw_unavailable"):
        layout_policy.disk_spec(
            {"root": "*", "reserve_gb": 0},
            root_fs="btrfs",
            schema_version=2,
            swap_policy="off",
            swap_partition_count=0,
            run_io_benchmark=True,
        )


def test_schema_two_btrfs_subvolume_config_loads_and_is_accepted():
    config = load_config(raw_json=json.dumps(VALID_BTRFS_CONFIG))
    spec = layout_policy.disk_spec(
        config.partitioning,
        root_fs="btrfs",
        schema_version=config.schema_version,
        swap_policy=config.swap_policy,
        swap_partition_count=config.swap_partition_count,
        run_io_benchmark=config.run_io_benchmark,
    )
    assert spec.data.kind == "subvolumes"
    assert spec.data.volumes[0].name == "@cow"
    assert spec.data.volumes[0].nocow is False
    assert spec.partition_count == 0
    assert layout_policy.data_area_packages(spec.data) == ["btrfs-progs"]


def test_data_none_is_accepted_on_btrfs():
    partitioning = {
        "root": "*", "reserve_gb": 0,
        "data": {"kind": "none"},
    }
    spec = layout_policy.disk_spec(
        partitioning,
        root_fs="btrfs",
        schema_version=2,
        swap_policy="off",
        swap_partition_count=0,
    )
    assert spec.data.kind == "none"


# ------------------------------------------------------------- exclusions


@pytest.mark.parametrize("mount", [
    "/", "/boot", "/etc", "/usr", "/usr/local", "/root", "/tmp", "/proc",
    "/sys", "/dev", "/run", "/bin", "/sbin", "/var", "/var/lib",
    "/var/lib/dpkg", "/var/lib/apt", "/var/lib/systemd", "/var/lib/cloud",
    "/var/cache/apt", "/srv", "/home", "/var/lib/docker", "/var/lib/containerd",
])
def test_subvolume_mount_at_or_above_an_excluded_path_is_refused(mount):
    partitioning = {
        **VALID_PARTITIONING,
        "data": {"kind": "subvolumes", "volumes": [
            {"name": "@cow", "mount": mount},
        ]},
    }
    with pytest.raises(layout_policy.LayoutPolicyError, match="mount"):
        layout_policy.disk_spec(partitioning, root_fs="btrfs", schema_version=2,
                                swap_policy="off", swap_partition_count=0)


@pytest.mark.parametrize(
    ("mount", "isolate_iso_rule"),
    [
        ("/var/www", False),
        ("/var/lib/containers", False),
        ("/var/lib/postgresql", False),
        ("/var/lib/mysql", False),
        ("/var/lib", True),
    ],
)
def test_iso_exclusions_refuse_paths_not_covered_by_general_mount_policy(
    mount, isolate_iso_rule, monkeypatch,
):
    if isolate_iso_rule:
        # /var/lib is also a general mount-policy refusal. Isolate C9 here so
        # the ancestor direction is pinned to the ISO exclusion message itself.
        monkeypatch.setattr(layout_policy, "_check_mount", lambda value, _what: value)
    partitioning = {
        **VALID_PARTITIONING,
        "data": {"kind": "subvolumes", "volumes": [
            {"name": "@cow", "mount": mount},
        ]},
    }
    with pytest.raises(
        layout_policy.LayoutPolicyError, match="at or above excluded path",
    ):
        layout_policy.disk_spec(
            partitioning, root_fs="btrfs", schema_version=2,
            swap_policy="off", swap_partition_count=0,
        )


@pytest.mark.parametrize(
    "mount",
    [
        "/srv/cow",
        "/home/alice/data",
        "/var/lib/postgresql/x",
    ],
)
def test_subvolume_mount_below_an_excluded_path_is_accepted(mount):
    partitioning = {
        **VALID_PARTITIONING,
        "data": {"kind": "subvolumes", "volumes": [
            {"name": "@cow", "mount": mount},
        ]},
    }
    spec = layout_policy.disk_spec(
        partitioning, root_fs="btrfs", schema_version=2,
        swap_policy="off", swap_partition_count=0,
    )
    assert spec.data.volumes[0].mount == mount


@pytest.mark.parametrize("mount", [
    "/etc/example",
    "/usr/example",
    "/boot/cache",
    "/tmp/scratch",
    "/proc/x",
    "/sys/x",
    "/dev/x",
    "/run/x",
    "/var/lib/dpkg/cache",
    "/var/log/other",
    "/var/lib/docker/volumes/scratch",
    "/var/lib/containerd",
])
def test_subvolume_mounts_keep_existing_protected_tree_checks(mount):
    partitioning = {
        **VALID_PARTITIONING,
        "data": {"kind": "subvolumes", "volumes": [
            {"name": "@cow", "mount": mount},
        ]},
    }
    with pytest.raises(layout_policy.LayoutPolicyError, match="mount"):
        layout_policy.disk_spec(
            partitioning, root_fs="btrfs", schema_version=2,
            swap_policy="off", swap_partition_count=0,
        )


# ---------------------------------------------------------- no disk mutation


def test_btrfs_stage_two_never_calls_ext4_layout_methods(tmp_path, monkeypatch):
    actions = BoundaryActions()
    installer = _btrfs_installer(tmp_path, actions=actions)
    for name in (
        "_configure_control_plane_memory_min", "_packages", "_configure_zswap",
        "_configure_cgroup2_flags", "_configure_ksm", "_configure_oomd",
        "_configure_fstrim", "_configure_auto_reboot", "_configure_iocost",
        "_configure_data_area", "_health_gate_swap_devices",
        "_verify_control_plane_memory_min", "_run_cpu_benchmark",
        "_release_apt_timers", "_cleanup_cloud_init_cache",
    ):
        monkeypatch.setattr(installer, name, lambda *args, **kwargs: None)
    for name in (
        "_apply_known_swap_shape", "_grow_root_to_plan",
        "_verify_and_apply_root_shrink", "_ensure_disk_transaction_manifest",
        "_configure_root_cap_boot_guards", "_plan_root_shrink",
        "_disable_active_swap", "_activate_swap_partitions",
    ):
        monkeypatch.setattr(installer, name, lambda name=name: (_ for _ in ()).throw(
            AssertionError(f"ext4-only {name} ran on btrfs")
        ))

    installer._stage2()

    _assert_btrfs_command_allowlist(actions, {"tee", "env"})
    assert installer._root_policy() == "untouched"
    assert installer._root_guarded() is False
    plan = installer.show_plan()
    assert plan["root_fs"] == "btrfs"
    assert plan["data_partitions"] == []
    assert plan["new_root_size_sectors"] == 0
    assert "sfdisk_plan" not in plan


def test_btrfs_stage_one_records_resize_skips_without_calling_ext4_hooks(tmp_path, monkeypatch):
    actions = BoundaryActions()
    installer = _btrfs_installer(
        tmp_path,
        actions=actions,
        state=RecordingState({"root_shrink": {"status": "planned"}}),
    )
    for name in ("_configure_root_cap_boot_guards", "_plan_root_shrink"):
        monkeypatch.setattr(installer, name, lambda name=name: (_ for _ in ()).throw(
            AssertionError(f"ext4-only {name} ran on btrfs")
        ))

    installer._mark_btrfs_stage1_layout_skips()

    step = installer.state.data["steps"]["root_shrink"]
    assert step["status"] == "skipped"
    assert "btrfs_root_resize_unsupported" in step["detail"]
    assert not actions.planned


def test_stage_one_btrfs_branch_does_not_plan_shrink_or_partition(tmp_path, monkeypatch):
    actions = BoundaryActions()
    installer = _btrfs_installer(tmp_path, actions=actions)
    events = []
    def record(*_args, _name, **_kwargs):
        events.append(_name)

    for name in (
        "_secure_bootstrap_files", "_configure_persistent_ssh_keys", "_configure_controller_ssh_key",
        "_configure_journald", "_hold_apt_timers", "_configure_apt", "_packages",
        "_install_notify_helper", "_configure_users", "_install_docker",
        "_configure_docker_daemon", "_configure_docker_cleanup", "_configure_apt_auto_upgrade",
        "_upgrade_at_install", "_configure_control_plane_memory_min", "_install_stage2", "_reboot",
    ):
        monkeypatch.setattr(installer, name, lambda *args, name=name, **kwargs: record(*args, _name=name, **kwargs))

    installer._stage1()

    assert events[-3:] == ["_configure_control_plane_memory_min", "_install_stage2", "_reboot"]
    assert "_resolve_root_shrink_plan" not in events
    assert "_remove_managed_swap_backings" not in events
    assert installer.state.data["steps"]["root_cap_guards"]["status"] == "skipped"
    assert installer.state.data["steps"]["root_shrink"]["status"] == "skipped"
    _assert_btrfs_command_allowlist(actions, set())


# -------------------------------------------------------------- subvolumes
def test_nocow_is_set_on_the_empty_subvolume_before_any_file_activity(tmp_path):
    show = ("/usr/bin/btrfs", "subvolume", "show", "/.top/@cow")
    create = ("/usr/bin/btrfs", "subvolume", "create", "/.top/@cow")
    chattr = ("/usr/bin/chattr", "+C", "/.top/@cow")
    actions = BoundaryActions(errors={show: ActionError("action failed (1): not a subvolume")})
    installer = _btrfs_installer(tmp_path, actions=actions)

    assert installer._ensure_btrfs_subvolume("@cow", True) == "/.top/@cow"

    assert [action.argv for action in actions.planned] == [show, create, chattr]
    assert not actions.files
    assert all(action.dangerous for action in actions.planned[1:])


def test_subvolume_rerun_accepts_the_same_nocow_property_without_mutation(tmp_path):
    show = ("/usr/bin/btrfs", "subvolume", "show", "/.top/@cow")
    lsattr = ("/usr/bin/lsattr", "-d", "/.top/@cow")
    actions = BoundaryActions(
        outputs={show: "Name: @cow\n", lsattr: "---------------C------ /.top/@cow\n"},
        existing={"/.top/@cow"},
    )
    installer = _btrfs_installer(tmp_path, actions=actions)

    assert installer._ensure_btrfs_subvolume("@cow", True) == "/.top/@cow"
    assert [action.argv for action in actions.planned] == [show, lsattr]


def test_subvolume_rerun_with_different_nocow_property_is_refused(tmp_path):
    show = ("/usr/bin/btrfs", "subvolume", "show", "/.top/@cow")
    lsattr = ("/usr/bin/lsattr", "-d", "/.top/@cow")
    find = (
        "/usr/bin/find", "/.top/@cow",
        "-mindepth", "1", "-maxdepth", "1", "-print", "-quit",
    )
    actions = BoundaryActions(
        outputs={
            show: "Name: @cow\n",
            lsattr: "---------------------- /.top/@cow\n",
            find: "/.top/@cow/data\n",
        },
        existing={"/.top/@cow"},
    )
    installer = _btrfs_installer(tmp_path, actions=actions)

    with pytest.raises(InstallerError, match="btrfs_subvolume_properties_differ"):
        installer._ensure_btrfs_subvolume("@cow", True)


def test_existing_empty_subvolume_recovers_missing_nocow(tmp_path):
    show = ("/usr/bin/btrfs", "subvolume", "show", "/.top/@cow")
    lsattr = ("/usr/bin/lsattr", "-d", "/.top/@cow")
    find = (
        "/usr/bin/find", "/.top/@cow",
        "-mindepth", "1", "-maxdepth", "1", "-print", "-quit",
    )
    chattr = ("/usr/bin/chattr", "+C", "/.top/@cow")
    actions = BoundaryActions(
        outputs={show: "Name: @cow\n", lsattr: "---------------------- /.top/@cow\n"},
        existing={"/.top/@cow"},
    )
    installer = _btrfs_installer(tmp_path, actions=actions)

    assert installer._ensure_btrfs_subvolume("@cow", True) == "/.top/@cow"
    assert [action.argv for action in actions.planned] == [show, lsattr, find, chattr]


def test_existing_non_subvolume_path_is_a_named_property_refusal(tmp_path):
    show = ("/usr/bin/btrfs", "subvolume", "show", "/.top/@cow")
    actions = BoundaryActions(
        errors={show: ActionError("action failed (1): not a subvolume")},
        existing={"/.top/@cow"},
    )
    installer = _btrfs_installer(tmp_path, actions=actions)

    with pytest.raises(InstallerError, match="btrfs_subvolume_properties_differ"):
        installer._ensure_btrfs_subvolume("@cow", False)


def test_top_level_mount_must_be_subvolid_five(tmp_path):
    argv = ("/usr/bin/findmnt", "-n", "-o", "SOURCE,FSTYPE,FSROOT", "/.top")
    actions = BoundaryActions(outputs={argv: "/dev/vda2 btrfs /@rootfs\n"})
    installer = _btrfs_installer(tmp_path, actions=actions)

    with pytest.raises(InstallerError, match="btrfs_top_level_required"):
        installer._require_btrfs_top_level()


@pytest.mark.parametrize(
    "row",
    [
        "/dev/vda2 tmpfs /\n",
        "/dev/vdb2 btrfs /\n",
        "/dev/vda3 btrfs /\n",
        "/dev/vdas2 btrfs /\n",
        "tmpfs tmpfs /\n",
    ],
)
def test_top_level_mount_must_be_the_btrfs_root_partition(tmp_path, row):
    argv = ("/usr/bin/findmnt", "-n", "-o", "SOURCE,FSTYPE,FSROOT", "/.top")
    actions = BoundaryActions(outputs={argv: row})
    installer = _btrfs_installer(tmp_path, actions=actions)

    with pytest.raises(InstallerError, match="btrfs_top_level_required") as exc_info:
        installer._require_btrfs_top_level()
    assert "/dev/vda2" in str(exc_info.value)


def test_fstab_subvolume_lines_use_root_options_plus_user_subvol_rm_allowed(tmp_path, monkeypatch):
    top = ("/usr/bin/findmnt", "-n", "-o", "SOURCE,FSTYPE,FSROOT", "/.top")
    show_cow = ("/usr/bin/btrfs", "subvolume", "show", "/.top/@cow")
    show_nocow = ("/usr/bin/btrfs", "subvolume", "show", "/.top/@cow-nocow")
    create_cow = ("/usr/bin/btrfs", "subvolume", "create", "/.top/@cow")
    create_nocow = ("/usr/bin/btrfs", "subvolume", "create", "/.top/@cow-nocow")
    chattr_nocow = ("/usr/bin/chattr", "+C", "/.top/@cow-nocow")
    findmnt_cow = ("/usr/bin/findmnt", "-rn", "-o", "SOURCE,FSTYPE,TARGET,FSROOT", "/srv/cow")
    findmnt_nocow = (
        "/usr/bin/findmnt", "-rn", "-o", "SOURCE,FSTYPE,TARGET,FSROOT", "/srv/cow-nocow",
    )
    actions = BoundaryActions(
        outputs={top: "/dev/vda2 btrfs /\n"},
        errors={show_cow: ActionError("action failed (1): no subvolume"), show_nocow: ActionError("action failed (1): no subvolume"),
                findmnt_cow: ActionError("action failed (1): not mounted"),
                findmnt_nocow: ActionError("action failed (1): not mounted")},
    )
    installer = _btrfs_installer(
        tmp_path,
        actions=actions,
        partitioning={**VALID_PARTITIONING, "data": {
            "kind": "subvolumes", "volumes": [COW_VOLUME, NOCOW_VOLUME],
        }},
    )
    monkeypatch.setattr(
        installer,
        "_read_fstab_lines",
        lambda: ["UUID=root / btrfs noatime,compress=zstd:3 0 0"],
    )
    monkeypatch.setattr(installer, "_packages", lambda *args, **kwargs: None)

    installer._configure_btrfs_subvolumes(installer._data_area_cfg())

    fstab = actions.files["/etc/fstab"]
    assert "UUID=root /srv/cow btrfs noatime,compress=zstd:3,user_subvol_rm_allowed,subvol=@cow 0 0" in fstab
    assert "UUID=root /srv/cow-nocow btrfs noatime,compress=zstd:3,user_subvol_rm_allowed,subvol=@cow-nocow 0 0" in fstab
    assert create_cow in [action.argv for action in actions.planned]
    assert create_nocow in [action.argv for action in actions.planned]
    assert chattr_nocow in [action.argv for action in actions.planned]
    assert ("/usr/bin/mount", "/srv/cow") in [action.argv for action in actions.planned]
    assert ("/usr/bin/mount", "/srv/cow-nocow") in [action.argv for action in actions.planned]
    assert installer.state.data["steps"]["data_area"]["status"] == "success"


def test_existing_mount_is_accepted_only_for_the_configured_subvolume(tmp_path, monkeypatch):
    top = ("/usr/bin/findmnt", "-n", "-o", "SOURCE,FSTYPE,FSROOT", "/.top")
    show = ("/usr/bin/btrfs", "subvolume", "show", "/.top/@cow")
    lsattr = ("/usr/bin/lsattr", "-d", "/.top/@cow")
    findmnt = ("/usr/bin/findmnt", "-rn", "-o", "SOURCE,FSTYPE,TARGET,FSROOT", "/srv/cow")
    actions = BoundaryActions(outputs={
        top: "/dev/vda2 btrfs /\n",
        show: "Name: @cow\n",
        lsattr: "---------------------- /.top/@cow\n",
        findmnt: "/dev/vda2[/@cow] btrfs /srv/cow /@cow\n",
    })
    installer = _btrfs_installer(tmp_path, actions=actions)
    monkeypatch.setattr(installer, "_read_fstab_lines", lambda: ["UUID=root / btrfs defaults 0 0"])
    monkeypatch.setattr(installer, "_packages", lambda *args, **kwargs: None)

    installer._configure_btrfs_subvolumes(installer._data_area_cfg())

    assert ("/usr/bin/mount", "/srv/cow") not in [action.argv for action in actions.planned]
    actions.outputs[findmnt] = "/dev/vda2[/@rootfs] btrfs /srv/cow /@rootfs\n"
    actions.planned.clear()
    with pytest.raises(InstallerError, match="btrfs_subvolume_mount_differs"):
        installer._configure_btrfs_subvolumes(installer._data_area_cfg())
    assert ("/usr/bin/mount", "/srv/cow") not in [action.argv for action in actions.planned]
    actions.outputs[findmnt] = "/dev/vdb2[/@cow] btrfs /srv/cow /@cow\n"
    actions.planned.clear()
    with pytest.raises(InstallerError, match="btrfs_subvolume_mount_differs"):
        installer._configure_btrfs_subvolumes(installer._data_area_cfg())
    assert ("/usr/bin/mount", "/srv/cow") not in [action.argv for action in actions.planned]


def test_subvolume_names_are_top_level_and_reserved_names_are_refused():
    for name in ("home", "@/etc", "@os-000001-20261124-121500Z-host", "@docker", "a" * 50):
        partitioning = {
            **VALID_PARTITIONING,
            "data": {"kind": "subvolumes", "volumes": [{"name": name, "mount": "/srv/x"}]},
        }
        with pytest.raises(layout_policy.LayoutPolicyError, match="name"):
            layout_policy.disk_spec(
                partitioning, root_fs="btrfs", schema_version=2,
                swap_policy="off", swap_partition_count=0,
            )


def test_multiple_data_subvolumes_do_not_accept_nested_mounts():
    partitioning = {
        **VALID_PARTITIONING,
        "data": {"kind": "subvolumes", "volumes": [
            {"name": "@a", "mount": "/srv/a"},
            {"name": "@b", "mount": "/srv/a/nested"},
        ]},
    }
    with pytest.raises(layout_policy.LayoutPolicyError, match="mount point collision"):
        layout_policy.disk_spec(
            partitioning, root_fs="btrfs", schema_version=2,
            swap_policy="off", swap_partition_count=0,
        )


# ------------------------------------------------------ swap / benchmark / cloud-init


def test_btrfs_swap_benchmark_and_health_skips_are_named(tmp_path, monkeypatch):
    stage2_log = tmp_path / "stage2.log"
    stage2_log.write_text("running\n")
    actions = BoundaryActions()
    installer = _btrfs_installer(
        tmp_path,
        actions=actions,
        config_overrides={"stage2_output": str(stage2_log), "run_zswap_config": False},
    )

    for name in (
        "_configure_control_plane_memory_min", "_packages", "_configure_zswap",
        "_configure_cgroup2_flags", "_configure_ksm", "_configure_oomd",
        "_configure_fstrim", "_configure_auto_reboot", "_configure_iocost",
        "_configure_data_area", "_verify_control_plane_memory_min",
        "_run_cpu_benchmark", "_release_apt_timers", "_cleanup_cloud_init_cache",
    ):
        monkeypatch.setattr(installer, name, lambda *args, **kwargs: None)
    installer._stage2()

    steps = installer.state.data["steps"]
    assert "btrfs_swap_needs_p3" in steps["swap_partitions"]["detail"]
    assert "iobench_raw_unavailable_on_btrfs" in steps["io_benchmark"]["detail"]
    assert "Q-A" in steps["io_benchmark"]["detail"]
    assert "btrfs_swap_needs_p3" in steps["health_gate"]["detail"]
    _assert_btrfs_command_allowlist(actions, {"tee"})


def test_btrfs_iocost_ignores_a_stale_benchmark_file_and_names_the_skip(tmp_path):
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True)
    (state_dir / "io-benchmark.json").write_text(json.dumps({
        "results": {
            key: 1
            for key in (
                "rbps", "wbps", "rseqiops", "rrandiops", "wseqiops", "wrandiops",
            )
        }
    }), encoding="utf-8")
    actions = BoundaryActions(existing={"/etc/systemd/system/vbpub-iocost.service"})
    installer = _btrfs_installer(tmp_path, actions=actions)

    installer._configure_iocost()

    step = installer.state.data["steps"]["iocost"]
    assert step["status"] == "skipped"
    assert "iocost_not_configured_btrfs" in step["detail"]
    assert "Q-A" in step["detail"]
    assert actions.planned[0].argv == (
        "/usr/bin/systemctl", "disable", "vbpub-iocost.service",
    )
    assert not actions.files


def test_cloud_init_cleanup_absent_is_a_named_noop(tmp_path):
    actions = BoundaryActions(existing=set())
    installer = _btrfs_installer(
        tmp_path,
        actions=actions,
        config_overrides={"cloud_init_cleanup_mode": "clean"},
    )

    installer._cleanup_cloud_init_cache()

    step = installer.state.data["steps"]["cloud_init_cache_cleanup"]
    assert step["status"] == "skipped"
    assert "cloud_init_absent" in step["detail"]
    assert not actions.planned


# --------------------------------------------------------------- snapshots


def _snapshot_actions(**overrides) -> BoundaryActions:
    list_argv = ("/usr/bin/env", "-u", "DR_STATE", ROLLBACK_STATE_COMMAND, "list")
    snapshot_argv = (
        "/usr/bin/env", "-u", "DR_STATE", ROLLBACK_STATE_COMMAND,
        "snapshot", "--reason", "host-setup",
    )
    outputs = {list_argv: MANAGED_LIST}
    errors = {}
    outputs.update(overrides.pop("outputs", {}))
    errors.update(overrides.pop("errors", {}))
    return BoundaryActions(
        outputs=outputs,
        errors=errors,
        existing={ROLLBACK_STATE_COMMAND},
        **overrides,
    )


def _resume_btrfs_installer(
    tmp_path: Path, actions: BoundaryActions, monkeypatch,
) -> Installer:
    installer = _btrfs_installer(tmp_path, actions=actions)
    store = StateStore(installer.config.state_dir)
    store.save_new(StateStore.new(installer.config))
    installer.state = store
    for name in (
        "_configure_control_plane_memory_min", "_packages", "_configure_zswap",
        "_configure_cgroup2_flags", "_configure_ksm", "_configure_oomd",
        "_configure_fstrim", "_configure_auto_reboot", "_configure_iocost",
        "_configure_data_area", "_health_gate_swap_devices",
        "_verify_control_plane_memory_min", "_run_cpu_benchmark",
        "_release_apt_timers", "_cleanup_cloud_init_cache",
    ):
        monkeypatch.setattr(installer, name, lambda *args, **kwargs: None)
    return installer


def test_host_setup_snapshot_runs_once_and_is_the_last_successful_step(tmp_path):
    actions = _snapshot_actions()
    installer = _btrfs_installer(tmp_path, actions=actions)
    installer._configure_control_plane_memory_min = lambda: installer._mark_step("memory", "success")
    installer._packages = lambda *args, **kwargs: installer._mark_step("packages", "success")
    for name in (
        "_configure_zswap", "_configure_cgroup2_flags", "_configure_ksm",
        "_configure_oomd", "_configure_fstrim", "_configure_auto_reboot",
        "_run_io_benchmark", "_configure_iocost", "_configure_data_area",
        "_health_gate_swap_devices", "_verify_control_plane_memory_min",
        "_run_cpu_benchmark", "_release_apt_timers", "_cleanup_cloud_init_cache",
    ):
        setattr(installer, name, lambda name=name: None)

    installer._stage2()

    steps = installer.state.data["steps"]
    successful = [name for name, value in steps.items() if value["status"] == "success"]
    assert successful[-1] == "host_setup_snapshot"
    snapshot_calls = [
        action.argv for action in actions.planned
        if action.argv[4:] == ("snapshot", "--reason", "host-setup")
    ]
    assert len(snapshot_calls) == 1
    assert actions.planned[-1].argv == (
        "/usr/bin/env", "-u", "DR_STATE", ROLLBACK_STATE_COMMAND,
        "snapshot", "--reason", "host-setup",
    )
    assert actions.planned[-1].dangerous is True
    _assert_btrfs_command_allowlist(actions, {"env", "tee"})


def test_successful_host_setup_snapshot_is_not_repeated_on_retry(tmp_path):
    actions = _snapshot_actions()
    installer = _btrfs_installer(tmp_path, actions=actions)
    installer._snapshot_host_setup_once()
    first_calls = list(actions.planned)
    installer._snapshot_host_setup_once()

    assert actions.planned == first_calls


def test_host_setup_snapshot_failure_is_visible_and_marks_the_step_failed(tmp_path):
    snapshot_argv = (
        "/usr/bin/env", "-u", "DR_STATE", ROLLBACK_STATE_COMMAND,
        "snapshot", "--reason", "host-setup",
    )
    actions = _snapshot_actions(errors={snapshot_argv: ActionError("snapshot failed")})
    installer = _btrfs_installer(tmp_path, actions=actions, state=RecordingState())

    with pytest.raises(ActionError, match="snapshot failed"):
        installer._snapshot_host_setup_once()

    step = installer.state.data["steps"]["host_setup_snapshot"]
    assert step["status"] == "failed"
    assert "snapshot failed" in step["detail"]


def test_snapshot_step_is_pending_when_the_snapshot_command_runs(tmp_path):
    snapshot_argv = (
        "/usr/bin/env", "-u", "DR_STATE", ROLLBACK_STATE_COMMAND,
        "snapshot", "--reason", "host-setup",
    )
    actions = _snapshot_actions()
    installer = _btrfs_installer(tmp_path, actions=actions)
    observed = []
    real_run = actions.run

    def run(argv, *args, **kwargs):
        if tuple(argv) == snapshot_argv:
            observed.append(
                installer.state.load()["steps"]["host_setup_snapshot"]["status"]
            )
        return real_run(argv, *args, **kwargs)

    actions.run = run
    installer._snapshot_host_setup_once()

    assert observed == ["run"]
    assert installer.state.data["steps"]["host_setup_snapshot"]["status"] == "success"


def test_layer_absent_or_unmanaged_root_takes_no_snapshot(tmp_path):
    actions = BoundaryActions(existing=set())
    installer = _btrfs_installer(tmp_path, actions=actions)
    installer._snapshot_host_setup_once()
    assert installer.state.data["steps"]["host_setup_snapshot"]["status"] == "skipped"
    assert "rollback_layer_absent" in installer.state.data["steps"]["host_setup_snapshot"]["detail"]
    assert not actions.planned

    list_argv = ("/usr/bin/env", "-u", "DR_STATE", ROLLBACK_STATE_COMMAND, "list")
    actions = BoundaryActions(
        outputs={
            list_argv: (
                "@os-000001-20261124-121500Z-host "
                "kernel=6.12.0-1-amd64 reason=running\n"
            )
        },
        existing={ROLLBACK_STATE_COMMAND, "/usr/local/sbin/dr-state"},
    )
    installer = _btrfs_installer(tmp_path, actions=actions)
    installer._snapshot_host_setup_once()
    assert installer.state.data["steps"]["host_setup_snapshot"]["status"] == "skipped"
    assert "rollback_layer_unmanaged" in installer.state.data["steps"]["host_setup_snapshot"]["detail"]
    assert [action.argv for action in actions.planned] == [list_argv]


def test_resume_completes_marker_before_the_last_snapshot_step(tmp_path, monkeypatch):
    actions = _snapshot_actions()
    installer = _resume_btrfs_installer(tmp_path, actions, monkeypatch)
    marker = Path(installer.config.state_dir) / "stage2_done"
    snapshot_probe = {}
    real_snapshot = installer._snapshot_host_setup_once

    def snapshot():
        snapshot_probe["marker"] = marker.exists()
        snapshot_probe["state"] = installer.state.load()
        real_snapshot()

    monkeypatch.setattr(installer, "_snapshot_host_setup_once", snapshot)

    assert installer.resume() is True

    assert snapshot_probe["marker"] is True
    assert snapshot_probe["state"]["phase"] == "done"
    assert snapshot_probe["state"]["status"] == "success"
    assert actions.planned[-1].argv[4:] == ("snapshot", "--reason", "host-setup")


def test_resume_failed_snapshot_removes_marker_and_fails_stage_two(tmp_path, monkeypatch):
    snapshot_argv = (
        "/usr/bin/env", "-u", "DR_STATE", ROLLBACK_STATE_COMMAND,
        "snapshot", "--reason", "host-setup",
    )
    actions = _snapshot_actions(errors={snapshot_argv: ActionError("snapshot failed")})
    installer = _resume_btrfs_installer(tmp_path, actions, monkeypatch)
    marker = Path(installer.config.state_dir) / "stage2_done"

    with pytest.raises(ActionError, match="snapshot failed"):
        installer.resume()

    assert not marker.exists()
    state = installer.state.load()
    assert state["status"] == "failed"
    assert state["phase"] == "stage2"
    assert "snapshot failed" in state["last_error"]
    assert actions.planned[-1].argv[4:] == ("snapshot", "--reason", "host-setup")


def test_btrfs_complete_summary_names_the_layer_skip(tmp_path, monkeypatch):
    actions = BoundaryActions(existing=set())
    installer = _resume_btrfs_installer(tmp_path, actions, monkeypatch)
    sent = []
    monkeypatch.setattr(installer, "_notify", lambda message, **_kwargs: sent.append(message))
    monkeypatch.setattr(Installer, "_notifications_enabled", property(lambda self: True))
    monkeypatch.setattr("debian_install_v2.installer.collect_host_facts", lambda _installer: {})
    monkeypatch.setattr(
        "debian_install_v2.installer.format_facts_html", lambda _facts: "",
    )

    installer.resume()

    assert any(
        "Install complete" in message and "rollback_layer_absent" in message
        for message in sent
    ), sent


# ------------------------------------------------------------- stage2 rerun


def test_stage_two_rerun_after_completion_marker_plans_nothing(tmp_path, monkeypatch):
    actions = BoundaryActions()
    installer = _btrfs_installer(tmp_path, actions=actions)
    marker = Path(installer.config.state_dir) / "stage2_done"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.touch()
    monkeypatch.setattr(installer, "_stage2", lambda: (_ for _ in ()).throw(AssertionError("stage2 reran")))
    monkeypatch.setattr(installer, "_secure_bootstrap_files", lambda: (_ for _ in ()).throw(
        AssertionError("bootstrap cleanup reran")
    ))
    monkeypatch.setattr(installer, "_snapshot_host_setup_once", lambda: (_ for _ in ()).throw(
        AssertionError("snapshot reran")
    ))
    monkeypatch.setattr(installer.state, "load", lambda: (_ for _ in ()).throw(AssertionError("state loaded")))

    assert installer.resume() is False

    assert not actions.planned


def test_completed_resume_skips_host_discovery(tmp_path):
    actions = BoundaryActions(errors={
        ("/usr/bin/findmnt", "-n", "-o", "SOURCE,FSTYPE", "--nofsroot", "/"):
            HostIdentityRefusal("host discovery must not run"),
    })
    installer = _btrfs_installer(tmp_path, actions=actions)
    marker = Path(installer.config.state_dir) / "stage2_done"
    marker.touch()

    completed = Installer(installer.config, actions, inspect_host=False)
    completed._inspect_on_resume = True

    assert completed.stage2_complete is True
    assert completed.resume() is False
    assert not actions.planned


# ------------------------------------------------------- stage2 unit / env


def test_stage_two_unit_has_the_exact_firstboot_cleanup_line(tmp_path):
    actions = BoundaryActions()
    installer = _btrfs_installer(tmp_path, actions=actions)
    installer._install_stage2()

    unit = actions.files["/etc/systemd/system/vbpub-bootstrap-stage2.service"]
    completion = f"{installer.config.state_dir}/stage2_done"
    expected = (
        "ExecStartPost=/bin/sh -c 'if [ -e "
        f"{completion}"
        " ]; then exec /usr/bin/rm -f -- "
        "/root/.vbpub/firstboot.json /root/.vbpub/firstboot.started; fi'"
    )
    cleanup_lines = [line for line in unit.splitlines() if line.startswith("ExecStartPost=/bin/sh -c")]
    assert cleanup_lines == [expected]
    assert "Environment=DR_STATE=none" in unit.splitlines()
    assert "%" not in cleanup_lines[0]


def test_stage_two_unit_full_render_is_golden(tmp_path):
    actions = BoundaryActions()
    installer = _btrfs_installer(tmp_path, actions=actions)
    installer._install_stage2()

    unit = actions.files["/etc/systemd/system/vbpub-bootstrap-stage2.service"]
    working_directory = str(Path(__file__).resolve().parents[2])
    python = shutil.which("python3") or "/usr/bin/python3"
    state_dir = installer.config.state_dir
    expected = f"""\
[Unit]
Description=vbpub debian install stage2
After=network-online.target local-fs.target
Wants=network-online.target
ConditionPathExists={state_dir}/stage1_done
ConditionPathExists=!{state_dir}/stage2_done
OnFailure=vbpub-bootstrap-failed@%n.service

[Service]
Type=oneshot
EnvironmentFile=-/etc/vbpub/bootstrap.env
Environment=DR_STATE=none
WorkingDirectory={working_directory}
# ONE launch path: the installed entrypoint puts the cli-extended wheel beside
# it on sys.path. `python3 -m debian_install_v2...` bypasses that and crashes
# with ModuleNotFoundError: cli_extended (live v1001 2026-10-06).
ExecStart={python} {working_directory}/debian-install-v2.py resume --yes
ExecStartPost=/bin/sh -c 'if [ -e {state_dir}/stage2_done ]; then exec /usr/bin/rm -f -- /root/.vbpub/firstboot.json /root/.vbpub/firstboot.started; fi'
TimeoutStartSec=infinity
StandardOutput=append:{installer.config.stage2_output}
StandardError=append:{installer.config.stage2_output}

[Install]
WantedBy=multi-user.target
"""
    assert unit == expected


def test_stage_units_export_dr_state_none(tmp_path):
    actions = BoundaryActions()
    installer = _btrfs_installer(tmp_path, actions=actions)
    installer._install_stage2()
    unit = actions.files["/etc/systemd/system/vbpub-bootstrap-stage2.service"]
    bundle = build_customscript_bundle(
        installer.config,
        repo_url="https://example.test/repo.git",
        repo_branch="main",
        repo_commit=None,
        bootstrap_url="https://example.test/bootstrap.py",
    )

    assert "Environment=DR_STATE=none" in unit
    assert "DR_STATE=none" in bundle["customScript"]


def test_stage2_btrfs_writes_schema_two_completion_marker(tmp_path, monkeypatch):
    actions = BoundaryActions()
    installer = _btrfs_installer(tmp_path, actions=actions)
    for name in (
        "_configure_control_plane_memory_min", "_packages", "_configure_zswap",
        "_configure_cgroup2_flags", "_configure_ksm", "_configure_oomd",
        "_configure_fstrim", "_configure_auto_reboot", "_run_io_benchmark",
        "_configure_iocost", "_configure_data_area", "_health_gate_swap_devices",
        "_verify_control_plane_memory_min", "_run_cpu_benchmark",
        "_release_apt_timers", "_cleanup_cloud_init_cache",
    ):
        monkeypatch.setattr(installer, name, lambda *args, **kwargs: None)
    installer._stage2()

    assert actions.files[INSTALL_COMPLETE_MARKER] == "schema_version=2\n"
    assert installer.state.saved[-1] == {"phase": "done", "status": "success"}


def test_invalid_schema_versions_still_fail_config_load():
    with pytest.raises(ConfigError, match="schema_version must be one of 1, 2"):
        load_config(raw_json='{"schema_version":3}')


@pytest.mark.parametrize("state_dir", [
    "/var/lib/v%b",
    "/var/lib/a b",
    "/var/lib/a'b",
    "/var/lib/$b",
    "/var/lib/*",
    "/var/lib/../escape",
])
def test_state_dir_outside_the_strict_safe_path_pattern_is_refused(state_dir):
    with pytest.raises(ConfigError, match="unsafe_state_path"):
        load_config(raw_json=json.dumps({"state_dir": state_dir}))
