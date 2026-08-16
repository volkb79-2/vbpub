"""Final runtime/release behavioural boundary witnesses."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest


class _GH:
    def __init__(self): self.calls = []
    def publish(self, *args, **kwargs): self.calls.append((args, kwargs)); return {}
    def asset_download_url(self, tag, name): return f"https://x/{tag}/{name}"


class TestReleasePublicationPairs:
    def test_dev_and_immutable_publish_have_distinct_artifacts_and_no_hidden_version(self, tmp_path):
        from cmru.release import publish_versioned
        asset = tmp_path / "demo.whl"; asset.write_bytes(b"artifact")
        gh = _GH()
        dev = publish_versioned(gh, prefix="demo", version="0.0.0.dev1", asset_path=asset)
        assert dev["release_tag"] is None and len(gh.calls) == 1
        stable = publish_versioned(gh, prefix="demo", version="1.2.3", asset_path=asset)
        assert stable["release_tag"] == "demo-v1.2.3" and (tmp_path / "latest.json").exists()
        assert len(gh.calls) == 3

    def test_variant_publication_failure_does_not_claim_release(self, tmp_path):
        from cmru.release import VariantArtifact, publish_versioned_variants
        gh = _GH(); a = tmp_path / "linux.whl"; a.write_bytes(b"x")
        result = publish_versioned_variants(gh, prefix="p", version="1.0.0", variants=[VariantArtifact("linux", a)], asset_suffix=".whl")
        assert result["release_tag"] == "p-v1.0.0"
        assert any(call[0][0] == "p-v1.0.0" for call in gh.calls)
        with pytest.raises(SystemExit): publish_versioned_variants(gh, prefix="p", version="1", variants=[], asset_suffix=".whl")

    def test_read_wheel_version_and_artifact_ambiguity_fail_closed(self, tmp_path):
        from cmru.release import find_artifact, read_wheel_version
        with pytest.raises(Exception): read_wheel_version(tmp_path / "missing.whl")
        (tmp_path / "a.whl").write_bytes(b"not zip")
        with pytest.raises(Exception): read_wheel_version(tmp_path / "a.whl")
        (tmp_path / "a.tar.xz").write_bytes(b"a"); (tmp_path / "b.tar.xz").write_bytes(b"b")
        with pytest.raises(SystemExit): find_artifact(tmp_path, "*.tar.xz")


class TestVersionReleaseStrategies:
    def test_status_preview_and_release_dry_run_are_nonmutating(self, tmp_path, capsys):
        from cmru.version import status_cmd, release_cmd
        subprocess.run(["git", "init", "-q", "-b", "main", str(tmp_path)], check=True)
        subprocess.run(["git", "-C", str(tmp_path), "config", "user.email", "t@x"], check=True)
        subprocess.run(["git", "-C", str(tmp_path), "config", "user.name", "T"], check=True)
        p = tmp_path / "demo"; p.mkdir(); (p / "x").write_text("x")
        subprocess.run(["git", "-C", str(tmp_path), "add", "."], check=True); subprocess.run(["git", "-C", str(tmp_path), "commit", "-qm", "feat: first"], check=True)
        project = SimpleNamespace(prefix="demo-v", paths=["demo"], cwd="demo", git_tag=True, version=SimpleNamespace(strategy="scm", bump="conventional"))
        status_cmd(tmp_path, {"demo": project})
        assert "demo-v0.1.0" in capsys.readouterr().out
        before = subprocess.check_output(["git", "-C", str(tmp_path), "tag"], text=True)
        assert release_cmd(tmp_path, {"demo": project}, dry_run=True) == ["demo-v0.1.0"]
        assert subprocess.check_output(["git", "-C", str(tmp_path), "tag"], text=True) == before

    def test_release_dirty_tree_refuses_without_tag(self, tmp_path):
        from cmru.version import release_cmd
        subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
        (tmp_path / "dirty").write_text("x")
        with pytest.raises(SystemExit): release_cmd(tmp_path, {})


class TestRetentionAtomicity:
    def test_retain_artifacts_rolls_back_destination_on_missing_source(self, tmp_path):
        from cmru.transaction import ReleaseWorkspace, retain_success_outputs
        repo = tmp_path / "repo"; repo.mkdir(); subprocess.run(["git", "init", "-q", str(repo)], check=True)
        project = repo / "demo"; project.mkdir()
        ws_path = repo / ".worktrees" / "w"; ws_path.mkdir(parents=True)
        workspace = ReleaseWorkspace(repo, ws_path, "cmru/release/x", "abc")
        cfg = SimpleNamespace(project_root=project, artifact_dirs=("dist",))
        with pytest.raises(RuntimeError, match="missing"):
            retain_success_outputs(repo, workspace, {"demo": cfg}, {"demo": "id"}, retain_logs=False, retain_artifacts=True)
        assert not (project / "artifacts" / "id").exists()

    def test_discard_build_dry_run_preserves_worktree(self, tmp_path, monkeypatch):
        from cmru.transaction import discard_build_workspace
        repo = tmp_path; (repo / ".worktrees").mkdir()
        path = repo / ".worktrees" / "b"; path.mkdir()
        monkeypatch.setattr("cmru.transaction._common_git_dir", lambda p: repo / ".git")
        monkeypatch.setattr("cmru.transaction._git", lambda p, *a, **k: "cmru/build/x" if a == ("branch", "--show-current") else "sha")
        ws = discard_build_workspace(repo, path, dry_run=True)
        assert ws.path == path and path.exists()
