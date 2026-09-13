# RG-55 P2 final adversarial review — round 3

**Date:** 2026-09-13  
**Verdict:** **ACCEPT**  
**Reviewed:** `rg55-run-gate-client` at
`83094759f71b21f2d90fbc8e27a45204297d22dc`; mutation-judged tree
`186461de5ef5e58031c10a65c3ebf1087dfae76f`; canary/final-gate code tip
`050c641740646091ec19c95e7111eabfb431e81d`.

## Evidence

- The delta from `186461de` to the reviewed tip changes no production Python:
  it is the R3 canary repair, CHANGES, and close-out records. The final
  `83094759` commit changes only the REPORT. `git diff --check` is clean.
- The repaired `median-not-mean-series-stats` target occurs exactly once in
  `run-gate.py` and anchors the non-byte `else` branch. A live shipped-canary
  run returned exit 0 with `2 rejected, 0 survived`. In a disposable copy,
  restoring only the old pre-R-36k canary target produced `BROKEN CANARY` and
  exit 1. In a separate disposable copy, applying the canary's arithmetic-mean
  mutation to the new branch made the named outlier selector fail on the CPU
  median (`3.833 != 1.3`). Thus the repaired canary is both aligned and
  behaviorally discriminating; the nearest-rank byte branch is not mutated.
- `.assay/verdict-r2.json` is keyed to `186461de` and records
  `BUDGET_EXCEEDED` / `LANE_TIMEOUT`, exit 4, R0 PASS, and exactly 283
  candidates: 263 killed, 18 survived, 2 budget placeholders, 0 crashed, 0
  equivalent. The placeholders are `run-gate.py:2097` (`True->False`) and
  `run-gate.py:2476` (`None->[]`). The progress stream records the final
  283-candidate resume, and candidate 105's matching progress/state records
  identify `tools/coverage_gate.py:128` (`Eq->NotEq`) as killed after
  1844.634 seconds. The REPORT plainly discloses that RW-58 accepted this
  281/283 judged record and that no full mutation PASS was obtained. Its 18
  survivor dispositions match the retained verdict and do not falsely relabel
  the non-equivalent telemetry gaps as killed.
- Retained history shows selftest and R1 passing on `050c6417`, and the real R3
  failure on `186461de` followed by R3 PASS on `050c6417`. The retained R1
  verdict is PASS at `050c6417` with 1609/1609 lines and 370/370 branches.
  Re-running the shipped diff-coverage judge against the retained coverage
  artifact returned exit 0 with exactly 875/875 changed lines and 336/336
  branches. Collection finds 1090 tests, consistent with the reported 1087
  passed / 3 skipped. A live `doctor` recheck returned exit 0 with the reported
  6 OK, 2 warnings, 2 skipped, and 1 info.
- R-36h remains structurally enforced: profiler start/tick/finish failures are
  contained on container and exec paths; fallback samples unavailable facts as
  `None` rather than inventing zero; unknown cgroup paths remain `null`; slice
  selection still reads declared/ambient configuration and refuses absence;
  the profile token is placed before the container positional and redacted in
  displayed argv. A bounded safety selector set covering timeout/missing
  binary fallback, unreadable cgroup data, daemon-to-basic fallback, token
  redaction, cleanup containment, and container/exec verdict preservation
  passed 10/10. These production paths are unchanged after the round-2
  accepted implementation tree apart from the already-reviewed R-36k median
  policy.
- `__revision__` remains 41. SPEC R-36k/R-44a, CHANGES, and the byte/non-byte
  tests agree on nearest-rank p50 for byte series and arithmetic median for
  CPU/stall series. RG-58 through RG-61 accurately retain the accepted deferred
  work; RG-61 contains the known README/CONSUMERS/SPEC documentation remainder.
  The R3 repair itself changes no public capability and is accurately recorded
  in CHANGES, so it creates no new documentation or intended-23.7.0 blocker.

## Blockers

None.

