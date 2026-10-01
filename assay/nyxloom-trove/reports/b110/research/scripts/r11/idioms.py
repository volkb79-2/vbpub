#!/usr/bin/env python3
"""R11 idiom-family counter: typed-field guards inside BoolOps.

A "subject guard group" is the maximal set of operands of one BoolOp that
test the SAME subject expression (by source text) and include at least one
isinstance()/type() check on it, e.g.

    isinstance(x, bool) or not isinstance(x, int) or x < 1

The group's own candidates are its compare ops, bool constants, and the
(len(group)-1) boolop tokens that join its operands (only exact when the
group operands are contiguous; we require contiguity). Replacing the group
by one helper call `_strict_int(x, minimum=1)` removes all of them at the
call site; the helper itself carries one copy.

Also counts a few fixed idioms anywhere: aware-datetime, sha256-hex check,
`X is not None and X.attr is not None` optional chains.

Usage: idioms.py <src_root> [--json out]
"""
import ast
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from candidates import sites_of  # noqa: E402


def cands(node):
    return sum(len(sites_of(n)) for n in ast.walk(node) if not isinstance(n, ast.BoolOp))


def strip_not(e):
    return e.operand if isinstance(e, ast.UnaryOp) and isinstance(e.op, ast.Not) else e


def subject(e):
    """(subject_src, kind) for an operand, or None."""
    e0 = strip_not(e)
    if isinstance(e0, ast.Call) and isinstance(e0.func, ast.Name) and e0.func.id == "isinstance" and len(e0.args) == 2:
        return ast.unparse(e0.args[0]), "isinstance:" + ast.unparse(e0.args[1])
    if isinstance(e0, ast.Compare):
        left = e0.left
        if isinstance(left, ast.Call) and isinstance(left.func, ast.Name) and left.func.id in ("type", "len"):
            if left.args:
                return ast.unparse(left.args[0]), f"{left.func.id}-cmp"
        # chained bound: 1 <= x <= N  -> subject is the middle
        if len(e0.comparators) == 2:
            return ast.unparse(e0.comparators[0]), "range"
        return ast.unparse(left), "cmp"
    if isinstance(e0, ast.Call) and isinstance(e0.func, ast.Attribute) and isinstance(e0.func.value, ast.Name) and e0.func.value.id == "math":
        if e0.args:
            return ast.unparse(e0.args[0]), "math." + e0.func.attr
    if isinstance(e0, ast.Name):
        return e0.id, "truthy"
    if isinstance(e0, ast.Call) and isinstance(e0.func, ast.Attribute) and e0.func.attr in ("fullmatch", "match"):
        if e0.args:
            return ast.unparse(e0.args[-1]), "regex:" + ast.unparse(e0.func.value)
    if isinstance(e0, ast.Call) and isinstance(e0.func, ast.Name) and e0.func.id == "any":
        return None
    return None


def family_key(group_ops):
    kinds = []
    for e in group_ops:
        sub, kind = subject(e)
        neg = isinstance(e, ast.UnaryOp)
        if kind in ("cmp", "range", "len-cmp", "type-cmp"):
            e0 = strip_not(e)
            kind += ":" + ",".join(type(o).__name__ for o in e0.ops)
        kinds.append(("not " if neg else "") + kind)
    return " | ".join(kinds)


def guard_groups(tree):
    for node in ast.walk(tree):
        if not isinstance(node, ast.BoolOp):
            continue
        vals = node.values
        subs = [subject(v) for v in vals]
        i = 0
        while i < len(vals):
            if subs[i] is None:
                i += 1
                continue
            j = i
            while j + 1 < len(vals) and subs[j + 1] is not None and subs[j + 1][0] == subs[i][0]:
                j += 1
            grp = vals[i:j + 1]
            if len(grp) >= 2 and any(s[1].startswith("isinstance") for s in subs[i:j + 1]):
                c = (len(grp) - 1) + sum(cands(g) for g in grp)
                yield node, grp, c, type(node.op).__name__
            i = j + 1


def main():
    root = Path(sys.argv[1])
    files = sorted(p for p in root.rglob("*.py") if "__pycache__" not in p.parts)
    fam = defaultdict(list)
    fixed = Counter()
    fixed_c = Counter()
    fixed_where = defaultdict(list)
    for p in files:
        rel = str(p.relative_to(root))
        try:
            tree = ast.parse(p.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node, grp, c, op in guard_groups(tree):
            key = op + ": " + family_key(grp)
            fam[key].append((rel, grp[0].lineno, c))
        for node in ast.walk(tree):
            src = None
            if isinstance(node, (ast.BoolOp, ast.Compare)):
                src = ast.unparse(node)
            if src is None:
                continue
            if isinstance(node, ast.BoolOp) and ".tzinfo is None" in src and "utcoffset" in src:
                fixed["aware-datetime"] += 1; fixed_c["aware-datetime"] += sum(len(sites_of(n)) for n in ast.walk(node)); fixed_where["aware-datetime"].append(f"{rel}:{node.lineno}")
            if isinstance(node, ast.BoolOp) and ("0123456789abcdef" in src or "_SHA256_RE" in src or "!= 64" in src or "== 64" in src):
                fixed["sha256-hex"] += 1; fixed_c["sha256-hex"] += sum(len(sites_of(n)) for n in ast.walk(node)); fixed_where["sha256-hex"].append(f"{rel}:{node.lineno}")
    rows = []
    for key, occ in fam.items():
        rows.append((sum(o[2] for o in occ), len(occ), key, occ))
    rows.sort(reverse=True)
    total_c = sum(r[0] for r in rows)
    total_n = sum(r[1] for r in rows)
    print(f"typed guard groups: {total_n} occurrences, {total_c} candidates, {len(rows)} families")
    for tot, n, key, occ in rows[:30]:
        where = ", ".join(f"{f}:{l}" for f, l, _ in occ[:5])
        print(f"{tot:4d} cand {n:3d}x  {key}\n        {where}{' ...' if n > 5 else ''}")
    by_file = Counter()
    for tot, n, key, occ in rows:
        for f, l, c in occ:
            by_file[f] += c
    print("by file:", by_file.most_common(12))
    print("fixed idioms:", dict(fixed), dict(fixed_c))
    for k, v in fixed_where.items():
        print(" ", k, v[:12])
    if "--json" in sys.argv:
        json.dump({"families": [{"cand": r[0], "n": r[1], "key": r[2], "occ": r[3]} for r in rows],
                   "fixed": dict(fixed), "fixed_cand": dict(fixed_c), "fixed_where": fixed_where},
                  open(sys.argv[sys.argv.index("--json") + 1], "w"), indent=1)


if __name__ == "__main__":
    main()
