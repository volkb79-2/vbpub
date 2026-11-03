"""AST scan of the repository-root paths the CMRU test suite builds (KI-88).

The fixture-closure test needs every repository file the suite reads to be carried
by the disposable fixture. A regular expression over ``REPO_ROOT / "..."`` sees
only one spelling. This module resolves path expressions symbolically, for every
root spelling in use (``REPO_ROOT``, ``REPO``, ``ROOT.parent``, ``Path(__file__)
.resolve().parents[N]``, ``.joinpath(...)``, ``.parent`` chains), inside functions
as well as at import time, and reports reads whose tail is not a literal.

A path is a tuple of components relative to the repository root. ``UNKNOWN`` marks
a component the scan cannot resolve (a variable).
"""
from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import PurePosixPath

UNKNOWN = "<non-literal>"
_IDENTITY_METHODS = {"resolve", "absolute", "expanduser"}
_PREDICATE_METHODS = {"exists", "is_file", "is_dir", "is_symlink"}


@dataclass(frozen=True)
class RootRead:
    parts: tuple[str, ...]      # components below the repository root; UNKNOWN allowed
    function: str | None        # None => executed at import time
    guarded: bool               # inside a function carrying a skipif marker
    line: int
    predicate: bool = False     # only asked whether it exists (exists/is_file/...)

    @property
    def literal(self) -> bool:
        return UNKNOWN not in self.parts

    @property
    def path(self) -> str:
        return "/".join(self.parts)


def _is_skipif(node: ast.AST) -> bool:
    return any(
        isinstance(sub, ast.Attribute) and sub.attr == "skipif" for sub in ast.walk(node)
    )


def _module_marker_names(module: ast.Module) -> set[str]:
    """Names bound at module scope to a ``pytest.mark.skipif(...)`` marker."""
    names: set[str] = set()
    for statement in module.body:
        if isinstance(statement, ast.Assign) and _is_skipif(statement.value):
            names.update(t.id for t in statement.targets if isinstance(t, ast.Name))
    return names


def module_has_skip_marker(source: str) -> bool:
    """True when the module assigns ``pytestmark`` a skipif marker."""
    for statement in ast.parse(source).body:
        if isinstance(statement, ast.Assign) and _is_skipif(statement.value):
            if any(isinstance(t, ast.Name) and t.id == "pytestmark" for t in statement.targets):
                return True
    return False


class _Resolver:
    def __init__(self, file_parts: tuple[str, ...]):
        self.file_parts = file_parts  # the module file below the repository root
        self.reads: list[RootRead] = []
        self._decorating = False

    # -- expression resolution ---------------------------------------------------
    def resolve(self, node: ast.AST, aliases: dict[str, tuple[str, ...]]):
        """Return the repo-relative parts of ``node`` or None when it is no path."""
        if isinstance(node, ast.Name):
            if node.id == "__file__":
                return self.file_parts
            return aliases.get(node.id)
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id in {"Path", "PurePath", "PurePosixPath"}:
                if len(node.args) == 1:
                    return self.resolve(node.args[0], aliases)
                return None
            if isinstance(func, ast.Attribute):
                base = self.resolve(func.value, aliases)
                if base is None:
                    return None
                if func.attr in _IDENTITY_METHODS:
                    return base
                if func.attr == "joinpath":
                    return (*base, *(self._component(arg) for arg in node.args))
            return None
        if isinstance(node, ast.Attribute):
            base = self.resolve(node.value, aliases)
            if base is not None and node.attr == "parent":
                return base[:-1] if base else base
            return None
        if isinstance(node, ast.Subscript):
            base_node = node.value
            if isinstance(base_node, ast.Attribute) and base_node.attr == "parents":
                base = self.resolve(base_node.value, aliases)
                index = node.slice
                if (
                    base is not None
                    and isinstance(index, ast.Constant)
                    and isinstance(index.value, int)
                    and index.value >= 0
                ):
                    keep = len(base) - index.value - 1
                    return base[: max(keep, 0)]
            return None
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
            base = self.resolve(node.left, aliases)
            if base is None:
                return None
            return (*base, self._component(node.right))
        return None

    @staticmethod
    def _component(node: ast.AST) -> str:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        return UNKNOWN

    # -- statement walk ----------------------------------------------------------
    def scan(self, module: ast.Module, marker_names: set[str]) -> None:
        aliases: dict[str, tuple[str, ...]] = {}
        self._body(module.body, aliases, marker_names, function=None, guarded=False)

    def _body(self, statements, aliases, marker_names, function, guarded):
        for statement in statements:
            if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for expression in (
                    *statement.decorator_list, *statement.args.defaults,
                    *(d for d in statement.args.kw_defaults if d is not None),
                ):
                    # A path listed in a decorator or default (parametrize, a keyword
                    # default) is only passed along; its read happens in the body.
                    self._decorating = True
                    try:
                        self._expression(expression, aliases, function, guarded)
                    finally:
                        self._decorating = False
                inner_guarded = any(
                    _is_skipif(d)
                    or (isinstance(d, ast.Name) and d.id in marker_names)
                    for d in statement.decorator_list
                )
                self._body(
                    statement.body, dict(aliases), marker_names,
                    function=statement.name, guarded=inner_guarded,
                )
            elif isinstance(statement, ast.ClassDef):
                self._body(statement.body, aliases, marker_names, function, guarded)
            else:
                self._statement(statement, aliases, marker_names, function, guarded)

    def _statement(self, statement, aliases, marker_names, function, guarded):
        bound_path = None  # a path merely bound to a name is read where the name is used
        if isinstance(statement, (ast.Assign, ast.AnnAssign)) and statement.value is not None:
            parts = self.resolve(statement.value, aliases)
            if parts is not None:
                bound_path = statement.value
            targets = statement.targets if isinstance(statement, ast.Assign) else [statement.target]
            for target in targets:
                if isinstance(target, ast.Name):
                    if parts is not None:
                        aliases[target.id] = parts
                    else:
                        aliases.pop(target.id, None)
        for field_name, value in ast.iter_fields(statement):
            children = value if isinstance(value, list) else [value]
            nested_statements = [c for c in children if isinstance(c, ast.stmt)]
            if nested_statements:
                self._body(nested_statements, aliases, marker_names, function, guarded)
            for child in children:
                if (
                    isinstance(child, ast.AST) and not isinstance(child, ast.stmt)
                    and child is not bound_path
                ):
                    self._expression(child, aliases, function, guarded)

    def _expression(self, node, aliases, function, guarded, predicate=False):
        """Record each OUTERMOST resolvable path expression below a root."""
        if isinstance(node, ast.Lambda) or (
            isinstance(node, ast.Name) and not isinstance(node.ctx, ast.Load)
        ):
            return
        parts = self.resolve(node, aliases)
        if parts is not None:
            if parts:
                self.reads.append(RootRead(
                    parts, function, guarded, getattr(node, "lineno", 0),
                    predicate or self._decorating,
                ))
            # Still visit non-path arguments (e.g. joinpath(variable_path)).
            for child in ast.iter_child_nodes(node):
                is_callee = isinstance(node, ast.Call) and child is node.func
                if not is_callee and not self._is_path_child(child, aliases):
                    self._expression(child, aliases, function, guarded)
            return
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in _PREDICATE_METHODS
            and self.resolve(node.func.value, aliases) is not None
        ):
            # ``path.exists()`` asks whether a file is there; it reads nothing.
            self._expression(node.func.value, aliases, function, guarded, predicate=True)
            for child in (*node.args, *(k.value for k in node.keywords)):
                self._expression(child, aliases, function, guarded)
            return
        for child in ast.iter_child_nodes(node):
            self._expression(child, aliases, function, guarded)

    def _is_path_child(self, child, aliases) -> bool:
        return self.resolve(child, aliases) is not None


def scan_module(source: str, module_file: str) -> list[RootRead]:
    """Return every repository-root path built by the test module ``module_file``.

    ``module_file`` is the module's path below the repository root, for example
    ``cmru/tests/test_x.py``. Reads of ``cmru/`` itself or of paths above the
    repository root are returned too; the caller decides what needs carrying.
    """
    tree = ast.parse(source)
    resolver = _Resolver(tuple(PurePosixPath(module_file).parts))
    resolver.scan(tree, _module_marker_names(tree))
    return resolver.reads
