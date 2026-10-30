"""`iogov preflight`: missing host tools are reported with their apt package before any install step; exit non-zero."""

from __future__ import annotations

import os
import stat

from conftest import write_file

from iogov import hostcheck
from iogov.cli import main


def bin_dir(root, names):
    d = root / "bin"
    d.mkdir(exist_ok=True)
    for n in names:
        p = d / n
        p.write_text("#!/bin/sh\n")
        p.chmod(p.stat().st_mode | stat.S_IXUSR)
    return str(d)


ALL = [t[1] for t in hostcheck.TOOLS]


def host(world, controllers="cpu io memory pids\n"):
    write_file(world.cg / "cgroup.controllers", controllers)
    return world.config(devices=[], data_paths=[])


def test_all_present_passes(world, tmp_path):
    cfg = host(world)
    text, code = hostcheck.render(hostcheck.run_checks(cfg, path=bin_dir(tmp_path, ALL), py_version=(3, 13, 5)))
    assert code == 0 and "preflight OK" in text and "MISSING" not in text


def test_missing_setfacl_names_the_acl_package_and_fails(world, tmp_path):
    cfg = host(world)
    path = bin_dir(tmp_path, [n for n in ALL if n != "setfacl"])
    text, code = hostcheck.render(hostcheck.run_checks(cfg, path=path, py_version=(3, 13, 0)))
    assert code == 1
    assert "MISSING setfacl" in text and "[apt: acl]" in text and "apt-get install acl" in text and "udev rule" in text


def test_several_missing_are_all_listed_with_one_install_line(world, tmp_path):
    cfg = host(world)
    path = bin_dir(tmp_path, ["install", "dd", "useradd"])
    text, code = hostcheck.render(hostcheck.run_checks(cfg, path=path, py_version=(3, 13, 0)))
    assert code == 1 and "apt-get install acl systemd udev" in text  # sorted, de-duplicated (systemd provides two tools)


def test_old_python_fails_without_an_apt_hint_for_other_items(world, tmp_path):
    cfg = host(world)
    checks = hostcheck.run_checks(cfg, path=bin_dir(tmp_path, ALL), py_version=(3, 10, 12))
    assert [c.name for c in checks if not c.ok] == ["python3"]
    assert hostcheck.render(checks)[1] == 1


def test_cgroup_without_io_controller_and_missing_psi_fail(world, tmp_path):
    cfg = host(world, "cpu memory\n")
    os.unlink(world.proc / "pressure/io")
    bad = [c.name for c in hostcheck.run_checks(cfg, path=bin_dir(tmp_path, ALL), py_version=(3, 12, 0)) if not c.ok]
    assert bad == ["cgroup v2 io controller", "PSI (pressure/io)"]


def test_unconfigured_disks_are_skipped_and_configured_unresolved_ones_fail(world, tmp_path):
    cfg = host(world)
    c = {c.name: c for c in hostcheck.run_checks(cfg, path=bin_dir(tmp_path, ALL), py_version=(3, 12, 0))}
    assert c["disks"].ok and "skipped" in c["disks"].detail
    cfg2 = world.config(devices=[str(world.dev / "nope")])
    c2 = {c.name: c for c in hostcheck.run_checks(cfg2, path=bin_dir(tmp_path, ALL), py_version=(3, 12, 0))}
    assert not c2["disks"].ok and "does not exist" in c2["disks"].detail


def test_cli_preflight_exit_code_and_output(world, tmp_path, monkeypatch, capsys):
    host(world)
    conf = world.root / "iogov.toml"
    write_file(conf, f'cgroup_root = "{world.cg}"\nprocfs_root = "{world.proc}"\nsysfs_root = "{world.sys}"\n')
    os.chmod(conf, 0o644)
    monkeypatch.setattr(hostcheck, "SBIN", ())
    monkeypatch.setenv("PATH", bin_dir(tmp_path, [n for n in ALL if n != "udevadm"]))
    assert main(["--config", str(conf), "preflight"]) == 1
    out = capsys.readouterr().out
    assert "MISSING udevadm" in out and "[apt: udev]" in out
    monkeypatch.setenv("PATH", bin_dir(tmp_path, ALL))
    assert main(["--config", str(conf), "preflight"]) == 0
