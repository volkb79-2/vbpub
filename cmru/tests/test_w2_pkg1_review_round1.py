"""W2-PKG1 review fix round 1.

1. Every global the parent parsed is forwarded to a transaction child (CLI-19):
   ``cmru --dry-run release`` must NOT run a real release in the child.
2. An empty mode value never falls through to the destructive/unsafe default.
3. ``run --step`` selection is behaviourally pinned.
"""

from __future__ import annotations

import subprocess
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest

from cmru import cli, transaction


# --- 1. child argv carries the parent's parsed globals --------------------------


def _config(tmp_path, projects):
    return (
        tmp_path, {p.name: p for p in projects}, [p.name for p in projects],
        [p.name for p in projects], [p.name for p in projects], "project-first", {},
        cli.CleanupConfig([], [], [], []), cli.GitHubConfig("o", "r", "token", "user"),
        cli.ReleaseEnvConfig({}, None),
    )


def _common_patches(monkeypatch, tmp_path, projects):
    monkeypatch.setattr(cli, "_resolve_config", lambda _: tmp_path / "cmru.toml")
    monkeypatch.setattr(cli, "load_config", lambda _: _config(tmp_path, projects))
    monkeypatch.setattr(cli, "apply_release_env", lambda *_: None)
    monkeypatch.setattr(cli.transaction, "release_lock", lambda _: nullcontext())
    monkeypatch.setattr(cli, "_uncommitted_release_paths", lambda *args: {})
    monkeypatch.setattr(cli.transaction, "fetch_origin_main", lambda *_, **__: "b" * 40)
    monkeypatch.setattr(cli.transaction, "assert_local_main_not_ahead", lambda *_, **__: 0)
    monkeypatch.setattr(
        cli, "_project_config_paths_at_snapshot",
        lambda _root, _base, _config, _configs, names: {n: Path(n) / "cmru.toml" for n in names},
    )
    monkeypatch.setattr(cli, "require_project_publish_credentials", lambda *a, **k: None)
    monkeypatch.setattr(cli.transaction, "source_git_root_for_projects", lambda root, _p: root)
    monkeypatch.setattr(cli.transaction, "clear_plan_refused", lambda *a, **k: None)
    monkeypatch.setattr(cli, "_current_git_root", lambda: tmp_path)
    monkeypatch.setattr(cli, "_project_git_tag_policy_at_snapshot", lambda *a, **k: False)
    monkeypatch.setattr(cli, "_read_origin_tag_refs", lambda *a, **k: {})
    monkeypatch.setattr(cli.transaction, "write_release_tag_snapshot", lambda *a, **k: None)


def _single_family(monkeypatch, tmp_path):
    project = cli.ProjectConfig(
        "demo", {}, {}, project_root=tmp_path / "demo", prefix="demo-v",
        github_token="token", build_step="build",
    )
    _common_patches(monkeypatch, tmp_path, [project])
    monkeypatch.setattr(
        cli.transaction, "project_git_family_groups", lambda root, projects: {root: list(projects)},
    )
    workspace = transaction.ReleaseWorkspace(tmp_path, tmp_path / "child", "cmru/release/x", "b" * 40)
    monkeypatch.setattr(cli.transaction, "create_workspace", lambda *a, **k: workspace)
    monkeypatch.setattr(cli.transaction, "copy_secret_overlays", lambda *a, **k: None)
    monkeypatch.setattr(cli.transaction, "remove_backup_branch", lambda *a, **k: None)
    monkeypatch.setattr(cli.transaction, "remove_workspace", lambda *a, **k: None)
    monkeypatch.setattr(cli.transaction, "forget_release_scope", lambda *a, **k: None)
    monkeypatch.setattr(
        cli.transaction, "retain_successful_build_outputs",
        lambda *a: [tmp_path / "demo" / "artifacts" / "build-id"],
    )
    monkeypatch.setattr(
        cli.transaction, "_sync_local_main_result",
        lambda *a, **k: transaction._SyncLocalMainResult(True),
    )
    child = []
    monkeypatch.setattr(
        cli.transaction, "run_child", lambda ws, args, **kw: child.append(list(args)) or 0,
    )
    return child


def _two_families(monkeypatch, tmp_path):
    alpha = cli.ProjectConfig(
        "alpha", {}, {}, project_root=tmp_path / "a" / "alpha", prefix="alpha-v",
        github_token="token", build_step="build",
    )
    beta = cli.ProjectConfig(
        "beta", {}, {}, project_root=tmp_path / "b" / "beta", prefix="beta-v",
        github_token="token", build_step="build",
    )
    _common_patches(monkeypatch, tmp_path, [alpha, beta])
    monkeypatch.setattr(
        cli.transaction, "project_git_family_groups",
        lambda root, projects: {tmp_path / p.name: [p] for p in projects},
    )
    monkeypatch.setattr(cli, "_preflight_multi_family_release_tag_support", lambda *a, **k: None)
    spawned = []
    monkeypatch.setattr(
        subprocess, "run",
        lambda argv, **kw: spawned.append(list(argv)) or SimpleNamespace(returncode=0),
    )
    return spawned


_FLAGS = [
    pytest.param(["--dry-run"], ["--dry-run"], id="dry-run"),
    pytest.param(["--log-level", "debug"], ["--log-level", "debug"], id="log-level"),
    pytest.param(["--quiet"], ["--quiet"], id="quiet"),
    pytest.param(["--debug"], ["--debug"], id="debug"),
    pytest.param(["--no-color"], ["--no-color"], id="no-color"),
    pytest.param(["--log-prefix-time-short"], ["--log-prefix-time-short"], id="time-short"),
]


def _argv(verb, flags, position, tmp_path, target):
    base = [target, "--config", str(tmp_path / "cmru.toml")]
    if verb == "release":
        base += ["--discard", "logs", "--discard", "artifacts", "--discard", "evidence"]
    if position == "pre":
        return [*flags, verb, *base]
    return [verb, *base, *flags]


def _contains_once(child_args, expected):
    n = len(expected)
    hits = [i for i in range(len(child_args) - n + 1) if child_args[i:i + n] == expected]
    return len(hits) == 1


@pytest.mark.parametrize("family", ["single", "multi"])
@pytest.mark.parametrize("position", ["pre", "post"])
@pytest.mark.parametrize("verb", ["release", "build"])
@pytest.mark.parametrize("flags,expected", _FLAGS)
def test_every_child_spawning_path_forwards_each_parsed_global_once(
    monkeypatch, tmp_path, verb, position, family, flags, expected,
):
    if verb == "build" and flags == ["--dry-run"]:
        pytest.skip("a dry-run build never spawns a transaction child")
    if family == "single":
        child = _single_family(monkeypatch, tmp_path)
        target = "demo"
    else:
        spawned = _two_families(monkeypatch, tmp_path)
        target = "alpha,beta"

    rc = cli.main(_argv(verb, flags, position, tmp_path, target))

    assert rc == 0
    if family == "single":
        assert len(child) == 1, "the single-family child must be launched"
        argvs = child
    else:
        assert len(spawned) == 2, "one child per Git family"
        argvs = [a[a.index(verb) + 1:] for a in spawned]
    for child_args in argvs:
        assert _contains_once(child_args, expected), (child_args, expected)


def test_release_with_a_root_dry_run_is_a_dry_run_child(monkeypatch, tmp_path):
    """The reviewer's probe: ``cmru --dry-run release alpha``."""
    child = _single_family(monkeypatch, tmp_path)
    discard = ["--discard", "logs", "--discard", "artifacts", "--discard", "evidence"]
    assert cli.main(["--dry-run", "release", "demo", "--config", str(tmp_path / "cmru.toml"), *discard]) == 0
    assert child and "--dry-run" in child[0]
    child.clear()
    assert cli.main(["release", "demo", "--dry-run", "--config", str(tmp_path / "cmru.toml"), *discard]) == 0
    assert child and child[0].count("--dry-run") == 1


def test_a_plain_release_child_gets_no_invented_globals(monkeypatch, tmp_path):
    child = _single_family(monkeypatch, tmp_path)
    discard = ["--discard", "logs", "--discard", "artifacts", "--discard", "evidence"]
    assert cli.main(["release", "demo", "--config", str(tmp_path / "cmru.toml"), *discard]) == 0
    for flag in ("--dry-run", "--quiet", "--debug", "--log-level", "--no-color", "--color"):
        assert flag not in child[0]


def test_forwarding_never_doubles_a_flag_or_mixes_verbosity_families():
    parsed = SimpleNamespace(
        dry_run=True, log_level="debug", quiet=False, debug=False, debug_raw=True, color=True,
        log_prefix_time_short=True,
    )
    assert cli._forwarded_global_args(parsed, ["--dry-run", "--quiet", "--color"]) == [
        "--debug-raw", "--log-prefix-time-short",
    ]
    assert cli._forwarded_global_args(parsed, []) == [
        "--dry-run", "--log-level", "debug", "--debug-raw", "--color", "--log-prefix-time-short",
    ]
    assert cli._forwarded_global_args(None, []) == []
    assert cli._forwarded_global_args(SimpleNamespace(quiet=True), []) == ["--quiet"]
    assert cli._forwarded_global_args(SimpleNamespace(debug=True), []) == ["--debug"]


def test_no_other_code_rebuilds_a_command_from_argv():
    """Only the audited seams read the raw/command argv (grep guard)."""
    source = Path(cli.__file__).read_text(encoding="utf-8")
    assert source.count("list(runtime.command_argv)") == 1  # _dispatch: `rest`
    assert source.count("sys.argv") == 1  # main(): the console entry's own argv


# --- 2. empty mode values never reach the destructive default ---------------------


@pytest.fixture()
def cleanup_estate(monkeypatch, tmp_path):
    project = cli.ProjectConfig("demo", {}, {}, project_root=tmp_path / "demo", prefix="demo-v", github_token="token")
    _common_patches(monkeypatch, tmp_path, [project])
    reached = []
    monkeypatch.setattr(cli, "run_cleanup_verb", lambda *a, **k: reached.append("policy"))
    monkeypatch.setattr(cli, "remove_assets", lambda *a, **k: reached.append("assets"))
    return reached


@pytest.mark.parametrize("option", [
    "--remove-assets", "--delete-build-output", "--delete-unmanaged-release-tag",
])
@pytest.mark.parametrize("value", ["", "   "])
def test_an_empty_cleanup_mode_value_is_a_usage_error_never_the_policy_cleanup(
    cleanup_estate, capsys, option, value,
):
    argv = ["cleanup", option, value, "--yes"]
    rc = cli.main(argv)

    assert rc == 2
    assert cleanup_estate == []
    assert "must not be empty" in capsys.readouterr().err


def test_the_cleanup_dispatch_fails_closed_without_any_mode(cleanup_estate):
    """Defence in depth behind the parser: no mode set -> usage error, no policy run."""
    from cli_extended import CliFailure

    args = SimpleNamespace(
        verb="cleanup", config=None, target=None, dry_run=False, yes=True, policy=False,
        remove_assets=None, delete_build_output=None, delete_unmanaged_release_tag=None,
    )
    with pytest.raises(CliFailure) as caught:
        cli._dispatch(args, SimpleNamespace(command_argv=[], confirm=lambda *_: True))
    assert caught.value.exit_code == 2
    assert cleanup_estate == []


@pytest.mark.parametrize("mode", [
    "remove_assets", "delete_build_output", "delete_unmanaged_release_tag",
])
def test_an_empty_mode_that_slips_past_the_parser_never_reaches_the_policy_cleanup(
    cleanup_estate, mode,
):
    """Behind the parser: ``--remove-assets "$UNSET"`` must not become ``--policy``."""
    from cli_extended import CliFailure

    fields = dict(
        verb="cleanup", config=None, target=None, dry_run=False, yes=True, policy=False,
        remove_assets=None, delete_build_output=None, delete_unmanaged_release_tag=None,
    )
    fields[mode] = ""
    runtime = SimpleNamespace(command_argv=[], confirm=lambda *_: True)
    try:
        cli._dispatch(SimpleNamespace(**fields), runtime)
    except (CliFailure, ValueError, RuntimeError, OSError):
        pass  # a refusal is fine; what matters is that policy cleanup never ran
    assert "policy" not in cleanup_estate


def test_a_zero_like_but_set_mode_is_not_mistaken_for_unset(cleanup_estate, monkeypatch):
    """``is not None`` dispatch: ``--remove-assets 0`` runs assets cleanup, not the policy."""
    rc = cli.main(["cleanup", "--remove-assets", "0", "--yes"])
    assert rc == 0
    assert cleanup_estate == ["assets"]


def test_publish_with_an_empty_build_output_exits_2_and_never_publishes(monkeypatch, tmp_path, capsys):
    project = cli.ProjectConfig("demo", {}, {}, project_root=tmp_path / "demo", prefix="demo-v", github_token="token")
    _common_patches(monkeypatch, tmp_path, [project])
    ran = []
    monkeypatch.setattr(cli, "_run_project_steps", lambda *a, **k: ran.append(a))

    assert cli.main(["publish", "demo", "--build-output", ""]) == 2
    assert ran == []
    assert "must not be empty" in capsys.readouterr().err


def test_publish_dispatch_fails_closed_without_a_source(monkeypatch, tmp_path):
    from cli_extended import CliFailure

    project = cli.ProjectConfig("demo", {}, {}, project_root=tmp_path / "demo", prefix="demo-v", github_token="token")
    _common_patches(monkeypatch, tmp_path, [project])
    ran = []
    monkeypatch.setattr(cli, "_run_project_steps", lambda *a, **k: ran.append(a))
    args = SimpleNamespace(
        verb="publish", config=None, target="demo", dry_run=False, yes=False,
        build_output=None, from_checkout=False,
    )
    with pytest.raises(CliFailure) as caught:
        cli._dispatch(args, SimpleNamespace(command_argv=[]))
    assert caught.value.exit_code == 2
    assert "needs a source" in caught.value.message if hasattr(caught.value, "message") else True
    assert ran == []


def test_an_empty_id_that_slips_past_the_parser_still_fails_validation(monkeypatch, tmp_path):
    from cli_extended import CliFailure

    project = cli.ProjectConfig("demo", {}, {}, project_root=tmp_path / "demo", prefix="demo-v", github_token="token")
    _common_patches(monkeypatch, tmp_path, [project])
    ran = []
    monkeypatch.setattr(cli, "_run_project_steps", lambda *a, **k: ran.append(a))
    args = SimpleNamespace(
        verb="publish", config=None, target="demo", dry_run=False, yes=False,
        build_output="", from_checkout=False,
    )
    with pytest.raises(CliFailure) as caught:
        cli._dispatch(args, SimpleNamespace(command_argv=[]))
    assert caught.value.exit_code == 2
    assert ran == []


# --- 3. run --step is honoured, in order, over default_steps -----------------------


def _run_estate(monkeypatch, tmp_path, default_steps):
    (tmp_path / "demo").mkdir()
    steps = {name: SimpleNamespace(command=["true"]) for name in ("build", "test", "lint")}
    project = cli.ProjectConfig(
        "demo", {}, steps, project_root=tmp_path / "demo", runner_steps=steps,
    )
    config = (
        tmp_path, {"demo": project}, ["demo"], ["demo"], default_steps, "project-first", {},
        cli.CleanupConfig([], [], [], []), cli.GitHubConfig("o", "r", "t", "u"),
        cli.ReleaseEnvConfig({}, None),
    )
    monkeypatch.setattr(cli, "_resolve_config", lambda _: tmp_path / "cmru.toml")
    monkeypatch.setattr(cli, "load_config", lambda _: config)
    monkeypatch.setattr(cli, "apply_release_env", lambda *_: None)
    monkeypatch.setattr(cli, "resolve_versions_from_git", lambda *_a, **_k: None)


def test_run_step_list_is_executed_in_the_given_order_and_overrides_default_steps(
    monkeypatch, tmp_path,
):
    _run_estate(monkeypatch, tmp_path, ["build"])
    executed = []
    monkeypatch.setattr(
        cli, "run_project_step",
        lambda project, step, *_a, **_k: executed.append((project.name, step)),
    )

    rc = cli.main(["run", "demo", "--step", "lint", "--step", "test", "--config", str(tmp_path / "cmru.toml")])

    assert rc == 0
    assert executed == [("demo", "lint"), ("demo", "test")]


def test_run_without_step_uses_the_configured_default_steps(monkeypatch, tmp_path):
    _run_estate(monkeypatch, tmp_path, ["build", "test"])
    executed = []
    monkeypatch.setattr(
        cli, "run_project_step",
        lambda project, step, *_a, **_k: executed.append((project.name, step)),
    )

    assert cli.main(["run", "demo", "--config", str(tmp_path / "cmru.toml")]) == 0
    assert executed == [("demo", "build"), ("demo", "test")]
