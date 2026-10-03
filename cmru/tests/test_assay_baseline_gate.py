from __future__ import annotations

import json
import os
import subprocess
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
_SPEC = spec_from_file_location(
    "check_assay_baseline", ROOT / "tools" / "check_assay_baseline.py",
)
assert _SPEC is not None and _SPEC.loader is not None
check_assay_baseline = module_from_spec(_SPEC)
_SPEC.loader.exec_module(check_assay_baseline)
_PREP_SPEC = spec_from_file_location(
    "prepare_assay_baseline", ROOT / "tools" / "prepare_assay_baseline.py",
)
assert _PREP_SPEC is not None and _PREP_SPEC.loader is not None
prepare_assay_baseline = module_from_spec(_PREP_SPEC)
_PREP_SPEC.loader.exec_module(prepare_assay_baseline)


def _prepare_checker(monkeypatch, project: Path) -> None:
    from assay import git as assay_git

    monkeypatch.setattr(check_assay_baseline, "PROJECT_ROOT", project)
    facts = prepare_assay_baseline.build_facts(
        project.parent,
        project,
        assay_git=assay_git,
    )
    monkeypatch.setenv(
        "CMRU_ASSAY_BASELINE_FACTS",
        json.dumps(facts, separators=(",", ":"), sort_keys=True),
    )


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        check=True,
    )


def _add_origin(root: Path, *, tags: tuple[str, ...] = ()) -> None:
    origin = root.parent / f"{root.name}-origin.git"
    _git(root.parent, "init", "--quiet", "--bare", str(origin))
    _git(root, "remote", "add", "origin", str(origin))
    _git(root, "push", "--quiet", "origin", "main")
    for tag in tags:
        _git(root, "push", "--quiet", "origin", f"refs/tags/{tag}")


def _repository(root: Path, *, with_tag: bool) -> Path:
    _git(root, "init", "--quiet", "--initial-branch=main")
    _git(root, "config", "user.name", "CMRU test")
    _git(root, "config", "user.email", "cmru-test@example.invalid")
    project = root / "cmru"
    project.mkdir()
    (project / "assay.toml").write_text(
        '[lanes.cmru.judge]\nbase = "cmru-v1.0.0"\nsource_roots = ["src"]\n',
        encoding="utf-8",
    )
    source = project / "src" / "cmru" / "module.py"
    source.parent.mkdir(parents=True)
    source.write_text("first\n", encoding="utf-8")
    _git(root, "add", "cmru/assay.toml", "cmru/src/cmru/module.py")
    _git(root, "commit", "--quiet", "-m", "initial")
    if with_tag:
        _git(root, "tag", "cmru-v1.0.0")
        (project / "assay.toml").write_text(
            '[lanes.cmru.judge]\nbase = "cmru-v1.1.0"\nsource_roots = ["src"]\n',
            encoding="utf-8",
        )
        source.write_text("second\n", encoding="utf-8")
        _git(root, "commit", "--quiet", "-am", "later source")
        _git(root, "tag", "cmru-v1.1.0")
        (project / "docs.md").write_text("docs after previous release\n", encoding="utf-8")
        _git(root, "add", "cmru/docs.md")
        _git(root, "commit", "--quiet", "-m", "documentation after release")
        source.write_text("third\n", encoding="utf-8")
        _git(root, "commit", "--quiet", "-am", "latest source")

        # A higher-numbered tag outside HEAD's ancestry must not be selected.
        _git(root, "checkout", "--quiet", "--orphan", "unrelated")
        _git(root, "rm", "--quiet", "-rf", ".")
        (root / "unrelated.txt").write_text("unrelated\n", encoding="utf-8")
        _git(root, "add", "unrelated.txt")
        _git(root, "commit", "--quiet", "-m", "unrelated history")
        _git(root, "tag", "cmru-v9.0.0")
        _git(root, "checkout", "--quiet", "main")
    _add_origin(
        root,
        tags=("cmru-v1.0.0", "cmru-v1.1.0") if with_tag else (),
    )
    return project


def _merge_from(root: Path, *, first_parent: str) -> str:
    _git(root, "checkout", "--quiet", "-B", "merge-main", first_parent)
    _git(root, "branch", "merge-incoming", "cmru-v1.1.0")
    _git(root, "checkout", "--quiet", "merge-incoming")
    (root / "incoming.txt").write_text("incoming\n", encoding="utf-8")
    _git(root, "add", "incoming.txt")
    _git(root, "commit", "--quiet", "-m", "incoming change")
    _git(root, "checkout", "--quiet", "merge-main")
    _git(root, "merge", "--quiet", "--no-ff", "--no-edit", "merge-incoming")
    return _git(root, "rev-parse", "HEAD").stdout.strip()


def _commit_docs_only(root: Path, project: Path) -> None:
    source = project / "src" / "cmru" / "module.py"
    source.write_text("second\n", encoding="utf-8")
    (project / "docs.md").write_text("documentation-only change\n", encoding="utf-8")
    _git(root, "add", "cmru/src/cmru/module.py", "cmru/docs.md")
    _git(root, "commit", "--quiet", "-m", "documentation-only change")


def test_assay_baseline_checker_matches_tag_and_rejects_mismatch(
    monkeypatch, tmp_path, capsys,
):
    project = _repository(tmp_path, with_tag=True)
    _prepare_checker(monkeypatch, project)

    assert check_assay_baseline.main([]) == 0
    captured = capsys.readouterr()
    assert captured.out == "cmru-v1.1.0\n"
    assert "match nearest ancestor tag cmru-v1.1.0" in captured.err

    (project / "assay.toml").write_text(
        '[lanes.cmru.judge]\nbase = "cmru-v1.0.0"\nsource_roots = ["src"]\n',
        encoding="utf-8",
    )
    assert check_assay_baseline.main([]) == 1
    assert (
        "does not match nearest ancestor CMRU release tag 'cmru-v1.1.0'"
        in capsys.readouterr().err
    )


def test_remote_baseline_preparation_uses_scoped_cmru_git_auth_without_exporting_it(
    monkeypatch, tmp_path,
):
    from assay import git as assay_git
    from cmru import version
    from cmru.git_auth import GitHubGitAuth

    project = _repository(tmp_path, with_tag=True)
    auth = GitHubGitAuth("owner", "repo", "test-only-publisher-token")
    tag_commit = _git(tmp_path, "rev-parse", "refs/tags/cmru-v1.1.0^{commit}").stdout.strip()
    calls = []

    def highest_remote_tag(repo_root, prefix, tag_key, *, git_auth):
        assert repo_root == tmp_path
        assert prefix == "cmru-v"
        assert git_auth is auth
        return "cmru-v1.1.0"

    def remote_tag_commit(repo_root, tag, git_auth):
        calls.append((repo_root, tag, git_auth))
        return tag_commit

    monkeypatch.setattr(version, "_highest_remote_tag_for_prefix", highest_remote_tag)
    monkeypatch.setattr(prepare_assay_baseline, "_remote_tag_commit", remote_tag_commit)

    facts = prepare_assay_baseline.build_facts(
        tmp_path,
        project,
        git_auth=auth,
        assay_git=assay_git,
    )

    assert facts["schema_version"] == 2
    assert facts["published_latest"] == "cmru-v1.1.0"
    assert facts["head_release_tags"] == []
    assert facts["remote_tag_commits"] == {"cmru-v1.1.0": tag_commit}
    assert calls == [(tmp_path, "cmru-v1.1.0", auth)]
    assert "test-only-publisher-token" not in json.dumps(facts)


def test_assay_baseline_checker_uses_previous_tag_when_head_is_release_tagged(
    monkeypatch, tmp_path, capsys,
):
    project = _repository(tmp_path, with_tag=True)
    _git(tmp_path, "tag", "cmru-v1.2.0")
    _git(tmp_path, "push", "--quiet", "origin", "refs/tags/cmru-v1.2.0")
    _prepare_checker(monkeypatch, project)

    assert check_assay_baseline.main([]) == 0
    captured = capsys.readouterr()
    assert captured.out == "cmru-v1.1.0\n"
    assert "match nearest ancestor tag cmru-v1.1.0" in captured.err


def test_assay_baseline_checker_uses_previous_tag_from_second_parent_on_tagged_merge(
    monkeypatch, tmp_path, capsys,
):
    project = _repository(tmp_path, with_tag=True)

    _git(tmp_path, "checkout", "--quiet", "-B", "tagged-merge-first", "cmru-v1.0.0")
    (project / "assay.toml").write_text(
        '[lanes.cmru.judge]\nbase = "cmru-v1.1.0"\nsource_roots = ["src"]\n',
        encoding="utf-8",
    )
    (project / "src" / "cmru" / "module.py").write_text("second\n", encoding="utf-8")
    _git(tmp_path, "add", "cmru/assay.toml", "cmru/src/cmru/module.py")
    _git(tmp_path, "commit", "--quiet", "-m", "source-equivalent first parent")

    _git(tmp_path, "branch", "tagged-merge-incoming", "cmru-v1.1.0")
    _git(tmp_path, "checkout", "--quiet", "tagged-merge-incoming")
    (tmp_path / "incoming.txt").write_text("incoming\n", encoding="utf-8")
    _git(tmp_path, "add", "incoming.txt")
    _git(tmp_path, "commit", "--quiet", "-m", "incoming merge change")
    _git(tmp_path, "checkout", "--quiet", "tagged-merge-first")
    _git(tmp_path, "merge", "--quiet", "--no-ff", "--no-edit", "tagged-merge-incoming")

    _prepare_checker(monkeypatch, project)
    assert check_assay_baseline.main([]) == 0
    captured = capsys.readouterr()
    assert captured.out == "cmru-v1.1.0\n"
    assert "match nearest ancestor tag cmru-v1.1.0" in captured.err

    _git(tmp_path, "tag", "cmru-v1.2.0")
    _git(tmp_path, "push", "--quiet", "origin", "refs/tags/cmru-v1.2.0")
    _prepare_checker(monkeypatch, project)
    assert check_assay_baseline.main([]) == 0
    captured = capsys.readouterr()
    assert captured.out == "cmru-v1.1.0\n"
    assert "match nearest ancestor tag cmru-v1.1.0" in captured.err


def test_assay_baseline_checker_refuses_a_moved_older_tag_ahead_of_published_baseline(
    monkeypatch, tmp_path, capsys,
):
    project = _repository(tmp_path, with_tag=True)
    docs_commit = _git(tmp_path, "rev-parse", "HEAD^").stdout.strip()
    _git(tmp_path, "tag", "--force", "cmru-v1.0.0", docs_commit)
    (project / "assay.toml").write_text(
        '[lanes.cmru.judge]\nbase = "cmru-v1.0.0"\nsource_roots = ["src"]\n',
        encoding="utf-8",
    )
    _prepare_checker(monkeypatch, project)

    assert check_assay_baseline.main([]) == 1
    error = capsys.readouterr().err
    assert "selected ancestor tag 'cmru-v1.0.0'" in error
    assert "verified published baseline 'cmru-v1.1.0'" in error


def test_assay_baseline_checker_refuses_when_origin_has_a_newer_unmerged_release(
    monkeypatch, tmp_path, capsys,
):
    project = _repository(tmp_path, with_tag=True)
    _git(tmp_path, "push", "--quiet", "origin", "refs/tags/cmru-v9.0.0")
    _prepare_checker(monkeypatch, project)

    assert check_assay_baseline.main([]) == 1
    error = capsys.readouterr().err
    assert "selected ancestor tag 'cmru-v1.1.0'" in error
    assert "verified published baseline 'cmru-v9.0.0'" in error


def test_assay_baseline_checker_refuses_local_tag_commit_changed_from_origin(
    monkeypatch, tmp_path, capsys,
):
    project = _repository(tmp_path, with_tag=True)
    docs_commit = _git(tmp_path, "rev-parse", "HEAD^").stdout.strip()
    _git(tmp_path, "tag", "--force", "cmru-v1.1.0", docs_commit)
    _prepare_checker(monkeypatch, project)

    assert check_assay_baseline.main([]) == 1
    error = capsys.readouterr().err
    assert "selected tag 'cmru-v1.1.0' resolves locally" in error
    assert "origin publishes it at" in error


def test_assay_baseline_checker_refuses_older_tag_moved_to_head(
    monkeypatch, tmp_path, capsys,
):
    project = _repository(tmp_path, with_tag=True)
    _git(tmp_path, "tag", "--force", "cmru-v1.0.0", "HEAD")
    _prepare_checker(monkeypatch, project)

    assert check_assay_baseline.main([]) == 1
    error = capsys.readouterr().err
    assert "release tag at HEAD 'cmru-v1.0.0'" in error
    assert "origin publishes it at" in error


def test_assay_baseline_checker_checks_every_local_release_tag_at_head(
    monkeypatch, tmp_path, capsys,
):
    project = _repository(tmp_path, with_tag=True)
    _git(tmp_path, "tag", "--annotate", "cmru-v1.2.0", "--message", "published current release")
    _git(tmp_path, "push", "--quiet", "origin", "refs/tags/cmru-v1.2.0")
    _git(tmp_path, "tag", "--force", "cmru-v1.0.0", "HEAD")
    _prepare_checker(monkeypatch, project)
    facts = json.loads(os.environ["CMRU_ASSAY_BASELINE_FACTS"])
    assert facts["released_tag"] == "cmru-v1.2.0"
    assert facts["head_release_tags"] == ["cmru-v1.0.0", "cmru-v1.2.0"]

    assert check_assay_baseline.main([]) == 1
    error = capsys.readouterr().err
    assert "release tag at HEAD 'cmru-v1.0.0'" in error
    assert "origin publishes it at" in error


def test_assay_baseline_checker_skips_all_release_tags_at_head_for_previous_baseline(
    monkeypatch, tmp_path, capsys,
):
    project = _repository(tmp_path, with_tag=True)
    _git(
        tmp_path,
        "tag",
        "--annotate",
        "cmru-v1.2.0",
        "--message",
        "published current release",
    )
    _git(tmp_path, "push", "--quiet", "origin", "refs/tags/cmru-v1.2.0")
    _git(tmp_path, "tag", "--force", "cmru-v1.0.0", "HEAD")
    _git(tmp_path, "push", "--force", "--quiet", "origin", "refs/tags/cmru-v1.0.0")
    _prepare_checker(monkeypatch, project)
    facts = json.loads(os.environ["CMRU_ASSAY_BASELINE_FACTS"])

    assert facts["released_tag"] == "cmru-v1.2.0"
    assert facts["head_release_tags"] == ["cmru-v1.0.0", "cmru-v1.2.0"]
    assert facts["selected_tag"] == "cmru-v1.1.0"
    assert check_assay_baseline.main([]) == 0
    captured = capsys.readouterr()
    assert captured.out == "cmru-v1.1.0\n"
    assert "match nearest ancestor tag cmru-v1.1.0" in captured.err


def test_assay_baseline_checker_accepts_latest_lightweight_head_tag_when_describe_prefers_annotated(
    monkeypatch, tmp_path, capsys,
):
    project = _repository(tmp_path, with_tag=True)
    _git(
        tmp_path,
        "tag",
        "--annotate",
        "cmru-v1.2.0",
        "--message",
        "older annotated release at HEAD",
    )
    _git(tmp_path, "tag", "cmru-v1.3.0", "HEAD")
    _git(tmp_path, "push", "--quiet", "origin", "refs/tags/cmru-v1.2.0")
    _git(tmp_path, "push", "--quiet", "origin", "refs/tags/cmru-v1.3.0")
    _prepare_checker(monkeypatch, project)
    facts = json.loads(os.environ["CMRU_ASSAY_BASELINE_FACTS"])

    assert facts["released_tag"] == "cmru-v1.2.0"
    assert facts["published_latest"] == "cmru-v1.3.0"
    assert facts["head_release_tags"] == ["cmru-v1.2.0", "cmru-v1.3.0"]
    assert check_assay_baseline.main([]) == 0
    captured = capsys.readouterr()
    assert captured.out == "cmru-v1.1.0\n"
    assert "match nearest ancestor tag cmru-v1.1.0" in captured.err


def test_assay_baseline_checker_refuses_release_tagged_root_without_previous_tag(
    monkeypatch, tmp_path, capsys,
):
    project = _repository(tmp_path, with_tag=False)
    _git(tmp_path, "tag", "cmru-v1.0.0")
    monkeypatch.setattr(check_assay_baseline, "PROJECT_ROOT", project)

    assert check_assay_baseline.main([]) == 1
    error = capsys.readouterr().err
    assert "cannot resolve previous ancestor CMRU release tag" in error


def test_assay_baseline_checker_refuses_merge_whose_assay_first_parent_is_not_tag(
    monkeypatch, tmp_path, capsys,
):
    project = _repository(tmp_path, with_tag=True)
    _merge_from(tmp_path, first_parent="main")
    _prepare_checker(monkeypatch, project)

    assert check_assay_baseline.main([]) == 1
    error = capsys.readouterr().err
    assert "Assay's effective base" in error
    assert "(first parent)" in error
    assert "source paths differ" in error
    assert "cmru-v1.1.0" in error


def test_assay_baseline_checker_accepts_merge_whose_first_parent_is_tag(
    monkeypatch, tmp_path, capsys,
):
    project = _repository(tmp_path, with_tag=True)
    _merge_from(tmp_path, first_parent="cmru-v1.1.0")
    _prepare_checker(monkeypatch, project)

    assert check_assay_baseline.main([]) == 0
    captured = capsys.readouterr()
    assert captured.out == "cmru-v1.1.0\n"
    assert "first parent" not in captured.err


def test_assay_baseline_checker_accepts_merge_with_docs_only_first_parent_gap(
    monkeypatch, tmp_path, capsys,
):
    project = _repository(tmp_path, with_tag=True)
    docs_parent = _git(tmp_path, "rev-parse", "HEAD^").stdout.strip()
    _merge_from(tmp_path, first_parent=docs_parent)
    _prepare_checker(monkeypatch, project)

    assert check_assay_baseline.main([]) == 0
    captured = capsys.readouterr()
    assert captured.out == "cmru-v1.1.0\n"
    assert "source paths differ" not in captured.err


def test_assay_baseline_checker_does_not_skip_source_hidden_by_replace_ref(
    monkeypatch, tmp_path, capsys,
):
    project = _repository(tmp_path, with_tag=True)
    candidate = _git(tmp_path, "rev-parse", "HEAD").stdout.strip()
    tag_commit = _git(tmp_path, "rev-parse", "refs/tags/cmru-v1.1.0^{commit}").stdout.strip()
    monkeypatch.delenv("GIT_NO_REPLACE_OBJECTS", raising=False)
    _git(tmp_path, "replace", candidate, tag_commit)

    raw_diff = _git(
        tmp_path, "diff", "--name-only", "cmru-v1.1.0..HEAD", "--", "cmru/src",
    )
    assert raw_diff.stdout == ""
    _prepare_checker(monkeypatch, project)
    evidence = project / ".assay" / "mutation-cmru.json"

    assert check_assay_baseline.main(["--skip-evidence"]) == 0
    captured = capsys.readouterr()
    assert captured.out == "cmru-v1.1.0\n"
    assert not evidence.exists()


def test_assay_baseline_checker_records_explicit_skip_for_empty_source_diff(
    monkeypatch, tmp_path, capsys,
):
    project = _repository(tmp_path, with_tag=True)
    _commit_docs_only(tmp_path, project)
    _prepare_checker(monkeypatch, project)
    evidence = Path(".assay/mutation-cmru.json")

    assert check_assay_baseline.main(["--skip-evidence"]) == 0
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "explicit skip evidence" in captured.err
    assert json.loads((project / evidence).read_text(encoding="utf-8")) == {
        "status": "skipped",
        "reason": "no-changed-source",
        "base": "cmru-v1.1.0",
        "head": _git(tmp_path, "rev-parse", "HEAD").stdout.strip(),
    }

    source = project / "src" / "cmru" / "module.py"
    source.write_text("fourth\n", encoding="utf-8")
    _git(tmp_path, "add", "cmru/src/cmru/module.py")
    _git(tmp_path, "commit", "--quiet", "-m", "source change after skipped run")
    _prepare_checker(monkeypatch, project)
    assert check_assay_baseline.main(["--skip-evidence"]) == 0
    resumed = capsys.readouterr()
    assert resumed.out == "cmru-v1.1.0\n"
    assert "removed prior empty-source skip evidence" in resumed.err
    assert not (project / evidence).exists()


def test_assay_baseline_checker_refuses_symlink_skip_evidence(
    monkeypatch, tmp_path, capsys,
):
    project = _repository(tmp_path, with_tag=True)
    _commit_docs_only(tmp_path, project)
    _prepare_checker(monkeypatch, project)
    evidence = project / ".assay" / "mutation-cmru.json"
    evidence.parent.mkdir()
    preserved = evidence.parent / "preserved.json"
    preserved.write_text("keep this file\n", encoding="utf-8")
    evidence.symlink_to(preserved.name)

    assert check_assay_baseline.main(["--skip-evidence"]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "must not be a symlink" in captured.err
    assert preserved.read_text(encoding="utf-8") == "keep this file\n"
    assert evidence.is_symlink()


def test_assay_baseline_checker_refuses_symlink_evidence_parent_before_creating_target(
    monkeypatch, tmp_path, capsys,
):
    project = _repository(tmp_path, with_tag=True)
    _commit_docs_only(tmp_path, project)
    _prepare_checker(monkeypatch, project)
    outside = tmp_path / "outside"
    (project / ".assay").symlink_to(outside, target_is_directory=True)

    assert check_assay_baseline.main(["--skip-evidence"]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "parent must not be a symlink" in captured.err
    assert not outside.exists()


def test_assay_baseline_checker_preserves_active_mutation_evidence(
    monkeypatch, tmp_path, capsys,
):
    project = _repository(tmp_path, with_tag=True)
    _commit_docs_only(tmp_path, project)
    _prepare_checker(monkeypatch, project)
    evidence = project / ".assay" / "mutation-cmru.json"
    evidence.parent.mkdir()
    evidence.write_text('{"status": "running", "head": "candidate"}\n', encoding="utf-8")
    prior_record = evidence.read_text(encoding="utf-8")
    source = project / "src" / "cmru" / "module.py"
    source.write_text("fourth\n", encoding="utf-8")
    _git(tmp_path, "add", "cmru/src/cmru/module.py")
    _git(tmp_path, "commit", "--quiet", "-m", "source change after interrupted mutation")
    _prepare_checker(monkeypatch, project)

    assert check_assay_baseline.main(["--skip-evidence"]) == 0
    captured = capsys.readouterr()
    assert captured.out == "cmru-v1.1.0\n"
    assert "removed prior empty-source skip evidence" not in captured.err
    assert evidence.read_text(encoding="utf-8") == prior_record


def test_assay_baseline_checker_refuses_shadowed_declared_tag_with_empty_stdout(
    monkeypatch, tmp_path, capsys,
):
    project = _repository(tmp_path, with_tag=True)
    shadow_commit = _git(tmp_path, "rev-parse", "main").stdout.strip()
    tag_commit = _git(tmp_path, "rev-parse", "refs/tags/cmru-v1.1.0^{commit}").stdout.strip()
    _merge_from(tmp_path, first_parent="cmru-v1.1.0")
    _git(tmp_path, "update-ref", "refs/cmru-v1.1.0", shadow_commit)
    from assay import git as assay_git

    resolved_declared = assay_git.run(
        project, "rev-parse", "--verify", "--end-of-options", "cmru-v1.1.0^{commit}",
    ).strip()
    assert resolved_declared == shadow_commit
    assert resolved_declared != tag_commit
    _prepare_checker(monkeypatch, project)

    assert check_assay_baseline.main([]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "resolves to" in captured.err
    assert "selected tag commit" in captured.err


def test_assay_baseline_checker_refuses_when_no_release_tag_exists(
    monkeypatch, tmp_path, capsys,
):
    project = _repository(tmp_path, with_tag=False)
    monkeypatch.setattr(check_assay_baseline, "PROJECT_ROOT", project)

    assert check_assay_baseline.main([]) == 1
    error = capsys.readouterr().err
    assert "cannot resolve nearest ancestor CMRU release tag" in error


def test_assay_baseline_checker_refuses_missing_host_prepared_remote_facts(
    monkeypatch, tmp_path, capsys,
):
    project = _repository(tmp_path, with_tag=True)
    monkeypatch.setattr(check_assay_baseline, "PROJECT_ROOT", project)
    monkeypatch.delenv("CMRU_ASSAY_BASELINE_FACTS", raising=False)

    assert check_assay_baseline.main([]) == 1
    assert "host-prepared origin facts are missing" in capsys.readouterr().err


def test_assay_baseline_checker_refuses_remote_facts_from_another_head(
    monkeypatch, tmp_path, capsys,
):
    project = _repository(tmp_path, with_tag=True)
    _prepare_checker(monkeypatch, project)
    source = project / "src" / "cmru" / "module.py"
    source.write_text("fourth\n", encoding="utf-8")
    _git(tmp_path, "add", "cmru/src/cmru/module.py")
    _git(tmp_path, "commit", "--quiet", "-m", "advance after baseline preparation")

    assert check_assay_baseline.main([]) == 1
    assert "facts refer to a different HEAD" in capsys.readouterr().err


def test_assay_baseline_refusal_removes_stale_skip_evidence(
    monkeypatch, tmp_path, capsys,
):
    project = _repository(tmp_path, with_tag=True)
    evidence = project / ".assay" / "mutation-cmru.json"
    evidence.parent.mkdir()
    evidence.write_text(
        '{"status": "skipped", "reason": "no-changed-source", '
        '"base": "cmru-v1.1.0", "head": "old-candidate"}\n',
        encoding="utf-8",
    )
    (project / "assay.toml").write_text(
        '[lanes.cmru.judge]\nbase = "cmru-v1.0.0"\nsource_roots = ["src"]\n',
        encoding="utf-8",
    )
    _prepare_checker(monkeypatch, project)

    assert check_assay_baseline.main(["--skip-evidence"]) == 1
    error = capsys.readouterr().err
    assert "removed prior empty-source skip evidence" in error
    assert "does not match nearest ancestor CMRU release tag 'cmru-v1.1.0'" in error
    assert not evidence.exists()
