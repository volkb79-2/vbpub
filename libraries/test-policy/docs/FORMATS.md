# Stable formats

Two artefacts of this library are consumed by other tools (a future `run-gate flakes`, TP-05, and the release-gate
flakehunt pass). They are stable: a change that breaks a consumer needs a version bump as stated below.

## flakehunt JSON report (`--report FILE`)

One JSON object. `schema_version` is currently `1`. Additive changes (new optional keys) keep the version; a rename,
removal or type change bumps it, and the old version is documented here until nothing reads it.

| Key | Type | Meaning |
|---|---|---|
| `schema_version` | int | Format version (1). |
| `recipe` | string | `none`, `slow-cpu`, `cpu-contended`, `slow-disk`, `mem-pressure` or `combo`. |
| `mode` | string | `container` or `inner`. |
| `run_id` | string | Part of every exact container name `flakehunt-<run_id>-<n>-{lane,cpu,mem}`. |
| `selection` | list of string | The pytest arguments after `--`. |
| `psi_at_start` | object | `memory_some` and `io_full`, the `avg10` values read before the first start (percent). CPU PSI is not read: it is a stall measure, not a stop signal. |
| `runs` | list of object | One per executed run: `run` (1-based), `outcome` (`pass`, `fail`, `error`), `returncode`, `seconds`. |
| `passes`, `failures` | int | Counts over `runs`. |
| `first_failure` | object or null | `{run, trace}`: the first failing run and the last 60 lines of its output. |
| `verdict` | string | `stable`, `failed`, `inconclusive` or `error`. |
| `exit_code` | int | The process exit status: 0 stable, 1 failed, 2 error, 3 inconclusive. |
| `error_trace` | string | Only when `verdict` is `error`. |

A one-run `failed` is evidence of a failing test, not of flakiness: pytest exit 1 is classified `fail` whatever the
cause, so a deterministic environment problem looks the same. Establish flakiness by the same selection passing
without the stress recipe (`--recipe none`) and failing with it (backlog TP-07).

## Flake ledger (`docs/FLAKE-LEDGER.md`)

A markdown table, one row per flake, exactly five columns: `ID | Test / location | Signature | Root cause | Status`.

* ID: `FL-NN`, two or more digits, unique, allocated by taking the next number after the highest in the table; an ID is
  never reused or renumbered.
* Status: starts with one status word, then a space (or a colon) and detail.
  * `FIXED <ref>`: root cause removed; `<ref>` is a commit hash (9 to 40 hex digits) that is on `main`, or a backlog
    id such as `CP-53`.
  * `FIXED-ON-BRANCH <hash> pending merge: ...`: fix committed on a named branch that is not on `main`. Never write
    FIXED for such a row.
  * `OPEN-HYPOTHESIS`, `CANDIDATE`, `NOT-A-FLAKE`: free detail text.
* Creating a row from a report: take the failing test id and `first_failure.trace` signature, add the recipe that
  exposed it, state the root cause (or `unknown`), and set the status word. Close a row only after 20 green repeats
  under the profile that exposed it (`verdict: stable`, `passes >= 20`, same `recipe`).

`tests/test_ledger.py` parses the table and enforces this grammar.
