#!/usr/bin/env python3
"""R11: static re-implementation of assay's four python mutation operators'
SITE COUNT (assay/src/assay/adapters/python.py:_candidate_sites), per file and
per innermost enclosing function. No line filter (whole_target mode).

compare-swap: one site per Compare op in {Lt,LtE,Gt,GtE,Eq,NotEq,Is,IsNot}
boolop-swap:  len(values)-1 per BoolOp
bool-const-flip: one per Constant whose value is a bool
falsy-swap:   one per Return whose value is None-const / 0 / "" / b"" /
              empty List/Tuple/Set / empty Dict (bools excluded)

Usage: candidates.py <src_root> <out.json>
"""
import ast
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

CMP = (ast.Lt, ast.LtE, ast.Gt, ast.GtE, ast.Eq, ast.NotEq, ast.Is, ast.IsNot)


def falsy(value):
    if value is None:
        return False
    if isinstance(value, ast.Constant):
        v = value.value
        if isinstance(v, bool):
            return False
        if v is None:
            return True
        try:
            return v == 0 or v == "" or v == b""
        except Exception:
            return False
    if isinstance(value, (ast.List, ast.Tuple, ast.Set)) and not value.elts:
        return True
    if isinstance(value, ast.Dict) and not value.keys:
        return True
    return False


def sites_of(node):
    out = []
    if isinstance(node, ast.Compare):
        for op in node.ops:
            if isinstance(op, CMP):
                out.append(("compare-swap", node.lineno))
    elif isinstance(node, ast.BoolOp):
        for v in node.values[1:]:
            out.append(("boolop-swap", v.lineno))
    elif isinstance(node, ast.Constant) and isinstance(node.value, bool):
        out.append(("bool-const-flip", node.lineno))
    elif isinstance(node, ast.Return) and falsy(node.value):
        out.append(("falsy-swap", node.value.lineno))
    return out


def walk_with_scope(tree):
    """Yield (node, qualname-of-innermost-function-or-<module>, func_node)."""
    stack = [(tree, "<module>", None)]
    while stack:
        node, qual, fn = stack.pop()
        yield node, qual, fn
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                cq = child.name if qual == "<module>" else f"{qual}.{child.name}"
                stack.append((child, cq, child))
            elif isinstance(child, ast.ClassDef):
                cq = child.name if qual == "<module>" else f"{qual}.{child.name}"
                stack.append((child, cq, fn))
            else:
                stack.append((child, qual, fn))


def main():
    root = Path(sys.argv[1])
    out = sys.argv[2]
    files = sorted(p for p in root.rglob("*.py") if "__pycache__" not in p.parts)
    per_file = {}
    per_func = {}
    sites = []
    total = Counter()
    for p in files:
        rel = str(p.relative_to(root.parent.parent)) if root.name == "assay" else str(p)
        text = p.read_text(encoding="utf-8")
        tree = ast.parse(text)
        fc = Counter()
        for node, qual, fn in walk_with_scope(tree):
            for op, line in sites_of(node):
                fc[op] += 1
                total[op] += 1
                key = f"{rel}::{qual}"
                if key not in per_func:
                    per_func[key] = {
                        "file": rel,
                        "qual": qual,
                        "lineno": fn.lineno if fn else 0,
                        "end": fn.end_lineno if fn else 0,
                        "ops": Counter(),
                    }
                per_func[key]["ops"][op] += 1
                sites.append({"file": rel, "line": line, "op": op, "func": qual})
        per_file[rel] = {"lines": text.count("\n"), "ops": dict(fc), "n": sum(fc.values())}
    for v in per_func.values():
        v["n"] = sum(v["ops"].values())
        v["ops"] = dict(v["ops"])
    json.dump(
        {"total": dict(total), "n": sum(total.values()), "per_file": per_file,
         "per_func": per_func, "sites": sites},
        open(out, "w"), indent=1)
    ns = sorted(v["n"] for v in per_file.values() if v["n"])
    print("total", sum(total.values()), dict(total))
    print("files with candidates", len(ns), "min", ns[0], "median", ns[len(ns)//2], "max", ns[-1])


if __name__ == "__main__":
    main()
