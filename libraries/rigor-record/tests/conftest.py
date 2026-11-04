import copy
import json
from pathlib import Path

import pytest

GOLDEN = Path(__file__).resolve().parents[1] / "golden"


def golden(relative: str):
    return json.loads((GOLDEN / relative).read_text(encoding="utf-8"))


def edit(document, *steps):
    """Deep-copy ``document`` and apply ``(path, value)`` steps; a path is a tuple of keys/indexes.

    A value of ``DELETE`` removes the key.
    """
    out = copy.deepcopy(document)
    for path, value in steps:
        node = out
        for key in path[:-1]:
            node = node[key]
        if value is DELETE:
            del node[path[-1]]
        else:
            node[path[-1]] = value
    return out


DELETE = object()


def messages(violations):
    return [str(v) for v in violations]


def has(violations, path, fragment):
    """True when some violation is at exactly ``path`` and its message contains ``fragment``."""
    return any(v.path == path and fragment in v.message for v in violations)


@pytest.fixture
def pf():
    return lambda name: golden(f"fragments/{name}.json")


@pytest.fixture
def rec():
    return lambda name: golden(f"records/{name}.json")


# -- RR-2 helpers ---------------------------------------------------------------------------------------------
import tomllib  # noqa: E402

ASSEMBLE = GOLDEN / "assemble"
COMMIT = "a" * 40
TREE = "b" * 40


class FakeGit:
    """Every tag resolves to COMMIT and every path at any commit has TREE, unless overridden."""

    def __init__(self, tag=COMMIT, trees=None, default=TREE, missing=(), published=True):
        self.tag, self.trees, self.default, self.missing = tag, trees or {}, default, set(missing)
        self.published_commits = published  # True: every commit is published; or a collection of commits

    def published(self, commit):
        return self.published_commits is True or commit in self.published_commits

    def tag_commit(self, tag):
        from rigor_record.gitfacts import GitError

        if self.tag is None:
            raise GitError("no such tag")
        return self.tag

    def tree(self, commit, path):
        from rigor_record.gitfacts import GitError

        if commit in self.missing:
            raise GitError("no such commit")
        return self.trees.get(commit, self.default)


PROJECT_COMMITS = [str(n) * 40 for n in range(1, 8)]
PROJECT_TREES = {commit: f"{n}f" * 20 for n, commit in enumerate(PROJECT_COMMITS[:6], start=1)}
PROJECT_TREES[PROJECT_COMMITS[6]] = PROJECT_TREES[PROJECT_COMMITS[5]]  # 7777.. shares 6666..'s tree


def project_git(**kwargs):
    """A git whose trees equal the ones the golden project's records were rendered against."""
    kwargs.setdefault("trees", dict(PROJECT_TREES))
    return FakeGit(**kwargs)


def fragment_bytes(name):
    return (ASSEMBLE / "fragments" / name).read_bytes()


def fragment_doc(name):
    return json.loads(fragment_bytes(name))


def dump(document):
    from rigor_record import canonical_json

    return canonical_json(document).encode()


def assemble_kwargs(**overrides):
    kwargs = dict(
        project="cgroup-profiler", tag="cgprofile-v2.0.0", commit=COMMIT, project_dir="cgroup-profiler",
        fragments=[(p.name, p.read_bytes()) for p in sorted((ASSEMBLE / "fragments").iterdir())],
        review=(ASSEMBLE / "review.md").read_bytes(),
        artifacts=json.loads((ASSEMBLE / "build.json").read_text()),
        waivers=tomllib.loads((ASSEMBLE / "waivers.toml").read_text()),
        published_at="2026-11-04T18:02:11Z", git=FakeGit(),
    )
    kwargs.update(overrides)
    return kwargs


def run_assemble(**overrides):
    from rigor_record.assemble import assemble

    return assemble(**assemble_kwargs(**overrides))


def refusal(**overrides):
    from rigor_record.assemble import Refusal

    with pytest.raises(Refusal) as info:
        run_assemble(**overrides)
    return info.value.problems


def any_problem(problems, fragment):
    return any(fragment in p for p in problems)


@pytest.fixture(autouse=True)
def git_identity(monkeypatch):
    """The write procedure commits with the checkout's configured identity; the test container has none."""
    for key, value in (("NAME", "t"), ("EMAIL", "t@example.invalid")):
        monkeypatch.setenv(f"GIT_AUTHOR_{key}", value)
        monkeypatch.setenv(f"GIT_COMMITTER_{key}", value)
