"""B223: ``isolation.materialize = "tmpfs-worker"``.

The directory the worker trees live in is an ordinary temporary directory here;
``_mount_type`` is patched to answer ``tmpfs`` so the capacity proof passes.
The proof itself is tested against a fake mount table.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path, PurePosixPath

import pytest
from test_isolation import (
    TIMEOUT,
    _CheapCloneWorkspaceProvider,
    _CopyingWorkspaceProvider,
    _git,
    _scratch,
    _snapshot_file_signature,
    _snapshot_git_state,
    _spec,
)

from assay import _tmpfs_worker, git as git_module, isolation
from assay.config import IsolationConfig, LaneConfigError
from assay.errors import AssayError, Outcome, ReasonCode
from assay.isolation import prepare_snapshot

FILES = {
    "dup1.txt": b"dup\n",
    "other.txt": b"other\n",
    "sub/deep.txt": b"deep\n",
    "bin/tool.sh": b"#!/bin/sh\n",
    ".gitignore": b"deps\n__pycache__\n",
}


def _repo(tmp_path: Path, *, big: int = 0) -> tuple[Path, str]:
    repo = tmp_path / "consumer"
    repo.mkdir(parents=True)
    _git(repo, "init", "-q")
    for name, data in FILES.items():
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    (repo / "bin" / "tool.sh").chmod(0o755)
    os.symlink("dup1.txt", repo / "alias")
    if big:
        (repo / "big.bin").write_bytes(b"\0" * big)
    (repo / "deps").mkdir()
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    return repo, _git(repo, "rev-parse", "HEAD")


def _policy(shm: Path, **changes: object) -> IsolationConfig:
    values: dict[str, object] = {
        "snapshot_selection": "repository",
        "unsafe_symlink_omissions": (),
        "materialize": "tmpfs-worker",
        "tmpfs_root": str(shm),
        "tmpfs_max_mib": 64,
    }
    values.update(changes)
    return IsolationConfig(**values)  # type: ignore[arg-type]


@pytest.fixture
def shm(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    directory = tmp_path / "shm"
    directory.mkdir()
    monkeypatch.setattr(_tmpfs_worker, "_mount_type", lambda path, mounts: "tmpfs")
    return directory


def _open(tmp_path: Path, repo: Path, commit: str, policy: IsolationConfig, provider=None):
    tmp_path.mkdir(parents=True, exist_ok=True)
    provider = provider or _CheapCloneWorkspaceProvider(tmp_path / "run")
    return prepare_snapshot(
        _spec(repo, _scratch(tmp_path), commit, snapshot_policy=policy),
        timeout=TIMEOUT,
        workspace=provider,
    )


def _candidate(prepared, name: str, data: bytes):
    return prepared.materialize_replacement(
        path=PurePosixPath(name),
        expected=FILES[name],
        replacement=data,
        timeout=TIMEOUT,
    )


def _worker_dirs(shm: Path) -> list[Path]:
    return [item for private in shm.iterdir() for item in private.iterdir()]


# -- capacity proof ---------------------------------------------------------


MOUNTS = (
    "rootfs / ext4 rw 0 0\n"
    "short\n"
    "tmpfs /dev/shm tmpfs rw 0 0\n"
    "tmpfs /mnt/a\\040b tmpfs rw 0 0\n"
    "ram /ramdisk ramfs rw 0 0\n"
)


def test_mount_type_picks_the_longest_covering_mount(tmp_path: Path) -> None:
    table = tmp_path / "mounts"
    table.write_text(MOUNTS)
    assert _tmpfs_worker._mount_type("/dev/shm", str(table)) == "tmpfs"
    assert _tmpfs_worker._mount_type("/dev/shm/x/y", str(table)) == "tmpfs"
    assert _tmpfs_worker._mount_type("/mnt/a b/c", str(table)) == "tmpfs"
    assert _tmpfs_worker._mount_type("/dev/shmx", str(table)) == "ext4"
    assert _tmpfs_worker._mount_type("/home", str(table)) == "ext4"


def test_capacity_proof_names_each_refusal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    table = tmp_path / "mounts"
    table.write_text(f"tmpfs {tmp_path} tmpfs rw 0 0\n")
    ok = dict(max_bytes=10**9, mounts_file=str(table))
    _tmpfs_worker.check_capacity(tmp_path, 1024, **ok)
    with pytest.raises(_tmpfs_worker.TmpfsUnavailable, match="above the declared"):
        _tmpfs_worker.check_capacity(
            tmp_path, 1024, max_bytes=10, mounts_file=str(table)
        )
    with pytest.raises(_tmpfs_worker.TmpfsUnavailable, match="bytes free"):
        _tmpfs_worker.check_capacity(tmp_path, 10**15, max_bytes=10**16, mounts_file=str(table))
    table.write_text("")
    with pytest.raises(_tmpfs_worker.TmpfsUnavailable, match="unknown filesystem"):
        _tmpfs_worker.check_capacity(tmp_path, 1, **ok)
    table.write_text(f"d {tmp_path} ext4 rw 0 0\n")
    with pytest.raises(_tmpfs_worker.TmpfsUnavailable, match="not tmpfs"):
        _tmpfs_worker.check_capacity(tmp_path, 1, **ok)
    with pytest.raises(_tmpfs_worker.TmpfsUnavailable, match="cannot be inspected"):
        _tmpfs_worker.check_capacity(tmp_path / "missing", 1, **ok)


def test_tree_bytes_and_copy_preserve_size_and_mtime(tmp_path: Path) -> None:
    source = tmp_path / "src"
    (source / "d").mkdir(parents=True)
    (source / "d" / "f").write_bytes(b"12345")
    os.utime(source / "d" / "f", (1000, 1000))
    os.symlink("d/f", source / "ln")
    assert _tmpfs_worker.tree_bytes(source) == 5 + len("d/f")  # link counts too? no
    copy = tmp_path / "copy"
    _tmpfs_worker.copy_template(source, copy)
    assert (copy / "d" / "f").stat().st_mtime == 1000
    assert os.readlink(copy / "ln") == "d/f"


# -- config -----------------------------------------------------------------


def test_config_validation_and_declaration() -> None:
    default = IsolationConfig(snapshot_selection="repository", unsafe_symlink_omissions=())
    assert default.materialize == "cow"
    assert default.as_declared() == {"snapshot_selection": "repository"}
    declared = IsolationConfig(
        snapshot_selection="repository",
        unsafe_symlink_omissions=(),
        materialize="tmpfs-worker",
        tmpfs_root="/mnt/ram",
        tmpfs_max_mib=7,
    ).as_declared()
    assert declared["materialize"] == "tmpfs-worker"
    assert declared["tmpfs_root"] == "/mnt/ram"
    assert declared["tmpfs_max_mib"] == 7
    for bad, match in (
        ({"materialize": "overlay"}, "materialize.*must be one of"),
        ({"materialize": 1}, "materialize.*must be one of"),
        ({"tmpfs_root": "relative"}, "tmpfs_root"),
        ({"tmpfs_root": "/a/../b"}, "tmpfs_root"),
        ({"tmpfs_root": 3}, "tmpfs_root"),
        ({"tmpfs_max_mib": 0}, "tmpfs_max_mib"),
        ({"tmpfs_max_mib": True}, "tmpfs_max_mib"),
        ({"tmpfs_max_mib": "5"}, "tmpfs_max_mib"),
    ):
        with pytest.raises(LaneConfigError, match=match):
            IsolationConfig(
                snapshot_selection="repository", unsafe_symlink_omissions=(), **bad
            )


# -- behaviour ---------------------------------------------------------------


def _run_sequence(tmp_path: Path, policy: IsolationConfig, label: str):
    """Three candidates; returns what each exposed, plus the root each used."""
    repo, commit = _repo(tmp_path / label)
    seen = []
    with _open(tmp_path / label, repo, commit, policy) as prepared:
        for name, data in (
            ("other.txt", b"changed\n"),
            ("bin/tool.sh", b"#!/bin/sh\necho x\n"),
            ("other.txt", b"again\n"),
        ):
            with _candidate(prepared, name, data) as snapshot:
                assert git_module.dirty_paths(snapshot.root) == ()
                seen.append(
                    (
                        snapshot.commit,
                        _snapshot_file_signature(snapshot.root),
                        _snapshot_git_state(snapshot.root),
                        snapshot.root,
                    )
                )
    return seen


def test_worker_candidates_equal_cow_candidates_and_reuse_one_tree(
    tmp_path: Path, shm: Path
) -> None:
    cow = _run_sequence(
        tmp_path,
        IsolationConfig(snapshot_selection="repository", unsafe_symlink_omissions=()),
        "cow",
    )
    worker = _run_sequence(tmp_path, _policy(shm), "worker")
    assert [item[:3] for item in cow] == [item[:3] for item in worker]
    assert len({item[3] for item in worker}) == 1
    assert len({item[3] for item in cow}) == 3
    assert worker[0][3].is_relative_to(shm)
    assert list(shm.iterdir()) == []  # the private directory is gone on close


def test_the_first_candidate_runs_the_full_proof_then_the_tier_applies(
    tmp_path: Path, shm: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, commit = _repo(tmp_path)
    calls = []
    original = isolation.SnapshotRepository._verify
    monkeypatch.setattr(
        isolation.SnapshotRepository,
        "_verify",
        lambda self, *args: (calls.append(1), original(self, *args))[1],
    )
    with _open(tmp_path, repo, commit, _policy(shm)) as prepared:
        for data in (b"1\n", b"2\n", b"3\n"):
            with _candidate(prepared, "other.txt", data):
                pass
    # one for the sealed template's build, one for the fresh worker tree
    assert len(calls) == 2


def test_added_entries_are_removed_and_the_tree_is_kept(
    tmp_path: Path, shm: Path
) -> None:
    repo, commit = _repo(tmp_path)
    with _open(tmp_path, repo, commit, _policy(shm)) as prepared:
        with _candidate(prepared, "other.txt", b"x\n") as first:
            root = first.root
            (root / "__pycache__").mkdir()
            (root / "__pycache__" / "m.pyc").write_bytes(b"c")
            (root / "sub" / "leak.txt").write_bytes(b"l")
            (root / ".git" / "objects" / "zz").mkdir()
        with _candidate(prepared, "other.txt", b"y\n") as second:
            assert second.root == root
            assert not (root / "__pycache__").exists()
            assert not (root / "sub" / "leak.txt").exists()
            assert not (root / ".git" / "objects" / "zz").exists()
            assert (root / "other.txt").read_bytes() == b"y\n"
        assert (root / "other.txt").read_bytes() == FILES["other.txt"]
        assert (root / "bin" / "tool.sh").stat().st_mode & 0o111 == 0o111


@pytest.mark.parametrize(
    "damage",
    ["edit", "delete", "chmod", "retarget", "head"],
)
def test_a_changed_or_missing_entry_drops_the_tree(
    tmp_path: Path, shm: Path, damage: str
) -> None:
    repo, commit = _repo(tmp_path)
    with _open(tmp_path, repo, commit, _policy(shm)) as prepared:
        with _candidate(prepared, "other.txt", b"x\n") as first:
            root = first.root
            if damage == "edit":
                (root / "sub" / "deep.txt").write_bytes(b"DEEP!\n")
            elif damage == "delete":
                (root / "dup1.txt").unlink()
            elif damage == "chmod":
                (root / "dup1.txt").chmod(0o755)
            elif damage == "retarget":
                (root / "alias").unlink()
                os.symlink("other.txt", root / "alias")
            else:
                (root / ".git" / "HEAD").write_bytes(b"x")
                (root / ".git" / "HEAD").chmod(0o000)
        with _candidate(prepared, "other.txt", b"y\n") as second:
            assert second.root != root or damage == "head"
            assert (second.root / "sub" / "deep.txt").read_bytes() == FILES["sub/deep.txt"]
            assert (second.root / "dup1.txt").read_bytes() == FILES["dup1.txt"]


def test_an_exception_in_the_body_drops_the_tree(tmp_path: Path, shm: Path) -> None:
    repo, commit = _repo(tmp_path)
    with _open(tmp_path, repo, commit, _policy(shm)) as prepared:
        with pytest.raises(RuntimeError, match="boom"):
            with _candidate(prepared, "other.txt", b"x\n"):
                assert len(_worker_dirs(shm)) == 1
                raise RuntimeError("boom")
        assert _worker_dirs(shm) == []
        with _candidate(prepared, "other.txt", b"y\n"):
            assert len(_worker_dirs(shm)) == 1


def test_a_failed_settle_drops_the_tree(
    tmp_path: Path, shm: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, commit = _repo(tmp_path)

    def broken(self, **_kw):
        raise OSError("disk gone")

    with _open(tmp_path, repo, commit, _policy(shm)) as prepared:
        with monkeypatch.context() as patch:
            patch.setattr(_tmpfs_worker.WorkerTree, "settle", broken)
            with _candidate(prepared, "other.txt", b"x\n"):
                pass
        assert _worker_dirs(shm) == []


def test_link_paths_are_removed_after_the_candidate(tmp_path: Path, shm: Path) -> None:
    repo, commit = _repo(tmp_path)
    policy = _policy(shm, link_paths=("deps",))
    with _open(tmp_path, repo, commit, policy) as prepared:
        with _candidate(prepared, "other.txt", b"x\n") as first:
            root = first.root
            assert (root / "deps").is_symlink()
        with _candidate(prepared, "other.txt", b"y\n") as second:
            assert second.root == root
            assert (root / "deps").is_symlink()
            (root / "deps").unlink()  # a command removing it is tolerated
        with _candidate(prepared, "other.txt", b"z\n") as third:
            assert third.root == root


def test_unavailable_tmpfs_falls_back_to_clones_with_one_advisory(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo, commit = _repo(tmp_path)
    plain = tmp_path / "plain"  # a real directory that is not a tmpfs here
    plain.mkdir()
    policy = _policy(plain)
    roots = []
    with _open(tmp_path, repo, commit, policy) as prepared:
        for data in (b"x\n", b"y\n"):
            with _candidate(prepared, "other.txt", data) as snapshot:
                roots.append(snapshot.root)
                assert not snapshot.root.is_relative_to(plain)
    assert len(set(roots)) == 2
    err = capsys.readouterr().err
    assert err.count("tmpfs-worker' is unavailable") == 1


def test_missing_root_and_oversize_and_write_failure_fall_back(
    tmp_path: Path, shm: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, commit = _repo(tmp_path, big=2 * 1024 * 1024)
    cases = [
        _policy(tmp_path / "does-not-exist"),
        _policy(shm, tmpfs_max_mib=1),
    ]
    for index, policy in enumerate(cases):
        with _open(tmp_path / f"case{index}", repo, commit, policy) as prepared:
            with _candidate(prepared, "other.txt", b"x\n") as snapshot:
                assert not snapshot.root.is_relative_to(shm)
    monkeypatch.setattr(
        _tmpfs_worker,
        "copy_template",
        lambda *a: (_ for _ in ()).throw(OSError("no space")),
    )
    with _open(tmp_path / "b", repo, commit, _policy(shm)) as prepared:
        with _candidate(prepared, "other.txt", b"x\n") as snapshot:
            assert not snapshot.root.is_relative_to(shm)
        assert _worker_dirs(shm) == []


def test_an_assay_error_while_building_a_worker_propagates_and_cleans_up(
    tmp_path: Path, shm: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, commit = _repo(tmp_path)
    with _open(tmp_path, repo, commit, _policy(shm)) as prepared:
        original = prepared._prime_index
        calls = []

        def prime(root, deadline):
            calls.append(root)
            if len(calls) == 2:
                raise AssayError("git failed", outcome=Outcome.ERROR, reason_code=ReasonCode.EXEC_FAILED)
            original(root, deadline)

        monkeypatch.setattr(prepared, "_prime_index", prime)
        with pytest.raises(AssayError, match="git failed"):
            with _candidate(prepared, "other.txt", b"x\n"):
                pass
        assert _worker_dirs(shm) == []


def test_baseline_nested_and_non_cow_materializations_use_the_old_paths(
    tmp_path: Path, shm: Path
) -> None:
    repo, commit = _repo(tmp_path)
    with _open(tmp_path / "a", repo, commit, _policy(shm)) as prepared:
        with prepared.materialize(timeout=TIMEOUT) as baseline:
            assert not baseline.root.is_relative_to(shm)
        with _candidate(prepared, "other.txt", b"x\n") as outer:
            with _candidate(prepared, "dup1.txt", b"n\n") as nested:
                assert nested.root != outer.root
                assert not nested.root.is_relative_to(shm)
            assert outer.root.is_relative_to(shm)
    for index, provider in enumerate(
        (_CopyingWorkspaceProvider(tmp_path / "copying"), None)
    ):
        (tmp_path / f"b{index}").mkdir()
        with prepare_snapshot(
            _spec(repo, _scratch(tmp_path / f"b{index}"), commit, snapshot_policy=_policy(shm)),
            timeout=TIMEOUT,
            workspace=provider,
        ) as prepared:
            with _candidate(prepared, "other.txt", b"x\n") as snapshot:
                assert not snapshot.root.is_relative_to(shm)


def test_each_worker_thread_owns_its_tree(tmp_path: Path, shm: Path) -> None:
    repo, commit = _repo(tmp_path)
    roots: dict[int, set[Path]] = {}
    errors: list[BaseException] = []
    barrier = threading.Barrier(2)
    with _open(tmp_path, repo, commit, _policy(shm)) as prepared:
        prepared._ensure_template(isolation._git._P22Deadline(TIMEOUT))

        def work(index: int) -> None:
            try:
                for count in range(3):
                    with _candidate(prepared, "other.txt", b"%d%d\n" % (index, count)) as snap:
                        roots.setdefault(index, set()).add(snap.root)
                        barrier.wait(timeout=60) if count == 0 else None
            except BaseException as exc:  # pragma: no cover - reported below
                errors.append(exc)

        threads = [threading.Thread(target=work, args=(i,)) for i in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
    assert errors == []
    assert len(roots[0]) == len(roots[1]) == 1
    assert roots[0] != roots[1]


def test_unremovable_tmpfs_directory_is_reported(
    tmp_path: Path, shm: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    repo, commit = _repo(tmp_path)
    with _open(tmp_path, repo, commit, _policy(shm)) as prepared:
        with _candidate(prepared, "other.txt", b"x\n"):
            pass
        monkeypatch.setattr(isolation.shutil, "rmtree", lambda *a, **k: None)
        prepared._release_workers()
        monkeypatch.undo()
        monkeypatch.setattr(_tmpfs_worker, "_mount_type", lambda p, m: "tmpfs")
    assert "could not be removed" in capsys.readouterr().err


def test_release_after_a_failing_context_removes_the_trees(
    tmp_path: Path, shm: Path
) -> None:
    repo, commit = _repo(tmp_path)
    with pytest.raises(RuntimeError, match="stop"):
        with _open(tmp_path, repo, commit, _policy(shm)) as prepared:
            with _candidate(prepared, "other.txt", b"x\n"):
                pass
            assert len(_worker_dirs(shm)) == 1
            raise RuntimeError("stop")
    assert list(shm.iterdir()) == []


def test_dropping_one_workers_tree_leaves_the_other_workers_alone(
    tmp_path: Path, shm: Path
) -> None:
    repo, commit = _repo(tmp_path)
    with _open(tmp_path, repo, commit, _policy(shm)) as prepared:
        with _candidate(prepared, "other.txt", b"x\n") as mine:
            mine_root = mine.root

        def failing() -> None:
            with pytest.raises(RuntimeError):
                with _candidate(prepared, "other.txt", b"y\n"):
                    raise RuntimeError("worker failed")

        thread = threading.Thread(target=failing)
        thread.start()
        thread.join()
        with _candidate(prepared, "other.txt", b"z\n") as again:
            assert again.root == mine_root


def test_a_concurrent_refusal_is_announced_once(
    tmp_path: Path, shm: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    repo, commit = _repo(tmp_path)
    with _open(tmp_path, repo, commit, _policy(shm)) as prepared:

        def lose_the_race(template, deadline):
            prepared._tmpfs_off = True  # another worker refused first
            raise _tmpfs_worker.TmpfsUnavailable("raced")

        monkeypatch.setattr(prepared, "_build_worker", lose_the_race)
        with _candidate(prepared, "other.txt", b"x\n") as snapshot:
            assert not snapshot.root.is_relative_to(shm)
    assert "raced" not in capsys.readouterr().err
