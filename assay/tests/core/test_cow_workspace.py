"""CoW workspace lifecycle and refusal mapping with a controlled cowtree API."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import shutil
from types import SimpleNamespace

import pytest

from assay import _cow_workspace
from assay.errors import AssayError, Outcome, ReasonCode
from assay.workspace import WorkspaceCleanupError, WorkspaceEstimate


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
    def __init__(self, root: Path, *, caps=None, clone_error=None, exit_error=None):
        self.exit_error = exit_error
        self.path = root / "run"
        self.path.mkdir()
        self.trees = self.path / "trees"
        self.trees.mkdir()
        self.caps = caps or SimpleNamespace(
            backend="btrfs", fs_type="btrfs", cheap_clone=True
        )
        self.clone_error = clone_error
        self.clones = []
        self.tree_clones = []
        self.seal_calls = []
        self.closed = False
        self._number = 0
        self.reservations = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.closed = True
        if self.exit_error is not None:
            raise self.exit_error
        return False

    def ingest_tree(self, source, name):
        destination = self.trees / name
        shutil.copytree(source, destination)
        return destination

    def make_tree(self, name):
        destination = self.trees / name
        destination.mkdir()
        return destination

    def clone_tree(self, source, name):
        destination = self.trees / name
        shutil.copytree(source, destination)
        self.tree_clones.append((Path(source), destination))
        return destination

    def seal(self, path):
        self.seal_calls.append(Path(path))

    def clone_file(self, source, destination):
        if self.clone_error is not None:
            raise self.clone_error("clone failed")
        shutil.copyfile(source, destination)
        self.clones.append((Path(source), Path(destination)))

    def delete_tree(self, path):
        shutil.rmtree(path)

    def reserve(self, estimate):
        self.reservations.append(estimate)


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


def _area(*, backend="btrfs", fs_type="btrfs", runs_dir="/cow/runs"):
    return SimpleNamespace(
        caps=SimpleNamespace(backend=backend, fs_type=fs_type), runs_dir=runs_dir
    )


class FakeGc:
    """Stands in for the `cowtree.gc` module; records the scanned dirs."""

    def __init__(self, items=(), error=None):
        self.items = list(items)
        self.error = error
        self.scanned = []

    def gc(self, scan_dir):
        self.scanned.append(scan_dir)
        if self.error is not None:
            raise self.error("gc failed")
        return self.items


def _install(monkeypatch, cowtree, gc=None):
    monkeypatch.setenv("MDT_COW_ROOT", "/cow")
    monkeypatch.delenv("COWTREE_MODE", raising=False)
    monkeypatch.setattr(_cow_workspace, "_load_cowtree", lambda: cowtree)
    fake_gc = gc if gc is not None else FakeGc()
    monkeypatch.setattr(_cow_workspace, "_load_cowtree_gc", lambda: fake_gc)
    return fake_gc


def test_cowtree_gc_import_names_the_gc_module(monkeypatch):
    module = object()
    calls = []
    monkeypatch.setattr(
        _cow_workspace.importlib,
        "import_module",
        lambda name: calls.append(name) or module,
    )

    assert _cow_workspace._load_cowtree_gc() is module
    assert calls == ["cowtree.gc"]


def test_unconfigured_cow_root_never_imports_cowtree(tmp_path, monkeypatch):
    # B196: no MDT_COW_ROOT means no area; a missing cowtree must not matter.
    monkeypatch.delenv("MDT_COW_ROOT", raising=False)
    monkeypatch.delenv("COWTREE_MODE", raising=False)

    def fail_import():
        raise ImportError("cowtree is not installed")

    monkeypatch.setattr(_cow_workspace, "_load_cowtree", fail_import)

    with _cow_workspace.open_workspace(
        WorkspaceEstimate(1, 1, 2), scratch_root=tmp_path
    ) as selected:
        assert selected is None


def test_cowtree_mode_off_without_a_root_never_imports_cowtree(tmp_path, monkeypatch):
    monkeypatch.delenv("MDT_COW_ROOT", raising=False)
    monkeypatch.setenv("COWTREE_MODE", "off")
    monkeypatch.setattr(
        _cow_workspace, "_load_cowtree", lambda: pytest.fail("must not import")
    )

    with _cow_workspace.open_workspace(
        WorkspaceEstimate(1, 1, 2), scratch_root=tmp_path
    ) as selected:
        assert selected is None


def test_an_invalid_cowtree_mode_still_reaches_cowtree(tmp_path, monkeypatch):
    cowtree = FakeCowtree(detect_error=FakeRootInvalid)
    _install(monkeypatch, cowtree)
    monkeypatch.delenv("MDT_COW_ROOT", raising=False)
    monkeypatch.setenv("COWTREE_MODE", "bogus")

    with pytest.raises(AssayError, match="detect failed"):
        with _cow_workspace.open_workspace(
            WorkspaceEstimate(1, 1, 2), scratch_root=tmp_path
        ):
            pytest.fail("an invalid mode must be refused by cowtree")


def test_dead_runs_are_collected_before_the_run_opens(tmp_path, monkeypatch, capsys):
    # B197: a SIGKILLed earlier run left its directory; gc runs first.
    run = FakeRun(tmp_path)
    order = []
    cowtree = FakeCowtree(area=_area(runs_dir="/cow/runs"), run=run)
    real_open = cowtree.open_run

    def recording_open(*args, **kwargs):
        order.append("open_run")
        return real_open(*args, **kwargs)

    cowtree.open_run = recording_open
    gc = _install(
        monkeypatch,
        cowtree,
        FakeGc([SimpleNamespace(status="deleted"), SimpleNamespace(status="owner-live")]),
    )
    original_gc = gc.gc
    gc.gc = lambda scan_dir: order.append("gc") or original_gc(scan_dir)

    with _cow_workspace.open_workspace(WorkspaceEstimate(1, 1, 2), scratch_root=tmp_path):
        pass

    assert gc.scanned == ["/cow/runs"]
    assert order == ["gc", "open_run"]
    assert "collected 1 dead copy-on-write run(s)" in capsys.readouterr().err


def test_a_failing_dead_run_collection_is_an_advisory_not_a_refusal(
    tmp_path, monkeypatch, capsys
):
    run = FakeRun(tmp_path)
    _install(
        monkeypatch,
        FakeCowtree(area=_area(), run=run),
        FakeGc(error=FakeCowError),
    )

    with _cow_workspace.open_workspace(
        WorkspaceEstimate(1, 1, 2), scratch_root=tmp_path
    ) as selected:
        assert selected is not None

    assert "dead copy-on-write run collection failed: gc failed" in capsys.readouterr().err


def test_a_non_btrfs_area_is_not_collected(tmp_path, monkeypatch):
    gc = _install(monkeypatch, FakeCowtree(area=_area(backend="reflink", fs_type="xfs")))

    with _cow_workspace.open_workspace(
        WorkspaceEstimate(1, 1, 2), scratch_root=tmp_path
    ) as selected:
        assert selected is None

    assert gc.scanned == []


def test_a_close_failure_after_the_body_is_a_cleanup_error(tmp_path, monkeypatch):
    # B199: Run.close failing AFTER the measurement completed is a distinct,
    # advisory-able error; the same failure DURING the body stays a refusal.
    run = FakeRun(
        tmp_path, exit_error=FakeCowError("owner.lock could not be removed")
    )
    _install(monkeypatch, FakeCowtree(area=_area(), run=run))

    with pytest.raises(WorkspaceCleanupError) as caught:
        with _cow_workspace.open_workspace(
            WorkspaceEstimate(1, 1, 2), scratch_root=tmp_path
        ):
            pass

    assert caught.value.reason_code is ReasonCode.EXEC_FAILED
    assert "cleanup failed: owner.lock could not be removed" in str(caught.value)


def test_a_body_failure_is_not_reported_as_a_cleanup_error(tmp_path, monkeypatch):
    run = FakeRun(tmp_path, clone_error=FakeCowError)
    _install(monkeypatch, FakeCowtree(area=_area(), run=run))
    source = tmp_path / "source"
    source.write_bytes(b"x")

    with pytest.raises(AssayError) as caught:
        with _cow_workspace.open_workspace(
            WorkspaceEstimate(1, 1, 2), scratch_root=tmp_path
        ) as selected:
            selected.clone_file(source, tmp_path / "dest")

    assert not isinstance(caught.value, WorkspaceCleanupError)


def test_cowtree_import_is_lazy_and_names_the_embedded_package(monkeypatch):
    module = object()
    calls = []
    monkeypatch.setattr(
        _cow_workspace.importlib,
        "import_module",
        lambda name: calls.append(name) or module,
    )

    assert _cow_workspace._load_cowtree() is module
    assert calls == ["cowtree"]


def test_no_detected_area_yields_plain_behavior_without_opening_run(tmp_path, monkeypatch):
    cowtree = FakeCowtree(area=None)
    _install(monkeypatch, cowtree)

    with _cow_workspace.open_workspace(
        WorkspaceEstimate(10, 20, 2), scratch_root=tmp_path
    ) as selected:
        assert selected is None

    assert cowtree.detect_calls
    assert cowtree.open_calls == []


@pytest.mark.parametrize(
    ("backend", "fs_type"),
    [("reflink", "xfs"), ("reflink", "btrfs"), ("btrfs", "xfs")],
)
def test_a_non_btrfs_area_falls_back_to_plain_with_one_advisory(
    tmp_path, monkeypatch, capsys, backend, fs_type
):
    # An auto-detected optimization that does not apply must not turn every
    # snapshot lane into ERROR; the plain path is the 10.1.0 behaviour.
    cowtree = FakeCowtree(area=_area(backend=backend, fs_type=fs_type))
    _install(monkeypatch, cowtree)

    with _cow_workspace.open_workspace(
        WorkspaceEstimate(10, 20, 2), scratch_root=tmp_path
    ) as selected:
        assert selected is None

    assert cowtree.open_calls == []
    advisories = [
        line
        for line in capsys.readouterr().err.splitlines()
        if line.startswith("Assay advisory: copy-on-write area")
    ]
    assert advisories == [
        f"Assay advisory: copy-on-write area uses the {backend} backend on "
        f"{fs_type}; Assay snapshot clones need btrfs, using plain snapshots"
    ]


def test_active_workspace_forwards_estimate_and_run_operations(tmp_path, monkeypatch):
    run = FakeRun(tmp_path)
    cowtree = FakeCowtree(area=_area(), run=run)
    _install(monkeypatch, cowtree)
    estimate = WorkspaceEstimate(101, 202, 3)
    source = tmp_path / "input"
    source.mkdir()
    (source / "seed.pack").write_bytes(b"seed pack")
    clone_source = run.trees / "seed" / "seed.pack"

    with _cow_workspace.open_workspace(
        estimate, scratch_root=tmp_path
    ) as selected:
        ingested = selected.ingest_tree(source, "seed")
        snapshot = selected.make_tree()
        clone_destination = snapshot / "pack"
        selected.clone_file(clone_source, clone_destination)
        cloned_bytes = clone_destination.read_bytes()
        cloned_tree = selected.clone_tree(ingested)
        selected.seal(cloned_tree)
        selected.delete_tree(snapshot)

    assert cowtree.open_calls == [
        ("assay", FakeSpaceEstimate(101, 202, 0), cowtree.detect_calls[0], str(tmp_path))
    ]
    assert run.reservations == [FakeSpaceEstimate(0, 202, 3)]
    assert Path(ingested) == run.trees / "seed"
    assert run.clones == [(clone_source, clone_destination)]
    assert selected.cheap_clone is True
    assert run.tree_clones == [(ingested, cloned_tree)]
    assert run.seal_calls == [cloned_tree]
    assert cloned_bytes == b"seed pack"
    assert run.closed


def test_workspace_refuses_if_open_run_loses_btrfs_backend(tmp_path, monkeypatch):
    run = FakeRun(tmp_path, caps=SimpleNamespace(backend="copy", fs_type="btrfs"))
    cowtree = FakeCowtree(area=_area(), run=run)
    _install(monkeypatch, cowtree)

    with pytest.raises(AssayError, match="btrfs filesystem with the btrfs backend"):
        with _cow_workspace.open_workspace(
            WorkspaceEstimate(1, 1, 2), scratch_root=tmp_path
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
            WorkspaceEstimate(1, 1, 2), scratch_root=tmp_path
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
            WorkspaceEstimate(1, 1, 2), scratch_root=tmp_path
        ) as selected:
            selected.clone_file(source, destination / "pack")

    assert caught.value.outcome is Outcome.ERROR
    assert caught.value.reason_code is ReasonCode.EXEC_FAILED
    assert "clone failed" in str(caught.value)
