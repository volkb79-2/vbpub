from contextlib import nullcontext
from pathlib import Path

import pytest

from cmru import cli, transaction


@pytest.fixture(autouse=True)
def fake_release_preflight(monkeypatch):
    monkeypatch.setattr(cli.transaction, "read_release_source", lambda *_args: None)
    monkeypatch.setattr(cli.transaction, "source_git_root_for_projects", lambda root, _projects: root)
    monkeypatch.setattr(
        cli.transaction,
        "project_git_family_groups",
        lambda root, projects: {root: list(projects)},
    )
    monkeypatch.setattr(
        cli, "_project_git_tag_policy_at_snapshot",
        lambda _root, _base, project, **_kwargs: getattr(project, "git_tag", True),
    )
    monkeypatch.setattr(
        cli, "_project_config_paths_at_snapshot",
        lambda _root, _base, _config, _configs, names: {
            name: Path(name) / "cmru.toml" for name in names
        },
    )
    monkeypatch.setattr(cli, "_require_local_tag_inspection_support", lambda _root: None)
    monkeypatch.setattr(cli, "_read_origin_tag_refs", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(cli.transaction, "write_release_tag_snapshot", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(cli.transaction, "clear_plan_refused", lambda *_args, **_kwargs: None)


def _loaded(tmp_path):
    project = cli.ProjectConfig("alpha", {}, {}, project_root=tmp_path / "alpha", prefix="alpha-v", github_token="token")
    return (
        tmp_path, {"alpha": project}, ["alpha"], ["alpha"], ["alpha"], "project-first", {},
        cli.CleanupConfig([], [], [], []), cli.GitHubConfig("o", "r", "token", "user"),
        cli.ReleaseEnvConfig({}, None),
    )


def test_release_failure_retains_candidate_without_automatic_revert(monkeypatch, tmp_path, capsys):
    config = _loaded(tmp_path)
    workspace = transaction.ReleaseWorkspace(tmp_path, tmp_path / "release", "cmru/release/x", "a" * 40)
    monkeypatch.setattr(cli, "_resolve_config", lambda _: tmp_path / "cmru.toml")
    monkeypatch.setattr(cli, "load_config", lambda _: config)
    monkeypatch.setattr(cli, "apply_release_env", lambda *_: None)
    monkeypatch.setattr(cli.transaction, "release_lock", lambda _: nullcontext())
    monkeypatch.setattr(cli, "_uncommitted_release_paths", lambda *args: {})
    monkeypatch.setattr(cli.transaction, "fetch_origin_main", lambda *_, **__: "a" * 40)
    monkeypatch.setattr(cli.transaction, "assert_local_main_not_ahead", lambda *_, **__: 0)
    monkeypatch.setattr(cli.transaction, "create_workspace", lambda *args, **kwargs: workspace)
    monkeypatch.setattr(cli.transaction, "run_child", lambda *args, **kwargs: 1)
    monkeypatch.setattr(cli.transaction, "plan_was_refused", lambda *args: False)
    calls = []
    monkeypatch.setattr(cli.transaction, "promotion_landed", lambda *args: calls.append("promotion-check") or True)
    monkeypatch.setattr(cli.transaction, "revert_promotion", lambda *args, **kwargs: calls.append("revert"))
    monkeypatch.setattr(
        cli.transaction, "_sync_local_main_result",
        lambda *args, **kwargs: transaction._SyncLocalMainResult(True),
    )
    monkeypatch.setattr(cli.transaction, "remove_workspace", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("failed releases retain worktree")))
    exc = cli.main(["release", "alpha", "--config", str(tmp_path / "cmru.toml")])
    assert exc == 1
    captured = capsys.readouterr()
    output = captured.out + captured.err
    assert "Release candidate was not promoted" in output
    assert "candidate branch" in output
    assert calls == []


def _failing_dry_run_fixture(monkeypatch, tmp_path, *, pushed=False, results=None):
    """A NEW ``release --dry-run`` whose child exits 1 (KI-91)."""
    config = _loaded(tmp_path)
    workspace = transaction.ReleaseWorkspace(
        tmp_path, tmp_path / "cmru-release-20261021_012919-alpha-oes4gj",
        "cmru-release-20261021_012919-alpha-oes4gj", "a" * 40,
    )
    monkeypatch.setattr(cli, "_resolve_config", lambda _: tmp_path / "cmru.toml")
    monkeypatch.setattr(cli, "load_config", lambda _: config)
    monkeypatch.setattr(cli, "apply_release_env", lambda *_: None)
    monkeypatch.setattr(cli.transaction, "release_lock", lambda _: nullcontext())
    monkeypatch.setattr(cli, "_uncommitted_release_paths", lambda *args: {})
    monkeypatch.setattr(cli.transaction, "fetch_origin_main", lambda *_, **__: "a" * 40)
    monkeypatch.setattr(cli.transaction, "assert_local_main_not_ahead", lambda *_, **__: 0)
    monkeypatch.setattr(cli.transaction, "create_workspace", lambda *args, **kwargs: workspace)
    monkeypatch.setattr(cli.transaction, "run_child", lambda *args, **kwargs: 1)
    monkeypatch.setattr(cli.transaction, "plan_was_refused", lambda *args: False)
    monkeypatch.setattr(cli.transaction, "backup_was_pushed", lambda *args: pushed)
    monkeypatch.setattr(cli.transaction, "read_release_results", lambda *args: dict(results or {}))
    calls = []
    monkeypatch.setattr(
        cli.transaction, "remove_workspace", lambda ws: calls.append(("remove", ws.branch)),
    )
    monkeypatch.setattr(
        cli.transaction, "forget_release_scope", lambda _root, ws: calls.append(("forget", ws.branch)),
    )
    monkeypatch.setattr(
        cli.transaction, "_sync_local_main_result",
        lambda *args, **kwargs: calls.append(("sync",)) or transaction._SyncLocalMainResult(True),
    )
    return workspace, calls


def test_ki91_failed_new_dry_run_discards_its_disposable_worktree(monkeypatch, tmp_path, capsys):
    workspace, calls = _failing_dry_run_fixture(monkeypatch, tmp_path)
    exc = cli.main(["release", "alpha", "--dry-run", "--config", str(tmp_path / "cmru.toml")])
    assert exc == 1
    # The worktree and branch go, the sidecars go, and the caller's main is not touched.
    assert calls == [("remove", workspace.branch), ("forget", workspace.branch)]
    captured = capsys.readouterr()
    output = captured.out + captured.err
    assert "Release dry run failed" in output
    assert str(workspace.path) in output
    assert "were discarded" in output
    assert "retained" not in output


@pytest.mark.parametrize("pushed, results", [(True, {}), (False, {"alpha": "alpha-v1.0.0"})])
def test_ki91_dry_run_discard_never_touches_a_pushed_or_published_candidate(
    monkeypatch, tmp_path, capsys, pushed, results,
):
    """Controlled wrong: a candidate with remote or publication evidence is not a dry run's."""
    workspace, calls = _failing_dry_run_fixture(monkeypatch, tmp_path, pushed=pushed, results=results)
    exc = cli.main(["release", "alpha", "--dry-run", "--config", str(tmp_path / "cmru.toml")])
    assert exc == 1
    assert ("remove", workspace.branch) not in calls
    assert ("forget", workspace.branch) not in calls
    captured = capsys.readouterr()
    assert "retained" in captured.out + captured.err


def test_ki91_failed_real_release_still_retains(monkeypatch, tmp_path, capsys):
    """The discard is dry-run only: the real failure path is unchanged."""
    workspace, calls = _failing_dry_run_fixture(monkeypatch, tmp_path)
    monkeypatch.setattr(cli, "_record_release_controller_identity", lambda _phase: {
        "version": "0", "wheel_sha256": "0", "distribution_sha256": "0",
    })
    monkeypatch.setattr(cli, "_preflight_multi_family_release_tag_support", lambda *a, **k: None)
    exc = cli.main(["release", "alpha", "--config", str(tmp_path / "cmru.toml")])
    assert exc == 1
    assert ("remove", workspace.branch) not in calls
    captured = capsys.readouterr()
    assert "retained" in captured.out + captured.err
