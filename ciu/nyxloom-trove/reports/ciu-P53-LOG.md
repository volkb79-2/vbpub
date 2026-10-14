# ciu-P53 — LOG

Package: CIU-135 follow-up — make the initial overlay recovery boundary
durable across interrupted repair of a ready worktree. Also correct active
Run-Gate instructions discovered while preparing the registered gate.

Worktree: `.worktrees/ciu-overlay-intent-20261013`, branch
`ciu-overlay-intent-20261013`. Base `main` at `6bafe05c3f7bfa774f67138cbaaf57c92cd9f7fe`;
starting branch tip `cad352895376cd55d2839e67c71c7942ca95e000`.

## Finding

`ensure()` changes an incomplete ready record to `allocating` before repairing
generated root state. If that repair is interrupted, the next `ensure()` sees
an allocating record and treats it like a first allocation. The schema-v3
overlay checkpoint remains present, so recovery can attempt to replay it over
an operator edit or recreate a file the operator deleted. The existing
successful-repair test did not cover that interruption boundary.

While adding the durable marker, a persistence compatibility defect surfaced:
the first draft added a required field to schema-v3 records without changing
their version. That would reject records written by the earlier CIU-135
candidate. The marker now uses schema v4; ready v3 records remain readable,
and incomplete v3 records fail closed because they do not identify which
recovery phase was interrupted.

## Work in progress

- Add a schema-v4 `initial_overlay_pending` bit. It is true while the first
  allocation is incomplete and cleared in the same record write that first
  publishes `ready`.
- Keep the bit clear through a later repair's `allocating` state; retries skip
  the initial overlay replay. Preserve the ready record's allowance for its
  existing runtime network during an interrupted repair.
- Preserve schema-v3 read compatibility and safely migrate ready v3 records
  when root repair is needed; refuse incomplete v3 records as ambiguous.
- Add edit/delete interruption regressions and malformed-record checks.
- Update the CIU adopter documents and normative spec. Replace active
  checkout-relative `run-gate.py` instructions with the installed
  `run-gate <lane>` CLI and use it in the nyxloom gate argv.

## Evidence status

At 2026-10-14 03:38 UTC, implementation and documentation edits are in progress.
Standard-library TOML parsing passed for `ciu/run-gate.toml`, `ciu/assay.toml`,
and `ciu/nyxloom-trove/nyxloom.toml`; `git diff --check` is clean. The installed
Run-Gate CLI certified the CIU nyxloom gate pointer:
`run-gate validate-pointers ciu/nyxloom-trove/nyxloom.toml --root .` (one
invocation, exit 0). No focused test suite or registered gate has run on these
edits yet. Independent review and the final gate are pending; see
`ciu-P53-REPORT.md` when complete.
