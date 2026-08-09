"""The gate refuses an absent or unverified host cgroup tier."""

from __future__ import annotations

import os
import subprocess
import tomllib
from pathlib import Path

from conftest import PROJECT_ROOT

SCRIPT = PROJECT_ROOT / "tools" / "cgroup-parent.sh"
GATE_DRIVER = PROJECT_ROOT / "tools" / "tester-unified-gate.sh"
NYXLOOM_TOML = PROJECT_ROOT / "nyxloom-trove" / "nyxloom.toml"


def run_probe(tmp_path: Path, verdict: str, *, slice_name: str | None):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir(exist_ok=True)
    argv_log = tmp_path / "docker.argv"
    docker = fake_bin / "docker"
    docker.write_text(
        "#!/bin/sh\nprintf '%s\\n' \"$*\" >\"$DOCKER_ARGV_LOG\"\n"
        f"printf '%s\\n' {verdict!r}\n",
        encoding="utf-8",
    )
    docker.chmod(0o755)
    env = {
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "DOCKER_ARGV_LOG": str(argv_log),
    }
    if slice_name is not None:
        env["CGROUP_PARENT_DEV_BACKGROUND"] = slice_name
    proc = subprocess.run(
        [str(SCRIPT)], capture_output=True, text=True, env=env, check=False
    )
    return proc, argv_log


def test_unset_background_tier_refuses_before_docker(tmp_path: Path):
    proc, argv_log = run_probe(tmp_path, "OK", slice_name=None)

    assert proc.returncode != 0
    assert "CGROUP_PARENT_DEV_BACKGROUND is unset" in proc.stderr
    assert not argv_log.exists()


def test_verified_configured_slice_is_the_only_stdout_value(tmp_path: Path):
    proc, argv_log = run_probe(
        tmp_path, "OK", slice_name="dev-background.slice"
    )

    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == "dev-background.slice\n"
    argv = argv_log.read_text(encoding="utf-8")
    assert "--cgroupns=host" in argv
    assert "--network=none" in argv
    assert "CG_REL=/dev.slice/dev-background.slice" in argv


def test_missing_or_unconfigured_slice_is_refused(tmp_path: Path):
    for verdict in (
        "MISSING /sys/fs/cgroup/dev.slice/typo.slice",
        "UNCONFIGURED /sys/fs/cgroup/dev.slice/dev-background.slice",
    ):
        proc, _ = run_probe(
            tmp_path, verdict, slice_name="dev-background.slice"
        )
        assert proc.returncode != 0
        assert "refusing" in proc.stderr or "kernel-default" in proc.stderr


def test_non_slice_name_is_refused_before_docker(tmp_path: Path):
    proc, argv_log = run_probe(tmp_path, "OK", slice_name="dev-background")

    assert proc.returncode != 0
    assert "does not name a systemd slice" in proc.stderr
    assert not argv_log.exists()


def test_nyxloom_gate_uses_verified_value_without_a_literal_slice():
    document = tomllib.loads(NYXLOOM_TOML.read_text(encoding="utf-8"))
    argv = document["gates"]["tester-unified"]["argv"]
    driver = GATE_DRIVER.read_text(encoding="utf-8")

    assert argv == [
        "bash",
        "{worktree}/assay/tools/tester-unified-gate.sh",
        "{worktree}",
    ]
    assert '"$worktree/assay/tools/cgroup-parent.sh"' in driver
    assert '--cgroup-parent="$cgroup_parent"' in driver
    assert "nyxloom-gates.slice" not in driver
    assert "dev-background.slice" not in driver
    assert "ASSAY_GATE_HOST_REPO_ROOT" in driver
    assert '--mount "type=bind,src=$host_repo_root,dst=/workspaces/vbpub"' in driver
