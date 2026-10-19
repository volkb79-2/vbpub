from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest


TOOLS = Path(__file__).resolve().parents[1] / "tools"


def _load_tool(filename: str):
    path = TOOLS / filename
    spec = importlib.util.spec_from_file_location(path.stem, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def worker():
    return _load_tool("remote_mutation_audit.py")


@pytest.fixture(scope="module")
def zfs_broker():
    return _load_tool("remote_mutation_zfs_broker.py")


def _manifest(tmp_path: Path, *, project: str = "demo", project_root: str = ".", source: str = "src/demo") -> Path:
    path = tmp_path / "audit.toml"
    path.write_text(
        f'''[audit]
project = "{project}"
project_root = "{project_root}"
source_roots = ["{source}"]
test_argv = ["python", "-m", "pytest", "-q", "-x"]
jobs = 1
''',
        encoding="utf-8",
    )
    return path


def test_host_launcher_refuses_missing_cgroup_parent_before_git_or_docker(tmp_path):
    script = TOOLS / "remote-mutation-audit-host.sh"
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    effects = tmp_path / "effects.log"
    for name in ("git", "docker", "systemctl"):
        tool = fake_bin / name
        tool.write_text("#!/bin/sh\necho called >> \"$EFFECTS\"\nexit 99\n", encoding="utf-8")
        tool.chmod(0o755)
    env = os.environ.copy()
    for name in (
        "CGROUP_PARENT_DEV_GATES", "MUTATION_AUDIT_CGROUP_PARENT",
    ):
        env.pop(name, None)
    env["PATH"] = f"{fake_bin}:/usr/bin:/bin"
    env["EFFECTS"] = str(effects)
    result = subprocess.run(
        [str(script), "--manifest-rel", "audit.toml", "--repo", "https://example.invalid/repo.git"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2
    assert "refusing ungoverned Docker containers" in result.stderr
    assert not effects.exists()


def test_host_launcher_refuses_unloaded_cgroup_parent_before_clone(tmp_path):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    systemctl = fake_bin / "systemctl"
    systemctl.write_text(
        "#!/bin/sh\nprintf 'LoadState=not-found\\nFragmentPath=\\n'\n",
        encoding="utf-8",
    )
    systemctl.chmod(0o755)
    effects = tmp_path / "effects.log"
    for name in ("git", "docker"):
        tool = fake_bin / name
        tool.write_text("#!/bin/sh\necho called >> \"$EFFECTS\"\nexit 99\n", encoding="utf-8")
        tool.chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}:/usr/bin:/bin"
    env["EFFECTS"] = str(effects)
    env["CGROUP_PARENT_DEV_GATES"] = "misspelled.slice"
    result = subprocess.run(
        [str(TOOLS / "remote-mutation-audit-host.sh"), "--manifest-rel", "audit.toml", "--repo", "https://example.invalid/repo.git"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2
    assert "not a loaded systemd unit" in result.stderr
    assert not effects.exists()


def test_host_launcher_refuses_auto_vivified_transient_slice_before_clone(tmp_path):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    systemctl = fake_bin / "systemctl"
    systemctl.write_text(
        "#!/bin/sh\nprintf 'LoadState=loaded\\nFragmentPath=\\n'\n",
        encoding="utf-8",
    )
    systemctl.chmod(0o755)
    effects = tmp_path / "effects.log"
    for name in ("git", "docker"):
        tool = fake_bin / name
        tool.write_text("#!/bin/sh\necho called >> \"$EFFECTS\"\nexit 99\n", encoding="utf-8")
        tool.chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}:/usr/bin:/bin"
    env["EFFECTS"] = str(effects)
    env["CGROUP_PARENT_DEV_GATES"] = "misspelled.slice"

    result = subprocess.run(
        [str(TOOLS / "remote-mutation-audit-host.sh"), "--manifest-rel", "audit.toml", "--repo", "https://example.invalid/repo.git"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 2
    assert "only a transient systemd slice" in result.stderr
    assert not effects.exists()


def test_host_launcher_requires_supported_cmru_before_cloning(tmp_path):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    systemctl = fake_bin / "systemctl"
    systemctl.write_text(
        "#!/bin/sh\nprintf 'LoadState=loaded\\nFragmentPath=/etc/systemd/system/dev-gates.slice\\n'\n",
        encoding="utf-8",
    )
    systemctl.chmod(0o755)
    effects = tmp_path / "effects.log"
    cmru = fake_bin / "cmru"
    cmru.write_text(
        "#!/bin/sh\n"
        "if [ \"$1\" = --version ]; then printf 'cmru 6.1.0\\n'; exit 0; fi\n"
        "echo called >> \"$EFFECTS\"\n"
        "exit 99\n",
        encoding="utf-8",
    )
    cmru.chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}:/usr/bin:/bin"
    env["EFFECTS"] = str(effects)
    env["CGROUP_PARENT_DEV_GATES"] = "dev-gates.slice"

    result = subprocess.run(
        [
            str(TOOLS / "remote-mutation-audit-host.sh"),
            "--manifest-rel", "audit.toml",
            "--repo", "https://example.invalid/repo.git",
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 2
    assert "CMRU 6.2.0 or later" in result.stderr
    assert not effects.exists()


def test_host_launcher_passes_verified_parent_to_both_cmru_and_buildx():
    script = (TOOLS / "remote-mutation-audit-host.sh").read_text(encoding="utf-8")
    assert 'CGROUP_PARENT_DEV_GATES="$cgroup_parent" \\' in script
    assert 'CMRU_DOCKER_CGROUP_PARENT="$cgroup_parent" \\' in script
    assert "cmru handler wheel-build --cwd \"$checkout/run-gate-project\"" in script
    assert '"$checkout/tester-unified/build-image.sh" \\' in script
    assert '--cgroup-parent "$cgroup_parent"' in script


@pytest.mark.parametrize(
    ("project", "project_root", "source"),
    [
        ("../escape", ".", "src/demo"),
        ("demo", "../escape", "src/demo"),
        ("demo", ".", "../escape"),
        ("demo", ".", "."),
    ],
)
def test_manifest_paths_cannot_escape_checkout(worker, tmp_path, project, project_root, source):
    with pytest.raises(ValueError):
        worker.load_config(_manifest(tmp_path, project=project, project_root=project_root, source=source))


def test_stable_ordinals_partition_without_overlap(worker, tmp_path):
    source = tmp_path / "src" / "demo"
    source.mkdir(parents=True)
    (source / "logic.py").write_text(
        "def choose(value):\n    if value <= 3 and value != 1:\n        return True\n    return False\n",
        encoding="utf-8",
    )
    cfg = worker.load_config(_manifest(tmp_path))
    total, all_jobs = worker._source_jobs(tmp_path, cfg, None, 1, 0, 1)
    total_even, even = worker._source_jobs(tmp_path, cfg, None, 1, 0, 2)
    total_odd, odd = worker._source_jobs(tmp_path, cfg, None, 1, 1, 2)
    assert total == total_even == total_odd
    assert {job[0] for job in all_jobs} == {job[0] for job in even} | {job[0] for job in odd}
    assert {job[0] for job in even}.isdisjoint({job[0] for job in odd})


@pytest.mark.parametrize(
    ("counts", "mutants", "outcome", "exit_code"),
    [
        ({"KILLED": 0, "SURVIVED": 0, "TIMEOUT": 0, "ERROR": 0}, 0, "INCONCLUSIVE_NO_MUTANTS", 2),
        ({"KILLED": 1, "SURVIVED": 0, "TIMEOUT": 0, "ERROR": 0}, 1, "PASS", 0),
        ({"KILLED": 0, "SURVIVED": 1, "TIMEOUT": 0, "ERROR": 0}, 1, "SURVIVORS", 1),
        ({"KILLED": 0, "SURVIVED": 0, "TIMEOUT": 1, "ERROR": 0}, 1, "INCONCLUSIVE_ERRORS", 2),
    ],
)
def test_audit_verdict_never_treats_empty_or_broken_run_as_pass(
    worker, counts, mutants, outcome, exit_code,
):
    assert worker._audit_verdict(counts, mutants) == (outcome, exit_code)


def _zfs_manifest(tmp_path: Path) -> Path:
    manifest = tmp_path / "zfs.toml"
    manifest.write_text(
        '''[zfs]
enabled = true
dataset = "tank/nyxloom-audits/demo"
container_mount = "/mutation-state"
''',
        encoding="utf-8",
    )
    return manifest


def test_zfs_broker_is_allowlisted_guarded_and_nonrecursive(zfs_broker, monkeypatch, tmp_path):
    calls: list[list[str]] = []
    mountpoint = tmp_path / "dataset"
    mountpoint.mkdir()

    def fake_run(argv, **_kwargs):
        calls.append(list(argv))
        if argv[1:5] == ["list", "-H", "-o", "name"]:
            return subprocess.CompletedProcess(argv, 0, "tank/nyxloom-audits/demo\n", "")
        if argv[1:5] == ["get", "-H", "-o", "value"] and argv[-2] == "nyxloom:mutation-audit":
            return subprocess.CompletedProcess(argv, 0, "on\n", "")
        if argv[1:5] == ["get", "-H", "-o", "value"] and argv[-2] == "mountpoint":
            return subprocess.CompletedProcess(argv, 0, f"{mountpoint}\n", "")
        if "snapshot" in argv and "-t" in argv:
            return subprocess.CompletedProcess(argv, 1, "", "not found")
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(zfs_broker.subprocess, "run", fake_run)
    monkeypatch.setenv("NYXLOOM_ZFS_BROKER_TOKEN", "a" * 64)
    broker = zfs_broker.ZfsBroker(
        _zfs_manifest(tmp_path), "nyxloom-test-run", tmp_path / "events.jsonl",
        "tank/nyxloom-audits", False,
    )
    assert broker.execute("snapshot_create", None)["outcome"] == "PASS"
    assert broker.execute("restore", "mutant-1")["outcome"] == "PASS"
    assert broker.execute("snapshot_destroy", None)["outcome"] == "PASS"
    rollback = next(call for call in calls if "rollback" in call)
    assert "-r" not in rollback and "-R" not in rollback
    assert any("nyxloom:mutation-audit" in call for call in calls)
    assert zfs_broker._dispatch(broker, {"operation": "status", "token": "wrong"}) == {
        "outcome": "ERROR", "error": "unauthorized host lifecycle request",
    }
    assert zfs_broker._dispatch(
        broker, {"operation": "status", "token": "a" * 64},
    )["outcome"] == "PASS"


def test_zfs_broker_rejects_dataset_outside_host_allowlist(zfs_broker, monkeypatch, tmp_path):
    monkeypatch.setenv("NYXLOOM_ZFS_BROKER_TOKEN", "b" * 64)
    with pytest.raises(zfs_broker.BrokerError, match="host-authorized"):
        zfs_broker.ZfsBroker(
            _zfs_manifest(tmp_path), "nyxloom-test-run", tmp_path / "events.jsonl",
            "tank/somewhere-else", False,
        )
