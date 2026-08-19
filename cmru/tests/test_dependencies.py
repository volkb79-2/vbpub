from __future__ import annotations

import dataclasses
from pathlib import Path
from types import SimpleNamespace

import pytest

from cmru.dependencies import (
    ToolDependencyRef,
    build_report,
    render_comment_block,
    render_text,
    write_comment_block,
)


def _project(
    root: Path, name: str, *, scm_dist: str | None = None, tool_dependencies=(),
) -> SimpleNamespace:
    project_root = root / name
    project_root.mkdir()
    return SimpleNamespace(
        project_root=project_root, scm_dist=scm_dist, tool_dependencies=tool_dependencies,
    )


def _tool_dep(project: str, version: str = "1.0.0", path: str = "tools/x.pyz") -> SimpleNamespace:
    return SimpleNamespace(project=project, version=version, path=path)


def test_first_party_wheel_manifest_must_match_declared_release_dependency(tmp_path):
    provider = _project(tmp_path, "cmru")
    consumer = _project(tmp_path, "modern-debian-tools-python-debug")
    wheels = consumer.project_root / "pip"
    wheels.mkdir()
    (wheels / "wheels.list").write_text("cmru\n", encoding="utf-8")

    report = build_report(
        repo_root=tmp_path,
        project_order=["cmru", "modern-debian-tools-python-debug"],
        declared={"cmru": [], "modern-debian-tools-python-debug": []},
        projects={"cmru": provider, "modern-debian-tools-python-debug": consumer},
    )

    assert not report.ok
    assert any("does not declare it" in error for error in report.errors)
    assert report.artifact_inputs["modern-debian-tools-python-debug"] == ("cmru",)


def test_dependency_comment_writer_replaces_only_its_marked_region(tmp_path):
    project = _project(tmp_path, "cmru")
    report = build_report(
        repo_root=tmp_path,
        project_order=["cmru"],
        declared={"cmru": []},
        projects={"cmru": project},
    )
    config = tmp_path / "cmru.orchestration.toml"
    config.write_text(
        "schema_version = 1\n\n[orchestration]\ncustom = true\n",
        encoding="utf-8",
    )

    write_comment_block(config, report)
    first = config.read_text(encoding="utf-8")
    assert "BEGIN CMRU GENERATED DEPENDENCY GRAPH" in first
    assert "custom = true" in first

    write_comment_block(config, report)
    second = config.read_text(encoding="utf-8")
    assert second == first
    assert render_comment_block(report) in second


# --- S15: tool-dependency edges ------------------------------------------------

def test_tool_dependency_edge_is_reported_but_never_checked_against_project_order(tmp_path):
    """This is the exact cmru/assay shape: assay depends_on cmru for RELEASE
    ORDER (assay comes AFTER cmru in project_order), while cmru's tests consume
    a vendored assay tool. If a tool edge were routed through the SAME ordering
    check as a "declared" edge, this would be flagged as a cycle -- it MUST NOT
    be (S15.2)."""
    cmru = _project(tmp_path, "cmru", tool_dependencies=[_tool_dep("assay", "1.0.0", "tools/assay/assay-1.0.0.pyz")])
    assay = _project(tmp_path, "assay")

    report = build_report(
        repo_root=tmp_path,
        project_order=["cmru", "assay"],  # cmru releases BEFORE assay
        declared={"cmru": [], "assay": ["cmru"]},  # assay depends_on cmru (release order)
        projects={"cmru": cmru, "assay": assay},
    )

    assert report.ok, report.errors
    assert any(
        edge.provider == "assay" and edge.consumer == "cmru" and edge.kind == "tool"
        for edge in report.edges
    )
    assert report.tool_dependencies["cmru"] == (
        ToolDependencyRef("assay", "1.0.0", "tools/assay/assay-1.0.0.pyz"),
    )
    text = render_text(report)
    assert "consumes first-party tools (excluded from release ordering): assay@1.0.0" in text
    assert "PREFLIGHT: PASS" in text


def test_tool_dependency_on_unknown_project_is_rejected(tmp_path):
    cmru = _project(tmp_path, "cmru", tool_dependencies=[_tool_dep("ghost")])

    report = build_report(
        repo_root=tmp_path, project_order=["cmru"], declared={"cmru": []},
        projects={"cmru": cmru},
    )

    assert not report.ok
    assert any(
        "unknown project 'ghost'" in error for error in report.errors
    )
    assert "cmru" not in report.tool_dependencies


def test_tool_dependency_on_itself_is_rejected(tmp_path):
    cmru = _project(tmp_path, "cmru", tool_dependencies=[_tool_dep("cmru")])

    report = build_report(
        repo_root=tmp_path, project_order=["cmru"], declared={"cmru": []},
        projects={"cmru": cmru},
    )

    assert not report.ok
    assert any("declares a tool dependency on itself" in error for error in report.errors)


def test_no_tool_dependencies_declared_is_a_silent_no_op(tmp_path):
    cmru = _project(tmp_path, "cmru")

    report = build_report(
        repo_root=tmp_path, project_order=["cmru"], declared={"cmru": []},
        projects={"cmru": cmru},
    )

    assert report.ok
    assert report.tool_dependencies == {}
    assert "consumes first-party tools" not in render_text(report)


def test_report_as_dict_includes_tool_dependencies(tmp_path):
    cmru = _project(tmp_path, "cmru", tool_dependencies=[_tool_dep("assay", "1.0.0", "tools/assay/assay-1.0.0.pyz")])
    assay = _project(tmp_path, "assay")

    report = build_report(
        repo_root=tmp_path, project_order=["cmru", "assay"], declared={"cmru": [], "assay": []},
        projects={"cmru": cmru, "assay": assay},
    )

    data = report.as_dict()
    assert data["tool_dependencies"] == {
        "cmru": [{"provider": "assay", "version": "1.0.0", "path": "tools/assay/assay-1.0.0.pyz"}],
    }
    assert any(e["kind"] == "tool" for e in data["edges"])


def test_constructing_and_reading_a_tool_dependency_ref_works_normally():
    # Paired control for the frozen-ness assertion below.
    ref = ToolDependencyRef(provider="assay", version="1.0.0", path="tools/x.pyz")
    assert ref.provider == "assay"
    assert ref.version == "1.0.0"
    assert ref.path == "tools/x.pyz"


def test_a_tool_dependency_ref_is_frozen():
    """ToolDependencyRef is a shared, reported graph fact (surfaced verbatim in
    `cmru dependencies --json`); if mutable, a caller could rewrite it after
    the report was built."""
    ref = ToolDependencyRef(provider="assay", version="1.0.0", path="tools/x.pyz")
    with pytest.raises(dataclasses.FrozenInstanceError):
        ref.version = "9.9.9"
