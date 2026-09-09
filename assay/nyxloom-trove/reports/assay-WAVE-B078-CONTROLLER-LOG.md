# Wave controller log — B078 checkpoint 1: R0 structured-report tiebreak (2026-09-08)

Wave prompt: `assay/nyxloom-trove/WAVE-PROMPT-2026-09-08-b078-r0-structured-report-checkpoint1.md`.
Design doc: `assay/nyxloom-trove/R0-STRUCTURED-REPORT-DESIGN.md` (already written,
SR-0 through SR-6, this wave builds exactly Checkpoint 1 of the SR-5 sequencing).

## PR-R1 — wave dispatched, parallel-track authorization

2026-09-08. Fresh Opus implementer dispatched on
`feat/assay-b078-r0-structured-report-2026-09-08`, worktree
`.worktrees/assay-b078-r0-structured-report`, branched off `main` @
`9c2c435f` — AFTER B070 (verdict v10→v11) merged, so this wave does not
need to rebase through that schema churn.

**Operator-authorized parallel track**: this wave runs in TRUE PARALLEL
with a sibling wave (`feat/assay-b074-b077-quickwins-2026-09-08`,
controller log `assay-WAVE-B074-B077-CONTROLLER-LOG.md`) on the same
shared 8-core host — an explicit, one-time reversal of the 2026-09-03
single-agent/single-gate standing directive. Both implementers were told
explicitly to identify their own gate container by worktree path in its
launch argv, never by recency, after B070's fix round capped a stranger's
container by mistake under similar contention.

Scope: Checkpoint 1 ONLY (opt-in `result_report` framework +
format-agnostic completeness core + `vitest-json` reader + R0's three-way
branch + fault-injection tests). Checkpoints 2 (`pytest-json-report`) and
3 (`go test -json`) explicitly deferred to future waves.

Next: await LOG/REPORT + green gate, independently verify from the gate's
own log markers, then dispatch a fresh adversarial reviewer (never fork).

## PR-R2 — implementation returned, gate independently re-verified GREEN, reviewer dispatched

2026-09-09. Implementer landed all of Checkpoint 1: opt-in
`[lanes.X.result_report]` declaration, a new `src/assay/result_reports/`
reader package (mirroring `coverage_parsers/`/`mutation_parsers/`'s
shape), the vitest-json reader, R0's three-way branch, docs, plus a fix
round for two self-found bugs (an unguarded `RecursionError` in the new
reader's `json.loads`, a stale mutation-test pin). Gate independently
re-verified GREEN from `b078-gate2.log`'s own markers on `c80d94d4`.

**Important self-reported correction to the design doc itself**: the
design doc (and this wave's own prompt, which repeated it) named
`runner.py:1140-1142` / `execute_command` as the wiring site — the
implementer says those lines are actually `execute_command`'s docstring,
and that a SEPARATE direct R0-only path in `run_lane` never calls
`execute_command` at all — which they say is the EXACT path dstdns's
`ui_unit` lane hits to reproduce RG-45 live. If true, wiring only the
originally-named site would have shipped green tests while fixing
NOTHING for the real-world case this backlog item exists for. This is
the single most important thing for the reviewer to independently
verify — told explicitly not to accept this claim on the implementer's
word.

Also flagged: `Claim.detail` deliberately unused (a real constraint,
`_check_detail` forbids detail on PASS, per the implementer — reviewer to
confirm), and a documented-not-fixed limitation (no parent-directory
creation for the report path, silent A-073 fallback) that may warrant a
non-blocking "should this be a named diagnostic instead" observation.

Next: await ACCEPT (or blockers) before merging.

## PR-R3 — review returned: REJECT, 4 blockers, rulings made, fix dispatched

2026-09-09. Round-1 review committed at `27b66c25`. The implementer's
self-reported correction (design doc's wiring pointer was a docstring;
`execute_plan` not `execute_command` is the real site) was independently
confirmed CORRECT. But the justification for wiring ONLY the direct
R0-only path rested on a false claim (stated 3x as settled fact) that
dstdns's `ui_unit` — the confirmed live RG-45 repro — is R0-only. It
isn't: `rigor = ["R0", "R1"]`. An R1 lane never reaches the wired direct
branch; it goes through `_execute_snapshot_unit`, which was NOT wired.
Reviewer proved live: an R0+R1 probe reproducing the exact RG-45 shape
still comes out FAIL. **Checkpoint 1 as shipped fixes nothing for the
case it exists for**, despite every test being green — reviewer named
this precisely as the failure mode the implementer had itself warned
about one layer up, landing one layer further in.

Three additional blockers, all real: `result_report` declarable-but-inert
on any R1+/R2/R3 lane with no load-time guard (B2); the branch's own
stated "canary halves unchanged by construction" invariant is FALSE —
`canary.py`'s shared engine calls `execute_command`, which now forwards
`result_report` to both canary halves untested (B3); `CONSUMERS.md`
documents a verdict field (`returncode`) that does not exist on the
schema at all (B4).

Reviewer explicitly declined to pick answers on 3 decision asks. Rulings
made:
- **D-1 (blocker 1's seam)**: pass `result_report` explicitly from the
  CALLER (`_run_prepared_lane`'s baseline call site only), not as a
  default `_execute_snapshot_unit` itself forwards — keeps "only callers
  that explicitly opt in" literally auditable. Also ruled: use
  `create_missing_parents=True` for this snapshot-path call (B006(b)'s
  own contract permits it for an assay-owned ephemeral snapshot),
  narrowing the missing-parent-dir limitation to the direct path only.
- **D-2 (blocker 3)**: exclude canary halves explicitly —
  `result_report=None` from `canary.py:248` — keeping the stated
  invariant literally true, matching the design's own explicit scoping
  (a canary probe's outcome is a different kind of judgment than "did
  the wrapped suite pass").
- **D-3 (blocker 4)**: no verdict-schema change in this checkpoint
  (adding `returncode` would be a real schema change, out of scope) —
  fix the documentation instead to describe what's actually retained
  (the output tails; a PASS carrying tails IS the detection signal).

Same implementer resumed via SendMessage (repair round, not a fresh
agent) with all rulings + exact blocker prescriptions + two cheap
non-blocking fixes (OBS 2: add the new reader to the pinned
untrusted-JSON sweep list; OBS 5: reword a now-inaccurate DESIGN-GUIDE
sentence once fixed). Everything the reviewer verified as correct
(SR-6 constraints, Checkpoints 2/3 genuinely unbuilt, A-073 default,
`Claim._check_detail` finding, RecursionError guard, mutation repin, both
design decisions) is explicitly marked do-not-touch. Next: await repair
commit + still-green gate, then resume the SAME reviewer for
fix-verification.
