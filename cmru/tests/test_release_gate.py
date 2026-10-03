from __future__ import annotations

import importlib.util
import os
import stat
import sys
from pathlib import Path
from types import SimpleNamespace


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


def test_release_gate_retains_private_backups_when_secret_restoration_fails(
    tmp_path, monkeypatch,
):
    repo, root_secret, project_secret, _metadata, _auth, calls = _fixture(tmp_path, monkeypatch)
    private_root = tmp_path / "private-backups"
    private_root.mkdir(mode=0o700)

    def fail_restore(backups):
        backups[0].path.unlink()
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
    assert (backup_root / "overlay-0.bin").read_bytes() == b"root credential\n"
    assert (backup_root / "overlay-1.bin").read_bytes() == b"project override\n"


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
