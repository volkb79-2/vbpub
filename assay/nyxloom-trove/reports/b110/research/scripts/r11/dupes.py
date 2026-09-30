#!/usr/bin/env python3
"""R11 duplication analysis with mutation-candidate accounting.

Three clone levels over a Python source tree, each counting the assay R2
candidates (same operator catalogue as candidates.py) inside the clones:

  F  function clones: whole function bodies, docstring stripped, identifiers
     alpha-renamed (T2), string constants abstracted.
  W  statement-run clones: maximal runs (>= MIN_RUN statements) of consecutive
     sibling statements whose normalized forms match another run elsewhere.
  E  decision-expression clones: the top-level expression of a statement
     (if/while test, return value, assert test, assignment value, bare expr)
     that carries >= 2 candidates, normalized per-expression (T2).

"Redundant candidates" of a cluster with n copies of c candidates each is
(n-1)*c: what disappears if the copies become one shared function.

Usage: dupes.py <src_root> <out.json> [--min-run N]
"""
import ast
import builtins
import copy
import hashlib
import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from candidates import sites_of  # noqa: E402

BUILTINS = set(dir(builtins))
MIN_RUN = 3


def ncand(node):
    return sum(len(sites_of(n)) for n in ast.walk(node))


class Normalizer(ast.NodeTransformer):
    """T2 normalization: rename local-ish Name/arg ids by first occurrence;
    keep builtins, call targets, ALL_CAPS / _ALL_CAPS constants, attribute
    names; abstract str/bytes constants (messages differ between copies)."""

    def __init__(self):
        self.map = {}

    def _rn(self, name):
        if name in BUILTINS or name.lstrip("_").isupper():
            return name
        if name not in self.map:
            self.map[name] = f"v{len(self.map)}"
        return self.map[name]

    def visit_Call(self, node):
        # keep the callee spelling when it is a bare Name (semantic anchor)
        func = node.func
        node.args = [self.visit(a) for a in node.args]
        node.keywords = [self.visit(k) for k in node.keywords]
        if not isinstance(func, ast.Name):
            node.func = self.visit(func)
        return node

    def visit_Name(self, node):
        return ast.copy_location(ast.Name(id=self._rn(node.id), ctx=ast.Load()), node)

    def visit_arg(self, node):
        node.arg = self._rn(node.arg)
        node.annotation = None
        return node

    def visit_Constant(self, node):
        if isinstance(node.value, (str, bytes)):
            return ast.copy_location(ast.Constant(value="S"), node)
        return node

    def visit_JoinedStr(self, node):
        return ast.copy_location(ast.Constant(value="S"), node)

    def visit_FunctionDef(self, node):
        node.name = "F"
        node.returns = None
        node.decorator_list = []
        body = node.body
        if body and isinstance(body[0], ast.Expr) and isinstance(getattr(body[0], "value", None), ast.Constant) and isinstance(body[0].value.value, str):
            body = body[1:]
        node.body = body or [ast.Pass()]
        self.generic_visit(node)
        return node

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_AnnAssign(self, node):
        node.annotation = ast.Name(id="T", ctx=ast.Load())
        self.generic_visit(node)
        return node


def norm_hash(node):
    n = Normalizer().visit(copy.deepcopy(node))
    return hashlib.sha1(ast.dump(n, annotate_fields=False, include_attributes=False).encode()).hexdigest()[:16]


def size(node):
    return sum(1 for _ in ast.walk(node))


def load(root):
    files = sorted(p for p in Path(root).rglob("*.py") if "__pycache__" not in p.parts)
    out = []
    for p in files:
        try:
            tree = ast.parse(p.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        out.append((str(p.relative_to(root)), tree))
    return out


def enclosing_funcs(tree):
    res = {}
    def rec(node, qual):
        for ch in ast.iter_child_nodes(node):
            if isinstance(ch, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                q = ch.name if qual is None else f"{qual}.{ch.name}"
                res[id(ch)] = q
                rec(ch, q)
            else:
                rec(ch, qual)
    rec(tree, None)
    return res


def function_clones(trees):
    groups = defaultdict(list)
    for rel, tree in trees:
        names = enclosing_funcs(tree)
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if size(node) < 25:
                    continue
                groups[norm_hash(node)].append({
                    "file": rel, "line": node.lineno, "end": node.end_lineno,
                    "qual": names.get(id(node), node.name), "cand": ncand(node),
                    "lines": node.end_lineno - node.lineno + 1})
    return [g for g in groups.values() if len(g) > 1]


def stmt_blocks(tree):
    for node in ast.walk(tree):
        for field in ("body", "orelse", "finalbody", "handlers"):
            val = getattr(node, field, None)
            if isinstance(val, list) and val and isinstance(val[0], (ast.stmt, ast.excepthandler)):
                yield val


def run_clones(trees, min_run=MIN_RUN):
    """Maximal runs of >= min_run consecutive sibling statements whose T2
    hashes match elsewhere. Returns (clusters, redundant_site_ids) where the
    redundant set is the union of candidate sites in every NON-canonical copy
    (canonical = first member by file/line), so overlapping clusters are not
    double counted."""
    blocks = []  # (rel, [stmt], [hash])
    for rel, tree in trees:
        for blk in stmt_blocks(tree):
            stmts = [s for s in blk if not (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant))]
            if len(stmts) < min_run:
                continue
            blocks.append((rel, stmts, [norm_hash(s) for s in stmts]))
    index = defaultdict(list)
    for bi, (_, stmts, hs) in enumerate(blocks):
        for i in range(len(hs) - min_run + 1):
            index[tuple(hs[i:i + min_run])].append((bi, i))
    regions = {}
    for key, occ in index.items():
        if len(occ) < 2:
            continue
        # non-overlapping occurrences only (same block, overlapping windows)
        starts = [i for _, i in occ]
        back = 0
        while all(i - back - 1 >= 0 for _, i in occ) and len({blocks[bi][2][i - back - 1] for bi, i in occ}) == 1:
            back += 1
        length = min_run + back
        occ2 = [(bi, i - back) for bi, i in occ]
        while all(i + length < len(blocks[bi][2]) for bi, i in occ2) and len({blocks[bi][2][i + length] for bi, i in occ2}) == 1:
            length += 1
        sig = (tuple(sorted(occ2)), length)
        regions[sig] = occ2
    clusters = []
    redundant = set()
    for (occ_sorted, length), occ2 in regions.items():
        members = []
        for bi, i in sorted(occ2, key=lambda t: (blocks[t[0]][0], blocks[t[0]][1][t[1]].lineno)):
            rel, stmts, _ = blocks[bi]
            seg = stmts[i:i + length]
            # reject self-overlap within one block
            members.append({"file": rel, "line": seg[0].lineno, "end": seg[-1].end_lineno,
                            "cand": sum(ncand(s) for s in seg), "stmts": length, "_seg": seg})
        # drop overlapping members in the same block (periodic runs)
        clean = []
        for m in members:
            if any(c["file"] == m["file"] and not (m["line"] > c["end"] or m["end"] < c["line"]) for c in clean):
                continue
            clean.append(m)
        if len(clean) < 2:
            continue
        for m in clean[1:]:
            for s in m["_seg"]:
                for n in ast.walk(s):
                    for k, _ in enumerate(sites_of(n)):
                        redundant.add((m["file"], id(n), k))
        for m in clean:
            del m["_seg"]
        clusters.append(clean)
    return clusters, redundant


def top_exprs(tree):
    for node in ast.walk(tree):
        if isinstance(node, (ast.If, ast.While)):
            yield node, node.test
        elif isinstance(node, ast.Return) and node.value is not None:
            yield node, node.value
        elif isinstance(node, ast.Assert):
            yield node, node.test
        elif isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)) and node.value is not None:
            yield node, node.value
        elif isinstance(node, ast.Expr):
            yield node, node.value
        elif isinstance(node, ast.IfExp):
            yield node, node.test
        elif isinstance(node, ast.comprehension):
            for cond in node.ifs:
                yield node, cond


def expr_clones(trees, min_cand=2):
    groups = defaultdict(list)
    for rel, tree in trees:
        for stmt, expr in top_exprs(tree):
            if not isinstance(expr, (ast.BoolOp, ast.Compare, ast.UnaryOp)):
                continue
            c = ncand(expr)
            if c < min_cand:
                continue
            groups[norm_hash(expr)].append({"file": rel, "line": expr.lineno, "cand": c,
                                            "src": ast.unparse(expr)[:140], "_n": expr})
    clusters = [sorted(g, key=lambda m: (m["file"], m["line"])) for g in groups.values() if len(g) > 1]
    redundant = set()
    for g in clusters:
        for m in g[1:]:
            for n in ast.walk(m["_n"]):
                for k, _ in enumerate(sites_of(n)):
                    redundant.add((m["file"], id(n), k))
        for m in g:
            del m["_n"]
    return clusters, redundant


def summarize(clusters):
    red = sum((len(g) - 1) * min(m["cand"] for m in g) for g in clusters)
    tot = sum(sum(m["cand"] for m in g) for g in clusters)
    return {"clusters": len(clusters), "copies": sum(len(g) for g in clusters),
            "candidates_in_clones": tot, "redundant_candidates": red}


def main():
    root = sys.argv[1]
    out = sys.argv[2]
    min_run = int(sys.argv[sys.argv.index("--min-run") + 1]) if "--min-run" in sys.argv else MIN_RUN
    trees = load(root)
    F = function_clones(trees)
    W, Wred = run_clones(trees, min_run)
    E, Ered = expr_clones(trees)
    E1, E1red = expr_clones(trees, min_cand=1)
    res = {"F": F, "W": W, "E": E, "E1": E1}
    print("F", summarize(F))
    print("W", summarize(W), "union-redundant", len(Wred))
    print("E(>=2 cand)", summarize(E), "union-redundant", len(Ered))
    print("E(>=1 cand)", summarize(E1), "union-redundant", len(E1red))
    print("W|E union-redundant", len(Wred | Ered))
    json.dump(res, open(out, "w"), indent=1)


if __name__ == "__main__":
    main()
