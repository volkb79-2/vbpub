"""O4b/O4c — lcov and Cobertura's own identity, merge and capability-
disagreement rules, over the REAL fixtures plus hand-authored Cobertura XML
(matching ``tests/test_coverage_parsers_cobertura.py``'s own established
convention -- "every fixture here is independently hand-written (A-080)" --
since the real ``cobertura.branch.xml`` witnesses only ONE ``<class>`` per
file and the multi-``<class>``-same-file shape has no real-fixture form to
copy from).

Every mutation of a real fixture here is a string-level edit of a COPY of
that fixture's own text, read fresh per test -- the committed file itself is
never opened for writing. Each test proves the unmodified control parses
clean in the SAME test that proves the injected defect does not (A-124/
A-131).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from assay.coverage import derive_branch_capability, load_coverage_profile
from assay.errors import AssayError, Outcome, ReasonCode

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "coverage"
LCOV_BRANCH = (FIXTURES / "lcov.branch.info").read_text(encoding="utf-8")
COBERTURA_BRANCH = (FIXTURES / "cobertura.branch.xml").read_text(encoding="utf-8")
COBERTURA_NOBRANCH = (FIXTURES / "cobertura.nobranch.xml").read_text(encoding="utf-8")


def _assert_unreadable(text: str, declared_format: str) -> None:
    with pytest.raises(AssayError) as caught:
        load_coverage_profile(text, declared_format=declared_format)
    assert caught.value.outcome is Outcome.ERROR
    assert caught.value.reason_code is ReasonCode.UNREADABLE_ARTIFACT


def _require_replaced(original: str, old: str, new: str) -> str:
    """``original.replace(old, new, 1)``, asserting the substitution actually
    happened -- a silent no-op ``.replace()`` (a stale/mistyped needle) would
    otherwise leave a "mutated" fixture byte-identical to its own control,
    turning the negative half of a differential test into a second positive
    one that cannot fail."""
    mutated = original.replace(old, new, 1)
    assert mutated != original, f"{old!r} was not found in the source text"
    return mutated


# ---------------------------------------------------------------------------
# O4b — lcov: a repeated (line, block, branch_id) triple refuses
# ---------------------------------------------------------------------------


def _lcov_control() -> None:
    profile = load_coverage_profile(LCOV_BRANCH, declared_format="lcov")
    assert profile.files["sample.py"].branches.by_line[5] == (1, 2)


def test_lcov_a_repeated_identity_triple_is_refused_even_with_contradictory_taken():
    _lcov_control()
    # The identity (line=5, block=0, branch="jump to line 6") already exists
    # once, with taken=1. Repeating it with a DIFFERENT taken value proves
    # the rule fires on the identity alone, "whatever the taken values"
    # (§3.1a) -- not merely on a byte-identical duplicate line.
    mutated = _require_replaced(
        LCOV_BRANCH,
        "BRDA:5,0,jump to line 6,1\n",
        "BRDA:5,0,jump to line 6,1\nBRDA:5,0,jump to line 6,0\n",
    )
    _assert_unreadable(mutated, "lcov")


# ---------------------------------------------------------------------------
# O4b — Cobertura: two <class> elements naming one file, branch merge
# ---------------------------------------------------------------------------

_MULTI_CLASS_AGREEING = """\
<coverage branches-valid="2" branches-covered="1">
  <packages>
    <package name="pkg">
      <classes>
        <class name="a" filename="pkg/shared.py">
          <lines>
            <line number="5" hits="1" branch="true" condition-coverage="50% (1/2)"/>
          </lines>
        </class>
        <class name="b" filename="pkg/shared.py">
          <lines>
            <line number="5" hits="1" branch="true" condition-coverage="50% (1/2)"/>
          </lines>
        </class>
      </classes>
    </package>
  </packages>
</coverage>
"""


def _multi_class_control() -> None:
    profile = load_coverage_profile(_MULTI_CLASS_AGREEING, declared_format="cobertura")
    assert profile.files["pkg/shared.py"].branches.by_line == {5: (1, 2)}


def test_two_classes_agreeing_on_one_shared_branch_line_parses_clean():
    _multi_class_control()


def test_two_classes_disagreeing_on_one_shared_branch_line_is_refused():
    _multi_class_control()
    disagreeing = _require_replaced(
        _MULTI_CLASS_AGREEING,
        '<class name="b" filename="pkg/shared.py">\n          <lines>\n'
        '            <line number="5" hits="1" branch="true" '
        'condition-coverage="50% (1/2)"/>',
        '<class name="b" filename="pkg/shared.py">\n          <lines>\n'
        '            <line number="5" hits="1" branch="true" '
        'condition-coverage="100% (2/2)"/>',
    )
    _assert_unreadable(disagreeing, "cobertura")


# ---------------------------------------------------------------------------
# O4b — Cobertura: the condition-coverage PERCENTAGE is never read
# ---------------------------------------------------------------------------


def test_a_nonsense_percentage_with_an_intact_c_over_t_parses_clean():
    # This test is what stops someone "helpfully" adding a P-vs-(C/T)
    # tolerance rule later: the percentage text is deliberately never
    # verified, so nonsense there must not refuse.
    _cobertura_control()
    mutated = _require_replaced(
        COBERTURA_BRANCH,
        'condition-coverage="50% (1/2)"',
        'condition-coverage="not-a-percent (1/2)"',
    )
    profile = load_coverage_profile(mutated, declared_format="cobertura")
    assert profile.files["sample.py"].branches.by_line[5] == (1, 2)


# ---------------------------------------------------------------------------
# O4c — capability disagreement is a refusal in BOTH directions
# ---------------------------------------------------------------------------


def _cobertura_control() -> None:
    profile = load_coverage_profile(COBERTURA_BRANCH, declared_format="cobertura")
    assert derive_branch_capability(profile) == "reported"


def test_branches_valid_zero_with_per_line_detail_present_is_refused():
    _cobertura_control()
    mutated = _require_replaced(COBERTURA_BRANCH, 'branches-valid="8"', 'branches-valid="0"')
    _assert_unreadable(mutated, "cobertura")


def test_branches_valid_eight_with_no_per_line_detail_is_refused():
    # cobertura.nobranch.xml is the real no-detail sibling artifact; its own
    # real branches-valid there is "0".
    profile = load_coverage_profile(COBERTURA_NOBRANCH, declared_format="cobertura")
    assert derive_branch_capability(profile) == "unavailable"
    mutated = _require_replaced(COBERTURA_NOBRANCH, 'branches-valid="0"', 'branches-valid="8"')
    _assert_unreadable(mutated, "cobertura")
