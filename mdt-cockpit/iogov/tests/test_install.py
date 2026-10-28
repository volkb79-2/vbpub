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
