"""Snapshot invariant guards G1-G5 (B111 W6, A-472). Test-only; all pass on today's code.

The snapshot substrate promises that every candidate runs in a tree that no other
candidate can have touched. Each guard pins one way that promise could be lost by a
future optimisation (a hardlinked object store, a reused mutant tree, a weakened
stat cache), using the real ``prepare_snapshot``/``materialize``/``materialize_replacement``.
The helpers duplicate ``test_isolation.py``'s on purpose: this module imports no
other test module.
"""

from __future__ import annotations

import itertools
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path, PurePosixPath

import pytest

from assay import isolation
from assay.config import IsolationConfig
from assay.errors import AssayError, ReasonCode
from assay.isolation import DEFAULT_SNAPSHOT_LIMITS, SnapshotSpec, prepare_snapshot

TIMEOUT = 600.0
REPOSITORY_POLICY = IsolationConfig(
    snapshot_selection="repository", unsafe_symlink_omissions=()
)
STALE_SOURCE = b"a = 1 == 1\nprint(a)\n"
MUTANT_SOURCE = b"a = 1 != 1\nprint(a)\n"
TOOL = b"#!/bin/sh\necho tool\n"

_FIXTURE_ENV = {
    "GIT_AUTHOR_NAME": "Assay Fixture",
    "GIT_AUTHOR_EMAIL": "fixture@assay.invalid",
    "GIT_COMMITTER_NAME": "Assay Fixture",
    "GIT_COMMITTER_EMAIL": "fixture@assay.invalid",
    "GIT_AUTHOR_DATE": "2000-01-01T00:00:00+00:00",
    "GIT_COMMITTER_DATE": "2000-01-01T00:00:00+00:00",
}


def _git(repo: Path, *args: str) -> str:
    """Drive the fixture repository with a real git, independently of assay."""
    env = {
        "PATH": os.environ["PATH"],
        "LC_ALL": "C.UTF-8",
        "LANG": "C.UTF-8",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_TERMINAL_PROMPT": "0",
        **_FIXTURE_ENV,
    }
    completed = subprocess.run(
        [shutil.which("git") or "git", "-C", str(repo), *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr.decode(errors="replace")
    return completed.stdout.decode("utf-8").strip()


def _repo(tmp_path: Path) -> tuple[Path, str]:
    """Two commits: a base holding ``m.py`` and an executable, then a second file and a link."""
    repo = tmp_path / "consumer"
    repo.mkdir()
    _git(repo, "init", "-q")
    (repo / "m.py").write_bytes(STALE_SOURCE)
    (repo / "bin").mkdir()
    (repo / "bin" / "tool.sh").write_bytes(TOOL)
    (repo / "bin" / "tool.sh").chmod(0o755)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    (repo / "pkg").mkdir()
    (repo / "pkg" / "other.py").write_bytes(b"value = 2\n")
    os.symlink("m.py", repo / "link-to-m")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "head")
    return repo, _git(repo, "rev-parse", "HEAD")


def _scratch(tmp_path: Path) -> Path:
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    return scratch


def _spec(repo: Path, scratch: Path, commit: str) -> SnapshotSpec:
    return SnapshotSpec(
        repo_top=repo.resolve(),
        commit=commit,
        project_prefix=PurePosixPath("."),
        scratch_root=scratch.resolve(),
        snapshot_policy=REPOSITORY_POLICY,
        limits=DEFAULT_SNAPSHOT_LIMITS,
    )


def _replacement(prepared, path: str = "m.py", expected: bytes = STALE_SOURCE, new: bytes = MUTANT_SOURCE):
    return prepared.materialize_replacement(
        path=PurePosixPath(path), expected=expected, replacement=new, timeout=TIMEOUT
    )


def _inodes(root: Path, *, skip_git: bool) -> set[tuple[int, int]]:
    """``(st_dev, st_ino)`` of every regular file below *root* (never a link, never a directory)."""
    found: set[tuple[int, int]] = set()
    for directory, names, files in os.walk(root):
        if skip_git and Path(directory) == root:
            names[:] = [name for name in names if name != ".git"]
        for name in files:
            info = os.lstat(os.path.join(directory, name))
            if stat.S_ISREG(info.st_mode):
                found.add((info.st_dev, info.st_ino))
    return found


def test_g1_no_two_materializations_share_an_inode_with_each_other_the_seed_or_the_source(
    tmp_path: Path,
) -> None:
    """A hardlinked object store or worktree would make two candidates one filesystem object."""
    repo, commit = _repo(tmp_path)
    scratch = _scratch(tmp_path)
    with prepare_snapshot(_spec(repo, scratch, commit), timeout=TIMEOUT) as prepared:
        with prepared.materialize(timeout=TIMEOUT) as base, _replacement(prepared) as changed:
            groups = {
                "base objects": _inodes(base.root / ".git" / "objects", skip_git=False),
                "replacement objects": _inodes(changed.root / ".git" / "objects", skip_git=False),
                "base worktree": _inodes(base.root, skip_git=True),
                "replacement worktree": _inodes(changed.root, skip_git=True),
                "seed objects": _inodes(prepared._seed_git_dir / "objects", skip_git=False),
                "source objects": _inodes(repo / ".git" / "objects", skip_git=False),
            }
    assert all(groups.values()), {name: len(found) for name, found in groups.items()}
    for (left, first), (right, second) in itertools.combinations(groups.items(), 2):
        assert first.isdisjoint(second), f"{left} and {right} share an inode"


def test_g2_no_residue_crosses_from_one_candidate_to_the_next(tmp_path: Path) -> None:
    repo, commit = _repo(tmp_path)
    scratch = _scratch(tmp_path)
    trap = '[filter "trap"]\n\tclean = false\n'
    with prepare_snapshot(_spec(repo, scratch, commit), timeout=TIMEOUT) as prepared:
        with prepared.materialize(timeout=TIMEOUT) as first:
            residue = {
                "pkg/__pycache__/m.cpython-314.pyc": b"\x00pyc",
                ".pytest_cache/v/x": b"cache",
                ".git/info/exclude": b"*\n",
            }
            for name, data in residue.items():
                target = first.root / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
            with (first.root / ".git" / "config").open("a", encoding="utf-8") as handle:
                handle.write(trap)
        with prepared.materialize(timeout=TIMEOUT) as second:
            for name in residue:
                assert not (second.root / name).exists(), name
            assert "trap" not in (second.root / ".git" / "config").read_text(encoding="utf-8")
            assert _git(second.root, "status", "--porcelain=v1") == ""


def test_g3_a_stale_bytecode_file_can_not_leak_into_the_next_candidate(tmp_path: Path) -> None:
    """The mutant is IMPORTED (CPython never caches a ``__main__`` script), same size as the base.

    Fresh-per-candidate trees are what keep a same-size, same-mtime ``.pyc`` from making the
    base run as the mutant; a tree-reuse design would fail here.
    """
    repo, commit = _repo(tmp_path)
    scratch = _scratch(tmp_path)
    assert len(STALE_SOURCE) == len(MUTANT_SOURCE)
    command = [sys.executable, "-c", "import m"]
    env = {"PATH": os.environ["PATH"]}  # no PYTHONDONTWRITEBYTECODE, no PYTHONPYCACHEPREFIX

    def imported(root: Path) -> str:
        done = subprocess.run(
            command, cwd=root, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False
        )
        assert done.returncode == 0, done.stderr.decode(errors="replace")
        return done.stdout.decode()

    with prepare_snapshot(_spec(repo, scratch, commit), timeout=TIMEOUT) as prepared:
        with _replacement(prepared) as mutant:
            assert imported(mutant.root) == "False\n"
            assert list((mutant.root / "__pycache__").glob("m.*.pyc")), "the mutant run left no bytecode"
        with prepared.materialize(timeout=TIMEOUT) as base:
            assert imported(base.root) == "True\n"


@pytest.mark.parametrize(
    ("path", "expected", "mode"),
    [("m.py", STALE_SOURCE, 0o644), ("bin/tool.sh", TOOL, 0o755)],
)
def test_g4_the_replaced_file_has_the_fixed_mtime_and_its_committed_mode(
    tmp_path: Path, path: str, expected: bytes, mode: int
) -> None:
    repo, commit = _repo(tmp_path)
    scratch = _scratch(tmp_path)
    with prepare_snapshot(_spec(repo, scratch, commit), timeout=TIMEOUT) as prepared:
        with _replacement(prepared, path, expected, expected + b"# changed\n") as changed:
            target = changed.root / path
            assert target.read_bytes() == expected + b"# changed\n"
            assert target.stat().st_mtime == isolation._FIXED_MTIME
            assert stat.S_IMODE(target.stat().st_mode) == mode
            assert (changed.root / "link-to-m").is_symlink()
            assert os.readlink(changed.root / "link-to-m") == "m.py"


def test_g5_a_same_size_edit_with_a_restored_mtime_is_still_caught(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The content proof survives stat caching: size and mtime match, only the bytes differ.

    Scope: the tamper happens before any index refresh, so this cannot see a later
    ``core.checkStat``/``trustctime`` weakening; the isolation ctime sweep owns that case.
    """
    real = isolation._write_worktree

    def tampering(*args, **kwargs):
        real(*args, **kwargs)
        victim = kwargs["root"] / "m.py"
        data = victim.read_bytes()
        victim.write_bytes(data[:-1] + (b"X" if data[-1:] != b"X" else b"Y"))
        os.utime(victim, (isolation._FIXED_MTIME, isolation._FIXED_MTIME))

    repo, commit = _repo(tmp_path)
    scratch = _scratch(tmp_path)
    with prepare_snapshot(_spec(repo, scratch, commit), timeout=TIMEOUT) as prepared:
        monkeypatch.setattr(isolation, "_write_worktree", tampering)
        with pytest.raises(AssayError) as caught:
            with prepared.materialize(timeout=TIMEOUT):
                pytest.fail("a tampered worktree must not be yielded")
        assert caught.value.reason_code is ReasonCode.GIT_FAILED
    assert list(scratch.iterdir()) == []
