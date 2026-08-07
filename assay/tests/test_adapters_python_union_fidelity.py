"""O1 (end-to-end) and O2 (end-to-end) — the real ``PythonAdapter`` plugged
into ``evaluate_coverage`` (unmodified, P05's own function), proving the
adapter WIRES correctly into the real pipeline for realistic multi-construct
Python source and for a genuinely differing coverage-key spelling, not just
in isolated unit calls.

The four-way union itself (executable requires execution, excluded fails
unless ``allow_excluded``, non-executable is silently ignored, outside-diff
never enters the computation) is ``evaluate.py``'s own already-proven
contract (P05) — these tests do not re-litigate it. What they add is:
committed, hand-derived (never adapter-generated) expected
executed/missing/excluded sets for a SINGLE file that genuinely exercises
every construct family A-098 names, run through the REAL adapter's
``is_test_path``/``normalize_coverage_key`` (never a ``FakeAdapter`` stand-
in), proving nothing about the real Python adapter's own glob/test-path/key
logic silently disqualifies or misroutes any of them.
"""

from __future__ import annotations

from pathlib import Path
from types import MappingProxyType

from assay.adapters.python import PythonAdapter
from assay.coverage_parsers.model import CoverageProfile, FileCoverage
from assay.diff import AddedLines
from assay.errors import Outcome, ReasonCode
from assay.evaluate import evaluate_coverage

REPO_TOP = Path("/repo")

# --- the O1 fixture: every construct family on its own single line ---
#
#  1  """Module docstring, not tracked."""
#  2  # a stray top-level comment, also not tracked
#  3  (blank)
#  4  import os
#  5  (blank)
#  6  (blank)
#  7  @staticmethod
#  8  def helper():
#  9      return os.getpid()
# 10  (blank)
# 11  (blank)
# 12  if True:
# 13      pass
# 14  (blank)
# 15  (blank)
# 16  async def fetch():
# 17      return 1  # pragma: no cover
# 18  (blank)
# 19  (blank)
# 20  class Thing:
# 21      """Class docstring, not tracked."""
# 22  (blank)
# 23      def method(self):
# 24          pass
#
# Independently reasoned (never adapter-generated) real coverage.py
# semantics for this file:
#   * L1/L2/L21 (docstrings/comment) -- coverage.py never traces these at
#     all; absent from executed/missing/excluded (rule 4).
#   * L4, L7, L8, L12, L16, L20, L23 -- module- and class-body top-level
#     statements (an import, a decorator expression, a def/async-def/class/
#     if header) all execute unconditionally the moment the module is
#     imported, regardless of whether anything defined in it is later
#     CALLED -- EXECUTED.
#   * L13 -- inside `if True:` at module level, unconditionally entered at
#     import time -- EXECUTED.
#   * L9 -- `helper`'s own body; only runs if `helper()` is actually
#     CALLED. This fixture models a function defined but never invoked --
#     MISSING.
#   * L24 -- `Thing.method`'s own body; this fixture models the method
#     actually being called on an instance elsewhere -- EXECUTED.
#   * L17 -- carries `# pragma: no cover`; EXCLUDED, regardless of whether
#     `fetch()` was ever awaited -- exclusion is a static, not a dynamic,
#     property of the line.
SOURCE = (
    '"""Module docstring, not tracked."""\n'
    "# a stray top-level comment, also not tracked\n"
    "\n"
    "import os\n"
    "\n"
    "\n"
    "@staticmethod\n"
    "def helper():\n"
    "    return os.getpid()\n"
    "\n"
    "\n"
    "if True:\n"
    "    pass\n"
    "\n"
    "\n"
    "async def fetch():\n"
    "    return 1  # pragma: no cover\n"
    "\n"
    "\n"
    "class Thing:\n"
    '    """Class docstring, not tracked."""\n'
    "\n"
    "    def method(self):\n"
    "        pass\n"
)
assert SOURCE.count("\n") == 24, "fixture must have exactly 24 physical lines"

_EXECUTED = frozenset({4, 7, 8, 12, 13, 16, 20, 23, 24})
_MISSING = frozenset({9})
_EXCLUDED = frozenset({17})
_ALL_LINES = frozenset(range(1, 25))


def _evaluate(added, profile, adapter, *, allow_excluded, fail_under=100.0):
    def read_source_text(path: str) -> str:
        raise AssertionError("every fixture file below has a coverage entry")

    return evaluate_coverage(
        added=added,
        profile=profile,
        adapter=adapter,
        repo_top=REPO_TOP,
        # A single source root covering the whole repo tree -- this test
        # module's own O2 fixtures deliberately span two different
        # top-level project directories (`pkg/`, `myapp_legacy/pkg/`), and
        # boundary matching against source roots is `evaluate.py`'s own
        # already-proven concern (A-099), not what these tests exercise.
        source_root_paths=(REPO_TOP,),
        fail_under=fail_under,
        allow_excluded=allow_excluded,
        read_source_text=read_source_text,
    )


def test_every_construct_family_is_classified_correctly_via_the_real_adapter():
    """The whole 24-line fixture is treated as newly added (every physical
    line is 'changed'), so every construct family passes through
    ``is_test_path`` (must stay False -- ``pkg/mod.py`` is not a test path)
    and the four-way union simultaneously."""
    adapter = PythonAdapter()
    added = AddedLines(by_file=MappingProxyType({"pkg/mod.py": _ALL_LINES}))
    profile = CoverageProfile(
        files=MappingProxyType(
            {
                "pkg/mod.py": FileCoverage(
                    executed=_EXECUTED, missing=_MISSING, excluded=_EXCLUDED
                )
            }
        )
    )

    result = _evaluate(added, profile, adapter, allow_excluded=False)

    # rule 3 (an excluded changed line, disallowed) takes precedence over
    # rule 2's percentage check -- proven independently by P05, exercised
    # here through the real adapter's own pass-through.
    assert result.outcome is Outcome.FAIL
    assert result.reason_code is ReasonCode.EXCLUDED_LINES
    assert result.considered == 1
    assert result.changed_executable == 10  # executed(9) + missing(1)
    assert result.covered == 9
    assert result.missing_lines == {"pkg/mod.py": frozenset({9})}
    assert result.files_missing_coverage == ()


def test_the_same_fixture_with_allow_excluded_falls_through_to_rule_2():
    """With the exclusion opted into, the same fixture's real remaining
    gap (L9, `helper`'s uncalled body) surfaces as rule 2's own
    UNCOVERED_LINES -- a second, independent confirmation that the pragma
    line (L17) truly contributed nothing to either the numerator or the
    denominator once it stopped being disallowed."""
    adapter = PythonAdapter()
    added = AddedLines(by_file=MappingProxyType({"pkg/mod.py": _ALL_LINES}))
    profile = CoverageProfile(
        files=MappingProxyType(
            {
                "pkg/mod.py": FileCoverage(
                    executed=_EXECUTED, missing=_MISSING, excluded=_EXCLUDED
                )
            }
        )
    )

    result = _evaluate(added, profile, adapter, allow_excluded=True)

    assert result.outcome is Outcome.FAIL
    assert result.reason_code is ReasonCode.UNCOVERED_LINES
    assert result.pct == 90.0
    assert result.missing_lines == {"pkg/mod.py": frozenset({9})}


def test_a_fully_covered_variant_of_the_same_fixture_passes():
    """Same construct shapes, but `helper()` is also modelled as called
    (L9 moves from missing to executed) -- proving the FAIL above was
    really about the real gap, not an artefact of this fixture's shape."""
    adapter = PythonAdapter()
    added = AddedLines(by_file=MappingProxyType({"pkg/mod.py": _ALL_LINES}))
    profile = CoverageProfile(
        files=MappingProxyType(
            {
                "pkg/mod.py": FileCoverage(
                    executed=_EXECUTED | _MISSING,
                    missing=frozenset(),
                    excluded=_EXCLUDED,
                )
            }
        )
    )

    result = _evaluate(added, profile, adapter, allow_excluded=True)

    assert result.outcome is Outcome.PASS
    assert result.pct == 100.0
    assert result.missing_lines == {}


# --- O2 end-to-end: the genuinely-differing key spelling, and the sibling
# hazard, both resolved correctly through the real evaluation pipeline ---


def test_normalize_coverage_key_reconciles_the_real_pipeline_end_to_end():
    """The coverage artifact's own key (`"myapp/pkg/foo.py"`, carrying the
    project directory's name the way a wider-cwd coverage run produces --
    see ``python.py``'s own module docstring) and the diff's spelling
    (`"pkg/foo.py"`) genuinely differ; only the adapter's own prefix strip
    lets them match."""
    adapter = PythonAdapter(coverage_key_prefix="myapp")
    added = AddedLines(by_file=MappingProxyType({"pkg/foo.py": frozenset({2, 3})}))
    profile = CoverageProfile(
        files=MappingProxyType(
            {
                "myapp/pkg/foo.py": FileCoverage(
                    executed=frozenset({2}),
                    missing=frozenset({3}),
                    excluded=frozenset(),
                )
            }
        )
    )

    result = _evaluate(added, profile, adapter, allow_excluded=False)

    assert result.covered == 1
    assert result.changed_executable == 2
    assert result.missing_lines == {"pkg/foo.py": frozenset({3})}


def test_a_sibling_prefixed_files_own_coverage_is_not_stolen_or_lost():
    """Two DIFFERENT, unrelated changed files reach the same evaluation:
    `myapp/pkg/foo.py`'s own coverage key genuinely needs the `myapp`
    strip, while `myapp_legacy/pkg/bar.py` -- a sibling directory that
    merely shares those characters -- must keep its OWN, already-correct
    key untouched. A naive `removeprefix` would corrupt the second file's
    key into `_legacy/pkg/bar.py`, which matches no diff path at all, so
    that file would wrongly fall through to 'missing from coverage'
    (`files_missing_coverage`) even though it genuinely was measured."""
    adapter = PythonAdapter(coverage_key_prefix="myapp")
    added = AddedLines(
        by_file=MappingProxyType(
            {
                "pkg/foo.py": frozenset({2}),
                "myapp_legacy/pkg/bar.py": frozenset({5}),
            }
        )
    )
    profile = CoverageProfile(
        files=MappingProxyType(
            {
                "myapp/pkg/foo.py": FileCoverage(
                    executed=frozenset({2}), missing=frozenset(), excluded=frozenset()
                ),
                "myapp_legacy/pkg/bar.py": FileCoverage(
                    executed=frozenset({5}), missing=frozenset(), excluded=frozenset()
                ),
            }
        )
    )

    result = _evaluate(
        added,
        profile,
        adapter,
        allow_excluded=False,
        fail_under=100.0,
    )

    assert result.outcome is Outcome.PASS
    assert result.considered == 2
    assert result.covered == 2
    assert result.changed_executable == 2
    assert result.files_missing_coverage == ()
    assert result.missing_lines == {}
