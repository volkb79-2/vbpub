"""The Assay workspace seam stays independent of cowtree's implementation."""

from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from assay import workspace


@pytest.mark.parametrize(
    "values",
    [
        (-1, 0, 1),
        (0, -1, 1),
        (0, 0, -1),
        (True, 0, 1),
        (0, 1.5, 1),
    ],
)
def test_workspace_estimate_requires_non_negative_integer_bytes_and_clones(values):
    with pytest.raises(ValueError, match="non-negative integer"):
        workspace.WorkspaceEstimate(*values)


def test_workspace_factory_imports_implementation_only_when_opened(tmp_path, monkeypatch):
    calls = []
    active = object()

    @contextmanager
    def opener(estimate, *, scratch_root, max_live_clones):
        calls.append((estimate, scratch_root, max_live_clones))
        yield active

    monkeypatch.setattr(workspace, "_cow_workspace_opener", lambda: opener)
    estimate = workspace.WorkspaceEstimate(10, 20, 3)
    factory = workspace.WorkspaceFactory(max_live_clones=3)

    with factory.open(estimate, scratch_root=tmp_path) as selected:
        assert selected is active

    assert calls == [(estimate, tmp_path, 3)]


def test_factory_for_lane_derives_live_clones_from_declared_jobs():
    lane = SimpleNamespace(
        judge=SimpleNamespace(mutation=SimpleNamespace(jobs=4))
    )

    assert workspace.factory_for_lane(lane).max_live_clones == 5


@pytest.mark.parametrize(
    "lane",
    [
        SimpleNamespace(judge=None),
        SimpleNamespace(judge=SimpleNamespace(mutation=None)),
        SimpleNamespace(judge=SimpleNamespace(mutation=SimpleNamespace(jobs=None))),
    ],
)
def test_factory_for_lane_uses_one_worker_when_mutation_is_not_declared(lane):
    assert workspace.factory_for_lane(lane).max_live_clones == 2
