<!-- Verbatim round-2 pre-dispatch review output (fresh-session, read-only reviewer), 2026-09-28, against 3ee09b61. Dispositions: ../REVIEW-2026-09-28-round2.md -->

**B110 round-2 review (docs at `3ee09b61`): READY-WITH-FIXES**

The round-1 fixes landed: 21 of 24 findings are resolved and 3 are partial. The corrected cost table is right. The fixes introduced or exposed 4 major and 11 minor issues. All of them are document fixes; none needs a redesign. Paths below are relative to `assay/nyxloom-trove/`. I did not run anything; the code facts I checked all hold.

### (a) Round-1 findings R-1..R-24

| R | Status | Evidence / remaining gap |
|---|---|---|
| 1 | RESOLVED | plan:51-53 and 330-345; backlog:11343-11344 |
| 2 | RESOLVED | plan:315 and 326; backlog:11342 |
| 3 | PARTIAL | The NO default is enforced (plan:84, 427-429; P6:179; P9 OC8). The "yes" path is contradictory (N-2, N-8). |
| 4 | PARTIAL | The step is removed (plan:397), but the conditions for bringing it back disagree across documents (N-2). |
| 5 | RESOLVED | plan:199-205; A-473 |
| 6 | RESOLVED | plan:78; A-465:953. Residual in N-15. |
| 7 | RESOLVED | A-465:952-955. The text does not match the P10 brief (N-5). |
| 8 | RESOLVED | backlog:11321-11327 |
| 9 | RESOLVED | plan:191 and 194; P3a:294; P3c:145 |
| 10 | RESOLVED | Every row at report:242-246 re-derives exactly (`per_kill × 3760/3/3600`, plus 0.6–0.67 h). Headline :54-56 is right: 54,000/3,760 = 14.36 s, so the prefix must be ≤ ~1–5 s. One bullet was left behind (N-11). |
| 11 | RESOLVED | Fixed as proposed (C2; plan:103). Residual in N-14. |
| 12 | RESOLVED | A-465:956. The B105 deadline bullet (backlog:10755) could also be annotated; optional. |
| 13 | PARTIAL | P2→P1, P6→P3d and the P3c placement are fixed. Still missing: the P0→P6 merge order (plan:461 says it; table:142 says "—"). Graph:158 hangs P10b off P3a alone, but table:147 says P3a+P3b+P7. See N-10. |
| 14 | RESOLVED | report:38 and :390; backlog:11386 |
| 15 | RESOLVED | plan:83 (research R3 §11 exists); the report adds a line-number caveat |
| 16 | RESOLVED | plan:87; A-474 |
| 17 | RESOLVED | P6:26 |
| 18 | RESOLVED | plan:372-374; P7:349-361 |
| 19 | RESOLVED | plan:393 |
| 20 | PARTIAL | The status is fixed (backlog:10777, :175), but boxes 10816-10829 are still unchecked. This is disclosed and acceptable for now. |
| 21 | RESOLVED | plan:478-481 |
| 22 | RESOLVED | plan:335-337; P7:667 and :679 |
| 23 | RESOLVED | P8:163 |
| 24 | RESOLVED | A-468:963 |

### (b) New findings

**N-1: MAJOR. Whether P10b and P10c are needed contradicts the merge graph, and the NO-GO branch deadlocks.**
- The graph (plan:159) and the P10 brief header put P10b inside v14 ("v14 (incl. P10b)"). P7b needs v14, and the pilot needs P7b. That puts P10b, and the operator's OC4/OC7/OC10/OC15 ratification, on the pilot's critical path.
- But plan:290 ("P10b is not needed"), §8.2.3:345 and backlog:11331 make P10b and P10c conditional on a non-empty ledger. That is only known after the screens.
- The gate-script order (plan:467; P9:350) also puts P9 after P10c. After a Pilot NO-GO, the P9 remedy therefore waits on P10c. P10c waits on a screen result, and screens need a Pilot GO. That is circular.
- Fix:
  - State that v14 merges back without P10b.
  - Add: "P10b merges later from a branch off `assay-b110-v14` (the schema is already in P3a)."
  - Change the order to "P7b → {P10c, P9} in either order, whichever is dispatched. The later one rebases."
  - Change graph:159 to "v14 (P3a–d) ──> integration; P10b ──> integration (only if the ledger is non-empty)".

**N-2: MAJOR. The conditions for the D7 import step disagree, and so do the descriptions of `b119-import`.**
- The conditions:
  - plan:397 requires a D7 "yes" **and** the gate mode;
  - plan:429 (the "no" branch) requires only the gate mode, and says workers can still contribute within the deadline;
  - C8 (REVIEW:58) says "D7 is answered", either way.
- The P9 runbook (P9:427-435) operates under the NO default.
- plan:282 says `b119-import` "runs shard 0 into STORE", and plan:397 says it runs "init → identity baseline → import → run". In P9:361-365, `b119-import` only runs init and then import; shard 0 is `b119-worker`.
- Fix: plan:397 should read: "Removed until P9's gate modes exist. Under the D7 default (NO), import accepts only records bound to this campaign's deadline. `--accept-unbound-records` additionally needs a §11.2 'yes'." Align :282 and :397 with P9:361-365.

**N-3: MAJOR. The audit sample can be predicted by the worker, so the "honesty" claim is false.**
- The sample is the lowest `blake2b(campaign + ":" + candidate_id)` (P9:124). A worker holds the deadline file (P9 runbook step 4) and knows its shard's candidate IDs, so it can compute exactly which records will be held back.
- A dishonest worker can therefore stay honest on the sample and never be caught. The detection probability 1−(1−f)^k (P9:50-51) holds only for errors that fall at random, i.e. bugs.
- The same wording appears at plan:283, A-471:984 and REVIEW C12.
- Fix: seed the sample from a coordinator nonce generated after transfer (for example 32 random bytes recorded in the import receipt, so it stays reproducible). Or restate P9:47 and :51 as "detects nondeterminism or bugs, not a dishonest worker".

**N-4: MAJOR. The definition of "qualified" ignores the gate outcome.**
- `audit-check` runs after the consolidating `assay run` has already written the verdict (P9:367). A failed `audit-check` fails the lane, but the PASS verdict still passes `assay verify` and `b105_report_check.py`.
- plan §1:37-47 and §9.3 step 5 (:398-401) define qualification only by those two checks.
- Fix: add to §1 and §9.3.5: "…and the registered gate exited 0 (`ASSAY_REGISTERED_GATE_COMPLETE=1`). When import receipts exist, the `audit-check` result is `pass`. A verdict from a failed gate is never qualifying."

**N-5: MINOR. The ledger-audit acceptance rule contradicts the P10 brief.**
- plan:386, A-465:952 and C10 say the entry must survive "with evidence matching the R2 baseline".
- P10:265 also accepts a `declared`-command survivor that matches the coverage baseline (the A2 fallback). As written, the fallback path can never pass.
- Fix: "…survived with valid survivor evidence: `r2` matching the R2 baseline, or `declared` matching the coverage baseline."

**N-6: MINOR.** plan:122 and C10 say "a ledger combined with `--shard` is refused". But plan:476 and P10:316/488 refuse only `--equivalence-audit` with `--shard`; screens with a declared ledger may still shard. Fix: reword plan:122.

**N-7: MINOR. The judge-identity lists are out of date.**
- A-470:971 omits the R2 hook fingerprint and both coverage-baseline fingerprints (P3B-4; plan:254-259; P3b:273).
- P9:154-168's `judge_identity_inputs` lists 5 of the 7 facts, although plan:259 says CAMPAIGN-IDENTITY "records all seven". It is saved only by P9's "use P3b's" clause.
- "With the two R2 inputs above, that is seven" (plan:259) counts a different set from P3b's "seven optional values".
- Fix: list the inputs explicitly once, in A-470, and point to it from the plan and P9.

**N-8: MINOR. The D7 "yes" path cannot work as specified.**
- P9 `--accept-unbound-records` imports records that lack `campaign_deadline_sha256`. But under `--campaign-deadline --resume`, P6:179 rejects every such record and re-executes it.
- So the "yes" consequence stated at plan:428 (the qualifying run executes nothing) cannot happen.
- Fix: state that a "yes" also requires a P6 change, or drop the flag until the operator answers.

**N-9: MINOR.** plan:473 lists `b121-probe` as a `run-gate.toml` lane with 3 CPU / 2 GiB / 8 GiB. But P11:328 forbids editing `run-gate.toml`, and P11:147-154 launches a raw `docker run --cpus=1`. Fix: remove it from the lane list.

**N-10: MINOR. The plan never says to merge the integration line into v14.**
- v14 is cut after P0, P1 and P2 (plan:465). But P3b and P3d need P6 "in the v14 base", and P10b needs P7 there.
- P3d:9 and P3b:9 assume the controller merges the integration line into `assay-b110-v14`, but plan §10 and §11.6 never say so.
- Fix: add "after P6 (and P7) merge, merge the integration line into `assay-b110-v14` (`--no-ff`)". Also add P0→P6 to table:142.

**N-11: MINOR. Leftover stale text.**
- report:52-53 says "≈10–21 s → 3.5–7.3 h"; §6 gives 9.4–21.4 s → 3.3–7.5 h (3.9–8.1 h with fixed work).
- report:216 ("every kill `tests_completed=5831`") and backlog:11303 are wrong for e79, which recorded 5,819.
- report:222 and the A-467 reason (decisions:962) still say "0.83–0.89 cores"; the report now marks 0.83 as unverified.
- The P7 template (P7:677-683) still numbers the GO criteria 1–5, and REVIEW C6 and C19 use the old numbering. Plan §8.1 now numbers them 1–4.

**N-12: MINOR. The cost headline doesn't reflect the GO thresholds.**
- Pilot GO requires ≤ 4 h at the median including fixed work (plan:335). With about 0.63 h of fixed work, that means ≤ ~9.7 s per kill (p90 at ≤ 5 h: ≤ ~12.5 s).
- The §6 floor after P5, with no prefix at all, is already 9.4–11.4 s. So under the report's own model, Pilot GO is reachable only if the unmeasured no-cov collection time is well below 7.9 s.
- report:53 and the §6 "Fits 6 h?" column imply much more slack. Fix: add a "meets Pilot GO?" column and one sentence to the headline, so the operator can plan for P11 or remote capacity early.

**N-13: MINOR. §8.2.1 has a false-GO gap and the triage list misses outcomes.**
- A screen invocation cut by `LANE_TIMEOUT` produces a verdict with no mutation buckets, so it trivially shows 0/0/0/0.
- Fix: "a complete screen at X*: P8 `analyze campaign` reports `complete` over the full plan".
- §9.1 step 4 triages only survivors and hangs. Crashed and budget-exceeded candidates also block Qualifying GO; add them.

**N-14: MINOR. C2 has residuals that make A-467's "enforced" too strong.**
- A load-induced OOM kill of a *grandchild* makes a test fail, so it still becomes a cold kill.
- A deterministic signal death (SIGSEGV or SIGABRT) becomes `crashed`, which no ledger entry can dispose of.
- Fix: count the cgroup's `memory.events` `oom_kill` during each attempt. Any increase makes every in-flight attempt unclassified (the C3 path). Reword A-467:959 accordingly.

**N-15: MINOR.** The caller-dependence residual of `scope_sha256` is recorded only in P10:170. A-465 and D1 should state it, since it is a known false-PASS residual that the per-entry review has to catch.

### Soundness checks that passed
- **C1:** the whole-lane refusal path is confirmed at `runner.py:4532` (`InvalidRejudgeIdError`, re-raised). `verify.py:~2458` excludes `BAD_LANE_CONFIG`. A deadline hit during the R2 baseline takes `LANE_TIMEOUT` (P3b:345; P6:259).
- **C3:** acyclic, and work stays bounded. A re-invoked candidate starts early and gets its full per-candidate budget, so it gets classified.
- **C4:** the sweep is sound: `>=` catches edits within the same clock tick, and rename-replacement is caught by the inode change. The recorded residual is correct.
- **C9:** closes R-5.
- **C10:** judge-identity binding is honest; the known limit is stated at plan:475.
- **Merge order:** once N-1 and N-10 are fixed, the §11.6 order is acyclic.
- **Other fact checks:** the code references `verdict.py:2950`, `config.py:457`, `mutation.py:3726-3769` and the identity label `/2` at `mutation.py:225` all hold.

### (c) Verdict: READY-WITH-FIXES
1. **Gating and merge order:** P10b/P10c conditionality and the P9-after-P10c deadlock (N-1), plus the inconsistent D7 import conditions (N-2).
2. **Integrity:** the predictable audit sample invalidates the honesty mitigation (N-3), and the qualifying claim doesn't require the gate or `audit-check` to pass (N-4).
3. **Consistency:** stale identity lists, audit acceptance text, lane list and figures (N-5 to N-11), plus a GO-realism gap in the headline (N-12).