# Wave controller log — progress/resume family (2026-09-08)

Wave prompt: `assay/nyxloom-trove/WAVE-PROMPT-2026-09-08-progress-resume.md`
(rulings live there: B067's stall-detection-stays-external ruling, B064's
heartbeat-not-percentage ruling with the 60s/`--progress-heartbeat`
default, B073's explicit out-of-scope boundary). This log records rulings
made AFTER dispatch, in sequence.

## PR-R1 — wave dispatched

2026-09-08. Fresh implementer dispatched on
`feat/assay-progress-resume-2026-09-08` from `main` @ `a4addfc5` (assay
5.1.0, Wave 1 fully shipped), worktree `.worktrees/assay-progress-resume`.
Scope: B067, B064, B065, B066, in that order. No verdict-schema change.
Sequenced as Wave 2 of 3 under the operator's `/goal proceed through
impl/review/test/release all discussed next waves for assay` — the
pipeline (implementer → independently-verified gate → fresh adversarial
reviewer → fix rounds via SendMessage on the same two agents → merge →
release → deploy → notify → tool-deps sweep including `run-gate.toml`'s
own pin → cleanup) is to run the same way Wave 1 did, without pausing for
confirmation at each step. B070 (v11 schema) is explicitly Wave 3, not
this wave's scope; B073 (live-test-progress adapter) is filed and
deliberately deferred, not this wave's scope either.

## PR-R2 — E-008 checkpoint after B067, fresh successor dispatched

2026-09-08. Implementer landed B067 (`7f2ba056`) — real design work: a new
`LaneDeadline.tightened()` mechanism, four `math.inf`-to-"no timeout"
conversion boundaries, a new `judge.canary.budget_per_attempt` key. Filed
**B076** in passing (an unbounded R2 lane's own baseline run is still the
one unbounded command -- deliberate, three options, none chosen, correctly
left unfixed as out of this wave's scope). Crossed the checkpoint
threshold there, cut cleanly per the wave prompt's own clause: commit,
clean tree, gate independently re-verified GREEN by the controller from
the gate's own log markers before trusting the report. Continuation
brief committed at `8bc6b9ad`, very thorough (located seams for all three
remaining items, a sharpened DIRTY_TREE lesson from a genuine mid-run
mistake it made and disclosed, host-load discipline notes from a real
load-17 saturation event). **Fresh successor dispatched** (never a
resume -- per E-008, a checkpoint hand-off gets a fresh agent) seeded with
the brief, scoped to B064, then B065, then B066.

## PR-R3 — all four items returned, gate GREEN first try, review dispatched

2026-09-08. Successor implementer landed B064+B065 together (`940b5ba2`,
same lines touched) and B066 (`243de634`), then LOG+REPORT (`48561aba`).
Registered gate GREEN on the FIRST attempt this time (prior wave's
DIRTY_TREE lesson correctly applied — LOG/REPORT drafted outside the tree,
moved in only after the verdict). Controller independently re-verified
from the gate's own log markers before trusting the report — confirmed
`tester-unified: PASS (exit 0)` / `ASSAY_REGISTERED_GATE_COMPLETE=1` at
`243de634`, matching exactly.

Two things disclosed honestly by the implementer, noted for the reviewer
rather than acted on unilaterally: (1) B064's R3/canary half is
deliberately left unimplemented (needs to reuse B007's per-attempt
identity), acceptance box correctly left unticked, not claimed done;
(2) a process deviation — a Python `write_text` script was used for two
mechanical multi-site edits before the implementer caught itself and
switched back to Edit-tool-shaped edits for the rest. Both flagged to the
reviewer explicitly rather than pre-judged.

**Fresh adversarial reviewer dispatched** (never fork) against the full
branch diff, told to independently re-derive every claim (the four
`math.inf` boundaries, the closed-vocabulary enforcement, B066's
git-ignore-check oddity, the two-worktree resume behavior) rather than
trust the LOG/REPORT/BRIEF. Report expected at
`assay/nyxloom-trove/reports/assay-WAVE-PROGRESS-RESUME-REVIEW-round1.md`.
