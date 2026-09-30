"""W3 Part 1 shared helpers: component map (brief 2a) and AST import edges.

Run scripts from the assay/ directory of the worktree.
"""
import ast
from pathlib import Path

SRC = Path("src")
PKG = SRC / "assay"


def module_name(path):
    parts = list(path.relative_to(SRC).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def all_modules():
    return {module_name(p): p for p in sorted(PKG.rglob("*.py")) if "__pycache__" not in p.parts}


def component(m):
    if m in ("assay.adapters", "assay.adapters.base"):
        return "core"
    for lang in ("python", "javascript", "go", "sql"):
        if m.startswith(f"assay.adapters.{lang}"):
            return f"adapter.{lang}"
    for pkg, name in (("coverage_parsers", "parsers.coverage"), ("mutation_parsers", "parsers.mutation"),
                      ("result_reports", "parsers.result_reports")):
        if m == f"assay.{pkg}" or m.startswith(f"assay.{pkg}."):
            return name
    if m == "assay.analysis":
        return "analysis"
    if m == "assay.cli":
        return "cli"
    return "core"


def import_edges(modules=None):
    """Return {(importer, imported): kinds} with kinds a subset of {'top','nested','type_checking'}."""
    modules = modules if modules is not None else all_modules()
    known = set(modules)
    edges = {}
    for importer, path in modules.items():
        is_pkg = path.name == "__init__.py"
        pkg = importer if is_pkg else importer.rpartition(".")[0]
        tree = ast.parse(path.read_text(encoding="utf-8"))
        # each stack item is a node to PROCESS, with its context kind
        stack = [(tree, "top")]
        while stack:
            node, kind = stack.pop()
            if isinstance(node, ast.Import):
                for a in node.names:
                    _add(edges, importer, a.name, kind)
            elif isinstance(node, ast.ImportFrom):
                base = node.module or ""
                if node.level:
                    up = pkg.split(".")
                    up = up[: len(up) - (node.level - 1)]
                    base = ".".join(up + ([node.module] if node.module else []))
                for a in node.names:
                    cand = f"{base}.{a.name}"
                    _add(edges, importer, cand if cand in known else base, kind)
            if isinstance(node, ast.If) and _is_tc(node.test):
                stack.extend((c, "type_checking") for c in node.body)
                stack.extend((c, kind) for c in node.orelse)
                continue
            k = "nested" if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and kind == "top" else kind
            stack.extend((c, k) for c in ast.iter_child_nodes(node))
    return edges


def _is_tc(test):
    return (isinstance(test, ast.Name) and test.id == "TYPE_CHECKING") or (
        isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING")


def _add(edges, importer, imported, kind):
    if not (imported == "assay" or imported.startswith("assay.")):
        return
    if imported == importer:
        return
    edges.setdefault((importer, imported), set()).add(kind)
