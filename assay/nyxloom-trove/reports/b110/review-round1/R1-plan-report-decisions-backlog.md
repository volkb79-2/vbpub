<!-- Verbatim round-1 pre-dispatch review output (fresh-session, read-only reviewer), 2026-09-28, against a3b68800. Dispositions: ../REVIEW-2026-09-28-round1.md -->

## B110 plan review: READY-WITH-FIXES

The measured facts hold up well: 22 of the 29 claims I checked are confirmed. The plan's ordering has two blocking gaps (R-1, R-2), and there are several soundness and consistency gaps, the most serious of which are R-3 to R-9. Nothing was edited or run; only light reads.

### 1. Fact-check (29 claims)

| Claim | Location | Verdict | Evidence |
|---|---|---|---|
| 30eec294 means: killed 559.5, survived 561.1, all 560.3 s; baseline 519.891 s | report §1.1, §4.1 | CONFIRMED | recomputed from the 15 candidate events and the `plan` event |
| e79: killed 530.9, survived 515.8, baseline 504.6 s; 4 × ≈1,518 s = 24.8% | §4.1 | CONFIRMED | e79 progress file (it still exists) |
| "Every killed candidate in both attempts recorded `tests_completed = 5831`" | §4.1 | **WRONG** for e79 | e79 records 5,819, its own baseline count. The "no early stop" point still holds. |
| First 15 outcomes identical in both attempts | §4.1 | CONFIRMED | index-by-index comparison |
| budget 1559.67 s, next-event 107.7 s, pre-first 22.21 s | §4.1 | CONFIRMED | `plan` event |
| Leading gap 7.40 s = 22.21/3, measured from `session_start` to the first record | §4.4 | CONFIRMED | liveness.py:1008-1010 (`max(3×leading, 15)`); `session_start` is written in `pytest_configure`; the next same-pid record is the first setup `phase` |
| Corrected fixed cost ≈9–13 s per kill | §6 | CONFIRMED | S 2–4 + C 7.9–8.9 ≈ 9.9–12.9 s |
| 441.7 s of call time; top 10/25/50 tests = 152.2/221.8/271.6 s | §4.3 | CONFIRMED | recomputed |
| 113 tests / 317.7 s / 71.9%; 63/286.5; 36/248.4; 17/193.7 | §4.3 | CONFIRMED | recomputed |
| 4,875 tests under 10 ms ≈ 4 s | §4.3 | CONFIRMED | 4.05 s |
| Fastest 80% = 3.0 s; 90% = 17.9 s | §4.3 | CONFIRMED | 2.96 s and 17.8–17.9 s |
| 99% of call time by test #4,360; first `test_v*` at #4,363 | §4.3 | CONFIRMED | |
| `test_python_qualification.py` = 93.7 s = 73.6 + 13.8 + 6.2 s → 97.8 worker-hours | §4.3, §7.3 | CONFIRMED | 32 events |
| Real-window liveness tests 30.83 + 35.90 s | §4.4 | CONFIRMED | |
| Operators 2169/958/498/135; 47 files; per-file min 1 / median 30 / max 623; verdict+verify hold 950 | §4.6 | CONFIRMED | plan JSON |
| 190 import-time candidates, 189 of them bool-const-flip | §4.6 | CONFIRMED | AST scan; the extra one is compare-swap at cli.py:2061 |
| 148 dataclass flags (80 frozen / 68 kw_only); base.py 77/129/164 | §4.6 | CONFIRMED | AST scan and byte offsets |
| cli.py:1786-1801: 60 s fallback for omitted/`auto`/`none` | §1.2 | CONFIRMED | |
| CONSUMERS.md:2307-2312 calls the estimate an "upper bound" | §1.2 | CONFIRMED | |
| PASS rule at mutation.py:3748-3769 | §1.6 | PARTLY | The crashed/budget/hung checks are at 3726-3740. Cite 3726-3769. |
| config.py:3082-3106 shows native lanes cannot lower `fail_under` | §1.6 | **WRONG cite** | That range is the SQL `equivalence_artifact` requirement. The `fail_under` restriction is `_MUTATION_INGESTED_FIELDS` at config.py:457. |
| verdict.py:5041-5053: `equivalent` requires `equivalence_artifact` | §1.6 | CONFIRMED | 5038-5061 |
| Liveness constants at liveness.py:523-526, 641, 646 | §7.7 | CONFIRMED | |
| test_liveness.py:581/601 leak `session_finish`; two call records lost | §7.3 | CONFIRMED | The tests at 629 and 798 are absent from the events, so the suite really has 5,833 tests. The HUNG branch needs a pid match plus 30 s of silence (liveness.py:1046-1053, 1514-1518). |
| `test_python_qualification.py` never imports assay | §7.3 | CONFIRMED, with a nuance | Lines 105-106 check the harness `gate/python/qualify_topos.py`, not the test file. The test file itself has no assay import. |
| Isolation: `_FIXED_MTIME` at :67/:1942; `start_new_session` at liveness.py:1375; character-length `netstring` at :301; repo scale 5,298 entries / 26 symlinks / 6,338 commits | §7.2, §5 | CONFIRMED | |
| `EXIT_CODES` 0–5; exit 6 unused; `BAD_LANE_CONFIG` / `LANE_TIMEOUT` / `CANDIDATE_HUNG` exist; except clause at runner.py:4532 | D10, A5 | CONFIRMED | errors.py:57-66 |
| 738/612 MiB, 0.89 cores, 2.3 s stall, 987 MiB, 573.17/548.35 s | §4.5 | CONFIRMED | B105-LOG:215, 304-312 |
| 0.83 cores; 588 MiB in attempt 3 | §4.5 | UNVERIFIABLE | not found in the log or the run-gate logs |
| "Only four files differ" between 30eec294 and db85f747 | §3 | **WRONG detail** | README, CONSUMERS and DESIGN-GUIDE also differ. `src` is identical, so the conclusion stands. |

The two arithmetic errors (§6 row 2 and the §1.7 headline) are covered in R-10.

### 2. Findings

**Blocking**

**R-1: The go/no-go is circular.** Plan §8 GO criterion 1 needs a clean screen and ledger audit. But §1 ("pilot … returned GO"), the §4 graph (pilot → GO → screen) and §9.3 step 1 ("After GO … then screen") all put the screen after GO. P7's report template even marks criterion 1 "n/a". So B110 can never close, or a controller runs the full screen campaign before any go/no-go.
- **Fix:** split §8 into two gates. "Pilot GO" uses criteria 2–5 and authorises screens. "Qualifying GO" uses criterion 1, P10b if the ledger is non-empty, and a re-check of 2–4. Update §1 and the B110 backlog acceptance to match.

**R-2: The pilot's consolidation-cost step is unsafe and depends on an unlisted package.** Plan §7 phase C and P7:534 say: "`assay state import` (P9) … then time `--resume` there".
- P9 is not a pilot prerequisite (§7 lists P0–P8, v14 and P7b).
- P9's import requires `STORE/campaign-identity.json` from a prior run (P9:144, 281).
- A full-lane `--resume` over 70 records would execute the other ~3,690 candidates. That is a full campaign, which §0 forbids.
- **Fix:** either make P9 a pilot prerequisite and time `--resume --candidates-file <same selection>` (no new execution), or move the consolidation measurement into B119's acceptance.

**Major**

**R-3: D7/A-471 is under-disclosed and not enforced.**
- It contradicts B105 acceptance ("Persist … before preflight or worker dispatch"), not just A-464. It also contradicts B110's rule that queue time counts against the budget.
- Combined with a clean screen on X*, it lets the qualifying run import every record and execute nothing. The 6 h/8 h ceiling would then bound nothing, and §11.2 doesn't tell the operator this.
- P9 OC8 makes the code accept pre-deadline records whatever the answer.
- §9.1 step 2 and P7b's run-gate lane comment state the D7 path without the "pending" flag.
- **Fix:** state that consequence in §11.2. Have P9 refuse records produced before `created_at_utc` unless an explicit flag is given, tied to the operator's answer. List the B105 bullet as amended if the answer is yes.

**R-4: §9.3 step 4 cannot be executed.**
- Before the gate runs, P6 `init` refuses a state dir that holds records but has no deadline (P6:102), and P9 import needs a store identity.
- After the first gate invocation, everything has already executed.
- **Fix:** add a gate step (init → identity-only baseline → import → run), or drop step 4.

**R-5: The qualifying verdict carries no deadline binding.** P6 forbids verdict-schema changes. Neither `assay verify` nor `b105_report_check.py` can tell a clean `b110-screen` verdict (same lane, no deadline, many invocations) from a qualifying one.
- **Fix:** v14 adds a campaign block `{name, deadline_sha256, created_at_utc, expires_at_utc}`, and the checker requires `--deadline` and matches it.

**R-6: A stale ledger entry can produce a false PASS.**
- Anchors are designed to survive edits, and OC3 fingerprints only the smallest statement.
- P10's own example reason ("every caller passes end > start + 1", P10:132) depends on code outside the fingerprint.
- The audit proves only "still survives", which a weak suite satisfies.
- **Fix:** bind each entry to a `scope_sha256` of the enclosing function at review time; a mismatch refuses as `stale-review`. Also move OC7's fresh-session per-entry review from "pending" into A-465.

**R-7: The audit is checked under a different command than the claim, and its runtime isn't actually bound.**
- The audit runs the declared coverage command with cold mode off (P10:187-222). A-470 judges survivors under the no-cov R2 command.
- OC12 is marked "Resolved", but the audit never gets the manifest-only plugin (A6 injects it only with `--cold-witness`; see P3b:289). The receipt has no runtime-fingerprint field, and `judge_sha256` is computed with the cold-only inputs set to None.
- **Fix:** audit on the R2 command and bind the R2 collection digest and runtime fingerprint, or record OC12 as "(a) not bound".

**R-8: B110 text that the decisions contradict is not marked superseded.**
- 4-backlog.md:11162-11167 and 11259 say "one execution-evidence item for every candidate ID" and "evidence IDs equal the candidate buckets". A-470/A4 contradicts this: evidence is optional on full kills, crashed, hung and budget-exceeded outcomes, and forbidden on ledger equivalents.
- B110's desired-behaviour bullet 1 says "Survivors … run the full declared suite", which A-470's no-cov survivor rule replaces.
- Superseded item 3 quotes a phrase that is not in the B110 text; it appears only in R0:99.
- **Fix:** add all three to the "Superseded details" list.

**R-9: An oracle contradicts an existing invariant.** P3a:234 marks `{"active":true,"reason":null,…}` as Valid, and plan §5 and P3c:103 repeat it. verdict.py:2950-2954 requires a non-empty string; the current producer emits `"declared-true"`.
- **Fix:** use `"reason": "declared-true"` in all three places.

**R-10: The cost-model headline is wrong.**
- §6 row 2 should read 9.4–**11.4** s → 3.3–**4.0** h (+fixed 3.9–4.6 h).
- Row 3 should read 12.4–14.4 s → 4.3–5.0 h (+fixed 4.9–5.7 h).
- §1.7's "2.9–4.0 h … prefix ≤ ~3–6 s" doesn't match §6. Correct figures: **3.3–4.7 h, prefix ≤ ~1–5 s** (54,000 s / 3,760 = 14.36 s per kill, minus 9.4–13.4 s).
- Row 5's 1 s lower bound is below S alone. The realistic range is ≈2–6.5 s, i.e. 0.7–2.3 h.

**R-11: A-467's "concurrency never changes a classification" is asserted, not enforced.** A signal-killed process is FAIL → `killed` (runner.py:1301-1340; mutation.py:1683-1686). So an OOM kill in the declared fallback path, where A4 doesn't require evidence, becomes a false kill. Swap stalls can also produce `hung`.
- **Fix:** classify `returncode < 0` as `crashed` in v14 native mode.

**R-12: A-465 narrows the deadline's scope without saying so.** The audit runs outside the persisted deadline, which narrows B105's "8-hour ceiling covering … R2 … verification". A-465 doesn't state this amendment. Add one line.

**Minor**

- **R-13:** The §4 and README dependency tables leave out §11.6's merge orders (P2 before P1; P6 before P3d). The graph draws P3c under P3b. P3d:61's placeholder assumes P6 lands later.
- **R-14:** The at-risk count says 14 but the listed set sums to 15 (report §1.3 and §7.4, B113, R6).
- **R-15:** Stale references:
  - D6 cites "analysis report §7.12", which doesn't exist; the A1–A9 list is in R3.
  - The report's backlog line numbers are from db85f747. At HEAD, B105 is at 10686 and B110 at 11065.
- **R-16:** D10/A-474 say "fixed code 6". It should be "6 when the selection completed; otherwise the verdict's 1–5 code, never 0" (P7 §G).
- **R-17:** P6:21 uses campaign name `pilot`; the plan uses `b110-pilot-<commit12>`.
- **R-18:** §9.1 step 5 uses `--reuse-from .assay/verdict-screen-X.json`, but the screen mode never passes `--reuse-from` and writes `verdict-b110-screen.json`.
- **R-19:** §9.3 says "X* is the first commit on which the screen is clean". Ledger candidates always survive in the screen (P10:231), so this is unreachable with a non-empty ledger. Reword to "only surviving candidates are ledger entries the same-commit audit accepts".
- **R-20:** B106 is marked DONE but its five acceptance boxes (10816-10829) are still unchecked, and it is still listed under "Open items".
- **R-21:** Three things have no owner: a shallow-snapshot follow-up, the reflink TMPDIR, and any projection or budget for the screen itself (survivors × ~380 s on a shared host).
- **R-22:** GO measurement definitions need tightening:
  - Criterion 3 uses the 1 Hz RSS sample, which §7.8 itself calls a lower bound; use the cgroup `memory.peak`.
  - Criterion 4's pilot figure omits the preflight and R3.
  - With about 70 samples, P8's n≥20 rule means the file×operator strata are never used.
- **R-23:** P8 says its exit codes "follow A-460", but its 3 means "incomplete", while A-460's 3 means "running ≤120 s" and stale input is 2.
- **R-24:** A-468 says the override "alone" made the lane witness-ineligible. pytest-cov and the conftest `sessionfinish` hook did too (report §7.1).

### 3. Verdict: READY-WITH-FIXES

The top three reasons:
1. The plan's ordering has gaps that block execution: the circular GO (R-1), the pilot's consolidation step (R-2) and the D7 import step (R-4).
2. The deadline and the ledger have integrity holes that allow a false or unbounded PASS: the verdict has no deadline binding (R-5), the D7 import isn't enforced (R-3), and ledger entries can go stale (R-6) or be audited under the wrong command (R-7).
3. The headline headroom number is overstated (R-10), and some specification text contradicts the code or the decisions (R-8, R-9).

All of these are document fixes; no redesign is needed.