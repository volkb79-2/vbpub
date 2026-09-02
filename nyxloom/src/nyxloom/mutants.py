"""Pure mutant-generation engine -- extracted from mutation_gate.py (nyxloom-
P98, 2026-09-02) when the rest of that module (the gate-judgment half:
`evaluate`, `_fanout_safe`, `_run_is_killed*`, `_resolve_added_lines`, and the
CLI) was retired along with `coverage_gate.py` and `gate_canary.py`. This is
the one piece with a real, load-bearing external consumer:
`tools/remote_mutation_audit.py` imports `Mutant`/`generate_mutants` to build
mutant jobs for its own remote-worker fan-out, entirely independent of the
retired gate/CLI machinery. `generate_mutants` calls nothing outside this
module (verified before extraction, nyxloom-P98 escalate_if re-check).

The mutation catalogue is `compare-swap`, `boolop-swap`, `bool-const-flip`, and
`falsy-swap`. The last one changes a direct falsy return value to a different
falsy value, exposing error paths that degrade to a benign result.
"""

from __future__ import annotations

import ast
import copy
from dataclasses import dataclass

# --------------------------------------------------------------------------- #
# Comparison operator swaps (each maps to its "boundary neighbour" or negation)
# --------------------------------------------------------------------------- #

_COMPARE_SWAP: dict[type, type] = {
    ast.Lt: ast.LtE,
    ast.LtE: ast.Lt,
    ast.Gt: ast.GtE,
    ast.GtE: ast.Gt,
    ast.Eq: ast.NotEq,
    ast.NotEq: ast.Eq,
    ast.Is: ast.IsNot,
    ast.IsNot: ast.Is,
}

_BOOLOP_SWAP: dict[type, type] = {
    ast.And: ast.Or,
    ast.Or: ast.And,
}


# --------------------------------------------------------------------------- #
# Data types
# --------------------------------------------------------------------------- #

@dataclass
class Mutant:
    lineno: int
    operator: str          # "compare-swap" | "boolop-swap" | "bool-const-flip" | "falsy-swap"
    description: str       # e.g. "Lt->LtE", "And->Or", "True->False"
    mutated_source: str    # the FULL file source with exactly this one mutation


# --------------------------------------------------------------------------- #
# PURE CORE — generate_mutants
# --------------------------------------------------------------------------- #

def generate_mutants(source: str, target_lines: set[int]) -> list[Mutant]:
    """Parse `source`; for every mutable node whose `lineno` is in `target_lines`,
    emit one Mutant per applicable mutation. Deterministic order (lineno, operator,
    description). See module docstring for the mutation catalogue.

    Returns [] on parse failure."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []

    # Collect all mutation sites deterministically.
    # Each site: (sort_key, lineno, operator, description, target_cls_or_value,
    #             node_idx, op_idx_or_0)
    # node_idx tracks which Nth node of that type on the line (disambiguation).
    # op_idx_or_0 is the operator index within a Compare node's .ops list;
    # 0 for BoolOp/Constant (ignored).
    sites: list[tuple[tuple[int, int, int], int, str, str, type | object, int, int]] = []

    # Per-line counters for disambiguation
    _compare_idx: dict[int, int] = {}
    _boolop_idx: dict[int, int] = {}
    _const_idx: dict[int, int] = {}
    _falsy_idx: dict[int, int] = {}

    def _falsy_swap_target(value: ast.expr) -> ast.expr | None:
        """Return a distinct falsy AST value for a direct return value."""
        if isinstance(value, ast.Constant):
            if isinstance(value.value, bool):
                return None
            if value.value is None:
                return ast.List(elts=[], ctx=ast.Load())
            if value.value == 0 or value.value == "" or value.value == b"":
                return ast.Constant(value=None)
            return None
        if isinstance(value, (ast.List, ast.Tuple, ast.Set)) and not value.elts:
            return ast.Constant(value=None)
        if isinstance(value, ast.Dict) and not value.keys:
            return ast.Constant(value=None)
        return None

    class _Collector(ast.NodeVisitor):
        def visit_Compare(self, node):
            if node.lineno in target_lines:
                idx = _compare_idx.get(node.lineno, 0)
                _compare_idx[node.lineno] = idx + 1
                for i, op in enumerate(node.ops):
                    op_cls = type(op)
                    if op_cls in _COMPARE_SWAP:
                        target_cls = _COMPARE_SWAP[op_cls]
                        sites.append((
                            (node.lineno, 0, idx * 10 + i),  # type 0 = compare
                            node.lineno,
                            "compare-swap",
                            f"{op_cls.__name__}->{target_cls.__name__}",
                            target_cls,
                            idx,   # which compare node on this line
                            i,     # which operator within .ops
                        ))
            self.generic_visit(node)

        def visit_BoolOp(self, node):
            if node.lineno in target_lines:
                idx = _boolop_idx.get(node.lineno, 0)
                _boolop_idx[node.lineno] = idx + 1
                op_cls = type(node.op)
                if op_cls in _BOOLOP_SWAP:
                    target_cls = _BOOLOP_SWAP[op_cls]
                    sites.append((
                        (node.lineno, 1, idx),  # type 1 = boolop
                        node.lineno,
                        "boolop-swap",
                        f"{op_cls.__name__}->{target_cls.__name__}",
                        target_cls,
                        idx,  # which boolop node on this line
                        0,
                    ))
            self.generic_visit(node)

        def visit_Constant(self, node):
            if isinstance(node.value, bool) and node.lineno in target_lines:
                idx = _const_idx.get(node.lineno, 0)
                _const_idx[node.lineno] = idx + 1
                new_val = not node.value
                sites.append((
                    (node.lineno, 2, idx),  # type 2 = bool const
                    node.lineno,
                    "bool-const-flip",
                    f"{node.value}->{new_val}",
                    new_val,
                    idx,  # which bool constant on this line
                    0,
                ))
            self.generic_visit(node)

        def visit_Return(self, node):
            if node.lineno in target_lines:
                target_val = _falsy_swap_target(node.value)
                if target_val is not None:
                    idx = _falsy_idx.get(node.lineno, 0)
                    _falsy_idx[node.lineno] = idx + 1
                    sites.append((
                        (node.lineno, 3, idx),  # type 3 = falsy return
                        node.lineno,
                        "falsy-swap",
                        f"{ast.unparse(node.value)}->{ast.unparse(target_val)}",
                        target_val,
                        idx,  # which falsy return on this line
                        0,
                    ))
            self.generic_visit(node)

    _Collector().visit(tree)
    sites.sort(key=lambda s: s[0])

    mutants: list[Mutant] = []
    for sort_key, lineno, operator, description, target_val, node_idx, op_i in sites:
        tree_copy = copy.deepcopy(tree)

        class _Mutator(ast.NodeTransformer):
            """Apply exactly one mutation to the deep-copied tree."""

            _compare_visited: int = 0

            def visit_Compare(self, node):
                if node.lineno == lineno and operator == "compare-swap":
                    if self._compare_visited == node_idx:
                        node.ops[op_i] = target_val()  # type: ignore[operator]
                    self._compare_visited += 1
                return self.generic_visit(node)

            _boolop_visited: int = 0

            def visit_BoolOp(self, node):
                if node.lineno == lineno and operator == "boolop-swap":
                    if self._boolop_visited == node_idx:
                        node.op = target_val()  # type: ignore[operator]
                    self._boolop_visited += 1
                return self.generic_visit(node)

            _const_visited: int = 0

            def visit_Constant(self, node):
                if (
                    isinstance(node.value, bool)
                    and node.lineno == lineno
                    and operator == "bool-const-flip"
                ):
                    if self._const_visited == node_idx:
                        node.value = target_val  # type: ignore[assignment]
                    self._const_visited += 1
                return self.generic_visit(node)

            _falsy_visited: int = 0

            def visit_Return(self, node):
                if node.lineno == lineno and operator == "falsy-swap":
                    if _falsy_swap_target(node.value) is not None:
                        if self._falsy_visited == node_idx:
                            node.value = copy.deepcopy(target_val)  # type: ignore[assignment]
                        self._falsy_visited += 1
                return self.generic_visit(node)

        _Mutator().visit(tree_copy)
        ast.fix_missing_locations(tree_copy)
        mutated_source = ast.unparse(tree_copy)

        mutants.append(Mutant(
            lineno=lineno,
            operator=operator,
            description=description,
            mutated_source=mutated_source,
        ))

    return mutants
