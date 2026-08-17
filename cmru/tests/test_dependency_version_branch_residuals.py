"""Exact witnesses for dependency ordering/deduplication and git path branches."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from cmru import dependencies, version


def test_dependencies_accepts_ordered_declaration_and_deduplicates_repeated_wheels(tmp_path):
    consumer = tmp_path / "consumer"
    (consumer / "pip").mkdir(parents=True)
    (consumer / "pip" / "wheels.list").write_text("provider\nprovider\n", encoding="utf-8")
    projects = {
        "provider": SimpleNamespace(scm_dist="provider", project_root=None),
        "consumer": SimpleNamespace(scm_dist="consumer", project_root=consumer),
    }
    report = dependencies.build_report(
        repo_root=tmp_path,
        project_order=["provider", "consumer"],
        declared={"consumer": ["provider"]},
        projects=projects,
    )
    artifact_edges = [edge for edge in report.edges if edge.kind == "artifact"]
    assert len(artifact_edges) == 1
    assert report.artifact_inputs["consumer"] == ("provider",)
    assert report.ok
    unscoped = dependencies.build_report(
        project_order=["provider", "consumer"],
        declared={"consumer": ["provider"]},
        projects=projects,
    )
    assert next(edge for edge in unscoped.edges if edge.kind == "artifact").source.startswith("/")


def test_version_git_log_without_paths_preserves_unscoped_git_command(monkeypatch, tmp_path):
    captured = {}

    def run(command, **kwargs):
        captured.update(command=command, kwargs=kwargs)
        return SimpleNamespace(stdout="subject\n\n", returncode=0)

    monkeypatch.setattr(version.subprocess, "run", run)
    assert version._git_log(tmp_path, "HEAD~1") == ["subject"]
    assert "--" not in captured["command"]
