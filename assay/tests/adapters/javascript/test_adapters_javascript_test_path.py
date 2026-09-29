"""B036 — ``JavaScriptAdapter.is_test_path``, the union of Vitest's own
default ``include`` glob (``**/*.{test,spec}.?(c|m)[jt]s?(x)``) and Jest's
``__tests__`` directory convention, and NOTHING invented beyond them
(DESIGN-GUIDE §5).

Negative: dropping the ``(^|/)`` anchor on the directory branch matches a
sibling directory that merely CONTAINS the characters
(``app__tests__helpers/``); dropping the required ``.`` before
``test``/``spec`` matches ordinary source (``latest.ts``, ``respec.ts``);
matching a test path at all makes a real test file's own changed lines count
toward the numerator and denominator of changed-line coverage, which every
cited sibling gate's ``_is_test_path`` skip exists to prevent.
"""

from __future__ import annotations

import pytest

from assay.adapters.javascript import JavaScriptAdapter

ADAPTER = JavaScriptAdapter()


@pytest.mark.parametrize(
    "path",
    [
        # Vitest's own default include glob, all four base extensions...
        "src/roles.test.ts",
        "src/Badge.test.tsx",
        "src/util.test.js",
        "src/Widget.test.jsx",
        "src/roles.spec.ts",
        "src/Badge.spec.tsx",
        "src/util.spec.js",
        "src/Widget.spec.jsx",
        # ...and the ESM/CJS spellings that glob also accepts.
        "src/util.test.mjs",
        "src/util.spec.cjs",
        "src/util.test.mts",
        "src/util.spec.cts",
        # Jest's `__tests__` directory convention, at any depth, holding a
        # file with no test-ish name of its own.
        "__tests__/helpers.ts",
        "src/__tests__/roles.ts",
        "apps/ui/src/components/__tests__/Badge.tsx",
        # Both conventions at once.
        "src/__tests__/roles.test.ts",
    ],
)
def test_a_vitest_test_path_is_recognised(path: str):
    assert ADAPTER.is_test_path(path) is True


@pytest.mark.parametrize(
    "path",
    [
        # Ordinary source, including names that a substring match would
        # wrongly claim.
        "src/roles.ts",
        "src/Badge.tsx",
        "src/latest.ts",
        "src/respec.ts",
        "src/testing.ts",
        "src/spectrum.tsx",
        "src/protest.js",
        # `.test`/`.spec` must be the segment BEFORE the extension, not
        # anywhere in the name.
        "src/test.helpers.ts",
        "src/spec.builder.ts",
        # The directory rule is anchored on a whole path segment.
        "src/app__tests__helpers/Badge.tsx",
        "src/my__tests__/Badge.tsx",
        "__tests__extra/Badge.tsx",
        # A plain `tests/` directory is NOT a convention this adapter can
        # cite for JS/TS, so it is deliberately not claimed (DESIGN-GUIDE §5:
        # never invent a fact no source supplies). Documented in the module
        # docstring, and pinned here so a later change is a deliberate one.
        "tests/roles.ts",
        "src/tests/roles.ts",
    ],
)
def test_ordinary_source_is_not_a_test_path(path: str):
    assert ADAPTER.is_test_path(path) is False


def test_a_declaration_file_is_not_a_test_path():
    """A ``.d.ts`` file is this adapter's source with no executable content
    (``has_executable_code``'s job), never a test file -- two different facts
    that must not be collapsed."""
    assert ADAPTER.is_test_path("src/types.d.ts") is False
    assert ADAPTER.is_test_path("src/types.test.d.ts") is False
