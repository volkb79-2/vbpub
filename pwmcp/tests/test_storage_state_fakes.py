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

The rule covers every spelling of the same act, because a spelling that escapes it is a way
around the rule rather than a variant of it: `setattr(<module>, "<symbol>", ...)`
(`monkeypatch.setattr`), its string-addressed forms `setattr("os", "fstat", ...)` and
`setattr("os.fstat", ...)`, and the mock spellings `mock.patch("os.fstat")` / `.patch.object`
— where an auto-generated mock is refused unless `wraps=<real>` states the function it
delegates to. A module outside `PROCESS_WIDE`, or a nested path such as `os.path.join`, is
outside the rule's vocabulary and the reasons for that are stated at `_patch_target`.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

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


def _patch_target(call: ast.Call) -> tuple[str, str, ast.expr | None] | None:
    """A patch of a process-wide symbol, as ``(module, symbol, replacement_or_None)``.

    Four spellings of the same act — replacing a process-wide function that pytest itself
    calls — and all four are covered, so none is a way around the rule:

    * ``setattr(<module object>, "<symbol>", <replacement>)``  (``monkeypatch.setattr``)
    * ``setattr("<module>", "<symbol>", <replacement>)``       (same, addressed by name)
    * ``setattr("<module>.<symbol>", <replacement>)``          (same, one string)
    * ``.patch("<module>.<symbol>", [<replacement>])``         (``mock.patch`` /
      ``mocker.patch``), and ``.patch.object(<module object>, "<symbol>", ...)``

    A ``replacement`` of ``None`` means the call installs an auto-generated mock (nothing was
    passed): that mock delegates to nothing, so it is refused unless the call names the real
    function through ``wraps``.

    The rule's vocabulary is the MODULE set ``PROCESS_WIDE`` (``os``, ``builtins``, ``io``), so
    a nested module name (``os.path.exists``, ``io.open`` is covered but a deeper path is
    not) is outside it. That limit is stated here rather than left implicit; widening it
    means widening ``PROCESS_WIDE``, not special-casing a spelling.
    """

    func = call.func
    if not isinstance(func, ast.Attribute):
        return None
    if func.attr == "setattr":
        if len(call.args) >= 3:
            module, symbol = _module_and_symbol(call.args[0], call.args[1])
            if module is None:
                return None
            return module, symbol, call.args[2]
        if len(call.args) >= 2:
            return _string_address(call.args[0], call.args[1])
        return None
    if func.attr == "object" and isinstance(func.value, ast.Attribute) and func.value.attr == "patch":
        if len(call.args) < 2:
            return None
        module, symbol = _module_and_symbol(call.args[0], call.args[1])
        if module is None:
            return None
        return module, symbol, call.args[2] if len(call.args) >= 3 else None
    if func.attr == "patch" and call.args:
        return _string_address(call.args[0], call.args[1] if len(call.args) >= 2 else None)
    return None


def _module_and_symbol(owner: ast.expr, symbol: ast.expr) -> tuple[str | None, str]:
    """Resolve an ``(owner, symbol)`` pair to a process-wide module name and a symbol name.

    The owner may be the module object itself (``os``) or a reference to it through another
    module under test (``ss.os``, the pattern the storage-state suite uses); both name the
    same process-wide module, so both are in the rule's vocabulary.
    """

    if not (isinstance(symbol, ast.Constant) and isinstance(symbol.value, str)):
        return None, ""
    if isinstance(owner, ast.Constant) and isinstance(owner.value, str):
        return (owner.value, symbol.value) if owner.value in PROCESS_WIDE else (None, "")
    module = _rightmost(owner)
    if module in PROCESS_WIDE:
        return module, symbol.value
    return None, ""


def _string_address(target: ast.expr, replacement: ast.expr | None) -> tuple[str, str, ast.expr | None] | None:
    """A patch addressed as one string: ``"os.fstat"``."""

    if not (isinstance(target, ast.Constant) and isinstance(target.value, str)):
        return None
    parts = target.value.split(".")
    if len(parts) != 2 or parts[0] not in PROCESS_WIDE or not parts[1]:
        return None  # a nested or non-process-wide module is outside the rule's vocabulary
    return parts[0], parts[1], replacement


def _wraps_the_real(call: ast.Call, symbol: str) -> bool:
    """Does this mock call state the real function through ``wraps`` (delegation by name)?"""

    for keyword in call.keywords:
        if keyword.arg != "wraps" or not isinstance(keyword.value, (ast.Attribute, ast.Name)):
            continue
        if _rightmost(keyword.value) == symbol:
            return True
    return False


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
    """The function a patch installs: a module-level/nested name, or a lambda (refused)."""

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


def _innermost_scope(node: ast.AST, tree: ast.AST) -> ast.AST:
    """The nearest enclosing function or class body of `node`, or `tree` when there is none."""

    parents: dict[ast.AST, ast.AST] = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[child] = parent
    current = parents.get(node)
    while current is not None:
        if isinstance(current, FUNCTION_DEFS) or isinstance(current, ast.ClassDef):
            return current
        current = parents.get(current)
    return tree


def _offenders(path: Path) -> list[str]:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    found: list[str] = []

    # A patch is judged inside the scope that executed it: the module for a module-level
    # `setattr`, the test function for the usual `monkeypatch.setattr` call, and the class body
    # for a patch a fixture sets up there. A call is attributed to its INNERMOST enclosing
    # scope, so one call is reported once instead of once per enclosing scope.
    scopes: list[ast.AST] = [tree]
    for node in ast.walk(tree):
        if isinstance(node, FUNCTION_DEFS) or isinstance(node, ast.ClassDef):
            scopes.append(node)

    for scope in scopes:
        for node in ast.walk(scope):
            if not isinstance(node, ast.Call):
                continue
            if _innermost_scope(node, tree) is not scope:
                continue
            parsed = _patch_target(node)
            if parsed is None:
                continue
            module, symbol, replacement = parsed
            if replacement is None:
                # `mock.patch("os.fstat")` installs an auto-generated mock: it delegates to
                # nothing, so it answers for pytest's own callers too. `wraps=` is the one
                # spelling that states the real function, and it names it.
                if _wraps_the_real(node, symbol):
                    continue
                found.append(
                    f"{path.name}:{node.lineno}: patches {module}.{symbol} with a mock that does not "
                    "wrap the real function — every call is intercepted"
                )
                continue
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


# --- the STRING-target spellings (PWMCP-60 review round 1) -------------------------
# The object form is the one the suite uses, so an oracle that only reads `setattr(<module>,
# "<symbol>", ...)` would refuse a `monkeypatch.setattr("os", "fstat", ...)` that means
# exactly the same thing — a spelling that is a way AROUND the rule, not a variant of it.
def _write_module(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "test_sample.py"
    path.write_text(body, encoding="utf-8")
    return path


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        (
            'def test_x(monkeypatch):\n    monkeypatch.setattr("os", "fstat", lambda fd: None)\n',
            "patches os.fstat without saving the real function",
        ),
        (
            'def test_x(monkeypatch):\n    monkeypatch.setattr("os.fstat", lambda fd: None)\n',
            "patches os.fstat without saving the real function",
        ),
        (
            'def test_x(mocker):\n    mocker.patch("os.fstat")\n',
            "patches os.fstat with a mock that does not wrap the real function",
        ),
        (
            'def test_x():\n    with mock.patch.object(os, "fstat"):\n        pass\n',
            "patches os.fstat with a mock that does not wrap the real function",
        ),
        (
            'def test_x():\n    with mock.patch("builtins.print"):\n        pass\n',
            "patches builtins.print with a mock that does not wrap the real function",
        ),
    ],
)
def test_a_string_target_patch_is_refused_like_the_object_form(tmp_path: Path, body: str, expected: str) -> None:
    assert [offender for offender in _offenders(_write_module(tmp_path, body)) if expected in offender]


@pytest.mark.parametrize(
    ("body", "why"),
    [
        (
            'def test_x():\n    with mock.patch("os.fstat", wraps=os.fstat) as patched:\n        patched(3)\n',
            "wraps=os.fstat STATES the real function, so the mock delegates",
        ),
        (
            'def test_x(mocker):\n    mocker.patch("shutil.which", return_value=None)\n',
            "shutil is not process-wide in the way pytest itself calls it",
        ),
        (
            'def test_x(monkeypatch):\n    monkeypatch.setattr("os.path.join", lambda *a: "x")\n',
            "a nested module path is outside the rule's PROCESS_WIDE vocabulary",
        ),
    ],
)
def test_a_delegating_or_out_of_scope_patch_is_accepted(tmp_path: Path, body: str, why: str) -> None:
    assert _offenders(_write_module(tmp_path, body)) == [], why


def test_the_storage_state_suite_still_scopes_its_descriptor_fakes() -> None:
    """The observed hazard, pinned by name: the storage-state fakes keep their fd/path comparison."""

    source = (PWMCP / "client" / "tests" / "test_storage_state.py").read_text(encoding="utf-8")
    assert 'monkeypatch.setattr(ss.os, "fdopen", broken_target)' in source
    assert 'monkeypatch.setattr(ss.os, "open", spy)' in source
    assert 'monkeypatch.setattr(ss.os, "open", fail_open)' in source
    assert "real_fdopen" in source and "real_open" in source and "real_geteuid" in source
    # the descriptor spy identifies the file under test instead of answering for every descriptor
    assert "if opened_path == target" in source and "if opened != target" in source
