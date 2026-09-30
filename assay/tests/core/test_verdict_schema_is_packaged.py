"""The shipped verdict schema against the vocabulary module, and its resource path.

The judge half of the former ``test_verdict_schema_is_packaged.py``. The
wheel half (the schema is INSIDE the installed wheel and resolves from a clean
venv, O5) builds a wheel, so it moved to
``gate/tests/test_verdict_schema_wheel.py`` (W4, B123).
"""

from __future__ import annotations

import json

from conftest import PROJECT_ROOT, SCHEMA_PATH

from assay.verdict import SCHEMA_RESOURCE
from assay.vocabulary import (
    INGESTED_OPERATOR_RE,
    MUTATION_OPERATORS,
    MUTATION_OPERATORS_BY_LANGUAGE,
    is_ingested_operator,
)


def test_the_shipped_schema_enumerates_exactly_the_vocabulary_module_declares():
    """(P33/V5-2) The drift guard between two independently maintained
    artifacts, in the same shape `test_verdict_reason_codes.py` already uses
    for the reason vocabulary.

    v5 makes this necessary rather than merely tidy: the operator catalogue
    is now THREE per-language enums in the schema against one per-language
    map in :mod:`assay.vocabulary`, and nothing else in the suite compares
    them. A language whose enum was added to one and not the other would
    make the config loader and the shipped schema disagree about what a
    lane may declare -- exactly the model/schema/verifier mismatch P21's own
    work item 2 deleted for the flat four-value list.

    ORDER is asserted too, not just membership: `judgment.r2.operators`
    records a lane's own order-preserving selection, and the module
    docstring calls its tuple order normative for these branches.

    **B046 (schema v9) adds a FOURTH branch that is deliberately not an
    enum** -- the `^stryker:[A-Za-z0-9]+$` ingested namespace. It is
    separated out here rather than folded in, because folding it in is
    exactly the drift this test exists to catch: an open pattern compared
    against a closed vocabulary would either force `MUTATION_OPERATORS` to
    grow names no adapter implements, or quietly stop asserting anything.
    The two halves are checked by their own rules -- the enums against
    `MUTATION_OPERATORS_BY_LANGUAGE`, the pattern against
    `INGESTED_OPERATOR_RE` -- and the count is pinned so a fifth branch
    appearing in either shape fails here first.
    """
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    branches = schema["$defs"]["mutation_operator"]["oneOf"]
    enum_branches = [branch for branch in branches if "enum" in branch]
    pattern_branches = [branch for branch in branches if "pattern" in branch]
    assert len(enum_branches) + len(pattern_branches) == len(branches)
    assert len(pattern_branches) == 1

    shipped = [branch["enum"] for branch in enum_branches]
    declared = [list(operators) for operators in MUTATION_OPERATORS_BY_LANGUAGE.values()]
    assert shipped == declared

    # B046: the ingested branch's pattern is the SAME source string
    # `assay.vocabulary` compiles, not a hand-transcribed twin -- the schema
    # and the predicate cannot disagree about which names are ingested.
    assert pattern_branches[0]["pattern"] == INGESTED_OPERATOR_RE.pattern
    # ...and it really is open where the others are closed: a name matching
    # it is legal without appearing in any vocabulary the module ships.
    assert is_ingested_operator("stryker:ArithmeticOperator")
    assert "stryker:ArithmeticOperator" not in MUTATION_OPERATORS

    # Every ENUM branch is single-language, so a `oneOf` really does partition
    # the catalogue rather than merely covering it -- two branches sharing a
    # name would make a document ambiguous under `oneOf` and reject a valid
    # operator. The pattern branch cannot collide with them: no
    # language-qualified name assay ships starts with the ingested namespace.
    for branch, (language, operators) in zip(
        enum_branches, MUTATION_OPERATORS_BY_LANGUAGE.items()
    ):
        assert {name.split(":", 1)[0] for name in branch["enum"]} == {language}
        assert len(set(operators)) == len(operators)
    assert not [name for name in MUTATION_OPERATORS if is_ingested_operator(name)]

    # ...and the flat tuple the model and config close against is exactly the
    # union of what the schema ships in its CLOSED branches.
    assert set(MUTATION_OPERATORS) == {
        name for branch in enum_branches for name in branch["enum"]
    }


def test_the_resource_path_the_code_uses_matches_where_the_file_is():
    assert SCHEMA_RESOURCE == "schemas/verdict.schema.json"
    assert (PROJECT_ROOT / "src" / "assay" / SCHEMA_RESOURCE).is_file()
