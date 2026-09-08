# nyxloom-P104 — REPORT

Branch `nyxloom-P104`, worktree `/workspaces/vbpub/.worktrees/nyxloom-p104`.
NOT merged, no PR. (Written by the controller into the worktree — the
implementer agent was blocked from writing this file itself.)

## Commits (3, merge-base with main is `ac329929`)

| Commit | What |
|---|---|
| `2beeca4d` | `fix(nyxloom): B13 -- watchdog streak baselines to the operator's resume` |
| `4c0c3f73` | `perf(nyxloom): B25 -- skip SESSION_CAPTURE_DELAY when capture can never succeed` |
| `0364a980` | `docs(nyxloom): re-measure watchdog.py's ownership-inventory row for P104` |

## Gate

`./run-gate.py tester-unified` (the registered lane `cmru.toml` releases on, not bare pytest):

```
tester-unified: PASS (exit 0)   commit 0364a9804731f6285636058d86ed3b31c750d947
  R0 PASS   R1 PASS  100.0% of 57 changed executable lines across 4 source files
  base ac329929 (merge-base)
```

First run was red on one pre-existing drift check (`test_inventory_sizes_are_within_the_declared_tolerance`,
watchdog.py's ownership-inventory row stale), fixed by `0364a980`, then green.

---

## Part A — B13 (watchdog thrash-streak re-pausing after resume)

### The bug was worse than the backlog said

P49 froze the streak at 0 while a project is **paused**. `detect_runaways`
re-detects a persistent-but-acknowledged condition identically on every
pass. Reading the live code found the part the backlog missed: **in the
common shape P49's reset never fires at all.** The pause flag is written
*during* pass N, but `project_paused` for that pass was computed at pass
start — so the streak ends pass N sitting exactly *at* the threshold. If
the operator resumes before another pass runs while paused (the normal
case), P49's `= 0` never executes and the **very first** post-resume pass
re-pauses. Reproduced with an instrumented run: `REPAUSED at pass 0`,
streak dict `{'demo:attempt-loop:...': 3}`.

A *hold* on the streak (the first cut attempted) is not enough — it has to
be a real **baseline shift**, reset exactly once per resume.

### The fix

`watchdog.resume_baseline(sig, recent_events) -> (marker, has_new_evidence)`
plus `last_resume_index` / `has_new_evidence_after`. Additive:
`WatchdogConfig`, `RunawaySignal`, `detect_runaways` untouched byte-for-byte.

- **Anchor** = most recent `PAUSE_CLEARED`, identified by event **sequence**
  (several events land per pass, so a timestamp can't distinguish "already
  counted" from "since my last pass"). Verified all three resume surfaces
  append one: `daemon._post_config_pause`, `cli.cmd_resume` (both normal and
  forced-override), `commands._cmd_resume`.
- **Evidence**, one branch per pattern (same key parse as
  `_suppress_runaway_action`): `notification-storm:total` -> any
  `NOTIFICATION_REQUESTED`; `notification-storm:<TYPE>:<reason>` -> that
  type+reason; `reconcile-thrash:<reason>` -> `SPEC_ATTENTION` with that
  reason; `attempt-loop:<task_id>` -> `ATTEMPT_CREATED` for that task;
  `tick-error-streak` -> `TICK_ERROR`.
- **The watchdog's own output is never its own evidence** — `NEEDS_OPERATOR
  {reason:'runaway'}` is excluded, else `notification-storm:NEEDS_OPERATOR:
  runaway` would re-climb from its own escalation tail.
- **Fail-open toward armed**: unknown pattern, or no `PAUSE_CLEARED` in the
  ~500-event slice -> pre-B13 unconditional advance.

`daemon._apply_watchdog` uses it via a new disposable
`self._runaway_resume_marker` shadowing `_runaway_streak`: reset to 0 when
the marker differs from the one it last counted from, then advance only on
new evidence. Escalation and suppression stay ungated — the repeating
action is still dropped every pass, resumed or not.

### Verification

Three behavioural tests in `tests/test_daemon.py` beside P49's:
1. `..._does_not_repause_after_operator_resume_without_new_evidence`
2. `..._still_suppresses_the_repeating_action_after_a_resume`
3. `..._repauses_after_a_resume_when_genuinely_new_evidence_arrives`

Non-hollow check: `git stash` reverting only `daemon.py` + `watchdog.py`
(tests kept) makes tests 1 and 3 FAIL pre-fix; test 2 passes pre-fix by
design (pins the fix against over-reaching).

Property test: `tests/test_invariants.py`, new INVARIANT 5 (the prompt's
pointer to an existing "I6 property" was wrong — I6 is prose in
`docs/flow-system-review-and-redesign.md:58`, P49's own tests are
behavioural in `test_daemon.py`, not a pre-existing property). INVARIANT 5:
- `test_every_watchdog_pattern_has_a_b13_evidence_branch` reads the pattern
  space out of `detect_runaways`'s own source, requires a case per member
  (fail-open means an untested 5th pattern would silently lose B13
  protection).
- Parametrized both directions (unchanged -> 0 advances over N*5 passes;
  one new event -> still reaches threshold).
- Plus self-escalation exclusion, no-resume fail-open, only-the-latest-
  resume-is-the-baseline, evidence is per-`key` not per-pattern.

Two fail-open edge tests added in `tests/test_watchdog.py`.

---

## Part B — B25

### Half 1 (de-flake) was already done — backlog entry was 6 weeks stale

`test_transient_throttle_resumes_same_attempt_end_to_end` is not xfail'd;
the de-flake landed 2026-07-25 (`_sync_launch`/`_tick_sync` replace the real
double-fork). Re-verified: 12/12 solo (nice -n 10, serial), and passes
inside the full registered gate under xdist at a 3-CPU cap. No change made.

### Half 2 (the product speedup) — done, with a correction to the backlog

**The backlog's stated skip condition was wrong.** `capture_session`'s
first branch is `route.cli == "claude"`, which needs neither
`session_capture` nor `session_discover`. Skipping on "neither field set"
would have silently broken resume-handle capture for the estate's main
route. `tests/test_wrapper.py::TestStreamJsonSessionCapture` pins exactly
that shape and still passes.

Real provable-never-succeeds condition: `cli != "claude"` AND no
`session_capture` AND no `session_discover` ->
`adapters.can_capture_session(route)`, placed beside `capture_session` so
the two can't drift. `wrapper_main` skips both the 5s wait and the useless
call, DEBUG-logs `session-capture-skipped`. Purely subtractive.

Evidence:
- `test_adapters.py`: truth table both directions, incl. the
  claude-needs-neither-field case.
- Coupling test: for every route shape the predicate calls impossible, run
  the real `capture_session` against a real attempt dir + a log a claude
  route *would* capture -> assert `None`. Guards future drift between the
  predicate and the real function.
- `test_wrapper.py::TestSessionCaptureDelaySkip`: two real `wrapper_main`
  legs, wall-clock measured. No mechanism + 60s delay patched in -> finishes
  under 15s, `capture_session` never called. Capture-capable + 1.0s delay ->
  still honoured (>=0.9s) and still called.
- Non-hollow: stashing only `wrapper.py` + `adapters.py` makes the skip
  test FAIL.

Production effect: 5s saved per leg on every non-claude route with no
capture mechanism, dispatch and resume. Zero change for claude routes or
any route declaring a mechanism.

---

## Host discipline — one incident, self-corrected

On the gate re-run, a gate was launched while the sibling P106 agent's lane
had started 87s earlier (slot was checked before Part B, not immediately
before launch). Two gate containers were briefly live, load spiked to 13.3.
Detected, killed its own (the later starter, not P106's), waited for
P106's lane to clear, re-ran with `--fresh`. The green verdict is from a
run that was the sole gate container on the host, capped at `--cpus=3`.

## Files touched

`src/nyxloom/watchdog.py`, `src/nyxloom/daemon.py`, `src/nyxloom/adapters.py`,
`src/nyxloom/wrapper.py`, `tests/test_daemon.py`, `tests/test_invariants.py`,
`tests/test_watchdog.py`, `tests/test_adapters.py`, `tests/test_wrapper.py`,
`nyxloom-trove/4-backlog-inbox.md` (B13 + B25 removed),
`nyxloom-trove/reports/CORE-REDESIGN-OWNERSHIP-INVENTORY-2026-08-02.md`.

Confirmed via `git diff --stat $(git merge-base main HEAD)..HEAD` (the
merge-base, `ac329929`, not a straight tip-vs-tip diff — main has since
advanced with unrelated concurrent work from a different session) that this
file list is exactly right; no cross-contamination from other projects.

## Needs reviewer / operator judgement before merge

1. B25's backlog text was materially wrong on the skip condition — it
   omitted the `cli == "claude"` branch. Implemented as literally written,
   it would have broken session capture for the estate's main route.
2. B25 half 1 shipped 2026-07-25 and was never retired from the inbox,
   which claimed a live xfail that had not existed for six weeks. Both
   entries (B13, B25) are now removed from `4-backlog-inbox.md`.
3. Hand-deleting the pause flag directly (bypassing all three resume
   surfaces) still gets pre-B13 behaviour, since no `PAUSE_CLEARED` lands.
   Every real surface appends the event, so this is a hand-edit-only gap.
   P49's two existing tests that resume this way were deliberately left as
   the regression pins they already are — a reviewer may want them made
   event-realistic instead.
4. Semantics chosen: monotone-since-resume, not since-last-advance — one
   new qualifying event lets the streak climb on each of the next
   RUNAWAY_PERSIST_AFTER_CYCLES passes, not just once. This is what the
   operator interview locked in (safety-forward reading). The stricter
   alternative (require fresh evidence every single pass) was considered
   and rejected; flagging in case a reviewer wants the tighter semantics.
   **SUPERSEDED by the review round below — strict is now implemented.**

---

# Review round 1 — REJECT addressed (2026-09-08)

Commit `c90faf82` `fix(nyxloom): B13 review round -- F1 suppression
starvation, strict streak`. Everything below amends the report above; the
core B13 design (baseline shift to resume, anchored on `PAUSE_CLEARED`)
is unchanged.

## F1 (BLOCKING) — auto-pause was permanently unreachable after a resume

### The mechanism

B13 shipped with ONE evidence source: an event of the signal's own shape,
emitted after the resume. For two of the four patterns that event is
exactly what the watchdog itself prevents:

| Signal | Evidence B13 required | What `_suppress_runaway_action` drops |
|---|---|---|
| `reconcile-thrash:<reason>` | `SPEC_ATTENTION{reason}` | the `SpecAttention(reason)` action that would append it |
| `attempt-loop:<task_id>` | `ATTEMPT_CREATED` for the task | `DispatchImplementer`/`ResumeAttempt` for the task |

**The report is the thing suppressed.** So once the watchdog engaged, no
qualifying event could ever be appended again, `newest_evidence` stayed
`None` on every pass, the streak froze at 0, and the project never
re-paused — for a condition that was actively, continuously worsening.
`notification-storm:total` was partially starved (some notification
sources survive suppression); `tick-error-streak` was unaffected
(nothing is suppressed for it). The reviewer is right, and this directly
contradicted my own docstring claim that "a condition that IS still
worsening still re-pauses".

### I checked the reviewer's premise rather than adopting it

The suggested direction — count "this pass suppressed an action for this
signal" as evidence — is what I implemented, but the reviewer's stated
*reason* needed verification, and it is sharper than they put it:

- `rules_attention.py` gates **every** `SpecAttention` branch on an
  "already open in the recent window" flag (`rejections_already_open`
  etc.), computed by `effects.spec_attention_recently_emitted` over the
  last **500 events**.
- `_apply_watchdog` reads the **same** 500-event slice, and detector (b)
  only reports `reconcile-thrash:<reason>` when a trailing run of **>5**
  `SPEC_ATTENTION{reason}` events sits **inside that window**.
- Therefore, whenever `reconcile-thrash:<reason>` is detected, the dedup
  flag is *necessarily* set, the rule *cannot* re-plan, and there is
  nothing to suppress.

So the new source is structurally silent in exactly the stale case B13
exists for — not by luck, by a window-identity argument. Note the
corollary the reviewer's phrasing missed: this also means their own
reproduction (a `SpecAttention('rejections')` re-emitted every pass) is
**not reachable through the real planner** — it requires monkeypatching
`plan_project`. That does not weaken the finding: the watchdog must not
depend on a dedup invariant living in another module, and the *same*
starvation is reachable through the real planner for `attempt-loop`
(dispatch rules have no such dedup). Both are now pinned.

### The fix

`fresh = would_suppress or (newest_evidence is not None and newest_evidence
> counted_evidence)`.

`would_suppress` is computed against **this pass's original action list**,
not the progressively-filtered one — two signals routinely come from one
condition (a 6× `SpecAttention` run trips both `reconcile-thrash` and the
per-reason `notification-storm`), and whichever ran first would otherwise
have eaten the only action the second could have seen, reporting "nothing
to suppress" purely from iteration order. This is also the reviewer's own
fallback hint ("computed before suppression runs").

Escalation and suppression remain ungated, unchanged.

### How I verified it no longer starves

Three new tests in `test_daemon.py`, all driving the **full** loop
(plan → suppress → next pass), not injecting events behind it:

- `test_watchdog_repauses_after_resume_while_the_planner_keeps_replanning`
  — reconcile-thrash. Asserts `run_pass` returns 0 actions every pass (the
  suppression really is active), that the ladder does **not** short-circuit
  (`N-1` passes → no pause), that pass `N` **does** pause with
  `drain-agents`, and — the load-bearing assertion — that the log still
  holds exactly **6** `SPEC_ATTENTION` events, i.e. the event-shaped source
  was silent throughout, so the test genuinely exercises the starvation.
- `test_watchdog_repauses_after_resume_while_dispatch_keeps_being_suppressed`
  — the same for `attempt-loop`, asserting no new `ATTEMPT_CREATED` ever
  landed (still exactly 7).
- `test_watchdog_suppression_evidence_stays_quiet_for_a_deduped_condition`
  — the anti-regression side, run against the **REAL planner** (no
  `plan_project` monkeypatch): records what the planner actually emits over
  `N*4` passes and asserts it never re-plans the acknowledged
  `SpecAttention`, **and** that the project is never re-paused. This pins
  the window-identity argument above as behaviour rather than prose.

**Non-hollow**: stashing only `daemon.py` + `watchdog.py` (tests kept), the
two F1 pins **FAIL** against the rejected HEAD, as does the strict pin. The
two safety pins pass on both, as they should.

Plus a pattern-space property in `test_invariants.py`
(`test_a_signal_the_watchdog_keeps_suppressing_still_reaches_the_threshold`)
asserting for **every** pattern that a suppressed-every-pass signal still
reaches the threshold — so a future fifth detector cannot inherit the
starvation.

## F2 — tests bypassed the coupling that breaks

- Both P49 pins now resume through `_operator_resume` (flag removed **and**
  `PAUSE_CLEARED` appended). A bare `unlink()` took B13's fail-open branch,
  so post-B13 they covered nothing.
- `test_watchdog_repauses_after_fresh_persist_cycles_if_condition_still_open`
  additionally now *makes* the condition genuinely open — one new
  `ATTEMPT_CREATED` per pass — instead of relying on a frozen log that
  `detect_runaways` merely keeps re-reporting. That reliance was the exact
  conflation B13 exists to end, so the pin had to supply real ongoing
  evidence to still mean what its name says. Neither change weakens it.
- The new suppression-loop tests above are the "system still produces
  evidence under the suppression regime" coverage that was missing.

## F3 / F4 / F5 / F6 / F7

- **F3**: `daemon.py` docstring cited `watchdog.streak_should_advance`
  (never existed) → `resume_baseline`, and the surrounding paragraph now
  describes the two-source rule.
- **F4**: the `[-500:]` fail-open lifetime is documented at the slice
  itself: it bounds how long a resume protects the project (~5 days at the
  measured 70–110 events/project/day, against a 7-day
  `HISTORY_REJECTION_WINDOW_SECONDS`), why fail-open-toward-armed is the
  safe direction, why the window cannot be widened without widening the
  detector's (they must be the same slice), and that the same window
  identity is what makes the new suppression evidence safe.
- **F5**: rather than adding a second dict, the resume marker and the
  counted-evidence sequence moved **onto** `_runaway_streak` as a frozen
  `_RunawayStreak` value. B13 now adds **zero** new unbounded-growth
  surface. The pre-existing never-pruned shape of that one dict is noted
  in place and left out of scope (its keys are bounded by the distinct
  `RunawaySignal.key`s a project has ever produced).
- **F6**: `last_resume_index` / `has_new_evidence_after` are now
  `_last_resume_index` / `_newest_evidence_after`, consistent with
  `_is_evidence_for`. `resume_baseline` is the only public addition.
- **F7**: trailing blank line removed from `test_daemon.py`.

## Judgement call — strict adopted

`resume_baseline` now returns the newest qualifying event's `sequence`
instead of a bool, and the daemon compares it against the sequence it has
already counted. One new event advances the streak **once**, not
`RUNAWAY_PERSIST_AFTER_CYCLES` times.

One scoping decision the controller should sanity-check: **strict applies
to the post-resume regime only.** With no `PAUSE_CLEARED` in the window the
advance stays unconditional. Applying strict there too would have broken
P44's own oracle
(`test_watchdog_persistent_runaway_auto_pauses_project_single_escalation`:
a frozen 7-event attempt-loop with `plan_project → []`, which under strict
would produce evidence on pass 1 only and never pause). That contract is
P44's, predates B13, and is not B13's to change — B13's mandate is "do not
re-climb on stale history *after an acknowledgment*". Flagging rather than
silently narrowing.

Strictness is pinned in both layers: `test_invariants.py`'s
`test_new_evidence_after_a_resume_advances_the_streak_exactly_once` (over
every pattern) and `test_daemon.py`'s
`test_watchdog_repauses_after_a_resume_when_genuinely_new_evidence_arrives`
(one event + `N*3` passes ⇒ still no pause; then one event per pass ⇒
pause).

## Gate (review round)

```
tester-unified: PASS (exit 0)   commit c90faf828fba3dc3b63ccd29ec4d9df6213eceb0
  R0 PASS   R1 PASS  100.0%, 83/83 changed executable lines across 4 source files
```

Green on the first attempt this round. Same host discipline: `docker ps`
checked for a live sibling lane before launching (none — the P106 lane had
finished), the container `docker update --cpus=3`'d immediately after
launch, one gate container on the host, ad-hoc pytest serial under
`nice -n 10`.

`watchdog.py` grew 395 → 426 lines, which stays **inside** its declared
inventory tolerance (`max(40, 10%) = 42`), so no further inventory
re-measure was needed this round.
