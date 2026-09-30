# Adversarial review: B110 reuse/testability docs + nyxloom testability doctrine + cmru KI-35

Reviewer: fresh, independent session (read-only). Date 2026-09-28.
All commands ran under `nice -n 19 ionice -c3`. No gate, container or test suite was run. The only tool run was the permitted `cmru abandon --dry-run` from source; its output is in `scratchpad/abandon-dryrun.txt`.

## Scope

| Doc | Worktree / commit | Files |
|---|---|---|
| D1 | `.worktrees/assay-b110-landing` `95622e3a`, `84e4618f` | `assay/nyxloom-trove/reports/assay-B110-REUSE-AND-TESTABILITY-2026-09-28.md` (Parts A, B, C) |
| D2 | same | `assay-B110-PLAN-2026-09-28.md` §12 (lines 556–599) |
| D3 | same | `b110/README.md`, `b110/research/README.md` |
| D4 | same | merge `a004286d` |
| D5 | `.worktrees/docs-testability-cleanup-20260928` `703505aa` | `nyxloom/reference/TESTING-METHODOLOGY.md` |
| D6 | same | `cmru/KNOWN_ISSUES_TODO_BACKLOG.md` KI-35 |

Line numbers below are for the files at `84e4618f` (D1–D3) and `703505aa` (D5–D6).

## Verified OK (spot-checks that passed; 30+ claims)

- **Code anchors.** Every one of these shows what the report says:
  - `cli.py:1786-1801` (60 s fallback);
  - `config.py:457` (`fail_under` is ingested-only), `:2969` ("any language whose tool emits a registered format may ingest"), `:3082-3106` (`equivalence_artifact` is required on sql lanes);
  - `mutation.py:1683-1686` (PASS→survived, FAIL→killed), `mutation.py:3741-3770` (native PASS only with no survivors);
  - `liveness.py:1299-1330` (monotonic/sleep/cpu_reader/popen seams);
  - `verify.py:1-30`, `:14-16` (jsonschema is test-only);
  - `tests/conftest.py:1431-1499` (`standalone`), `test_analysis.py:721` (FIFO test uses `standalone`), `test_python_qualification.py:105-106`;
  - `assay.toml:44` (release lane, no PQ ignore), `assay.toml:73-74` (the "history required" rationale), `test_self_lane.py:102`, `tester-unified-gate.sh:671-679`;
  - `verdict.py:4769-4910` (R1–R4 blocks), `provenance.py:193-194` (`module`/`dist` seams).
- **Module sizes** [M] match exactly: `runner.py` 6,554; `verdict.py` 5,455; `mutation.py` 3,812; `config.py` 3,773; `test_cli_run.py` 2,565.
- **Planning volume:**
  - "≈1.04 MB": 1,040,940 bytes at `5eb5768f` (analysis + plan + b110/*.md);
  - 15 briefs, average 59.0 KB, max 86.5 KB (P3b), P9 81.3 KB second;
  - `nyxloom/docs/competitive-landscape.md` §5 item 5 is indeed "Spec-verbosity risk";
  - analysis §8 quote ("No established tool reruns the full coverage-instrumented suite…");
  - P25 commit `2607cb7d` exists with the stated subject.
- **Research numbers carried correctly:**
  - R9: 221.8 s top-25, 54/36/8% split, RC1 93.7 s, RC3 ≈43–45 s = 66.7 − (22…24);
  - R10: 2.71/2.50/1.60/1.50/0.46 s; 2.55/0.9 s clones; 0 fsyncs; 140–170 / ≈60 MiB; 17 fns / 24 cases; 11 fail / 33 skip;
  - R11: 395/528 = 10.5/14.0%; ≈144 of 148; 290 of 327; 821 stable; ≤197 / 80–150.
- **"10.5–14%" and "≈27–37 min"** reproduce: 395×12.5/3/60 = 27.4 min; 528×12.5/3/60 = 36.7 min. The 12.5 s/kill assumption is R11's, not stated in D1; see A-m4.
- **Relative links.** Every relative link in D1–D3 resolves. The anchor `## Designing for mutation testability` exists (D5 line 298).
- **Merge `a004286d` is clean.**
  - `git merge-tree --write-tree a004286d^1 a004286d^2` = `d68c7800…` = `a004286d^{tree}`, so it is not an evil merge.
  - `git diff a004286d^2 a004286d` equals the branch's own 25 commits since merge-base `f0bebc82` (128 files, B105 work + B110 docs).
  - `main` has since advanced two commits (`a1f88cb5`, `87c13eff`) that touch none of the reviewed files.
- **KI-35 claims that check out:**
  - installed `cmru 5.4.2.dev365+ge5e9b95c` has no `abandon` verb, and `release --help` still shows `--abandon … then proceed with a fresh release`;
  - exactly 8 withheld worktrees carry modified `package-manifests-versioned/*` files;
  - the quoted example worktrees and published versions (assay-v6.5.0, assay-v7.1.0) match the dry run;
  - KI-29's fail-closed contract is represented fairly.

---

## Findings

### Blocking

**1. [blocking] D5 `TESTING-METHODOLOGY.md:298`, `:447`, `:659`. The `## Property-based testing and Hypothesis` heading was deleted.**
- **What.** The diff replaces the line `## Property-based testing and Hypothesis` with `## Designing for mutation testability` and never re-adds it (`git show 703505aa`, hunk `@@ -295,7 +295,154 @@`).
- **Effects:**
  - the whole Hypothesis block (lines 447–628: the Property/state-machine intro, "### Case study — two 2026-09-10 bugs…", "### The rule this earns", "### Hypothesis: already adopted…") is now a sub-part of "Designing for mutation testability". The 2026-09-10 case study reads as a mutation-testability case study;
  - the DoD line at `:659` still says "(`## Property-based testing and Hypothesis` above)", which is now a dangling cross-reference. `grep -n '^#'` confirms no such heading remains.
- **Fix.** Insert this immediately before line 447 ("The "Property/state-machine testing" catalogue row names what it"), with blank lines around it:
  ```
  ## Property-based testing and Hypothesis
  ```

### Major

**2. [major] D1 `:90-97` (A4 item 1) and D2 `:573` (R1). The premise "pilot phase B already builds" a per-test coverage map is false.**
- **What.** The text says to "select the covering tests from the per-test coverage map that pilot phase B already builds (coverage.py `--cov-context=test`)".
- **Evidence:**
  - Plan §7 phase B records only per-candidate `cpu_seconds`, `peak_rss_bytes`, `elapsed_seconds`, `phase_seconds` and the first-test offset. There are no coverage contexts.
  - The only `--cov-context=test` map in the plan set is P11's T4 probe / OC3 (`b110/P11-isolation-unit-fallback.md:54,102,211`). P11 is dispatched only after a Pilot NO-GO plus an operator decision.
  - P11R2-4 records that binding such a map is a three-place identity change: the P3b label bump, the A-470 row, and P9's key set.
- **Why it matters.** This is the top-ranked proposal, and its cost basis ("the largest saving… small P7b/P8 extension") rests on this premise.
- **Fix (D1 :91):**
  > **Proposal:** in the screen only, select the covering tests from a per-test coverage map (coverage.py `--cov-context=test`). None is built today: P11's T4 probe describes one, and the pilot does not build it. This is new work: one context run per screen commit, used only as a screen hint and never bound into the judge identity. Or run mutmut as a scout.

  In D1 `:97` and D2 `:573`, add "a coverage-context run in the P7b screen mode" to the "Would need" / "Plan change" cells.

**3. [major] D1 `:98` (A4 item 2) and D2 `:574` (R2). The proposed order conflicts with the plan's dependency graph and Pilot GO, and contradicts S6.**
- **Order conflicts.** "Front-load P0, P1, P2, P3b and P4, then the pilot" cannot be followed:
  - P3b depends on P3a (which needs P10a accepted) and on P6 and P4 in the v14 base (plan §4 table and graph);
  - the pilot requires P0–P8, the whole v14 branch and P7b (plan §7 first line; §8.1 item 4 "Readiness").
- **Contradiction.** D1 `:98` also says "P5, P6 and v14 are required for correctness and the claim". That contradicts S6 (`:387`, defer P5) in the same document, and it is wrong on its own terms: P5 is a cost package (A-472 C1/C2). Its ctime sweep closes a hole that C1 itself opens, plus an existing assume-unchanged hole that S6 decouples (R10 §4).
- **Fix (D1 :98):**
  > 2. **Prioritize the packages that carry most of the saving:** P0, P1, P2, P4 and the v14 chain P10a→P3a→P3b (with P6 in its base). The pilot still needs P7, P7b and P8 (plan §8.1.4). P6 and v14 are required for the claim; P5 is a cost package (see S6). P9, P10b and P11 are conditional.
- **Fix (D2 :574):**
  > | R2 | Prioritize P0, P1, P2, P4, P6 and the v14 chain (P10a→P3a→P3b); the pilot-readiness set (§8.1.4) is unchanged | §4 ordering note |

**4. [major] D1 `:387` (S6), `:348`, `:374`; D2 `:585`. The "Needs" cell for deferring P5 is incomplete, and S6 is unconditioned.**
- **What.** S6 says it needs only "plan §4". Deferring P5 also contradicts:
  - plan §1 ("packages P0–P8 … are merged" for B110 done);
  - §7's opening (the pilot runs after P0–P8);
  - §8.1 item 4 (Pilot GO readiness: P0–P8 merged);
  - §2 item 3 ("The snapshot changes (P5) cut the fixed cost");
  - D8 / A-472, which decides C1+C2 and the ctime sweep, and B116's status.
- **Missing condition.** S6 depends on S4. R10 §4 says that with shallow only, C1 is still worth ≈0.1 s, and "deferring P5 entirely is reasonable until the pilot's snapshot share is known". D1 `:348` states "**Defer P5's C1/C2 and its ctime sweep.**" as a directive, and C5's table cell "P5 no longer needed" reads as a conclusion.
- **Fix (D1 :387):**
  > | S6 | Once S4 is adopted, defer P5 (C1/C2 + sweep); keep P0's guard tests G1–G5; decouple the assume-unchanged fix | plan §1 and §8.1.4 (drop P5 from "P0–P8"), §2 item 3, §4 and §7; a D8/A-472 note; B116 status |
- **Fix (D1 :348):** replace the bold sentence with "**Proposal (S6): defer P5's C1/C2 and its ctime sweep if the shallow snapshot (S4) is adopted.**"
- **Fix (D1 :374):** "P5 no longer needed" → "P5 deferrable (S6)".

**5. [major] D1 `:282` (RC4) and `:385` (S3). The proposal touches A-472, A-161 and A-470 without naming them.**
- **(a) Carry-in.** Carrying the baseline's `tests/**/__pycache__/*-pytest-*.pyc` into every candidate is residue crossing snapshots:
  - A-472's guard list includes "no residue between siblings";
  - P0 W6 G2 (`b110/P0-measurement-hygiene.md:455-461`) writes `pkg/__pycache__/m.cpython-314.pyc` in materialization A and requires B to lack it;
  - A-161: "ignored/untracked files are not implicit inputs".
- **(b) `--assert=plain`.** This changes A-470's versioned R2 transform `assay-r2-pytest-nocov/1`. It needs a transform-version bump plus an A-470 amendment, not only a "D6-style ruling".
- **Technical caveats the probe must cover** (checked against pytest 9.1.1 source):
  - `_read_pyc` validates a cached rewritten pyc only by source mtime and size, and every snapshot file has `_FIXED_MTIME`;
  - `AssertionRewritingHook.exec_module` runs `exec(co, …)` on cached code without a `co_filename` fix-up, so carried code objects name the baseline snapshot's (deleted) directory in tracebacks and `inspect`. "Byte-identical" source does not imply identical behavior for tests that read frame or traceback paths.
- **Fix (D1 :385, S3 "Needs" cell):**
  > a gate probe (cold vs. warm collection, plus a check that no test depends on `co_filename`/traceback paths); then either (a) carry-in: an A-472 amendment and an A-161 note, re-specifying P0 G2 to admit exactly the harvested test-pyc set, digest-bound and never `src`; or (b) `--assert=plain`: an A-470 amendment bumping the R2 transform version.

**6. [major] D1 `:384`, `:393`; D2 `:597`. S2 is scheduled ahead of S1, which it depends on.**
- **What.** The order paragraph places S2 with P0/P1 but leaves S1 out.
- **Evidence.** R9 RC1's claim-effect column says "B105's R0 gets narrower, and only the release-gate binding restores it". D1 C2 itself says that without S1, "the release lane keeps it" "transfers no assurance". Landing S2 before S1 widens an unbound narrowing of B105's R0.
- **Also missing.** S2 and S7 reopen the P1 brief after the round-3 cap, so it needs a fresh pre-dispatch review (plan status block).
- **Fix (D1 :384):** S2 "Needs" → "S1 first (or in the same change); A-468 amendment; P1 brief + fresh pre-dispatch review".
- **Fix (D1 :393 and D2 :597):**
  > S1 with S2, then S4, S7, S9 and the S3 probe, belong with P0/P1 (proposal; each needs the decision listed above).

**7. [major] D1 `:191` (G7). The claim is contradicted by the same document and by A-182.**
- **What.** G7 says verify.py's reuse of the verdict dataclasses "keeps the 950 candidates in the two files from being a doubled set".
- **Evidence:**
  - D1 C4 `:358` and R11 §3: 290 of verify.py's 327 candidates deliberately re-check producer rules (A-182);
  - verify.py's own stages 2–3 (lines 45–63) say the raw checks "live inside `Verdict.__post_init__` too", and that R3/R4 are hand-transcribed because importing the producer "is exactly what A-182 forbids";
  - A-056 and A-129 cover not re-implementing JSON Schema; they say nothing about avoiding duplicated verdict rules.
- **Fix (D1 :191, "Assay evidence" cell):**
  > [I] Not measured per site. Assay's verify.py is the counter-example by design: it reuses verdict.py's dataclasses only for schema conformance (verify.py:1-30, A-056, A-129), while its raw cross-checks and R1–R3 re-derivation deliberately restate producer rules (A-182), so 290 of its 327 candidates are twins (R11 §3). Within one trust side, R11 finds ≈111 removable guard candidates.

**8. [major] D1 `:280` (RC1/RC2). The report overclaims that the false-kill channel is closed.**
- **What.** "It also closes a false-kill channel: 82 literal subprocess `timeout=` values in 19 test files can fail under load…" reads as if RC1/RC2 remove all 82.
- **Evidence.**
  - RC1/RC2 move only these: the PQ file, `test_distribution_gate`, `test_distribution_release_wheel`, the standalone negatives and the cmru harness.
  - A naive `grep -rnE 'timeout=[0-9]' tests/` finds 88 lines in 21 files. Only ≈19 are in RC1/RC2 files (dist_gate 11, PQ 5, release_wheel 3).
  - Many others stay in R2: `test_distribution_build_release` 7, `test_gate_qualify_dstdns_sql` 8, `test_b105_isolation_*` 8, runner/measurability ≥5.
- **Fix:**
  > It also narrows a false-kill channel. R9 counts 82 literal subprocess `timeout=` values in 19 test files; any of them can fail under load, and any non-zero exit is `killed` (`mutation.py:1683-1686`). The moved files hold only a minority, so the rest still need their time dependence removed (nyxloom LESSONS L20).

**9. [major] D5 `TESTING-METHODOLOGY.md:309-315`. "The measured causes were all design facts, not tool facts" is false.**
- **Evidence.** Analysis §5 root causes #1, #2 and #7 are tool facts:
  - #1: the declared command had no stop condition and the witness plugin was ineligible, so every kill ran the whole suite. That is the largest single cause;
  - #2: a 60 s planner constant;
  - #7: `jobs=1` with joined waves.
  The snapshot shape is the tool's P22 design plus a lane opt-in (`snapshot_history="full"`). Canonical doctrine would teach other projects to discount runner fixes.
- **Fix:**
  > The largest cause was a tool fact: the runner had no early exit, so every killed mutant still ran the whole suite (being fixed as a "cold witness"). The other measured causes were design facts:

**10. [major] D5 `:355-358`. The per-mutant comparison with runner optimizations is overclaimed.**
- **What.** "test bytecode reuse, a shallow project-bounded snapshot and removing mutant-independent tests were each worth more per mutant than the planned runner optimizations".
- **Evidence.**
  - Early exit (cold witness) takes a kill from ≈520 s to ≈10 s (analysis §6, §9 row 2). RC4 is ≈4–5 s [I, not yet probed] and shallow is ≈1 s [M]. Removing mutant-independent tests saves ≈0 on cold kills; the tests sit in `zz_slow`, and the saving lands on survivors.
  - All three are unratified proposals (S2–S4).
  - The statement is true only against P5 (C1 measured at 0.1–0.2 s).
- **Fix:**
  > In assay's case, early exit at the first failing test was by far the largest lever. After it, the estimated per-mutant savings from test-bytecode reuse (≈4–5 s, not yet probed), a shallow project-bounded snapshot (≈1–2 s, measured) and removing mutant-independent tests each exceeded the planned snapshot-copy optimization (≈0.1–0.2 s, measured).

**11. [major] D5 `:391-394` (guideline 7). The advice is wrong as general doctrine and contradicts the same section.**
- **What.** "When verification must be independent, reuse the producer's construction rules rather than re-implementing them (assay's `verify.py` does this)."
- **Evidence:**
  - it contradicts `:432-433` of the same section ("Deliberate duplication, such as an independent verifier re-checking a producer's rules, stays");
  - it contradicts assay A-182 ("leaving these only in producer constructors lets a raw verifier share producer trust");
  - it contradicts verify.py's own docstring (R3/R4 hand-transcribed per A-182);
  - it contradicts the estate rule "a check is only as strong as what it compares". A verifier that reuses the producer's rules compares the producer with itself.
- **Fix:**
  > 7. **State each boundary condition once per trust side.** Shared validation helpers mean fewer mutants and fewer equivalent mutants. Never share logic across an independence boundary: an independent verifier re-derives the relations it checks, and may reuse the producer's data model only for shape or schema conformance. Assay's `verify.py` reconstructs `verdict.py`'s dataclasses for schema conformance but restates the cross-field rules (A-182). That duplication is the price of independence: count it, don't remove it.

**12. [major] D5 `:317-319`, `:436`. The cited evidence does not exist on `main` (merge-order precondition).**
- **Evidence.** `git cat-file -e` shows that `assay-B110-REUSE-AND-TESTABILITY-2026-09-28.md`, `assay-B110-RUNTIME-ANALYSIS-2026-09-28.md` and `b110/research/R10-snapshot-structural.md` are missing on `main` (87c13eff) and on `docs/testability-cleanup-20260928`. They exist only on `assay-b110-landing`. Merging 703505aa first leaves canonical doctrine citing non-existent files.
- **Also:**
  - "(Part B)" is incomplete: the DRY and environment material comes from Part C / R10 / R11;
  - the analysis file is named without its directory;
  - "research R10" has no path.
- **Fix.** Merge only after `assay-b110-landing`, and say so in the merge plan. Replace `:317-319` with:
  > Evidence and numbers: `assay/nyxloom-trove/reports/assay-B110-REUSE-AND-TESTABILITY-2026-09-28.md` (Parts B and C), `assay/nyxloom-trove/reports/assay-B110-RUNTIME-ANALYSIS-2026-09-28.md`, and the research records `assay/nyxloom-trove/reports/b110/research/R9-heavy-tests-structural.md`, `R10-snapshot-structural.md` and `R11-dry-libraries.md`.

  At `:436`, write "(assay B110, `…/b110/research/R10-snapshot-structural.md`; one host, n=3, directional)".

**13. [major] D5 DoD line `:650-654`. The checklist line instructs the half the section calls unsafe.**
- **What.** "Tests of installed artifacts go to a release lane, not the per-mutant suite".
- **Evidence.** The section's own table row (`:353`) says that without a same-commit release-gate binding, "the move silently drops assurance". The DoD line is what authors actually follow.
- **Fix:**
  > Tests of installed artifacts go to a release lane, not the per-mutant suite, and "done" then requires a same-commit pass of that release lane.

**14. [major] D6 KI-35 "Observed". The headline count is wrong: 19 withheld, not 18.**
- **Evidence.** A fresh `cmru abandon --dry-run` from source shows 0 candidates and **19** withheld (`grep -c '^Withheld:'` = 19). There are 19 `cmru-release-*` worktrees, and all 19 were created 2026-09-13 … 2026-09-27, before the 2026-09-28 cleanup (`stat` of each `.git`). So the cleanup-time totals were 5 candidates + 19 withheld = 24, not 18/23. The commit message repeats "18 of 23".
- **Fix.** Change "the 23 retained" → "the 24 retained" and "18 withheld" → "19 withheld". Or, if 18 is a copy of that run's output, attach that output; the current state contradicts it.

### Minor

**15. [minor] D1 `:31`. CPU attribution and label.**
- **What.** "The campaign used ≈0.85 of its 3 CPUs [M]".
- **Evidence.** Analysis §4.5 says the *preflight* used 0.89 [M]; 0.83 is [unverified]. "≈0.85" blends the two under [M].
- **Fix:** "The preflight used 0.89 of its 3 CPUs on average [M] (a 0.83 reading is unverified)."

**16. [minor] D1 `:131` (A6). Arithmetic.**
- **What.** 9.4–11.4 s × 3,760 / 3 / 3,600 = **3.3–4.0 h**. The quoted 3.3–4.7 h pairs this range with the upper bound for today's 13.4 s (analysis §1.7 conflates the same two rows).
- **Fix:** "That is 3.3–4.0 h".

**17. [minor] D1 `:374`, `:378`; D2 `:585`. The C5 scenario arithmetic does not follow as written.**
- **What.** Straight subtraction (9.4 − 5 − 1 − 1, 11.4 − 4 − 1 − 1) gives 2.4–5.4 s, not 3.5–6 s. It also double-counts: after P5, S is only 1–2 s [A], so the two ≈1 s snapshot savings cannot both come out of it.
- **Recomputed by component:**
  - S ≈ 0.46 s (R10 shallow + `assay/`-only);
  - C = 7.9–8.9 − RC4 4–5 = 2.9–4.9 s;
  - O ≈ 0.5 s;
  - total ≈ **3.9–5.9 s**.
- **Also.** "comfortable" (`:378`) holds only if S3 (probe + decision), S4 and S5 (an A-161/A-269 decision) are *all* adopted.
- **Fix (cell):**
  > S ≈ 0.5 s (S4 + S5, R10) + C ≈ 2.9–4.9 s (RC4, [I], unprobed; the no-cov C is unmeasured) + O ≈ 0.5 s → **≈4–6 s**; P5 deferrable (S6)

  **Fix (`:378`):** add "if S3, S4 and S5 are all adopted".

**18. [minor] D1 `:353` (C4). The minutes figure depends on an unstated per-kill cost.**
- **What.** "≈27–37 min … [C]" uses R11's 12.5 s/kill, which is roughly the "mean P = 3 s after P5" row. Under C5's own scenario (≈4–6 s + P), the same 395–528 candidates are worth ≈8–18 min plus their prefix share.
- **Fix.** Add "(at ≈12.5 s per kill, R11 §5.1; less if C5's scenario holds)".

**19. [minor] D1 `:348`. The C1 saving is misattributed.**
- **What.** "The plan assumed 0.25–0.85 s."
- **Evidence.** 0.25–0.85 s is research R2's figure for C1 (`R2-snapshot-internals.md:226`). The P5 brief (`:24`) estimates 0.6–1.4 s for C1+C2 together.
- **Fix:** "Research R2 estimated 0.25–0.85 s for C1, and the P5 brief 0.6–1.4 s for C1+C2; R10 measured C1 at 0.1–0.2 s (full) and ≈0.1 s (shallow)."

**20. [minor] D1 `:344`. A table row is mislabeled.**
- **What.** "Shallow, `assay/`-only worktree 0.46 s" was measured as "shallow **+ refresh**, `assay/`-only worktree" (R10 `:181`), i.e. with P5's C1.
- **Fix.** Relabel it "Shallow + refresh, `assay/`-only worktree". Without the refresh it is ≈0.03 s more (R10 §4).

**21. [minor] D1 `:329`; D2 `:585`. The "saves nothing" claim for eatmydata is unscoped.**
- **Fix (D1):**
  > **Saves nothing in the snapshot path** (0 fsyncs [M]); test-side, at most ≈0.16 s per candidate that runs the `built` clones [M]; pip/venv steps were not traced.

  **Fix (D2):** "…so eatmydata saves nothing there".

**22. [minor] D1 `:243` (B4 item 2); D2 `:578` (R6). The reflink claim is stale against Part C.**
- **What.** "It turns ≈130 MB of copies per candidate into metadata operations … an environment item."
- **Evidence (R10 §3.3):** the worktree is written from `cat-file`, not copied, so reflink helps only the pack copy unless Design B (C4) is built. It also needs a new XFS/btrfs LV on the production host (never a loop file).
- **Fix.** Append to both: "Only the pack copy benefits unless the optional C4 template copy is built; it needs a new LV on the production host (no loop file); pursue only if the pilot shows I/O matters (Part C, C3)."

**23. [minor] D1 `:271`. Measurement label.**
- **What.** "≈5.5 CPU-s per candidate [M]".
- **Evidence.** It was measured per collection in the devcontainer, without coverage, at load ≈13. Its share of the gate's 7.4 s is estimated at 4–5 s [I] (R9 §5).
- **Fix:** "≈5.5 CPU-s per collection, measured in the devcontainer [M]; ≈4–5 s of the gate's 7.4 s [I, needs the S3 probe]".

**24. [minor] D1 `:356`, `:359`, `:361` (C4). Three small misquotes of R11.**
- `:356`: "(≈35)" is placed under `verdict.py:4769-4910`. It is ≈20 there + ≈15 in verify.py (`_raw_claim`, R11 §6).
- `:359`: "verdict, runner and mutation changed in 5–8 of 12". Those three are 6–8 of 12; 5/12 is verify.py.
- `:361`: "because `b105_report_check.py` checks only `src/assay`" is imprecise. The targets are pinned by `test_self_lane.py:128-135`, and the checker binds commit/tree/lane/version/wheel (`:14-104`), so imported code passes both (R11 §5.2).
- **Fix:** correct each accordingly.

**25. [minor] D1 `:53` (A2 table, P1 row). Wrong citation.**
- **What.** "(A-464, analysis §3)". A-464 governs deadlines and time; analysis §3 is the evidence-base table.
- **Fix:** "(analysis §8; A-470's prefix manifest)".

**26. [minor] D1/D2/D3. Identifier collisions.**
- The proposals R1–R6 (D2 §12, D3 `:11`) collide with rigor R0–R3 and with research records R1–R11. For example, "R6" is both the hang-loop research record and the shallow-snapshot proposal, and "R2" is both mutation rigor and "front-load".
- The guidelines G1–G12 collide with P0's guard tests G1–G5. S6's "keep P0 G1–G5" sits in the same document as guideline references.
- **Fix.** Rename the proposals to RP1–RP6 and the guidelines to TG1–TG12. (nyxloom numbers its guidelines 0–12.)

**27. [minor] D3 `b110/README.md:11`. The index row omits Part C.**
- **Fix:** "…(non-binding proposals R1–R6 and S1–S10, guidelines G1–G12; Part C summarizes research R9–R11)".

**28. [minor] D1 `:389` (S8). The "small P0/P3b extension" hides a possible wire change.**
- If failing node IDs go into the v14 `evidence` object, that changes plan §5 and A-470 ("An `evidence` object always has all six keys").
- **Fix.** Name the location: a state-record or sidecar field outside the v14 wire, or else "A-470 amendment".

**29. [minor] D1 `:309`, `:284`. Labels and scope.**
- `:309`: "**Full history: no.**" rests on static reading (R10 §6: "Every PASS under shallow … is [I]"). Add "[I], pending S4's drift-proof preflight".
- `:284` (RC6): "drop the second zipapp build". Say it *moves* the byte-reproducibility check to the release lane. Otherwise it narrows R0 and needs S1.

**30. [minor] D1 Part C evidence. The committed strace outputs cannot be told apart from failed runs.**
- **What.** `scripts/r10/strace-outputs/strace-cand-{full,nofsync,refresh}.txt` and `sanity2.txt` are 0-byte files.
- **Evidence.** strace 6.13 `-c` with zero matching calls does write an empty file (reproduced here), so they are consistent with "0 fsyncs". But the same output results if the traced command died early, and `sanity2.txt` is empty and unexplained.
- **Fix.** Add `scripts/r10/README` recording the exact strace commands and each run's exit status.

**31. [minor] D5 `:350`. The bytecode-reuse advice is stated without caveats.**
- **Evidence.**
  - Assay treats this as an unprobed [I] proposal that conflicts with a "no residue between candidates" invariant;
  - pytest validates its rewrite cache by mtime+size only;
  - carried code keeps the original tree's `co_filename`.
  See finding 5.
- **Fix (cell):**
  > In a fresh-tree-per-mutant design, consider reusing the unchanged **test** bytecode (never the mutated source's): bind it by content digest (pytest checks only mtime and size), and check that no test depends on code-object paths. Or run with plain asserts. Measure first.

**32. [minor] D5 `:360-364` (False kills). The advice omits L20.**
- **What.** "Keep such tests out of the per-mutant suite" is containment. LESSONS L20 says a load-sensitive test is a TRUE red whose fix is removing the time dependence.
- **Fix.** Append: "and fix the time dependence itself (LESSONS L20); moving the test only contains it."

**33. [minor] D5 `:436-445`. Measurements are generalized beyond what was measured (one host, n=3, directional).**
- "A plain copy of a prepared base is no cheaper than a fresh write on ext4/overlay2" → "was measured no cheaper on this host".
- fsync "in a typical snapshot/materialization path" → "in a path that copies packs itself and writes only loose objects and an index; a `git clone` fsyncs its pack (≈27 ms per fsync measured)".
- "usually the largest environment-free saving" → "were the largest measured environment-free saving in assay's case".

**34. [minor] D5 `:425-429`. The DRY paragraph overstates two points.**
- "internal consolidation alone removed an estimated 10–14%" → "would remove an estimated 10.5–14% (static estimate; not done)".
- "name the pinned, independently qualified library version" → "name the library's pinned content (e.g. its git subtree OID) and its own verified qualification verdict". R11 §5.2 says explicitly that the pin is a subtree OID, not a version or wheel digest.

**35. [minor] D5 `:324`, `:399-400`. Terminology and determinism.**
- "qualifying run" is an assay term not defined in nyxloom → "a passing run (every mutant killed)".
- Guideline 9, "Detect order dependence in ordinary CI (random order, repetition)", sits uneasily with the DoD "Determinism" line (`:671-673`). Add "in a separate, non-gating job with a recorded seed; the gate lane itself stays deterministic".

**36. [minor] D6 KI-35. The list of withheld reasons is incomplete.**
- **What.** It omits "release progress metadata is missing" (`cmru-release-20260917_041022-all-891e9fce`).
- **Current tally:**
  - 9 "original snapshot commit is unavailable";
  - 5 scope metadata missing/malformed;
  - 3 published;
  - 1 progress metadata missing;
  - 1 untagged publisher.
- **Fix.** Add the fifth reason.

**37. [minor] D6 KI-35. A ninth dirty worktree is the riskiest one and goes unmentioned.**
- **Evidence.** `cmru-release-20260917_043157-all-d5847937` has an **empty index**: `git ls-files` = 0, with 4,644 staged deletions while the files remain on disk. It is the most hazardous case for the "remove with raw git" path the KI describes.
- **Fix.** Add to Observed: "One more (`20260917_043157-all-d5847937`) has an empty index (4,644 staged deletions)". Reference it in Wanted #2.

**38. [minor] D6 KI-35 "Also observed". The gap is larger than an old install.**
- **What.** The claim is true: the installed `5.4.2.dev365+ge5e9b95c` has no `abandon` verb. But *no released* cmru contains `cmru abandon`.
- **Evidence.**
  - `cmru-v5.5.0` (tagged 2026-09-26) still ships only `release --abandon` (`cli.py:2638`);
  - the `abandon` verb (`def _abandon`) landed in `c54e9058` (2026-09-24), which is not an ancestor of `cmru-v5.5.0`;
  - `CHANGES.md` `[Unreleased]` does not mention it.
  So upgrading to the latest release would not help, and KI-29's "SHIPPED" status is source-only.
- **Fix.** Append: "No released cmru (latest `cmru-v5.5.0`) contains `cmru abandon`: KI-29 is shipped in source only, and `CHANGES.md` `[Unreleased]` does not list it." Optionally file the CHANGES gap as its own KI.

**39. [minor] D2 `:597`. A sequencing sentence reads as a directive in the plan.**
- **What.** "S2, S4, S7, S9 and the S3 probe belong with P0/P1, before the pilot sizes anything." sits inside a plan file whose §3 is binding. It reads as a directive, and it omits S1 (finding 6).
- **Fix.** Use the text in finding 6 and mark it "(proposal)".

---

## Verdicts

| Document | Verdict |
|---|---|
| D1 `assay-B110-REUSE-AND-TESTABILITY-2026-09-28.md` | **READY-WITH-FIXES.** It is correctly marked non-binding throughout, and nothing silently amends D1–D10 or A-465..A-474. But findings 2–8 are false premises or incomplete decision lists and must be fixed before merge. Minors 15–30 are recommended. |
| D2 plan §12 | **READY-WITH-FIXES.** Findings 2, 3, 4, 6, 17, 21, 22, 39. |
| D3 `b110/README.md`, `b110/research/README.md` | **READY-WITH-FIXES.** Finding 27 (the research README is fine). |
| D4 merge `a004286d` | **READY.** Clean merge, no evil changes. |
| D5 `nyxloom/reference/TESTING-METHODOLOGY.md` | **NOT READY.** Finding 1 (blocking: deleted heading, dangling cross-reference, Hypothesis content re-parented), plus majors 9–13. Merge only after `assay-b110-landing` (finding 12). |
| D6 `cmru/KNOWN_ISSUES_TODO_BACKLOG.md` KI-35 | **READY-WITH-FIXES.** Finding 14 (major count), plus 36–38. KI-29 is represented fairly. |
