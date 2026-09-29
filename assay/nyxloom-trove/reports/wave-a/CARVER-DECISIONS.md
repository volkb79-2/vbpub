# Wave A carver decisions (answers to the brief writers' questions)

**Written:** 2026-09-29 by the carver.

These are binding for the implementers. Each brief already assumes the answer recorded here. Where a brief says otherwise, this file wins until the brief is fixed in the pre-dispatch review pass.

| # | Question (source) | Decision |
|---|---|---|
| CD1 | Where does P0's measured-baseline plan estimate live now that `analysis.py` leaves the judge? (REBASE-P0-P2 D-W8-1) | It becomes an `assay analyze` subcommand in the analysis package. `assay plan` keeps its fallback and prints how to obtain the measured estimate. **W8 depends on W2.** |
| CD2 | P2's scanner-guard structural test spans four adapters and core (Q-P2-1) | Split it by component, following W3's layout: one test per adapter plus one for core. |
| CD3 | P2's real-child liveness watchdog step | Dropped: main's `35adca38` removed the real-clock liveness tests it protected. |
| CD4 | P0's resource sampler after B107 | It must run after B107's resource read in the monitor and use B107's load-independent test helper. The P0 brief is re-carved for this in the review fix pass, before W8 is dispatched. |
| CD5 | The two release-tag audit tests (`test_runner_snapshot_selection.py`) read tags and commit `c56a13ea` from history (W1 Q1) | Delete them (A-475). Remove their `--deselect` entries from the B105 lanes. |
| CD6 | `reuse.py` still accepts v12 verdicts as a cold start | Deferred to the v14 wave (A-477 already says so). |
| CD7 | `adjudication.py` accepts ciu provenance schema 1 as well as 2 | Remove schema 1 (A-477). ciu emits schema 2 since 7.10.1, and the estate runs ciu 7.x. Note it in CHANGES as a removed legacy input. |
| CD8 | Carve-asset files of retired harnesses (W1 Q2) | They stay in place as history and are no longer executed. Tests that pin their paths are removed with the harness. |
| CD9 | Scope of the S1 receipt requirement (W4 C1) | Only the full `self-qualification` lane. The preflight does not require it. |
| CD10 | Where do the static source-rule sweeps and W3's import-contract test live? (W4 C2) | In `tests/`: they read the judge source and are judge tests. |
| CD11 | What the S1 receipt asserts (W4 C3) | That the latest registered `tester-unified` run at this exact commit and tree passed. The gate writes `.assay/registered-gate/tester-unified.json` with commit, tree, assay version and completion markers. `b105_report_check.py --receipt-only` pre-checks it, and the full check refuses a report without a matching receipt. |
| CD12 | `test_self_lane.py` becomes a tooling test (W4 C4) | It moves to `gate/tests/`. W7 (plan §3) edits it at its new path. |
| CD13 | The analysis FIFO test used the `standalone` wheel (W4 C5) | After W2 it runs assay from source (`sys.executable -m assay` with `src` on the path), so the wheel fixture leaves the fast tier. |
| CD14 | `gate/tests/` import mechanics (W4) | `gate/tests` is a package (`__init__.py`), and `support.py` loads `tests/conftest.py` under a unique module name, as the W4 brief specifies. |
| CD15 | Analysis package layout (W2) | `analysis/src/assay_analysis/` with tests in `analysis/tests/`. `pyproject.toml` gets `package-dir` covering `src` and `analysis/src`. The A-468(c) `pythonpath` pin becomes `["src", "analysis/src"]`, and W4's drift test pins that value. |
| CD16 | Analysis schemas location (W2 Q1) | They stay in `src/assay/schemas/`. No documented path changes. |
| CD17 | R2 for the analysis package (W2 Q2) | Wave A gives it an R0+R1 whole-target lane only. R2 for analysis is a follow-up (B131), cheap after the v14 cold witness. |
| CD18 | Private judge names used by analysis (W2 Q3) | The allowlist mechanism exists but stays empty. Anything analysis needs becomes a public judge name. |
| CD19 | Dropping the undocumented `import assay.analysis` (W2 Q4) | Acceptable for 7.2.0: undocumented and internal. |
| CD20 | P8 after W2 (REBASE-P8) | P8 does the `_discover_plan_jobs` extraction itself; P6's brief is annotated. Hung rows show B107's liveness decision and evidence status, read-only. P8's pilot step 9 moves to the v14 wave. P8 ports the preserved draft in `b108-draft-20260927/`. |
| CD21 | Where the real-PostgreSQL evidence runs (W5) | As an outer phase of the registered `tester-unified` gate, after the tester container has exited, using the host's `python3` and Docker. It is one container at a time and is removed by exact name. W5 merges after W4. The image is pinned by digest to the locally present 18.6 image; there is no pull. |
| CD22 | SQL constructs PostgreSQL may refuse, and two mislabelled operators (W5 findings) | Out of Wave A. Filed as B132 (a hazard-construct probe on W5's harness) and B133 (operator labels: `NOT IN` widening narrows; drop-check also rewrites `CREATE POLICY … WITH CHECK`). |
| CD23 | W3 test layout (W3) | `tests/core/…`, `tests/adapters/<lang>/…`, `tests/parsers/…` as the brief lists. The 24 files that W1, W2, W4 or W5 touch next stay at the root until those packages move or remove them. |
| CD24 | Brief sizes over the 15 KB cap (W10 ≈19 KB, W2 ≈15.3 KB) | Accepted: the extra length is call-site enumeration that a Sonnet implementer needs. |

**Plan consequences:**
- W8 now depends on W2 (CD1).
- W7 edits `gate/tests/test_self_lane.py` (CD12).
- Stage 2 merge order is W6, then W4, then W5 (CD21; REBASE-P0-P2).
- Stage 3 merge order is W8 before W9.
