"""Mutation gate — D-CORRECT-4: assert changed lines have non-hollow tests.

The coverage gate proves a changed line was *executed*, but cannot prove its
behavior was *asserted* — a test that runs the line but asserts nothing
meaningful passes it. This gate adds MUTATION: mutate the changed lines' code,
re-run the tests; a mutant that SURVIVES (tests still green) marks a
hollow-tested line. Scoped to changed lines only via
`coverage_gate.parse_added_lines` so it remains affordable.

Usage:

    python -m nyxloom.mutation_gate --test "pytest -q -x tests/"

The `-x` (stop-on-first-failure) is recommended: a mutant only needs one test
to fail to be killed, so -x makes each mutant run cheap.
"""

from __future__ import annotations

import argparse
import ast
import copy
import os
import shlex
import subprocess
import sys
from dataclasses import dataclass

from nyxloom import coverage_gate

# --------------------------------------------------------------------------- #
# Comparison operator swaps (each maps to its "boundary neighbour" or negation)
# --------------------------------------------------------------------------- #

_COMPARE_SWAP: dict[type, type] = {
    ast.Lt: ast.LtE,
    ast.LtE: ast.Lt,
    ast.Gt: ast.GtE,
    ast.GtE: ast.Gt,
    ast.Eq: ast.NotEq,
    ast.NotEq: ast.Eq,
    ast.Is: ast.IsNot,
    ast.IsNot: ast.Is,
}

_BOOLOP_SWAP: dict[type, type] = {
    ast.And: ast.Or,
    ast.Or: ast.And,
}


# --------------------------------------------------------------------------- #
# Data types
# --------------------------------------------------------------------------- #

@dataclass
class Mutant:
    lineno: int
    operator: str          # "compare-swap" | "boolop-swap" | "bool-const-flip"
    description: str       # e.g. "Lt->LtE", "And->Or", "True->False"
    mutated_source: str    # the FULL file source with exactly this one mutation


@dataclass
class MutationResult:
    total: int
    killed: int
    survivors: list[tuple[str, int, str]]   # (path, lineno, description) sorted

    @property
    def passed(self) -> bool:
        return not self.survivors


# --------------------------------------------------------------------------- #
# PURE CORE — generate_mutants
# --------------------------------------------------------------------------- #

def generate_mutants(source: str, target_lines: set[int]) -> list[Mutant]:
    """Parse `source`; for every mutable node whose `lineno` is in `target_lines`,
    emit one Mutant per applicable mutation. Deterministic order (lineno, operator,
    description). See module docstring for the mutation catalogue.

    Returns [] on parse failure."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []

    # Collect all mutation sites deterministically.
    # Each site: (sort_key, lineno, operator, description, target_cls_or_value,
    #             node_idx, op_idx_or_0)
    # node_idx tracks which Nth node of that type on the line (disambiguation).
    # op_idx_or_0 is the operator index within a Compare node's .ops list;
    # 0 for BoolOp/Constant (ignored).
    sites: list[tuple[tuple[int, int, int], int, str, str, type | object, int, int]] = []

    # Per-line counters for disambiguation
    _compare_idx: dict[int, int] = {}
    _boolop_idx: dict[int, int] = {}
    _const_idx: dict[int, int] = {}

    class _Collector(ast.NodeVisitor):
        def visit_Compare(self, node):
            if node.lineno in target_lines:
                idx = _compare_idx.get(node.lineno, 0)
                _compare_idx[node.lineno] = idx + 1
                for i, op in enumerate(node.ops):
                    op_cls = type(op)
                    if op_cls in _COMPARE_SWAP:
                        target_cls = _COMPARE_SWAP[op_cls]
                        sites.append((
                            (node.lineno, 0, idx * 10 + i),  # type 0 = compare
                            node.lineno,
                            "compare-swap",
                            f"{op_cls.__name__}->{target_cls.__name__}",
                            target_cls,
                            idx,   # which compare node on this line
                            i,     # which operator within .ops
                        ))
            self.generic_visit(node)

        def visit_BoolOp(self, node):
            if node.lineno in target_lines:
                idx = _boolop_idx.get(node.lineno, 0)
                _boolop_idx[node.lineno] = idx + 1
                op_cls = type(node.op)
                if op_cls in _BOOLOP_SWAP:
                    target_cls = _BOOLOP_SWAP[op_cls]
                    sites.append((
                        (node.lineno, 1, idx),  # type 1 = boolop
                        node.lineno,
                        "boolop-swap",
                        f"{op_cls.__name__}->{target_cls.__name__}",
                        target_cls,
                        idx,  # which boolop node on this line
                        0,
                    ))
            self.generic_visit(node)

        def visit_Constant(self, node):
            if isinstance(node.value, bool) and node.lineno in target_lines:
                idx = _const_idx.get(node.lineno, 0)
                _const_idx[node.lineno] = idx + 1
                new_val = not node.value
                sites.append((
                    (node.lineno, 2, idx),  # type 2 = bool const
                    node.lineno,
                    "bool-const-flip",
                    f"{node.value}->{new_val}",
                    new_val,
                    idx,  # which bool constant on this line
                    0,
                ))
            self.generic_visit(node)

    _Collector().visit(tree)
    sites.sort(key=lambda s: s[0])

    mutants: list[Mutant] = []
    for sort_key, lineno, operator, description, target_val, node_idx, op_i in sites:
        tree_copy = copy.deepcopy(tree)

        class _Mutator(ast.NodeTransformer):
            """Apply exactly one mutation to the deep-copied tree."""

            _compare_visited: int = 0

            def visit_Compare(self, node):
                if node.lineno == lineno and operator == "compare-swap":
                    if self._compare_visited == node_idx:
                        node.ops[op_i] = target_val()  # type: ignore[operator]
                    self._compare_visited += 1
                return self.generic_visit(node)

            _boolop_visited: int = 0

            def visit_BoolOp(self, node):
                if node.lineno == lineno and operator == "boolop-swap":
                    if self._boolop_visited == node_idx:
                        node.op = target_val()  # type: ignore[operator]
                    self._boolop_visited += 1
                return self.generic_visit(node)

            _const_visited: int = 0

            def visit_Constant(self, node):
                if (
                    isinstance(node.value, bool)
                    and node.lineno == lineno
                    and operator == "bool-const-flip"
                ):
                    if self._const_visited == node_idx:
                        node.value = target_val  # type: ignore[assignment]
                    self._const_visited += 1
                return self.generic_visit(node)

        _Mutator().visit(tree_copy)
        ast.fix_missing_locations(tree_copy)
        mutated_source = ast.unparse(tree_copy)

        mutants.append(Mutant(
            lineno=lineno,
            operator=operator,
            description=description,
            mutated_source=mutated_source,
        ))

    return mutants


# --------------------------------------------------------------------------- #
# PURE CORE — evaluate (orchestrator, test-runner injected)
# --------------------------------------------------------------------------- #

def evaluate(
    targets: dict[str, tuple[str, set[int]]],
    run_is_killed,  # callable: (path: str, mutant: Mutant) -> bool
) -> MutationResult:
    """Run mutation testing on the given targets.

    `targets`: {path: (source, changed_lines)} — per file, the full source text
    and the set of changed (added/edited) line numbers to mutate.

    `run_is_killed(path, mutant) -> bool`: injected test runner. Must return
    True when the mutant is KILLED (tests fail with it applied), False when it
    SURVIVED (tests still pass). The real implementation writes the mutant to
    disk, runs tests, and restores; a test stub can return a canned value.

    Returns a MutationResult with aggregate statistics.
    """
    total = 0
    killed = 0
    survivors: list[tuple[str, int, str]] = []

    for path, (source, changed_lines) in sorted(targets.items()):
        mutants = generate_mutants(source, changed_lines)
        for mutant in mutants:
            total += 1
            if run_is_killed(path, mutant):
                killed += 1
            else:
                survivors.append((path, mutant.lineno, mutant.description))

    survivors.sort(key=lambda x: (x[0], x[1], x[2]))
    return MutationResult(total=total, killed=killed, survivors=survivors)


# --------------------------------------------------------------------------- #
# IO SHELL — git / file I/O / subprocess
# --------------------------------------------------------------------------- #

def _run_is_killed(
    repo: str,
    path: str,
    mutant: Mutant,
    test_argv: list[str],
) -> bool:
    """Write the mutant source to disk, run the test command, restore original.

    Returns True (killed) if the test subprocess exits with a non-zero code
    (tests failed → the mutant was detected). Returns False (survived) if the
    exit code is zero. Always restores the original file content, even on
    exception or KeyboardInterrupt.
    """
    full_path = os.path.join(repo, path)

    # Stash original bytes
    with open(full_path, "rb") as fh:
        original = fh.read()

    try:
        # Write mutant
        with open(full_path, "w", encoding="utf-8") as fh:
            fh.write(mutant.mutated_source)

        # Run tests
        proc = subprocess.run(
            test_argv,
            cwd=repo,
            capture_output=True,
            text=True,
        )
        return proc.returncode != 0
    finally:
        # Always restore original, even on failure / KeyboardInterrupt
        with open(full_path, "wb") as fh:
            fh.write(original)


def _resolve_added_lines(
    repo: str,
    base: str,
    source: str,
) -> dict[str, set[int]]:
    """Resolve the merge base and return added lines (delegates to
    coverage_gate)."""
    base_rev = coverage_gate._resolve_base(repo, base)
    return coverage_gate._git_added_lines(repo, base_rev, source)


def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m nyxloom.mutation_gate",
        description="Fail when changed lines survive mutation (D-CORRECT-4).",
    )
    p.add_argument("--base", default="main",
                   help="ref the change is measured against (default: main)")
    p.add_argument("--source", default="src/nyxloom",
                   help="source path prefix the gate enforces (default: src/nyxloom)")
    p.add_argument("--repo", default=".",
                   help="git repo/worktree to run diff in (default: cwd)")
    p.add_argument("--test", required=False, default=None,
                   help="test command, e.g. 'pytest -q -x tests/' (default: derive"
                        " from changed source files)")
    return p


def _derive_test_command(path: str) -> list[str] | None:
    """Map a changed source file path to its sibling test command.

    Converts `src/nyxloom/<mod>.py` -> `tests/test_<mod>.py` and returns
    `["python", "-m", "pytest", "-q", "<test_file>"]` if that file exists.
    Returns None when no sibling test file exists.
    """
    mod = path.replace("\\", "/")
    if not mod.endswith(".py"):
        return None
    if "/" in mod:
        parts = mod.split("/")
        mod_name = parts[-1]
    else:
        mod_name = mod
    test_file = mod_name.replace(".py", "")
    if test_file.startswith("test_"):
        return None
    test_file = f"tests/test_{test_file}.py"
    return ["python", "-m", "pytest", "-q", test_file]


def main(argv: list[str] | None = None) -> int:
    args = _build_arg_parser().parse_args(argv)
    explicit_test = args.test is not None
    if explicit_test:
        test_argv = shlex.split(args.test)
    else:
        test_argv = None  # derived per-file below

    try:
        added = _resolve_added_lines(args.repo, args.base, args.source)
    except coverage_gate.CoverageGateError as exc:
        print(f"mutation-gate ERROR: {exc}", file=sys.stderr)
        return 2

    # Build targets: read each changed source file
    targets: dict[str, tuple[str, set[int]]] = {}
    missing_test: list[str] = []
    for path, lines in added.items():
        full_path = os.path.join(args.repo, path)
        if not os.path.isfile(full_path):
            print(f"mutation-gate WARNING: changed file not found: {path}", file=sys.stderr)
            continue
        with open(full_path, encoding="utf-8") as fh:
            source = fh.read()
        targets[path] = (source, lines)

    if not targets:
        print("mutation OK: no changed source files to mutate")
        return 0

    # When --test is omitted, derive the test command per file and check for
    # missing sibling tests BEFORE mutating.
    if not explicit_test:
        derived_test_argv: dict[str, list[str]] = {}
        for path in targets:
            cmd = _derive_test_command(path)
            if cmd is None:
                missing_test.append(path)
            else:
                # Only add if the sibling test file actually exists on disk
                # (cmd[-1] is the test file path relative to repo)
                test_file = os.path.join(args.repo, cmd[-1])
                if os.path.isfile(test_file):
                    derived_test_argv[path] = cmd
                else:
                    missing_test.append(path)
        if missing_test:
            missing_test.sort()
            for mp in missing_test:
                print(f"mutation-gate FAIL: changed file {mp} has no sibling test file"
                      " (no test to mutate against)",
                      file=sys.stderr)
            return 1
        # All paths have tests; use a single deduped test command for all mutants
        all_cmds = list(dict.fromkeys(
            tuple(cmd) for cmd in derived_test_argv.values()))
        if len(all_cmds) == 1:
            test_argv = list(all_cmds[0])
        else:
            # Multiple distinct test commands; concatenate the test file args
            base = ["python", "-m", "pytest", "-q"]
            test_files = list(dict.fromkeys(
                cmd[-1] for cmd in derived_test_argv.values()))
            test_argv = base + test_files
    # test_argv is now guaranteed to be set (either explicit or derived)
    assert test_argv is not None

    def _run_is_killed_bound(path: str, mutant: Mutant) -> bool:
        return _run_is_killed(args.repo, path, mutant, test_argv)

    result = evaluate(targets, _run_is_killed_bound)

    if result.passed:
        print(
            f"mutation OK: {result.killed}/{result.total} mutants killed"
        )
        return 0

    print(
        f"mutation FAIL: {result.killed}/{result.total} mutants killed, "
        f"{len(result.survivors)} survived."
    )
    print("Surviving mutants (a test asserting this line's behavior would have "
          "failed; add/repair it):")
    for path, lineno, desc in result.survivors:
        print(f"  SURVIVED {path}:{lineno} {desc}")
    return 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
