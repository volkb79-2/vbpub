from __future__ import annotations

import importlib.util
import os
import signal
import stat
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "run_release_gate", ROOT / "tools" / "run_release_gate.py",
)
assert SPEC is not None and SPEC.loader is not None
run_release_gate = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = run_release_gate
SPEC.loader.exec_module(run_release_gate)


class _AssayGit:
    def __init__(self, repo: Path):
        self.repo = repo

    def repo_top(self, project: Path) -> Path:
        return self.repo

    def run(self, repo: Path, *args: str) -> str:
        assert repo == self.repo
        assert args == ("rev-parse", "--path-format=absolute", "--git-common-dir")
        return str(self.repo / ".git")


def _fixture(tmp_path: Path, monkeypatch):
    repo = tmp_path / "repository"
    project = repo / "cmru"
    project.mkdir(parents=True)
    root_secret = repo / "cmru.secret.toml"
    project_secret = project / "cmru.secret.toml"
    root_secret.write_bytes(b"root credential\n")
    project_secret.write_bytes(b"project override\n")
    root_secret.chmod(0o640)
    project_secret.chmod(0o600)
    metadata = {
        path: (
            stat.S_IMODE(path.stat().st_mode),
            path.stat().st_mtime_ns,
            path.stat().st_uid,
            path.stat().st_gid,
        )
        for path in (root_secret, project_secret)
    }
    auth = SimpleNamespace(token="test-publisher-token")
    git = _AssayGit(repo)
    calls = []

    def load_components():
        def build_facts(repo_root, project_root, *, git_auth, assay_git):
            assert repo_root == repo
            assert project_root == project
            assert git_auth is auth
            assert assay_git is git
            assert root_secret.is_symlink()
            assert project_secret.is_symlink()
            return {"head": "abc", "selected_tag": "cmru-v1.0.0"}

        baseline = SimpleNamespace(
            _repository_git_auth=lambda root: auth,
            build_facts=build_facts,
        )
        return git, baseline

    monkeypatch.setattr(run_release_gate, "PROJECT_ROOT", project)
    monkeypatch.setattr(run_release_gate, "_load_components", load_components)
    return repo, root_secret, project_secret, metadata, auth, calls


def test_release_lanes_run_with_secret_files_and_ambient_credentials_masked(
    tmp_path, monkeypatch,
):
    repo, root_secret, project_secret, metadata, _auth, calls = _fixture(tmp_path, monkeypatch)
    private_root = tmp_path / "private-backups"
    private_root.mkdir(mode=0o700)
    monkeypatch.setenv("GITHUB_PUSH_PAT", "ambient-push-token")
    monkeypatch.setenv("GITHUB_TOKEN", "ambient-api-token")
    monkeypatch.setenv("CMRU_GIT_AUTH_TOKEN", "ambient-git-token")
    monkeypatch.setenv("RUN_GATE_EXTRA_MOUNTS", "/tmp/private=private")
    monkeypatch.setenv("CMRU_ASSAY_BASELINE_FACTS", "stale-facts")

    def invoke(root, lane, environment):
        assert root == repo
        assert root_secret.is_symlink()
        assert project_secret.is_symlink()
        for path in (root_secret, project_secret):
            target = Path(os.readlink(path)) if path.is_symlink() else None
            assert target is not None and target.is_absolute()
            assert not target.is_relative_to(repo)
        backups = [path for path in private_root.iterdir() if path.is_dir()]
        assert len(backups) == 1
        assert stat.S_IMODE(backups[0].stat().st_mode) == 0o700
        assert not private_root.resolve().is_relative_to(repo)
        assert all(key not in environment for key in run_release_gate.SECRET_ENV_KEYS)
        assert run_release_gate.EXTRA_MOUNTS_ENV_KEY not in environment
        if lane == "mutation":
            assert environment[run_release_gate.FACTS_ENV_KEY] == (
                '{"head":"abc","selected_tag":"cmru-v1.0.0"}'
            )
        else:
            assert run_release_gate.FACTS_ENV_KEY not in environment
        calls.append(lane)
        return 0

    monkeypatch.setattr(run_release_gate, "_invoke_lane", invoke)
    assert run_release_gate.run_release_gate(repo, temp_parent=private_root) == 0
    assert calls == ["installed-wheel", "assay", "coverage", "mutation", "canary", "enroll"]
    assert root_secret.read_bytes() == b"root credential\n"
    assert project_secret.read_bytes() == b"project override\n"
    assert {
        path: (
            stat.S_IMODE(path.stat().st_mode),
            path.stat().st_mtime_ns,
            path.stat().st_uid,
            path.stat().st_gid,
        )
        for path in (root_secret, project_secret)
    } == metadata


def test_release_gate_restores_secrets_after_a_failed_registered_lane(tmp_path, monkeypatch):
    repo, root_secret, project_secret, _metadata, _auth, calls = _fixture(tmp_path, monkeypatch)
    private_root = tmp_path / "private-backups"
    private_root.mkdir(mode=0o700)

    def invoke(_root, lane, _environment):
        calls.append(lane)
        return 19 if lane == "assay" else 0

    monkeypatch.setattr(run_release_gate, "_invoke_lane", invoke)
    assert run_release_gate.run_release_gate(repo, temp_parent=private_root) == 19
    assert calls == ["installed-wheel", "assay"]
    assert root_secret.read_bytes() == b"root credential\n"
    assert project_secret.read_bytes() == b"project override\n"


@pytest.mark.parametrize("signum", [signal.SIGTERM, signal.SIGHUP])
def test_secret_overlay_restores_files_and_handler_when_termination_signal_arrives(
    tmp_path, monkeypatch, signum,
):
    repo = tmp_path / "repository"
    repo.mkdir()
    secret = repo / "cmru.secret.toml"
    secret.write_bytes(b"publisher credential\n")
    private_root = tmp_path / "private-backups"
    private_root.mkdir(mode=0o700)
    handler_calls = []

    def record_signal_handler(received_signum, handler):
        handler_calls.append((received_signum, handler))
        return signal.SIG_DFL

    monkeypatch.setattr(run_release_gate.signal, "signal", record_signal_handler)

    with pytest.raises(SystemExit) as interrupted:
        with run_release_gate._mask_secret_overlays(
            [secret], mount_root=repo, temp_parent=private_root,
        ):
            assert secret.is_symlink()
            handler_calls[0][1](signum, None)

    assert interrupted.value.code == 128 + signum
    assert secret.read_bytes() == b"publisher credential\n"
    assert handler_calls[2:] == [
        (signal.SIGTERM, signal.SIG_DFL),
        (signal.SIGHUP, signal.SIG_DFL),
    ]


def test_secret_overlay_preserves_atomic_rotation_during_gate(tmp_path, monkeypatch):
    repo = tmp_path / "repository"
    repo.mkdir()
    secret = repo / "cmru.secret.toml"
    secret.write_bytes(b"old publisher credential\n")
    private_root = tmp_path / "private-backups"
    private_root.mkdir(mode=0o700)
    replacement = tmp_path / "rotated.secret"
    replacement.write_bytes(b"rotated publisher credential\n")

    try:
        with run_release_gate._mask_secret_overlays(
            [secret], mount_root=repo, temp_parent=private_root,
        ):
            assert secret.is_symlink()
            real_replace = os.replace
            rotated = False

            def rotate_at_restore(source, destination):
                nonlocal rotated
                if (
                    not rotated
                    and Path(source) == secret
                    and ".restore-" in Path(destination).name
                ):
                    rotated = True
                    real_replace(replacement, secret)
                return real_replace(source, destination)

            monkeypatch.setattr(os, "replace", rotate_at_restore)
    except RuntimeError as exc:
        assert "preserving the replacement" in str(exc)
        assert "private publisher-secret backups retained at" in str(exc)
        assert rotated
        backup_root = Path(str(exc).rsplit("retained at ", 1)[1])
    else:
        raise AssertionError("atomic credential rotation was silently overwritten")

    assert secret.read_bytes() == b"rotated publisher credential\n"
    backup = next(backup_root.glob("overlay-*.bin"))
    assert backup.read_bytes() == b"old publisher credential\n"


def test_secret_overlay_preserves_atomic_rotation_at_mask_boundary(tmp_path, monkeypatch):
    repo = tmp_path / "repository"
    repo.mkdir()
    secret = repo / "cmru.secret.toml"
    secret.write_bytes(b"old publisher credential\n")
    private_root = tmp_path / "private-backups"
    private_root.mkdir(mode=0o700)
    replacement = tmp_path / "rotated.secret"
    replacement.write_bytes(b"rotated publisher credential\n")
    real_replace = os.replace
    rotated = False

    def rotate_before_mask_move(source, destination):
        nonlocal rotated
        if (
            not rotated
            and Path(source) == secret
            and ".mask-displaced-" in Path(destination).name
        ):
            rotated = True
            real_replace(replacement, secret)
        return real_replace(source, destination)

    monkeypatch.setattr(os, "replace", rotate_before_mask_move)
    with pytest.raises(RuntimeError, match="publisher secret changed before masking"):
        with run_release_gate._mask_secret_overlays(
            [secret], mount_root=repo, temp_parent=private_root,
        ):
            pytest.fail("the overlay was installed after a credential rotation")

    assert rotated
    assert secret.read_bytes() == b"rotated publisher credential\n"
    assert not list(repo.glob(".cmru.secret.toml.mask-*"))
    assert not list(repo.glob(".cmru.secret.toml.mask-displaced-*"))


def test_release_gate_retains_private_backups_when_secret_restoration_fails(
    tmp_path, monkeypatch,
):
    repo, root_secret, project_secret, _metadata, _auth, calls = _fixture(tmp_path, monkeypatch)
    private_root = tmp_path / "private-backups"
    private_root.mkdir(mode=0o700)

    def fail_restore(backups):
        root_secret_backup = next(item for item in backups if item.path == root_secret)
        root_secret_backup.path.unlink()
        raise OSError("simulated restore failure")

    monkeypatch.setattr(run_release_gate, "_restore", fail_restore)
    monkeypatch.setattr(
        run_release_gate,
        "_invoke_lane",
        lambda _root, lane, _environment: calls.append(lane) or 0,
    )

    try:
        run_release_gate.run_release_gate(repo, temp_parent=private_root)
    except RuntimeError as exc:
        assert "simulated restore failure" in str(exc)
        assert "private publisher-secret backups retained at" in str(exc)
        backup_root = Path(str(exc).rsplit("retained at ", 1)[1])
    else:
        raise AssertionError("failed secret restoration did not fail the gate")

    assert not root_secret.exists()
    assert project_secret.is_symlink()
    assert calls == ["installed-wheel", "assay", "coverage", "mutation", "canary", "enroll"]
    assert backup_root.is_dir()
    assert stat.S_IMODE(backup_root.stat().st_mode) == 0o700
    backup_files = list(backup_root.glob("overlay-*.bin"))
    assert len(backup_files) == 2
    assert {path.read_bytes() for path in backup_files} == {
        b"root credential\n", b"project override\n",
    }


def test_release_gate_refuses_symlink_secret_and_restores_any_prior_mask(
    tmp_path, monkeypatch,
):
    repo, root_secret, project_secret, _metadata, _auth, calls = _fixture(tmp_path, monkeypatch)
    private_root = tmp_path / "private-backups"
    private_root.mkdir(mode=0o700)
    target = tmp_path / "target.secret"
    target.write_text("preserve\n", encoding="utf-8")
    project_secret.unlink()
    project_secret.symlink_to(target)

    monkeypatch.setattr(
        run_release_gate,
        "_invoke_lane",
        lambda *_args: calls.append("unexpected") or 0,
    )
    try:
        run_release_gate.run_release_gate(repo, temp_parent=private_root)
    except RuntimeError as exc:
        assert "regular file" in str(exc)
    else:
        raise AssertionError("symlinked secret was accepted")

    assert root_secret.read_bytes() == b"root credential\n"
    assert project_secret.is_symlink()
    assert target.read_text(encoding="utf-8") == "preserve\n"
    assert calls == []


def _fake_lane_run(project: Path, writer, returncode: int):
    def run(argv, **_kwargs):
        writer(project)
        return SimpleNamespace(returncode=returncode)

    return run


def test_failed_lane_names_the_failing_test_from_the_verdict_tail(tmp_path, monkeypatch, capsys):
    project = tmp_path / "cmru"
    (project / ".assay").mkdir(parents=True)
    tail = "....F\nFAILED tests/test_x.py::test_boom - assert 1 == 2\nERROR tests/test_y.py::test_err\n1 failed"

    def write(_project):
        import json as _json

        (_project / ".assay" / "verdict-cmru.json").write_text(
            _json.dumps({"result_stdout_tail": tail}), encoding="utf-8",
        )

    monkeypatch.setattr(run_release_gate.subprocess, "run", _fake_lane_run(project, write, 1))
    assert run_release_gate._invoke_lane(tmp_path, "assay", {}) == 1
    err = capsys.readouterr().err
    assert "FAILED tests/test_x.py::test_boom - assert 1 == 2" in err
    assert "ERROR tests/test_y.py::test_err" in err
    assert "1 failed" not in err


def test_failed_lane_names_failures_from_junit_and_ignores_stale_or_bad_files(
    tmp_path, monkeypatch, capsys,
):
    project = tmp_path / "cmru"
    (project / ".assay").mkdir(parents=True)
    stale = project / ".assay" / "verdict-old.json"
    stale.write_text('{"result_stdout_tail": "FAILED tests/stale.py::t"}', encoding="utf-8")
    os.utime(stale, ns=(1, 1))
    (project / ".assay" / "verdict-bad.json").write_text("{not json", encoding="utf-8")
    (project / ".assay" / "verdict-notail.json").write_text("{}", encoding="utf-8")

    def write(_project):
        (_project / ".assay" / "verdict-bad.json").write_text("{not json", encoding="utf-8")
        (_project / "junit-coverage.xml").write_text(
            '<testsuites><testsuite><testcase classname="tests.test_a" name="test_ok"/>'
            '<testcase classname="tests.test_a" name="test_bad"><failure/></testcase>'
            "</testsuite></testsuites>",
            encoding="utf-8",
        )

    monkeypatch.setattr(run_release_gate.subprocess, "run", _fake_lane_run(project, write, 3))
    assert run_release_gate._invoke_lane(tmp_path, "coverage", {}) == 3
    err = capsys.readouterr().err
    assert "FAILED tests.test_a::test_bad (junit)" in err
    assert "test_ok" not in err
    assert "stale.py" not in err


def test_passing_lane_and_unreadable_reports_stay_silent(tmp_path, monkeypatch, capsys):
    project = tmp_path / "cmru"
    project.mkdir()

    def write(_project):
        (_project / "junit-coverage.xml").write_text("<broken", encoding="utf-8")

    monkeypatch.setattr(run_release_gate.subprocess, "run", _fake_lane_run(project, write, 0))
    assert run_release_gate._invoke_lane(tmp_path, "coverage", {}) == 0
    monkeypatch.setattr(run_release_gate.subprocess, "run", _fake_lane_run(project, write, 2))
    assert run_release_gate._invoke_lane(tmp_path, "coverage", {}) == 2
    assert capsys.readouterr().err == ""
