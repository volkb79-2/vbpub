# B110 research records (verbatim, 2026-09-28)

These are the unedited final outputs of read-only research agents, plus the first terminal analysis, kept so no researched fact is lost. Line numbers refer to `assay/` at `db85f74784c354188650939e6ade1354ae29f3fa`.

- Mentions of a "scratchpad" refer to the session's temporary directory. The probe scripts from it are copied into `scripts/`.
- The `/tmp` evidence paths named in the records may no longer exist. Every number that matters is restated in the analysis report §4.

| File | Content |
|---|---|
| `R0-initial-analysis.md` | first terminal deliverable. §6 of the analysis report corrects its "snapshot + collection ≈ 6.5–8 s" line to ≈ 9–13 s. |
| `R1-external-tools-research.md` | PIT, Stryker, mutmut, Cosmic Ray, cargo-mutants, mutatest, MutPy, Major, mull, Google, research papers; four required quotes; unverified list |
| `R2-snapshot-internals.md` | per-step snapshot inventory, timings, invariants/pins, designs A/B, stale-bytecode reproduction, C1–C4, guard (G) and red-first (R) tests |
| `R3-v14-cold-witness-map.md` | v14 traps, insertion points, v13 cut checklist, doc anchors, ambiguities A1–A9 (resolved by A-470) |
| `R4-executor-liveness-planner-deadline-pilot-resources.md` | executor invariants and pinned tests, liveness windows, planner estimate, deadline, pilot subset, resource sampling |
| `R5-test-tiers-pathwheel-hangs-dataclasses-order.md` | heavy tier and `zz_slow` layout, PATH-wheel analysis, hang summary, dataclass contract, liveness leak |
| `R6-hang-loop-inventory.md` | all 54 loops/recursions with risk class and simulated hangs; guard design |
| `R7-order-dependence-scan.md` | module-state and test global-state scan |
| `R8-heavy-test-classification.md` | per-test table of the 113 heavy tests |
| `R9-heavy-tests-structural.md` | (2026-09-28, second pass) why the heavy tests are slow, the installed-wheel tests, root causes RC1–RC8, assertion-rewrite cost, the missing release-gate binding |
| `R10-snapshot-structural.md` | (second pass) minimal snapshot content, why not a worktree copy, tmpfs/fsync/reflink/overlay/ZFS/alternates, a measured shape comparison, what P5 becomes |
| `R11-dry-libraries.md` | (second pass) inventory re-derivation, idiom duplication, deliberate duplication, estate-core, library cost and claim binding, hotspots |
| `scripts/` | research probes: `sim_hang.py` (step-capped hang simulation), `contract_probe.py`/`dc*.py` (dataclass contract prototype and counts), `table.py`/`heavy.py` (per-test classification from the progress JSONL), `deps.py` (split-file helper dependencies), `modstate.py`/`modcalls.py` (module-state scan); `r10/` (snapshot-shape probe and strace outputs); `r11/` (candidate inventory, idiom, duplication, estate and churn scripts). They are not product code; they are neither collected nor linted. |
