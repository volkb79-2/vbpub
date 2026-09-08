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
