"""Unit tests for gate_canary (PACKAGE GA1) -- the canary-commit builder for
`nyxloom gate verify`. cli tests monkeypatch gate_runner.run_gate_at_commit
(to force a verdict cheaply), so this module's REAL body -- mutation/
fallback selection, in-place injection, and the scratch-worktree
build/cleanup cycle -- is exercised here directly against real git, mirroring
test_gate_runner.py's own split.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from nyxloom import gate_canary


def _run(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True)


def _init_repo(root: Path, files: dict[str, str]) -> str:
    """Create a git repo at `root` with `files` {relpath: content}, commit
    everything, and return the HEAD sha."""
    root.mkdir(parents=True, exist_ok=True)
    for rel, content in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
    assert _run(root, "init", "-q", "-b", "main").returncode == 0
    assert _run(root, "-c", "user.email=t@t", "-c", "user.name=t", "add", "-A").returncode == 0
    assert _run(root, "-c", "user.email=t@t", "-c", "user.name=t",
                "commit", "-qm", "init").returncode == 0
    return _run(root, "rev-parse", "HEAD").stdout.strip()


class _FakeCfg:
    """Minimal stand-in for ProjectConfig -- gate_canary only reads `.root`."""
    def __init__(self, root: Path):
        self.root = root


# --------------------------------------------------------------------------- #
# inject_canary -- pure(ish) in-place mutation, no git involved
# --------------------------------------------------------------------------- #

def test_inject_canary_mutation_flips_a_comparison(tmp_path):
    """A file with a mutable comparison -> MECHANISM_MUTATION, and the file
    content on disk actually changes (the operator is flipped)."""
    target = tmp_path / "thing.py"
    original = "def over_limit(x):\n    return x > 10\n"
    target.write_text(original)

    mechanism, rel, description = gate_canary.inject_canary(tmp_path)

    assert mechanism == gate_canary.MECHANISM_MUTATION
    assert rel == "thing.py"
    mutated = target.read_text()
    assert mutated != original
    assert "line 2" in description


def test_inject_canary_fallback_when_no_mutable_site(tmp_path):
    """A file with no comparison/boolop/bool-const -> falls back to a
    module-top-level `raise AssertionError`, prepended to the original
    content (which is preserved, not discarded)."""
    target = tmp_path / "data.py"
    original = "GREETING = 'hello'\n"
    target.write_text(original)

    mechanism, rel, description = gate_canary.inject_canary(tmp_path)

    assert mechanism == gate_canary.MECHANISM_FALLBACK
    assert rel == "data.py"
    mutated = target.read_text()
    assert mutated.startswith('raise AssertionError("nyxloom-gate-canary")\n')
    assert original in mutated
    assert "AssertionError" in description


def test_inject_canary_raises_when_no_python_source(tmp_path):
    """No .py file anywhere under the worktree -> CanaryError, not a silent
    no-op (a project with nothing to mutate cannot be canary-verified)."""
    (tmp_path / "README.md").write_text("just docs\n")

    with pytest.raises(gate_canary.CanaryError):
        gate_canary.inject_canary(tmp_path)


def test_inject_canary_skips_test_and_vcs_directories(tmp_path):
    """A mutable comparison that lives ONLY under tests/ or .git/ is not an
    eligible canary target -- inject_canary must fall through to the
    (non-existent) real source and raise, not silently mutate a test file."""
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_thing.py").write_text(
        "def test_x():\n    assert 1 < 2\n"
    )

    with pytest.raises(gate_canary.CanaryError):
        gate_canary.inject_canary(tmp_path)


def test_inject_canary_deterministic_file_order(tmp_path):
    """Two eligible files -> the alphabetically-first path is always chosen
    (deterministic, not filesystem-iteration-order-dependent)."""
    (tmp_path / "zzz.py").write_text("def f(x):\n    return x > 1\n")
    (tmp_path / "aaa.py").write_text("def g(x):\n    return x < 1\n")

    _, rel, _ = gate_canary.inject_canary(tmp_path)

    assert rel == "aaa.py"


# --------------------------------------------------------------------------- #
# build_canary_commit -- the real git scratch-worktree build/cleanup cycle
# --------------------------------------------------------------------------- #

def test_build_canary_commit_produces_a_real_commit_and_cleans_up(tmp_path):
    root = tmp_path / "repo"
    head = _init_repo(root, {"thing.py": "def over_limit(x):\n    return x > 10\n"})
    cfg = _FakeCfg(root)

    outcome = gate_canary.build_canary_commit(cfg, head)

    # The canary commit exists in the repo's object graph, distinct from HEAD.
    assert outcome.commit != head
    assert _run(root, "cat-file", "-e", outcome.commit).returncode == 0
    assert outcome.mechanism == gate_canary.MECHANISM_MUTATION
    assert outcome.target_path == "thing.py"

    # Exactly one file changed between HEAD and the canary commit.
    diff = _run(root, "diff", "--name-only", head, outcome.commit).stdout.split()
    assert diff == ["thing.py"]

    # The scratch worktree used to build it is gone.
    scratch = root / ".worktrees" / f"canary-{head[:12]}"
    assert not scratch.exists()
    worktrees = _run(root, "worktree", "list").stdout
    assert str(scratch) not in worktrees

    # The ORIGINAL checkout (the live root) is untouched -- the mutation only
    # ever lived in the disposable scratch worktree.
    assert (root / "thing.py").read_text() == "def over_limit(x):\n    return x > 10\n"


def test_build_canary_commit_raises_and_still_cleans_up_scratch(tmp_path):
    """No eligible .py source -> CanaryError propagates, and the scratch
    worktree created to look for one is still removed (the `finally`)."""
    root = tmp_path / "repo"
    head = _init_repo(root, {"README.md": "docs only\n"})
    cfg = _FakeCfg(root)

    with pytest.raises(gate_canary.CanaryError):
        gate_canary.build_canary_commit(cfg, head)

    scratch = root / ".worktrees" / f"canary-{head[:12]}"
    assert not scratch.exists()
    assert str(scratch) not in _run(root, "worktree", "list").stdout


def test_build_canary_commit_removes_stale_scratch_before_building(tmp_path):
    """A leftover scratch worktree from an interrupted prior run is removed
    before this run re-adds it (idempotent, mirrors gate_runner's own
    stale-scratch handling)."""
    root = tmp_path / "repo"
    head = _init_repo(root, {"thing.py": "def over_limit(x):\n    return x > 10\n"})
    cfg = _FakeCfg(root)

    stale = root / ".worktrees" / f"canary-{head[:12]}"
    assert _run(root, "worktree", "add", "--detach", str(stale), head).returncode == 0
    assert stale.exists()

    outcome = gate_canary.build_canary_commit(cfg, head)

    assert outcome.commit != head
    assert not stale.exists()
