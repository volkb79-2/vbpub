"""The exact-tree proof: every tracked byte, mode, link and gitlink of a judged
snapshot must equal the commit the gate claims to have judged.

``git status`` is not an oracle (index flags hide edits), so
``_verify_tracked_files_match_commit`` hashes the working tree against the commit's
tree itself. These cases use a real linked worktree and real Git objects; only the
``ls-tree`` listing is replaced where Git can never emit the malformed record under
test.
"""
from __future__ import annotations

import os
import re
import shutil
from types import SimpleNamespace

import pytest

from cmru import gate_snapshot as gs
from tests.test_release_gate import _fixture, _git


def _head(repo) -> str:
    return _git(repo, "rev-parse", "HEAD")


def _verify(repo) -> None:
    gs._verify_tracked_files_match_commit(repo, _head(repo))


def _commit(repo, message: str, *paths: str) -> None:
    # Explicit paths: the fixture keeps untracked credential files in the tree.
    _git(repo, "add", *paths)
    _git(repo, "commit", "--quiet", "-m", message)


def test_an_unmodified_snapshot_matches_its_commit(tmp_path, monkeypatch):
    repo, *_rest = _fixture(tmp_path, monkeypatch)

    _verify(repo)  # does not raise


def _remove_parent(repo):
    shutil.rmtree(repo / "cmru")


def _parent_becomes_a_file(repo):
    shutil.rmtree(repo / "cmru")
    (repo / "cmru").write_text("not a directory\n", encoding="utf-8")


def _file_removed(repo):
    (repo / "README.md").unlink()


def _file_becomes_a_directory(repo):
    (repo / "README.md").unlink()
    (repo / "README.md").mkdir()


def _file_gains_the_executable_bit(repo):
    (repo / "README.md").chmod(0o755)


def _file_bytes_change(repo):
    (repo / "README.md").write_text("tampered fixture\n", encoding="utf-8")


@pytest.mark.parametrize(
    ("tamper", "refusal"),
    [
        (_remove_parent, "tracked snapshot path is missing: cmru/CHANGES.md"),
        (_parent_becomes_a_file, "tracked snapshot path has a non-directory parent: cmru/CHANGES.md"),
        (_file_removed, "tracked snapshot file is missing: README.md"),
        (_file_becomes_a_directory, "tracked snapshot file changed type: README.md"),
        (_file_gains_the_executable_bit, "tracked snapshot file mode changed: README.md"),
        (_file_bytes_change, "tracked snapshot file differs from exact commit"),
    ],
)
def test_a_tracked_file_that_no_longer_matches_the_commit_is_refused(
    tmp_path, monkeypatch, tamper, refusal,
):
    repo, *_rest = _fixture(tmp_path, monkeypatch)
    tamper(repo)

    with pytest.raises(RuntimeError, match=re.escape(refusal)):
        _verify(repo)


def test_a_tracked_symlink_is_compared_by_type_and_target(tmp_path, monkeypatch):
    repo, *_rest = _fixture(tmp_path, monkeypatch)
    link = repo / "link"
    os.symlink("README.md", link)
    _commit(repo, "add a tracked symlink", "link")
    _verify(repo)  # an intact link matches the target recorded in the commit

    link.unlink()
    os.symlink("cmru/CHANGES.md", link)  # retargeted
    with pytest.raises(RuntimeError, match=r"differs from exact commit .*: link"):
        _verify(repo)

    link.unlink()
    link.write_text("README.md", encoding="utf-8")  # same text, but no longer a link
    with pytest.raises(RuntimeError, match="tracked snapshot symlink changed type: link"):
        _verify(repo)


def _gitlink(repo, tmp_path) -> tuple:
    """Commit a gitlink ``vendor`` whose directory is a real nested repository."""
    nested = repo / "vendor"
    nested.mkdir()
    _git(nested, "init", "--quiet", "--initial-branch=main")
    (nested / "lib.py").write_text("VALUE = 1\n", encoding="utf-8")
    identity = ("-c", "user.name=Nested", "-c", "user.email=nested@example.invalid")
    _git(nested, *identity, "add", "lib.py")
    _git(nested, *identity, "commit", "--quiet", "-m", "nested")
    _git(
        repo, "update-index", "--add", "--cacheinfo", f"160000,{_head(nested)},vendor",
    )
    _git(repo, "commit", "--quiet", "-m", "record the gitlink")
    return nested, identity


def test_a_gitlink_must_still_name_the_nested_checkout_it_recorded(tmp_path, monkeypatch):
    repo, *_rest = _fixture(tmp_path, monkeypatch)
    nested, identity = _gitlink(repo, tmp_path)
    _verify(repo)  # the nested checkout is at the recorded commit

    (nested / "lib.py").write_text("VALUE = 2\n", encoding="utf-8")
    _git(nested, *identity, "commit", "--quiet", "-am", "move the nested checkout")
    with pytest.raises(RuntimeError, match="tracked snapshot submodule changed: vendor"):
        _verify(repo)


def test_a_gitlink_whose_checkout_cannot_be_inspected_is_refused(tmp_path, monkeypatch):
    repo, *_rest = _fixture(tmp_path, monkeypatch)
    nested, _identity = _gitlink(repo, tmp_path)
    shutil.rmtree(nested / ".git")
    (nested / ".git").write_text(f"gitdir: {tmp_path / 'nowhere'}\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="cannot inspect tracked snapshot submodule: vendor"):
        _verify(repo)


def _listing(monkeypatch, *records: bytes) -> None:
    """Replace only Git's ``ls-tree`` answer with records Git never emits."""
    payload = b"".join(record + b"\0" for record in records)
    monkeypatch.setattr(gs, "_git", lambda *_args, **_kwargs: SimpleNamespace(stdout=payload))


_OID = b"0" * 40


@pytest.mark.parametrize("record", [
    b"not a tree record at all",
    b"100644 blob\tREADME.md",
    b"100644 blob " + _OID + b" extra\tREADME.md",
])
def test_a_tree_record_that_cannot_be_parsed_is_refused(tmp_path, monkeypatch, record):
    repo, *_rest = _fixture(tmp_path, monkeypatch)
    _listing(monkeypatch, record)

    with pytest.raises(RuntimeError, match="cannot parse the exact release snapshot tree"):
        gs._verify_tracked_files_match_commit(repo, "0" * 40)


@pytest.mark.parametrize("unsafe", [b"../escape.txt", b"nested/../escape.txt", b"/etc/passwd", b""])
def test_a_tracked_path_that_leaves_the_snapshot_is_refused(tmp_path, monkeypatch, unsafe):
    repo, *_rest = _fixture(tmp_path, monkeypatch)
    _listing(monkeypatch, b"100644 blob " + _OID + b"\t" + unsafe)

    with pytest.raises(RuntimeError, match="release snapshot contains an unsafe tracked path"):
        gs._verify_tracked_files_match_commit(repo, "0" * 40)


@pytest.mark.parametrize("header", [b"100644 tree", b"120000 commit", b"100755 commit"])
def test_a_tree_entry_of_an_unsupported_kind_is_refused(tmp_path, monkeypatch, header):
    repo, *_rest = _fixture(tmp_path, monkeypatch)
    _listing(monkeypatch, header + b" " + _OID + b"\tREADME.md")

    kind = header.decode()
    with pytest.raises(RuntimeError, match=re.escape(f"unsupported tracked snapshot entry {kind} README.md")):
        gs._verify_tracked_files_match_commit(repo, "0" * 40)
