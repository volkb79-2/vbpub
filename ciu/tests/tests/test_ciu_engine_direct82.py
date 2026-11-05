"""Privileged filesystem-helper and injected ownership contracts."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from ciu import engine


def test_privileged_fs_op_runs_exact_root_helper_argv(monkeypatch, tmp_path: Path):
    """A fallback chown/chmod operation mounts only its physical target at `/t`."""

    monkeypatch.setenv("CGROUP_PARENT_DEV_INTERACTIVE", "dev-interactive.slice")
    monkeypatch.setattr(engine.governance, "check_slice_unit", lambda _name: (True, "loaded"))
    calls: list[tuple[list[str], bool]] = []
    monkeypatch.setattr(
        engine.procutil,
        "docker",
        lambda argv, *, check: calls.append((argv, check)),
    )

    engine.privileged_fs_op(tmp_path / "volume", "chown", "999:999")

    assert calls == [(
        ["run", "--rm", "--cgroup-parent", "dev-interactive.slice",
         "-v", f"{tmp_path / 'volume'}:/t", "alpine", "chown", "999:999", "/t"],
        True,
    )]


def test_privileged_rmtree_uses_loaded_slice_and_refuses_missing_bind_targets(
    monkeypatch, tmp_path: Path
):
    target = tmp_path / "root-owned"
    target.mkdir()
    target_info = target.lstat()
    parent_info = target.parent.stat()
    monkeypatch.setenv("CGROUP_PARENT_DEV_INTERACTIVE", "dev-interactive.slice")
    checked = []
    monkeypatch.setattr(
        engine.governance, "check_slice_unit",
        lambda name: checked.append(name) or (True, "loaded"),
    )
    calls = []
    def fake_docker(argv, *, check):
        if argv[:1] == ["version"]:
            return subprocess.CompletedProcess(
                argv, 0, "29.9.2|29.9.2", ""
            )
        calls.append((argv, check))
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(engine.procutil, "docker", fake_docker)

    engine.privileged_rmtree(
        target,
        target_identity=(target_info.st_dev, target_info.st_ino),
        parent_identity=(parent_info.st_dev, parent_info.st_ino),
    )

    assert checked == ["dev-interactive.slice"]
    assert calls == [(
        [
            "run", "--rm", "--cgroup-parent", "dev-interactive.slice",
            "--mount", f"type=bind,src={tmp_path},dst=/t,bind-recursive=disabled",
            "alpine", "sh", "-c", engine._privileged_rmtree_script(),
            "ciu-rmtree", "/t/root-owned", f"{target_info.st_dev}:{target_info.st_ino}",
            f"{parent_info.st_dev}:{parent_info.st_ino}", "/proc/self/mountinfo",
        ],
        True,
    )]


def test_privileged_rmtree_fails_closed_without_slice_or_for_unsafe_path(
    monkeypatch, tmp_path: Path
):
    calls = []
    monkeypatch.delenv("CGROUP_PARENT_DEV_INTERACTIVE", raising=False)
    monkeypatch.setattr(
        engine.procutil, "docker", lambda argv, **kwargs: calls.append((argv, kwargs))
    )
    with pytest.raises(RuntimeError, match="CGROUP_PARENT_DEV_INTERACTIVE is required"):
        engine.privileged_rmtree(
            tmp_path / "target", target_identity=(1, 2), parent_identity=(1, 1)
        )
    assert calls == []

    monkeypatch.setenv("CGROUP_PARENT_DEV_INTERACTIVE", "not/a.slice")
    with pytest.raises(RuntimeError, match="not a slice unit name"):
        engine.privileged_rmtree(
            tmp_path / "target", target_identity=(1, 2), parent_identity=(1, 1)
        )
    assert calls == []

    monkeypatch.setenv("CGROUP_PARENT_DEV_INTERACTIVE", "dev-interactive.slice")
    monkeypatch.setattr(engine.governance, "check_slice_unit", lambda _name: (False, "absent"))
    with pytest.raises(RuntimeError, match="not verified loaded"):
        engine.privileged_rmtree(
            tmp_path / "target", target_identity=(1, 2), parent_identity=(1, 1)
        )
    assert calls == []

    monkeypatch.setattr(engine.governance, "check_slice_unit", lambda _name: (True, "loaded"))
    with pytest.raises(RuntimeError, match="absolute child path"):
        engine.privileged_rmtree(
            Path("relative-target"), target_identity=(1, 2), parent_identity=(1, 1)
        )
    with pytest.raises(RuntimeError, match="containing ','"):
        engine.privileged_rmtree(
            tmp_path / "comma,parent" / "target",
            target_identity=(1, 2), parent_identity=(1, 1),
        )
    assert calls == []


def test_privileged_rmtree_requires_supported_client_and_engine_versions(
    monkeypatch, tmp_path: Path
):
    target = tmp_path / "target"
    target.mkdir()
    target_info = target.lstat()
    parent_info = target.parent.stat()
    monkeypatch.setenv("CGROUP_PARENT_DEV_INTERACTIVE", "dev-interactive.slice")
    monkeypatch.setattr(
        engine.governance, "check_slice_unit", lambda _name: (True, "loaded")
    )
    calls = []

    def old_docker(argv, *, check):
        calls.append((argv, check))
        return subprocess.CompletedProcess(argv, 0, "24.0.9|29.9.2", "")

    monkeypatch.setattr(engine.procutil, "docker", old_docker)
    with pytest.raises(
        RuntimeError, match=r"CLI and Engine 25\.0\.0 or newer"
    ):
        engine.privileged_rmtree(
            target,
            target_identity=(target_info.st_dev, target_info.st_ino),
            parent_identity=(parent_info.st_dev, parent_info.st_ino),
        )
    assert len(calls) == 1
    assert calls[0][0][0] == "version"

    def unknown_docker(argv, *, check):
        return subprocess.CompletedProcess(argv, 0, "|29.9.2", "")

    monkeypatch.setattr(engine.procutil, "docker", unknown_docker)
    with pytest.raises(RuntimeError, match="could not determine Docker CLI version"):
        engine.privileged_rmtree(
            target,
            target_identity=(target_info.st_dev, target_info.st_ino),
            parent_identity=(parent_info.st_dev, parent_info.st_ino),
        )


@pytest.mark.parametrize(
    ("version_result", "error_match"),
    [
        (RuntimeError("docker unavailable"), "docker unavailable"),
        (subprocess.CompletedProcess([], 1, "", "daemon denied"), "daemon denied"),
        (subprocess.CompletedProcess([], 1, "probe output", ""), "probe output"),
        (subprocess.CompletedProcess([], 1, "", ""), "no version output"),
        (subprocess.CompletedProcess([], 0, "malformed", ""), "malformed"),
        (subprocess.CompletedProcess([], 0, "29.9.2|unknown", ""), "Docker Engine version"),
        (subprocess.CompletedProcess([], 0, "29.9.2|24.0.9", ""), "Docker Engine version"),
    ],
)
def test_privileged_rmtree_rejects_unverifiable_or_old_docker_versions(
    monkeypatch, tmp_path: Path, version_result, error_match
):
    target = tmp_path / "target"
    target.mkdir()
    target_info = target.lstat()
    parent_info = target.parent.stat()
    monkeypatch.setenv("CGROUP_PARENT_DEV_INTERACTIVE", "dev-interactive.slice")
    monkeypatch.setattr(
        engine.governance, "check_slice_unit", lambda _name: (True, "loaded")
    )
    calls = []

    def fake_docker(argv, *, check):
        calls.append((argv, check))
        if isinstance(version_result, Exception):
            raise version_result
        return version_result

    monkeypatch.setattr(engine.procutil, "docker", fake_docker)
    with pytest.raises(RuntimeError, match=error_match):
        engine.privileged_rmtree(
            target,
            target_identity=(target_info.st_dev, target_info.st_ino),
            parent_identity=(parent_info.st_dev, parent_info.st_ino),
        )
    assert len(calls) == 1
    assert calls[0][0][0] == "version"


@pytest.mark.parametrize(
    "target_identity,parent_identity",
    [
        (None, (1, 2)),
        ((1,), (1, 2)),
        (("1", 2), (1, 2)),
        ((1, -1), (1, 2)),
        ((1, 2), None),
    ],
)
def test_privileged_rmtree_rejects_invalid_device_inode_identities(
    monkeypatch, tmp_path: Path, target_identity, parent_identity
):
    monkeypatch.setenv("CGROUP_PARENT_DEV_INTERACTIVE", "dev-interactive.slice")
    monkeypatch.setattr(
        engine.governance, "check_slice_unit", lambda _name: (True, "loaded")
    )
    with pytest.raises(RuntimeError, match="valid .* device:inode"):
        engine.privileged_rmtree(
            tmp_path / "target",
            target_identity=target_identity,
            parent_identity=parent_identity,
        )


def test_privileged_rmtree_reports_identity_mismatch_without_deleting_other_data(
    monkeypatch, tmp_path: Path
):
    target = tmp_path / "target"
    target.mkdir()
    payload = target / "keep.txt"
    payload.write_text("leave intact", encoding="utf-8")
    target_info = target.lstat()
    parent_info = target.parent.stat()
    monkeypatch.setenv("CGROUP_PARENT_DEV_INTERACTIVE", "dev-interactive.slice")
    monkeypatch.setattr(
        engine.governance, "check_slice_unit", lambda _name: (True, "loaded")
    )
    calls = []

    def fake_docker(argv, *, check):
        calls.append(argv)
        if argv[0] == "version":
            return subprocess.CompletedProcess(argv, 0, "29.9.2|29.9.2", "")
        raise subprocess.CalledProcessError(
            43, argv, output="", stderr="S6.5 identity mismatch"
        )

    monkeypatch.setattr(engine.procutil, "docker", fake_docker)
    with pytest.raises(
        RuntimeError, match=r"target or parent identity changed \(exit 43\)"
    ):
        engine.privileged_rmtree(
            target,
            target_identity=(target_info.st_dev, target_info.st_ino),
            parent_identity=(parent_info.st_dev, parent_info.st_ino),
        )

    assert len(calls) == 2
    assert payload.read_text(encoding="utf-8") == "leave intact"


@pytest.mark.parametrize(
    ("returncode", "message"),
    [
        (44, "nested mount below the checkout target"),
        (42, "bind target is absent"),
        (45, "mount information could not be read"),
        (1, None),
    ],
)
def test_privileged_rmtree_reports_mount_refusal_and_preserves_other_errors(
    monkeypatch, tmp_path: Path, returncode, message
):
    target = tmp_path / "target"
    target.mkdir()
    target_info = target.lstat()
    parent_info = target.parent.stat()
    monkeypatch.setenv("CGROUP_PARENT_DEV_INTERACTIVE", "dev-interactive.slice")
    monkeypatch.setattr(
        engine.governance, "check_slice_unit", lambda _name: (True, "loaded")
    )

    def fake_docker(argv, *, check):
        if argv[0] == "version":
            return subprocess.CompletedProcess(argv, 0, "29.9.2|29.9.2", "")
        raise subprocess.CalledProcessError(returncode, argv, output="", stderr="refused")

    monkeypatch.setattr(engine.procutil, "docker", fake_docker)
    if message is None:
        with pytest.raises(subprocess.CalledProcessError) as raised:
            engine.privileged_rmtree(
                target,
                target_identity=(target_info.st_dev, target_info.st_ino),
                parent_identity=(parent_info.st_dev, parent_info.st_ino),
            )
        assert raised.value.returncode == returncode
    else:
        with pytest.raises(RuntimeError, match=message):
            engine.privileged_rmtree(
                target,
                target_identity=(target_info.st_dev, target_info.st_ino),
                parent_identity=(parent_info.st_dev, parent_info.st_ino),
            )
    assert target.is_dir()


def test_privileged_rmtree_mountinfo_guard_refuses_nested_mount(tmp_path: Path):
    mountinfo = tmp_path / "mountinfo"
    mountinfo.write_text(
        "36 25 0:32 / /t rw,relatime - ext4 /dev/sda rw\n"
        "37 36 0:55 / /t/volume rw,relatime - ext4 /dev/sdb rw\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        [
            "sh", "-c", engine._privileged_rmtree_script(), "ciu-rmtree",
            "/t/target", "0:2", "0:3", str(mountinfo),
        ],
        capture_output=True, text=True, check=False,
    )

    assert result.returncode == 44
    assert "refusing nested mount below /t" in result.stderr


def test_privileged_rmtree_script_reports_absence_before_identity_mismatch(
    tmp_path: Path,
):
    mountinfo = tmp_path / "mountinfo"
    mountinfo.write_text(
        "36 25 0:32 / / rw,relatime - ext4 /dev/sda rw\n",
        encoding="utf-8",
    )
    target = f"/t/ciu-test-absent-{tmp_path.name}"
    result = subprocess.run(
        [
            "sh", "-c", engine._privileged_rmtree_script(), "ciu-rmtree",
            target, "bad-target-identity", "bad-parent-identity", str(mountinfo),
        ],
        capture_output=True, text=True, check=False,
    )

    assert result.returncode == 42
    assert "refusing absent bind target" in result.stderr


def test_chown_with_injected_seam_uses_seam_and_never_calls_os_chown(monkeypatch, tmp_path: Path):
    """Tests/callers can supply ownership behavior without mutating host ownership."""

    calls: list[tuple[Path, int, int]] = []
    monkeypatch.setattr(
        engine.os,
        "chown",
        lambda *_args: (_ for _ in ()).throw(AssertionError("native chown must not run")),
    )

    engine._chown_with_fallback(
        tmp_path / "logical",
        999,
        998,
        physical_path=tmp_path / "physical",
        chown_fn=lambda path, uid, gid: calls.append((path, uid, gid)),
    )

    assert calls == [(tmp_path / "logical", 999, 998)]
