"""Behavioral witnesses for transaction provenance and retention boundaries."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from cmru import transaction


def _workspace(repo: Path, child: Path, branch="cmru/build/test-token"):
    return transaction.ReleaseWorkspace(repo, child, branch, "a" * 40)


def test_transaction_git_and_divergence_failures_preserve_repair_guidance(monkeypatch, tmp_path):
    monkeypatch.setattr(transaction.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=2, stdout="", stderr="bad ref"))
    with pytest.raises(RuntimeError, match="git rev-parse HEAD failed"):
        transaction._git(tmp_path, "rev-parse", "HEAD")
    monkeypatch.setattr(transaction, "_git", lambda *a, **k: "not two counts")
    with pytest.raises(RuntimeError, match="Cannot compare local main"):
        transaction.local_main_divergence(tmp_path)


def test_transaction_scope_and_result_records_are_sorted_and_validated(tmp_path, monkeypatch):
    monkeypatch.setattr(transaction, "_common_git_dir", lambda _root: tmp_path / ".git")
    workspace = _workspace(tmp_path, tmp_path / "child", "cmru/release/tok")
    transaction.write_release_scope(tmp_path, workspace, ["z", "a"])
    assert transaction.read_release_scope(tmp_path, workspace) == ["a", "z"]
    transaction.write_release_result(tmp_path, workspace, "demo", "demo-v1")
    transaction.write_release_result(tmp_path, workspace, "other", "other-v1")
    assert transaction.read_release_results(tmp_path, workspace) == {"demo": "demo-v1", "other": "other-v1"}
    scope = tmp_path / ".git" / "cmru-release-scopes" / "tok.json"
    scope.parent.mkdir(parents=True, exist_ok=True)
    scope.write_text("not-json", encoding="utf-8")
    assert transaction.read_release_scope(tmp_path, workspace) is None


def test_transaction_copy_secret_overlays_preserves_private_mode_and_rejects_outside(tmp_path):
    repo = tmp_path / "repo"; repo.mkdir()
    child = tmp_path / "child"; child.mkdir()
    (repo / "cmru.secret.toml").write_text("token=secret\n", encoding="utf-8")
    project = repo / "demo"; project.mkdir()
    (project / "cmru.secret.toml").write_text("project=true\n", encoding="utf-8")
    ws = _workspace(repo, child)
    transaction.copy_secret_overlays(repo, ws, [project / "cmru.toml"])
    assert (child / "cmru.secret.toml").read_text() == "token=secret\n"
    assert (child / "demo" / "cmru.secret.toml").read_text() == "project=true\n"
    assert oct((child / "cmru.secret.toml").stat().st_mode & 0o777) == "0o600"
    with pytest.raises(RuntimeError, match="outside repository"):
        transaction.copy_secret_overlays(repo, ws, [tmp_path / "outside" / "cmru.toml"])


def test_transaction_build_output_id_requires_real_source_facts(monkeypatch, tmp_path):
    ws = _workspace(tmp_path, tmp_path / "child")
    monkeypatch.setattr(transaction, "_git", lambda *a, **k: "not-a-sha")
    with pytest.raises(RuntimeError, match="invalid build source commit"):
        transaction.build_output_id(ws)
    monkeypatch.setattr(transaction, "_git", lambda repo, *args, **kwargs: "a" * 40 if args[-1] == "HEAD" else "not-time")
    with pytest.raises(RuntimeError, match="timestamp"):
        transaction.build_output_id(ws)


def test_transaction_retention_refuses_missing_artifacts_before_mutating_main(tmp_path, monkeypatch):
    repo = tmp_path / "repo"; repo.mkdir()
    child = repo / "child"; child.mkdir()
    project_root = repo / "demo"; project_root.mkdir()
    ws = _workspace(repo, child)
    project = SimpleNamespace(project_root=project_root, artifact_dirs=("dist",))
    monkeypatch.setattr(transaction, "build_output_id", lambda ws: ("20260101T000000Z_" + "a" * 40, "a" * 40, "date"))
    monkeypatch.setattr(transaction, "_git", lambda *a, **k: "")
    with pytest.raises(RuntimeError, match="build logs are missing"):
        transaction.retain_successful_build_outputs(repo, ws, {"demo": project}, ["demo"])
    assert not (project_root / "artifacts").exists()


def test_transaction_delete_retained_output_validates_manifest_and_supports_dry_run(tmp_path):
    project_root = tmp_path / "demo"; project_root.mkdir()
    project = SimpleNamespace(project_root=project_root)
    output_id = "20260101T000000Z_" + "a" * 40
    with pytest.raises(RuntimeError, match="exact"):
        transaction.delete_retained_build_output(tmp_path, project, "demo", "bad", dry_run=True)
    artifacts = project_root / "artifacts" / output_id; artifacts.mkdir(parents=True)
    logs = project_root / "logs" / output_id; logs.mkdir(parents=True)
    (artifacts / "build.json").write_text(json.dumps({
        "schema_version": 1, "kind": "cmru-local-build", "publication": "forbidden",
        "project": "demo", "build_id": output_id,
    }), encoding="utf-8")
    targets = transaction.delete_retained_build_output(tmp_path, project, "demo", output_id, dry_run=True)
    assert targets == [logs, artifacts] and artifacts.is_dir()
    transaction.delete_retained_build_output(tmp_path, project, "demo", output_id, dry_run=False)
    assert not artifacts.exists() and not logs.exists()


def test_transaction_release_output_retention_rolls_back_partial_artifact_move(tmp_path, monkeypatch):
    repo = tmp_path / "repo"; repo.mkdir()
    project_root = repo / "demo"; project_root.mkdir()
    child = repo / "child" / "demo"; child.mkdir(parents=True)
    source = child / "dist"; source.mkdir(); (source / "artifact.whl").write_text("x")
    ws = _workspace(repo, repo / "child", "cmru/release/tok")
    monkeypatch.setattr(transaction, "_git", lambda *a, **k: "a" * 40)
    project = SimpleNamespace(project_root=project_root, artifact_dirs=("dist",))
    retained = transaction.retain_success_outputs(
        repo, ws, {"demo": project}, {"demo": "demo-v1"}, retain_logs=False, retain_artifacts=True,
    )
    assert retained and (project_root / "artifacts" / "demo-v1" / "release.json").is_file()
    with pytest.raises(RuntimeError, match="already exists"):
        transaction.retain_success_outputs(
            repo, ws, {"demo": project}, {"demo": "demo-v1"}, retain_logs=False, retain_artifacts=True,
        )
