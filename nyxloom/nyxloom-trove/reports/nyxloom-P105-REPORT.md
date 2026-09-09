# nyxloom-P105 — B29: review-leg progress watchdog

**Branch** `nyxloom-P105` · **Worktree** `/workspaces/vbpub/.worktrees/nyxloom-p105`
**Backlog** B29 (`nyxloom-trove/4-backlog-inbox.md`) · **Origin** `nyxloom-trove/LESSONS.md` PL11
**Date** 2026-09-09

B26 (the sibling item in this package's original scope) was found already
shipped by an earlier program and removed from the inbox before this work
started. B29 was the whole package.

---

## 1. The defect, restated against this codebase

PL11's incident (P173 Topos review) is a review leg that is **loud and
useless**: it holds a lease, runs a live process, and writes transcript
continuously for ten-plus minutes, while producing no correction, no gate run
and no verdict — hundreds of kilobytes of delivery-runtime orchestration
overhead (todo/sign-off bookkeeping, capability routing, evidence-format
retries). A retry from scratch replayed the same broad setup.

Read against nyxloom's actual attempt-recovery ladder
(`src/nyxloom/rules_attempts.py`), that shape defeats **both** existing
liveness gates by construction:

| gate | where | why it cannot see this |
| --- | --- | --- |
| tier-1/tier-2 stall | ladder branch 5; `daemon._attempt_scan` log **mtime** + `_confirm_stall`'s `/proc` CPU signature | a reviewer writing stream-json every few seconds is never quiet and never CPU-flat, so it is never even a candidate |
| wall-clock cap | ladder branch 3; `policy.attempt_max_wall_seconds` = **10800s (3h)** | it does fire — three hours late, with no reason recorded and nothing salvaged |

So the gap is precise and real, not speculative. `daemon._resume_failures`
already carries the same lesson in the opposite direction ("do NOT score
progress by log size — the P26 bug this replaces scored a noisily-dying
session, stack traces and retry spam, as progress"); B29 is that lesson
applied to a *live* leg.

---

## 2. What was built

### 2.1 `src/nyxloom/review_progress.py` (new, pure)

PL11's four signals, with the rule that makes the detector work: **signal (a)
is not one of the three that count.**

- **(a) transcript growth** (`transcript_growing` / `transcript_bytes` /
  `transcript_records`) — the **discriminator**, deliberately not progress.
  Growth is exactly what hides this leg from the stall gate, so a leg whose
  transcript is *quiet* is handed back to the P14 ladder untouched (which has
  a CPU-signature tier-2 confirmation this module does not duplicate).
- **(b) branch/worktree commit activity** (`branch_advanced`) — progress.
- **(c) gate activity** (`gate_active`) — progress.
- **(d) concrete finding/verdict** (`finding_recorded`) — progress.

Any one of (b)/(c)/(d) makes a leg immune. Only a leg growing its transcript
while producing none of them can be stalled.

`branch_advanced` is **tri-state**, and the asymmetry is the safety argument:

- `None` = *structurally* unmeasurable (no baseline exists). Does **not**
  count as progress — treating it as progress would disable the detector
  permanently for exactly the legs PL11 is about.
- a *transient* git read fault is resolved the other way by the daemon
  (reports `True`, leg immune for that pass) — never interrupt on a signal
  that failed to read.

Public surface: `evaluate_leg`, `event_progress`, `attempt_start_sequences`,
`already_recorded`, `inspected_paths`, `compact_summary`, plus
`ReviewLegSignals` / `ReviewProgressBudget` / `REVIEW_ROLES` and the typed
constants `REVIEW_NO_CONCRETE_PROGRESS`, `TRIGGER_WALL_CLOCK`,
`TRIGGER_ORCHESTRATION_RECORDS`, `TRIGGER_BOTH`. Every function is pure — no
clock, no I/O, no imports beyond `.types` — the same contract `watchdog.py`
states for its detectors, and for the same reason.

### 2.2 The two budgets (`config.Policy`)

| key | default | why |
| --- | --- | --- |
| `review_progress_wall_seconds` | **1800** (enabled) | PL11's reviewer was already unproductive at ten minutes; 1800 is 6× **below** `attempt_max_wall_seconds`, so this can only ever fire *earlier* than the existing absolute backstop, never instead of a case it would have caught |
| `review_progress_max_records` | **0** (disabled) | mechanism built and tested; the *threshold* is not set — see §4.1 |

Both are `_POLICY_BOUNDS`-validated and in
`schemas/nyxloom-config.schema.json` (that object is
`additionalProperties: false`, so an unlisted key is a CFG1 error).

**On "orchestration turns."** Verified against the real dispatch model: there
is **no** per-attempt turn counter anywhere in nyxloom (`Usage` has none;
`CarveConfig.compact_after_turns` belongs to the carver-session subsystem and
counts `CARVER_SESSION_RESUMED` events; the chat subsystems count
`len(transcript)` for log filenames). The claude stream-json `result` line
*does* carry `num_turns` — and `adapters.extract_usage` already parses that
line and discards it — but that line only exists once the leg has **already
exited**, which is too late to budget against.

The smallest correct mid-flight counter is therefore **newline-delimited
records in the attempt log**: for a claude route the log is
`--output-format stream-json --verbose` (that argv choice exists precisely so
the log is written incrementally), so one line *is* one orchestration event.
It is named `transcript_records`, not "turns", because separating assistant
turns from tool traffic would mean JSON-parsing every line of a growing
multi-megabyte log on every 30s pass. Counting is a chunked binary
`bytes.count(b"\n")` scan.

### 2.3 Detection → typed interrupt (no new kill path)

New action `reconcile.MarkReviewStalled` (carrying the typed trigger **and**
the `ReviewLegSignals` the decision was made from), planned by a new ladder
branch inserted **between** branch 4 ("already STALLED") and branch 5 (the
quiet-log gate). It emits `REVIEW_PROGRESS_STALLED` + `ATTEMPT_STALLED`, and
then the ladder's **already-existing** branch 4 interrupts the leg on the next
pass through the same incident-hardened signal path every other interrupt
uses. That is P14 item 2's discipline (make a confirmed stall visible before
anything kills it) reused rather than re-litigated:

> **this package adds no second interrupt mechanism — only a new reason to
> reach the existing one.**

**A defect caught and fixed during implementation, now pinned as a regression
guard:** the first cut put the budget check *inside* the branch's own `elif`
condition. A review leg the daemon merely *measured* then consumed the branch
whether or not it stalled — silently disabling the P14 quiet-log stall path
for every review leg on the project. The trigger is now computed **before**
the chain (`_review_stall_trigger`), and
`test_a_measured_but_clean_review_leg_still_reaches_the_quiet_log_gate` fails
against the broken form (verified, §5).

### 2.4 The compact controller summary

`REVIEW_PROGRESS_STALLED` (new `EventType`, audit-only, registered in
`test_invariants.KNOWN_IGNORED_EVENT_TYPES` — its state consequence arrives
via the `ATTEMPT_STALLED` appended alongside, the same shape as
`MERGE_REVERTED`). Deliberately **not** named `ATTEMPT_*`: that prefix is a
projection branch (`projection.py`) and a trace-grouping key
(`handoff_trace.py`).

Payload = `review_progress.compact_summary(...)`: fixed fields only — ids,
enum values, counts, budgets, the four signals, and `inspected_paths`.

`inspected_paths` is the half that carries real reviewer knowledge forward
(so a restart is seeded from it instead of replaying the transcript). It is
derived **structure** — tool-call `file_path`/`path`/`notebook_path`
arguments walked out of stream-json records — never the reviewer's prose,
which is why it can be persisted without inheriting the transcript's trust
problem. Bounded in both directions (40 paths, 200 chars each), control
characters rejected outright, sorted for determinism, unparsable lines
skipped (a partially-flushed final line is the *normal* state of a log
belonging to a running process).

The transcript read happens **at the effect boundary**, and **once per
stalled leg** — not on every 30s pass. An unreadable transcript degrades to
an empty path list; losing the salvage half must never cost the detection
half.

### 2.5 Wave-review correctness (found by reading, not by test)

A wave review is **one** `Attempt` spanning N member tasks, and the ladder
walks per task — so one stalled leg plans N `MarkReviewStalled` actions in a
single pass. Two consequences, both handled:

- **measurement** is grouped by *attempt*, and (b)/(c)/(d) are **OR-ed across
  members**: a leg that ran a gate or landed a correction on *any* member is
  immune as a whole. Judging members in isolation would stall a leg that is
  demonstrably getting somewhere on the strength of its quietest task.
- **the audit event** is recorded **once per leg**
  (`review_progress.already_recorded`, an event-log read in the established
  `spec_attention_recently_emitted` debounce idiom, so a daemon restart
  cannot resurrect the duplicate); the **state mark** stays per member
  statefile, because each member has its own statefile to update.

### 2.6 `pre_review_sha` made unconditional (`effects_review.launch_review`)

It was gated on `cfg.policy.reviewer_repair` because a repair's blast radius
was its only consumer. B29 gives it a second: signal (b) is measured against
exactly this sha, and without it that signal is *permanently* unmeasurable on
any project with `reviewer_repair` off. Cost is one `rev-parse` per member
per **review**, not per pass.

### 2.7 Dashboard visibility (B26's Processing Trace)

New `handoff_trace` leg kind **`review-stall`**: `outcome` is the typed reason
(`review-no-concrete-progress`), `detail` carries the trigger, elapsed,
transcript extent, the three progress signals and an inspected-file **count**
(the path list stays in the event — a forty-entry list would drown the flat
`k=v` row). `summary` is `None`: no transcript prose reaches the rendered
table. `render.py`'s per-task page picks this up with no change beyond its
docstring, because it renders whatever `build_trace` returns.

This is the point PL11 makes about visibility: the attempt row alone cannot
distinguish a review that reached a verdict from one that burned its budget
saying nothing.

### 2.8 Files touched

```
new  src/nyxloom/review_progress.py                     (405 lines, pure)
new  tests/test_review_progress.py                      (43 tests)
     src/nyxloom/types.py            EventType.REVIEW_PROGRESS_STALLED
     src/nyxloom/config.py           2 Policy fields
     src/nyxloom/schemas/nyxloom-config.schema.json
     src/nyxloom/reconcile.py        MarkReviewStalled + ReconcileInput.review_leg_signals
     src/nyxloom/rules_attempts.py   _review_stall_trigger + ladder branch 5
     src/nyxloom/planning.py         attempt-ladder RuleSpec.emits
     src/nyxloom/effects_lifecycle.py mark_review_stalled + _review_stall_summary + spec
     src/nyxloom/effects_review.py   pre_review_sha unconditional
     src/nyxloom/daemon.py           _review_leg_signals / _review_branch_advanced /
                                     _pre_review_sha / _transcript_extent + wiring + bounds
     src/nyxloom/handoff_trace.py    review-stall leg
     src/nyxloom/render.py           docstring
     tests/test_invariants.py        KNOWN_IGNORED_EVENT_TYPES
     tests/test_effects.py           key-builder sample
     nyxloom-trove/reports/CORE-REDESIGN-OWNERSHIP-INVENTORY-2026-08-02.md
```

---

## 3. The `watchdog.py` decision — separate module, and why

**Decision: a NEW module, not a new signal source feeding `watchdog.py`.**
Checked against what that module actually models rather than assumed either
way.

`watchdog.py` detects **repeating patterns over a window of one project's
recent event log** — notification storms, reconcile thrash, attempt loops,
tick-error streaks — and its remedy is **project-wide**: escalate
`NEEDS_OPERATOR{reason:'runaway'}`, suppress the repeating action,
auto-pause. Its four detectors share one shape: *count/adjacency over
`Event`s*, keyed by a stable `RunawaySignal.key` that `_is_evidence_for` and
`_suppress_runaway_action` both re-parse.

B29 is a different animal on all three axes:

| | `watchdog.py` | `review_progress.py` |
| --- | --- | --- |
| unit | a project's recent event window | **one in-flight attempt** |
| inputs | events only | events **plus facts that are not in the event log at all** — attempt-log byte/record extent, task-branch HEAD |
| remedy | project-wide escalate / suppress / auto-pause | a **per-attempt** mark that hands off to the existing ladder |

Folding it in would also have meant editing a contract the module's own
docstring declares **frozen** ("INTERFACE CONTRACT (frozen except for CR-16's
(d))"), which B13/nyxloom-P104 respected by adding `resume_baseline`
*beside* — explicitly "ADDITIVE — WatchdogConfig, RunawaySignal and
detect_runaways are untouched, byte-for-byte".

**Shared plumbing checked for reuse, and the honest answer is that none
applies:** every `watchdog.py` helper (`_last_resume_index`,
`_is_evidence_for`, `_newest_evidence_after`) keys off parsing
`RunawaySignal.key`, which B29 does not produce. What *is* reused is the
**convention**, deliberately and visibly: pure detector + separate impure
daemon measurement + typed non-prose keys + `RuleSpec`/`HandlerSpec`
registration. Two of `watchdog.py`'s stated design rules are cited directly in
the new module (sequence-over-timestamp ordering; ids/enums/counts only in
anything that reaches a payload).

The ownership-inventory row records this decision so it does not have to be
re-derived.

---

## 4. Deliberately deferred, with forcing functions

### 4.1 `review_progress_max_records` default stays 0 (disabled)

The mechanism is fully built, wired and tested; only the *threshold* is
unset. No measured distribution of healthy-review transcript-record counts
exists in this project, and enabling a guessed floor is how a healthy
reviewer gets killed — the "declaration without an oracle" mistake this
program has caught before. Same opt-in convention as
`test_health_interval_days` / `gap_audit_after_changed_lines` /
`mutation_gate`.

**Forcing function:** every stall the *wall-clock* budget records carries
`transcript_records` in its summary. A handful of real
`REVIEW_PROGRESS_STALLED` events is exactly the distribution needed to set
this number, and the code that produces them is live by default.

### 4.2 PL11 item 5 — "suppress delivery-profile subagents / capability bookkeeping for a bounded review leg"

**Not buildable here today, verified rather than assumed.** `grep -rn
'subagent\|delivery.profile\|dispatch_profile' src/nyxloom/` returns
**nothing**: nyxloom has no subagent-expansion or capability-bookkeeping
concept at all. PL11's incident ran under a *different* delivery runtime
(Reasonix/DeepSeek Pro). There is nothing in this codebase to suppress.

The natural vehicle if there were — a standing instruction on the
`REVIEW_INDEPENDENT` dispatch prompt — is **blocked by a measured ceiling**:
`adapters.build_dispatch`'s reviewer prompt is ~1369 chars against a pinned
regression cap of 1400 and `argv_max` 1500
(`test_review_independent_prompt_stays_under_argv_max_with_real_paths`, whose
docstring records that overflowing it once stranded every review dispatch).
Optional appends there are already being skipped for realistic paths.

**Forcing function (two, either suffices):** (a) an adapter/route is added
that expands a review dispatch into subagent workflows — the suppression then
belongs in *its* `build_dispatch` branch; or (b) the reviewer prompt gains
argv headroom, either by raising `argv_max` on the review routes or by moving
standing instructions into the review **packet file** (which the prompt
already references by path) instead of argv.

### 4.3 "Escalate to AI to determine the next action" / restart-with-hints / model-or-tier switch

Built as the **first cut the backlog entry allows**: the compact summary plus
the typed reason **is** the escalation artifact. It is durable, replayable,
visible on the dashboard trace, and structured for a consumer.

What is *not* built is an automated consumer that reads the summary and
adapts the relaunch. Two blockers, both real:

1. **Restarting *from* the summary hits the same argv ceiling as §4.2** — the
   relaunch path is a cold `adapters.build_dispatch`, and a bounded summary
   append would be skipped for realistic paths exactly like
   `review_focus`/DRY already are.
2. **`effects_attempt.resume_attempt` builds a generic prompt** (`f"Resume
   {task_id} attempt {attempt_id} in {worktree}"`) and, for a review leg
   whose `attempt.worktree` is `None`, resumes at repo root having lost the
   packet pointer *and* the A7 attempt-id verdict binding. Wiring a
   summary-seeded restart onto that path would need it repaired first — its
   own package, not a rider on this one.

**Forcing function:** the same packet-file-vs-argv decision as §4.2, plus a
repair of the review-leg resume prompt. Both are pre-existing gaps this
package documented rather than created.

### 4.4 "Residual finding" as the reviewer's actual in-flight partial finding

The summary records the residual finding **state** — the four signals, all
negative on (b)/(c)/(d) by construction at the moment of the stall, which is
the load-bearing claim (nothing of the leg's output needs honouring; only its
*reading* is worth carrying). It does **not** extract the reviewer's
half-formed finding *text*, which would mean parsing and trusting agent prose
mid-transcript.

**Forcing function:** a structured in-flight finding marker — the same
convention `adapters.classify_log_tail` already parses for `BLOCKED:` and
`SCOPE_AMENDMENT_REQUEST:`. Adding a `FINDING:` marker is cheap *mechanically*
and pointless *today*, because instructing the reviewer to emit one runs
straight into §4.2's argv ceiling. Same unblocker.

### 4.5 Noted, not fixed (pre-existing, out of scope)

- `attempt.started` is never refreshed on resume, so `elapsed_seconds` is the
  attempt **record's** total life, not the current leg's. Harmless for a
  review leg (rarely resumed) and consistent with how
  `_wall_clock_cap_exceeded` already reads the same field.
- A wave review's wall-clock cap is taken from whichever member task the
  ladder is iterating, so a *task*-authored `budget.max_wall_seconds` can
  govern the reviewer. B29's budget is policy-level and has no such
  ambiguity; the pre-existing branch-3 quirk is untouched.

---

## 5. Tests — and proof they are not vacuous

`tests/test_review_progress.py`, **44** tests across four layers (plus one in
`tests/test_config_ui.py`, §5.1): the pure
detector (one test per signal, each flipping exactly **one** field of a shared
stalled baseline), the event scan, the daemon measurement half, the ladder,
the effect, and the trace.

Per this project's own discipline (a past round here caught a test seeded with
the wrong event type, making its central assertion trivially true), **five
targeted mutants were introduced and each confirmed to fail the suite**:

| mutant | broken behaviour | caught by |
| --- | --- | --- |
| 1 | drop the `transcript_growing` guard | `test_a_quiet_leg_is_left_to_the_stall_ladder` |
| 3 | `event_progress` ignores `since_sequence` | `test_a_gate_from_before_this_leg_started_is_not_its_progress` |
| 4 | the original ladder bug (branch consumes the `elif` for any *measured* leg) | `test_a_measured_but_clean_review_leg_still_reaches_the_quiet_log_gate` |
| 5 | drop the wave audit dedup | `test_a_wave_review_records_the_audit_summary_once_but_marks_every_member` |
| 6 | `branch_advanced=None` counts as progress | `test_an_unmeasurABLE_branch_is_not_read_as_progress` + 5 others |

All five reverted; suite green. Several tests additionally carry an explicit
**negative** in the same test body (the same event *after* the boundary does
count; a `False` baseline is a different answer from `None`; a trace without
the event carries no such leg) so no assertion can pass for the wrong reason.

### 5.1 A latent drift caught by self-review, and pinned

`render._EDITABLE_POLICY_KEYS` states in its own comment that it is "the
render-side copy" of `daemon._POLICY_BOUNDS`' key set (render.py has no
import on daemon.py), and the two were in **exact** correspondence before
this package — with **nothing** enforcing it. Adding the two budgets to
`_POLICY_BOUNDS` alone broke it silently: the server would accept and
validate two knobs the dashboard gave an operator no way to reach.

Both keys now appear in the form, and
`test_the_dashboard_form_and_the_server_bounds_name_the_same_policy_keys`
(`tests/test_config_ui.py`) pins the correspondence in both directions —
verified against the broken state, where it names the drifted keys. Commit
`3232a0ff`.

---

## 6. Gate

`run-gate tester-unified` (assay lane, `rigor = ["R0","R1"]`,
`fail_under = 100.0` changed-line coverage against `origin/main`),
run from the worktree's `nyxloom/` directory, verdict read from
`.assay/verdict-tester-unified.json` in a separate step.

**Result: PASS.** `run-gate` exit 0; verdict read from
`.assay/verdict-tester-unified.json` in a separate step:

```
outcome        PASS          exit_code 0
lane           tester-unified   scope S1   rigor [R0, R1]   enforcement gate
commit         3232a0ff607f43af924cb7028f72046dfb71b0ca
judge          assay 6.0.0 (zipapp, sha256 43fffa70…), verdict schema 11
claim R0       PASS   verified_by_assay=true
claim R1       PASS   verified_by_assay=true
  changed-line coverage   384 / 384 = 100.0 %   (fail_under 100.0)
  files considered        11        files_missing_coverage []
  unclassified {}   excluded {}      (allow_excluded=false, require_branch=false)
base           996048ac  (base_resolution: merge-base)   source_roots ["src"]
argv_modified  false
```

`unclassified_lines {}` and `excluded_lines {}` are worth stating rather than
skipping: an empty denominator or a silently-excluded changed line are exactly
the two ways a 100% changed-line reading can be vacuous (PL12, and B30's
"0/0 = 100.0%" trap). Neither applies here — 384 changed executable lines were
measured and all 384 covered, across 11 files.

Independently corroborated before the gate by a local reconstruction of the
same judgment (`git diff -U0` against the base ∩ `coverage.json`), which
reported 253/253 = 100.0% over `src`. The two numbers differ because the local
check diffed against `origin/main` two-dot while assay resolves the base by
**merge-base**; both agree on the only thing that matters, that no changed
executable line is uncovered.

Host discipline observed throughout: load checked before every run, pytest
under `nice -n 10 ionice -c2 -n7`, and the gate deliberately **deferred**
while another agent's `tester-unified:local` container was live (only one gate
container at a time across the host).
