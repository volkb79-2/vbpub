"""Adversarial tests for B105's isolation proof and cleanup boundaries."""

from __future__ import annotations

from pathlib import Path, PurePosixPath
from types import SimpleNamespace

import pytest

from assay.config import IsolationConfig
from assay.errors import AssayError
from assay.isolation import (
    SnapshotRepository,
    SnapshotSpec,
    _Entry,
    _Manifest,
    _batch_objects,
    _check_timeout,
    _closure_oids,
    _copy_objects,
    _decode,
    _normalize_path,
    _read_shallow,
    _write_worktree,
    prepare_snapshot,
)


COMMIT = "a" * 40
TREE = "b" * 40


def _spec(tmp_path, *, history="full", resolved_base=None, prefix=PurePosixPath(".")):
    repo = tmp_path / "repo"
    repo.mkdir()
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    return SnapshotSpec(
        repo_top=repo,
        scratch_root=scratch,
        commit=COMMIT,
        project_prefix=prefix,
        resolved_base=resolved_base,
        snapshot_policy=IsolationConfig(
            snapshot_selection="repository",
            unsafe_symlink_omissions=(),
            snapshot_history=history,
        ),
    )


def _repository(tmp_path, *, history="full", resolved_base=None, prefix=PurePosixPath(".")):
    spec = _spec(
        tmp_path,
        history=history,
        resolved_base=resolved_base,
        prefix=prefix,
    )
    git_dir = tmp_path / "private.git"
    git_dir.mkdir()
    (git_dir / "config").write_text("[core]\n\trepositoryformatversion = 0\n")
    source = SimpleNamespace(
        repo_top=spec.repo_top,
        common_dir=tmp_path / "source-common",
        git_executable=Path("/usr/bin/git"),
    )
    source.common_dir.mkdir()
    repository = SnapshotRepository(
        spec=spec,
        source=source,
        seed_git_dir=tmp_path / "seed.git",
        template=tmp_path / "template",
        manifest=_Manifest(entries=(), directories=(PurePosixPath("."),), omitted=()),
    )
    return repository, git_dir


def _verify_run(*, count=b"1\n", tree=TREE.encode() + b"\n"):
    def run(*args):
        if args == ("rev-parse", "HEAD"):
            return (COMMIT + "\n").encode()
        if args == ("rev-list", "--count", "HEAD"):
            return count
        if args == ("status", "--porcelain=v1", "-z"):
            return b""
        if args == ("write-tree",) or args == ("rev-parse", "HEAD^{tree}"):
            return tree
        if args == ("ls-files", "-v", "-z"):
            return b""
        raise AssertionError(args)

    return run


def test_read_blob_rejects_a_batch_payload_with_the_wrong_declared_size(tmp_path, monkeypatch):
    repo, _git_dir = _repository(tmp_path)

    def batch(_executable, *, oids, open_sink, **_kwargs):
        assert oids == ("c" * 40,)
        open_sink(oids[0], "blob", 4)(b"bad")

    monkeypatch.setattr("assay.isolation._batch_objects", batch)
    with pytest.raises(AssayError, match="read back as 3 bytes, expected 4"):
        repo._read_blob("c" * 40, 4, SimpleNamespace())


def test_verify_rejects_shallow_boundary_mismatch_before_other_checks(tmp_path):
    repo, git_dir = _repository(tmp_path)
    (git_dir / "shallow").write_text("c" * 40 + "\n")
    with pytest.raises(AssayError, match="shallow boundaries"):
        repo._verify(tmp_path / "snapshot", git_dir, COMMIT, _verify_run())


@pytest.mark.parametrize(
    ("count", "match"),
    [
        (b"one\n", "was not an integer"),
        (b"0\n", "returned no commits"),
    ],
)
def test_verify_rejects_invalid_or_empty_visible_commit_count(tmp_path, count, match):
    repo, git_dir = _repository(tmp_path)
    with pytest.raises(AssayError, match=match):
        repo._verify(
            tmp_path / "snapshot",
            git_dir,
            COMMIT,
            _verify_run(count=count),
        )


def test_verify_checks_expected_shallow_visible_commit_count(tmp_path):
    repo, git_dir = _repository(tmp_path, history="shallow")
    (git_dir / "shallow").write_text(COMMIT + "\n")
    with pytest.raises(AssayError, match="expected 1 for a shallow materialization"):
        repo._verify(
            tmp_path / "snapshot",
            git_dir,
            COMMIT,
            _verify_run(count=b"2\n"),
        )


def test_verify_rejects_a_missing_declared_project_prefix(tmp_path):
    repo, git_dir = _repository(tmp_path, prefix=PurePosixPath("assay"))
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()
    with pytest.raises(AssayError, match="project prefix assay is not a directory"):
        repo._verify(snapshot, git_dir, COMMIT, _verify_run())


@pytest.mark.parametrize(
    "contents",
    [b"\xff\n", b"not-an-oid\n", (("c" * 40 + "\n") * 2).encode("ascii")],
)
def test_read_shallow_rejects_unreadable_or_noncanonical_lists(tmp_path, contents):
    (tmp_path / "shallow").write_bytes(contents)
    with pytest.raises(AssayError, match="shallow file"):
        _read_shallow(tmp_path)


def test_read_shallow_rejects_a_directory_instead_of_a_file(tmp_path):
    (tmp_path / "shallow").mkdir()
    with pytest.raises(AssayError, match="unreadable"):
        _read_shallow(tmp_path)


@pytest.mark.parametrize("value", [True, "1", 0, -1, float("nan"), float("-inf")])
def test_snapshot_timeout_requires_a_positive_number_or_positive_infinity(value):
    with pytest.raises(ValueError, match="timeout must be"):
        _check_timeout(value)


def test_snapshot_timeout_accepts_finite_positive_and_unbounded_values():
    _check_timeout(0.001)
    _check_timeout(float("inf"))


def test_snapshot_git_output_and_paths_reject_wrong_encodings_and_types():
    with pytest.raises(AssayError, match="not valid UTF-8"):
        _decode(b"\xff")
    with pytest.raises(ValueError, match="path must be PurePosixPath"):
        _normalize_path("src/a.py")
    with pytest.raises(ValueError, match="normalized repo-relative"):
        _normalize_path(PurePosixPath("../escape.py"))
    with pytest.raises(ValueError, match="normalized repo-relative"):
        _normalize_path(PurePosixPath("."))


def test_batch_objects_returns_without_spawning_git_for_empty_inventory(monkeypatch):
    def unexpected(*_args, **_kwargs):
        raise AssertionError("empty closure must not start cat-file")

    monkeypatch.setattr("assay.isolation._git._p22_git", unexpected)
    _batch_objects(
        Path("/usr/bin/git"),
        git_dir=Path("/repo/.git"),
        oids=(),
        deadline=SimpleNamespace(),
        open_sink=lambda *_args: None,
    )


def test_batch_objects_deduplicates_object_names_before_git_request(monkeypatch):
    observed = {}
    chunks = []

    def fake_git(_executable, *, stdin_bytes, on_stdout, **_kwargs):
        observed["stdin"] = stdin_bytes
        oid = "c" * 40
        on_stdout((oid + " blob 3\nabc\n").encode())
        return b""

    monkeypatch.setattr("assay.isolation._git._p22_git", fake_git)
    _batch_objects(
        Path("/usr/bin/git"),
        git_dir=Path("/repo/.git"),
        oids=("c" * 40, "c" * 40),
        deadline=SimpleNamespace(),
        open_sink=lambda oid, kind, size: chunks.append((oid, kind, size)) or bytearray().extend,
    )
    assert observed["stdin"] == ("c" * 40 + "\n").encode("ascii")
    assert chunks == [("c" * 40, "blob", 3)]


def test_closure_oid_inventory_ignores_repeated_git_records(monkeypatch, tmp_path):
    oid = "c" * 40
    monkeypatch.setattr(
        "assay.isolation._git._p22_git",
        lambda *_args, **_kwargs: f"{oid}\n{oid}\n".encode("ascii"),
    )
    assert _closure_oids(
        Path("/usr/bin/git"),
        git_dir=tmp_path / "repo.git",
        work_tree=None,
        cwd=tmp_path,
        commit=COMMIT,
        deadline=SimpleNamespace(),
        max_objects=2,
    ) == (oid,)


def test_copy_objects_copies_only_regular_pack_files(tmp_path):
    source = tmp_path / "source.git"
    pack = source / "objects/pack"
    pack.mkdir(parents=True)
    (pack / "pack-1.pack").write_bytes(b"pack")
    (pack / "nested").mkdir()
    external = tmp_path / "outside.pack"
    external.write_bytes(b"external")
    (pack / "pack-link.pack").symlink_to(external)

    destination = tmp_path / "destination.git"
    _copy_objects(source, destination)
    copied = destination / "objects/pack"
    assert sorted(path.name for path in copied.iterdir()) == ["pack-1.pack"]
    assert (copied / "pack-1.pack").read_bytes() == b"pack"


def test_build_refuses_a_commit_tree_response_that_is_not_an_oid(tmp_path, monkeypatch):
    import assay.isolation as isolation

    repository, _git_dir = _repository(tmp_path)
    entry = _Entry(
        path=PurePosixPath("assay/src.py"),
        mode="100644",
        oid="b" * 40,
        size=3,
    )
    repository._manifest = _Manifest(
        entries=(entry,),
        directories=(PurePosixPath("."), PurePosixPath("assay")),
        omitted=(),
    )
    monkeypatch.setattr(isolation._git, "_p22_init_private", lambda *_a, **_k: None)
    monkeypatch.setattr(isolation, "_copy_objects", lambda *_a, **_k: None)
    monkeypatch.setattr(isolation, "_write_shallow", lambda *_a, **_k: None)

    def fake_git(_executable, *, args, **_kwargs):
        if args[0] == "hash-object":
            return ("c" * 40 + "\n").encode("ascii")
        if args[0] == "write-tree":
            return b"tree\n"
        if args[0] == "commit-tree":
            return b"not-an-oid\n"
        return b""

    monkeypatch.setattr(isolation._git, "_p22_git", fake_git)
    with pytest.raises(AssayError, match="commit-tree returned.*not a full OID"):
        repository._build(
            tmp_path / "snapshot",
            (PurePosixPath("assay/src.py"), b"old", b"new"),
            SimpleNamespace(),
        )


def test_write_worktree_rejects_a_tree_object_for_a_regular_entry(tmp_path, monkeypatch):
    root = tmp_path / "worktree"
    root.mkdir()
    entry = _Entry(
        path=PurePosixPath("src/a.py"),
        mode="100644",
        oid="c" * 40,
        size=1,
    )

    def batch(_executable, *, oids, open_sink, **_kwargs):
        open_sink(oids[0], "tree", 0)

    monkeypatch.setattr("assay.isolation._batch_objects", batch)
    with pytest.raises(AssayError, match="where a blob was required"):
        _write_worktree(
            Path("/usr/bin/git"),
            git_dir=tmp_path / "seed.git",
            root=root,
            entries=(entry,),
            directories=(PurePosixPath("."), PurePosixPath("src")),
            deadline=SimpleNamespace(),
        )


def test_write_worktree_closes_an_open_blob_stream_when_batch_fails(tmp_path, monkeypatch):
    root = tmp_path / "worktree"
    root.mkdir()
    entry = _Entry(
        path=PurePosixPath("src/a.py"),
        mode="100644",
        oid="c" * 40,
        size=3,
    )
    opened = []

    def batch(_executable, *, oids, open_sink, **_kwargs):
        opened.append(open_sink(oids[0], "blob", 3))
        opened[0](b"abc")
        raise RuntimeError("simulated pipe failure")

    monkeypatch.setattr("assay.isolation._batch_objects", batch)
    with pytest.raises(RuntimeError, match="simulated pipe failure"):
        _write_worktree(
            Path("/usr/bin/git"),
            git_dir=tmp_path / "seed.git",
            root=root,
            entries=(entry,),
            directories=(PurePosixPath("."), PurePosixPath("src")),
            deadline=SimpleNamespace(),
        )
    assert (root / "src/a.py").read_bytes() == b"abc"
    # The handle is inaccessible through the returned bound method, so the
    # follow-up rename/delete proves the finally block released it on Windows
    # too; on POSIX it also proves the written file is complete.
    (root / "src/a.py").rename(root / "src/renamed.py")
    (root / "src/renamed.py").unlink()


def _patch_prepare_dependencies(monkeypatch, tmp_path, *, seed_stream=None):
    """Make prepare_snapshot reach post-transfer checks with deterministic Git facts."""
    import assay.isolation as isolation

    spec = _spec(tmp_path)
    source = SimpleNamespace(
        repo_top=spec.repo_top,
        git_dir=tmp_path / "source.git",
        common_dir=tmp_path / "source-common",
        git_executable=Path("/usr/bin/git"),
    )
    source.common_dir.mkdir()
    monkeypatch.setattr(isolation._git, "_p22_open_source", lambda *_args: source)
    monkeypatch.setattr(
        isolation._git,
        "_p22_git",
        lambda *_args, **_kwargs: (COMMIT + "\n").encode("ascii"),
    )

    def init_private(_executable, *, path, **_kwargs):
        path.mkdir()
        (path / "objects/info").mkdir(parents=True)
        (path / "config").write_text("[core]\n\trepositoryformatversion = 0\n")

    def stream_pack(_executable, *, seed_git_dir, **kwargs):
        if seed_stream is not None:
            seed_stream(seed_git_dir)

    monkeypatch.setattr(isolation._git, "_p22_init_private", init_private)
    monkeypatch.setattr(isolation._git, "_p22_stream_pack", stream_pack)
    return spec


def test_prepare_snapshot_rejects_nonexact_written_shallow_boundaries_and_cleans(
    tmp_path, monkeypatch
):
    import assay.isolation as isolation

    spec = _patch_prepare_dependencies(monkeypatch, tmp_path)
    monkeypatch.setattr(isolation, "_closure_oids", lambda *_args, **_kwargs: ("c" * 40,))
    monkeypatch.setattr(
        isolation,
        "_object_metadata",
        lambda *_args, **_kwargs: {"c" * 40: ("commit", 0)},
    )
    monkeypatch.setattr(
        isolation,
        "_read_shallow",
        lambda _git_dir: frozenset({"d" * 40}),
    )

    with pytest.raises(AssayError, match="seed's shallow boundaries are not exact"):
        with prepare_snapshot(spec, timeout=1.0):
            pytest.fail("the invalid seed must not be yielded")
    assert list(spec.scratch_root.iterdir()) == []


def test_prepare_snapshot_rejects_seed_object_closure_drift_and_cleans(
    tmp_path, monkeypatch
):
    import assay.isolation as isolation

    spec = _patch_prepare_dependencies(monkeypatch, tmp_path)
    closures = iter((("c" * 40,), ("d" * 40,)))
    monkeypatch.setattr(
        isolation,
        "_closure_oids",
        lambda *_args, **_kwargs: next(closures),
    )
    monkeypatch.setattr(
        isolation,
        "_object_metadata",
        lambda *_args, **_kwargs: {"c" * 40: ("commit", 0)},
    )

    with pytest.raises(AssayError, match="not the 1 inventoried at the source"):
        with prepare_snapshot(spec, timeout=1.0):
            pytest.fail("the drifted seed must not be yielded")
    assert list(spec.scratch_root.iterdir()) == []


def test_prepare_snapshot_rejects_seed_metadata_drift_and_cleans(tmp_path, monkeypatch):
    import assay.isolation as isolation

    spec = _patch_prepare_dependencies(monkeypatch, tmp_path)
    monkeypatch.setattr(isolation, "_closure_oids", lambda *_args, **_kwargs: ("c" * 40,))
    metadata = iter(
        (
            {"c" * 40: ("commit", 0)},
            {"c" * 40: ("blob", 0)},
        )
    )
    monkeypatch.setattr(
        isolation,
        "_object_metadata",
        lambda *_args, **_kwargs: next(metadata),
    )

    with pytest.raises(AssayError, match="types/sizes differ"):
        with prepare_snapshot(spec, timeout=1.0):
            pytest.fail("the drifted seed must not be yielded")
    assert list(spec.scratch_root.iterdir()) == []


def test_prepare_snapshot_rejects_seed_alternates_and_cleans(tmp_path, monkeypatch):
    import assay.isolation as isolation

    def add_alternates(seed_git_dir):
        (seed_git_dir / "objects/info/alternates").write_text("/outside/objects\n")

    spec = _patch_prepare_dependencies(monkeypatch, tmp_path, seed_stream=add_alternates)
    monkeypatch.setattr(isolation, "_closure_oids", lambda *_args, **_kwargs: ("c" * 40,))
    monkeypatch.setattr(
        isolation,
        "_object_metadata",
        lambda *_args, **_kwargs: {"c" * 40: ("commit", 0)},
    )

    with pytest.raises(AssayError, match="seed depends on an alternate object store"):
        with prepare_snapshot(spec, timeout=1.0):
            pytest.fail("the alternate-backed seed must not be yielded")
    assert list(spec.scratch_root.iterdir()) == []


def test_prepare_snapshot_discards_live_materialization_when_caller_fails(
    tmp_path, monkeypatch
):
    import assay.isolation as isolation

    spec = _patch_prepare_dependencies(monkeypatch, tmp_path)
    monkeypatch.setattr(isolation, "_closure_oids", lambda *_args, **_kwargs: ("c" * 40,))
    monkeypatch.setattr(
        isolation,
        "_object_metadata",
        lambda *_args, **_kwargs: {"c" * 40: ("commit", 0)},
    )
    monkeypatch.setattr(
        isolation,
        "_build_manifest",
        lambda *_args, **_kwargs: _Manifest(
            entries=(), directories=(PurePosixPath("."),), omitted=()
        ),
    )

    with pytest.raises(RuntimeError, match="caller failure"):
        with prepare_snapshot(spec, timeout=1.0) as repository:
            leaked = spec.scratch_root / "leaked-snapshot"
            leaked.mkdir()
            repository._track(leaked)
            raise RuntimeError("caller failure")
    assert list(spec.scratch_root.iterdir()) == []
