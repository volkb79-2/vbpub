#!/usr/bin/env python3
"""R11 hotspot profile: classify every candidate site into a site kind, per
function and overall.

Kinds:
  none-check   compare-swap on `is None` / `is not None`
  dispatch     compare-swap Eq/NotEq/Is/IsNot against a constant, enum member
               (Attribute with UPPER name) or string -- a dispatch/tag test
  boundary     compare-swap on an ordering op (< <= > >=)
  equality     other Eq/NotEq/Is/IsNot (two computed operands)
  boolop       and/or token
  flag-kwarg   bool constant passed as a keyword argument / keyword default
  flag-decor   bool constant inside a decorator (dataclass flags)
  flag-other   any other bool constant (assignment, return, positional arg)
  falsy        falsy-swap return

Usage: hotspots.py <src_root> [top_n]
"""
import ast
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from candidates import CMP  # noqa: E402

ORDER = (ast.Lt, ast.LtE, ast.Gt, ast.GtE)


def const_like(e):
    if isinstance(e, ast.Constant):
        return True
    if isinstance(e, ast.Attribute) and e.attr.lstrip("_").isupper():
        return True
    if isinstance(e, ast.Name) and e.id.lstrip("_").isupper():
        return True
    return False


def classify(tree):
    parent = {}
    for p in ast.walk(tree):
        for ch in ast.iter_child_nodes(p):
            parent[ch] = p
    decor = set()
    for n in ast.walk(tree):
        if isinstance(n, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            for d in n.decorator_list:
                for x in ast.walk(d):
                    decor.add(x)
    kw_vals = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.keyword):
            kw_vals.add(n.value)
        if isinstance(n, ast.arguments):
            for d in n.kw_defaults + n.defaults:
                if d is not None:
                    kw_vals.add(d)
    out = []  # (node, kind)
    for n in ast.walk(tree):
        if isinstance(n, ast.Compare):
            operands = [n.left] + n.comparators
            for i, op in enumerate(n.ops):
                if not isinstance(op, CMP):
                    continue
                l, r = operands[i], operands[i + 1]
                if isinstance(op, (ast.Is, ast.IsNot)) and (isinstance(r, ast.Constant) and r.value is None):
                    k = "none-check"
                elif isinstance(op, ORDER):
                    k = "boundary"
                elif const_like(r) or const_like(l):
                    k = "dispatch"
                else:
                    k = "equality"
                out.append((n, k))
        elif isinstance(n, ast.BoolOp):
            for _ in n.values[1:]:
                out.append((n, "boolop"))
        elif isinstance(n, ast.Constant) and isinstance(n.value, bool):
            if n in decor:
                k = "flag-decor"
            elif n in kw_vals:
                k = "flag-kwarg"
            else:
                k = "flag-other"
            out.append((n, k))
        elif isinstance(n, ast.Return):
            from candidates import falsy
            if falsy(n.value):
                out.append((n, "falsy"))
    return out


def main():
    root = Path(sys.argv[1])
    top = int(sys.argv[2]) if len(sys.argv) > 2 else 20
    overall = Counter()
    per_func = defaultdict(Counter)
    meta = {}
    for p in sorted(root.rglob("*.py")):
        if "__pycache__" in p.parts:
            continue
        rel = str(p.relative_to(root))
        tree = ast.parse(p.read_text(encoding="utf-8"))
        # innermost function per node
        owner = {}
        def rec(node, fn):
            for ch in ast.iter_child_nodes(node):
                if isinstance(ch, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    q = ch.name if fn is None else f"{fn}.{ch.name}"
                    meta[(rel, q)] = (ch.lineno, ch.end_lineno)
                    owner[ch] = fn or "<module>"
                    rec(ch, q)
                elif isinstance(ch, ast.ClassDef):
                    q = ch.name if fn is None else f"{fn}.{ch.name}"
                    owner[ch] = fn or "<module>"
                    rec(ch, q)
                else:
                    owner[ch] = fn or "<module>"
                    rec(ch, fn)
        rec(tree, None)
        for node, kind in classify(tree):
            overall[kind] += 1
            per_func[(rel, owner.get(node, "<module>"))][kind] += 1
    tot = sum(overall.values())
    print("overall", tot, {k: f"{v} ({100*v/tot:.0f}%)" for k, v in overall.most_common()})
    rows = sorted(per_func.items(), key=lambda kv: -sum(kv[1].values()))
    for (rel, q), c in rows[:top]:
        lo, hi = meta.get((rel, q), (0, 0))
        print(f"{sum(c.values()):4d} {rel}:{lo}-{hi} {q}  {dict(c.most_common())}")


if __name__ == "__main__":
    main()
