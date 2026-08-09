"""O1-O3 — ``PythonAdapter.generate_mutants``, proven against the literal
committed fixture ``tests/fixtures/mutation/python/sample.py`` (never a
string synthesized inline, matching ``tests/fixtures/canary/python/``'s own
discipline) and a hand-derived, independent expected manifest built by
READING that fixture and reasoning about Python's own grammar — never by
calling :meth:`PythonAdapter.generate_mutants` first and copying its
output (A-041/A-080's own "independent oracle" discipline).

* **O1** — :data:`EXPECTED_MUTATIONS` gives the EXACT expected full-file
  text for every one of the fixture's 27 eligible sites, built by rewriting
  exactly one physical line of the original text
  (:func:`_with_line_replaced`) — never touching
  :mod:`assay.mutation`/:mod:`assay.adapters.python`'s own byte-offset
  machinery, so a bug there cannot also corrupt the expected values.
* **O2** — every returned mutant's ``mutated_text`` round-trips through
  ``ast.parse``; the Go adapter and unsupported Python constructs
  (``in``/``not in``, a non-falsy ``return``, a bare ``return``) never
  contribute a text-guessed mutant.
* **O3** — the actual identity manifest equals :data:`EXPECTED_IDENTITIES`
  exactly (no more, no fewer) and *lines* scopes candidates to real
  changed-line membership.
"""

from __future__ import annotations

import ast
from collections import Counter

import pytest
from conftest import PROJECT_ROOT

from assay.adapters.python import PythonAdapter, _falsy_swap_replacement
from assay.mutation import MutationDiscoveryError

FIXTURE_DIR = PROJECT_ROOT / "tests" / "fixtures" / "mutation" / "python"
SAMPLE_TEXT = (FIXTURE_DIR / "sample.py").read_text(encoding="utf-8")
BROKEN_TEXT = (FIXTURE_DIR / "broken.py").read_text(encoding="utf-8")
ALL_LINES = set(range(1, len(SAMPLE_TEXT.splitlines()) + 1))

ADAPTER = PythonAdapter()


def _with_line_replaced(text: str, lineno: int, new_line: str) -> str:
    """*text* with its ``lineno``-th physical line (1-based) replaced by
    *new_line* (which must include its own trailing ``"\\n"``) — an
    independent, from-scratch expected-text builder that never calls into
    :mod:`assay.mutation`/:mod:`assay.adapters.python`'s own byte-offset
    arithmetic, so it cannot share a bug with the code it is checking."""
    lines = text.splitlines(keepends=True)
    lines[lineno - 1] = new_line
    return "".join(lines)


# --- the hand-derived, independent expected manifest ------------------------
#
# One entry per (lineno, operator, description) triple actually present in
# sample.py, keyed to the ORDERED list of expected full-file texts for every
# site sharing that triple (only lines 48/54, the boolean chains, have more
# than one — A-115). Deliberately excludes every line the catalogue does NOT
# cover (21 `in`, 23 `not in`, 94 a non-falsy `return 42`, 98 a bare
# `return`) — their absence here IS O3's own claim: a removed eligibility
# filter would add an identity this manifest does not expect.

EXPECTED_MUTATIONS: dict[tuple[int, str, str], list[str]] = {
    (24, "compare-swap", "Lt->LtE"): [
        _with_line_replaced(SAMPLE_TEXT, 24, "    if a <= b:\n")
    ],
    (26, "compare-swap", "LtE->Lt"): [
        _with_line_replaced(SAMPLE_TEXT, 26, "    if a < b:\n")
    ],
    (28, "compare-swap", "Gt->GtE"): [
        _with_line_replaced(SAMPLE_TEXT, 28, "    if a >= b:\n")
    ],
    (30, "compare-swap", "GtE->Gt"): [
        _with_line_replaced(SAMPLE_TEXT, 30, "    if a > b:\n")
    ],
    (32, "compare-swap", "Eq->NotEq"): [
        _with_line_replaced(SAMPLE_TEXT, 32, "    if a != b:\n")
    ],
    (34, "compare-swap", "NotEq->Eq"): [
        _with_line_replaced(SAMPLE_TEXT, 34, "    if a == b:\n")
    ],
    (36, "compare-swap", "Is->IsNot"): [
        _with_line_replaced(SAMPLE_TEXT, 36, "    if a is not b:\n")
    ],
    (38, "compare-swap", "IsNot->Is"): [
        _with_line_replaced(SAMPLE_TEXT, 38, "    if a is b:\n")
    ],
    (44, "falsy-swap", "None->[]"): [
        _with_line_replaced(SAMPLE_TEXT, 44, "    return []\n")
    ],
    (48, "boolop-swap", "And->Or"): [
        _with_line_replaced(SAMPLE_TEXT, 48, "    if a or b and c:\n"),
        _with_line_replaced(SAMPLE_TEXT, 48, "    if a and b or c:\n"),
    ],
    (49, "bool-const-flip", "True->False"): [
        _with_line_replaced(SAMPLE_TEXT, 49, "        return False\n")
    ],
    (50, "bool-const-flip", "False->True"): [
        _with_line_replaced(SAMPLE_TEXT, 50, "    return True\n")
    ],
    (54, "boolop-swap", "Or->And"): [
        _with_line_replaced(SAMPLE_TEXT, 54, "    if a and b or c or d:\n"),
        _with_line_replaced(SAMPLE_TEXT, 54, "    if a or b and c or d:\n"),
        _with_line_replaced(SAMPLE_TEXT, 54, "    if a or b or c and d:\n"),
    ],
    (55, "bool-const-flip", "True->False"): [
        _with_line_replaced(SAMPLE_TEXT, 55, "        return False\n")
    ],
    (56, "bool-const-flip", "False->True"): [
        _with_line_replaced(SAMPLE_TEXT, 56, "    return True\n")
    ],
    (60, "bool-const-flip", "True->False"): [
        _with_line_replaced(SAMPLE_TEXT, 60, "    x = False\n")
    ],
    (61, "bool-const-flip", "False->True"): [
        _with_line_replaced(SAMPLE_TEXT, 61, "    y = True\n")
    ],
    (66, "falsy-swap", "None->[]"): [
        _with_line_replaced(SAMPLE_TEXT, 66, "    return []\n")
    ],
    (70, "falsy-swap", "0->None"): [
        _with_line_replaced(SAMPLE_TEXT, 70, "    return None\n")
    ],
    (74, "falsy-swap", "''->None"): [
        _with_line_replaced(SAMPLE_TEXT, 74, "    return None\n")
    ],
    (78, "falsy-swap", "b''->None"): [
        _with_line_replaced(SAMPLE_TEXT, 78, "    return None\n")
    ],
    (82, "falsy-swap", "[]->None"): [
        _with_line_replaced(SAMPLE_TEXT, 82, "    return None\n")
    ],
    (86, "falsy-swap", "()->None"): [
        _with_line_replaced(SAMPLE_TEXT, 86, "    return None\n")
    ],
    (90, "falsy-swap", "{}->None"): [
        _with_line_replaced(SAMPLE_TEXT, 90, "    return None\n")
    ],
}

EXPECTED_IDENTITIES = frozenset(
    (lineno, operator, description, text)
    for (lineno, operator, description), texts in EXPECTED_MUTATIONS.items()
    for text in texts
)

#: Every operator the catalogue knows -- this module exercises the fixture's
#: whole eligible set, so it always selects all four.
ALL_OPERATORS = ("compare-swap", "boolop-swap", "bool-const-flip", "falsy-swap")

#: Comfortably above the fixture's 27 sites, so `limit` never truncates here
#: (the bound itself is proven in `test_mutation_collect.py`).
NO_TRUNCATION = 1000


def _sites(lines, *, operators=ALL_OPERATORS, limit=NO_TRUNCATION, text=None):
    """P21: the bounded seam replaces ``generate_mutants``. Sites carry a
    byte span and a replacement rather than a full mutated file."""
    return ADAPTER.generate_mutation_sites(
        SAMPLE_TEXT if text is None else text,
        lines,
        operators=operators,
        limit=limit,
    )


def _applied(site, text: str = None) -> str:
    """The full mutated text a site produces -- what P11's ``mutated_text``
    used to carry eagerly, now materialised on demand. Every assertion in
    this module that used to read ``mutant.mutated_text`` reads this, so the
    hand-derived expected manifest is unchanged and still independent."""
    source = (SAMPLE_TEXT if text is None else text).encode("utf-8")
    return site.apply(source).decode("utf-8")

#: Total eligible sites the fixture is hand-verified to contain — 8
#: compare-swap + 5 boolop-swap (2 + 3 across the two chains) + 6
#: bool-const-flip + 8 falsy-swap.
EXPECTED_TOTAL = 27


def test_the_expected_manifest_itself_totals_the_hand_counted_site_count():
    """Guards the manifest's own arithmetic — a typo dropping an entry
    would otherwise silently weaken every test below it."""
    assert sum(len(texts) for texts in EXPECTED_MUTATIONS.values()) == EXPECTED_TOTAL
    assert len(EXPECTED_IDENTITIES) == EXPECTED_TOTAL


# --- O1: every eligible site produces the hand-derived exact mutated text ---


def test_every_eligible_site_produces_the_hand_derived_exact_mutated_text():
    """O1's core claim, proven for every eligible site in the fixture at
    once: grouping the actual result by its own ``(lineno, operator,
    description)`` triple and comparing each group's texts, in order,
    against :data:`EXPECTED_MUTATIONS` — built independently by rewriting
    exactly one line of the ORIGINAL text, never by calling this method and
    trusting its output."""
    mutants = _sites(ALL_LINES)
    assert isinstance(mutants, tuple)
    assert len(mutants) == EXPECTED_TOTAL

    grouped: dict[tuple[int, str, str], list[str]] = {}
    for mutant in mutants:
        key = (mutant.lineno, mutant.operator, mutant.description)
        grouped.setdefault(key, []).append(_applied(mutant))

    assert set(grouped) == set(EXPECTED_MUTATIONS)
    for triple, expected_texts in EXPECTED_MUTATIONS.items():
        assert grouped[triple] == expected_texts, f"mismatch for {triple}"


def test_a_boolean_chains_untouched_operator_token_is_still_literally_present():
    """A-115's own trap, made explicit: for the 3-operand ``and`` chain
    (line 48), the FIRST site's mutated text still contains a literal
    ``and`` (only the first token flipped), and the SECOND site's mutated
    text still contains a literal ``or``-untouched... i.e. each site
    flips exactly ONE of the two ``and`` tokens, never both at once (which
    reassigning the shared ``ast.BoolOp.op`` field wholesale would do)."""
    mutants = _sites({48})
    assert len(mutants) == 2
    first, second = mutants  # sorted by byte offset — left-to-right

    assert "if a or b and c:" in _applied(first)
    assert "if a and b or c:" in _applied(second)
    # Neither site's own splice touched the OTHER "and" -- each mutated
    # text contains exactly one "or" and one "and" on that line, never two
    # "or"s.
    changed_line_first = _applied(first).splitlines()[47]
    changed_line_second = _applied(second).splitlines()[47]
    assert changed_line_first.count(" or ") == 1
    assert changed_line_first.count(" and ") == 1
    assert changed_line_second.count(" or ") == 1
    assert changed_line_second.count(" and ") == 1


def test_the_non_ascii_comment_survives_every_mutation_byte_for_byte():
    """A direct check on the file's own non-ASCII comment (line 16): every
    mutant's ``mutated_text`` reproduces it EXACTLY, proving the byte-offset
    arithmetic used for a site further down the file did not silently
    misalign against the multi-byte character above it."""
    non_ascii_line = SAMPLE_TEXT.splitlines(keepends=True)[15]
    assert "café" in non_ascii_line
    mutants = _sites(ALL_LINES)
    assert mutants  # guard against a vacuously-true loop below
    for mutant in mutants:
        assert _applied(mutant).splitlines(keepends=True)[15] == non_ascii_line


def test_mutant_identity_is_stable_and_unique_across_the_whole_fixture():
    mutants = _sites(ALL_LINES)
    identities = [m.identity for m in mutants]
    assert len(set(identities)) == len(identities), "two sites share one identity"
    # Calling .identity twice on the SAME mutant is stable.
    assert mutants[0].identity == mutants[0].identity


def test_generate_mutants_is_deterministic_across_repeated_calls():
    first = _sites(ALL_LINES)
    second = _sites(ALL_LINES)
    assert first == second


# --- O2: validity ------------------------------------------------------------


def test_every_generated_mutant_parses_with_ast_parse():
    mutants = _sites(ALL_LINES)
    assert mutants
    for mutant in mutants:
        ast.parse(_applied(mutant))  # raises on any invalid syntax


def test_unparseable_source_raises_the_typed_discovery_failure():
    """P21/A-183 changes this terminal deliberately. Under P11 unparseable
    Python returned ``"UNSUPPORTED"``, which now means something else
    entirely -- *the adapter has no mutation engine* -- and is Go's answer
    until P29. Python HAS an engine, so source it cannot parse is a FAILED
    boundary (``ERROR``/``MUTATION_DISCOVERY_FAILED``), never capability
    absence and never a valid empty result. Collapsing any pair of the
    three would turn an inability to measure into evidence that nothing was
    mutable."""
    with pytest.raises(MutationDiscoveryError, match="cannot parse"):
        _sites({1, 2}, text=BROKEN_TEXT)


def test_an_unsupported_construct_in_an_otherwise_parseable_file_contributes_zero_sites_not_an_abort():
    """`in`/`not in` are real comparators the ``compare-swap`` catalogue
    does not cover (A-112) — the ENCLOSING file is fully parseable, so the
    correct outcome is a real (here empty) tuple for those two lines, never
    ``"UNSUPPORTED"`` (A-114's own "an individual unsupported construct...
    simply contributes zero mutants... never an early abort")."""
    result = _sites({40, 42})
    assert result == ()
    assert result != "UNSUPPORTED"


def test_a_non_falsy_return_and_a_bare_return_contribute_zero_sites():
    result = _sites({94, 98})
    assert result == ()


# --- O3: scoping to real changed-line membership -----------------------------


def test_only_sites_on_the_declared_lines_are_returned():
    """Requesting a SINGLE line returns exactly the one site on it, never
    any of the other 26 real sites elsewhere in the same file."""
    mutants = _sites({24})
    assert len(mutants) == 1
    assert _applied(mutants[0]) == EXPECTED_MUTATIONS[(24, "compare-swap", "Lt->LtE")][0]


def test_an_empty_lines_set_returns_an_empty_tuple_never_unsupported():
    result = _sites(set())
    assert result == ()


def test_o3_actual_identity_manifest_equals_the_independent_expected_manifest_exactly():
    """The oracle's own negative, directly: removing any eligibility filter
    in the implementation would add an identity absent from
    :data:`EXPECTED_IDENTITIES`, which this equality catches immediately.

    P21 note: the comparison key is the hand-derived
    ``(lineno, operator, description, full mutated text)`` -- NOT the new
    wire identity. That is deliberate. The expected manifest is built by
    rewriting exactly one line of the original text, independently of this
    implementation; comparing against byte spans instead would mean
    hand-transcribing 27 offsets FROM the implementation, which is the
    producer-authored evidence A-041 forbids. The wire identity's own
    uniqueness and stability are asserted separately above."""
    mutants = _sites(ALL_LINES)
    actual_identities = frozenset(
        (site.lineno, site.operator, site.description, _applied(site))
        for site in mutants
    )
    assert actual_identities == EXPECTED_IDENTITIES


def test_o3_manifest_multiset_counts_match_not_just_the_set_of_triples():
    """Distinguishes "an identity is missing/extra" from "a triple's COUNT
    is wrong" (e.g. only one of the chain's two `And->Or` sites fired) --
    the plain set-equality check above would not catch a count mismatch on
    its own."""
    mutants = _sites(ALL_LINES)
    actual_counts = Counter((m.lineno, m.operator, m.description) for m in mutants)
    expected_counts = Counter(
        {triple: len(texts) for triple, texts in EXPECTED_MUTATIONS.items()}
    )
    assert actual_counts == expected_counts


# --- the private per-site eligibility classifier (Work item 2, P10's own
# `_check_ancestor_or_equal` precedent for direct unit coverage) -------------


def _value_node(expr_text: str) -> ast.expr:
    return ast.parse(expr_text, mode="eval").body


@pytest.mark.parametrize(
    "expr_text, expected",
    [
        ("None", ("None", "[]")),
        ("0", ("0", "None")),
        ("0.0", ("0.0", "None")),
        ('""', ("''", "None")),
        ('b""', ("b''", "None")),
        ("[]", ("[]", "None")),
        ("()", ("()", "None")),
        ("{}", ("{}", "None")),
    ],
)
def test_falsy_swap_replacement_recognises_every_catalogue_shape(expr_text, expected):
    assert _falsy_swap_replacement(_value_node(expr_text)) == expected


@pytest.mark.parametrize(
    "expr_text",
    [
        "True",
        "False",
        "42",
        '"nonempty"',
        "[1, 2]",
        "(1,)",
        "{1: 2}",
        "{1, 2}",
        "x",
        "x + 1",
    ],
)
def test_falsy_swap_replacement_rejects_everything_outside_the_catalogue(expr_text):
    assert _falsy_swap_replacement(_value_node(expr_text)) is None
