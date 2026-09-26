"""Pure manifest and SnapshotSpec boundaries for B105 whole-source coverage."""

from __future__ import annotations

from pathlib import Path, PurePosixPath

import pytest

from assay.config import IsolationConfig
from assay.errors import AssayError
from assay.isolation import (
    SnapshotRepository,
    SnapshotSpec,
    _Entry,
    _Manifest,
    _manifest_sha256,
    _identity_excluded,
    netstring,
)


def _spec(base_repo, base_scratch, **overrides):
    values = {
        "repo_top": base_repo,
        "scratch_root": base_scratch,
        "commit": "a" * 40,
        "project_prefix": PurePosixPath("assay"),
        "snapshot_policy": IsolationConfig(
            snapshot_selection="repository",
            unsafe_symlink_omissions=(),
        ),
    }
    values.update(overrides)
    return SnapshotSpec(**values)


@pytest.mark.parametrize(
    "override",
    [
        {"limits": object()},
        {"snapshot_policy": object()},
        {"repo_top": Path("relative")},
        {"commit": "A" * 40},
        {"project_prefix": "assay"},
        {"project_prefix": PurePosixPath("/assay")},
        {"project_prefix": PurePosixPath("../assay")},
        {"resolved_base": "bad"},
    ],
)
def test_snapshot_spec_rejects_wrong_types_and_noncanonical_identities(
    tmp_path, override
):
    repo = tmp_path / "repo"
    repo.mkdir()
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    with pytest.raises(ValueError):
        _spec(repo, scratch, **override)


def test_snapshot_spec_rejects_symlink_and_in_tree_scratch_roots(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    scratch = repo / "scratch"
    scratch.mkdir()
    with pytest.raises(ValueError, match="outside repo_top"):
        _spec(repo, scratch)

    alias = tmp_path / "repo-alias"
    alias.symlink_to(repo, target_is_directory=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    with pytest.raises(ValueError, match="existing resolved non-symlink"):
        _spec(alias, outside)


def test_netstring_and_tree_identity_cover_legacy_filtered_and_omitted_entries():
    assert netstring("é") == "1:é"
    entry_a = _Entry(
        path=PurePosixPath("src/a.py"),
        mode="100644",
        oid="a" * 40,
        size=1,
    )
    entry_test = _Entry(
        path=PurePosixPath("tests/test_a.py"),
        mode="100644",
        oid="b" * 40,
        size=1,
        target="nul\0target",
    )
    manifest = _Manifest(
        entries=(entry_test, entry_a),
        directories=(PurePosixPath("."), PurePosixPath("src"), PurePosixPath("tests")),
        omitted=(PurePosixPath("vendor/link"), PurePosixPath("docs/link")),
    )

    legacy = _manifest_sha256(manifest)
    filtered = _manifest_sha256(manifest, identity_exclude=("tests/**",))
    explicit_empty = _manifest_sha256(manifest, identity_exclude=())
    assert len({legacy, filtered, explicit_empty}) == 3
    assert _manifest_sha256(manifest, identity_exclude=("tests/**", "src/**")) == (
        _manifest_sha256(manifest, identity_exclude=("src/**", "tests/**"))
    )
    assert _identity_excluded(PurePosixPath("tests/test_a.py"), ("tests/**",))
    assert not _identity_excluded(PurePosixPath("src/a.py"), ())


def test_prepared_repository_reads_manifest_identity_and_regular_blob(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    spec = _spec(repo, scratch)
    entry = _Entry(
        path=PurePosixPath("assay/src.py"),
        mode="100644",
        oid="b" * 40,
        size=3,
    )
    manifest = _Manifest(
        entries=(entry,),
        directories=(PurePosixPath("."), PurePosixPath("assay")),
        omitted=(),
    )
    prepared = SnapshotRepository(
        spec=spec,
        source=None,
        seed_git_dir=scratch / "seed.git",
        template=scratch / "template",
        manifest=manifest,
    )
    assert prepared.tree_sha256 == _manifest_sha256(manifest)
    assert prepared.tree_sha256_for_identity_exclude(()) == _manifest_sha256(
        manifest, identity_exclude=()
    )
    monkeypatch.setattr(prepared, "_read_blob", lambda oid, size, deadline: b"src")
    assert prepared.read_regular_file(PurePosixPath("assay/src.py"), timeout=1.0) == b"src"
    with pytest.raises(AssayError, match="not a regular tracked file"):
        prepared.read_regular_file(PurePosixPath("missing.py"), timeout=1.0)


def test_prepared_repository_checks_mutation_site_identity_before_materializing(
    tmp_path, monkeypatch
):
    repo = tmp_path / "repo"
    repo.mkdir()
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    spec = _spec(repo, scratch)
    path = PurePosixPath("assay/src.py")
    regular = _Entry(path=path, mode="100644", oid="b" * 40, size=3)
    manifest = _Manifest(
        entries=(regular,),
        directories=(PurePosixPath("."), PurePosixPath("assay")),
        omitted=(),
    )
    prepared = SnapshotRepository(
        spec=spec,
        source=None,
        seed_git_dir=scratch / "seed.git",
        template=scratch / "template",
        manifest=manifest,
    )
    with pytest.raises(AssayError, match="not tracked"):
        prepared._check_replacement_site(
            (PurePosixPath("assay/missing.py"), b"src", b"new"),
            None,
        )
    symlink_manifest = _Manifest(
        entries=(
            _Entry(path=path, mode="120000", oid="c" * 40, size=3, target="x"),
        ),
        directories=manifest.directories,
        omitted=(),
    )
    symlink_repo = SnapshotRepository(
        spec=spec,
        source=None,
        seed_git_dir=scratch / "seed.git",
        template=scratch / "template",
        manifest=symlink_manifest,
    )
    with pytest.raises(AssayError, match="not a regular file"):
        symlink_repo._check_replacement_site((path, b"src", b"new"), None)

    monkeypatch.setattr(prepared, "_read_blob", lambda oid, size, deadline: b"old")
    with pytest.raises(AssayError, match="site is stale"):
        prepared._check_replacement_site((path, b"src", b"new"), None)
    prepared._check_replacement_site((path, b"old", b"new"), None)


def test_prepared_repository_read_refuses_calls_after_context_closes(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    prepared = SnapshotRepository(
        spec=_spec(repo, scratch),
        source=None,
        seed_git_dir=scratch / "seed.git",
        template=scratch / "template",
        manifest=_Manifest(entries=(), directories=(PurePosixPath("."),), omitted=()),
    )
    prepared._closed = True
    with pytest.raises(RuntimeError, match="repository is closed"):
        prepared.read_regular_file(PurePosixPath("src.py"), timeout=1.0)
