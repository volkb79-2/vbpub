"""Behavioral witnesses for snapshot readers and retained release replay."""
from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from cmru import cli, transaction


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True,
    )
    if result.returncode:
        raise AssertionError(result.stderr)
    return result.stdout.strip()


def _repo(root: Path) -> tuple[Path, str]:
    root.mkdir(parents=True)
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "cmru-test@example.invalid")
    _git(root, "config", "user.name", "cmru test")
    return root, ""


def _commit(root: Path, message: str = "fixture") -> str:
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", message)
    return _git(root, "rev-parse", "HEAD")


def _candidate(tmp_path: Path, root: Path | None = None) -> transaction.ReleaseWorkspace:
    source = root or tmp_path
    path = tmp_path / "candidate"
    path.mkdir(exist_ok=True)
    return transaction.ReleaseWorkspace(source, path, "cmru/release/test", "a" * 40)


@pytest.mark.parametrize("relative", [Path("/outside/cmru.toml"), Path("../cmru.toml"), Path("")])
def test_git_snapshot_reader_rejects_paths_outside_the_family(tmp_path, relative):
    with pytest.raises(RuntimeError, match="Invalid tracked path"):
        cli._resolve_git_file_at_commit(
            tmp_path, "a" * 40, relative, source_label="origin/main",
        )


def test_git_snapshot_reader_follows_dot_and_parent_segments_without_leaving_tree(tmp_path):
    root, _ = _repo(tmp_path / "repo")
    (root / "shared").mkdir()
    (root / "shared" / "cmru.toml").write_text("project = 'snapshot'\n", encoding="utf-8")
    (root / "nested").mkdir()
    (root / "nested" / "parent.toml").symlink_to("../shared/cmru.toml")
    (root / "dot.toml").symlink_to("./shared//cmru.toml")
    (root / "shared-dir").symlink_to("shared")
    revision = _commit(root)

    expected = "project = 'snapshot'\n"
    for selected in ("nested/parent.toml", "dot.toml", "shared-dir/cmru.toml"):
        resolved, content = cli._resolve_git_file_at_commit(
            root, revision, Path(selected), source_label="origin/main",
        )
        assert resolved == Path("shared/cmru.toml")
        assert content == expected


@pytest.mark.parametrize("target", ["/etc/passwd", "../../outside/cmru.toml"])
def test_git_snapshot_reader_refuses_symlink_escape(tmp_path, target):
    root, _ = _repo(tmp_path / "repo")
    (root / "link").symlink_to(target)
    revision = _commit(root)

    with pytest.raises(RuntimeError, match="Symlink escapes the Git family"):
        cli._resolve_git_file_at_commit(
            root, revision, Path("link"), source_label="origin/main",
        )


def test_git_snapshot_reader_refuses_a_symlink_that_resolves_to_the_root(tmp_path):
    root, _ = _repo(tmp_path / "repo")
    (root / "root-link").symlink_to(".")
    revision = _commit(root)

    with pytest.raises(RuntimeError, match="does not resolve to a file"):
        cli._resolve_git_file_at_commit(
            root, revision, Path("root-link"), source_label="origin/main",
        )


def test_git_snapshot_reader_distinguishes_missing_files_directories_and_file_parents(tmp_path):
    root, _ = _repo(tmp_path / "repo")
    (root / "regular.txt").write_text("x\n", encoding="utf-8")
    (root / "directory").mkdir()
    (root / "directory" / "tracked.txt").write_text("inside\n", encoding="utf-8")
    revision = _commit(root)

    with pytest.raises(RuntimeError, match="Tracked path is missing"):
        cli._resolve_git_file_at_commit(
            root, revision, Path("missing.txt"), source_label="origin/main",
        )
    with pytest.raises(RuntimeError, match="not a regular file"):
        cli._resolve_git_file_at_commit(
            root, revision, Path("directory"), source_label="origin/main",
        )
    with pytest.raises(RuntimeError, match="component is not a directory"):
        cli._resolve_git_file_at_commit(
            root, revision, Path("regular.txt/child"), source_label="origin/main",
        )


@pytest.mark.parametrize("link_count, succeeds", [(39, True), (40, False)])
def test_git_snapshot_reader_enforces_the_symlink_chain_limit(tmp_path, link_count, succeeds):
    root, _ = _repo(tmp_path / "repo")
    (root / f"link{link_count}").write_text("target\n", encoding="utf-8")
    for index in reversed(range(link_count)):
        (root / f"link{index}").symlink_to(f"link{index + 1}")
    revision = _commit(root)

    if succeeds:
        assert cli._resolve_git_file_at_commit(
            root, revision, Path("link0"), source_label="origin/main",
        ) == (Path(f"link{link_count}"), "target\n")
    else:
        with pytest.raises(RuntimeError, match="resolution exceeded its limit"):
            cli._resolve_git_file_at_commit(
                root, revision, Path("link0"), source_label="origin/main",
            )


def test_git_snapshot_reader_reports_tree_and_blob_git_failures_without_diagnostics(
    monkeypatch, tmp_path,
):
    def failed_tree(*_args, **_kwargs):
        return SimpleNamespace(returncode=1, stdout="", stderr="")

    monkeypatch.setattr(cli, "run_local_git", failed_tree)
    with pytest.raises(RuntimeError, match="no diagnostic output"):
        cli._resolve_git_file_at_commit(
            tmp_path, "a" * 40, Path("config.toml"), source_label="origin/main",
        )

    def failed_blob(_root, *args, **_kwargs):
        if args[0] == "ls-tree":
            return SimpleNamespace(
                returncode=0, stdout=f"120000 blob {'b' * 40}\tlink\0", stderr="",
            )
        return SimpleNamespace(returncode=1, stdout="", stderr="")

    monkeypatch.setattr(cli, "run_local_git", failed_blob)
    with pytest.raises(RuntimeError, match="Failed to read symlink.*no diagnostic output"):
        cli._resolve_git_file_at_commit(
            tmp_path, "a" * 40, Path("link"), source_label="origin/main",
        )


def test_git_snapshot_reader_reports_malformed_tree_and_unreadable_regular_blob(
    monkeypatch, tmp_path,
):
    malformed_outputs = [
        ("malformed" + chr(0), "Malformed Git tree response.*missing path separator"),
        ("100644 blob" + chr(9) + "config.toml" + chr(0), "Malformed Git tree metadata"),
        (chr(0), "Malformed Git tree response.*empty entry"),
        (
            f"100644 blob {'c' * 40}{chr(9)}other.toml{chr(0)}",
            "Git returned an unexpected path",
        ),
        (
            f"100644 blob {'c' * 40}{chr(9)}config.toml",
            "Malformed Git tree response.*missing NUL terminator",
        ),
        (
            f"100644 blob {'c' * 40}{chr(9)}config.toml{chr(0)}"
            "malformed entry" + chr(0),
            "Malformed Git tree response.*expected a single entry",
        ),
    ]
    for output, message in malformed_outputs:
        monkeypatch.setattr(
            cli, "run_local_git",
            lambda *_args, _output=output, **_kwargs: SimpleNamespace(
                returncode=0, stdout=_output, stderr="",
            ),
        )
        with pytest.raises(RuntimeError, match=message):
            cli._resolve_git_file_at_commit(
                tmp_path, "a" * 40, Path("config.toml"), source_label="origin/main",
            )

    def unreadable_blob(_root, *args, **_kwargs):
        if args[0] == "ls-tree":
            return SimpleNamespace(
                returncode=0, stdout=f"100644 blob {'c' * 40}\tconfig.toml\0", stderr="",
            )
        return SimpleNamespace(returncode=1, stdout="", stderr="")

    monkeypatch.setattr(cli, "run_local_git", unreadable_blob)
    with pytest.raises(RuntimeError, match="Failed to read origin/main: no diagnostic output"):
        cli._resolve_git_file_at_commit(
            tmp_path, "a" * 40, Path("config.toml"), source_label="origin/main",
        )


@pytest.mark.parametrize("relative", [Path("/outside/cmru.toml"), Path("../cmru.toml"), Path("wrong.toml")])
def test_snapshot_tag_policy_rejects_paths_that_do_not_name_an_in_tree_project_config(
    tmp_path, relative,
):
    project = SimpleNamespace(name="demo", project_root=tmp_path / "demo")
    with pytest.raises(RuntimeError, match="invalid project config path"):
        cli._project_git_tag_policy_at_snapshot(
            tmp_path, "a" * 40, project, project_config_rel=relative,
        )


def test_snapshot_config_outside_the_selected_git_family_uses_loaded_project_path(
    tmp_path,
):
    root = tmp_path / "repo"
    project_root = root / "old-layout" / "demo"
    project_root.mkdir(parents=True)
    project = SimpleNamespace(project_root=project_root)

    paths = cli._project_config_paths_at_snapshot(
        root, "a" * 40, tmp_path / "central" / "cmru.orchestration.toml",
        {"demo": project}, ["demo"],
    )

    assert paths == {"demo": Path("old-layout/demo/cmru.toml")}


def test_snapshot_project_config_cannot_select_multiple_projects(tmp_path):
    root, _ = _repo(tmp_path / "repo")
    selected = root / "demo" / "cmru.toml"
    selected.parent.mkdir()
    selected.write_text("[project]\nid = 'demo'\n", encoding="utf-8")
    revision = _commit(root)

    with pytest.raises(RuntimeError, match="can select only one project"):
        cli._project_config_paths_at_snapshot(
            root, revision, selected,
            {"demo": SimpleNamespace(project_root=root / "demo"),
             "other": SimpleNamespace(project_root=root / "other")},
            ["demo", "other"],
        )


def test_snapshot_config_reference_can_be_relative_to_its_git_root(tmp_path, monkeypatch):
    root, _ = _repo(tmp_path / "repo")
    (root / "cmru.orchestration.toml").write_text(
        "[orchestration.project.demo]\nconfig = 'demo/cmru.toml'\n", encoding="utf-8",
    )
    (root / "demo").mkdir()
    (root / "demo" / "cmru.toml").write_text("[project]\nid = 'demo'\n", encoding="utf-8")
    revision = _commit(root)
    monkeypatch.chdir(root)

    assert cli._project_config_paths_at_snapshot(
        root, revision, Path("cmru.orchestration.toml"),
        {"demo": SimpleNamespace(project_root=root / "old")}, ["demo"],
    ) == {"demo": Path("demo/cmru.toml")}


def test_candidate_config_paths_refuse_git_failure_and_unsupported_names(monkeypatch, tmp_path):
    root = tmp_path / "source"
    candidate = tmp_path / "candidate"
    root.mkdir()
    candidate.mkdir()
    configs = {"demo": SimpleNamespace(project_root=root / "demo")}
    monkeypatch.setattr(
        cli, "run_local_git",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=1, stdout="", stderr=""),
    )
    with pytest.raises(RuntimeError, match="Could not identify committed retained candidate: no diagnostic output"):
        cli._project_config_paths_in_candidate(
            root, candidate, root / "cmru.orchestration.toml", configs, ["demo"],
        )

    monkeypatch.setattr(
        cli, "run_local_git",
        lambda _root, *args, **kwargs: SimpleNamespace(returncode=0, stdout="a" * 40, stderr=""),
    )
    monkeypatch.setattr(
        cli, "_resolve_git_file_at_commit",
        lambda _root, _revision, path, **_kwargs: (path, "configuration = true\n"),
    )
    with pytest.raises(RuntimeError, match="Unsupported CMRU config path"):
        cli._project_config_paths_in_candidate(
            root, candidate, root / "custom.toml", configs, ["demo"],
        )


def test_candidate_project_config_cannot_select_multiple_projects(monkeypatch, tmp_path):
    root = tmp_path / "source"
    candidate = tmp_path / "candidate"
    root.mkdir()
    candidate.mkdir()
    monkeypatch.setattr(
        cli, "run_local_git",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=0, stdout="a" * 40, stderr=""),
    )
    monkeypatch.setattr(
        cli, "_resolve_git_file_at_commit",
        lambda _root, _revision, path, **_kwargs: (path, "[project]\nid = 'demo'\n"),
    )

    with pytest.raises(RuntimeError, match="can select only one project"):
        cli._project_config_paths_in_candidate(
            root, candidate, root / "demo" / "cmru.toml",
            {"demo": SimpleNamespace(project_root=root / "demo"),
             "other": SimpleNamespace(project_root=root / "other")},
            ["demo", "other"],
        )


@pytest.mark.parametrize(
    "content, message",
    [
        ("not = [valid", "Invalid project config"),
        ("[project]\nid = 'other'\nprefix = 'demo-v'\n[project.release]\ngit_tag = true\n", "expected 'demo'"),
        ("[project]\nid = 'demo'\nprefix = '  '\n[project.release]\ngit_tag = true\n", "project.prefix is required"),
        ("[project]\nid = 'demo'\nprefix = 'demo-v'\n[project.release]\ngit_tag = 'yes'\n", "must be explicitly true or false"),
    ],
)
def test_project_release_policy_parser_rejects_invalid_snapshot_facts(content, message):
    with pytest.raises(RuntimeError, match=message):
        cli._parse_project_release_policy(content, "demo", "origin/main")


def test_candidate_release_policy_refuses_an_unidentifiable_commit(monkeypatch, tmp_path):
    monkeypatch.setattr(
        cli, "run_local_git",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=1, stdout="", stderr=""),
    )

    with pytest.raises(RuntimeError, match="Could not identify committed retained candidate: no diagnostic output"):
        cli._project_release_policy_in_candidate(
            tmp_path, "demo", Path("demo/cmru.toml"),
        )


def _resume_defaults(
    monkeypatch, tmp_path, *, results=None, snapshot=None, attempts=None,
    absence=None, local_tags=None, origin_tags=None,
):
    workspace = _candidate(tmp_path)
    project = SimpleNamespace(prefix="demo-v", git_tag=True)
    monkeypatch.setattr(transaction, "read_release_results", lambda *_args: results or {})
    monkeypatch.setattr(transaction, "read_release_tag_snapshot", lambda *_args: snapshot)
    monkeypatch.setattr(transaction, "read_release_tag_attempts", lambda *_args: attempts or {})
    monkeypatch.setattr(
        transaction, "read_confirmed_absent_release_tag_attempts",
        lambda *_args: absence or {},
    )
    monkeypatch.setattr(transaction, "list_local_tag_refs", lambda *_args: local_tags or {})
    monkeypatch.setattr(cli, "_read_origin_tag_refs", lambda *_args, **_kwargs: origin_tags or {})
    monkeypatch.setattr(transaction, "fetch_origin_main", lambda *_args, **_kwargs: "c" * 40)
    monkeypatch.setattr(
        cli, "run_local_git",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=0, stdout="", stderr=""),
    )
    return workspace, project


def test_resume_refuses_policy_or_result_metadata_outside_saved_scope(monkeypatch, tmp_path):
    workspace, project = _resume_defaults(monkeypatch, tmp_path)
    auth = cli.GitHubGitAuth("owner", "repo", "token")
    with pytest.raises(RuntimeError, match="policy names project.*outside the recorded scope"):
        cli._assert_resume_candidate_is_safe_to_replay(
            tmp_path, workspace, ["demo"], {"demo": project}, git_auth=auth,
            release_policies={"other": ("other-v", True)},
        )

    monkeypatch.setattr(transaction, "read_release_results", lambda *_args: {"other": "other-v1"})
    with pytest.raises(RuntimeError, match="result metadata names project.*outside the recorded scope"):
        cli._assert_resume_candidate_is_safe_to_replay(
            tmp_path, workspace, ["demo"], {"demo": project}, git_auth=auth,
        )


def test_resume_rejects_a_saved_scope_project_missing_from_current_config(monkeypatch, tmp_path):
    workspace, _project = _resume_defaults(monkeypatch, tmp_path)

    with pytest.raises(RuntimeError, match="scope names unknown project.*ghost"):
        cli._assert_resume_candidate_is_safe_to_replay(
            tmp_path, workspace, ["ghost"], {},
            git_auth=cli.GitHubGitAuth("owner", "repo", "token"),
        )


@pytest.mark.parametrize("tag", ["other-v1.0.0", "demo-v-latest"])
def test_resume_refuses_malformed_completed_tag_results(monkeypatch, tmp_path, tag):
    workspace, project = _resume_defaults(
        monkeypatch, tmp_path, results={"demo": tag}, origin_tags={f"refs/tags/{tag}": "b" * 40},
    )

    with pytest.raises(RuntimeError, match="not a valid release tag"):
        cli._assert_resume_candidate_is_safe_to_replay(
            tmp_path, workspace, ["demo"], {"demo": project},
            git_auth=cli.GitHubGitAuth("owner", "repo", "token"),
        )


def test_resume_requires_origin_to_advertise_a_completed_release_tag(monkeypatch, tmp_path):
    workspace, project = _resume_defaults(
        monkeypatch, tmp_path, results={"demo": "demo-v1.0.0"}, origin_tags={},
    )

    with pytest.raises(RuntimeError, match="does not advertise that release tag"):
        cli._assert_resume_candidate_is_safe_to_replay(
            tmp_path, workspace, ["demo"], {"demo": project},
            git_auth=cli.GitHubGitAuth("owner", "repo", "token"),
        )


def test_resume_rejects_a_completed_tag_that_changed_after_its_push_attempt(monkeypatch, tmp_path):
    tag = "demo-v1.0.0"
    workspace, project = _resume_defaults(
        monkeypatch, tmp_path, results={"demo": tag},
        origin_tags={f"refs/tags/{tag}": "b" * 40},
        attempts={f"refs/tags/{tag}": "a" * 40},
    )

    with pytest.raises(RuntimeError, match="no longer matches CMRU's recorded push attempt"):
        cli._assert_resume_candidate_is_safe_to_replay(
            tmp_path, workspace, ["demo"], {"demo": project},
            git_auth=cli.GitHubGitAuth("owner", "repo", "token"),
        )


@pytest.mark.parametrize("result_id", ["demo-v1.0.0", "source-not-a-commit", "source-zzzzzzzzzzzz"])
def test_resume_refuses_malformed_untagged_results(monkeypatch, tmp_path, result_id):
    workspace, _project = _resume_defaults(monkeypatch, tmp_path, results={"demo": result_id})

    with pytest.raises(RuntimeError, match="retained untagged release result.*malformed"):
        cli._assert_resume_candidate_is_safe_to_replay(
            tmp_path, workspace, ["demo"], {"demo": SimpleNamespace(prefix="demo-v", git_tag=False)},
            git_auth=cli.GitHubGitAuth("owner", "repo", "token"),
            release_policies={"demo": ("demo-v", False)},
        )


def test_resume_reports_when_an_untagged_source_result_cannot_be_resolved(monkeypatch, tmp_path):
    workspace, _project = _resume_defaults(monkeypatch, tmp_path, results={"demo": "source-" + "a" * 12})
    monkeypatch.setattr(
        cli, "run_local_git",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=1, stdout="", stderr=""),
    )

    with pytest.raises(RuntimeError, match="cannot resolve retained source result.*unknown Git error"):
        cli._assert_resume_candidate_is_safe_to_replay(
            tmp_path, workspace, ["demo"], {"demo": SimpleNamespace(prefix="demo-v", git_tag=False)},
            git_auth=cli.GitHubGitAuth("owner", "repo", "token"),
            release_policies={"demo": ("demo-v", False)},
        )


def test_resume_reports_an_indeterminate_origin_main_ancestry_check(monkeypatch, tmp_path):
    tag = "demo-v1.0.0"
    workspace, project = _resume_defaults(
        monkeypatch, tmp_path, results={"demo": tag},
        origin_tags={f"refs/tags/{tag}": "b" * 40},
    )
    monkeypatch.setattr(
        cli, "run_local_git",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=2, stdout="", stderr=""),
    )

    with pytest.raises(RuntimeError, match="cannot verify retained result.*unknown Git error"):
        cli._assert_resume_candidate_is_safe_to_replay(
            tmp_path, workspace, ["demo"], {"demo": project},
            git_auth=cli.GitHubGitAuth("owner", "repo", "token"),
        )


def test_resume_accepts_a_scope_with_no_pending_tags(monkeypatch, tmp_path):
    workspace, _project = _resume_defaults(monkeypatch, tmp_path)

    assert cli._assert_resume_candidate_is_safe_to_replay(
        tmp_path, workspace, ["demo"], {"demo": SimpleNamespace(prefix="demo-v", git_tag=False)},
        git_auth=cli.GitHubGitAuth("owner", "repo", "token"),
        release_policies={"demo": ("demo-v", False)},
    ) is None


def test_resume_checks_legacy_tagged_head_and_refuses_to_guess_after_git_failure(
    monkeypatch, tmp_path,
):
    tag = "demo-v1.0.0"
    workspace, project = _resume_defaults(
        monkeypatch, tmp_path, snapshot=None,
        origin_tags={f"refs/tags/{tag}": "b" * 40},
    )
    monkeypatch.setattr(
        cli, "run_local_git",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=1, stdout="", stderr=""),
    )
    with pytest.raises(RuntimeError, match="cannot identify retained candidate HEAD"):
        cli._assert_resume_candidate_is_safe_to_replay(
            tmp_path, workspace, ["demo"], {"demo": project},
            git_auth=cli.GitHubGitAuth("owner", "repo", "token"),
        )


def test_resume_accepts_a_completed_tag_only_when_its_source_is_on_origin_main(
    monkeypatch, tmp_path,
):
    tag = "demo-v1.0.0"
    workspace, project = _resume_defaults(
        monkeypatch, tmp_path, results={"demo": tag},
        origin_tags={f"refs/tags/{tag}": "b" * 40},
    )
    calls = []

    def git_result(_root, *args, **_kwargs):
        calls.append(args)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(cli, "run_local_git", git_result)

    assert cli._assert_resume_candidate_is_safe_to_replay(
        tmp_path, workspace, ["demo"], {"demo": project},
        git_auth=cli.GitHubGitAuth("owner", "repo", "token"),
    ) is None
    assert calls == [("merge-base", "--is-ancestor", "b" * 40, "c" * 40)]


def test_resume_accepts_a_valid_untagged_source_result_after_ancestry_check(
    monkeypatch, tmp_path,
):
    source_commit = "d" * 40
    workspace, project = _resume_defaults(
        monkeypatch, tmp_path, results={"demo": "source-" + source_commit[:12]},
    )
    git_calls = []

    def git_result(_root, *args, **_kwargs):
        git_calls.append(args)
        return SimpleNamespace(
            returncode=0,
            stdout=source_commit if args[0] == "rev-parse" else "",
            stderr="",
        )

    monkeypatch.setattr(cli, "run_local_git", git_result)

    assert cli._assert_resume_candidate_is_safe_to_replay(
        tmp_path, workspace, ["demo"],
        {"demo": SimpleNamespace(prefix="demo-v", git_tag=False)},
        git_auth=cli.GitHubGitAuth("owner", "repo", "token"),
        release_policies={"demo": ("demo-v", False)},
    ) is None
    assert git_calls == [
        ("rev-parse", "--verify", f"{source_commit[:12]}^{{commit}}"),
        ("merge-base", "--is-ancestor", source_commit, "c" * 40),
    ]

    def result_not_promoted(_root, *args, **_kwargs):
        if args[0] == "rev-parse":
            return SimpleNamespace(returncode=0, stdout=source_commit, stderr="")
        return SimpleNamespace(returncode=1, stdout="", stderr="")

    monkeypatch.setattr(cli, "run_local_git", result_not_promoted)
    with pytest.raises(RuntimeError, match="not in origin/main"):
        cli._assert_resume_candidate_is_safe_to_replay(
            tmp_path, workspace, ["demo"],
            {"demo": SimpleNamespace(prefix="demo-v", git_tag=False)},
            git_auth=cli.GitHubGitAuth("owner", "repo", "token"),
            release_policies={"demo": ("demo-v", False)},
        )


def test_resume_refuses_to_replay_when_origin_tags_changed_from_the_snapshot(
    monkeypatch, tmp_path,
):
    workspace, project = _resume_defaults(
        monkeypatch, tmp_path,
        snapshot={"refs/tags/demo-v1.0.0": "a" * 40},
        origin_tags={"refs/tags/demo-v1.0.0": "b" * 40},
    )

    with pytest.raises(RuntimeError, match="origin release tags for demo changed"):
        cli._assert_resume_candidate_is_safe_to_replay(
            tmp_path, workspace, ["demo"], {"demo": project},
            git_auth=cli.GitHubGitAuth("owner", "repo", "token"),
        )


def test_resume_refuses_an_attempted_tag_still_present_locally_or_remotely(
    monkeypatch, tmp_path,
):
    ref = "refs/tags/demo-v1.1.0"
    oid = "b" * 40
    workspace, project = _resume_defaults(
        monkeypatch, tmp_path, snapshot={}, attempts={ref: oid}, local_tags={ref: oid},
    )

    with pytest.raises(RuntimeError, match="still present or CMRU has no exact record"):
        cli._assert_resume_candidate_is_safe_to_replay(
            tmp_path, workspace, ["demo"], {"demo": project},
            git_auth=cli.GitHubGitAuth("owner", "repo", "token"),
        )


def test_resume_allows_a_legacy_candidate_when_no_current_tag_points_to_its_head(
    monkeypatch, tmp_path,
):
    workspace, project = _resume_defaults(
        monkeypatch, tmp_path, snapshot=None,
        origin_tags={"refs/tags/demo-v1.0.0": "b" * 40},
    )
    monkeypatch.setattr(
        cli, "run_local_git",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=0, stdout="c" * 40, stderr=""),
    )

    assert cli._assert_resume_candidate_is_safe_to_replay(
        tmp_path, workspace, ["demo"], {"demo": project},
        git_auth=cli.GitHubGitAuth("owner", "repo", "token"),
    ) is None

    monkeypatch.setattr(
        cli, "run_local_git",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=0, stdout="b" * 40, stderr=""),
    )
    with pytest.raises(RuntimeError, match="legacy retained candidate HEAD is tagged"):
        cli._assert_resume_candidate_is_safe_to_replay(
            tmp_path, workspace, ["demo"], {"demo": project},
            git_auth=cli.GitHubGitAuth("owner", "repo", "token"),
        )
