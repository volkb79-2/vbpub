# P04 — successor brief

You have `src/assay/runner.py` (new) and `src/assay/cli.py`'s `run`
subcommand. Nothing is re-exported from `src/assay/__init__.py` (out of P04's
`scope.touch`) — import directly: `from assay import runner` or
`from assay.runner import execute_command, build_r0_claim, assemble_verdict,
CommandPlan, CommandResult, write_verdict, default_process_runner`.

## The exact seam you call (A-090/A-094)

```python
result = runner.execute_command(lane, argv_append=appended, cwd=project_root)
r0_claim = runner.build_r0_claim(result)

# <-- YOUR R1 EVALUATION GOES HERE. Wire, in order, ahead of the four-way
#     coverage union (measurability.check_dirty_tree,
#     measurability.check_base_is_head, coverage.read_coverage_artifact,
#     coverage.check_empty_coverage — see the P02/P03 briefs). Build an
#     R1 Claim the same way build_r0_claim does: rigor="R1", source="computed",
#     verified_by_assay=True, status=<the four-way outcome>, reason_code=...,
#     coverage=Coverage(...) on a non-NO_MEASUREMENT status.

verdict = runner.assemble_verdict(
    lane=lane, commit=commit, result=result,
    claims=(r0_claim, r1_claim),          # <-- pass BOTH, in declared_rigor order
    assay_version=assay_version,
)
```

`assemble_verdict` takes the WHOLE `claims` tuple — not just R0's — and
derives `outcome`/`reason_code` via `verdict.rollup()` over every claim's
status. You do not restructure `execute_command` or `assemble_verdict`; you
insert code between the two calls and widen the `claims` tuple you pass to
the second one.

## What P04's own build does NOT yet do — the gap you're filling

**Controller repair, post-implementation review:** the original diff put this
gate directly in `cli.py`'s `_cmd_run` — but `cli.py` is not in P05's
`scope.touch`, so that would have forced you to BLOCK on your first day.
Relocated into `assemble_verdict` itself instead: it now refuses
(`ERROR`/`BAD_LANE_CONFIG`) BEFORE constructing a `Verdict` whenever the
`claims` tuple you pass does not cover every level in `lane.rigor` — checking
`{claim.rigor for claim in claims}` against `lane.rigor`. **You do not delete
or edit anything to satisfy this.** Once you pass `claims=(r0_claim,
r1_claim)` for a `rigor = ["R0", "R1"]` lane, the coverage check is satisfied
and the guard simply stops firing for that lane — no gate to find or touch,
in `cli.py` or anywhere else. `_cmd_run` itself needs zero changes from you;
you only ever touch the space between the two `runner.` calls it already
makes.

## Other shapes

```python
# ProcessRunner: Protocol(argv, *, env, cwd, timeout) -> subprocess.CompletedProcess[str]
# Clock: Callable[[], datetime]
# CommandPlan: argv_declared/appended/effective, env_declared/effective (all frozen kw_only)
# CommandResult: plan, outcome, reason_code, returncode, started, ended (ISO-8601 strings)

def write_verdict(verdict, target: str, *, stdout, replace=os.replace) -> None:
    """target == "-" -> stdout; else atomic file write. Injectable `replace`."""
```

## Traps

* **`execute_command` never raises for an ordinary R0 outcome** — append
  rejected, missing executable, budget expired, command failed, command
  passed are all a returned `CommandResult`, never an exception. Only
  `git.head_rev` failure and `assemble_verdict`'s own rigor-coverage guard
  (previous section) raise `AssayError` in `_cmd_run`'s call chain.
* **`result.started`/`result.ended` are already ISO-8601 strings** (via
  `verdict.iso_utc`) — pass them straight into `assemble_verdict` (it does,
  internally); do not re-derive timestamps for your R1 claim from a second
  clock call unless you actually want R1's own window reported separately
  (no oracle asks for this; P04's `Verdict` only has one `started`/`ended`
  pair for the whole run).
* **`env_declared`/`env_effective` on `CommandPlan` are already resolved**
  (declared env plus only the passthrough names actually present in the
  source) — nothing about coverage/diff evaluation needs to touch these.
* **The four new R0-only fixtures** (`tests/fixtures/verdicts/r0_*.json`)
  and the reused `budget_exceeded.json` are all `declared_rigor: ["R0"]`,
  single-claim. Your own O4-equivalent fixtures for an R0+R1 lane are new
  files you own — don't try to extend these in place.
