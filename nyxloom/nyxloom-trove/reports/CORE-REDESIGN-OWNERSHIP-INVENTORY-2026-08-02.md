# Core redesign source and test ownership inventory

Date: 2026-08-02 (mechanical contract added 2026-08-03, CR-00 review)

Purpose: planning input for the core-redesign reviews. This is deliberately not
a product gate and sets no size limit. It records the surfaces whose ownership
must be explicit while the control plane is decomposed.

Snapshot: `8578cbfa`, measured with `wc -l` in this checkout. Sizes are signals
for review allocation, not estimates of difficulty and not proof of coupling.

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
| `src/nyxloom/daemon.py` | 8,362 | Effect execution, process/gate/git/HTTP orchestration, shared mutable daemon state | CR-05, CR-13a, CR-15, CR-16 | Frontier implementation and review; split by effect boundary, never by arbitrary line ranges |
| `src/nyxloom/reconcile.py` | 2,302 | Pure planning, lifecycle routing, attempt recovery, wave scheduling | CR-06, CR-07, CR-09, CR-11 | Differential old/new planner comparison; classify touched tests before retirement |
| `src/nyxloom/types.py` | 692 | Persisted state, transition graph, events, serde | CR-03, CR-07 | Frozen until the package explicitly owns the migration; map every current transition to kernel or compiler |
| `src/nyxloom/storage.py` | 458 | File/SQLite dispatch, event projection API | CR-04 | Delete file path and selector; preserve validation semantics with replay/export proof |
| `src/nyxloom/storage_sqlite.py` | 326 | SQLite event/projection implementation | CR-04 | Atomic append/projection ownership, versioning, backup/export/re-import |
| `src/nyxloom/stages.py` | 370 | Seven-stage menu, stage ownership, preset closure | CR-07 | Replace menu composition with compiled workflow IR only after semantic parity proof |
| `src/nyxloom/wrapper.py` | 598 | Child launch, receipt publication, child environment | CR-13a, CR-13b | Container boundary and secret injection; receipt contract remains owned by CR-03 |
| `src/nyxloom/adapters.py` | 1,161 | Provider argv/prompt and usage adapters | CR-08, CR-10, CR-13a | Route selection must not remain at adapter call sites; preserve argv-budget tests |
| `src/nyxloom/render.py` | 2,526 | Dashboard/operator rendering | CR-14 | Consume trace/evidence projections; do not derive authority from presentation data |
| `src/nyxloom/cli.py` | 2,114 | Operator and recovery commands | CR-01, CR-04, CR-14, CR-15 | Keep state-changing paths on the same authoritative store/evidence rules |

## Supporting boundaries

| Surface | Lines | Package ownership / constraint |
| --- | ---: | --- |
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
| `src/nyxloom/commands.py` | 387 | CR-05, CR-16: operator chat-ops are effects; they must route through the same effect boundary and health alarm |
| `src/nyxloom/log.py` | 372 | CR-14: structured logging is the trace substrate. Reserved-key traps (`event=`, `level=`) are documented in `nyxloom-trove/DOCTRINE.md`; renaming a field is a behavioural change |
| `src/nyxloom/backlog_items.py` | 324 | CR-12: auto-tick on merge is product evidence; it must read the typed merge record, not re-parse markdown |
| `src/nyxloom/carver_session.py` | 291 | CR-06, CR-07: the carver session projector is planner input; keep it pure when the planner is rewritten |
| `src/nyxloom/frontmatter.py` | 281 | CR-01, CR-07: handoff parsing is the workflow compiler's front end; schema changes land here with the compiler, not before |
| `src/nyxloom/watchdog.py` | 201 | CR-16: the runaway backstop must remain independent of the engine it watches |
| `src/nyxloom/findings.py` | 207 | CR-14: advisory system-to-user channel; never an authority input |
| `src/nyxloom/leases.py` (see above) | 114 | CR-05 |
| `src/nyxloom/gate_runner.py` | 110 | CR-02, CR-12: shared gate execution primitive; its result is typed evidence bound to a commit |
| `src/nyxloom/paths.py` | 95 | CR-04: state layout. Frozen through the store rewrite except by an explicit migration contract |

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
| `tests/test_commands.py` | 693 | Operator chat-ops surface | Behavior oracle for the operator contract. CR-05 may re-route the effects underneath; the observable command-to-effect mapping asserted here must survive unchanged. |
| `tests/test_frontmatter.py` | 453 | Handoff parse/serde boundary | Behavior oracle. CR-07 extends the schema; every existing parse/reject assertion keeps its verdict, and new workflow fields are additive. |
| `tests/test_leases.py` | 179 | Lease acquire/release semantics | Behavior oracle. CR-05 injects leases as a port; capacity and race semantics asserted here are the port's contract. |
| `tests/test_core_characterization.py` | new | Cross-package semantic corpus | Keep through the entire program. New implementations must preserve active cases or explicitly amend them with reviewed evidence. |

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
