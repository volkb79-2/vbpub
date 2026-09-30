"""O1 — ``PythonAdapter.is_test_path``, adopted from dstdns's
``_TEST_FILE_RE`` verbatim (the sole holder among the three cited reference
gates; topos and nyxloom have no equivalent rule at all — confirmed by
reading both files in full, see ``python.py``'s own module docstring).

Each test below asserts one construct family from the rule's own three
branches (``tests/`` at any depth, a ``test_*.py`` filename, an exact
``conftest.py`` filename), paired with a SIBLING fixture that shares just
enough spelling to trip a naive substring check but must not match — the
same boundary discipline P05's own core proves one layer up for source
roots (``pkg`` vs ``pkg_evil``), reproduced here for this adapter's own,
different string-matching rule (never a retest of that already-proven
mechanism).
"""

from __future__ import annotations

from assay.adapters.python import PythonAdapter

ADAPTER = PythonAdapter()


def test_a_top_level_tests_directory_file_is_a_test_path():
    assert ADAPTER.is_test_path("tests/test_foo.py") is True


def test_a_nested_tests_directory_file_is_a_test_path():
    assert ADAPTER.is_test_path("pkg/tests/sub/mod.py") is True


def test_a_sibling_directory_merely_ending_in_tests_is_not_mismatched():
    """``mytests/`` is a real hazard, not a hypothetical one: the BARE
    substring ``"tests/"`` genuinely occurs inside ``"mytests/foo.py"``
    (verified: ``"tests/" in "mytests/foo.py"`` is ``True``), so an
    unanchored substring search WOULD wrongly classify it as a test path.
    The ``(^|/)`` anchor on the regex's own ``tests/`` branch is what
    requires ``tests`` to be its own path SEGMENT, not merely a trailing
    fragment of a longer segment name."""
    assert ADAPTER.is_test_path("mytests/foo.py") is False


def test_a_directory_sharing_the_tests_name_as_a_prefix_is_not_mismatched():
    """``tests_data/`` shares ``tests``' own leading letters but is a
    different segment name entirely -- correctly not a test path (and,
    unlike ``mytests/`` above, not even reachable by a bare substring
    search, since ``"tests/"`` never literally occurs inside
    ``"tests_data/"``)."""
    assert ADAPTER.is_test_path("pkg/tests_data/fixture.py") is False


def test_a_test_prefixed_filename_is_a_test_path():
    assert ADAPTER.is_test_path("pkg/test_widgets.py") is True


def test_a_filename_merely_containing_the_test_prefix_mid_word_is_not_mismatched():
    """``mytest_foo.py`` is the real hazard for this branch: the bare
    substring ``"test_"`` genuinely occurs inside it (verified:
    ``"test_" in "mytest_foo.py"`` is ``True``), so an unanchored search
    would wrongly flag a legitimately-named source module. The full
    ``(^|/)test_[^/]*\\.py$`` requires the FILENAME ITSELF to start with
    ``test_``, not merely contain it."""
    assert ADAPTER.is_test_path("pkg/mytest_foo.py") is False


def test_a_filename_merely_starting_with_test_is_not_mismatched():
    """``testing_helpers.py`` starts with ``test`` but not ``test_`` -- must
    not match, or every ``testing*``/``testament*``-named module in the
    codebase would wrongly lose its coverage obligation."""
    assert ADAPTER.is_test_path("pkg/testing_helpers.py") is False


def test_a_conftest_file_is_a_test_path():
    assert ADAPTER.is_test_path("pkg/sub/conftest.py") is True


def test_a_filename_merely_containing_conftest_is_not_mismatched():
    """A real hazard: the bare substring ``"conftest.py"`` genuinely occurs
    inside ``"myconftest.py"`` (verified). The full
    ``(^|/)conftest\\.py$`` requires the filename to be EXACTLY
    ``conftest.py``, not merely end with it."""
    assert ADAPTER.is_test_path("pkg/myconftest.py") is False


def test_a_regular_source_file_is_not_a_test_path():
    assert ADAPTER.is_test_path("src/assay/evaluate.py") is False


def test_a_suffix_style_test_filename_is_deliberately_not_recognised():
    """``foo_test.py`` (the suffix convention some Python projects use) is
    NOT part of the union: none of the three cited reference gates
    recognises it, only ``test_*.py``-prefix and ``tests/``/``conftest.py``.
    Recorded here as a positive assertion (not merely absence of a test) so
    a future change that silently starts recognising it is caught, and so
    this known scope boundary is visible rather than merely undocumented."""
    assert ADAPTER.is_test_path("pkg/widgets_test.py") is False
