# B110 documents: pre-dispatch review, round 2 (2026-09-28)

**Reviewers:** five fresh-session, read-only reviewers. None was a fork, none edited files, and none ran tests or gates. They reviewed the round-1-fixed documents at `3ee09b61`. Each checked the round-1 findings (resolved, partial or unresolved), attacked the new and changed text, and sampled about 300 new anchors.

**Verbatim outputs:** [`review-round2/`](review-round2/). Round 1 is in [`REVIEW-2026-09-28-round1.md`](REVIEW-2026-09-28-round1.md) and [`review-round1/`](review-round1/).

**Disposition:** every round-2 finding is **accepted as the reviewer proposed**, except where the carver decisions C21–C32 below modify it. Doctrine caps review at 3 rounds. A round-3 check is limited to the briefs that were still NOT READY after round 2.

## Verdicts (round 2)

| Document | Round-1 findings | Round-2 verdict | Blocking or decision-bearing items |
|---|---|---|---|
| Plan / report / decisions / backlog | 21 resolved, 3 partial | READY-WITH-FIXES | N-1 (P10b/P10c conditionality and the P9 deadlock), N-2 (D7 import conditions), N-3 (predictable audit sample), N-4 (qualified requires a passing gate) |
| P0 | all resolved | READY-WITH-FIXES | O7b handshake; O8 liveness |
| P1 | all resolved | READY-WITH-FIXES | — |
| P2 | 1 partial | READY-WITH-FIXES | failsafe floor |
| P3a | 1 partial | READY-WITH-FIXES | P1 path drift; W3 regeneration cannot run |
| P3b | 1 unresolved, 2 partial | **NOT READY** | P3B2-1 (R2-baseline timeout verdict fails verify); P3B2-2 (declared survivors impossible with `--cov`) |
| P3c | all resolved | READY-WITH-FIXES | property contract |
| P3d | all resolved | READY-WITH-FIXES | sourcing the gate runs heavy setup |
| P4 | 1 partial | READY-WITH-FIXES | T6/T7 are vacuous at total == jobs |
| P5 | resolved in design | **NOT READY** | P5R2-1 (pinned `git.py` pragma lines shift); P5R2-2 (sweep baseline from the wrong OID); P5R2-3/4 (timing-dependent oracles) |
| P6 | 2 partial | READY-WITH-FIXES | post-sweep spawn orphan; runner lifecycle; O12/O13 host safety |
| P7 | all resolved | READY-WITH-FIXES | ordering with P6's digest check |
| P8 | 1 partial | **NOT READY** | P8R2-1 (no evidence source carries the mutation judge) |
| P9 | 3 partial | **NOT READY** | identity keys; false-PASS paths P9R2-2/3; lock semantics; ledger × sample |
| P10a / P10b / P10c | all resolved | READY-WITH-FIXES / **NOT READY** / READY-WITH-FIXES | P10R2-1 (wheel digest not reproducible; decision); P10R2-3 (ordering) |
| P11 | 1 partial | READY-WITH-FIXES | probe venv and dirty-clone |

## Carver decisions taken in round 2 (binding)

**C21 (P3B2-1).** An R2 baseline that hits the lane or campaign deadline, or termination, is a **whole-lane** `BUDGET_EXCEEDED/LANE_TIMEOUT`.
- A dedicated `R2BaselineTimeoutError` is re-raised through the existing `runner.py:4532` tuple; extend the tuple, don't replace it.
- An R2-only `LANE_TIMEOUT` beside R0 PASS fails `assay verify`; the reviewer reproduced this.
- Tests use a fake `process_runner` that returns `LANE_TIMEOUT` for the `r2-baseline` phase. No wall-clock budget races.

**C22 (P3B2-2).** Survivor proofs differ by command:
- A **declared** survivor is proven by `started_prefix_ok`, session status 0, and collection plus hook-fingerprint equality with the coverage baseline. It is **not** required to be "not unsupported", because pytest-cov's loop wrapper always sets that flag.
- A cold `r2` survivor keeps "not unsupported".
- A real-run declared-survivor test uses a lane with `--cov`.

**C23 (P7R2-1, P10R2-3).** One pinned order for the shared `run_mutation` block (P6, P7 and P10 all edit it):
1. discovery;
2. P6's plan-digest check, over the **full discovered list**;
3. P10 ledger placement, only with `--equivalence-audit`;
4. P7 selection or shard;
5. resume lookup. Stored records for placed ledger candidates are ignored and reported as `superseded_by_ledger`.

Each brief states this order and adds a combined oracle with a deadline file: P7 T13 and P10 O7c.

**C24 (P10R2-1, P10R2-2, P10R2-5).** Audit receipts and gate modes:
- The audit receipt binds `assay_version` and the source commit/tree, the same way P6's deadline does. The wheel digest is **recorded only**, never compared, because the gate's `pip wheel` is not byte-reproducible without `SOURCE_DATE_EPOCH`.
- Reusing an existing accepted receipt prints `B110_LEDGER_AUDIT_REUSED=1`, never `EXIT=0`. The runbook retains the original audit log.
- `assay ledger audit` gets a judge-bound `--resume`.
- The ledger cap is set from measured survivor cost: at most what fits one 5 h lane invocation at the pilot's p90 survivor time, with an upper cap of 200.

**C25 (P8R2-1).** The `candidates` progress event gains `judge_sha256` whenever a state root is set. This is one additive key in `mutation.py`, owned by P8.
- Fallback: the single distinct judge among in-selection records that pair with a same-bucket latest-run event. If there are none, or several, the judge is unknown.
- A mismatch in `resumed_total` gives `state.unreconciled` and `incomplete`, never exit 2.

**C26 (P8R2-3).** A candidate's file-size class is set by the number of candidates in its file:
- `small`: ≤ 10;
- `medium`: 11–100;
- `large`: > 100.

The 12 strata are operator × class, falling back to operator, then to all. P8's `project()` owns this definition, and plan §8.1 cites it.

**C27 (N-3, P9R2-1..10).** P9 import and audit rules:
- **Identity keys** are copied verbatim from A-470's canonical list: the 7 facts, plus the ledger sha.
- **The audit sample is seeded** from the campaign name plus a nonce. The nonce is drawn after every `SHA256SUMS` verifies, recorded in the receipt, and injectable in tests. The formula covers only non-adaptive faults.
- **Held-back records** live under `STORE/.import-audit/<receipt-stem>/` and never become root records. `audit-check` runs whenever that directory is non-empty, and requires a latest-run `candidate` event for every held ID.
- **Ledger-candidate records** are refused per record when a ledger is declared.
- **The store lock** is taken once per process and held by its file descriptor. The gate never locks. A locked store gives a pre-run `BAD_LANE_CONFIG` with no verdict.
- **Transfers** ignore exactly `.lock` and `SHA256SUMS`, with `LC_ALL=C`.
- **P7's C5 test** asserts on `glob("*.json")`, which tolerates `.lock` and `CAMPAIGN-IDENTITY`.

**C28 (N-14).** OOM handling, owned by P6: if the gate cgroup's `memory.events` `oom_kill` counter increases during an attempt, every attempt in flight during that window is unclassified (the C3 path). If the counter is unreadable, record `oom_guard: "unavailable"`.

**C29.** P6's `_discover_plan_jobs` extraction (from `cli.py:1648-1765`) is the **single** planner-jobs helper. P8's `cli.plan_jobs()` wraps it and adds the digests. There is no second extraction.

**C30 (P3B2-6, P3A2-1).** Merge ordering and test locations:
- P4 merges into the integration line, and the integration line is merged into `assay-b110-v14`, **before** P3b edits `_execute_mutation_jobs`. P3b's Work step 1 checks this and stops with BLOCKED if it does not hold.
- Every brief finds tests **by name**, because P1 moves them into `tests/zz_slow/`.

**C31 (P6R2-5, P7R2-3).** Any exit status of 124 or higher from GNU `timeout` (124, 125–127 or 137) means "failsafe fired, no verdict".

**C32 (P6R2-1, P6R2-2).** Process-group lifecycle in P6:
- `register_live_group` kills the group at once if termination was already requested.
- `execute_plan` checks for termination before it calls the runner.
- `default_process_runner` mirrors `subprocess.run`: on timeout it runs killpg, `wait()`, then re-raises with the partial bytes. A `finally` always does a best-effort killpg before unregistering.
