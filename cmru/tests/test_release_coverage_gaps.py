"""Behavioral witnesses for release-gate coverage gaps.

These cases exercise the public diagnostic and contextual-selection edges that
the normal happy-path suite does not reach.  They deliberately assert the
refusal or output, rather than merely executing a line for coverage.
"""
from __future__ import annotations

import io
import subprocess
import sys
from dataclasses import FrozenInstanceError
from pathlib import Path
from types import SimpleNamespace

import pytest

from cmru import cli, cli_support, config, getpy, resolve as resolve_module, runner, scaffold, standards, tester_gate, tool_deps
from cmru.cli_support import select_target_names


def _loaded(projects, order=None):
    order = list(order or projects)
    return (
        Path("/repo"), projects, order, order, ["run-tests"], "project-first",
        {}, None, cli.GitHubConfig("owner", "repo", "token", "user"),
        cli.ReleaseEnvConfig({}, None),
    )


def _minimal_project(name: str = "demo") -> str:
    return f'''schema_version = 1

[project]
id = "{name}"
description = "{name} project"
prefix = "{name}-v"
artifacts = ["wheel"]

[project.version]
strategy = "scm"
bump = "patch"

[project.release]
git_tag = true
build_step = "build"

[steps.run-tests]
quiet = true
commands = [{{label = "test", argv = ["true"], cwd = "."}}]

[steps.build]
quiet = true
commands = [{{label = "build", argv = ["true"], cwd = "."}}]

[steps.push]
quiet = true
commands = [{{label = "push", argv = ["true"], cwd = "."}}]
'''


def test_selector_uses_declared_order_for_an_estate_without_context():
    assert select_target_names(None, {"a": object(), "b": object()}, ["b", "a"]) == ["b", "a"]


def test_config_parser_refuses_io_and_reserved_names(tmp_path, monkeypatch):
    path = tmp_path / "cmru.toml"
    path.write_text(_minimal_project(), encoding="utf-8")
    original_open = Path.open

    def fail_open(self, *args, **kwargs):
        if self == path:
            raise OSError("permission denied")
        return original_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", fail_open)
    with pytest.raises(SystemExit):
        config._read_toml(path, "cmru.toml")

    reserved = tmp_path / "reserved" / "cmru.toml"
    reserved.parent.mkdir()
    reserved.write_text(_minimal_project("all"), encoding="utf-8")
    with pytest.raises(SystemExit):
        config._parse_project_document(reserved, require_repository_facts=False)


def test_project_config_rejects_one_central_fact_duplicate(tmp_path):
    path = tmp_path / "cmru.toml"
    path.write_text(
        "schema_version = 1\n[github]\nowner = 'o'\nrepo = 'r'\nowner_type = 'user'\n\n"
        + _minimal_project().split("schema_version = 1\n", 1)[1],
        encoding="utf-8",
    )
    with pytest.raises(SystemExit):
        config._parse_project_document(path, require_repository_facts=False)


def test_invocation_context_is_immutable():
    context = config.InvocationContext(
        Path("/repo/cmru.toml"), "project", Path("/repo"), "demo", "project"
    )
    with pytest.raises(FrozenInstanceError):
        context.scope = "estate"


def _orchestration_with_entry(tmp_path: Path, entry_name: str, config_path: str) -> Path:
    path = tmp_path / "cmru.orchestration.toml"
    path.write_text(
        f'''schema_version = 1
[github]
owner = "o"
repo = "r"
owner_type = "user"
[targets]
host = "github"
registry = []
[orchestration]
project_order = ["{entry_name}"]
default_projects = ["{entry_name}"]
default_steps = ["run-tests"]
execution_mode = "project-first"
[orchestration.project.{entry_name}]
config = "{config_path}"
depends_on = []
[cleanup]
release_tag_prefixes = ["*"]
keep_release_tags = []
ghcr_packages = []
ghcr_delete_packages = []
''',
        encoding="utf-8",
    )
    return path


def test_orchestration_rejects_reserved_and_mismatched_project_ids(tmp_path, monkeypatch):
    reserved = _orchestration_with_entry(tmp_path, "all", "all/cmru.toml")
    with pytest.raises(SystemExit):
        config.load_forge_config(reserved, require_orchestration=True)

    mismatch = _orchestration_with_entry(tmp_path, "wanted", "wanted/cmru.toml")
    monkeypatch.setattr(
        config,
        "_parse_project_document",
        lambda *_args, **_kwargs: (SimpleNamespace(name="other"), None, None),
    )
    with pytest.raises(SystemExit):
        config.load_forge_config(mismatch, require_orchestration=True)


def test_context_discovery_refuses_non_file_and_outside_project(tmp_path, monkeypatch):
    bad = tmp_path / "cmru.orchestration.toml"
    bad.mkdir()
    with pytest.raises(SystemExit):
        config.resolve_invocation_context(cwd=tmp_path)

    project = tmp_path / "cmru.toml"
    project.write_text(_minimal_project(), encoding="utf-8")
    outside = tmp_path.parent / "outside-context"
    outside.mkdir()
    monkeypatch.setattr(
        config,
        "_nearest_file",
        lambda _cwd, filename: project if filename == "cmru.toml" else None,
    )
    monkeypatch.setattr(
        config,
        "load_forge_config",
        lambda _path: SimpleNamespace(projects={"demo": object()}),
    )
    with pytest.raises(SystemExit):
        config.resolve_invocation_context(cwd=outside)


def test_context_helpers_cover_standalone_and_invalid_selection(monkeypatch, tmp_path):
    assert config._project_for_directory(SimpleNamespace(orchestration=None), tmp_path) is None
    assert config._refuse_unregistered_project(SimpleNamespace(orchestration=None), tmp_path) is None
    with pytest.raises(SystemExit):
        config.resolve_invocation_context(cwd=tmp_path)

    monkeypatch.setattr(config, "_error", lambda _message: None)
    with pytest.raises(AssertionError, match="unreachable"):
        config.resolve_invocation_context(tmp_path / "wrong-name.toml", cwd=tmp_path)


def test_cli_native_logging_and_candidate_integrity_edges(tmp_path, monkeypatch):
    monkeypatch.setenv("CMRU_NATIVE_RELEASE_LOGGING", "0")
    cli._configure_native_release_logging(tmp_path, append=False)

    class Stdin:
        def fileno(self):
            return 17

    monkeypatch.delenv("CMRU_NATIVE_RELEASE_LOGGING", raising=False)
    proc = SimpleNamespace(stdin=Stdin())
    calls = []
    monkeypatch.setattr(cli.subprocess, "Popen", lambda *args, **kwargs: (calls.append((args, kwargs)) or proc))
    monkeypatch.setattr(cli.os, "dup2", lambda *args: calls.append(("dup2", args)))
    cli._configure_native_release_logging(tmp_path, append=True)
    assert calls[0][0][0] == ["tee", "-a", str(tmp_path / "cmru.release.log")]
    assert (tmp_path / "cmru.release.log").read_text(encoding="utf-8") == "\n---\n"

    monkeypatch.setattr(cli, "_git", lambda *_args: "actual")
    with pytest.raises(RuntimeError, match="moved HEAD"):
        cli._assert_release_candidate_unchanged(tmp_path, "demo", "expected")
    monkeypatch.setattr(cli, "_git", lambda *_args: "same")
    monkeypatch.setattr(cli, "_worktree_changed_paths", lambda *_args: ["tracked.txt"])
    with pytest.raises(RuntimeError, match="non-ignored changes"):
        cli._assert_release_candidate_unchanged(tmp_path, "demo", "same")

    marker = object()
    monkeypatch.setattr(cli, "resolve_invocation_context", lambda *_args, **_kwargs: marker)
    assert cli._invocation_context(None) is marker


def test_config_diagnostic_flushes_both_lines(monkeypatch):
    calls = []
    monkeypatch.setattr(
        cli_support,
        "print",
        lambda *args, **kwargs: calls.append((args, kwargs)),
        raising=False,
    )

    cli_support.write_config_diagnostic("bad")

    assert len(calls) == 2
    assert all(call[1]["flush"] is True for call in calls)


def test_cli_file_strategy_rechecks_the_changed_candidate(monkeypatch, tmp_path):
    project = cli.ProjectConfig(
        "demo", {}, {}, prefix="demo-v", version=cli.VersionSpec(strategy="file:VERSION"),
    )
    workspace = cli.transaction.ReleaseWorkspace(tmp_path, tmp_path / "child", "cmru/release/x", "a" * 40)
    calls = []
    monkeypatch.setattr(cli.transaction, "write_release_progress", lambda *args: None)
    monkeypatch.setattr(cli.transaction, "push_backup_branch", lambda *args: None)
    monkeypatch.setattr(cli.transaction, "promote_workspace", lambda *args: None)
    monkeypatch.setattr(cli.transaction, "write_release_result", lambda *args: None)
    monkeypatch.setattr(cli, "apply_project_release_env", lambda *args: None)
    monkeypatch.setattr(cli, "_prepare_release_projects", lambda *args, **kwargs: None)
    monkeypatch.setattr(cli, "_run_release_gates", lambda *args: calls.append("gate"))
    git_calls = []
    monkeypatch.setattr(cli, "_git", lambda *args, **kwargs: (git_calls.append(1) or ("gated" if len(git_calls) == 1 else "candidate")))
    monkeypatch.setattr("cmru.version.release_cmd", lambda *args, **kwargs: None)
    monkeypatch.setattr(cli, "_tag_on_head", lambda *args: None)
    cli._release_projects_sequentially(
        tmp_path, {"demo": project}, workspace, ["demo"],
        github_config=cli.GitHubConfig("o", "r", "t", "user"),
        env_config=cli.ReleaseEnvConfig({}, None), no_build=True,
    )
    assert calls == ["gate", "gate"]


def test_cli_status_from_console_entrypoint_configures_native_logging(monkeypatch, tmp_path):
    project = cli.ProjectConfig("demo", {}, {}, prefix="demo-v")
    monkeypatch.setattr(cli, "_resolve_config", lambda _arg: tmp_path / "cmru.toml")
    monkeypatch.setattr(cli, "load_config", lambda _path: _loaded({"demo": project}))
    monkeypatch.setattr(cli, "apply_release_env", lambda *_args: None)
    calls = []
    monkeypatch.setattr(
        cli,
        "_configure_native_release_logging",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )
    monkeypatch.setattr("cmru.version.status_cmd", lambda *args, **kwargs: None)
    monkeypatch.setattr(sys, "argv", ["cmru", "status", "demo"])
    assert cli.main() is None
    assert calls and calls[0][1] == {"append": False}

    calls.clear()
    assert cli.main(["status", "demo"]) is None
    assert calls == []


def test_project_loader_requires_both_repository_fact_tables(monkeypatch, tmp_path):
    github = SimpleNamespace(owner="owner", repo="repo", owner_type="user")
    project = SimpleNamespace(name="demo")
    monkeypatch.setattr(config, "_parse_project_document", lambda *_args: (project, github, None))
    monkeypatch.setattr(config, "_load_repository_secrets", lambda *_args: (None, {}))

    with pytest.raises(AssertionError):
        config._load_project_config(tmp_path / "cmru.toml")


def test_cli_changelog_backfill_rejects_bad_assignments(monkeypatch, tmp_path, capsys):
    projects = {
        "demo": cli.ProjectConfig("demo", {}, {}, prefix="demo-v", project_root=tmp_path),
        "other": cli.ProjectConfig("other", {}, {}, prefix="other-v", project_root=tmp_path),
    }
    loaded = _loaded(projects, ["demo", "other"])
    monkeypatch.setattr(cli, "_resolve_config", lambda _arg: tmp_path / "cmru.orchestration.toml")
    monkeypatch.setattr(cli, "load_config", lambda _path: loaded)
    with pytest.raises(SystemExit):
        cli.main(["changelog", "demo", "--backfill-tag", "other-v1"])
    with pytest.raises(SystemExit):
        cli.main(["changelog", "demo", "--backfill-tag", "demo-v1", "--backfill-tag", "demo-v2"])
    with pytest.raises(SystemExit):
        cli.main(["changelog", "all", "--backfill-tag", "demo-v1"])
    with pytest.raises(SystemExit):
        cli.main(["changelog", "demo", "--config", str(tmp_path / "cmru.orchestration.toml")])
    assert "required" in capsys.readouterr().err


def test_cli_changelog_backfill_diagnostic_discloses_no_prefix_match(monkeypatch, tmp_path, capsys):
    project = cli.ProjectConfig("demo", {}, {}, prefix="demo-v", project_root=tmp_path)
    cfg = tmp_path / "cmru.orchestration.toml"
    monkeypatch.setattr(cli, "_resolve_config", lambda _arg: cfg)
    monkeypatch.setattr(cli, "load_config", lambda _path: _loaded({"demo": project}))

    with pytest.raises(SystemExit):
        cli.main(["changelog", "demo", "--backfill-tag", "other-v1"])

    assert "matched none" in capsys.readouterr().err


def test_cleanup_rejects_wrong_scope_and_missing_project(monkeypatch, tmp_path):
    projects = {"demo": cli.ProjectConfig("demo", {}, {}, prefix="demo-v", project_root=tmp_path)}
    loaded = _loaded(projects)
    monkeypatch.setattr(cli, "_resolve_config", lambda _arg: tmp_path / "cmru.toml")
    monkeypatch.setattr(cli, "load_config", lambda _path: loaded)
    monkeypatch.setattr(cli, "_select_projects", lambda *_args: ["demo", "other"])
    with pytest.raises(SystemExit):
        cli.main(["cleanup", "all", "--delete-unmanaged-release-tag", "x-v1", "--dry-run"])
    monkeypatch.setattr(cli, "_select_projects", lambda *_args: ["demo", "other"])
    with pytest.raises(SystemExit):
        cli.main(["cleanup", "demo", "--delete-build-output", "x", "--dry-run"])
    monkeypatch.setattr(cli, "_select_projects", lambda *_args: ["missing"])
    with pytest.raises(SystemExit):
        cli.main(["cleanup", "demo", "--delete-unmanaged-release-tag", "x-v1", "--dry-run"])
    with pytest.raises(SystemExit):
        cli.main(["cleanup", "demo", "--delete-build-output", "x", "--dry-run"])


def _patch_loader(monkeypatch, module, config_path, loaded):
    monkeypatch.setattr("cmru.cli._resolve_config", lambda _arg: config_path)
    monkeypatch.setattr("cmru.cli.load_config", lambda _path: loaded)
    monkeypatch.setattr(module, "resolve_invocation_context", lambda *_args, **_kwargs: SimpleNamespace(project_name=None, scope="estate"), raising=False)


def test_getpy_context_outputs_and_rejections(monkeypatch, tmp_path, capsys):
    project = SimpleNamespace(prefix="demo-v", github_token="")
    loaded = _loaded({"demo": project, "other": project}, ["demo", "other"])
    cfg = tmp_path / "cmru.toml"
    cfg.write_text("", encoding="utf-8")
    monkeypatch.setattr("cmru.cli._resolve_config", lambda _arg: cfg)
    one_loaded = _loaded({"demo": project})
    monkeypatch.setattr("cmru.cli.load_config", lambda _path: one_loaded)
    monkeypatch.setattr(getpy, "render_from_config", lambda name, _path: f"# {name}\n")
    getpy.getpy_main([])
    assert "# demo" in capsys.readouterr().out
    monkeypatch.setattr("cmru.cli.load_config", lambda _path: loaded)
    monkeypatch.setattr("cmru.config.resolve_invocation_context", lambda *_args, **_kwargs: SimpleNamespace(project_name=None, scope="estate"))
    getpy.getpy_main([])
    assert "get.py Project" in capsys.readouterr().out
    orch = tmp_path / "cmru.orchestration.toml"
    monkeypatch.setattr("cmru.cli._resolve_config", lambda _arg: orch)
    getpy.getpy_main([])
    assert "get.py Project" in capsys.readouterr().out

    monkeypatch.setattr("cmru.cli._resolve_config", lambda _arg: cfg)
    getpy.getpy_main(["demo"])
    assert "# demo" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        getpy.getpy_main(["all", "--output", str(tmp_path / "one")])
    with pytest.raises(SystemExit):
        getpy.getpy_main(["demo", "--output", "one", "--output-dir", str(tmp_path / "many")])
    getpy.getpy_main(["all", "--output-dir", str(tmp_path / "many")])
    assert (tmp_path / "many/demo-get.py").is_file()
    out = capsys.readouterr().out
    assert "get.py Project: DEMO" in out or "Written to" in out

    monkeypatch.setattr(getpy, "_resolve_config", lambda _arg: cfg, raising=False)
    monkeypatch.setattr("cmru.config.resolve_invocation_context", lambda *_args, **_kwargs: SimpleNamespace(project_name=None, scope="estate"))
    monkeypatch.setattr(getpy, "render_from_config", lambda name, _path: "no-newline")
    getpy.getpy_main(["all"])
    assert "Project: DEMO" in capsys.readouterr().out
    monkeypatch.setattr(getpy, "render_from_config", lambda name, _path: "with-newline\n")
    getpy.getpy_main(["all"])
    assert "Project: DEMO" in capsys.readouterr().out


def test_getpy_render_refuses_unknown_and_non_installer(tmp_path, monkeypatch):
    cfg = tmp_path / "cmru.toml"
    cfg.write_text(_minimal_project(), encoding="utf-8")
    # The renderer's behavior is independent of repository-fact parsing; use a
    # loaded fixture so both project-level refusals are reached directly.
    from cmru import config as config_module
    monkeypatch.setattr(config_module, "load_forge_config", lambda _path: SimpleNamespace(
        projects={"demo": SimpleNamespace(installer=None)},
    ))
    with pytest.raises(ValueError, match="not found"):
        getpy.render_from_config("missing", cfg)
    with pytest.raises(ValueError, match="no .*installer"):
        getpy.render_from_config("demo", cfg)


def test_resolve_context_and_multi_formats(monkeypatch, tmp_path, capsys):
    project = SimpleNamespace(prefix="demo-v", github_token="")
    loaded = _loaded({"demo": project, "other": SimpleNamespace(prefix="other-v", github_token="")}, ["demo", "other"])
    cfg = tmp_path / "cmru.toml"
    monkeypatch.setattr(resolve_module, "resolve", lambda *_args, **_kwargs: {"version": "1", "tag": "demo-v1", "url": "https://x"})
    monkeypatch.setattr("cmru.cli._resolve_config", lambda _arg: cfg)
    monkeypatch.setattr("cmru.cli.load_config", lambda _path: loaded)
    monkeypatch.setattr("cmru.config.resolve_invocation_context", lambda *_args, **_kwargs: SimpleNamespace(project_name=None, scope="estate"))
    monkeypatch.setattr("cmru.hosts.github.GitHubReleaseHost", lambda **_kwargs: object())
    resolve_module.resolve_main(["all", "--format", "env"])
    assert "# Project: demo" in capsys.readouterr().out
    resolve_module.resolve_main(["all", "--format", "url"])
    assert "Resolve Project" in capsys.readouterr().out
    resolve_module.resolve_main(["all", "--format", "json"])
    assert '"demo"' in capsys.readouterr().out


def test_resolve_standalone_implicit_target(monkeypatch, tmp_path, capsys):
    cfg = tmp_path / "cmru.toml"
    project = SimpleNamespace(prefix="demo-v", github_token="")
    monkeypatch.setattr("cmru.cli._resolve_config", lambda _arg: cfg)
    monkeypatch.setattr("cmru.cli.load_config", lambda _path: _loaded({"demo": project}))
    monkeypatch.setattr(resolve_module, "resolve", lambda *_args, **_kwargs: {"version": "1", "url": "https://x"})
    monkeypatch.setattr("cmru.hosts.github.GitHubReleaseHost", lambda **_kwargs: object())
    resolve_module.resolve_main([])
    assert '"version": "1"' in capsys.readouterr().out


def test_resolve_orchestration_context_without_explicit_target(monkeypatch, tmp_path, capsys):
    cfg = tmp_path / "cmru.orchestration.toml"
    project = SimpleNamespace(prefix="demo-v", github_token="")
    monkeypatch.setattr("cmru.cli._resolve_config", lambda _arg: cfg)
    monkeypatch.setattr("cmru.cli.load_config", lambda _path: _loaded({"demo": project}))
    monkeypatch.setattr("cmru.config.resolve_invocation_context", lambda *_args, **_kwargs: SimpleNamespace(project_name=None, scope="estate"))
    monkeypatch.setattr(resolve_module, "resolve", lambda *_args, **_kwargs: {"version": "1", "url": "https://x"})
    monkeypatch.setattr("cmru.hosts.github.GitHubReleaseHost", lambda **_kwargs: object())
    resolve_module.resolve_main([])
    assert '"version": "1"' in capsys.readouterr().out


def test_runner_context_branches_and_exactly_one_guard(monkeypatch, tmp_path):
    cfg = tmp_path / "cmru.toml"
    one = _loaded({"demo": SimpleNamespace(project_root=tmp_path)})
    monkeypatch.setattr("cmru.cli._resolve_config", lambda _arg: cfg)
    monkeypatch.setattr("cmru.cli.load_config", lambda _path: one)
    monkeypatch.setattr("cmru.config.resolve_invocation_context", lambda *_args, **_kwargs: SimpleNamespace(project_name=None, scope="estate"))
    monkeypatch.setattr("cmru.config.load_forge_config", lambda _path: SimpleNamespace(orchestration=None))
    monkeypatch.setattr(runner, "run_step", lambda *_args: None)
    runner.main(["--step", "build"])
    many = _loaded({"demo": object(), "other": object()}, ["demo", "other"])
    monkeypatch.setattr("cmru.cli.load_config", lambda _path: many)
    with pytest.raises(SystemExit):
        runner.main(["all", "--step", "build"])
    cfg = tmp_path / "cmru.orchestration.toml"
    monkeypatch.setattr("cmru.cli._resolve_config", lambda _arg: cfg)
    monkeypatch.setattr("cmru.cli.load_config", lambda _path: one)
    monkeypatch.setattr("cmru.config.resolve_invocation_context", lambda *_args, **_kwargs: SimpleNamespace(project_name=None, scope="estate"))
    runner.main(["--step", "build"])
    with pytest.raises(SystemExit):
        runner.main(["missing", "--step", "build"])


def test_standards_reports_explicit_tester_resources():
    env = {key: "set" for key in standards.REQUIRED_TESTER_ENV}
    command = SimpleNamespace(argv=["python", "tester-gate"])
    project = SimpleNamespace(
        template_revision=4, changelog="CHANGES.md", steps={"run-tests": [command]},
        runner_steps={"run-tests": SimpleNamespace(quiet=True)}, env=env,
    )
    result = standards.assess_projects(Path("."), {"demo": project}, ["demo"], ["demo"])[0]
    assert "explicit tester resources and host probe image" in result.messages


def test_standards_contextual_target_paths(monkeypatch, tmp_path):
    project = SimpleNamespace(template_revision=4, changelog="CHANGES.md", steps={}, runner_steps={}, env={})
    loaded = _loaded({"demo": project})
    cfg = tmp_path / "cmru.toml"
    monkeypatch.setattr("cmru.cli._resolve_config", lambda _arg: cfg)
    monkeypatch.setattr("cmru.cli.load_config", lambda _path: loaded)
    monkeypatch.setattr(standards, "assess_projects", lambda *args: [])
    standards.standards_main([])
    orch = tmp_path / "cmru.orchestration.toml"
    monkeypatch.setattr(standards, "resolve_invocation_context", lambda *_args, **_kwargs: SimpleNamespace(project_name=None, scope="estate"), raising=False)
    monkeypatch.setattr("cmru.config.resolve_invocation_context", lambda *_args, **_kwargs: SimpleNamespace(project_name=None, scope="estate"))
    monkeypatch.setattr("cmru.cli._resolve_config", lambda _arg: orch)
    standards.standards_main([])


def test_scaffold_validation_edges(monkeypatch, tmp_path):
    with pytest.raises(SystemExit):
        scaffold._yes_no("maybe", "answer")
    with pytest.raises(SystemExit):
        scaffold._artifact_selection("wheel,,bundle")
    with pytest.raises(SystemExit):
        scaffold._artifact_selection("wheel,wheel")
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("git unavailable")))
    assert scaffold._git_owner_repo(tmp_path) == ("", "")
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: SimpleNamespace(stdout="git@github.com:acme/vbpub.git\n"))
    assert scaffold._git_owner_repo(tmp_path) == ("acme", "vbpub")
    with pytest.raises(SystemExit):
        scaffold._ask_project(tmp_path, False)
    answers = iter(["bad id", "Description", "python"])
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(answers))
    with pytest.raises(SystemExit):
        scaffold._ask_project(tmp_path, True)
    answers = iter(["demo", "Demo", "python", "generic", "wheel", "yes", "build", "publish"])
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(answers))
    assert scaffold._ask_project(tmp_path, True)["artifacts"] == ["wheel"]
    monkeypatch.setattr(scaffold, "_artifact_selection", lambda _value: [])
    answers = iter(["demo", "Demo", "python", "wheel"])
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(answers))
    with pytest.raises(SystemExit):
        scaffold._ask_project(tmp_path, True)
    answers = iter(["demo", "Demo", "invalid"])
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(answers))
    with pytest.raises(SystemExit):
        scaffold._ask_project(tmp_path, True)


def test_scaffold_collect_plan_refuses_invalid_adoption_inputs(monkeypatch, tmp_path):
    answers = iter(["demo", "repo", "user", "single", "missing"])
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(answers))
    with pytest.raises(SystemExit):
        scaffold.collect_plan([], tmp_path)
    with pytest.raises(SystemExit):
        scaffold.collect_plan(["--root", str(tmp_path / "missing")], tmp_path)
    with pytest.raises(SystemExit):
        scaffold.collect_plan(["--owner", "", "--repo", "r", "--owner-type", "user", "--layout", "single"], tmp_path)
    with pytest.raises(SystemExit):
        scaffold.collect_plan(["--owner", "o", "--repo", "", "--owner-type", "user", "--layout", "single"], tmp_path)
    with pytest.raises(SystemExit):
        scaffold.collect_plan(["--owner", "o", "--repo", "r", "--owner-type", "bad", "--layout", "single"], tmp_path)
    with pytest.raises(SystemExit):
        scaffold.collect_plan(["--owner", "o", "--repo", "r", "--owner-type", "user", "--layout", "bad"], tmp_path)


def test_scaffold_monorepo_path_and_standards_failure(monkeypatch, tmp_path):
    monkeypatch.setenv("CGROUP_PARENT_DEV_GATES", "dev-gates.slice")
    (tmp_path / "child").mkdir()
    answers = iter(["child", "child", "Child", "python", "wheel", "yes"])
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(answers))
    plan = scaffold.collect_plan(["--owner", "o", "--repo", "r", "--owner-type", "user", "--layout", "2"], tmp_path)
    files = scaffold.build_files(plan, tmp_path)
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=1, stdout="bad", stderr="standards"))
    with pytest.raises(SystemExit):
        scaffold.validate(files, tmp_path)

    outside = tmp_path.parent / "outside-project"
    outside.mkdir(exist_ok=True)
    answers = iter([str(outside), "x", "X", "python", "wheel", "yes"])
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(answers))
    with pytest.raises(SystemExit):
        scaffold.collect_plan(["--owner", "o", "--repo", "r", "--owner-type", "user", "--layout", "2"], tmp_path)

    answers = iter(["child", "child", "Child", "python", "wheel", "yes"])
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(answers))
    scaffold.collect_plan(["--owner", "o", "--repo", "r", "--owner-type", "user", "--layout=2"], tmp_path)
    answers = iter(["missing"])
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(answers))
    with pytest.raises(SystemExit):
        scaffold.collect_plan(["--owner", "o", "--repo", "r", "--owner-type", "user", "--layout", "2"], tmp_path)
    answers = iter([str(tmp_path), "demo", "Demo", "python", "wheel", "yes"])
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(answers))
    plan = scaffold.collect_plan(["--owner", "o", "--repo", "r", "--owner-type", "user", "--layout", "2"], tmp_path)
    assert plan["projects"][0]["config"] == "cmru.toml"

    # Directly exercise both optional command substitutions in the renderer.
    scaffold.render_project_toml(
        project_id="demo", description="d", owner="o", repo="r", owner_type="user",
        generated_by="test", centralized=True, build_argv=["make", "build"],
    )
    scaffold.render_project_toml(
        project_id="demo", description="d", owner="o", repo="r", owner_type="user",
        generated_by="test", centralized=False, push_argv=["make", "publish"],
    )
    scaffold.render_project_toml(
        project_id="demo", description="d", owner="o", repo="r", owner_type="user",
        generated_by="test", centralized=True,
    )


def test_tester_gate_forwards_gate_slice(monkeypatch):
    argv = tester_gate.build_docker_command(
        repo_root=Path("/repo"), relative_cwd=".", memory="1g", memory_swap="2g",
        cpus="1", image="tester", command=["true"],
        cgroup_parent_dev_gates="dev-gates.slice",
    )
    assert "CGROUP_PARENT_DEV_GATES=dev-gates.slice" in argv


def test_tool_deps_implicit_standalone_context(monkeypatch, tmp_path):
    project = SimpleNamespace(tool_dependencies=[], project_root=tmp_path)
    loaded = _loaded({"demo": project})
    cfg = tmp_path / "cmru.toml"
    monkeypatch.setattr("cmru.cli._resolve_config", lambda _arg: cfg)
    monkeypatch.setattr("cmru.cli.load_config", lambda _path: loaded)
    monkeypatch.setattr("cmru.config.resolve_invocation_context", lambda *_args, **_kwargs: SimpleNamespace(project_name=None, scope="estate"))
    # No dependencies means the command has a valid empty report.
    tool_deps.tool_deps_main([])
