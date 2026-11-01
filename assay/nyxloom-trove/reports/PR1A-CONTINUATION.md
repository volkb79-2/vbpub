# PR1a continuation

**Branch:** `assay-pr1a`

**Base:** `346b6fc671168eec09757519233fddc7e78ba48d` (`main`, Assay 10.2.0)

**Scope:** serial native R2 command-window isolation only.

## Implemented

- A candidate command now runs in one disposable Assay process. The worker
  returns the existing command result, process-tree evidence, observation, and
  liveness metrics. Resource-counter sampling, classification, retries,
  resume state, and progress remain in the campaign coordinator.
- The `jobs=1` native mutation path selects this worker when the runner and
  clock have a supported serialization protocol. Injected test seams remain
  in-process unless they explicitly provide a factory specification.
- Worker cancellation reaps the worker; abrupt worker exit kills and verifies
  the candidate process group and becomes `ERROR` / `EXEC_FAILED`, which the
  mutation classifier records as `crashed`.
- `_window_host.py` is included in all three sorted B105 target inventories.
  The liveness exclusion line map is refreshed. B165 records the serial
  isolation implementation; B171 is unchanged. No additional B202 backlog
  item was needed: the implementation review findings were corrected here.
- The changelog records the internal execution change. Verdict schema v16,
  bucket vocabulary, and progress keys are unchanged.

## Evidence

- Focused host and mutation regression selection: **179 passed**. The new
  `_window_host.py` module measured **100% line coverage** (335/335 statements)
  and **100% branch coverage** (114/114 branches).
- Focused B105 exclusion-map and shared-inventory checks: **4 passed**.
- Abrupt worker-exit classification and candidate cleanup probe: **1 passed**.
- `python -m pyflakes src tests gate`: clean using the estate venv.
- The full registered `self-qualification-preflight` was not invoked under the
  task's focused-pytest-only and no-run-gate constraints. The new module's
  focused line and branch coverage is complete; package-wide preflight
  evidence remains for the registered workflow.

## Deferred scope

PR1b owns scheduling, `LimitLedger`, and `--jobs`. WP8/v17, RS1/RS2, and waiver
changes remain untouched. No `run-gate` or Docker command was run.
