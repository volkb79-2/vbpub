import subprocess

import pytest

from rigor_record.gitfacts import GitError, RepoGit


def git(repo, *args):
    env = {
        "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid", "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@example.invalid", "HOME": str(repo), "PATH": __import__("os").environ["PATH"],
        "GIT_CONFIG_NOSYSTEM": "1",
    }
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True, env=env).stdout.strip()


@pytest.fixture
def repo(tmp_path):
    git(tmp_path, "init", "-q", "-b", "main")
    (tmp_path / "proj").mkdir()
    (tmp_path / "proj" / "a.txt").write_text("a")
    git(tmp_path, "add", "-A")
    git(tmp_path, "-c", "commit.gpgsign=false", "commit", "-q", "-m", "one")
    git(tmp_path, "-c", "tag.gpgsign=false", "tag", "-a", "-m", "release", "p-v1.0.0")
    return tmp_path


def test_tag_commit_peels_an_annotated_tag(repo):
    assert RepoGit(repo).tag_commit("p-v1.0.0") == git(repo, "rev-parse", "HEAD")


def test_tree_of_a_subdirectory_and_of_the_root(repo):
    head = git(repo, "rev-parse", "HEAD")
    facts = RepoGit(repo)
    assert facts.tree(head, "proj") == git(repo, "rev-parse", "HEAD:proj")
    assert facts.tree(head, ".") == git(repo, "rev-parse", "HEAD^{tree}")


def commit_more(repo, name):
    (repo / "proj" / name).write_text(name)
    git(repo, "add", "-A")
    git(repo, "-c", "commit.gpgsign=false", "commit", "-q", "-m", name)
    return git(repo, "rev-parse", "HEAD")


def test_published_needs_origin_evidence_not_a_local_tag(repo, tmp_path):
    head = git(repo, "rev-parse", "HEAD")
    facts = RepoGit(repo)
    assert not facts.published("-bad") and not facts.published("abc")  # not an object id: refused, never passed to git
    assert not facts.published(head)  # no origin at all: fail closed, although the LOCAL tag p-v1.0.0 contains it
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", str(origin)], check=True)
    git(repo, "remote", "add", "origin", str(origin))
    assert not facts.published(head)  # origin knows nothing yet
    git(repo, "push", "-q", "origin", "p-v1.0.0")  # an annotated tag on origin: it peels to the commit
    assert facts.published(head)
    later = commit_more(repo, "later")
    git(repo, "-c", "tag.gpgsign=false", "tag", "p-v2.0.0")  # a second local tag, never pushed
    assert not facts.published(later)
    git(repo, "-c", "tag.gpgsign=false", "tag", "light")  # lightweight, pushed
    git(repo, "push", "-q", "origin", "light")
    assert facts.published(later)
    side = commit_more(repo, "side")  # not under any tag and not on origin/main
    assert not facts.published(side)
    git(repo, "push", "-q", "origin", "main")
    git(repo, "fetch", "-q", "origin")
    assert facts.published(side)  # now reachable from refs/remotes/origin/main


def test_unknown_things_raise(repo):
    facts = RepoGit(repo)
    with pytest.raises(GitError, match="cannot resolve"):
        facts.tag_commit("p-v9.9.9")
    with pytest.raises(GitError):
        facts.tree("0" * 40, "proj")
    with pytest.raises(GitError):
        facts.tree(git(repo, "rev-parse", "HEAD"), "missing")
