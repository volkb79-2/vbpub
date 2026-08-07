"""O1 — parse_added_lines returns only new-side added line numbers.

Against literal ``-U0`` diff-text fixtures: omitted hunk counts, explicit hunk
counts, paths with and without the ``a/``/``b/`` prefix, multiple hunks in one
file, multiple files, and a wider-context line all resolve to the correct
new-side line numbers. A pure-deletion hunk and a deleted file (``+++
/dev/null``) both contribute no changed lines at all.

Negative: changing the parser to advance (or record) on a ``-`` line makes the
pure-deletion and deleted-file fixtures report line numbers that were never
added — see this module's mutation evidence in the package LOG.
"""

from __future__ import annotations

import dataclasses

import pytest

from assay.diff import AddedLines, parse_added_lines


def test_single_hunk_with_omitted_counts_reports_the_one_added_line():
    # Omitted counts on BOTH sides default to 1 -- balanced against a real
    # one-line substitution (one "-" line, one "+" line), the only body shape
    # a hunk header with both counts implied can legally carry.
    diff_text = (
        "diff --git a/foo.py b/foo.py\n"
        "index 1111111..2222222 100644\n"
        "--- a/foo.py\n"
        "+++ b/foo.py\n"
        "@@ -10 +11 @@\n"
        "-old_line = 0\n"
        "+new_line = 1\n"
    )
    result = parse_added_lines(diff_text)
    assert result.by_file == {"foo.py": frozenset({11})}


def test_explicit_hunk_counts_and_multiple_added_lines():
    diff_text = (
        "diff --git a/bar.py b/bar.py\n"
        "--- a/bar.py\n"
        "+++ b/bar.py\n"
        "@@ -5,0 +6,3 @@\n"
        "+one\n"
        "+two\n"
        "+three\n"
    )
    result = parse_added_lines(diff_text)
    assert result.by_file == {"bar.py": frozenset({6, 7, 8})}


def test_path_without_a_b_prefix_no_prefix_diff():
    diff_text = (
        "diff --git foo.py foo.py\n"
        "--- foo.py\n"
        "+++ foo.py\n"
        "@@ -1,0 +1 @@\n"
        "+x = 1\n"
    )
    result = parse_added_lines(diff_text)
    assert result.by_file == {"foo.py": frozenset({1})}


def test_path_with_a_b_prefix_strips_only_the_b_side():
    diff_text = (
        "diff --git a/pkg/foo.py b/pkg/foo.py\n"
        "--- a/pkg/foo.py\n"
        "+++ b/pkg/foo.py\n"
        "@@ -1,0 +1 @@\n"
        "+x = 1\n"
    )
    result = parse_added_lines(diff_text)
    assert result.by_file == {"pkg/foo.py": frozenset({1})}


def test_pure_deletion_hunk_reports_no_changed_lines_for_that_file():
    diff_text = (
        "diff --git a/foo.py b/foo.py\n"
        "--- a/foo.py\n"
        "+++ b/foo.py\n"
        "@@ -3,2 +2,0 @@\n"
        "-old_one\n"
        "-old_two\n"
    )
    result = parse_added_lines(diff_text)
    assert result.by_file == {}


def test_deleted_file_reports_no_changed_lines():
    diff_text = (
        "diff --git a/gone.py b/gone.py\n"
        "deleted file mode 100644\n"
        "--- a/gone.py\n"
        "+++ /dev/null\n"
        "@@ -1,2 +0,0 @@\n"
        "-x = 1\n"
        "-y = 2\n"
    )
    result = parse_added_lines(diff_text)
    assert result.by_file == {}


def test_dev_null_new_side_skips_every_body_line_even_a_plus_line():
    # Contrived: a real git deletion diff never emits a '+' line under
    # `+++ /dev/null`, but the parser must not record one even if it saw it —
    # there is no new-side file for the line number to belong to.
    diff_text = "--- a/gone.py\n+++ /dev/null\n@@ -1 +0,0 @@\n-x = 1\n+not_real\n"
    result = parse_added_lines(diff_text)
    assert result.by_file == {}


def test_dev_null_new_side_skips_a_plus_line_even_mid_hunk():
    # Contrived, like the fixture above -- but unlike it, this hunk's own
    # counts keep it OPEN across the "+" line (old_count=2 needs a second
    # "-" line after it), so this specifically exercises the in-hunk
    # "current is None" branch rather than the hunk having already closed.
    diff_text = (
        "--- a/gone.py\n"
        "+++ /dev/null\n"
        "@@ -1,2 +0,1 @@\n"
        "-old_one\n"
        "+not_real\n"
        "-old_two\n"
    )
    result = parse_added_lines(diff_text)
    assert result.by_file == {}


def test_multiple_hunks_in_one_file_do_not_leak_line_numbers_across_hunks():
    diff_text = (
        "diff --git a/foo.py b/foo.py\n"
        "--- a/foo.py\n"
        "+++ b/foo.py\n"
        "@@ -1,0 +1 @@\n"
        "+first\n"
        "@@ -50,0 +50,2 @@\n"
        "+second\n"
        "+third\n"
    )
    result = parse_added_lines(diff_text)
    assert result.by_file == {"foo.py": frozenset({1, 50, 51})}


def test_multiple_files_are_kept_separate():
    diff_text = (
        "diff --git a/a.py b/a.py\n"
        "--- a/a.py\n"
        "+++ b/a.py\n"
        "@@ -1,0 +1 @@\n"
        "+in_a\n"
        "diff --git a/b.py b/b.py\n"
        "--- a/b.py\n"
        "+++ b/b.py\n"
        "@@ -5,0 +5 @@\n"
        "+in_b\n"
    )
    result = parse_added_lines(diff_text)
    assert result.by_file == {"a.py": frozenset({1}), "b.py": frozenset({5})}


def test_context_line_advances_the_new_side_counter_without_recording_it():
    # -U0 (every caller in this codebase) never emits a context line, but the
    # running counter must stay correct for a caller that passes wider
    # context — this is the one branch -U0 fixtures alone cannot exercise.
    diff_text = (
        "diff --git a/foo.py b/foo.py\n"
        "--- a/foo.py\n"
        "+++ b/foo.py\n"
        "@@ -1,3 +1,3 @@\n"
        " context_before\n"
        "-old\n"
        "+new\n"
        " context_after\n"
    )
    result = parse_added_lines(diff_text)
    assert result.by_file == {"foo.py": frozenset({2})}


def test_empty_diff_text_reports_no_files():
    result = parse_added_lines("")
    assert result.by_file == {}


def test_returns_a_frozen_kw_only_added_lines_with_immutable_contents():
    result = parse_added_lines("--- a/x.py\n+++ b/x.py\n@@ -1,0 +1 @@\n+y\n")

    assert isinstance(result, AddedLines)
    assert result.by_file == {"x.py": frozenset({1})}

    with pytest.raises(dataclasses.FrozenInstanceError):
        result.by_file = {}  # type: ignore[misc]

    with pytest.raises(TypeError):
        AddedLines({"x.py": frozenset({1})})  # type: ignore[misc]  # kw_only

    with pytest.raises(TypeError):
        result.by_file["x.py"] = frozenset()  # MappingProxyType is read-only
