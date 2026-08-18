"""O1 -- ``SqlAdapter.is_test_path`` (carve §3.3): ``True`` for a path with a
``tests/`` or ``test/`` segment at any depth, or a basename matching
``test_*.sql`` / ``*_test.sql``.

Each construct family gets its own SIBLING hazard fixture that shares just
enough spelling to trip a naive substring check but must not match --
mirroring ``test_adapters_python_test_path.py``'s own boundary discipline,
reproduced here for this adapter's own (wider -- BOTH ``tests/`` and
``test/`` are recognised segments, unlike Python's ``tests/``-only rule)
string-matching rule.
"""

from __future__ import annotations

from assay.adapters.sql import SqlAdapter

ADAPTER = SqlAdapter()


def test_a_top_level_tests_directory_file_is_a_test_path():
    assert ADAPTER.is_test_path("tests/fixture.sql") is True


def test_a_nested_tests_directory_file_is_a_test_path():
    assert ADAPTER.is_test_path("db/tests/sub/fixture.sql") is True


def test_a_singular_test_directory_is_also_a_test_path():
    """SQL's own rule is deliberately wider than Python's: dstdns-shaped
    layouts are not consistent about ``tests/`` vs ``test/``, so both are
    recognised segments (unlike ``PythonAdapter``, which only recognises
    ``tests/``)."""
    assert ADAPTER.is_test_path("db/test/sub/fixture.sql") is True


def test_a_sibling_directory_merely_ending_in_tests_is_not_mismatched():
    """A real hazard: the bare substring ``"tests/"`` genuinely occurs
    inside ``"mytests/foo.sql"``, so an unanchored search would wrongly
    classify it as a test path."""
    assert ADAPTER.is_test_path("mytests/foo.sql") is False


def test_a_sibling_directory_merely_ending_in_test_is_not_mismatched():
    assert ADAPTER.is_test_path("mytest/foo.sql") is False


def test_a_directory_sharing_the_tests_name_as_a_prefix_is_not_mismatched():
    assert ADAPTER.is_test_path("db/tests_data/fixture.sql") is False


def test_a_test_prefixed_filename_is_a_test_path():
    assert ADAPTER.is_test_path("db/test_widgets.sql") is True


def test_a_filename_merely_containing_the_test_prefix_mid_word_is_not_mismatched():
    """``mytest_foo.sql`` genuinely contains the substring ``"test_"``, so
    an unanchored search would wrongly flag a legitimately-named source
    file."""
    assert ADAPTER.is_test_path("db/mytest_foo.sql") is False


def test_a_test_suffixed_filename_is_a_test_path():
    assert ADAPTER.is_test_path("db/schema_test.sql") is True


def test_a_filename_merely_containing_the_test_suffix_mid_word_is_not_mismatched():
    """``schema_testing.sql`` genuinely contains ``"_test"`` but does not
    itself END in ``_test.sql``."""
    assert ADAPTER.is_test_path("db/schema_testing.sql") is False


def test_a_regular_source_file_is_not_a_test_path():
    assert ADAPTER.is_test_path("infra/db-init/init-scripts/20-create-corpora.sql") is False
