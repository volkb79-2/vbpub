from __future__ import annotations

import asyncio
import importlib.util
import json
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
from types import SimpleNamespace

import pytest


PATCHER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PATCHER_DIR))
sys.path.insert(0, str(PATCHER_DIR / "runtime"))
import iso_profiles  # noqa: E402


def _load_runtime_module(name: str):
    path = PATCHER_DIR / "runtime" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"vanilla_iso_runtime_{name}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


safety = _load_runtime_module("safety")
sys.modules["safety"] = safety
relaxation = _load_runtime_module("relaxation")
dispatch = _load_runtime_module("dispatch")
autoinstall = _load_runtime_module("autoinstall")


def test_adapter_package_paths_are_relative_to_the_mounted_usr_dataset():
    assert relaxation.DIST_PACKAGES.as_posix() == "lib/python3/dist-packages"


def _disk(name: str, *, model: str = "QEMU HARDDISK", size: int | None = None, removable: bool = False):
    return SimpleNamespace(
        name=name,
        model=model,
        size=size if size is not None else safety.MIN_TARGET_BYTES + 2_000_000_000,
        removable=removable,
    )


def _inventory(*targets: dict, source_type: str = "disk"):
    source = {
        "name": "sda",
        "path": "/dev/sda",
        "type": source_type,
        "size": 4_000_000_000,
        "model": "INSTALL MEDIA",
        "rm": 1,
        "ro": 0,
        "mountpoints": None,
        "children": [
            {
                "name": "sda1",
                "path": "/dev/sda1",
                "type": "part",
                "pkname": "sda",
                "mountpoints": ["/cdrom"],
            }
        ],
    }
    return {
        "blockdevices": [
            source,
            *[
                {
                    "name": item["name"],
                    "path": f"/dev/{item['name']}",
                    "type": "disk",
                    "size": item.get("size", safety.MIN_TARGET_BYTES + 2_000_000_000),
                    "model": item.get("model", "QEMU HARDDISK"),
                    "tran": item.get("tran", "sata"),
                    "rm": item.get("rm", 0),
                    "ro": item.get("ro", 0),
                    "mountpoints": item.get("mountpoints"),
                    "children": [
                        {
                            "name": f"{item['name']}1",
                            "path": f"/dev/{item['name']}1",
                            "type": "part",
                            "pkname": item["name"],
                            "mountpoints": item.get("child_mountpoints"),
                        }
                    ],
                }
                for item in targets
            ],
        ]
    }


def _empty_zfs_labels(document):
    labels = {}

    def visit(nodes):
        for node in nodes:
            if node.get("type") not in {"rom", "loop"}:
                labels[node["name"]] = ()
            visit(node.get("children") or [])

    visit(document["blockdevices"])
    return labels


def test_selects_one_fixed_target_and_excludes_installer_media():
    target = _disk("sdb")

    selected = safety.select_target_disks(
        _inventory({"name": "sdb"}),
        [target],
        "/dev/sda1",
        _empty_zfs_labels(_inventory({"name": "sdb"})),
    )

    assert selected == [target]


def test_accepts_unknown_model_for_one_unambiguously_matched_disk():
    size = safety.MIN_TARGET_BYTES + 2_000_000_000
    target = _disk("vda", model="Unknown Model", size=size)

    selected = safety.select_target_disks(
        _inventory({"name": "vda", "model": None, "tran": None, "size": size}),
        [target],
        "/dev/sda1",
        _empty_zfs_labels(_inventory({"name": "vda", "model": None, "tran": None, "size": size})),
    )

    assert selected == [target]


def test_selects_two_identical_disks_for_the_installers_mirror_path():
    first = _disk("sdb")
    second = _disk("sdc")
    size = first.size

    selected = safety.select_target_disks(
        _inventory({"name": "sdb", "size": size}, {"name": "sdc", "size": size}),
        [second, first],
        "/dev/sda1",
        _empty_zfs_labels(_inventory({"name": "sdb", "size": size}, {"name": "sdc", "size": size})),
    )

    assert [disk.name for disk in selected] == ["sdb", "sdc"]


def test_refuses_unknown_models_when_selecting_a_two_disk_mirror():
    size = safety.MIN_TARGET_BYTES + 2_000_000_000
    targets = [_disk(name, model="Unknown Model", size=size) for name in ("sdb", "sdc")]
    inventory = _inventory(
        {"name": "sdb", "model": None, "tran": None, "size": size},
        {"name": "sdc", "model": None, "tran": None, "size": size},
    )

    with pytest.raises(safety.UnsafeHardwareConfiguration):
        safety.select_target_disks(inventory, targets, "/dev/sda1", _empty_zfs_labels(inventory))


def test_accepts_a_mounted_optical_installer_source():
    target = _disk("sdb")
    inventory = _inventory({"name": "sdb"})
    inventory["blockdevices"][0] = {
        "name": "sr0",
        "path": "/dev/sr0",
        "type": "rom",
        "size": 4_000_000_000,
        "model": "QEMU DVD-ROM",
        "rm": 1,
        "ro": 1,
        "mountpoints": ["/cdrom"],
    }

    assert safety.select_target_disks(
        inventory,
        [target],
        "/dev/sr0",
        _empty_zfs_labels(inventory),
    ) == [target]


def test_existing_boot_pool_is_the_only_zfs_pool_name_allowed_on_a_target():
    inventory = _inventory({"name": "sdb"})
    labels = _empty_zfs_labels(inventory)
    labels["sdb1"] = ("boot-pool",)

    assert safety.select_target_disks(inventory, [_disk("sdb")], "/dev/sda1", labels) == [_disk("sdb")]


def test_refuses_a_non_boot_zfs_pool_on_a_target_partition():
    inventory = _inventory({"name": "sdb"})
    labels = _empty_zfs_labels(inventory)
    labels["sdb1"] = ("tank",)

    with pytest.raises(safety.UnsafeHardwareConfiguration):
        safety.select_target_disks(inventory, [_disk("sdb")], "/dev/sda1", labels)


def test_refuses_a_partition_with_an_unreadable_zfs_probe():
    inventory = _inventory({"name": "sdb"})
    labels = _empty_zfs_labels(inventory)
    del labels["sdb1"]

    with pytest.raises(safety.UnsafeHardwareConfiguration):
        safety.select_target_disks(inventory, [_disk("sdb")], "/dev/sda1", labels)


@pytest.mark.parametrize("pool_name", ["boot-pool", "tank"])
def test_zdb_label_probe_extracts_pool_name(pool_name):
    stdout = "\n".join(
        f"name: '{pool_name}'" if index == 0 else "    state: ONLINE"
        for index in range(4)
    )

    assert safety.parse_zdb_label_probe(stdout, "", 0) == (pool_name,)


@pytest.mark.parametrize("returncode", [0, 1, 2])
def test_zdb_label_probe_recognizes_exact_empty_device_output(returncode):
    output = "\n".join(f"failed to unpack label {index}" for index in range(4))

    assert safety.parse_zdb_label_probe("", output, returncode) == ()


def test_zdb_label_probe_fails_closed_on_unknown_output():
    with pytest.raises(safety.UnsafeHardwareConfiguration):
        safety.parse_zdb_label_probe("", "cannot read device", 2)


def test_scans_all_block_nodes_for_zfs_labels_without_importing_pools(monkeypatch):
    inventory = _hardware_inventory([_target_node("sda")])
    calls = []
    empty_labels = "\n".join(f"failed to unpack label {index}" for index in range(4))

    def run(argv, *, capture_output, text, check):
        calls.append(argv)
        assert capture_output and text and not check
        return SimpleNamespace(stdout="", stderr=empty_labels, returncode=2)

    monkeypatch.setattr(autoinstall.subprocess, "run", run)

    assert autoinstall._scan_zfs_pool_labels(inventory) == {"sda": ()}
    assert calls == [["zdb", "-l", "/dev/sda"]]


def test_zfs_scan_refuses_when_probe_tool_cannot_run(monkeypatch):
    inventory = _hardware_inventory([_target_node("sda")])

    def run(*_argv, **_kwargs):
        raise FileNotFoundError("zdb unavailable")

    monkeypatch.setattr(autoinstall.subprocess, "run", run)

    with pytest.raises(safety.UnsafeHardwareConfiguration):
        autoinstall._scan_zfs_pool_labels(inventory)


def test_failed_pool_export_makes_adapter_cleanup_fail_closed(tmp_path, monkeypatch):
    calls = []

    def failed_export(argv, *, check=True):
        calls.append(argv)
        raise relaxation.RelaxationError("export refused")

    monkeypatch.setattr(relaxation, "_run", failed_export)
    mountpoint = tmp_path / "temporary-root"
    mountpoint.mkdir()

    with pytest.raises(relaxation.RelaxationError, match="boot-pool cleanup failed: export boot-pool"):
        relaxation._cleanup_imported_pool(
            imported=True,
            usr_was_mounted=False,
            usr_mount=None,
            usr_dataset=None,
            original_readonly=None,
            root_was_mounted=False,
            root_mount=mountpoint,
        )

    assert calls == [["zpool", "export", "-f", "boot-pool"]]
    assert not mountpoint.exists()


def test_adapter_verifies_release_from_separate_etc_dataset_in_private_namespace(monkeypatch):
    root_dataset = "boot-pool/ROOT/28.0.0-MASTER-20261014-230204"
    etc_dataset = f"{root_dataset}/etc"
    expected = "28.0.0-MASTER+20261014-230204"
    calls = []

    def run(argv, *, check=True):
        calls.append((argv, check))
        if argv[:2] == ["zfs", "list"]:
            return SimpleNamespace(returncode=0, stdout=f"{etc_dataset}\n", stderr="")
        if argv[0] == "unshare":
            return SimpleNamespace(returncode=0, stdout=f"{expected}\n", stderr="")
        raise AssertionError(f"unexpected command: {argv}")

    monkeypatch.setattr(relaxation, "_run", run)

    relaxation._verify_release(root_dataset, expected)

    assert calls[0][0][-1] == etc_dataset
    assert calls[1][0][:6] == [
        "unshare",
        "--mount",
        "--propagation",
        "private",
        "--",
        "sh",
    ]


def test_adapter_rejects_release_mismatch_from_separate_etc_dataset(monkeypatch):
    root_dataset = "boot-pool/ROOT/28.0.0-MASTER-20261014-230204"
    etc_dataset = f"{root_dataset}/etc"

    def run(argv, *, check=True):
        if argv[:2] == ["zfs", "list"]:
            return SimpleNamespace(returncode=0, stdout=f"{etc_dataset}\n", stderr="")
        if argv[0] == "unshare":
            return SimpleNamespace(returncode=0, stdout="28.0.0-MASTER+20261014-230203\n", stderr="")
        raise AssertionError(f"unexpected command: {argv}")

    monkeypatch.setattr(relaxation, "_run", run)

    with pytest.raises(relaxation.RelaxationError, match="does not match adapter release"):
        relaxation._verify_release(root_dataset, "28.0.0-MASTER+20261014-230204")


def test_dispatcher_preserves_vanilla_installer_without_grub_flag(monkeypatch):
    observed = []
    monkeypatch.setattr(dispatch, "_autoinstall_selected", lambda: False)
    monkeypatch.setattr(dispatch.os, "execv", lambda path, argv: observed.append((path, argv)))

    dispatch.main()

    assert observed == [
        ("/usr/bin/python3", ["/usr/bin/python3", "-m", "truenas_installer", "--server"])
    ]


def test_dispatcher_runs_unattended_helper_only_for_grub_flag(monkeypatch):
    observed = []
    monkeypatch.setattr(dispatch, "_autoinstall_selected", lambda: True)
    monkeypatch.setattr(dispatch.runpy, "run_path", lambda path, run_name: observed.append((path, run_name)))

    dispatch.main()

    assert observed == [
        ("/usr/local/lib/truenas-vanilla-iso/autoinstall.py", "__main__")
    ]


def _fake_installer_modules(monkeypatch, installer_disks, install):
    package = ModuleType("truenas_installer")
    package.__path__ = []
    disks = ModuleType("truenas_installer.disks")

    async def list_disks():
        return installer_disks

    disks.list_disks = list_disks
    installer_install = ModuleType("truenas_installer.install")
    installer_install.InstallError = RuntimeError
    installer_install.install = install
    serial = ModuleType("truenas_installer.serial")

    async def serial_sql():
        return "serial-settings"

    serial.serial_sql = serial_sql
    monkeypatch.setitem(sys.modules, "truenas_installer", package)
    monkeypatch.setitem(sys.modules, "truenas_installer.disks", disks)
    monkeypatch.setitem(sys.modules, "truenas_installer.install", installer_install)
    monkeypatch.setitem(sys.modules, "truenas_installer.serial", serial)


def _hardware_inventory(targets):
    source = {
        "name": "sr0",
        "path": "/dev/sr0",
        "type": "rom",
        "size": 4_000_000_000,
        "model": "QEMU DVD-ROM",
        "tran": "ata",
        "rm": 1,
        "ro": 1,
        "mountpoints": ["/cdrom"],
        "children": [],
    }
    return {"blockdevices": [source, *targets]}


def _target_node(name, *, size=None, model="QEMU HARDDISK", tran="sata"):
    return {
        "name": name,
        "path": f"/dev/{name}",
        "type": "disk",
        "size": size or safety.MIN_TARGET_BYTES + 2_000_000_000,
        "model": model,
        "tran": tran,
        "rm": 0,
        "ro": 0,
        "mountpoints": None,
        "children": [],
    }


def test_unattended_preflight_refuses_three_disks_before_installer_api(monkeypatch, capsys):
    targets = [_target_node(name) for name in ("sda", "sdb", "sdc")]
    disks = [_disk(name) for name in ("sda", "sdb", "sdc")]
    install_calls = []

    async def install(*args):
        install_calls.append(args)

    _fake_installer_modules(monkeypatch, disks, install)
    monkeypatch.setattr(autoinstall, "_read_json_command", lambda _argv: _hardware_inventory(targets))
    monkeypatch.setattr(autoinstall, "_installer_media_source", lambda: "/dev/sr0")
    monkeypatch.setattr(autoinstall, "_scan_zfs_pool_labels", lambda _inventory: {disk.name: () for disk in disks})

    with pytest.raises(safety.UnsafeHardwareConfiguration, match="^unsupported unsafe disk hardware configuration$"):
        asyncio.run(autoinstall._install({"flavor": "unattended", "boot_pool_size_gib": 16}))

    assert install_calls == []
    assert "unsupported unsafe disk hardware configuration" in capsys.readouterr().out


@pytest.mark.parametrize("post_install_action", ["reboot", "poweroff"])
def test_unattended_passes_fixed_admin_credentials_and_action_to_installer(monkeypatch, post_install_action):
    target_node = _target_node("sda")
    disk = _disk("sda")
    install_calls = []

    async def install(*args):
        install_calls.append(args)

    class Reboot:
        returncode = 0

        async def communicate(self):
            return b"", b""

    calls = []

    async def reboot(*argv):
        calls.append(argv)
        return Reboot()

    _fake_installer_modules(monkeypatch, [disk], install)
    monkeypatch.setattr(autoinstall, "_read_json_command", lambda _argv: _hardware_inventory([target_node]))
    monkeypatch.setattr(autoinstall, "_installer_media_source", lambda: "/dev/sr0")
    monkeypatch.setattr(autoinstall, "_scan_zfs_pool_labels", lambda _inventory: {"sda": ()})
    monkeypatch.setattr(autoinstall.asyncio, "create_subprocess_exec", reboot)

    asyncio.run(
        autoinstall._install(
            {
                "flavor": "unattended",
                "boot_pool_size_gib": 16,
                "administrator_username": "truenas_admin",
                "fixed_password": "truenas",
                "set_pmbr": False,
                "relaxation_adapter": None,
                "post_install_action": post_install_action,
            }
        )
    )

    assert len(install_calls) == 1
    args = install_calls[0]
    assert [disk.name for disk in args[0]] == ["sda"]
    assert args[1] == []
    assert args[2] is False
    assert args[3] == {"username": "truenas_admin", "password": "truenas"}
    assert args[5] == "serial-settings"
    assert calls == [("systemctl", post_install_action)]


def test_unattended_relaxed_bridges_adapter_progress_callback(monkeypatch, capsys):
    target_node = _target_node("sda")
    disk = _disk("sda")

    async def install(*_args):
        return None

    class Reboot:
        returncode = 0

        async def communicate(self):
            return b"", b""

    async def reboot(*_argv):
        return Reboot()

    relaxation_module = ModuleType("relaxation")

    def apply_relaxation(adapter_id, release_version, progress):
        assert adapter_id == "v28-nightly-20261014"
        assert release_version == "28.0.0-MASTER+20261014-230204"
        progress("adapter applied")

    relaxation_module.apply_relaxation = apply_relaxation
    monkeypatch.setitem(sys.modules, "relaxation", relaxation_module)
    _fake_installer_modules(monkeypatch, [disk], install)
    monkeypatch.setattr(autoinstall, "_read_json_command", lambda _argv: _hardware_inventory([target_node]))
    monkeypatch.setattr(autoinstall, "_installer_media_source", lambda: "/dev/sr0")
    monkeypatch.setattr(autoinstall, "_scan_zfs_pool_labels", lambda _inventory: {"sda": ()})
    monkeypatch.setattr(autoinstall.asyncio, "create_subprocess_exec", reboot)

    asyncio.run(
        autoinstall._install(
            {
                "flavor": "unattended-relaxed",
                "boot_pool_size_gib": 16,
                "administrator_username": "truenas_admin",
                "fixed_password": "truenas",
                "set_pmbr": False,
                "relaxation_adapter": "v28-nightly-20261014",
                "release_version": "28.0.0-MASTER+20261014-230204",
            }
        )
    )

    assert "adapter applied" in capsys.readouterr().out


def _make_installer_root(root: Path) -> Path:
    package = root / "usr/lib/python3/dist-packages/truenas_installer"
    package.mkdir(parents=True)
    (package / "disks.py").write_text(
        "from dataclasses import dataclass\n"
        "@dataclass\n"
        "class Disk:\n"
        "    name: str\n    size: int\n    model: str\n    removable: bool\n"
        "async def list_disks():\n    return []\n",
        encoding="utf-8",
    )
    (package / "install.py").write_text(
        "async def install(destination_disks, wipe_disks, set_pmbr, authentication, "
        "post_install, sql, callback):\n    pass\n",
        encoding="utf-8",
    )
    (package / "serial.py").write_text(
        "async def serial_sql():\n    return ''\n",
        encoding="utf-8",
    )
    return root


@pytest.mark.parametrize(
    ("release", "adapter_id"),
    [
        ("27.0.0-RC.1", "v27"),
        ("28.0.0-MASTER+20261014-230204", "v28-nightly-20261014"),
    ],
)
def test_unattended_relaxed_profile_vendoring_is_pinned_and_fingerprinted(
    tmp_path, release, adapter_id
):
    root = _make_installer_root(tmp_path / "live-root")

    result = iso_profiles.install_profile(root, "unattended-relaxed", 16, release)

    profile = json.loads(
        (root / "usr/local/lib/truenas-vanilla-iso/profile.json").read_text(encoding="utf-8")
    )
    adapter_path = root / "usr/local/lib/truenas-vanilla-iso/adapters" / adapter_id
    assert result["adapter_id"] == adapter_id
    assert profile["relaxation_adapter"] == adapter_id
    assert profile["fixed_password"] == "truenas"
    assert profile["post_install_action"] == "reboot"
    assert (adapter_path / "bundle.sha256").is_file()
    dropin = root / "etc/systemd/system/truenas-installer.service.d/10-vanilla-iso-patcher.conf"
    assert dropin.read_text(encoding="utf-8") == (
        "[Service]\n"
        "ExecStart=\n"
        "ExecStart=/usr/bin/python3 /usr/local/lib/truenas-vanilla-iso/dispatch.py\n"
        "StandardOutput=journal+console\n"
        "StandardError=journal+console\n"
        "Restart=no\n"
    )


@pytest.mark.parametrize(
    ("targets", "api_disks"),
    [
        ([], []),
        ([{"name": "sdb"}, {"name": "sdc"}, {"name": "sdd"}], [_disk("sdb"), _disk("sdc"), _disk("sdd")]),
        ([{"name": "sdb", "model": "Model A"}, {"name": "sdc", "model": "Model B"}], [_disk("sdb", model="Model A"), _disk("sdc", model="Model B")]),
        ([{"name": "sdb", "size": 20_000_000_000}, {"name": "sdc", "size": 20_000_000_001}], [_disk("sdb", size=20_000_000_000), _disk("sdc", size=20_000_000_001)]),
        ([{"name": "sdb", "tran": "sata"}, {"name": "sdc", "tran": "nvme"}], [_disk("sdb"), _disk("sdc")]),
        ([{"name": "sdb", "tran": ""}, {"name": "sdc", "tran": ""}], [_disk("sdb"), _disk("sdc")]),
        ([{"name": "sdb", "rm": 1}], [_disk("sdb")]),
        ([{"name": "sdb", "ro": 1}], [_disk("sdb")]),
        ([{"name": "sdb", "child_mountpoints": ["/mnt/data"]}], [_disk("sdb")]),
        ([{"name": "sdb"}], []),
    ],
)
def test_refuses_unsafe_or_ambiguous_disk_topology(targets, api_disks):
    with pytest.raises(safety.UnsafeHardwareConfiguration, match="^unsupported unsafe disk hardware configuration$"):
        inventory = _inventory(*targets)
        safety.select_target_disks(inventory, api_disks, "/dev/sda1", _empty_zfs_labels(inventory))


def test_refuses_a_loop_backed_installer_source():
    inventory = _inventory({"name": "sdb"})
    inventory["blockdevices"].append(
        {"name": "loop0", "path": "/dev/loop0", "type": "loop", "mountpoints": None}
    )
    with pytest.raises(safety.UnsafeHardwareConfiguration, match="unsupported unsafe disk hardware configuration"):
        safety.select_target_disks(
            inventory,
            [_disk("sdb")],
            "/dev/loop0",
            _empty_zfs_labels(inventory),
        )


VANILLA_GRUB = b"""function load_video {
  insmod efi_gop
}
set prefix=($root)/boot/grub
set default=video
set timeout=15
insmod play
menuentry --hotkey=i --id=video 'Start TrueNAS Installation' {
    linux /vmlinuz gfxpayload=text quiet nomodeset boot=live toram=filesystem.squashfs $gfx_console
    initrd   /initrd.img
}
menuentry --hotkey=j --id=serial 'Start TrueNAS Installation (115200 baud)' {
    linux /vmlinuz gfxpayload=text quiet nomodeset boot=live toram=filesystem.squashfs $gfx_console $serial_console
    initrd   /initrd.img
}
if [ ! -f /.disk/info ]; then
    insmod bli
fi
"""


def test_grub_patch_preserves_vanilla_entries_and_selects_auto_entry_by_default():
    patched = iso_profiles.patch_grub_config(VANILLA_GRUB, 2048)
    text = patched.decode("ascii")

    assert len(patched) == 2048
    assert "--id=video 'Start TrueNAS Installation'" in text
    assert "--id=serial 'Start TrueNAS Installation (115200 baud)'" in text
    assert 'set default="truenas-unattended"' in text
    assert "set timeout=5" in text
    assert "--id=truenas-unattended" in text
    assert "truenas.autoinstall=1" in text
    assert "noeject truenas.autoinstall=1" in text
    assert patched.endswith(b" ")


def test_grub_patch_can_use_filesystem_module_layers_without_single_image_toram():
    patched = iso_profiles.patch_grub_config(VANILLA_GRUB, 2048, use_toram=False)
    text = patched.decode("ascii")

    assert "toram=filesystem.squashfs" not in text
    assert text.count("boot=live") == 3
    assert 'set default="truenas-unattended"' in text
    assert "truenas.autoinstall=1" in text
    assert "noeject truenas.autoinstall=1" in text


@pytest.mark.parametrize(
    ("config", "record_bytes", "message"),
    [
        (VANILLA_GRUB.replace(b"--id=serial", b"--id=unknown"), 2048, "serial installer"),
        (VANILLA_GRUB, 50, "needs"),
        (VANILLA_GRUB.replace(b"insmod play", b"insmod sound"), 2048, "timeout section"),
        (VANILLA_GRUB + b"\n# truenas-unattended\n", 2048, "already has"),
    ],
)
def test_grub_patch_refuses_unrecognized_or_too_small_configs(config, record_bytes, message):
    with pytest.raises(iso_profiles.ProfileError, match=message):
        iso_profiles.patch_grub_config(config, record_bytes)
