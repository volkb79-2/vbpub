# P1 final merge review — initial blocker, 2026-10-06

REJECT

## Initial identity and review boundary

- Target P1; caller-selected route `gpt-6-sol` / `xhigh` (caller evidence, not reviewer self-attestation).
- Initial clean branch `rg55-p1-final-review-20261006`, HEAD `0afa24a6e4a206f260df848d9650d9bc849745f3`, tree `296b2ed27646bd06277e6bac260876bdfbfffa70`; current main and merge base `ac335808fb86c43644093a76c854c3032906ac5a`.
- Initial status and complete `main...HEAD` diff were captured before any edit at `/tmp/rg55-p1-final-review-initial.diff`. The candidate delta is test and review-record repairs only; the blocker below is in the daemon source already shared with main.
- This record is written before any repair. The final review artifact will separately state the result after repair and exact-tree verification.

## B1 — startup fault strands a placed process and destroys its recovery journal

**Severity:** blocker; D-15/D-25 placement safety and contract §8.3 stop/recovery behavior. `lib/serve.py:1005-1040` applies placement before the start transaction's final sample and manifest. The exception path at `lib/serve.py:1166-1176` releases DAMON and unconditionally removes the session directory, without calling `placement.release()`. The thread-start exception path at `lib/serve.py:895-911` has the same omission. An earlier exception between `placement.apply()` and the final `try` likewise has no cleanup. `lib/placement.py:1820-1890` requires an identity-checked release and a retained journal when restoration is indeterminate.

**Observable failure and reproduction:** with the existing fake cgroupfs and token PID 101, inject `OSError("synthetic sessions-volume failure")` into `RunDir.write_manifest` after successful placement. The pre-repair request raised that error; direct inspection returned `leaf_exists=true`, `scope_exists=true`, `session_dir_exists=false`, `journal_exists=false`. The PID remains moved under an owned leaf while restart has no placement journal to recover it. The normal start failure is a daemon fault; it must not silently abandon host placement state.

**Prescription:** on every post-placement start failure, attempt the placement object's identity-checked release. Remove the incomplete session directory only after cleanup is confirmed, or when no scope could have been created. Preserve the durable placement journal and refuse a misleading clean retry when restoration is unconfirmed. Cover both successful rollback and indeterminate rollback with fake-cgroup behavioral tests; rerun the registered short gates on the repaired exact tree. A new code commit invalidates all prior exact-tree gate and mutation evidence. R2 and the registered full gate remain release holds under RW-381; do not launch duplicate long lanes in this review.

## B2 — retention prunes a failed placement's recovery record

**Severity:** blocker; same §8.3 safety invariant on the ordinary stop path. `lib/serve.py:2050-2055` finalizes a session even when `placement.release()` cannot restore the lane. `handle_stop()` immediately calls `_run_retention()`. At `lib/serve.py:2218-2240`, retention treats every non-live manifest as deletable by age/count and does not inspect `placement-state.json`. This can erase the only recovery state while the leaf and scope still exist.

**Observable failure and reproduction:** the fake cgroupfs starts a placed token PID 101. Set `keep_sessions=0` and `keep_days=0`, then make placement restoration return unconfirmed at `stop`. The pre-repair response was `ok=true`, the leaf remained, and direct inspection returned `session_dir_exists=false`, `journal_exists=false`. The stop summary's placement error may be null when the failure seam omits a diagnostic; the durable journal must be the retention authority in either case.

**Prescription:** retention must protect every session whose placement journal is present and not positively complete, including malformed or unreadable journals. Only a positively complete journal may be removed by ordinary count/age policy. Add a behavioral test that creates a real fake leaf, fails restoration, invokes stop/gc with zero retention, and observes the leaf and its recovery record still present; also show a completed placement remains eligible for ordinary pruning.
