"""KI-12: prove the CLI actually wires the release plan's new guarantees --
not just that ``version.detect_changed_projects`` supports them.

* the isolated release transaction's plan computation always requires a
  pushed baseline (``require_pushed_baseline=True``) and always checks the
  tag-at-head relationship (``check_tag_at_head=True``) -- neither has a CLI
  override;
* ``--allow-tag-ahead-of-head`` (and only that flag) flips
  ``allow_tag_ahead_of_head``; its deprecated alias ``--allow-tag-at-head``
  maps to the exact same thing;
* the plan is computed over the run's own scope (``--project X``, else every
  orchestrated project) -- not blindly over every orchestrated project
  regardless of what this run will actually touch;
* a ``ReleasePlanRefused`` from the plan computation makes the PARENT process
  discard the just-created worktree (never retain it for inspection).
"""
from __future__ import annotations

from contextlib import nullcontext

import pytest

from cmru import cli, transaction, version


def _config(tmp_path):
    alpha = cli.ProjectConfig("alpha", {}, {}, prefix="alpha-v", github_token="token")
    beta = cli.ProjectConfig("beta", {}, {}, prefix="beta-v", github_token="token")
    return (
        tmp_path, {"alpha": alpha, "beta": beta}, ["alpha", "beta"], ["alpha", "beta"], [],
        "project-first", {}, cli.CleanupConfig([], [], [], []),
        cli.GitHubConfig("owner", "repo", "token", "user"), cli.ReleaseEnvConfig({}, None),
    )


def _run_release(monkeypatch, tmp_path, extra_args):
    calls = []
    monkeypatch.setattr(cli, "_resolve_config", lambda _: tmp_path / "cmru.toml")
    monkeypatch.setattr(cli, "load_config", lambda _: _config(tmp_path))
    monkeypatch.setattr(cli, "apply_release_env", lambda *_: None)

    def fake_detect(repo_root, projects, **kwargs):
        calls.append((dict(projects), kwargs))
        return []

    monkeypatch.setattr(version, "detect_changed_projects", fake_detect)
    # release_cmd's own dirty-tree guard shells out to a real `git status`;
    # irrelevant to what this test asserts (the detect_changed_projects call),
    # so it is stubbed out exactly like test_cli_release_final_dispatch.py does.
    monkeypatch.setattr(version, "release_cmd", lambda *args, **kwargs: None)
    cli.main(
        ["release", "--_transaction-child", "--dry-run", "--config", str(tmp_path / "cmru.toml")]
        + extra_args
    )
    return calls


def test_release_plan_requires_pushed_baseline_and_checks_tag_at_head_unconditionally(monkeypatch, tmp_path):
    calls = _run_release(monkeypatch, tmp_path, [])
    assert len(calls) == 1
    projects, kwargs = calls[0]
    assert set(projects) == {"alpha", "beta"}  # unscoped run: every orchestrated project
    assert kwargs["require_pushed_baseline"] is True
    assert kwargs["check_tag_at_head"] is True
    assert kwargs["allow_tag_ahead_of_head"] is False


def test_allow_tag_ahead_of_head_flag_flips_only_that_one_knob(monkeypatch, tmp_path):
    calls = _run_release(monkeypatch, tmp_path, ["--allow-tag-ahead-of-head"])
    projects, kwargs = calls[0]
    assert kwargs["require_pushed_baseline"] is True  # unaffected by the flag
    assert kwargs["check_tag_at_head"] is True         # unaffected by the flag
    assert kwargs["allow_tag_ahead_of_head"] is True


def test_deprecated_allow_tag_at_head_alias_maps_to_the_same_flag(monkeypatch, tmp_path, capsys):
    """The old spelling must keep working exactly like the new one -- and
    warn that it is deprecated, so operators migrate off it."""
    calls = _run_release(monkeypatch, tmp_path, ["--allow-tag-at-head"])
    projects, kwargs = calls[0]
    assert kwargs["allow_tag_ahead_of_head"] is True
    assert "deprecated" in capsys.readouterr().out.lower()


def test_project_scoped_release_plan_only_checks_the_scoped_project(monkeypatch, tmp_path):
    """An unrelated orchestrated project's degenerate tag state must never
    abort a --project-scoped run that never touches it -- the plan must be
    computed over exactly the run's own scope, not silently over everything."""
    calls = _run_release(monkeypatch, tmp_path, ["--project", "alpha"])
    projects, kwargs = calls[0]
    assert set(projects) == {"alpha"}
    assert kwargs["require_pushed_baseline"] is True
    assert kwargs["check_tag_at_head"] is True


# ---------------------------------------------------------------------------
# A release-plan refusal discards the just-created worktree (parent side) --
# never retains it, unlike a genuine mid-release failure.
# ---------------------------------------------------------------------------

def _loaded(tmp_path):
    project = cli.ProjectConfig("alpha", {}, {}, project_root=tmp_path / "alpha", prefix="alpha-v", github_token="token")
    return (
        tmp_path, {"alpha": project}, ["alpha"], ["alpha"], ["alpha"], "project-first", {},
        cli.CleanupConfig([], [], [], []), cli.GitHubConfig("o", "r", "token", "user"),
        cli.ReleaseEnvConfig({}, None),
    )


def test_child_side_marks_plan_refused_and_exits_nonzero(monkeypatch, tmp_path, capsys):
    """The child process: a ReleasePlanRefused from detect_changed_projects
    must print a clean operator message (no raw traceback), mark the
    transaction as plan-refused, and exit non-zero -- not propagate as an
    unhandled exception."""
    config = _loaded(tmp_path)
    monkeypatch.setattr(cli, "_resolve_config", lambda _: tmp_path / "cmru.toml")
    monkeypatch.setattr(cli, "load_config", lambda _: config)
    monkeypatch.setattr(cli, "apply_release_env", lambda *_: None)
    monkeypatch.setenv(transaction.BRANCH_ENV, "cmru/release/child")
    monkeypatch.setenv(transaction.BASE_ENV, "a" * 40)

    def refuse(*_args, **_kwargs):
        raise version.ReleasePlanRefused("alpha: latest tag alpha-v1.0.0 is AHEAD of the snapshot commit")

    monkeypatch.setattr(version, "detect_changed_projects", refuse)
    marks = []
    monkeypatch.setattr(transaction, "mark_plan_refused", lambda *args: marks.append(args))

    with pytest.raises(SystemExit) as exc:
        cli.main(["release", "--_transaction-child", "--config", str(tmp_path / "cmru.toml")])

    assert exc.value.code != 0
    assert marks  # mark_plan_refused was called for this transaction
    err = capsys.readouterr().err
    assert "AHEAD of the snapshot commit" in err
    assert "Traceback" not in err  # a clean operator message, not a raw traceback


def test_parent_discards_worktree_on_a_plan_refusal_instead_of_retaining_it(monkeypatch, tmp_path):
    """The parent process: when the child's transaction was marked
    plan-refused, the worktree/branch MUST be removed exactly like a success
    would be -- never retained the way a genuine mid-release failure is."""
    config = _loaded(tmp_path)
    workspace = transaction.ReleaseWorkspace(tmp_path, tmp_path / "release", "cmru/release/x", "a" * 40)
    monkeypatch.setattr(cli, "_resolve_config", lambda _: tmp_path / "cmru.toml")
    monkeypatch.setattr(cli, "load_config", lambda _: config)
    monkeypatch.setattr(cli, "apply_release_env", lambda *_: None)
    monkeypatch.setattr(cli.transaction, "release_lock", lambda _: nullcontext())
    monkeypatch.setattr(cli, "_uncommitted_release_paths", lambda *args: {})
    monkeypatch.setattr(cli.transaction, "fetch_origin_main", lambda *_: "a" * 40)
    monkeypatch.setattr(cli.transaction, "assert_local_main_not_ahead", lambda *_: 0)
    monkeypatch.setattr(cli.transaction, "create_workspace", lambda *args, **kwargs: workspace)
    monkeypatch.setattr(cli.transaction, "copy_secret_overlays", lambda *args: None)
    monkeypatch.setattr(cli.transaction, "run_child", lambda *args, **kwargs: 1)
    monkeypatch.setattr(cli.transaction, "plan_was_refused", lambda *args: True)
    monkeypatch.setattr(cli.transaction, "sync_local_main", lambda *args: True)
    removed = []
    monkeypatch.setattr(cli.transaction, "remove_workspace", lambda *args: removed.append("removed"))
    forgotten = []
    monkeypatch.setattr(cli.transaction, "forget_release_scope", lambda *args: forgotten.append("forgotten"))
    # These must NEVER be called for a plan refusal: nothing was promoted (no
    # revert to attempt) and nothing was ever pushed as a backup (no remote
    # branch to delete).
    monkeypatch.setattr(
        cli.transaction, "promotion_landed",
        lambda *args: (_ for _ in ()).throw(AssertionError("must not check promotion for a plan refusal")),
    )
    monkeypatch.setattr(
        cli.transaction, "remove_backup_branch",
        lambda *args: (_ for _ in ()).throw(AssertionError("nothing was ever pushed to delete")),
    )

    with pytest.raises(SystemExit) as exc:
        cli.main(["release", "--project", "alpha", "--config", str(tmp_path / "cmru.toml")])

    assert exc.value.code == 1
    assert removed == ["removed"]
    assert forgotten == ["forgotten"]
