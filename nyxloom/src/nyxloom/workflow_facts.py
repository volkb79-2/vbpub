"""Deriving :class:`~nyxloom.workflow_guards.GuardFacts` from the authoritative
snapshot -- the seam where agent-authored text is tempted in.

WHY THIS MODULE EXISTS (CR-07b)
-------------------------------
CR-07a made it structurally impossible for a guard BODY to read text: every
``GuardFacts`` field is a ``bool`` or an ``int`` (layer 1, an annotation
oracle), every value handed to one must be exactly a ``bool`` or an ``int``
(layer 2, ``__post_init__``), and a guard body's AST may contain nothing but
its parameters, comparisons, boolean/unary operators, integer constants and
``facts.<declared field>`` (layer 3, an allow-list whose ``reads`` are DERIVED
from the walk). The amendment's example, ``reviewer_said_ok`` reading a report
body, is not rejected by a rule -- it is unwritable.

That guarantee stops at the predicate. This module is where it has to be
extended BACKWARDS, because a snapshot is full of prose -- review reports,
receipts, handoff bodies, blocker details, notes -- and the derivation is the
one piece of code that touches both ends. CR-07a's docstring says as much:
"deriving the facts is where an author will be tempted to smuggle text in".

The interesting question here is NOT "can a guard read text" -- CR-07a answered
that. It is:

    **can text influence a fact's VALUE in a way that smuggles content rather
    than a decision?**

Two shapes answer yes while satisfying every one of CR-07a's layers:

* an ``int`` that is an INDEX into a table of reviewer strings. It is an
  ``int``; it passes all three layers; it carries whichever reviewer wrote the
  report.
* a ``bool`` computed by MATCHING A SUBSTRING of a report body. It is a
  ``bool``; it passes all three layers; ``reviewer_said_ok`` has simply moved
  one function upstream.

WHERE THE LINE IS, AND WHY IT IS STRUCTURAL
-------------------------------------------
The distinction is not "prose versus not prose" -- a path is not prose, and
``touches_tests`` is a legitimate substring-ish question about one. The
distinction is whether the reduction can be INVERTED:

    **An admissible reduction of text LOSES the text.** Its codomain is a
    closed, code-declared, finite vocabulary whose size does not grow with the
    data. An index into a table of reviewer names is injective on that table;
    a match on an arbitrary substring reads a bit the classifier discards.

This module makes that structural in three parts, and the parts compose into
one checkable property rather than three claims:

1. **Text enters in exactly one shape.** :class:`FactSources` is the LAST
   text-bearing record on the path to a guard, and its ``__post_init__``
   admits a field only if it is exactly a ``bool``, exactly an ``int``, or a
   ``tuple`` whose every element is a ``str``. It is an ALLOW-list, so a shape
   nobody anticipated -- a dataclass of review results, a mapping of receipts,
   a nested tuple -- is refused without anyone having had to enumerate it.
   A ``str`` FIELD is refused outright: there is no place to put a body.
2. **A tuple can be read exactly two ways.** :class:`Reducer` is a closed
   vocabulary of three reductions, and only two of them accept a tuple:
   :attr:`Reducer.COUNT` (its cardinality) and :attr:`Reducer.ANY_OF_CLASS`
   (does any element carry a :class:`PathClass`). Neither can return an
   element. The third, :attr:`Reducer.SCALAR`, passes through a reading that
   was already a ``bool``/``int`` and never sees a string at all.
3. **Every fact is produced by the table, not by hand.** :data:`FACT_RULES`
   has one rule per ``GuardFacts`` field and :func:`derive_facts` is a loop
   over it -- so the table is the derivation rather than a description of one.
   A declaration that is not verified is decoration, and this program has paid
   for that four times.

Together those give the property ``tests/test_workflow_facts.py`` actually
asserts, which is stronger than any of them alone:

    **THE DERIVATION FACTORS THROUGH THE CLASSIFIER.** Replacing every string
    in a :class:`FactSources` with the canonical representative of its
    :func:`path_classes` value must not change the derived ``GuardFacts``.

An int index changes under that substitution. A substring bool changes under
it. ``touches_security_boundary`` does not, because the representative was
chosen to carry the same classes. The property is metamorphic, so it does not
need to know what a smuggling derivation would look like -- which is the point,
since the four defects CR-06 and CR-07a shipped were all things nobody thought
to write a test for.

ADVISORY INPUTS (CR-02a)
------------------------
**No fact derives from an advisory input, and :func:`sources_from_inputs`
refuses one by class rather than by convention.** A guard narrows a compiled
outcome edge: it decides whether a task takes a transition, which is
authorization. CR-02a's rule is that a fact which authorizes an effect is
KNOWN-GOOD or UNAVAILABLE, never a cheerful third reading -- and the only way
to read an advisory input is :meth:`SnapshotInput.degraded_or`, whose whole
signature is a fallback. A fallback here would be the
unavailable-collapses-to-benign defect CR-02a exists to remove, wearing a
guard's name. So every source is read with :meth:`SnapshotInput.require`, an
absent one raises, and an ADVISORY one raises even when it is OK -- because the
fault to prevent is the day it stops being OK, and by then nothing would say
so.

WHAT THIS MODULE DOES NOT DO
----------------------------
It does not acquire anything, know a task id, or import ``daemon`` / ``config``
/ ``storage``. :data:`SOURCE_NAMES` is the acquisition CONTRACT the wiring
package must satisfy, and :class:`FactSources`'s allow-list is what enforces it
from this side: an acquisition site that registers a list of decision records,
or a review report, under one of these names cannot be consumed -- it raises
:class:`UntypedFactSource` at derivation rather than quietly reducing prose
somewhere this module's oracles cannot see.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from enum import Enum
from typing import Mapping

from .snapshot import InputClass, SnapshotInput
from .workflow_guards import GuardFacts

__all__ = [
    "FACT_RULES",
    "PATH_MARKS",
    "SOURCE_NAMES",
    "AdvisoryFactSource",
    "FactDerivationError",
    "FactRule",
    "FactSources",
    "MissingFactSource",
    "PathClass",
    "Reducer",
    "UntypedFactSource",
    "derive_facts",
    "facts_from_inputs",
    "path_classes",
    "sources_from_inputs",
]


class FactDerivationError(Exception):
    """Base for derivation failures. Every one is fail-closed: the pass gets no
    ``GuardFacts`` at all rather than a partially-trusted record."""


class MissingFactSource(FactDerivationError):
    """A source named by :data:`FACT_RULES` was not acquired for this pass."""


class AdvisoryFactSource(FactDerivationError):
    """A source was acquired as ADVISORY.

    Raised even when the descriptor is ``OK``. An advisory reading that happens
    to be present today is still a reading nobody vouched for, and the failure
    mode to prevent is the pass on which it degrades -- at which point a
    ``degraded_or`` default would have decided a transition and said nothing.
    """


class UntypedFactSource(FactDerivationError):
    """A reading arrived in a shape the derivation may not read.

    The counterpart of :class:`~nyxloom.workflow_guards.UntypedGuardFact`, one
    stage earlier: layer 2 catches text that reached a FACT, and this catches
    text that reached the SOURCE -- where a careless reduction would still have
    had a chance to turn it into a plausible-looking ``bool``.
    """


class PathClass(Enum):
    """The complete vocabulary a changed path may be reduced to.

    Three members, closed, declared in source. This is the whole codomain of
    the only reduction in this module that reads a string, which is what bounds
    how much of the path can survive it: a path collapses to a subset of these
    three, and everything else about it -- who wrote it, what it says, which
    order it arrived in -- is destroyed.
    """

    TEST = "test"
    SECURITY = "security"
    INFRA = "infra"


#: The DIRECTORY segments that mark a path as belonging to a class. Reviewed
#: source, not agent output and not (yet) project configuration -- CR-12 owns
#: gate evidence and is where a project-supplied mark set belongs. Keeping it
#: here for now is deliberate: it means the classifier's inputs are entirely
#: code, so the factorization property has no operator-controlled term in it.
PATH_MARKS: dict = {
    PathClass.TEST: frozenset({"test", "tests"}),
    PathClass.SECURITY: frozenset({"auth", "credentials", "leases", "secrets"}),
    PathClass.INFRA: frozenset({"deploy", "infra", "nyxloomd", "ntfy"}),
}


def path_classes(path: str) -> frozenset:
    """The classes a repo-relative path carries. TOTAL, and non-injective.

    A path is split on ``/`` and only its DIRECTORY segments are matched, so
    the file's own name -- the part an agent picks most freely -- reaches
    nothing. The result is a subset of :class:`PathClass`, so at most three
    bits of any path survive into a fact, whatever the path says.
    """
    segments = frozenset(path.split("/")[:-1])
    return frozenset(cls for cls, marks in PATH_MARKS.items() if segments & marks)


class Reducer(Enum):
    """The closed vocabulary of admissible reductions.

    There is no fourth member, and that is the claim this module makes. Adding
    one is a review-visible edit here AND in the oracle that checks each
    reducer against the runtime type of the source it is applied to.
    """

    #: Pass through a reading that is already a ``bool`` or an ``int``. Never
    #: sees a string: applying it to a tuple hands ``GuardFacts`` a tuple, and
    #: CR-07a's layer 2 refuses it.
    SCALAR = "scalar"
    #: The cardinality of a tuple. Invariant under any substitution of the
    #: elements, so it provably carries none of them.
    COUNT = "count"
    #: Does any element of a tuple carry a :class:`PathClass`. Factors through
    #: :func:`path_classes`, so it carries a class and not an element.
    ANY_OF_CLASS = "any_of_class"


@dataclass(frozen=True)
class FactRule:
    """How one ``GuardFacts`` field is produced from one source reading."""

    field: str
    source: str
    reducer: Reducer
    path_class: PathClass | None = None


#: One rule per ``GuardFacts`` field. Totality is checked against
#: ``fields(GuardFacts)`` -- the record CR-07a owns -- so a fact added there and
#: forgotten here fails rather than silently deriving as its default, which is
#: exactly CR-06c's shape (a branch no input reaches, at 100% coverage).
FACT_RULES: tuple = (
    FactRule("touches_tests", "changed_paths", Reducer.ANY_OF_CLASS, PathClass.TEST),
    FactRule("touches_security_boundary", "changed_paths", Reducer.ANY_OF_CLASS,
             PathClass.SECURITY),
    FactRule("touches_infra", "changed_paths", Reducer.ANY_OF_CLASS, PathClass.INFRA),
    FactRule("changed_file_count", "changed_paths", Reducer.COUNT),
    FactRule("decision_open", "decision_open", Reducer.SCALAR),
    FactRule("gate_rigor", "gate_rigor", Reducer.SCALAR),
    FactRule("effective_band", "effective_band", Reducer.SCALAR),
    FactRule("attempts_used", "attempts_used", Reducer.SCALAR),
    FactRule("attempts_budget", "attempts_budget", Reducer.SCALAR),
)

#: The acquisition contract: the snapshot input names a pass must register
#: before a guard can be evaluated. DERIVED from the rules, so a new rule
#: demands its input rather than reading a stale default.
SOURCE_NAMES: frozenset = frozenset(rule.source for rule in FACT_RULES)


@dataclass(frozen=True)
class FactSources:
    """The LAST text-bearing record on the path from a snapshot to a guard.

    Every field name is also the snapshot input name it is acquired under.
    ``changed_paths`` is the only field that may contain a string at all, and
    :data:`FACT_RULES` reads it only through :attr:`Reducer.COUNT` and
    :attr:`Reducer.ANY_OF_CLASS`.

    Note that ``decision_open`` is NOT the ``decisions_open`` inbox reading: it
    is the mechanical "an operator decision is open against THIS task" flag,
    and the singular name is the difference. The reduction from an inbox to a
    flag belongs at the acquisition site; what this record guarantees is that
    the reduction has already happened, because an inbox cannot be put here.
    """

    changed_paths: tuple = ()
    decision_open: bool = False
    gate_rigor: int = 0
    effective_band: int = 0
    attempts_used: int = 0
    attempts_budget: int = 0

    def __post_init__(self) -> None:
        for spec in fields(self):
            value = getattr(self, spec.name)
            if type(value) is bool or type(value) is int:
                continue
            if type(value) is tuple and all(type(item) is str for item in value):
                continue
            raise UntypedFactSource(
                f"FactSources.{spec.name} is a {type(value).__name__}; a source "
                f"may only be a bool, an int, or a tuple of str -- anything "
                f"else is a place an agent's words could be reduced out of "
                f"sight of the derivation oracles")


def _reduce(rule: FactRule, sources: FactSources) -> object:
    """Apply one rule. The only place a reduction happens."""
    value = getattr(sources, rule.source)
    if rule.reducer is Reducer.SCALAR:
        return value
    if rule.reducer is Reducer.COUNT:
        return len(value)
    return any(rule.path_class in path_classes(item) for item in value)


def derive_facts(sources: FactSources) -> GuardFacts:
    """Derive the complete text-free fact record.

    Total over :data:`FACT_RULES`, which is total over ``GuardFacts`` -- so
    every fact a guard can read was produced by a named, typed reduction, and
    none was left at a default nobody chose.
    """
    return GuardFacts(**{rule.field: _reduce(rule, sources) for rule in FACT_RULES})


def sources_from_inputs(inputs: Mapping[str, SnapshotInput]) -> FactSources:
    """Read every declared source from the pass's acquisitions, or refuse.

    Fail-closed three ways, in the order a fault is most likely to be
    introduced: a source nobody acquired, a source acquired as advisory, and a
    source acquired authoritatively but not OK (``require`` raises
    :class:`~nyxloom.snapshot.SnapshotUnavailable`). None of the three has a
    default, because a default here is a transition taken on a fact nobody
    vouched for.
    """
    values: dict = {}
    for name in sorted(SOURCE_NAMES):
        descriptor = inputs.get(name)
        if descriptor is None:
            raise MissingFactSource(
                f"guard fact source {name!r} was not acquired; the derivation "
                f"requires {sorted(SOURCE_NAMES)}")
        if descriptor.input_class is not InputClass.AUTHORITATIVE:
            raise AdvisoryFactSource(
                f"guard fact source {name!r} is {descriptor.input_class.value}; "
                f"a guard decides a transition, so it may not read a fact that "
                f"is allowed to degrade silently")
        values[name] = descriptor.require()
    return FactSources(**values)


def facts_from_inputs(inputs: Mapping[str, SnapshotInput]) -> GuardFacts:
    """The seam: authoritative snapshot in, text-free guard facts out."""
    return derive_facts(sources_from_inputs(inputs))
