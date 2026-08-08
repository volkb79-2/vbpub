"""Mutant identity, and the byte-offset arithmetic every mutation engine
shares — P11: **each mutant assay generates is a valid single changed-line
experiment, not arbitrary broken text.**

This module owns exactly two things, both deliberately language-free so a
future second mutation engine (were one ever built — none is, for Go, per
A-042) would not need to duplicate them:

* :class:`Mutant` — the typed result of one construction (A-092/A-114).
  Lives here, not in ``adapters/base.py``, because ``verdict.py`` is
  forbidden to this package and ``Mutant`` needs a home the frozen protocol
  module can import from for its own type hint.
* :func:`byte_offset` / :func:`line_for_offset` — the ``(lineno,
  col_offset) <-> absolute byte index`` conversions a byte-exact splice
  needs. ``ast``'s own ``col_offset``/``end_col_offset`` are **UTF-8 byte**
  offsets within a physical line (verified empirically against a non-ASCII
  fixture while authoring this package: a multi-byte character before a
  node shifts its ``col_offset`` by its own BYTE width, not by 1), so any
  splice arithmetic done in Python ``str`` character units would silently
  misalign the moment source contains a non-ASCII character. Operating in
  ``bytes`` throughout removes that whole class of bug rather than trusting
  ASCII-only fixtures to hide it.

**Not here, deliberately**: any AST walk, any node-type catalogue, any
splice construction. Those are PYTHON syntax knowledge (A-097's own
adapter-surface split: a source language's own syntax lives only in its
adapter) and stay in ``adapters/python.py``, mirroring where P07 put
``_statement_spans`` — the shared TYPE (``StatementSpan``) lives beside the
protocol, the language-specific WALK stays in the language's own adapter
file. ``Mutant`` is the identical split, one module over, forced sideways
into its own module only because ``verdict.py`` (where ``StatementSpan``'s
sibling types live) is off-limits here.

**P12 adds execution** (A-119): baseline-gated, isolated, ``jobs``-bounded
mutation running lives HERE too, beside :class:`Mutant`, mirroring
``canary.py``'s own precedent of holding one claim-tier's entire
orchestration in a single dedicated module. The surface is
:func:`run_mutation` (the entry point: a bounded executor fan-out over
:func:`collect_mutants`'s job list, then deterministic aggregation into a
:class:`~assay.verdict.Mutation`), :func:`judge_mutation` /
:func:`build_mutation_claim` (the pure outcome mapping and R2
:class:`~assay.verdict.Claim` wiring, A-117), and :func:`collect_mutants`
(the cross-file aggregation this package's own implementation choice, see
the LOG).

**P18 wires this into the installed CLI** (work items 2-5): the baseline
:func:`run_mutation` gates on is no longer run BY this module — it is now
a required *baseline* parameter, the exact :class:`~assay.runner.
CommandResult` R0 already produced, so the lane's command runs at most
once per ``assay run`` invocation (sol finding 11). :func:`
resolve_mutation_targets` builds the per-file candidate list from the same
resolved diff R1 measures against, and *operators* filters
:func:`collect_mutants`'s output down to the lane's own declared,
closed selection before anything is submitted.

**A circular-import note, since it shapes this module's own imports below**:
``assay.runner`` imports ``assay.adapters.base``, which imports THIS module
for :class:`Mutant`. A module-level ``from .runner import execute_command``
here would therefore be a genuine cycle (``mutation -> runner -> adapters.
base -> mutation``), not merely a style preference — verified empirically
while authoring this package: it breaks for whichever entry point happens
to import ``assay.adapters.base`` (or ``assay.runner``) BEFORE
``assay.mutation`` finishes loading, which is common (most adapter tests
trigger it). :func:`run_mutation` resolves ``execute_command`` /
``default_process_runner`` with a DEFERRED (function-body-local) import
instead — safe because by the time the function is actually CALLED, module
loading for the whole program has long finished, regardless of which module
was imported first. The ``ProcessRunner``/``CommandResult``/
``LanguageAdapter`` type hints below reference their real homes (
``assay.runner``, ``assay.adapters.base``) by bare name, never imported —
safe under this module's own ``from __future__ import annotations``, which
defers every annotation to a string never evaluated at runtime (verified
empirically), and avoids re-opening the same cycle purely for a type hint.
"""

from __future__ import annotations

import fnmatch
import shutil
from concurrent.futures import Executor, ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable, Sequence

from .config import Lane
from .diff import AddedLines
from .errors import Outcome, ReasonCode
from .verdict import Claim, Mutation, MutantOutcome

__all__ = [
    "MUTATION_OPERATORS",
    "Clock",
    "ExecutorFactory",
    "Mutant",
    "MutantJob",
    "MutationTarget",
    "build_mutation_claim",
    "byte_offset",
    "collect_mutants",
    "judge_mutation",
    "line_for_offset",
    "resolve_mutation_targets",
    "run_mutation",
]

#: The injectable clock, identical in shape to ``assay.runner.Clock`` --
#: duplicated rather than imported (this module's own circular-import note,
#: above): a plain function type alias costs nothing to restate and needs
#: no deferral, unlike the runtime-called ``execute_command``.
Clock = Callable[[], datetime]

#: The closed, four-value mutation catalogue (A-112/A-114), adopted verbatim
#: from ``/workspaces/vbpub/nyxloom/src/nyxloom/mutation_gate.py`` and
#: DESIGN-GUIDE §11's own TOML example (``operators =
#: ["compare-swap","boolop-swap","bool-const-flip","falsy-swap"]``).
#: ``Mutant.operator`` is validated against this set as a plain ``str``, not
#: an enum — matching :class:`~assay.verdict.CanaryResult`'s own
#: ``mechanism: str`` precedent for a package-local closed vocabulary
#: (A-114).
MUTATION_OPERATORS: frozenset[str] = frozenset(
    {"compare-swap", "boolop-swap", "bool-const-flip", "falsy-swap"}
)


@dataclass(frozen=True, kw_only=True)
class Mutant:
    """One valid, single-site mutation of a source file (A-092/A-114).

    ``mutated_text`` is the FULL text with exactly ONE construct changed —
    the byte-exact splice result, never a whole-file reprint (A-112's own
    "not ``ast.unparse`` on a tree copy" ruling). Every byte of
    ``mutated_text`` outside the one changed span is identical to the input
    ``text`` :meth:`~assay.adapters.python.PythonAdapter.generate_mutants`
    was called with, including every newline, comment, and quote style —
    that byte-preservation property is O1's own claim and is proven at the
    test layer (a hand-derived one-diff assertion per fixture), not
    re-asserted structurally here: this dataclass has no way to see the
    original ``text`` to compare against, by design (A-114 lists exactly
    four fields; carrying the pre-mutation text as a fifth would let a
    caller diff against the WRONG original after a mutant crosses a
    boundary, e.g. a JSON round-trip, where only ``mutated_text`` survives).

    :attr:`identity` is **derived**, not stored (A-114): it mirrors
    :class:`~assay.verdict.EvidenceDeclaration.identity` /
    :class:`~assay.verdict.Evidence.identity`'s own "a property built from
    already-present fields" shape. ``(lineno, operator, description)`` alone
    is not always unique — a 3+-operand boolean chain (A-115) produces
    several sites sharing an identical ``(lineno, operator, description)``
    triple (e.g. two ``"And->Or"`` sites on the same line, for ``a and b
    and c``) — so ``mutated_text`` is folded into the tuple too: two
    genuinely different splices always produce two different full texts
    (the changed span sits at a different byte offset), which is exactly
    the "stable identity" O1 asks for without inventing a fifth stored
    field (a byte-offset column) nothing else in the return contract needs.
    """

    lineno: int
    operator: str
    description: str
    mutated_text: str

    def __post_init__(self) -> None:
        if isinstance(self.lineno, bool) or not isinstance(self.lineno, int):
            raise ValueError(f"Mutant.lineno must be an integer, got {self.lineno!r}")
        if self.lineno < 1:
            raise ValueError(f"Mutant.lineno must be >= 1, got {self.lineno}")
        if self.operator not in MUTATION_OPERATORS:
            raise ValueError(
                f"Mutant.operator must be one of {sorted(MUTATION_OPERATORS)}, "
                f"got {self.operator!r}"
            )
        if not isinstance(self.description, str) or not self.description:
            raise ValueError(
                f"Mutant.description must be a non-empty string, got "
                f"{self.description!r}"
            )
        if not isinstance(self.mutated_text, str) or not self.mutated_text:
            raise ValueError(
                f"Mutant.mutated_text must be a non-empty string, got "
                f"{self.mutated_text!r}"
            )

    @property
    def identity(self) -> tuple[int, str, str, str]:
        return (self.lineno, self.operator, self.description, self.mutated_text)


def byte_offset(text_bytes: bytes, lineno: int, col_offset: int) -> int:
    """Convert a 1-based ``lineno`` and a 0-based UTF-8 byte ``col_offset``
    — the exact units ``ast`` reports on every node's own ``lineno``/
    ``col_offset``/``end_lineno``/``end_col_offset`` — into an absolute byte
    index into ``text_bytes`` (``text.encode("utf-8")``).

    Assumes ``\\n`` alone terminates a physical line, matching how every
    fixture this project commits is written (and how ``ast`` itself counts
    ``lineno`` for LF-terminated source); a ``\\r\\n`` file would shift
    every offset after the first line by the accumulated ``\\r`` count,
    which this project's own fixtures never exercise.
    """
    offset = 0
    remaining = lineno - 1
    while remaining > 0:
        newline_index = text_bytes.index(b"\n", offset)
        offset = newline_index + 1
        remaining -= 1
    return offset + col_offset


def line_for_offset(text_bytes: bytes, offset: int) -> int:
    """The 1-based physical line containing byte index ``offset`` in
    ``text_bytes`` — the inverse direction of :func:`byte_offset`, used to
    give a mutation SITE (an operator token's own location, which may sit
    on a different physical line than the AST node that owns it — A-115's
    own boolean-chain-wrapping case) its own precise ``lineno``, rather than
    inheriting the enclosing node's ``lineno``.
    """
    return text_bytes.count(b"\n", 0, offset) + 1


# --------------------------------------------------------------------------
# P12: bounded mutation execution
# --------------------------------------------------------------------------


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True, kw_only=True)
class MutationTarget:
    """One changed file to mutate: its repo-relative *path* (the same
    forward-slash, repo-top-relative spelling ``adapters/base.py``'s own
    path contract requires), its CURRENT text, and the changed line numbers
    scoping candidate sites — the exact per-file input
    ``LanguageAdapter.generate_mutants(text, lines)`` needs (P11), paired
    with the *path* identity :class:`Mutant` itself carries no field for.

    Building this from a real diff/adapter/filesystem is deliberately
    OUTSIDE this package's scope — P12 owns EXECUTION, not diff-to-target
    resolution (the handoff's own "Scope / forbid" section: "Mutant
    construction and adapter capability are frozen P11 inputs. This package
    owns execution and the R2 producer only.") — a future caller (P14's CLI
    wiring) constructs these from :class:`~assay.diff.AddedLines` plus the
    real source tree, the same way
    :func:`~assay.canary.run_python_canary` receives its ``target_path``
    already resolved rather than deriving it itself.
    """

    path: str
    text: str
    lines: frozenset[int]

    def __post_init__(self) -> None:
        if not isinstance(self.path, str) or not self.path:
            raise ValueError(
                f"MutationTarget.path must be a non-empty string, got {self.path!r}"
            )
        if not isinstance(self.text, str):
            raise ValueError(
                f"MutationTarget.text must be a string, got {self.text!r}"
            )
        if not isinstance(self.lines, frozenset) or not self.lines:
            raise ValueError(
                f"MutationTarget.lines must be a non-empty frozenset of line "
                f"numbers, got {self.lines!r} — a file contributing no "
                f"changed lines has nothing to mutate and should simply be "
                f"omitted from the target list"
            )
        for line in self.lines:
            if isinstance(line, bool) or not isinstance(line, int) or line < 1:
                raise ValueError(
                    f"MutationTarget.lines must contain only positive line "
                    f"numbers, got {line!r}"
                )


def resolve_mutation_targets(
    added: AddedLines,
    *,
    repo_top: Path,
    source_root_paths: Sequence[Path],
    adapter: LanguageAdapter,
    read_source_text: Callable[[str], str],
) -> tuple[MutationTarget, ...]:
    """Build R2's per-file candidate list from the SAME resolved diff R1
    measures against (P18, work item 2) -- *added* is
    :func:`~assay.diff.parse_added_lines`'s own output, threaded in by the
    caller (:func:`~assay.runner.run_lane`) rather than re-derived here, so
    a lane declaring both R1 and R2 diffs its base exactly once (this
    package's own carried-in note: "R2 target selection must consume the
    same measurement, not invoke Git independently with a second base").

    A changed file becomes a candidate target under the identical gates
    :func:`assay.evaluate.evaluate_coverage`'s own (private)
    ``_is_considered`` already applies for R1 -- under a declared source
    root, not inside one of the adapter's own excluded directories, and
    matching one of the adapter's own ``source_globs`` -- plus the
    adapter's own :meth:`~assay.adapters.base.LanguageAdapter.is_test_path`
    exclusion. Deliberately a SEPARATE, duplicated copy of that check
    rather than an import from :mod:`assay.evaluate`: that module sits
    outside this package's own ``scope.touch``, and two independently
    written copies that must agree is a real check one level up -- the
    same reasoning this module's own :func:`_mutant_outcome_sort_key`
    already gives for keeping its own copy rather than importing
    ``verdict``'s private sort key.

    Returned in PATH order (never *added.by_file*'s own iteration order,
    which is not itself a promised ordering) -- the same determinism
    :func:`collect_mutants` already applies one level down, so a caller
    never has to sort twice. *read_source_text* is the injectable
    filesystem boundary (AUTHORING.md §3b.E), called once per considered
    file, mirroring :func:`assay.runner.evaluate_r1`'s own
    ``read_source_text`` closure.

    A file contributing no lines that survive these gates is simply
    absent from the result -- never present with an empty
    :class:`MutationTarget`, which could not construct anyway (its own
    ``lines`` field refuses empty, A-092).
    """
    targets: list[MutationTarget] = []
    for path in sorted(added.by_file):
        lines = added.by_file[path]
        abs_path = (repo_top / path).resolve()
        if not any(abs_path.is_relative_to(root) for root in source_root_paths):
            continue
        if any(part in adapter.excluded_dir_names for part in Path(path).parts[:-1]):
            continue
        if not any(fnmatch.fnmatch(path, glob) for glob in adapter.source_globs):
            continue
        if adapter.is_test_path(path):
            continue
        targets.append(
            MutationTarget(
                path=path, text=read_source_text(path), lines=frozenset(lines)
            )
        )
    return tuple(targets)


@dataclass(frozen=True, kw_only=True)
class MutantJob:
    """One ``(file, mutant)`` pair — the unit of work :func:`run_mutation`
    fans out over the executor. A dataclass rather than a bare tuple
    (A-092): the two fields are semantically distinct (a file identity and
    a construction result), not an ad hoc pair.
    """

    path: str
    mutant: Mutant


def collect_mutants(
    targets: Iterable[MutationTarget],
    *,
    adapter: LanguageAdapter,
) -> tuple[MutantJob, ...]:
    """Aggregate every candidate mutant across POSSIBLY MANY changed files
    into ONE deterministic job list — this package's own answer to
    aggregating a lane-wide claim from per-file ``generate_mutants`` calls
    (a design choice the handoff leaves to the implementer; see the LOG for
    the full reasoning).

    Calls ``adapter.generate_mutants(target.text, set(target.lines))`` once
    per *target*, in *target.path* order — deterministic regardless of the
    iteration order of *targets* itself, so a caller passing an unordered
    collection still gets a stable job list. ``"UNSUPPORTED"`` for one file
    contributes ZERO mutants from that file and is never an abort of the
    whole call (P11's own per-file union, A-114); the ALL-UNSUPPORTED-or-
    empty case collapses naturally into ``len(result) == 0``, which
    :func:`run_mutation` reads as ``total == 0`` -> ``NO_MUTANTS`` (A-117)
    without this function needing to special-case it.

    Within one file, P11's own ``generate_mutants`` order (``lineno``,
    ``operator``, ``description``, byte offset) is preserved verbatim —
    this function only adds the cross-file ``path`` ordering on top.
    """
    jobs: list[MutantJob] = []
    for target in sorted(targets, key=lambda item: item.path):
        result = adapter.generate_mutants(target.text, set(target.lines))
        if result == "UNSUPPORTED":
            continue
        for mutant in result:
            jobs.append(MutantJob(path=target.path, mutant=mutant))
    return tuple(jobs)


#: The injectable executor boundary (A-082/A-119/A-122): the entry point
#: :func:`run_mutation` calls with EXACTLY the caller-declared ``jobs`` —
#: never ``os.cpu_count()`` or a mutant-count-derived value (A-122's own
#: confirmed trap in ``mutation_gate.evaluate``, not ported). The default
#: constructs the real ``ThreadPoolExecutor``; a test injects a factory that
#: RECORDS what it was called with, proving the bound at the construction
#: boundary itself (O2), never by elapsed time.
ExecutorFactory = Callable[[int], Executor]


def _default_executor_factory(jobs: int) -> Executor:
    return ThreadPoolExecutor(max_workers=jobs)


def _mutant_outcome_sort_key(outcome: MutantOutcome) -> tuple[str, int, str, str]:
    """The stable identity :func:`run_mutation` sorts the three non-killed
    buckets by before constructing :class:`~assay.verdict.Mutation` — kept
    as this module's OWN copy rather than importing ``verdict``'s private
    validation helper of the same shape: two independently written copies
    that must agree is a real check (a drift between them would surface as
    ``Mutation`` refusing to construct, A-067's own "two independently
    verified layers" discipline one level down), not merely duplication.
    """
    return (outcome.path, outcome.lineno, outcome.operator, outcome.description)


def _classify_mutant_result(result: CommandResult) -> str:
    """Map one mutant's :class:`~assay.runner.CommandResult` onto one of
    the FOUR terminal buckets A-116/A-117 name — reusing
    :func:`~assay.runner.execute_command`'s own already-correct
    PASS/FAIL/ERROR/BUDGET_EXCEEDED split, never nyxloom's collapsed
    any-non-zero-exit-is-"killed" rule (A-122, confirmed present in
    ``mutation_gate._run_is_killed*``, deliberately not ported).

    An ordinary non-zero exit (FAIL/COMMAND_FAILED) means the suite caught
    the mutant — KILLED. An exit-0 PASS means the changed behaviour was
    never asserted — SURVIVED. ERROR/EXEC_FAILED (the process itself could
    not even start) is CRASHED, never conflated with a genuine test
    failure. BUDGET_EXCEEDED is its own bucket, never silently folded into
    either kill or survival.
    """
    if result.outcome is Outcome.PASS:
        return "survived"
    if result.outcome is Outcome.FAIL:
        return "killed"
    if result.outcome is Outcome.BUDGET_EXCEEDED:
        return "budget_exceeded"
    return "crashed"  # Outcome.ERROR -- the only remaining R0-producible outcome.


def _run_one_mutant(
    lane: Lane,
    *,
    job: MutantJob,
    project_root: Path,
    scratch_parent: Path,
    index: int,
    execute: Callable[..., CommandResult],
    process_runner: ProcessRunner,
    clock: Clock,
) -> CommandResult:
    """Isolate *job* into a FRESH ``shutil.copytree`` scratch directory
    (A-120 — never in-place, never a ``git worktree``), write
    ``job.mutant.mutated_text`` over ``job.path`` INSIDE the copy, and run
    the SAME ``execute_command`` (*execute*) the baseline used, varying
    only ``cwd``. The scratch copy is discarded afterward; *project_root*
    itself is never opened for writing here — that is what makes "the
    shared source tree is provably unchanged" true BY CONSTRUCTION, not by
    a write-then-restore round-trip (O3).

    *index* names the scratch directory (``mutant-{index:06d}``) — unique
    per submitted job with no shared counter or uuid needed, because the
    job list's own length is already known up front before any job is
    submitted (A-113's "deterministic submission order").
    """
    scratch_dir = scratch_parent / f"mutant-{index:06d}"
    shutil.copytree(project_root, scratch_dir)
    try:
        (scratch_dir / job.path).write_text(job.mutant.mutated_text, encoding="utf-8")
        return execute(lane, cwd=scratch_dir, process_runner=process_runner, clock=clock)
    finally:
        shutil.rmtree(scratch_dir, ignore_errors=True)


def _filter_by_operators(
    jobs: tuple[MutantJob, ...], operators: frozenset[str]
) -> tuple[MutantJob, ...]:
    """P18 work item 3's own filtering step, kept separate from
    :func:`collect_mutants` (P11's already-independently-tested
    construction/aggregation surface) rather than folded into it: retains
    only the jobs whose :attr:`Mutant.operator` is in the lane's declared,
    closed *operators* set. An adapter's ``UNSUPPORTED`` return, a
    syntactically valid file contributing no eligible site, and an
    operator filter excluding every remaining candidate all collapse into
    the identical empty result here — :func:`run_mutation` reads all three
    the same honest way, ``total == 0`` -> ``INCONCLUSIVE``/``NO_MUTANTS``
    (A-117), with no special-casing needed for which of the three actually
    happened. Order is preserved from *jobs* (already path/lineno/operator/
    description-ordered by :func:`collect_mutants`) — filtering removes
    entries, it never reorders the survivors.
    """
    return tuple(job for job in jobs if job.mutant.operator in operators)


def run_mutation(
    lane: Lane,
    *,
    baseline: CommandResult,
    project_root: Path,
    scratch_root: Path,
    targets: Iterable[MutationTarget],
    adapter: LanguageAdapter,
    jobs: int,
    operators: Iterable[str],
    process_runner: ProcessRunner | None = None,
    clock: Clock | None = None,
    executor_factory: ExecutorFactory = _default_executor_factory,
) -> Mutation | None:
    """The R2 execution entry point (A-119/A-120; *baseline* refactored in
    P18 work item 4). *baseline* is now a MANDATORY, ALREADY-OBTAINED
    :class:`~assay.runner.CommandResult` — the exact R0 result
    :func:`~assay.runner.run_lane` already produced for this same lane
    against this same, still-unmodified *project_root* — never re-executed
    here (sol finding 11: the old internal baseline doubled the command
    ledger). A baseline that did not PASS stops HERE, before
    :func:`collect_mutants` is even called, let alone anything submitted
    to an executor (O1's own negative: "submits mutant work... when the
    original suite is already red") — and this function returns ``None``
    (A-116's baseline-conditional presence rule: mutation testing never
    started). :func:`judge_mutation`/:func:`build_mutation_claim` read the
    caller's OWN *baseline* to propagate its exact ``(outcome,
    reason_code)`` verbatim in that case, so a caller passes the identical
    object to both this function and those.

    *jobs* is validated (a positive, non-boolean integer) BEFORE the
    executor boundary (work item 5): this function is a public surface a
    caller may invoke directly — every test in this module's own suite
    does — so :mod:`assay.config`'s load-time discipline for a real
    ``assay.toml`` cannot be assumed to have already run here.

    Only when the baseline PASSES: collects every candidate site across
    *targets*, retains only the ones whose operator is in *operators*
    (work item 3 — the lane's own declared, closed selection; a
    :class:`Mutant` may name any of :data:`MUTATION_OPERATORS`, but only
    the DECLARED subset is ever actually submitted; *operators* itself is
    trusted here, not re-validated against the closed vocabulary — that
    check is :mod:`assay.config`'s, at load time, and an unknown or empty
    *operators* passed directly still degrades honestly, matching nothing
    and collapsing into the same ``total == 0`` path below), then fans the
    resulting job list out over ``executor_factory(jobs)`` — called with
    EXACTLY *jobs*, never a derived or machine-sourced value (A-082/
    A-122) — isolating each mutant into its own fresh scratch copy under
    *scratch_root* (A-120). Results are collected POSITION-ALIGNED with
    the submitted job list (each ``Future`` is awaited by its own index
    via the returned futures list, never via ``as_completed`` or a dict
    keyed by arrival order), and the three non-killed buckets are sorted
    by stable identity before :class:`~assay.verdict.Mutation` is built —
    so ``jobs=1`` and ``jobs=3`` render IDENTICAL records regardless of
    which thread's subprocess happens to finish first (O2/O3).

    *process_runner*/*clock* default to the real boundary
    (``assay.runner.default_process_runner`` / a real UTC clock), resolved
    with a DEFERRED import inside this function body rather than at module
    level — this module's own docstring explains why (a genuine circular
    import, not a style choice).
    """
    if isinstance(jobs, bool) or not isinstance(jobs, int):
        raise ValueError(f"run_mutation jobs must be an integer, got {jobs!r}")
    if jobs < 1:
        raise ValueError(f"run_mutation jobs must be >= 1, got {jobs}")

    if baseline.outcome is not Outcome.PASS:
        return None

    job_list = _filter_by_operators(
        collect_mutants(targets, adapter=adapter), frozenset(operators)
    )
    total = len(job_list)
    if total == 0:
        return Mutation(total=0, killed=0)

    from .runner import default_process_runner, execute_command

    resolved_process_runner = (
        default_process_runner if process_runner is None else process_runner
    )
    resolved_clock = _utc_now if clock is None else clock

    def _run(job: MutantJob, index: int) -> CommandResult:
        return _run_one_mutant(
            lane,
            job=job,
            project_root=project_root,
            scratch_parent=scratch_root,
            index=index,
            execute=execute_command,
            process_runner=resolved_process_runner,
            clock=resolved_clock,
        )

    with executor_factory(jobs) as pool:
        futures = [pool.submit(_run, job, index) for index, job in enumerate(job_list)]
        results = [future.result() for future in futures]

    killed = 0
    survived: list[MutantOutcome] = []
    crashed: list[MutantOutcome] = []
    budget_exceeded: list[MutantOutcome] = []
    for job, result in zip(job_list, results):
        bucket = _classify_mutant_result(result)
        if bucket == "killed":
            killed += 1
            continue
        outcome_entry = MutantOutcome(
            path=job.path,
            lineno=job.mutant.lineno,
            operator=job.mutant.operator,
            description=job.mutant.description,
        )
        if bucket == "survived":
            survived.append(outcome_entry)
        elif bucket == "crashed":
            crashed.append(outcome_entry)
        else:
            budget_exceeded.append(outcome_entry)

    survived.sort(key=_mutant_outcome_sort_key)
    crashed.sort(key=_mutant_outcome_sort_key)
    budget_exceeded.sort(key=_mutant_outcome_sort_key)

    return Mutation(
        total=total,
        killed=killed,
        survived=tuple(survived),
        crashed=tuple(crashed),
        budget_exceeded=tuple(budget_exceeded),
    )


def judge_mutation(
    baseline: CommandResult, mutation: Mutation | None
) -> tuple[Outcome, ReasonCode | None]:
    """A-117's outcome/reason-code mapping, using only already-existing
    ``ReasonCode``s (``errors.py`` stays forbidden, A-121): baseline
    non-PASS reuses ``execute_command``'s own ``(outcome, reason_code)``
    verbatim (*mutation* is ``None`` in that case, A-116); else
    ``mutation.total == 0`` -> ``INCONCLUSIVE``/``NO_MUTANTS``; else
    non-empty ``crashed`` -> ``ERROR``/``EXEC_FAILED``; else non-empty
    ``budget_exceeded`` -> ``BUDGET_EXCEEDED``/``LANE_TIMEOUT``; else
    non-empty ``survived`` -> ``FAIL``/``MUTANTS_SURVIVED``; else ``PASS``.
    This precedence (crashed > budget_exceeded > survived) matches the
    existing cross-claim ``ROLLUP_PRECEDENCE`` applied one level down.
    """
    if mutation is None:
        return baseline.outcome, baseline.reason_code
    if mutation.total == 0:
        return Outcome.INCONCLUSIVE, ReasonCode.NO_MUTANTS
    if mutation.crashed:
        return Outcome.ERROR, ReasonCode.EXEC_FAILED
    if mutation.budget_exceeded:
        return Outcome.BUDGET_EXCEEDED, ReasonCode.LANE_TIMEOUT
    if mutation.survived:
        return Outcome.FAIL, ReasonCode.MUTANTS_SURVIVED
    return Outcome.PASS, None


def build_mutation_claim(baseline: CommandResult, mutation: Mutation | None) -> Claim:
    """The R2 :class:`~assay.verdict.Claim` from :func:`run_mutation`'s own
    return — the exact mapping ``assay.runner.build_r0_claim`` /
    ``assay.canary.build_canary_claim`` perform, one level over."""
    status, reason_code = judge_mutation(baseline, mutation)
    return Claim(
        rigor="R2",
        source="computed",
        status=status,
        verified_by_assay=True,
        reason_code=reason_code,
        mutation=mutation,
    )
