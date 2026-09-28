"""Behavioral contract for publishing one retained, verified build output."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from cmru import cli, handlers, release, runner, transaction


OUTPUT_ID = "20260927T120000Z_" + "a" * 40
SOURCE_COMMIT = "a" * 40
ARTIFACT_SHA = "b" * 64


def _step(name: str, commands: list[dict], **overrides) -> runner.StepConfig:
    options = {
        "name": name,
        "commands": commands,
        "bake_set_prefix": None,
        "bake_set_vars": [],
        "no_cache_env": None,
        "clean_dirs": [],
        "required_env": [],
        "login": None,
        "step_env": {},
        "env_command": None,
        "quiet": False,
    }
    options.update(overrides)
    return runner.StepConfig(**options)


def _project(name: str, root: Path) -> SimpleNamespace:
    return SimpleNamespace(
        name=name,
        cwd=name,
        project_root=root / name,
        env={},
        build_metadata={},
        runtime_kind="none",
        artifact_dirs=("dist",),
        runner_steps={
            "push": _step(
                "push",
                [{"label": "push", "argv": ["python", "push.py"], "cwd": "."}],
                quiet=True,
            ),
        },
    )


def _loaded(root: Path, projects: dict[str, SimpleNamespace]):
    names = list(projects)
    return (
        root,
        projects,
        names,
        names,
        ["build"],
        "project-first",
        {},
        cli.CleanupConfig([], [], [], []),
        cli.GitHubConfig("owner", "repo", "token", "user"),
        cli.ReleaseEnvConfig({}, None),
    )


def _record(root: Path):
    return {
        "artifact_root": root / "alpha" / "artifacts" / OUTPUT_ID,
        "manifest": {
            "source_commit": SOURCE_COMMIT,
            "source_commit_date": "2026-09-27T12:00:00+00:00",
            "artifacts": [{
                "directory": "dist",
                "files": [{"path": "alpha.whl", "sha256": ARTIFACT_SHA, "bytes": "3"}],
            }],
        },
    }


def _materialized_output(project_root: Path):
    artifact_root = project_root / "artifacts" / OUTPUT_ID
    artifact_dir = artifact_root / "dist"
    artifact_dir.mkdir(parents=True)
    logs_root = project_root / "logs" / OUTPUT_ID
    logs_root.mkdir(parents=True)
    artifact = artifact_dir / "alpha.whl"
    artifact.write_bytes(b"wheel")
    digest = hashlib.sha256(b"wheel").hexdigest()
    manifest = {
        "schema_version": 1,
        "kind": "cmru-local-build",
        "publication": "eligible",
        "project": "alpha",
        "build_id": OUTPUT_ID,
        "source_commit": SOURCE_COMMIT,
        "source_commit_date": "2026-09-27T12:00:00+00:00",
        "source_tree_changes": [],
        "logs": [],
        "artifacts": [{"directory": "dist", "files": [{
            "path": "alpha.whl",
            "sha256": digest,
            "bytes": "5",
        }]}],
    }
    manifest_path = artifact_root / "build.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    project = SimpleNamespace(project_root=project_root, artifact_dirs=("dist",))
    return project, artifact_root, logs_root, artifact, manifest, manifest_path


def _patch_config(monkeypatch, root: Path, projects):
    config_path = root / "cmru.orchestration.toml"
    monkeypatch.setattr(cli, "_resolve_config", lambda _path: config_path)
    monkeypatch.setattr(cli, "load_config", lambda _path: _loaded(root, projects))
    monkeypatch.setattr(cli, "apply_release_env", lambda *_args: None)
    monkeypatch.setattr(transaction, "is_transaction_child", lambda _root: False)
    return config_path


@pytest.mark.parametrize(
    ("fault", "message"),
    (
        ("manifest-json", "invalid retained build manifest"),
        ("manifest-shape", "does not authorize publication"),
        ("publication-forbidden", "does not authorize publication"),
        ("source-commit", "source commit does not match"),
        ("source-date-missing", "has no source commit date"),
        ("source-date-invalid", "source commit date is invalid"),
        ("source-date-naive", "source date does not match"),
        ("source-changes", "source tree changes are invalid"),
        ("source-changes-nonempty", "source tree changes; only a clean source tree"),
        ("no-artifacts", "no publishable artifacts"),
        ("artifact-shape", "malformed artifact inventory"),
        ("artifact-dir", "malformed artifact directory"),
        ("artifact-dir-missing", "declared artifact directory is missing"),
        ("file-shape", "malformed file inventory"),
        ("file-path", "unsafe file coordinate"),
        ("duplicate-file", "duplicate file coordinate"),
        ("file-missing", "retained artifact bytes differ"),
        ("file-symlink", "contains a symlink"),
        ("file-special", "contains a non-regular path"),
    ),
)
def test_build_output_tree_validator_refuses_invalid_manifest_and_bytes(
    tmp_path, fault, message,
):
    _project, artifact_root, _logs_root, artifact, manifest, manifest_path = (
        _materialized_output(tmp_path / "alpha")
    )
    if fault == "manifest-json":
        manifest_path.write_text("{", encoding="utf-8")
    elif fault == "manifest-shape":
        manifest_path.write_text("[]", encoding="utf-8")
    elif fault == "publication-forbidden":
        manifest["publication"] = "forbidden"
    elif fault == "source-commit":
        manifest["source_commit"] = "b" * 40
    elif fault == "source-date-missing":
        manifest["source_commit_date"] = ""
    elif fault == "source-date-invalid":
        manifest["source_commit_date"] = "not-a-date"
    elif fault == "source-date-naive":
        manifest["source_commit_date"] = "2026-09-27T12:00:00"
    elif fault == "source-changes":
        manifest["source_tree_changes"] = [None]
    elif fault == "source-changes-nonempty":
        manifest["source_tree_changes"] = [" M alpha/generated.txt"]
    elif fault == "no-artifacts":
        manifest["artifacts"] = []
    elif fault == "artifact-shape":
        manifest["artifacts"] = [{"directory": "dist"}]
    elif fault == "artifact-dir":
        manifest["artifacts"][0]["directory"] = "../dist"
    elif fault == "artifact-dir-missing":
        manifest["artifacts"][0]["directory"] = "absent"
    elif fault == "file-shape":
        manifest["artifacts"][0]["files"] = [{}]
    elif fault == "file-path":
        manifest["artifacts"][0]["files"][0]["path"] = "../alpha.whl"
    elif fault == "duplicate-file":
        manifest["artifacts"][0]["files"].append(
            dict(manifest["artifacts"][0]["files"][0])
        )
    elif fault == "file-missing":
        artifact.unlink()
    elif fault == "file-symlink":
        artifact.unlink()
        artifact.symlink_to(tmp_path / "outside.whl")
    elif fault == "file-special":
        artifact.unlink()
        os.mkfifo(artifact)
    if fault not in {"manifest-json", "manifest-shape", "file-missing", "file-symlink", "file-special"}:
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(RuntimeError, match=message):
        transaction.validate_build_output_tree(artifact_root, "alpha", OUTPUT_ID)


@pytest.mark.parametrize("fault", ("bad-id", "wrong-name", "missing-root", "root-symlink", "manifest-missing", "manifest-symlink"))
def test_build_output_tree_validator_refuses_unsafe_record_paths(tmp_path, fault):
    _project, artifact_root, _logs_root, _artifact, _manifest, manifest_path = (
        _materialized_output(tmp_path / "alpha")
    )
    output_id = OUTPUT_ID
    if fault == "bad-id":
        output_id = "bad"
    elif fault == "wrong-name":
        artifact_root = artifact_root.with_name("wrong")
    elif fault == "missing-root":
        artifact_root = artifact_root.with_name("20260927T120001Z_" + "a" * 40)
    elif fault == "root-symlink":
        linked_root = tmp_path / "linked" / OUTPUT_ID
        linked_root.parent.mkdir()
        linked_root.symlink_to(artifact_root, target_is_directory=True)
        artifact_root = linked_root
    elif fault == "manifest-missing":
        manifest_path.unlink()
    elif fault == "manifest-symlink":
        manifest_path.unlink()
        manifest_path.symlink_to(tmp_path / "outside.json")

    expected = "invalid retained build output ID" if fault == "bad-id" else (
        "manifest is missing or unsafe" if fault.startswith("manifest") else
        "retained build output is missing or unsafe"
    )
    with pytest.raises(RuntimeError, match=expected):
        transaction.validate_build_output_tree(artifact_root, "alpha", output_id)


@pytest.mark.parametrize(
    ("fault", "message"),
    (
        ("no-root", "without project_root"),
        ("missing-root", "project root is missing or unsafe"),
        ("symlink-root", "project root is missing or unsafe"),
        ("missing-logs", "build logs are missing or unsafe"),
        ("symlink-logs", "evidence path is or crosses a symlink"),
        ("changed-logs", "retained build logs differ"),
        ("log-record-shape", "retained build logs differ"),
        ("artifact-dirs", "artifact_dirs do not match"),
    ),
)
def test_retained_output_validator_checks_project_logs_and_artifact_policy(
    tmp_path, fault, message,
):
    project_root = tmp_path / "alpha"
    project, _artifact_root, logs_root, _artifact, manifest, manifest_path = (
        _materialized_output(project_root)
    )
    if fault == "no-root":
        project.project_root = None
    elif fault == "missing-root":
        project.project_root = tmp_path / "missing"
    elif fault == "symlink-root":
        target = tmp_path / "target"
        target.mkdir()
        link = tmp_path / "alpha-link"
        link.symlink_to(target, target_is_directory=True)
        project.project_root = link
    elif fault == "missing-logs":
        logs_root.rmdir()
    elif fault == "symlink-logs":
        logs_root.rmdir()
        logs_root.symlink_to(tmp_path / "outside-logs", target_is_directory=True)
    elif fault == "changed-logs":
        (logs_root / "run.log").write_text("unexpected", encoding="utf-8")
    elif fault == "log-record-shape":
        manifest["logs"] = {}
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    elif fault == "artifact-dirs":
        project.artifact_dirs = ("other",)

    with pytest.raises(RuntimeError, match=message):
        transaction.validate_retained_build_output(project, "alpha", OUTPUT_ID)


def test_safe_digest_tree_refuses_missing_roots_and_symlinks(tmp_path):
    with pytest.raises(RuntimeError, match="missing or unsafe"):
        transaction._safe_digest_tree(tmp_path / "missing")
    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "link"
    link.symlink_to(target, target_is_directory=True)
    with pytest.raises(RuntimeError, match="missing or unsafe"):
        transaction._safe_digest_tree(link)


def test_build_output_publication_refuses_ambiguous_multi_project_selection(
    monkeypatch, tmp_path,
):
    projects = {name: _project(name, tmp_path) for name in ("alpha", "beta")}
    _patch_config(monkeypatch, tmp_path, projects)
    monkeypatch.setattr(cli, "_select_projects", lambda *_args: ["alpha", "beta"])
    monkeypatch.setattr(
        transaction,
        "validate_retained_build_output",
        lambda *_args: (_ for _ in ()).throw(AssertionError("must refuse before record lookup")),
    )

    assert cli.main(["publish", "--build-output", OUTPUT_ID, "--dry-run"]) == 2


def test_build_output_publication_refuses_malformed_id_as_usage_error(monkeypatch, tmp_path):
    project = _project("alpha", tmp_path)
    _patch_config(monkeypatch, tmp_path, {"alpha": project})
    monkeypatch.setattr(cli, "_select_projects", lambda *_args: ["alpha"])
    monkeypatch.setattr(
        transaction,
        "validate_retained_build_output",
        lambda *_args: (_ for _ in ()).throw(AssertionError("must reject ID before lookup")),
    )

    assert cli.main(["publish", "alpha", "--build-output", "bad", "--dry-run"]) == 2
    invalid_date = "20261327T120000Z_" + "a" * 40
    assert cli.main(["publish", "alpha", "--build-output", invalid_date, "--dry-run"]) == 2


def test_build_output_publication_refuses_projects_without_push_step(
    monkeypatch, tmp_path,
):
    project = _project("alpha", tmp_path)
    project.runner_steps = {}
    _patch_config(monkeypatch, tmp_path, {"alpha": project})
    monkeypatch.setattr(cli, "_select_projects", lambda *_args: ["alpha"])
    monkeypatch.setattr(
        transaction,
        "validate_retained_build_output",
        lambda *_args: {"manifest": {}, "artifact_root": tmp_path},
    )
    monkeypatch.setattr(
        cli,
        "require_project_publish_credentials",
        lambda *_args: (_ for _ in ()).throw(AssertionError("refuse before credentials")),
    )

    assert cli.main(["publish", "alpha", "--build-output", OUTPUT_ID]) == 2


def test_build_output_context_and_asset_selector_refuse_unsafe_inputs(
    monkeypatch, tmp_path,
):
    for key in (
        "CMRU_BUILD_OUTPUT_ROOT", "CMRU_BUILD_OUTPUT_ID",
        "CMRU_BUILD_OUTPUT_PROJECT", "CMRU_BUILD_SOURCE_COMMIT",
        "CMRU_BUILD_SOURCE_DATE",
    ):
        monkeypatch.delenv(key, raising=False)

    assert handlers._build_output_record(None) is None
    with pytest.raises(RuntimeError, match="context is not active"):
        handlers._build_output_files(None, "*.whl")

    monkeypatch.setenv("CMRU_BUILD_OUTPUT_ROOT", str(tmp_path / OUTPUT_ID))
    monkeypatch.setenv("CMRU_BUILD_OUTPUT_ID", OUTPUT_ID)
    with pytest.raises(RuntimeError, match="context is incomplete"):
        handlers._build_output_record(None)

    monkeypatch.setenv("CMRU_BUILD_OUTPUT_PROJECT", "alpha")
    monkeypatch.setenv("CMRU_BUILD_OUTPUT_ROOT", str(tmp_path / "wrong-id"))
    with pytest.raises(RuntimeError, match="does not match"):
        handlers._build_output_record(None)

    record = {
        "artifact_root": tmp_path,
        "manifest": {"artifacts": [{
            "directory": "dist",
            "files": [
                {"path": "alpha.whl"},
                {"path": "nested/beta.whl"},
            ],
        }]},
    }
    assert handlers._build_output_files(None, "*.whl", record=record) == [
        tmp_path / "dist" / "alpha.whl",
        tmp_path / "dist" / "nested/beta.whl",
    ]
    assert handlers._build_output_files(None, "dist/nested/*.whl", record=record) == [
        tmp_path / "dist" / "nested/beta.whl",
    ]
    assert handlers._build_output_files(None, "*.tar.gz", record=record) == []
    for unsafe in ("../outside.whl", "/outside.whl"):
        with pytest.raises(RuntimeError, match="must stay inside"):
            handlers._build_output_files(None, unsafe, record=record)


def test_wheel_publish_refuses_non_unique_retained_selection(monkeypatch, tmp_path):
    monkeypatch.setattr(handlers, "_require_env", lambda _name: "configured")
    monkeypatch.setattr(handlers, "_build_output_record", lambda _args: {"manifest": {}})
    monkeypatch.setattr(handlers, "_build_output_files", lambda *_args, **_kwargs: [Path("a"), Path("b")])

    with pytest.raises(RuntimeError, match="expected one retained wheel"):
        handlers.cmd_wheel_publish(SimpleNamespace(
            cwd=str(tmp_path), prefix="alpha", glob="*.whl", notes_env=None,
            extra_asset=None,
        ))


def test_tarball_publish_validates_retained_version_file_and_artifact_selection(
    monkeypatch, tmp_path,
):
    monkeypatch.setattr(handlers, "_require_env", lambda _name: "configured")
    record = {"manifest": {}}
    monkeypatch.setattr(handlers, "_build_output_record", lambda _args: record)
    version_path = tmp_path / "VERSION"
    version_path.write_text("1.2.3\n", encoding="utf-8")

    def select(_args, pattern, *, record=None):
        if pattern == "VERSION":
            return [version_path]
        return []

    monkeypatch.setattr(handlers, "_build_output_files", select)
    with pytest.raises(RuntimeError, match="expected one retained tarball"):
        handlers.cmd_tarball_publish(SimpleNamespace(
            cwd=str(tmp_path), prefix="alpha", glob="*.tar.xz", notes_env=None,
            version_env=None, version_file="VERSION",
        ))

    monkeypatch.setattr(
        handlers, "_build_output_files", lambda *_args, **_kwargs: []
    )
    with pytest.raises(RuntimeError, match="expected one retained version file"):
        handlers.cmd_tarball_publish(SimpleNamespace(
            cwd=str(tmp_path), prefix="alpha", glob="*.tar.xz", notes_env=None,
            version_env=None, version_file="VERSION",
        ))


def test_build_output_dry_run_validates_and_displays_retained_bytes_without_credentials(
    monkeypatch, tmp_path, capsys,
):
    project = _project("alpha", tmp_path)
    _patch_config(monkeypatch, tmp_path, {"alpha": project})
    record = _record(tmp_path)
    monkeypatch.setattr(
        transaction,
        "validate_retained_build_output",
        lambda selected, name, output_id: (
            record if selected is project and name == "alpha" and output_id == OUTPUT_ID
            else AssertionError("unexpected retained output lookup")
        ),
    )
    monkeypatch.setattr(
        cli,
        "require_project_publish_credentials",
        lambda *_args: (_ for _ in ()).throw(AssertionError("dry-run must not require credentials")),
    )
    monkeypatch.setattr(
        cli,
        "run_project_step",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("dry-run executed push")),
    )

    assert cli.main(["publish", "alpha", "--build-output", OUTPUT_ID, "--dry-run"]) == 0
    output = capsys.readouterr().out
    assert SOURCE_COMMIT in output
    assert ARTIFACT_SHA in output
    assert "CMRU_BUILD_OUTPUT_ROOT" in output
    assert "Would run declared step push" in output


def test_build_output_publication_passes_protected_record_context_to_push_step(
    monkeypatch, tmp_path,
):
    project = _project("alpha", tmp_path)
    _patch_config(monkeypatch, tmp_path, {"alpha": project})
    record = _record(tmp_path)
    monkeypatch.setattr(
        transaction,
        "validate_retained_build_output",
        lambda *_args: record,
    )
    monkeypatch.setattr(cli, "require_project_publish_credentials", lambda *_args: None)
    monkeypatch.setattr(
        transaction,
        "project_git_family_groups",
        lambda _root, selected: {tmp_path: list(selected)},
    )
    monkeypatch.setattr(cli, "_configs_for_git_family", lambda configs, _names, _root: configs)
    monkeypatch.setattr(cli, "apply_project_release_env", lambda *_args: None)
    calls = []
    monkeypatch.setattr(
        cli,
        "run_project_step",
        lambda selected, step, _root, _logs, **kwargs: calls.append((selected, step, kwargs)),
    )
    monkeypatch.setattr(
        cli,
        "_run_isolated_build_projects",
        lambda *_args: (_ for _ in ()).throw(AssertionError("publish must not rebuild")),
    )

    assert cli.main(["publish", "alpha", "--build-output", OUTPUT_ID]) == 0
    assert len(calls) == 1
    selected, step, kwargs = calls[0]
    assert selected is project and step == "push"
    assert kwargs["protected_env"] == {
        "CMRU_BUILD_OUTPUT_ROOT": str(record["artifact_root"]),
        "CMRU_BUILD_OUTPUT_ID": OUTPUT_ID,
        "CMRU_BUILD_OUTPUT_PROJECT": "alpha",
        "CMRU_BUILD_SOURCE_COMMIT": SOURCE_COMMIT,
        "CMRU_BUILD_SOURCE_DATE": record["manifest"]["source_commit_date"],
    }


def test_builtin_publish_handlers_select_only_inventoried_retained_bytes(
    monkeypatch, tmp_path,
):
    artifact_root = tmp_path / OUTPUT_ID
    dist = artifact_root / "dist"
    dist.mkdir(parents=True)
    retained_artifact = dist / "alpha.whl"
    retained_artifact.write_bytes(b"verified wheel")
    digest = hashlib.sha256(retained_artifact.read_bytes()).hexdigest()
    manifest = {
        "schema_version": 1,
        "kind": "cmru-local-build",
        "publication": "eligible",
        "project": "alpha",
        "build_id": OUTPUT_ID,
        "source_commit": SOURCE_COMMIT,
        "source_commit_date": "2026-09-27T12:00:00+00:00",
        "source_tree_changes": [],
        "logs": [],
        "artifacts": [{"directory": "dist", "files": [{
            "path": "alpha.whl", "sha256": digest, "bytes": str(len(b"verified wheel")),
        }]}],
    }
    (artifact_root / "build.json").write_text(json.dumps(manifest), encoding="utf-8")
    monkeypatch.setenv("CMRU_BUILD_OUTPUT_ROOT", str(artifact_root))
    monkeypatch.setenv("CMRU_BUILD_OUTPUT_ID", OUTPUT_ID)
    monkeypatch.setenv("CMRU_BUILD_OUTPUT_PROJECT", "alpha")
    monkeypatch.setenv("CMRU_BUILD_SOURCE_COMMIT", SOURCE_COMMIT)
    monkeypatch.setenv("CMRU_BUILD_SOURCE_DATE", "2026-09-27T12:00:00+00:00")
    monkeypatch.setenv("VERSION", "1.2.3")
    monkeypatch.setattr(handlers, "_require_env", lambda _name: "configured")
    monkeypatch.setattr(handlers, "GitHubReleases", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(handlers, "read_wheel_version", lambda _path: "1.2.3")
    published = []

    def capture_publish(_gh, **kwargs):
        published.append({
            **kwargs,
            "asset_bytes": kwargs["asset_path"].read_bytes(),
        })
        return {"sha256": digest, "asset_url": "https://example.invalid/a", "release_tag": "alpha-v1.2.3"}

    monkeypatch.setattr(handlers, "publish_versioned", capture_publish)
    handlers.cmd_wheel_publish(SimpleNamespace(
        cwd=str(tmp_path), prefix="alpha", glob="*.whl", notes_env=None, extra_asset=None,
    ))
    handlers.cmd_tarball_publish(SimpleNamespace(
        cwd=str(tmp_path), prefix="alpha", glob="*.whl", notes_env=None,
        version_env="VERSION", version_file=None,
    ))

    assert [item["asset_bytes"] for item in published] == [
        b"verified wheel", b"verified wheel",
    ]
    assert all(item["latest_pointer"] is True for item in published)
    assert all(item["require_existing_targets"] is True for item in published)
    assert all(item["latest_pointer_recreate"] is False for item in published)
    assert all(item["expected_tag_commit"] == SOURCE_COMMIT for item in published)
    assert all(item["asset_path"].parent != dist for item in published)
    assert all(item["require_existing_targets"] for item in published)
    assert all(not item["latest_pointer_recreate"] for item in published)
    assert all(item["expected_tag_commit"] == SOURCE_COMMIT for item in published)


def test_builtin_retained_publish_preserves_manifest_and_existing_tag_targets(
    monkeypatch, tmp_path,
):
    artifact_root = tmp_path / OUTPUT_ID
    dist = artifact_root / "dist"
    dist.mkdir(parents=True)
    retained_artifact = dist / "alpha-1.2.3.whl"
    retained_artifact.write_bytes(b"verified wheel")
    companion = dist / "alpha-1.2.3.pyz"
    companion.write_bytes(b"verified companion")
    digest = hashlib.sha256(retained_artifact.read_bytes()).hexdigest()
    companion_digest = hashlib.sha256(companion.read_bytes()).hexdigest()
    manifest = {
        "schema_version": 1,
        "kind": "cmru-local-build",
        "publication": "eligible",
        "project": "alpha",
        "build_id": OUTPUT_ID,
        "source_commit": SOURCE_COMMIT,
        "source_commit_date": "2026-09-27T12:00:00+00:00",
        "source_tree_changes": [],
        "logs": [],
        "artifacts": [{"directory": "dist", "files": [
            {
                "path": retained_artifact.name,
                "sha256": digest,
                "bytes": str(len(b"verified wheel")),
            },
            {
                "path": companion.name,
                "sha256": companion_digest,
                "bytes": str(len(b"verified companion")),
            },
        ]}],
    }
    manifest_path = artifact_root / "build.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    original_files = {path.relative_to(artifact_root): path.read_bytes() for path in artifact_root.rglob("*") if path.is_file()}
    monkeypatch.setenv("CMRU_BUILD_OUTPUT_ROOT", str(artifact_root))
    monkeypatch.setenv("CMRU_BUILD_OUTPUT_ID", OUTPUT_ID)
    monkeypatch.setenv("CMRU_BUILD_OUTPUT_PROJECT", "alpha")
    monkeypatch.setenv("CMRU_BUILD_SOURCE_COMMIT", SOURCE_COMMIT)
    monkeypatch.setenv("CMRU_BUILD_SOURCE_DATE", manifest["source_commit_date"])
    monkeypatch.setenv("VERSION", "1.2.3")
    monkeypatch.setattr(handlers, "_require_env", lambda _name: "configured")
    monkeypatch.setattr(handlers, "read_wheel_version", lambda _path: "1.2.3")

    class ExistingTagsOnlyPublisher:
        def __init__(self):
            self.published = []

        def get_tag_commit(self, tag):
            if tag == "alpha-v1.2.3":
                return SOURCE_COMMIT
            if tag == "alpha-latest":
                return "c" * 40
            return None

        def get_release_by_tag(self, tag):
            return {"id": 1} if tag in {"alpha-v1.2.3", "alpha-latest"} else None

        def publish(self, tag, _title, _notes, assets, *, recreate=False,
                    target_commitish=None, require_existing_release=False):
            self.published.append((tag, recreate, target_commitish, require_existing_release,
                                   [(path.name, path.read_bytes()) for path in assets]))

        def asset_download_url(self, tag, name):
            return f"https://example.invalid/{tag}/{name}"

    publisher = ExistingTagsOnlyPublisher()
    monkeypatch.setattr(handlers, "GitHubReleases", lambda *_args, **_kwargs: publisher)
    handlers.cmd_wheel_publish(SimpleNamespace(
        cwd=str(tmp_path), prefix="alpha", glob="*.whl", notes_env=None,
        extra_asset=[companion.name],
    ))

    assert [call[0] for call in publisher.published] == ["alpha-v1.2.3", "alpha-latest"]
    assert all(not call[1] and call[2] is None and call[3] for call in publisher.published)
    assert publisher.published[0][4][:2] == [
        ("alpha-1.2.3.whl", b"verified wheel"),
        ("alpha-1.2.3.whl.sha256", f"{digest}  alpha-1.2.3.whl\n".encode()),
    ]
    assert publisher.published[0][4][2] == (companion.name, b"verified companion")
    assert publisher.published[1][4][0][0] == "latest.json"
    assert {path.relative_to(artifact_root): path.read_bytes() for path in artifact_root.rglob("*") if path.is_file()} == original_files
    transaction.validate_build_output_tree(artifact_root, "alpha", OUTPUT_ID)
    monkeypatch.setenv("CMRU_BUILD_SOURCE_COMMIT", "f" * 40)
    with pytest.raises(RuntimeError, match="SOURCE_COMMIT"):
        handlers._build_output_record(None)
    monkeypatch.setenv("CMRU_BUILD_SOURCE_COMMIT", SOURCE_COMMIT)
    monkeypatch.setenv("CMRU_BUILD_SOURCE_DATE", "2026-09-28T12:00:00+00:00")
    with pytest.raises(RuntimeError, match="SOURCE_DATE"):
        handlers._build_output_record(None)


def test_retained_publish_refuses_missing_or_mismatched_tags_before_upload(
    tmp_path,
):
    artifact_root = tmp_path / OUTPUT_ID
    asset = artifact_root / "dist" / "alpha-1.2.3.whl"
    asset.parent.mkdir(parents=True)
    asset.write_bytes(b"wheel")
    class TagLookup:
        def __init__(self, refs, release_tags=None):
            self.refs = refs
            self.release_tags = set(release_tags or {"alpha-v1.2.3", "alpha-latest"})
            self.uploads = []

        def get_tag_commit(self, tag):
            return self.refs.get(tag)

        def get_release_by_tag(self, tag):
            return {"id": 1} if tag in self.release_tags else None

        def publish(self, *args, **kwargs):
            self.uploads.append((args, kwargs))

        def asset_download_url(self, tag, name):
            return f"https://example.invalid/{tag}/{name}"

    for refs, release_tags in (
        (
            {"alpha-v1.2.3": SOURCE_COMMIT, "alpha-latest": "c" * 40},
            {"alpha-v1.2.3"},
        ),  # latest Release is missing
        (
            {"alpha-v1.2.3": SOURCE_COMMIT},
            {"alpha-v1.2.3", "alpha-latest"},
        ),  # latest Git tag is missing
        (
            {"alpha-v1.2.3": "d" * 40, "alpha-latest": "c" * 40},
            {"alpha-v1.2.3", "alpha-latest"},
        ),  # version tag does not identify the retained source
    ):
        publisher = TagLookup(refs, release_tags)
        with pytest.raises(SystemExit):
            release.publish_versioned(
                publisher, prefix="alpha", version="1.2.3", asset_path=asset,
                require_existing_targets=True, latest_pointer_recreate=False,
                expected_tag_commit=SOURCE_COMMIT,
            )
        assert publisher.uploads == []

    for options in (
        {"target_commitish": SOURCE_COMMIT},
        {},  # the default would recreate the latest pointer tag
    ):
        with pytest.raises(SystemExit):
            release.publish_versioned(
                TagLookup({}), prefix="alpha", version="1.2.3", asset_path=asset,
                require_existing_targets=True, **options,
            )


def test_build_output_dev_version_updates_only_preexisting_latest_targets(tmp_path):
    asset = tmp_path / "alpha-1.2.3.dev1.whl"
    asset.write_bytes(b"dev wheel")

    class ExistingLatest:
        def __init__(self):
            self.uploads = []

        def get_release_by_tag(self, tag):
            return {"id": 1} if tag == "alpha-latest" else None

        def get_tag_commit(self, tag):
            return "c" * 40 if tag == "alpha-latest" else None

        def publish(self, *args, **kwargs):
            self.uploads.append((args, kwargs))

    publisher = ExistingLatest()
    result = release.publish_versioned(
        publisher, prefix="alpha", version="1.2.3.dev1", asset_path=asset,
        require_existing_targets=True, latest_pointer_recreate=False,
    )
    assert result["release_tag"] is None
    assert len(publisher.uploads) == 1
    assert publisher.uploads[0][0][0] == "alpha-latest"
    assert publisher.uploads[0][1]["recreate"] is False
    assert publisher.uploads[0][1]["require_existing_release"] is True


def test_build_output_can_skip_latest_pointer_validation_when_disabled(tmp_path):
    asset = tmp_path / "alpha-1.2.3.whl"
    asset.write_bytes(b"wheel")

    class VersionOnlyPublisher:
        def __init__(self):
            self.uploads = []

        def get_release_by_tag(self, tag):
            return {"id": 1} if tag == "alpha-v1.2.3" else None

        def get_tag_commit(self, tag):
            return SOURCE_COMMIT if tag == "alpha-v1.2.3" else None

        def publish(self, *args, **kwargs):
            self.uploads.append((args, kwargs))
            return {"asset_url": "https://example.invalid/alpha.whl"}

        def asset_download_url(self, tag, name):
            return f"https://example.invalid/{tag}/{name}"

    publisher = VersionOnlyPublisher()
    result = release.publish_versioned(
        publisher,
        prefix="alpha",
        version="1.2.3",
        asset_path=asset,
        latest_pointer=False,
        require_existing_targets=True,
        latest_pointer_recreate=False,
        expected_tag_commit=SOURCE_COMMIT,
    )
    assert result["release_tag"] == "alpha-v1.2.3"
    assert [call[0][0] for call in publisher.uploads] == ["alpha-v1.2.3"]


def test_github_tag_commit_lookup_resolves_tag_ref_and_handles_unverifiable_results(
    monkeypatch,
):
    client = release.GitHubReleases("owner", "repo", "token")
    requests = []

    def found(method, url, *_args, **_kwargs):
        requests.append((method, url))
        return 200, json.dumps({"sha": SOURCE_COMMIT})

    monkeypatch.setattr(client, "_request", found)
    assert client.get_tag_commit("alpha-v1.2.3") == SOURCE_COMMIT
    assert requests == [("GET", "https://api.github.com/repos/owner/repo/commits/tags/alpha-v1.2.3")]

    monkeypatch.setattr(client, "_request", lambda *_args, **_kwargs: (404, ""))
    assert client.get_tag_commit("missing") is None

    monkeypatch.setattr(client, "_request", lambda *_args, **_kwargs: (403, "forbidden"))
    with pytest.raises(SystemExit):
        client.get_tag_commit("hidden")

    for status, body in ((200, "not-json"), (200, json.dumps({"sha": "bad"}))):
        monkeypatch.setattr(client, "_request", lambda *_args, **_kwargs: (status, body))
        with pytest.raises(SystemExit):
            client.get_tag_commit("malformed")


def test_github_publish_refuses_release_creation_or_recreation_in_existing_only_mode(
    monkeypatch,
):
    client = release.GitHubReleases("owner", "repo", "token")
    monkeypatch.setattr(client, "get_release_by_tag", lambda _tag: None)
    monkeypatch.setattr(
        client, "create_release",
        lambda *_args, **_kwargs: pytest.fail("must not create a Release record"),
    )
    with pytest.raises(SystemExit):
        client.publish("alpha-v1", "title", "notes", [], require_existing_release=True)

    monkeypatch.setattr(client, "get_release_by_tag", lambda _tag: {"id": 1})
    with pytest.raises(SystemExit):
        client.publish(
            "alpha-v1", "title", "notes", [], recreate=True,
            require_existing_release=True,
        )


def test_bound_cmru_launcher_precedes_ambient_path_and_checks_identity(monkeypatch, tmp_path):
    ambient = tmp_path / "ambient"
    ambient.mkdir()
    fake_cmru = ambient / "cmru"
    fake_cmru.write_text("#!/bin/sh\nexit 99\n", encoding="utf-8")
    fake_cmru.chmod(0o700)
    monkeypatch.setenv("PATH", str(ambient))
    monkeypatch.setattr("cmru.cli_support.cmru_version", lambda: "9.8.7")
    checks = []

    def fake_version_probe(argv, **kwargs):
        checks.append((argv, kwargs))
        return SimpleNamespace(returncode=0, stdout="cmru 9.8.7\n", stderr="")

    monkeypatch.setattr(cli.subprocess, "run", fake_version_probe)
    launcher_dir = tmp_path / "bound"
    launcher_dir.mkdir()
    launcher = cli._create_bound_cmru_launcher(launcher_dir)

    assert launcher == launcher_dir / "cmru"
    assert Path(checks[0][1]["env"]["PATH"].split(os.pathsep, 1)[0]) == launcher_dir
    assert checks[0][0] == [str(launcher), "version"]
    assert "exec " in launcher.read_text(encoding="utf-8")
    assert '"$@"' in launcher.read_text(encoding="utf-8")

    monkeypatch.setattr(
        cli.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=0, stdout="cmru 9.8.6\n", stderr=""),
    )
    mismatched = tmp_path / "mismatched"
    mismatched.mkdir()
    with pytest.raises(RuntimeError, match="identity verification"):
        cli._create_bound_cmru_launcher(mismatched)


def test_bound_cmru_launcher_refuses_path_that_resolves_elsewhere(monkeypatch, tmp_path):
    unexpected = tmp_path / "unexpected-cmru"
    monkeypatch.setattr(cli.shutil, "which", lambda _name, path=None: str(unexpected))
    launcher_dir = tmp_path / "bound"
    launcher_dir.mkdir()
    with pytest.raises(RuntimeError, match="project PATH does not resolve to this launcher"):
        cli._create_bound_cmru_launcher(launcher_dir)


def test_run_project_step_protects_bound_cmru_environment(monkeypatch, tmp_path):
    step = _step(
        "build",
        [{"label": "build", "argv": ["cmru", "tester-gate"], "cwd": "."}],
        step_env={"CMRU_BIN": "/wrong/step", "PATH": "/wrong/step/bin"},
        quiet=True,
    )
    project = SimpleNamespace(
        name="alpha", cwd="alpha", project_root=tmp_path / "alpha", env={"CMRU_BIN": "/wrong/project"},
        runtime_kind="none", build_metadata={}, runner_steps={"build": step},
    )
    launcher_directory = []

    def bind_runtime(directory):
        launcher_directory.append(directory)
        return directory / "cmru"

    monkeypatch.setattr(
        cli, "_create_bound_cmru_launcher", bind_runtime
    )
    captured = {}
    monkeypatch.setattr(cli, "execute_step", lambda _step, _root, _logs, **kwargs: captured.update(kwargs))

    cli.run_project_step(project, "build", tmp_path, tmp_path / "logs")

    assert captured["extra_env"] == {"CMRU_BIN": "/wrong/project"}
    assert captured["protected_env"]["CMRU_BIN"] == str(launcher_directory[0] / "cmru")
    assert captured["path_prefixes"] == (launcher_directory[0],)


def test_runner_reapplies_runtime_binding_after_project_environment_commands(
    monkeypatch, tmp_path,
):
    step = _step(
        "build",
        [{"label": "check", "argv": ["cmru", "version"], "cwd": "."}],
        step_env={"CMRU_BIN": "/wrong/step", "PATH": "/wrong/step/bin"},
        env_command=["./set-env"],
        quiet=True,
    )
    monkeypatch.setattr(
        runner,
        "apply_env_command",
        lambda *_args: os.environ.update({"CMRU_BIN": "/wrong/env-command", "PATH": "/wrong/env-command/bin"}),
    )
    monkeypatch.setattr(runner, "maybe_login_multi", lambda *_args: None)
    captured = []
    monkeypatch.setattr(
        runner,
        "run_command",
        lambda argv, cwd, log_file, **_kwargs: (
            captured.append((argv, dict(os.environ)))
            or runner.CommandResult(elapsed_seconds=0, evidence=None)
        ),
    )

    runner.execute_step(
        step,
        tmp_path,
        tmp_path / "logs",
        extra_env={"PATH": "/wrong/project", "CMRU_BIN": "/wrong/project/cmru"},
        protected_env={"CMRU_BIN": "/bound/cmru"},
        path_prefixes=(Path("/bound"),),
    )

    argv, command_env = captured[0]
    assert argv == ["cmru", "version"]
    assert command_env["CMRU_BIN"] == "/bound/cmru"
    assert command_env["PATH"].split(":", 1)[0] == "/bound"
