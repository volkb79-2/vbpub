"""Mutation gate — D-CORRECT-4: assert changed lines have non-hollow tests.

The coverage gate proves a changed line was *executed*, but cannot prove its
behavior was *asserted* — a test that runs the line but asserts nothing
meaningful passes it. This gate adds MUTATION: mutate the changed lines' code,
re-run the tests; a mutant that SURVIVES (tests still green) marks a
hollow-tested line. Scoped to changed lines only via
`coverage_gate.parse_added_lines` so it remains affordable.

The mutation catalogue is `compare-swap`, `boolop-swap`, `bool-const-flip`, and
`falsy-swap`. The last one changes a direct falsy return value to a different
falsy value, exposing error paths that degrade to a benign result.

Usage:

    python -m nyxloom.mutation_gate --test "pytest -q -x tests/"

The `-x` (stop-on-first-failure) is recommended: a mutant only needs one test
to fail to be killed, so -x makes each mutant run cheap.

Exit codes mirror `coverage_gate` exactly (2026-07-27): 0 = all mutants killed,
1 = at least one survived, 2 = a git/IO boundary broke (`CoverageGateError`),
3 = no measurement was possible at all -- a dirty tree under `--source` or a
`base == HEAD` resolution (`coverage_gate.NoMeasurementError`, reused rather
than reimplemented via `_resolve_added_lines`). Without that guard this gate
would report `{} -> "no changed source files to mutate"` on exactly the same
defect: a vacuous pass indistinguishable from "genuinely nothing changed".
"""

from __future__ import annotations

import argparse
import ast
import copy
import itertools
import os
import shlex
import subprocess
import sys
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from types import SimpleNamespace

from nyxloom import coverage_gate, gate_runner

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
    operator: str          # "compare-swap" | "boolop-swap" | "bool-const-flip" | "falsy-swap"
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
    _falsy_idx: dict[int, int] = {}

    def _falsy_swap_target(value: ast.expr) -> ast.expr | None:
        """Return a distinct falsy AST value for a direct return value."""
        if isinstance(value, ast.Constant):
            if isinstance(value.value, bool):
                return None
            if value.value is None:
                return ast.List(elts=[], ctx=ast.Load())
            if value.value == 0 or value.value == "" or value.value == b"":
                return ast.Constant(value=None)
            return None
        if isinstance(value, (ast.List, ast.Tuple, ast.Set)) and not value.elts:
            return ast.Constant(value=None)
        if isinstance(value, ast.Dict) and not value.keys:
            return ast.Constant(value=None)
        return None

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

        def visit_Return(self, node):
            if node.lineno in target_lines:
                target_val = _falsy_swap_target(node.value)
                if target_val is not None:
                    idx = _falsy_idx.get(node.lineno, 0)
                    _falsy_idx[node.lineno] = idx + 1
                    sites.append((
                        (node.lineno, 3, idx),  # type 3 = falsy return
                        node.lineno,
                        "falsy-swap",
                        f"{ast.unparse(node.value)}->{ast.unparse(target_val)}",
                        target_val,
                        idx,  # which falsy return on this line
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

            _falsy_visited: int = 0

            def visit_Return(self, node):
                if node.lineno == lineno and operator == "falsy-swap":
                    if _falsy_swap_target(node.value) is not None:
                        if self._falsy_visited == node_idx:
                            node.value = copy.deepcopy(target_val)  # type: ignore[assignment]
                        self._falsy_visited += 1
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
    SURVIVED (tests still pass). The real implementation isolates the mutant
    in a scratch git worktree (or falls back to in-place on a dirty tree —
    see `_run_is_killed`), runs tests, and restores; a test stub can return a
    canned value. `run_is_killed` MUST be safe to call concurrently from
    multiple threads — mutants are fanned out over a thread pool below.

    Mutant jobs are fanned out over a `ThreadPoolExecutor` (mutants are
    independent: each gets its own isolated worktree/subprocess), but
    aggregation of `total`/`killed`/`survivors` happens single-threaded AFTER
    the pool has joined all work, and `survivors` is sorted before return —
    so the resulting MutationResult is identical regardless of which mutant's
    test subprocess happens to finish first. Parallelizing this loop changes
    wall-clock time only, never the verdict.

    Returns a MutationResult with aggregate statistics.
    """
    killed = 0
    survivors: list[tuple[str, int, str]] = []

    # Build the full job list up front (deterministic submission order), then
    # fan it out. `total` is the job count; `killed`/`survivors` are computed
    # below from the position-aligned results, never from arrival order.
    jobs: list[tuple[str, Mutant]] = []
    for path, (source, changed_lines) in sorted(targets.items()):
        mutants = generate_mutants(source, changed_lines)
        for mutant in mutants:
            jobs.append((path, mutant))

    total = len(jobs)
    max_workers = max(1, (os.cpu_count() or 2) - 2)
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        # `.map` preserves input order in its output regardless of which
        # future completes first, and blocks (joins) until all are done.
        results = list(pool.map(lambda job: run_is_killed(job[0], job[1]), jobs))

    for (path, mutant), is_killed in zip(jobs, results):
        if is_killed:
            killed += 1
        else:
            survivors.append((path, mutant.lineno, mutant.description))

    survivors.sort(key=lambda x: (x[0], x[1], x[2]))
    return MutationResult(total=total, killed=killed, survivors=survivors)


# --------------------------------------------------------------------------- #
# IO SHELL — git / file I/O / subprocess
# --------------------------------------------------------------------------- #

_SCRATCH_COUNTER = itertools.count()  # process-unique, thread-safe (C-level next())


def _fanout_safe(repo: str) -> bool:
    """True only when `repo` sits inside a CLEAN git working tree — the one
    case where per-mutant isolation via a scratch `git worktree add --detach
    HEAD` is safe.

    A scratch worktree checked out at HEAD reflects committed content only.
    If `repo`'s working tree carries uncommitted edits, isolating at HEAD
    would silently test a DIFFERENT source than what's actually on disk — a
    mutation gate that tests the wrong source is a LAUNDERING gate (false
    "all killed"), so a dirty tree must use the in-place fallback instead.

    `repo` not being a git work tree at all (e.g. a bare tmp_path in unit
    tests) also returns False — there is no HEAD to isolate against, so the
    in-place path is the only option.
    """
    inside = subprocess.run(
        ["git", "-C", repo, "rev-parse", "--is-inside-work-tree"],
        capture_output=True, text=True,
    )
    if inside.returncode != 0 or inside.stdout.strip() != "true":
        return False
    status = subprocess.run(
        ["git", "-C", repo, "status", "--porcelain"],
        capture_output=True, text=True,
    )
    return status.returncode == 0 and status.stdout.strip() == ""


def _run_is_killed(
    repo: str,
    path: str,
    mutant: Mutant,
    test_argv: list[str],
) -> bool:
    """Write the mutant source, run the test command, restore original.

    Returns True (killed) if the test subprocess exits with a non-zero code
    (tests failed → the mutant was detected). Returns False (survived) if the
    exit code is zero.

    Dispatches to one of two IO strategies, chosen by `_fanout_safe`:

    - CLEAN git tree → `_run_is_killed_isolated`: each call gets its own
      scratch `git worktree` at HEAD, so concurrent calls (this is the
      callable `evaluate` fans out over a thread pool) never share a mutable
      path. The live checkout is never touched.
    - DIRTY tree, or `repo` isn't a git work tree at all →
      `_run_is_killed_in_place` (today's original, serial-only behavior,
      byte-identical): mutating a scratch worktree-at-HEAD would silently
      drop the uncommitted edits, so isolation is unsafe there and this
      function falls back to writing directly into `repo`. This is the ONLY
      path exercised by callers that pass a non-git `repo` (e.g. existing
      unit tests using a bare `tmp_path`), so their behavior is unchanged.
    """
    if _fanout_safe(repo):
        return _run_is_killed_isolated(repo, path, mutant, test_argv)
    return _run_is_killed_in_place(repo, path, mutant, test_argv)


_IN_PLACE_LOCK = threading.Lock()


def _run_is_killed_in_place(
    repo: str,
    path: str,
    mutant: Mutant,
    test_argv: list[str],
) -> bool:
    """Write the mutant source directly into `repo` (the live/shared tree),
    run the test command there, restore the original. Always restores, even
    on exception or KeyboardInterrupt.

    Serialized process-wide via `_IN_PLACE_LOCK`: two concurrent calls
    against a SHARED path would otherwise clobber each other's write to
    `full_path` (write A, write B, restore A -> B's write is lost; or worse,
    interleaved partial writes). `evaluate`'s fan-out always dispatches every
    mutant through a thread pool regardless of which IO strategy
    `_run_is_killed` picks, so this fallback -- used whenever `repo` is a
    dirty git tree or not a git repo at all (see `_run_is_killed`) -- must
    serialize itself rather than assume single-threaded callers. The lock
    reduces this path to the ORIGINAL one-mutant-at-a-time behavior (byte-
    identical read/write/restore sequence per call), just interleaved with
    other threads' waits instead of a for-loop.
    """
    with _IN_PLACE_LOCK:
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


def _run_is_killed_isolated(
    repo: str,
    path: str,
    mutant: Mutant,
    test_argv: list[str],
) -> bool:
    """Concurrency-safe runner: checks the mutant into a per-call scratch
    `git worktree` at HEAD, runs the test command there, and always tears the
    scratch worktree down — safe to call from many threads at once because
    each call gets its own scratch directory and never touches the live
    checkout.

    `repo` may be a SUBDIRECTORY of the actual git top-level (nyxloom
    self-hosts as a subdir of the vbpub monorepo — see
    `gate_runner._repo_root`'s docstring): the scratch worktree is created at
    the top-level, and `path`/`test_argv`'s cwd are resolved against the same
    relative offset `repo` has from that top-level, so callers that pass
    paths relative to `repo` (as `main` does) keep working unchanged whether
    `repo` IS the top-level or a subdir of it.
    """
    repo_root = gate_runner._repo_root(SimpleNamespace(root=repo))
    offset = os.path.relpath(os.path.abspath(repo), os.path.abspath(repo_root))

    # Unique per call: lineno (readability) + a process-unique counter + a
    # uuid4 suffix (collision-proof across processes too) — NOT just a commit
    # hash, which is shared by every mutant in the same gate run and would
    # collide the instant two mutants ran concurrently.
    uniq = f"mut-{mutant.lineno}-{next(_SCRATCH_COUNTER)}-{uuid.uuid4().hex[:8]}"
    scratch = os.path.join(repo_root, ".worktrees", uniq)

    add = subprocess.run(
        ["git", "-C", repo_root, "worktree", "add", "--detach", scratch, "HEAD"],
        capture_output=True, text=True,
    )
    if add.returncode != 0:
        # Could not create the isolated scratch tree for some reason (e.g. a
        # racing cleanup); fall back to in-place rather than silently
        # skipping the mutant.
        return _run_is_killed_in_place(repo, path, mutant, test_argv)

    scratch_repo = scratch if offset == "." else os.path.join(scratch, offset)
    try:
        full_path = os.path.join(scratch_repo, path)
        with open(full_path, "w", encoding="utf-8") as fh:
            fh.write(mutant.mutated_source)

        proc = subprocess.run(
            test_argv,
            cwd=scratch_repo,
            capture_output=True,
            text=True,
        )
        return proc.returncode != 0
    finally:
        # Always remove the scratch worktree, even on exception/timeout —
        # mirrors gate_runner.run_gate_at_commit's own finally-block discipline.
        subprocess.run(
            ["git", "-C", repo_root, "worktree", "remove", "--force", scratch],
            capture_output=True, text=True,
        )


def _resolve_added_lines(
    repo: str,
    base: str,
    source: str,
) -> dict[str, set[int]]:
    """Resolve the merge base and return added lines (delegates to
    coverage_gate). Also runs coverage_gate's own no-measurement guard
    (`_check_measurable`) between the two -- reusing it rather than
    duplicating it means a dirty tree under `source` or a `base == HEAD`
    resolution refuses HERE too, exactly as it does for the coverage gate,
    instead of this gate separately reaching its own `{} -> "no changed
    source files to mutate"` vacuous pass on the same defect (2026-07-27)."""
    base_rev = coverage_gate._resolve_base(repo, base)
    coverage_gate._check_measurable(repo, base_rev, source)
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
    except coverage_gate.NoMeasurementError as exc:
        print(f"mutation-gate NO MEASUREMENT: {exc}", file=sys.stderr)
        return 3
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
