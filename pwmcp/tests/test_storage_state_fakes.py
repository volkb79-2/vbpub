"""PWMCP-60: a test that patches a process-wide `os.*` symbol must delegate the calls it does not own.

Pytest 9.2 calls `os.fstat` during capture and teardown. A test that replaces a whole `os` symbol
with a fake which does not delegate does not observe only the file under test: it also answers for
pytest's own calls, and the failure lands somewhere unrelated — or nowhere at all, which is worse,
because the test then certifies a behaviour it never exercised.

The rule this oracle enforces, on the syntax tree rather than on prose:

1. the enclosing test saves the REAL function (`real = <module>.os.<symbol>`);
2. the replacement function calls that saved real function on the calls it does not own;
3. a replacement that receives arguments examines them before delegating (`if fd_under_test ...`),
   so "identify the fd/path under test" is a comparison, not an intention.

A patch whose replacement never calls the saved real is refused even when its body looks
harmless: there is no comparison left to make.
"""

from __future__ import annotations

import ast
from pathlib import Path

PWMCP = Path(__file__).resolve().parents[1]
SUITE_ROOTS = (PWMCP / "tests", PWMCP / "client" / "tests")
# Modules whose attribute patch is process-wide (not an attribute of a module under test).
PROCESS_WIDE = frozenset({"os", "builtins", "io"})
FUNCTION_DEFS = (ast.FunctionDef, ast.AsyncFunctionDef)


def _rightmost(node: ast.expr) -> str | None:
    """The right-most identifier of a name/attribute chain (`ss.os` -> `os`)."""

    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _patch_target(call: ast.Call) -> tuple[str, str] | None:
    """`setattr(<root>.<module>, "<symbol>", <replacement>)` where the module is process-wide."""

    func = call.func
    if not (isinstance(func, ast.Attribute) and func.attr == "setattr" and len(call.args) >= 3):
        return None
    owner, symbol = call.args[0], call.args[1]
    module = _rightmost(owner)
    if module not in PROCESS_WIDE or not (isinstance(symbol, ast.Constant) and isinstance(symbol.value, str)):
        return None
    return module, symbol.value


def _saved_real(function: ast.AST, module: str, symbol: str) -> set[str]:
    """Names bound in `function` to `<anything>.os.<symbol>` (the real function)."""

    saved: set[str] = set()
    for node in ast.walk(function):
        if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Attribute):
            continue
        value = node.value
        if value.attr != symbol or _rightmost(value.value) != module:
            continue
        for target in node.targets:
            if isinstance(target, ast.Name):
                saved.add(target.id)
    return saved


def _replacement_definition(owner_scope: ast.AST, replacement: ast.expr) -> ast.FunctionDef | None:
    """The function a `setattr` installs: a nested/lambda def, or a module-level name."""

    if isinstance(replacement, ast.Name):
        for node in ast.walk(owner_scope):
            if isinstance(node, FUNCTION_DEFS) and node.name == replacement.id:
                return node
        return None
    if isinstance(replacement, ast.Lambda):
        return None  # a lambda cannot be checked for delegation; refused as a shape
    return None


def _calls(function: ast.AST, names: set[str]) -> bool:
    for node in ast.walk(function):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in names:
            return True
    return False


def _has_conditional(function: ast.AST) -> bool:
    return any(isinstance(node, (ast.If, ast.IfExp)) for node in ast.walk(function))


def _takes_arguments(function: ast.AST) -> bool:
    """Does the replacement receive anything it could compare (an fd, a path, a name)?"""

    if not isinstance(function, ast.FunctionDef):
        return False
    args = function.args
    required = len(args.posonlyargs) + len(args.args) - len(args.defaults)
    return required > 0 or bool(args.vararg) or bool(args.kwarg)


def _offenders(path: Path) -> list[str]:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    found: list[str] = []

    # A patch is judged inside the scope that executed it: the whole module for a module-level
    # `setattr`, the test function for the usual `monkeypatch.setattr` call.
    scopes: list[ast.AST] = [tree]
    for node in ast.walk(tree):
        if isinstance(node, FUNCTION_DEFS):
            scopes.append(node)

    for scope in scopes:
        for node in ast.walk(scope):
            if not isinstance(node, ast.Call):
                continue
            parsed = _patch_target(node)
            if parsed is None:
                continue
            module, symbol = parsed
            replacement = node.args[2]
            if isinstance(replacement, ast.Attribute) and replacement.attr == symbol:
                continue  # an explicit alias of the real function: nothing is intercepted
            saved = _saved_real(scope, module, symbol)
            if not saved:
                found.append(
                    f"{path.name}:{node.lineno}: patches {module}.{symbol} without saving the real function — "
                    "the fake answers for every caller in the process"
                )
                continue
            replacement_body = _replacement_definition(scope, replacement)
            if replacement_body is None:
                found.append(
                    f"{path.name}:{node.lineno}: patches {module}.{symbol} with a replacement that is not a "
                    "def/name in the same scope, so delegation cannot be checked"
                )
                continue
            if not _calls(replacement_body, saved):
                found.append(
                    f"{path.name}:{node.lineno}: {replacement_body.name} never calls the saved real "
                    f"{symbol}({', '.join(sorted(saved))}) — every call is intercepted"
                )
                continue
            if _takes_arguments(replacement_body) and not _has_conditional(replacement_body):
                found.append(
                    f"{path.name}:{node.lineno}: {replacement_body.name} takes arguments but never examines "
                    "them, so it cannot tell the fd/path under test from an unrelated caller"
                )
    return found


def test_every_process_wide_os_patch_delegates_to_the_real_function() -> None:
    offenders: list[str] = []
    scanned = 0
    for root in SUITE_ROOTS:
        for path in sorted(root.rglob("*.py")):
            scanned += 1
            offenders.extend(_offenders(path))
    assert scanned, "no test modules were scanned"
    assert offenders == [], "process-wide os patches must delegate: " + "; ".join(offenders)


def test_the_storage_state_suite_still_scopes_its_descriptor_fakes() -> None:
    """The observed hazard, pinned by name: the storage-state fakes keep their fd/path comparison."""

    source = (PWMCP / "client" / "tests" / "test_storage_state.py").read_text(encoding="utf-8")
    assert 'monkeypatch.setattr(ss.os, "fdopen", broken_target)' in source
    assert 'monkeypatch.setattr(ss.os, "open", spy)' in source
    assert 'monkeypatch.setattr(ss.os, "open", fail_open)' in source
    assert "real_fdopen" in source and "real_open" in source and "real_geteuid" in source
    # the descriptor spy identifies the file under test instead of answering for every descriptor
    assert "if opened_path == target" in source and "if opened != target" in source
