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


def _workspace(root: Path, path: Path | None = None) -> transaction.ReleaseWorkspace:
    return transaction.ReleaseWorkspace(
        root, path or root / "candidate", "cmru/release/token", BASE,
    )


def _build_sources(candidate: Path, name: str = "demo") -> Path:
    project = candidate / name
    (project / "logs").mkdir(parents=True)
    (project / "logs" / "build.log").write_text("log", encoding="utf-8")
    (project / "dist").mkdir()
    (project / "dist" / "demo.whl").write_text("wheel", encoding="utf-8")
    return project


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


def test_build_retention_refuses_missing_repo_root_without_recreating_it(monkeypatch, tmp_path):
    caller_repo = _repo(tmp_path)
    missing_repo = tmp_path / "removed-repo"
    candidate = tmp_path / "candidate"
    _build_sources(candidate)
    workspace = _workspace(missing_repo, candidate)
    monkeypatch.setattr(
        transaction, "build_output_id",
        lambda _workspace: ("20261128T000000Z_" + BASE, BASE, "date"),
    )
    monkeypatch.setattr(transaction, "_git", lambda *_args, **_kwargs: "")
    project = SimpleNamespace(project_root=missing_repo / "demo", artifact_dirs=("dist",))

    with pytest.raises(transaction.TransactionDirectoryRootError, match="build project directory"):
        transaction.retain_successful_build_outputs(
            caller_repo, workspace, {"demo": project}, ["demo"], source_mode="origin-main",
        )

    assert not missing_repo.exists()


def test_build_retention_creates_missing_project_directory_below_repo_root(monkeypatch, tmp_path):
    root = _repo(tmp_path)
    candidate = tmp_path / "candidate"
    _build_sources(candidate)
    workspace = _workspace(root, candidate)
    monkeypatch.setattr(
        transaction, "build_output_id",
        lambda _workspace: ("20261128T000000Z_" + BASE, BASE, "date"),
    )
    monkeypatch.setattr(transaction, "_git", lambda *_args, **_kwargs: "")
    project_root = root / "demo"
    project = SimpleNamespace(project_root=project_root, artifact_dirs=("dist",))

    retained = transaction.retain_successful_build_outputs(
        root, workspace, {"demo": project}, ["demo"], source_mode="origin-main",
    )

    assert project_root.is_dir()
    assert len(retained) == 2
    assert (project_root / "artifacts" / retained[1].name / "dist" / "demo.whl").is_file()
    assert (project_root / "logs" / retained[0].name / "build.log").is_file()


@pytest.mark.parametrize("target_exists", [True, False])
def test_build_retention_keeps_in_repo_project_symlink(monkeypatch, tmp_path, target_exists):
    root = _repo(tmp_path)
    candidate = tmp_path / "candidate"
    _build_sources(candidate, "real-demo")
    project_root = root / "demo"
    target = root / "real-demo"
    if target_exists:
        target.mkdir()
    project_root.symlink_to(target, target_is_directory=True)
    workspace = _workspace(root, candidate)
    monkeypatch.setattr(
        transaction, "build_output_id",
        lambda _workspace: ("20261128T000000Z_" + BASE, BASE, "date"),
    )
    monkeypatch.setattr(transaction, "_git", lambda *_args, **_kwargs: "")
    project = SimpleNamespace(project_root=project_root, artifact_dirs=("dist",))

    retained = transaction.retain_successful_build_outputs(
        root, workspace, {"demo": project}, ["demo"], source_mode="origin-main",
    )

    assert project_root.is_symlink()
    assert target.is_dir()
    assert len(retained) == 2
    assert (target / "artifacts" / retained[1].name / "dist" / "demo.whl").is_file()


def test_build_retention_checks_repo_root_before_logs_copytree(monkeypatch, tmp_path):
    root = _repo(tmp_path)
    candidate = tmp_path / "candidate"
    source_project = _build_sources(candidate)
    workspace = _workspace(root, candidate)
    monkeypatch.setattr(
        transaction, "build_output_id",
        lambda _workspace: ("20261128T000000Z_" + BASE, BASE, "date"),
    )
    monkeypatch.setattr(transaction, "_git", lambda *_args, **_kwargs: "")
    original_mkdtemp = transaction.tempfile.mkdtemp

    def remove_repo_after_stage(*args, **kwargs):
        stage = original_mkdtemp(*args, **kwargs)
        shutil.rmtree(root)
        return stage

    monkeypatch.setattr(transaction.tempfile, "mkdtemp", remove_repo_after_stage)
    project = SimpleNamespace(project_root=root / "demo", artifact_dirs=("dist",))

    with pytest.raises(transaction.TransactionDirectoryRootError, match="copy retained build logs"):
        transaction.retain_successful_build_outputs(
            root, workspace, {"demo": project}, ["demo"], source_mode="origin-main",
        )

    assert not root.exists()
    assert (source_project / "logs" / "build.log").is_file()


def test_build_retention_checks_repo_root_before_artifact_copytree(monkeypatch, tmp_path):
    root = _repo(tmp_path)
    candidate = tmp_path / "candidate"
    source_project = _build_sources(candidate)
    workspace = _workspace(root, candidate)
    monkeypatch.setattr(
        transaction, "build_output_id",
        lambda _workspace: ("20261128T000000Z_" + BASE, BASE, "date"),
    )
    monkeypatch.setattr(transaction, "_git", lambda *_args, **_kwargs: "")
    original_mkdir_below = transaction._mkdir_below
    calls = 0

    def remove_repo_after_staged_artifact_root(*args, **kwargs):
        nonlocal calls
        result = original_mkdir_below(*args, **kwargs)
        calls += 1
        if calls == 2:
            shutil.rmtree(root)
        return result

    monkeypatch.setattr(transaction, "_mkdir_below", remove_repo_after_staged_artifact_root)
    project = SimpleNamespace(project_root=root / "demo", artifact_dirs=("dist",))

    with pytest.raises(transaction.TransactionDirectoryRootError, match="copy retained build artifacts"):
        transaction.retain_successful_build_outputs(
            root, workspace, {"demo": project}, ["demo"], source_mode="origin-main",
        )

    assert not root.exists()
    assert (source_project / "dist" / "demo.whl").is_file()


def test_release_retention_skips_a_missing_root_when_there_is_nothing_to_retain(tmp_path):
    root = _repo(tmp_path)
    project_root = root / "demo"
    project = SimpleNamespace(project_root=project_root)

    retained = transaction.retain_success_outputs(
        root, _workspace(root), {"demo": project}, {"demo": "demo-v1"},
        retain_logs=True, retain_artifacts=True,
    )

    assert retained == []
    assert not project_root.exists()


def test_release_retention_refuses_missing_repo_root_without_recreating_it(tmp_path):
    caller_repo = _repo(tmp_path)
    missing_repo = tmp_path / "removed-repo"
    candidate = tmp_path / "candidate"
    child_logs = candidate / "demo" / "logs"
    child_logs.mkdir(parents=True)
    (child_logs / "release.log").write_text("log", encoding="utf-8")
    workspace = _workspace(missing_repo, candidate)
    project = SimpleNamespace(project_root=missing_repo / "demo")

    with pytest.raises(transaction.TransactionDirectoryRootError, match="release project directory"):
        transaction.retain_success_outputs(
            caller_repo, workspace, {"demo": project}, {"demo": "demo-v1"},
            retain_logs=True, retain_artifacts=False,
        )

    assert not missing_repo.exists()
    assert (child_logs / "release.log").is_file()


def test_release_retention_creates_missing_project_directory_below_repo_root(tmp_path):
    root = _repo(tmp_path)
    candidate = tmp_path / "candidate"
    child_logs = candidate / "demo" / "logs"
    child_logs.mkdir(parents=True)
    (child_logs / "release.log").write_text("log", encoding="utf-8")
    workspace = _workspace(root, candidate)
    project_root = root / "demo"
    project = SimpleNamespace(project_root=project_root)

    retained = transaction.retain_success_outputs(
        root, workspace, {"demo": project}, {"demo": "demo-v1"},
        retain_logs=True, retain_artifacts=False,
    )

    target = project_root / "logs" / "cmru-release" / "demo-v1" / "release.log"
    assert project_root.is_dir()
    assert retained == [target.parent]
    assert target.read_text(encoding="utf-8") == "log"


@pytest.mark.parametrize("target_exists", [True, False])
def test_release_retention_keeps_in_repo_project_symlink(tmp_path, target_exists):
    root = _repo(tmp_path)
    candidate = tmp_path / "candidate"
    child_logs = candidate / "real-demo" / "logs"
    child_logs.mkdir(parents=True)
    (child_logs / "release.log").write_text("log", encoding="utf-8")
    project_root = root / "demo"
    target = root / "real-demo"
    if target_exists:
        target.mkdir()
    project_root.symlink_to(target, target_is_directory=True)
    workspace = _workspace(root, candidate)
    project = SimpleNamespace(project_root=project_root)

    retained = transaction.retain_success_outputs(
        root, workspace, {"demo": project}, {"demo": "demo-v1"},
        retain_logs=True, retain_artifacts=False,
    )

    assert project_root.is_symlink()
    assert target.is_dir()
    assert retained[0] == target / "logs" / "cmru-release" / "demo-v1"
    assert (retained[0] / "release.log").read_text(encoding="utf-8") == "log"


def test_release_retention_checks_repo_root_before_logs_move(monkeypatch, tmp_path):
    root = _repo(tmp_path)
    candidate = tmp_path / "candidate"
    child_logs = candidate / "demo" / "logs"
    child_logs.mkdir(parents=True)
    (child_logs / "release.log").write_text("log", encoding="utf-8")
    workspace = _workspace(root, candidate)
    project = SimpleNamespace(project_root=root / "demo")
    original_check = transaction._require_real_directory_root

    def remove_repo_before_logs_move(check_root, operation, **kwargs):
        if operation == "move retained release logs":
            shutil.rmtree(root)
        return original_check(check_root, operation, **kwargs)

    monkeypatch.setattr(transaction, "_require_real_directory_root", remove_repo_before_logs_move)
    with pytest.raises(transaction.TransactionDirectoryRootError, match="move retained release logs"):
        transaction.retain_success_outputs(
            root, workspace, {"demo": project}, {"demo": "demo-v1"},
            retain_logs=True, retain_artifacts=False,
        )

    assert not root.exists()
    assert (child_logs / "release.log").is_file()


def test_release_retention_01735170_does_not_recreate_root_between_parent_creation(
    monkeypatch, tmp_path,
):
    root = _repo(tmp_path)
    candidate = tmp_path / "candidate"
    child_project = _build_sources(candidate)
    (child_project / "logs" / "release.log").write_text("release", encoding="utf-8")
    workspace = _workspace(root, candidate)
    project = SimpleNamespace(project_root=root / "demo", artifact_dirs=("dist",))
    original = transaction._mkdir_below
    calls = 0

    def remove_repo_after_log_parent(*args, **kwargs):
        nonlocal calls
        result = original(*args, **kwargs)
        calls += 1
        if calls == 2:
            shutil.rmtree(root)
        return result

    monkeypatch.setattr(transaction, "_mkdir_below", remove_repo_after_log_parent)
    with pytest.raises(transaction.TransactionDirectoryRootError, match="artifact record"):
        transaction.retain_success_outputs(
            root, workspace, {"demo": project}, {"demo": "demo-v1"},
            retain_logs=True, retain_artifacts=True, retain_evidence=False,
        )

    assert not root.exists()
    assert (child_project / "logs" / "release.log").is_file()
    assert (child_project / "dist" / "demo.whl").is_file()


def test_release_retention_checks_repo_root_before_artifact_move(monkeypatch, tmp_path):
    root = _repo(tmp_path)
    candidate = tmp_path / "candidate"
    source_project = _build_sources(candidate)
    workspace = _workspace(root, candidate)
    project = SimpleNamespace(project_root=root / "demo", artifact_dirs=("dist",))
    original_check = transaction._require_real_directory_root

    def remove_repo_before_artifact_move(check_root, operation, **kwargs):
        if operation == "move retained release artifacts":
            shutil.rmtree(root)
        return original_check(check_root, operation, **kwargs)

    monkeypatch.setattr(transaction, "_require_real_directory_root", remove_repo_before_artifact_move)
    with pytest.raises(transaction.TransactionDirectoryRootError, match="move retained release artifacts"):
        transaction.retain_success_outputs(
            root, workspace, {"demo": project}, {"demo": "demo-v1"},
            retain_logs=False, retain_artifacts=True, retain_evidence=False,
        )

    assert not root.exists()
    assert (source_project / "dist" / "demo.whl").is_file()


def test_release_retention_checks_repo_root_before_evidence_move(monkeypatch, tmp_path):
    root = _repo(tmp_path)
    candidate = tmp_path / "candidate"
    source = candidate / "demo" / "reports" / "summary.txt"
    source.parent.mkdir(parents=True)
    source.write_text("evidence", encoding="utf-8")
    workspace = _workspace(root, candidate)
    project = SimpleNamespace(project_root=root / "demo", evidence_paths=("reports/summary.txt",))
    original_check = transaction._require_real_directory_root

    def remove_repo_before_evidence_move(check_root, operation, **kwargs):
        if operation == "move retained release evidence":
            shutil.rmtree(root)
        return original_check(check_root, operation, **kwargs)

    monkeypatch.setattr(transaction, "_require_real_directory_root", remove_repo_before_evidence_move)
    with pytest.raises(transaction.TransactionDirectoryRootError, match="move retained release evidence"):
        transaction.retain_success_outputs(
            root, workspace, {"demo": project}, {"demo": "demo-v1"},
            retain_logs=False, retain_artifacts=False, retain_evidence=True,
        )

    assert not root.exists()
    assert source.is_file()


def test_release_retention_checks_repo_root_before_rollback_move(monkeypatch, tmp_path):
    root = _repo(tmp_path)
    candidate = tmp_path / "candidate"
    _build_sources(candidate)
    (candidate / "demo" / "logs" / "release.log").write_text("release", encoding="utf-8")
    workspace = _workspace(root, candidate)
    project = SimpleNamespace(project_root=root / "demo", artifact_dirs=("dist",))
    original_move = transaction.shutil.move
    move_count = 0

    def fail_artifact_move_after_removing_root(source, target, *args, **kwargs):
        nonlocal move_count
        move_count += 1
        if move_count == 1:
            return original_move(source, target, *args, **kwargs)
        if move_count == 2:
            shutil.rmtree(root)
            raise OSError("start rollback after caller root removal")
        return original_move(source, target, *args, **kwargs)

    monkeypatch.setattr(transaction.shutil, "move", fail_artifact_move_after_removing_root)
    with pytest.raises(RuntimeError, match="retention rollback failed") as caught:
        transaction.retain_success_outputs(
            root, workspace, {"demo": project}, {"demo": "demo-v1"},
            retain_logs=True, retain_artifacts=True, retain_evidence=False,
        )

    assert isinstance(caught.value.__cause__, transaction.TransactionDirectoryRootError)
    assert not root.exists()


def test_release_evidence_creates_nested_levels_below_its_new_root(monkeypatch, tmp_path):
    root = _repo(tmp_path)
    project_root = root / "demo"
    project_root.mkdir()
    child_project = root / "candidate" / "demo"
    source = child_project / "reports" / "summary.txt"
    source.parent.mkdir(parents=True)
    source.write_text("evidence", encoding="utf-8")
    workspace = _workspace(root)
    monkeypatch.setattr(transaction, "_git", lambda *_args, **_kwargs: BASE)
    project = SimpleNamespace(project_root=project_root, evidence_paths=("reports/summary.txt",))

    retained = transaction.retain_success_outputs(
        root, workspace, {"demo": project}, {"demo": "demo-v1"},
        retain_logs=False, retain_artifacts=False, retain_evidence=True,
    )

    evidence_root = project_root / "evidence" / "cmru-release" / "demo-v1"
    assert retained == [evidence_root]
    assert (evidence_root / "reports" / "summary.txt").read_text(encoding="utf-8") == "evidence"
    assert (evidence_root / "evidence.json").is_file()


def test_release_evidence_does_not_recreate_its_root_removed_before_nested_creation(
    monkeypatch, tmp_path,
):
    root = _repo(tmp_path)
    project_root = root / "demo"
    project_root.mkdir()
    child_project = root / "candidate" / "demo"
    source = child_project / "reports" / "summary.txt"
    source.parent.mkdir(parents=True)
    source.write_text("evidence", encoding="utf-8")
    workspace = _workspace(root)
    monkeypatch.setattr(transaction, "_git", lambda *_args, **_kwargs: BASE)
    project = SimpleNamespace(project_root=project_root, evidence_paths=("reports/summary.txt",))
    evidence_root = project_root / "evidence" / "cmru-release" / "demo-v1"
    original = transaction._mkdir_below
    calls = 0

    def remove_created_evidence_root(*args, **kwargs):
        nonlocal calls
        result = original(*args, **kwargs)
        calls += 1
        if calls == 2:
            shutil.rmtree(evidence_root)
        return result

    monkeypatch.setattr(transaction, "_mkdir_below", remove_created_evidence_root)
    with pytest.raises(transaction.TransactionDirectoryRootError, match="evidence path"):
        transaction.retain_success_outputs(
            root, workspace, {"demo": project}, {"demo": "demo-v1"},
            retain_logs=False, retain_artifacts=False, retain_evidence=True,
        )

    assert not evidence_root.exists()


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
    root = _repo(tmp_path) / "helper-root"
    root.mkdir()
    nested = root / "one" / "two"
    transaction._mkdir_below(root, nested, "create nested test directory")
    assert nested.is_dir()
    transaction._mkdir_below(root, nested, "accept existing nested test directory")
    assert nested.is_dir()

    target = root / "link-target"
    target.mkdir()
    link = root / "link"
    link.symlink_to(target, target_is_directory=True)
    transaction._mkdir_below(root, link / "through-link", "create through directory link")
    assert (link / "through-link").is_dir()

    regular_file = root / "regular-file"
    regular_file.write_text("file", encoding="utf-8")
    with pytest.raises(FileExistsError):
        transaction._mkdir_below(root, regular_file / "child", "reject non-directory level")


def test_directory_helper_preserves_exclusive_leaf_creation(tmp_path):
    root = _repo(tmp_path) / "helper-root"
    root.mkdir()
    existing = root / "existing"
    existing.mkdir()

    with pytest.raises(FileExistsError):
        transaction._mkdir_below(root, existing, "create exclusive leaf", exist_ok=False)


def test_directory_helper_passes_through_non_root_oserror(monkeypatch, tmp_path):
    root = _repo(tmp_path) / "helper-root"
    root.mkdir()
    original = PermissionError("directory denied")

    def fail_mkdir(self, *args, **kwargs):
        raise original

    monkeypatch.setattr(Path, "mkdir", fail_mkdir)
    with pytest.raises(PermissionError) as caught:
        transaction._mkdir_below(root, root / "child", "create child")
    assert caught.value is original


def test_directory_helper_passes_through_missing_level_error_when_root_still_exists(
    monkeypatch, tmp_path,
):
    root = _repo(tmp_path) / "helper-root"
    root.mkdir()
    original = FileNotFoundError("child vanished")

    def fail_mkdir(self, *args, **kwargs):
        raise original

    monkeypatch.setattr(Path, "mkdir", fail_mkdir)
    with pytest.raises(FileNotFoundError) as caught:
        transaction._mkdir_below(root, root / "child", "create child")
    assert caught.value is original
    assert root.is_dir()


@pytest.mark.parametrize("root_replacement", ["missing", "file"])
def test_directory_helper_converts_missing_level_error_when_root_changes(
    monkeypatch, tmp_path, root_replacement,
):
    root = _repo(tmp_path) / "helper-root"
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
