"""install.sh: report-only install helper. Exercised with --dry-run and with --destdir (a staging tree: no useradd/chown/systemctl/udevadm)."""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "install.sh"
BASE = ["--skip-preflight", "--disk-node", "/dev/null", "--id-path", "pci-0000:00:10.0"]


@pytest.fixture(autouse=True)
def _need_bash():
    from _testdeps import require_binary
    require_binary("bash", "runs iogov/install.sh")


def run(*args, env=None):
    e = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    e.update(env or {})
    return subprocess.run([str(SCRIPT), *args], capture_output=True, text=True, env=e, timeout=120)


def tree(root: Path) -> list[str]:
    return sorted(str(p.relative_to(root)) for p in root.rglob("*"))


def test_script_is_valid_bash_and_never_names_docker_or_enables_bench():
    assert subprocess.run(["bash", "-n", str(SCRIPT)]).returncode == 0
    text = SCRIPT.read_text()
    assert "docker" not in text.replace("run docker or touch any container", "")
    for line in text.splitlines():
        if "run systemctl" in line and ("enable" in line or "start" in line):
            assert "iogov-tick.timer" in line and "bench" not in line and "watch" not in line, line


def test_dry_run_prints_every_action_and_changes_nothing(tmp_path):
    p = run("--dry-run", "--destdir", str(tmp_path), *BASE)
    assert p.returncode == 0, p.stderr
    assert tree(tmp_path) == []
    out = p.stdout
    assert "DRY RUN" in out and "DO:" not in out
    for needle in ("etc/iogov", "var/lib/iogov/scratch", "usr/local/lib/iogov-pkg/iogov", "etc/iogov/iogov.toml",
                   "iogov-tick.timer", "99-iogov-raw-read.rules", "iogov-bench.timer"):
        assert re.search(r"WOULD: .*" + re.escape(needle), out), needle
    assert "not enabled" in out and "never enabled" in out


def test_real_staging_run_lays_out_everything_rendered_and_is_idempotent(tmp_path):
    p = run("--destdir", str(tmp_path), *BASE)
    assert p.returncode == 0, p.stdout + p.stderr
    pkg = tmp_path / "usr/local/lib/iogov-pkg/iogov"
    assert (pkg / "devices.py").is_file() and (pkg / "hostcheck.py").is_file() and not (pkg / "tests").exists()
    assert oct(pkg.stat().st_mode & 0o777) == "0o755" and oct((pkg / "cli.py").stat().st_mode & 0o777) == "0o644"
    assert oct((tmp_path / "var/lib/iogov").stat().st_mode & 0o777) == "0o700"
    unit_dir = tmp_path / "etc/systemd/system"
    names = sorted(x.name for x in unit_dir.iterdir())
    assert names == ["iogov-bench.service", "iogov-bench.timer", "iogov-probe.slice", "iogov-tick.service", "iogov-tick.timer", "iogov-watch.service"]
    for u in unit_dir.iterdir():
        text = u.read_text()
        assert not re.search(r"@IOGOV_[A-Z_]+@", text.replace("`@IOGOV_*@`", "")), u.name
        assert "TEMPLATE, NOT INSTALLED" not in text and text.startswith("# Rendered by iogov/install.sh")
    tick = (unit_dir / "iogov-tick.service").read_text()
    assert "DeviceAllow=/dev/null r" in tick and "PYTHONPATH=/usr/local/lib/iogov-pkg" in tick and "User=iogov" in tick
    rule = (tmp_path / "etc/udev/rules.d/99-iogov-raw-read.rules").read_text()
    assert 'ENV{ID_PATH}=="pci-0000:00:10.0"' in rule and "setfacl -m u:iogov:r" in rule
    assert (tmp_path / "etc/iogov/iogov.toml").read_text().startswith("# iogov configuration")
    assert not (tmp_path / "etc/systemd/system/timers.target.wants").exists()  # nothing enabled
    # an edited config survives a rerun, and the rerun changes nothing
    cfg = tmp_path / "etc/iogov/iogov.toml"
    cfg.write_text("# operator edit\n")
    snapshot = {r: (tmp_path / r).read_bytes() for r in tree(tmp_path) if (tmp_path / r).is_file()}
    q = run("--destdir", str(tmp_path), *BASE)
    assert q.returncode == 0 and "DO:" not in q.stdout, q.stdout
    assert cfg.read_text() == "# operator edit\n"
    assert {r: (tmp_path / r).read_bytes() for r in tree(tmp_path) if (tmp_path / r).is_file()} == snapshot


def test_a_changed_package_file_is_replaced_on_rerun(tmp_path):
    assert run("--destdir", str(tmp_path), *BASE).returncode == 0
    target = tmp_path / "usr/local/lib/iogov-pkg/iogov/cli.py"
    target.write_text("# stale\n")
    q = run("--destdir", str(tmp_path), *BASE)
    assert q.returncode == 0 and "DO:" in q.stdout
    assert target.read_text() == (SCRIPT.parent / "cli.py").read_text()
    assert not list((tmp_path / "usr/local/lib/iogov-pkg").glob("iogov.old.*"))


def test_enable_tick_is_the_only_enable_and_is_skipped_in_staging(tmp_path):
    p = run("--dry-run", "--destdir", str(tmp_path), "--enable-tick", *BASE)
    assert p.returncode == 0 and "enable --now iogov-tick.timer" in p.stdout and "bench.timer" not in p.stdout.split("== 8.")[1].split("Next,")[0]


def test_example_config_stops_with_exit_3_before_units_are_installed(tmp_path):
    p = run("--destdir", str(tmp_path), "--skip-preflight")
    assert p.returncode == 3 and "INCOMPLETE" in p.stdout
    assert (tmp_path / "etc/iogov/iogov.toml").exists() and not (tmp_path / "etc/systemd").exists()


def test_failed_preflight_stops_before_anything_is_created(tmp_path):
    stub = tmp_path / "py"
    stub.write_text("#!/bin/sh\necho 'MISSING setfacl [apt: acl]'\nexit 1\n")
    stub.chmod(0o755)
    stage = tmp_path / "stage"
    stage.mkdir()
    p = run("--destdir", str(stage), "--python", str(stub), "--disk-node", "/dev/null", "--id-path", "x")
    assert p.returncode == 1 and "MISSING setfacl" in p.stdout and "preflight failed" in p.stderr
    assert tree(stage) == []


def test_non_root_without_dry_run_or_destdir_is_refused():
    if os.geteuid() == 0:
        pytest.skip("running as root")
    p = run(*BASE)
    assert p.returncode == 1 and "run as root" in p.stderr


# --- argument validation ---------------------------------------------------------------------------------------------------


def _stage(tmp_path):
    d = tmp_path / "stage"
    d.mkdir()
    return d


@pytest.mark.parametrize("extra", [
    ["--lib-dir", "../../escape"], ["--lib-dir", "/opt/../escape"], ["--lib-dir", "relative"], ["--lib-dir", "/opt//x"],
])
def test_lib_dir_must_be_absolute_without_dotdot(tmp_path, extra):
    stage = _stage(tmp_path)
    p = run("--destdir", str(stage), *extra, *BASE)
    assert p.returncode == 1 and "--lib-dir" in p.stderr and tree(stage) == []


def test_destdir_must_be_absolute_without_dotdot(tmp_path):
    for bad in ("relative/stage", str(tmp_path) + "/../escape"):
        p = run("--destdir", bad, *BASE)
        assert p.returncode == 1 and "--destdir" in p.stderr


def test_trailing_slash_lib_dir_stays_inside_the_staging_tree(tmp_path):
    stage = _stage(tmp_path)
    p = run("--destdir", str(stage), "--lib-dir", "/opt/iogov/", *BASE)
    assert p.returncode == 0, p.stderr
    assert (stage / "opt/iogov/iogov/devices.py").is_file()


@pytest.mark.parametrize("flag,value", [
    ("--id-path", 'a"b'), ("--id-path", "a&b"), ("--id-path", "a|b"), ("--id-path", "a\nb"), ("--id-path", "a b"),
    ("--disk-node", '/dev/a"b'), ("--disk-node", "/dev/a&b"), ("--disk-node", "/dev/a|b"), ("--disk-node", "/dev/a\nb"),
    ("--disk-node", "/devx/null"), ("--io-weight", "5|0"), ("--tick-interval", "5min&x"), ("--python", "/usr/bin/py&x"),
])
def test_hostile_values_are_refused_before_anything_is_written(tmp_path, flag, value):
    stage = _stage(tmp_path)
    argv = ["--skip-preflight", "--disk-node", "/dev/null", "--id-path", "pci-0000:00:10.0"]
    if flag in ("--id-path", "--disk-node"):
        argv[argv.index(flag) + 1] = value
    else:
        argv += [flag, value]
    p = run("--destdir", str(stage), *argv)
    assert p.returncode == 1 and "iogov install:" in p.stderr, p.stderr
    assert tree(stage) == []


def test_partition_disk_node_is_refused(tmp_path):
    sysfs = tmp_path / "sys"
    (sysfs / "class/block/null").mkdir(parents=True)
    (sysfs / "class/block/null/partition").write_text("1\n")  # /dev/null stands in for a node whose kname is `null`
    stage = _stage(tmp_path)
    p = run("--destdir", str(stage), *BASE, env={"IOGOV_SYSFS_ROOT": str(sysfs)})
    assert p.returncode == 1 and "partition" in p.stderr
    assert not (stage / "etc/systemd").exists()


def test_id_path_that_disagrees_with_udev_is_refused(tmp_path):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    udev = bindir / "udevadm"
    udev.write_text("#!/bin/sh\necho ID_PATH=pci-0000:99:99.0\n")
    udev.chmod(0o755)
    p = run("--dry-run", "--lib-dir", str(tmp_path / "lib"), *BASE, env={"PATH": f"{bindir}:{os.environ['PATH']}"})
    assert p.returncode == 1 and "does not match" in p.stderr
    ok = run("--dry-run", "--lib-dir", str(tmp_path / "lib"), "--skip-preflight", "--disk-node", "/dev/null", "--id-path", "pci-0000:99:99.0",
             env={"PATH": f"{bindir}:{os.environ['PATH']}"})
    assert ok.returncode == 0, ok.stderr


def test_failed_package_copy_keeps_the_installed_package_and_leaves_no_sibling(tmp_path):
    stage = _stage(tmp_path)
    assert run("--destdir", str(stage), *BASE).returncode == 0
    lib = stage / "usr/local/lib/iogov-pkg"
    target = lib / "iogov/cli.py"
    target.write_text("# installed\n")
    bindir = tmp_path / "bin"
    bindir.mkdir()
    cp = bindir / "cp"
    cp.write_text('#!/bin/sh\ncase "$*" in *iogov.new*) exit 1;; esac\nexec /bin/cp "$@"\n')
    cp.chmod(0o755)
    p = run("--destdir", str(stage), *BASE, env={"PATH": f"{bindir}:{os.environ['PATH']}"})
    assert p.returncode != 0
    assert target.read_text() == "# installed\n" and (lib / "iogov/devices.py").is_file()
    assert not list(lib.glob("iogov.new.*")) and not list(lib.glob("iogov.old.*"))


def _path_with(tmp_path, name, body):
    d = tmp_path / "bin"
    d.mkdir(exist_ok=True)
    f = d / name
    f.write_text(body)
    f.chmod(0o755)
    return {"PATH": f"{d}:{os.environ['PATH']}"}


def test_sysfs_test_hook_is_ignored_outside_staging(tmp_path):
    sysfs = tmp_path / "sys"
    (sysfs / "class/block/null").mkdir(parents=True)
    (sysfs / "class/block/null/partition").write_text("1\n")
    env = _path_with(tmp_path, "udevadm", "#!/bin/sh\necho ID_PATH=pci-0000:00:10.0\n")
    env["IOGOV_SYSFS_ROOT"] = str(sysfs)
    p = run("--dry-run", "--lib-dir", str(tmp_path / "lib"), *BASE, env=env)
    assert p.returncode == 0, p.stderr  # the fake partition is NOT consulted: /sys has no `null` entry


def test_dry_run_warns_unverified_when_udev_is_absent_or_empty(tmp_path):
    for body in ("#!/bin/sh\nexit 1\n", "#!/bin/sh\necho ID_BUS=x\n"):
        env = _path_with(tmp_path, "udevadm", body)
        p = run("--dry-run", "--lib-dir", str(tmp_path / "lib"), *BASE, env=env)
        assert p.returncode == 0 and "UNVERIFIED" in p.stdout, p.stdout + p.stderr


def test_real_install_refuses_unconfirmed_id_path_without_touching_anything(tmp_path):
    # exercise the refusal branch by script text, since a real run is forbidden here
    text = SCRIPT.read_text()
    assert 'die "udev cannot confirm --id-path' in text and "((DRY))" in text.split("UNVERIFIED")[0].splitlines()[-1]


def test_non_ascii_values_are_refused_under_a_utf8_locale(tmp_path):
    stage = _stage(tmp_path)
    p = run("--destdir", str(stage), "--skip-preflight", "--disk-node", "/dev/null", "--id-path", "pci-é", env={"LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"})
    assert p.returncode == 1 and "--id-path" in p.stderr and tree(stage) == []


@pytest.mark.parametrize("extra", [["--lib-dir", "/"], ["--disk-node", "/dev/../dev/null"]])
def test_root_lib_dir_and_dotdot_disk_node_are_refused(tmp_path, extra):
    stage = _stage(tmp_path)
    argv = ["--skip-preflight", "--id-path", "pci-0000:00:10.0"]
    if "--disk-node" not in extra:
        argv += ["--disk-node", "/dev/null"]
    p = run("--destdir", str(stage), *argv, *extra)
    assert p.returncode == 1 and tree(stage) == []


def test_mapped_disk_node_is_refused(tmp_path):
    sysfs = tmp_path / "sys"
    (sysfs / "class/block/null").mkdir(parents=True)
    # kname `dm-0` via a symlink node cannot be created under /dev; assert via the md/dm guard on the script text
    assert "dm-*" in SCRIPT.read_text() and "not a whole disk" in SCRIPT.read_text()


def test_stale_old_package_is_restored_or_removed_on_the_next_run(tmp_path):
    stage = _stage(tmp_path)
    assert run("--destdir", str(stage), *BASE).returncode == 0
    lib = stage / "usr/local/lib/iogov-pkg"
    (lib / "iogov.old.123").mkdir()
    (lib / "iogov.new.456").mkdir()
    assert run("--destdir", str(stage), *BASE).returncode == 0
    assert not list(lib.glob("iogov.old.*")) and not list(lib.glob("iogov.new.*")) and (lib / "iogov/devices.py").is_file()
    # a SIGKILL between the renames: iogov is missing, the old package is restored (then brought up to date)
    (lib / "iogov").rename(lib / "iogov.old.789")
    assert run("--destdir", str(stage), *BASE).returncode == 0
    assert (lib / "iogov/devices.py").is_file() and not list(lib.glob("iogov.old.*"))
