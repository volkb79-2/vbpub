"""The closed mutation-operator vocabulary, in one cycle-safe leaf module.

P21 work item 2. Before this module the same four operator names lived in
three places that could drift independently: :data:`assay.mutation.
MUTATION_OPERATORS` (a ``frozenset``, so it carried no order),
``config._load_mutation``'s load-time cross-check (which reached it through a
function-body-local import to dodge a real ``config -> mutation -> config``
cycle), and the shipped JSON Schema's own ``mutation_operator`` enum. The
model and the raw verifier closed neither: :class:`~assay.verdict.
MutantOutcome.operator` and :class:`~assay.verdict.JudgmentR2.operators` both
accepted any non-empty string, so an artifact naming an operator no adapter
can produce was schema-invalid and model-valid at the same time.

This module exists to make that impossible by construction. It imports
NOTHING -- not even :mod:`assay.errors` -- so every layer that needs the
vocabulary can import it at module level without opening a cycle, which is
what removes the deferred-import workaround from :mod:`assay.config` and lets
:mod:`assay.verdict` close the vocabulary without importing
:mod:`assay.mutation`'s execution orchestration (that import direction is the
cycle A-114 originally cited as the reason the model could NOT close it).

The value is an ORDERED tuple, not a set. Order is part of the contract:
``judgment.r2.operators`` records the lane's own declared, order-preserving
selection, and the shipped schema's enum is required to list these exact
members in this exact order (``tests/test_verdict_schema_is_packaged.py``
asserts set equality AND order, so a hand-edited schema cannot drift from the
tuple below without a red test).

**P33/V5-2: the vocabulary is language-qualified and closed PER LANGUAGE.**
Through v4 it was one flat four-value list of bare Python operator names, so
nothing could express SQL's catalogue at all and a flat extension would have
let a Python lane declare ``drop-check``. v5 qualifies every name with its
language and closes each language separately, which makes a cross-language
declaration a load-time error rather than a run that reports operators it
could not possibly have applied.

The rename is a RENAME, not an alias (A-220/V5-2): ``compare-swap`` has no
accepted bare spelling anywhere -- not in config, not in the artifact, not
here. That absence is exactly what makes this a version bump rather than a
widening, and it is why a v4 artifact is refused on its version rather than
re-read under the new vocabulary.
"""

from __future__ import annotations

from types import MappingProxyType
from typing import Mapping

__all__ = [
    "MUTATION_OPERATORS",
    "MUTATION_OPERATORS_BY_LANGUAGE",
    "WITHDRAWN_MUTATION_OPERATORS",
    "operator_language",
]

#: Operators that are still SPELLABLE in a schema-v7 artifact but that no
#: lane may declare and no adapter produces (B034/A-326).
#:
#: B015's two "semantic" Python families shipped in 2.3.0 and were measured
#: in the 2.1.0->2.3.0 review-gap audit to be a byte-identical SUBSET of
#: ``python:compare-swap``'s own output: same span, same replacement bytes,
#: 87 sites over ``src/assay/**.py``, zero of them new. Co-selecting them
#: with ``compare-swap`` emitted every shared site twice, and the enum
#: predicate matched any ``name.attr`` access rather than an enum member.
#: They are withdrawn from the PRODUCER and from lane DECLARATION.
#:
#: They are NOT removed from :data:`MUTATION_OPERATORS_BY_LANGUAGE` or from
#: the packaged schema's ``oneOf``, and that is a deliberate, reasoned
#: asymmetry rather than an oversight (A-326, applying A-324's own test):
#: released ``assay verify`` builds ACCEPT a v7 document naming either
#: operator, and released ``assay run`` builds EMITTED such documents, so
#: deleting the spellings would stop real artifacts from verifying -- the
#: exact breakage A-324 requires a schema-version bump for. The spelling
#: therefore stays until the next bump, where it is dropped; the BEHAVIOUR
#: goes now. This module's own contract is unchanged by that: membership in
#: the catalogue says a name is spellable, never that a lane may use it.
WITHDRAWN_MUTATION_OPERATORS: frozenset[str] = frozenset(
    {"python:uuid-equality-swap", "python:enum-comparison-swap"}
)

#: The closed per-language mutation catalogue (A-112/A-114/A-221). Ordered,
#: and the order is normative for the shipped schema's own per-language
#: ``oneOf`` branches.
#:
#: * ``python`` -- the original four, adopted verbatim from
#:   ``/workspaces/vbpub/nyxloom/src/nyxloom/mutation_gate.py`` and
#:   DESIGN-GUIDE §11's own TOML example, now qualified. B015 added two
#:   further "semantic" families, ``python:uuid-equality-swap`` and
#:   ``python:enum-comparison-swap``; both are **withdrawn** (B034/A-326) and
#:   listed here only so v7 artifacts that already name them keep verifying --
#:   see :data:`WITHDRAWN_MUTATION_OPERATORS`. The four producible Python
#:   operators are the original four.
#: * ``go`` (A-221) -- three faithful analogues of the Python catalogue,
#:   transcribed under A-112 rather than invented. There is deliberately NO
#:   ``falsy-swap`` analogue: Python's exploits duck-typed truthiness, while
#:   every Go condition is a strict ``bool``, so a ``nil``-swap or zero-value
#:   operator would be new, unmeasured mutation-testing design. Arithmetic
#:   swap, increment/decrement and statement removal are excluded for the
#:   same reason. **Declarable but unproducible in this build (A-225):** no
#:   adapter generates them and ``cli._built_in_registry`` registers Python
#:   only, so a Go R2 lane is refused ``ERROR``/``BAD_LANE_CONFIG`` before
#:   anything executes. P29 lands the producer.
#: * ``sql`` (A-220) -- the seven DDL operators v5 exists to make
#:   expressible. ``weaken-delete-action`` names the operator CLASS
#:   (``RESTRICT``->``CASCADE``, ``RESTRICT``->``NO ACTION``) rather than one
#:   instance, which keeps the catalogue honest and finite. Also declarable
#:   but unproducible here; P34 lands the adapter.
MUTATION_OPERATORS_BY_LANGUAGE: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {
        "python": (
            "python:compare-swap",
            "python:boolop-swap",
            "python:bool-const-flip",
            "python:falsy-swap",
            "python:uuid-equality-swap",
            "python:enum-comparison-swap",
        ),
        "go": (
            "go:compare-swap",
            "go:boolop-swap",
            "go:bool-const-flip",
        ),
        "sql": (
            "sql:drop-check",
            "sql:drop-unique",
            "sql:drop-not-null",
            "sql:drop-foreign-key",
            "sql:weaken-delete-action",
            "sql:drop-trigger",
            "sql:widen-check-in",
        ),
    }
)

#: Every qualified operator name, in schema order (python, then go, then
#: sql). This is the whole closed catalogue -- membership here says a name is
#: spellable, NOT that the lane declaring it may use it. The
#: prefix-equals-resolved-language rule is a cross-object relation and lives
#: in :mod:`assay.config` (at load) and in the model and raw verifier (for an
#: artifact), which is where the language is actually known.
MUTATION_OPERATORS: tuple[str, ...] = tuple(
    operator
    for operators in MUTATION_OPERATORS_BY_LANGUAGE.values()
    for operator in operators
)


def operator_language(operator: str) -> str | None:
    """The language *operator* is qualified with, or ``None`` if it carries
    no ``<language>:`` prefix at all.

    Derived by splitting rather than by table lookup, so an operator this
    module does not know still reports the language it CLAIMS -- which is
    what lets a caller say "a python lane cannot declare ``sql:drop-check``"
    instead of the much weaker "that is not a known operator".
    """
    language, separator, _ = operator.partition(":")
    if not separator or not language:
        return None
    return language
