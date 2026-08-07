# P12 successor brief — for P13 (standalone wheel) and P14 (self-hosted conformance)

P12 shipped mutation EXECUTION: a baseline-gated, isolated, `jobs`-bounded
runner producing the R2 claim. Here is what you need.

## The call (`src/assay/mutation.py`)

`run_mutation(lane, *, project_root: Path, scratch_root: Path, targets:
Iterable[MutationTarget], adapter: LanguageAdapter, jobs: int,
process_runner=None, clock=None, executor_factory=_default_executor_factory)
-> tuple[CommandResult, Mutation | None]`. Runs the baseline via the real
`execute_command` against `project_root` unmodified; if it doesn't PASS,
returns `(baseline, None)` — no mutant is ever generated or run. Otherwise
collects every candidate mutant across `targets` (`collect_mutants`), fans
the job list out over `executor_factory(jobs)` (a real `ThreadPoolExecutor`
by default), isolates each mutant into a fresh `shutil.copytree` scratch
directory under `scratch_root`, and aggregates deterministically into a
`Mutation`. `judge_mutation(baseline, mutation) -> (Outcome, ReasonCode |
None)` and `build_mutation_claim(baseline, mutation) -> Claim` (rigor="R2")
are the pure mapping and Claim-wiring, mirroring `canary.py`'s own
`judge_canary`/`build_canary_claim` shape exactly.

**Not wired**: nothing in P12 reads `assay.toml`'s `judge.mutation` table
(`jobs`/`operators` stay unconsumed — A-121, deliberate). `MutationTarget`
construction (turning a real diff + changed files into `(path, text,
lines)` triples) is NOT this package's job either — P12 owns execution
only, per its own "Scope / forbid" section. **Both of these are P14's job**:
wiring `assay.toml`'s R2 config into a real `jobs` value and a real
`MutationTarget` list built from `assay.diff.AddedLines` + the source tree,
the same way P05 already wires R1's coverage config through.

## The R2 payload (`src/assay/verdict.py`)

`Mutation(total, killed, survived=(), crashed=(), budget_exceeded=())` —
four buckets. `killed` is a bare count; the other three carry
`MutantOutcome(path, lineno, operator, description)` tuples, sorted by that
same 4-tuple key (construction-time enforced). `Claim.mutation: Mutation |
None`, gated to `rigor == "R2"`. **Presence is baseline-conditional**:
`None` means the baseline never passed (mutation testing never started);
present means it did, even if `crashed` alone is non-empty. Two claims can
legitimately both render `(ERROR, EXEC_FAILED)` this way — check
`reason_code` for the CLASS, `mutation.crashed` (present vs. absent) for
the MECHANISM.

`assemble_verdict` (runner.py) gained `mutation_claim: Claim | None = None`
— pass the whole `Claim` from `build_mutation_claim`, it gets folded into
`claims` before the existing rigor-coverage guard runs. Do not also put it
in `claims` yourself — that raises (duplicate rigor).

## A real circular-import trap for anyone touching this file again

`adapters/base.py` imports `mutation.py` for `Mutant` (P11, unconditional,
module-level). `runner.py` imports `adapters/base.py` for `LanguageAdapter`
(unconditional, module-level). `mutation.py`'s own P12 code needs
`execute_command`/`default_process_runner` from `runner.py` at RUNTIME. A
module-level import the third direction closes the loop and breaks under
common import orders (verified empirically — see the LOG). `run_mutation`
resolves them with a function-body-local `from .runner import ...` instead.
If you add MORE runtime calls into `runner.py` from `mutation.py`, keep
using the deferred-import pattern; do not hoist it to module level. Type
hints for `LanguageAdapter`/`ProcessRunner`/`Clock`/`CommandResult` are bare
names, unimported, relying on `from __future__ import annotations` — this
works but means an IDE/mypy run would need `TYPE_CHECKING` imports added if
this project ever adopts static type checking (it does not currently).

## Fixtures

`tests/fixtures/mutation_exec/python/` (new, P12's own — NOT
`tests/fixtures/mutation/python/`, which is P11's construction-only
fixture and does not stage for controllable kills/survivals): a real
pytest project, `is_adult` (well-tested, produces a genuine KILLED mutant)
and `is_valid_status` (hollow-tested, produces a genuine SURVIVED mutant).
Both proven via a REAL `pytest` subprocess in
`tests/test_mutation_python_pipeline.py` — no fake/mocked survivor
anywhere in the suite, per this project's own A-041/A-067 discipline.

## For P13 (standalone wheel)

Nothing in P12 adds a runtime dependency (still stdlib-only). Check that
`mutation.py`'s new imports (`concurrent.futures`, `shutil`, `datetime`,
`pathlib`, `typing`) are all stdlib — they are. The scratch-venv proof
should need no changes on P12's account.

## For P14 (self-hosted conformance / full CLI wiring)

Two concrete wiring gaps P12 deliberately left (see "Not wired" above):
build real `MutationTarget`s from a real diff, and read `jobs`/`operators`
from `assay.toml`'s `judge.mutation` table (currently fully opaque,
`JudgeConfig.mutation: Mapping[str, Any] | None`). Also worth auditing: R2
is not yet reachable from `cli.py`'s `run` command at all — `assemble_verdict`
accepts `mutation_claim` but nothing in `cli.py` calls `run_mutation` or
passes one through yet.
