# B110 package briefs

Carved 2026-09-28 from the B110 analysis. Nothing here has been dispatched yet.

- **Plan (decisions D1–D10, order, gates, pilot, go/no-go, runbooks):** [`../assay-B110-PLAN-2026-09-28.md`](../assay-B110-PLAN-2026-09-28.md)
- **Analysis (every measured fact, the cost model, external comparison, citations):** [`../assay-B110-RUNTIME-ANALYSIS-2026-09-28.md`](../assay-B110-RUNTIME-ANALYSIS-2026-09-28.md)
- **Verbatim research records R0–R8 and probe scripts:** [`research/`](research/)
- **Round-1 pre-dispatch review and carver decisions C1–C20:** [`REVIEW-2026-09-28-round1.md`](REVIEW-2026-09-28-round1.md). Verbatim outputs are in [`review-round1/`](review-round1/).
- **Round-2 review and carver decisions C21–C32:** [`REVIEW-2026-09-28-round2.md`](REVIEW-2026-09-28-round2.md). Verbatim outputs are in [`review-round2/`](review-round2/).
- **Decisions:** `../../decisions.md` A-465–A-474.
- **Backlog:** `../../4-backlog.md` B110 (umbrella: pilot and go/no-go), B111–B121, B108 phase 1.

| Pkg | Backlog | Brief | Class | Starts after |
|---|---|---|---|---|
| P0 | B111 | [P0-measurement-hygiene.md](P0-measurement-hygiene.md) | 2c | — |
| P1 | B112 | [P1-suite-scope.md](P1-suite-scope.md) | 2d | P0 (liveness-leak fix), P2 |
| P2 | B113 | [P2-loop-guards.md](P2-loop-guards.md) | 2d | — |
| P3a | B114 | [P3a-v14-schema-verify.md](P3a-v14-schema-verify.md) | 2b | P10a accepted |
| P3b | B114 | [P3b-r2-command-cold-witness.md](P3b-r2-command-cold-witness.md) | 2b | P3a, P1, P6 in the v14 base |
| P3c | B114 | [P3c-liveness-lane-keys.md](P3c-liveness-lane-keys.md) | 2c | P3a; P2 and P1 in the v14 base |
| P3d | B114 | [P3d-gate-report-binding.md](P3d-gate-report-binding.md) | 2c | P3b, P0, P6 in the v14 base |
| P4 | B115 | [P4-work-queue-executor.md](P4-work-queue-executor.md) | 2b | P0 |
| P5 | B116 | [P5-snapshot-costs.md](P5-snapshot-costs.md) | 2b | P0 (G1–G5), P1, P2; merges after P4/P6 |
| P6 | B117 | [P6-campaign-deadline.md](P6-campaign-deadline.md) | 2b | — |
| P7 | B118 | [P7-pilot-tooling.md](P7-pilot-tooling.md) (steps 1–8) | 2c | P0, P6 |
| P7b | B118 | [P7-pilot-tooling.md](P7-pilot-tooling.md) (step 9: `b110-pilot` / `b110-screen` gate modes) | 2c | P7, P6, v14 merged |
| P8 | B108 ph. 1 | [P8-campaign-analysis-core.md](P8-campaign-analysis-core.md) | 2c | P0 (P7 before its step 9) |
| P9 | B119 | [P9-distributed-evidence.md](P9-distributed-evidence.md) | 2a→2b | P8, P3b, P6, P1 (gate mode after P7b) |
| P10 | B120 | [P10-equivalence-ledger.md](P10-equivalence-ledger.md) | 2a / 2b / 2c | P10a: —; P10b: P3a, P3b, P7 + operator ratification (plan §11.7); P10c (gate mode): P10b, P7b |
| P11 | B121 | [P11-isolation-unit-fallback.md](P11-isolation-unit-fallback.md) | 2a | pilot NO-GO + operator decision |

## Rules that apply to every package

These are repeated inside each brief.

1. **Before dispatch,** each brief passes the pre-dispatch adversarial handoff review (`nyxloom/reference/AUTHORING.md`). Run it in a fresh session, never a fork.
2. **Branch** from the integration line `assay-b110-integration` (created by the plan §11.1 reconciliation, C18). B114 sub-packages and P10b branch from `assay-b110-v14`. Merges are serial `--no-ff`.
3. **Host-load rule** (plan §0):
   - nice/ionice for everything;
   - one gate container at a time, and none while another session's gate runs;
   - never the full `self-qualification` lane outside the plan's pilot or runbooks;
   - remove containers by exact name only.
4. **Gate:** `cd <worktree>/assay && python ./run-gate.py tester-unified`. The worktree must be under `/workspaces/vbpub`. Read the exit markers in a separate step. Add `self-qualification-preflight` when the brief says so.
5. **Review:** a fresh-session adversarial review before merge. The reviewer adds a combined-axis attack of their own.
6. **Commit trailer:** `Co-Authored-By: Claude Sonnet <noreply@anthropic.com>`.
7. **BLOCKED** is a success mode: write `BLOCKED: <reason>` to the package LOG, commit, and stop. Product gaps become `D-NNN` decisions.
