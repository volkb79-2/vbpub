"""O1 -- ``GoAdapter.is_test_path``, adopted verbatim from
``covergate/main.go``'s own ``isGoTestFile``
(``strings.HasSuffix(p, "_test.go")``).

Unlike :class:`~assay.adapters.python.PythonAdapter`'s own ``tests/``
directory convention (dstdns's own ``(^|/)`` anchor hazard --
``python.py``'s module docstring), Go's suffix rule is already
boundary-safe on its own: ``"_test.go"`` is specific enough that no
ordinary source-file name ends with it by accident, so this module needs no
regex and no anchoring, and its own test suite is correspondingly about
proving the suffix is exactly what fires -- no shorter, no looser.
"""

from __future__ import annotations

from assay.adapters.go import GoAdapter

ADAPTER = GoAdapter()


def test_a_go_test_suffixed_file_is_a_test_path():
    assert ADAPTER.is_test_path("pkg/widgets_test.go") is True


def test_a_top_level_go_test_suffixed_file_is_a_test_path():
    assert ADAPTER.is_test_path("widgets_test.go") is True


def test_a_nested_directory_test_file_is_a_test_path():
    assert ADAPTER.is_test_path("pkg/sub/store_test.go") is True


def test_a_regular_source_file_is_not_a_test_path():
    assert ADAPTER.is_test_path("src/assay/evaluate.go") is False


def test_a_file_ending_in_test_go_without_the_underscore_is_not_a_test_path():
    """The real hazard for a suffix rule: ``"contest.go"`` genuinely ends
    in ``"test.go"`` (verified: ``"contest.go".endswith("test.go")`` is
    ``True``), but does NOT end in the full ``"_test.go"`` (the character
    before ``test.go`` is ``n``, not ``_``) -- proving the underscore is
    genuinely required, not merely decorative."""
    assert ADAPTER.is_test_path("pkg/contest.go") is False


def test_pythons_test_prefix_convention_is_deliberately_not_recognised_for_go():
    """``test_widgets.go`` (Python's own ``test_*.py`` convention) is NOT
    part of Go's convention -- Go's own tooling never recognises a
    ``test_``-prefixed filename as a test file, only a ``_test.go``-suffixed
    one. Recorded as a positive assertion so a future change that silently
    starts recognising it is caught."""
    assert ADAPTER.is_test_path("pkg/test_widgets.go") is False


def test_a_file_named_exactly_underscore_test_go_is_a_test_path():
    """An edge with no real-world Go package behind it (no importable
    package would name a file exactly this), kept as a defensive proof
    that the suffix check does not behave surprisingly with no filename
    stem before it."""
    assert ADAPTER.is_test_path("pkg/_test.go") is True
