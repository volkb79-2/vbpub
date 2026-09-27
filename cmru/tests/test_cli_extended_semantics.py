"""Behavioral checks for cli-extended adapters and real mutation previews."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from cmru import bundle, changelog, cli, dependencies, getpy, handlers, runner
from cmru import scaffold, standards, tester_gate, tool_deps, transaction


def _step(name: str = "build") -> runner.StepConfig:
    return runner.StepConfig(
        name=name,
        commands=[{"label": "compile", "argv": ["python", "build.py"], "cwd": "src"}],
        bake_set_prefix="IMAGE_", bake_set_vars=["TAG"], no_cache_env="NO_CACHE",
        clean_dirs=["dist"], required_env=["BUILD_TOKEN"],
        login={"registry": "registry.example"}, step_env={"MODE": "release"},
        env_command=["./resolve-env"], quiet=True,
    )


def _loaded(root: Path, projects, *, mode="project-first", step_order=None):
    names = list(projects)
    return (
        root, projects, names, names, ["build"], mode, step_order or {},
        cli.CleanupConfig([], [], [], []), cli.GitHubConfig("owner", "repo", "token", "user"),
        cli.ReleaseEnvConfig({}, None),
    )


def test_run_dry_run_respects_step_first_project_order_and_rejects_bad_plans(
    monkeypatch, tmp_path, capsys,
):
    alpha = SimpleNamespace(
        name="alpha", cwd="alpha", project_root=tmp_path / "alpha", env={},
        build_metadata={}, runtime_kind="none", runner_steps={"build": _step()},
    )
    beta = SimpleNamespace(
        name="beta", cwd="beta", project_root=tmp_path / "beta", env={},
        build_metadata={}, runtime_kind="none", runner_steps={"build": _step()},
    )
    projects = {"alpha": alpha, "beta": beta}
    monkeypatch.setattr(cli, "_resolve_config", lambda _path: tmp_path / "cmru.orchestration.toml")
    monkeypatch.setattr(cli, "load_config", lambda _path: _loaded(
        tmp_path, projects, mode="step-first", step_order={"build": ["beta", "alpha"]},
    ))
    monkeypatch.setattr(cli, "run_project_step", lambda *_a, **_k: pytest.fail("dry-run executed a project command"))

    assert cli.main(["run", "alpha", "--dry-run"]) == 0
    output = capsys.readouterr().out
    assert "alpha:build" in output
    assert "beta:build" not in output
    assert "registry.example" in output
    assert "May append build arguments from environment variables: TAG" in output
    assert "May append --no-cache when NO_CACHE=1" in output
    assert "Sets step environment keys: MODE" in output

    monkeypatch.setattr(cli, "load_config", lambda _path: _loaded(
        tmp_path, projects, mode="step-first", step_order={"build": ["missing"]},
    ))
    with pytest.raises(ValueError, match="Unknown project in step_project_order"):
        cli.main(["run", "alpha", "--dry-run"])

    monkeypatch.setattr(cli, "load_config", lambda _path: _loaded(
        tmp_path, {"alpha": SimpleNamespace(**{**vars(alpha), "runner_steps": {}})},
        mode="step-first",
    ))
    with pytest.raises(RuntimeError, match="required declared step 'build' is absent"):
        cli.main(["run", "alpha", "--dry-run"])


def test_dependencies_write_dry_run_prints_diff_without_changing_config(
    monkeypatch, tmp_path, capsys,
):
    config = tmp_path / "cmru.orchestration.toml"
    original = "schema_version = 1\n[orchestration]\nproject_order = ['demo']\n"
    config.write_text(original, encoding="utf-8")
    forge = SimpleNamespace(
        repo_root=tmp_path,
        orchestration=SimpleNamespace(project_order=["demo"], dependencies={"demo": []}),
        projects={"demo": object()},
    )
    report = dependencies.DependencyReport(("demo",), {"demo": ()}, {}, (), ())
    monkeypatch.setattr(cli, "_resolve_config", lambda _path: config)
    monkeypatch.setattr(cli, "load_forge_config", lambda _path: forge)
    monkeypatch.setattr(cli, "build_report", lambda **_kwargs: report)

    assert cli.main(["dependencies", "--write", "--dry-run", "--config", str(config)]) == 0
    output = capsys.readouterr().out
    assert "(planned)" in output
    assert config.read_text(encoding="utf-8") == original

    monkeypatch.setattr(dependencies, "write_comment_block", lambda *_a, **_k: False)
    assert cli.main(["dependencies", "--write", "--dry-run", "--config", str(config)]) == 0
    assert "already matches" in capsys.readouterr().out
    assert cli.main(["dependencies", "--dry-run", "--config", str(config)]) == 2
    assert "requires --write" in capsys.readouterr().err


@pytest.mark.parametrize("verb, step", [("build", "build"), ("publish", "push")])
def test_build_and_publish_dry_runs_share_the_declared_plan_without_credentials(
    monkeypatch, tmp_path, capsys, verb, step,
):
    project = SimpleNamespace(
        name="demo", cwd="demo", project_root=tmp_path / "demo", runner_steps={step: _step(step)},
    )
    projects = {"demo": project}
    monkeypatch.setattr(cli, "_resolve_config", lambda _path: tmp_path / "cmru.toml")
    monkeypatch.setattr(cli, "load_config", lambda _path: _loaded(tmp_path, projects))
    monkeypatch.setattr(cli, "_ordered_configs", lambda _configs, _order: [project])
    monkeypatch.setattr(cli, "_select_projects", lambda *_args: ["demo"])
    monkeypatch.setattr(cli, "apply_release_env", lambda *_args: None)
    monkeypatch.setattr(cli, "require_project_publish_credentials", lambda *_a: pytest.fail("dry-run required credentials"))
    monkeypatch.setattr(cli, "run_project_step", lambda *_a, **_k: pytest.fail("dry-run executed a step"))

    assert cli.main([verb, "demo", "--dry-run", "--config", "x"]) == 0
    output = capsys.readouterr().out
    assert f"demo:{step}: Would run declared step {step}" in output
    assert "argv=python build.py" in output

    project.runner_steps = {}
    with pytest.raises(RuntimeError, match=f"required declared step {step!r} is absent"):
        cli.main([verb, "demo", "--dry-run", "--config", "x"])


def test_changelog_backfill_dry_run_prints_exact_diff_and_writes_nothing(
    monkeypatch, tmp_path, capsys,
):
    project_root = tmp_path / "demo"
    project_root.mkdir()
    path = project_root / "CHANGES.md"
    original = "# Changelog\n\n<!-- cmru: release history -->\n"
    path.write_text(original, encoding="utf-8")
    project = SimpleNamespace(
        name="demo", cwd="demo", paths=["demo"], prefix="demo-v", changelog="CHANGES.md",
    )
    monkeypatch.setattr(
        changelog, "_git",
        lambda _root, *args: "a" * 40 if args[0] == "rev-parse" else "2026-09-25",
    )
    monkeypatch.setattr(changelog, "_previous_project_tag", lambda *_args: None)
    monkeypatch.setattr(changelog, "_subject_groups", lambda *_args, **_kwargs: {"Fixed": ["fix: item"]})
    monkeypatch.setattr(changelog, "_generated_exclusions", lambda *_args: [])

    assert changelog.backfill_release_changelog(tmp_path, project, "demo-v1.2.3", dry_run=True) is True
    output = capsys.readouterr().out
    assert "(planned)" in output and "backfilled-after-release" in output
    assert path.read_text(encoding="utf-8") == original

    project_config = tmp_path / "cmru.toml"
    monkeypatch.setattr(cli, "_resolve_config", lambda _path: project_config)
    monkeypatch.setattr(cli, "load_config", lambda _path: _loaded(tmp_path, {"demo": project}))
    calls = []
    monkeypatch.setattr(
        changelog, "backfill_release_changelog",
        lambda *args, **kwargs: calls.append((args, kwargs)) or True,
    )
    assert cli.main([
        "changelog", "demo", "--backfill-tag", "demo-v1.2.3",
        "--dry-run", "--config", str(project_config),
    ]) == 0
    assert calls[0][1]["dry_run"] is True
    assert "Would backfill CHANGES.md for demo-v1.2.3" in capsys.readouterr().out


def test_get_py_dry_run_reports_each_destination_without_creating_it(
    monkeypatch, tmp_path, capsys,
):
    config = tmp_path / "cmru.orchestration.toml"
    projects = {name: SimpleNamespace(name=name) for name in ("alpha", "beta")}
    monkeypatch.setattr(cli, "_resolve_config", lambda _path: config)
    monkeypatch.setattr(cli, "load_config", lambda _path: (tmp_path, projects, list(projects)))
    monkeypatch.setattr(getpy, "render_from_config", lambda name, _path: f"# {name}\n")

    output_dir = tmp_path / "generated"
    assert getpy.getpy_main(["alpha,beta", "--dry-run", "--output-dir", str(output_dir)]) == 0
    output = capsys.readouterr().out
    assert "alpha-get.py" in output and "beta-get.py" in output
    assert not output_dir.exists()

    output_file = tmp_path / "one.py"
    assert getpy.getpy_main(["alpha", "--dry-run", "--output", str(output_file)]) == 0
    assert f"Would write {output_file}" in capsys.readouterr().out
    assert not output_file.exists()

    assert getpy.getpy_main(["alpha", "--dry-run"]) == 0
    assert "Would render 1 installer(s) to stdout" in capsys.readouterr().out


def test_standards_dry_run_updates_only_the_marker_preview(monkeypatch, tmp_path, capsys):
    config = tmp_path / "cmru.orchestration.toml"
    project_root = tmp_path / "demo"
    project_root.mkdir()
    project_config = project_root / "cmru.toml"
    project_config.write_text("[project]\nid='demo'\ntemplate_revision=3\n", encoding="utf-8")
    project = SimpleNamespace(
        name="demo", project_root=project_root, template_revision=3, changelog="CHANGES.md",
        steps={"run-tests": []}, runner_steps={}, env={},
    )
    monkeypatch.setattr(cli, "_resolve_config", lambda _path: config)
    monkeypatch.setattr(cli, "load_config", lambda _path: (tmp_path, {"demo": project}, ["demo"]))

    assert standards.standards_main([
        "demo", "--config", str(config), "--update", "--dry-run",
    ]) == 2
    output = capsys.readouterr().out
    assert "template_revision = 4" in output
    assert "Marker updates were previewed" in output
    assert "template_revision=3" in project_config.read_text(encoding="utf-8")

    assert standards.standards_main(["demo", "--config", str(config), "--dry-run"]) == 2
    assert "standards --dry-run requires --update" in capsys.readouterr().err


def test_tool_dependency_refresh_dry_run_does_not_write_pin_files(monkeypatch, tmp_path, capsys):
    from cmru.config import ToolDependency

    project_root = tmp_path / "consumer"
    project_root.mkdir()
    config = project_root / "cmru.toml"
    config.write_text("[project]\nid='consumer'\n", encoding="utf-8")
    old = project_root / "tools" / "assay-1.0.0.pyz"
    old.parent.mkdir()
    old.write_bytes(b"old")
    dependency = ToolDependency(
        project="assay", version="1.0.0", path="tools/assay-1.0.0.pyz", sha256="0" * 64,
    )
    monkeypatch.setattr(tool_deps, "resolve_latest_release", lambda *_a, **_k: {
        "version": "1.1.0", "tag": "assay-v1.1.0",
        "assets": [{"name": "assay-1.1.0.pyz", "url": "https://example.invalid/assay.pyz"}],
    })
    monkeypatch.setattr(tool_deps, "_download_asset", lambda *_a, **_k: b"new artifact")
    monkeypatch.setattr(tool_deps, "_rewrite_tool_dependency_toml", lambda *_a: pytest.fail("dry-run rewrote config"))

    result = tool_deps.refresh_tool_dependency(
        owner="o", repo="r", provider_prefix="assay-v", dependency=dependency,
        project_root=project_root, config_path=config, dry_run=True,
    )
    assert result.version == "1.1.0"
    assert "Would write" in capsys.readouterr().out
    assert old.read_bytes() == b"old"
    assert not (project_root / "tools" / "assay-1.1.0.pyz").exists()
    assert cli.main(["tool-deps", "--dry-run"]) == 2
    assert "requires --refresh" in capsys.readouterr().err


def test_bundle_dry_run_validates_and_lists_operations_without_running_them(
    monkeypatch, tmp_path, capsys,
):
    root = tmp_path / "project"
    root.mkdir()
    (root / "README.md").write_text("docs\n", encoding="utf-8")
    (root / "assets").mkdir()
    (root / "dist").mkdir()
    config = bundle.BundleConfig(
        project_root=root, wheel_project_root=root / "client", dist_dir=root / "dist",
        bundle_dir=root / "dist" / "bundle", client_dir=root / "dist" / "client",
        wheel_enabled=True, wheel_python_bin="python3", wheel_find_links=root / "wheels",
        archive_template="demo-{version}.tar.xz", archive_version_env="VERSION",
        archive_format="xztar", copy_files=["README.md"], copy_dirs=["assets"],
    )
    monkeypatch.setattr(bundle, "parse_config", lambda _path: config)
    monkeypatch.setattr(bundle, "run_bundle", lambda *_: pytest.fail("bundle dry-run executed build"))
    monkeypatch.setenv("VERSION", "2.3.4")

    assert bundle.main(["--config", str(root / "bundle.toml"), "--dry-run"]) == 0
    output = capsys.readouterr().out
    assert "Would remove existing dist tree" in output
    assert "--find-links" in output
    assert "Would copy" in output and "Would copy tree" in output
    assert "demo-2.3.4.tar.xz" in output

    monkeypatch.delenv("VERSION")
    with pytest.raises(RuntimeError, match="VERSION must be set"):
        bundle.main(["--config", str(root / "bundle.toml"), "--dry-run"])
    monkeypatch.setenv("VERSION", "2.3.4")
    missing = bundle.BundleConfig(
        **{**vars(config), "copy_files": ["missing.txt"]},
    )
    monkeypatch.setattr(bundle, "parse_config", lambda _path: missing)
    with pytest.raises(FileNotFoundError, match="Bundle source not found"):
        bundle.main(["--config", str(root / "bundle.toml"), "--dry-run"])


@pytest.mark.parametrize("wheel_enabled", [False, True])
def test_bundle_dry_run_without_existing_outputs_or_optional_wheel_settings(
    monkeypatch, tmp_path, capsys, wheel_enabled,
):
    root = tmp_path / "project"
    root.mkdir()
    config = bundle.BundleConfig(
        project_root=root, wheel_project_root=root, dist_dir=root / "dist",
        bundle_dir=root / "dist" / "bundle", client_dir=root / "dist" / "client",
        wheel_enabled=wheel_enabled, wheel_python_bin="python3", wheel_find_links=None,
        archive_template="demo-{version}.tar.xz", archive_version_env="VERSION",
        archive_format="xztar", copy_files=[], copy_dirs=[],
    )
    monkeypatch.setattr(bundle, "parse_config", lambda _path: config)
    monkeypatch.setattr(bundle, "run_bundle", lambda *_: pytest.fail("bundle dry-run executed build"))
    monkeypatch.setenv("VERSION", "2.3.4")

    assert bundle.main(["--config", str(root / "bundle.toml"), "--dry-run"]) == 0
    output = capsys.readouterr().out
    assert "Would remove existing dist tree" not in output
    assert ("Would run python3 -m pip wheel" in output) is wheel_enabled
    assert "--find-links" not in output
    assert "Would create archive" in output
    assert not config.dist_dir.exists()


def test_project_handler_dry_run_uses_registered_cli_and_skips_handler(
    monkeypatch, tmp_path, capsys,
):
    monkeypatch.setattr(handlers, "cmd_wheel_build", lambda _args: pytest.fail("handler ran in dry-run"))
    assert handlers.main(["wheel-build", "--cwd", str(tmp_path), "--dry-run"]) == 0
    output = capsys.readouterr().out
    assert "Would run cmru handler wheel-build" in output
    assert str(tmp_path) in output

    assert handlers.main([
        "oci-image-build", "--cwd", str(tmp_path), "--bake-file", "bake.hcl",
        "--target", "image", "--repack", "--dry-run",
    ]) == 2
    assert "path is disabled" in capsys.readouterr().err


def test_init_dry_run_validates_generated_contract_without_writing(
    monkeypatch, tmp_path, capsys,
):
    generated = tmp_path / "cmru.toml"
    monkeypatch.setattr(scaffold, "collect_plan", lambda _options, _cwd: {"root": tmp_path})
    monkeypatch.setattr(scaffold, "build_files", lambda _plan, _root: [(generated, "valid")])
    monkeypatch.setattr(scaffold, "validate", lambda _files, _root: None)

    assert scaffold.init_main([
        "--root", str(tmp_path), "--layout", "single", "--owner", "o", "--repo", "r",
        "--owner-type", "user", "--dry-run",
    ]) == 0
    assert "Validated plan only" in capsys.readouterr().out
    assert not generated.exists()


def test_runner_step_dry_run_uses_shared_renderer_without_invoking_step(
    monkeypatch, tmp_path, capsys,
):
    project = SimpleNamespace(
        name="demo", project_root=tmp_path / "demo", runner_steps={"build": _step()},
    )
    monkeypatch.setattr(cli, "_resolve_config", lambda _path: tmp_path / "cmru.orchestration.toml")
    monkeypatch.setattr(cli, "load_config", lambda _path: (tmp_path, {"demo": project}, ["demo"]))
    monkeypatch.setattr(runner, "run_step", lambda *_: pytest.fail("dry-run invoked run_step"))

    assert runner.main(["demo", "--step", "build", "--dry-run", "--config", "x"]) == 0
    output = capsys.readouterr().out
    assert "Would remove" in output
    assert "Would resolve dynamic environment" in output
    assert "Would log in to configured registry" in output
    assert "Would run declared step build" in output

    assert runner.main(["demo", "--step", "missing", "--dry-run", "--config", "x"]) == 2
    assert "step 'missing' is not declared" in capsys.readouterr().err
    malformed = _step()
    malformed.commands[:] = [{}]
    with pytest.raises(ValueError, match="contains an invalid command"):
        runner.render_step_plan(malformed, tmp_path / "demo")


def test_runner_plan_omits_unconfigured_environment_details():
    step = runner.StepConfig(
        name="build", commands=[], bake_set_prefix=None, bake_set_vars=[],
        no_cache_env=None, clean_dirs=[], required_env=[], login=None,
        step_env={}, env_command=None,
    )

    plan = runner.render_step_plan(step, Path("/project"))

    assert plan == ["Would run declared step build from /project"]


@pytest.mark.parametrize(
    "prefix, variables",
    [("IMAGE_", []), (None, ["TAG"])],
)
def test_runner_plan_only_reports_bake_args_when_prefix_and_variables_are_set(
    prefix, variables,
):
    step = runner.StepConfig(
        name="build", commands=[], bake_set_prefix=prefix,
        bake_set_vars=variables, no_cache_env=None, clean_dirs=[],
        required_env=[], login=None, step_env={}, env_command=None,
    )

    assert not any(
        "May append build arguments" in line
        for line in runner.render_step_plan(step, Path("/project"))
    )


@pytest.mark.parametrize(
    "command",
    [None, "bad command", {"cwd": "."}, {"argv": ["make"]}],
)
def test_runner_plan_rejects_each_incomplete_command_shape(command):
    step = runner.StepConfig(
        name="build", commands=[command], bake_set_prefix=None,
        bake_set_vars=[], no_cache_env=None, clean_dirs=[], required_env=[],
        login=None, step_env={}, env_command=None,
    )

    with pytest.raises(ValueError, match="contains an invalid command"):
        runner.render_step_plan(step, Path("/project"))


def test_runner_plan_uses_fallback_label_for_an_empty_command_label():
    step = runner.StepConfig(
        name="build", commands=[{"label": "", "argv": ["make"], "cwd": "."}],
        bake_set_prefix=None, bake_set_vars=[], no_cache_env=None,
        clean_dirs=[], required_env=[], login=None, step_env={}, env_command=None,
    )

    plan = runner.render_step_plan(step, Path("/project"))

    assert any(line.startswith("command: cwd=/project; argv=make") for line in plan)


def test_cleanup_declined_confirmation_keeps_the_previewed_target_untouched(
    monkeypatch, tmp_path, capsys,
):
    from cli_extended import CliRuntime

    project = cli.ProjectConfig("demo", {}, {}, project_root=tmp_path / "demo")
    monkeypatch.setattr(cli, "_resolve_config", lambda _path: tmp_path / "cmru.toml")
    monkeypatch.setattr(cli, "load_config", lambda _path: _loaded(tmp_path, {"demo": project}))
    prompts = []
    monkeypatch.setattr(CliRuntime, "confirm", lambda _runtime, prompt: prompts.append(prompt) or False)
    calls = []
    monkeypatch.setattr(
        transaction, "delete_retained_build_output",
        lambda *_args, dry_run, **_kwargs: calls.append(dry_run) or [tmp_path / "artifact"],
    )

    result = cli.main([
        "cleanup", "demo", "--delete-build-output",
        "20240101T000000Z_" + "a" * 40, "--config", "x",
    ])
    assert result == 0
    assert calls == [True]
    capsys.readouterr()
    assert prompts == ["Apply the cleanup actions listed above?"]


@pytest.mark.parametrize("enable_docker", [False, True])
def test_tester_gate_dry_run_prints_docker_argv_without_host_probes_or_launch(
    monkeypatch, tmp_path, capsys, enable_docker,
):
    monkeypatch.setattr(tester_gate, "_missing_orchestration_env", lambda _args: [])
    monkeypatch.setattr(tester_gate, "check_slice_unit", lambda *_: (True, "loaded"))
    monkeypatch.setattr(tester_gate, "_resolve_worktree_context", lambda *_: (tmp_path, "cmru"))
    monkeypatch.setattr(tester_gate, "_probe_io_support", lambda *_: pytest.fail("dry-run probed privileged Docker IO"))
    monkeypatch.setattr(tester_gate, "dind_sidecar", lambda *_: pytest.fail("dry-run launched DinD"))
    calls = []
    monkeypatch.setattr(
        tester_gate, "build_docker_command",
        lambda *args, **kwargs: calls.append((args, kwargs)) or ["docker", "run", "tester", *args[2]],
    )
    if enable_docker:
        monkeypatch.setattr(tester_gate, "resolve_dind_image", lambda _value: "dind:test")

    argv = [
        "--cwd", ".", "--dry-run", "--image", "tester:test",
        "--cgroup-parent", "gates.slice", "--cgroup-probe-image", "debian:test",
        "--memory", "1g", "--memory-swap", "2g", "--cpus", "1",
    ]
    if enable_docker:
        argv += ["--enable-docker", "--dind-image", "dind:test"]
    argv += ["--device-read-iops", "/dev/sda:10", "--", "pytest", "-q"]
    assert tester_gate.main(argv) == 0
    output = capsys.readouterr().out
    assert "DRY RUN" in output and "docker run tester pytest -q" in output
    assert calls[0][1].get("sidecar_name") == ("cmru-dry-run-dind-sidecar" if enable_docker else None)


@pytest.mark.parametrize(
    "pushed, removed, remote_present, expected_error",
    [
        (True, False, False, "origin candidate ref is missing"),
        (False, False, True, "origin candidate ref exists without matching"),
        (True, True, True, "origin candidate ref exists without matching"),
    ],
)
def test_abandon_workspace_refuses_remote_state_that_disagrees_with_its_markers(
    monkeypatch, tmp_path, pushed, removed, remote_present, expected_error,
):
    workspace = SimpleNamespace(branch="cmru-release-candidate", context=None)
    stdout = (
        "a" * 40 + "\trefs/heads/" + workspace.branch + "\n"
        if remote_present else ""
    )
    monkeypatch.setattr(transaction, "backup_was_pushed", lambda *_: pushed)
    monkeypatch.setattr(transaction, "backup_was_removed", lambda *_: removed)
    monkeypatch.setattr(transaction.subprocess, "run", lambda argv, **_kw: SimpleNamespace(
        returncode=0, stdout=stdout, stderr="",
    ))
    monkeypatch.setattr(transaction, "remove_workspace", lambda *_: pytest.fail("refusal removed local state"))

    with pytest.raises(RuntimeError, match=expected_error):
        transaction.abandon_workspace(tmp_path, workspace)


def test_abandon_workspace_refuses_when_remote_state_cannot_be_read(monkeypatch, tmp_path):
    workspace = SimpleNamespace(branch="cmru-release-candidate", context=None)
    monkeypatch.setattr(transaction, "backup_was_pushed", lambda *_: False)
    monkeypatch.setattr(transaction, "backup_was_removed", lambda *_: False)
    monkeypatch.setattr(transaction, "remove_workspace", lambda *_: pytest.fail("removed local state"))
    monkeypatch.setattr(transaction.subprocess, "run", lambda *_a, **_k: SimpleNamespace(
        returncode=2, stdout="", stderr="offline",
    ))

    with pytest.raises(RuntimeError, match="cannot determine origin candidate state"):
        transaction.abandon_workspace(tmp_path, workspace)


@pytest.mark.parametrize("verification", ["deleted", "still-present", "unknown", "delete-failed"])
def test_abandon_workspace_deletes_remote_ref_then_verifies_before_local_cleanup(
    monkeypatch, tmp_path, verification,
):
    workspace = SimpleNamespace(branch="cmru-release-candidate", context=None)
    calls = []
    removed_local = []
    removed_marker = []
    forgotten = []

    def run(argv, **_kwargs):
        calls.append(argv)
        if argv[:3] == ["git", "ls-remote", "--heads"]:
            if len([call for call in calls if call[:3] == ["git", "ls-remote", "--heads"]]) == 1:
                return SimpleNamespace(
                    returncode=0,
                    stdout="a" * 40 + "\trefs/heads/" + workspace.branch + "\n",
                    stderr="",
                )
            if verification == "unknown":
                return SimpleNamespace(returncode=2, stdout="", stderr="offline")
            if verification == "still-present":
                return SimpleNamespace(
                    returncode=0,
                    stdout="a" * 40 + "\trefs/heads/" + workspace.branch + "\n",
                    stderr="",
                )
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if argv[:3] == ["git", "push", "origin"]:
            return SimpleNamespace(
                returncode=3 if verification == "delete-failed" else 0,
                stdout="", stderr="delete refused",
            )
        raise AssertionError(argv)

    monkeypatch.setattr(transaction.subprocess, "run", run)
    monkeypatch.setattr(transaction, "backup_was_pushed", lambda *_: True)
    monkeypatch.setattr(transaction, "backup_was_removed", lambda *_: False)
    monkeypatch.setattr(transaction, "mark_backup_removed", lambda *_: removed_marker.append(True))
    monkeypatch.setattr(transaction, "remove_workspace", lambda *_: removed_local.append(True))
    monkeypatch.setattr(transaction, "forget_release_scope", lambda *_: forgotten.append(True))

    if verification == "deleted":
        transaction.abandon_workspace(tmp_path, workspace)
        assert removed_marker == [True] and removed_local == [True] and forgotten == [True]
        assert calls[1][0:4] == ["git", "push", "origin", "--delete"]
    else:
        with pytest.raises(RuntimeError):
            transaction.abandon_workspace(tmp_path, workspace)
        assert removed_marker == [] and removed_local == [] and forgotten == []


def test_transaction_child_early_return_and_repository_root_mismatch(monkeypatch, tmp_path):
    monkeypatch.delenv(transaction.CHILD_ENV, raising=False)
    assert transaction.is_transaction_child(tmp_path) is False

    source, child = _minimal_transaction_context(monkeypatch, tmp_path)
    with pytest.raises(RuntimeError, match="does not match the loaded repository root"):
        transaction.is_transaction_child(source)


def _minimal_transaction_context(monkeypatch, tmp_path, *, record=None, common=None):
    source = tmp_path / "source"
    child = tmp_path / "child"
    source.mkdir(exist_ok=True)
    child.mkdir(exist_ok=True)
    common = common or (tmp_path / ".git")
    if record is None:
        record = SimpleNamespace(
            purpose="cmru-legacy", branch="cmru-release-child", worktree_path=child,
            source_git_root=source, workspace_id="workspace-1",
        )

    class Shared:
        def discover_git_context(self, path):
            if Path(path).resolve() == child.resolve():
                return child, common, "cmru-release-child", "a" * 40
            return source, common, "main", "b" * 40

        def list_git_worktrees(self, _path):
            return [SimpleNamespace(path=child, is_primary=False, branch="cmru-release-child")]

        def find_workspace(self, *_args):
            return record

    monkeypatch.setattr(transaction, "_shared_worktree", lambda: Shared())
    monkeypatch.setenv(transaction.CHILD_ENV, "1")
    monkeypatch.setenv("CMRU_WORKSPACE_PATH", str(child))
    monkeypatch.setenv("CMRU_SOURCE_GIT_ROOT", str(source))
    monkeypatch.setenv(transaction.BRANCH_ENV, "cmru-release-child")
    monkeypatch.setenv("CMRU_WORKSPACE_ID", "workspace-1")
    return source, child


def test_transaction_child_accepts_legacy_record_and_rejects_source_root_mismatch(
    monkeypatch, tmp_path,
):
    source, child = _minimal_transaction_context(monkeypatch, tmp_path)
    assert transaction.is_transaction_child(child) is True

    wrong = tmp_path / "other-source"
    wrong.mkdir()
    _minimal_transaction_context(
        monkeypatch, tmp_path, record=SimpleNamespace(
            purpose="cmru-legacy", branch="cmru-release-child",
            worktree_path=child, source_git_root=wrong, workspace_id="workspace-1",
        ),
    )
    with pytest.raises(RuntimeError, match="shared transaction record has a different source root"):
        transaction.is_transaction_child(child)


def test_agent_cli_dry_runs_and_reconciler_suppress_mutations(monkeypatch, capsys, caplog):
    from cmru.agent import cli as agent_cli
    from cmru.agent.reconciler import Reconciler

    monkeypatch.setattr(agent_cli, "_build_backend", lambda *_: pytest.fail("dry-run contacted the backend"))
    assert agent_cli.cmd_enroll(SimpleNamespace(
        node_id="node-a", landscape="test", token="", minisign_pubkey="",
        scope="user", consul_addr="http://consul", dry_run=True,
    )) == 0
    assert "Would enroll node_id=node-a" in capsys.readouterr().out

    monkeypatch.setenv("CMRU_NODE_ID", "node-from-env")
    monkeypatch.setenv("CMRU_LANDSCAPE", "landscape-from-env")
    monkeypatch.setenv("CONSUL_HTTP_ADDR", "http://consul-from-env")
    assert agent_cli.cmd_enroll(SimpleNamespace(
        node_id="", landscape="", token="", minisign_pubkey=None,
        scope="system", consul_addr=None, dry_run=True,
    )) == 0
    output = capsys.readouterr().out
    assert "node-from-env" in output and "landscape-from-env" in output
    assert "http://consul-from-env" in output

    monkeypatch.delenv("CMRU_LANDSCAPE")
    monkeypatch.setattr(agent_cli, "_load_identity", lambda _scope: ("node-a", {"landscape": "test", "public_key": ""}))
    assert agent_cli.cmd_run(SimpleNamespace(scope="user", dry_run=True, release_root=None)) == 0
    assert "Would start the long-running reconciler" in capsys.readouterr().out

    monkeypatch.setattr(agent_cli, "_load_identity", lambda _scope: ("node-a", {"landscape": "", "public_key": ""}))
    monkeypatch.setenv("CMRU_LANDSCAPE", "landscape-from-env")
    assert agent_cli.cmd_run(SimpleNamespace(scope="user", dry_run=True, release_root=None)) == 0
    assert "landscape-from-env" in capsys.readouterr().out

    backend = object()
    captured = {}
    class DryRunReconciler:
        def __init__(self, **kwargs):
            captured.update(kwargs)
        def once(self):
            return True
    monkeypatch.setattr(agent_cli, "_build_backend", lambda _args: backend)
    monkeypatch.setattr("cmru.agent.reconciler.Reconciler", DryRunReconciler)
    assert agent_cli.cmd_once(SimpleNamespace(scope="user", dry_run=True, release_root=None)) == 0
    assert captured["backend"] is backend and captured["dry_run"] is True
    assert "change would be applied" in capsys.readouterr().out
    class NoChangeReconciler(DryRunReconciler):
        def once(self):
            return False
    monkeypatch.setattr("cmru.agent.reconciler.Reconciler", NoChangeReconciler)
    assert agent_cli.cmd_once(SimpleNamespace(scope="user", dry_run=True, release_root=None)) == 0
    assert "no change" in capsys.readouterr().out

    class WatchBackend:
        def __init__(self, raw):
            self.raw = raw
            self.health = []
        def watch_desired(self, *_args, **_kwargs):
            return self.raw, 1
        def pass_health_check(self, node):
            self.health.append(node)
        def read_desired_sig(self, *_args):
            return None
        def acquire_lock(self, *_args):
            pytest.fail("dry-run acquired an apply lock")
        def publish_observed(self, *_args):
            pytest.fail("dry-run published state")

    empty_backend = WatchBackend(None)
    dry_reconciler = Reconciler(empty_backend, "node-a", "test", dry_run=True)
    assert dry_reconciler.once() is False
    assert empty_backend.health == []

    payload = (
        '{"schema_version":1,"generation":1,"action":"update",'
        '"release":{"tag":"demo-v1","manifest_url":"https://example.invalid/m.json",'
        '"manifest_sha256":"' + "a" * 64 + '"},"profiles":["core"],'
        '"config_hash":"cfg","plan_id":"p","step_id":"p.step"}'
    ).encode()
    apply_backend = WatchBackend(payload)
    monkeypatch.setattr("cmru.agent.reconciler.read_observed", lambda _scope: None)
    assert Reconciler(apply_backend, "node-a", "test", dry_run=True).once() is True
    assert apply_backend.health == []

    noop_backend = WatchBackend(payload)
    monkeypatch.setattr(Reconciler, "_is_noop", lambda *_: True)
    assert Reconciler(noop_backend, "node-a", "test", dry_run=True).once() is False
    assert noop_backend.health == []
    Reconciler(object(), "node-a", "test", dry_run=True)._publish_error("invalid", "bad")
    assert "Would publish error state" in caplog.text


def test_controller_generation_and_rollout_dry_run_boundaries(monkeypatch, tmp_path, caplog):
    caplog.set_level("INFO")
    from cmru.controller import cli as controller_cli
    from cmru.controller.planner import LandscapePlan, PlanStep
    from cmru.controller.rollout import RolloutEngine

    assert controller_cli._positive_generation("1") == 1
    with pytest.raises(ValueError, match="positive integer"):
        controller_cli._positive_generation("0")
    with pytest.raises(ValueError, match="generation_base must be a positive integer"):
        RolloutEngine(object(), "landscape", generation_base=0)

    steps = [
        PlanStep(
            plan_id="plan", wave_name="canary", phase=1, wave_type="canary",
            nodes=["node-a"], profiles=["core"], release_tag="demo-v1",
            manifest_url="https://example.invalid/manifest.json", manifest_sha256="a" * 64,
            config_hash="cfg", step_id="plan.phase-1.canary", required=True,
            requires_approval=True,
        ),
        PlanStep(
            plan_id="plan", wave_name="optional", phase=2, wave_type="dev",
            nodes=["node-b"], profiles=["worker"], release_tag="demo-v1",
            manifest_url="https://example.invalid/manifest.json", manifest_sha256="a" * 64,
            config_hash="cfg", step_id="plan.phase-2.optional", required=False,
            requires_approval=False,
        ),
    ]
    plan = LandscapePlan("plan", "landscape", steps)
    engine = RolloutEngine(object(), "landscape", generation_base=3, dry_run=True)
    engine.publish(plan)
    engine.approve("plan")
    engine.hold("plan")
    engine.release_hold("plan")
    engine._write_plan_status("plan", "complete", None)
    engine.rollback(plan, to_tag="demo-v0", generation=9)
    with pytest.raises(ValueError, match="rollback generation must be a positive integer"):
        engine.rollback(plan, generation=0)

    messages = caplog.text
    assert "requires approval before publishing" in messages
    assert "Would wait for wave canary health" in messages
    assert "Would write desired state gen=103" in messages
    assert "Would write desired state gen=203" in messages
    assert "Would approve plan" in messages and "Would hold plan" in messages
    assert "Would release hold" in messages and "Would write plan plan status=complete" in messages
    assert "tag=demo-v0" in messages and "digest=" + "a" * 64 in messages

    plan_path = tmp_path / "plan.toml"
    plan_path.write_text("placeholder", encoding="utf-8")
    from cmru.controller import planner as controller_planner
    monkeypatch.setattr(controller_planner, "load_plan", lambda _path: plan)
    seen = []
    class CliEngine:
        def publish(self, received):
            seen.append(received)
    monkeypatch.setattr(controller_cli, "_build_engine", lambda args, landscape: CliEngine())
    assert controller_cli.main([
        "publish", "--plan", str(plan_path), "--landscape", "landscape",
        "--generation-base", "3", "--dry-run",
    ]) == 0
    assert seen == [plan]
