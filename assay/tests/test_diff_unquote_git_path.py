"""O1 (P15) — ``_unquote_git_path`` reverses git's C-style path quoting.

White-box, direct tests of the helper itself: the real-git-fixture tests in
``test_diff_real_git_fixtures.py`` prove the cases a real git binary actually
produces (named escapes for tab/newline, raw non-ASCII, the space-only
trailing-tab marker); the escape grammar's remaining shapes — an octal
escape for a control byte with no one-letter name, a truncated escape at the
end of the string, and a backslash git never emits — can only be reached by
constructing the input directly, the same way malformed-JSON coverage
fixtures test ``coverage_py_json.py`` directly rather than only through a
real coverage tool.
"""

from __future__ import annotations

import pytest

from assay.diff import _unquote_git_path


def test_a_plain_unquoted_path_with_nothing_unusual_passes_through_verbatim():
    assert _unquote_git_path("b/pkg/mod.py") == "b/pkg/mod.py"


def test_an_unquoted_path_with_a_trailing_tab_has_the_marker_tab_stripped():
    # git's own disambiguation marker for a SPACE-containing unquoted path
    # (verified empirically against a real git binary — see
    # test_diff_real_git_fixtures.py's space fixture); a real trailing tab
    # in the path could never reach here unquoted, since a tab is a control
    # byte and always forces full quoting instead.
    assert _unquote_git_path("b/with space.py\t") == "b/with space.py"


def test_a_quoted_path_with_named_escapes_decodes_to_the_real_bytes():
    assert _unquote_git_path('"b/with\\ttab.py"') == "b/with\ttab.py"
    assert _unquote_git_path('"b/weird\\nname.py"') == "b/weird\nname.py"
    assert _unquote_git_path('"b/say \\"hi\\".py"') == 'b/say "hi".py'
    assert _unquote_git_path('"b/back\\\\slash.py"') == "b/back\\slash.py"


def test_a_quoted_path_that_also_contains_a_space_loses_only_the_marker_tab():
    # Controller repair (A-134): git appends its space-disambiguation tab to
    # the printed LINE, so on a path that some other byte already forced into
    # quoted form the tab lands AFTER the closing quote. Testing the marker
    # against `spelled[-1] != '"'` therefore has to happen the other way
    # round -- strip, then ask whether what remains is quoted.
    assert _unquote_git_path('"b/say \\"hi\\" here.py"\t') == 'b/say "hi" here.py'
    assert _unquote_git_path('"b/weird\\nname with space.py"\t') == (
        "b/weird\nname with space.py"
    )


def test_a_quoted_path_with_an_octal_escape_decodes_the_named_byte():
    # 0x01 (SOH) has no one-letter C escape name, so git spells it \001.
    assert _unquote_git_path('"b/ctrl\\001byte.py"') == "b/ctrl\x01byte.py"


def test_a_quoted_path_with_a_truncated_escape_is_rejected():
    # A lone trailing backslash immediately before the closing quote -- no
    # byte follows it to name an escape.
    with pytest.raises(ValueError, match="truncated escape"):
        _unquote_git_path('"b/broken\\"')


def test_a_quoted_path_with_an_unrecognised_escape_is_rejected():
    with pytest.raises(ValueError, match="unrecognised escape"):
        _unquote_git_path('"b/bad\\qescape.py"')
