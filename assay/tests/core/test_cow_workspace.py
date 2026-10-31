"""CoW workspace lifecycle and refusal mapping with a controlled cowtree API."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
import shutil
from types import SimpleNamespace

import pytest

from assay import _cow_workspace
from assay.errors import AssayError, Outcome, ReasonCode
from assay.workspace import WorkspaceEstimate


class FakeCowError(Exception):
    pass


class FakeRootInvalid(FakeCowError):
    pass


@dataclass(frozen=True)
class FakeSpaceEstimate:
    ingest_bytes: int
    per_clone_bytes: int
    max_live_clones: int


class FakeRun:
    def __init__(self, root: Path, *, caps=None, clone_error=None):
        self.path = root / "run"
        self.path.mkdir()
        self.trees = self.path / "trees"
        self.trees.mkdir()
        self.caps = caps or SimpleNamespace(backend="btrfs", fs_type="btrfs")
        self.clone_error = clone_error
        self.clones = []
        self.closed = False
        self._number = 0

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.closed = True
        return False

    def ingest_tree(self, source, name):
        destination = self.trees / name
        shutil.copytree(source, destination)
        return destination

    def make_tree(self, name):
        destination = self.trees / name
        destination.mkdir()
        return destination

    def clone_file(self, source, destination):
        if self.clone_error is not None:
            raise self.clone_error("clone failed")
        shutil.copyfile(source, destination)
        self.clones.append((Path(source), Path(destination)))

    def delete_tree(self, path):
        shutil.rmtree(path)


class FakeCowtree:
    CowError = FakeCowError
    RootInvalid = FakeRootInvalid
    SpaceEstimate = FakeSpaceEstimate

    def __init__(self, *, area=None, run=None, detect_error=None, open_error=None):
        self.area = area
        self.run = run
        self.detect_error = detect_error
        self.open_error = open_error
        self.detect_calls = []
        self.open_calls = []

    def detect(self, *, environ):
        self.detect_calls.append(dict(environ))
        if self.detect_error is not None:
            raise self.detect_error("detect failed")
        return self.area

    def open_run(self, tool, *, estimate, environ, plain_dir):
        self.open_calls.append((tool, estimate, dict(environ), plain_dir))
        if self.open_error is not None:
            raise self.open_error("open failed")
        return self.run


def _area(*, backend="btrfs", fs_type="btrfs"):
    return SimpleNamespace(caps=SimpleNamespace(backend=backend, fs_type=fs_type))


def _install(monkeypatch, cowtree):
    monkeypatch.setattr(_cow_workspace, "_load_cowtree", lambda: cowtree)


def test_no_detected_area_yields_plain_behavior_without_opening_run(tmp_path, monkeypatch):
    cowtree = FakeCowtree(area=None)
    _install(monkeypatch, cowtree)

    with _cow_workspace.open_workspace(
        WorkspaceEstimate(10, 20, 2), scratch_root=tmp_path, max_live_clones=2
    ) as selected:
        assert selected is None

    assert cowtree.detect_calls
    assert cowtree.open_calls == []


@pytest.mark.parametrize(
    ("backend", "fs_type"),
    [("reflink", "xfs"), ("reflink", "btrfs"), ("btrfs", "xfs")],
)
def test_detected_area_requires_btrfs_ficlone_backend(
    tmp_path, monkeypatch, backend, fs_type
):
    cowtree = FakeCowtree(area=_area(backend=backend, fs_type=fs_type))
    _install(monkeypatch, cowtree)

    with pytest.raises(AssayError) as caught:
        with _cow_workspace.open_workspace(
            WorkspaceEstimate(10, 20, 2), scratch_root=tmp_path, max_live_clones=2
        ):
            pytest.fail("an unsupported CoW area must be refused")

    assert caught.value.outcome is Outcome.ERROR
    assert caught.value.reason_code is ReasonCode.EXEC_FAILED
    assert "requires a btrfs filesystem with the btrfs backend" in str(caught.value)
    assert cowtree.open_calls == []


def test_active_workspace_forwards_estimate_and_run_operations(tmp_path, monkeypatch):
    run = FakeRun(tmp_path)
    cowtree = FakeCowtree(area=_area(), run=run)
    _install(monkeypatch, cowtree)
    estimate = WorkspaceEstimate(101, 202, 3)
    source = tmp_path / "input"
    source.mkdir()
    (source / "seed.pack").write_bytes(b"seed pack")
    clone_source = run.trees / "seed" / "seed.pack"
    clone_destination = run.trees / "snapshot" / "pack"

    with _cow_workspace.open_workspace(
        estimate, scratch_root=tmp_path, max_live_clones=3
    ) as selected:
        ingested = selected.ingest_tree(source, "seed")
        snapshot = selected.make_tree("snapshot")
        selected.clone_file(clone_source, clone_destination)
        selected.delete_tree(snapshot)

    assert cowtree.open_calls == [
        ("assay", FakeSpaceEstimate(101, 202, 3), cowtree.detect_calls[0], str(tmp_path))
    ]
    assert Path(ingested) == run.trees / "seed"
    assert run.clones == [(clone_source, clone_destination)]
    assert clone_destination.read_bytes() == b"seed pack"
    assert run.closed


def test_workspace_refuses_if_open_run_loses_btrfs_backend(tmp_path, monkeypatch):
    run = FakeRun(tmp_path, caps=SimpleNamespace(backend="copy", fs_type="btrfs"))
    cowtree = FakeCowtree(area=_area(), run=run)
    _install(monkeypatch, cowtree)

    with pytest.raises(AssayError, match="requires a btrfs filesystem with the btrfs backend"):
        with _cow_workspace.open_workspace(
            WorkspaceEstimate(1, 1, 2), scratch_root=tmp_path, max_live_clones=2
        ):
            pytest.fail("a changed backend must be refused")

    assert run.closed


@pytest.mark.parametrize("failure", ["detect", "open"])
def test_cow_errors_become_exec_failed_assay_errors(tmp_path, monkeypatch, failure):
    cowtree = FakeCowtree(
        area=_area(),
        run=FakeRun(tmp_path),
        detect_error=FakeCowError if failure == "detect" else None,
        open_error=FakeCowError if failure == "open" else None,
    )
    _install(monkeypatch, cowtree)

    with pytest.raises(AssayError) as caught:
        with _cow_workspace.open_workspace(
            WorkspaceEstimate(1, 1, 2), scratch_root=tmp_path, max_live_clones=2
        ):
            pytest.fail("cowtree errors must be mapped")

    assert caught.value.outcome is Outcome.ERROR
    assert caught.value.reason_code is ReasonCode.EXEC_FAILED
    assert "CowError" not in str(caught.value)
    assert "failed" in str(caught.value)


def test_cow_errors_from_reflink_operation_become_exec_failed(tmp_path, monkeypatch):
    run = FakeRun(tmp_path, clone_error=FakeCowError)
    cowtree = FakeCowtree(area=_area(), run=run)
    _install(monkeypatch, cowtree)
    source = tmp_path / "source"
    source.write_bytes(b"source")
    destination = run.trees / "snapshot"
    destination.mkdir()

    with pytest.raises(AssayError) as caught:
        with _cow_workspace.open_workspace(
            WorkspaceEstimate(1, 1, 2), scratch_root=tmp_path, max_live_clones=2
        ) as selected:
            selected.clone_file(source, destination / "pack")

    assert caught.value.outcome is Outcome.ERROR
    assert caught.value.reason_code is ReasonCode.EXEC_FAILED
    assert "clone failed" in str(caught.value)
