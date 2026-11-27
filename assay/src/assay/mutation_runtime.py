"""The one data source for what each mutation runtime actually watches (P4/S4).

Operator item (D) of `p4-operator-input-20261125.md`: after P4 replaced the
per-candidate time budget with CPU-based progress bounds on ONE runtime
(Python/pytest), a reader could no longer tell, from the documentation, which
of the following applies to the lane they are about to declare -- a progress
watch, a binding or advisory budget, a candidate store, shards, a per-test
bound. The README's language matrix answered a *different* question (which
rigor levels are registered) and one of its cells had silently become false:
it claimed SQL shared Python's `hung` results, and `hung` is unreachable on a
SQL lane because liveness is injected only for a Python/pytest lane.

This module is the SINGLE source of that matrix. `render_markdown()` produces
the README section readers see, `tests/core/test_mutation_runtime_matrix.py`
fails when the README text differs from it, and the same test proves the
comparison can fail (a mutated row must render differently). One table, one
renderer, one check that can go red -- the alternative, a hand-written README
section beside a separate list in the test, is two sources that drift and a
check that could not fail for the right reason.

**Every cell states what the code does TODAY, and nothing else.** A capability
that would be nicer to have is not a capability; writing one down here is the
precise defect this module exists to prevent. Each row's own comment carries
the anchors a reviewer re-derives it from, and the package report for P4 names
them per cell.

Rows are a `NamedTuple`, not a `@record` dataclass: they are immutable and
ordered like a record, and `tests/core/test_dataclass_contract.py` pins the
dataclass SHAPE of every dataclass under `src/assay` against a committed
fixture -- a data table gaining a column would force a fixture regeneration
for no behavioural reason. It imports nothing from `assay`, too, so the module
is a leaf that any layer (and a consumer's own docs tooling) can import.
"""

from __future__ import annotations

from typing import NamedTuple

#: Markers around the generated README section. Both must occur EXACTLY once
#: in `README.md`; the pinning test asserts that, so a hand-edited block that
#: drops or duplicates one fails rather than silently rendering twice.
MATRIX_BEGIN = "<!-- assay-mutation-runtime-matrix:begin -->"
MATRIX_END = "<!-- assay-mutation-runtime-matrix:end -->"

#: The README heading inside the generated block. It is generated (not
#: hand-written above the markers) so the anchor a consumer document links to
#: cannot outlive the block it names.
MATRIX_HEADING = "#### What each runtime actually watches (generated, P4)"

#: Explicit anchor for the generated block. A consumer document links to THIS
#: name rather than to the heading's slug, so an edit to the heading wording
#: cannot silently break the cross-document link.
MATRIX_ANCHOR_ID = "mutation-runtime-matrix"
MATRIX_ANCHOR = f'<a id="{MATRIX_ANCHOR_ID}"></a>'


class LanguageRuntimeRow(NamedTuple):
    """One language's row: the eight questions a lane author actually asks.

    ``r2_path`` says whether an R2 campaign exists at all and which path runs
    it; every other column is meaningless for a language whose R2 is not
    registered, and says so rather than leaving a blank a reader would have to
    interpret.
    """

    language: str
    display: str
    r2_path: str
    progress_signal: str
    idle_hang: str
    cpu_without_progress: str
    per_test_bound: str
    timeout_classification: str
    resume_store_carry_over: str
    shard: str
    cancel: str
    not_supported: str


#: Column headers, in the row's own field order (minus `language`, the key).
COLUMNS: tuple[str, ...] = (
    "Language",
    "R2 path",
    "Progress signal",
    "Idle hang",
    "CPU-without-progress",
    "Per-test CPU bound",
    "Timeout classification",
    "Resume / store / carry-over",
    "Shard",
    "Cancel",
    "Not supported",
)

_NOT_PROVIDED = "not provided"


#: The ordered matrix. `python` first (the only runtime with a progress watch),
#: then the two other R2-reachable languages, then the two without native R2 --
#: an order that groups the rows by what actually exists, not alphabetically.
RUNTIME_MATRIX: tuple[LanguageRuntimeRow, ...] = (
    LanguageRuntimeRow(
        language="python",
        display="Python (pytest)",
        # cli.py:_built_in_registry -- python is {"R1","R2","R3"}.
        r2_path="native, registered at R1/R2/R3",
        # liveness.py:_PLUGIN_SOURCE (the pytest plugin) +
        # liveness.py:COMPLETED_EVENT_KINDS; injection requires the lane argv
        # to invoke pytest (liveness.py:argv_invokes_pytest) and the adapter to
        # be python (runner.py:5081). stdout/stderr file growth is the
        # plugin-inactive fallback the same monitor reads.
        progress_signal=(
            "pytest plugin events (`session_start`, `test_start`, per-phase "
            "`test`/`phase`, `test_finish`, `session_finish`) plus "
            "stdout/stderr file growth, "
            "only when the lane argv invokes pytest"
        ),
        # liveness.py:IDLE_HANG_POLICY -- a complete resource trace plus the
        # pressure-adjusted idle window; decisions idle-hang and
        # session-finish-hang.
        idle_hang="provided: pressure-adjusted idle window over a complete resource trace",
        # liveness.py:cpu_without_progress_decision + derived_bound_from_baseline.
        cpu_without_progress=(
            "provided: process-tree CPU seconds since the last COMPLETED plugin "
            "event, bound max(3x baseline wall seconds, baseline wall seconds "
            "+ 60s); a parallel suite whose baseline tree CPU exceeds wall "
            "can stop a healthy candidate as `hung`"
        ),
        # liveness.py:per_test_cpu_bound_decision +
        # per_test_cpu_bound_from_baseline + per_test_bound_enabled.
        per_test_bound=(
            "provided: process-tree CPU max(10x that nodeid's baseline "
            "setup+call+teardown seconds, 60s); background workers, tests "
            "that fan out far beyond 10x, or random-order fixture-cost shifts "
            "can stop a healthy candidate as `hung`; skipped under xdist or "
            "with more than one test in flight"
        ),
        # termination.py:52 (TIMED_OUT -> budget_exceeded), :53 (HUNG -> hung);
        # liveness.py:advisory_budget_s + runner.py:6070 (watch_armed).
        timeout_classification=(
            "wall-budget expiry is `budget_exceeded`; any progress bound is "
            "`hung`, never `killed`. Armed watch: the wall budget is advisory "
            "(one WARN, no stop). Unarmed watch: it binds as declared"
        ),
        # mutation_store.py:590/599 (+ the --resume/--rejudge/--carry-over CLI
        # flags, cli.py:_resolve_flex_options and the rejudge parser in
        # runner.py:8516-8580).
        resume_store_carry_over=(
            "provided: candidate state records, `--resume`, "
            "`--rejudge`/`--rejudge-outcome`, `--carry-over`/`--carry-forward` "
            "and shard stores"
        ),
        # mutation_store.py:2698 select_mutation_shard; verdict.py:3630-3634.
        shard=(
            "provided: `--shard INDEX/COUNT` partitions the candidate set and "
            "`judgment.r2.shard_index/count` records it"
        ),
        # cli.py:_termination_signal_handler; mutation.py:4781;
        # liveness.py:terminate_live_process_groups.
        cancel=(
            "provided: SIGTERM/SIGINT/SIGHUP stop dispatching, kill the live "
            "candidate process group and window hosts, and write an incomplete "
            "verdict"
        ),
        # runner.py:5079/5081 (liveness_reason defaults to language-not-python,
        # and injection additionally needs a pytest argv).
        not_supported=(
            "a Python lane whose argv does not invoke pytest keeps a binding "
            "per-candidate budget and has no hang detection at all"
        ),
    ),
    LanguageRuntimeRow(
        language="javascript",
        display="JavaScript/TypeScript",
        # cli.py:1892 registers {"R1","R2","R3"}; R2 is reachable only through
        # judge.mutation.format (B046), and adapters/javascript.py:604 returns
        # UNSUPPORTED, so no native campaign exists.
        r2_path='ingested only (`format = "mutation-report-json"`)',
        progress_signal=(
            "none from assay: the lane's own command runs the external producer "
            "and assay only reads its finished report"
        ),
        idle_hang=_NOT_PROVIDED,
        cpu_without_progress=_NOT_PROVIDED,
        per_test_bound=(
            "not provided by assay; the external producer's own per-mutant "
            "timeout (`timeoutMS`, or `timeoutFactor` applied to the measured "
            "baseline) is the only per-test bound, and assay never sets it"
        ),
        # mutation_parsers/mutation_report_json.py:91 ("Timeout" -> budget_exceeded).
        timeout_classification=(
            "the report's `Timeout` status is `budget_exceeded`, never `killed`; "
            "assay applies no bound of its own"
        ),
        # config.py:4014-4034 (orchestration keys forbidden on an ingested lane);
        # cli.py:2393-2398 (carry family refused without a native R2 lane); the
        # ingested branch in runner.py never builds a candidate state root.
        resume_store_carry_over=(
            "not provided: assay keeps no candidate state for an ingested "
            "report. The `--carry-over` family is refused by name; `--resume` "
            "and `--rejudge` are accepted, take the usual state-directory lock, "
            "and read or write no candidate record"
        ),
        # runner.py:5939-6000 (ingested judgment takes no shard arguments).
        shard=(
            "accepted but partitions nothing: there is no assay candidate "
            "inventory, and no shard index reaches the verdict"
        ),
        # runner.py:default_process_runner registers the live group, so the
        # signal handler (cli.py:801) kills the tool; no candidate is ours.
        cancel=(
            "lane level only: a signal kills the lane command's process group "
            "and writes an incomplete verdict; there is no assay candidate to stop"
        ),
        not_supported=(
            "assay-native mutation, candidate watch, resume/rejudge, "
            "per-candidate cgroup evidence"
        ),
    ),
    LanguageRuntimeRow(
        language="sql",
        display="SQL/DDL",
        # cli.py:1887 registers {"R2"} only, native; a sql lane cannot ingest
        # (config.py:4077-4086) because equivalence_artifact is required there.
        r2_path="native, registered at R2 only",
        # runner.py:5081 -- injection requires the python adapter, so a sql
        # lane has no plugin events, hence no candidate progress at all.
        progress_signal=(
            "none: liveness is injected only for a Python/pytest lane, so no "
            "plugin events and no candidate progress"
        ),
        idle_hang=_NOT_PROVIDED,
        cpu_without_progress=_NOT_PROVIDED,
        per_test_bound=(
            "not provided, and no external tool setting exists to borrow: the "
            "SQL lane's own command applies each mutant, bounded only by the "
            "per-candidate budget"
        ),
        # termination.py:52; and `hung` is unreachable because no bound exists.
        timeout_classification=(
            "wall-budget expiry is `budget_exceeded`. `hung` is unreachable: no "
            "bound can produce it, so the per-candidate budget always binds"
        ),
        resume_store_carry_over=(
            "provided: the same native candidate state, `--resume`, `--rejudge` "
            "and carry-over flags as Python"
        ),
        shard="provided: the same `--shard` partition and verdict fields as Python",
        cancel=(
            "provided: the same dispatch-loop stop, process-group kill and "
            "incomplete verdict as Python"
        ),
        not_supported=(
            "any progress or hang watch, so a CPU-spinning DDL candidate is "
            "ended only by its wall budget; also no R1 and no R3"
        ),
    ),
    LanguageRuntimeRow(
        language="rust",
        display="Rust",
        # cli.py:1900 registers {"R1","R2"}; R2 only by ingestion (B245), and
        # adapters/rust.py:299-308 (generate_mutation_sites) returns UNSUPPORTED.
        r2_path='ingested only (`format = "cargo-mutants-outcomes"`)',
        progress_signal=(
            "none from assay: cargo-mutants runs the campaign and assay reads "
            "its report"
        ),
        idle_hang=_NOT_PROVIDED,
        cpu_without_progress=_NOT_PROVIDED,
        per_test_bound=(
            "not provided by assay; cargo-mutants' own `--timeout` or "
            "`--timeout-multiplier` with `--minimum-test-timeout` is the "
            "per-mutant bound (the lane argv sets it), and nextest's "
            "`slow-timeout` only warns. Assay sets neither"
        ),
        # mutation_parsers/cargo_mutants_outcomes.py:78 ("Timeout" ->
        # budget_exceeded).
        timeout_classification=(
            "cargo-mutants' `Timeout` outcome is `budget_exceeded`, never `killed`"
        ),
        resume_store_carry_over=(
            "not provided: cargo-mutants owns its candidates and retries and "
            "assay keeps no candidate state. The `--carry-over` family is "
            "refused by name; `--resume` and `--rejudge` are accepted, take the "
            "usual state-directory lock, and read or write no candidate record"
        ),
        shard="accepted but partitions nothing: there is no assay candidate inventory",
        cancel=(
            "lane level only: a signal kills cargo-mutants' process group and "
            "writes an incomplete verdict; there is no assay candidate to stop"
        ),
        not_supported=(
            "assay-native mutation, candidate watch, resume/rejudge, R3"
        ),
    ),
    LanguageRuntimeRow(
        language="go",
        display="Go",
        # cli.py:1897 registers {"R1"} only; adapters/go.py:717-741
        # (generate_mutation_sites) is UNSUPPORTED (A-042/A-183).
        r2_path=(
            "not registered: R1 only, and `generate_mutation_sites` is "
            "`UNSUPPORTED`"
        ),
        progress_signal="not applicable: assay runs no Go mutation campaign",
        idle_hang=_NOT_PROVIDED,
        cpu_without_progress=_NOT_PROVIDED,
        per_test_bound=(
            "not provided: `go test -timeout` would be the per-test timeout of a "
            "consumer's own Go mutation carrier, and assay does not set it because "
            "assay has no Go R2 to apply it to"
        ),
        timeout_classification="not applicable: there is no R2 result to classify",
        resume_store_carry_over="not applicable: there is no assay R2 campaign",
        # cli.py never gates `--shard` on a native R2 lane (only the carry /
        # jobs / candidates-file / reuse flags are gated there), so an R1-only
        # lane accepts and validates it and then partitions nothing.
        shard=(
            "accepted but partitions nothing: the spec is validated, but this "
            "lane has no R2 candidate inventory and no shard reaches the verdict"
        ),
        cancel=(
            "not applicable to R2; an R0/R1 Go lane's command is stopped by the "
            "same signal handler that stops every lane's command"
        ),
        not_supported=(
            "R2 in every form (native and ingested), R3, and every campaign "
            "feature in this table"
        ),
    ),
)

#: The languages whose R2 some path reaches today. Derived facts, pinned
#: against `assay.cli._built_in_registry` by the matrix test, so a new
#: registration cannot escape this table silently.
R2_REACHABLE_LANGUAGES: tuple[str, ...] = ("python", "javascript", "sql", "rust")

#: The note printed under the table. It says the two things a reader gets wrong
#: most often: `hung` is a Python-only bucket, and an ingested Rust campaign
#: lends none of assay's campaign machinery to its own report.
MATRIX_NOTE = (
    "Only a Python/pytest lane has a progress watch, so `hung` is the only bucket "
    "a candidate watch can produce and it is reachable on exactly one row. For an "
    "ingested Rust R2 lane, assay's candidate watch, candidate store, resume and "
    "shards do not apply: cargo-mutants owns the candidates and their retries, and "
    "`--timeout-multiplier`/`--minimum-test-timeout` (with nextest) supply every "
    "hang bound in that campaign."
)


def _escape(cell: str) -> str:
    """Table cells are single-line and may not contain an unescaped pipe."""
    return cell.replace("|", "\\|").replace("\n", " ")


def render_markdown(rows: tuple[LanguageRuntimeRow, ...] = RUNTIME_MATRIX) -> str:
    """The README section body, from *rows* (identical to `RUNTIME_MATRIX` by default).

    *rows* is a parameter so a test can render an obviously mutated table and
    prove the README comparison is capable of failing (A-124/A-131: a check
    that cannot go red is this project's most expensive recurring defect).
    """
    header = "| " + " | ".join(COLUMNS) + " |"
    rule = "|" + "|".join("---" for _ in COLUMNS) + "|"
    lines = [MATRIX_ANCHOR, "", MATRIX_HEADING, "", header, rule]
    for row in rows:
        cells = [
            row.display,
            row.r2_path,
            row.progress_signal,
            row.idle_hang,
            row.cpu_without_progress,
            row.per_test_bound,
            row.timeout_classification,
            row.resume_store_carry_over,
            row.shard,
            row.cancel,
            row.not_supported,
        ]
        lines.append("| " + " | ".join(_escape(cell) for cell in cells) + " |")
    lines += ["", MATRIX_NOTE]
    return "\n".join(lines)


def readme_block(
    rows: tuple[LanguageRuntimeRow, ...] = RUNTIME_MATRIX,
) -> str:
    """The whole generated README section, markers included.

    No trailing newline: a caller compares this against the exact slice of
    `README.md` from `MATRIX_BEGIN` through `MATRIX_END`, and a trailing
    newline would make the comparison depend on how the file ends.
    """
    return f"{MATRIX_BEGIN}\n\n{render_markdown(rows)}\n\n{MATRIX_END}"


def block_bounds(document: str) -> tuple[int, int] | None:
    """``(start, end)`` of the generated block in *document*, or ``None``.

    ``None`` covers every shape a caller must refuse rather than guess: an
    absent marker, either marker twice, or an end before its begin. Returning
    a span for a duplicated marker would let a stale block be compared against
    the render and pass.
    """
    if document.count(MATRIX_BEGIN) != 1 or document.count(MATRIX_END) != 1:
        return None
    start = document.index(MATRIX_BEGIN)
    end = document.index(MATRIX_END) + len(MATRIX_END)
    if end < start:
        return None
    return start, end
