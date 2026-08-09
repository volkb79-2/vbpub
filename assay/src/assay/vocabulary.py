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
"""

from __future__ import annotations

__all__ = ["MUTATION_OPERATORS"]

#: The closed, four-value mutation catalogue (A-112/A-114), adopted verbatim
#: from ``/workspaces/vbpub/nyxloom/src/nyxloom/mutation_gate.py`` and
#: DESIGN-GUIDE §11's own TOML example. Ordered, and the order is normative
#: for the shipped schema's own enum.
MUTATION_OPERATORS: tuple[str, ...] = (
    "compare-swap",
    "boolop-swap",
    "bool-const-flip",
    "falsy-swap",
)
