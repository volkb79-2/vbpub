"""LT-MEM: zswap/KSM/THP/sysctl adoption, iocost from the benchmark, operator extras."""

from __future__ import annotations

import json
import logging
import os
import re
import stat
import subprocess
from pathlib import Path

import pytest

from debian_install_v2 import config as config_module
from debian_install_v2 import templates
from debian_install_v2.actions import HostActions
from debian_install_v2.config import Config, ConfigError, load_config, persisted_config_data
from debian_install_v2.installer import Installer, InstallerError
from debian_install_v2.state import StateStore
from debian_install_v2.userconfig import USER_RC_FILES

PKG = Path(templates.__file__).resolve().parent
RESULTS = {
    "rbps": 1481338880, "rseqiops": 20001, "rrandiops": 17002,
    "wbps": 600000000, "wseqiops": 9001, "wrandiops": 8002,
}
BASE = {"schema_version": 1, "telegram_bot_token": "", "telegram_chat_id": ""}


def make(tmp_path, *, dry_run=True, **overrides) -> Installer:
    config = Config(
        state_dir=str(tmp_path / "state"), log_dir=str(tmp_path / "logs"),
        telegram_bot_token="", telegram_chat_id="", credential_mode="systemd",
        **overrides,
    )
    installer = Installer(config, HostActions(dry_run=dry_run), inspect_host=False)
    installer.root_disk = "vda"
    installer.state.dry_run = False  # state lives under tmp_path: let steps persist
    StateStore(config.state_dir).save_new(StateStore.new(config))
    return installer


def write_fixture(tmp_path, results=RESULTS) -> None:
    state = tmp_path / "state"
    state.mkdir(parents=True, exist_ok=True)
    (state / "io-benchmark.json").write_text(json.dumps({"devno": "254:0", "results": results}))


def exec_lines(unit: str) -> list[str]:
    return [line for line in unit.splitlines() if line.startswith("ExecStart=")]


def zswap_unit(tmp_path, **overrides) -> str:
    installer = make(tmp_path, **overrides)
    installer._configure_zswap()
    return installer.actions.dry_run_writes["/etc/systemd/system/zswap-config.service"]


# --- 1. zswap ------------------------------------------------------------------

def test_zswap_unit_has_no_zpool_and_enables_last_after_the_compressor(tmp_path):
    unit = zswap_unit(tmp_path)
    assert "zpool" not in unit
    lines = exec_lines(unit)
    knobs = [re.search(r"parameters/(\w+)", line).group(1) if "parameters/" in line else "modprobe" for line in lines]
    assert knobs == [
        "modprobe", "compressor", "max_pool_percent", "accept_threshold_percent",
        "shrinker_enabled", "enabled",
    ]
    assert lines[-1].endswith("echo 1 > /sys/module/zswap/parameters/enabled'")
    assert lines[0] == "ExecStart=/usr/sbin/modprobe zstd"
    # One knob per ExecStart line, no chaining.
    assert all(line.count("/sys/module/zswap/parameters/") <= 1 for line in lines)
    assert "ExecStartPost=" in unit and "shrinker=" in unit


def test_zswap_unit_header_matches_gstammtisch(tmp_path):
    unit = zswap_unit(tmp_path)
    for line in (
        "DefaultDependencies=no", "After=systemd-modules-load.service", "Before=swap.target",
        "ConditionPathExists=/sys/module/zswap/parameters/enabled", "WantedBy=sysinit.target",
    ):
        assert line in unit.splitlines()


def test_zswap_unit_follows_config_values(tmp_path):
    unit = zswap_unit(
        tmp_path, zswap_compressor="lz4", zswap_pool_percent=33,
        zswap_accept_threshold_percent=70, zswap_shrinker_enabled=False,
    )
    assert "echo lz4 > /sys/module/zswap/parameters/compressor" in unit
    assert "echo 33 > /sys/module/zswap/parameters/max_pool_percent" in unit
    assert "echo 70 > /sys/module/zswap/parameters/accept_threshold_percent" in unit
    assert "echo N > /sys/module/zswap/parameters/shrinker_enabled" in unit
    assert "echo Y > /sys/module/zswap/parameters/shrinker_enabled" not in unit
    assert "echo Y > /sys/module/zswap/parameters/shrinker_enabled" in zswap_unit(tmp_path)


def test_zswap_defaults_and_modules_load(tmp_path):
    config = Config()
    assert (config.zswap_compressor, config.zswap_pool_percent) == ("zstd", 25)
    assert config.zswap_accept_threshold_percent == 90
    assert config.zswap_shrinker_enabled is True
    assert not hasattr(config, "zswap_zpool")
    installer = make(tmp_path)
    installer._configure_zswap()
    modules = installer.actions.dry_run_writes["/etc/modules-load.d/vbpub-zstd.conf"]
    assert [l for l in modules.splitlines() if l and not l.startswith("#")] == ["zstd"]


def test_no_zswap_parameters_on_the_kernel_command_line(tmp_path):
    installer = make(tmp_path)
    installer._configure_zswap()
    assert not [path for path in installer.actions.dry_run_writes if "grub" in path or "cmdline" in path]
    sources = "\n".join(p.read_text() for p in PKG.glob("*.py"))
    assert "zswap.enabled=" not in sources and "zswap.compressor=" not in sources


@pytest.mark.parametrize("value", [-1, 101])
def test_accept_threshold_range(value):
    with pytest.raises(ConfigError, match="zswap_accept_threshold_percent"):
        load_config(raw_json=json.dumps(dict(BASE, zswap_accept_threshold_percent=value)))


def test_shrinker_flag_must_be_boolean():
    with pytest.raises(ConfigError, match="zswap_shrinker_enabled"):
        load_config(raw_json=json.dumps(dict(BASE, zswap_shrinker_enabled="Y")))


def test_removed_zpool_key_is_ignored_with_one_warning(caplog):
    config_module._WARNED_REMOVED_KEYS.discard("zswap_zpool")
    data = dict(BASE, zswap_zpool="z3fold", zswap_pool_percent=30)
    with caplog.at_level(logging.WARNING, logger="debian_install_v2.config"):
        config = load_config(raw_json=json.dumps(data))
        load_config(raw_json=json.dumps(data))
        saved = persisted_config_data(data)
    assert config.zswap_pool_percent == 30
    assert "zswap_zpool" not in saved
    warnings = [r for r in caplog.records if "zswap_zpool" in r.getMessage()]
    assert len(warnings) == 1
    # A genuinely unknown key is still rejected.
    with pytest.raises(ConfigError, match="unknown configuration key"):
        load_config(raw_json=json.dumps(dict(BASE, zswap_zpoool="x")))


def test_health_gate_zswap_reads_back_every_knob(tmp_path):
    installer = make(tmp_path, dry_run=False, zswap_shrinker_enabled=False)
    params = tmp_path / "zswap"
    params.mkdir()
    values = {
        "compressor": "zstd", "enabled": "Y", "max_pool_percent": "25",
        "accept_threshold_percent": "90", "shrinker_enabled": "N",
    }
    for name, value in values.items():
        (params / name).write_text(value + "\n")
    installer._ZSWAP_PARAMS = params
    installer._health_gate_zswap()
    for name, bad in (
        ("compressor", "lzo"), ("enabled", "N"), ("max_pool_percent", "40"),
        ("accept_threshold_percent", "80"), ("shrinker_enabled", "Y"),
    ):
        (params / name).write_text(bad + "\n")
        with pytest.raises(InstallerError, match=f"zswap {name} is"):
            installer._health_gate_zswap()
        (params / name).write_text(values[name] + "\n")
    installer._health_gate_zswap()


# --- 2. KSM / THP via tmpfiles.d ------------------------------------------------

def test_thp_and_ksm_are_tmpfiles_entries_with_gstammtisch_values(tmp_path):
    installer = make(tmp_path)
    installer._configure_zswap()
    installer._configure_ksm()
    writes = installer.actions.dry_run_writes
    thp = writes["/etc/tmpfiles.d/vbpub-thp.conf"]
    ksm = writes["/etc/tmpfiles.d/vbpub-ksm.conf"]
    assert "w! /sys/kernel/mm/transparent_hugepage/enabled - - - - madvise" in thp
    assert "w! /sys/kernel/mm/transparent_hugepage/defrag  - - - - madvise" in thp
    for needle in (
        "/sys/kernel/mm/ksm/run                      - - - - 1",
        "/sys/kernel/mm/ksm/advisor_mode             - - - - scan-time",
        "/sys/kernel/mm/ksm/advisor_target_scan_time - - - - 200",
        "/sys/kernel/mm/ksm/use_zero_pages           - - - - 1",
    ):
        assert f"w! {needle}" in ksm
    assert "/etc/systemd/system/thp-config.service" not in writes
    assert "/etc/systemd/system/ksm-config.service" not in writes
    argvs = [a.argv for a in installer.actions.planned]
    assert ("/usr/bin/systemd-tmpfiles", "--create", "/etc/tmpfiles.d/vbpub-thp.conf") in argvs
    assert ("/usr/bin/systemd-tmpfiles", "--create", "/etc/tmpfiles.d/vbpub-ksm.conf") in argvs


def test_gstammtisch_tmpfiles_content_is_what_v2_ships():
    root = PKG.parents[1] / "gstammtisch-guide" / "files" / "etc" / "tmpfiles.d"
    if not root.is_dir():
        pytest.skip("gstammtisch-guide not present in this checkout")

    def entries(text):
        return [" ".join(line.split()) for line in text.splitlines() if line.startswith("w!")]

    assert entries(templates.THP_TMPFILES) == entries((root / "thp.conf").read_text())
    assert entries(templates.KSM_TMPFILES) == entries((root / "ksm.conf").read_text())


def test_legacy_units_are_disabled_and_removed_when_present(tmp_path, monkeypatch):
    installer = make(tmp_path)
    present = {"/etc/systemd/system/thp-config.service", "/etc/systemd/system/ksm-config.service"}
    monkeypatch.setattr(installer.actions, "exists", lambda path: path in present)
    installer._configure_zswap()
    argvs = [a.argv for a in installer.actions.planned]
    assert ("/usr/bin/systemctl", "disable", "thp-config.service") in argvs
    assert ("/usr/bin/systemctl", "disable", "ksm-config.service") in argvs
    assert set(installer.actions.dry_run_removals) >= present


def test_absent_legacy_units_cause_no_disable(tmp_path):
    installer = make(tmp_path)
    installer._configure_zswap()
    assert not any(a.argv[1:2] == ("disable",) for a in installer.actions.planned)


def test_ksm_disabled_removes_a_stale_tmpfile(tmp_path, monkeypatch):
    installer = make(tmp_path, run_ksm=False)
    monkeypatch.setattr(installer.actions, "exists", lambda path: path == "/etc/tmpfiles.d/vbpub-ksm.conf")
    installer._configure_zswap()
    assert "/etc/tmpfiles.d/vbpub-ksm.conf" in installer.actions.dry_run_removals


# --- 3. sysctl --------------------------------------------------------------------

def sysctl_values(text: str) -> dict[str, str]:
    return dict(
        (k.strip(), v.strip())
        for k, v in (line.split("=", 1) for line in text.splitlines() if line and not line.startswith("#"))
    )


def test_sysctl_values_and_why_comments(tmp_path):
    installer = make(tmp_path)
    installer._configure_zswap()
    text = installer.actions.dry_run_writes["/etc/sysctl.d/99-vbpub-swap.conf"]
    assert sysctl_values(text) == {
        "vm.swappiness": "100", "vm.vfs_cache_pressure": "50", "vm.watermark_scale_factor": "50",
        "vm.page-cluster": "0", "vm.admin_reserve_kbytes": "65536",
        "vm.dirty_ratio": "15", "vm.dirty_background_ratio": "5",
    }
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if line and not line.startswith("#"):
            assert lines[index - 1].startswith("#"), f"{line} lacks a why comment"
    lowered = text.lower()
    for word in ("game", "soulmask", "docker", "wings"):
        assert word not in lowered


def test_min_free_is_not_a_static_sysctl(tmp_path):
    installer = make(tmp_path)
    installer._configure_zswap()
    assert "vm.min_free_kbytes" not in sysctl_values(installer.actions.dry_run_writes["/etc/sysctl.d/99-vbpub-swap.conf"])


@pytest.mark.parametrize("value", [0, 100, 150, 200])
def test_swappiness_accepts_the_kernel_range(value, tmp_path):
    config = load_config(raw_json=json.dumps(dict(BASE, vm_swappiness=value)))
    assert config.vm_swappiness == value
    installer = make(tmp_path, vm_swappiness=value)
    installer._configure_zswap()
    assert f"vm.swappiness = {value}" in installer.actions.dry_run_writes["/etc/sysctl.d/99-vbpub-swap.conf"]


def test_swappiness_above_200_is_rejected():
    with pytest.raises(ConfigError, match="0 to 200"):
        load_config(raw_json=json.dumps(dict(BASE, vm_swappiness=201)))


def run_min_free(tmp_path, current: int) -> tuple[str, str]:
    script = tmp_path / "floor.sh"
    script.write_text(templates.render_min_free_floor_script())
    proc = tmp_path / "min_free_kbytes"
    proc.write_text(f"{current}\n")
    result = subprocess.run(["sh", str(script), str(proc)], capture_output=True, text=True, check=True)
    return proc.read_text().strip(), result.stdout


@pytest.mark.parametrize("current,expected", [
    (11000, "65536"), (65535, "65536"), (65536, "65536"), (65537, "65537"), (900000, "900000"),
])
def test_min_free_floor_raises_but_never_lowers(tmp_path, current, expected):
    final, _ = run_min_free(tmp_path, current)
    assert final == expected
    assert int(final) >= current


def test_min_free_floor_refuses_garbage(tmp_path):
    script = tmp_path / "floor.sh"
    script.write_text(templates.render_min_free_floor_script())
    proc = tmp_path / "v"
    proc.write_text("abc\n")
    assert subprocess.run(["sh", str(script), str(proc)], capture_output=True).returncode != 0
    assert proc.read_text() == "abc\n"


def test_min_free_unit_runs_after_sysctl_and_is_installed(tmp_path):
    installer = make(tmp_path)
    installer._configure_zswap()
    writes = installer.actions.dry_run_writes
    assert "After=systemd-sysctl.service" in writes["/etc/systemd/system/vbpub-min-free-floor.service"]
    assert "FLOOR_KB=65536" in writes["/usr/local/sbin/vbpub-min-free-floor"]


def test_oomd_config_matches_gstammtisch():
    ref = PKG.parents[1] / "gstammtisch-guide" / "files" / "etc" / "systemd" / "oomd.conf.d" / "gstammtisch.conf"
    if not ref.is_file():
        pytest.skip("gstammtisch-guide not present in this checkout")

    def settings(text):
        return [line for line in text.splitlines() if "=" in line and not line.startswith("#")]

    assert settings(templates.OOMD_CONFIG) == settings(ref.read_text())


# --- 4. iocost ----------------------------------------------------------------------

def test_no_result_installs_nothing_and_does_not_fail(tmp_path):
    installer = make(tmp_path)
    installer._configure_iocost()
    writes = installer.actions.dry_run_writes
    assert not [p for p in writes if "iocost" in p]
    assert not [a for a in installer.actions.planned if "iocost" in " ".join(a.argv)]
    assert installer._iocost_note.startswith("io.cost not configured")
    state = StateStore(installer.config.state_dir).load()["steps"]["iocost"]
    assert state["status"] == "skipped"


def test_no_result_sets_the_note_shown_in_the_install_complete_message(tmp_path):
    installer = make(tmp_path)
    installer._configure_iocost()
    assert "no valid io benchmark result" in installer._iocost_note


@pytest.mark.parametrize("bad", [
    {k: 0 for k in RESULTS}, {**RESULTS, "wbps": "x"}, {k: v for k, v in RESULTS.items() if k != "rbps"},
    {**RESULTS, "rbps": True}, {**RESULTS, "rbps": -5},
])
def test_invalid_result_is_treated_as_no_result(tmp_path, bad):
    write_fixture(tmp_path, bad)
    installer = make(tmp_path)
    installer._configure_iocost()
    assert not [p for p in installer.actions.dry_run_writes if "iocost" in p]


def test_unparseable_result_file_is_no_result(tmp_path):
    (tmp_path / "state").mkdir()
    (tmp_path / "state" / "io-benchmark.json").write_text("{not json")
    installer = make(tmp_path)
    installer._configure_iocost()
    assert not [p for p in installer.actions.dry_run_writes if "iocost" in p]


def test_iocost_disabled_by_config_even_with_a_result(tmp_path):
    write_fixture(tmp_path)
    installer = make(tmp_path, iocost_enabled=False)
    installer._configure_iocost()
    assert not [p for p in installer.actions.dry_run_writes if "iocost" in p]


def test_result_installs_model_file_script_and_unit(tmp_path):
    write_fixture(tmp_path)
    installer = make(tmp_path)
    installer._configure_iocost()
    writes = installer.actions.dry_run_writes
    assert writes["/etc/vbpub/iocost-model"] == (
        "rbps=1481338880 rseqiops=20001 rrandiops=17002 wbps=600000000 wseqiops=9001 wrandiops=8002\n"
    )
    assert "/usr/local/sbin/vbpub-iocost-setup" in writes
    unit = writes["/etc/systemd/system/vbpub-iocost.service"]
    assert "ExecStart=/usr/local/sbin/vbpub-iocost-setup" in unit
    assert "ConditionPathExists=/sys/fs/cgroup/io.cost.model" in unit
    argvs = [a.argv for a in installer.actions.planned]
    assert ("/usr/bin/systemctl", "enable", "--now", "vbpub-iocost.service") in argvs


def test_stale_iocost_unit_is_removed_when_the_result_is_gone(tmp_path, monkeypatch):
    installer = make(tmp_path)
    monkeypatch.setattr(installer.actions, "exists", lambda p: p == "/etc/systemd/system/vbpub-iocost.service")
    installer._configure_iocost()
    argvs = [a.argv for a in installer.actions.planned]
    assert ("/usr/bin/systemctl", "disable", "vbpub-iocost.service") in argvs
    assert "/etc/vbpub/iocost-model" in installer.actions.dry_run_removals


def iocost_sandbox(tmp_path, *, root_src="/dev/vda3", parents=None, devnos=None, coeffs=None):
    """A fake sysfs/cgroup tree plus findmnt/lsblk stubs for running the boot script."""
    sys_root = tmp_path / "sys"
    cg = sys_root / "fs" / "cgroup"
    cg.mkdir(parents=True)
    (cg / "io.cost.model").write_text("")
    (cg / "io.cost.qos").write_text("")
    for name, devno in (devnos or {"vda": "254:0"}).items():
        (sys_root / "class" / "block" / name).mkdir(parents=True)
        (sys_root / "class" / "block" / name / "dev").write_text(devno + "\n")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "findmnt").write_text(f"#!/bin/sh\necho '{root_src}'\n")
    cases = "".join(f"  /dev/{child}) echo {parent};;\n" for child, parent in (parents or {"vda3": "vda"}).items())
    (bin_dir / "lsblk").write_text(f"#!/bin/sh\ncase \"$3\" in\n{cases}  *) ;;\nesac\n")
    for stub in ("findmnt", "lsblk"):
        (bin_dir / stub).chmod(0o755)
    model = tmp_path / "iocost-model"
    model.write_text((coeffs if coeffs is not None else " ".join(f"{k}={v}" for k, v in RESULTS.items())) + "\n")
    script = tmp_path / "setup.sh"
    script.write_text(templates.IOCOST_SCRIPT)
    env = dict(os.environ, PATH=f"{bin_dir}:{os.environ['PATH']}", VBPUB_SYS=str(sys_root), VBPUB_IOCOST_MODEL=str(model))
    return script, env, cg


def test_boot_script_writes_model_line_then_qos_for_the_resolved_disk(tmp_path):
    script, env, cg = iocost_sandbox(tmp_path)
    subprocess.run(["sh", str(script)], env=env, check=True, capture_output=True)
    assert (cg / "io.cost.model").read_text().strip() == (
        "254:0 ctrl=user model=linear rbps=1481338880 rseqiops=20001 rrandiops=17002 "
        "wbps=600000000 wseqiops=9001 wrandiops=8002"
    )
    qos = (cg / "io.cost.qos").read_text().strip()
    assert qos.startswith("254:0 enable=1 ctrl=user")
    assert "rpct=95.00" in qos and "max=100.00" in qos


def test_boot_script_resolves_majmin_at_boot_not_install_time(tmp_path):
    # The same installed script, two different boots: a renamed/renumbered disk.
    for name in ("a", "b"):
        (tmp_path / name).mkdir()
    first, env1, cg1 = iocost_sandbox(tmp_path / "a", devnos={"vda": "254:0"})
    second, env2, cg2 = iocost_sandbox(
        tmp_path / "b", root_src="/dev/nvme0n1p2", parents={"nvme0n1p2": "nvme0n1"}, devnos={"nvme0n1": "259:0"},
    )
    assert not re.search(r"\b\d+:\d+\b", templates.IOCOST_SCRIPT)
    assert not re.search(r"\b\d+:\d+\b", templates.IOCOST_SERVICE)
    subprocess.run(["sh", str(first)], env=env1, check=True, capture_output=True)
    subprocess.run(["sh", str(second)], env=env2, check=True, capture_output=True)
    assert (cg1 / "io.cost.model").read_text().startswith("254:0 ctrl=user model=linear")
    assert (cg2 / "io.cost.model").read_text().startswith("259:0 ctrl=user model=linear")
    assert (cg2 / "io.cost.qos").read_text().startswith("259:0 enable=1")


def test_boot_script_follows_nested_parents_to_the_whole_disk(tmp_path):
    script, env, cg = iocost_sandbox(
        tmp_path, root_src="/dev/mapper/vg-root",
        parents={"vg-root": "vda3", "vda3": "vda"},
    )
    subprocess.run(["sh", str(script)], env=env, check=True, capture_output=True)
    assert (cg / "io.cost.model").read_text().startswith("254:0 ctrl=user")


def test_boot_script_refuses_unverifiable_input(tmp_path):
    (tmp_path / "bad").mkdir()
    script, env, cg = iocost_sandbox(tmp_path / "bad", coeffs="rbps=1 oops")
    result = subprocess.run(["sh", str(script)], env=env, capture_output=True, text=True)
    assert result.returncode != 0
    assert (cg / "io.cost.model").read_text() == ""
    (tmp_path / "x").mkdir()
    script, env, cg = iocost_sandbox(tmp_path / "x", devnos={"sdz": "8:0"})
    result = subprocess.run(["sh", str(script)], env=env, capture_output=True, text=True)
    assert result.returncode != 0  # root disk has no sysfs dev node
    assert (cg / "io.cost.qos").read_text() == ""


def test_health_gate_iocost_reads_model_and_qos_back(tmp_path):
    write_fixture(tmp_path)
    installer = make(tmp_path, dry_run=False)
    cg = tmp_path / "cg"
    cg.mkdir()
    blk = tmp_path / "blk" / "vda"
    blk.mkdir(parents=True)
    (blk / "dev").write_text("254:0\n")
    installer._CGROUP_ROOT = cg
    installer._SYS_CLASS_BLOCK = tmp_path / "blk"
    model = (
        "254:0 ctrl=user model=linear rbps=1481338880 rseqiops=20001 rrandiops=17002 "
        "wbps=600000000 wseqiops=9001 wrandiops=8002\n"
    )
    qos = "254:0 enable=1 ctrl=user rpct=95.00 rlat=5000 wpct=95.00 wlat=5000 min=1.00 max=100.00\n"
    (cg / "io.cost.model").write_text(model)
    (cg / "io.cost.qos").write_text(qos)
    installer._health_gate_iocost()
    (cg / "io.cost.qos").write_text(qos.replace("enable=1", "enable=0"))
    with pytest.raises(InstallerError, match="io.cost.qos is not enabled"):
        installer._health_gate_iocost()
    (cg / "io.cost.qos").write_text(qos)
    (cg / "io.cost.model").write_text(model.replace("rbps=1481338880", "rbps=1"))
    with pytest.raises(InstallerError, match="io.cost.model does not read back"):
        installer._health_gate_iocost()
    (cg / "io.cost.model").write_text("")
    with pytest.raises(InstallerError, match="io.cost.model"):
        installer._health_gate_iocost()


def test_health_gate_iocost_is_a_noop_without_a_result(tmp_path):
    installer = make(tmp_path, dry_run=False)
    cg = tmp_path / "cg"
    cg.mkdir()
    (cg / "io.cost.model").write_text("")
    installer._CGROUP_ROOT = cg
    installer._health_gate_iocost()


def test_iocost_is_wired_into_stage2_after_zswap():
    source = (PKG / "installer.py").read_text()
    assert source.index("self._configure_zswap()") < source.index("self._configure_iocost()")


def test_no_bfq_anywhere_in_shipped_code_or_rendered_output(tmp_path):
    write_fixture(tmp_path)
    installer = make(tmp_path)
    installer._configure_zswap()
    installer._configure_ksm()
    installer._configure_iocost()
    installer._configure_users()
    rendered = "\n".join(installer.actions.dry_run_writes.values())
    sources = "\n".join(p.read_text() for p in sorted(PKG.glob("*.py")))
    assert "bfq" not in rendered.lower()
    assert "bfq" not in sources.lower()
    assert "bfq" not in (PKG.parent / "bootstrap-remote.py").read_text().lower()


# --- 5. extras ------------------------------------------------------------------------

def swap_health(tmp_path, *, proc_files: dict[str, str], sys_files: dict[str, str]) -> str:
    proc, sys_root = tmp_path / "proc", tmp_path / "sys"
    for root, files in ((proc, proc_files), (sys_root, sys_files)):
        for rel, content in files.items():
            path = root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
    script = tmp_path / "swap-health"
    script.write_text(templates.SWAP_HEALTH_SCRIPT)
    env = dict(os.environ, VBPUB_PROC=str(proc), VBPUB_SYS=str(sys_root))
    return subprocess.run(["bash", str(script)], env=env, capture_output=True, text=True, check=True).stdout


PROC_OK = {
    "meminfo": "MemTotal: 1 kB\nZswap:             1000 kB\nZswapped:          2500 kB\n",
    "vmstat": "pgmajfault 7\nzswpin 3\nzswpout 200\nzswpwb 20\n",
    "pressure/memory": "some avg10=0.00 avg60=0.00 avg300=0.00 total=0\n",
    "pressure/io": "some avg10=0.00 avg60=0.00 avg300=0.00 total=0\n",
}
ZSWAP_PARAMS = {
    f"module/zswap/parameters/{name}": value + "\n" for name, value in {
        "enabled": "Y", "compressor": "zstd", "max_pool_percent": "25",
        "accept_threshold_percent": "90", "shrinker_enabled": "Y",
    }.items()
}


def test_swap_health_without_debugfs_still_reports_ratio_writeback_and_psi(tmp_path):
    out = swap_health(tmp_path, proc_files=PROC_OK, sys_files=ZSWAP_PARAMS)
    assert "compressor=zstd" in out and "shrinker_enabled=Y" in out
    assert "compression ratio: 2.50x" in out
    assert "writeback ratio: 10.0%" in out
    assert "some avg10=0.00" in out
    assert "debugfs zswap counters not available" in out


def test_swap_health_with_debugfs_adds_counters(tmp_path):
    sys_files = dict(ZSWAP_PARAMS, **{"kernel/debug/zswap/stored_pages": "5\n", "kernel/debug/zswap/pool_total_size": "99\n"})
    out = swap_health(tmp_path, proc_files=PROC_OK, sys_files=sys_files)
    assert "debugfs stored_pages=5" in out and "debugfs pool_total_size=99" in out


def test_swap_health_survives_missing_zswap_and_empty_pool(tmp_path):
    out = swap_health(tmp_path, proc_files={"meminfo": "MemTotal: 1 kB\n", "vmstat": "pgmajfault 1\n"}, sys_files={})
    assert "ZSWAP  not available" in out
    (tmp_path / "e").mkdir()
    out = swap_health(
        tmp_path / "e", proc_files=dict(PROC_OK, meminfo="Zswap: 0 kB\nZswapped: 0 kB\n", vmstat="zswpout 0\nzswpwb 0\n"),
        sys_files=ZSWAP_PARAMS,
    )
    assert "n/a (pool empty)" in out and "n/a (nothing stored yet)" in out


def test_swap_health_is_installed_executable_at_the_operator_path(tmp_path):
    installer = make(tmp_path)
    installer._configure_zswap()
    assert installer.actions.dry_run_writes["/usr/local/sbin/vbpub-swap-health"] == templates.SWAP_HEALTH_SCRIPT
    assert any(a.argv == ("/usr/bin/tee", "/usr/local/sbin/vbpub-swap-health") for a in installer.actions.planned)


def test_user_ergonomics_written_for_root_and_skel(tmp_path):
    installer = make(tmp_path)
    installer._configure_users()
    writes = installer.actions.dry_run_writes
    for home in ("/root", "/etc/skel"):
        for relative, content in USER_RC_FILES:
            assert writes[f"{home}/{relative}"] == content
    for needle in ("alias df='df -h'", "alias du='du -h'", "alias free='free -h'", "catlog()"):
        assert needle in writes["/root/.bash_aliases"]
    assert "fgrep" not in writes["/root/.bash_aliases"] and "egrep" not in writes["/root/.bash_aliases"]
    iftop = writes["/root/.iftoprc"]
    assert "sort:" not in iftop and "num-lines" not in iftop
    assert "fields=" not in writes["/root/.config/htop/htoprc"]


def test_user_ergonomics_are_idempotent(tmp_path):
    first = make(tmp_path)
    first._configure_users()
    second = make(tmp_path)
    second._configure_users()
    assert first.actions.dry_run_writes == second.actions.dry_run_writes


def test_bashrc_sourcing_snippet_added_once(tmp_path):
    installer = make(tmp_path)
    bashrc = tmp_path / "bashrc"
    bashrc.write_text("# stock\n")
    bashrc.chmod(0o640)
    installer._ensure_bashrc_sources_aliases(str(bashrc))
    written = installer.actions.dry_run_writes[str(bashrc)]
    assert written.count(".bash_aliases") >= 1 and written.startswith("# stock\n")
    bashrc.write_text(written)
    installer.actions.dry_run_writes.clear()
    installer._ensure_bashrc_sources_aliases(str(bashrc))
    assert str(bashrc) not in installer.actions.dry_run_writes
    # Debian's own skel .bashrc already mentions it: untouched.
    bashrc.write_text("if [ -f ~/.bash_aliases ]; then . ~/.bash_aliases; fi\n")
    installer._ensure_bashrc_sources_aliases(str(bashrc))
    assert str(bashrc) not in installer.actions.dry_run_writes


def test_rc_files_only_use_known_directives():
    by_name = dict(USER_RC_FILES)
    for line in by_name[".iftoprc"].splitlines():
        if line and not line.startswith("#"):
            assert line.split(":")[0] in {
                "show-bars", "port-resolution", "dns-resolution", "show-totals", "log-scale",
                "line-display", "port-display",
            }
