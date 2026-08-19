"""S15: prove the CLI actually wires tool-dependency verification into the
release preflight -- not just that ``cmru.tool_deps`` supports it.

* the check runs in the SAME plan-computation phase as the tag preflight
  (S12.2a/S12.2b), scoped to exactly `release_names` -- the projects THIS run
  will actually release -- never the full orchestrated set;
* it is a complete no-op (never even called) when nothing changed;
* ``--allow-stale-tool-deps`` threads through to the check, and only that flag;
* a blocking finding raises ``ReleasePlanRefused``, which the surrounding
  handling (shared with the tag preflight, KI-12) turns into a clean refusal:
  ``mark_plan_refused`` + non-zero exit, no raw traceback;
* ``cmru tool-deps`` dispatches to ``cmru.tool_deps.tool_deps_main``.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from cmru import cli, tool_deps, transaction, version


def _tool_dep(project: str = "beta") -> SimpleNamespace:
    return SimpleNamespace(project=project, version="1.0.0", path="tools/x.pyz", sha256="a" * 64)


def _config(tmp_path, *, alpha_tool_deps=()):
    alpha = cli.ProjectConfig(
        "alpha", {}, {}, prefix="alpha-v", github_token="token", tool_dependencies=alpha_tool_deps,
    )
    beta = cli.ProjectConfig("beta", {}, {}, prefix="beta-v", github_token="token")
    return (
        tmp_path, {"alpha": alpha, "beta": beta}, ["alpha", "beta"], ["alpha", "beta"], [],
        "project-first", {}, cli.CleanupConfig([], [], [], []),
        cli.GitHubConfig("owner", "repo", "token", "user"), cli.ReleaseEnvConfig({}, None),
    )


def _run_release(monkeypatch, tmp_path, extra_args, *, changed, alpha_tool_deps=()):
    monkeypatch.setattr(cli, "_resolve_config", lambda _: tmp_path / "cmru.toml")
    monkeypatch.setattr(cli, "load_config", lambda _: _config(tmp_path, alpha_tool_deps=alpha_tool_deps))
    monkeypatch.setattr(cli, "apply_release_env", lambda *_: None)
    monkeypatch.setattr(version, "detect_changed_projects", lambda repo_root, projects, **kwargs: changed)
    monkeypatch.setattr(version, "release_cmd", lambda *a, **k: None)

    calls = []

    def fake_check(scoped, configs, *, github_config, allow_stale):
        calls.append((dict(scoped), set(configs), github_config, allow_stale))

    monkeypatch.setattr(cli, "_check_release_tool_dependencies", fake_check)
    cli.main(
        ["release", "--_transaction-child", "--dry-run", "--config", str(tmp_path / "cmru.toml")] + extra_args
    )
    return calls


def test_tool_dependency_check_is_never_called_when_nothing_changed(monkeypatch, tmp_path):
    calls = _run_release(monkeypatch, tmp_path, [], changed=[], alpha_tool_deps=(_tool_dep(),))
    assert calls == [({}, {"alpha", "beta"}, cli.GitHubConfig("owner", "repo", "token", "user"), False)]


def test_tool_dependency_check_is_scoped_to_exactly_what_will_release(monkeypatch, tmp_path):
    """alpha changed, beta did not -- the check must see only alpha, never beta,
    even though `configs` (used to resolve a PROVIDER project's own prefix) is
    the full estate."""
    calls = _run_release(
        monkeypatch, tmp_path, [], changed=[("alpha", None, None, "minor")], alpha_tool_deps=(_tool_dep(),),
    )
    assert len(calls) == 1
    scoped, all_names, github_config, allow_stale = calls[0]
    assert set(scoped) == {"alpha"}
    assert all_names == {"alpha", "beta"}
    assert github_config.owner == "owner"
    assert allow_stale is False


def test_allow_stale_tool_deps_flag_threads_through_and_nothing_else(monkeypatch, tmp_path):
    calls = _run_release(
        monkeypatch, tmp_path, ["--allow-stale-tool-deps"],
        changed=[("alpha", None, None, "minor")], alpha_tool_deps=(_tool_dep(),),
    )
    assert calls[0][3] is True


def test_a_blocking_tool_dependency_finding_refuses_the_release_cleanly(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(cli, "_resolve_config", lambda _: tmp_path / "cmru.toml")
    monkeypatch.setattr(cli, "load_config", lambda _: _config(tmp_path, alpha_tool_deps=(_tool_dep(),)))
    monkeypatch.setattr(cli, "apply_release_env", lambda *_: None)
    monkeypatch.setattr(
        version, "detect_changed_projects",
        lambda repo_root, projects, **kwargs: [("alpha", None, None, "minor")],
    )
    monkeypatch.setattr(version, "release_cmd", lambda *a, **k: None)
    monkeypatch.setenv(transaction.BRANCH_ENV, "cmru/release/child")
    monkeypatch.setenv(transaction.BASE_ENV, "a" * 40)

    def refuse(*_args, **_kwargs):
        raise version.ReleasePlanRefused("alpha: tool dependency beta@1.0.0 is stale")

    monkeypatch.setattr(cli, "_check_release_tool_dependencies", refuse)
    marks = []
    monkeypatch.setattr(transaction, "mark_plan_refused", lambda *args: marks.append(args))

    with pytest.raises(SystemExit) as exc:
        cli.main(["release", "--_transaction-child", "--config", str(tmp_path / "cmru.toml")])

    assert exc.value.code != 0
    assert marks
    err = capsys.readouterr().err
    assert "beta@1.0.0 is stale" in err
    assert "Traceback" not in err


def test_check_release_tool_dependencies_raises_on_a_blocking_status(monkeypatch, tmp_path):
    """Unit-level: the real (non-monkeypatched) helper turns a blocking
    verify_project result into a ReleasePlanRefused naming the offending line,
    and never calls verify_project at all for a project with nothing declared."""
    alpha = cli.ProjectConfig("alpha", {}, {}, prefix="alpha-v", tool_dependencies=(_tool_dep(),))
    beta = cli.ProjectConfig("beta", {}, {}, prefix="beta-v", tool_dependencies=())
    configs = {"alpha": alpha, "beta": beta}
    github_config = cli.GitHubConfig("owner", "repo", "token", "user")

    monkeypatch.setattr(
        tool_deps, "verify_project",
        lambda **kwargs: (
            tool_deps.ToolDependencyStatus(
                "alpha", _tool_dep(), tool_deps.CheckOutcome("pass", None, "ok"),
                tool_deps.CheckOutcome("fail", "hash-mismatch", "bytes disagree"),
                tool_deps.CheckOutcome("pass", None, "ok"), "1.0.0",
            ),
        ),
    )

    with pytest.raises(version.ReleasePlanRefused, match="bytes disagree"):
        cli._check_release_tool_dependencies(
            {"alpha": alpha}, configs, github_config=github_config, allow_stale=False,
        )


def test_check_release_tool_dependencies_reports_every_blocking_dependency_not_just_the_first(monkeypatch, tmp_path):
    """A single project declaring TWO tool dependencies: both are checked (the
    inner per-status loop does not stop after the first), and a refusal names
    both offending findings, not only the first one encountered."""
    alpha = cli.ProjectConfig(
        "alpha", {}, {}, prefix="alpha-v",
        tool_dependencies=(_tool_dep("beta"), _tool_dep("gamma")),
    )
    github_config = cli.GitHubConfig("owner", "repo", "token", "user")

    def fake_verify_project(**kwargs):
        return (
            tool_deps.ToolDependencyStatus(
                "alpha", _tool_dep("beta"), tool_deps.CheckOutcome("pass", None, "ok"),
                tool_deps.CheckOutcome("pass", None, "ok"),
                tool_deps.CheckOutcome("pass", None, "ok"), "1.0.0",
            ),
            tool_deps.ToolDependencyStatus(
                "alpha", _tool_dep("gamma"), tool_deps.CheckOutcome("fail", "hash-mismatch", "gamma is corrupt"),
                tool_deps.CheckOutcome("pass", None, "ok"),
                tool_deps.CheckOutcome("pass", None, "ok"), "1.0.0",
            ),
        )

    monkeypatch.setattr(tool_deps, "verify_project", fake_verify_project)

    with pytest.raises(version.ReleasePlanRefused, match="gamma is corrupt"):
        cli._check_release_tool_dependencies(
            {"alpha": alpha}, {"alpha": alpha}, github_config=github_config, allow_stale=False,
        )


def test_check_release_tool_dependencies_is_a_no_op_for_a_project_with_nothing_declared(tmp_path):
    """Real (non-monkeypatched) path end to end: a project with an empty
    ``tool_dependencies`` makes zero network calls -- ``verify_project``'s own
    loop never iterates -- and the helper returns normally (no refusal)."""
    beta = cli.ProjectConfig("beta", {}, {}, prefix="beta-v", tool_dependencies=())
    cli._check_release_tool_dependencies(
        {"beta": beta}, {"beta": beta},
        github_config=cli.GitHubConfig("o", "r", "t", "user"), allow_stale=False,
    )


def test_tool_deps_verb_dispatches_to_tool_deps_main(monkeypatch):
    calls = []
    monkeypatch.setattr(tool_deps, "tool_deps_main", lambda rest: calls.append(rest))
    cli.main(["tool-deps", "--json"])
    assert calls == [["--json"]]
