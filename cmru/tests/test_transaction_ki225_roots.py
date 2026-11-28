"""KI-225 witnesses: transaction directory creation never recreates a root."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from cmru import transaction
from tests._git_isolation import git_isolation  # noqa: F401

pytestmark = pytest.mark.usefixtures("git_isolation")
BASE = "a" * 40


def _repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(root)], check=True)
    (root / "source.txt").write_text("source\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "add", "source.txt"], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-q", "-m", "initial"], check=True)
    return root


def _workspace(root: Path) -> transaction.ReleaseWorkspace:
    return transaction.ReleaseWorkspace(root, root / "candidate", "cmru/release/token", BASE)


def test_tag_attempt_writer_does_not_recreate_removed_common_directory(monkeypatch, tmp_path):
    root = _repo(tmp_path)
    common = transaction._common_git_dir(root)
    workspace = _workspace(root)

    def remove_after_absence_clear(*_args):
        shutil.rmtree(common)

    monkeypatch.setattr(transaction, "clear_release_tag_absence", remove_after_absence_clear)
    with pytest.raises(transaction.TransactionDirectoryRootError, match="tag attempt"):
        transaction.write_release_tag_attempts(root, workspace, {"refs/tags/demo": BASE})

    assert not common.exists()


def test_absent_tag_writer_does_not_recreate_removed_common_directory(monkeypatch, tmp_path):
    root = _repo(tmp_path)
    common = transaction._common_git_dir(root)
    workspace = _workspace(root)
    monkeypatch.setattr(transaction, "read_release_tag_attempts", lambda *_args: {"refs/tags/demo": BASE})
    monkeypatch.setattr(transaction, "list_local_tag_refs", lambda *_args: {})

    def remove_after_absence_read(*_args):
        shutil.rmtree(common)
        return {}

    monkeypatch.setattr(transaction, "read_confirmed_absent_release_tag_attempts", remove_after_absence_read)
    with pytest.raises(transaction.TransactionDirectoryRootError, match="absent release tag"):
        transaction.write_confirmed_absent_release_tag_attempts(
            root, workspace, {"refs/tags/demo": BASE},
        )

    assert not common.exists()


def test_build_retention_does_not_recreate_removed_project_root(monkeypatch, tmp_path):
    root = _repo(tmp_path)
    project_root = root / "demo"
    project_root.mkdir()
    child_project = root / "candidate" / "demo"
    (child_project / "logs").mkdir(parents=True)
    (child_project / "logs" / "build.log").write_text("log", encoding="utf-8")
    (child_project / "dist").mkdir()
    (child_project / "dist" / "demo.whl").write_text("wheel", encoding="utf-8")
    workspace = _workspace(root)
    monkeypatch.setattr(
        transaction, "build_output_id",
        lambda _workspace: ("20261128T000000Z_" + BASE, BASE, "date"),
    )
    monkeypatch.setattr(transaction, "_git", lambda *_args, **_kwargs: "")
    original_digest = transaction._safe_digest_tree

    def remove_project_after_staging(path):
        result = original_digest(path)
        if path.name == "dist":
            shutil.rmtree(project_root)
        return result

    monkeypatch.setattr(transaction, "_safe_digest_tree", remove_project_after_staging)
    project = SimpleNamespace(project_root=project_root, artifact_dirs=("dist",))
    # The temporary build staging tree is inside project_root, so deleting the
    # root also removes staged_logs. Its integrity read fails before retention
    # reaches the destination mkdir; rollback leaves the removed root absent.
    with pytest.raises(RuntimeError, match="build output path is missing or unsafe"):
        transaction.retain_successful_build_outputs(
            root, workspace, {"demo": project}, ["demo"], source_mode="origin-main",
        )

    assert not project_root.exists()


@pytest.mark.parametrize("root_kind", ["symlink", "file"])
def test_build_retention_refuses_non_directory_project_root(monkeypatch, tmp_path, root_kind):
    root = _repo(tmp_path)
    project_root = root / "demo"
    if root_kind == "symlink":
        target = tmp_path / "real-project"
        target.mkdir()
        project_root.symlink_to(target, target_is_directory=True)
    else:
        project_root.write_text("file", encoding="utf-8")
    workspace = _workspace(root)
    monkeypatch.setattr(
        transaction, "build_output_id",
        lambda _workspace: ("20261128T000000Z_" + BASE, BASE, "date"),
    )
    monkeypatch.setattr(transaction, "_git", lambda *_args, **_kwargs: "")
    project = SimpleNamespace(project_root=project_root, artifact_dirs=("dist",))

    with pytest.raises(transaction.TransactionDirectoryRootError, match="build project root"):
        transaction.retain_successful_build_outputs(
            root, workspace, {"demo": project}, ["demo"], source_mode="origin-main",
        )


@pytest.mark.parametrize("root_kind", ["symlink", "file"])
def test_release_retention_refuses_non_directory_project_root(tmp_path, root_kind):
    root = _repo(tmp_path)
    project_root = root / "demo"
    if root_kind == "symlink":
        target = tmp_path / "real-project"
        target.mkdir()
        project_root.symlink_to(target, target_is_directory=True)
    else:
        project_root.write_text("file", encoding="utf-8")
    workspace = _workspace(root)
    project = SimpleNamespace(project_root=project_root)

    with pytest.raises(transaction.TransactionDirectoryRootError, match="release project root"):
        transaction.retain_success_outputs(
            root, workspace, {"demo": project}, {"demo": "demo-v1"},
            retain_logs=True, retain_artifacts=True,
        )


def test_release_retention_does_not_recreate_root_removed_between_parent_creations(
    monkeypatch, tmp_path,
):
    root = _repo(tmp_path)
    project_root = root / "demo"
    project_root.mkdir()
    child_project = root / "candidate" / "demo"
    (child_project / "logs").mkdir(parents=True)
    (child_project / "logs" / "release.log").write_text("log", encoding="utf-8")
    (child_project / "dist").mkdir()
    (child_project / "dist" / "demo.whl").write_text("wheel", encoding="utf-8")
    workspace = _workspace(root)
    project = SimpleNamespace(project_root=project_root, artifact_dirs=("dist",))
    original = transaction._mkdir_below
    calls = 0

    def remove_after_log_parent(*args, **kwargs):
        nonlocal calls
        result = original(*args, **kwargs)
        calls += 1
        if calls == 2:
            shutil.rmtree(project_root)
        return result

    monkeypatch.setattr(transaction, "_mkdir_below", remove_after_log_parent)
    with pytest.raises(transaction.TransactionDirectoryRootError, match="release artifact"):
        transaction.retain_success_outputs(
            root, workspace, {"demo": project}, {"demo": "demo-v1"},
            retain_logs=True, retain_artifacts=True, retain_evidence=False,
        )

    assert not project_root.exists()


def test_release_result_writer_refuses_a_missing_common_directory(monkeypatch, tmp_path):
    root = _repo(tmp_path)
    missing_common = tmp_path / "missing-common"
    monkeypatch.setattr(
        transaction, "_scope_dir",
        lambda _root: missing_common / "cmru-release-scopes",
    )

    with pytest.raises(transaction.TransactionDirectoryRootError, match="release result"):
        transaction.write_release_result(root, _workspace(root), "demo", "demo-v1")

    assert not missing_common.exists()


@pytest.mark.parametrize("root_kind", ["symlink", "file"])
def test_release_result_writer_refuses_non_directory_common_root(
    monkeypatch, tmp_path, root_kind,
):
    root = _repo(tmp_path)
    bad_root = tmp_path / "bad-common"
    if root_kind == "symlink":
        target = tmp_path / "real-common"
        target.mkdir()
        bad_root.symlink_to(target, target_is_directory=True)
    else:
        bad_root.write_text("file", encoding="utf-8")
    monkeypatch.setattr(
        transaction, "_scope_dir",
        lambda _root: bad_root / "cmru-release-scopes",
    )

    with pytest.raises(transaction.TransactionDirectoryRootError, match="release result"):
        transaction.write_release_result(root, _workspace(root), "demo", "demo-v1")


@pytest.mark.parametrize("root_kind", ["symlink", "file"])
def test_release_lock_refuses_non_directory_common_root(monkeypatch, tmp_path, root_kind):
    root = _repo(tmp_path)
    bad_root = tmp_path / "bad-common"
    if root_kind == "symlink":
        target = tmp_path / "real-common"
        target.mkdir()
        bad_root.symlink_to(target, target_is_directory=True)
    else:
        bad_root.write_text("file", encoding="utf-8")
    monkeypatch.setattr(transaction, "_common_git_dir", lambda _root: bad_root)

    with pytest.raises(transaction.TransactionDirectoryRootError, match="release lock"):
        with transaction.release_lock(root):
            pass


@pytest.mark.parametrize("root_kind", ["symlink", "file"])
def test_tag_attempt_writer_refuses_non_directory_root(monkeypatch, tmp_path, root_kind):
    root = _repo(tmp_path)
    workspace = _workspace(root)
    bad_root = tmp_path / "bad-root"
    if root_kind == "symlink":
        real_root = tmp_path / "real-root"
        real_root.mkdir()
        bad_root.symlink_to(real_root, target_is_directory=True)
    else:
        bad_root.write_text("file", encoding="utf-8")
    monkeypatch.setattr(transaction, "_common_git_dir", lambda _root: bad_root)
    monkeypatch.setattr(transaction, "read_release_tag_attempts", lambda *_args: None)
    monkeypatch.setattr(transaction, "read_confirmed_absent_release_tag_attempts", lambda *_args: {})

    with pytest.raises(transaction.TransactionDirectoryRootError, match="release scope"):
        transaction.write_release_tag_attempts(root, workspace, {"refs/tags/demo": BASE})


@pytest.mark.parametrize("root_kind", ["symlink", "file"])
def test_absent_tag_writer_refuses_non_directory_root(monkeypatch, tmp_path, root_kind):
    root = _repo(tmp_path)
    workspace = _workspace(root)
    bad_root = tmp_path / "bad-root"
    if root_kind == "symlink":
        real_root = tmp_path / "real-root"
        real_root.mkdir()
        bad_root.symlink_to(real_root, target_is_directory=True)
    else:
        bad_root.write_text("file", encoding="utf-8")
    monkeypatch.setattr(transaction, "_scope_dir", lambda _root: bad_root / "cmru-release-scopes")
    monkeypatch.setattr(transaction, "read_release_tag_attempts", lambda *_args: {"refs/tags/demo": BASE})
    monkeypatch.setattr(transaction, "list_local_tag_refs", lambda *_args: {})
    monkeypatch.setattr(transaction, "read_confirmed_absent_release_tag_attempts", lambda *_args: {})

    with pytest.raises(transaction.TransactionDirectoryRootError, match="absent release tag"):
        transaction.write_confirmed_absent_release_tag_attempts(
            root, workspace, {"refs/tags/demo": BASE},
        )


def test_directory_helper_creates_missing_levels_and_accepts_existing_intermediate(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    nested = root / "one" / "two"
    transaction._mkdir_below(root, nested, "create nested test directory")
    assert nested.is_dir()
    transaction._mkdir_below(root, nested, "accept existing nested test directory")
    assert nested.is_dir()


def test_directory_helper_passes_through_non_root_oserror(monkeypatch, tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    original = PermissionError("directory denied")

    def fail_mkdir(self, *args, **kwargs):
        raise original

    monkeypatch.setattr(Path, "mkdir", fail_mkdir)
    with pytest.raises(PermissionError) as caught:
        transaction._mkdir_below(root, root / "child", "create child")
    assert caught.value is original


@pytest.mark.parametrize("root_replacement", ["missing", "file"])
def test_directory_helper_converts_missing_level_error_when_root_changes(
    monkeypatch, tmp_path, root_replacement,
):
    root = tmp_path / "root"
    root.mkdir()
    original_mkdir = Path.mkdir

    def remove_root_before_level_creation(path, *args, **kwargs):
        shutil.rmtree(root)
        if root_replacement == "file":
            root.write_text("replacement", encoding="utf-8")
            raise FileNotFoundError("root changed during creation")
        return original_mkdir(path, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", remove_root_before_level_creation)
    with pytest.raises(transaction.TransactionDirectoryRootError) as caught:
        transaction._mkdir_below(root, root / "child", "create child")

    assert isinstance(caught.value.__cause__, FileNotFoundError)
    assert (not root.exists()) is (root_replacement == "missing")
