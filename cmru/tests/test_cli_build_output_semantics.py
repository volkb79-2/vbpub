"""Behavioral contract for publishing one retained, verified build output."""
from __future__ import annotations

import hashlib
from html.parser import HTMLParser
import json
import os
import re
import tomllib
import venv
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

from markdown_it import MarkdownIt
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
            "source_mode": "origin-main",
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
        ("artifact-dir-type", "malformed artifact directory"),
        ("artifact-dir-empty", "malformed artifact directory"),
        ("artifact-dir-backslash", "malformed artifact directory"),
        ("artifact-dir-absolute", "malformed artifact directory"),
        ("artifact-dir-noncanonical", "malformed artifact directory"),
        ("artifact-dir-duplicate", "malformed artifact directory"),
        ("artifact-dir-missing", "declared artifact directory is missing"),
        ("artifact-files-empty", "malformed artifact directory"),
        ("file-shape", "malformed file inventory"),
        ("file-path", "unsafe file coordinate"),
        ("file-path-empty", "unsafe file coordinate"),
        ("file-path-type", "unsafe file coordinate"),
        ("file-path-dot", "unsafe file coordinate"),
        ("file-path-backslash", "unsafe file coordinate"),
        ("file-path-absolute", "unsafe file coordinate"),
        ("file-path-noncanonical", "unsafe file coordinate"),
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
    elif fault == "artifact-dir-type":
        manifest["artifacts"][0]["directory"] = 17
    elif fault == "artifact-dir-empty":
        manifest["artifacts"][0]["directory"] = ""
    elif fault == "artifact-dir-backslash":
        manifest["artifacts"][0]["directory"] = "dist\\nested"
    elif fault == "artifact-dir-absolute":
        manifest["artifacts"][0]["directory"] = "/"
    elif fault == "artifact-dir-noncanonical":
        manifest["artifacts"][0]["directory"] = "dist/"
    elif fault == "artifact-dir-duplicate":
        manifest["artifacts"].append(dict(manifest["artifacts"][0]))
    elif fault == "artifact-dir-missing":
        manifest["artifacts"][0]["directory"] = "absent"
    elif fault == "artifact-files-empty":
        manifest["artifacts"][0]["files"] = []
    elif fault == "file-shape":
        manifest["artifacts"][0]["files"] = [{}]
    elif fault == "file-path":
        manifest["artifacts"][0]["files"][0]["path"] = "../alpha.whl"
    elif fault == "file-path-empty":
        manifest["artifacts"][0]["files"][0]["path"] = ""
    elif fault == "file-path-type":
        manifest["artifacts"][0]["files"][0]["path"] = 17
    elif fault == "file-path-dot":
        manifest["artifacts"][0]["files"][0]["path"] = "."
    elif fault == "file-path-backslash":
        manifest["artifacts"][0]["files"][0]["path"] = "nested\\alpha.whl"
    elif fault == "file-path-absolute":
        manifest["artifacts"][0]["files"][0]["path"] = "/alpha.whl"
    elif fault == "file-path-noncanonical":
        manifest["artifacts"][0]["files"][0]["path"] = "alpha.whl/"
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


def test_tls_edge_retained_tarball_inventory_contains_the_publisher_version_file():
    repo_root = Path(__file__).resolve().parents[2]
    if not (repo_root / "tls-edge" / "cmru.toml").is_file():
        # The disposable gate fixtures (canary, mutation) copy a closed set of
        # sibling files that excludes tls-edge; same guard as test_installer's
        # real-config test. The full-suite lane still runs it.
        pytest.skip("tls-edge tree is not part of the disposable gate fixture")
    config = tomllib.loads((repo_root / "tls-edge" / "cmru.toml").read_text(encoding="utf-8"))
    script = (repo_root / "tls-edge" / "scripts" / "build-artifact.sh").read_text(encoding="utf-8")
    publish_argv = config["steps"]["push"]["commands"][0]["argv"]

    assert config["project"]["release"]["artifact_dirs"] == ["dist"]
    assert publish_argv[publish_argv.index("--version-file") + 1] == "dist/VERSION"
    assert 'cp -p "$VERSION_FILE" "$DIST_DIR/VERSION"' in script


def test_tarball_publish_reads_version_and_tarball_from_real_retained_inventory(
    monkeypatch, tmp_path,
):
    artifact_root = tmp_path / "artifacts"
    dist = artifact_root / "dist"
    dist.mkdir(parents=True)
    version_file = dist / "VERSION"
    version_file.write_text("1.2.3\n", encoding="utf-8")
    tarball = dist / "tls-edge-v1.2.3.tar.xz"
    tarball.write_bytes(b"tarball bytes")

    def entry(path: Path) -> dict[str, str]:
        data = path.read_bytes()
        return {
            "path": path.name,
            "sha256": hashlib.sha256(data).hexdigest(),
            "bytes": str(len(data)),
        }

    record = {
        "artifact_root": artifact_root,
        "manifest": {"artifacts": [{
            "directory": "dist",
            "files": [entry(version_file), entry(tarball)],
        }]},
    }
    monkeypatch.setattr(handlers, "_require_env", lambda _name: "configured")
    monkeypatch.setattr(handlers, "_build_output_record", lambda _args: record)
    published = []
    monkeypatch.setattr(
        handlers, "_publish_versioned_artifacts",
        lambda _gh, **kwargs: published.append(kwargs) or {"ok": True},
    )

    handlers.cmd_tarball_publish(SimpleNamespace(
        cwd=str(tmp_path), prefix="tls-edge", glob="tls-edge-v*.tar.xz",
        notes_env=None, version_env=None, version_file="dist/VERSION",
    ))

    assert published == [{
        "prefix": "tls-edge", "version": "1.2.3", "asset_path": tarball,
        "notes": None, "extra_assets": None, "build_output": record,
    }]


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


_GFM_MARKDOWN = MarkdownIt("gfm-like", {"html": True})
_NON_PROSE_HTML_TAGS = {
    "code", "del", "kbd", "noscript", "pre", "s", "samp", "script", "style",
    "strike", "svg", "table", "template", "textarea", "title", "xmp", "iframe", "noembed", "noframes",
}
_HTML_BLOCK_TAGS = {
    "address", "article", "aside", "blockquote", "div", "dl", "fieldset",
    "dd", "details", "dt", "figcaption", "figure", "footer", "form", "h1", "h2", "h3", "h4",
    "h5", "h6", "header", "hr", "li", "main", "nav", "ol", "p",
    "section", "summary", "ul",
}
_HTML_HEADING_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6"}
_VOID_HTML_TAGS = {
    "area", "base", "br", "col", "embed", "hr", "img", "input", "link",
    "meta", "param", "source", "track", "wbr",
}
_OMITTED_MARKER = "\ufffc"
_HTML_BLOCK_SEPARATOR = "\ufdd0"


@dataclass
class _HTMLStackEntry:
    tag: str
    suppressed: bool
    closed_details: bool = False
    visible_summary_owner: _HTMLStackEntry | None = None
    details_summary_seen: bool = False
    source_code_container: bool = False


class _NonProseHTMLTracker(HTMLParser):
    """Track visible and non-prose HTML elements across Markdown tokens."""

    def __init__(
        self,
        *,
        include_headings: bool = False,
        include_source_code_claims: bool = False,
    ):
        super().__init__(convert_charrefs=True)
        self.stack: list[_HTMLStackEntry] = []
        self.capture_data = False
        self.include_headings = include_headings
        self.include_source_code_claims = include_source_code_claims
        self.heading_depth = 0
        self.visible_chunks: list[str] = []

    @property
    def suppresses(self) -> bool:
        for index, element in enumerate(self.stack):
            if not element.suppressed:
                continue
            if element.closed_details and any(
                child.visible_summary_owner is element
                for child in self.stack[index + 1:]
            ):
                continue
            return True
        return False

    @property
    def suppresses_source_code_claims(self) -> bool:
        for index, element in enumerate(self.stack):
            if not element.suppressed or element.source_code_container:
                continue
            if element.closed_details and any(
                child.visible_summary_owner is element
                for child in self.stack[index + 1:]
            ):
                continue
            return True
        return False

    @property
    def captures_source_code_claims(self) -> bool:
        return (
            self.include_source_code_claims
            and any(element.source_code_container for element in self.stack)
            and not self.suppresses_source_code_claims
        )

    def handle_starttag(self, tag, attrs):
        hidden = any(name and name.lower() == "hidden" for name, _value in attrs)
        is_open = any(name and name.lower() == "open" for name, _value in attrs)
        closed_details = tag == "details" and not is_open and not hidden
        suppressed = tag in _NON_PROSE_HTML_TAGS or hidden or closed_details
        source_code_container = (
            tag in {"code", "kbd", "pre", "samp"}
            and not hidden
            and not closed_details
        )
        if tag in _HTML_HEADING_TAGS:
            self.heading_depth += 1
        visible_summary_owner = None
        if tag == "summary" and self.stack and self.stack[-1].tag == "details":
            details = self.stack[-1]
            if not details.details_summary_seen:
                visible_summary_owner = details
            details.details_summary_seen = True
        if self.capture_data and tag in _HTML_BLOCK_TAGS:
            self.visible_chunks.append(_HTML_BLOCK_SEPARATOR)
        if self.capture_data and tag == "img":
            self.visible_chunks.append(_OMITTED_MARKER)
        if self.capture_data and suppressed:
            self.visible_chunks.append(_OMITTED_MARKER)
        elif self.capture_data and tag == "br":
            self.visible_chunks.append(" ")
        elif self.capture_data and tag == "hr":
            self.visible_chunks.append(_HTML_BLOCK_SEPARATOR)
        if tag not in _VOID_HTML_TAGS:
            self.stack.append(
                _HTMLStackEntry(
                    tag,
                    suppressed,
                    closed_details,
                    visible_summary_owner,
                    source_code_container=source_code_container,
                )
            )

    def handle_endtag(self, tag):
        was_suppressed = self.suppresses
        matching = [
            index for index, element in enumerate(self.stack) if element.tag == tag
        ]
        if matching:
            del self.stack[matching[-1]:]
            if tag in _HTML_HEADING_TAGS:
                self.heading_depth = max(0, self.heading_depth - 1)
        if self.capture_data and tag in _HTML_BLOCK_TAGS:
            self.visible_chunks.append(_HTML_BLOCK_SEPARATOR)
        if self.capture_data and was_suppressed and not self.suppresses:
            self.visible_chunks.append(_OMITTED_MARKER)

    def handle_startendtag(self, tag, attrs):
        # In HTML, the slash does not self-close non-void elements such as
        # <code/> or <pre/>; the browser keeps their contents in that element.
        if tag in _VOID_HTML_TAGS:
            hidden = any(name and name.lower() == "hidden" for name, _value in attrs)
            if self.capture_data and (tag == "img" or hidden):
                self.visible_chunks.append(_OMITTED_MARKER)
            elif self.capture_data and tag == "br":
                self.visible_chunks.append(" ")
            elif self.capture_data and tag == "hr":
                self.visible_chunks.append(_HTML_BLOCK_SEPARATOR)
            return
        self.handle_starttag(tag, attrs)

    def handle_data(self, data):
        if not self.capture_data:
            return
        if self.suppresses:
            if (
                self.include_source_code_claims
                and any(element.source_code_container for element in self.stack)
                and not self.suppresses_source_code_claims
            ):
                self.visible_chunks.append(data)
            else:
                self.visible_chunks.append(_OMITTED_MARKER)
        elif self.heading_depth and not self.include_headings:
            self.visible_chunks.append(_OMITTED_MARKER)
        else:
            self.visible_chunks.append(data)

    def handle_comment(self, _data):
        if self.capture_data:
            self.visible_chunks.append(_OMITTED_MARKER)

    def handle_decl(self, _decl):
        if self.capture_data:
            self.visible_chunks.append(_OMITTED_MARKER)

    def unknown_decl(self, _data):
        if self.capture_data:
            self.visible_chunks.append(_OMITTED_MARKER)

    def take_visible_text(self) -> str:
        text = "".join(self.visible_chunks)
        self.visible_chunks.clear()
        return text


def _markdown_prose_blocks(
    text: str,
    *,
    include_headings: bool = False,
    include_tables: bool = False,
    include_source_code_claims: bool = False,
) -> list[str]:
    """Return visible prose and optionally source claims in inline code."""
    tokens = _GFM_MARKDOWN.parse(text)
    blocks: list[str] = []
    table_depth = 0
    table_row_cells: list[str] | None = None
    non_prose_html = _NonProseHTMLTracker(
        include_headings=include_headings,
        include_source_code_claims=include_source_code_claims,
    )
    for index, token in enumerate(tokens):
        if token.type == "html_block":
            non_prose_html.capture_data = True
            non_prose_html.feed(token.content)
            non_prose_html.capture_data = False
            for raw_block in non_prose_html.take_visible_text().split(_HTML_BLOCK_SEPARATOR):
                visible_html = " ".join(raw_block.split())
                if visible_html:
                    blocks.append(visible_html)
            continue
        if token.type == "table_open":
            table_depth += 1
            continue
        if token.type == "table_close":
            table_depth -= 1
            continue
        if include_tables and table_depth and token.type == "tr_open":
            table_row_cells = []
            continue
        if include_tables and table_depth and token.type == "tr_close":
            if table_row_cells:
                blocks.append(" | ".join(table_row_cells))
            table_row_cells = None
            continue
        if token.type != "inline" or index == 0:
            continue
        previous_type = tokens[index - 1].type
        if table_depth:
            if not include_tables or previous_type not in {"th_open", "td_open"}:
                continue
        elif previous_type != "paragraph_open" and not (
            include_headings and previous_type == "heading_open"
        ):
            continue
        visible: list[str] = []
        strikethrough_depth = 0
        for child in token.children or ():
            if child.type == "s_open":
                strikethrough_depth += 1
                visible.append(_OMITTED_MARKER)
                continue
            if child.type == "s_close":
                strikethrough_depth = max(0, strikethrough_depth - 1)
                visible.append(_OMITTED_MARKER)
                continue
            if child.type == "html_inline":
                was_capturing = non_prose_html.capture_data
                non_prose_html.capture_data = True
                non_prose_html.feed(child.content)
                inline_html = non_prose_html.take_visible_text()
                if strikethrough_depth:
                    non_prose_html.capture_data = was_capturing
                    continue
                html_blocks = inline_html.split(_HTML_BLOCK_SEPARATOR)
                if len(html_blocks) == 1:
                    visible.append(inline_html)
                else:
                    visible.append(html_blocks[0])
                    current = " ".join("".join(visible).split())
                    if current:
                        blocks.append(current)
                    visible = []
                    for html_block in html_blocks[1:-1]:
                        current = " ".join(html_block.split())
                        if current:
                            blocks.append(current)
                    visible.append(html_blocks[-1])
                non_prose_html.capture_data = was_capturing
                continue
            if strikethrough_depth:
                continue
            if non_prose_html.suppresses and not (
                non_prose_html.captures_source_code_claims
                and child.type in {"text", "text_special", "softbreak", "hardbreak"}
            ):
                continue
            if child.type in {"text", "text_special", "softbreak", "hardbreak"}:
                # Markdown-it has already decoded character references. Decoding
                # text_special again would turn a literal `&#32;` into a space
                # and let encoded non-matching text satisfy the prose oracle.
                visible.append(child.content if child.type in {"text", "text_special"} else " ")
            elif child.type == "code_inline":
                # These identifiers can carry the source of a release claim,
                # including when documentation formats them as inline code.
                source_references = re.findall(
                    r"\b(?:CMRU_RELEASE_(?:COMMIT|TAG)|HEAD|origin/main|main|trunk)\b|[=|]",
                    child.content,
                    flags=re.IGNORECASE,
                )
                if include_source_code_claims and source_references:
                    visible.append(child.content)
                elif source_references:
                    visible.append(" ".join(source_references))
                else:
                    # Omitted inline code can narrow a claim between
                    # surrounding words. Preserve a boundary rather than
                    # concatenating them.
                    visible.append(_OMITTED_MARKER)
            elif child.type == "image":
                # Alt text is not prose for this assertion, but it must not
                # make words on either side appear adjacent.
                visible.append(_OMITTED_MARKER)
        rendered = "".join(visible)
        if table_depth:
            if include_tables and table_row_cells is not None:
                table_row_cells.append(rendered)
        else:
            blocks.append(rendered)
    return blocks


def test_markdown_wrapped_lines_join_but_paragraphs_remain_separate():
    wrapped = _markdown_prose_blocks("The phrase clears\nambient values here.")
    separated = _markdown_prose_blocks("The phrase clears\n\nambient values here.")
    assert any("clears ambient values" in paragraph for paragraph in wrapped)
    assert not any("clears ambient values" in paragraph for paragraph in separated)


def test_markdown_assertion_blocks_do_not_cross_list_items_or_code_fences():
    separate_items = _markdown_prose_blocks(
        "- CMRU_RELEASE_COMMIT and CMRU_RELEASE_TAG\n"
        "- clears ambient values\n"
        "- not an authentication token"
    )
    separate_headings = _markdown_prose_blocks(
        "# CMRU_RELEASE_COMMIT and CMRU_RELEASE_TAG\n"
        "# clears ambient values\n"
        "# not an authentication token"
    )
    setext_heading = _markdown_prose_blocks(
        "CMRU_RELEASE_COMMIT CMRU_RELEASE_TAG clears ambient values; "
        "not an authentication token\n---"
    )
    multiline_setext_heading = _markdown_prose_blocks(
        "CMRU_RELEASE_COMMIT CMRU_RELEASE_TAG clears ambient values; "
        "not an authentication token\ncontinued title\n---"
    )
    code_only = _markdown_prose_blocks(
        "````text\n```python\nCMRU_RELEASE_COMMIT CMRU_RELEASE_TAG "
        "clears ambient values; not an authentication token\n````"
    )
    table_only = _markdown_prose_blocks(
        "CMRU_RELEASE_COMMIT | CMRU_RELEASE_TAG | clears ambient values\n"
        "--- | --- | ---\n"
        "not an authentication token | explanatory data | table data"
    )
    tab_indented_code = _markdown_prose_blocks(
        "\tCMRU_RELEASE_COMMIT CMRU_RELEASE_TAG clears ambient values; "
        "not an authentication token"
    )
    indented_code = _markdown_prose_blocks(
        "    CMRU_RELEASE_COMMIT CMRU_RELEASE_TAG clears ambient values; "
        "not an authentication token"
    )
    list_continuation = _markdown_prose_blocks(
        "- CMRU_RELEASE_COMMIT and CMRU_RELEASE_TAG\n"
        "    clears ambient values; not an authentication token"
    )
    list_fenced_code = _markdown_prose_blocks(
        "- release identity\n"
        "    ```text\n"
        "    CMRU_RELEASE_COMMIT CMRU_RELEASE_TAG clears ambient values; "
        "not an authentication token\n"
        "    ```"
    )
    details_only = _markdown_prose_blocks(
        "<details><summary>"
        "CMRU_RELEASE_COMMIT CMRU_RELEASE_TAG clears ambient values; "
        "not an authentication token"
        "</summary><p>Details</p></details>"
    )
    hanging_indentation = _markdown_prose_blocks(
        "CMRU_RELEASE_COMMIT CMRU_RELEASE_TAG clears ambient values;\n"
        "    not an authentication token"
    )
    inline_html = _markdown_prose_blocks(
        "<span>CMRU_RELEASE_COMMIT CMRU_RELEASE_TAG clears ambient values; "
        "not an authentication token</span>"
    )
    type7_html_inside_paragraph = _markdown_prose_blocks(
        "A paragraph starts\n"
        "<Warning>\n"
        "CMRU_RELEASE_COMMIT CMRU_RELEASE_TAG clears ambient values; "
        "not an authentication token"
    )
    prose_after_comment = _markdown_prose_blocks(
        "<!-- hidden CMRU_RELEASE_COMMIT CMRU_RELEASE_TAG clears ambient values; "
        "not an authentication token -->\n"
        "CMRU_RELEASE_COMMIT CMRU_RELEASE_TAG clears ambient values; "
        "not an authentication token"
    )
    required = (
        "CMRU_RELEASE_COMMIT", "CMRU_RELEASE_TAG", "clears ambient values",
        "not an authentication token",
    )
    assert not any(all(item in block for item in required) for block in separate_items)
    assert not any(all(item in block for item in required) for block in separate_headings)
    assert not any(all(item in block for item in required) for block in setext_heading)
    assert not any(all(item in block for item in required) for block in multiline_setext_heading)
    assert not any(all(item in block for item in required) for block in code_only)
    assert not any(all(item in block for item in required) for block in list_fenced_code)
    assert not any(all(item in block for item in required) for block in table_only)
    assert not any(all(item in block for item in required) for block in tab_indented_code)
    assert not any(all(item in block for item in required) for block in indented_code)
    assert any(all(item in block for item in required) for block in details_only)
    assert any(all(item in block for item in required) for block in hanging_indentation)
    assert any(all(item in block for item in required) for block in inline_html)
    assert any(all(item in block for item in required) for block in type7_html_inside_paragraph)
    assert any(all(item in block for item in required) for block in list_continuation)
    assert any(all(item in block for item in required) for block in prose_after_comment)


def test_markdown_table_single_cell_rows_and_raw_html_are_not_prose():
    required = (
        "CMRU_RELEASE_COMMIT", "CMRU_RELEASE_TAG", "clears ambient values",
        "not an authentication token",
    )
    single_cell_table_row = _markdown_prose_blocks(
        "| name |\n| --- |\n"
        "CMRU_RELEASE_COMMIT CMRU_RELEASE_TAG clears ambient values; "
        "not an authentication token\n\n"
        "CMRU_RELEASE_COMMIT CMRU_RELEASE_TAG clears ambient values; "
        "not an authentication token"
    )
    raw_html = _markdown_prose_blocks(
        "<pre>\n\nCMRU_RELEASE_COMMIT CMRU_RELEASE_TAG clears ambient values; "
        "not an authentication token\n\n</pre>\n"
        "<script>\n\nCMRU_RELEASE_COMMIT CMRU_RELEASE_TAG clears ambient values; "
        "not an authentication token\n\n</script>\n\n"
        "CMRU_RELEASE_COMMIT CMRU_RELEASE_TAG clears ambient values; "
        "not an authentication token"
    )
    custom_html_tag = _markdown_prose_blocks(
        "<Warning>\n"
        "\n"
        "CMRU_RELEASE_COMMIT CMRU_RELEASE_TAG clears ambient values; "
        "not an authentication token\n\n"
        "</Warning>\n\n"
        "CMRU_RELEASE_COMMIT CMRU_RELEASE_TAG clears ambient values; "
        "not an authentication token"
    )
    standard_html_container = _markdown_prose_blocks(
        "<div>\n\n"
        "CMRU_RELEASE_COMMIT CMRU_RELEASE_TAG clears ambient values; "
        "not an authentication token\n\n"
        "</div>\n\n"
        "CMRU_RELEASE_COMMIT CMRU_RELEASE_TAG clears ambient values; "
        "not an authentication token"
    )
    html_block_content_after_blank = _markdown_prose_blocks(
        "<div>\n\n"
        "CMRU_RELEASE_COMMIT CMRU_RELEASE_TAG clears ambient values; "
        "not an authentication token\n\n"
        "</div>\n\n"
        "CMRU_RELEASE_COMMIT CMRU_RELEASE_TAG clears ambient values; "
        "not an authentication token"
    )
    html_close_without_blank = _markdown_prose_blocks(
        "<div>\n"
        "</div>\n"
        "CMRU_RELEASE_COMMIT CMRU_RELEASE_TAG clears ambient values; "
        "not an authentication token\n\n"
        "CMRU_RELEASE_COMMIT CMRU_RELEASE_TAG clears ambient values; "
        "not an authentication token"
    )
    malformed_table = _markdown_prose_blocks(
        "| CMRU_RELEASE_COMMIT | CMRU_RELEASE_TAG | clears ambient values |\n"
        "| --- | --- |\n"
        "not an authentication token"
    )
    pipe_prefixed_prose = _markdown_prose_blocks(
        "| CMRU_RELEASE_COMMIT CMRU_RELEASE_TAG clears ambient values; "
        "not an authentication token"
    )
    commented_html_closer = _markdown_prose_blocks(
        "<details>\n"
        "<!-- </details> -->\n\n"
        "CMRU_RELEASE_COMMIT CMRU_RELEASE_TAG clears ambient values; "
        "not an authentication token\n\n"
        "</details>\n\n"
        "CMRU_RELEASE_COMMIT CMRU_RELEASE_TAG clears ambient values; "
        "not an authentication token"
    )
    commented_html_opening = _markdown_prose_blocks(
        "<details><!-- comment starts on the opening line\n"
        "-->\n\n"
        "CMRU_RELEASE_COMMIT CMRU_RELEASE_TAG clears ambient values; "
        "not an authentication token\n\n"
        "</details>\n\n"
        "CMRU_RELEASE_COMMIT CMRU_RELEASE_TAG clears ambient values; "
        "not an authentication token"
    )
    commented_tag_before_container = _markdown_prose_blocks(
        "<!-- padding </div> --> <div>\n\n"
        "CMRU_RELEASE_COMMIT CMRU_RELEASE_TAG clears ambient values; "
        "not an authentication token\n\n"
        "</div>\n\n"
        "CMRU_RELEASE_COMMIT CMRU_RELEASE_TAG clears ambient values; "
        "not an authentication token"
    )
    assert sum(all(item in block for item in required) for block in single_cell_table_row) == 1
    assert sum(all(item in block for item in required) for block in raw_html) == 1
    assert sum(all(item in block for item in required) for block in custom_html_tag) == 2
    assert sum(all(item in block for item in required) for block in standard_html_container) == 2
    assert sum(
        all(item in block for item in required) for block in html_block_content_after_blank
    ) == 2
    assert sum(all(item in block for item in required) for block in html_close_without_blank) == 2
    assert any(all(item in block for item in required) for block in malformed_table)
    assert any(all(item in block for item in required) for block in pipe_prefixed_prose)
    assert sum(all(item in block for item in required) for block in commented_html_closer) == 1
    assert sum(
        all(item in block for item in required) for block in commented_html_opening
    ) == 1
    assert sum(
        all(item in block for item in required)
        for block in commented_tag_before_container
    ) == 2


def test_markdown_list_headings_fences_and_code_comments_keep_their_context():
    required = (
        "CMRU_RELEASE_COMMIT", "CMRU_RELEASE_TAG", "clears ambient values",
        "not an authentication token",
    )
    nested_heading = _markdown_prose_blocks(
        "- item\n"
        "    # CMRU_RELEASE_COMMIT CMRU_RELEASE_TAG clears ambient values; "
        "not an authentication token"
    )
    nested_setext_heading = _markdown_prose_blocks(
        "- item\n"
        "    CMRU_RELEASE_COMMIT CMRU_RELEASE_TAG clears ambient values; "
        "not an authentication token\n"
        "    ---"
    )
    inline_list_heading = _markdown_prose_blocks(
        "- # CMRU_RELEASE_COMMIT CMRU_RELEASE_TAG clears ambient values; "
        "not an authentication token"
    )
    inline_list_fence = _markdown_prose_blocks(
        "- ~~~text\n"
        "  CMRU_RELEASE_COMMIT CMRU_RELEASE_TAG clears ambient values; "
        "not an authentication token\n"
        "  ~~~"
    )
    prose_after_nested_fence = _markdown_prose_blocks(
        "- item\n"
        "    ```text\n"
        "    hidden code\n"
        "    ```\n"
        "    CMRU_RELEASE_COMMIT CMRU_RELEASE_TAG clears ambient values; "
        "not an authentication token"
    )
    prose_after_nested_fence_and_blank = _markdown_prose_blocks(
        "- item\n"
        "    ```text\n"
        "    hidden code\n"
        "    ```\n\n"
        "    CMRU_RELEASE_COMMIT CMRU_RELEASE_TAG clears ambient values; "
        "not an authentication token"
    )
    prose_after_code_comment = _markdown_prose_blocks(
        "```text\n"
        "<!-- this is code, not a document comment\n"
        "```\n"
        "CMRU_RELEASE_COMMIT CMRU_RELEASE_TAG clears ambient values; "
        "not an authentication token"
    )
    prose_after_indented_code_comment = _markdown_prose_blocks(
        "    <!-- this is indented code, not a document comment\n"
        "CMRU_RELEASE_COMMIT CMRU_RELEASE_TAG clears ambient values; "
        "not an authentication token"
    )
    prose_after_lazy_blockquote = _markdown_prose_blocks(
        "> quoted preface\n"
        "CMRU_RELEASE_COMMIT CMRU_RELEASE_TAG clears ambient values; "
        "not an authentication token\n\n"
        "CMRU_RELEASE_COMMIT CMRU_RELEASE_TAG clears ambient values; "
        "not an authentication token"
    )
    prose_after_indented_comment_close = _markdown_prose_blocks(
        "<!-- hidden comment\n"
        "    -->\n"
        "CMRU_RELEASE_COMMIT CMRU_RELEASE_TAG clears ambient values; "
        "not an authentication token"
    )
    assert not any(all(item in block for item in required) for block in nested_heading)
    assert not any(all(item in block for item in required) for block in nested_setext_heading)
    assert not any(all(item in block for item in required) for block in inline_list_heading)
    assert not any(all(item in block for item in required) for block in inline_list_fence)
    assert any(all(item in block for item in required) for block in prose_after_nested_fence)
    assert any(all(item in block for item in required) for block in prose_after_nested_fence_and_blank)
    assert any(all(item in block for item in required) for block in prose_after_code_comment)
    assert any(all(item in block for item in required) for block in prose_after_indented_code_comment)
    assert sum(all(item in block for item in required) for block in prose_after_lazy_blockquote) == 2
    assert any(
        all(item in block for item in required)
        for block in prose_after_indented_comment_close
    )


def test_markdown_inline_code_spans_do_not_satisfy_prose_assertion():
    required = (
        "CMRU_RELEASE_COMMIT", "CMRU_RELEASE_TAG", "clears ambient values",
        "not an authentication token",
    )
    inline = _markdown_prose_blocks(
        "`CMRU_RELEASE_COMMIT CMRU_RELEASE_TAG clears ambient values; "
        "not an authentication token`"
    )
    multiline_inline = _markdown_prose_blocks(
        "`CMRU_RELEASE_COMMIT CMRU_RELEASE_TAG clears\n"
        "ambient values; not an authentication token`"
    )
    prose_with_inline_code = _markdown_prose_blocks(
        "CMRU_RELEASE_COMMIT CMRU_RELEASE_TAG clears ambient values; "
        "not an authentication token; see `example`"
    )
    prose_with_identifier_references = _markdown_prose_blocks(
        "`CMRU_RELEASE_COMMIT` and `CMRU_RELEASE_TAG` clear the release; "
        "CMRU clears ambient values and this pair is not an authentication token"
    )
    assert not any(all(item in block for item in required) for block in inline)
    assert not any(all(item in block for item in required) for block in multiline_inline)
    assert any(all(item in block for item in required) for block in prose_with_inline_code)
    assert any(
        all(item in block for item in required)
        for block in prose_with_identifier_references
    )


def test_markdown_prose_oracle_uses_visible_gfm_paragraph_text():
    required = (
        "CMRU_RELEASE_COMMIT", "CMRU_RELEASE_TAG", "clears ambient values",
        "not an authentication token",
    )

    def contains_required_prose(source):
        return any(
            all(item in block for item in required)
            for block in _markdown_prose_blocks(source)
        )

    attribute_only = (
        'See <span title="CMRU_RELEASE_COMMIT CMRU_RELEASE_TAG clears ambient '
        'values; not an authentication token"></span>.'
    )
    image_alt_only = (
        "![CMRU_RELEASE_COMMIT CMRU_RELEASE_TAG clears ambient values; "
        "not an authentication token](identity.png)"
    )
    link_definition_only = (
        '[identity]: /release "CMRU_RELEASE_COMMIT CMRU_RELEASE_TAG clears '
        'ambient values; not an authentication token"'
    )
    nested_table_only = (
        "- | name |\n"
        "  | --- |\n"
        "  | CMRU_RELEASE_COMMIT CMRU_RELEASE_TAG clears ambient values; "
        "not an authentication token |"
    )
    raw_pre_only = (
        "<pre\n\n"
        "CMRU_RELEASE_COMMIT CMRU_RELEASE_TAG clears ambient values; "
        "not an authentication token\n\n"
        "</pre>"
    )
    cdata_only = (
        "<![CDATA[\n"
        "CMRU_RELEASE_COMMIT CMRU_RELEASE_TAG clears ambient values; "
        "not an authentication token\n"
        "]]>"
    )
    custom_html_with_quoted_gt = (
        '<Warning title="a>b">\n'
        "CMRU_RELEASE_COMMIT CMRU_RELEASE_TAG clears ambient values; "
        "not an authentication token"
    )
    invalid_fence = (
        "```lang`bad\n"
        "CMRU_RELEASE_COMMIT CMRU_RELEASE_TAG clears ambient values; "
        "not an authentication token"
    )
    emphasis = (
        "CMRU_RELEASE_COMMIT CMRU_RELEASE_TAG clears *ambient* values; "
        "not an authentication token"
    )
    blockquote = (
        "> CMRU_RELEASE_COMMIT CMRU_RELEASE_TAG clears ambient values; "
        "not an authentication token"
    )
    entity_spaced = (
        "CMRU_RELEASE_COMMIT CMRU_RELEASE_TAG clears&#32;ambient values; "
        "not an authentication token"
    )
    double_encoded_entity = (
        "CMRU_RELEASE_COMMIT CMRU_RELEASE_TAG clears&amp;#32;ambient values; "
        "not an authentication token"
    )
    script_content = (
        "Intro <script>CMRU_RELEASE_COMMIT CMRU_RELEASE_TAG clears ambient values; "
        "not an authentication token</script>"
    )
    script_content_across_paragraphs = (
        "Intro <script>opening\n\n"
        "CMRU_RELEASE_COMMIT CMRU_RELEASE_TAG clears ambient values; "
        "not an authentication token\n\n"
        "</script> closing"
    )
    script_content_closed_in_html_block = (
        "Intro <script>opening\n\n"
        "CMRU_RELEASE_COMMIT CMRU_RELEASE_TAG clears ambient values; "
        "not an authentication token\n\n"
        "</script>\n\n"
        "CMRU_RELEASE_COMMIT CMRU_RELEASE_TAG clears ambient values; "
        "not an authentication token"
    )
    html_code_elements = tuple(
        f"Intro <{tag}>CMRU_RELEASE_COMMIT CMRU_RELEASE_TAG clears ambient values; "
        f"not an authentication token</{tag}>"
        for tag in (
            "code", "kbd", "pre", "samp", "textarea", "xmp", "iframe",
            "noembed", "noframes",
        )
    )
    standalone_code_block = (
        "<code>\n\n"
        "CMRU_RELEASE_COMMIT CMRU_RELEASE_TAG clears ambient values; "
        "not an authentication token\n\n"
        "</code>"
    )
    commented_code_close = (
        "Intro <code>opening\n\n"
        "<!-- </code> -->\n\n"
        "CMRU_RELEASE_COMMIT CMRU_RELEASE_TAG clears ambient values; "
        "not an authentication token"
    )
    self_closing_code = (
        "Intro <code/>CMRU_RELEASE_COMMIT CMRU_RELEASE_TAG clears ambient values; "
        "not an authentication token"
    )
    hidden_html = (
        "Intro <span hidden>CMRU_RELEASE_COMMIT CMRU_RELEASE_TAG clears ambient "
        "values; not an authentication token</span>"
    )
    hidden_nested_html = (
        "Intro <span hidden><span>hidden content</span>CMRU_RELEASE_COMMIT "
        "CMRU_RELEASE_TAG clears ambient values; not an authentication token</span>"
    )
    raw_html_image_bridge = (
        "CMRU_RELEASE_COMMIT CMRU_RELEASE_TAG clears"
        '<img src="claim.png"> ambient values; not an authentication token'
    )
    visible_html_block = (
        "<p>CMRU_RELEASE_COMMIT CMRU_RELEASE_TAG clears ambient values; "
        "not an authentication token</p>"
    )
    hidden_html_block = (
        "<p hidden>CMRU_RELEASE_COMMIT CMRU_RELEASE_TAG clears ambient values; "
        "not an authentication token</p>"
    )
    raw_html_split_paragraphs = (
        "<p>CMRU_RELEASE_COMMIT CMRU_RELEASE_TAG</p>\n"
        "<p>clears ambient values; not an authentication token</p>"
    )
    raw_html_table_claim = (
        "<table><tr><td>CMRU_RELEASE_COMMIT CMRU_RELEASE_TAG clears ambient "
        "values; not an authentication token</td></tr></table>"
    )
    separate_html_details = (
        "<details><summary>CMRU_RELEASE_COMMIT CMRU_RELEASE_TAG</summary></details>"
        "<details><summary>clears ambient values; not an authentication token</summary></details>"
    )
    closed_details_body_claim = (
        "<details><summary>More</summary><p>CMRU_RELEASE_COMMIT "
        "CMRU_RELEASE_TAG clears ambient values; not an authentication token</p></details>"
    )
    later_details_summary_claim = (
        "<details><summary>More</summary><summary>CMRU_RELEASE_COMMIT "
        "CMRU_RELEASE_TAG clears ambient values; not an authentication token</summary></details>"
    )
    nested_details_summary_claim = (
        "<details><div><summary>CMRU_RELEASE_COMMIT CMRU_RELEASE_TAG clears "
        "ambient values; not an authentication token</summary></div></details>"
    )
    nested_details_descendant_summary_claim = (
        "<details><summary>More</summary><details><summary>CMRU_RELEASE_COMMIT "
        "CMRU_RELEASE_TAG clears ambient values; not an authentication token"
        "</summary></details></details>"
    )
    visible_details_summary_claim = (
        "<details><summary>CMRU_RELEASE_COMMIT CMRU_RELEASE_TAG clears ambient "
        "values; not an authentication token</summary><p>Details</p></details>"
    )
    open_details_body_claim = (
        "<details open><summary>More</summary><p>CMRU_RELEASE_COMMIT "
        "CMRU_RELEASE_TAG clears ambient values; not an authentication token</p></details>"
    )
    svg_text_only = (
        "<svg><text>CMRU_RELEASE_COMMIT CMRU_RELEASE_TAG clears ambient values; "
        "not an authentication token</text></svg>"
    )
    html_retracted_claim = (
        "<del>CMRU_RELEASE_COMMIT CMRU_RELEASE_TAG clears ambient values; "
        "not an authentication token</del>"
    )
    html_struck_claim = (
        "<strike>CMRU_RELEASE_COMMIT CMRU_RELEASE_TAG clears ambient values; "
        "not an authentication token</strike>"
    )
    separate_html_definition_terms = (
        "<dl><dt>CMRU_RELEASE_COMMIT CMRU_RELEASE_TAG</dt>"
        "<dd>clears ambient values; not an authentication token</dd></dl>"
    )
    prose_after_hidden_void = (
        "Intro <img hidden>CMRU_RELEASE_COMMIT CMRU_RELEASE_TAG clears ambient "
        "values; not an authentication token"
    )
    inline_code_bridge = _markdown_prose_blocks(
        "CMRU_RELEASE_COMMIT CMRU_RELEASE_TAG clears` only selected` ambient values; "
        "not an authentication token"
    )
    inline_image_bridge = _markdown_prose_blocks(
        "CMRU_RELEASE_COMMIT CMRU_RELEASE_TAG clears"
        "![only selected](claim.png) ambient values; not an authentication token"
    )
    inline_comment_bridge = _markdown_prose_blocks(
        "CMRU_RELEASE_COMMIT CMRU_RELEASE_TAG clears"
        "<!-- only selected --> ambient values; not an authentication token"
    )
    visible_html_inline = (
        "CMRU_RELEASE_COMMIT CMRU_RELEASE_TAG clears <span>ambient</span> values; "
        "not an authentication token"
    )
    inline_html_split_paragraphs = _markdown_prose_blocks(
        "Intro <p>CMRU_RELEASE_COMMIT CMRU_RELEASE_TAG</p>"
        "<p>clears ambient values; not an authentication token</p>"
    )
    strikethrough_claim = (
        "~~CMRU_RELEASE_COMMIT CMRU_RELEASE_TAG clears ambient values; "
        "not an authentication token~~"
    )
    visible_html_break_inline = (
        "CMRU_RELEASE_COMMIT CMRU_RELEASE_TAG clears<br>ambient values; "
        "not an authentication token"
    )
    visible_html_break_block = (
        "<p>CMRU_RELEASE_COMMIT CMRU_RELEASE_TAG clears<br>ambient values; "
        "not an authentication token</p>"
    )

    assert not contains_required_prose(attribute_only)
    assert not contains_required_prose(image_alt_only)
    assert not contains_required_prose(link_definition_only)
    assert not contains_required_prose(nested_table_only)
    assert not contains_required_prose(raw_pre_only)
    assert not contains_required_prose(cdata_only)
    assert contains_required_prose(custom_html_with_quoted_gt)
    assert contains_required_prose(invalid_fence)
    assert contains_required_prose(emphasis)
    assert contains_required_prose(blockquote)
    assert contains_required_prose(entity_spaced)
    assert not contains_required_prose(double_encoded_entity)
    assert not contains_required_prose(script_content)
    assert not contains_required_prose(script_content_across_paragraphs)
    assert contains_required_prose(script_content_closed_in_html_block)
    assert all(not contains_required_prose(source) for source in html_code_elements)
    assert not contains_required_prose(standalone_code_block)
    assert not contains_required_prose(commented_code_close)
    assert not contains_required_prose(self_closing_code)
    assert not contains_required_prose(hidden_html)
    assert not contains_required_prose(hidden_nested_html)
    assert not contains_required_prose(raw_html_image_bridge)
    assert contains_required_prose(prose_after_hidden_void)
    assert contains_required_prose(visible_html_block)
    assert not contains_required_prose(hidden_html_block)
    assert not contains_required_prose(raw_html_split_paragraphs)
    assert not contains_required_prose(raw_html_table_claim)
    assert not contains_required_prose(separate_html_details)
    assert not contains_required_prose(closed_details_body_claim)
    assert not contains_required_prose(later_details_summary_claim)
    assert not contains_required_prose(nested_details_summary_claim)
    assert not contains_required_prose(nested_details_descendant_summary_claim)
    assert contains_required_prose(visible_details_summary_claim)
    assert contains_required_prose(open_details_body_claim)
    assert not contains_required_prose(svg_text_only)
    assert not contains_required_prose(html_retracted_claim)
    assert not contains_required_prose(html_struck_claim)
    assert not contains_required_prose(separate_html_definition_terms)
    assert not contains_required_prose(strikethrough_claim)
    assert not any("clears ambient values" in block for block in inline_code_bridge)
    assert not any("clears ambient values" in block for block in inline_image_bridge)
    assert not contains_required_prose("\n".join(inline_comment_bridge))
    assert not any(
        all(item in block for item in required)
        for block in inline_html_split_paragraphs
    )
    assert contains_required_prose(visible_html_inline)
    assert contains_required_prose(visible_html_break_inline)
    assert contains_required_prose(visible_html_break_block)


_TAGGED_RELEASE_IDENTITY_CONTRACT = (
    "For tagged release artifact steps, CMRU supplies CMRU_RELEASE_COMMIT as the "
    "exact gated candidate commit and CMRU_RELEASE_TAG as its tag. It clears both "
    "ambient values from every other project step, and project and step configuration "
    "cannot override these protected values. The pair is source-consistency metadata, "
    "not an authentication token or proof of CMRU provenance."
)


_BAD_RELEASE_SOURCE = (
    r"(?:the\s+)?(?:(?:current\s+)?(?:HEAD(?:\s+of\s+origin/main)?|"
    r"origin/main|main|trunk))"
    r"(?:\s+(?:tip|head|commit|branch))?"
    r"(?:\s+at\s+build\s+time)?\b"
)
_SOURCE_RELATION = (
    r"(?:\|\s*(?:=\s*)?|=\s*|\b(?:is based on|comes directly from|comes from|"
    r"built from|sourced from|is sourced from|is|equals|uses|points? to|"
    r"tracks|identifies|contains|comes)\s+)"
)
_TAGGED_RELEASE_MAIN_SOURCE = re.compile(
    rf"\btagged releases?\b[^.!?]{{0,100}}"
    rf"(?P<action>\b(?:use|take|set\s+to|point(?:s)?\s+to|derive\s+from|"
    rf"build\s+from|source\s+from))\s+(?:the\s+)?(?P<source>{_BAD_RELEASE_SOURCE})",
    re.IGNORECASE,
)
_RELEASE_SOURCE_MAIN = re.compile(
    rf"\b(?:release|artifact|build)(?:\s+\w+){{0,2}}\s+"
    rf"(?:source|commit|revision)\s*(?P<relation>{_SOURCE_RELATION})"
    rf"(?P<source>{_BAD_RELEASE_SOURCE})",
    re.IGNORECASE,
)
_CMRU_COMMIT_MAIN_SOURCE = re.compile(
    rf"\bCMRU_RELEASE_COMMIT\s*(?P<relation>{_SOURCE_RELATION})"
    rf"(?P<source>{_BAD_RELEASE_SOURCE})",
    re.IGNORECASE,
)
_MAIN_IS_CMRU_COMMIT = re.compile(
    rf"(?P<source>{_BAD_RELEASE_SOURCE})\s*(?P<relation>"
    rf"(?:\|\s*(?:=\s*)?|=\s*|\b(?:is|equals|sets?)\s+(?:the\s+)?))"
    rf"CMRU_RELEASE_COMMIT\b",
    re.IGNORECASE,
)
_NEGATED_RELATION = re.compile(
    r"(?:\b(?:do|does|did|must|should|can|could|would)\s+not|"
    r"\b(?:don['’]t|doesn['’]t|didn['’]t|can['’]t|couldn['’]t|"
    r"wouldn['’]t|shouldn['’]t|mustn['’]t|never|avoid|not))\s*$",
    re.IGNORECASE,
)
_NEGATED_SOURCE_CLAIM = re.compile(
    r"\b(?:do\s+not|does\s+not|did\s+not|must\s+not|should\s+not|"
    r"cannot|can['’]t|don['’]t|doesn['’]t|didn['’]t|never)\s+"
    r"(?:use|set|assign|define|map|base|build|source|take|select|choose|"
    r"treat|equate|claim|state|assert)\s+(?:the\s+)?$",
    re.IGNORECASE,
)
_VERIFIED_HEAD_SOURCE = re.compile(
    r"^\s*(?:after|if|when|once|provided that)\s+"
    r"(?:(?:verifying|checking|confirming)(?:\s+that)?\s+)?"
    r"HEAD\s*(?:equals|matches|is identical to|==)\s*"
    r"CMRU_RELEASE_COMMIT\b",
    re.IGNORECASE,
)
_VERIFIED_HEAD_IDENTITY = re.compile(
    r"\b(?:after|if|when|once|provided that)\s+"
    r"(?:(?:verifying|checking|confirming)(?:\s+that)?\s+)?"
    r"HEAD\s*(?:equals|matches|is identical to|==)\s*"
    r"CMRU_RELEASE_COMMIT\b",
    re.IGNORECASE,
)


def _relation_is_negated(block: str, relation_start: int) -> bool:
    prefix = block[max(0, relation_start - 60):relation_start]
    return _NEGATED_RELATION.search(prefix) is not None


def _source_claim_is_negated(
    block: str,
    claim_start: int,
    relation_start: int,
) -> bool:
    if _relation_is_negated(block, relation_start):
        return True
    prefix = block[max(0, claim_start - 80):claim_start]
    return _NEGATED_SOURCE_CLAIM.search(prefix) is not None


def _has_tagged_release_identity_contradiction(block: str) -> bool:
    contradiction_patterns = (
        re.compile(r"\b(?:do|does) not clear ambient values\b", re.IGNORECASE),
        re.compile(
            r"\bproject and step configuration can override these protected values\b",
            re.IGNORECASE,
        ),
        re.compile(
            r"\b(?:clears ambient values|not an authentication token)\b"
            r"[^.!?]{0,60}\bis obsolete\b",
            re.IGNORECASE,
        ),
        re.compile(
            r"\b(?:they|these values|the pair)\s+(?:is|are) an authentication token\b",
            re.IGNORECASE,
        ),
    )
    if any(pattern.search(block) for pattern in contradiction_patterns):
        return True

    for match in _TAGGED_RELEASE_MAIN_SOURCE.finditer(block):
        if _relation_is_negated(block, match.start("action")):
            continue
        source = match.group("source").strip().casefold()
        if source.startswith("the "):
            source = source[4:]
        if source == "head" and _VERIFIED_HEAD_SOURCE.search(
            block[match.end():match.end() + 120],
        ):
            continue
        return True

    for pattern in (_RELEASE_SOURCE_MAIN, _CMRU_COMMIT_MAIN_SOURCE):
        for match in pattern.finditer(block):
            if _source_claim_is_negated(
                block,
                match.start(),
                match.start("relation"),
            ):
                continue
            source = match.group("source").strip().casefold()
            if source.startswith("the "):
                source = source[4:]
            trailing_text = block[match.end():match.end() + 120]
            if source == "head" and _VERIFIED_HEAD_SOURCE.search(trailing_text):
                continue
            return True

    for match in _MAIN_IS_CMRU_COMMIT.finditer(block):
        if _source_claim_is_negated(
            block,
            match.start(),
            match.start("relation"),
        ):
            continue
        if match.group("source").strip().casefold() == "head" and any(
            checked.start() <= match.start("source")
            and checked.end() >= match.end()
            for checked in _VERIFIED_HEAD_IDENTITY.finditer(block)
        ):
            continue
        return True
    return False


def _has_tagged_release_identity_contract(
    paragraphs: list[str],
    *,
    visible_blocks: list[str] | None = None,
) -> bool:
    visible_blocks = paragraphs if visible_blocks is None else visible_blocks
    def contradicts(block: str) -> bool:
        return _has_tagged_release_identity_contradiction(block)

    return (
        _TAGGED_RELEASE_IDENTITY_CONTRACT in paragraphs
        and not any(contradicts(block) for block in visible_blocks)
    )


def test_tagged_release_identity_is_documented_for_adopters_and_maintainers():
    root = Path(__file__).resolve().parents[1]
    documents = (
        root / "README.md",
        root / "docs" / "DESIGN-GUIDE.md",
        root / "docs" / "CONSUMERS.md",
    )
    for path in documents:
        text = path.read_text(encoding="utf-8")
        paragraphs = _markdown_prose_blocks(text)
        visible_blocks = _markdown_prose_blocks(
            text,
            include_headings=True,
            include_tables=True,
            include_source_code_claims=True,
        )
        assert _has_tagged_release_identity_contract(
            paragraphs, visible_blocks=visible_blocks,
        ), path

    contradictory_paragraph = _markdown_prose_blocks(
        "CMRU_RELEASE_COMMIT and CMRU_RELEASE_TAG do not clear ambient values. "
        "‘clears ambient values’ is obsolete. They are an authentication token; "
        "‘not an authentication token’ is obsolete."
    )
    legacy_phrases = (
        "CMRU_RELEASE_COMMIT", "CMRU_RELEASE_TAG", "clears ambient values",
        "not an authentication token",
    )
    assert any(
        all(phrase in paragraph for phrase in legacy_phrases)
        for paragraph in contradictory_paragraph
    )
    assert not _has_tagged_release_identity_contract(contradictory_paragraph)
    assert not _has_tagged_release_identity_contract([
        _TAGGED_RELEASE_IDENTITY_CONTRACT,
        *contradictory_paragraph,
    ])
    current_main_headline = (
        "CMRU_RELEASE_COMMIT is the current main tip, not the gated candidate."
    )
    assert not _has_tagged_release_identity_contract([
        _TAGGED_RELEASE_IDENTITY_CONTRACT,
        current_main_headline,
    ])
    independent_of_main = (
        _TAGGED_RELEASE_IDENTITY_CONTRACT
        + "\n\nCMRU_RELEASE_COMMIT is independent of the current origin/main tip."
    )
    assert _has_tagged_release_identity_contract(
        _markdown_prose_blocks(independent_of_main),
    )
    accurate_negated_guidance = (
        _TAGGED_RELEASE_IDENTITY_CONTRACT
        + "\n\nFor tagged releases, do not use the current origin/main tip."
    )
    assert _has_tagged_release_identity_contract(
        _markdown_prose_blocks(accurate_negated_guidance),
    )
    colloquial_negated_guidance = (
        _TAGGED_RELEASE_IDENTITY_CONTRACT
        + "\n\nFor tagged releases, don't use the current main tip."
    )
    assert _has_tagged_release_identity_contract(
        _markdown_prose_blocks(colloquial_negated_guidance),
    )
    denied_commit_equivalence = (
        _TAGGED_RELEASE_IDENTITY_CONTRACT
        + "\n\nCMRU_RELEASE_COMMIT never equals HEAD."
    )
    assert _has_tagged_release_identity_contract(
        _markdown_prose_blocks(denied_commit_equivalence),
    )
    verified_commit_equivalence = (
        _TAGGED_RELEASE_IDENTITY_CONTRACT
        + "\n\nCMRU_RELEASE_COMMIT is HEAD after verifying HEAD equals "
        "CMRU_RELEASE_COMMIT."
    )
    assert _has_tagged_release_identity_contract(
        _markdown_prose_blocks(verified_commit_equivalence),
    )
    verified_tagged_head_source = (
        _TAGGED_RELEASE_IDENTITY_CONTRACT
        + "\n\nFor tagged releases, use HEAD after verifying HEAD equals "
        "CMRU_RELEASE_COMMIT."
    )
    assert _has_tagged_release_identity_contract(
        _markdown_prose_blocks(verified_tagged_head_source),
    )
    verified_head_guidance = (
        _TAGGED_RELEASE_IDENTITY_CONTRACT
        + "\n\nThe release build source is HEAD after verifying HEAD equals "
        "CMRU_RELEASE_COMMIT."
    )
    assert _has_tagged_release_identity_contract(
        _markdown_prose_blocks(verified_head_guidance),
    )
    if_verified_head_guidance = (
        _TAGGED_RELEASE_IDENTITY_CONTRACT
        + "\n\nThe release build source is HEAD if HEAD equals CMRU_RELEASE_COMMIT."
    )
    assert _has_tagged_release_identity_contract(
        _markdown_prose_blocks(if_verified_head_guidance),
    )
    unverified_head_source = (
        _TAGGED_RELEASE_IDENTITY_CONTRACT
        + "\n\nThe release source = HEAD."
    )
    assert not _has_tagged_release_identity_contract(
        _markdown_prose_blocks(unverified_head_source),
    )
    unrelated_main_source_and_check = (
        _TAGGED_RELEASE_IDENTITY_CONTRACT
        + "\n\nThe release source is origin/main; after verifying HEAD equals "
        "CMRU_RELEASE_COMMIT, continue."
    )
    assert not _has_tagged_release_identity_contract(
        _markdown_prose_blocks(unrelated_main_source_and_check),
    )
    alternate_release_guidance = (
        _TAGGED_RELEASE_IDENTITY_CONTRACT
        + "\n\nFor tagged releases, use the current main tip."
    )
    assert not _has_tagged_release_identity_contract(
        _markdown_prose_blocks(alternate_release_guidance),
        visible_blocks=_markdown_prose_blocks(
            alternate_release_guidance, include_headings=True, include_tables=True,
        ),
    )
    table_contradiction = (
        _TAGGED_RELEASE_IDENTITY_CONTRACT
        + "\n\n| Name | Meaning |\n| --- | --- |\n"
        + "| CMRU_RELEASE_COMMIT | current main tip, not gated candidate |"
    )
    table_visible_blocks = _markdown_prose_blocks(
        table_contradiction, include_headings=True, include_tables=True,
    )
    assert any("current main tip" in block for block in table_visible_blocks)
    assert not _has_tagged_release_identity_contract(
        _markdown_prose_blocks(table_contradiction),
        visible_blocks=table_visible_blocks,
    )
    accurate_table = (
        _TAGGED_RELEASE_IDENTITY_CONTRACT
        + "\n\n| Name | Meaning |\n| --- | --- |\n"
        + "| CMRU_RELEASE_COMMIT | exact gated candidate SHA |"
    )
    assert _has_tagged_release_identity_contract(
        _markdown_prose_blocks(accurate_table),
        visible_blocks=_markdown_prose_blocks(
            accurate_table, include_headings=True, include_tables=True,
        ),
    )
    wrong_head_table = (
        _TAGGED_RELEASE_IDENTITY_CONTRACT
        + "\n\n| Name | Meaning |\n| --- | --- |\n"
        + "| CMRU_RELEASE_COMMIT | HEAD of origin/main at build time |"
    )
    assert not _has_tagged_release_identity_contract(
        _markdown_prose_blocks(wrong_head_table),
        visible_blocks=_markdown_prose_blocks(
            wrong_head_table, include_headings=True, include_tables=True,
        ),
    )
    assignment_contradiction = (
        _TAGGED_RELEASE_IDENTITY_CONTRACT
        + "\n\nCMRU_RELEASE_COMMIT = origin/main"
    )
    assert not _has_tagged_release_identity_contract(
        _markdown_prose_blocks(assignment_contradiction),
    )
    code_source_contradiction = (
        _TAGGED_RELEASE_IDENTITY_CONTRACT
        + "\n\nThe release source is `origin/main`."
    )
    assert not _has_tagged_release_identity_contract(
        _markdown_prose_blocks(code_source_contradiction),
    )
    backticked_assignment_contradiction = (
        _TAGGED_RELEASE_IDENTITY_CONTRACT
        + "\n\nThe incorrect mapping is `CMRU_RELEASE_COMMIT=origin/main`."
    )
    assert not _has_tagged_release_identity_contract(
        _markdown_prose_blocks(
            backticked_assignment_contradiction,
            include_source_code_claims=True,
        ),
    )
    explicitly_negated_assignment = (
        _TAGGED_RELEASE_IDENTITY_CONTRACT
        + "\n\nDo not use `CMRU_RELEASE_COMMIT=origin/main`."
    )
    assert _has_tagged_release_identity_contract(
        _markdown_prose_blocks(explicitly_negated_assignment),
        visible_blocks=_markdown_prose_blocks(
            explicitly_negated_assignment,
            include_source_code_claims=True,
        ),
    )
    backticked_word_relation_contradiction = (
        _TAGGED_RELEASE_IDENTITY_CONTRACT
        + "\n\nThe incorrect mapping is `CMRU_RELEASE_COMMIT is origin/main`."
    )
    assert not _has_tagged_release_identity_contract(
        _markdown_prose_blocks(
            backticked_word_relation_contradiction,
            include_source_code_claims=True,
        ),
    )
    html_code_source_contradiction = (
        _TAGGED_RELEASE_IDENTITY_CONTRACT
        + "\n\n<code>CMRU_RELEASE_COMMIT is origin/main</code>"
    )
    assert not _has_tagged_release_identity_contract(
        _markdown_prose_blocks(
            html_code_source_contradiction,
            include_source_code_claims=True,
        ),
    )
    hidden_html_code_source = (
        _TAGGED_RELEASE_IDENTITY_CONTRACT
        + "\n\n<code hidden>CMRU_RELEASE_COMMIT is origin/main</code>"
    )
    assert _has_tagged_release_identity_contract(
        _markdown_prose_blocks(hidden_html_code_source),
        visible_blocks=_markdown_prose_blocks(
            hidden_html_code_source,
            include_headings=True,
            include_tables=True,
            include_source_code_claims=True,
        ),
    )
    assignment_table_contradiction = (
        _TAGGED_RELEASE_IDENTITY_CONTRACT
        + "\n\n| Name | Meaning |\n| --- | --- |\n"
        + "| CMRU_RELEASE_COMMIT | = `HEAD` |"
    )
    assert not _has_tagged_release_identity_contract(
        _markdown_prose_blocks(assignment_table_contradiction),
        visible_blocks=_markdown_prose_blocks(
            assignment_table_contradiction, include_headings=True, include_tables=True,
        ),
    )
    verified_head_table = (
        _TAGGED_RELEASE_IDENTITY_CONTRACT
        + "\n\n| Release build source | HEAD if HEAD equals CMRU_RELEASE_COMMIT |"
    )
    assert _has_tagged_release_identity_contract(
        _markdown_prose_blocks(verified_head_table),
        visible_blocks=_markdown_prose_blocks(
            verified_head_table, include_headings=True, include_tables=True,
        ),
    )
    generic_main_source_table = (
        _TAGGED_RELEASE_IDENTITY_CONTRACT
        + "\n\n| Release build source | HEAD of origin/main at build time |"
    )
    assert not _has_tagged_release_identity_contract(
        _markdown_prose_blocks(generic_main_source_table),
        visible_blocks=_markdown_prose_blocks(
            generic_main_source_table, include_headings=True, include_tables=True,
        ),
    )

    contradictory_heading_document = (
        _TAGGED_RELEASE_IDENTITY_CONTRACT
        + "\n\n# CMRU_RELEASE_COMMIT and CMRU_RELEASE_TAG do not clear ambient values."
    )
    paragraphs = _markdown_prose_blocks(contradictory_heading_document)
    visible_blocks = _markdown_prose_blocks(
        contradictory_heading_document, include_headings=True, include_tables=True,
        include_source_code_claims=True,
    )
    assert _TAGGED_RELEASE_IDENTITY_CONTRACT in paragraphs
    assert any("do not clear ambient values" in block for block in visible_blocks)
    assert not _has_tagged_release_identity_contract(
        paragraphs, visible_blocks=visible_blocks,
    )

    contradictory_override_heading = (
        _TAGGED_RELEASE_IDENTITY_CONTRACT
        + "\n\n## Project and step configuration can override these protected values."
    )
    paragraphs = _markdown_prose_blocks(contradictory_override_heading)
    visible_blocks = _markdown_prose_blocks(
        contradictory_override_heading, include_headings=True, include_tables=True,
        include_source_code_claims=True,
    )
    assert any("override these protected values" in block for block in visible_blocks)
    assert not _has_tagged_release_identity_contract(
        paragraphs, visible_blocks=visible_blocks,
    )

    html_heading_only = f"<h2>{_TAGGED_RELEASE_IDENTITY_CONTRACT}</h2>"
    assert _TAGGED_RELEASE_IDENTITY_CONTRACT not in _markdown_prose_blocks(
        html_heading_only,
    )
    assert _TAGGED_RELEASE_IDENTITY_CONTRACT in _markdown_prose_blocks(
        html_heading_only, include_headings=True,
    )
    assert not _has_tagged_release_identity_contract(
        _markdown_prose_blocks(html_heading_only),
        visible_blocks=_markdown_prose_blocks(
            html_heading_only,
            include_headings=True,
            include_tables=True,
            include_source_code_claims=True,
        ),
    )


def test_tagged_release_identity_docs_link_and_consumer_check_resolve():
    root = Path(__file__).resolve().parents[1]
    readme = (root / "README.md").read_text(encoding="utf-8")
    design_guide = (root / "docs" / "DESIGN-GUIDE.md").read_text(encoding="utf-8")
    consumers = (root / "docs" / "CONSUMERS.md").read_text(encoding="utf-8")
    heading = "Tagged artifact identity binds builds to CMRU's candidate"
    anchor = "tagged-artifact-identity-binds-builds-to-cmrus-candidate"
    assert f"### {heading}" in design_guide
    assert f"docs/DESIGN-GUIDE.md#{anchor}" in readme
    assert 'head=$(git rev-parse --verify \'HEAD^{commit}\')' in consumers
    assert (
        'tag_commit=$(git rev-parse --verify '
        '"refs/tags/${CMRU_RELEASE_TAG}^{commit}")'
    ) in consumers
    assert 'test "$head" = "$CMRU_RELEASE_COMMIT"' in consumers
    assert 'test "$tag_commit" = "$CMRU_RELEASE_COMMIT"' in consumers


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

        def resolve_latest(self, prefix):
            assert prefix == "alpha"
            return {"version": "1.2.3"}

        def get_tag_commit(self, tag):
            if tag == "alpha-v1.2.3":
                return SOURCE_COMMIT
            if tag == "alpha-latest":
                return "c" * 40
            return None

        def get_release_by_tag(self, tag):
            return {"id": 1, "tag_name": tag} if tag in {"alpha-v1.2.3", "alpha-latest"} else None

        def publish(self, tag, _title, _notes, assets, *, recreate=False,
                    target_commitish=None, require_existing_release=False,
                    expected_release_id=None, expected_tag_commit=None):
            self.published.append((tag, recreate, target_commitish, require_existing_release,
                                   expected_release_id, expected_tag_commit,
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
    assert all(
        not call[1] and call[2] is None and call[3] and call[4] == 1
        for call in publisher.published
    )
    assert publisher.published[0][5] == SOURCE_COMMIT
    assert publisher.published[1][5] == "c" * 40
    assert publisher.published[0][6][:2] == [
        ("alpha-1.2.3.whl", b"verified wheel"),
        ("alpha-1.2.3.whl.sha256", f"{digest}  alpha-1.2.3.whl\n".encode()),
    ]
    assert publisher.published[0][6][2] == (companion.name, b"verified companion")
    assert publisher.published[1][6][0][0] == "latest.json"
    assert {path.relative_to(artifact_root): path.read_bytes() for path in artifact_root.rglob("*") if path.is_file()} == original_files
    transaction.validate_build_output_tree(artifact_root, "alpha", OUTPUT_ID)
    monkeypatch.setenv("CMRU_BUILD_SOURCE_COMMIT", "f" * 40)
    with pytest.raises(RuntimeError, match="SOURCE_COMMIT"):
        handlers._build_output_record(None)
    monkeypatch.setenv("CMRU_BUILD_SOURCE_COMMIT", SOURCE_COMMIT)
    monkeypatch.setenv("CMRU_BUILD_SOURCE_DATE", "2026-09-28T12:00:00+00:00")
    with pytest.raises(RuntimeError, match="SOURCE_DATE"):
        handlers._build_output_record(None)


def test_builtin_retained_publish_rejects_changed_staging_bytes_before_remote_publish(
    monkeypatch, tmp_path,
):
    artifact_root = tmp_path / OUTPUT_ID
    asset = artifact_root / "dist" / "alpha-1.2.3.whl"
    asset.parent.mkdir(parents=True)
    expected_bytes = b"recorded wheel"
    asset.write_bytes(expected_bytes)
    manifest = {
        "source_commit": SOURCE_COMMIT,
        "artifacts": [{"directory": "dist", "files": [{
            "path": asset.name,
            "sha256": hashlib.sha256(expected_bytes).hexdigest(),
            "bytes": str(len(expected_bytes)),
        }]}],
    }
    record = {"artifact_root": artifact_root, "manifest": manifest}
    remote_calls = []

    def substituted_copy(_source, destination):
        Path(destination).write_bytes(b"substituted bytes")
        return destination

    monkeypatch.setattr(handlers.shutil, "copy2", substituted_copy)
    monkeypatch.setattr(
        handlers, "publish_versioned",
        lambda *args, **kwargs: remote_calls.append((args, kwargs)),
    )

    with pytest.raises(RuntimeError, match="staged retained artifact differs from build.json"):
        handlers._publish_versioned_artifacts(
            object(), prefix="alpha", version="1.2.3", asset_path=asset,
            notes=None, extra_assets=None, build_output=record,
        )

    assert remote_calls == []


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

        def resolve_latest(self, prefix):
            assert prefix == "alpha"
            return {"version": "1.2.3"}

        def get_tag_commit(self, tag):
            return self.refs.get(tag)

        def get_release_by_tag(self, tag):
            return {"id": 1, "tag_name": tag} if tag in self.release_tags else None

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


@pytest.mark.parametrize(
    "latest_versions",
    [
        ["2.0.0"],
        ["1.2.3", "2.0.0"],
    ],
)
def test_retained_publish_does_not_move_latest_pointer_to_an_older_release(
    tmp_path, latest_versions,
):
    asset = tmp_path / "alpha-1.2.3.whl"
    asset.write_bytes(b"retained wheel")

    class Publisher:
        def __init__(self):
            self.latest_checks = []
            self.uploads = []

        def resolve_latest(self, prefix):
            assert prefix == "alpha"
            version = latest_versions[min(len(self.latest_checks), len(latest_versions) - 1)]
            self.latest_checks.append(version)
            return {"version": version}

        def get_release_by_tag(self, tag):
            return {"id": 1, "tag_name": tag} if tag in {"alpha-v1.2.3", "alpha-latest"} else None

        def get_tag_commit(self, tag):
            if tag == "alpha-v1.2.3":
                return SOURCE_COMMIT
            if tag == "alpha-latest":
                return "c" * 40
            return None

        def publish(self, tag, _title, _notes, assets, **kwargs):
            self.uploads.append((tag, [path.name for path in assets], kwargs))
            return {"id": 1}

        def asset_download_url(self, tag, name):
            return f"https://example.invalid/{tag}/{name}"

    publisher = Publisher()
    release.publish_versioned(
        publisher,
        prefix="alpha",
        version="1.2.3",
        asset_path=asset,
        require_existing_targets=True,
        latest_pointer_recreate=False,
        expected_tag_commit=SOURCE_COMMIT,
    )

    assert [call[0] for call in publisher.uploads] == ["alpha-v1.2.3"]
    assert publisher.uploads[0][1][:2] == [
        "alpha-1.2.3.whl", "alpha-1.2.3.whl.sha256",
    ]


def test_build_output_dev_version_updates_only_preexisting_latest_targets(tmp_path):
    asset = tmp_path / "alpha-1.2.3.dev1.whl"
    asset.write_bytes(b"dev wheel")

    class ExistingLatest:
        def __init__(self):
            self.uploads = []

        def get_release_by_tag(self, tag):
            return {"id": 1, "tag_name": tag} if tag == "alpha-latest" else None

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
    assert publisher.uploads[0][1]["expected_release_id"] == 1
    assert publisher.uploads[0][1]["expected_tag_commit"] == "c" * 40


def test_build_output_can_skip_latest_pointer_validation_when_disabled(tmp_path):
    asset = tmp_path / "alpha-1.2.3.whl"
    asset.write_bytes(b"wheel")

    class VersionOnlyPublisher:
        def __init__(self):
            self.uploads = []

        def get_release_by_tag(self, tag):
            return {"id": 1, "tag_name": tag} if tag == "alpha-v1.2.3" else None

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
    assert publisher.uploads[0][1]["expected_release_id"] == 1
    assert publisher.uploads[0][1]["expected_tag_commit"] == SOURCE_COMMIT


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
    assert requests == [("GET", "https://api.github.com/repos/owner/repo/commits/alpha-v1.2.3")]

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

    with pytest.raises(SystemExit):
        client.publish(
            "alpha-v1", "title", "notes", [],
            require_existing_release=True,
            expected_release_id=1,
            expected_tag_commit="bad",
        )


@pytest.mark.parametrize(
    "record, commit, expected",
    [
        (None, SOURCE_COMMIT, "no longer available"),
        ([], SOURCE_COMMIT, "record is malformed"),
        ({"id": True, "tag_name": "alpha-v1", "upload_url": "u"}, SOURCE_COMMIT, "release ID changed"),
        ({"id": 7, "tag_name": "other", "upload_url": "u"}, SOURCE_COMMIT, "tag identity is malformed"),
        ({"id": 7, "tag_name": "alpha-v1", "upload_url": "u"}, "c" * 40, "tag target changed"),
        ({"id": 7, "tag_name": "alpha-v1", "upload_url": ""}, SOURCE_COMMIT, "missing upload_url"),
    ],
)
def test_verified_existing_release_rejects_changed_or_malformed_identity(
    monkeypatch, capsys, record, commit, expected,
):
    client = release.GitHubReleases("owner", "repo", "token")
    monkeypatch.setattr(client, "get_release_by_tag", lambda _tag: record)
    monkeypatch.setattr(client, "get_tag_commit", lambda _tag: commit)

    with pytest.raises(SystemExit) as exc_info:
        client._verified_existing_release("alpha-v1", 7, SOURCE_COMMIT)

    assert exc_info.value.code == 1
    assert expected in capsys.readouterr().err


def test_verified_existing_release_returns_only_matching_release(monkeypatch):
    client = release.GitHubReleases("owner", "repo", "token")
    record = {"id": 7, "tag_name": "alpha-v1", "upload_url": "https://upload/{?name}"}
    monkeypatch.setattr(client, "get_release_by_tag", lambda _tag: record)
    monkeypatch.setattr(client, "get_tag_commit", lambda _tag: SOURCE_COMMIT)

    assert client._verified_existing_release("alpha-v1", 7, SOURCE_COMMIT) is record


def test_existing_only_publish_rechecks_each_asset_before_mutation(tmp_path):
    old_asset = tmp_path / "old.whl"
    malformed_old_asset = tmp_path / "legacy.whl"
    new_asset = tmp_path / "new.whl"
    for path in (old_asset, malformed_old_asset, new_asset):
        path.write_bytes(path.name.encode())
    client = release.GitHubReleases("owner", "repo", "token")
    record = {"id": 7, "tag_name": "alpha-v1", "upload_url": "https://upload/{?name}"}
    actions = []
    client.get_release_by_tag = lambda _tag: record
    client.get_tag_commit = lambda _tag: SOURCE_COMMIT
    client.update_release = lambda *args: actions.append(("update", args))
    client.list_assets = lambda _rid: [
        {"name": old_asset.name, "id": 8},
        {"name": malformed_old_asset.name, "id": True},
    ]
    client.delete_asset = lambda ident: actions.append(("delete", ident))
    client.upload_asset = lambda _url, _path, name: actions.append(("upload", name))

    result = client.publish(
        "alpha-v1", "title", "notes", [old_asset, malformed_old_asset, new_asset],
        require_existing_release=True, expected_release_id=7,
        expected_tag_commit=SOURCE_COMMIT,
    )

    assert result is record
    assert actions == [
        ("update", (7, "title", "notes")),
        ("delete", 8),
        ("upload", "old.whl"),
        ("upload", "legacy.whl"),
        ("upload", "new.whl"),
    ]


@pytest.mark.parametrize(
    "changed_check, expected_actions",
    [
        (3, ["update"]),
        (4, ["update", "delete"]),
    ],
)
def test_existing_only_publish_stops_when_tag_target_changes_between_asset_mutations(
    tmp_path, capsys, changed_check, expected_actions,
):
    asset = tmp_path / "old.whl"
    asset.write_bytes(b"wheel")
    client = release.GitHubReleases("owner", "repo", "token")
    record = {"id": 7, "tag_name": "alpha-v1", "upload_url": "https://upload/{?name}"}
    tag_reads = 0
    actions = []

    def tag_commit(_tag):
        nonlocal tag_reads
        tag_reads += 1
        return "c" * 40 if tag_reads == changed_check else SOURCE_COMMIT

    client.get_release_by_tag = lambda _tag: record
    client.get_tag_commit = tag_commit
    client.update_release = lambda *args: actions.append("update")
    client.list_assets = lambda _rid: [{"name": asset.name, "id": 8}]
    client.delete_asset = lambda _ident: actions.append("delete")
    client.upload_asset = lambda *_args: actions.append("upload")

    with pytest.raises(SystemExit):
        client.publish(
            "alpha-v1", "title", "notes", [asset],
            require_existing_release=True, expected_release_id=7,
            expected_tag_commit=SOURCE_COMMIT,
        )

    assert "tag target changed" in capsys.readouterr().err
    assert actions == expected_actions


@pytest.mark.parametrize(
    "release_record, tag_commit, expected",
    [
        ("not-a-record", SOURCE_COMMIT, "malformed GitHub Release"),
        ({"id": 0, "tag_name": "alpha-v1"}, SOURCE_COMMIT, "existing GitHub Release ID"),
        ({"id": 7, "tag_name": "other"}, SOURCE_COMMIT, "malformed GitHub Release identity"),
        (
            {"id": 7, "tag_name": "alpha-v1.2.3"},
            None,
            "existing Git tag alpha-v1.2.3",
        ),
    ],
)
def test_retained_publish_rejects_malformed_existing_release_records(
    tmp_path, release_record, tag_commit, expected, capsys,
):
    asset = tmp_path / "alpha-1.2.3.whl"
    asset.write_bytes(b"wheel")

    class Publisher:
        def resolve_latest(self, _prefix):
            return {"version": "1.2.3"}

        def get_release_by_tag(self, _tag):
            return release_record

        def get_tag_commit(self, _tag):
            return tag_commit

        def publish(self, *_args, **_kwargs):
            pytest.fail("invalid retained release was published")

    with pytest.raises(SystemExit) as excinfo:
        release.publish_versioned(
            Publisher(), prefix="alpha", version="1.2.3", asset_path=asset,
            require_existing_targets=True, latest_pointer_recreate=False,
            expected_tag_commit=SOURCE_COMMIT,
        )
    assert excinfo.value.code == 1
    assert expected in capsys.readouterr().err


@pytest.mark.parametrize("latest", [None, {"version": "1.2.2"}])
def test_newer_release_comparison_handles_no_clean_latest_release(latest):
    class Publisher:
        def resolve_latest(self, _prefix):
            return latest

    assert release._newer_release_version(Publisher(), "alpha", "1.2.3") is None


def test_newer_release_comparison_refuses_malformed_latest_version(capsys):
    class Publisher:
        def resolve_latest(self, _prefix):
            return {"version": 17}

    with pytest.raises(SystemExit) as excinfo:
        release._newer_release_version(Publisher(), "alpha", "1.2.3")
    assert excinfo.value.code == 1
    assert "malformed result" in capsys.readouterr().err


def test_newer_release_comparison_returns_a_newer_release_version():
    class Publisher:
        def resolve_latest(self, _prefix):
            return {"version": "1.3.0"}

    assert release._newer_release_version(Publisher(), "alpha", "1.2.3") == "1.3.0"


@pytest.mark.parametrize("mode", ["create", "update", "recreate"])
def test_github_publish_handles_create_update_and_recreate_paths(tmp_path, mode):
    asset = tmp_path / "alpha.whl"
    asset.write_bytes(b"wheel")
    client = release.GitHubReleases("owner", "repo", "token")
    current = None if mode == "create" else {
        "id": 7, "tag_name": "alpha-v1", "upload_url": "https://upload/{?name}",
    }
    events = []
    client.get_release_by_tag = lambda _tag: current
    client.create_release = lambda *args: events.append(("create", args)) or {
        "id": 8, "tag_name": "alpha-v1", "upload_url": "https://upload/{?name}",
    }
    client.update_release = lambda *args: events.append(("update", args))
    client.delete_release = lambda ident: events.append(("delete", ident))
    client.list_assets = lambda _rid: []
    client.upload_asset = lambda _url, _path, name: events.append(("upload", name))

    result = client.publish(
        "alpha-v1", "title", "notes", [asset], recreate=(mode == "recreate"),
    )

    action_names = [event[0] for event in events]
    if mode == "create":
        assert action_names == ["create", "upload"]
    elif mode == "update":
        assert action_names == ["update", "upload"]
    else:
        assert action_names == ["delete", "create", "upload"]
    assert result["id"] == (8 if mode in {"create", "recreate"} else 7)


@pytest.mark.parametrize("latest_pointer", [False, True])
def test_retained_publish_updates_only_verified_existing_targets(
    tmp_path, latest_pointer,
):
    asset = tmp_path / "alpha-1.2.3.whl"
    asset.write_bytes(b"wheel")
    existing = {
        "alpha-v1.2.3": {"id": 17, "tag_name": "alpha-v1.2.3"},
        "alpha-latest": {"id": 18, "tag_name": "alpha-latest"},
    }
    published = []

    class Publisher:
        def resolve_latest(self, _prefix):
            return {"version": "1.2.3"}

        def get_release_by_tag(self, tag):
            return existing[tag]

        def get_tag_commit(self, _tag):
            return SOURCE_COMMIT

        def publish(self, tag, title, notes, assets, **kwargs):
            published.append((tag, title, notes, tuple(assets), kwargs))
            return existing[tag]

        def asset_download_url(self, tag, name):
            return f"https://example.test/{tag}/{name}"

    result = release.publish_versioned(
        Publisher(), prefix="alpha", version="1.2.3", asset_path=asset,
        latest_pointer=latest_pointer, require_existing_targets=True,
        latest_pointer_recreate=False, expected_tag_commit=SOURCE_COMMIT,
    )

    assert result["release_tag"] == "alpha-v1.2.3"
    assert result["asset_url"] == "https://example.test/alpha-v1.2.3/alpha-1.2.3.whl"
    assert [entry[0] for entry in published] == (
        ["alpha-v1.2.3", "alpha-latest"] if latest_pointer else ["alpha-v1.2.3"]
    )
    assert all(entry[4]["require_existing_release"] is True for entry in published)


def test_retained_dev_publish_verifies_and_updates_only_latest_target(tmp_path):
    asset = tmp_path / "alpha-dev.whl"
    asset.write_bytes(b"wheel")
    published = []

    class Publisher:
        def get_release_by_tag(self, tag):
            assert tag == "alpha-latest"
            return {"id": 18, "tag_name": tag}

        def get_tag_commit(self, _tag):
            return SOURCE_COMMIT

        def publish(self, tag, _title, _notes, assets, **kwargs):
            published.append((tag, tuple(assets), kwargs))
            return {"id": 18}

    release.publish_versioned(
        Publisher(), prefix="alpha", version="1.2.3.dev4", asset_path=asset,
        require_existing_targets=True, latest_pointer_recreate=False,
        expected_tag_commit=SOURCE_COMMIT,
    )

    assert len(published) == 1
    assert published[0][0] == "alpha-latest"
    assert published[0][2]["expected_tag_commit"] == SOURCE_COMMIT


def test_staged_build_output_verifier_accepts_matching_manifest_artifact(tmp_path):
    source = tmp_path / "build" / "dist" / "artifact.whl"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"wheel")
    staged = tmp_path / "staged.whl"
    staged.write_bytes(b"wheel")
    record = {
        "artifact_root": str(tmp_path / "build"),
        "manifest": {"artifacts": [{
            "directory": "dist",
            "files": [{
                "path": "artifact.whl", "sha256": ARTIFACT_SHA, "bytes": 5,
            }],
        }]},
    }
    record["manifest"]["artifacts"][0]["files"][0]["sha256"] = hashlib.sha256(b"wheel").hexdigest()

    handlers._verify_staged_build_output_file(record, source, staged)


@pytest.mark.parametrize("fault", ["digest", "length"])
def test_staged_build_output_verifier_refuses_changed_staged_content(tmp_path, fault):
    source = tmp_path / "build" / "dist" / "artifact.whl"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"wheel")
    staged = tmp_path / "staged.whl"
    staged_content = b"other" if fault == "digest" else b"longer"
    staged.write_bytes(staged_content)
    record = {
        "artifact_root": str(tmp_path / "build"),
        "manifest": {"artifacts": [{
            "directory": "dist",
            "files": [{
                "path": "artifact.whl",
                "sha256": (
                    hashlib.sha256(staged_content).hexdigest()
                    if fault == "length" else hashlib.sha256(b"wheel").hexdigest()
                ),
                "bytes": 5,
            }],
        }]},
    }
    with pytest.raises(RuntimeError, match="differs from build.json"):
        handlers._verify_staged_build_output_file(record, source, staged)


@pytest.mark.parametrize(
    "record, source_path, staged_path, expected",
    [
        ({"artifact_root": "/build", "manifest": {"artifacts": []}}, Path("/outside/file.whl"), Path("/staged.whl"), "outside its build record"),
        ({"artifact_root": "/build", "manifest": {"artifacts": [{"directory": "dist", "files": []}]}}, Path("/build/dist/missing.whl"), Path("/staged.whl"), "not present in build.json"),
    ],
)
def test_staged_build_output_verifier_rejects_unlisted_sources(
    tmp_path, record, source_path, staged_path, expected,
):
    with pytest.raises(RuntimeError, match=expected):
        handlers._verify_staged_build_output_file(record, source_path, staged_path)


def test_staged_build_output_verifier_requires_regular_staged_file(tmp_path):
    source_path = tmp_path / "build" / "dist" / "artifact.whl"
    record = {
        "artifact_root": str(tmp_path / "build"),
        "manifest": {"artifacts": [{
            "directory": "dist",
            "files": [{"path": "artifact.whl", "sha256": ARTIFACT_SHA, "bytes": 1}],
        }]},
    }
    staged = tmp_path / "staged"
    staged.mkdir()

    with pytest.raises(RuntimeError, match="not a regular file"):
        handlers._verify_staged_build_output_file(record, source_path, staged)


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


def test_bound_cmru_launcher_identity_refusal_names_the_shadowing_metadata_location(
    monkeypatch, tmp_path,
):
    stale = "/src/cmru/cmru.egg-info"

    def fake_run(argv, **_kwargs):
        if argv[-1] == "version":
            return SimpleNamespace(returncode=0, stdout="cmru 5.3.2.dev36\n", stderr="")
        return SimpleNamespace(returncode=0, stdout=stale + "\n", stderr="")

    monkeypatch.setattr(cli.subprocess, "run", fake_run)
    launcher_dir = tmp_path / "stale"
    launcher_dir.mkdir()
    with pytest.raises(RuntimeError, match="identity verification") as excinfo:
        cli._create_bound_cmru_launcher(launcher_dir)
    message = str(excinfo.value)
    assert f"metadata resolved from {stale}" in message
    assert "stale egg-info" in message


def test_bound_cmru_launcher_identity_refusal_survives_an_unrunnable_metadata_probe(
    monkeypatch, tmp_path,
):
    def fake_run(argv, **_kwargs):
        if argv[-1] == "version":
            return SimpleNamespace(returncode=0, stdout="cmru 5.3.2.dev36\n", stderr="")
        raise OSError("probe interpreter vanished")

    monkeypatch.setattr(cli.subprocess, "run", fake_run)
    launcher_dir = tmp_path / "unprobeable"
    launcher_dir.mkdir()
    with pytest.raises(RuntimeError, match="identity verification") as excinfo:
        cli._create_bound_cmru_launcher(launcher_dir)
    assert "metadata resolved from <unresolved>" in str(excinfo.value)


def test_bound_cmru_launcher_imports_only_the_worktree_sibling_source_with_isolated_python(
    monkeypatch, tmp_path,
):
    repo_root = tmp_path / "repo"
    cmru_package = repo_root / "cmru" / "src" / "cmru"
    cli_extended_package = (
        repo_root / "libraries" / "cli-extended" / "src" / "cli_extended"
    )
    worktree_package = repo_root / "libraries" / "worktree" / "src" / "worktree"
    cmru_package.mkdir(parents=True)
    cli_extended_package.mkdir(parents=True)
    (cmru_package / "__init__.py").write_text("", encoding="utf-8")
    (cli_extended_package / "__init__.py").write_text(
        "SOURCE_MARKER = 'source'\n", encoding="utf-8",
    )
    isolated_python_dir = tmp_path / "isolated-python"
    venv.EnvBuilder(with_pip=False).create(isolated_python_dir)
    isolated_python = isolated_python_dir / "bin" / "python"

    monkeypatch.setattr(cli, "__file__", str(cmru_package / "cli.py"))
    monkeypatch.setattr(cli.sys, "executable", str(isolated_python))
    monkeypatch.setattr("cmru.cli_support.cmru_version", lambda: "9.8.7")
    monkeypatch.delenv("PYTHONPATH", raising=False)

    def write_cli(import_worktree: bool) -> None:
        # D10: cli-extended is an installed wheel dependency, so its checkout
        # (present here) must NOT be put on the launcher's import path.
        imports = ""
        expected = "not any('cli-extended' in entry for entry in sys.path)"
        if import_worktree:
            imports += "from worktree import SOURCE_MARKER as WORKTREE_MARKER\n"
            expected += " and WORKTREE_MARKER == 'source'"
        (cmru_package / "cli.py").write_text(
            imports
            + "import sys\n"
            + "def main():\n"
            + f"    if sys.argv[1:] == ['version'] and {expected}:\n"
            + "        print('cmru 9.8.7')\n"
            + "        return 0\n"
            + "    return 1\n",
            encoding="utf-8",
        )

    # A checkout without the optional worktree library must still bind, and
    # the second run proves the launcher adds it when that source tree exists.
    write_cli(import_worktree=False)
    first_launcher_dir = tmp_path / "bound-without-worktree"
    first_launcher_dir.mkdir()
    first_launcher = cli._create_bound_cmru_launcher(first_launcher_dir)
    assert first_launcher == first_launcher_dir / "cmru"

    worktree_package.mkdir(parents=True)
    (worktree_package / "__init__.py").write_text(
        "SOURCE_MARKER = 'source'\n", encoding="utf-8",
    )
    write_cli(import_worktree=True)
    second_launcher_dir = tmp_path / "bound-with-worktree"
    second_launcher_dir.mkdir()
    second_launcher = cli._create_bound_cmru_launcher(second_launcher_dir)

    assert second_launcher == second_launcher_dir / "cmru"


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
        step_env={
            "CMRU_INTERNAL_BIN": "/wrong/step",
            "CMRU_RELEASE_COMMIT": "wrong-step-commit",
            "CMRU_RELEASE_TAG": "wrong-step-tag",
            "PATH": "/wrong/step/bin",
        },
        quiet=True,
    )
    project = SimpleNamespace(
        name="alpha", cwd="alpha", project_root=tmp_path / "alpha",
        env={
            "CMRU_INTERNAL_BIN": "/wrong/project",
            "CMRU_RELEASE_COMMIT": "wrong-project-commit",
            "CMRU_RELEASE_TAG": "wrong-project-tag",
            "GITHUB_PUSH_PAT": "declared-value",
        },
        runtime_kind="none", build_metadata={}, github_token="resolved-token",
        runner_steps={"build": step},
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

    release_identity = {
        "CMRU_RELEASE_COMMIT": SOURCE_COMMIT,
        "CMRU_RELEASE_TAG": "alpha-v1.2.3",
    }
    cli.run_project_step(
        project, "build", tmp_path, tmp_path / "logs", protected_env=release_identity,
    )

    assert captured["extra_env"] == {
        "CMRU_INTERNAL_BIN": "/wrong/project",
        "CMRU_RELEASE_COMMIT": "wrong-project-commit",
        "CMRU_RELEASE_TAG": "wrong-project-tag",
        "GITHUB_PUSH_PAT": "declared-value",
    }
    assert captured["protected_env"]["CMRU_INTERNAL_BIN"] == str(launcher_directory[0] / "cmru")
    assert captured["protected_env"]["GITHUB_PUSH_PAT"] == "resolved-token"
    assert captured["protected_env"]["CMRU_RELEASE_COMMIT"] == SOURCE_COMMIT
    assert captured["protected_env"]["CMRU_RELEASE_TAG"] == "alpha-v1.2.3"
    assert captured["path_prefixes"] == (launcher_directory[0],)


def test_run_project_step_clears_ambient_release_identity_from_ordinary_steps(
    monkeypatch, tmp_path,
):
    monkeypatch.setenv("CMRU_RELEASE_COMMIT", "stale-shell-commit")
    monkeypatch.setenv("CMRU_RELEASE_TAG", "stale-shell-tag")
    project = SimpleNamespace(
        name="alpha", cwd="alpha", project_root=tmp_path / "alpha",
        env={}, runtime_kind="none", build_metadata={}, github_token=None,
        runner_steps={"build": _step(
            "build", [{"label": "build", "argv": ["build"], "cwd": "."}], quiet=True,
        )},
    )
    monkeypatch.setattr(cli, "_create_bound_cmru_launcher", lambda directory: directory / "cmru")
    captured = {}
    monkeypatch.setattr(cli, "execute_step", lambda _step, _root, _logs, **kwargs: captured.update(kwargs))

    cli.run_project_step(project, "build", tmp_path, tmp_path / "logs")

    assert captured["protected_env"]["CMRU_RELEASE_COMMIT"] is None
    assert captured["protected_env"]["CMRU_RELEASE_TAG"] is None
    assert os.environ["CMRU_RELEASE_COMMIT"] == "stale-shell-commit"
    assert os.environ["CMRU_RELEASE_TAG"] == "stale-shell-tag"


def test_runner_reapplies_runtime_binding_after_project_environment_commands(
    monkeypatch, tmp_path,
):
    monkeypatch.setenv("CMRU_RELEASE_COMMIT", "stale-shell-commit")
    monkeypatch.setenv("CMRU_RELEASE_TAG", "stale-shell-tag")
    step = _step(
        "build",
        [{"label": "check", "argv": ["cmru", "version"], "cwd": "."}],
        step_env={
            "CMRU_INTERNAL_BIN": "/wrong/step",
            "PATH": "/wrong/step/bin",
            "CMRU_RELEASE_COMMIT": "wrong-step-commit",
            "CMRU_RELEASE_TAG": "wrong-step-tag",
        },
        env_command=["./set-env"],
        quiet=True,
    )

    helper_environment = {}

    def set_environment(*_args):
        helper_environment.update(os.environ)
        os.environ.update({
            "CMRU_INTERNAL_BIN": "/wrong/env-command",
            "PATH": "/wrong/env-command/bin",
            "CMRU_RELEASE_COMMIT": "env-command-commit",
            "CMRU_RELEASE_TAG": "env-command-tag",
        })

    monkeypatch.setattr(
        runner,
        "apply_env_command",
        set_environment,
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
        extra_env={"PATH": "/wrong/project", "CMRU_INTERNAL_BIN": "/wrong/project/cmru"},
        protected_env={
            "CMRU_INTERNAL_BIN": "/bound/cmru",
            "CMRU_RELEASE_COMMIT": None,
            "CMRU_RELEASE_TAG": None,
        },
        path_prefixes=(Path("/bound"),),
    )

    argv, command_env = captured[0]
    assert argv == ["cmru", "version"]
    assert command_env["CMRU_INTERNAL_BIN"] == "/bound/cmru"
    assert command_env["PATH"].split(":", 1)[0] == "/bound"
    assert "CMRU_RELEASE_COMMIT" not in command_env
    assert "CMRU_RELEASE_TAG" not in command_env
    assert "CMRU_RELEASE_COMMIT" not in helper_environment
    assert "CMRU_RELEASE_TAG" not in helper_environment


def test_tagged_artifact_release_identity_reaches_environment_helper(
    monkeypatch, tmp_path,
):
    candidate_commit = "a" * 40
    candidate_tag = "alpha-v1.2.3"
    monkeypatch.setenv("CMRU_RELEASE_COMMIT", "stale-shell-commit")
    monkeypatch.setenv("CMRU_RELEASE_TAG", "stale-shell-tag")
    step = _step(
        "build",
        [{"label": "check", "argv": ["build"], "cwd": "."}],
        step_env={
            "CMRU_RELEASE_COMMIT": "wrong-step-commit",
            "CMRU_RELEASE_TAG": "wrong-step-tag",
        },
        env_command=["./set-env"],
        quiet=True,
    )
    helper_environment = {}
    command_environment = {}

    def set_environment(*_args):
        helper_environment.update(os.environ)
        os.environ.update({
            "CMRU_RELEASE_COMMIT": "wrong-helper-commit",
            "CMRU_RELEASE_TAG": "wrong-helper-tag",
        })

    monkeypatch.setattr(runner, "apply_env_command", set_environment)
    monkeypatch.setattr(runner, "maybe_login_multi", lambda *_args: None)
    monkeypatch.setattr(
        runner,
        "run_command",
        lambda *_args, **_kwargs: (
            command_environment.update(os.environ)
            or runner.CommandResult(elapsed_seconds=0, evidence=None)
        ),
    )

    runner.execute_step(
        step,
        tmp_path,
        tmp_path / "logs",
        protected_env={
            "CMRU_RELEASE_COMMIT": candidate_commit,
            "CMRU_RELEASE_TAG": candidate_tag,
        },
    )

    assert helper_environment["CMRU_RELEASE_COMMIT"] == candidate_commit
    assert helper_environment["CMRU_RELEASE_TAG"] == candidate_tag
    assert command_environment["CMRU_RELEASE_COMMIT"] == candidate_commit
    assert command_environment["CMRU_RELEASE_TAG"] == candidate_tag
