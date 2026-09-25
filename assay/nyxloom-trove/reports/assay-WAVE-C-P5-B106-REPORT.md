# Wave C P5 — B106 selective mutation reuse report

**Status:** IMPLEMENTED; final `tester-unified` gate pending. This report is
bound to CIU worktree
`/workspaces/vbpub/.worktrees/assay-wave-c-p5-b106-selective-reuse`, branch
`assay-wave-c-p5-b106-selective-reuse`, based on main tip `3f117b27`.

## Decision and review

B106 is limited to a verdict schema v13 change. The xhigh design recommendation
and the operator's ruling are recorded as A-461 in `decisions.md`: the existing
candidate inventory field is used, current kills require current pytest
witnesses, incomplete/v12 inputs cold-start, and no lane schema or reason-code
change is needed.

The first GPT-6-Luna xhigh code review found five actionable defects. The
implementation now rejects third-party pytest lifecycle hooks for replay,
prevents `--rejudge-outcome` candidates from consuming old witnesses, persists
and restores execution provenance in resume records, includes effective
pytest configuration in plan eligibility, and describes JavaScript R2
ingestion as an existing supported path rather than as a new registration.

The final review found one remaining P2: a plugin with module name
`assay_mutation_witness_plugin_extra` could pass a prefix-based allowlist. The
allowlist now checks exact Assay plugin names and resolved generated plugin
paths; pytest built-ins must originate under pytest's `_pytest` package path.
The regression loads a lookalike plugin that changes a passing report and the
session exit status. Its campaign must fall back and record the mutant as a
full-run survivor. A fresh GPT-6-Luna xhigh follow-up returned **no actionable
P1/P2 findings**. The review did not edit files or run tests.

## Verification before the registered gate

- The combined focused B106, verifier, standalone and distribution checks
  reached **315 passed**. ShellCheck initially caught the new command's
  intentional empty `PYTHONPATH`; a local `SC1007` suppression fixed it.
- After that fix, `shellcheck tools/tester-unified-gate.sh` exited 0 and the
  gate marker/order test passed.
- After the final hook-allowlist repair, the lookalike-hook and genuine
  liveness-plugin integration tests passed (**2 passed**), and
  `tests/test_b106_reuse_and_witness.py` passed (**25 passed in 19.35s**).
- The final `git diff --check` and registered gate result are pending.

## Registered gate

Pending from this worktree after commit: `./run-gate.py tester-unified`.
The passing run must include the existing twelve Wave C markers and
`ASSAY_GATE_PHASE=verdict-v13-successors-verified`, plus
`ASSAY_REGISTERED_GATE_COMPLETE=1`, the container exit, and the outer `GATE_EXIT`.
The run will be checked at 90 seconds for a progress log and runtime estimate;
its exact container will be capped at 3 CPUs under the configured gate slice.

## Agreed work after P5

After P5 is merged and the single Wave C release is complete, B105 is the next
package, before M7. The pre-release Wave C lane remains R0-only. B105 must add
an explicitly invocable self-qualification route that measures Assay's own
source at R0–R3. R1 must measure the whole declared source target with branch
arcs and a full branch-coverage floor; exclusions must be explicit and
reviewable. R2 must run native mutation across the full source target and
produce a complete, verifier-accepted candidate inventory. R3 must exercise
the declared canary contract. The final B105 tree must produce and retain the
actual self-qualification report through tester-unified; reviews are useful
but cannot stand in for that measurement.

Recommended shape: keep ordinary release acceptance fast, and add a separate
named self-qualification gate/lane that operators can invoke deliberately
when preparing an Assay release. Since B106 ships in Wave C, that later
campaign can use `--reuse-from` for safe current-witness replay after test or
source changes; it must still cold-start uncertain candidates and rerun the
current baseline. Its thresholds must not depend on host load or incidental
scheduling.
