# assay Wave D phase 1 — adversarial review R-1, round 2 (fix verification)

**Reviewer:** R-1, resumed. **Round 2 of a 3-round cap.**
**Subject:** `93188912..e3ae8ada` only. The branch tip `fb8d03f5` is
records-only (BRIEF-5 / LOG / REPORT) — verified: `git diff --name-only
e3ae8ada..fb8d03f5` touches nothing outside
`assay/nyxloom-trove/reports/`.
**Commits judged:** `e44c1056` (my round-1 report, verbatim), `8895ffbf`
(A-418..A-424), `e3ae8ada` (B063 filed), and — inside the range but from
generation 4 — `7c9e8dd1`/`efbab2bb` (B024/A-417 lint wiring + the DA-D7
ciu-asset re-capture).
**Method:** blind pass on the diff first, then each round-1 prescription
checked against what landed, then my own probes re-run. Every claim below
carries a transcript from **this** round; nothing is carried forward on
trust.
**Host rules honoured:** one `pytest` at a time, no xdist, everything
`nice -n 19 ionice -c 3`, targeted test files only. I did **not** run the
registered gate — `docker ps --format '{{.Image}}' | grep tester-unified`
was checked (empty) before any work, and generation 6 is on the same branch.
Gate verdict below is read from `gate-gen5.log`'s own markers, in a separate
step. `/workspaces/vbpub/.worktrees/assay-wave-d-v10` was never touched; I
worked in `scratchpad/r2wt` (detached at `e3ae8ada`).

---

## VERDICT: **ACCEPT-conditional**

**Both round-1 blockers are fixed, and fixed properly — not papered over.**
All five should-fixes landed. No regressions in anything I verified in round
1. The gate is green on the subject commit. The one condition is a six-line
residue of my own SF-2 that the fix package missed, sitting four lines below
the branch it did fix, and generation 6 is already inside that function for
A-425.

**The condition (one item, SF-6 below):** land the `except RuntimeError`
announcement at `runner.py:4066-4076` alongside A-425 — or rule explicitly
that the residue is accepted and narrow `CHANGES.md:32`. Until one of those
happens, `CHANGES.md:32`'s "with no exceptions" has exactly one narrow
exception left. **Merging on this word is fine provided that rides along; I
do not need a round 3 to re-verify a six-line mirror of an adjacent branch,
though I will if asked.**

---

## BLOCKER 1 — **RESOLVED.** Measured, all four post-command terminals.

DA-R8 ruled "FIX, in scope — no narrowing", and the prescription was followed
literally: `post_dirty` / `post_observed_head` carried on
`SnapshotUnitResult` beside `post_reason` (`runner.py:2157-2166`, populated
`:2414-2429`, consumed `:2894-2907`); `_finish_direct_r0_lane` given a
`diagnostics` parameter (`runner.py:4696`, passed by its single caller at
`:4641`); one shared composer `runner.post_command_refusal`
(`runner.py:352-405`) so the two paths cannot drift.

**Round 1 measured 0 emitter lines at every one of these. Round 2, same
probes (`scratchpad/probe53b`), on `e3ae8ada`:**

| terminal | dispatch path | round 1 | round 2 |
|---|---|---|---|
| post-command `DIRTY_TREE` | direct R0 | 0 lines | **1 line** |
| post-command `DIRTY_TREE` | snapshot R1 | 0 lines | **1 line** |
| post-command `HEAD_CHANGED` | direct R0 | 0 lines | **1 line** |
| post-command `HEAD_CHANGED` | snapshot R1 | (not reached in r1) | **1 line** |

Verdicts unchanged: `[R0 NO_MEASUREMENT/DIRTY_TREE]`,
`[R0 PASS, R1 NO_MEASUREMENT/DIRTY_TREE]`,
`[R0 NO_MEASUREMENT/HEAD_CHANGED]`,
`[R0 PASS, R1 NO_MEASUREMENT/HEAD_CHANGED]` — the document did not move, only
the sentence appeared.

**The sentence is the right sentence**, which was the substance of the
prescription and not a detail. Verbatim from the direct-R0 run:

```
assay: NO_MEASUREMENT/DIRTY_TREE: the lane's own command left 1 uncommitted
file(s) in <repo> -- assay observed that tree CLEAN at 382dd84c… immediately
before starting the command, so this dirt is the command's own output and
re-running after a commit or a stash reproduces it. Point the command's
output at the artifact path the lane declares (assay reserves it for exactly
this), or at a gitignored path, so what assay measured stays the commit it
judges. Affected: leftover.txt
```

It blames the command, names the file, states the pre-condition it observed,
and — critically — **does not offer "commit or stash"**, the pre-run remedy
that cannot work here. The `HEAD_CHANGED` half names both revisions:

```
assay: NO_MEASUREMENT/HEAD_CHANGED: the lane's own command moved HEAD in
<repo> from 9f551a06… to 57f0671e… -- assay judges the commit it resolved
BEFORE the command ran, never one the command created while it ran… Remove
the commit step from the lane's command.
```

The snapshot path correctly reports the **snapshot's** root and commit
(`/tmp/assay-p23-…/assay-p22-snap-…`), not the user's repo — which is where
the dirt actually is.

**Tests:** three CLI-level tests in the existing shape
(`test_refusal_announcement.py:718-869`), one per dispatch path plus the
`HEAD_CHANGED` branch, each asserting `len(lines) == 1`, the named path or
both revisions, **and** the two phrases that encode the blame
(`"the lane's own command"`, `"commit or a stash reproduces it"`). Suite:
`20 passed`.

**A-178's observation order is preserved** — one `dirty_paths` call,
`head_rev` only on the clean branch, dirt keeping precedence. The change is
that both facts are kept rather than evaluated for truthiness and dropped.

## BLOCKER 2 — **RESOLVED.** The guard now guards, and discriminates.

A-419 replaced the signature/docstring assertions with value assertions at
both ends of the threading
(`test_r3_canary_sees_infrastructure.py:141-240`): one calling
`runner.execute_command` directly and asserting
`result.plan.env_effective[FACT_NAME] == FACT_VALUE` **and** that the child
process — which `assert`s the variable out of `os.environ` and exits nonzero
without it — passed; one driving `canary.run_python_canary` and asserting
`control_outcome is PASS` / `transformed_outcome is FAIL` /
`observed_reason_code is COMMAND_FAILED`.

**I re-ran my round-1 mutants against the new tests.** Fresh copies of
`e3ae8ada`, each forward deleted with `Edit`, targeted file, serial:

```
baseline (unmutated e3ae8ada)                      3 passed
n1  runner.execute_command's forwards deleted      2 failed, 1 passed
      FAILED …::test_execute_command_resolves_a_derived_fact_into_the_environment_that_runs
      FAILED …::test_the_legacy_standalone_canary_runs_both_halves_in_the_lanes_own_world
n2  canary._run_pipeline's forwards deleted        1 failed, 2 passed
      FAILED …::test_the_legacy_standalone_canary_runs_both_halves_in_the_lanes_own_world
```

Both mutants that were **GREEN in round 1 are RED in round 2**, and the
discrimination is exact: `n1` kills both tests because `execute_command` is
upstream of the canary; `n2` kills only the canary test, because that is the
only half it breaks. That is a better guard than I prescribed — I asked for a
value assertion, and got one that also localises which forward broke. The
failure surfaces at `runner.py:681` as `AssayError`, i.e. the resolver
refusing a `derived:` fact with no source, which is B029's original
misattribution reproduced on demand.

---

## SHOULD-FIXES FROM ROUND 1 — all five landed

| id | what I asked for | landed | verified this round |
|---|---|---|---|
| SF-1 | B028 residue: a `LANE_TIMEOUT` upstream of `run_lane` writes no verdict | **A-420**, DA-R9 option (a) — two `LANE_TIMEOUT`-scoped handlers in `cli._run_reserved` | measured, below |
| SF-2 | silent `GIT_FAILED` from `except OSError` | **A-421** — announces `snapshot preparation or cleanup failed: {exc}` (`runner.py:4045-4059`) | read; **residue → SF-6** |
| SF-3 | `_report_probe_refusal` is a second spelling of the emitter | **A-422** — folded onto `announce_refusal` (`runner.py:504-515`), tails kept as indented context | measured, below |
| SF-4 | leaked fd on B049's new raise path | **A-423** — `try`/`finally` around both reads (`mutation.py:1640-1662`) | measured, below |
| SF-5 | the `statement_attribution` carry is unreachable | **A-424** — kept as insurance, documented as *not covered code*, citing my `m5` measurement by name | read; correct disposition |

### SF-1 / A-420 — verified, both paths, real commit label

`scratchpad/probe28`, `budget = "0.001s"`, through the installed CLI:

```
r0slow  exit=4  VERDICT WRITTEN: BUDGET_EXCEEDED [R0 BUDGET_EXCEEDED/LANE_TIMEOUT]
                commit == real HEAD: True (3689816189b2)
r1slow  exit=4  VERDICT WRITTEN: BUDGET_EXCEEDED [R0 …/LANE_TIMEOUT, R1 …/LANE_TIMEOUT]
                commit == real HEAD: True (3689816189b2)
```

Round 1 measured `*** NO VERDICT FILE ***` for both. The commit label is the
**real** `HEAD`, not fabricated and not a sentinel — I compared it against
`git rev-parse HEAD` in the same step, which is the specific thing the
controller asked me to confirm. Both documents are accepted by `assay verify`
under the branch build **and** under the shipped 4.1.0 verifier (`exit 0`
each). The original command-overrun case (`budget = "1s"`, `sleep 3`) still
writes its verdict on both paths — no regression.

**Scoping is correct:** all three handlers are guarded
`if exc.reason_code is not ReasonCode.LANE_TIMEOUT: raise`
(`cli.py:687`, `:748`, `:865`), so nothing but a timeout can be laundered
into a verdict.

**DA-R13's known issue — confirmed present, ruled, pending A-425.**
`cli.py:711` re-reads `commit = git.head_rev(lane_file.project_root)` with no
`remaining=` bound, inside the pre-`run_lane` handler. On a stalled mount this
would hang the refusal itself, which is the one thing `budget` exists to
prevent. **This is DA-R13, already ruled** (a documented
`LABEL_GRACE_SECONDS = 2.0` through the existing `remaining=` shape,
generation 6 lands it as A-425 before phase 2). **Recorded, not a finding of
mine.** I note only that the `except AssayError: raise exc from None` fallback
around it is the right shape — a failed label read re-raises the *original*
timeout rather than renaming the fault — so A-425 has a correct seam to plug
the grace into.

### SF-3 / A-422 — verified

A lane whose `environment_command` exits 9:

```
assay: ERROR/BAD_LANE_CONFIG: lane 'envprobe': its declared environment does
not match the invoking one (the probe exited 9), so the lane's own command
never started. Run via the declared wrapper: /bin/sh -c 'echo probe-stderr >&2; exit 9'
  probe stderr: probe-stderr
   [emitter-format lines: 1]
```

Exactly one line in the emitter's format; the probe tail is an indented
context line that does not match the pattern, so the one-refusal-one-line
counting contract is intact.

### SF-4 / A-423 — verified behaviourally

`scratchpad/probe49sql` with the kill-signal directory replaced only on
mutant runs: still `1` emitter line, still
`ERROR/UNREADABLE_ARTIFACT: '.ks/kill-signal.txt'`, still
`[R0 PASS, R2 ERROR/UNREADABLE_ARTIFACT]` — the `try`/`finally` closes the
descriptor without changing the refusal it was leaking on.

---

## SF-6 — NEW, and the one condition on this ACCEPT

**`runner.py:4066-4076`: the `except RuntimeError` sibling of the branch
A-421 just fixed is still a bare, silent `GIT_FAILED`.**

```python
    except OSError as exc:
        announce_refusal(AssayError(f"snapshot preparation or cleanup failed: {exc}", …))   # A-421 ✔
        if outcome_holder:
            outcome = _replace_highest_higher_rigor_claim_with_git_failed(lane, outcome_holder[0])
        else:
            return refuse_all(Outcome.ERROR, ReasonCode.GIT_FAILED)
    except RuntimeError:                                                                     # ← silent
        if outcome_holder:
            outcome = _replace_highest_higher_rigor_claim_with_git_failed(lane, outcome_holder[0])
        else:
            raise
```

When `outcome_holder` is non-empty this replaces the highest declared
higher-rigor claim with `ERROR`/`GIT_FAILED` and prints nothing — the exact
defect class of my round-1 SF-2 and BLOCKER 1, four lines below the branch
that was fixed for it. It is the last one: I re-audited every
`refuse_lane` / `refuse_all` / claim-conversion site in `runner.py` on the fix
tip and this is the only terminal without an announce. (The one other
`return refuse_lane(...)` with no adjacent `announce_refusal`, at
`runner.py:4331`, is the probe path — announced by `_report_probe_refusal`,
which SF-3 routed through the emitter; measured above.)

**Why it is a condition and not a blocker.** It is `prepare_snapshot`'s own
programmer-error leak detection: an operator cannot reach it by
misconfiguring a lane, only an internal assay bug can, and the non-cleanup
case correctly re-raises. So it does not have BLOCKER 1's operator-facing
sting. But `CHANGES.md:32` says "**with no exceptions**", and this is one.

**Prescription (6 lines, mirroring the branch directly above).** Bind the
exception (`except RuntimeError as exc:`) and announce
`AssayError(f"snapshot cleanup hit an internal consistency check: {exc}",
outcome=Outcome.ERROR, reason_code=ReasonCode.GIT_FAILED)` before the
`if outcome_holder:` — only on the branch that converts to a claim, never on
the `raise` branch, where the traceback is the diagnosis and a refusal line
would be a lie. No test needed beyond what exists; if one is wanted, it is the
`scratch_root_factory` double `test_lane_timeout_writes_a_verdict.py` already
uses, raising `RuntimeError` instead of `AssayError`. Land it with A-425 —
generation 6 is already in this function.

**Alternative, if the controller prefers no further phase-1 churn:** rule the
residue accepted and change `CHANGES.md:32` from "with no exceptions" to
"with one exception, an internal snapshot-consistency check that no lane
configuration can reach". I have no preference between the two; I do have a
preference against shipping the sentence as it stands.

**Observation, explicitly NOT a finding:** `runner.py:861`'s `except OSError`
also returns a payload-free `ERROR`/`EXEC_FAILED` with no sentence, but that
one is a **judged R0 command outcome** carrying a real `CommandResult` with
`argv_effective` and the output tails — not an `AssayError` refusal, and not
in B053's class. Leave it.

---

## REGRESSION SWEEP — nothing I verified in round 1 moved

All re-run on `e3ae8ada` with my own round-1 probes, one at a time:

```
DA-R3's six pre-run sites
  env_required                    1 line  ERROR/BAD_LANE_CONFIG
  bad --shard                     1 line  ERROR/BAD_LANE_CONFIG
  MISSING_EXTERNAL_TOOL           1 line  NO_MEASUREMENT/MISSING_EXTERNAL_TOOL
  pre-run DIRTY_TREE  direct R0   1 line  NO_MEASUREMENT/DIRTY_TREE
  pre-run DIRTY_TREE  higher-rig  1 line  NO_MEASUREMENT/DIRTY_TREE
evaluate_r1's refusal classes
  EMPTY_COVERAGE / FORMAT_MISMATCH / TARGET_NOT_MEASURED / BRANCH_UNAVAILABLE
                                  1 line each, correct pair each
B049  replaced coverage directory 1 line  ERROR/UNREADABLE_ARTIFACT
B049  kill-signal read (SF-4)     1 line  ERROR/UNREADABLE_ARTIFACT
B054  defective file, judged      1 refusal line + 1 named-record line, ERROR/UNREADABLE_ARTIFACT
B029  R3 lane, derived: fact      PASS [R0 PASS, R1 PASS, R3 PASS]
```

Targeted suites, serial: `test_refusal_announcement.py` **20 passed**;
`test_lane_timeout_writes_a_verdict.py` **12 passed**;
`test_safeio_replaced_output_directory.py` +
`test_coverage_istanbul_contradictory_branch_arcs.py` **15 passed**;
`test_r3_canary_sees_infrastructure.py` **3 passed**.

## PHASE BOUNDARY — clean

```
git diff --name-only 93188912..e3ae8ada | grep -E 'src/assay/(verdict|verify)\.py$|src/assay/schemas/'  → (none)
git log --format=%s 93188912..e3ae8ada | grep -c '!'                                                     → 0
config.py:141  LANE_SCHEMA_VERSION = 2
cli.py:1138    LANE_INVENTORY_SCHEMA_VERSION = 1
verdict.py:245 VERDICT_SCHEMA_VERSION = 9
assay/{diff,git,mutation,adapters/python}.py all present and unrenamed
```

`carve-assets/W2/MANIFEST.md` and a new
`ciu-provenance-live-mismatch-ciu-7.10.1.json` **do** change in this range.
That is generation 4's `efbab2bb`, and it is **required** by DA-D7 ("the W2
frozen assets … are STALE and must be re-captured from the real verb before
anything is built against them"), governed by DA-R12. It is phase-2
preparation, not a phase-1 boundary violation, and it touches no schema, no
verdict model and no drift-guard. Correct.

## GATE — GREEN on `e3ae8ada`, read from the markers myself

Not re-run (host rule; my review changes no code). From
`…/e35fad96…/scratchpad/gate-gen5.log`, read in its own step:

```
COMPLETE_MARKERS=1        (ASSAY_REGISTERED_GATE_COMPLETE=1, exactly one)
GATE_EXIT=0
BAD=0                     (grep -cE 'FAILED|DIRTY_TREE|Traceback')
wheel: assay-4.1.1.dev20+ge3ae8ada          ← names the judged commit
phases: wheel-installed → attestation-hardened → verdict-v5-accepted →
        lane-schema-v2-successors-verified → verdict-v6-v7-v8-hard-cut-verified →
        verdict-v9-successors-verified → judge-provenance-bound-to-the-installed-wheel →
        self-hosted-lane-passed → topos-qualified → cmru-b006a-qualified →
        independent-self-hosting-passed → pyflakes-clean
```

The new `pyflakes-clean` phase (B024/A-417) is present and terminal.

## B063 / DA-R14 — filed, correctly

My round-1 harness caveat is `4-backlog.md:6322`, "three test modules
`git -C PROJECT_ROOT.parent`, so the suite cannot run from a copy of the
tree". DA-R14 rules it filed-not-fixed this wave; I agree — it is a
test-harness portability defect, not a judge defect, and I worked around it
this round by running targeted files only.

---

## DECISION ASKS — none new

DA-R10 held DA-D3 as written (my recommendation); DA-R11 kept DA-R5. I have
nothing to reopen. The only open item is SF-6's disposition, stated above as
a binary the controller picks.

---

## SELF-COMPACTION PROMPT

*(Round 2 of 3 is spent. One round remains, and it should only be needed if
the controller wants SF-6 re-verified.)*

**KEEP:**

- **Verdict:** round 2 = **ACCEPT-conditional** on `e3ae8ada`. Both round-1
  blockers RESOLVED and measured; all five should-fixes landed; gate green;
  no regressions. Report:
  `…/scratchpad/assay-WAVE-D-v10-REVIEW-R1-round2.md`.
- **The single condition — SF-6:** `runner.py:4066-4076`'s
  `except RuntimeError` still relabels to `ERROR`/`GIT_FAILED` with no
  announcement, four lines below the `except OSError` A-421 fixed. Either
  land the 6-line announce (bind `as exc`, announce only on the
  `if outcome_holder:` branch, never on the `raise` branch) with A-425, or
  narrow `CHANGES.md:32`'s "with no exceptions". Verify by reading those ten
  lines — no probe needed.
- **Ruled, do not re-raise:** `cli.py:711`'s unbounded `git.head_rev` is
  DA-R13, generation 6 lands `LABEL_GRACE_SECONDS = 2.0` as A-425. B063 is
  DA-R14, filed-not-fixed. DA-R10/DA-R11 closed my round-1 asks 3 and 4.
- **Already verified on `e3ae8ada` — do NOT re-verify unless a fix touches
  them:** all four post-command terminals announce once with command-blaming
  sentences; A-419's two value tests go red under `n1` (2 failed) and `n2`
  (1 failed); A-420 writes verdicts on both paths at `budget = "0.001s"` with
  the real commit, verify-clean under both builds; SF-3's probe path emits
  one emitter line + indented tails; SF-4 preserves the refusal; DA-R3's six
  sites, `evaluate_r1`'s four classes, B049, B054 and B029 all unchanged;
  phase boundary clean (schema 9 / lane 2 / inventory 1, zero `!`);
  gate `GATE_EXIT=0`, one completion marker, wheel
  `assay-4.1.1.dev20+ge3ae8ada`, `pyflakes-clean` terminal.
- **Harness (rebuild if the scratchpad is gone):** `scratchpad/r2wt` =
  detached worktree at `e3ae8ada`; `scratchpad/r2venv` =
  `python -m venv --system-site-packages` + `pip install --no-index
  --no-build-isolation -e r2wt/assay`. Probes: `probe53b` (post-command
  guards, both paths — flip `dirty.sh` between the `leftover.txt` and the
  `git commit --allow-empty` variants), `probe28` (`budget` "0.001s" vs
  "1s"+`SLEEP=3`), `probe29` (R3 + `derived:` fact), `probe49`/`probe49sql`
  (B049's five reads), `probe54` (two-file istanbul), `probe53`/`probe53c`
  (refusal classes), `probe_env` (SF-3). Mutation copies `n1`/`n2` are
  `cp -r r2wt/assay` with one forward deleted by `Edit`.
- **Host rules (binding):** one `pytest` alive at a time, no `-n`/xdist,
  always `nice -n 19 ionice -c 3`, targeted test files, never launch a gate
  container while `docker ps --format '{{.Image}}' | grep tester-unified` is
  non-empty; generation 6 is on the same branch and may be gating. Never
  touch `/workspaces/vbpub/.worktrees/assay-wave-d-v10`. Read gate verdicts
  from the log's markers in a separate step; do not re-run a gate to confirm
  someone else's green.

**DROP:** the full `93188912..e3ae8ada` diff; round 1's blocker narratives
(both resolved); the DA-D1..DA-D16 and DA-R1..DA-R12 text (only DA-R13/R14
remain live); the byte-identity and B024/B009 verification detail (settled in
round 1).

**ON RESUME:** read `git diff e3ae8ada..<new tip>` FIRST, blind. If the only
change is A-425 + SF-6, this is a ten-line read and an ACCEPT. If phase 2
has landed on the same branch, that is **R-2's** scope, not mine — say so
rather than reviewing it.
