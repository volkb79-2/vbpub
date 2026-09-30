#!/usr/bin/env python3
"""R11: break the 907 `is None`/`is not None` sites into shapes."""
import ast, sys
from collections import Counter
from pathlib import Path
root = Path(sys.argv[1])
c = Counter()
for p in sorted(root.rglob("*.py")):
    if "__pycache__" in p.parts: continue
    tree = ast.parse(p.read_text())
    parent = {ch: n for n in ast.walk(tree) for ch in ast.iter_child_nodes(n)}
    for n in ast.walk(tree):
        if not isinstance(n, ast.Compare): continue
        for op, r in zip(n.ops, n.comparators):
            if isinstance(op, (ast.Is, ast.IsNot)) and isinstance(r, ast.Constant) and r.value is None:
                q = parent.get(n)
                while isinstance(q, (ast.BoolOp, ast.UnaryOp)): q = parent.get(q)
                if isinstance(q, ast.Assert): k = "assert (type narrowing)"
                elif isinstance(q, ast.IfExp): k = "x if x is not None else default"
                elif isinstance(q, (ast.If, ast.While)): k = "if-guard"
                elif isinstance(q, (ast.Return,)): k = "return expr"
                elif isinstance(q, (ast.Assign, ast.AnnAssign)): k = "assigned bool"
                elif isinstance(q, ast.comprehension): k = "comprehension filter"
                else: k = "other:" + type(q).__name__
                in_post = any(isinstance(a, ast.FunctionDef) and a.name == "__post_init__" for a in [])
                c[k] += 1
tot = sum(c.values())
print(tot, c.most_common())
