# Wave controller log — B068 + quick-wins (2026-09-08)

Wave prompt: `assay/nyxloom-trove/WAVE-PROMPT-2026-09-08-b068-quickwins.md`
(rulings QW-D1..QW-D3 live there: B068's investigate-then-choose ruling,
B063's skip-vs-resolve ruling, B071's crashed-only scope ruling). This log
records rulings made AFTER dispatch, in sequence.

## QW-R1 — wave dispatched

2026-09-08. Fresh implementer dispatched on `fix/assay-b068-quickwins-2026-09-08`
from `main` @ `faaa49c2`, worktree `.worktrees/assay-b068-quickwins`. Scope:
B068, B072, B062, B063, B071, in that order, one commit group per item, no
schema/wire change. Interview + wave-plan context: operator chose "First
item in the next wave" for B068's sequencing (2026-09-07/08 conversation);
B070 (v11, discarded-mutants shape 1) and the B064/65/66/67 progress/resume
family are explicitly OUT of this wave's scope — B070 gets its own MAJOR
release later, the progress family is Wave 2, dispatched separately.

## QW-R2 — all five items returned, gate GREEN, then B074 found and ruled in

2026-09-08. Implementer returned all five items (`96973575`, `9cd5ef28`,
`c2d89888`, `e426c29f`, `b12ec9f2`), then LOG/REPORT (`95177803`).
Controller independently re-verified the gate's own log markers before
trusting the implementer's report (LESSONS L4) — confirmed
`tester-unified: PASS (exit 0)` / `ASSAY_REGISTERED_GATE_COMPLETE=1` at
`b12ec9f2`, matching the report exactly. Two disclosed process slips
(a `docker update --cpus=3` briefly touching a peer agent's containers,
no-op in effect; a blurred commit boundary, B071's backlog note riding
inside the B063 commit) — ruled: no action needed, both harmless as
disclosed.

**B068's own premise refuted by investigation, as instructed.** There is
no R0/R1-vs-R2 code divergence in git resolution — proven end to end in a
real severed linked worktree. Fix (b) (name the gap, keep git's own
`fatal:`) applied since fix (a) was never available, not merely
not-preferred. Ruled: accepted, no further action.

**B072's required sweep found a THIRD instance** of the uncaught-
`RecursionError` gap, on `verify.py`'s `verify_text` (the parser behind
`assay verify` itself, reading untrusted input by definition) — correctly
filed as **B074** rather than fixed out-of-scope, since this wave's
binding constraint said "`assay verify` is unaffected."

**Ruling: fix B074 now, as a sixth commit, before review — not a hotfix,
not a separate wave.** Same one-line pattern as B072/`f0126b35`, zero
design risk, closes a live crash on assay's own verify path; the "verify
unaffected" constraint was a guard against THIS wave's five ORIGINAL
items touching verify, not a prohibition on fixing a bug found by their
own required sweep. Resumed the same implementer via SendMessage (not a
fresh dispatch) to preserve its accumulated context.

Two pre-existing reds on `main` (`environment = "host"` assertions in
`test_cgroup_parent.py`/`test_self_hosting.py`, stale since run-gate's
RG-43 moved this project's lane to `bare-host`) were repaired inside the
B063 commit because they blocked the gate — ruled: fine as landed, no
extraction needed.

## QW-R3 — B074 landed, gate re-run clean from scratch

2026-09-08. `767393d1` (fix) + `7bcf089a` (docs, LOG/REPORT updated with
item 6). Gate re-run FROM SCRATCH at the new tip (implementer did not
carry the `b12ec9f2` green over) — controller independently confirmed
from `gate3.log`'s own markers: `tester-unified: PASS (exit 0)` at
`767393d1`, 12/12 phase markers, zero diagnostics,
`ASSAY_REGISTERED_GATE_COMPLETE=1`. Branch pushed, tip `7bcf089a`.

**Fresh adversarial reviewer dispatched** (never fork) against the full
branch diff, with explicit instructions to independently re-derive every
claim above (including B068's refuted premise and B074's
"`provenance.py:137` is trusted input" reasoning) rather than trust the
LOG/REPORT. Report expected at
`assay/nyxloom-trove/reports/assay-WAVE-QUICKWINS-REVIEW-round1.md`.
