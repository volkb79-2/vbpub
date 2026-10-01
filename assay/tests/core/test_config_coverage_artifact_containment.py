"""P17 work item 3 (config-load half) — ``judge.coverage.artifact`` must
resolve INSIDE the project it measures, the identical containment reasoning
:mod:`assay.config`'s own ``_resolve_source_root`` already applies to
``source_roots`` (A-049/A-067 finding 9), one field over. Existence is
deliberately NOT checked here: the artifact is an OUTPUT of the lane's own
command (A-048's timing), so it need not exist yet when ``assay.toml`` loads
— only :func:`assay.runner.run_lane`'s own runtime checks (symlink, tracked)
need live filesystem/git state, and those have their own test modules.
"""

from __future__ import annotations

from conftest import R1_LANE, Project, set_key

import pytest

from assay.config import load_lane_file
from assay.errors import LaneConfigError


def test_an_absolute_artifact_path_is_rejected(project: Project, tmp_path):
    lane = set_key(
        R1_LANE,
        "coverage",
        '{ format = "coverage-py-json", artifact = "%s" }' % (tmp_path / "cov.json"),
    )
    with pytest.raises(LaneConfigError, match="is absolute"):
        load_lane_file(project.write(lane))


def test_an_artifact_path_escaping_the_project_root_via_dotdot_is_rejected(
    project: Project,
):
    lane = set_key(
        R1_LANE,
        "coverage",
        '{ format = "coverage-py-json", artifact = "../outside/cov.json" }',
    )
    with pytest.raises(LaneConfigError, match="not contained beneath the project root"):
        load_lane_file(project.write(lane))


def test_a_relative_artifact_path_inside_the_project_loads(project: Project):
    lane = set_key(
        R1_LANE,
        "coverage",
        '{ format = "coverage-py-json", artifact = "build/cov.json" }',
    )
    judge = load_lane_file(project.write(lane)).lane("package").judge

    assert judge is not None
    assert judge.coverage.artifact == "build/cov.json"


def test_the_artifact_need_not_exist_yet_at_load_time(project: Project):
    # A-048's own timing: the artifact is an OUTPUT of the lane's command.
    assert not (project.root / "cov.json").exists()
    lane = load_lane_file(project.write(R1_LANE)).lane("package")
    assert lane.judge.coverage.artifact == "cov.json"


def test_a_symlinked_project_root_ancestor_escaping_via_the_artifact_is_rejected(
    project: Project, tmp_path
):
    # A symlink INSIDE the project resolving outside it is the same escape
    # `_resolve_source_root` already guards for source_roots -- Path.resolve()
    # collapses both '..' and symlinks to their real final target, so
    # comparing resolved paths catches either route the same way.
    outside = tmp_path / "outside"
    outside.mkdir()
    link = project.root / "linked"
    link.symlink_to(outside)
    lane = set_key(
        R1_LANE,
        "coverage",
        '{ format = "coverage-py-json", artifact = "linked/cov.json" }',
    )
    with pytest.raises(LaneConfigError, match="not contained beneath the project root"):
        load_lane_file(project.write(lane))
