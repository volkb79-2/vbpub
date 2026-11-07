"""Behavioral oracles for the CMRU v7.2.2 R2 shard s1 survivors."""
from __future__ import annotations

import dataclasses
from pathlib import Path
from types import SimpleNamespace

import pytest

from cmru import config, record_step, release_preconditions


@pytest.mark.parametrize(
    "record_type,field_name",
    [
        pytest.param(config.GitHubS2Config, "owner", id="GitHubS2Config"),
        pytest.param(config.TargetsConfig, "host", id="TargetsConfig"),
        pytest.param(config.VersionConfig, "strategy", id="VersionConfig"),
        pytest.param(config.InstallerWheel, "path", id="InstallerWheel"),
        pytest.param(config.InstallerConfig, "install_dir_system", id="InstallerConfig"),
        pytest.param(config.InstallerQuestion, "id", id="InstallerQuestion"),
        pytest.param(config.VariantConfig, "name", id="VariantConfig"),
        pytest.param(config.ToolDependency, "project", id="ToolDependency"),
        pytest.param(config.ProjectS2Config, "name", id="ProjectS2Config"),
        pytest.param(config.CleanupS2Config, "max_age_days", id="CleanupS2Config"),
        pytest.param(config.ForgeConfig, "github", id="ForgeConfig"),
        pytest.param(release_preconditions.GatePrecondition, "status", id="GatePrecondition"),
    ],
)
def test_config_and_precondition_records_reject_field_mutation(record_type, field_name):
    instance = object.__new__(record_type)

    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(instance, field_name, "changed")


def test_github_config_equality_does_not_depend_on_its_secret_token():
    base = {"owner": "octocat", "repo": "demo", "owner_type": "user"}
    left = config.GitHubS2Config(**base, token="secret-one")
    right = config.GitHubS2Config(**base, token="secret-two")

    assert left == right


def test_installer_question_model_defaults_to_required():
    question = config.InstallerQuestion(id="name", prompt="Name", type="string")

    assert question.required is True


def test_question_type_error_lists_choices_only_for_choice_questions():
    problems = config.question_problems([{
        "id": "count", "prompt": "Count", "type": "string", "default": 3,
    }])

    assert len(problems) == 1
    assert "does not fit type 'string'" in problems[0]
    assert "one of" not in problems[0]


def test_relative_path_validator_returns_none_for_valid_paths():
    assert config._relative_problem("releases/current", "path") is None


def test_installer_system_path_rejects_control_characters_even_when_absolute():
    problems = config.installer_problems(
        install_dir_system="/opt/cmru\nbad",
        install_dir_user="cmru",
        asset_suffix=".tar.xz",
        entrypoint="get.py",
        manifest_name="manifest.json",
        signature_name="manifest.minisig",
        required_commands=[],
        preserve=[],
        wheels=[],
        launchers=[],
        manifest_pubkey="",
    )

    assert any("install_dir_system must be an absolute path" in problem for problem in problems)


def _parse_version(strategy: str, bump: object = "patch"):
    return config._parse_version({"strategy": strategy, "bump": bump}, "demo")


@pytest.mark.parametrize("strategy", ["file:", "external:", "not-a-supported-strategy"])
def test_version_strategy_requires_a_supported_value_and_nonempty_suffix(strategy):
    with pytest.raises(SystemExit):
        _parse_version(strategy)


def test_version_bump_must_be_a_nonempty_string():
    with pytest.raises(SystemExit):
        _parse_version("scm", bump=7)


def test_installer_extensions_default_to_an_empty_list():
    assert config._parse_installer_extensions("demo", None) == []


@pytest.mark.parametrize("path", [r"\outside\extension.py", r"nested\..\outside.py"])
def test_installer_extensions_refuse_windows_rooted_and_traversal_paths(path):
    with pytest.raises(SystemExit):
        config._parse_installer_extensions("demo", [path])


def test_tool_dependency_path_must_be_a_nonempty_string():
    with pytest.raises(SystemExit):
        config._parse_tool_dependencies("demo", {
            "tool_dependencies": [{
                "project": "assay",
                "version": "12.0.0",
                "path": 42,
                "sha256": "0" * 64,
            }],
        })


def test_scalar_env_requires_the_call_site_to_choose_credential_policy():
    with pytest.raises(TypeError):
        config._scalar_env({}, "env")


def test_scalar_env_rejects_non_string_keys():
    with pytest.raises(SystemExit):
        config._scalar_env({1: "value"}, "env", reject_credentials=False)


def test_github_config_rejects_an_empty_owner():
    with pytest.raises(SystemExit):
        config._github({
            "owner": "", "repo": "demo", "owner_type": "user", "token_source": "cmru",
        })


def test_missing_github_token_source_is_reported_as_missing(capsys):
    with pytest.raises(SystemExit):
        config._github({"owner": "octocat", "repo": "demo", "owner_type": "user"})

    detail = capsys.readouterr().err
    assert "token_source is missing" in detail
    assert "token_source is None" not in detail


def _fake_executable(tmp_path: Path, monkeypatch, name: str, body: str) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    executable = bin_dir / name
    executable.write_text("#!/bin/sh\nset -eu\n" + body + "\n", encoding="utf-8")
    executable.chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir))
    monkeypatch.setenv("HOME", str(tmp_path))


def test_gh_token_reads_text_output_without_echoing_the_secret(tmp_path, monkeypatch, capsys):
    _fake_executable(tmp_path, monkeypatch, "gh", "printf 'private-token\\n'")

    assert config._gh_token() == "private-token"
    assert capsys.readouterr().out == ""


def test_gh_token_reports_the_nonzero_exit_code(tmp_path, monkeypatch, capsys):
    _fake_executable(tmp_path, monkeypatch, "gh", "exit 7")

    with pytest.raises(SystemExit):
        config._gh_token()

    assert "returned exit 7" in capsys.readouterr().err


def test_deep_merge_replaces_values_when_the_shapes_do_not_both_match():
    merged = config._deep_merge(
        {"github": {"token": "old"}},
        {"github": "disabled"},
    )

    assert merged == {"github": "disabled"}


def test_empty_resolved_secret_token_is_an_empty_string():
    assert config._resolved_secret_token({}, "secrets") == ""


def _valid_step() -> dict:
    return {
        "quiet": True,
        "commands": [{"label": "run", "argv": ["true"], "cwd": "."}],
    }


def test_runner_steps_must_be_a_nonempty_table():
    with pytest.raises(SystemExit):
        config._validate_runner_steps(["truthy"])


def test_runner_step_names_must_be_strings():
    with pytest.raises(SystemExit):
        config._validate_runner_steps({1: _valid_step()})


def test_runner_step_names_must_not_be_empty():
    with pytest.raises(SystemExit):
        config._validate_runner_steps({"": _valid_step()})


def test_runner_command_cwd_must_be_a_nonempty_string():
    with pytest.raises(SystemExit):
        config._validate_runner_steps({
            "build": {
                "quiet": True,
                "commands": [{"label": "run", "argv": ["true"], "cwd": 1}],
            },
        })


def _project_document(*, template_revision: int | None = 1, build_step: str = '"build"') -> str:
    revision_line = "" if template_revision is None else f"template_revision = {template_revision}\n"
    return f'''schema_version = 1

[github]
owner = "octocat"
repo = "demo"
owner_type = "user"
token_source = "cmru"

[targets]
host = "github"
registry = []

[runtime]
kind = "none"

[project]
id = "demo"
description = "Demo project"
{revision_line}prefix = "demo-v"
artifacts = ["wheel"]
scm_dist = "demo"

[project.version]
strategy = "scm"
bump = "patch"
paths = []

[project.release]
git_tag = true
build_step = {build_step}

[steps.run-tests]
quiet = true
commands = [{{ label = "test", argv = ["true"], cwd = "." }}]

[steps.build]
quiet = true
commands = [{{ label = "build", argv = ["true"], cwd = "." }}]

[steps.push]
quiet = true
commands = [{{ label = "push", argv = ["true"], cwd = "." }}]
'''


def test_template_revision_one_is_valid(tmp_path):
    path = tmp_path / "cmru.toml"
    path.write_text(_project_document(template_revision=1), encoding="utf-8")

    project = config._parse_project_document(path)[0]

    assert project.template_revision == 1


def test_project_release_build_step_type_errors_are_configuration_refusals(tmp_path):
    path = tmp_path / "cmru.toml"
    path.write_text(_project_document(build_step="17"), encoding="utf-8")

    with pytest.raises(SystemExit):
        config._parse_project_document(path)


def test_orchestration_project_table_cannot_be_empty(tmp_path, monkeypatch, capsys):
    raw = {
        "schema_version": 1,
        "github": {"owner": "octocat", "repo": "demo", "owner_type": "user", "token_source": "cmru"},
        "targets": {"host": "github", "registry": []},
        "orchestration": {
            "project_order": [],
            "default_steps": [],
            "execution_mode": "project-first",
            "project": {},
        },
    }
    monkeypatch.setattr(config, "_read_toml", lambda *_args: raw)

    with pytest.raises(SystemExit):
        config._load_orchestration_config(tmp_path / "cmru.orchestration.toml")

    assert "[orchestration.project.<id>] entries are required" in capsys.readouterr().err


def test_record_inventory_keeps_two_space_json_indentation(tmp_path):
    path = record_step.write_inventory(
        tmp_path,
        project="demo",
        tag="demo-v1.0.0",
        commit="a" * 40,
        artifacts=[],
    )

    text = path.read_text(encoding="utf-8")
    assert '\n  "artifacts": []' in text
    assert '\n "artifacts": []' not in text


def _fake_git(tmp_path: Path, monkeypatch, body: str) -> None:
    _fake_executable(tmp_path, monkeypatch, "git", body)


def test_git_blob_reader_returns_text_and_handles_git_failure_as_a_runtime_error(
    tmp_path, monkeypatch,
):
    success_root = tmp_path / "success"
    success_root.mkdir()
    _fake_git(tmp_path, monkeypatch, "printf 'source text\\n'")

    result = release_preconditions._git_blob_text(success_root, "a" * 40, "docs/file.md")
    assert result == "source text\n"
    assert isinstance(result, str)

    _fake_git(tmp_path, monkeypatch, "printf 'missing blob\\n' >&2; exit 1")
    with pytest.raises(RuntimeError, match="missing blob"):
        release_preconditions._git_blob_text(success_root, "a" * 40, "docs/file.md")


def test_git_blob_reader_uses_stdout_when_git_has_no_stderr(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    root.mkdir()
    _fake_git(tmp_path, monkeypatch, "printf 'stdout diagnostic\\n'; exit 1")

    with pytest.raises(RuntimeError) as caught:
        release_preconditions._git_blob_text(root, "b" * 40, "docs/file.md")

    assert "stdout diagnostic" in str(caught.value)


def _gate_config_blobs(orchestration: str) -> dict[str, str]:
    return {
        "cmru.orchestration.toml": orchestration,
        "run-gate.root.toml": "[environments]\n",
        "nested/demo/cmru.toml": "schema_version = 1\n",
    }


def test_gate_identity_config_materialization_creates_nested_parents_and_is_repeatable(
    tmp_path, monkeypatch,
):
    blobs = _gate_config_blobs(
        '[orchestration]\n[orchestration.project]\ndemo = { config = "nested/demo/cmru.toml" }\n'
    )
    monkeypatch.setattr(
        release_preconditions,
        "_git_blob_text",
        lambda _root, _revision, path: blobs[path],
    )
    destination = tmp_path / "already-created"
    destination.mkdir()

    release_preconditions._materialize_gate_identity_config(tmp_path, "HEAD", destination)
    release_preconditions._materialize_gate_identity_config(tmp_path, "HEAD", destination)

    assert (destination / "nested/demo/cmru.toml").read_text(encoding="utf-8") == "schema_version = 1\n"


def test_gate_identity_config_refuses_an_empty_project_table(tmp_path, monkeypatch):
    blobs = _gate_config_blobs("[orchestration]\n[orchestration.project]\n")
    monkeypatch.setattr(
        release_preconditions,
        "_git_blob_text",
        lambda _root, _revision, path: blobs[path],
    )

    with pytest.raises(RuntimeError, match="has no project configs"):
        release_preconditions._materialize_gate_identity_config(
            tmp_path, "HEAD", tmp_path / "identity",
        )


def test_gate_identity_preflight_extracts_the_selected_image_from_its_error(monkeypatch, tmp_path):
    detail = (
        "expected version='24.3.0'; tester-unified image 'tester-unified:local' was not found"
    )

    def unavailable(*_args):
        raise RuntimeError(detail)

    monkeypatch.setattr(
        release_preconditions, "_selected_tester_image_from_source", unavailable,
    )

    row = release_preconditions._run_gate_release_identity_preflight(tmp_path, tmp_path, "HEAD")

    assert row.status == "FAIL"
    assert "24.3.0" in row.remedy
    assert "tester-unified:local" in row.remedy


@pytest.mark.parametrize(
    ("include_version", "version"),
    [
        pytest.param(False, None, id="missing"),
        pytest.param(True, "", id="empty"),
        pytest.param(True, 24, id="non-string"),
    ],
)
def test_gate_identity_preflight_fails_when_the_check_returns_an_invalid_version(
    include_version, version, monkeypatch, tmp_path,
):
    monkeypatch.setattr(
        release_preconditions,
        "_selected_tester_image_from_source",
        lambda *_args: "tester-unified:local",
    )
    identity = {
        "tester_image": "tester-unified:local",
        "published_wheel_sha256": "a" * 64,
    }
    if include_version:
        identity["version"] = version
    monkeypatch.setattr(
        release_preconditions,
        "_load_release_gate",
        lambda *_args: SimpleNamespace(
            _current_run_gate_identity=lambda _root: identity,
        ),
    )
    monkeypatch.setattr(
        release_preconditions, "_materialize_gate_identity_config", lambda *_args: None,
    )

    row = release_preconditions._run_gate_release_identity_preflight(tmp_path, tmp_path, "HEAD")

    assert row.status == "FAIL"
    assert "did not report its version" in row.detail


@pytest.mark.parametrize("digest", [None, "not-a-sha256"], ids=["missing", "malformed"])
def test_gate_identity_preflight_requires_a_valid_published_wheel_digest(
    digest, monkeypatch, tmp_path,
):
    identity = {
        "version": "24.3.0",
        "tester_image": "tester-unified:local",
    }
    if digest is not None:
        identity["published_wheel_sha256"] = digest
    monkeypatch.setattr(
        release_preconditions,
        "_selected_tester_image_from_source",
        lambda *_args: "tester-unified:local",
    )
    monkeypatch.setattr(
        release_preconditions,
        "_load_release_gate",
        lambda *_args: SimpleNamespace(
            _current_run_gate_identity=lambda _root: identity,
        ),
    )
    monkeypatch.setattr(
        release_preconditions, "_materialize_gate_identity_config", lambda *_args: None,
    )

    row = release_preconditions._run_gate_release_identity_preflight(tmp_path, tmp_path, "HEAD")

    assert row.status == "FAIL"
    assert "did not report its published wheel SHA-256" in row.detail
