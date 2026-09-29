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

## Amendments and additions after the pre-dispatch review (2026-09-29)

Reviews: `REVIEW-waveA-tests-gate.md`, `REVIEW-waveA-code.md` and `REVIEW-waveA-sql-plan.md` (verbatim copies in `review-predispatch/`). Every finding was accepted. Where a decision below amends an earlier CD, the amendment wins.

| # | Decision |
|---|---|
| CD7 (amended) | **Withdrawn from Wave A; deferred to the v14 wave**, like CD6. Removing ciu provenance schema 1 is consumer-visible, and the only real green ciu reference fixture is schema 1, used byte-for-byte by two pipeline tests. W1 changes nothing in `src/assay/adjudication.py`. |
| CD11 (amended) | The receipt is **exactly W4's P4 document**: `{"schema_version": 1, "lane": "tester-unified", "commit": …, "tree": …}`. It is written host-side by `finish_registered_gate` only after the container exits green and HEAD/tree are unchanged, and cleared at launch. No version or marker fields: the host script cannot see them, and commit plus tree plus the clear-on-launch rule already bind "the latest run at this commit passed". |
| CD13 (amended) | **W2 owns the moved FIFO test.** In `analysis/tests/`, it launches assay from source with `[sys.executable, "-c", "import sys; from assay.cli import main; raise SystemExit(main(sys.argv[1:]))", …]` and `PYTHONPATH` set to the absolute `src` and `analysis/src` paths. No `standalone` fixture, and no new `assay/__main__.py`. W2 creates `analysis/tests/conftest.py`, which loads the fixtures the moved tests need from `tests/conftest.py` under a unique module name (the same mechanism as CD14). W4 then reuses that loader for `gate/tests/`. |
| CD15 (amended) | Exact pyproject values: `pythonpath = ["src", "analysis/src"]`, `testpaths = ["tests", "analysis/tests"]`. W2 sets them. W4's drift test pins exactly these values, and W4's Work text uses them. `gate/tests/` is collected by explicit path in the registered gate, not through `testpaths`. |
| CD23 (amended) | **W4 finishes the layout.** After W4, `tests/` root holds only `conftest.py`, `fixtures/` and `__init__`-free support files. W4 moves the judge files still pinned at the root (M14's seven, plus any W1/W2/W5 leave) into the W3 component folders, and deletes W3's `ROOT_PINNED` list. The layout checks exclude `tests/fixtures/` (M-finding). |
| CD25 | **P0's plan estimate (CD1 detail).** `assay analyze plan-estimate --plan-json PLAN --progress PROGRESS [--workers N]`:<br>• reads `assay plan`'s JSON inventory and a progress stream containing a completed baseline (a preflight or an R0/R1 run);<br>• prints one JSON object: `schema_version: 1`, `commit`, `tree`, `candidates`, `baseline_s` (measured), `per_candidate_s` (= `baseline_s` today, since every candidate runs the full suite; the cold witness changes this in v14), `workers` (default 1), `projected_worker_hours`, `projected_wall_hours`;<br>• exits 0 on success and 2 on bad input: no completed baseline, commit or tree mismatch, unreadable files.<br>It never classifies a candidate. `assay plan` keeps its fallback and appends one line naming `assay analyze plan-estimate`. P0's oracles are rewritten to this spec in the W8 brief. |
| CD26 | **W8 and W9 become self-contained briefs** (`wave-a/W8-measurement.md`, `wave-a/W9-campaign-analysis.md`), replacing REBASE-P0-P2's P0 half and REBASE-P8. They are re-carved from the B110 briefs, the rebase notes, the review fix texts, CD1/CD4/CD18/CD20/CD25 and the preserved draft. CD18 holds: no private judge names from analysis, and the allowlist stays empty. P6's brief gets a one-line annotation that `_discover_plan_jobs` is extracted by W9. |
| CD27 | **W10 scope fixes.**<br>• `candidate_identity.py` is excluded from the guard helpers: `verify.py` imports it and it is documented as dependency-free (A-182).<br>• W3's import contract allows adapters and parsers to import the core leaf modules `records` and `guards`, and nothing else from core; W3's `ADAPTER_DEPS`/`PARSER_DEPS` include them from the start.<br>• W10's dataclass contract test detects the record decorators by the resolved decorator object, not the literal `@dataclass`.<br>• The A-182 boundary test checks the defining module of every name `verify.py` binds, which catches re-exports. |
| CD28 | **W5 harness contract.**<br>• Residue check: re-applying the mutant on a database that already has the schema must fail loudly and is recorded as expected, not re-applied cleanly.<br>• Probes are given verbatim.<br>• One database per scenario, dropped after it.<br>• The PostgreSQL container is named `run-gate-assay-sql-<pid>-<epoch>`, so other sessions' `docker ps` checks see it. It runs only after a green tester container and is removed by exact name.<br>• New backlog ids start at B134.<br>• The dstdns 171-site → 13-class mapping is recorded in W5's report before the corpus is deleted.<br>• PostgreSQL behaviour is measured by the implementer as Work step 0, BLOCKED if it contradicts the matrix.<br>• W5 branches after W4 merges. |
| CD29 | **The B107 known-red note is obsolete.** Main's `35adca38` removed that test, and landing merged it (`1e3c8a49`). Every gate failure is now real. Conditions reading "after the B107 fix lands" are satisfied. |
| CD31 | W4 moves `test_gate_qualify_dstdns_sql.py` whole into `gate/tests/`, with path-rule edits only; it is tooling, since it runs containers. W5 then replaces it there (CD21, CD28). The shared fixture loader's module name is `assay_judge_conftest`, created by W2 in `analysis/tests/conftest.py` and reused by W4's `gate/tests/support.py`. |
| CD30 | **Consumer-visible list for the release (A-479).** Candidates:<br>• the second top-level package `assay_analysis` in the wheel;<br>• removed gate phases (not consumer-facing);<br>• removed `import assay.analysis` (undocumented, CD19);<br>• the B105 checker's new receipt requirement (assay-internal);<br>• W5's new SQL fixtures (tests only).<br>None of these changes a documented consumer surface, so 7.2.0 stands, unless an implementer reports a change to a documented CLI, schema path or lane key. That report goes to the carver before the release. |

**Plan consequences:**
- W8 now depends on W2 (CD1).
- W7 edits `gate/tests/test_self_lane.py` (CD12).
- Stage 2 merge order is W6, then W4, then W5 (CD21; REBASE-P0-P2). **W5 branches only after W4 has merged** (CD28).
- W7 runs after W4 (CD12).
- Stage 3 merge order is W8 before W9.
