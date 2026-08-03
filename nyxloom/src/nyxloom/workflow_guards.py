"""Workflow guards: registered pure predicates over a TEXT-FREE typed snapshot.

WHY THIS MODULE EXISTS (CR-07a)
-------------------------------
The parent plan's §4.3 says a workflow manifest may not carry executable code,
and that "guards name registered pure predicates over a typed snapshot". The
amendment then names the hole that wording leaves open, and it is the whole
reason this module is a separate file with its own oracles::

    a guard named `reviewer_said_ok` reading a report body would satisfy every
    listed condition and defeat the boundary.

A registry of names plus a purity claim does not stop that guard. The predicate
would be registered, pure, and total; it would simply be reading agent-authored
prose and returning authority derived from it -- exactly what CR-03 deleted
when it removed ``_parse_review_verdict``.

So the boundary here is not "guards are documented as mechanical". It is:

    **THERE IS NO FIELD A GUARD COULD READ THAT COULD HOLD TEXT.**

:class:`GuardFacts` is the ONLY argument a guard receives, and every one of its
fields is a ``bool`` or an ``int``. A report body, a verdict string, a commit
message, a reviewer's sentence -- none of them can be put into this record at
all. ``reviewer_said_ok`` is not rejected by a rule; it is unwritable, because
there is nothing for it to read.

THREE STRUCTURAL LAYERS, NOT ONE
--------------------------------
Each layer fails independently, which is the point -- CR-05's ``emits`` and
CR-06's ``EXCLUSIVE_ACTIONS`` were both single declarations that nothing
verified, and both were wrong when finally checked.

1. **The type.** Every :class:`GuardFacts` annotation is ``bool`` or ``int``.
   ``tests/test_workflow_guards.py`` reads the annotations and fails on any
   other type, so widening the record to ``str`` is a review-visible edit to
   this module AND to that oracle, never a quiet field addition.
2. **The value.** :meth:`GuardFacts.__post_init__` rejects any value whose type
   is not exactly ``bool`` or ``int`` -- so even a field annotated ``object``
   or ``Any`` (a future author's shortcut) cannot carry a string at runtime.
   ``str`` is not the only thing refused: tuples, enums and paths are refused
   too, because "not text" is not the same claim as "not a container that could
   hold text".
3. **The body.** A registered guard's AST may contain nothing but its
   parameters, comparisons, boolean/unary operators, integer constants, and
   attribute reads of the form ``facts.<declared field>``. No calls, no
   imports, no subscripts, no attribute chains. A guard therefore cannot reach
   a module global, a file, a clock, or another object that might carry text.
   This is the same shape as ``planning.py``'s purity oracle, which found a
   real violation two modules away behind an ordinary helper call.

``GuardSpec.reads`` is DERIVED from the body by the oracle and compared against
the declaration, for the reason ``planning.py``'s docstring states once and
this program has now paid for three times: a declaration that is not verified
is decoration.

WHAT A GUARD IS FOR
-------------------
A guard narrows a compiled outcome edge: "this outcome takes this edge only
when the mechanical fact holds". CR-07a compiles and type-checks them; CR-07b
derives :class:`GuardFacts` from the authoritative snapshot and evaluates them.
Deriving the facts is where an author will be tempted to smuggle text in -- and
the derivation will have to produce ``bool``/``int`` or fail construction,
which is layer 2 doing its job on the other side of the package boundary.

This module imports nothing from the package. It is a leaf, so a guard can be
exercised without a snapshot, a config, or a store.
"""

from __future__ import annotations

from dataclasses import dataclass, fields


class GuardError(Exception):
    """Base for guard resolution failures (raised at COMPILE time, never at
    plan time -- an unresolvable guard must stop a workflow being compiled,
    not surface as a runtime surprise)."""


class UnknownGuard(GuardError):
    """A manifest named a guard that is not in :data:`GUARD_REGISTRY`."""


class GuardArgumentError(GuardError):
    """A guard was given an argument it does not take, or was denied one it
    requires, or was given one of the wrong type."""


class UntypedGuardFact(GuardError):
    """A :class:`GuardFacts` field was handed a value that is not exactly a
    ``bool`` or an ``int``.

    This is the runtime half of the text boundary. It is deliberately a hard
    failure rather than a coercion: a fact that arrives as text has already
    lost the property that makes it a fact, and quietly ``str()``-ing or
    truthiness-testing it would put agent prose back inside the guard input
    under a different name.
    """


@dataclass(frozen=True)
class GuardFacts:
    """The complete, mechanical, TEXT-FREE snapshot a guard may read.

    Every field is a fact the control plane establishes for itself -- from the
    diff, from the store, from configuration -- and NONE of them is anything an
    agent wrote. See the module docstring for why the field types are the
    boundary rather than a convention.

    Adding a field is allowed. Adding a field that is not a ``bool`` or an
    ``int`` is not, and both the annotation oracle and
    :meth:`__post_init__` will say so.
    """

    #: The diff touches at least one test file.
    touches_tests: bool = False
    #: The diff touches a path the project marks as a security boundary.
    touches_security_boundary: bool = False
    #: The diff touches a path the project marks as infrastructure.
    touches_infra: bool = False
    #: An operator decision is open against this task.
    decision_open: bool = False
    #: The configured rigor of the gate that ran, as an integer band.
    gate_rigor: int = 0
    #: The task's effective capability band after promotion.
    effective_band: int = 0
    #: Attempts already consumed against this task's retry budget.
    attempts_used: int = 0
    #: The declared retry budget for this task.
    attempts_budget: int = 0
    #: Files changed in the artifact under consideration.
    changed_file_count: int = 0

    def __post_init__(self) -> None:
        for spec in fields(self):
            value = getattr(self, spec.name)
            if type(value) is not bool and type(value) is not int:
                raise UntypedGuardFact(
                    f"GuardFacts.{spec.name} must be a bool or an int, got "
                    f"{type(value).__name__} -- a guard may not read anything "
                    f"an agent could have authored")


@dataclass(frozen=True, order=True)
class GuardRef:
    """A compiled reference to a registered guard, with its bound argument.

    Ordered so a compiled edge set has a stable canonical form.
    """

    name: str
    arg: int | None = None

    def as_canonical(self) -> dict:
        out: dict = {"name": self.name}
        if self.arg is not None:
            out["arg"] = self.arg
        return out


# --- the registered predicates -------------------------------------------
#
# Each is a module-level function of exactly (facts, arg). The bodies are
# deliberately trivial: the AST oracle admits only comparisons, boolean and
# unary operators, integer constants, and `facts.<field>` reads, so a guard
# that needs more than that is a guard that wants to leave the boundary.


def _touches_tests(facts: GuardFacts, arg: int | None) -> bool:
    return facts.touches_tests


def _touches_security_boundary(facts: GuardFacts, arg: int | None) -> bool:
    return facts.touches_security_boundary


def _touches_infra(facts: GuardFacts, arg: int | None) -> bool:
    return facts.touches_infra


def _decision_open(facts: GuardFacts, arg: int | None) -> bool:
    return facts.decision_open


def _gate_rigor_below(facts: GuardFacts, arg: int | None) -> bool:
    return facts.gate_rigor < arg


def _effective_band_at_least(facts: GuardFacts, arg: int | None) -> bool:
    return facts.effective_band >= arg


def _attempts_remaining(facts: GuardFacts, arg: int | None) -> bool:
    return facts.attempts_used < facts.attempts_budget


def _diff_larger_than(facts: GuardFacts, arg: int | None) -> bool:
    return facts.changed_file_count > arg


@dataclass(frozen=True)
class GuardSpec:
    """What one guard IS.

    ``reads`` names the :class:`GuardFacts` fields the body touches. It is
    DERIVED from the AST by ``tests/test_workflow_guards.py`` and compared
    against this declaration -- over-broad fails exactly like missing.
    """

    name: str
    predicate: object          # Callable[[GuardFacts, int | None], bool]
    arg_type: type | None      # None == the guard takes no argument
    reads: frozenset


GUARD_REGISTRY: dict[str, GuardSpec] = {
    "touches_tests": GuardSpec(
        "touches_tests", _touches_tests, None, frozenset({"touches_tests"})),
    "touches_security_boundary": GuardSpec(
        "touches_security_boundary", _touches_security_boundary, None,
        frozenset({"touches_security_boundary"})),
    "touches_infra": GuardSpec(
        "touches_infra", _touches_infra, None, frozenset({"touches_infra"})),
    "decision_open": GuardSpec(
        "decision_open", _decision_open, None, frozenset({"decision_open"})),
    "gate_rigor_below": GuardSpec(
        "gate_rigor_below", _gate_rigor_below, int, frozenset({"gate_rigor"})),
    "effective_band_at_least": GuardSpec(
        "effective_band_at_least", _effective_band_at_least, int,
        frozenset({"effective_band"})),
    "attempts_remaining": GuardSpec(
        "attempts_remaining", _attempts_remaining, None,
        frozenset({"attempts_used", "attempts_budget"})),
    "diff_larger_than": GuardSpec(
        "diff_larger_than", _diff_larger_than, int,
        frozenset({"changed_file_count"})),
}

#: The field names a guard body may read. Derived from the record itself so a
#: new fact joins the allowed set the moment it is declared, and a REMOVED one
#: leaves it -- which is what makes the AST oracle's `facts.<field>` rule a
#: check against the real type rather than against a second hand-kept list.
GUARD_FACT_FIELDS: frozenset = frozenset(f.name for f in fields(GuardFacts))


def resolve_guard(name: str, arg: object = None) -> GuardRef:
    """Resolve a manifest's guard reference, or raise.

    Raises :class:`UnknownGuard` for a name outside the registry and
    :class:`GuardArgumentError` for an argument mismatch. Both are compile-time
    conditions (parent §4.3 rejection condition 1).
    """
    spec = GUARD_REGISTRY.get(name)
    if spec is None:
        raise UnknownGuard(
            f"unknown guard {name!r}; registered guards: {sorted(GUARD_REGISTRY)}")
    if spec.arg_type is None:
        if arg is not None:
            raise GuardArgumentError(f"guard {name!r} takes no argument, got {arg!r}")
        return GuardRef(name, None)
    if type(arg) is not int:
        raise GuardArgumentError(
            f"guard {name!r} requires an integer argument, got {arg!r}")
    return GuardRef(name, arg)


def evaluate(ref: GuardRef, facts: GuardFacts) -> bool:
    """Evaluate a resolved guard against a typed snapshot.

    Total and pure: the reference was resolved at compile time, so the lookup
    cannot miss and the argument cannot be of the wrong type.
    """
    spec = GUARD_REGISTRY[ref.name]
    return bool(spec.predicate(facts, ref.arg))
