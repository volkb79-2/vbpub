from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import signal
import stat
import subprocess
import sys
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


def _fixture(tmp_path: Path, monkeypatch):
    primary = tmp_path / "primary"
    primary.mkdir()
    _git(primary, "init", "--quiet")
    _git(primary, "config", "user.name", "CMRU Gate Test")
    _git(primary, "config", "user.email", "cmru-gate@example.invalid")
    (primary / "cmru").mkdir()
    (primary / "README.md").write_text("tracked fixture\n", encoding="utf-8")
    (primary / "cmru" / "cmru.toml").write_text("schema_version = 1\n", encoding="utf-8")
    _git(primary, "add", "README.md", "cmru/cmru.toml")
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
        def build_facts(repo_root, project_root, *, git_auth, assay_git):
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
            assert root_secret.is_file() and not root_secret.is_symlink()
            assert project_secret.is_file() and not project_secret.is_symlink()
            return {"head": "abc", "selected_tag": "cmru-v1.0.0"}

        baseline = SimpleNamespace(
            _repository_git_auth=lambda root: auth,
            build_facts=build_facts,
        )
        return git, baseline

    monkeypatch.setattr(run_release_gate, "PROJECT_ROOT", project)
    monkeypatch.setattr(run_release_gate, "_load_components", load_components)
    monkeypatch.setattr(run_release_gate, "_preflight_assay_source", lambda _root: None)
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
    monkeypatch.setattr(run_release_gate, "_invoke_lane", invoke)
    private_root = tmp_path / "private-backups"
    private_root.mkdir(mode=0o700)

    assert run_release_gate.run_release_gate(repo, temp_parent=private_root) == 0
    assert events == [
        "assay-source-preflight",
        "load-components",
        "installed-wheel",
        "assay",
        "coverage",
        "mutation",
        "canary",
    ]
    assert sys.pycache_prefix == prior_cache_prefix
    assert os.environ.get("PYTHONPYCACHEPREFIX") == prior_cache_env


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
        if lane == "installed-wheel":
            manifest = json.loads((
                worktree / "cmru" / ".assay" / "release-gate-snapshot.json"
            ).read_text(encoding="utf-8"))
            host_canary = Path(manifest["source_root"]) / manifest["source_canary"]
            assert manifest["state"] == "running"
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

    monkeypatch.setattr(run_release_gate, "_invoke_lane", invoke)
    assert run_release_gate.run_release_gate(repo, temp_parent=private_root) == 0
    assert calls == ["installed-wheel", "assay", "coverage", "mutation", "canary"]
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

    monkeypatch.setattr(run_release_gate, "_invoke_lane", invoke)
    assert run_release_gate.run_release_gate(repo, temp_parent=private_root) == 19
    assert calls == ["installed-wheel", "assay"]
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

    monkeypatch.setattr(run_release_gate, "_invoke_lane", invoke)
    assert run_release_gate.run_release_gate(repo, temp_parent=private_root) == 0
    assert calls == ["installed-wheel", "assay", "coverage", "mutation", "canary"]
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
        if lane == "installed-wheel":
            evidence = worktree / "cmru" / ".assay" / "verdict-cmru.json"
            evidence.parent.mkdir(parents=True, exist_ok=True)
            evidence.write_text('{"status":"PASS"}\n', encoding="utf-8")
            (worktree / "cmru" / "unapproved-output.txt").write_text("not evidence")
        return 0

    monkeypatch.setattr(run_release_gate, "_invoke_lane", invoke)
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
    _git(primary, "init", "--quiet")
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

    monkeypatch.setattr(run_release_gate, "_invoke_lane", invoke)
    private_root = tmp_path / "private-backups"
    private_root.mkdir(mode=0o700)
    return repo, private_root, calls


def test_postponed_mutation_skips_only_the_mutation_lane_and_records_the_marker(
    tmp_path, monkeypatch, capsys,
):
    repo, private_root, calls = _recording_gate(tmp_path, monkeypatch)
    status = run_release_gate.run_release_gate(
        repo, temp_parent=private_root, postpone_mutation="KI-62",
    )
    assert status == 0
    # Every other lane runs exactly as before, in order; no mutation lane and
    # no remote-facts preparation (which only the mutation lane consumes).
    assert calls == ["installed-wheel", "assay", "coverage", "canary"]
    marker = repo / "cmru" / ".assay" / "mutation-postponed-cmru.json"
    record = json.loads(marker.read_text(encoding="utf-8"))
    assert record["tracking_id"] == "KI-62"
    assert record["lane"] == "mutation"
    assert record["status"] == "postponed"
    assert record["reason"] == run_release_gate.POSTPONE_REASON
    assert datetime.fromisoformat(record["timestamp"]).tzinfo is not None
    err = capsys.readouterr().err
    warn_lines = [line for line in err.splitlines() if "WARN" in line]
    assert len(warn_lines) == 1
    assert "POSTPONED" in warn_lines[0] and "KI-62" in warn_lines[0]


def test_postponement_still_runs_later_lanes_and_stops_at_a_failing_one(tmp_path, monkeypatch):
    repo, private_root, calls = _recording_gate(tmp_path, monkeypatch)

    def invoke(_root, lane, _environment, _worktree):
        calls.append(lane)
        return 7 if lane == "canary" else 0

    monkeypatch.setattr(run_release_gate, "_invoke_lane", invoke)
    status = run_release_gate.run_release_gate(
        repo, temp_parent=private_root, postpone_mutation="KI-62",
    )
    assert status == 7
    assert calls == ["installed-wheel", "assay", "coverage", "canary"]


def test_postponement_does_not_hide_an_earlier_lane_failure(tmp_path, monkeypatch):
    repo, private_root, calls = _recording_gate(tmp_path, monkeypatch)

    def invoke(_root, lane, _environment, _worktree):
        calls.append(lane)
        return 5 if lane == "coverage" else 0

    monkeypatch.setattr(run_release_gate, "_invoke_lane", invoke)
    assert run_release_gate.run_release_gate(
        repo, temp_parent=private_root, postpone_mutation="KI-62",
    ) == 5
    assert not (repo / "cmru" / ".assay" / "mutation-postponed-cmru.json").exists()


def test_full_gate_writes_no_marker_and_removes_a_stale_one(tmp_path, monkeypatch, capsys):
    repo, private_root, calls = _recording_gate(tmp_path, monkeypatch)
    marker = repo / "cmru" / ".assay" / "mutation-postponed-cmru.json"
    assert run_release_gate.run_release_gate(repo, temp_parent=private_root) == 0
    assert calls == ["installed-wheel", "assay", "coverage", "facts", "mutation", "canary"]
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
    assert run_release_gate.main(["--worktree", str(tmp_path), "--postpone-mutation", " KI-62 "]) == 0
    assert run_release_gate.main(["--worktree", str(tmp_path)]) == 0
    assert seen == [
        (tmp_path, {"postpone_mutation": "KI-62"}),
        (tmp_path, {"postpone_mutation": None}),
    ]
