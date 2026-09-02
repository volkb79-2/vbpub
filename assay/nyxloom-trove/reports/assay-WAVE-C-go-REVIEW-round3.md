# assay Wave C (Go / P27 re-carve) — adversarial review, **round 3** (fix verification, the cap)

Reviewer: same adversarial reviewer as rounds 1 and 2.
Subject: `feature/assay-wave-c-go` @ **`4889b742`** (worktree
`/workspaces/vbpub/.worktrees/assay-wave-c-go`).
Round-2 review committed verbatim on the branch as
`assay/nyxloom-trove/reports/assay-WAVE-C-go-REVIEW-round2.md` (`71a59967`).

---

## 0. Verdict

# ACCEPT.

Unambiguously, and with no conditions attached. **BLOCKER R2-1 is fixed**,
SF-R2-1, SF-R2-2 and SF-R2-3 are all discharged, and every one of the five
in-image scenarios I used to prove the blocker now behaves as DA-R2 and
CONSUMERS.md say it should — measured by me, against a zipapp I built from
`4889b742`, with the verdict documents read back in a separate step. The
registered gate is PASS on the judged tip in my own hands. The generation
also added a control test nobody asked it for that catches the one hazard its
own fix creates, and I confirmed by mutation that it is the *only* test that
catches it.

I found nothing new that blocks, and nothing new that I would file. This is
the strongest generation of the wave.

---

## 1. Method

* Nothing was edited or committed in `/workspaces/vbpub/.worktrees/assay-wave-c-go`;
  clean at `4889b742` on entry and on return (§6).
* Blind pass on `git diff 71a59967..4889b742` and the tree FIRST; LOG/REPORT
  §50-53 read only afterwards (§5).
* Probes ran in a detached scratch worktree of my own
  (`…/scratchpad/r3wt` at `4889b742`) and in disposable container mounts.
  Every mutation was applied with the **Edit tool**, never a script, and every
  one was reverted — `git status --short` empty after each (shown below).
* Every Go execution: `tester-unified-go:local`, `--network=none`,
  `--cgroup-parent=dev-background.slice`, `GOPROXY=off GOWORK=off
  GOTOOLCHAIN=local GOFLAGS=-mod=mod`.
* The zipapp under test was built OUTSIDE the worktree from the judged tip:
  `assay-4.0.1.dev54+g4889b742.pyz`, sha256
  `d7460c7344f3235dbd9919c2029a2883cff88bfcfa03a80da27b8bc92b5d6757`.
  Where a probe's subject is a *built artifact* the mutation was a COMMIT
  (round 1's invalid-probe rule); where the subject is `pytest`, which imports
  `src/` from the working tree, a working-tree edit is what runs and is what I
  used. Same distinction the generation drew for itself in `4889b742`, and it
  is correct.
* Verdicts read in a step separate from the runs that produced them (L4).

---

## 2. Blind pass — what the diff says before anyone tells me

Source surface since `71a59967` is one file: `assay/src/assay/runner.py`
(+79). Tests: four modules. Docs: `CONSUMERS.md`, `CHANGES.md`,
`decisions.md`, LOG, REPORT. Nothing else.

The fix at `runner.py:2780-2833` is **my round-2 prescription applied
exactly** — `if r1_claim.coverage is None:` immediately after
`claims += (r1_claim,)`, filtering `helpers_seen` through
`supported_helper_roles((r1_claim,))`, with `assemble_verdict`'s guard
untouched. Two things in the diff I did not ask for and which improve on what
I wrote:

1. **Both "the ONE such site" comments were corrected to two** —
   `assemble_verdict`'s (`runner.py:1485-1496`) and
   `_replace_highest_higher_rigor_claim_with_git_failed`'s docstring
   (`:3471-3479`). My prescription would have left two comments asserting a
   fact the fix falsifies. That is the class of defect I raised as round-1
   BLOCKER 2 against this same wave; the generation caught it in its own work.
2. **The rejected alternative is recorded at the guard**, not just in
   `decisions.md`: a filter *there* would cover both drop sites and swallow a
   genuine wiring defect with them, which is the one thing the guard exists
   for. That is the right argument and it is the one I would have made.

The comment at the new site also states a constraint on future work that I
checked mechanically and agree with: `helpers_seen` has exactly ONE producer
(the `_record_statement_position_helper` sink handed to `evaluate_r1`,
`runner.py:2779`; the role is supplied by the call site, not the adapter,
`runner.py:1032-1050`), so asking `supported_helper_roles` about the R1 claim
alone is exact rather than convenient, and a future second helper channel must
carry its own drop rather than share this sink.

---

## 3. The checklist, ruled item by item

### 1. BLOCKER R2-1 — my five scenarios, re-run verbatim in-image · **PASS**

Transcript `…/probes3/ldlane-r3.log`; the harness is unchanged from round 2
(`…/scratchpad/ldlane/work/{setup.sh,laneA..E.sh,all.sh}`), only the zipapp
differs.

```text
=== SCENARIO A ===  unit:  PASS (exit 0)
=== SCENARIO B ===  unit:  ERROR/BAD_LANE_CONFIG    (exit 2)   verdictB.json written
=== SCENARIO C ===  stale: ERROR/UNREADABLE_ARTIFACT (exit 2)  verdictC.json written
=== SCENARIO D ===  unit:  ERROR/BAD_LANE_CONFIG    (exit 2)   verdictD.json written
=== SCENARIO E ===  stale: ERROR/UNREADABLE_ARTIFACT (exit 2)  verdictE.json written
```

**The masked sentence "recorded helper role(s)" appears 0 times in the whole
transcript.** (Round 2: three times, in B, C and E, each with no artifact at
all.)

The five documents, read back in a separate step:

| verdict | outcome | reason_code | R1 claim | R1 `coverage` | `helpers` |
|---|---|---|---|---|---|
| A | `PASS` | — | `PASS` | present | **present — `statement-positions` / `go`** |
| B | `ERROR` | `BAD_LANE_CONFIG` | `ERROR`/`BAD_LANE_CONFIG` | absent | absent |
| C | `ERROR` | `UNREADABLE_ARTIFACT` | `ERROR`/`UNREADABLE_ARTIFACT` | absent | absent |
| D | `ERROR` | `BAD_LANE_CONFIG` | `ERROR`/`BAD_LANE_CONFIG` | absent | absent |
| E | `ERROR` | `UNREADABLE_ARTIFACT` | `ERROR`/`UNREADABLE_ARTIFACT` | absent | absent |

Every clause of the checklist is met: B/C/E carry the **judge's own** reason
code with `helpers` absent and a document on disk; A keeps its `helpers[]`
entry, which is what stops the fix being a blanket filter. `assay_version` is
`4.0.1.dev54+g4889b742` in all five — the judged tip, not a stale build.

### 2. SF-R2-1 and SF-R2-2 — my two mutants, re-applied verbatim · **PASS**

Baseline, four A-405 modules in `…/r3wt`: **70 passed** (round 2: 69; +1 is
the new SF-R2-2 test).

**M-B2** — `evaluate.py:1071`'s
`if file_cov is not None and file_cov.line_directive_remapped:` → `if False:`
(Edit tool):

```text
FAILED tests/test_go_line_directive_witness.py::test_the_same_refusal_fires_in_whole_target_mode
1 failed, 69 passed
```

**Killed, by exactly the named test.** In round 2 this mutant left all 69
green. The assertion it dies on is the right one — the emptied file trips
`TARGET_NOT_MEASURED` ("zero executable lines … refuses rather than pass on a
target that was never measured"), which is precisely the misdirection the
branch's own ordering comment predicts. Reproduced independently by me, not
quoted from the REPORT.

**M-F** — `runner.py`'s `to_attribute` filter clause removed (Edit tool):

```text
FAILED tests/test_runner_statement_attribution_wiring.py::test_the_oracle_is_never_ASKED_about_a_line_directive_remapped_file
1 failed, 69 passed
```

**Killed, by exactly the named test.** (My count is 1-of-70; the REPORT's
"1 of 61" is the same kill over a different module set — no disagreement.)

Both mutants reverted; `git status --short` empty after each.

### 3. SF-R2-3 — the docs against what a consumer actually sees · **PASS**

CONSUMERS.md now prints the console line first and labels the long text as
"the refusal assay *raises* underneath". Checked against my own scenarios:

| CONSUMERS.md says | my scenarios |
|---|---|
| `unit: ERROR/BAD_LANE_CONFIG (exit 2)` (limit 5, `//line`) | scenario B and D, verbatim |
| `unit: ERROR/UNREADABLE_ARTIFACT (exit 2)` (limit 2, stale) | scenario C and E, verbatim (`stale:`, the lane's own name) |
| "the verdict document's R1 claim carries the same … pair with no coverage payload" | the table in item 1 — exactly that, in all four |
| "that text reaches only a caller that invokes the evaluation layer itself … a CLI consumer gets the reason code and nothing more" | matches my round-2 control (a Python lane's `FORMAT_MISMATCH` prints no text either) |

It also names **B053** as the tracking item and says it is not this wave's to
fix. That is the honest shape and it closes SF-R2-3. DA-R3 is the
controller's, pre-adjudicated, and I do not re-open it.

### 4. A-407's own regression tests, mutated · **PASS**

Baseline over `test_runner_helpers_envelope.py`,
`test_runner_statement_attribution_wiring.py`, `test_runner_run_lane.py`,
`test_go_line_directive_witness.py`: **60 passed**.

| # | mutation at the A-407 site | measured | killed by |
|---|---|---|---|
| M-H | the drop removed (`if False:`) | 2 failed, 58 passed | `test_a_judge_that_refuses_after_the_oracle_ran_reports_ITS_reason_not_the_wiring` **and** `test_that_refusal_really_reaches_a_verdict_ARTIFACT` |
| M-G2 | unconditional bare clear (`if True:` + `helpers_seen[:] = []`) | 1 failed, 59 passed | **`test_a_lane_whose_r1_really_JUDGED_keeps_its_helper_through_run_lane` — and nothing else** |
| M-G0 | bare clear *inside* the guard | 60 passed | — (semantically identical to the fix; the filter and a clear coincide when `coverage is None`) |
| M-G | `if True:` with the real filter kept | 60 passed — and **3943 passed / 11 skipped on the whole suite** | — (survives) |

**The control at `99d2a443` is exactly the test that catches the unconditional
drop, and it is the only one.** M-G2 is the real hazard the fix creates: every
other new assertion is about a helper going *away*, so an unconditional drop
would have satisfied all of them while silently deleting `helpers[]` from every
passing Go verdict in the product. The implementer noticed that about its own
work and wrote the control unprompted. I measured it: without that test, M-G2
is a surviving mutant. It is a real strengthening of the suite, and I judge it
on its merits as such.

**On M-G surviving: I agree it is correct-by-construction, not a gap.** When
`r1_claim.coverage is not None`, `supported_helper_roles((r1_claim,))` contains
`statement-positions`, and `statement-positions` is the only role
`helpers_seen` can hold (single producer, verified above), so the filter is the
identity on the passing path and the two programs are behaviourally the same.
I checked that claim against the *entire* suite rather than the four modules:
3943 passed, 11 skipped (`--ignore=tests/qualification`), zero difference. The
`if` is an intent statement, and the REPORT says so in those words.

**The two new in-image qualification tests are not hollow either — I proved
them red myself.** At `…/g8/prewt2` (`4c11ca30`: the tests, committed, without
the fix), so the zipapp the fixture builds really carries the defect:

```text
E  AssertionError: A-407: the consumer is being told about assay's helpers[] wiring instead of about the artifact:
E    assay: ERROR/BAD_LANE_CONFIG: lane 'stale' recorded helper role(s) ['statement-positions'] …
FAILED tests/qualification/test_go_r1_real.py::test_a_line_directive_file_with_judged_lines_refuses_and_writes_a_verdict
FAILED tests/qualification/test_go_r1_real.py::test_a_stale_go_profile_refuses_through_the_cli_and_writes_a_verdict
2 failed, 5 deselected
```

Green on the tip: `ASSAY_GO_QUALIFICATION=1 pytest
tests/qualification/test_go_r1_real.py` → **7 passed** (was 5).

### 5. A-399 — is every touched test still about what its name says? · **PASS**

| test | name promises | what it asserts | ruling |
|---|---|---|---|
| `test_the_same_refusal_fires_in_whole_target_mode` | the `//line` refusal, in whole-target mode | now on a real `tmp_path` target, asserts `` `//line` `` in the message, the filename, `judge.targets`, **and negatively** excludes both wrong refusals ("does not exist as a regular file", "zero executable lines") | honest **now**; it was not before, and the negative assertions are what make it stay honest |
| `test_the_oracle_is_never_ASKED_about_a_line_directive_remapped_file` | the oracle is not asked | `adapter.calls == [(…, ("pkg/mod.blk",))]`, plus an explicit vacuity guard that the flagged file really is in the profile | honest |
| `test_a_lane_whose_r1_really_JUDGED_keeps_its_helper_through_run_lane` | a judged lane keeps its helper | `PASS`, statement-granular `2/2` (not `5/5`), `helpers == (Helper(role="statement-positions", …),)` | honest; the `2/2` is what makes it a *statement-attribution* control rather than a coverage one |
| `test_a_judge_that_refuses_after_the_oracle_ran_reports_ITS_reason_not_the_wiring` | the judge's reason, not the wiring | `UNREADABLE_ARTIFACT` on both verdict and claim, `coverage is None`, `helpers is None`, **and R0 `PASS`** so it cannot pass on a run that never reached the oracle | honest |
| `test_that_refusal_really_reaches_a_verdict_ARTIFACT` | it reaches an artifact | reserves, writes, reads the file back off disk | honest — and this is the half a returned object cannot prove |
| the two qualification tests | refuses **and writes a verdict** | asserts exit 2, the masked sentence absent from stderr, the file exists, then the document's fields; plus toolchain witnesses (a real zero-column record for B; *no* zero column for E) | honest, and the witnesses stop each fixture drifting into the other's case |

The `_BlockOracleAdapter` pair differs by exactly one field (`end_line=7` vs
`9`), which is the discipline I would ask for: the two tests differ by the
thing under test and nothing else.

---

## 4. Gate, suite, qualification — in my own hands, on the judged tip

| check | reported | me, on `4889b742` |
|---|---|---|
| registered gate | run 12 PASS on `99d2a443` | **PASS on `4889b742`**: 11 `ASSAY_GATE_PHASE=` markers, `topos-qualified` (`outcome=PASS exit_code=0`), `cmru-b006a-qualified`, `independent-self-hosting-passed`, exactly one `ASSAY_REGISTERED_GATE_COMPLETE=1`, wrapper exit 0, wheel `assay-4.0.1.dev54+g4889b742`, no `FAILED`/`DIRTY_TREE`/`Traceback` (`…/probes3/gate-tip-r3.log`) |
| full suite | 3943 passed, 20 skipped | **3943 passed, 20 skipped**, exit 0 — identical |
| Go qualification | 7 passed | **7 passed** |
| skip arithmetic | 3939+4 tests, 18+2 skips | reproduced: 3943/11 with `--ignore=tests/qualification`, 3943/20 without. Generation 7's "11" is explained and the LOG now says which command produced it |

Every number the generation reported, I reproduced exactly. That has not been
true of every generation of this wave.

---

## 5. Reconciliation with LOG / REPORT §50-53 (read after the probes)

* **§50's before/after table matches mine cell for cell**, including scenario
  A keeping its helper and D being correct-by-accident because its oracle never
  runs. Their "before" is `71a59967`, mine was `1d464fc4`+`875382d2`; same
  masking, independently produced.
* **§50's M-G/M-G2 pair matches my measurements** (`1 failed, 9 passed` over
  their module set; `1 failed, 59 passed` over mine — same single kill).
* **§51's M-B2 failure text is the text I got**, character for character.
* **§52's skip arithmetic is right** and matches my own two measurements.
* **§53's "did NOT do" list is accurate**: no `diagnostics` route, no reason
  code, no wire field, guard not weakened, B054 still deferred, no backlog
  entry. I verified each mechanically (§6).
* **§53 declines to re-run the srdm F008-A5 harness.** I agree, and say so as
  the reviewer who ran it twice: the diff since `71a59967` is one guard in
  claim assembly plus tests and docs — it cannot touch the parser, the extent
  join or the B061 fold, which are the only things 418/394/94.26% depends on.
  Round 2's re-run against `1d464fc4` stands.
* **`4889b742`'s self-correction is right and I checked the substance, not
  just the honesty.** `pytest` imports `src/` from the working tree, so a
  working-tree mutation IS what it runs; round 1's rule binds only when the
  subject is a built artifact. The four pytest mutants were validly measured,
  and the two artifact-subject probes (`prewt` `835fd0d9`, `prewt2`
  `4c11ca30`) were commits. Volunteering the correction cost the generation
  nothing and is exactly the record-keeping I want to see.

Nothing in the LOG or REPORT claims something my probes contradict.

---

## 6. Controller checklist

| check | result |
|---|---|
| no new reason code | ✔ `vocabulary.py` untouched since `71a59967` |
| no schema / `verify.py` / `verdict.py` / gate / W5 change | ✔ 0 files |
| nothing under `shared-ramdisk-depot-manager/` | ✔ 0 files |
| no `!` commits | ✔ 0 |
| no `diagnostics` route | ✔ `runner.py:365` untouched |
| two commits above `99d2a443` are docs-only | ✔ LOG + REPORT only, 125 insertions |
| source/test surface | ✔ `runner.py` + 4 test modules, nothing else |
| worktree clean at `4889b742` | ✔ on entry and on return |
| my scratch worktrees restored | ✔ `…/r3wt` clean at `4889b742` after every mutant; `…/g8/prewt2` left as I found it |

---

## 7. Claims I could not verify independently

None that matter. Two notes for the record:

* The REPORT's per-mutant *passed* counts differ from mine because we ran
  different module sets (their 48/9, my 69/59). The **kills** — which test
  dies and why — are identical, and those are the load-bearing half.
* `4889b742`'s claim that both scratch worktrees were restored: `prewt2` is
  clean and at `4c11ca30`, which I confirmed while using it. I did not audit
  `prewt`'s reflog.

---

## 8. Ruling

**ACCEPT.** BLOCKER R2-1 is fixed at the site I prescribed, with the two
comments my prescription would have left stale corrected, a control test I did
not ask for that closes the one hazard the fix creates, and four regression
tests I mutated individually and found each to be load-bearing. SF-R2-1 and
SF-R2-2 kill both of my surviving mutants. SF-R2-3 tells consumers the truth.
The registered gate is green on the judged tip in my own hands, and every
count the generation reported reproduces.

No blockers. No should-fixes. Nothing to file. The controller may merge.

---

## SELF-COMPACTION PROMPT

**KEEP:** the verdict (ACCEPT, unconditional, on `4889b742`); that the five
in-image scenarios A-E all behave (B/C/E = judge's own reason code + verdict
written + `helpers` absent + the masked sentence gone; A = `helpers[]` kept);
the mutation matrix (M-H → the two A-407 refusal tests; M-G2 → the control
alone; M-G survives correct-by-construction, verified over the whole suite;
M-B2 and M-F each killed by their named test); the qualification red-proof at
`prewt2`; gate PASS on the tip with 11 phases and exit 0; 3943/20 and 7
qualification passed.

**DROP:** the per-item narrative of §2-3 once the verdict is recorded; the
skip-count arithmetic (settled); the round-1 and round-2 findings (all
discharged); the harness paths (they are in the file).
