"""The domain-error family (W2-PKG5 review fix round 1, B1).

Deliberate refusals and failures reach the CLI boundary as ``CmruError``: the
taxonomy exit code, a plain ``[ERROR] <message>`` line, and never the library's
"unexpected <Type>" label. They subclass ``RuntimeError`` on purpose, so every
internal ``except RuntimeError`` / ``_DOMAIN_ERRORS`` cleanup or rollback site
still catches them.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from cli_extended import CliFailure

from cmru import cli, errors, exit_codes, runner, transaction


def _project(**overrides):
    values = dict(
        name="demo", cwd="demo", project_root=Path("demo"), env={}, build_metadata={},
        runtime_kind="none", runner_steps={}, steps={}, github_token="", prefix="demo-v",
    )
    values.update(overrides)
    return SimpleNamespace(**values)


def _loaded(root: Path, project, *, github_token=""):
    return (
        root, {project.name: project}, [project.name], [project.name], ["build"],
        "project-first", {}, cli.CleanupConfig([], [], [], []),
        cli.GitHubConfig("owner", "repo", github_token, "user"),
        cli.ReleaseEnvConfig({}, None),
    )


def _install(monkeypatch, tmp_path, project, *, github_token="", config="cmru.orchestration.toml"):
    (tmp_path / config).write_text("[project]\n" if config == "cmru.toml" else "")
    monkeypatch.setattr(cli, "_resolve_config", lambda _path: tmp_path / config)
    monkeypatch.setattr(cli, "load_config", lambda _path: _loaded(tmp_path, project, github_token=github_token))
    # Hard stop: nothing in this module may reach the network.
    monkeypatch.setattr(
        cli, "http_request",
        lambda *_a, **_k: pytest.fail("a refusal test reached the GitHub API"),
    )
    monkeypatch.setattr(cli, "resolve_versions_from_git", lambda *_a, **_k: None)


def _refused(capsys, rc: int, expected: int, message: str) -> str:
    err = capsys.readouterr().err
    assert rc == expected, err
    assert f"[ERROR] {message}" in err, err
    assert "unexpected" not in err and "Traceback" not in err, err
    return err


FAMILY = [
    (errors.UsageRefusal, exit_codes.CONFIG_ERROR),
    (errors.CredentialMissing, exit_codes.PREREQ_MISSING),
    (errors.StepUnavailable, exit_codes.PREREQ_MISSING),
    (errors.UnsafeRecord, exit_codes.REFUSED),
    (errors.RefusedBeforeChange, exit_codes.REFUSED),
    (errors.ReleaseLockHeld, exit_codes.REFUSED),
]


@pytest.mark.parametrize(("kind", "code"), FAMILY)
def test_each_family_member_carries_its_exit_code_and_stays_a_runtime_error(kind, code):
    exc = kind("plain message", hint="do this")
    assert exc.exit_code == code and exc.hint == "do this" and str(exc) == "plain message"
    # RuntimeError ON PURPOSE: cleanup/rollback sites catch it; CliFailure so the
    # library renders it without the "unexpected" label.
    assert isinstance(exc, RuntimeError) and isinstance(exc, CliFailure)
    assert isinstance(exc, errors.CmruError)
    assert issubclass(transaction._DOMAIN_ERRORS[0], RuntimeError)
    assert isinstance(exc, transaction._DOMAIN_ERRORS) and isinstance(exc, cli._DOMAIN_ERRORS)


def test_the_transaction_module_exposes_the_family_it_always_had():
    assert transaction.RefusedBeforeChange is errors.RefusedBeforeChange
    assert transaction.ReleaseLockHeld is errors.ReleaseLockHeld
    assert issubclass(errors.ReleaseLockHeld, errors.RefusedBeforeChange)


def test_a_domain_error_is_caught_by_the_transaction_cleanup_catch_all():
    """The rollback `except RuntimeError` clause must still see every member."""
    for kind, _code in FAMILY:
        try:
            try:
                raise kind("boom")
            except RuntimeError:
                caught = True
        except BaseException:  # pragma: no cover - the failure being guarded
            caught = False
        assert caught, kind


def test_step_failed_is_a_called_process_error_with_the_exit_status():
    exc = errors.StepFailed("step 'build' of project 'p' failed (exit 7); see /x.log",
                            returncode=7, cmd=["false"])
    assert isinstance(exc, subprocess.CalledProcessError) and isinstance(exc, errors.CmruError)
    assert exc.returncode == 7 and exc.cmd == ["false"] and exc.exit_code == exit_codes.FAILURE
    assert str(exc) == "step 'build' of project 'p' failed (exit 7); see /x.log"


# ---- the converted refusal sites, through the real CLI boundary ------------------------------

def test_publish_without_a_token_is_exit_3(monkeypatch, tmp_path, capsys):
    _install(monkeypatch, tmp_path, _project())
    rc = cli.main(["publish", "demo", "--from-checkout", "--config", str(tmp_path / "cmru.toml")])
    _refused(capsys, rc, 3, "Publishing requires GITHUB_PUSH_PAT")


def test_cleanup_remove_assets_without_a_token_is_exit_3(monkeypatch, tmp_path, capsys):
    _install(monkeypatch, tmp_path, _project(), config="cmru.toml")
    rc = cli.main(["cleanup", "--remove-assets", "30d", "--dry-run"])
    _refused(capsys, rc, 3, "github.token is required for cleanup")


def test_cleanup_policy_without_a_token_is_exit_3(monkeypatch, tmp_path, capsys):
    _install(monkeypatch, tmp_path, _project())
    rc = cli.main(["cleanup", "demo", "--policy", "--dry-run"])
    _refused(capsys, rc, 3, "Cleanup for 'demo' requires GITHUB_PUSH_PAT")


def test_repository_wide_ghcr_cleanup_without_a_token_is_exit_3(monkeypatch, tmp_path, capsys):
    project = _project(github_token="tok")
    _install(monkeypatch, tmp_path, project)
    cleanup = cli.CleanupConfig(["demo-v"], [], ["pkg"], ["pkg"])
    loaded = list(_loaded(tmp_path, project))
    loaded[7] = cleanup
    monkeypatch.setattr(cli, "load_config", lambda _path: tuple(loaded))
    monkeypatch.setattr(cli, "list_releases", lambda *_a: [])
    monkeypatch.setattr(cli, "list_remote_tag_refs_matching", lambda *_a, **_k: {})
    monkeypatch.setattr(cli, "local_git_tag_oid", lambda *_a: None)
    monkeypatch.setattr(cli, "_latest_version_for_prefix", lambda *_a, **_k: "")
    rc = cli.main(["cleanup", "demo", "--policy", "--dry-run"])
    _refused(capsys, rc, 3, "Repository-wide GHCR cleanup requires GITHUB_PUSH_PAT")


def test_a_declared_step_that_is_absent_is_exit_3(monkeypatch, tmp_path, capsys):
    _install(monkeypatch, tmp_path, _project(runner_steps={}))
    rc = cli.main(["publish", "demo", "--from-checkout", "--dry-run", "--config", "x"])
    _refused(capsys, rc, 3, "demo: required declared step 'push' is absent")


def test_a_missing_derived_working_directory_is_exit_3(monkeypatch, tmp_path, capsys):
    _install(monkeypatch, tmp_path, _project(cwd=None, runner_steps={"build": _failing_step()}))
    rc = cli.main(["run", "demo", "--dry-run"])
    _refused(capsys, rc, 3, "demo: derived project working directory is absent")


def test_an_unsafe_retained_build_record_is_exit_4(monkeypatch, tmp_path, capsys):
    project = _project(project_root=tmp_path / "demo", github_token="tok")
    (tmp_path / "demo" / "artifacts").mkdir(parents=True)
    _install(monkeypatch, tmp_path, project, github_token="tok")
    rc = cli.main(["cleanup", "demo", "--delete-build-output", "20260101T000000Z_" + "a" * 40, "--yes"])
    _refused(capsys, rc, 4, "demo: retained build")


def test_a_bad_delete_build_output_id_is_a_usage_error_2():
    with pytest.raises(errors.UsageRefusal, match="exact <commit-date>_<40-hex-commit>") as raised:
        transaction._require_build_output_id("not-an-id")
    assert raised.value.exit_code == exit_codes.CONFIG_ERROR


def test_resuming_a_missing_release_worktree_is_a_usage_error_2(tmp_path):
    missing = tmp_path / "nosuch"
    with pytest.raises(errors.UsageRefusal, match="release worktree does not exist") as first:
        transaction.resume_workspace(tmp_path, missing)
    with pytest.raises(errors.UsageRefusal, match="release worktree does not exist") as second:
        transaction.read_release_scope_for_path(missing)
    assert first.value.exit_code == second.value.exit_code == exit_codes.CONFIG_ERROR


def test_resume_of_a_missing_worktree_through_main_is_exit_2(monkeypatch, tmp_path, capsys):
    """The refusal also survives the release launcher's own catch-all (no flattening to 1)."""
    _install(monkeypatch, tmp_path, _project())
    rc = cli.main(["release", "demo", "--resume", str(tmp_path / "nosuch")])
    err = capsys.readouterr().err
    assert rc == 2, err
    assert "unexpected" not in err and "Traceback" not in err


@pytest.mark.parametrize(("kind", "code"), FAMILY)
def test_the_legacy_exit_paths_keep_the_family_exit_code(kind, code, capsys):
    with pytest.raises(SystemExit) as exited:
        cli._exit_for_domain_error(kind("refused here", hint="try that"))
    assert exited.value.code == code
    captured = capsys.readouterr()
    assert "[ERROR] refused here" in captured.err and "try that" in captured.out + captured.err


# ---- a failing project step -------------------------------------------------------------------

def _failing_step():
    return runner.StepConfig(
        name="build", commands=[{"label": "compile", "argv": ["false"], "cwd": "."}],
        bake_set_prefix=None, bake_set_vars=[], no_cache_env=None, clean_dirs=[],
        required_env=[], login=None, step_env={}, env_command=None,
    )


def test_a_failing_step_command_raises_step_failed_naming_step_project_and_log(tmp_path):
    project_root = tmp_path / "proj"
    project_root.mkdir()
    with pytest.raises(errors.StepFailed) as raised:
        runner.execute_step(_failing_step(), project_root, tmp_path / "logs")
    exc = raised.value
    log_path = tmp_path / "logs" / "build.log"
    assert str(exc) == f"step 'build' of project 'proj' failed (exit 1); see {log_path}"
    assert exc.returncode == 1 and exc.exit_code == exit_codes.FAILURE
    assert isinstance(exc, subprocess.CalledProcessError)
    assert isinstance(exc.__cause__, subprocess.CalledProcessError)


def test_run_of_a_failing_step_is_exit_1_with_a_plain_message(monkeypatch, tmp_path, capsys):
    project_root = tmp_path / "demo"
    project_root.mkdir()
    project = _project(project_root=project_root, runner_steps={"build": _failing_step()})
    _install(monkeypatch, tmp_path, project)
    rc = cli.main(["run", "demo"])
    err = _refused(capsys, rc, 1, "step 'build' of project 'demo' failed (exit 1); see ")
    assert "build.log" in err


def test_traceback_prints_no_stack_for_a_deliberate_refusal(monkeypatch, tmp_path, capsys):
    _install(monkeypatch, tmp_path, _project())
    # ``--traceback`` is the library's own switch for *unexpected* errors; a deliberate
    # refusal has no stack worth expanding, so it stays the one-line refusal, same code.
    rc = cli.main(["publish", "demo", "--from-checkout", "--traceback", "--config", str(tmp_path / "cmru.toml")])
    _refused(capsys, rc, 3, "Publishing requires GITHUB_PUSH_PAT")


def test_traceback_still_shows_the_stack_of_a_genuine_internal_error(monkeypatch, tmp_path):
    _install(monkeypatch, tmp_path, _project())

    def boom(*_args, **_kwargs):
        raise ZeroDivisionError("internal invariant")

    monkeypatch.setattr(cli, "require_project_publish_credentials", boom)
    with pytest.raises(ZeroDivisionError, match="internal invariant"):
        cli.main(["publish", "demo", "--from-checkout", "--traceback", "--config", str(tmp_path / "cmru.toml")])


@pytest.mark.parametrize("child_status", [1, 2, 3, 4])
def test_a_release_child_exit_code_still_propagates_through_the_parent(
    monkeypatch, tmp_path, child_status,
):
    """The parent returns the child's status unchanged, whatever the family says."""
    left, right = SimpleNamespace(name="left"), SimpleNamespace(name="right")
    monkeypatch.setattr(
        transaction, "project_git_family_groups",
        lambda *_args: {tmp_path / "left": [left], tmp_path / "right": [right]},
    )
    monkeypatch.setenv("CMRU_INTERNAL_BIN", "/usr/bin/cmru")
    monkeypatch.setattr(
        cli.subprocess, "run",
        lambda argv, **_kwargs: subprocess.CompletedProcess(argv, child_status),
    )
    assert cli._dispatch_independent_git_families(
        "release", [], tmp_path / "cmru.toml", tmp_path, {"left": left, "right": right},
        ["left", "right"], original_target=None, forward_from=None,
    ) == child_status


# ---- the sweep: refusals reachable from a user action, one test per converted class -----------

def _raises(kind, match):
    return pytest.raises(kind, match=match)


def test_reserved_release_env_keys_are_a_usage_refusal(tmp_path):
    github = cli.GitHubConfig("owner", "repo", "", "user")
    with _raises(errors.UsageRefusal, "reserved for resolved publisher credentials") as first:
        cli.apply_release_env(github, cli.ReleaseEnvConfig({"GITHUB_TOKEN": "x"}, None))
    assert first.value.exit_code == exit_codes.CONFIG_ERROR


def test_resume_scope_refusals_carry_their_codes(tmp_path):
    configs, order = {"a": _project(name="a")}, ["a"]
    cfg = tmp_path / "cmru.toml"
    with _raises(errors.UsageRefusal, "no recorded project scope") as none_recorded:
        cli._release_resume_target(cfg, None, None, configs, order)
    with _raises(errors.UnsafeRecord, "malformed project-scope metadata") as malformed:
        cli._release_resume_target(cfg, None, ["a", "a"], configs, order)
    with _raises(errors.UsageRefusal, "absent from the selected config") as unknown:
        cli._release_resume_target(cfg, None, ["ghost"], configs, order)
    assert none_recorded.value.exit_code == unknown.value.exit_code == exit_codes.CONFIG_ERROR
    assert malformed.value.exit_code == exit_codes.REFUSED


def test_a_requested_step_that_is_not_declared_is_exit_3(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "resolve_versions_from_git", lambda *_a, **_k: None)
    with _raises(errors.StepUnavailable, "requested step 'build' is not declared") as raised:
        cli._run_project_steps(tmp_path, {"demo": _project()}, ["demo"], ["build"])
    assert raised.value.exit_code == exit_codes.PREREQ_MISSING


def test_a_release_without_a_declared_gate_is_a_refusal_4(tmp_path):
    with _raises(errors.RefusedBeforeChange, "no release gate is declared") as raised:
        cli._run_release_gates(tmp_path, {"demo": _project()}, ["demo"])
    assert raised.value.exit_code == exit_codes.REFUSED


def test_a_local_main_ahead_of_origin_is_a_refusal_4(tmp_path, monkeypatch):
    monkeypatch.setattr(transaction, "local_main_divergence", lambda *_a, **_k: (2, 0))
    with _raises(errors.RefusedBeforeChange, "2 commit.s. ahead of origin/main") as raised:
        transaction.assert_local_main_not_ahead(tmp_path)
    assert raised.value.exit_code == exit_codes.REFUSED


def test_a_build_worktree_outside_the_managed_directory_is_a_usage_error_2(tmp_path):
    with _raises(errors.UsageRefusal, "outside this repository's managed .worktrees") as raised:
        transaction.discard_build_workspace(tmp_path, tmp_path / "elsewhere", dry_run=True)
    assert raised.value.exit_code == exit_codes.CONFIG_ERROR


def test_missing_required_step_environment_is_exit_3(monkeypatch):
    monkeypatch.delenv("CMRU_SWEEP_NEEDED", raising=False)
    with _raises(errors.CredentialMissing, "CMRU_SWEEP_NEEDED") as raised:
        runner.ensure_required_env(["CMRU_SWEEP_NEEDED"])
    assert raised.value.exit_code == exit_codes.PREREQ_MISSING


def test_changelog_refusals_carry_their_codes(tmp_path):
    from cmru import changelog

    with _raises(errors.StepUnavailable, "no release.changelog is configured") as absent:
        changelog._validate_changelog_path(_project(changelog=None), tmp_path)
    with _raises(errors.UsageRefusal, "must be a non-empty project-relative path") as bad:
        changelog._validate_changelog_path(_project(changelog="../x.md"), tmp_path)
    assert absent.value.exit_code == exit_codes.PREREQ_MISSING
    assert bad.value.exit_code == exit_codes.CONFIG_ERROR
