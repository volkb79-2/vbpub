"""O1 (P15) — ``parse_added_lines`` against text a REAL ``git diff
--unified=0`` produced, not hand-written diff text.

Every expected line map here is written independently of the parser under
test (by reading the fixture's own construction, never by calling
``parse_added_lines`` and trusting its answer) — the same discipline
``test_diff_added_lines.py`` already applies to hand-written fixtures,
extended here to real git output for exactly the shapes sol's post-series
review found broken: a genuinely added source line whose own content begins
``++`` (finding 3's first reproduction), and git's ``\\ No newline at end of
file`` marker advancing a side it must not (finding 3's second
reproduction) — plus renames, and paths containing a space, a tab, a
non-ASCII character, and a literal embedded newline (oracle O1's full list),
proven through :func:`assay.git.run` itself so this test also exercises that
``-c core.quotePath=false`` and this module's C-style unquoter compose
correctly, the same way :mod:`assay.runner` will actually call them.

Negative (implicit in every case here): restoring the old
``line.startswith("+++ ")``-anywhere heuristic misreads the ``++``-content
fixture's added line as a new file header and loses it; treating the
no-newline marker as an ordinary line shifts every line number after it.
"""

from __future__ import annotations

from assay.diff import parse_added_lines
from assay.git import run as git_diff_run


def _added_lines(repo, base: str, head: str) -> dict[str, frozenset[int]]:
    diff_text = git_diff_run(repo, "diff", "--unified=0", base, head)
    return dict(parse_added_lines(diff_text).by_file)


def test_an_added_line_whose_own_content_begins_plusplus_is_not_mistaken_for_a_header(
    git_repo,
):
    git_repo.write("a.py", "first\n")
    base = git_repo.commit_all("seed")
    git_repo.write("a.py", "first\n++ sentinel value\n")
    head = git_repo.commit_all("add a line starting with ++")

    assert _added_lines(git_repo.path, base, head) == {"a.py": frozenset({2})}


def test_the_no_newline_marker_advances_neither_side(git_repo):
    # Real git: appending a second (also newline-less) line to a file whose
    # single line previously had no trailing newline shows the FIRST line
    # as replaced too (its own newline-ness changed), so both new-side lines
    # are genuinely "added" per the diff's own literal shape -- independently
    # confirmed by reading the real diff bytes below, not by trusting the
    # parser.
    git_repo.write("b.py", "one")  # no trailing newline
    base = git_repo.commit_all("seed")
    git_repo.write("b.py", "one\ntwo")  # still no trailing newline
    head = git_repo.commit_all("append a line, still no eof newline")

    diff_text = git_diff_run(git_repo.path, "diff", "--unified=0", base, head)
    assert "\\ No newline at end of file" in diff_text  # the fixture is real
    assert diff_text.count("\\ No newline at end of file") == 2  # old AND new side

    assert parse_added_lines(diff_text).by_file == {"b.py": frozenset({1, 2})}


def test_a_rename_with_a_content_change_reports_only_the_new_path(git_repo):
    git_repo.write("sub/old_name.py", "x = 1\n")
    base = git_repo.commit_all("seed")
    git_repo.git("mv", "sub/old_name.py", "sub/new_name.py")
    git_repo.write("sub/new_name.py", "x = 1\ny = 2\n")
    head = git_repo.commit_all("rename and edit")

    result = _added_lines(git_repo.path, base, head)
    assert result == {"sub/new_name.py": frozenset({2})}
    assert "sub/old_name.py" not in result


def test_a_path_containing_a_space_is_recovered_exactly(git_repo):
    git_repo.write("with space.py", "x = 1\n")
    base = git_repo.commit_all("seed")
    git_repo.write("with space.py", "x = 1\ny = 2\n")
    head = git_repo.commit_all("edit the spaced file")

    assert _added_lines(git_repo.path, base, head) == {
        "with space.py": frozenset({2})
    }


def test_a_path_containing_a_tab_is_recovered_exactly(git_repo):
    tabbed = "with\ttab.py"
    git_repo.write(tabbed, "x = 1\n")
    base = git_repo.commit_all("seed")
    git_repo.write(tabbed, "x = 1\ny = 2\n")
    head = git_repo.commit_all("edit the tabbed file")

    assert _added_lines(git_repo.path, base, head) == {tabbed: frozenset({2})}


def test_a_path_containing_a_non_ascii_character_is_recovered_exactly(git_repo):
    git_repo.write("café.py", "x = 1\n")
    base = git_repo.commit_all("seed")
    git_repo.write("café.py", "x = 1\ny = 2\n")
    head = git_repo.commit_all("edit the unicode-named file")

    assert _added_lines(git_repo.path, base, head) == {"café.py": frozenset({2})}


def test_a_path_containing_an_embedded_newline_is_recovered_exactly(git_repo):
    weird = "weird\nname.py"
    git_repo.write(weird, "x = 1\n")
    base = git_repo.commit_all("seed")
    git_repo.write(weird, "x = 1\ny = 2\n")
    head = git_repo.commit_all("edit the newline-named file")

    assert _added_lines(git_repo.path, base, head) == {weird: frozenset({2})}


def test_multiple_real_hunks_in_one_file_do_not_leak_line_numbers(git_repo):
    git_repo.write("m.py", "\n".join(f"line{i}" for i in range(1, 6)) + "\n")
    base = git_repo.commit_all("seed")
    lines = [f"line{i}" for i in range(1, 6)]
    lines.insert(1, "inserted_near_top")
    lines.append("inserted_at_end")
    git_repo.write("m.py", "\n".join(lines) + "\n")
    head = git_repo.commit_all("two separate insertions")

    result = _added_lines(git_repo.path, base, head)
    assert result == {"m.py": frozenset({2, 7})}
