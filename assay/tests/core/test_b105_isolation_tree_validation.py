"""Adversarial raw-tree validation controls for B105."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path, PurePosixPath

import pytest

from assay.config import IsolationConfig
from assay.errors import AssayError
from assay import isolation
from assay.isolation import (
    DEFAULT_SNAPSHOT_LIMITS,
    SnapshotSpec,
    _build_manifest,
)


ROOT = "a" * 40
BLOB = "b" * 40
CHILD_TREE = "c" * 40


def _entry(mode: str, name: bytes, oid: str) -> bytes:
    return mode.encode("ascii") + b" " + name + b"\0" + bytes.fromhex(oid)


def _build(
    monkeypatch,
    tmp_path,
    entries,
    *,
    metadata=None,
    objects=None,
    limits=DEFAULT_SNAPSHOT_LIMITS,
    project_prefix=PurePosixPath("."),
):
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    scratch = tmp_path / "scratch"
    scratch.mkdir(parents=True)
    spec = SnapshotSpec(
        repo_top=repo,
        scratch_root=scratch,
        commit="d" * 40,
        project_prefix=project_prefix,
        snapshot_policy=IsolationConfig(
            snapshot_selection="repository",
            unsafe_symlink_omissions=(),
        ),
        limits=limits,
    )
    raw_trees = {ROOT: b"".join(entries)}
    actual_objects = {ROOT: ("tree", raw_trees[ROOT])}
    actual_objects.update(objects or {})
    object_metadata = {ROOT: ("tree", len(raw_trees[ROOT]))}
    object_metadata.update(metadata or {})

    monkeypatch.setattr(isolation, "_resolve_declared_omission_modes", lambda *a, **k: None)
    monkeypatch.setattr(
        isolation._git,
        "_p22_git",
        lambda *a, **k: (ROOT + "\n").encode("ascii"),
    )

    def batch(_executable, *, oids, open_sink, close_sink=None, **_kwargs):
        for oid in dict.fromkeys(oids):
            kind, payload = actual_objects[oid]
            sink = open_sink(oid, kind, len(payload))
            if sink is not None:
                sink(payload)
            if close_sink is not None:
                close_sink(oid)

    monkeypatch.setattr(isolation, "_batch_objects", batch)
    return _build_manifest(
        Path("/usr/bin/git"),
        seed_git_dir=scratch / "seed.git",
        spec=spec,
        metadata=object_metadata,
        deadline=None,
    )


def test_manifest_builder_accepts_a_complete_regular_tree(monkeypatch, tmp_path):
    manifest = _build(
        monkeypatch,
        tmp_path,
        [_entry("100644", b"src.py", BLOB)],
        metadata={BLOB: ("blob", 3)},
        objects={BLOB: ("blob", b"src")},
    )
    assert [str(entry.path) for entry in manifest.entries] == ["src.py"]
    assert manifest.directories == (PurePosixPath("."),)


@pytest.mark.parametrize(
    ("entries", "metadata", "objects", "match"),
    [
        ([], {}, {ROOT: ("blob", b"not-tree")}, "where a tree was required"),
        (
            [_entry("40000", b"pkg", CHILD_TREE)],
            {CHILD_TREE: ("blob", 0)},
            {CHILD_TREE: ("tree", b"")},
            "not a tree",
        ),
        (
            [_entry("100644", b"src.py", BLOB)],
            {},
            {BLOB: ("blob", b"src")},
            "absent from the inventory",
        ),
        (
            [_entry("100644", b"src.py", BLOB)],
            {BLOB: ("tree", 3)},
            {BLOB: ("blob", b"src")},
            "not a blob",
        ),
        (
            [_entry("160000", b"vendor", BLOB)],
            {BLOB: ("commit", 0)},
            {BLOB: ("commit", b"")},
            "gitlink",
        ),
        (
            [_entry("100600", b"private", BLOB)],
            {BLOB: ("blob", 0)},
            {BLOB: ("blob", b"")},
            "unsupported mode",
        ),
        (
            [_entry("100644", b"\xff", BLOB)],
            {BLOB: ("blob", 0)},
            {BLOB: ("blob", b"")},
            "non-UTF-8 name",
        ),
        (
            [_entry("100644", b".git", BLOB)],
            {BLOB: ("blob", 0)},
            {BLOB: ("blob", b"")},
            "'.git' component",
        ),
        (
            [_entry("100644", b"same", BLOB), _entry("100644", b"same", BLOB)],
            {BLOB: ("blob", 0)},
            {BLOB: ("blob", b"")},
            "duplicate or colliding path",
        ),
    ],
)
def test_manifest_builder_refuses_untrusted_tree_metadata_and_names(
    monkeypatch, tmp_path, entries, metadata, objects, match
):
    with pytest.raises(AssayError, match=match):
        _build(
            monkeypatch,
            tmp_path,
            entries,
            metadata=metadata,
            objects=objects,
        )


def test_manifest_builder_enforces_entry_and_path_byte_limits(monkeypatch, tmp_path):
    two = [_entry("100644", b"a", BLOB), _entry("100644", b"b", CHILD_TREE)]
    with pytest.raises(AssayError, match="max_entries"):
        _build(
            monkeypatch,
            tmp_path / "entries",
            two,
            metadata={BLOB: ("blob", 0), CHILD_TREE: ("blob", 0)},
            objects={BLOB: ("blob", b""), CHILD_TREE: ("blob", b"")},
            limits=replace(DEFAULT_SNAPSHOT_LIMITS, max_entries=1),
        )

    with pytest.raises(AssayError, match="max_path_bytes"):
        _build(
            monkeypatch,
            tmp_path / "path",
            [_entry("100644", b"long-name", BLOB)],
            metadata={BLOB: ("blob", 0)},
            objects={BLOB: ("blob", b"")},
            limits=replace(DEFAULT_SNAPSHOT_LIMITS, max_path_bytes=3),
        )

    with pytest.raises(AssayError, match="max_total_path_bytes"):
        _build(
            monkeypatch,
            tmp_path / "total",
            [_entry("100644", b"a", BLOB), _entry("100644", b"b", CHILD_TREE)],
            metadata={BLOB: ("blob", 0), CHILD_TREE: ("blob", 0)},
            objects={BLOB: ("blob", b""), CHILD_TREE: ("blob", b"")},
            limits=replace(DEFAULT_SNAPSHOT_LIMITS, max_total_path_bytes=1),
        )


def test_manifest_builder_checks_symlink_object_type_and_target_encoding(
    monkeypatch, tmp_path
):
    symlink = _entry("120000", b"link", BLOB)
    with pytest.raises(AssayError, match="symlink object.*is a tree"):
        _build(
            monkeypatch,
            tmp_path / "kind",
            [symlink],
            metadata={BLOB: ("blob", 0)},
            objects={BLOB: ("tree", b"")},
        )

    with pytest.raises(AssayError, match="non-UTF-8 target"):
        _build(
            monkeypatch,
            tmp_path / "encoding",
            [symlink],
            metadata={BLOB: ("blob", 1)},
            objects={BLOB: ("blob", b"\xff")},
        )


def test_manifest_builder_requires_the_declared_project_prefix(monkeypatch, tmp_path):
    with pytest.raises(AssayError, match="does not name a tree"):
        _build(
            monkeypatch,
            tmp_path,
            [_entry("100644", b"src.py", BLOB)],
            metadata={BLOB: ("blob", 0)},
            objects={BLOB: ("blob", b"")},
            project_prefix=PurePosixPath("assay"),
        )
