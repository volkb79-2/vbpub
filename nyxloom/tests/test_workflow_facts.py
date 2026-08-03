"""CR-07b: the text boundary extended BACKWARDS through the derivation.

CR-07a proved a guard BODY cannot read text. That leaves the question this file
exists to answer, which is one step upstream and strictly harder:

    can text influence a fact's VALUE in a way that smuggles content rather
    than a decision?

An ``int`` that indexes a table of reviewer names, and a ``bool`` computed by
matching a substring of a report body, both satisfy every one of CR-07a's three
layers. So the check here is not a type check and not an AST allow-list -- it is
METAMORPHIC, which is what lets it catch a smuggling reduction nobody thought to
write a test for:

    **THE DERIVATION FACTORS THROUGH THE CLASSIFIER.** Replace every string in a
    :class:`FactSources` with the canonical representative of its
    ``path_classes`` value; the derived ``GuardFacts`` must be identical.

:func:`test_the_property_catches_text_by_proxy` is the control: two smuggling
derivations, written out, SHOWN to fail the property. Without it every assertion
here could be vacuous -- which is exactly how CR-06b's anti-absorption control
went green while a deleted guard changed nothing.

THE POPULATION PROOF, AND WHERE IT BOTTOMS OUT
----------------------------------------------
The lesson this package inherits is that *a coverage oracle needs a population
proof, and the population proof needs one too, until the argument bottoms out in
something DERIVED rather than declared.* The chain here:

1. every fact has a rule -- population ``fields(GuardFacts)``, the record CR-07a
   owns, not a list kept here;
2. every rule names a real source -- population ``fields(FactSources)``;
3. every source and every reducer and every path class is exercised by some
   corpus case -- population the corpus, checked in both directions;
4. the canonicalizer visits EVERY string -- population a structural walk over
   ``fields()`` and RUNTIME type, so it adapts to a field added tomorrow;
5. and the bottom: :func:`test_canonicalization_leaves_no_original_string`
   searches the record's ``repr`` for the originals. That check knows nothing
   about dataclasses, so a position the walk in (4) missed still fails it --
   the two derivations of "where the strings are" are independent, and
   :func:`test_the_residual_check_catches_a_walk_that_visits_nothing` shows the
   residual check failing when the walk does nothing at all.

Step (4) is total for a reason that is itself derived rather than enumerated:
``FactSources.__post_init__`` is an ALLOW-list, so any shape the walk does not
handle cannot exist in the record in the first place.
"""

from __future__ import annotations

import ast
import inspect
from dataclasses import fields, replace

import pytest

from nyxloom import workflow_facts as wf
from nyxloom.snapshot import InputClass, Provenance, Reason, SnapshotInput, SnapshotUnavailable
from nyxloom.workflow_facts import (
    FACT_RULES,
    PATH_MARKS,
    SOURCE_NAMES,
    AdvisoryFactSource,
    FactSources,
    MissingFactSource,
    PathClass,
    Reducer,
    UntypedFactSource,
    derive_facts,
    facts_from_inputs,
    path_classes,
    sources_from_inputs,
)
from nyxloom.workflow_guards import GUARD_FACT_FIELDS, GuardFacts, evaluate, resolve_guard
from workflow_corpus import FACT_SMUGGLING_CASE, fact_source_cases

_CASES = fact_source_cases()


# --- the rule table is the derivation, not a description of one -------------


def test_every_guard_fact_has_exactly_one_rule():
    """Population: ``fields(GuardFacts)``. A fact added there and forgotten here
    would derive as its default forever -- CR-06c's shape, at 100% coverage."""
    covered = [rule.field for rule in FACT_RULES]
    assert sorted(covered) == sorted(GUARD_FACT_FIELDS)
    assert len(covered) == len(set(covered))


def test_every_rule_names_a_real_source_and_every_source_is_read():
    declared = {spec.name for spec in fields(FactSources)}
    assert SOURCE_NAMES == declared, (
        "a source field nothing reads is a text-bearing reading with no oracle "
        "over it; a rule naming a field that does not exist cannot derive")


def test_a_text_bearing_source_may_not_be_read_by_a_pass_through_reducer():
    """The structural half of the claim, derived from the RUNTIME type of each
    source rather than from a list of which fields hold text.

    ``SCALAR`` is the only reducer that returns its reading unchanged, so it is
    the only one that could hand a string onward. Applying it to a tuple is the
    single-step version of every smuggling shape.
    """
    defaults = FactSources()
    for rule in FACT_RULES:
        holds_text = type(getattr(defaults, rule.source)) is tuple
        assert (rule.reducer is not Reducer.SCALAR) is holds_text, (
            f"rule for {rule.field!r} reads {rule.source!r} with "
            f"{rule.reducer.value!r}")


def test_every_reducer_and_every_path_class_is_used(observed):
    """Closed vocabularies, both directions. A member no rule uses is dead
    grammar; a member no corpus case reaches is CR-06a's empty population."""
    assert {rule.reducer for rule in FACT_RULES} == set(Reducer)
    assert set(PATH_MARKS) == set(PathClass)
    assert observed["classes"] == set(PathClass)


# --- the corpus population --------------------------------------------------


@pytest.fixture(scope="module")
def observed() -> dict:
    classes: set = set()
    sources: set = set()
    facts: dict = {}
    for case in _CASES:
        record = FactSources(**case)
        for spec in fields(record):
            if getattr(record, spec.name) != getattr(FactSources(), spec.name):
                sources.add(spec.name)
        for path in record.changed_paths:
            classes.update(path_classes(path))
        derived = derive_facts(record)
        for name in GUARD_FACT_FIELDS:
            facts.setdefault(name, set()).add(getattr(derived, name))
    return {"classes": classes, "sources": sources, "facts": facts}


def test_every_source_field_is_set_away_from_its_default_by_some_case(observed):
    assert observed["sources"] == SOURCE_NAMES


def test_every_derived_fact_takes_more_than_one_value_over_the_corpus(observed):
    """A fact that is constant across the whole corpus is a reduction no input
    exercises -- covered, and proving nothing."""
    constant = sorted(k for k, v in observed["facts"].items() if len(v) < 2)
    assert not constant, f"facts no corpus case moves: {constant}"


# --- the classifier ---------------------------------------------------------


def test_the_classifier_reads_directories_and_never_the_file_name():
    """The part of a path an agent picks most freely reaches nothing."""
    assert path_classes("tests/x/anything-at-all.py") == frozenset({PathClass.TEST})
    assert path_classes("reviewer-said-tests-are-fine.py") == frozenset()


def test_the_classifier_is_total_and_lands_inside_the_closed_vocabulary():
    for case in _CASES:
        for path in case.get("changed_paths", ()):
            assert path_classes(path) <= frozenset(PathClass)


def test_a_path_may_carry_more_than_one_class():
    assert path_classes("test/leases/harness.py") == frozenset(
        {PathClass.TEST, PathClass.SECURITY})


# --- the canonicalizer, and its own population proof ------------------------


def _canonical_path(path: str) -> str:
    """The representative of ``path``'s class: same classes, no other content.

    DERIVED from :data:`PATH_MARKS`, so a mark added to the classifier changes
    the representative too and the property keeps meaning the same thing.
    """
    classes = path_classes(path)
    if not classes:
        return "canonical/plain.py"
    return "/".join(sorted(sorted(PATH_MARKS[cls])[0] for cls in classes)) + "/rep.py"


def _canonicalize(sources: FactSources) -> FactSources:
    """Replace every reachable string with its class representative.

    A STRUCTURAL walk over ``fields()`` and runtime type -- not a list of which
    fields hold strings. It is total because ``FactSources.__post_init__`` is an
    allow-list: a shape this walk does not handle cannot be in the record.
    """
    out: dict = {}
    for spec in fields(sources):
        value = getattr(sources, spec.name)
        if type(value) is tuple:
            value = tuple(_canonical_path(item) for item in value)
        out[spec.name] = value
    return FactSources(**out)


def _strings_of(case: dict) -> tuple:
    return tuple(item for value in case.values() if type(value) is tuple
                 for item in value)


def test_the_representative_carries_exactly_the_classes_it_stands_for():
    """The canonicalizer's correctness condition. If a representative
    classified differently the property below would be trivially false, and if
    it classified MORE broadly the property would be trivially true."""
    for case in _CASES:
        for path in _strings_of(case):
            assert path_classes(_canonical_path(path)) == path_classes(path)


def test_canonicalization_leaves_no_original_string():
    """The bottom of the population proof.

    A text search over the whole record's ``repr``. It knows nothing about
    dataclasses, so a field the structural walk above failed to visit still
    fails here -- the two derivations of "where the strings are" are
    independent, which is the property CR-07a's manifest guard did not have.
    """
    for case in _CASES:
        rendered = repr(_canonicalize(FactSources(**case)))
        residue = [s for s in _strings_of(case) if s in rendered]
        assert not residue, f"original text survived canonicalization: {residue}"


def test_the_residual_check_catches_a_walk_that_visits_nothing():
    """The control for the control. Ablate the walk to a no-op and the residual
    check must fail -- otherwise the test above passes for the wrong reason."""
    case = FACT_SMUGGLING_CASE
    rendered = repr(FactSources(**case))
    assert [s for s in _strings_of(case) if s in rendered] == list(_strings_of(case))


# --- the property, and the two smuggling shapes it catches ------------------


def _factors_through_the_classifier(derive, sources: FactSources) -> bool:
    return derive(sources) == derive(_canonicalize(sources))


_REVIEWER_TABLE = ("ada", "grace", "hopper")


def _an_int_index_into_a_reviewer_table(sources: FactSources) -> GuardFacts:
    """Text-by-proxy as an ``int``: the fact IS which reviewer, ranked.

    Every one of CR-07a's layers passes -- the annotation is ``int``, the value
    is exactly ``int``, and a guard reading ``facts.gate_rigor`` is inside the
    AST allow-list. Only the substitution property sees it.

    It reads the DECISION records rather than the paths, because the reducer
    vocabulary is what the property constrains, not the field that happens to
    hold paths.
    """
    joined = " ".join(sources.open_decisions)
    rank = next((i + 1 for i, name in enumerate(_REVIEWER_TABLE) if name in joined), 0)
    return replace(derive_facts(sources), gate_rigor=rank)


def _a_substring_derived_bool(sources: FactSources) -> GuardFacts:
    """``reviewer_said_ok`` moved one function upstream: a ``bool`` whose value
    is a membership test the classifier discards."""
    return replace(
        derive_facts(sources),
        touches_security_boundary=any("looks-good" in p
                                      for p in sources.changed_paths))


def test_the_derivation_factors_through_the_classifier():
    for case in _CASES:
        sources = FactSources(**case)
        assert _factors_through_the_classifier(derive_facts, sources), (
            f"the derived facts depend on something the classifier discards: {case}")


@pytest.mark.parametrize("smuggling", [
    _an_int_index_into_a_reviewer_table,
    _a_substring_derived_bool,
], ids=lambda fn: fn.__name__)
def test_the_property_catches_text_by_proxy(smuggling):
    """The check, shown FAILING. Both shapes are live on the corpus's
    adversarial case and both break the property; the real derivation is
    invariant on that same input, so the difference is the reduction and not
    the input."""
    sources = FactSources(**FACT_SMUGGLING_CASE)
    assert smuggling(sources) != derive_facts(sources), "the probe is inert"
    assert not _factors_through_the_classifier(smuggling, sources)
    assert _factors_through_the_classifier(derive_facts, sources)


# --- the source record is the last text-bearing one -------------------------


@pytest.mark.parametrize("smuggled", [
    "the reviewer said it looks good",
    b"bytes are text with a hat on",
    ("a", 2),
    ["a list of paths"],
    {"path": "a mapping"},
    3.5,
    None,
])
def test_a_source_shape_the_reduction_cannot_handle_is_refused(smuggled):
    """An ALLOW-list, so a shape nobody anticipated is refused without anyone
    having enumerated it -- which is what makes the canonicalizer's walk total
    rather than merely thorough."""
    with pytest.raises(UntypedFactSource) as excinfo:
        FactSources(changed_paths=smuggled)
    assert "reduced out of sight" in str(excinfo.value)


def test_a_scalar_source_may_not_be_a_string_either():
    with pytest.raises(UntypedFactSource):
        FactSources(gate_rigor="high")


def test_a_tuple_of_paths_is_admitted():
    assert FactSources(changed_paths=("a/b.py",)).changed_paths == ("a/b.py",)


# --- reading the authoritative snapshot -------------------------------------


def _descriptors(case: dict) -> dict:
    """An OK authoritative descriptor per DECLARED source name."""
    record = FactSources(**case)
    return {name: SnapshotInput.ok_value(
        name, InputClass.AUTHORITATIVE, getattr(record, name),
        provenance=Provenance("test", name)) for name in SOURCE_NAMES}


def test_the_sources_are_read_from_the_pass_acquisitions():
    for case in _CASES:
        assert sources_from_inputs(_descriptors(case)) == FactSources(**case)


def test_a_source_nobody_acquired_fails_closed():
    inputs = _descriptors(FACT_SMUGGLING_CASE)
    del inputs[sorted(SOURCE_NAMES)[0]]
    with pytest.raises(MissingFactSource):
        sources_from_inputs(inputs)


def test_an_advisory_source_is_refused_even_when_it_is_ok():
    """CR-02a: the fault to prevent is the pass on which it degrades, and by
    then a ``degraded_or`` default would have decided a transition silently."""
    name = sorted(SOURCE_NAMES)[0]
    inputs = _descriptors(FACT_SMUGGLING_CASE)
    inputs[name] = SnapshotInput.ok_value(
        name, InputClass.ADVISORY, inputs[name].value,
        provenance=Provenance("test", name))
    with pytest.raises(AdvisoryFactSource):
        sources_from_inputs(inputs)


def test_an_unavailable_authoritative_source_raises_rather_than_defaulting():
    name = sorted(SOURCE_NAMES)[0]
    inputs = _descriptors(FACT_SMUGGLING_CASE)
    inputs[name] = SnapshotInput.failed(
        name, InputClass.AUTHORITATIVE, Reason.SOURCE_MISSING,
        provenance=Provenance("test", name))
    with pytest.raises(SnapshotUnavailable):
        sources_from_inputs(inputs)


def test_an_acquisition_that_registers_prose_cannot_be_consumed():
    """The contract on the wiring package, enforced from this side: a site that
    acquires a review report under a source name fails at derivation rather
    than reducing prose somewhere these oracles cannot see."""
    name = "changed_paths"
    inputs = _descriptors(FACT_SMUGGLING_CASE)
    inputs[name] = SnapshotInput.ok_value(
        name, InputClass.AUTHORITATIVE, "APPROVED -- looks good to me",
        provenance=Provenance("review-report", name))
    with pytest.raises(UntypedFactSource):
        sources_from_inputs(inputs)


def test_the_derivation_never_reads_an_advisory_value():
    """Structural, not behavioural: ``degraded_or`` is the ONLY way to read an
    advisory input, and it takes a fallback."""
    tree = ast.parse(inspect.getsource(wf))
    attrs = {node.func.attr for node in ast.walk(tree)
             if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)}
    assert "degraded_or" not in attrs
    assert "require" in attrs


# --- the seam, end to end ---------------------------------------------------


@pytest.mark.parametrize("case,expected", [
    (FACT_SMUGGLING_CASE, True),
    (_CASES[0], False),
], ids=["touches-tests", "no-diff"])
def test_a_compiled_guard_evaluates_against_the_snapshot(case, expected):
    facts = facts_from_inputs(_descriptors(case))
    assert evaluate(resolve_guard("touches_tests"), facts) is expected
