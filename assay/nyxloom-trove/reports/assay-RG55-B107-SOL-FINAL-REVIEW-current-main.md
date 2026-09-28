# RG-55 Assay B107 — current-main final adversarial review

**Verdict: ACCEPT.** No B107 code or regression-oracle blocker remains.

## Identity and scope

- Reviewed clean candidate `801fa0332515bb6c34743cf133acc51fc28a2cc1` on branch `rg55-assay-b107-current-main-20260928` against current main `87c13eff5b03c65f07733a282c5b1dac24609e54` (the range's complete 16-file Assay diff). `git diff --check` passed.
- Read the prior B107 final review, the four production files, current-main tests, public docs and CHANGES, and the B107 backlog contract. The prior review's CPU-drop, cached-evidence, and malformed-counter repairs remain present.
- Compared Git blob IDs for `liveness.py`, `liveness_resources.py`, `mutation.py`, and `runner.py` at this tip and at previously accepted `5a7308d6c1c6d685850a436b86c91f0913b3b8ad`: each pair is identical. Commit `801fa033` adds only the cached idle-span oracle to `test_mutation_judge_identity.py`.

## Adversarial result

1. **The new oracle reaches the intended guard.** `_hung_evidence()` is valid before alteration. Its samples prove an eligible suffix of `31.0 - 0.0 = 31.0` seconds. Changing only `idle_eligible_s` to `30.0` passes the preceding type, bound, sample, resource-delta, progress, and CPU-window guards. A line trace reached `_valid_hung_resource_evidence`'s idle-span `math.isclose` at `mutation.py:1461` and returned false there. The one-second mismatch exceeds its 0.0011-second absolute tolerance. The focused pytest case passed.
2. **The cache refuses that behavioral mismatch.** With all identity and judge fields held fixed, `_load_validated_state_record` accepted the intact `hung` record and returned `_RECORD_REJECTED` after only the idle-duration change. This distinguishes the intended evidence refusal from a stale judge, corrupt identity, absent record, or an earlier validator guard.
3. **The liveness policy preserves incompleteness under uncertainty.** CPU-total drops restart the comparable CPU window in both the live monitor and cache validator. Positive host/cgroup PSI or throttle deltas consume no eligible time; unreadable, reset, or malformed counters cannot establish a clear interval. Event/output growth resets idle evidence. Both ordinary idle and post-`session_finish` grace require a complete quiet trailing CPU window. A wall-budget expiry without that evidence remains `budget_exceeded`, never a functional `killed`, `survived`, or `hung` claim. The bounded trace cannot certify a hang after truncation. The current-main tests exercise these branches with virtual clocks and injected observations, including the combined unavailable-resource/CPU-drop probe from the prior review.
4. **Public documentation is synchronized.** README states the feature and links to the design rationale and consumer guidance; DESIGN-GUIDE explains the pressure and CPU-window choices; CONSUMERS describes observable outcomes, retained fields, the new evidence `decision` values, and resume behavior. CHANGES records the fix. No config schema changed. The doc-example/vocabulary/anchor suite passed, including the new cross-document anchors.

## Verification and gate boundary

- Local focused pytest: **44 passed** across the new idle-span and accepted-record cases, combined CPU-drop and growing-CPU finish probes, and the resource-counter module.
- Local public-doc pytest: **45 passed** in `test_docs_examples_and_vocabulary.py`.
- The controller supplied the earlier exact-candidate focused `tester-unified` result (**555 passed**) and branch-aware changed-source judge (**326/326 lines, 162/162 branches**). I inspected the ignored coverage JSON's presence but did not rerun or independently certify those aggregate counts. The focused run's recorded `dirty=true` referred to the temporary coverage lane, since removed; the pre-report worktree was clean.
- No gate container, mutation campaign, merge, release, or installation was started in this review. The controller retains responsibility for any registered exact-tree gate required before merge; this report-only commit advances HEAD after the supplied gate evidence.

**Blockers: none.**
