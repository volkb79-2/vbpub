# Wave controller log — B074 + B077 quick-wins bundle (2026-09-08)

Wave prompt: `assay/nyxloom-trove/WAVE-PROMPT-2026-09-08-b074-b077-quickwins.md`.

## PR-R1 — wave dispatched, parallel-track authorization

2026-09-08. Fresh Opus implementer dispatched on
`feat/assay-b074-b077-quickwins-2026-09-08`, worktree
`.worktrees/assay-b074-b077-quickwins`, branched off `main` @ `9c2c435f`
— AFTER B070 (verdict v10→v11) merged, so this wave does not need to
rebase through that schema churn.

**Operator-authorized parallel track**: this wave runs in TRUE PARALLEL
with a sibling wave (`feat/assay-b078-r0-structured-report-2026-09-08`,
controller log `assay-WAVE-B078-CONTROLLER-LOG.md`) on the same shared
8-core host — an explicit, one-time reversal of the 2026-09-03
single-agent/single-gate standing directive. Both implementers were told
explicitly to identify their own gate container by worktree path in its
launch argv, never by recency, after B070's fix round capped a stranger's
container by mistake under similar contention.

Scope: **B074** (`judge.allow_test_path_targets` opt-out for the
test-path veto on explicitly-declared whole-target lanes — ruled Shape 1
from the backlog entry, the smaller option that keeps a guard-rail) and
**B077** (named refusal for a `--state-dir`/`--progress` destination
reached through a symlink inside the judged tree, currently a raw git
stderr passthrough). Both independent, no schema surface, no overlap with
each other or with the B078 sibling wave.

Next: await LOG/REPORT + green gate, independently verify from the gate's
own log markers, then dispatch a fresh adversarial reviewer (never fork).

## PR-R2 — implementation returned, gate independently re-verified GREEN, reviewer dispatched

2026-09-09. Implementer landed both items: `991ede05` (B074,
`judge.allow_test_path_targets`), `dc944932` (B077, named symlink
refusal), `427157c1` (a real B070-surface interaction: the gate caught
the new field missing from the locked W7 v11 schema asset — fixed per
the guard's own docstring instructions), LOG+REPORT at `b3a33415`.
Controller independently re-verified the gate from
`b074-b077-gate2.log`'s own markers — `tester-unified: PASS (exit 0)` /
`ASSAY_REGISTERED_GATE_COMPLETE=1` on `427157c1`, matching.

B074's ambiguous edge case (a genuine test file, WITH the flag set) was
resolved: still refuses — the flag overrides only the directory half of
an adapter's test-path convention, never the filename half. Flagged for
the reviewer to independently confirm, not accept.

One judgment call ratified without waiting for review: the wave prompt's
"no verdict-schema change" and B074's own backlog acceptance box ("the
flag appears in the verdict's resolved judgment") were genuinely in
tension — a wording gap in the prompt, not a real conflict. Implementer
added the flag to `judgment.r1` additively, no `VERDICT_SCHEMA_VERSION`
bump, byte-identical verdict for a non-opting lane. Ruled: this is fine,
matches every other additive MINOR feature this project has shipped —
reviewer told not to treat "a field was added" as a blocker by itself,
but to independently verify the additivity claim and the three-place
wiring.

Also disclosed, not this wave's to fix: 8 pre-existing comments elsewhere
in the tree say "B074" meaning a DIFFERENT, older item (the RecursionError
sweep, renumbered to B075 after those comments shipped) — a naming
collision predating this wave. Reviewer told to confirm it's genuinely
pre-existing, not something to fix now.

Same host running B078 checkpoint 1 concurrently (operator-authorized
parallel track) — reviewer told a sibling gate container may appear and
to identify its own by worktree-path argv only.

Next: await ACCEPT (or blockers) before merging. `CHANGES.md` will
conflict at merge (assay 6.0.0 released to main mid-wave, folding
`[Unreleased]`) — controller resolves that at merge time, not a review
blocker.
