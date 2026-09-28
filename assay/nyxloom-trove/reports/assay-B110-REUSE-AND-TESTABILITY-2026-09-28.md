# B110 reuse review and design for mutation testability

**Written:** 2026-09-28, after the three pre-dispatch review rounds of the B110 plan. The author is a Claude Opus 5.5 session working with the operator.

**Status:** analysis and proposals only. **Nothing here is a binding decision.** No `decisions.md` row, backlog item or brief was changed because of this document. The binding set stays in [`assay-B110-PLAN-2026-09-28.md`](assay-B110-PLAN-2026-09-28.md) §3. Where a proposal below would change the plan, it says so and names the decision it would need.

**Companions:**
- [`assay-B110-RUNTIME-ANALYSIS-2026-09-28.md`](assay-B110-RUNTIME-ANALYSIS-2026-09-28.md): measured facts, cost model and external comparison. Every number below is taken from it unless marked otherwise.
- [`assay-B110-PLAN-2026-09-28.md`](assay-B110-PLAN-2026-09-28.md): the actionable plan. Its §12 summarizes this document.
- [`b110/README.md`](b110/README.md): the package briefs.

**Labels:** [M] measured, [C] calculated, [I] inference, [U] unverified.

**Contents:**
- Part A answers two questions: are we reinventing the wheel, and what does the plan change?
- Part B answers a third: can a product be designed so that R2 costs less, and what does that mean for assay?

---

# Part A. Reuse review of the B110 plan

## A1. What has been done so far

**The problem.** B105 is assay's own full-source self-qualification (R0–R3) on one exact revision. Its R2 inventory has 3,760 candidates, and each ran the declared 5,831-test suite. The measured opening prefix projects about 585 worker-hours [C] against a 6 h target. The analysis found the causes:
- **Every candidate paid about one baseline.** It ran the whole coverage-instrumented suite in a fresh interpreter, on a fresh full-history snapshot of the whole vbpub repository. Killed and surviving candidates cost the same, ≈505–560 s [M].
- **The planner never saw a measurement.** It fell back to a hard-coded 60 s per candidate (`cli.py:1786-1801`), about 9× too low.
- **Spinning hangs cost three baselines each** (`budget_exceeded`, ≈1,518 s [M]).
- **The heavy tests run last.** 113 tests of 0.5 s or more account for 72% of call time [M], and alphabetical file order runs them late.
- **Some tests can never kill a mutant.** `tests/test_python_qualification.py` never executes snapshot source, yet costs ≈94 s per candidate [M].
- **CPU sat idle.** The campaign used ≈0.85 of its 3 CPUs [M].
- **The structural finding: a passing native R2 contains only kills.** Native Python has no equivalence disposition and no `fail_under` (`mutation.py:3748-3769`, `config.py:3082-3106`, A-462). The opening prefix had 7/15 and 9/39 survivors, so the 585 hours was the price of a campaign that would have failed anyway.

**The work done.** All of it is docs, committed in the separate clone `.worktrees/.assay-b105-ciu-root-20260926-30eec294-copy` on `assay-b105-evidence-integrity` (`a3b68800`, `3ee09b61`, `210d6130`, `5eb5768f`):
- the operator interview, recorded as decisions A-465 to A-474;
- the analysis report, the plan and 15 package briefs (B111–B121);
- three review rounds using about 35 subagent sessions: research, writers, reviewers and fixers [C, counted from the session record].

That is about 1.04 MB of plan, analysis, briefs and review summaries [M, `wc -c`], not counting the verbatim research records and reviewer outputs. **No product code has changed and no package has been dispatched.**

## A2. Are we reinventing the wheel?

**Short answer: yes for the execution mechanics, no for the evidence layer.** Almost every speed-up in the plan is a mechanism PIT, Stryker or mutmut have shipped for years. Analysis §8 says so directly: no established tool re-runs the full, coverage-instrumented suite in a fresh interpreter per mutant. Today's assay design is the outlier, and B110 mostly brings its execution in line with the field. What no surveyed tool offers is the evidence layer:
- a verdict bound to an exact revision;
- `assay verify`;
- a fresh private snapshot per candidate;
- receipts;
- refusal instead of a guess when evidence is ambiguous.

| Package | What changes in assay | Existing-tool equivalent | Verdict |
|---|---|---|---|
| P3b cold witness | stop a candidate at its first verified test-call failure | pytest `-x`; PIT and Stryker stop at the first failure by default | Mechanism is standard. The receipt (a failed-call proof plus a prefix manifest digest) is assay's addition. |
| P1 tiered order | heavy tests move to `tests/zz_slow/`; one declared order shared by R0/R1/R2 | PIT runs the fastest tests first, chosen per mutant | Assay needs one static order because per-candidate reordering changes what is proven (A-464, analysis §3) |
| P4 work queue | 3 workers, no joined waves | every tool runs in parallel | Standard and small; fine to own |
| P5 snapshots | cheaper fresh private repo per candidate | Cosmic Ray rewrites the file in place; mutmut switches mutants inside one process | Needed to keep per-candidate isolation (the stale-pyc false-kill hazard was reproduced) |
| P0 / P2 / P7 | measured planner; loop guards in assay's source; pilot selection | mostly glue, plus fixes to assay's own code | Needed whatever the design |
| P6 campaign deadline | persisted deadline; a cut-off candidate is unclassified, never guessed | every tool has timeouts | The "time never classifies" semantics (A-464) are assay's addition |
| P10 equivalence ledger | reviewed, scope-fingerprinted, audited equivalents | `# pragma: no mutate`, `// Stryker disable`, PIT exclusions | Heavier by design: each entry is reviewed and re-checked |
| P9 distributed evidence | portable records, conflict-refusing import, nonce audit sample | Cosmic Ray distributed mode, cargo-mutants `--shard`, Buildkite | Mechanics are standard; the trust model is assay's addition |
| P8 campaign analysis | survivor, hang and crash lists; projections | Stryker's HTML report and dashboard, on the mutation-testing-elements schema | Partly reinvented (see A4 item 4) |

**Why the plan cannot simply adopt those tools.** Their main speed-up is choosing tests per mutant (PIT, Stryker `perTest`, mutmut's stats run). That changes what a survivor means, from "survived the declared suite" to "survived its covering tests". mutmut and Stryker also switch mutants inside an instrumented process instead of running an exact snapshot. B105's claim forbids both. So the plan rebuilds the mechanisms that still fit inside the claim: early exit, a fixed order, a work queue, cheaper snapshots.

## A3. The existing wrap-a-tool path the plan did not evaluate

**Assay can already run a foreign mutation tool and judge its report.** This is ingested R2 (B046, schema v9; DESIGN-GUIDE "Ingested R2"):
- The lane's own argv runs the tool inside the private snapshot.
- Assay parses the report through a format-keyed registry. Today the only format is the mutation-testing-elements JSON that Stryker writes (`src/assay/mutation_parsers/mutation_report_json.py`).
- It applies three non-repudiation tiers: identity, anchoring, and content read back from the committed blob.
- Admission is keyed by format, not by language: "any language whose tool emits a registered format may ingest" (`config.py:2969`).
- Ingested lanes may set `fail_under` (`config.py:457`).

**So "reuse the tool, wrap assay's logic around it" is already a supported model.** For Python it would look like this: the lane runs mutmut (or Cosmic Ray) plus a small emitter that writes the registered format, and assay binds and verifies the result.

**The catch: it establishes a different, weaker claim,** which would need its own name (analysis §9 row 13 pattern):
- mutmut mutates only inside functions. The 190 import-time candidates, including the 148 dataclass flag flips, would not be in the inventory at all.
- A survivor would mean "survived its covering tests", not "survived the declared suite".
- mutmut runs the trampolined source in a forked process, not an exact snapshot per mutant.

**Unverified [U]:**
- whether mutmut 3.8 or Cosmic Ray 8.7 can write mutation-testing-elements JSON natively;
- whether ingested R2 supports the whole-source scope that B105 declares. DESIGN-GUIDE says R2 "never silently upgrades itself into a whole-project … audit"; check before relying on it.

**Where the ingested path fits:** as a separately named fast tier (for example, a per-change regression lane on consumers' Python code), or as the survivor screen's scout (A4 item 1). It cannot fit as the B105 qualifying claim.

## A4. Proposals: reuse more, build less

Ranked by expected value. None is ratified.

1. **Use a tool's fast path in the survivor screen, where no claim is made.** Plan §9.1 runs the screen through the full qualifying attempt path. Every survivor therefore costs a whole declared suite (≈385 s after P1 [C]) on every re-screen iteration. B106 replays only kills, and the judge identity changes with each fix commit.
   - **Proposal:** in the screen only, select the covering tests from the per-test coverage map that pilot phase B already builds (coverage.py `--cov-context=test`), or run mutmut as a scout.
   - **Errors land on the safe side** [I]:
     - a subset that kills implies the full suite kills, apart from order dependence (research R7 scanned for it);
     - a subset that misses a kill produces a false survivor, and triage finds it.
   - **Known gap:** assay's tests run a lot of assay in child processes that the coverage map does not see, because no `COVERAGE_PROCESS_START` is set. The map under-attributes those tests, which only raises the false-survivor rate.
   - **This is the largest saving available in the find-and-fix loop.** It does not touch the qualifying run.
   - **Would need:** a plan §9.1 amendment and a small P7b/P8 extension. It is not a new claim, because the screen is already non-qualifying (A-474).
2. **Front-load the packages that carry most of the saving:** P0, P1, P2, P3b (cold witness) and P4, then the pilot. P5, P6 and v14 are required for correctness and the claim. P9, P10b and P11 are conditional.
3. **Defer P9 (distributed evidence) until remote capacity is measured.**
   - It has the heaviest brief after P3b (81 KB).
   - It is built for capacity the plan itself counts as zero.
   - D7 already defaults to "no pre-deadline evidence".
   - The P3b foresight (runtime fingerprint, portable records) should stay, because it is cheap. The import/audit machinery can wait.
4. **Have native R2 also write the mutation-testing-elements report.** Assay already parses that schema. Emitting it for native lanes too would give P8's survivor triage the existing Stryker HTML report component and dashboard. It would also give a consumer-readable format with `killedBy`/`coveredBy`/`testsCompleted`/`static` fields (analysis §8 transfer column).
5. **Match brief depth to risk.** The briefs average ≈60 KB (up to 86 KB). nyxloom's own `docs/competitive-landscape.md` §5 names spec verbosity as a risk. Keep the deep form for the evidence layer (v14, deadline, ledger, distributed trust); use short briefs for standard plumbing (work queue, pilot selection, analysis). The review rounds justified themselves (see A6), so this is about brief length, not about skipping review.

## A5. How the workflow changes

**Today:** start the `self-qualification` gate, wait, find survivors late, stop, start again. Every candidate runs the full coverage suite, and the planner under-estimates the cost about 9×.

**Planned (plan §7–§9):**
1. **Measure (P0).** `assay plan` uses the measured baseline, so a ≈585 h campaign is visible before it starts.
2. **Pilot (§7).** 64+6 candidates via `--candidates-file`, which is non-qualifying and exits 6. It takes 90 minutes or less and gives the Pilot GO/NO-GO.
3. **Survivor screen (§9.1), repeated until clean.** `run-gate.py b110-screen` runs with a cold witness and resume. Each survivor is fixed with a test, filed in the ledger, given a loop guard, or investigated. B106 replays earlier kills.
4. **Ledger audit (§9.2),** at the same commit, and only if the ledger is non-empty.
5. **Qualifying run (§9.3):**
   - `assay campaign init` persists the deadline;
   - a cold witness on 3 workers, projected at 6 h or less, with an 8 h hard stop;
   - qualifying requires `assay verify`, the `tools/b105_report_check.py --deadline` check and the registered gate's exit 0.
6. **Optional (P9, D7 answer pending):** worker hosts produce records, `assay state import` checks them, and a consolidating `--resume` produces one ordinary verdict.

**Consumers (dstdns, nyxloom and other assay lanes):**
- Verdict schema v14 is a hard cut: every consumer updates its verifier and pins.
- The cold witness (`--cold-witness`) and the liveness lane keys (`judge.mutation.liveness_cpu_window`, `…_idle_floor`) are opt-in.
- Any consumer R2 lane gets the cold-kill and work-queue savings if it opts in. That is the part of B110 with value beyond B105.

## A6. Efficiency: what gets better and what doesn't

| Area | Before | After the plan | Better? |
|---|---|---|---|
| Qualifying run | ≈585 worker-h straight-line [C], and it fails on survivors | ≈9.4–11.4 s fixed per kill after P5, plus the prefix to the first failure. That is 3.3–4.7 h of the 5 h candidate budget on 3 workers before any prefix [C] | Much better, but **borderline** against 6 h. The pilot decides; P11 or measured remote capacity is a likely outcome |
| Survivors | full suite each | still a full suite each (≈385 s after P1), on every re-screen | **No**, unless A4 item 1 is adopted |
| Hangs | 3 baselines each, can never pass | P2 guards turn them into fast kills | Yes |
| Ledger audit | n/a | one full suite per entry, outside the 8 h window | **Moved, not removed** |
| Distributed workers | n/a | spread the same work across hosts | **Moved, not removed** |
| Engineering | — | 15 packages, a v14 hard cut, a gate and a fresh review per package | a real cost |
| Planning | — | ≈1 MB of docs, ≈35 sessions, 0 lines of code | Justified by real defects found before code existed; brief length is the lever (A4 item 5) |

**Defects the review rounds found before dispatch** (plan review files):
- a refusal rule whose verdicts `assay verify` rejects by design (C1, P3B2-1);
- a resume bug that made every resumed campaign unpassable (C3);
- a signal death or OOM counting as a kill (C2, N-14);
- two false-PASS paths in the distributed design (C12, C27);
- circular go/no-go gating (C6).

## A7. Open before dispatch (unchanged; see plan §11 and round-3)

- **D7:** may pre-deadline evidence count? The default is no.
- **OC4/7/10/15/17 and the ledger's narrowing of the ceiling:** needed only for P10b.
- **Reconciliation:** the clone must be brought into canonical vbpub as `assay-b110-integration` (C18). It cannot be fast-forwarded.
- **P3a/P3b fixtures:** they are still recipes. P9's carve log is also missing.

---

# Part B. Designing a product so that R2 costs less

## B1. The cost levers

Under the current single-process claim (plan D3), with the cold witness:

```
qualifying run ≈ T_fixed + Σ_candidates (S + C + P_i) / W
find-and-fix   ≈ Σ_iterations  N_survivors,i × F      (F = one full declared suite)
hangs          ≈ N_hangs × 3F                         (until P2 guards them)
```

Where:
- `S` is the snapshot, `C` is interpreter start plus collection, `W` is the number of workers;
- `P_i` is the time from collection to the first failing test call for candidate *i*;
- the number of candidates grows with the amount of own tracked code in scope times the operator density. Assay's catalogue is compare-swap 2,169, boolop-swap 958, bool-const-flip 498, falsy-swap 135 [M].

So a product can lower R2 effort in five ways. Its design decides most of them:
1. **Short `P_i`:** a fast, local test kills each mutant early.
2. **Cheap `S + C`:** a small snapshot and fast collection.
3. **Few survivors and equivalents:** less find-and-fix and fewer ledger entries.
4. **No hangs.**
5. **Fewer candidates that need executing,** without narrowing the claim.

**Yes: libraries, file sizes and boundaries all play in, but not all in the way one would guess.** B2 lists the guidelines, B3 answers those three factors directly, and B4 lists what assay itself could improve.

## B2. Guidelines, with assay's own evidence

| # | Guideline | Lever | Assay evidence | Existing tool that enforces or measures it |
|---|---|---|---|---|
| G1 | **Functional core, imperative shell.** Put comparisons and boolean decisions in pure functions with direct, millisecond unit tests. Keep subprocesses, git, venvs and clocks in a thin shell. | `P_i` | 4,875 tests under 10 ms cost ≈4 s in total [M]. 162 s of heavy-test time executes snapshot `src/assay` through nested in-process runs [M]. A kill that only a nested run can make is expensive. | coverage.py contexts show which tests reach a line |
| G2 | **Test locality.** Each module's fast tests live in a predictable place, so the killer for module M's mutants is near the front of the order. | `P_i`, P11, B109 | `verdict.py` and `verify.py` hold 950 of 3,760 candidates, and their `test_v*` files ran last alphabetically [M] | per-test coverage contexts; kill locality from cold-witness receipts (B4 item 1) |
| G3 | **Inject time; never wait on it.** Pass a clock or window instead of sleeping on real time. | `P_i`, `F` | 91.0 s per suite run waits on real-time windows; two liveness tests alone cost 66.7 s [M]. Plan D5 makes the windows lane settings, but tests-only injection would be cheaper still. | `grep` for `time.sleep` / `monotonic` waits in tests; freezegun-style injection |
| G4 | **A test of the source must execute the source.** Release and distribution tests that exercise an installed wheel belong in a separate lane. | `F` | 73.6 s on the unmutated PATH wheel and 13.8 s on a committed 1.2.5 wheel can never kill a mutant [M]. Plan D4(a) ignores the file. | a lane split (the `test_self_hosting.py` precedent) |
| G5 | **Every loop over external input provably advances or raises.** | hangs | 4 of 39 e79 candidates spun at ≈1,518 s each, e.g. `adapters/go.py:292` [M]. Research R6 inventories the loops; plan D2 adds `errors.require_advance`. | an AST check for `while` loops without a progress guard (custom, small) |
| G6 | **Declarative structure gets a reflective contract test.** Dataclass flags, enum tables and config key sets are checked by one test that asserts the declared contract. | survivors, `P_i` | 190 import-time candidates (5.1%), 189 of them bool-const-flip; 148 are dataclass `frozen`/`kw_only` flags, and 5 of the first 7 survivors were such flips [M]. One contract test kills them in milliseconds (plan D4(d)). Import-time mutants also defeat schemata and fork-server designs (analysis §3). | pytest; `dataclasses.fields()` introspection |
| G7 | **State each boundary condition once.** Shared helpers for bounds and validation produce fewer candidates and fewer equivalents. When two guards check the same thing, mutating the redundant one is equivalent. | candidates, equivalents | [I] Not measured per site. Assay already does this in one important place: `verify.py` does not re-implement the schema rules. It rebuilds the `verdict.py` dataclass graph and reuses its construction-time checks (`verify.py:1-30`, A-056, A-129). That one decision keeps the 950 candidates in the two files from being a doubled set. | a review checklist; P10 ledger reasons naming the redundant guard |
| G8 | **No unreachable defensive code:** make it reachable and tested, or delete it. | survivors, ledger | [I] Every survivor in a dead branch becomes a ledger entry with a fresh-session review and an audit run (plan D1). | coverage R1 already forces 100% line+branch; a survivor in a covered-but-inert branch is the signal |
| G9 | **Order-independent tests with no shared state.** | enables tiering, P11 units, parallelism | Research R7 ran an order-dependence scan; tiering and P11 are sound only with it | pytest-randomly and pytest-xdist in ordinary CI detect order dependence cheaply |
| G10 | **Cheap fixtures, scoped to the tests that need them.** | `F`, `P_i` | ≈71 s per suite run is non-call time: session and module fixtures, teardown [C]. The `standalone` fixture builds a wheel and a venv (`tests/conftest.py:1431-1499`). | pytest `--setup-show`, `--durations` with setup phases (P0 retains them) |
| G11 | **The project boundary is the snapshot boundary.** Tests do not reach outside their project or into repository history. | `S` | Each candidate snapshots the **whole vbpub monorepo with full history**: ≈130 MB written, ≈490 GB over a campaign, ≈2–4 s [A]. Only the 32 tests in `test_python_qualification.py` need history (they pin a `topos` commit). `test_distribution_build_release.py` clones the snapshot twice per candidate [M]. | shallow snapshot (A-451) once P1 lands (plan §11 item 9) |
| G12 | **Layered module dependencies (a DAG, no cycles).** | enables provable test selection (B109) | [I] A test whose dependency closure excludes module M cannot kill M's mutants, and a layered import graph makes that closure provable. A test that runs the CLI in a subprocess depends on everything. | import-linter contracts; Ekstazi-style file-dependency tracing (analysis §8) |

## B3. The three factors asked about

**Libraries.** Third-party code is not in the mutation inventory, because R2 mutates tracked source in scope. So delegating parsing or formatting to a well-tested library shrinks the inventory and moves that verification burden to the library's maintainers. Assay deliberately goes the other way:
- It ships zero runtime dependencies (A-005). `jsonschema` is a test-only extra (`verify.py:14-16`), and the SQL lexer is "stdlib-only" (DESIGN-GUIDE).
- The price is candidates: every hand-rolled parser (`adapters/sql_lex.py`, `adapters/go_stmtpos.py`, the `coverage_parsers/` and `mutation_parsers/` modules) needs its own killing tests.
- It is a legitimate trade (supply-chain minimalism, reproducible wheels), but it should be made knowingly.

Libraries used by the *tests* matter more to runtime than libraries in the product. pip, venv builds and real git are why the heavy tests are heavy (heavy-test categories (b) 54.2 s and (e) 24.9 s [M]).

**File and module size.** Under today's model, size barely matters to per-candidate cost, because every candidate pays the whole suite. It starts to matter in four places:
1. **Isolation units (P11):** a unit is a test file. A 2,565-line `test_cli_run.py` that mixes millisecond tests with 30 s liveness waits makes a poor unit.
2. **Reuse and fingerprints:** B109's dependency-aware reuse and P10's `scope_sha256` both invalidate on change within an enclosing scope, so large scopes invalidate more after an edit.
3. **Import cost in subprocess tests:** every test that runs the `assay` CLI in a child imports the large modules (`runner.py` 6,554 lines, `verdict.py` 5,455, `mutation.py` 3,812, `config.py` 3,773 [M]). Lazy imports in the CLI entry could cut that. [I, unmeasured]
4. **Readability of kill locality:** a 6.5k-line module has no single natural "its tests" file (G2).

Splitting a big module is worth it when it creates a clear layer or unit, not for its own sake.

**Boundaries.** This is the strongest lever. Four boundaries decide how much work R2 needs:
- **Source vs. installed artifact (G4):** tests must execute what is mutated.
- **Project vs. monorepo (G11):** the snapshot copies everything inside the git boundary.
- **Module layers (G12):** they decide whether test selection can ever be proven safe.
- **Pure core vs. shell (G1):** it decides whether kills are milliseconds or tens of seconds.

## B4. Room for improvement in assay

**For assay as a product (useful to every consumer):**
1. **A mutation cost profile in `assay analyze campaign` (P8 extension).** Per source file:
   - candidates, kill rate, median and p90 time-to-first-kill;
   - the killer test files (kill locality);
   - survivors.

   Per test file: its cost and how many kills it made. This needs the cold-witness receipts from P3b. It turns B2 from advice into numbers a developer can act on. [proposal; small once P3b exists]
2. **A "designing for R2" section in `docs/CONSUMERS.md`,** derived from B2 and backed by B110's measurements. [proposal]
3. **Cheap static advisories,** with no new claim:
   - `while` loops without a progress guard;
   - tests that sleep on real time above a threshold;
   - self-lanes that run the PATH binary;
   - import-time boolean flags with no contract test.

   Prefer existing tools where they exist (import-linter, pytest-randomly) and write only the assay-specific checks. [proposal]
4. **Planner warnings (P0 extension).** `assay plan` flags a suite whose call time is concentrated in a few tests that the declared order runs late. [proposal]
5. **Declared "arid" exclusions** (Google ICSE-SEIP'18: skip mutants in logging and diagnostics). This narrows the inventory, so it is a **separately named claim**. It would need a reviewed exclusion list like the ledger. Listed for completeness; not recommended for B105. [option]

**For assay's own codebase and environment (B105-relevant):**
1. **After P1, move to a shallow snapshot (A-451)** and move the release-clone fixtures out of the self-qualification lanes. This is already an unowned follow-up in plan §11 item 9, and it is the cheapest `S` saving after P5.
2. **A reflink-capable `TMPDIR` for tester-unified.** It turns ≈130 MB of copies per candidate into metadata operations. A-184 already allows it; it is an environment item.
3. **Split the mega test files along the tier line** (`test_cli_run.py`, `test_standalone.py`) so that the fast part stays in the early tier. This is a P1 detail.
4. **Do not split `runner.py` or `verdict.py` as a standalone refactor now.** It pays only under P11 units or B109 dependency proofs. Decide after the pilot.
