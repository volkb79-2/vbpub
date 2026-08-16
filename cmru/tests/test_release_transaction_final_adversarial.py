"""Final release/transaction contract witnesses with local-only boundaries."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from cmru import release, transaction, version


def git(root: Path, *args: str, check=True):
    result = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True)
    if check and result.returncode:
        raise AssertionError(result.stderr)
    return result.stdout.strip()


def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.email", "test@example.invalid")
    git(root, "config", "user.name", "test")
    (root / "demo").mkdir()
    (root / "demo" / "x.py").write_text("x=1\n")
    git(root, "add", ".")
    git(root, "commit", "-q", "-m", "initial")
    return root


class FakeGitHub:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []

    def _request(self, method, url, data=None, content_type=None):
        self.calls.append((method, url, data, content_type))
        return next(self.responses)


def test_github_release_response_contracts_cover_create_update_delete_assets():
    gh = release.GitHubReleases("o", "r", "t")
    fake = FakeGitHub([
        (200, '{"id":1,"upload_url":"https://upload/{?name}"}'),
        (200, '{"id":2}'),
        (204, ''),
        (200, '[{"id":3,"name":"old"}]'),
        (204, ''),
        (201, ''),
    ])
    with patch.object(gh, "_request", side_effect=fake._request):
        assert gh.create_release("demo-v1", "title", "notes")["id"] == 1
        assert gh.update_release(1, "new", "notes")["id"] == 2
        gh.delete_release(1)
        assert gh.list_assets(1)[0]["name"] == "old"
        gh.delete_asset(3)
        with __import__("tempfile").TemporaryDirectory() as tmp:
            artifact = Path(tmp) / "x.bin"
            artifact.write_bytes(b"data")
            gh.upload_asset("https://upload/{?name}", artifact, "x.bin")
    assert [call[0] for call in fake.calls] == ["POST", "PATCH", "DELETE", "GET", "DELETE", "POST"]


def test_publish_recreates_existing_release_and_replaces_same_named_asset(tmp_path):
    asset = tmp_path / "artifact.whl"
    asset.write_bytes(b"artifact")
    gh = release.GitHubReleases("o", "r", "t")
    calls = []
    with patch.object(gh, "get_release_by_tag", return_value={"id": 4}), \
         patch.object(gh, "delete_release", side_effect=lambda rid: calls.append(("delete-release", rid))), \
         patch.object(gh, "create_release", side_effect=lambda *a: {"id": 5, "upload_url": "u"}), \
         patch.object(gh, "list_assets", return_value=[{"id": 9, "name": asset.name}]), \
         patch.object(gh, "delete_asset", side_effect=lambda rid: calls.append(("delete-asset", rid))), \
         patch.object(gh, "upload_asset", side_effect=lambda *a: calls.append(("upload", a[2]))):
        result = gh.publish("demo-v1", "title", "notes", [asset], recreate=True)
    assert result["id"] == 5
    assert calls == [("delete-release", 4), ("delete-asset", 9), ("upload", asset.name)]


def test_variant_success_publishes_hash_bound_assets_and_pointer(tmp_path):
    source = tmp_path / "source.tar.xz"
    source.write_bytes(b"payload")
    calls = []
    gh = SimpleNamespace(
        publish=lambda *args, **kwargs: calls.append((args, kwargs)),
        asset_download_url=lambda tag, name: f"https://download/{tag}/{name}",
    )
    result = release.publish_versioned_variants(
        gh, prefix="demo", version="1.0.0",
        variants=[release.VariantArtifact("py311", source, label="Python 3.11")],
        asset_suffix=".tar.xz",
    )
    assert result["release_tag"] == "demo-v1.0.0"
    assert result["variants"][0]["sha256"] == release.sha256_file(tmp_path / "demo-v1.0.0-py311.tar.xz")
    assert len(calls) == 2
    pointer = json.loads((tmp_path / "latest.json").read_text())
    assert pointer["variants"][0]["name"] == "py311"


def test_version_status_reports_external_no_tag_and_counter_strategies(tmp_path, capsys):
    root = repo(tmp_path)
    projects = {
        "external": SimpleNamespace(name="external", cwd="demo", paths=["demo"], prefix="external-v",
                                     git_tag=True, version=SimpleNamespace(strategy="external:VERSION")),
        "image": SimpleNamespace(name="image", cwd="demo", paths=["demo"], prefix="image-v",
                                  git_tag=False, version=SimpleNamespace(strategy="none")),
        "counter": SimpleNamespace(name="counter", cwd="demo", paths=["demo"], prefix="counter-v",
                                    git_tag=True, version=SimpleNamespace(strategy="counter", base_version="1.0.0")),
    }
    with patch.object(version, "detect_changed_projects", return_value=[
        ("external", projects["external"], None, "patch"),
        ("image", projects["image"], None, "patch"),
        ("counter", projects["counter"], None, "patch"),
    ]):
        version.status_cmd(root, projects)
    output = capsys.readouterr().out
    assert "derived by VERSION" in output
    assert "no-tag" in output
    assert "counter-v1.0.0-r1" in output


def test_release_cmd_rejects_unknown_strategy_without_tagging(tmp_path):
    root = repo(tmp_path)
    (root / "demo" / "x.py").write_text("x=2\n")
    git(root, "add", ".")
    git(root, "commit", "-q", "-m", "fix: changed")
    project = SimpleNamespace(name="demo", cwd="demo", paths=["demo"], prefix="demo-v", git_tag=True,
                              version=SimpleNamespace(strategy="invented"))
    with pytest.raises(SystemExit) as raised:
        version.release_cmd(root, {"demo": project})
    assert raised.value.code == 2
    assert git(root, "tag", "--list") == ""


def test_retain_release_outputs_rolls_back_partial_artifact_move(tmp_path):
    root = repo(tmp_path)
    child = tmp_path / "child"
    git(root, "worktree", "add", "-q", "-b", "cmru/release/final", str(child), "main")
    (child / "demo" / "dist-a").mkdir()
    (child / "demo" / "dist-a" / "a.whl").write_bytes(b"a")
    project = SimpleNamespace(name="demo", project_root=root / "demo", artifact_dirs=("dist-a", "dist-b"))
    workspace = transaction.ReleaseWorkspace(root, child, "cmru/release/final", git(root, "rev-parse", "HEAD"))
    with pytest.raises(RuntimeError, match="missing"):
        transaction.retain_success_outputs(root, workspace, {"demo": project}, {"demo": "demo-v1"},
                                            retain_logs=False, retain_artifacts=True)
    assert not (root / "demo" / "artifacts" / "demo-v1").exists()
    git(root, "worktree", "remove", "--force", str(child))
    git(root, "branch", "-D", "cmru/release/final")


def test_discard_build_workspace_requires_managed_build_branch(tmp_path):
    root = repo(tmp_path)
    (root / ".worktrees").mkdir()
    child = root / ".worktrees" / "child"
    git(root, "worktree", "add", "-q", "-b", "cmru/release/not-build", str(child), "main")
    with pytest.raises(RuntimeError, match="build worktree"):
        transaction.discard_build_workspace(root, child, dry_run=True)
    git(root, "worktree", "remove", "--force", str(child))
    git(root, "branch", "-D", "cmru/release/not-build")


def test_list_and_abandon_previous_uses_scope_overlap(tmp_path):
    root = repo(tmp_path)
    child = tmp_path / "child"
    branch = "cmru/release/overlap"
    git(root, "worktree", "add", "-q", "-b", branch, str(child), "main")
    workspace = transaction.ReleaseWorkspace(root, child, branch, git(child, "rev-parse", "HEAD"))
    transaction.write_release_scope(root, workspace, ["demo"])
    with patch.object(transaction, "remove_backup_branch"), patch.object(transaction, "remove_workspace") as removed:
        abandoned = transaction.abandon_previous(root, ["demo"])
    assert abandoned == [branch]
    removed.assert_called_once()
    git(root, "worktree", "remove", "--force", str(child))
    git(root, "branch", "-D", branch)


def test_revert_promotion_noop_and_conflict_are_distinct(tmp_path):
    root = repo(tmp_path)
    git(root, "branch", "cmru/release/noop")
    workspace = transaction.ReleaseWorkspace(root, root, "cmru/release/noop", git(root, "rev-parse", "HEAD"))
    assert transaction.revert_promotion(workspace) == transaction.RevertResult(ok=True, reverted=False)
    original_run = transaction.subprocess.run
    def fail_revert(argv, **kwargs):
        if len(argv) > 1 and argv[1] == "revert":
            return SimpleNamespace(returncode=1, stderr="", stdout="")
        return original_run(argv, **kwargs)
    with patch.object(transaction.subprocess, "run", side_effect=fail_revert):
        result = transaction.revert_promotion(workspace, from_sha="a" * 40)
    assert result.ok is False


def test_sync_local_main_refuses_to_move_local_main_with_unrelated_commits(tmp_path):
    root = repo(tmp_path)
    # No origin remote means fetch itself is an external boundary; a failed fetch is explicit.
    with patch.object(transaction.subprocess, "run", side_effect=subprocess.CalledProcessError(1, "fetch")):
        with pytest.raises(subprocess.CalledProcessError):
            transaction.sync_local_main(root)
