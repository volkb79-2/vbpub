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
