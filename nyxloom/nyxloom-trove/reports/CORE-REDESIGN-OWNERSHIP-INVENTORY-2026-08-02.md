# Core redesign source and test ownership inventory

Date: 2026-08-02

Purpose: planning input for the core-redesign reviews. This is deliberately not
a gate and sets no size limit. It records the surfaces whose ownership must be
explicit while the control plane is decomposed.

Snapshot: `8578cbfa`, measured with `wc -l` in this checkout. Sizes are signals
for review allocation, not estimates of difficulty and not proof of coupling.

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

## Existing test pressure and retirement policy

| Test surface | Lines | Current character | Required handling in CR-05 to CR-07 |
| --- | ---: | --- | --- |
| `tests/test_daemon.py` | 7,082 | Largest structure-mirroring and behavior mix | Classify each touched test. Keep observable event/state/artifact oracles; delete only tests coupled to removed private shape, naming them in the package report. |
| `tests/test_reconcile.py` | 4,343 | Planner behavior and structure mix | Retain semantic action/output tests; use the CR-00 corpus plus differential planner output as the migration boundary. |
| `tests/test_behavioral.py` | 1,136 | Real daemon/fake-agent integration behaviors | Behavior oracle. Do not retire it merely because the executor shape changes. |
| `tests/test_adapters.py` | 2,619 | Adapter boundary, prompt construction and argv budget | Keep role/route boundary tests, especially realistic-path argv-limit behavior. |
| `tests/test_wrapper.py` | 1,394 | Child-launch and receipt boundary | Behavior oracle for CR-03 and CR-13a; expand containment checks rather than mirroring a new implementation. |
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
does not claim that the current `TaskState` enum is the target architecture. The future five-band
fixtures are structurally complete and explicitly marked `expected-red-until-CR-09`; they become
behavioral comparisons only when CR-09 supplies the ladder driver.
