# B110 documents: pre-dispatch review, round 1 (2026-09-28)

**Reviewers:** six fresh-session read-only reviewers. None was a fork, none edited files, and none ran tests or gates. They reviewed the analysis report, the plan, decisions A-465..A-474, the backlog changes, and all 15 briefs at `a3b68800`. Each brief got AUTHORING's pre-dispatch adversarial handoff review prompt. About 300 file:line anchors were sampled.

**Disposition:** every finding is **accepted as the reviewer proposed**, except where the carver decisions (C1–C20) below modify or replace the fix. The fixes are applied in the commit that adds this file.

## Verdicts (round 1)

| Document | Verdict | Blocking findings |
|---|---|---|
| Report / plan / decisions / backlog | READY-WITH-FIXES | R-1 (circular go/no-go), R-2 (pilot consolidation step) |
| P0 | NOT READY | — (4 MAJOR: G3 hollow; exclusion-line trap; plan-JSON proof; CPU/phase semantics) |
| P1 | NOT READY | — (3 MAJOR: `sys.modules` registration; product-definition evidence IDs; dataclass fixture obligation) |
| P2 | READY-WITH-FIXES | — (P2-1 watchdog thread vs P6 signals) |
| P3a | NOT READY | P3A-1 (W3 migration forbidden), P3A-2 (invalid liveness example), P3A-3 (A5 verdict fails verify) |
| P3b | NOT READY | P3B-1 (A5 verdict fails verify); fixtures impossible under pytest 9.1.1 (P3B-2) |
| P3c | NOT READY | P3C-1 (default bound at def time), P3C-2 (tests moved by P1) |
| P3d | NOT READY | P3D-1 (conflicts with P6/P7b wiring and pins) |
| P4 | READY-WITH-FIXES | — |
| P5 | NOT READY | P5-1 (same-second in-place edit after C1) |
| P6 | NOT READY | P6-1 (in-flight lane-timeout candidates recorded and replayed) |
| P7 | NOT READY | P7-1 (pilot writes no state records) |
| P8 | NOT READY | P8-1 (plan rows lack source digests), P8-2 (store semantics across judges) |
| P9 | NOT READY | P9-1 (`campaign-identity.json` breaks `*.json` globs), P9-2 (plan rows) |
| P10a / P10b | READY-WITH-FIXES / NOT READY | P10-1 (OC12 not wired) |
| P11 | NOT READY (dormant) | — (probe not runnable as written) |

## Carver decisions taken in response (binding; supersede earlier text)

- **C1 (A5 revised; P3A-3, P3B-1, P3B-6).**
  - A runtime transform-proof failure triggers a **whole-lane** `ERROR/BAD_LANE_CONFIG` refusal. The failures are: the no-cov R2 baseline not PASS, differing manifests, an untrusted hook set, or a missing fingerprint.
  - A dedicated `R2CommandProofError` is re-raised through the existing whole-lane refusal path at `runner.py:4532`, following the B094/A-458 precedent. Earlier measurements are discarded, and `verify.py`'s `_INDEPENDENT_R2_TERMINALS` is **not** widened.
  - If the R2 baseline times out on the lane or campaign deadline, the existing `BUDGET_EXCEEDED/LANE_TIMEOUT` path applies, never `BAD_LANE_CONFIG`.
- **C2 (R-11).** In `--cold-witness` mode, a candidate attempt whose process ended by signal (`returncode < 0`) is never `killed`:
  - termination requested, or lane/campaign deadline expired → C3;
  - otherwise → `crashed`.

  Legacy (non-cold) classification is unchanged, except for C3.
- **C3 (P6-1, P6-2, P6-3, P7-5).** A candidate attempt is **unclassified** in any of these cases:
  - its timeout came from the lane or campaign remainder (not from `budget_per_candidate`);
  - it ended while termination was requested, on any exit path, including the liveness hung/timeout paths;
  - it ended by −SIGTERM/−SIGINT during termination.

  `_run_attempt` raises `LANE_TIMEOUT`, the executor masks the position like any unsubmitted leftover, and **no state record is written**. `--resume` re-executes it. P6 owns this and may edit `_run_attempt`'s result handling. P4 rebases onto it.
- **C4 (P5-1).**
  - C1 keeps the stat-based post-command dirt check and adds a **ctime-nanosecond sweep**. After the refresh, record `watermark_ns` = the maximum `st_ctime_ns` over the materialized tree. After the command, every tracked regular file with `st_ctime_ns >= watermark_ns`, or whose `(st_size, st_ino)` changed, is re-hashed in one `git hash-object --stdin-paths` batch and compared to its index OID. A mismatch is `DIRTY_TREE`.
  - P5's first work step is a probe on the tester-unified image: git USE_NSEC, and kernel coarse-clock ctime ordering. The implementer stops with BLOCKED if the probe contradicts the design.
  - A-472 records the residual: a backwards realtime-clock step during a candidate.
  - R4 uses a rename-replacement, plus a separate same-second in-place case that the sweep must catch.
- **C5 (P7-1).** A candidate selection (`--candidates-file`) enables the state root. Every executed candidate writes its record, and `--resume` is permitted with a selection.
- **C6 (R-1).** Go/no-go has two stages:
  - **Pilot GO** (criteria 2–5) authorises screens.
  - **Qualifying GO** authorises the qualifying run. It needs criterion 1, P10b if the ledger is non-empty, and criteria 2–4 re-checked on the screen's measured per-candidate costs.
- **C7 (R-2).** The pilot measures no consolidation cost; B119's acceptance measures it.
- **C8 (R-3, R-4, P6-6, P9-7). D7 defaults to NO until the operator answers.**
  - State records written under `--campaign-deadline` gain an optional top-level `campaign_deadline_sha256` (P6).
  - `assay state import` requires `--require-campaign-deadline FILE` and refuses records not bound to it. The runbook forbids the `--accept-unbound-records` escape unless the operator answers D7 yes.
  - Plan §9.3's import step is removed until P9's gate mode exists and D7 is answered.
  - §11.2 states the consequence of a yes: the ceiling would then bound only consolidation.
- **C9 (R-5).** The qualifying verdict binds its deadline.
  - v14 adds an optional top-level `campaign` block `{name, deadline_sha256, created_at_utc, expires_at_utc}`, emitted when `--campaign-deadline` is used.
  - P3a owns the schema, model and verify; P3d owns producer wiring and `b105_report_check.py --deadline FILE` (required); P3d depends on P6.
- **C10 (R-6, R-7, P10-1..P10-9).**
  - Ledger entries bind a `scope_sha256`: a canonical serialization of the enclosing function or class body, or module-level statement, at review time. A mismatch refuses with `stale-review`. A fresh-session review of each entry is part of A-465.
  - **Ledger audit.** The audit runs the qualifying attempt path: the R2 command with the cold attempt, falling back to the declared command on uncertainty. It requires `survived` with evidence matching the R2 baseline. Its receipt binds:
    - the R2 collection digest;
    - both runtime fingerprints;
    - the judge identity with cold inputs;
    - the wheel sha256, commit and tree, and the ledger sha.

    The receipt path includes `commit12`. The audit runs outside the persisted deadline, and A-465 records that this narrows B105's "8-hour ceiling covering … R2" for ledger entries.
  - **Anchors and fingerprints.**
    - OC12 is bound through the R2 baseline.
    - Fingerprints use a canonical AST serializer, not `ast.dump` (OC14).
    - Resolution is: exact anchor → fingerprint equality → uniqueness within the fingerprint class over the file's sites; the wire anchor is the current one (OC13).
  - **Judge identity and ordering.** The ledger sha enters the judge identity whenever the lane declares a ledger. Ledger placement precedes resume lookup, and stored records for ledger candidates are ignored and reported.
  - **Refusals and fallbacks.**
    - A declared ledger without an audit executes normally.
    - A ledger with `--shard` is refused.
    - An empty ledger is refused.
    - X8 is split by producer: a ledger requires every equivalent to be `ledger` mode, and an artifact allows no `ledger` modes; both together are refused.
  - **P10c.** The gate mode moves to **P10c**, off the integration line after P7b.
- **C11 (R-8, R-15, R-19, R-20, R-24).** Superseded, stale and imprecise text is corrected, as listed in the R-findings.
- **C12 (P9-1..P9-10).**
  - **Store and gate.**
    - The identity file is `CAMPAIGN-IDENTITY`, with no `.json` extension.
    - Import runs inside tester-unified through gate mode `b119-import`, owned by P9. STORE is the gate's state path.
    - An exclusive `STORE/.lock` (`fcntl.flock`) is honored by `run` and `import`. Bytes are re-hashed at write time, and the receipt is journaled.
  - **Trust model.** The trust model is stated honestly: worker-authored records are trusted once identity-matched. Mitigation: consolidation re-executes a deterministic audit sample of imported records (2% per source, minimum 5, seeded by the campaign name), and any disagreement refuses that source.
  - **Refusals.**
    - Non-final buckets (hung, budget_exceeded, crashed) are refused per record.
    - Kills that differ only by mode are reported, not treated as conflicts.
    - Pilot state is refused through the `selection_sha256` recorded in the identity.
  - **Dependencies.** P9 depends on P8 (C13) and P1.
- **C13 (P8-1, P9-2).** P8 adds `source_sha256`, `mutated_file_sha256` and the byte span to `assay plan` rows (additive), and exposes `cli.plan_jobs()`.
- **C14 (P8-2..P8-10).**
  - Out-of-plan records are `state.foreign`, and stale-judge records are not counted.
  - `LANE_TIMEOUT` or never-started budget leftovers give `incomplete` (exit 3). `complete` requires `--command-exit` (the WIP rule).
  - `--project` requires `--project-jobs N`. The fixed-overhead formula is pinned.
  - `mutation.plan_sha256` is computed in plan order.
  - O14 moves to the v14 merge. There is one real-planner fixture.
  - Exit 3 means "incomplete", which deliberately diverges from A-460's freshness-bounded `running`; this is recorded in the brief.
  - P8 exports a pure `project(samples, rows, jobs, fixed)` for P11.
- **C15 (P1-2, P1-3).**
  - P1's scope includes updating the 12 `nyxloom-trove/2-product-definition.md` evidence node IDs.
  - Every brief whose package adds or changes dataclasses lists `tests/fixtures/dataclass-contract.json` in scope, with the regeneration command that P1 documents: P3a, P3b, P4, P6, P7, P8, P9, P10.
- **C16 (P2-1).** The watchdog keeps `run(argv)` on the main thread. A timer thread `killpg`s the registered groups. P6 installs signal handlers only on the main thread.
- **C17 (P3C-1..P3C-8).**
  - `cpu_window_s: float | None = None` is resolved in `__init__`, with `_HUNG_CPU_GROWTH_FRACTION = 1/30` as its own constant.
  - Both keys are refused if not finite or above `"1h"`.
  - They are also refused on lanes where liveness can never activate (the `config.py:3172` rule).
  - The test file is found by test name after P1.
  - The hang-test budget is `"25s"`.
  - There is a calibration-threading oracle, and fixtures use distinct values (6s window, 5s floor).
  - P3c runs the preflight.
- **C18 (§11.1).** The canonical branch `assay-b105-evidence-integrity` (`30eec294`) is not an ancestor of this clone's tip, because the branch was rebased. Bring this tip into the canonical repository as a **new** branch, `assay-b110-integration`. Never force-update the old branch without an operator decision.
- **C19 (R-22).**
  - GO criterion 3 uses the run-gate cgroup `memory.peak`; the 1 Hz RSS sample is only a per-candidate lower bound.
  - Criterion 4 includes the preflight and an R3 estimate, taken as two suite runs from measured baselines.
  - Projection strata fall back from operator × file-size class to operator to all.
- **C20 (R-10, R-14).** The cost-model rows are corrected (§6 of the report). The hang tally reads "14 source-level at-risk mutants plus `liveness.py:1530` at test level (15)".

## Wrong anchors reported

Corrected in the briefs; the reviewers' tables are the source.

- P0: `gate.sh:156-214`; `test_docs_examples…:72-245`; `liveness.py:540-541`; `_EventProgressReader :1060-1130`.
- P1: `test_distribution_gate.py:236-242`; `test_python_qualification.py:125/:778`.
- P2: drain `:335-356`, wait `:357-367`; `conftest.py:163-205`.
- P3a: `verdict.py:4858`; schema `1900–1907`; `test_b106…:96` (future-version case).
- P3c: `_monitor` def `liveness.py:1441`; README `:654–660`.
- P3d: `run_and_verify_lane 156–214`, verify at 195.
- P4: the executor total comes from `:2483`.
- P5: `_FIXED_CONFIG` 143–178.
- P6: `cli.py:1238`; `liveness.py:1333`; `mutation.py:290`; `run_lane` call `cli.py:1481` / handler `:1527`.
- P7: `test_b105_report_check.py` runs the checker as a subprocess.
- P9: `1137-1156`.
- P10: `python.py` from 434; mask 2890–2993.

## Round 2

The briefs that were NOT READY get a focused round-2 check after the fixes, in a fresh session. Doctrine caps review at 3 rounds.
