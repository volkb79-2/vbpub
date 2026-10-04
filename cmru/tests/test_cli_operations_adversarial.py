"""CLI operation-level witnesses for orchestration and release boundaries."""
from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from cmru import cli


class _Parser:
    def __init__(self, args): self.args = args
    def parse_args(self): return self.args


def _project(name="demo", **overrides):
    values = dict(name=name, cwd=name, paths=[name], runner_steps={"test": object()},
                  env={}, project_root=None, build_step="build", steps={}, github_token="token")
    values.update(overrides)
    return SimpleNamespace(**values)


def _config(tmp_path, project, *, mode="project-first"):
    return (tmp_path, {project.name: project}, [project.name], [project.name], ["test"], mode, {},
            SimpleNamespace(), cli.GitHubConfig("o", "r", "token", "user"), cli.ReleaseEnvConfig({}, None))


def test_orchestrate_project_first_runs_selected_steps_in_declared_order(monkeypatch, tmp_path):
    project = _project(project_root=tmp_path / "demo")
    args = SimpleNamespace(project=["demo"], run_tests=True, build=False, push=False, validate=False,
                            remove_assets=None, dry_run=False, show_run_details=False, log_append=False, config=None)
    calls = []
    monkeypatch.setattr(cli, "build_arg_parser", lambda: _Parser(args))
    monkeypatch.setattr(cli, "_resolve_config", lambda value: tmp_path / "cmru.toml")
    monkeypatch.setattr(cli, "load_config", lambda path: _config(tmp_path, project))
    monkeypatch.setattr(cli, "resolve_versions_from_git", lambda *a: None)
    monkeypatch.setattr(cli, "apply_project_release_env", lambda *a: None)
    monkeypatch.setattr(cli, "run_project_step", lambda project, step, root, logs: calls.append((project.name, step)))
    cli._orchestrate()
    assert calls == [("demo", "run-tests")]


def test_orchestrate_step_first_uses_step_order_and_rejects_unknown_project(monkeypatch, tmp_path):
    project = _project(project_root=tmp_path / "demo", runner_steps={"test": object(), "build": object()})
    args = SimpleNamespace(target="missing", run_tests=False, build=True, push=False, validate=False,
                            remove_assets=None, dry_run=False, show_run_details=False, log_append=False, config=None)
    monkeypatch.setattr(cli, "build_arg_parser", lambda: _Parser(args))
    monkeypatch.setattr(cli, "_resolve_config", lambda value: tmp_path / "cmru.toml")
    monkeypatch.setattr(cli, "load_config", lambda path: _config(tmp_path, project, mode="step-first"))
    with pytest.raises(SystemExit):
        cli._orchestrate()


def test_untagged_release_refuses_missing_build_and_post_build_mutation(monkeypatch, tmp_path):
    project = _project(build_step=None, runner_steps={"push": object()})
    monkeypatch.setattr(cli, "resolve_versions_from_git", lambda *a: None)
    monkeypatch.setattr(cli, "apply_project_release_env", lambda *a: None)
    with pytest.raises(RuntimeError, match="build_step is absent"):
        cli._run_untagged_project(tmp_path, {"demo": project}, "demo", github_config=None, env_config=None)
    project.build_step = "build"
    project.runner_steps = {"build": object(), "push": object()}
    calls = []
    monkeypatch.setattr(cli, "run_project_step", lambda project, step, root, logs: calls.append(step))
    monkeypatch.setattr(cli, "_worktree_changed_paths", lambda root: ["demo/generated.txt"])
    with pytest.raises(RuntimeError, match="changed tracked source"):
        cli._run_untagged_project(tmp_path, {"demo": project}, "demo", github_config=None, env_config=None)
    assert calls == ["build"]  # mutation refusal precedes the push boundary


def test_cleanup_commit_deletions_commits_only_real_changes(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["git", "config", "user.email", "t@x"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True)
    file = tmp_path / "generated"; file.write_text("x")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "initial"], cwd=tmp_path, check=True)
    file.unlink()
    cli.cleanup_commit_deletions(
        tmp_path, "demo", ["demo-v1"], False, before_paths=set(),
    )
    assert "cleanup deleted demo-v1" in subprocess.check_output(["git", "log", "-1", "--format=%s"], cwd=tmp_path, text=True)


def test_push_tags_failure_is_fatal_and_empty_input_is_a_noop(monkeypatch, tmp_path):
    calls = []
    current_local_oid = ["b" * 40]
    monkeypatch.setattr(
        cli, "run_remote_git",
        lambda *argv, **kwargs: calls.append((argv, kwargs))
        or SimpleNamespace(returncode=1),
    )
    monkeypatch.setattr(
        cli, "local_git_tag_oid",
        lambda *_args, **_kwargs: current_local_oid[0],
    )
    monkeypatch.setattr(cli, "_release_tag_matches_origin", lambda *_args, **_kwargs: False)
    deleted = []
    def delete_local_tag(root, tag, dry_run, **kwargs):
        deleted.append((root, tag, dry_run, kwargs))
        current_local_oid[0] = None

    monkeypatch.setattr(cli, "delete_git_tag_local", delete_local_tag)
    cli._push_tags(tmp_path, [])
    assert calls == []
    with pytest.raises(RuntimeError, match="removed the unpushed local tag"):
        cli._push_tags(tmp_path, ["demo-v1"])
    assert len(calls) == 1
    assert calls[0][0] == (tmp_path, "push", "origin", "demo-v1")
    assert deleted == [(
        tmp_path, "demo-v1", False,
        {"expected_present": True, "expected_oid": "b" * 40},
    )]


def test_push_tags_continues_when_failed_transport_left_the_exact_tag_on_origin(
    monkeypatch, tmp_path,
):
    monkeypatch.setattr(
        cli, "run_remote_git", lambda *_args, **_kwargs: SimpleNamespace(returncode=1),
    )
    monkeypatch.setattr(cli, "local_git_tag_oid", lambda *_args, **_kwargs: "b" * 40)
    monkeypatch.setattr(cli, "_release_tag_matches_origin", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        cli, "delete_git_tag_local",
        lambda *_args, **_kwargs: pytest.fail("a published candidate tag was deleted"),
    )

    cli._push_tags(tmp_path, ["demo-v1"])


def test_push_tags_retains_local_tag_when_origin_state_is_indeterminate(monkeypatch, tmp_path):
    monkeypatch.setattr(
        cli, "run_remote_git", lambda *_args, **_kwargs: SimpleNamespace(returncode=1),
    )
    monkeypatch.setattr(cli, "local_git_tag_oid", lambda *_args, **_kwargs: "b" * 40)

    def origin_is_unavailable(*_args, **_kwargs):
        raise RuntimeError("offline")

    monkeypatch.setattr(
        cli, "_release_tag_matches_origin", origin_is_unavailable,
    )
    monkeypatch.setattr(
        cli, "delete_git_tag_local",
        lambda *_args, **_kwargs: pytest.fail("an unverified tag was deleted"),
    )

    with pytest.raises(RuntimeError, match="could not determine origin state"):
        cli._push_tags(tmp_path, ["demo-v1"])


def test_push_tags_records_exact_local_tag_attempt_before_network_call(monkeypatch, tmp_path):
    workspace = cli.transaction.ReleaseWorkspace(
        repo_root=tmp_path,
        path=tmp_path,
        branch="cmru-release-20261003_120000-demo-abcdef",
        base="a" * 40,
    )
    events = []
    local_oid = ["b" * 40]
    monkeypatch.setattr(cli, "local_git_tag_oid", lambda *_args, **_kwargs: local_oid[0])
    monkeypatch.setattr(
        cli.transaction,
        "write_release_tag_attempts",
        lambda _root, _workspace, refs: events.append(("record", refs)),
    )
    monkeypatch.setattr(
        cli, "run_remote_git",
        lambda *_args, **_kwargs: events.append(("push", None))
        or SimpleNamespace(returncode=1),
    )
    monkeypatch.setattr(cli, "_release_tag_matches_origin", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(
        cli, "delete_git_tag_local", lambda *_args, **_kwargs: local_oid.__setitem__(0, None),
    )

    with pytest.raises(RuntimeError, match="removed the unpushed local tag"):
        cli._push_tags(tmp_path, ["demo-v1"], workspace=workspace)

    assert events == [
        ("record", {"refs/tags/demo-v1": "b" * 40}),
        ("push", None),
    ]


def test_push_tags_refuses_a_tag_that_disappears_before_push(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "local_git_tag_oid", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        cli, "run_remote_git",
        lambda *_args, **_kwargs: pytest.fail("push ran after the local tag disappeared"),
    )
    with pytest.raises(RuntimeError, match="disappeared before it could be pushed"):
        cli._push_tags(tmp_path, ["demo-v1"])


def test_push_tags_retains_candidate_when_local_tag_cleanup_fails(monkeypatch, tmp_path):
    monkeypatch.setattr(
        cli, "local_git_tag_oid", lambda *_args, **_kwargs: "b" * 40,
    )
    monkeypatch.setattr(
        cli, "run_remote_git", lambda *_args, **_kwargs: SimpleNamespace(returncode=1),
    )
    monkeypatch.setattr(cli, "_release_tag_matches_origin", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(
        cli, "delete_git_tag_local",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("tag store unreadable")),
    )

    with pytest.raises(RuntimeError, match="could not remove every origin-absent local tag.*tag store unreadable"):
        cli._push_tags(tmp_path, ["demo-v1"])


def test_push_tags_refuses_a_partially_pushed_tag_set(monkeypatch, tmp_path):
    seen = {"demo-v1": 0, "demo-v2": 0}

    def local_tag_oid(_root, tag, **_kwargs):
        seen[tag] += 1
        if tag == "demo-v2" and seen[tag] > 1:
            return None
        return ("a" if tag == "demo-v1" else "b") * 40

    monkeypatch.setattr(cli, "local_git_tag_oid", local_tag_oid)
    monkeypatch.setattr(
        cli, "run_remote_git", lambda *_args, **_kwargs: SimpleNamespace(returncode=1),
    )
    monkeypatch.setattr(
        cli, "_release_tag_matches_origin", lambda _root, tag, **_kwargs: tag == "demo-v1",
    )
    monkeypatch.setattr(
        cli, "delete_git_tag_local",
        lambda *_args, **_kwargs: None,
    )

    with pytest.raises(RuntimeError, match="only some release tags were confirmed on origin"):
        cli._push_tags(tmp_path, ["demo-v1", "demo-v2"])


def test_release_tag_origin_lookup_error_preserves_git_diagnostic(monkeypatch, tmp_path):
    monkeypatch.setattr(
        cli, "run_remote_git",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=2, stdout="", stderr="",
        ),
    )

    with pytest.raises(RuntimeError, match="git ls-remote could not verify 'demo-v1': no diagnostic output"):
        cli._release_tag_matches_origin(tmp_path, "demo-v1", git_auth=None)


def test_push_tags_records_attempt_and_accepts_git_error_when_every_tag_is_verified(
    monkeypatch, tmp_path,
):
    oid = "a" * 40
    events = []
    monkeypatch.setattr(cli, "local_git_tag_oid", lambda *_args, **_kwargs: oid)
    monkeypatch.setattr(
        cli.transaction, "write_release_tag_attempts",
        lambda _root, _workspace, refs: events.append(("record", refs)),
    )
    monkeypatch.setattr(
        cli, "run_remote_git",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=1),
    )
    monkeypatch.setattr(
        cli, "_release_tag_matches_origin",
        lambda _root, tag, **_kwargs: events.append(("verify", tag)) or True,
    )
    monkeypatch.setattr(
        cli, "delete_git_tag_local",
        lambda *_args, **_kwargs: pytest.fail("verified remote tag was removed locally"),
    )
    workspace = SimpleNamespace(branch="cmru-release-test")

    cli._push_tags(tmp_path, ["demo-v1"], workspace=workspace)

    assert events == [
        ("record", {"refs/tags/demo-v1": oid}),
        ("verify", "demo-v1"),
    ]


@pytest.mark.parametrize(
    "stdout, local, expected",
    [
        ("", "a" * 40, False),
        ("a" * 40 + "\trefs/tags/demo-v1\n", "a" * 40, True),
        ("a" * 40 + "\trefs/tags/demo-v1\n", "b" * 40, False),
    ],
)
def test_release_tag_origin_lookup_requires_matching_remote_commit(
    monkeypatch, tmp_path, stdout, local, expected,
):
    monkeypatch.setattr(cli, "run_remote_git", lambda *_args, **_kwargs: SimpleNamespace(
        returncode=0, stdout=stdout, stderr="",
    ))
    monkeypatch.setattr(cli, "_git", lambda *_args, **_kwargs: local)

    assert cli._release_tag_matches_origin(
        tmp_path, "demo-v1", git_auth=None,
    ) is expected
