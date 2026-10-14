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
- The first independent Sol xhigh review found four additional CIU-135 defects:
  a failed create could claim a later foreign network, interrupted legacy
  ready-record repair could become stranded, CLI `--profile` was missing, and
  `ensure` silently ignored explicit overlay requests. All four are folded into
  this package with regression oracles; the review is preserved as
  `ciu-P53-REVIEW-01.md`.
- The second independent Sol xhigh review found six more issues, now folded
  into the implementation and docs: an ambiguous initial reset could be
  repeated over staged deletions; CLI overlays could target a rootless Git
  root instead of a selected nested CIU root; same-name networks needed durable
  ownership proof; the network-retry oracle itself was invalid; `ensure`
  ignored explicit base/worktree-directory constraints; and an empty profile
  was treated as no request. The review is preserved as
  `ciu-P53-REVIEW-02.md`. The retry oracle now writes identity facts through
  its fixture, targets `network-retry`, and asserts the replacement network
  remains unclaimed.
- Corrected the CIU-135 backlog's schema statement and documented profile
  selection and existing-record refusal across the CLI reference, README,
  design guide, and consumer guide.

## Evidence status

At 2026-10-14 UTC, the follow-up implementation and documentation edits are in
progress.
Standard-library TOML parsing passed for `ciu/run-gate.toml`, `ciu/assay.toml`,
and `ciu/nyxloom-trove/nyxloom.toml`; `git diff --check` is clean. The installed
Run-Gate CLI certified the CIU nyxloom gate pointer:
`run-gate validate-pointers ciu/nyxloom-trove/nyxloom.toml --root .` (one
invocation, exit 0). No focused test suite or registered gate has run on the
follow-up edits yet. Review 02 is complete and all six findings are addressed
in the candidate; the final gate and read-only review are pending. See
`ciu-P53-REPORT.md` when complete.

## Final broad review: findings and closure (2026-10-14)

Sol xhigh's final read-only review of commit
`8af65ea127009e9de6141694698a2f8c8db2e081` found eight more CIU defects. The
full findings are retained in `ciu-P53-REVIEW-03.md`. This follow-on folds in:

- selected nested-root routing for every worktree lifecycle verb;
- one resolved base commit for validation, workspace allocation, and reset;
- allocation-lock serialization for lease and lifecycle record updates;
- network ownership verification before managed startup;
- one-time legacy network claims that cannot overwrite a saved object id;
- refusal on indeterminate Docker probes instead of treating them as absence;
- parent-directory fsync after atomic record replacement; and
- schema-v5 descriptions corrected in the public specification.

Two adjacent defects found while closing that review are also folded in. A
worktree-registry or Docker ownership probe failure during allocation now uses
`runtime-verification-failed`; `runtime-collision` is reserved for proven
identity/ownership conflicts. The unique atomic record-writer scratch name is
now ignored by the repo, CIU project, and generated `ciu init` rules. Tests pin
the nested-root verb routing, snapshot race, concurrent lease ordering,
startup/retry ownership checks, recovery status distinction, record durability,
and generated ignore rules.

`git diff --check` and `python -m compileall -q ciu/src/ciu ciu/tests` pass on
the follow-on edits. No suite or gate has run on them; the registered CIU gate
and a final read-only review remain pending.
