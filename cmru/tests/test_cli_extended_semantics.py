"""Behavioral checks for cli-extended adapters and real mutation previews."""
from __future__ import annotations

import runpy
import warnings
from pathlib import Path
from types import SimpleNamespace

import pytest

from cmru import changelog, cli, dependencies, getpy, handlers, runner
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
    assert f"cwd={tmp_path / 'alpha' / 'src'}" in output

    monkeypatch.setattr(cli, "load_config", lambda _path: _loaded(
        tmp_path, {"alpha": SimpleNamespace(**{**vars(alpha), "runner_steps": {}})},
        mode="step-first",
    ))
    # CLI-09: an undeclared step is a usage error naming the declared ones.
    assert cli.main(["run", "alpha", "--dry-run"]) == 2
    refusal = capsys.readouterr().err
    assert "alpha: step(s) not declared: build; declared steps: (none)" in refusal

    project_without_cwd = SimpleNamespace(**{**vars(alpha), "cwd": None})
    monkeypatch.setattr(
        cli, "load_config",
        lambda _path: _loaded(tmp_path, {"alpha": project_without_cwd}),
    )
    with pytest.raises(RuntimeError, match="derived project working directory is absent"):
        cli.main(["run", "alpha", "--dry-run"])


@pytest.mark.parametrize("mode", ["project-first", "step-first"])
def test_run_with_no_configured_steps_performs_no_project_work(
    monkeypatch, tmp_path, mode,
):
    project = SimpleNamespace(name="alpha", env={}, runner_steps={})
    loaded = list(_loaded(tmp_path, {"alpha": project}, mode=mode))
    loaded[4] = []
    monkeypatch.setattr(cli, "_resolve_config", lambda _path: tmp_path / "cmru.toml")
    monkeypatch.setattr(cli, "load_config", lambda _path: tuple(loaded))
    monkeypatch.setattr(
        cli, "resolve_versions_from_git",
        lambda *_args: pytest.fail("empty run resolved project versions"),
    )
    monkeypatch.setattr(
        cli, "apply_project_release_env",
        lambda *_args: pytest.fail("empty run prepared a project environment"),
    )
    monkeypatch.setattr(
        cli, "run_project_step",
        lambda *_args: pytest.fail("empty run executed a project step"),
    )

    args = cli.build_arg_parser().parse_args([])
    cli._orchestrate(args)


def test_multi_project_single_git_family_uses_one_transaction_dispatch(
    monkeypatch, tmp_path,
):
    projects = {name: SimpleNamespace(name=name) for name in ("alpha", "beta")}
    monkeypatch.setattr(
        transaction, "project_git_family_groups",
        lambda _root, members: {tmp_path / "repo": list(members)},
    )
    monkeypatch.setattr(
        cli, "_child_release_args",
        lambda *_args, **_kwargs: pytest.fail("single-family dispatch spawned a child"),
    )

    assert cli._dispatch_independent_git_families(
        "build", [], tmp_path / "cmru.toml", tmp_path, projects,
        ["alpha", "beta"], original_target=None,
    ) is None


def test_dispatch_rejects_an_unregistered_verb_defensively(monkeypatch):
    diagnostics = []
    monkeypatch.setattr(cli, "write_config_diagnostic", diagnostics.append)

    with pytest.raises(SystemExit) as excinfo:
        cli._dispatch(
            SimpleNamespace(verb="not-registered"),
            SimpleNamespace(command_argv=[]),
        )

    assert excinfo.value.code == 2
    assert diagnostics and "Unknown verb 'not-registered'" in diagnostics[0]


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

    source = ["--from-checkout"] if verb == "publish" else []
    assert cli.main([verb, "demo", *source, "--dry-run", "--config", "x"]) == 0
    output = capsys.readouterr().out
    assert f"demo:{step}: Would run declared step {step}" in output
    assert "argv=python build.py" in output

    project.runner_steps = {}
    with pytest.raises(RuntimeError, match=f"required declared step {step!r} is absent"):
        cli.main([verb, "demo", *source, "--dry-run", "--config", "x"])


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
    assert f"template_revision = {standards.PROJECT_TEMPLATE_REVISION}" in output
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


@pytest.mark.parametrize(
    ("module", "message"),
    [
        ("cmru.bundle", "Python library, not a command"),
        ("cmru.runner", "Use the installed 'cmru run-step' command"),
        ("cmru.cli", "Use the installed 'cmru' command"),
    ],
)
def test_removed_module_cli_aliases_fail_with_the_canonical_interface(module, message):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        with pytest.raises(SystemExit) as error:
            runpy.run_module(module, run_name="__main__")
    assert message in str(error.value)


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
    assert "unrecognized arguments: --repack" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("handler_name", "argv"),
    [
        ("cmd_wheel_validate", ["wheel-validate", "--prefix", "demo"]),
        ("cmd_tarball_validate", ["tarball-validate", "--prefix", "demo"]),
    ],
)
def test_read_only_handlers_do_not_require_dry_run_argument(
    monkeypatch, handler_name, argv,
):
    called = []
    monkeypatch.setattr(
        handlers, handler_name,
        lambda args: called.append((args.prefix, getattr(args, "dry_run", None))) or 0,
    )

    assert handlers.main(argv) == 0
    assert called == [("demo", None)]


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

    assert runner.runner_cli().run(argv=["demo", "--step", "build", "--dry-run", "--config", "x"]) == 0
    output = capsys.readouterr().out
    assert "Would remove" in output
    assert "Would resolve dynamic environment" in output
    assert "Would log in to configured registry" in output
    assert "Would run declared step build" in output

    assert runner.runner_cli().run(argv=["demo", "--step", "missing", "--dry-run", "--config", "x"]) == 2
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
    monkeypatch.setattr(transaction, "retained_build_output_identity", lambda *_args: object())
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


def test_cleanup_applies_the_captured_preview_action_without_rediscovery(
    monkeypatch, tmp_path, capsys,
):
    project = cli.ProjectConfig("demo", {}, {}, project_root=tmp_path / "demo")
    monkeypatch.setattr(cli, "_resolve_config", lambda _path: tmp_path / "cmru.toml")
    monkeypatch.setattr(cli, "load_config", lambda _path: _loaded(tmp_path, {"demo": project}))
    expected_identity = object()
    monkeypatch.setattr(transaction, "retained_build_output_identity", lambda *_args: expected_identity)
    calls = []

    def delete_retained(_root, _project, _name, ident, *, dry_run, expected_identity):
        calls.append((ident, dry_run, expected_identity))
        return [tmp_path / "artifact"]

    monkeypatch.setattr(transaction, "delete_retained_build_output", delete_retained)

    result = cli.main([
        "cleanup", "demo", "--delete-build-output", "20240101T000000Z_" + "b" * 40,
        "--config", "x", "--yes",
    ])

    assert result == 0
    assert calls == [
        ("20240101T000000Z_" + "b" * 40, True, expected_identity),
        ("20240101T000000Z_" + "b" * 40, False, expected_identity),
    ]
    assert "Applying confirmed cleanup action" in capsys.readouterr().out


@pytest.mark.parametrize("enable_docker", [False, True])
def test_tester_gate_dry_run_prints_docker_argv_without_host_probes_or_launch(
    monkeypatch, tmp_path, capsys, enable_docker,
):
    monkeypatch.setattr(tester_gate, "_missing_orchestration_env", lambda _args: [])
    monkeypatch.setattr(
        tester_gate, "check_slice_unit",
        lambda *_: pytest.fail("dry-run ran a privileged host slice probe"),
    )
    monkeypatch.setattr(tester_gate, "_resolve_worktree_context", lambda *_: (tmp_path, "cmru"))
    monkeypatch.setattr(tester_gate, "_physical_path", lambda _path: tmp_path)
    monkeypatch.setattr(tester_gate, "_git_common_dir", lambda _path: None)
    monkeypatch.setattr(tester_gate, "_probe_io_support", lambda *_: pytest.fail("dry-run probed privileged Docker IO"))
    monkeypatch.setattr(tester_gate, "dind_sidecar", lambda *_: pytest.fail("dry-run launched DinD"))
    dind_image = "docker@sha256:" + "c" * 64
    argv = [
        "--cwd", ".", "--dry-run", "--image", "tester:test",
        "--cgroup-parent", "gates.slice", "--cgroup-probe-image", "debian@sha256:" + "d" * 64,
        "--memory", "1g", "--memory-swap", "2g", "--cpus", "1", "--pids-limit", "64",
    ]
    if enable_docker:
        argv += [
            "--enable-docker", "--dind-image", dind_image,
            "--dind-memory", "2g", "--dind-cpus", "1.5", "--dind-pids-limit", "128",
        ]
    argv += ["--device-read-iops", "/dev/sda:10", "--", "pytest", "-q"]
    assert tester_gate.main(argv) == 0
    output = capsys.readouterr().out
    assert "Host gates-slice verification skipped" in output
    assert "docker run --cgroup-parent=gates.slice --rm --init" in output
    assert "--pids-limit 64" in output
    assert "tester:test sh -c" in output and "pytest -q" in output
    assert ("docker run --cgroup-parent=gates.slice -d --rm --init --privileged --pull=never" in output) is enable_docker
    if enable_docker:
        assert dind_image in output and "cmru-dry-run-dind-sidecar" in output
        assert "--memory 2g --cpus 1.5 --pids-limit 128" in output


@pytest.mark.parametrize("cpus", ["0", "-1", "NaN", "Infinity", "0.0000099"])
def test_tester_gate_rejects_invalid_cpu_before_host_probe(monkeypatch, capsys, cpus):
    monkeypatch.setattr(tester_gate, "_missing_orchestration_env", lambda _args: [])
    monkeypatch.setattr(
        tester_gate,
        "check_slice_unit",
        lambda *_: pytest.fail("invalid CPU limit reached the privileged host probe"),
    )
    status = tester_gate.main([
        "--cwd", ".", "--image", "tester:test",
        "--cgroup-parent", "gates.slice", "--cgroup-probe-image", "debian:test",
        "--memory", "1g", "--memory-swap", "2g", "--cpus", cpus,
        "--", "true",
    ])
    assert status == 2
    assert "minimum 0.00001 CPUs" in capsys.readouterr().err


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


@pytest.mark.parametrize("changed", ["candidate", "tags"])
def test_abandon_workspace_rechecks_inspected_remote_facts_before_deleting(
    monkeypatch, tmp_path, changed,
):
    workspace = SimpleNamespace(branch="cmru-release-candidate", context=None)

    def run(argv, **_kwargs):
        if argv[:3] == ["git", "ls-remote", "--heads"]:
            oid = "b" * 40 if changed == "candidate" else "a" * 40
            return SimpleNamespace(
                returncode=0, stdout=oid + "\trefs/heads/" + workspace.branch + "\n", stderr="",
            )
        if argv[:3] == ["git", "ls-remote", "--tags"]:
            oid = "d" * 40 if changed == "tags" else "c" * 40
            return SimpleNamespace(
                returncode=0, stdout=oid + "\trefs/tags/demo-v1\n", stderr="",
            )
        if argv[:2] == ["git", "push"]:
            pytest.fail("changed remote facts must block deletion")
        raise AssertionError(argv)

    monkeypatch.setattr(transaction.subprocess, "run", run)
    monkeypatch.setattr(transaction, "backup_was_pushed", lambda *_: True)
    monkeypatch.setattr(transaction, "backup_was_removed", lambda *_: False)
    monkeypatch.setattr(transaction, "remove_workspace", lambda *_: pytest.fail("removed local state"))

    with pytest.raises(RuntimeError, match="changed after abandonment inspection"):
        transaction.abandon_workspace(
            tmp_path, workspace,
            expected_remote_candidate_oid="a" * 40,
            expected_remote_tag_refs={"refs/tags/demo-v1": "c" * 40},
        )


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
        if argv[:2] == ["git", "push"]:
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
        assert calls[1] == [
            "git", "push",
            f"--force-with-lease=refs/heads/{workspace.branch}:" + "a" * 40,
            "origin", f":refs/heads/{workspace.branch}",
        ]
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
            source_git_root=source, workspace_id="workspace-1", base_commit="a" * 40,
            metadata={
                transaction._LEGACY_RESUME_METADATA_KEY:
                    transaction._LEGACY_RESUME_METADATA_VALUE,
            },
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
    monkeypatch.setenv(transaction.BASE_ENV, "a" * 40)
    monkeypatch.setenv("CMRU_WORKSPACE_ID", "workspace-1")
    return source, child


def test_transaction_child_accepts_validated_legacy_record_and_rejects_source_root_mismatch(
    monkeypatch, tmp_path,
):
    source, child = _minimal_transaction_context(monkeypatch, tmp_path)
    monkeypatch.setattr(transaction, "read_release_progress", lambda *_args: "a" * 40)
    monkeypatch.setattr(
        transaction, "run_local_git",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=0, stdout="", stderr=""),
    )
    assert transaction.is_transaction_child(child) is True

    wrong = tmp_path / "other-source"
    wrong.mkdir()
    _minimal_transaction_context(
        monkeypatch, tmp_path, record=SimpleNamespace(
            purpose="cmru-legacy", branch="cmru-release-child",
            worktree_path=child, source_git_root=wrong, workspace_id="workspace-1",
            base_commit="a" * 40,
            metadata={
                transaction._LEGACY_RESUME_METADATA_KEY:
                    transaction._LEGACY_RESUME_METADATA_VALUE,
            },
        ),
    )
    with pytest.raises(RuntimeError, match="shared transaction record has a different source root"):
        transaction.is_transaction_child(child)
