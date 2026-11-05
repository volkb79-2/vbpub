"""KI-118 / rigor C-3: ``project.version.exclude_paths`` and the always-ignored
``rigor/`` + ``RIGOR.md`` record paths in change detection and note collection."""
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from test_version_git import _TempRepo, _commit, _tag  # noqa: E402

from cmru import changelog, cli, config, exit_codes  # noqa: E402
from cmru.version import detect_changed_projects, project_change_excludes  # noqa: E402


def _project(cwd, excludes=(), paths=None, changelog_name=None):
    return SimpleNamespace(
        prefix="demo-v", cwd=cwd, paths=paths or [cwd], changelog=changelog_name,
        version=SimpleNamespace(
            strategy="scm", bump="conventional", paths=[], exclude_paths=tuple(excludes),
            base_version="1.0.0",
        ),
    )


def _names(repo, project):
    return [name for name, *_ in detect_changed_projects(repo, {"demo": project})]


def test_docs_only_commit_is_unchanged_with_exclude_paths():
    with _TempRepo() as repo:
        _commit(repo, "feat: initial", {"demo/src/a.py": "1", "demo/docs/a.md": "1"})
        _tag(repo, "demo-v0.1.0")
        project = _project("demo", excludes=["docs/"])
        _commit(repo, "docs: more words", {"demo/docs/deep/b.md": "2"})
        assert _names(repo, project) == []
        # same commit without the exclude counts as a change
        assert _names(repo, _project("demo")) == ["demo"]


def test_docs_plus_src_commit_is_changed_and_glob_excludes_work():
    with _TempRepo() as repo:
        _commit(repo, "feat: initial", {"demo/src/a.py": "1"})
        _tag(repo, "demo-v0.1.0")
        project = _project("demo", excludes=["docs", "notes/*.md"])
        _commit(repo, "docs: only", {"demo/notes/x.md": "1", "demo/docs/y.md": "1"})
        assert _names(repo, project) == []
        _commit(repo, "fix: real", {"demo/docs/z.md": "1", "demo/src/a.py": "2"})
        assert _names(repo, project) == ["demo"]


@pytest.mark.parametrize("cwd,record", [
    ("demo", "demo/rigor/releases/demo-v0.1.0.json"),
    ("demo", "demo/RIGOR.md"),
    ("demo", "demo/rigor/index.json"),
])
def test_rigor_record_commits_never_change_a_project(cwd, record):
    with _TempRepo() as repo:
        _commit(repo, "feat: initial", {"demo/src/a.py": "1"})
        _tag(repo, "demo-v0.1.0")
        _commit(repo, "chore(rigor): demo-v0.1.0 R2 pass", {record: "{}"})
        assert _names(repo, _project(cwd)) == []
        _commit(repo, "fix: real", {"demo/src/a.py": "2"})
        assert _names(repo, _project(cwd)) == ["demo"]


def test_repo_root_project_ignores_root_rigor_paths():
    with _TempRepo() as repo:
        _commit(repo, "feat: initial", {"src/a.py": "1"})
        _tag(repo, "demo-v0.1.0")
        _commit(repo, "chore(rigor): x", {"rigor/index.json": "{}", "RIGOR.md": "x"})
        project = _project(".", paths=["."])
        assert _names(repo, project) == []
        _commit(repo, "feat: product", {"src/a.py": "2"})
        assert _names(repo, project) == ["demo"]


def test_a_rigor_named_directory_deeper_in_the_tree_still_counts():
    with _TempRepo() as repo:
        _commit(repo, "feat: initial", {"demo/src/a.py": "1"})
        _tag(repo, "demo-v0.1.0")
        _commit(repo, "feat: source named rigor", {"demo/src/rigor/mod.py": "1"})
        assert _names(repo, _project("demo")) == ["demo"]


def test_project_change_excludes_shapes():
    assert project_change_excludes(SimpleNamespace(), ".") == ["rigor", "RIGOR.md"]
    assert project_change_excludes(_project("p", ["docs"]), "p") == [
        "p/rigor", "p/RIGOR.md", "p/docs",
    ]


def test_generated_notes_omit_excluded_commits():
    with _TempRepo() as repo:
        _commit(repo, "feat: initial", {"demo/src/a.py": "1"})
        _tag(repo, "demo-v0.1.0")
        _commit(repo, "docs: prose", {"demo/docs/a.md": "1"})
        _commit(repo, "chore(rigor): record", {"demo/rigor/index.json": "{}"})
        _commit(repo, "fix: real thing", {"demo/src/a.py": "2"})
        project = _project("demo", excludes=["docs"], changelog_name="CHANGES.md")
        exclusions = changelog._generated_exclusions(
            project, repo / "demo" / "CHANGES.md", repo,
        )
        groups = changelog._subject_groups(
            repo, "demo-v0.1.0", ["demo"], exclude_paths=exclusions,
        )
        subjects = [item for items in groups.values() for item in items]
        assert len(subjects) == 1 and "fix: real thing" in subjects[0]


def test_config_parses_and_validates_exclude_paths():
    parsed = config._parse_version(
        {"strategy": "scm", "bump": "patch", "paths": ["src"], "exclude_paths": ["docs/", "a/*.md"]},
        "demo",
    )
    assert parsed.exclude_paths == ["docs", "a/*.md"]
    assert config._parse_version({"strategy": "scm", "bump": "patch"}, "demo").exclude_paths == []
    for bad in (["../x"], ["/abs"], ["."], "docs", [""], ["src"], ["a"]):
        paths = ["src", "a/b"]
        with pytest.raises(SystemExit) as error:
            config._parse_version(
                {"strategy": "scm", "bump": "patch", "paths": paths, "exclude_paths": bad},
                "demo",
            )
        assert error.value.code == exit_codes.CONFIG_ERROR


def test_cli_version_spec_carries_exclude_paths():
    spec = cli._parse_version_spec({"strategy": "scm", "exclude_paths": ["docs/"]}, "demo")
    assert spec.exclude_paths == ("docs",)
    assert cli._parse_version_spec({"strategy": "scm"}, "demo").exclude_paths == ()
