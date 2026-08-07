"""O2 (A-099) — ``PythonAdapter.normalize_coverage_key``'s OWN prefix-strip
boundary safety. Never a retest of ``evaluate.py``'s already-proven,
adapter-independent source-root boundary matching (``_is_considered``'s
``Path.is_relative_to`` check, proven by
``test_evaluate_language_free.py::test_a_sibling_directory_sharing_the_...``
via ``FakeAdapter`` — this package cannot touch ``evaluate.py`` and does not
re-derive that property here). This module's mutation target lives entirely
inside ``python.py``'s own ``normalize_coverage_key``.

The fixture below is deliberately NOT a same-spelling no-op: the coverage
artifact's own key spelling (``"myapp/src/foo.py"``) and the diff's spelling
(``"src/foo.py"``) genuinely differ by one leading directory segment — the
real-world case a coverage run's own cwd sitting one directory below the
diff's own repo top produces (``python.py``'s own module docstring works the
concrete scenario through in full).
"""

from __future__ import annotations

from assay.adapters.python import PythonAdapter


def test_no_prefix_configured_returns_the_key_unchanged():
    adapter = PythonAdapter()
    assert adapter.normalize_coverage_key("src/foo.py") == "src/foo.py"


def test_a_configured_prefix_is_stripped_at_the_path_segment_boundary():
    adapter = PythonAdapter(coverage_key_prefix="myapp")
    assert adapter.normalize_coverage_key("myapp/src/foo.py") == "src/foo.py"


def test_a_sibling_prefixed_key_is_left_unchanged_not_mis_stripped():
    """The A-099 hazard, proven directly: ``myapp_legacy/`` is a DIFFERENT,
    unrelated project directory that merely shares the ``myapp`` prefix's
    characters. A naive ``key.removeprefix("myapp")`` (or
    ``key[len("myapp"):]`` gated only by ``str.startswith``) would produce
    the nonsensical ``"_legacy/src/foo.py"``. The correct, boundary-safe
    strip requires the prefix to be followed by an exact ``"/"`` path
    separator, so this key is returned untouched."""
    adapter = PythonAdapter(coverage_key_prefix="myapp")
    assert (
        adapter.normalize_coverage_key("myapp_legacy/src/foo.py")
        == "myapp_legacy/src/foo.py"
    )


def test_a_key_equal_to_the_bare_prefix_with_no_trailing_segment_is_unchanged():
    """An edge with no real-world file behind it (a key can never BE a bare
    directory name), kept as a defensive proof that the boundary check does
    not crash or behave surprisingly when there is no ``"/"`` to find at
    all."""
    adapter = PythonAdapter(coverage_key_prefix="myapp")
    assert adapter.normalize_coverage_key("myapp") == "myapp"


def test_a_key_with_an_unrelated_prefix_is_left_unchanged():
    adapter = PythonAdapter(coverage_key_prefix="myapp")
    assert (
        adapter.normalize_coverage_key("otherpkg/src/foo.py")
        == "otherpkg/src/foo.py"
    )
