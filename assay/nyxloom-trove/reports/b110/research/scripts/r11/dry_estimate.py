#!/usr/bin/env python3
"""R11 internal-DRY estimate for assay: union of candidate sites that would
disappear if duplicated logic became one shared function, split by whether the
duplication crosses the verify.py <-> producer trust boundary (deliberate,
A-129/A-182: keep) or stays on one side (consolidatable).

Sources of redundancy (all T2-normalized, see dupes.py / idioms.py):
  G  typed-field guard groups (idioms.guard_groups), one helper per family
     per side keeps ONE representative copy of the family's candidates
  E  repeated decision expressions with >= 2 candidates
  W  repeated statement runs (>= 3 statements)
  F  repeated whole functions
  D  @dataclass(frozen=True[, kw_only=True]) flag constants: one shared
     decorator keeps one copy per spelling

Usage: dry_estimate.py <src_root>
"""
import ast
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from candidates import sites_of  # noqa: E402
from dupes import function_clones, load, norm_hash, stmt_blocks  # noqa: E402
from idioms import family_key, guard_groups  # noqa: E402


def side(rel):
    return "verify" if rel == "verify.py" else "producer"


def site_ids(rel, node, skip_boolop=False):
    out = set()
    for n in ast.walk(node):
        for k, _ in enumerate(sites_of(n)):
            out.add((rel, id(n), k))
    return out


def main():
    root = sys.argv[1]
    trees = load(root)
    total = sum(len(sites_of(n)) for _, t in trees for n in ast.walk(t))
    removable = defaultdict(set)   # source -> site ids
    deliberate = defaultdict(set)  # cross-boundary copies we must keep

    def account(label, members):
        """members: list of (rel, [nodes]) copies of ONE logic unit. Keep the
        first copy per side; the rest are removable. If both sides hold a
        copy, the second side's copy is deliberate, not removable."""
        by_side = defaultdict(list)
        for rel, nodes in members:
            by_side[side(rel)].append((rel, nodes))
        for s, copies in by_side.items():
            for rel, nodes in copies[1:]:
                for n in nodes:
                    removable[label] |= site_ids(rel, n)
        if len(by_side) == 2:
            rel, nodes = by_side["verify"][0]
            for n in nodes:
                deliberate[label] |= site_ids(rel, n)

    # G: guard groups; the boolop tokens joining the group are sites on the
    # parent BoolOp -- attribute (len(grp)-1) of them by position
    fam = defaultdict(list)
    for rel, tree in trees:
        for node, grp, c, op in guard_groups(tree):
            fam[op + family_key(grp)].append((rel, node, grp))
    for key, occ in fam.items():
        members = []
        for rel, node, grp in occ:
            ids = set()
            for g in grp:
                ids |= site_ids(rel, g)
            # boolop tokens between group operands: node.values index span
            idx = [node.values.index(g) for g in grp]
            for k in range(min(idx), max(idx)):
                ids.add((rel, id(node), k))
            members.append((rel, ids))
        by_side = defaultdict(list)
        for rel, ids in members:
            by_side[side(rel)].append(ids)
        for s, copies in by_side.items():
            for ids in copies[1:]:
                removable["G"] |= ids
        if len(by_side) == 2:
            deliberate["G"] |= by_side["verify"][0]

    # E: repeated decision expressions (>= 2 candidates)
    groups = defaultdict(list)
    for rel, tree in trees:
        for node in ast.walk(tree):
            expr = None
            if isinstance(node, (ast.If, ast.While, ast.Assert, ast.IfExp)):
                expr = node.test
            elif isinstance(node, (ast.Return, ast.Assign, ast.AnnAssign, ast.Expr)) and getattr(node, "value", None) is not None:
                expr = node.value
            if isinstance(expr, (ast.BoolOp, ast.Compare, ast.UnaryOp)) and sum(len(sites_of(n)) for n in ast.walk(expr)) >= 2:
                groups[norm_hash(expr)].append((rel, [expr]))
    for g in groups.values():
        if len(g) > 1:
            account("E", g)

    # W: repeated statement runs >= 3
    runs = defaultdict(list)
    for rel, tree in trees:
        for blk in stmt_blocks(tree):
            hs = [norm_hash(s) for s in blk]
            for i in range(len(blk) - 2):
                runs[tuple(hs[i:i + 3])].append((rel, blk[i:i + 3]))
    for g in runs.values():
        if len(g) > 1:
            # drop overlapping windows in the same block
            clean, seen = [], set()
            for rel, seg in g:
                key = (rel, seg[0].lineno)
                if any(r == rel and abs(l - seg[0].lineno) < 1 for r, l in seen):
                    continue
                seen.add(key)
                clean.append((rel, seg))
            if len(clean) > 1:
                account("W", clean)

    # F: whole-function clones
    fnodes = {}
    for rel, tree in trees:
        for n in ast.walk(tree):
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                fnodes[(rel, n.lineno)] = n
    for g in function_clones(trees):
        account("F", [(m["file"], [fnodes[(m["file"], m["line"])]]) for m in g])

    # D: dataclass flags
    spell = defaultdict(list)
    for rel, tree in trees:
        for n in ast.walk(tree):
            if isinstance(n, ast.ClassDef):
                for d in n.decorator_list:
                    if isinstance(d, ast.Call) and ast.unparse(d.func) in ("dataclass", "dataclasses.dataclass"):
                        spell[ast.unparse(d)].append((rel, [d]))
    for key, g in spell.items():
        members = g
        for rel, nodes in members[1:]:
            for n in nodes:
                removable["D"] |= site_ids(rel, n)

    union = set().union(*removable.values())
    delib = set().union(*deliberate.values()) - union
    print(f"total candidates: {total}")
    for k in "GEWFD":
        print(f"  {k}: removable {len(removable[k])}   deliberate cross-boundary copies {len(deliberate[k])}")
    print(f"union removable (no double count): {len(union)}  ({100*len(union)/total:.1f}%)")
    print(f"  of which D (dataclass flags): {len(removable['D'])}")
    print(f"  union without D: {len(union - removable['D'])}")
    by_file = defaultdict(int)
    for rel, _, _ in union:
        by_file[rel] += 1
    print("removable by file:", sorted(by_file.items(), key=lambda kv: -kv[1])[:14])
    print(f"deliberate cross-boundary (verify.py copy of producer logic, keep): {len(delib)}")


if __name__ == "__main__":
    main()
