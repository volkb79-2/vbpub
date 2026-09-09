# assay B078 Checkpoint 1 — fix-verification, round 2

Reviewer: same reviewer as round 1 (independent of the implementer).
Branch: `feat/assay-b078-r0-structured-report-2026-09-08`
Repair range: `27b66c25..8735b68d` (4 commits, 11 files, +1001/-76).
HEAD verified at `8735b68d`, working tree clean before and after this review.
Method: **my own round-1 probes re-run verbatim first**, then independent
break-probes of every new guard, and only after that a read of the
implementer's LOG/REPORT. Every mutation applied during this review was
reverted; `git status` is empty.

---

## Verdict: **ACCEPT-conditional** — 1 condition, test-only

**Blocker 1 — the one that determines whether this checkpoint does anything
real — is genuinely fixed, and I proved it with the same live probe that
proved it broken last round.** Blockers 2 and 4 are fully closed. Blocker 3's
*code* is correct and is mechanically enforced, but the specific artifact the
implementer offered as its behavioural proof does not work: it passes
unchanged under the exact bug it claims to exclude. That is the single
condition.

**The condition touches no shipped code** — one test body — so if the
controller prefers to merge now and land the repair as a follow-up commit,
that is a defensible call and I would not require another review round for
it. I am flagging it rather than waving it because round 1's entire lesson
was the gap between "the test is green" and "the test could ever be red".

---

## BLOCKER 1 — FIXED. Verified by re-running my own round-1 probe.

I recreated my round-1 probe verbatim (an R0+R1 lane matching `ui_unit`'s real
`rigor = ["R0", "R1"]`, `make_r1_judge`, `FakeAdapter`, a real `/bin/sh`
writing a verified-complete 140-test/0-failure vitest document then `exit 1`)
and added the two directions round 1 did not probe. Results on `8735b68d`:

| Probe | Round 1 | Round 2 |
|---|---|---|
| R0+R1, verified-complete report, 0 failures, exit 1 | `FAIL`/`COMMAND_FAILED` | **`PASS`, reason_code `None`** |
| same lane, **no** `result_report` declaration (must-fail control) | `FAIL` | `FAIL`/`COMMAND_FAILED` |
| R0+R1, verified-complete report naming 7 failures, exit **0** | — | `FAIL`/`COMMAND_FAILED` |

The must-fail control is what makes the first row mean something: the identical
lane, identical argv, identical report on disk, differing only in the
declaration, still fails. The PASS is caused by the declaration and nothing
else. (The probe's *overall* verdict outcome is `NO_MEASUREMENT` because my
`/bin/sh` argv writes no coverage artifact for the R1 half — orthogonal to
B078, which governs the R0 claim.) Probe file removed after the run.

**The mechanism is what was ruled (D-1), and it is implemented as
caller-side opt-in, not a default threaded through the shared engine.** I
traced this rather than accepting it:

- `_execute_snapshot_unit` (`src/assay/runner.py:2640`) gains
  `result_report: ResultReportConfig | None = None` and **only forwards** it
  to `execute_plan` (`runner.py:2907`).
- Its signature takes `plan: CommandPlan`, `snapshot`, `deadline`, ... and
  **no `Lane` at all** — so the implementer's claim that deriving the field
  inside the engine was structurally impossible without handing it a `Lane`
  is true, not rationalised. That is what makes "only the R0 command opts in"
  auditable from call sites.
- The only caller passing anything is `_run_prepared_lane`'s baseline unit
  (`runner.py:3454`, `result_report=lane.result_report`), two lines above the
  `build_r0_claim(result)` that turns it into the R0 claim.

**R2 candidates and R3 canary halves genuinely do not receive it.** Counted at
each site rather than trusted:

| Call site | passes `result_report`? |
|---|---|
| `runner.py:3454` `_run_prepared_lane` baseline → `_execute_snapshot_unit` | **yes** (`lane.result_report`) |
| `canary.py:539` `control_unit` → `_execute_snapshot_unit` | no (0 occurrences in the call) |
| `canary.py:618` `transform_unit` → `_execute_snapshot_unit` | no (0 occurrences) |
| `mutation.py:1936` R2 candidate → `execute_plan` | no (0 occurrences) |
| `runner.py` `environment_command` probe → `execute_plan` | no (0 occurrences) |

The stale comment that claimed `ui_unit` was R0-only is corrected in all three
places I cited in round 1 (`runner.py:5591-5605` now states the opposite
explicitly and names the dispatch, the test docstrings are rewritten, and the
REPORT is corrected).

**Bonus, correctly scoped:** the snapshot path now passes
`result_report_create_missing_parents=True` (`runner.py:2915`), which is
B006(b)-legitimate for an assay-owned ephemeral checkout and is exactly what I
named as available in round 1's Blocker-1 note. The direct R0-only path keeps
`False`. This narrows OBS 3 to R0-only lanes and is covered by
`test_run_lane_r0_r1_baseline_creates_the_reports_parent_directory`.

---

## BLOCKER 2 — FIXED. The sweep is load-bearing; I broke the wiring to prove it.

`tests/test_result_report_wiring_sweep.py` is an AST sweep over
`SOURCE_ROOT.rglob("*.py")` collecting every `execute_plan`/`execute_command`
call and requiring each to either pass `result_report=` or appear in
`EXCLUDED_SITES` with a written reason, plus a `REQUIRED_SITES` check in the
opposite direction and an AST **value** pin on canary's `None`.

I did not read-and-assume. Three break-probes:

| Break | Caught? |
|---|---|
| **A.** Delete `result_report=result_report` from `_execute_snapshot_unit`'s `execute_plan` call | **YES** — killed by *both* `test_every_execution_site_is_decided_about` and `test_every_required_site_still_passes_the_argument`, naming `runner.py:2904 (in _execute_snapshot_unit)` and printing the site's stated reason |
| **B.** Flip `canary.py`'s `result_report=None` to `lane.result_report` (silently extending the tiebreak to both canary halves) | **YES** — killed by `test_the_legacy_canary_pipeline_is_pinned_as_excluded_by_value` |
| **C.** Delete `result_report=lane.result_report` from `_run_prepared_lane`'s baseline call — i.e. undo Blocker 1's fix | **not by the sweep**; killed by three end-to-end tests (`test_run_lane_r0_r1_baseline_applies_the_tiebreak`, `..._report_naming_failures_fails_over_a_zero_exit`, `..._creates_the_reports_parent_directory`) |

So the sweep does what it claims for the sites it watches, and probe C's gap
is covered elsewhere. See OBS-A below for the residual.

---

## BLOCKER 3 — code FIXED and enforced; the behavioural test offered as proof is HOLLOW. **This is the condition.**

### What is right

`canary._run_pipeline` now passes `result_report=None` explicitly
(`src/assay/canary.py:255-264`) with a clear reason. The sentinel is real and
necessary, and I verified the necessity rather than accepting the argument:

`_LANE_DECLARED_REPORT` (`runner.py:1304`) exists because `execute_command`'s
pre-repair behaviour was *unconditional* `lane.result_report`. To give one
caller an opt-out you need a third state — `None` cannot be the default,
because that would disable the feature through the public API entirely.
Proven by mutation: collapsing the default to plain `None` kills **6 tests**,
including the dedicated control
`test_execute_command_honours_the_lane_declaration_by_default`. The sentinel
is correct, necessary, and covered. The coordinator's question ("why would the
default need to change at all if `None` was already safe?") has a real answer:
`None` was never the default, and making it one is itself the bug.

`test_the_legacy_canary_pipeline_never_consults_a_declared_report`
(`tests/test_runner_result_report.py:798`) is a good seam test — it proves an
explicit `result_report=None` really propagates through `execute_command` to
`execute_plan`.

### What is wrong — CONDITION 1

`test_a_declaring_lane_produces_identical_canary_behaviour`
(`tests/test_runner_result_report.py:857-881`) is offered as "the behavioural
half of blocker 3", asserting `outcome_for(VITEST_REPORT) == outcome_for(None)`.
Its lane is:

```python
argv=("/bin/sh", "-c", "exit 1"),
```

That argv **never writes a report**. Both sides therefore fall back to A-073
and both return the same `FAIL` no matter what `_run_pipeline` does with the
declaration. The test cannot distinguish the two states it exists to
distinguish.

Confirmed by mutation, not by reading: with `canary.py` flipped to
`result_report=lane.result_report` (the exact bug this test is named for),
`pytest -k canary` reports **2 passed**. Both canary tests survive the bug.
The seam test survives too, because it drives `execute_command` directly with
an explicit `None` rather than driving `_run_pipeline`.

So the *entire* behavioural enforcement of Blocker 3 rests on the AST value
pin in the sweep. That pin is real and it worked (probe B) — the invariant is
protected — but the branch's own prose claims a behavioural proof that does
not exist, and there is no test that would fail if `canary.py` regressed while
someone also relaxed the pin.

The irony worth naming: the sibling test's own docstring
(`test_runner_result_report.py:806-809`) says *"a behavioural test could pass
while the input was silently being consulted and happening not to change the
outcome"* — a precise description of the defect in the test 50 lines below it.

**Prescription.** Give `outcome_for` an argv that actually writes a
verified-complete, zero-failure report and exits non-zero — the
`shell_writing(vitest_document(total=140, failed=0), exit_code=1)` helper
already in this module does exactly that. Then the declaring lane would PASS
and the non-declaring lane would FAIL *if* the canary consulted the report, so
the equality assertion becomes load-bearing and the test goes red under the
`lane.result_report` mutation. This is a one-line change to a test body; no
shipped code moves.

---

## BLOCKER 4 — FIXED, accurately and discoverably.

`docs/CONSUMERS.md` now says, correctly: *"**The wrapped process's exit code
is not a verdict field** and never has been; it appears only in the progress
stream's `command_finished` event, which exists when you pass `--progress`."*
The false "still records the real `returncode`" sentence is gone, and the
`runner.py:1266-1276` comment is corrected in the same terms, explicitly
noting that an earlier version of it said otherwise.

The replacement is better than a deletion: a new *"How to tell, from a verdict,
that a report overrode an exit code"* subsection puts the detection rule in a
blockquote — a `PASS` carrying `result_stdout_tail`/`result_stderr_tail` is an
overridden `PASS`, because an ordinary green run omits both. **I verified that
premise holds** rather than accepting it: `CommandResult.stdout_tail` defaults
to `None` (`runner.py:748`), the green happy path constructs a `CommandResult`
without tails, and `Verdict.result_stdout_tail`'s own contract distinguishes
`None` (absent) from `""` (captured and empty). So the rule is sound, and it
also closes round-1 OBS 4.

`docs/CONSUMERS.md` additionally now distinguishes R0-only lanes (no parent
creation) from snapshot lanes (assay creates it), which is accurate to the new
code.

---

## Also checked

- **OBS 2 — actioned.** `("result_reports/vitest_json.py", "read")` added to
  `test_untrusted_json_parse_sweep.py`'s pinned list, with a reason, and the
  docstring's "These eight" updated to "These nine". Correct.
- **OBS 5 — actioned.** `docs/DESIGN-GUIDE.md:571-590` replaces the
  overstated sentence with an exhaustive, now-accurate list of the three
  consulting sites (one per lane shape) and the three excluded ones, and
  points at the sweep. Matches the code as traced.
- **OBS 1 — not actioned, sound reason.** Left as defence-in-depth for
  Checkpoints 2/3; collapsing the core's `finished` bullet into the reader
  would make the format-agnostic core format-specific. I agree — this was my
  own framing in round 1 and the disposition is right.
- **OBS 3 — not actioned, sound reason.** Genuinely narrowed by Blocker 1's
  fix (snapshot lanes now create the directory), so it survives only for
  R0-only lanes, and the `--progress` diagnostic is deferred to Checkpoint 2.
  Reasonable; I explicitly said in round 1 I would not block on it.
- **Backlog tick — correct box, nothing over-ticked.** `4-backlog.md:8122`
  Checkpoint 1 is reverted to `[ ]` with an honest note naming the round-1
  premature tick and why. The fault-injection item at 8137 stays `[x]`, which
  is the one I judged earned. Checkpoints 2 and 3 remain `[ ]`.
  **On this ACCEPT, the Checkpoint 1 box is the tick I am authorizing, and
  only that one.**
- **SR-6 constraints still hold after the repair.** `git diff --name-only
  27b66c25..8735b68d` touches no `errors.py`, no `verdict.py`, no
  `verdict.schema.json`, no `run-gate.py`, nothing outside `assay/`.
  `RESULT_REPORT_FORMATS` is still `frozenset({'vitest-json'})` — Checkpoints
  2 and 3 remain unbuilt.
- **The gate run under review is the clean one.** HEAD is `8735b68d`, the
  tree was clean at the start of this review, and the disclosed
  `NO_MEASUREMENT`/`DIRTY_TREE` detour was an earlier attempt, not this
  commit. The controller verified the log markers separately; I did not
  re-run the full gate. Targeted regression on the repaired branch:
  **298 passed, 1 skipped** across `test_runner_result_report.py`,
  `test_result_report_wiring_sweep.py`, `test_result_reports.py`,
  `test_config_result_report.py`, `test_untrusted_json_parse_sweep.py`,
  `test_runner_run_lane.py`, `test_self_hosting.py` and the canary suites.

---

## Non-blocking observations

- **OBS-A (new).** The wiring sweep watches `execute_plan`/`execute_command`
  calls only, so `_run_prepared_lane`'s call to `_execute_snapshot_unit` — the
  site that *is* Blocker 1's fix — is outside its watch set (probe C). Three
  end-to-end tests cover it, so this is defence-in-depth rather than a hole,
  but the sweep's own docstring implies a completeness it does not have for
  that one hop. Consider extending the watch set to `_execute_snapshot_unit`
  calls in Checkpoint 2.
- **OBS-B (new).** `EXCLUDED_SITES` and `REQUIRED_SITES` are keyed by
  `(module, function)`, and `("runner.py", "run_lane")` appears in **both** —
  legitimately, since `run_lane` contains the probe site (excluded) and the
  direct-branch site (required). The two tests cover each other for the
  single-edit cases, but a simultaneous pair of wrong edits (adding the
  argument to the probe site while removing it from the direct branch) would
  satisfy both. The end-to-end direct-branch test catches that, so it is not a
  gap in practice; noting it because function-level granularity is the sweep's
  one structural limit.
- **OBS-C.** `_LANE_DECLARED_REPORT: Any = object()` typed against a
  `ResultReportConfig | None` parameter is the standard sentinel escape and
  mirrors `conftest`'s `_ISOLATION_UNSET`, but it does mean the type checker
  cannot see the third state. Fine as is; noted only so it is a known choice.

---

## Reconciliation with the implementer's account

Read after the above. Their LOG is accurate and unusually honest: it names the
round-1 premature self-tick as their own error, records the `DIRTY_TREE` gate
detour rather than hiding it, and states plainly that the `ui_unit` R0-only
claim was wrong. Their reasoning on the three-state sentinel, on
forwarding-not-deriving in the shared engine, and on the OBS 1/OBS 3
non-actions all matches what I derived independently.

The one place their account overstates is the claim I have made the condition:
`test_a_declaring_lane_produces_identical_canary_behaviour` is described as
proving byte-identical canary behaviour, and it cannot. Everything else they
claim, I reproduced.

---

## Summary for the controller

Round 1 rejected this because a green branch did nothing for RG-45's real
lane. That is fixed, and I verified it the same way I found it — by running an
R0+R1 lane end to end and reading the R0 claim, which is now `PASS` where it
was `FAIL`/`COMMAND_FAILED`, with a must-fail control proving the declaration
is what changed it. The wiring sweep is a real guard, not a plausible one: I
broke the wiring three ways and it caught the two it is scoped to, with the
third caught by end-to-end tests. Blocker 4's documentation is now accurate
and the detection rule it teaches is verifiably true.

**ACCEPT-conditional on one test-only repair**
(`tests/test_runner_result_report.py:857-881` must use an argv that actually
writes a report, so the assertion can fail). The invariant that test is
guarding is already enforced by the sweep's AST value pin, which I confirmed
works — so the shipped behaviour is correct today. If the controller merges
now and lands that one-line test repair as a follow-up, I do not need to see
it again.
