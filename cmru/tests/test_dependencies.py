from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from cmru.dependencies import build_report, render_comment_block, write_comment_block


def _project(root: Path, name: str, *, scm_dist: str | None = None) -> SimpleNamespace:
    project_root = root / name
    project_root.mkdir()
    return SimpleNamespace(project_root=project_root, scm_dist=scm_dist)


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
