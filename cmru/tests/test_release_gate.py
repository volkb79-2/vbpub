from __future__ import annotations

import argparse
import hashlib
import importlib.util
import io
import json
import os
import shutil
import signal
import stat
import subprocess
import sys
import tomllib
import zipfile
from datetime import datetime
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
    def __init__(self, repo: Path, common: Path):
        self.repo = repo
        self.common = common

    def repo_top(self, project: Path) -> Path:
        return self.repo

    def run(self, repo: Path, *args: str) -> str:
        assert repo == self.repo
        assert args == ("rev-parse", "--path-format=absolute", "--git-common-dir")
        return str(self.common)


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=root, capture_output=True, text=True, check=True,
    )
    return result.stdout.strip()


LANES = [
    "ref-probe", "installed-wheel-live", "assay", "coverage", "mutation", "canary",
]


def _sample_run_gate_wheel() -> bytes:
    """Deterministic wheel bytes.

    ``ZipFile.writestr(name, ...)`` stamps the current time into each entry, so
    the archive (and its SHA-256) changed whenever a second boundary passed
    between computing the expected identity and rechecking it mid-gate. Fixed
    entry timestamps make every call return the same bytes.
    """
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        for name, content in (
            ("run_gate.py", b"wheel run gate module\n"),
            ("run_gate_admission.py", b"wheel admission module\n"),
        ):
            archive.writestr(zipfile.ZipInfo(name, date_time=(2026, 10, 15, 0, 0, 0)), content)
    return output.getvalue()


def _sample_payload_sha256() -> str:
    digest = hashlib.sha256()
    for name, content in (
        ("run_gate.py", b"wheel run gate module\n"),
        ("run_gate_admission.py", b"wheel admission module\n"),
    ):
        digest.update(name.encode("utf-8", "surrogateescape"))
        digest.update(b"\0")
        digest.update(content)
    return digest.hexdigest()


def _sample_wheel_sha256() -> str:
    return hashlib.sha256(_sample_run_gate_wheel()).hexdigest()


def _base_run_gate_identity(version: str = "24.0.0") -> dict[str, str]:
    return {
        "version": version,
        "executable": "/usr/bin/run-gate",
        "launcher_sha256": "a" * 64,
        "distribution_sha256": "b" * 64,
        "installed_wheel_sha256": _sample_wheel_sha256(),
        "installed_payload_sha256": _sample_payload_sha256(),
    }


def _tester_image_identity() -> dict[str, str]:
    return {
        "tester_image": "tester-unified:local",
        "tester_image_id": "sha256:" + "d" * 64,
        "tester_image_wheel_sha256": _sample_wheel_sha256(),
    }


def _run_gate_release_artifacts() -> dict[str, str]:
    return {
        "published_wheel_sha256": _sample_wheel_sha256(),
        "published_payload_sha256": _sample_payload_sha256(),
        **_tester_image_identity(),
    }


def _gate_identity(version: str = "24.0.0") -> dict[str, str]:
    return {**_base_run_gate_identity(version), **_run_gate_release_artifacts()}


def _matching_release_identity(wheel_sha: str) -> dict[str, str]:
    identity = _base_run_gate_identity()
    identity["installed_wheel_sha256"] = wheel_sha
    return identity


def _probe_ref(worktree: Path) -> str:
    branch = _git(worktree, "symbolic-ref", "--short", "HEAD")
    return f"refs/cmru-gate/{branch.removeprefix('cmru-gate-')}/ref-probe"


def _probing(inner):
    """Wrap a fake lane runner so the ref-probe lane really writes its ref."""
    def invoke(root, lane, environment, worktree):
        if lane == "ref-probe":
            _git(worktree, "update-ref", _probe_ref(worktree), "HEAD")
        return inner(root, lane, environment, worktree)
    return invoke


def _fixture(tmp_path: Path, monkeypatch):
    primary = tmp_path / "primary"
    primary.mkdir()
    _git(primary, "init", "--quiet", "--initial-branch=main")
    _git(primary, "config", "user.name", "CMRU Gate Test")
    _git(primary, "config", "user.email", "cmru-gate@example.invalid")
    (primary / "cmru").mkdir()
    (primary / "README.md").write_text("tracked fixture\n", encoding="utf-8")
    (primary / "cmru" / "cmru.toml").write_text("schema_version = 1\n", encoding="utf-8")
    (primary / "cmru" / "CHANGES.md").write_text(
        "# Changelog\n\n## [6.2.0] - 2026-10-14\n\n- prepared release\n",
        encoding="utf-8",
    )
    _git(primary, "add", "README.md", "cmru/cmru.toml", "cmru/CHANGES.md")
    _git(primary, "commit", "--quiet", "-m", "fixture")
    repo = tmp_path / "repository"
    _git(primary, "worktree", "add", "--quiet", "-b", "gate-source", str(repo), "HEAD")
    project = repo / "cmru"
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
    git = _AssayGit(repo, (primary / ".git").resolve())
    calls = []

    def load_components():
        def published_release_state(repo_root, *, git_auth):
            assert repo_root == repo
            assert git_auth is auth
            return "cmru-v6.1.0", {"cmru-v6.1.0": "1" * 40}

        def build_facts(
            repo_root, project_root, *, git_auth, assay_git, remote_tag_commits,
        ):
            count = int(os.environ.get("GIT_CONFIG_COUNT", "0"))
            assert any(
                os.environ.get(f"GIT_CONFIG_KEY_{index}") == "core.hooksPath"
                and os.environ.get(f"GIT_CONFIG_VALUE_{index}") == os.devnull
                for index in range(count)
            )
            assert repo_root == repo
            assert project_root == project
            assert git_auth is auth
            assert assay_git is git
            assert remote_tag_commits == {"cmru-v6.1.0": "1" * 40}
            assert root_secret.is_file() and not root_secret.is_symlink()
            assert project_secret.is_file() and not project_secret.is_symlink()
            return {"head": "abc", "selected_tag": "cmru-v1.0.0"}

        baseline = SimpleNamespace(
            _repository_git_auth=lambda root: auth,
            published_release_state=published_release_state,
            build_facts=build_facts,
        )
        return git, baseline

    monkeypatch.setattr(run_release_gate, "PROJECT_ROOT", project)
    monkeypatch.setattr(run_release_gate, "_load_components", load_components)
    monkeypatch.setattr(run_release_gate, "_preflight_assay_source", lambda _root: None)
    monkeypatch.setattr(
        run_release_gate, "_check_run_gate_identity",
        lambda _root: _base_run_gate_identity(),
    )
    monkeypatch.setattr(
        run_release_gate, "_check_run_gate_release_artifacts",
        lambda _root, _identity: _run_gate_release_artifacts(),
    )
    monkeypatch.setattr(
        run_release_gate, "_check_tester_image_identity",
        lambda _root, **_kwargs: _tester_image_identity(),
    )
    return repo, root_secret, project_secret, metadata, auth, calls


def test_assay_source_preflight_runs_before_loading_release_components_and_lanes(
    tmp_path, monkeypatch,
):
    repo, *_rest, _calls = _fixture(tmp_path, monkeypatch)
    events = []
    prior_cache_prefix = sys.pycache_prefix
    prior_cache_env = os.environ.get("PYTHONPYCACHEPREFIX")
    load_components = run_release_gate._load_components

    def preflight(root):
        assert root == repo
        assert sys.pycache_prefix
        assert not Path(sys.pycache_prefix).is_relative_to(repo)
        assert os.environ.get("PYTHONPYCACHEPREFIX") == sys.pycache_prefix
        events.append("assay-source-preflight")

    def load():
        events.append("load-components")
        return load_components()

    def invoke(_root, lane, _environment, _worktree):
        events.append(lane)
        return 0

    monkeypatch.setattr(run_release_gate, "_preflight_assay_source", preflight)
    monkeypatch.setattr(run_release_gate, "_load_components", load)
    monkeypatch.setattr(run_release_gate, "_invoke_lane", _probing(invoke))
    private_root = tmp_path / "private-backups"
    private_root.mkdir(mode=0o700)

    assert run_release_gate.run_release_gate(repo, temp_parent=private_root) == 0
    assert events == ["assay-source-preflight", "load-components", *LANES]
    assert sys.pycache_prefix == prior_cache_prefix
    assert os.environ.get("PYTHONPYCACHEPREFIX") == prior_cache_env


def test_first_release_uses_whole_source_lane_and_records_actionable_r2_follow_up(
    tmp_path, monkeypatch, capsys,
):
    repo, private_root, calls = _recording_gate(tmp_path, monkeypatch)
    (repo / "cmru" / "CHANGES.md").write_text(
        "# Changelog\n\n## [0.1.0] - 2026-10-15\n\n- first release\n",
        encoding="utf-8",
    )
    _git(repo, "add", "cmru/CHANGES.md")
    _git(repo, "commit", "--quiet", "-m", "prepare first CMRU release")
    monkeypatch.setenv("CMRU_RELEASE_GATE_COMMIT", _git(repo, "rev-parse", "HEAD"))
    original_load = run_release_gate._load_components

    def load_without_published_stable_tag():
        git, baseline = original_load()
        baseline.published_release_state = lambda _root, *, git_auth: (None, {})
        return git, baseline

    monkeypatch.setattr(run_release_gate, "_load_components", load_without_published_stable_tag)
    assert run_release_gate.run_release_gate(
        repo, temp_parent=private_root, postpone_mutation="KI-79",
    ) == 0
    assert calls == [
        "ref-probe", "installed-wheel-live", "assay-first-release", "coverage", "canary",
    ]
    marker = repo / "cmru" / ".assay" / "mutation-postponed-cmru.json"
    record = json.loads(marker.read_text(encoding="utf-8"))
    assert record["schema_version"] == 2
    assert record["tracking_id"] == "FIRST-RELEASE-R2"
    assert record["target_version"] == "0.1.0"
    assert record["baseline_state"] == "no-published-stable-release"
    assert record["follow_up_lane"] == "assay-first-release-r2"
    assert "whole-source R2" in record["reason"]
    assert "FIRST-RELEASE-R2" in capsys.readouterr().err


def test_first_release_refuses_without_protected_candidate_commit_before_any_lane(
    tmp_path, monkeypatch,
):
    repo, private_root, calls = _recording_gate(tmp_path, monkeypatch)
    (repo / "cmru" / "CHANGES.md").write_text(
        "# Changelog\n\n## [0.1.0] - 2026-10-15\n\n- first release\n",
        encoding="utf-8",
    )
    _git(repo, "add", "cmru/CHANGES.md")
    _git(repo, "commit", "--quiet", "-m", "prepare first CMRU release")
    original_load = run_release_gate._load_components

    def load_without_published_stable_tag():
        git, baseline = original_load()
        baseline.published_release_state = lambda _root, *, git_auth: (None, {})
        return git, baseline

    monkeypatch.setattr(run_release_gate, "_load_components", load_without_published_stable_tag)
    monkeypatch.delenv("CMRU_RELEASE_GATE_COMMIT", raising=False)
    with pytest.raises(RuntimeError, match="only inside CMRU's protected release transaction"):
        run_release_gate.run_release_gate(
            repo, temp_parent=private_root, postpone_mutation="KI-79",
        )
    assert calls == []


def test_first_release_refuses_when_origin_tag_query_fails_instead_of_claiming_empty(
    tmp_path, monkeypatch,
):
    repo, private_root, calls = _recording_gate(tmp_path, monkeypatch)
    original_load = run_release_gate._load_components

    def load_with_unavailable_origin():
        git, baseline = original_load()

        def fail(_root, *, git_auth):
            raise RuntimeError("cannot list CMRU release tags on origin")

        baseline.published_release_state = fail
        return git, baseline

    monkeypatch.setattr(run_release_gate, "_load_components", load_with_unavailable_origin)
    with pytest.raises(RuntimeError, match="cannot list CMRU release tags on origin"):
        run_release_gate.run_release_gate(
            repo, temp_parent=private_root, postpone_mutation="KI-79",
        )
    assert calls == []


@pytest.mark.parametrize("ignored", [False, True])
def test_untracked_caller_gate_helper_refuses_before_preflight_or_import(
    tmp_path: Path, monkeypatch, ignored: bool,
):
    repo, *_rest = _fixture(tmp_path, monkeypatch)
    helper = repo / "cmru" / "src" / "cmru" / "gate_attack.py"
    helper.parent.mkdir(parents=True)
    marker = tmp_path / "untracked-gate-helper-ran"
    helper.write_text(
        "from pathlib import Path\n"
        f"Path({str(marker)!r}).write_text('executed')\n",
        encoding="utf-8",
    )
    if ignored:
        (repo / ".gitignore").write_text(
            "cmru/src/cmru/gate_attack.py\n", encoding="utf-8",
        )
        _git(repo, "add", ".gitignore")
        _git(repo, "commit", "--quiet", "-m", "ignore a gate helper")

    events = []
    monkeypatch.setattr(
        run_release_gate, "_preflight_assay_source",
        lambda _root: events.append("preflight"),
    )

    def load_components():
        events.append("load-components")
        spec = importlib.util.spec_from_file_location("cmru.gate_attack", helper)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return None, None

    monkeypatch.setattr(run_release_gate, "_load_components", load_components)
    monkeypatch.setattr(
        run_release_gate, "_invoke_lane",
        lambda *_args: events.append("lane") or 0,
    )
    private_root = tmp_path / "private-backups"
    private_root.mkdir(mode=0o700)

    with pytest.raises(RuntimeError, match="untracked or ignored executable source"):
        run_release_gate.run_release_gate(repo, temp_parent=private_root)

    assert events == []
    assert not marker.exists()


def test_release_lanes_use_exact_snapshot_without_touching_source_secrets(
    tmp_path, monkeypatch,
):
    repo, root_secret, project_secret, metadata, _auth, calls = _fixture(tmp_path, monkeypatch)
    venv_secret = repo / ".venv" / "cmru.secret.toml"
    venv_secret.parent.mkdir()
    venv_secret.write_bytes(b"virtualenv publisher credential\n")
    venv_metadata = (
        stat.S_IMODE(venv_secret.stat().st_mode),
        venv_secret.stat().st_mtime_ns,
        venv_secret.stat().st_uid,
        venv_secret.stat().st_gid,
    )
    sibling_secret = tmp_path / "sibling-worktree" / "cmru.secret.toml"
    sibling_secret.parent.mkdir()
    sibling_secret.write_bytes(b"unrelated worktree credential\n")
    private_root = tmp_path / "private-backups"
    private_root.mkdir(mode=0o700)
    monkeypatch.setenv("GITHUB_PUSH_PAT", "ambient-push-token")
    monkeypatch.setenv("GITHUB_TOKEN", "ambient-api-token")
    monkeypatch.setenv("CMRU_GIT_AUTH_TOKEN", "ambient-git-token")
    monkeypatch.setenv("RUN_GATE_EXTRA_MOUNTS", "/tmp/private=private")
    monkeypatch.setenv("CMRU_ASSAY_BASELINE_FACTS", "stale-facts")

    def invoke(root, lane, environment, worktree):
        assert root == repo
        assert root_secret.is_file() and not root_secret.is_symlink()
        assert project_secret.is_file() and not project_secret.is_symlink()
        assert not (worktree / "cmru.secret.toml").exists()
        assert not (worktree / "cmru" / "cmru.secret.toml").exists()
        assert not (worktree / ".venv" / "cmru.secret.toml").exists()
        if lane == "installed-wheel-live":
            manifest = json.loads((
                worktree / "cmru" / ".assay" / "release-gate-snapshot.json"
            ).read_text(encoding="utf-8"))
            host_canary = Path(manifest["source_root"]) / manifest["source_canary"]
            assert manifest["state"] == "running"
            assert Path(manifest["snapshot_path"]).name == worktree.name
            assert manifest["snapshot_branch"] == worktree.name
            assert manifest["refs_scope"] == "protected-refs/1"
            assert manifest["refreshed_at_utc"].endswith("Z")
            assert host_canary.is_file()
            assert not (worktree / manifest["source_canary"]).exists()
        assert sibling_secret.is_file() and not sibling_secret.is_symlink()
        assert sibling_secret.read_bytes() == b"unrelated worktree credential\n"
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

    monkeypatch.setattr(run_release_gate, "_invoke_lane", _probing(invoke))
    assert run_release_gate.run_release_gate(repo, temp_parent=private_root) == 0
    assert calls == LANES
    assert root_secret.read_bytes() == b"root credential\n"
    assert project_secret.read_bytes() == b"project override\n"
    assert venv_secret.read_bytes() == b"virtualenv publisher credential\n"
    assert (
        stat.S_IMODE(venv_secret.stat().st_mode),
        venv_secret.stat().st_mtime_ns,
        venv_secret.stat().st_uid,
        venv_secret.stat().st_gid,
    ) == venv_metadata
    assert sibling_secret.read_bytes() == b"unrelated worktree credential\n"
    assert not list(repo.glob(".cmru-gate-canary-*.txt"))
    assert {
        path: (
            stat.S_IMODE(path.stat().st_mode),
            path.stat().st_mtime_ns,
            path.stat().st_uid,
            path.stat().st_gid,
        )
        for path in (root_secret, project_secret)
    } == metadata


def test_release_gate_failure_preserves_source_secret_files_and_metadata(tmp_path, monkeypatch):
    repo, root_secret, project_secret, _metadata, _auth, calls = _fixture(tmp_path, monkeypatch)
    private_root = tmp_path / "private-backups"
    private_root.mkdir(mode=0o700)

    def invoke(_root, lane, _environment, _worktree):
        calls.append(lane)
        return 19 if lane == "assay" else 0

    monkeypatch.setattr(run_release_gate, "_invoke_lane", _probing(invoke))
    assert run_release_gate.run_release_gate(repo, temp_parent=private_root) == 19
    assert calls == ["ref-probe", "installed-wheel-live", "assay"]
    assert root_secret.read_bytes() == b"root credential\n"
    assert project_secret.read_bytes() == b"project override\n"


def test_snapshot_is_attached_to_exact_head_and_excludes_source_secrets(tmp_path, monkeypatch):
    repo, root_secret, project_secret, _metadata, _auth, _calls = _fixture(tmp_path, monkeypatch)
    from cmru import gate_snapshot

    snapshot = gate_snapshot.open_gate_snapshot(repo)
    try:
        assert snapshot.worktree_path != repo
        assert snapshot.worktree_path.is_dir()
        assert not (snapshot.worktree_path / "cmru.secret.toml").exists()
        assert not (snapshot.worktree_path / "cmru" / "cmru.secret.toml").exists()
        assert root_secret.read_bytes() == b"root credential\n"
        assert project_secret.read_bytes() == b"project override\n"
        assert _git(snapshot.worktree_path, "rev-parse", "HEAD") == snapshot.commit
        assert _git(snapshot.worktree_path, "branch", "--show-current") == snapshot.branch
        (repo / ".venv" / "late" / "cmru.secret.toml").parent.mkdir(parents=True)
        (repo / ".venv" / "late" / "cmru.secret.toml").write_text("late secret")
        assert not (snapshot.worktree_path / ".venv" / "late" / "cmru.secret.toml").exists()
    finally:
        gate_snapshot.close_gate_snapshot(snapshot)


def test_git_pointer_reads_refuse_symlinks_and_fifos_without_blocking(tmp_path, monkeypatch):
    repo, *_rest = _fixture(tmp_path, monkeypatch)
    from cmru import gate_snapshot

    marker = repo / ".git"
    admin, _common = gate_snapshot._linked_git_context(repo)
    marker.unlink()
    os.mkfifo(marker)
    with pytest.raises(RuntimeError, match="marker is not a regular file"):
        gate_snapshot._linked_git_context(repo)
    marker.unlink()
    (repo / ".git").write_text(f"gitdir: {admin}\n", encoding="utf-8")
    (admin / "gitdir").unlink()
    (admin / "gitdir").symlink_to(repo / ".git")
    with pytest.raises(RuntimeError, match="cannot read linked-worktree pointer"):
        gate_snapshot._linked_git_context(repo)


def test_snapshot_copies_back_only_allowlisted_gate_evidence(tmp_path, monkeypatch):
    repo, *_rest, calls = _fixture(tmp_path, monkeypatch)
    private_root = tmp_path / "private-backups"
    private_root.mkdir(mode=0o700)

    def invoke(_root, lane, _environment, worktree):
        calls.append(lane)
        if lane == "assay":
            evidence = worktree / "cmru" / ".assay" / "verdict-cmru.json"
            evidence.parent.mkdir(parents=True, exist_ok=True)
            evidence.write_text('{"status":"PASS"}\n', encoding="utf-8")
        return 0

    monkeypatch.setattr(run_release_gate, "_invoke_lane", _probing(invoke))
    assert run_release_gate.run_release_gate(repo, temp_parent=private_root) == 0
    assert calls == LANES
    assert (repo / "cmru" / ".assay" / "verdict-cmru.json").read_text() == (
        '{"status":"PASS"}\n'
    )
    assert (repo / "cmru" / ".assay" / "release-gate-snapshot.json").is_file()


def test_unapproved_lane_output_fails_and_is_not_copied_back(tmp_path, monkeypatch):
    repo, *_rest, calls = _fixture(tmp_path, monkeypatch)
    private_root = tmp_path / "private-backups"
    private_root.mkdir(mode=0o700)

    def invoke(_root, lane, _environment, worktree):
        calls.append(lane)
        if lane == "installed-wheel-live":
            evidence = worktree / "cmru" / ".assay" / "verdict-cmru.json"
            evidence.parent.mkdir(parents=True, exist_ok=True)
            evidence.write_text('{"status":"PASS"}\n', encoding="utf-8")
            (worktree / "cmru" / "unapproved-output.txt").write_text("not evidence")
        return 0

    monkeypatch.setattr(run_release_gate, "_invoke_lane", _probing(invoke))
    with pytest.raises(RuntimeError, match="unapproved ignored/untracked files"):
        run_release_gate.run_release_gate(repo, temp_parent=private_root)
    assert not (repo / "cmru" / "unapproved-output.txt").exists()
    assert not (repo / "cmru" / ".assay" / "verdict-cmru.json").exists()


def test_tracked_publisher_secret_refuses_before_any_lane(tmp_path, monkeypatch):
    repo, *_rest, calls = _fixture(tmp_path, monkeypatch)
    tracked_secret = repo / "cmru" / "cmru.secret.toml"
    tracked_secret.write_text("must not enter a snapshot")
    _git(repo, "add", "cmru/cmru.secret.toml")
    _git(repo, "commit", "--quiet", "-m", "track forbidden secret")
    private_root = tmp_path / "private-backups"
    private_root.mkdir(mode=0o700)
    monkeypatch.setattr(
        run_release_gate, "_invoke_lane",
        lambda *_args: calls.append("launched") or 0,
    )
    with pytest.raises(RuntimeError, match="tracks a publisher-secret file"):
        run_release_gate.run_release_gate(repo, temp_parent=private_root)
    assert calls == []


def test_tracked_gate_evidence_path_refuses_before_any_lane(tmp_path, monkeypatch):
    repo, *_rest, calls = _fixture(tmp_path, monkeypatch)
    tracked_evidence = repo / "cmru" / "coverage.json"
    tracked_evidence.write_text("committed evidence is not lane state\n")
    _git(repo, "add", "cmru/coverage.json")
    _git(repo, "commit", "--quiet", "-m", "track gate evidence path")
    private_root = tmp_path / "private-backups"
    private_root.mkdir(mode=0o700)
    monkeypatch.setattr(
        run_release_gate, "_invoke_lane",
        lambda *_args: calls.append("launched") or 0,
    )
    with pytest.raises(RuntimeError, match="evidence paths must be ignored"):
        run_release_gate.run_release_gate(repo, temp_parent=private_root)
    assert calls == []


def test_release_candidate_cleanup_removes_its_gate_snapshot(tmp_path):
    import worktree

    from cmru import gate_snapshot, transaction

    primary = tmp_path / "primary"
    primary.mkdir()
    _git(primary, "init", "--quiet", "--initial-branch=main")
    _git(primary, "config", "user.name", "CMRU Gate Test")
    _git(primary, "config", "user.email", "cmru-gate@example.invalid")
    (primary / "README.md").write_text("tracked fixture\n", encoding="utf-8")
    _git(primary, "add", "README.md")
    _git(primary, "commit", "--quiet", "-m", "fixture")

    candidate_path = tmp_path / "release-candidate"
    context = worktree.create_workspace(
        primary,
        candidate_path,
        branch="cmru-test-release-candidate",
        purpose="cmru-release",
        base="HEAD",
    )
    _git(candidate_path, "reset", "--hard", "HEAD")
    snapshot = gate_snapshot.open_gate_snapshot(candidate_path)
    snapshot_path = snapshot.worktree_path
    gate_snapshot.close_gate_snapshot(snapshot)

    transaction.remove_workspace(transaction.ReleaseWorkspace(
        repo_root=primary,
        path=candidate_path,
        branch=context.branch,
        base=context.base_commit,
        context=context,
    ))

    assert not snapshot_path.exists()
    assert not candidate_path.exists()
    assert not worktree.list_workspaces(worktree.discover_git_context(primary)[1])


_STARTED_NS = 1_000_000
_FRESH_NS = 2_000_000


def _fake_lane_process(project: Path, writer, returncode: int, monkeypatch):
    """Fake the lane process with a deterministic clock.

    ``_invoke_lane`` stamps ``time.time_ns()`` before running; the clock is pinned
    to ``_STARTED_NS`` and every file the fake writes is stamped ``_FRESH_NS`` so
    freshness never depends on real filesystem timestamp granularity.
    """
    monkeypatch.setattr(run_release_gate, "time", SimpleNamespace(time_ns=lambda: _STARTED_NS))

    class FakeProcess:
        def wait(self, timeout=None):
            assert timeout is None
            return returncode

        def poll(self):
            return returncode

        def send_signal(self, _signum):
            raise AssertionError("unexpected signal in successful lane fixture")

        def kill(self):
            raise AssertionError("unexpected kill in successful lane fixture")

    def popen(argv, **_kwargs):
        assert argv[0] == "run-gate"
        assert argv[1:3] == ["--worktree", str(project.parent)]
        assert argv[-1] in {"assay", "coverage"}
        before = {p for p in project.rglob("*") if p.is_file()}
        writer(project)
        for written in (p for p in project.rglob("*") if p.is_file()):
            if written not in before:
                os.utime(written, ns=(_FRESH_NS, _FRESH_NS))
        return FakeProcess()

    monkeypatch.setattr(run_release_gate.subprocess, "Popen", popen)


def _stale(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")
    os.utime(path, ns=(1, 1))


def test_failed_lane_names_the_failing_test_from_the_verdict_tail(tmp_path, monkeypatch, capsys):
    tail = (
        "....F\nFAILED tests/test_x.py::test_boom - assert 1 == 2\n"
        "ERROR tests/test_y.py::test_err\nFAILED tests/test_x.py::test_boom - assert 1 == 2\n1 failed"
    )

    def write(_project):
        import json as _json

        (_project / ".assay" / "verdict-cmru.json").write_text(
            _json.dumps({"result_stdout_tail": tail}), encoding="utf-8",
        )

    repo = tmp_path / "source"
    (repo / "cmru").mkdir(parents=True)
    snapshot = tmp_path / "snapshot"
    snapshot_project = snapshot / "cmru"
    (snapshot_project / ".assay").mkdir(parents=True)
    _fake_lane_process(snapshot_project, write, 1, monkeypatch)
    assert run_release_gate._invoke_lane(repo, "assay", {}, snapshot) == 1
    err = capsys.readouterr().err
    assert err.count("FAILED tests/test_x.py::test_boom - assert 1 == 2") == 1  # deduplicated
    assert "ERROR tests/test_y.py::test_err" in err
    assert "1 failed" not in err


def test_failed_lane_names_failures_from_junit_and_ignores_stale_or_bad_files(
    tmp_path, monkeypatch, capsys,
):
    def write(_project):
        (_project / ".assay" / "verdict-fresh-bad.json").write_text("{not json", encoding="utf-8")
        (_project / "junit-coverage.xml").write_text(
            '<testsuites><testsuite><testcase classname="tests.test_a" name="test_ok"/>'
            '<testcase classname="tests.test_a" name="test_bad"><failure/></testcase>'
            '<testcase classname="tests.test_a" name="test_err"><error/></testcase>'
            "</testsuite></testsuites>",
            encoding="utf-8",
        )

    repo = tmp_path / "source"
    (repo / "cmru").mkdir(parents=True)
    snapshot = tmp_path / "snapshot"
    snapshot_project = snapshot / "cmru"
    (snapshot_project / ".assay").mkdir(parents=True)
    _stale(snapshot_project / ".assay" / "verdict-old.json",
           '{"result_stdout_tail": "FAILED tests/stale.py::t"}')
    _stale(snapshot_project / ".assay" / "verdict-bad.json", "{not json")
    _stale(snapshot_project / ".assay" / "verdict-notail.json", "{}")
    _fake_lane_process(snapshot_project, write, 3, monkeypatch)
    assert run_release_gate._invoke_lane(repo, "coverage", {}, snapshot) == 3
    err = capsys.readouterr().err
    assert "FAILED tests.test_a::test_bad (junit)" in err
    assert "FAILED tests.test_a::test_err (junit)" in err
    assert "test_ok" not in err
    assert "stale.py" not in err


def test_stale_junit_from_an_earlier_run_is_not_reported(tmp_path, monkeypatch, capsys):
    repo = tmp_path / "source"
    (repo / "cmru").mkdir(parents=True)
    snapshot = tmp_path / "snapshot"
    snapshot_project = snapshot / "cmru"
    snapshot_project.mkdir(parents=True)
    _stale(
        snapshot_project / "junit-coverage.xml",
        '<testsuites><testcase classname="tests.old" name="test_old"><failure/></testcase></testsuites>',
    )
    _fake_lane_process(snapshot_project, lambda _p: None, 1, monkeypatch)
    assert run_release_gate._invoke_lane(repo, "coverage", {}, snapshot) == 1
    assert capsys.readouterr().err == ""


def test_passing_lane_and_unreadable_reports_stay_silent(tmp_path, monkeypatch, capsys):
    def write(_project):
        (_project / "junit-coverage.xml").write_text("<broken", encoding="utf-8")

    repo = tmp_path / "source"
    (repo / "cmru").mkdir(parents=True)
    snapshot = tmp_path / "snapshot"
    snapshot_project = snapshot / "cmru"
    snapshot_project.mkdir(parents=True)
    _fake_lane_process(snapshot_project, write, 0, monkeypatch)
    assert run_release_gate._invoke_lane(repo, "coverage", {}, snapshot) == 0
    _fake_lane_process(snapshot_project, write, 2, monkeypatch)
    assert run_release_gate._invoke_lane(repo, "coverage", {}, snapshot) == 2
    assert capsys.readouterr().err == ""


# --- --postpone-mutation (PROVISIONAL gate, KI-62) ---------------------------


def _recording_gate(tmp_path, monkeypatch):
    """A fixture whose lanes and remote-facts builder are recorded, never run."""
    repo, *_rest, calls = _fixture(tmp_path, monkeypatch)
    load = run_release_gate._load_components

    def load_recording():
        git, baseline = load()
        original = baseline.build_facts

        def build_facts(*args, **kwargs):
            calls.append("facts")
            return original(*args, **kwargs)

        baseline.build_facts = build_facts
        return git, baseline

    monkeypatch.setattr(run_release_gate, "_load_components", load_recording)

    def invoke(_root, lane, _environment, _worktree):
        calls.append(lane)
        return 0

    monkeypatch.setattr(run_release_gate, "_invoke_lane", _probing(invoke))
    private_root = tmp_path / "private-backups"
    private_root.mkdir(mode=0o700)
    return repo, private_root, calls


def test_postponed_mutation_skips_only_the_mutation_lane_and_records_the_marker(
    tmp_path, monkeypatch, capsys,
):
    repo, private_root, calls = _recording_gate(tmp_path, monkeypatch)
    status = run_release_gate.run_release_gate(
        repo, temp_parent=private_root, postpone_mutation="KI-79",
    )
    assert status == 0
    # Every other lane runs exactly as before, in order; no mutation lane and
    # no remote-facts preparation (which only the mutation lane consumes).
    assert calls == ["ref-probe", "installed-wheel-live", "assay", "coverage", "canary"]
    marker = repo / "cmru" / ".assay" / "mutation-postponed-cmru.json"
    record = json.loads(marker.read_text(encoding="utf-8"))
    assert record["tracking_id"] == "KI-79"
    assert record["candidate_commit"] == _git(repo, "rev-parse", "HEAD")
    assert record["target_version"] == "6.2.0"
    assert record["lane"] == "mutation"
    assert record["status"] == "postponed"
    assert record["reason"] == run_release_gate.POSTPONE_REASON
    assert datetime.fromisoformat(record["timestamp"]).tzinfo is not None
    err = capsys.readouterr().err
    warn_lines = [line for line in err.splitlines() if "WARN" in line]
    assert len(warn_lines) == 1
    assert "POSTPONED" in warn_lines[0] and "KI-79" in warn_lines[0]


def test_postponement_is_refused_for_a_later_release_before_any_lane_runs(
    tmp_path, monkeypatch,
):
    repo, private_root, calls = _recording_gate(tmp_path, monkeypatch)
    (repo / "cmru" / "CHANGES.md").write_text(
        "# Changelog\n\n## [6.2.1] - 2026-10-15\n\n- later release\n",
        encoding="utf-8",
    )
    # The gate judges an exact commit and refuses a dirty tracked tree before it
    # reaches the postponement check, so the later release is committed.
    _git(repo, "add", "cmru/CHANGES.md")
    _git(repo, "commit", "--quiet", "-m", "prepare a later release")

    with pytest.raises(RuntimeError, match="authorized only for CMRU 6.2.0"):
        run_release_gate.run_release_gate(
            repo, temp_parent=private_root, postpone_mutation="KI-79",
        )

    assert calls == []
    assert not (repo / "cmru" / ".assay" / "mutation-postponed-cmru.json").exists()


def test_unknown_postponement_id_is_refused_before_any_lane_runs(tmp_path, monkeypatch):
    repo, private_root, calls = _recording_gate(tmp_path, monkeypatch)

    with pytest.raises(RuntimeError, match="is not authorized for a release"):
        run_release_gate.run_release_gate(
            repo, temp_parent=private_root, postpone_mutation="KI-62",
        )

    assert calls == []


def test_postponement_still_runs_later_lanes_and_stops_at_a_failing_one(tmp_path, monkeypatch):
    repo, private_root, calls = _recording_gate(tmp_path, monkeypatch)

    def invoke(_root, lane, _environment, _worktree):
        calls.append(lane)
        return 7 if lane == "canary" else 0

    monkeypatch.setattr(run_release_gate, "_invoke_lane", _probing(invoke))
    status = run_release_gate.run_release_gate(
        repo, temp_parent=private_root, postpone_mutation="KI-79",
    )
    assert status == 7
    assert calls == ["ref-probe", "installed-wheel-live", "assay", "coverage", "canary"]


def test_postponement_does_not_hide_an_earlier_lane_failure(tmp_path, monkeypatch):
    repo, private_root, calls = _recording_gate(tmp_path, monkeypatch)

    def invoke(_root, lane, _environment, _worktree):
        calls.append(lane)
        return 5 if lane == "coverage" else 0

    monkeypatch.setattr(run_release_gate, "_invoke_lane", _probing(invoke))
    assert run_release_gate.run_release_gate(
        repo, temp_parent=private_root, postpone_mutation="KI-79",
    ) == 5
    assert not (repo / "cmru" / ".assay" / "mutation-postponed-cmru.json").exists()


def test_full_gate_writes_no_marker_and_removes_a_stale_one(tmp_path, monkeypatch, capsys):
    repo, private_root, calls = _recording_gate(tmp_path, monkeypatch)
    marker = repo / "cmru" / ".assay" / "mutation-postponed-cmru.json"
    assert run_release_gate.run_release_gate(repo, temp_parent=private_root) == 0
    assert calls == [
        "ref-probe", "installed-wheel-live", "assay", "coverage", "facts", "mutation", "canary",
    ]
    assert not marker.exists()
    assert "WARN" not in capsys.readouterr().err
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("{}", encoding="utf-8")
    assert run_release_gate.run_release_gate(repo, temp_parent=private_root) == 0
    assert not marker.exists()


@pytest.mark.parametrize("bad", ["", "   ", "\t"])
def test_tracking_id_must_be_non_empty(tmp_path, monkeypatch, bad):
    repo, private_root, calls = _recording_gate(tmp_path, monkeypatch)
    with pytest.raises(argparse.ArgumentTypeError):
        run_release_gate.run_release_gate(repo, temp_parent=private_root, postpone_mutation=bad)
    assert calls == []
    with pytest.raises(SystemExit) as raised:
        run_release_gate.main(["--worktree", str(repo), "--postpone-mutation", bad])
    assert raised.value.code == 2
    assert calls == []


def test_postpone_mutation_option_requires_a_value(tmp_path):
    with pytest.raises(SystemExit) as raised:
        run_release_gate.main(["--worktree", str(tmp_path), "--postpone-mutation"])
    assert raised.value.code == 2


def test_main_passes_the_stripped_tracking_id_through(tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(
        run_release_gate,
        "run_release_gate",
        lambda worktree, **kwargs: seen.append((worktree, kwargs)) or 0,
    )
    assert run_release_gate.main(["--worktree", str(tmp_path), "--postpone-mutation", " KI-79 "]) == 0
    assert run_release_gate.main(["--worktree", str(tmp_path)]) == 0
    assert seen == [
        (tmp_path, {"postpone_mutation": "KI-79"}),
        (tmp_path, {"postpone_mutation": None}),
    ]


# --- C-1: the gate judges only code bound to the claimed HEAD ----------------


def _commit_helper(repo: Path, relative: str = "cmru/src/cmru/helper.py") -> Path:
    helper = repo / relative
    helper.parent.mkdir(parents=True, exist_ok=True)
    helper.write_text("VALUE = 1\n", encoding="utf-8")
    _git(repo, "add", relative)
    _git(repo, "commit", "--quiet", "-m", "add a gate helper")
    return helper


def _refusing_gate(tmp_path, monkeypatch, repo, mutate, match):
    events = []
    monkeypatch.setattr(
        run_release_gate, "_preflight_assay_source",
        lambda _root: events.append("preflight"),
    )
    monkeypatch.setattr(
        run_release_gate, "_invoke_lane", lambda *_args: events.append("lane") or 0,
    )
    private_root = tmp_path / "private-backups"
    private_root.mkdir(mode=0o700)
    mutate()
    with pytest.raises(RuntimeError, match=match):
        run_release_gate.run_release_gate(repo, temp_parent=private_root)
    assert events == []  # refused before the preflight imported or ran anything


def test_tracked_helper_edited_away_from_head_refuses_before_any_lane(tmp_path, monkeypatch):
    repo, *_rest = _fixture(tmp_path, monkeypatch)
    helper = _commit_helper(repo)
    _refusing_gate(
        tmp_path, monkeypatch, repo,
        lambda: helper.write_text("VALUE = 2\n", encoding="utf-8"),
        "tracked source changed from HEAD: cmru/src/cmru/helper.py",
    )


def test_tracked_helper_deleted_since_head_refuses_before_any_lane(tmp_path, monkeypatch):
    repo, *_rest = _fixture(tmp_path, monkeypatch)
    helper = _commit_helper(repo)
    _refusing_gate(tmp_path, monkeypatch, repo, helper.unlink, "tracked source changed from HEAD")


def test_symlink_and_shadowing_top_level_module_refuse_before_any_lane(tmp_path, monkeypatch):
    repo, *_rest = _fixture(tmp_path, monkeypatch)
    _commit_helper(repo)

    def plant():
        (repo / "cmru" / "src" / "alias.py").symlink_to("cmru/helper.py")
        (repo / "cmru" / "src" / "json.py").write_text("raise SystemExit(0)\n")

    _refusing_gate(tmp_path, monkeypatch, repo, plant, r"symlinks: cmru/src/alias\.py")


def test_untracked_non_code_data_under_a_code_root_is_tolerated(tmp_path, monkeypatch):
    repo, *_rest = _fixture(tmp_path, monkeypatch)
    _commit_helper(repo)
    (repo / "cmru" / "src" / "cmru.egg-info").mkdir()
    (repo / "cmru" / "src" / "cmru.egg-info" / "PKG-INFO").write_text("Name: cmru\n")
    cache = repo / "cmru" / "src" / "cmru" / "__pycache__"
    cache.mkdir()
    (cache / "helper.cpython-314.pyc").write_bytes(b"\0")
    run_release_gate._assert_no_untracked_gate_code(repo)  # must not raise


def test_hash_object_mismatch_is_an_error_not_a_pass(tmp_path, monkeypatch):
    repo, *_rest = _fixture(tmp_path, monkeypatch)
    _commit_helper(repo)
    real = run_release_gate._git_output

    def lying(root, *args):
        return b"" if args[0] == "hash-object" else real(root, *args)

    monkeypatch.setattr(run_release_gate, "_git_output", lying)
    with pytest.raises(RuntimeError, match="hash-object mismatch"):
        run_release_gate._assert_no_untracked_gate_code(repo)


def test_git_failure_while_binding_helpers_is_reported(tmp_path):
    with pytest.raises(RuntimeError, match="cannot bind gate helper source to HEAD: git ls-tree"):
        run_release_gate._assert_no_untracked_gate_code(tmp_path)


def test_registered_gate_entrypoint_uses_isolated_python_before_imports(tmp_path):
    repo = tmp_path / "repo"
    tools = repo / "cmru" / "tools"
    tools.mkdir(parents=True)
    script = tools / "run_release_gate.py"
    shutil.copyfile(ROOT / "tools" / "run_release_gate.py", script)
    marker = tmp_path / "shadow-json-ran"
    (tools / "json.py").write_text(
        "from pathlib import Path\n"
        f"Path({str(marker)!r}).write_text('ran')\n"
        "raise SystemExit(0)\n",
        encoding="utf-8",
    )

    config = tomllib.loads((ROOT / "run-gate.toml").read_text(encoding="utf-8"))
    argv = config["lanes"]["gate"]["argv"]
    assert argv[:3] == ["python3", "-I", "-B"]
    command = [part.replace("{worktree}", str(repo)) for part in argv]
    result = subprocess.run(command, cwd=repo, capture_output=True, text=True, check=False)

    assert result.returncode != 0
    assert "cmru-release-gate:" in result.stderr
    assert not marker.exists()


def test_isolated_component_loader_imports_real_gate_helpers_from_tools():
    runner = ROOT / "tools" / "run_release_gate.py"
    code = (
        "import importlib.util, pathlib, sys\n"
        "runner_path = pathlib.Path(sys.argv[1])\n"
        "spec = importlib.util.spec_from_file_location('isolated_gate_runner', runner_path)\n"
        "runner = importlib.util.module_from_spec(spec)\n"
        "spec.loader.exec_module(runner)\n"
        "assay_git, baseline = runner._load_components()\n"
        "assert pathlib.Path(assay_git.__file__).resolve().is_relative_to("
        "pathlib.Path(runner.REPOSITORY_ROOT, 'assay', 'src').resolve())\n"
        "assert pathlib.Path(baseline.__file__).resolve() == pathlib.Path("
        "runner.PROJECT_ROOT, 'tools', 'prepare_assay_baseline.py').resolve()\n"
        "print('CMRU_ISOLATED_COMPONENTS=loaded')\n"
    )
    result = subprocess.run(
        [sys.executable, "-I", "-B", "-c", code, str(runner)],
        cwd=ROOT.parent,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "CMRU_ISOLATED_COMPONENTS=loaded" in result.stdout


@pytest.mark.parametrize("cli_version, version, wheel_sha256, payload_sha256, expected", [
    ("run-gate 24.0.0", "24.0.0", _sample_wheel_sha256(), _sample_payload_sha256(), None),
    ("run-gate 23.11.9", "23.11.9", "c" * 64, _sample_payload_sha256(), "requires Run-Gate 24.0.0 or later"),
    ("run-gate 24.0.0", "24.0.0.dev4+g1234567", "c" * 64, _sample_payload_sha256(), "stable Run-Gate wheel"),
    ("run-gate 24.0.0", "24.0.0+tester.unified", "c" * 64, _sample_payload_sha256(), "stable Run-Gate wheel"),
    (
        "run-gate 23.10.1.dev458+g2c9f6be37",
        "23.10.1.dev458+g2c9f6be37",
        "c" * 64,
        _sample_payload_sha256(),
        "requires a stable Run-Gate wheel at SemVer 24.0.0 or later",
    ),
    ("run-gate 24.0.1", "24.0.0", "c" * 64, _sample_payload_sha256(), "does not match its wheel metadata"),
    ("run-gate rev 68", "24.0.0", "c" * 64, _sample_payload_sha256(), "unrecognized SemVer identity"),
    ("run-gate 24.0.0", "24.0.0", None, _sample_payload_sha256(), "not proven to come from a non-editable wheel"),
    ("run-gate 24.0.0", "24.0.0", "z" * 64, _sample_payload_sha256(), "not proven to come from a non-editable wheel"),
    ("run-gate 24.0.0", "24.0.0", _sample_wheel_sha256(), None, "module hashes are malformed"),
    ("run-gate 24.0.0", "24.0.0", _sample_wheel_sha256(), "z" * 64, "module hashes are malformed"),
])
def test_release_gate_requires_a_stable_run_gate_at_the_declared_floor(
    tmp_path, monkeypatch, cli_version, version, wheel_sha256, payload_sha256, expected,
):
    launcher = tmp_path / "run-gate"
    launcher.write_text(f"#!{sys.executable}\n", encoding="utf-8")
    launcher.chmod(0o755)

    def fake_run(argv, **_kwargs):
        if argv[0] == str(launcher):
            return subprocess.CompletedProcess(
                argv, 0, stdout=f"{cli_version}\n", stderr="",
            )
        assert argv[:3] == [sys.executable, "-I", "-B"]
        return subprocess.CompletedProcess(
            argv, 0,
            stdout=json.dumps({
                "version": version,
                "distribution_sha256": "b" * 64,
                "installed_wheel_sha256": wheel_sha256,
                "installed_payload_sha256": payload_sha256,
            }),
            stderr="",
        )

    monkeypatch.setattr(run_release_gate.shutil, "which", lambda name: str(launcher))
    monkeypatch.setattr(run_release_gate.subprocess, "run", fake_run)

    if isinstance(expected, str):
        with pytest.raises(RuntimeError, match=expected):
            run_release_gate._check_run_gate_identity(tmp_path)
    else:
        identity = run_release_gate._check_run_gate_identity(tmp_path)
        assert identity["version"] == version
        assert identity["executable"] == str(launcher.resolve())
        assert identity["launcher_sha256"] == hashlib.sha256(
            launcher.read_bytes()
        ).hexdigest()
        assert identity["distribution_sha256"] == "b" * 64
        assert identity["installed_wheel_sha256"] == wheel_sha256
        assert identity["installed_payload_sha256"] == payload_sha256


def test_release_gate_reads_legacy_pep610_archive_hash_from_installed_wheel(
    tmp_path, monkeypatch,
):
    import venv

    environment = tmp_path / "candidate-venv"
    venv.EnvBuilder(with_pip=False).create(environment)
    interpreter = environment / "bin" / "python"
    site_packages = environment / "lib" / (
        f"python{sys.version_info.major}.{sys.version_info.minor}"
    ) / "site-packages"
    site_packages.mkdir(parents=True, exist_ok=True)
    dist_info = site_packages / "run_gate-24.0.0.dist-info"
    dist_info.mkdir()
    (dist_info / "METADATA").write_text(
        "Metadata-Version: 2.1\nName: run-gate\nVersion: 24.0.0\n",
        encoding="utf-8",
    )
    (site_packages / "run_gate.py").write_text("VALUE = 1\n", encoding="utf-8")
    (site_packages / "run_gate_admission.py").write_text("VALUE = 2\n", encoding="utf-8")
    (dist_info / "RECORD").write_text(
        "run_gate.py,,\nrun_gate_admission.py,,\n", encoding="utf-8",
    )
    wheel = tmp_path / "run_gate-24.0.0-py3-none-any.whl"
    wheel.write_bytes(b"legacy direct-url wheel bytes")
    wheel_sha256 = hashlib.sha256(wheel.read_bytes()).hexdigest()
    (tmp_path / "cmru").mkdir()
    direct_url_path = dist_info / "direct_url.json"
    direct_url_path.write_text(json.dumps({
        "url": wheel.as_uri(),
        "archive_info": {"hash": f"sha256={wheel_sha256}"},
        "dir_info": {"editable": False},
    }), encoding="utf-8")

    launcher = environment / "bin" / "run-gate"
    launcher.write_text(f"#!{interpreter}\n", encoding="utf-8")
    launcher.chmod(0o755)
    original_run = subprocess.run

    def run(argv, **kwargs):
        if argv[0] == str(launcher):
            return subprocess.CompletedProcess(argv, 0, stdout="run-gate 24.0.0\n", stderr="")
        return original_run(argv, **kwargs)

    monkeypatch.setattr(run_release_gate.shutil, "which", lambda _name: str(launcher))
    monkeypatch.setattr(run_release_gate.subprocess, "run", run)

    identity = run_release_gate._check_run_gate_identity(tmp_path)

    assert identity["installed_wheel_sha256"] == wheel_sha256

    direct_url_path.write_text(json.dumps({
        "url": wheel.as_uri(),
        "archive_info": {
            "hash": f"sha256={wheel_sha256}",
            "hashes": {"sha256": "f" * 64},
        },
        "dir_info": {"editable": False},
    }), encoding="utf-8")
    with pytest.raises(RuntimeError, match="not proven to come from a non-editable wheel"):
        run_release_gate._check_run_gate_identity(tmp_path)


def test_release_gate_refuses_when_run_gate_is_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(run_release_gate.shutil, "which", lambda _name: None)
    with pytest.raises(RuntimeError, match="requires the installed run-gate CLI"):
        run_release_gate._check_run_gate_identity(tmp_path)


def _mock_run_gate_release_artifact_inputs(
    tmp_path, monkeypatch, *, version="24.0.0", asset_name=None, sidecar_sha=None,
    downloaded=None, sidecar_downloaded=None,
    image_labels=None, docker_returncode=0, omit_sidecar=False,
    duplicate_wheel=False, duplicate_sidecar=False, sidecar_url_value=None,
    docker_stdout=None, docker_stderr="", image_env="RUN_GATE_IMAGE_FOR_TEST",
    provider_present=True, release_result="configured", release_error=None,
    release_tag=None, release_draft=False, release_prerelease=False,
    asset_url="https://example.invalid/run-gate.whl", image_id=None,
    docker_error=None, configured_image="tester-unified:configured",
    run_gate_config_text=None, write_run_gate_config=True,
    provider_prefix="run-gate-v",
):
    from cmru import config as cmru_config
    from cmru import tool_deps

    published_wheel_bytes = _sample_run_gate_wheel() if downloaded is None else downloaded
    wheel_bytes_for_identity = (
        published_wheel_bytes if isinstance(published_wheel_bytes, bytes)
        else _sample_run_gate_wheel()
    )
    wheel_sha = hashlib.sha256(wheel_bytes_for_identity).hexdigest()
    (tmp_path / "cmru.orchestration.toml").write_text(
        "schema_version = 1\n", encoding="utf-8",
    )
    if write_run_gate_config:
        if run_gate_config_text is None:
            image_env_line = (
                "" if image_env is None
                else f'image_env = "{image_env}"\n'
            )
            (tmp_path / "run-gate.root.toml").write_text(
                "schema_version = 1\n"
                "[environments.tester-unified]\n"
                f'image = "{configured_image}"\n'
                f"{image_env_line}",
                encoding="utf-8",
            )
        else:
            (tmp_path / "run-gate.root.toml").write_text(
                run_gate_config_text, encoding="utf-8",
            )
    forge = SimpleNamespace(
        projects=(
            {"run-gate-project": SimpleNamespace(prefix=provider_prefix)}
            if provider_present else {}
        ),
        github=SimpleNamespace(owner="volkb79-2", repo="vbpub", token="test-token"),
    )
    monkeypatch.setattr(cmru_config, "load_forge_config", lambda *_args, **_kwargs: forge)
    wheel_name = asset_name or f"run_gate-{version}-py3-none-any.whl"
    sidecar_url = "https://example.invalid/run-gate.whl.sha256"
    sidecar_bytes = (
        f"{wheel_sha}  {wheel_name}\n".encode()
        if sidecar_sha is None else f"{sidecar_sha}  {wheel_name}\n".encode()
    )
    if sidecar_downloaded is not None:
        sidecar_bytes = sidecar_downloaded
    sidecar_url_value = sidecar_url if sidecar_url_value is None else sidecar_url_value
    checksum_asset = {
        "name": f"run_gate-{version}-py3-none-any.whl.sha256",
        "browser_download_url": sidecar_url_value,
    }
    wheel_asset = {"name": wheel_name, "browser_download_url": asset_url}
    release = {
        "tag_name": release_tag or f"{provider_prefix}{version}",
        "draft": release_draft,
        "prerelease": release_prerelease,
        "assets": [
            wheel_asset,
            *([wheel_asset.copy()] if duplicate_wheel else []),
            *([] if omit_sidecar else [checksum_asset]),
            *([checksum_asset.copy()] if duplicate_sidecar else []),
        ],
    }

    def get_release(path, **_kwargs):
        assert path == (
            "/repos/volkb79-2/vbpub/releases/tags/"
            f"{provider_prefix}24.0.0"
        )
        if release_error is not None:
            raise release_error
        return None if release_result is None else release

    monkeypatch.setattr(tool_deps, "_github_get_json", get_release)

    def download_asset(url, **_kwargs):
        result = sidecar_bytes if url == sidecar_url else published_wheel_bytes
        if isinstance(result, BaseException):
            raise result
        return result

    monkeypatch.setattr(tool_deps, "_download_asset", download_asset)
    labels = {
        "org.vbpub.run-gate.version": "24.0.0",
        "org.vbpub.run-gate.wheel-sha256": wheel_sha,
    } if image_labels is None else (None if image_labels == "__missing__" else image_labels)
    image_data = {
        "Id": "sha256:" + "d" * 64 if image_id is None else image_id,
        "Config": {"Labels": labels},
    }
    stdout = json.dumps(image_data) if docker_stdout is None else docker_stdout
    docker_calls = []

    def fake_run(argv, **_kwargs):
        docker_calls.append(argv)
        if docker_error is not None:
            raise docker_error
        return subprocess.CompletedProcess(
            argv, docker_returncode, stdout=stdout, stderr=docker_stderr,
        )

    monkeypatch.setattr(run_release_gate.subprocess, "run", fake_run)
    return release, wheel_sha, docker_calls


def test_release_gate_binds_installed_and_tester_run_gate_to_published_wheel(
    tmp_path, monkeypatch,
):
    monkeypatch.setenv("RUN_GATE_IMAGE_FOR_TEST", "tester-unified:release")
    release, wheel_sha, docker_calls = _mock_run_gate_release_artifact_inputs(
        tmp_path, monkeypatch,
    )

    identity = run_release_gate._check_run_gate_release_artifacts(
        tmp_path, _matching_release_identity(wheel_sha),
    )

    assert identity == {
        "published_wheel_sha256": wheel_sha,
        "published_payload_sha256": _sample_payload_sha256(),
        "tester_image": "tester-unified:release",
        "tester_image_id": "sha256:" + "d" * 64,
        "tester_image_wheel_sha256": wheel_sha,
    }
    assert release["tag_name"] == "run-gate-v24.0.0"
    assert docker_calls == [[
        "docker", "image", "inspect", "--format", "{{json .}}",
        "tester-unified:release",
    ]]


def test_release_gate_uses_prefix_from_the_repository_cmru_project_config(
    tmp_path, monkeypatch,
):
    project_config = tomllib.loads(
        (ROOT.parent / "run-gate-project" / "cmru.toml").read_text(
            encoding="utf-8",
        )
    )
    prefix = project_config["project"]["prefix"]
    assert prefix == "run-gate-v"
    monkeypatch.setenv("RUN_GATE_IMAGE_FOR_TEST", "tester-unified:release")
    release, wheel_sha, _docker_calls = _mock_run_gate_release_artifact_inputs(
        tmp_path, monkeypatch, provider_prefix=prefix,
    )

    run_release_gate._check_run_gate_release_artifacts(
        tmp_path, _matching_release_identity(wheel_sha),
    )

    assert release["tag_name"] == f"{prefix}24.0.0"


@pytest.mark.parametrize(
    ("overrides", "match"),
    [
        ({"provider_present": False}, "cannot resolve the configured run-gate-project"),
        ({"release_result": None}, "no published Run-Gate release"),
        ({"release_error": OSError("offline")}, "cannot resolve the published Run-Gate release"),
        ({"release_tag": "run-gate-v24.0.1"}, "returned Run-Gate release tag"),
        ({"release_draft": True}, "not a published stable release"),
        ({"release_prerelease": True}, "not a published stable release"),
        ({"asset_name": "run-gate-v24.0.0.tar.gz"}, "exactly one asset named"),
        ({"duplicate_wheel": True}, "exactly one asset named"),
        ({"omit_sidecar": True}, "exactly one checksum asset"),
        ({"duplicate_sidecar": True}, "exactly one checksum asset"),
        ({"asset_url": ""}, "has no download URL"),
        ({"sidecar_url_value": ""}, "checksum .* has no download URL"),
        ({"sidecar_downloaded": b"not a digest"}, "no trustworthy wheel digest"),
        ({"sidecar_sha": "bad"}, "no trustworthy wheel digest"),
        ({"sidecar_sha": 12}, "no trustworthy wheel digest"),
        ({"sidecar_downloaded": OSError("checksum download failed")}, "cannot read the published Run-Gate wheel checksum"),
        ({
            "downloaded": b"wrong bytes",
            "sidecar_sha": _sample_wheel_sha256(),
        }, "do not match the release checksum sidecar"),
        ({"downloaded": OSError("download failed")}, "cannot download the published Run-Gate wheel"),
        ({"image_labels": {
            "org.vbpub.run-gate.version": "24.0.1",
            "org.vbpub.run-gate.wheel-sha256": hashlib.sha256(
                b"published Run-Gate wheel"
            ).hexdigest(),
        }}, "does not contain the published Run-Gate wheel"),
        ({"image_labels": {
            "org.vbpub.run-gate.version": "24.0.0",
            "org.vbpub.run-gate.wheel-sha256": "e" * 64,
        }}, "does not contain the published Run-Gate wheel"),
        ({"image_labels": "__missing__"}, "no readable identity labels"),
        ({"image_id": 4}, "no readable identity labels"),
        ({"docker_stdout": "[]"}, "invalid JSON identity"),
        ({"docker_stdout": json.dumps({"Id": "sha256:" + "d" * 64, "Config": {"Labels": {}}})},
         "does not contain the published Run-Gate wheel"),
        ({"docker_stdout": json.dumps({
            "Id": "bad-image-id",
            "Config": {"Labels": {
                "org.vbpub.run-gate.version": "24.0.0",
                # The labels must name the published wheel so that the
                # identity check reaches the image-ID validation it targets.
                "org.vbpub.run-gate.wheel-sha256": _sample_wheel_sha256(),
            }},
        })}, "image ID is malformed"),
        ({"docker_returncode": 1, "docker_stderr": "No such image"}, "cannot inspect tester-unified image"),
        ({"docker_error": OSError("docker is unavailable")}, "cannot inspect tester-unified image"),
        ({"docker_error": subprocess.TimeoutExpired("docker", 10)}, "cannot inspect tester-unified image"),
        ({"run_gate_config_text": "not = [toml"}, "cannot resolve tester-unified image"),
        ({"run_gate_config_text": "schema_version = 1\n[environments]\n"}, "cannot resolve tester-unified image"),
        ({"run_gate_config_text": (
            "schema_version = 1\n[environments.tester-unified]\n"
            'image = "tester-unified:local"\nimage_env = 7\n'
        )}, "image_env is malformed"),
        ({"run_gate_config_text": (
            "schema_version = 1\n[environments.tester-unified]\nimage = 7\n"
        )}, "image is missing from run-gate.root.toml"),
        ({"write_run_gate_config": False}, "cannot resolve tester-unified image"),
        ({"configured_image": ""}, "image is missing from run-gate.root.toml"),
    ],
)
def test_release_gate_refuses_unbound_run_gate_release_artifacts(
    tmp_path, monkeypatch, overrides, match,
):
    _release, wheel_sha, _calls = _mock_run_gate_release_artifact_inputs(
        tmp_path, monkeypatch, **overrides,
    )

    with pytest.raises(RuntimeError, match=match):
        run_release_gate._check_run_gate_release_artifacts(
            tmp_path, _matching_release_identity(wheel_sha),
        )


def test_release_gate_refuses_installed_run_gate_wheel_digest_mismatch(
    tmp_path, monkeypatch,
):
    _mock_run_gate_release_artifact_inputs(tmp_path, monkeypatch)
    identity = _base_run_gate_identity()
    identity["installed_wheel_sha256"] = "e" * 64

    with pytest.raises(RuntimeError, match="installed Run-Gate wheel does not match"):
        run_release_gate._check_run_gate_release_artifacts(tmp_path, identity)


def test_release_gate_refuses_installed_run_gate_module_digest_mismatch(
    tmp_path, monkeypatch,
):
    _release, wheel_sha, _calls = _mock_run_gate_release_artifact_inputs(
        tmp_path, monkeypatch,
    )
    identity = _matching_release_identity(wheel_sha)
    identity["installed_payload_sha256"] = "f" * 64

    with pytest.raises(RuntimeError, match="executable modules do not match"):
        run_release_gate._check_run_gate_release_artifacts(tmp_path, identity)


def test_release_gate_refuses_unreadable_run_gate_wheel_payload(tmp_path, monkeypatch):
    bad_wheel = b"not a wheel archive"
    bad_sha = hashlib.sha256(bad_wheel).hexdigest()
    _release, _wheel_sha, _calls = _mock_run_gate_release_artifact_inputs(
        tmp_path, monkeypatch, downloaded=bad_wheel, sidecar_sha=bad_sha,
    )
    identity = _matching_release_identity(bad_sha)

    with pytest.raises(RuntimeError, match="not a readable archive"):
        run_release_gate._check_run_gate_release_artifacts(tmp_path, identity)


def test_release_gate_refuses_empty_tester_image_override(tmp_path, monkeypatch):
    monkeypatch.setenv("RUN_GATE_IMAGE_FOR_TEST", "")
    _release, wheel_sha, _calls = _mock_run_gate_release_artifact_inputs(
        tmp_path, monkeypatch,
    )

    with pytest.raises(RuntimeError, match="image override .* is empty"):
        run_release_gate._check_run_gate_release_artifacts(
            tmp_path, _matching_release_identity(wheel_sha),
        )


def test_release_gate_uses_configured_image_when_override_is_unset(tmp_path, monkeypatch):
    monkeypatch.delenv("RUN_GATE_IMAGE_FOR_TEST", raising=False)
    _release, wheel_sha, docker_calls = _mock_run_gate_release_artifact_inputs(
        tmp_path, monkeypatch,
    )

    identity = run_release_gate._check_run_gate_release_artifacts(
        tmp_path, _matching_release_identity(wheel_sha),
    )

    assert identity["tester_image"] == "tester-unified:configured"
    assert docker_calls[0][-1] == "tester-unified:configured"


def test_release_gate_accepts_environment_without_image_override(tmp_path, monkeypatch):
    monkeypatch.delenv("RUN_GATE_IMAGE_FOR_TEST", raising=False)
    _release, wheel_sha, docker_calls = _mock_run_gate_release_artifact_inputs(
        tmp_path, monkeypatch, image_env=None,
    )

    identity = run_release_gate._check_run_gate_release_artifacts(
        tmp_path, _matching_release_identity(wheel_sha),
    )

    assert identity["tester_image"] == "tester-unified:configured"
    assert docker_calls[0][-1] == "tester-unified:configured"


# --- C-2: only refs this gate owns are protected -----------------------------


def test_unrelated_commits_tags_and_fetches_do_not_fail_the_gate(tmp_path, monkeypatch):
    repo, *_rest, calls = _fixture(tmp_path, monkeypatch)
    primary = tmp_path / "primary"
    private_root = tmp_path / "private-backups"
    private_root.mkdir(mode=0o700)

    def invoke(_root, lane, environment, _worktree):
        calls.append(lane)
        assert environment["RUN_GATE_TESTER_UNIFIED_IMAGE"] == "sha256:" + "d" * 64
        if lane == "assay":  # another agent's worktree, mid-gate
            serial = len(calls)
            _git(primary, "commit", "--allow-empty", "-q", "-m", f"unrelated work {serial}")
            _git(primary, "branch", f"unrelated-branch-{serial}")
            _git(primary, "tag", f"unrelated-tag-{serial}")
            _git(primary, "update-ref", f"refs/remotes/origin/fetched-{serial}", "HEAD")
        return 0

    monkeypatch.setattr(run_release_gate, "_invoke_lane", _probing(invoke))
    assert run_release_gate.run_release_gate(repo, temp_parent=private_root) == 0
    assert calls == LANES
    # A retained-snapshot resume after more unrelated activity is also clean.
    _git(primary, "commit", "--allow-empty", "-q", "-m", "more unrelated work")
    assert run_release_gate.run_release_gate(repo, temp_parent=private_root) == 0


def test_a_lane_writing_a_protected_ref_fails_the_gate(tmp_path, monkeypatch):
    repo, *_rest, calls = _fixture(tmp_path, monkeypatch)
    private_root = tmp_path / "private-backups"
    private_root.mkdir(mode=0o700)

    def invoke(_root, lane, _environment, worktree):
        calls.append(lane)
        if lane == "coverage":
            _git(worktree, "update-ref", _probe_ref(worktree).replace("ref-probe", "stray"), "HEAD")
        return 0

    monkeypatch.setattr(run_release_gate, "_invoke_lane", _probing(invoke))
    with pytest.raises(RuntimeError, match="protected Git refs changed during"):
        run_release_gate.run_release_gate(repo, temp_parent=private_root)
    assert calls == ["ref-probe", "installed-wheel-live", "assay", "coverage"]


@pytest.mark.parametrize("mutation", ["move", "delete"])
def test_a_lane_cannot_move_or_delete_an_existing_release_tag(tmp_path, monkeypatch, mutation):
    repo, *_rest, calls = _fixture(tmp_path, monkeypatch)
    tag = "cmru-v6.1.0"
    _git(repo, "tag", tag)
    private_root = tmp_path / "private-backups"
    private_root.mkdir(mode=0o700)

    def invoke(_root, lane, _environment, worktree):
        calls.append(lane)
        if lane == "coverage":
            if mutation == "move":
                tree = _git(worktree, "rev-parse", "HEAD^{tree}")
                new_commit = _git(worktree, "commit-tree", tree, "-m", "replace tag target")
                _git(worktree, "update-ref", f"refs/tags/{tag}", new_commit)
            else:
                _git(worktree, "update-ref", "-d", f"refs/tags/{tag}")
        return 0

    monkeypatch.setattr(run_release_gate, "_invoke_lane", _probing(invoke))
    # The refusal names the exact protected ref a lane moved or deleted.
    with pytest.raises(
        RuntimeError,
        match=rf"protected release ref refs/tags/{tag} changed during release-gate execution",
    ):
        run_release_gate.run_release_gate(repo, temp_parent=private_root)
    assert calls == ["ref-probe", "installed-wheel-live", "assay", "coverage"]


def test_main_fast_forward_during_a_lane_is_allowed(tmp_path, monkeypatch):
    repo, *_rest, calls = _fixture(tmp_path, monkeypatch)
    _git(repo, "checkout", "--quiet", "-b", "release-candidate")
    main_before = _git(repo, "rev-parse", "refs/heads/main")
    private_root = tmp_path / "private-backups"
    private_root.mkdir(mode=0o700)

    def invoke(_root, lane, _environment, worktree):
        calls.append(lane)
        if lane == "coverage":
            tree = _git(worktree, "rev-parse", "HEAD^{tree}")
            advanced = _git(
                worktree, "commit-tree", tree, "-p", main_before, "-m", "advance main",
            )
            _git(worktree, "update-ref", "refs/heads/main", advanced, main_before)
        return 0

    monkeypatch.setattr(run_release_gate, "_invoke_lane", _probing(invoke))
    assert run_release_gate.run_release_gate(repo, temp_parent=private_root) == 0
    assert calls == ["ref-probe", "installed-wheel-live", "assay", "coverage", "mutation", "canary"]


def test_main_cannot_be_rewound_to_an_older_fast_forward_tip_between_lanes(
    tmp_path, monkeypatch,
):
    repo, *_rest, calls = _fixture(tmp_path, monkeypatch)
    _git(repo, "checkout", "--quiet", "-b", "release-candidate")
    main_before = _git(repo, "rev-parse", "refs/heads/main")
    advanced_main = None
    private_root = tmp_path / "private-backups"
    private_root.mkdir(mode=0o700)

    def invoke(_root, lane, _environment, worktree):
        nonlocal advanced_main
        calls.append(lane)
        if lane == "coverage":
            tree = _git(worktree, "rev-parse", "HEAD^{tree}")
            advanced_main = _git(
                worktree, "commit-tree", tree, "-p", main_before,
                "-m", "advance main",
            )
            _git(worktree, "update-ref", "refs/heads/main", advanced_main, main_before)
        elif lane == "mutation":
            assert advanced_main is not None
            _git(worktree, "update-ref", "refs/heads/main", main_before, advanced_main)
        return 0

    monkeypatch.setattr(run_release_gate, "_invoke_lane", _probing(invoke))
    with pytest.raises(RuntimeError, match="refs/heads/main changed to a non-fast-forward"):
        run_release_gate.run_release_gate(repo, temp_parent=private_root)
    assert calls == ["ref-probe", "installed-wheel-live", "assay", "coverage", "mutation"]


def test_main_non_fast_forward_during_a_lane_fails_the_gate(tmp_path, monkeypatch):
    repo, *_rest, calls = _fixture(tmp_path, monkeypatch)
    primary = tmp_path / "primary"
    old_main = _git(primary, "rev-parse", "refs/heads/main")
    _git(primary, "commit", "--allow-empty", "--quiet", "-m", "advance main")
    new_main = _git(primary, "rev-parse", "HEAD")
    _git(repo, "checkout", "--quiet", "-b", "release-candidate")
    private_root = tmp_path / "private-backups"
    private_root.mkdir(mode=0o700)

    def invoke(_root, lane, _environment, worktree):
        calls.append(lane)
        if lane == "coverage":
            tree = _git(worktree, "rev-parse", "HEAD^{tree}")
            divergent = _git(
                worktree, "commit-tree", tree, "-p", old_main, "-m", "rewrite main",
            )
            _git(worktree, "update-ref", "refs/heads/main", divergent, new_main)
        return 0

    monkeypatch.setattr(run_release_gate, "_invoke_lane", _probing(invoke))
    with pytest.raises(RuntimeError, match="refs/heads/main changed to a non-fast-forward"):
        run_release_gate.run_release_gate(repo, temp_parent=private_root)
    assert calls == ["ref-probe", "installed-wheel-live", "assay", "coverage"]


def test_resuming_a_snapshot_under_a_different_run_gate_identity_refuses(tmp_path, monkeypatch):
    repo, *_rest = _fixture(tmp_path, monkeypatch)
    private_root = tmp_path / "private-backups"
    private_root.mkdir(mode=0o700)
    monkeypatch.setattr(run_release_gate, "_invoke_lane", _probing(lambda *_args: 0))
    assert run_release_gate.run_release_gate(repo, temp_parent=private_root) == 0

    monkeypatch.setattr(
        run_release_gate, "_check_run_gate_identity",
        lambda _root: _base_run_gate_identity("23.12.0"),
    )
    with pytest.raises(RuntimeError, match="judged by a different Run-Gate identity"):
        run_release_gate.run_release_gate(repo, temp_parent=private_root)


def test_release_gate_refuses_when_run_gate_wheel_changes_during_a_lane(
    tmp_path, monkeypatch,
):
    repo, *_rest, calls = _fixture(tmp_path, monkeypatch)
    private_root = tmp_path / "private-backups"
    private_root.mkdir(mode=0o700)
    identity_reads = 0

    def changed_identity(_root):
        nonlocal identity_reads
        identity_reads += 1
        version = "24.0.0" if identity_reads <= 2 else "24.0.1"
        return _base_run_gate_identity(version)

    monkeypatch.setattr(run_release_gate, "_check_run_gate_identity", changed_identity)
    monkeypatch.setattr(
        run_release_gate, "_invoke_lane", _probing(lambda *_args: calls.append("lane") or 0),
    )

    with pytest.raises(RuntimeError, match="Run-Gate identity changed during"):
        run_release_gate.run_release_gate(repo, temp_parent=private_root)

    assert calls == ["lane"]


def test_release_gate_refuses_when_tester_image_changes_during_a_lane(
    tmp_path, monkeypatch,
):
    repo, *_rest, calls = _fixture(tmp_path, monkeypatch)
    private_root = tmp_path / "private-backups"
    private_root.mkdir(mode=0o700)
    image_reads = 0

    def changed_image(_root, **_kwargs):
        nonlocal image_reads
        image_reads += 1
        image = _tester_image_identity()
        if image_reads > 1:
            image["tester_image_id"] = "sha256:" + "e" * 64
        return image

    monkeypatch.setattr(run_release_gate, "_check_tester_image_identity", changed_image)
    monkeypatch.setattr(
        run_release_gate, "_invoke_lane", _probing(lambda *_args: calls.append("lane") or 0),
    )

    with pytest.raises(RuntimeError, match="tester-unified Run-Gate image identity changed"):
        run_release_gate.run_release_gate(repo, temp_parent=private_root)

    assert calls == ["lane"]


def test_run_gate_identity_recheck_preserves_published_payload_digest(
    tmp_path, monkeypatch,
):
    expected = _gate_identity()
    monkeypatch.setattr(
        run_release_gate, "_check_run_gate_identity",
        lambda _root: _base_run_gate_identity(),
    )
    monkeypatch.setattr(
        run_release_gate, "_check_tester_image_identity",
        lambda _root, **_kwargs: _tester_image_identity(),
    )

    assert run_release_gate._recheck_run_gate_identity(tmp_path, expected) == expected


def test_a_silent_ref_detector_fails_the_gate(tmp_path, monkeypatch):
    repo, *_rest, calls = _fixture(tmp_path, monkeypatch)
    private_root = tmp_path / "private-backups"
    private_root.mkdir(mode=0o700)

    def invoke(_root, lane, _environment, _worktree):
        calls.append(lane)
        return 0  # the probe lane "runs" but writes nothing

    monkeypatch.setattr(run_release_gate, "_invoke_lane", invoke)
    with pytest.raises(RuntimeError, match="did not detect it"):
        run_release_gate.run_release_gate(repo, temp_parent=private_root)
    assert calls == ["ref-probe"]


def test_a_non_ref_error_during_the_probe_check_is_not_swallowed(tmp_path, monkeypatch):
    repo, *_rest, calls = _fixture(tmp_path, monkeypatch)
    private_root = tmp_path / "private-backups"
    private_root.mkdir(mode=0o700)

    def invoke(_root, lane, _environment, worktree):
        calls.append(lane)
        _git(worktree, "commit", "--allow-empty", "-q", "-m", "lane drift")
        return 0

    monkeypatch.setattr(run_release_gate, "_invoke_lane", _probing(invoke))
    with pytest.raises(RuntimeError, match="is not attached to"):
        run_release_gate.run_release_gate(repo, temp_parent=private_root)
    assert calls == ["ref-probe"]


def test_failed_ref_probe_lane_returns_its_status_and_leaves_no_probe_ref(tmp_path, monkeypatch):
    repo, *_rest = _fixture(tmp_path, monkeypatch)
    private_root = tmp_path / "private-backups"
    private_root.mkdir(mode=0o700)
    seen = {}

    def invoke(_root, lane, _environment, worktree):
        seen["refs"] = lambda: _git(worktree, "for-each-ref", "refs/cmru-gate")
        return 3

    monkeypatch.setattr(run_release_gate, "_invoke_lane", _probing(invoke))
    assert run_release_gate.run_release_gate(repo, temp_parent=private_root) == 3
    assert seen["refs"]() == ""


def test_protected_ref_patterns_name_only_gate_owned_refs():
    from cmru import gate_snapshot

    assert gate_snapshot.protected_ref_patterns("cmru-gate-abc123", "release-x") == (
        "refs/heads/cmru-gate-abc123", "refs/heads/release-x", "refs/cmru-gate/abc123",
    )


# --- C-7: the host canary never dirties the source ---------------------------


@pytest.mark.parametrize("interrupted", [False, True])
def test_source_stays_clean_after_a_passing_or_interrupted_gate(
    tmp_path, monkeypatch, interrupted,
):
    repo, *_rest, calls = _fixture(tmp_path, monkeypatch)
    (repo / ".gitignore").write_text(
        (ROOT.parent / ".gitignore").read_text(encoding="utf-8"), encoding="utf-8",
    )
    _git(repo, "add", ".gitignore")
    _git(repo, "commit", "--quiet", "-m", "estate ignore rules")
    private_root = tmp_path / "private-backups"
    private_root.mkdir(mode=0o700)

    def invoke(_root, lane, _environment, _worktree):
        calls.append(lane)
        if interrupted and lane == "assay":
            raise run_release_gate.GateInterrupted(signal.SIGTERM)
        return 0

    monkeypatch.setattr(run_release_gate, "_invoke_lane", _probing(invoke))
    if interrupted:
        with pytest.raises(SystemExit) as raised:
            run_release_gate.run_release_gate(repo, temp_parent=private_root)
        assert raised.value.code == 128 + signal.SIGTERM
    else:
        assert run_release_gate.run_release_gate(repo, temp_parent=private_root) == 0
    assert _git(repo, "status", "--porcelain", "--untracked-files=all") == ""
    assert not list(repo.glob(".cmru-gate-canary-*.txt"))


# --- C-5: the live-snapshot proof binds to THIS gate, not to a retained file --


def _smoke():
    spec = importlib.util.spec_from_file_location(
        "installed_wheel_smoke_under_test", ROOT / "tools" / "installed_wheel_smoke.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _live_manifest(tmp_path: Path, **overrides):
    from datetime import timezone

    snapshot = tmp_path / "cmru-gate-abc"
    source = tmp_path / "source"
    source.mkdir()
    record = {
        "state": "running",
        "snapshot_path": str(snapshot),
        "snapshot_branch": "cmru-gate-abc",
        "source_root": str(source),
        "source_canary": ".cmru-gate-canary-0123.txt",
        "refreshed_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    record.update(overrides)
    manifest = snapshot / "cmru" / ".assay" / "release-gate-snapshot.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(json.dumps(record), encoding="utf-8")
    return snapshot, source


def test_live_snapshot_proof_accepts_only_a_fresh_manifest_bound_to_this_checkout(tmp_path):
    from datetime import timedelta, timezone

    smoke = _smoke()
    snapshot, source = _live_manifest(tmp_path)
    smoke._check_live_gate_snapshot(snapshot, required=True)  # canary invisible: proven

    (source / ".cmru-gate-canary-0123.txt").write_text("canary")
    with pytest.raises(RuntimeError, match="can see the host release-gate source canary"):
        smoke._check_live_gate_snapshot(snapshot, required=True)
    with pytest.raises(RuntimeError, match="can see the host"):  # also when not required
        smoke._check_live_gate_snapshot(snapshot)

    # A `running` manifest left by a killed gate is not a live gate.
    later = datetime.now(timezone.utc) + timedelta(hours=2)
    with pytest.raises(RuntimeError, match="not refreshed in the last 30 minutes"):
        smoke._check_live_gate_snapshot(snapshot, required=True, now=later)
    smoke._check_live_gate_snapshot(snapshot, now=later)  # direct run: ordinary lane
    earlier = datetime.now(timezone.utc) - timedelta(hours=1)
    with pytest.raises(RuntimeError, match="not refreshed"):
        smoke._check_live_gate_snapshot(snapshot, required=True, now=earlier)


@pytest.mark.parametrize("override, match", [
    ({"state": "passed"}, "its state is not running"),
    ({"snapshot_path": "/elsewhere/other-checkout"}, "different checkout"),
    ({"snapshot_branch": "other"}, "different checkout"),
    ({"snapshot_path": None}, "different checkout"),
    ({"refreshed_at_utc": "yesterday"}, "not refreshed"),
    ({"refreshed_at_utc": None}, "not refreshed"),
])
def test_live_snapshot_proof_refuses_a_manifest_that_is_not_the_current_gate(
    tmp_path, override, match,
):
    smoke = _smoke()
    snapshot, _source = _live_manifest(tmp_path, **override)
    with pytest.raises(RuntimeError, match=match):
        smoke._check_live_gate_snapshot(snapshot, required=True)
    smoke._check_live_gate_snapshot(snapshot)  # not required: a direct component run


def test_live_snapshot_proof_missing_unreadable_and_malformed_manifests(tmp_path):
    smoke = _smoke()
    checkout = tmp_path / "cmru-gate-abc"
    (checkout / "cmru" / ".assay").mkdir(parents=True)
    with pytest.raises(RuntimeError, match="the manifest is missing"):
        smoke._check_live_gate_snapshot(checkout, required=True)
    smoke._check_live_gate_snapshot(checkout)
    manifest = checkout / "cmru" / ".assay" / "release-gate-snapshot.json"
    manifest.write_text("{broken", encoding="utf-8")
    with pytest.raises(RuntimeError, match="manifest is unreadable"):
        smoke._check_live_gate_snapshot(checkout)
    manifest.write_text("[]", encoding="utf-8")
    with pytest.raises(RuntimeError, match="its state is not running"):
        smoke._check_live_gate_snapshot(checkout, required=True)


@pytest.mark.parametrize("override, match", [
    ({"source_root": "relative/path"}, "no absolute source_root"),
    ({"source_canary": "other.txt"}, "no host canary path"),
    ({"source_canary": ".cmru-gate-canary-x/../y"}, "not a root filename"),
])
def test_live_snapshot_proof_refuses_an_unusable_canary_description(tmp_path, override, match):
    smoke = _smoke()
    snapshot, _source = _live_manifest(tmp_path, **override)
    with pytest.raises(RuntimeError, match=match):
        smoke._check_live_gate_snapshot(snapshot, required=True)


# --- C-4: lease liveness is evidence, never a stat of an absent PID ----------


def _lease(holder: str, expiry: str | None):
    return SimpleNamespace(holder=holder, expires_at_utc=expiry)


def _stamp(delta):
    from datetime import timezone
    return (datetime.now(timezone.utc) + delta).strftime("%Y-%m-%dT%H:%M:%SZ")


def test_lease_state_needs_positive_evidence_before_calling_a_holder_dead(monkeypatch):
    from datetime import timedelta

    from cmru import gate_snapshot as gs

    future, past = _stamp(timedelta(hours=1)), _stamp(-timedelta(hours=1))
    identity = gs._local_namespace_identity()
    assert identity is not None
    assert gs.gate_lease_state(None) == gs.LEASE_DEAD
    assert gs.gate_lease_state(_lease("operator", None)) == gs.LEASE_LIVE
    assert gs.gate_lease_state(_lease("operator", future)) == gs.LEASE_LIVE
    assert gs.gate_lease_state(_lease("operator", past)) == gs.LEASE_DEAD

    # A PID absent from THIS namespace: legacy, malformed or foreign holders are
    # protected by the TTL and, once expired, are indeterminate, never dead.
    foreign = [
        "cmru-gate:99999999:abc",
        "cmru-gate:xx:1:abc:1:2",
        "cmru-gate:99999999:1:abc:1:deadbeef",
        f"cmru-gate:99999999:1:abc:1:{identity.split(':')[1]}",
    ]
    for holder in foreign:
        assert gs.gate_lease_state(_lease(holder, future)) == gs.LEASE_LIVE, holder
        assert gs.gate_lease_state(_lease(holder, past)) == gs.LEASE_INDETERMINATE, holder

    mine = gs._new_lease_holder()
    assert gs.gate_lease_state(_lease(mine, future)) == gs.LEASE_LIVE
    assert gs.gate_lease_state(_lease(mine, past)) == gs.LEASE_LIVE  # process still running
    ticks = int(gs._process_start_ticks(os.getpid()))
    recycled = f"cmru-gate:{os.getpid()}:{ticks + 1}:abc:{identity}"
    assert gs.gate_lease_state(_lease(recycled, future)) == gs.LEASE_DEAD

    child = subprocess.Popen([sys.executable, "-c", "pass"])
    child.wait()
    gone = f"cmru-gate:{child.pid}:1:abc:{identity}"
    assert gs.gate_lease_state(_lease(gone, future)) == gs.LEASE_DEAD

    monkeypatch.setattr(gs, "_process_start_ticks", lambda _pid: None)
    unreadable = f"cmru-gate:{os.getpid()}:1:abc:{identity}"
    assert gs.gate_lease_state(_lease(unreadable, future)) == gs.LEASE_INDETERMINATE


def test_namespace_identity_and_holder_fall_back_to_unprovable(monkeypatch):
    from cmru import gate_snapshot as gs

    def unreadable(_path):
        raise OSError("no /proc")

    monkeypatch.setattr(gs.os, "readlink", unreadable)
    assert gs._local_namespace_identity() is None
    monkeypatch.setattr(gs.os, "readlink", lambda _path: "not-a-namespace")
    assert gs._local_namespace_identity() is None
    monkeypatch.undo()
    monkeypatch.setattr(gs, "_local_namespace_identity", lambda: None)
    assert len(gs._new_lease_holder().split(":")) == 3
    monkeypatch.undo()
    monkeypatch.setattr(gs, "_process_start_ticks", lambda _pid: None)
    assert len(gs._new_lease_holder().split(":")) == 3


# --- C-6: supported, safe, repeatable cleanup of retained snapshots ----------


def _closed_snapshot(source: Path):
    from cmru import gate_snapshot

    snapshot = gate_snapshot.open_gate_snapshot(source)
    snapshot.run_gate_identity = _gate_identity()
    gate_snapshot.write_snapshot_manifest(snapshot, "failed")
    gate_snapshot.close_gate_snapshot(snapshot)
    return snapshot


def test_resuming_a_legacy_snapshot_upgrades_its_git_ref_digest(tmp_path, monkeypatch):
    from dataclasses import replace

    from cmru import gate_snapshot as gs

    repo, *_rest = _fixture(tmp_path, monkeypatch)
    snapshot = _closed_snapshot(repo)
    manifest = repo / "cmru" / ".assay" / "release-gate-snapshot.json"
    document = json.loads(manifest.read_text(encoding="utf-8"))
    document.pop("git_refs_sha256")
    document.pop("refs_scope")
    manifest.write_text(json.dumps(document), encoding="utf-8")

    api = gs._worktree_api()
    record = api.read_record(snapshot.context.record_path)
    metadata = dict(record.metadata)
    metadata.pop("refs_sha256")
    metadata.pop("refs_scope")
    api.write_record(replace(record, metadata=metadata))

    resumed = gs.open_gate_snapshot(repo)
    try:
        expected = gs._protected_refs_sha256(
            resumed.worktree_path, resumed.protected_refs,
        )
        assert resumed.refs_sha256 == expected
        upgraded_record = api.read_record(resumed.context.record_path)
        assert upgraded_record.metadata["refs_sha256"] == expected
        assert upgraded_record.metadata["refs_scope"] == gs._REFS_SCOPE
    finally:
        gs.close_gate_snapshot(resumed)


@pytest.mark.parametrize("lane_boundary", ["prepare_lane", "finish_lane"])
def test_lane_boundary_preserves_unavailable_anchor_evidence(
    tmp_path, monkeypatch, lane_boundary,
):
    from cmru import gate_snapshot as gs

    snapshot = SimpleNamespace(
        worktree_path=tmp_path / "snapshot",
        source_root=tmp_path / "source",
        commit="commit",
        protected_refs=(),
        refs_sha256="digest",
        anchor_refs=None,
        context=object(),
        lane_evidence_sha256={},
    )
    monkeypatch.setattr(gs, "validate_lane_outputs", lambda *_a, **_kw: None)
    monkeypatch.setattr(gs, "copy_evidence_to_source", lambda *_a, **_kw: None)
    monkeypatch.setattr(gs, "_git", lambda *_a, **_kw: None)
    monkeypatch.setattr(gs, "copy_evidence_to_snapshot", lambda *_a, **_kw: None)
    monkeypatch.setattr(gs, "_verify_snapshot", lambda *_a, **_kw: None)
    monkeypatch.setattr(gs, "_existing_evidence_hashes", lambda *_a, **_kw: {})

    getattr(gs, lane_boundary)(snapshot, "assay")

    assert snapshot.anchor_refs is None


@pytest.mark.parametrize("identity", [
    None,
    {},
    {"version": "24.0.0.dev1+g1234567"},
    {"version": ""},
    {"version": 23},
    {"revision": True, "version": "24.0.0"},
    {"revision": 68, "version": "24.0.0", "unexpected": True},
    {**_gate_identity(), "installed_payload_sha256": "f" * 64},
    {**_gate_identity(), "published_payload_sha256": "f" * 64},
])
def test_a_retained_snapshot_without_valid_run_gate_identity_refuses_resume(
    tmp_path, monkeypatch, identity,
):
    from cmru import gate_snapshot

    repo, *_rest = _fixture(tmp_path, monkeypatch)
    snapshot = _closed_snapshot(repo)
    manifest = repo / "cmru" / ".assay" / "release-gate-snapshot.json"
    document = json.loads(manifest.read_text(encoding="utf-8"))
    if identity is None:
        del document["run_gate_identity"]
    else:
        document["run_gate_identity"] = identity
    manifest.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(RuntimeError, match="no trusted Run-Gate identity"):
        gate_snapshot.open_gate_snapshot(repo)


def _lease_snapshot(snapshot, holder: str, *, hours: int):
    import worktree
    from datetime import timedelta, timezone

    return worktree.acquire_lease(
        snapshot.context, holder=holder, ttl=timedelta(hours=abs(hours)),
        now=datetime.now(timezone.utc) - timedelta(hours=2 if hours < 0 else 0),
    )


def _second_source(tmp_path: Path, name: str = "second-source") -> Path:
    source = tmp_path / name
    _git(tmp_path / "primary", "worktree", "add", "--quiet", "-b", name, str(source), "HEAD")
    return source


def test_cleanup_keeps_a_current_snapshot_and_is_repeatable(tmp_path, monkeypatch):
    from cmru import gate_snapshot as gs

    repo, *_rest = _fixture(tmp_path, monkeypatch)
    snapshot = _closed_snapshot(repo)
    record_path = snapshot.context.record_path

    (report,) = gs.inspect_gate_snapshots(repo)
    assert (report.status, report.source_present) == (gs.RETAINED, True)
    assert "resume" in report.reason
    with pytest.raises(RuntimeError, match="is retained"):
        gs.remove_gate_snapshot(record_path, executor_git_root=repo)
    assert snapshot.worktree_path.is_dir()

    (report,) = gs.inspect_gate_snapshots(repo, include_current=True)
    assert report.status == gs.REMOVABLE
    gs.remove_gate_snapshot(record_path, executor_git_root=repo, include_current=True)
    assert not snapshot.worktree_path.exists()
    assert _git(repo, "branch", "--list", snapshot.branch) == ""
    assert gs.inspect_gate_snapshots(repo, include_current=True) == []  # nothing left to do


def test_cleanup_removes_only_the_snapshot_whose_source_moved_on(tmp_path, monkeypatch):
    from cmru import gate_snapshot as gs

    repo, *_rest = _fixture(tmp_path, monkeypatch)
    old = _closed_snapshot(repo)
    _git(repo, "commit", "--allow-empty", "-q", "-m", "next commit")
    new = _closed_snapshot(repo)

    reports = {item.record.branch: item for item in gs.inspect_gate_snapshots(repo)}
    assert reports[old.branch].status == gs.REMOVABLE
    assert "moved to" in reports[old.branch].reason
    assert reports[new.branch].status == gs.RETAINED
    gs.remove_gate_snapshot(old.context.record_path, executor_git_root=repo)
    assert not old.worktree_path.exists() and new.worktree_path.is_dir()


def test_cleanup_never_touches_a_snapshot_with_a_live_or_unprovable_lease(tmp_path, monkeypatch):
    from cmru import gate_snapshot as gs

    repo, *_rest = _fixture(tmp_path, monkeypatch)
    live = gs.open_gate_snapshot(repo)  # this process still holds its lease
    try:
        (report,) = gs.inspect_gate_snapshots(repo, include_current=True)
        assert (report.status, "live" in report.reason) == (gs.RETAINED, True)
    finally:
        gs.close_gate_snapshot(live)

    # A holder whose PID is absent from this namespace, TTL in the future.
    _lease_snapshot(live, "cmru-gate:99999999:1:abc:1:deadbeef", hours=1)
    (report,) = gs.inspect_gate_snapshots(repo, include_current=True)
    assert report.status == gs.RETAINED and "live" in report.reason
    with pytest.raises(RuntimeError, match="is retained"):
        gs.remove_gate_snapshot(
            live.context.record_path, executor_git_root=repo, include_current=True,
        )
    with pytest.raises(RuntimeError, match="cannot be proven dead"):
        gs.remove_gate_snapshots_for_source(repo)
    assert live.worktree_path.is_dir()

    # The same foreign holder after its TTL: unprovable from here, still kept.
    _lease_snapshot(live, "cmru-gate:99999999:1:abc:1:deadbeef", hours=-1)
    (report,) = gs.inspect_gate_snapshots(repo, include_current=True)
    assert report.status == gs.RETAINED and "cannot be proven" in report.reason
    assert live.worktree_path.is_dir()


def test_cleanup_reclaims_the_snapshot_of_a_provably_dead_same_namespace_holder(
    tmp_path, monkeypatch,
):
    from cmru import gate_snapshot as gs

    repo, *_rest = _fixture(tmp_path, monkeypatch)
    snapshot = _closed_snapshot(repo)
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    child.wait()
    _lease_snapshot(
        snapshot, f"cmru-gate:{child.pid}:1:abc:{gs._local_namespace_identity()}", hours=1,
    )
    gs.remove_gate_snapshots_for_source(repo)
    assert not snapshot.worktree_path.exists()


def test_open_takes_over_a_dead_holders_lease_but_not_a_live_foreign_one(tmp_path, monkeypatch):
    import worktree
    from cmru import gate_snapshot as gs

    repo, *_rest = _fixture(tmp_path, monkeypatch)
    snapshot = _closed_snapshot(repo)
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    child.wait()
    _lease_snapshot(
        snapshot, f"cmru-gate:{child.pid}:1:abc:{gs._local_namespace_identity()}", hours=1,
    )
    reopened = gs.open_gate_snapshot(repo)  # dead holder: reaped, retry proceeds
    gs.close_gate_snapshot(reopened)
    _lease_snapshot(snapshot, "cmru-gate:99999999:1:abc:1:deadbeef", hours=1)
    with pytest.raises(worktree.WorkspaceError, match="active lease"):
        gs.open_gate_snapshot(repo)


def test_an_unregistered_or_prunable_source_releases_its_snapshot(tmp_path, monkeypatch):
    from cmru import gate_snapshot as gs

    _fixture(tmp_path, monkeypatch)
    prunable = _second_source(tmp_path, "prunable-source")
    kept = _closed_snapshot(prunable)
    shutil.rmtree(prunable)  # still registered; Git reports it prunable
    (report,) = gs.inspect_gate_snapshots(tmp_path / "primary")
    assert (report.status, report.source_present) == (gs.REMOVABLE, False)
    assert "prunable" in report.reason
    gs.remove_gate_snapshot(kept.context.record_path, executor_git_root=tmp_path / "primary")
    assert not kept.worktree_path.exists()
    assert _git(tmp_path / "primary", "branch", "--list", kept.branch) == ""

    deregistered = _second_source(tmp_path, "deregistered-source")
    other = _closed_snapshot(deregistered)
    _git(tmp_path / "primary", "worktree", "remove", "--force", str(deregistered))
    (report,) = gs.inspect_gate_snapshots(tmp_path / "primary")
    assert report.status == gs.REMOVABLE and "no longer registered" in report.reason
    gs.remove_gate_snapshot(other.context.record_path, executor_git_root=tmp_path / "primary")
    assert gs.inspect_gate_snapshots(tmp_path / "primary") == []


def test_an_unobservable_source_alone_never_justifies_deleting_a_snapshot(tmp_path, monkeypatch):
    from cmru import gate_snapshot as gs

    _fixture(tmp_path, monkeypatch)
    primary = tmp_path / "primary"
    locked = _second_source(tmp_path, "locked-source")
    snapshot = _closed_snapshot(locked)
    _git(primary, "worktree", "lock", str(locked))
    shutil.rmtree(locked)  # invisible, but Git still registers it as live
    (report,) = gs.inspect_gate_snapshots(primary)
    assert report.status == gs.RETAINED and "still registers it as live" in report.reason
    with pytest.raises(RuntimeError, match="is retained"):
        gs.remove_gate_snapshot(snapshot.context.record_path, executor_git_root=primary)
    assert snapshot.worktree_path.is_dir()

    # A stat that fails for any reason other than "absent" is also not evidence.
    real_lstat = Path.lstat

    def denied(self, *args, **kwargs):
        if self == locked:
            raise PermissionError("namespace boundary")
        return real_lstat(self, *args, **kwargs)

    monkeypatch.setattr(Path, "lstat", denied)
    (report,) = gs.inspect_gate_snapshots(primary)
    assert report.status == gs.RETAINED and "cannot inspect the source path" in report.reason


def test_unusable_source_state_retains_the_snapshot(tmp_path, monkeypatch):
    from cmru import gate_snapshot as gs

    _fixture(tmp_path, monkeypatch)
    primary = tmp_path / "primary"
    broken = _second_source(tmp_path, "broken-source")
    snapshot = _closed_snapshot(broken)
    (broken / ".git").write_text("gitdir: nowhere\n", encoding="utf-8")
    (report,) = gs.inspect_gate_snapshots(primary)
    assert report.status == gs.RETAINED and "cannot read the source worktree's Git state" in report.reason
    shutil.rmtree(broken)
    broken.write_text("not a directory", encoding="utf-8")
    (report,) = gs.inspect_gate_snapshots(primary)
    assert report.status == gs.RETAINED and "not a real directory" in report.reason
    assert snapshot.worktree_path.is_dir()


def test_a_snapshot_whose_own_checkout_changed_is_retained_for_inspection(tmp_path, monkeypatch):
    from cmru import gate_snapshot as gs

    repo, *_rest = _fixture(tmp_path, monkeypatch)
    snapshot = _closed_snapshot(repo)
    path = snapshot.worktree_path

    _git(path, "checkout", "--quiet", "--detach")
    (report,) = gs.inspect_gate_snapshots(repo)
    assert report.status == gs.RETAINED and "no longer matches its recorded branch" in report.reason
    _git(path, "checkout", "--quiet", snapshot.branch)

    marker = (path / ".git").read_text(encoding="utf-8")
    (path / ".git").write_text("gitdir: nowhere\n", encoding="utf-8")
    (report,) = gs.inspect_gate_snapshots(repo)
    assert report.status == gs.RETAINED and "cannot inspect linked-worktree admin dir" in report.reason
    (path / ".git").write_text(marker, encoding="utf-8")

    shutil.rmtree(path)
    (report,) = gs.inspect_gate_snapshots(repo)
    assert report.status == gs.RETAINED and "cannot inspect CMRU gate snapshot" in report.reason
    path.write_text("not a checkout", encoding="utf-8")
    (report,) = gs.inspect_gate_snapshots(repo)
    assert report.status == gs.RETAINED and "is not a real directory" in report.reason


def test_a_detached_source_releases_its_snapshot_and_foreign_records_are_refused(
    tmp_path, monkeypatch,
):
    import worktree
    from cmru import gate_snapshot as gs

    repo, *_rest = _fixture(tmp_path, monkeypatch)
    snapshot = _closed_snapshot(repo)
    foreign = worktree.create_workspace(
        repo, tmp_path / "not-a-gate", branch="not-a-gate", purpose="cmru-release",
    )
    worktree.create_workspace(
        repo, tmp_path / "mismatched", branch="mismatched", purpose="cmru-gate",
    )
    with pytest.raises(RuntimeError, match="not a CMRU gate snapshot record"):
        gs.remove_gate_snapshot(foreign.record_path, executor_git_root=repo)

    _git(repo, "checkout", "--quiet", "--detach")
    reports = {item.record.branch: item for item in gs.inspect_gate_snapshots(repo)}
    assert reports[snapshot.branch].status == gs.REMOVABLE
    assert "detached" in reports[snapshot.branch].reason
    assert reports["mismatched"].status == gs.RETAINED
    assert "does not match its source identity" in reports["mismatched"].reason


def test_stale_manifest_scope_is_upgraded_not_trusted(tmp_path, monkeypatch):
    from cmru import gate_snapshot as gs

    repo, *_rest = _fixture(tmp_path, monkeypatch)
    snapshot = gs.open_gate_snapshot(repo)
    snapshot.run_gate_identity = _gate_identity()
    gs.write_snapshot_manifest(snapshot, "running")
    manifest = repo / "cmru" / ".assay" / "release-gate-snapshot.json"
    document = json.loads(manifest.read_text(encoding="utf-8"))
    document.update(refs_scope="all-refs/0", git_refs_sha256="0" * 64)
    document["run_gate_identity"] = _gate_identity()
    manifest.write_text(json.dumps(document), encoding="utf-8")
    gs.close_gate_snapshot(snapshot)

    reopened = gs.open_gate_snapshot(repo)  # the old whole-repository digest is ignored
    try:
        assert reopened.refs_sha256 == snapshot.refs_sha256 != "0" * 64
        assert reopened.run_gate_identity == _gate_identity()
    finally:
        gs.close_gate_snapshot(reopened)
