"""W2-PKG1: root ``cmru`` CLI adoption and redesign contracts.

Each test pins one observable effect of the redesign (CLI-REDESIGN B1-B7, the
section C exception narrowing, the section E exit taxonomy, D2/D3/D10) so that
reverting the corresponding change fails it.
"""
from __future__ import annotations

import os
from types import SimpleNamespace

import pytest
from cli_extended import CliFailure

from cmru import cli, exit_codes, manifest, transaction


def _loaded(tmp_path, names=("alpha", "beta")):
    projects = {
        name: cli.ProjectConfig(
            name, {}, {}, prefix=f"{name}-v", github_token="t", project_root=tmp_path / name,
        )
        for name in names
    }
    return (
        tmp_path, projects, list(names), list(names), [], "project-first", {},
        cli.CleanupConfig([], [], [], []), cli.GitHubConfig("o", "r", "t", "user"),
        cli.ReleaseEnvConfig({}, None),
    )


@pytest.fixture
def estate(monkeypatch, tmp_path):
    loaded = _loaded(tmp_path)
    monkeypatch.setattr(cli, "_resolve_config", lambda _path: tmp_path / "cmru.orchestration.toml")
    monkeypatch.setattr(cli, "load_config", lambda _path: loaded)
    monkeypatch.setattr(cli, "apply_release_env", lambda *_a: None)
    monkeypatch.setattr(transaction, "is_transaction_child", lambda *_a, **_k: False)
    return loaded


# --- B13 / E: the shared library selector ------------------------------------


@pytest.mark.parametrize("target", ["a,,b", "alpha,alpha", "all,alpha"])
def test_malformed_targets_are_argparse_usage_errors_exit_2(target, capsys):
    assert cli.main(["status", target]) == 2
    assert "[ERROR]" in capsys.readouterr().err


# --- B2: status --json --------------------------------------------------------


def test_status_json_is_one_record_list_with_the_stable_keys(monkeypatch, tmp_path):
    alpha = SimpleNamespace(name="alpha", prefix="alpha-v", version=None, git_tag=True)
    external = SimpleNamespace(
        name="ext", prefix="ext-v", git_tag=True,
        version=SimpleNamespace(strategy="external:VERSION"),
    )
    notag = SimpleNamespace(name="raw", prefix="raw-v", version=None, git_tag=False)
    configs = {"alpha": alpha, "ext": external, "raw": notag}
    monkeypatch.setattr(
        "cmru.version.detect_changed_projects",
        lambda _root, projects, end_ref: [
            ("alpha", alpha, "alpha-v1.0.0", "patch"),
            ("ext", external, None, "patch"),
            ("raw", notag, None, "patch"),
        ],
    )
    monkeypatch.setattr(
        transaction, "project_git_family_groups", lambda _root, members: {tmp_path: members},
    )
    monkeypatch.setattr(cli, "_configs_for_git_family", lambda configs, _names, _root: configs)
    emitted = []
    runtime = SimpleNamespace(output=SimpleNamespace(primary=emitted.append))
    vargs = SimpleNamespace(json=True, minor=False, major=False, set_version=None, ref=None)

    cli._status(vargs, runtime, tmp_path, configs, ["alpha", "ext", "raw"])

    assert emitted == [[
        {"project": "alpha", "changed": True, "last_tag": "alpha-v1.0.0", "bump": "patch",
         "next_version": "alpha-v1.0.1", "note": None},
        {"project": "ext", "changed": True, "last_tag": None, "bump": "prepare",
         "next_version": None, "note": "(derived by VERSION)"},
        {"project": "raw", "changed": True, "last_tag": None, "bump": "no-tag",
         "next_version": None, "note": "(project-owned publication, no git tag)"},
    ]]


def test_status_records_list_every_selected_project_with_a_changed_field(monkeypatch, tmp_path):
    from cmru import version

    alpha = SimpleNamespace(name="alpha", prefix="alpha-v", version=None, git_tag=True)
    quiet = SimpleNamespace(name="quiet", prefix=None, version=None, git_tag=True)
    monkeypatch.setattr(
        version, "detect_changed_projects",
        lambda _root, projects, end_ref: [("alpha", alpha, "alpha-v1.0.0", "patch")],
    )
    monkeypatch.setattr(
        version, "_latest_tag_for_prefix",
        lambda _root, prefix, **_kw: {"quiet-v": "quiet-v2.0.0"}.get(prefix),
    )

    records = version.status_records(tmp_path, {"quiet": quiet, "alpha": alpha})

    assert records == [
        {"project": "quiet", "changed": False, "last_tag": "quiet-v2.0.0", "bump": None,
         "next_version": None, "note": None},
        {"project": "alpha", "changed": True, "last_tag": "alpha-v1.0.0", "bump": "patch",
         "next_version": "alpha-v1.0.1", "note": None},
    ]


def test_status_text_table_lists_only_changed_projects(monkeypatch, tmp_path, capsys):
    from cmru import version

    alpha = SimpleNamespace(name="alpha", prefix="alpha-v", version=None, git_tag=True)
    quiet = SimpleNamespace(name="quiet", prefix="quiet-v", version=None, git_tag=True)
    monkeypatch.setattr(
        version, "detect_changed_projects",
        lambda _root, projects, end_ref: [("alpha", alpha, "alpha-v1.0.0", "patch")],
    )
    monkeypatch.setattr(version, "_latest_tag_for_prefix", lambda *_a, **_k: "quiet-v2.0.0")

    version.status_cmd(tmp_path, {"alpha": alpha, "quiet": quiet})

    out = capsys.readouterr().out
    assert "alpha-v1.0.1" in out and "quiet" not in out


def test_value_taking_flags_knows_release_and_is_empty_for_an_unknown_verb():
    assert "--set-version" in cli._value_taking_flags("release")
    assert "--dry-run" not in cli._value_taking_flags("release")
    assert cli._value_taking_flags("no-such-verb") == frozenset()


# --- B3: release ---------------------------------------------------------------


def test_release_ref_is_a_deprecated_alias_of_ahead_check_ref(capsys):
    vargs = SimpleNamespace(ref="origin/main", ahead_check_ref=None)
    cli._resolve_ahead_check_ref(vargs)
    assert vargs.ahead_check_ref == "origin/main"
    assert "release --ref is deprecated; use --ahead-check-ref" in capsys.readouterr().out

    untouched = SimpleNamespace(ref=None, ahead_check_ref="x")
    cli._resolve_ahead_check_ref(untouched)
    assert untouched.ahead_check_ref == "x"

    with pytest.raises(CliFailure, match="pass only one") as refusal:
        cli._resolve_ahead_check_ref(SimpleNamespace(ref="a", ahead_check_ref="b"))
    assert refusal.value.exit_code == 2


def test_set_version_needs_exactly_one_project_for_release_and_status(estate, capsys):
    for verb in ("release", "status"):
        assert cli.main([verb, "all", "--set-version", "1.0.0", "--config", "x"]) == 2
        assert "--set-version needs exactly one selected project" in capsys.readouterr().err


def test_discard_takes_only_the_three_kinds(capsys):
    assert cli.main(["release", "--discard", "everything"]) == 2
    assert "invalid choice" in capsys.readouterr().err


def _child_vargs(**overrides):
    values = dict(
        dry_run=False, allow_tag_ahead_of_head=False, allow_stale_tool_deps=False,
        minor=False, major=False, set_version=None, no_build=False,
    )
    values.update(overrides)
    return SimpleNamespace(**values)


@pytest.mark.parametrize("which", ["plan", "tool-deps"])
def test_a_refused_release_plan_exits_4_and_marks_the_plan(monkeypatch, tmp_path, estate, which):
    from cmru.version import ReleasePlanRefused

    marked = []
    monkeypatch.setattr(
        transaction, "mark_plan_refused", lambda _root, workspace: marked.append(workspace),
    )
    monkeypatch.setattr(cli, "_transaction_workspace_from_env", lambda _root: "workspace")

    def refuse(*_a, **_k):
        raise ReleasePlanRefused("refused by policy")

    if which == "plan":
        monkeypatch.setattr("cmru.version.detect_changed_projects", refuse)
    else:
        monkeypatch.setattr("cmru.version.detect_changed_projects", lambda *_a, **_k: [])
        monkeypatch.setattr(cli, "_check_release_tool_dependencies", refuse)
    _root, configs, order, *_rest, github_config, env_config = estate

    with pytest.raises(SystemExit) as exited:
        cli._release_child(
            _child_vargs(), tmp_path, configs, configs, order, ["alpha"],
            github_config, env_config, None,
        )

    assert exited.value.code == exit_codes.REFUSED == 4
    assert marked == ["workspace"]


def test_uncommitted_paths_refuse_with_exit_4(monkeypatch, tmp_path, estate, capsys):
    from contextlib import nullcontext

    monkeypatch.setattr(transaction, "release_lock", lambda _root: nullcontext())
    monkeypatch.setattr(transaction, "source_git_root_for_projects", lambda root, _p: root)
    monkeypatch.setattr(cli, "_consume_release_snapshot_handoff", lambda *_a: None)
    monkeypatch.setattr(cli, "_dispatch_independent_git_families", lambda *_a, **_k: None)
    monkeypatch.setattr(cli, "_child_release_args", lambda *_a, **_k: [])
    monkeypatch.setattr(
        cli, "_uncommitted_release_paths", lambda *_a: {"alpha": ["alpha/file.txt"]},
    )
    _root, configs, order, *_rest = estate
    vargs = SimpleNamespace(
        dry_run=True, resume=None, target=None, allow_uncommitted=False,
    )
    # dry-run skips the guard, so make the guard apply for this check
    vargs.dry_run = False
    monkeypatch.setattr(
        cli, "_preflight_multi_family_release_tag_support", lambda *_a, **_k: None,
    )

    with pytest.raises(CliFailure, match="Uncommitted local changes") as refusal:
        cli._release_launcher(
            [], vargs, tmp_path / "cmru.toml", tmp_path, configs, configs, order,
            ["alpha"], None, None, None,
        )

    assert refusal.value.exit_code == exit_codes.REFUSED
    assert "alpha: uncommitted changes — alpha/file.txt" in capsys.readouterr().err


def test_release_while_the_release_lock_is_held_is_a_refusal_exit_4(monkeypatch, tmp_path, estate, capsys):
    from contextlib import contextmanager

    @contextmanager
    def held(_root):
        raise transaction.ReleaseLockHeld("Another cmru release transaction is already running.")
        yield

    monkeypatch.setattr(transaction, "release_lock", held)
    monkeypatch.setattr(transaction, "source_git_root_for_projects", lambda root, _p: root)
    monkeypatch.setattr(cli, "_consume_release_snapshot_handoff", lambda *_a: None)
    monkeypatch.setattr(cli, "_dispatch_independent_git_families", lambda *_a, **_k: None)
    monkeypatch.setattr(cli, "_child_release_args", lambda *_a, **_k: [])
    monkeypatch.setattr(
        cli, "_preflight_multi_family_release_tag_support", lambda *_a, **_k: None,
    )
    _root, configs, order, *_rest = estate
    vargs = SimpleNamespace(dry_run=False, resume=None, target=None, allow_uncommitted=False)

    with pytest.raises(SystemExit) as refused:
        cli._release_launcher(
            [], vargs, tmp_path / "cmru.toml", tmp_path, configs, configs, order,
            ["alpha"], None, None, None,
        )

    assert refused.value.code == exit_codes.REFUSED == 4
    assert "already running" in capsys.readouterr().err


def test_every_refusal_before_a_change_is_one_exception_type_mapped_to_exit_4():
    assert issubclass(transaction.ReleaseLockHeld, transaction.RefusedBeforeChange)
    assert issubclass(transaction.RefusedBeforeChange, RuntimeError)  # still a domain error


# --- B4: publish ---------------------------------------------------------------


def test_publish_requires_exactly_one_explicit_source(capsys):
    assert cli.main(["publish", "alpha"]) == 2
    err = capsys.readouterr().err
    assert "--build-output" in err and "--from-checkout" in err

    assert cli.main(["publish", "alpha", "--build-output", "x", "--from-checkout"]) == 2
    assert "not allowed with argument" in capsys.readouterr().err


# --- B5: cleanup ----------------------------------------------------------------


def test_cleanup_remove_assets_refuses_a_target_even_when_given_all(estate, capsys):
    assert cli.main(["cleanup", "all", "--remove-assets", "30d", "--dry-run"]) == 2
    assert "omit the target" in capsys.readouterr().err


# --- C: exception narrowing -----------------------------------------------------


def test_release_preflight_boundary_reports_domain_errors_only(monkeypatch, tmp_path, estate, capsys):
    _root, configs, order, *_rest = estate
    vargs = SimpleNamespace(dry_run=False, resume=None, target=None, allow_uncommitted=True)

    def launch():
        cli._release_launcher(
            [], vargs, tmp_path / "cmru.toml", tmp_path, configs, configs, order,
            ["alpha"], None, None, None,
        )

    monkeypatch.setattr(
        cli, "_preflight_multi_family_release_tag_support",
        lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("origin unreachable")),
    )
    with pytest.raises(SystemExit) as exited:
        launch()
    assert exited.value.code == 1
    assert "[ERROR] origin unreachable" in capsys.readouterr().err

    monkeypatch.setattr(
        cli, "_preflight_multi_family_release_tag_support",
        lambda *_a, **_k: (_ for _ in ()).throw(KeyError("typo_key")),
    )
    with pytest.raises(KeyError, match="typo_key"):  # a bug keeps its traceback (CLI-06)
        launch()


def test_abandon_inspection_translates_domain_errors_and_lets_bugs_through(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "_current_git_root", lambda: tmp_path)
    namespace = SimpleNamespace(branch=None, dry_run=True, yes=False, config=None)

    def failing(error):
        def listing(_root):
            raise error
        return listing

    from contextlib import nullcontext

    monkeypatch.setattr(transaction, "release_lock", lambda _root: nullcontext())
    monkeypatch.setattr(transaction, "list_cmru_workspaces", failing(OSError("disk")))
    with pytest.raises(CliFailure, match="cannot inspect CMRU release worktrees: disk") as refusal:
        cli._abandon(namespace, SimpleNamespace())
    assert refusal.value.exit_code == 2

    monkeypatch.setattr(transaction, "list_cmru_workspaces", failing(KeyError("bug")))
    with pytest.raises(KeyError):
        cli._abandon(namespace, SimpleNamespace())


def test_a_held_release_lock_is_a_policy_refusal_exit_4(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "_current_git_root", lambda: tmp_path)

    class Held:
        def __enter__(self):
            raise transaction.ReleaseLockHeld("Another cmru release transaction is already running.")

        def __exit__(self, *_exc):
            return False

    monkeypatch.setattr(transaction, "release_lock", lambda _root: Held())
    with pytest.raises(CliFailure) as refusal:
        cli._abandon(SimpleNamespace(branch=None, dry_run=True, yes=False, config=None), None)
    assert refusal.value.exit_code == exit_codes.REFUSED


# --- D2 / D3 / D10 --------------------------------------------------------------


def test_the_bound_launcher_reports_the_installed_metadata_version(tmp_path):
    import subprocess

    from cmru import cli_support

    launcher = cli._create_bound_cmru_launcher(tmp_path)  # verifies identity itself
    reported = subprocess.run(
        [str(launcher), "version"], capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert reported == f"cmru {cli_support.cmru_version()}"


def test_run_child_self_release_never_prefers_a_candidate_cli_extended(monkeypatch, tmp_path):
    candidate = tmp_path / "candidate"
    for relative in ("cmru/src/cmru/cli.py", "libraries/worktree/src", "libraries/cli-extended/src"):
        path = candidate / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.suffix:
            path.write_text("# candidate source\n", encoding="utf-8")
        else:
            path.mkdir(parents=True, exist_ok=True)
    observed = {}
    monkeypatch.setenv("PYTHONPATH", "/inherited/python/path")
    monkeypatch.setattr(
        transaction.subprocess, "run",
        lambda argv, *, cwd, env: observed.update(env=env) or SimpleNamespace(returncode=0),
    )
    workspace = SimpleNamespace(
        path=candidate, branch="cmru-release-test", base="a" * 40,
        workspace_id="test-id", repo_root=tmp_path,
    )

    assert transaction.run_child(workspace, ["cmru"], project_names=["cmru"]) == 0

    assert observed["env"]["PYTHONPATH"].split(os.pathsep) == [
        str(candidate / "cmru" / "src"),
        str(candidate / "libraries" / "worktree" / "src"),
        "/inherited/python/path",
    ]


def test_manifest_versions_come_from_the_wheel_names_not_the_installed_cmru(tmp_path, monkeypatch):
    monkeypatch.setenv("SOURCE_DATE_EPOCH", "1700000000")
    monkeypatch.setattr("importlib.metadata.version", lambda _name: "0.0.1")
    cmru_wheel = tmp_path / "cmru-3.2.1-py3-none-any.whl"
    ciu_wheel = tmp_path / "ciu-4.5.6-py3-none-any.whl"
    cmru_wheel.write_bytes(b"c")
    ciu_wheel.write_bytes(b"i")
    built = manifest.build_manifest(
        project="demo", tag="demo-v1", source_commit="abc", cmru_wheel=cmru_wheel,
        ciu_wheel=ciu_wheel, images=None, installer_schema_version=1,
        host_config_schema_version=1, platform={}, upgrade={},
    )
    assert (built["cmru"]["version"], built["ciu"]["version"]) == ("3.2.1", "4.5.6")
