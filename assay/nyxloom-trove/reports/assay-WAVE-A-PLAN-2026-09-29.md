# Wave A plan: structure before the next release

**Written:** 2026-09-29 by the carver (Claude Opus 5.5) with the operator.
**Decisions:** A-475 to A-480 in `../decisions.md`.
**Backlog:** B123 to B130, B111 (P0), B113 (P2), B108 phase 1 (P8) in `../4-backlog.md`.
**Why:** `assay-B110-REUSE-AND-TESTABILITY-2026-09-28.md` Part C and the operator's follow-up questions.

## 1. Goal and definition of done

Wave A changes assay's structure so the per-mutant work contains only what the mutant can change. It lands before the next release and before any v14 work. **Done** means all of the following:
- every package below is merged into `assay-b110-landing`, reviewed by a fresh session and gated with `./run-gate.py tester-unified`;
- `assay-b110-landing` is merged to main (after the B107 fix lands on main: see §5);
- the next release is cut (A-479: 7.2.0 unless a package changes a consumer-visible surface).

It is **not** done by a B105 qualification. B105 still needs the v14 wave and the pilot.

## 2. Packages and order

| Stage | Pkg | Backlog | What | Touches | Brief |
|---|---|---|---|---|---|
| **0** | W3 | B130 | **First (operator, 2026-09-29).** Part 1: component map, import graph, coupling measurement (research). Part 2: a stdlib-only import-contract test plus a component-organized judge-test layout (`tests/<component>/…`) that every later package builds on. Assertions are unchanged. A component-scoped R2 *claim* remains a separate decision informed by Part 1. | `tests/` layout, one new contract test; report | `wave-a/W3-component-boundaries.md` |
| 1 | W1 | B124 + B125 | Retire cross-project harnesses (Topos, 1.2.5 smoke, CMRU B006(a), dstdns checkout) and the historical schema phases; one refusal check per schema | `tools/tester-unified-gate.sh`, `gate/python/`, tests, carve-asset locks, docs | `wave-a/W1-retire.md` |
| 1 | W2 | B127 | `assay analyze` → its own package in the same distribution | `src/assay/analysis.py`, `cli.py`, `pyproject.toml`, analysis tests, `assay.toml` targets, B105 checker, docs | `wave-a/W2-analysis-package.md` |
| 2 | W4 | B123 | Judge tests vs tooling tests (`gate/tests/`); B105 lanes collect `tests/` only; S1 binding; drop `--override-ini` | tests tree, gate script, `assay.toml`, `run-gate.toml`, B105 checker, docs | `wave-a/W4-test-split.md` |
| 2 | W5 | B126 | Self-contained SQL qualification | `gate/python/qualify_dstdns_sql.py` (replaced), SQL fixtures, SQL tests | `wave-a/W5-sql-self-contained.md` |
| 2 | W6 | B113 | P2 loop guards (existing brief, rebased) | `errors.py`, `git.py`, adapters, tests | `b110/P2-loop-guards.md` + `wave-a/REBASE-P0-P2.md` |
| 3 | W7 | B128 | Shallow snapshot for both B105 lanes | `assay.toml`, `test_self_lane.py` | in §3 below |
| 3 | W8 | B111 | P0 measurement hygiene (existing brief, rebased) | `cli.py`, `mutation.py`, `liveness.py`, B105 checker, tests | `b110/P0-measurement-hygiene.md` + `wave-a/REBASE-P0-P2.md` |
| 3 | W9 | B108 ph. 1 | P8 campaign analysis, built in the W2 package | analysis package | `b110/P8-campaign-analysis-core.md` + `wave-a/REBASE-P8.md` |
| 4 | W10 | B129 | DRY consolidation, including the dataclass contract test | `verdict.py`, `verify.py`, other judge modules | `wave-a/W10-dry.md` |

**Ordering rules:**
- W3 before everything else. Later briefs cite `tests/` paths that W3 may move; implementers re-resolve them by filename.
- W1 before W4: W4 moves what survives W1.
- W1 before W7: W7 needs the history reader gone.
- W2 before W8 and W9 (CD1, CD20).
- Stage 2 merges in the order W6, W4, W5. Stage 3 merges W8 before W9.
- Carver answers to the writers' questions: `wave-a/CARVER-DECISIONS.md` (CD1–CD24).
- W10 last, because it touches the most judge modules.
- Within a stage, packages may run in parallel only in separate short-lived worktrees branched from `assay-b110-landing`. They merge back serially with `--no-ff`, and each worktree and branch is deleted right after its merge.

## 3. W7 (small enough to specify here)

W7 runs after W4 (CD12).
- In both `[lanes.self-qualification.isolation]` and `[lanes.self-qualification-preflight.isolation]`, set `snapshot_history = "shallow"` (A-451 default). Keep the two lanes identical (`gate/tests/test_self_lane.py`, moved there by W4).
- Update the pin in `gate/tests/test_self_lane.py` (`snapshot_history == "full"` → `"shallow"`) and the rationale comment in `assay.toml`.
- **Drift proof:** on the W7 branch, run `./run-gate.py self-qualification-preflight` twice, host permitting: first with the lane still at `"full"`, then with `"shallow"`, on the same commit apart from that one value. Collected, passed and skipped counts must be identical. Any test that newly skips or fails is a finding, not a pass.
- **Negative:** add `tests/core/test_snapshot_history_shallow.py` (W3 layout). It builds a two-commit repository with the existing `git_repo` fixture, materializes a shallow snapshot through the isolation API, and asserts that `git rev-parse HEAD~1` inside the snapshot fails. It must fail if the snapshot were full.

## 4. Rules for every package

- **Host-load rule, in every agent prompt:**
  - the host is shared with a production game server;
  - nice/ionice for everything;
  - at most one gate container at a time, and none while another session's gate runs (`docker ps` first);
  - never the full `self-qualification` lane;
  - remove containers by exact name only.
- **Gate:** `cd <worktree>/assay && python ./run-gate.py tester-unified`. Read the markers in a separate step.
- **Every gate failure is real** (CD29). The former B107 known-red test was removed on main (`35adca38`), and landing merged that (`1e3c8a49`).
- **Edits** through the editor tools, never sed or `write_text` scripts (operator directive).
- **Docs:** README / DESIGN-GUIDE / CONSUMERS stay in sync within the package (estate rule).
- **Implementers are Sonnet only** (operator, 2026-09-29). Briefs therefore leave no design choice to the implementer. Anything unsettled is a carver question answered before dispatch.
- **Review:** a fresh-session adversarial review before merge into landing.
- **BLOCKED** is a success mode.
- **Trailer:** `Co-Authored-By: Claude Sonnet <noreply@anthropic.com>`.

## 5. Merge and release

1. After W10, re-merge main into `assay-b110-landing` (needs the B107 fix on main).
2. Run the registered gate. It must be green.
3. Merge `--no-ff` to main, then `docs/testability-cleanup-20260928`.
4. `cmru release --project assay` with the version per A-479.
5. Deploy to the devcontainer per the standing rule, and notify dstdns (`.assay-inbox/release.json`).

## 6. Effect on the B110 plan

- **P1 (B112)** is superseded by W1/W4/W10. Its `--override-ini` drop moves to W4, and its dataclass contract test moves to W10.
- **P0, P2 and P8** run in Wave A.
- **The v14 wave** follows the release: P3a–P3d, P4, P6, P7/P7b and P10a, then the pilot.
- **P5 (B116)** is deferred (S6). P9, P10b, P11 and B122 remain conditional.
- **The B110 briefs' line anchors predate B107 and Wave A.** Every rebased brief must re-check its anchors (`wave-a/REBASE-P0-P2.md`, `wave-a/REBASE-P8.md`).
