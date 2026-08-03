# Core redesign source and test ownership inventory

Date: 2026-08-02 (mechanical contract added 2026-08-03, CR-00 review)

Purpose: planning input for the core-redesign reviews. This is deliberately not
a product gate and sets no size limit. It records the surfaces whose ownership
must be explicit while the control plane is decomposed.

Snapshot: `8578cbfa`, measured with `wc -l` in this checkout. Sizes are signals
for review allocation, not estimates of difficulty and not proof of coupling.

Re-measured 2026-08-03 (CR-15 review) for the three rows CR-15 moved past the
size tolerance, plus the one module it ADDED to the control-plane import
closure. `control_auth.py` was missing entirely: CR-15 imported a new module
into `daemon.py` without declaring an owner, which is exactly the omission rule
2 exists to catch, and the closure test had been failing on that branch since
the module landed. The rest of the table is unchanged and unre-measured, which
is the point of a tolerance.

## Mechanical contract (enforced by `tests/test_core_characterization.py`)

This document is checked by tests, so a reader editing it knows what fails and
why. The rules are deliberately structural, never line-exact:

1. **Every path named in a table must exist.** A rename or deletion that leaves
   this document behind fails.
2. **Membership is the control-plane import closure, computed, not listed.**
   The test parses `daemon.py` and `reconcile.py` with `ast` and requires every
   `src/nyxloom/*.py` module they import directly to appear in a source table.
   A new control-plane dependency therefore fails until someone assigns it an
   owning package. Modules outside the closure (`cli.py`, `doctor.py`,
   `storage_sqlite.py`) may be listed as well; the closure is a floor, not a cap.
3. **Sizes are checked with a tolerance, not for equality**: recorded vs actual
   may differ by 10% or 40 lines, whichever is larger. Ordinary edits do not
   churn this file; real drift fails visibly and is re-measured, not silenced.
4. **A surface owned by a rewrite package (CR-05, CR-06, CR-07) must have its
   test module's retirement handling declared** in the test table whenever
   `tests/test_<module>.py` exists. This is the amendment's 5.2 test-retirement
   policy made mechanical, so no rewrite package meets it by improvisation.

## Primary control surfaces

| Surface | Lines | Present responsibility | Owning redesign package(s) | Review treatment |
| --- | ---: | --- | --- | --- |
| `src/nyxloom/daemon.py` | 7,641 | Effect execution, process/gate/git/HTTP orchestration, shared mutable daemon state | CR-05, CR-13a, CR-15, CR-16 | Frontier implementation and review; split by effect boundary, never by arbitrary line ranges |
| `src/nyxloom/effects.py` | 714 | CR-05 (added 2026-08-03, CR-05a): the effect boundary itself — the injected ports (clock, processes, git, filesystem, background work, event log, journal), the per-action context, and the handler registry that gives every action type exactly one owner. Imports no other control-plane module, which is what lets an effector be exercised without constructing the shell. CR-05b lowers `LEGACY_HANDLER_BUDGET` to zero as it moves the remaining families; raising it is a review-rejectable change, not a merge-conflict resolution |
| `src/nyxloom/effects_gates.py` | 473 | CR-05 (added 2026-08-03, CR-05a): the gate-verify cadence and post-merge validation, including BOTH background-work registries the amendment's §3.3 requires to leave the shell. CR-12 owns making the gate result typed product evidence; CR-16 adds liveness on the same drain seam |
| `src/nyxloom/effects_dispatch.py` | 183 | CR-05 (added 2026-08-03, CR-05b): the launch primitives the four agent-dispatching families share — execute-time admission, pause mode, budget, gate hint, handoff frontmatter, mutex leases, approved scope amendments. Plain FUNCTIONS over the effect context, deliberately not a base class: inheritance would put "may this launch happen" on something an effector could override. CR-05c and CR-05d consume it as they move; CR-13a replaces the admission predicate with a minted token the wrapper requires |
| `src/nyxloom/effects_review.py` | 597 | CR-05 (added 2026-08-03, CR-05b): the review wave and gate-failure diagnosis dispatch, plus the committed-report readers (`review_report_text`, `parse_reject_class`) that carry routing hints and NO merge authority. The verdict CONSUMPTION half is CR-05c's, with the attempt lifecycle, because what an exit means depends on the attempt's role rather than on which effector dispatched it |
| `src/nyxloom/effects_merge.py` | 249 | CR-05 (added 2026-08-03, CR-05b): guarded-automatic merge — a real 3-way `merge --no-ff` in a disposable scratch worktree, gates run on the MERGED tree before publication, and a compare-and-swap `update-ref` so a concurrent merge is never clobbered. CR-12 makes the merge record typed product evidence |
| `src/nyxloom/effects_lifecycle.py` | 301 | CR-05 (added 2026-08-03, CR-05a): task/attempt lifecycle bookkeeping, wave opening, spec-attention escalation, and the provider backoff registry — owned by the effector that writes it and injected into the input builder that reads it |
| `src/nyxloom/reconcile.py` | 2,302 | Pure planning, lifecycle routing, attempt recovery, wave scheduling | CR-06, CR-07, CR-09, CR-11 | Differential old/new planner comparison; classify touched tests before retirement |
| `src/nyxloom/types.py` | 692 | Persisted state, transition graph, events, serde | CR-03, CR-07 | Frozen until the package explicitly owns the migration; map every current transition to kernel or compiler |
| `src/nyxloom/storage.py` | 172 | File/SQLite dispatch, event projection API | CR-04 | Delete file path and selector; preserve validation semantics with replay/export proof |
| `src/nyxloom/projection.py` | 246 | CR-04b (added 2026-08-03): the pure event validation and projection both the public store API and the store implementation depend on. Extracting it is what removed the import cycle the CR-04 contract names; it does no I/O, which is what lets the store call it INSIDE a transaction against committed state. CR-07 changes the transition rules it encodes and must keep it pure |
| `src/nyxloom/storage_sqlite.py` | 513 | SQLite event/projection implementation | CR-04 | Atomic append/projection ownership, versioning, backup/export/re-import |
| `src/nyxloom/stages.py` | 370 | Seven-stage menu, stage ownership, preset closure | CR-07 | Replace menu composition with compiled workflow IR only after semantic parity proof |
| `src/nyxloom/wrapper.py` | 690 | Child launch, receipt publication, child environment | CR-13a, CR-13b | Container boundary and secret injection; receipt contract remains owned by CR-03 |
| `src/nyxloom/adapters.py` | 1,161 | Provider argv/prompt and usage adapters | CR-08, CR-10, CR-13a | Route selection must not remain at adapter call sites; preserve argv-budget tests |
| `src/nyxloom/render.py` | 2,526 | Dashboard/operator rendering | CR-14 | Consume trace/evidence projections; do not derive authority from presentation data |
| `src/nyxloom/cli.py` | 2,114 | Operator and recovery commands | CR-01, CR-04, CR-14, CR-15 | Keep state-changing paths on the same authoritative store/evidence rules |

## Supporting boundaries

| Surface | Lines | Package ownership / constraint |
| --- | ---: | --- |
| `src/nyxloom/results.py` | 740 | CR-03 (added 2026-08-03): the typed agent-result envelope, its fail-closed reader, and the agent-authored judgement half. It is the ONLY place an agent's judgement becomes authoritative, which is why it imports nothing from the package and reads nothing it was not handed. CR-09 extends its DeclineReason vocabulary when it adds decline corroboration; CR-07 gives `workflow_digest`/`prompt_digest` real values. Neither may relax the reader |
| `src/nyxloom/exception_census.py` | 256 | CR-02b (added 2026-08-03): the broad-exception classification vocabulary, the structural fan-in derivation, and the per-module legacy budget. Pure AST reads; imports nothing from the package. Every other CR package interacts with it in ONE direction only -- by lowering its own module's budget as it classifies or retires handlers. Raising a budget is a review-rejectable change, not a merge conflict resolution |
| `src/nyxloom/snapshot.py` | 580 | CR-02 (added 2026-08-03): the typed snapshot-input vocabulary and the authoritative/advisory fan-in. Pure -- it imports no other package module and reads nothing. Owned by CR-02 alone; the later handler-registry and planning-rule packages CONSUME its `SnapshotAudit` (carried on `ReconcileInput`) instead of re-deriving authority classification per call site, which is a dependency on this surface, not shared ownership of it |
| `src/nyxloom/config.py` | 693 | CR-01, CR-08, CR-13b: instance configuration remains a boundary; workflow documents do not become arbitrary code |
| `src/nyxloom/lint.py` | 1,112 | CR-01: document-truth contradiction rule is a standing gate, not a cleanup script |
| `src/nyxloom/doctor.py` | 609 | CR-02, CR-04, CR-16: authority/snapshot and liveness fault reporting |
| `src/nyxloom/notify.py` | 429 | CR-16: health alarms need an independent escape path, not only this transport |
| `src/nyxloom/leases.py` | 114 | CR-05: injected effect port; no effector may reach through `Daemon` for lease state |

## Rest of the control-plane import closure

Modules `daemon.py` or `reconcile.py` import directly. They are smaller than the
surfaces above and mostly stable, but each is reachable from the control plane,
so each needs a named owner before that plane is rewritten around it.

| Surface | Lines | Package ownership / constraint |
| --- | ---: | --- |
| `src/nyxloom/merge_digest.py` | 558 | CR-12, CR-14: merge evidence is a typed product record, not rendered prose; digests feed evidence, never authority |
| `src/nyxloom/decision_chat.py` | 552 | CR-09: the human-decision escape path a band-5 decline lands on; its transport must stay independent of route health |
| `src/nyxloom/decisions.py` | 415 | CR-09, CR-11: `decisions_open` is a planner input; the open/resolved projection must survive the store rewrite unchanged |
| `src/nyxloom/intake_chat.py` | 423 | CR-01: intake writes handoff documents; document authority rules apply to what it produces |
| `src/nyxloom/gate_canary.py` | 402 | CR-02, CR-12: a gate that cannot be proven to reject is an advisory input, never authoritative evidence |
| `src/nyxloom/control_auth.py` | 469 | CR-15: the control plane's trust root. Owns the credential store, the operator identity that becomes an event `Actor`, the shared audited-refusal helper, and the notification channel's closed-by-default posture. CR-05 may move the handlers that call it; the auth-before-body/target boundary and the single refusal shape move with them, never around them |
| `src/nyxloom/commands.py` | 451 | CR-05, CR-16: operator chat-ops are effects; they must route through the same effect boundary and health alarm. CR-15 (2026-08-03) made its mutating verbs an authenticated ingress: they resolve a named channel operator before any project lookup and refuse otherwise |
| `src/nyxloom/log.py` | 372 | CR-14: structured logging is the trace substrate. Reserved-key traps (`event=`, `level=`) are documented in `nyxloom-trove/DOCTRINE.md`; renaming a field is a behavioural change |
| `src/nyxloom/backlog_items.py` | 324 | CR-12: auto-tick on merge is product evidence; it must read the typed merge record, not re-parse markdown |
| `src/nyxloom/carver_session.py` | 291 | CR-06, CR-07: the carver session projector is planner input; keep it pure when the planner is rewritten |
| `src/nyxloom/frontmatter.py` | 281 | CR-01, CR-07: handoff parsing is the workflow compiler's front end; schema changes land here with the compiler, not before |
| `src/nyxloom/watchdog.py` | 201 | CR-16: the runaway backstop must remain independent of the engine it watches |
| `src/nyxloom/findings.py` | 207 | CR-14: advisory system-to-user channel; never an authority input |
| `src/nyxloom/leases.py` (see above) | 114 | CR-05 |
| `src/nyxloom/gate_runner.py` | 110 | CR-02, CR-12: shared gate execution primitive; its result is typed evidence bound to a commit |
| `src/nyxloom/paths.py` | 95 | CR-04: state layout. Frozen through the store rewrite except by an explicit migration contract |
| `src/nyxloom/doc_lifecycle.py` | 153 | CR-01 (new, 2026-08-03): archive-containment model (`is_archived`) shared by `lint.py` (CFG4/L7/ARC1) and `daemon.py`'s carve-context assembly; a path-only check, never reads archived content to decide exclusion |

## Existing test pressure and retirement policy

| Test surface | Lines | Current character | Required handling in CR-05 to CR-07 |
| --- | ---: | --- | --- |
| `tests/test_daemon.py` | 7,082 | Largest structure-mirroring and behavior mix | Classify each touched test. Keep observable event/state/artifact oracles; delete only tests coupled to removed private shape, naming them in the package report. |
| `tests/test_reconcile.py` | 4,343 | Planner behavior and structure mix | Retain semantic action/output tests; use the CR-00 corpus plus differential planner output as the migration boundary. |
| `tests/test_behavioral.py` | 1,136 | Real daemon/fake-agent integration behaviors | Behavior oracle. Do not retire it merely because the executor shape changes. Its one synchronous-dispatch seam is a fork replacement, not a behaviour change: keep the real-fork `_tick` path covered by the other tests in the file. |
| `tests/test_adapters.py` | 2,619 | Adapter boundary, prompt construction and argv budget | Keep role/route boundary tests, especially realistic-path argv-limit behavior. |
| `tests/test_wrapper.py` | 1,394 | Child-launch and receipt boundary | Behavior oracle for CR-03 and CR-13a; expand containment checks rather than mirroring a new implementation. |
| `tests/test_stages.py` | 313 | Registry/closure invariants for the stage menu CR-07 replaces | Structure mirror of a mechanism being replaced. Its closure invariants (no dead-end, single ownership, terminal reachable) must be re-expressed against the compiled workflow IR before the menu is deleted; the file itself retires with the menu, named in the CR-07 report. |
| `tests/test_types.py` | 187 | Transition-graph and serde invariants | Behavior oracle. The lifecycle/node split (CR-07) may move members, never delete a proven invariant: every transition legality and serde round-trip assertion migrates with the type it covers. |
| `tests/test_carver_session.py` | 464 | Session projector behaviour | Behavior oracle. The projector must stay pure through CR-06; these tests move with it and are not rewritten to match a new planner shape. |
| `tests/test_control_auth.py` | 1,722 | Control-plane trust boundary: credential store, auth-before-body/target ordering, refusal indistinguishability, request framing, and the structural route census | Behavior oracle, kept whole. CR-05 moves the handlers, not the boundary: its route census and auth-ordering tests read `daemon.py` with `ast`, so they must be re-pointed at the new registry and keep their verdicts. No assertion here retires because a handler moved; one may only be amended with the reviewed evidence that the property it names still holds somewhere. |
| `tests/test_commands.py` | 843 | Operator chat-ops surface | Behavior oracle for the operator contract. CR-05 may re-route the effects underneath; the observable command-to-effect mapping asserted here must survive unchanged — including CR-15's closed-by-default mutating verbs and their audited refusals. |
| `tests/test_frontmatter.py` | 453 | Handoff parse/serde boundary | Behavior oracle. CR-07 extends the schema; every existing parse/reject assertion keeps its verdict, and new workflow fields are additive. |
| `tests/test_leases.py` | 179 | Lease acquire/release semantics | Behavior oracle. CR-05 injects leases as a port; capacity and race semantics asserted here are the port's contract. |
| `tests/test_core_characterization.py` | new | Cross-package semantic corpus | Keep through the entire program. New implementations must preserve active cases or explicitly amend them with reviewed evidence. |
| `tests/test_snapshot.py` | new | Snapshot-descriptor vocabulary, redaction, ordering, digest, payload replay | Behavior oracle owned by CR-02. Pure unit tests over `snapshot.py`; they do not mirror daemon structure and survive the control-plane rewrite unchanged. |
| `tests/test_snapshot_faults.py` | new | Authoritative/advisory fault matrix driven through the real reconcile pass | Behavior oracle owned by CR-02. It asserts effects and events, never helper calls, so CR-05/CR-06 must keep it green as written; a case that has to be relaxed is a behavioural regression, not a shape change. |
| `tests/test_results.py` | new | Typed result envelope: identity binding, discriminated verdicts, decline validity, evidence binding | Behavior oracle owned by CR-03, and a standing gate on the packages that extend it. CR-07 gives `workflow_digest`/`prompt_digest` real values and CR-09 extends the decline vocabulary; both ADD cases here. No existing rejection case may be relaxed -- every one of them is an acceptance criterion, so a case that has to be weakened is a behavioural regression, not a shape change. |
| `tests/test_effects.py` | new | The effect boundary: port behaviour, registry completeness and duplicate rejection, the structural `emits` derivation, the legacy-handler ratchet, and the no-`Daemon`-reference rule | Behavior oracle owned by CR-05, and a standing gate on CR-05b and CR-16. Every rule here is seen to FAIL as well as pass, over synthetic registries — a registry oracle that has never rejected a duplicate is not known to reject one. CR-05b lowers the legacy budget and may not raise it; CR-16 registering a new background family must add its drain and declare its `emits` here. |
| `tests/test_effects_dispatch.py` | new | The shared launch primitives: admission in both directions, pause modes, budget, and the advisory degradations that shape a prompt | Behavior oracle owned by CR-05. The admission cases ARE the acceptance criterion for "a guard evaluated only at plan time is a guard with a window" -- CR-05c and CR-05d add their launch kinds here, and CR-13a replaces the predicate with a minted token without relaxing any case. |
| `tests/test_effects_review.py` | new | Committed-report reading (documented path, broadened search, closed reject-class vocabulary), gate-failure evidence selection, warm-session reuse, and the wave lease union | Behavior oracle owned by CR-05. The reject-class vocabulary is closed on purpose: a case that has to be relaxed to admit a new class is a routing change, not a shape change, and belongs to the package that adds the class. |
| `tests/test_effects_merge.py` | new | The guarded merge's REFUSAL paths: no recorded branch, unresolvable root, refused worktree, real conflict, and a compare-and-swap the ref lost | Behavior oracle owned by CR-05. Every case asserts the task is left where an operator can rescue it; a case that has to be weakened is a publication-safety regression. The happy path stays covered by `tests/test_auto_merge.py` against a real repository. |
| `tests/test_effect_differential.py` | new | The amendment's §5.1 differential: the moved families' event sequences, recorded from the pre-CR-05a tree and replayed against the new one | Behavior oracle owned by CR-05. The fixture is the behavioural contract, not a snapshot to refresh: re-recording is opt-in, and the diff must be explained in the package report. CR-05b extends the scenario list as each family moves; a scenario may only leave it when its effect leaves the daemon. |
| `tests/test_exception_census.py` | new | Broad-exception classification: fan-in fully classified, legacy budget never exceeded and never stale | Behavior oracle owned by CR-02b, and a standing gate on every later package. CR-05/CR-06/CR-07 will move most of `daemon.py`; the budget is per module precisely so that move lowers a number rather than rewriting a registry. |

## Ownership rules for reviewers

1. CR-05 effectors own their background-work registries and injected ports; none retains a
   `Daemon` reference.
2. CR-06 owns only pure planning rules; clock, filesystem, subprocess, environment and logger
   access remain outside it.
3. CR-07 owns workflow ordering and node transitions; kernel lifecycle legality remains in
   `types.py`/storage validation until explicitly migrated through a reviewed schema change.
4. CR-09 owns capability-band policy only after CR-13a containment is proven. Free/untrusted
   routes remain disabled before then.
5. The characterization fixture is a semantic contract. It is not evidence that an arbitrary
   unlisted historical event sequence is unchanged; CR-05 through CR-07 add differential
   verification for that wider population.

## Current baseline limits

The active corpus characterizes the seven shipped stage menu, projection semantics, waits and
planner outputs that are stable enough to compare across the first refactors. It deliberately
does not claim that the current `TaskState` enum is the target architecture.

Two limits are recorded rather than papered over, and both are machine-checked as
`known_gaps` in `tests/fixtures/core_characterization_v1.json`:

- a queued task with no healthy route neither progresses nor parks visibly (owner CR-08);
- an auto-merge conflict is re-planned every pass with no backoff and no durable
  wait state (owner CR-05).

The five-band ladder fixtures are **inventory, not tests**: `executable: false`, with a
per-item activation contract naming the production vocabulary CR-09 must add and the exact
retirement step. `test_future_band_inventory_activates_when_production_vocabulary_lands`
fails the moment that vocabulary exists, so the inventory cannot survive as decoration.
