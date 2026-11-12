"""Pure rules of the estate test policy (AGENTS.md "Tests never depend on host load or speed").

Everything here is a pure function over source text or over snapshots, so it is unit-testable without pytest
hooks. ``plugin.py`` wires it into pytest.
"""

from __future__ import annotations

import ast
import re
import textwrap
from dataclasses import dataclass

RULE_SLEEP = "fixed-sleep"
RULE_TIMEOUT = "short-timeout"
RULE_WALLTIME = "wall-time-assert"
RULE_LEAK = "resource-leak"
RULES = (RULE_SLEEP, RULE_TIMEOUT, RULE_WALLTIME, RULE_LEAK)

TIMEOUT_FLOOR_S = 120.0
SLEEP_THRESHOLD_S = 0.2

BACKLOG_ID = re.compile(r"^[A-Z][A-Z0-9]*(?:-[A-Z0-9]+)*-\d+$")

_CLOCK_FUNCS = {"monotonic", "time", "perf_counter", "monotonic_ns", "time_ns", "perf_counter_ns"}
# Callees whose first positional argument is a timeout.
_POSITIONAL_TIMEOUT_CALLEES = {"wait", "join", "settimeout"}
# Timeout given as keyword is checked on any call; a bare constant is only meaningful there.
_STRING_TIMEOUT = re.compile(r"(?<![A-Za-z0-9_])timeout(?:=|\s+|\s*=\s*)(\d+(?:\.\d+)?)\s*(ms|s|m|h)?(?![A-Za-z0-9_.])")
_UNIT_SECONDS = {None: 1.0, "": 1.0, "s": 1.0, "ms": 0.001, "m": 60.0, "h": 3600.0}


@dataclass(frozen=True)
class Violation:
    rule: str
    lineno: int
    message: str

    def render(self) -> str:
        return f"[{self.rule}] line {self.lineno}: {self.message}"


def _callee_name(call: ast.Call) -> str:
    func = call.func
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return ""


def _callee_dotted(call: ast.Call) -> str:
    parts: list[str] = []
    node: ast.expr = call.func
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return ".".join(reversed(parts))


def _number(node: ast.expr | None) -> float | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
        return float(node.value)
    return None


def _is_clock_call(node: ast.AST) -> bool:
    if not isinstance(node, ast.Call):
        return False
    dotted = _callee_dotted(node)
    return dotted.removeprefix("time.") in _CLOCK_FUNCS


def _parents(tree: ast.AST) -> dict[ast.AST, ast.AST]:
    return {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}


def _inside_loop(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> bool:
    cur = parents.get(node)
    while cur is not None:
        if isinstance(cur, (ast.While, ast.For, ast.AsyncFor)):
            return True
        cur = parents.get(cur)
    return False


def _names_in(node: ast.AST) -> set[str]:
    return {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}


def _tainted_clock_names(tree: ast.AST) -> set[str]:
    """Names bound (transitively) to a clock reading or to arithmetic on one."""
    tainted: set[str] = set()
    changed = True
    while changed:
        changed = False
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
                value = node.value
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.value is not None:
                targets = [node.target.id]
                value = node.value
            else:
                continue
            if any(_is_clock_call(n) for n in ast.walk(value)) or (_names_in(value) & tainted):
                for name in targets:
                    if name not in tainted:
                        tainted.add(name)
                        changed = True
    return tainted


def _check_sleeps(tree: ast.AST, parents: dict[ast.AST, ast.AST], threshold: float) -> list[Violation]:
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        callee = _callee_dotted(node)
        if callee not in {"time.sleep", "asyncio.sleep", "sleep"}:
            continue
        seconds = _number(node.args[0]) if node.args else None
        if seconds is None or seconds < threshold:
            continue
        if _inside_loop(node, parents):
            continue  # a poll loop is a waiting condition
        found.append(
            Violation(
                RULE_SLEEP,
                node.lineno,
                f"{callee}({seconds:g}) is a fixed sleep used as synchronisation; wait on an event, condition or poll loop",
            )
        )
    return found


def _string_timeout_seconds(text: str) -> list[float]:
    values = []
    for match in _STRING_TIMEOUT.finditer(text):
        seconds = float(match.group(1)) * _UNIT_SECONDS[match.group(2)]
        if 0 < seconds < TIMEOUT_FLOOR_S:
            values.append(seconds)
    return values


def _check_timeouts(tree: ast.AST) -> list[Violation]:
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = _callee_name(node)
            for kw in node.keywords:
                value = _number(kw.value) if kw.arg == "timeout" else None
                if value is not None and 0 < value < TIMEOUT_FLOOR_S:
                    found.append(
                        Violation(
                            RULE_TIMEOUT, node.lineno, f"{name}(timeout={value:g}) is below the {TIMEOUT_FLOOR_S:g} s failsafe floor"
                        )
                    )
            if name in _POSITIONAL_TIMEOUT_CALLEES and node.args:
                value = _number(node.args[0])
                if value is not None and 0 < value < TIMEOUT_FLOOR_S:
                    found.append(
                        Violation(
                            RULE_TIMEOUT, node.lineno, f"{name}({value:g}) is below the {TIMEOUT_FLOOR_S:g} s failsafe floor"
                        )
                    )
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            for seconds in _string_timeout_seconds(node.value):
                found.append(
                    Violation(
                        RULE_TIMEOUT,
                        node.lineno,
                        f"string carries a timeout of {seconds:g} s, below the {TIMEOUT_FLOOR_S:g} s failsafe floor",
                    )
                )
    return found


def _check_walltime(tree: ast.AST) -> list[Violation]:
    tainted = _tainted_clock_names(tree)
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assert):
            continue
        compares = [n for n in ast.walk(node.test) if isinstance(n, ast.Compare)]
        for cmp_node in compares:
            has_clock = any(_is_clock_call(n) for n in ast.walk(cmp_node))
            uses_tainted = bool(_names_in(cmp_node) & tainted)
            if has_clock or uses_tainted:
                found.append(
                    Violation(RULE_WALLTIME, node.lineno, "assertion compares elapsed wall time; inject a clock instead")
                )
                break
    return found


def analyze_source(source: str, *, sleep_threshold: float = SLEEP_THRESHOLD_S, first_line: int = 1) -> list[Violation]:
    """Static violations of one test function (or any code fragment); ``first_line`` maps lines to the file."""
    tree = ast.parse(textwrap.dedent(source))
    parents = _parents(tree)
    found = _check_sleeps(tree, parents, sleep_threshold) + _check_timeouts(tree) + _check_walltime(tree)
    shifted = [Violation(v.rule, v.lineno + first_line - 1, v.message) for v in found]
    return sorted(shifted, key=lambda v: (v.lineno, v.rule))


def validate_allow(args: tuple, kwargs: dict) -> tuple[str | None, str | None]:
    """Check one ``estate_allow`` marker; return ``(rule, error)``."""
    rule = args[0] if args else kwargs.get("rule")
    reason = kwargs.get("reason")
    backlog = kwargs.get("backlog")
    if rule not in RULES:
        return None, f"estate_allow needs a rule from {', '.join(RULES)} (got {rule!r})"
    if not isinstance(reason, str) or len(reason.strip()) < 10:
        return rule, f"estate_allow({rule}) requires reason='...' stating why this test must deviate (at least 10 characters)"
    if not isinstance(backlog, str) or not BACKLOG_ID.match(backlog):
        return rule, f"estate_allow({rule}) requires backlog='XX-NN' naming the backlog item that owns the deviation"
    return rule, None


@dataclass(frozen=True)
class Snapshot:
    threads: frozenset
    fds: frozenset
    children: frozenset


def diff_snapshots(before: Snapshot, after: Snapshot) -> list[str]:
    """Describe every resource present in ``after`` but not in ``before`` (all of them, not just the first)."""
    out = []
    out += [f"thread still running: {name}" for name in sorted(after.threads - before.threads)]
    out += [f"file descriptor still open: {fd}" for fd in sorted(after.fds - before.fds)]
    out += [f"child process still present: pid {pid}" for pid in sorted(after.children - before.children)]
    return out
