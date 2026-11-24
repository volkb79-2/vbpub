# P4: sub-file candidate identity for native R2 carry-over

Read against `main` 3ef9104fb. Every `file:line` is in `assay/` at that commit, given relative to `assay/` unless it names another project.

**Status (2026-11-18).** Binding design note for controller design item P4. Author: the one approved Opus design reviewer (read-only on `src`). It does NOT change `candidate_id`. It adds a second key, the **locus**, that is used only to FIND a prior record after a file edit. Soundness comes from what the run does with the record it found, not from the key. Labels: **M** = measured (source named), **C** = computed here from M data or from git objects, **E** = estimate. §7 records the operator's decisions D1-D6 (2026-11-18).

## 0. Facts from the code that shape the design

- **The candidate id is file-granular.** `candidate_id_from_fields` hashes `path, source_sha256, start_byte, end_byte, mutated_file_sha256, operator` (`src/assay/candidate_identity.py:45-55`). Both digests cover the whole file (`src/assay/mutation.py:1504-1515`). The same id names the state record file (`mutation.py:1754-1769`), orders `plan_sha256` (`1523-1533`) and assigns shards (`select_mutation_shard`, blake2b of the id, `3502-3532`).
- **The judge identity covers the whole tree.** `judge_sha256` folds `tree_sha256`, the content digest of the whole judged tree (sources, tests, conftest, fixtures, data), plus argv, the declared env by value, passthrough-value fingerprints, cwd, prefix, `link_paths`, assay version, the R2 collection/hook/runtime fingerprints and `candidate_fail_fast` (`mutation.py:1611-1751`). So between two releases EVERY record is a cache miss for `--resume`. Records get across a release only through `--carry-over`.
- **Today's `--carry-over` keeps a kill without running anything.** `_carry_over_records` (`mutation.py:3992-4163`) imports any `killed`/`survived` record of another judge that validates for the current candidate id (`_load_validated_state_record(..., accept_any_judge=True)`, `2868-3330`; the judge test is skipped at `3284-3291`). The operator's `--carry-over-reason` is the only guard (`FlexOptions`, `1830-1901`). Consequence (C, by reading): if `a.py` is unchanged and its callee in `b.py` changed, the kills of `a.py` are carried although the mutant may now survive. **Today's file-granular carry-over can therefore already produce a false PASS for kills.** It is sound only for what the reason asserts.
- **B106 replay is not cheap under fail-fast.** `--reuse-from` collects the full suite in its normal order and stops AFTER the recorded node fails (`docs/DESIGN-GUIDE.md:1855-1861`, `1872-1876`; `mutation.py:6638-6672`, mode `witness-prefix`). Its cost is the prefix of the suite up to that node. A `candidate_fail_fast` kill also stops at the first failing test, so the two cost about the same (M: kills median 87 s, mean 234 s, median 1,937 tests before the first failure; research report §2). The brief's premise that "B106 already revalidates a kill cheaply" holds only against lanes that run the whole suite per candidate. B106 also refuses shards (`docs/CONSUMERS.md:4908-4909`).
- **Every survivor fails the lane.** `src/assay/config.py` has no survivor tolerance key (grep: no `min_score`, `max_survivors`, `allow_survivors`; M). The `equivalent` bucket comes from an artifact comparison made in the current run (`mutation.py:4423-4454`). The equivalence-ledger envelope is reserved and disabled (`src/assay/verdict.py:2001-2015`). The verdict does not yet mark carried outcomes: B532 is OPEN, and `VERDICT_SCHEMA_VERSION = 20` (`verdict.py:443`).

## 1. Goal, non-goals, gain

**Goal.** A release that edits a few functions re-judges in full only the candidates whose enclosing function changed. Every other candidate's prior outcome is either re-proved cheaply or carried in a form that can only make the verdict worse, never better.

**Measured basis for the gain** (C; `git show` of cmru `src/cmru/*.py` at the tags `cmru-v7.1.3` .. `cmru-v7.4.0`, 10 release pairs). The candidate proxy counts compare ops, extra boolop operands and bool constants. It omits falsy-swap (3.7 %). It gives 5,023 candidates at 7.4.0, against 5,022 in the real plan (M), so it is a usable proxy. The proxy was a read-only scratch script in the author's session, not in the repo. WP0 replaces it with the real adapter.

| Per release (cmru, mean of 10) | Candidates | Range |
|---|---|---|
| All candidates | 5,023 | 4,774-5,306 |
| Re-keyed, file-granular (candidates in any touched file) | **1,627** (32 %) | 240-3,218 |
| Re-keyed under the function-segment locus (§3 A') | **210** (4.2 %) | 75-447 |
| ... of which in new or changed functions (23.5 functions per release) | 181 | |
| ... of which module or class-body code (file fallback) | 20 | |
| ... of which in new files | 9 | |
| Line delta (research §5, density 0.139 per line) | ~65 | 3-140 |

The brief's "~65-150" is the line delta. Function granularity lands at **~210 (C)**, because cmru functions are large (`_abandon_locked` alone holds 140 candidates). Getting closer to 65 needs a finer key. Option B in §3 is sound under this design too, and WP0 measured it (cmru 79-93 re-keys per release against 211 for A'); the operator adopted B for kill lookup only (§7 D5).

**Cost per release** (serial; C from the M anchors 288 s per fail-fast candidate and 87 % kills; replay cost E):

| Mode | Full judges | Cheap replays | Carried | Serial hours |
|---|---|---|---|---|
| Today: file-granular, `--carry-over` with a reason | 1,627 | 0 | 3,396, kills unverified | ~130 h, not sound for kills |
| Phase 1, strict (§2.3): replay every carried kill | 210 | ~4,190 at 15-30 s (E), plus node baselines | ~626 survivors, pessimistic | ~35-56 h (E), sound |
| Phase 1, kills of unchanged files trusted as today (§7 D2): EXPLORATORY campaigns only; a release claim refuses PASS while a `carried-unverified` kill exists | 210 | ~1,230 | 3,396 unverified + 184 | ~23-31 h (E), as sound as today |
| Phase 2 (§2.5): footprint-proven kills | 210 | 10-40 % of kills (E) | rest | ~19-31 h (E), sound |

**Non-goals.** No change to `candidate_id`, `plan_sha256` or shard assignment. No test selection or tiers (B144/B2xy). No static call-graph analysis as a proof. No non-Python adapter (go, js, rust, sql, cargo-mutants ingestion): those keep exact-id behaviour.

**It must never:**

- report a candidate `killed` on evidence that the current judge did not produce or prove (the strict mode, which every release claim uses; an exploratory campaign may count a disclosed `carried-unverified` kill, §7 D2);
- let a carried record reach a PASS-supporting bucket (for release claims; an exploratory campaign may allow a `carried-unverified` kill under §7 D2 and must disclose the count);
- mix carried and fresh outcomes without per-candidate disclosure;
- need an operator reason for anything it does by itself.

## 2. Soundness argument

### 2.1 What a recorded verdict depends on

A record "killed by node X" states that the suite, run on the mutated tree M, failed at X while the baseline on the unmutated tree P passed. "Survived" states that the whole suite passed on M. Both are functions of:

| # | Input | Covered by a hash of the enclosing function? |
|---|---|---|
| a | the mutation itself (span, replacement, operator) | yes, with offsets taken relative to the function segment |
| b1 | the enclosing function body, signature, decorators, defaults, nested defs and lambdas | yes, if the segment runs from the first decorator to the end of the def |
| b2 | in-project callees | no; a static closure is incomplete (dynamic dispatch, `getattr`, registries, monkeypatching, plugins) |
| b3 | **callers and the consumers of f's results** (a caller that stops checking f's return value turns a kill into a survivor) | no; a callee closure misses them entirely, and the research sketch (§5) omits them |
| b4 | module-level constants and import-time code, class attributes, metaclasses, MRO | no; these are other segments of the same file or other files |
| b5 | data files read, directory listings, the set of files on `sys.path` (a new `json.py` can shadow a module) | no |
| c | tests, fixtures, `conftest.py`, pytest config, plugins, env, argv, interpreter, installed packages, assay version (it classifies the result) | no; these are the judge identity minus the tree |
| d | test selection and order: the fail-fast stop point, and state leaked from earlier tests | no |

**Conclusion.** No static identity, whether file, function or function plus callee closure, can prove a carried kill current. The function hash covers a and b1. The callee closure adds part of b2 and none of b3-d. A design that trusted kills on such a key would be unsound by construction. This design therefore does not use the key as a proof.

### 2.2 The asymmetry: which stale record yields a false PASS

- **A stale kill** is a record "killed" for a candidate that now survives. It contributes to a PASS. **This is the only direction that can produce a false PASS.** A callee, a caller, a constant or a weakened test can each create one.
- **A stale survivor** is a record "survived" for a candidate that is now killed. Every survivor fails R2 (§0), so it can only turn a true PASS into a FAIL. It hides an improvement and adds noise to the survivor list. It can never produce a PASS. The same holds for a survivor carried onto the wrong candidate through a locus collision.
- **A stale `equivalent`, `crashed`, `hung` or `budget_exceeded` record** is never carried (`CARRY_OVER_IMPORTABLE_BUCKETS`, `mutation.py:1805`). That rule stays.

Hence the design rule: **a carried KILL must be re-proved by the current judge before it counts (strict mode, mandatory for release claims; an exploratory campaign may keep a disclosed `carried-unverified` kill, §7 D2); a carried SURVIVOR may be carried pessimistically, with disclosure.**

### 2.3 Phase 1 theorem (no new trust)

The current judge J runs the current plan. Every candidate C ends in exactly one of five ways:

1. **Exact resume.** The same `candidate_id` and the same effective judge (unchanged; `mutation.py:5640-5669`).
2. **Fresh full judge.** Unchanged.
3. **Fresh isolated kill.** A prior `killed` record with witness node X is found for C by exact id or by locus. C is killed only if both of these hold, in J's snapshots:
   - (i) X alone passes on P, the **isolated node baseline**;
   - (ii) X alone fails on M, with the witness receipt conditions of B106: X occurs once, it fails in the call phase, nothing else fails, pytest status 1, child status 1 (`docs/DESIGN-GUIDE.md:1857-1866`).

   Anything else is a fresh full judge.
4. **Carried survivor.** A prior `survived` record found by exact id or by locus is imported into the bucket `survived` with execution mode `carried` and provenance.
5. **Never-started**, as today.

**Claim (strict mode, i.e. every release claim; the exploratory exception of §7 D2 is a disclosed `carried-unverified` bucket outside this claim).** In a Phase 1 verdict every `killed` outcome is evidence that J produced in this campaign, or an exact resume of such evidence. A wrong, stale or colliding locus match costs time (a failed replay followed by a full judge) or adds one disclosed pessimistic survivor. It can never add a kill. The proof depends only on (i) and (ii), which are executions under J. It does not depend on the key, on callees, on tests being unchanged, or on any static analysis. The verifier enforces the claim structurally (§5.2: `killed` is never `carried`).

**Isolated kill semantics, the price of cheapness.** "X alone fails on M, and X alone passes on P" proves that the suite contains a test which detects M. The current judge's semantics are different: the suite in its order fails on M. The two differ only for order-dependent tests: X fails on M alone but passes after earlier tests, and nothing else fails. Then isolated says killed, the suite run says survived. Both runs use the same one-item collection, so the B106 objection (a narrowed collection changes what session fixtures see, `DESIGN-GUIDE.md:1872-1876`) applies symmetrically to baseline and mutant and does not invalidate the comparison. This is a judge-semantics change; the operator decided it (§7 D1, yes): an isolated kill is accepted and disclosed as `witness-isolated`. For the record, the alternative that was not chosen is the B106 prefix replay: sound, with Phase 1's kill gain about 0 (§0).

### 2.4 The "whole-test-tree hash" variant, analysed

The proposal: carry kills and survivors when the sub-file locus matches and the tests plus config are unchanged.

- **Kills.** Unsound. An unchanged test tree does not cover b2-b5. A changed callee or caller in source turns a kill into a survivor with the same tests.
- **Survivors.** Sound for PASS, as survivors are anyway. Gating them on an unchanged test tree only reduces stale survivors. That is variant S2 in §7 D3.

The variant is therefore Phase 1 with an optional survivor gate. It is not a cheaper kill path.

### 2.5 Phase 2: kills proved by an observed footprint (later, separate review gate)

Phase 1 still executes ~4,190 replays per release. Phase 2 trusts a kill without executing it, only when the execution that produced the evidence provably could not differ.

- **Recording.** During both isolated runs (X on P, X on M), a recorder records every project code object executed. It uses `sys.monitoring` PY_START and returns DISABLE after the first event per code object, so overhead is near zero; this needs Python 3.12 or later (M: the cockpit runs 3.14.10). Each code object is mapped to its segment:
  - a function's segment is its def from the first decorator to its end;
  - the segment of `<module>` and of `<class C>` is the text minus nested def bodies. Signatures, defaults and decorators stay in it, because they run at import time.

  An audit hook records every `open` of a project file outside the import system as a whole-file item, and every `os.listdir`/`os.scandir`.
- **Footprint.** The sorted set of `(path, segment kind, qualname, segment_sha256)` items and whole-file items, plus a **context digest**: `judge_sha256` recomputed with `tree_sha256` replaced by the digest of the project's path listing (names only), so that an added, removed or renamed file invalidates. Decided (§7 D6): the assay version enters the digest only through a separate `classifier_version` (bumped when the candidate classification or locus rules change), not through the full assay version, so an assay upgrade without such a change does not void every footprint.
- **Trust rule.** A prior isolated kill is trusted under J, without execution, iff:
  - its context digest equals J's;
  - every footprint item of both runs (P and M) hashes the same in J's tree;
  - the mutation maps by locus.

  Under the determinism assumption (same executed code, same inputs read, same context imply the same outcome), the evidence run would repeat exactly.
- **Fail closed. The footprint is marked incomplete, and the kill is never trusted, when:**
  - a subprocess or exec was spawned (`subprocess.Popen`, `os.system`, `os.exec*`, `os.posix_spawn`, `os.fork` audit events), because cmru's e2e tests run the CLI in children;
  - a project module was imported before the recorder started;
  - `exec` or `compile` ran on non-file source;
  - the footprint exceeds 20,000 items or 512 KiB of the 1 MiB record limit (`mutation.py:281`);
  - the interpreter is below 3.12;
  - the lane is cold-witness, xdist, a wrapped command or has nonempty `PYTEST_ADDOPTS` (the B106 exclusions).

  Isolated runs use `--tb=no` so that traceback rendering does not read source files.
- **Flakiness guard.** Trust only kills observed at least twice: the original kill plus one fresh isolated replay. Also re-prove a rolling 10 % slice per release.
- **Named residuals** (same class as `link_paths` content and B013 values today, `mutation.py:1680-1694`): nondeterminism (time, random, network); reads by C extensions that bypass the audit hook; state outside the snapshot.

Phase 2 replaces the "transitive-callee closure" with what actually ran. The closure cap of the brief becomes the footprint cap above. Exceeding it means "not provable", hence replay.

## 3. Identity definition

### 3.1 Options

| | Definition | As a proof | As a finding key | Cost | Verdict |
|---|---|---|---|---|---|
| A | normalised-AST hash of the enclosing function + sorted hashes of in-project callee closures | unsound (§2.1, b3-d) | over-invalidates (a utility edit re-keys every caller); the closure is unbounded and unresolvable for dynamic dispatch; `ast.dump` changes with the interpreter version, so keys drift on upgrade | static call graph per plan | rejected |
| **A'** | exact bytes of the outermost enclosing def segment + relative span + operator + replacement | not needed (§2.3) | stable across moves and edits elsewhere in the file; simple; recomputable by `assay verify` from source bytes | one `ast.parse` per file | **chosen** |
| B | bytes of a ±k-line window around the site, clipped to the enclosing def | not needed | finer (approaches the ~65 line delta); more stale survivors carried (pessimistic) | trivial | adopted for kill lookup only; survivors stay on A' (§7 D5, WP0 measured) |
| C | today's file-granular id | n/a | exact-id path only | none | the fallback |

### 3.2 The locus key (A')

New dependency-light module `src/assay/candidate_locus.py`, beside `candidate_identity.py`. It is pure: `ast` and `hashlib` only.

```
locus_key = sha256( "assay-locus/1" \0 path \0 qualname \0 segment_sha256 \0
                    rel_start \0 rel_end \0 operator \0 replacement_sha256 )
```

- **Segment.** The outermost `FunctionDef`/`AsyncFunctionDef` that contains the site, i.e. one not nested in another def. Its byte range runs from the `@` of the first decorator (or the `def`/`async`) to `end_lineno`/`end_col_offset`.
- **`qualname`.** Class names plus the def name. A def inside a module-level `if`/`try` keeps its plain name.
- **`segment_sha256`.** The SHA-256 of the segment bytes. `rel_start = start_byte - segment_start`, and likewise `rel_end`.
- **`replacement_sha256`.** As in the record today (`mutation.py:2934`). Together these bind the mutated segment exactly, the segment-level analogue of `mutated_file_sha256`.
- **Fallback, locus = none (exact id only, option C):**
  - a site outside every def: module-level code, class bodies (a field default like `keep_tree: bool = False`), class decorators, module-level lambdas;
  - a site that crosses a segment boundary;
  - a parse failure;
  - a non-Python adapter;
  - **ambiguity**: two segments in the current file with the same `(qualname, segment_sha256)` (property getter and setter, `@overload`, a conditional redefinition). Then none of their sites get a locus.

  In a carry source, two records with one locus key are both ignored for locus matching.
- **Whitespace or comments inside a def re-key it.** This is deliberate: exact bytes need no normalisation that depends on the interpreter. Measured impact is part of WP0.

### 3.3 Storage, schemas, compatibility

- **`candidate_id`.** Unchanged (`candidate_identity.py` is not touched). So `plan_sha256`, `select_mutation_shard`, `--only/--exclude-candidates`, the shard manifests (`mutation.py:1795-1800`) and B106 are unaffected.
- **The state record gains an optional `locus` object:** `{schema: "assay-locus/1", key, qualname, segment_sha256, rel_start, rel_end}`, or `null` for the fallback.
  - It is written by the two record builders (`mutation.py:7092-7119` ff. and `~7236`), which already spread `_progress_event` (`1479-1501`). Adding the locus there puts it into the progress stream as well. The state writer is `_write_mutation_state_record` (`2185`).
  - It is validated in `_load_validated_state_record` next to the other identity keys (`2930-2967`). A present `locus` that differs from the value recomputed for the job is "stale", which means corruption, exactly as for `source_sha256`.
  - An absent `locus` is legal. A pre-P4 record therefore keeps resuming and carrying by exact id, but it can never be found by locus. That is explicit, not silent: progress counts it as `locus_unavailable`.
  - `MUTATION_STATE_SCHEMA_VERSION` stays 1 (`mutation.py:295`). An older assay never resumes a newer record anyway, because the assay version is in the judge (`1672-1678`).
- **A record carried or replayed by locus is written under the CURRENT candidate id,** with the current identity fields. It names its origin in a `carry_over` block of schema 2:
  - schema-1 keys plus `match` (`exact` or `locus`), `from_candidate_id` and `from_identity` (the prior path, `source_sha256`, span and `mutated_file_sha256`);
  - for a replayed kill, additionally `replay_node_id`.

  `_valid_carry_over` (`mutation.py:1904-1917`) accepts schema 1 and schema 2 with exact key sets each. `record_effective_judge` (`1920-1938`) is unchanged: such a record is evidence for its `into_judge`.
- **The verdict moves to schema v21** (v20 is current) and folds in B532:
  - per-candidate `execution.mode` gains `witness-isolated` and `carried`, and in Phase 2 `footprint-proven` (`verdict.py:2191-2266`), with the `prior` reference fields;
  - `judgment.r2.provenance` gives counts by mode (§5.2);
  - `assay verify` re-derives the counts and enforces the mode/bucket rules. A v20 verdict is accepted by `--reuse-from` as today.
- **Old file-granular stores remain valid** for exact resume and exact carry. Mixing is never silent: every non-fresh outcome carries its mode and origin.

### 3.4 Resume, merge and shards

| Mechanism | Behaviour |
|---|---|
| `--resume` | unchanged. A record with a `carry_over` block resumes under `into_judge` (`mutation.py:3279-3291`) |
| **new `--carry-forward DIR`** (repeatable, **no reason required**) | for each pending job, finds prior records by exact id, then by locus, in `DIR/<id>.json` and `DIR/judges/*/` (`_carry_over_candidates`, `3975-3989`; a locus index is built by one bounded scan of the source). Survivors are imported as `carried`, kills are queued for isolated replay, other buckets run in full |
| `--carry-over DIR --carry-over-reason` | unchanged exact-id import. Under v21 its imported kills are marked `carried-unverified` (§7 D2) |
| `--merge-state`, `--merge-shard-store` | unchanged: same effective judge only (`4088-4095`). Carried and replayed records merge like any other |
| `--shard K/N` | unchanged assignment (by current id). Each shard carries forward and replays its own jobs. Node baselines are cached per shard store (`<store>/node-baselines/<judge>/<sha256(node)>.json`), so a node shared by two shards costs two baselines (E: small). assay's `--merge-shard-store` copies the cache like records (run-gate's `--merge-shards` only drives it) |
| `--reuse-from` (B106) | unchanged. `--carry-forward` is its shard-compatible, store-driven sibling. The two flags are mutually exclusive in one run |
| `--rejudge-outcome survived` | also re-runs carried survivors. New `--rejudge-carried` re-runs every `carried` record |

## 4. Invalidation rules

### 4.1 Phase 1: what each change does

| Change | Candidates in the changed segment | Other candidates of the same file | Candidates of other files |
|---|---|---|---|
| edit inside def f (any byte, comments included) | new locus, **full judge** | locus matches: kill → isolated replay, survivor → carried | exact id: same as the cell on the left |
| edit module-level or class-body code of F | id changes, no locus, **full judge** | locus matches, as above (the replay sees the new constant) | as above |
| edit a callee g or a caller h (same or other file) | g's/h's own: full | f's kills replay, so the change is caught; f's survivors are carried and may be stale (pessimistic) | same |
| function moved, file reordered | locus matches (no full judge) | as above | n/a |
| file renamed or moved | `path` is in both keys, so **full judge** | | |
| tests, conftest, fixtures, pytest config, plugins, env, argv, interpreter, packages, assay version | the judge changes, every candidate goes through carry-forward. Kills: replay under the new judge, so the change is caught. Survivors: carried pessimistically (S1, decided §7 D3; the optional probe R-a comes later, WP8) | | |
| operator set changed | new operators' candidates: full; removed ones disappear from the plan | | |
| `--rejudge`, `--rejudge-outcome` | full judge for the named candidates | | |

Everything the analyser cannot map (§3.2 fallback) uses the exact id: one byte anywhere in the file means a full judge, as today.

### 4.2 Tests changed

- **The simple sound rule first.** Phase 1 needs no test identity for kills (replay). Survivors are pessimistic.
- **Refinement R-a, survivor probe.** Store a test-file manifest (path to blob id) per judge generation, `<store>/judges/<judge>/TESTS.manifest`. For a carried survivor, run only the test files added or changed since its judge, in isolation. A failure plus a passing isolated baseline of that file makes a fresh `witness-isolated` kill. Otherwise the survivor stays carried.
- **Refinement R-b, covering-test-set identity.** A survivor counts as current when the set of tests executing f (per-test coverage contexts) and their Phase 2 footprints are unchanged. This needs a contexts run, about 8 minutes for cmru (research §2, E), and subprocess coverage. It comes later and only if survivor noise is shown to matter.

### 4.3 Transitive callees, dispatch, monkeypatching

- **Phase 1** has no closure: replay executes the real program.
- **Phase 2** uses the observed footprint: dynamic dispatch, `getattr`, registries and monkeypatches are covered to the extent they executed. A change that alters which code would run must itself change executed code or a read input, under determinism. A registry built at import time is in the `<module>` segment; a newly added module is in the path listing. Subprocesses, early imports, dynamic `exec` and overflow are fail-closed (§2.5).
- **A static callee closure** is not used. If a later package wants one as a pre-filter, cap it at 256 functions. Exceeding the cap counts as "depends on everything", never as "independent".

## 5. Rigor impact

### 5.1 Claims

- **R2 PASS, "every candidate killed".** Phase 1 keeps it in the strict (release-claim) mode, because every kill is fresh; an exploratory campaign that allows `carried-unverified` kills does not make this claim (§7 D2). The claim's meaning changes from "the ordered suite fails" to "some test of the suite, run alone, fails while it passes alone on the baseline" (§7 D1).
- **Phase 2** adds an assumption to kills: determinism plus completeness of observation, with residuals named in §2.5. The verdict must state it.
- **The survivor list** may contain stale entries, each one disclosed as `carried` with its source judge. Its count is `provenance.carried_survivor`.
- **Legacy `--carry-over` kills** remain an unverified operator claim, now visible per candidate.
- **Lost:** nothing that the current carry-over provides. **Gained:** carried kills stop being an unverified claim.

### 5.2 Required disclosure (verdict v21)

- **Per candidate:** `execution.mode` is one of `full | witness-prefix | witness-cold | witness-isolated | footprint-proven | carried | carried-unverified`, plus `prior {from_candidate_id, from_judge_sha256, match, replay_node_id?, node_baseline_sha256?}`.
- **Counts:** `judgment.r2.provenance` with `fresh_full`, `fresh_prefix`, `fresh_isolated`, `footprint_proven`, `resumed_exact`, `carried_survivor` and `carried_unverified`, plus a per-source judge count. The counts sum to `candidate_count`.
- **`assay verify` refuses:**
  - `carried` on any bucket other than `survived`;
  - `witness-isolated` or `footprint-proven` on any bucket other than `killed`;
  - counts that disagree;
  - a PASS that contains a `carried-unverified` kill, for release claims (decided, §7 D2); exploratory campaigns may allow it and disclose the count.
- **Text closeout.** `assay analyze campaign` prints one line per mode.

### 5.3 Fault-injection tests (each must end as stated; these are the acceptance gate of WP6)

Each starts from a store in which candidate C in `f` was KILLED by node X and candidate S in `f` SURVIVED. One change is applied, then `--carry-forward` runs.

| # | Injected change | Required result |
|---|---|---|
| F1 | callee `g` edited so that C's mutant now survives | C: replay passes on M, so full judge, so `survived`. Never `killed` |
| F2 | caller `h` stops using f's result | as F1 |
| F3 | module-level constant read by f changed (same file, outside the segment) | C: replay re-executes. If X now passes, full judge |
| F4 | decorator of f changed | f re-keyed, C and S: full judge |
| F5 | X's assertion weakened | C: replay passes, full judge, `survived` |
| F6 | fixture in `conftest.py` changed so that X passes on M | as F5 |
| F7 | `pytest.ini`/`pyproject` addopts changed (for example `-p no:x`), or an env var in `env` | judge changes; C: replay under the new command |
| F8 | X deleted or renamed | replay finds no node, so full judge |
| F9 | X fails alone on P (order-dependent test) | node baseline fails, so full judge, no kill |
| F10 | two identical method segments (getter and setter) | no locus, so exact id only |
| F11 | site in a class body or at module level, file edited | full judge |
| F12 | a new test kills S | S1: S `carried` and the verdict FAILs (never PASS). With R-a, S becomes a `witness-isolated` kill |
| F13 | locus collision injected into the source store | both records ignored |
| F14 | tampered `locus` in a record | ignored and counted (`locus_unavailable`), never trusted; resume proceeds by exact id (AMENDED 2026-11-24, D-A9) |
| F15 | verdict edited: a `killed` outcome with mode `carried` | `assay verify` refuses |
| F16 | lane with xdist, nonempty `PYTEST_ADDOPTS` or cold-witness | no isolated replay, full judge |
| F17 | shard run, then `--merge-shard-store` | provenance and node baselines survive; counts re-derive |
| P2-1..P2-8 (Phase 2) | an unexecuted function edited (positive control: trusted); an executed callee edited; a module constant edited; a data file read via `open` edited; a file added under the source root; a test that spawns a subprocess; a project module imported before the recorder; footprint overflow | positive control: `footprint-proven`; every other case: replay, never trusted |

## 6. Work packages (Sonnet implementers; Opus review per series)

| WP | Content | Files | Tests | Size (E) |
|---|---|---|---|---|
| **WP0** | Offline measurement (container on a test host, never the cockpit). For the last 10 releases of cmru, ciu and run-gate: plan both tags with the real Python adapter (`collect_mutation_sites`, `mutation.py:837`), count re-keyed candidates under C, A' and B (k = 2, 3, 5), whitespace-only re-keys, fallback share, distinct witness nodes in the existing cmru stores (`cmru/docs/evidence/r2-*`). Then pilot 20 isolated replays plus node baselines on v1001 and measure the per-replay cost (replaces the 15-30 s E) | `assay/tools/locus_churn.py` (tool, not `src`) | doc-level only | S-M |
| WP1 | `candidate_locus.py`: segment map, key, fallback and ambiguity rules | new `src/assay/candidate_locus.py` | unit tests: every §3.2 rule, moves, nested defs, decorators, async, a non-UTF-8 refusal | S (~250 lines + tests) |
| WP2 | record `locus` write and validate; `carry_over` schema 2 | `mutation.py:1479-1501` (`_progress_event`), `1814-1938`, `2930-2967`, `7092-7119`, `~7236` | F13, F14, old-record compatibility | M |
| WP3 | `--carry-forward`: lookup, survivor import, kill queue, progress event `carry_forward`, `assay plan --list-pending` classification, `--rejudge-carried` | `cli.py:401-462, 2224-2292`; `FlexOptions` `mutation.py:1830-1901`; new `_carry_forward_records` beside `3992-4163`; `run_mutation` `5670-5721` | F10-F13, F17 | M-L |
| WP4 | `witness-isolated` execution: plugin keeps only the target item (the env beside `WITNESS_TARGET_ENV`, `mutation_witness.py:31, 1200-1222`), node baseline and cache, `_run_one` branch beside `6638-6672`, fallbacks, B106 exclusions | `mutation.py`, `mutation_witness.py` | F1-F9, F16 | L |
| WP5 | verdict v21 (folds B532): modes, `prior`, `provenance` counts, JSON schema, verify rules, CONSUMERS and DESIGN-GUIDE sections, v20 to v21 migration note | `verdict.py:443, 2191-2266`; `verify.py`; `src/assay/schemas/verdict.schema.json`; docs | F15, schema pin test | M |
| WP6 | fault-injection suite §5.3 F1-F17 as fixture projects (small, fast, no docker) | `tests/core/test_carry_forward_*.py` | itself | M |
| WP7 | Phase 2 footprint recorder and trust (its own design review first; starts only after D4) | new `src/assay/footprint.py`, witness plugin | P2-1..P2-8 | L |
| WP8 | survivor probe R-a (optional, after D3) | `mutation.py`, store manifest | F12 variant | M |

Order (the original proposal, superseded by the decided order below; its content remains the definition of WP3a/WP3b: WP3 first as a skeleton (the `--carry-forward` flag, lookup, survivor import, replay stubbed to "re-run in full"), then WP4 (isolated replay, whose tests F1-F9 and F16 need the flag), then the rest of WP3 (progress events, `--rejudge-carried`)). Decided order (2026-11-18): WP0 -> WP1 -> WP2 -> WP3a -> WP4 -> WP3b -> WP5 -> WP6; WP7 (Phase 2) only after D4 is taken (after WP0, Phase 1 in production and a measured replay share); WP8 (probe R-a) optional and later.

Every WP that changes `src` needs the B534 host self-qualification before merge. WP0 ends with a short M report that replaces §1's C/E numbers. If the measured replay cost is 60 s or more, Phase 1 strict loses most of its gain; that is a performance finding to report to the operator, and it does not reopen D2 (decided by claim type: release claims refuse PASS while `carried-unverified` kills exist, exploratory campaigns may allow it).

## 7. Decisions (operator, 2026-11-18)

**DECIDED 2026-11-18 (operator, relayed by the main controller): D1-D6 as recommended below, with these exact outcomes.** D1: yes, an isolated kill is acceptable, disclosed as `witness-isolated`. D2: a PASS is refused while any `carried-unverified` kill exists for release claims; allowed for exploratory campaigns. D3: S1, survivors always carried pessimistically; the optional probe R-a comes later (WP8). D4: deferred; decide after WP0, Phase 1 in production and a measured replay share. D5: the line-window key B is adopted for kill LOOKUP only (the WP0 threshold is met: B(k=2..5) re-keys 79-93 candidates per cmru release against 211 for A', 151-169 against 468 for ciu, 79-105 against 517 for run-gate; tables in the sub-controller's WP0 record). D6: split out a `classifier_version`. **KI-141 is resolved by the operator's reasoning:** a previously tested candidate still holds if the code that was tested did not change, so the R2 claim covers the WHOLE source, assembled from carried and re-verified results, not a delta-only claim.

**AMENDED 2026-11-24 (operator, Opus design review accepted as recommended; backlog B562-B570).** These outcomes supersede the 2026-11-18 text where they differ:
- **D2 replaced (D-A3, B567).** Any `carried-unverified` kill means the R2 status is not PASS (PARTIAL or FAIL by the v21 rule), with the count disclosed. There is no claim-type axis.
- **R-a promoted (D-A4, B564).** The survivor probe moves into Phase 1: WP8 becomes part of WP4, whose scope is one isolated-run primitive `run_isolated(tests, tree)` used for carried kills, witness hints for changed-function candidates, and carried survivors against changed tests. Key B (D5) is a hint-ranking input only.
- **Phase 2 parked (D-A5).** WP7 and D6 are parked; they re-open only if WP0b measures an isolated replay above about 20 s.
- **One provenance object (D-A8, B563).** The state record carries `provenance {kind: fresh|resumed|imported|carried|replayed, from_candidate_id, from_judge_sha256, match: exact-id|locus|window|hint, replay_node_id?, reason?}`, projected unchanged into verdict v21. A replayed kill is a FRESH current-judge record (`kind: replayed`, no `carry_over` block). Schema-1 `carry_over` blocks are read, never written; the WP2 schema 2 is redefined in place (it never shipped).
- **Advisory locus (D-A9, B563).** A stored `locus` or window key that is of an unknown schema or differs from the recomputed value is dropped and counted (`locus_unavailable`), never `MutationStateError`. This replaces the §3.3 "stale means corruption" rule; F14 reads accordingly.
- **One store-combine (D-A10, B565).** `combine(sources, policy)` with the policies `same-judge`, `strict-shard`, `owner-manifest`, `carry-forward`, `import-unverified`; today's flags become aliases; `--carry-forward` composes with `--shard`; `--reuse-from` becomes a hint source.
- **v21 is the single cut (D-A12, B567).** It collects B532, B545, the B556 reason code, B559 and the provenance projection; no other package touches the verdict schema, `ReasonCode` or the closed vocabularies.
- **F-tests with WP4 (D-A16).** WP6's F1-F9, F12 (R-a variant) and F16 land with WP4 as its acceptance tests; WP3b shrinks to the survivor-loop notice, and `--rejudge-carried` becomes an alias of `--rejudge-outcome survived` or is dropped.
- **Order.** WP0b (replay pilot) → WP2' (amended, B563) → cmru whole-source backfill ∥ WP3a → B566 (`mutation_store.py` extraction) → WP4 (with the WP6 F-tests) → WP5 (v21).

The original options and recommendations follow for reference.

- **D1, isolated kill semantics.** Is "X alone fails on M and passes alone on P" an acceptable kill, disclosed as `witness-isolated`? If not, kills replay by B106 prefix, and the Phase 1 gain is limited to survivors and correctness. Recommended: yes.
- **D2, legacy `--carry-over` kills.** Options: (a) keep them, marked `carried-unverified`, PASS allowed (as today); (b) PASS refused while any exist; (c) replace them with isolated replay by default. Recommended: (b) for release claims, (a) for exploratory campaigns.
- **D3, survivor policy.** Options: S1, always carried pessimistically; S2, carried only if the tests manifest is unchanged, otherwise re-judged in full (~200-600 × 646 s, E). Recommended: S1 plus R-a.
- **D4, Phase 2 go or no-go.** Decide after WP0, Phase 1 in production, and a measured replay share.
- **D5, finer key (B).** Adopt a line-window key for kill lookup only (survivors stay on A') if WP0 shows at least 30 % fewer full judges than A'.
- **D6, the assay version in the Phase 2 context.** Split out a `classifier_version` that is bumped only when bucket semantics change, so that an assay upgrade does not void every footprint.

## 8. Review corrections and specification gaps (fact-check of 2026-11-18, to settle before WP2/WP4)

Corrected above: `--merge-shard-store` (assay) instead of `--merge-shards` (run-gate only), the schema path, the WP3/WP4 order. Wording points: the research report §5 counts callers (x2-3) in its gain estimate though its identity definition omits them; the existing execution modes also include `ledger` (`verdict.py:2202`), which the mode list in §5.2 omits; §1 uses 1,627 re-keyed candidates (C, proxy) where the research report says ~1,560 (31 %): the proxy replaces it; the Phase 1 hours (35-56 h, 23-31 h) include padding for node baselines that the arithmetic of the table rows alone (34-52 h, 22-27 h) does not show.

Open specification gaps, each to be closed in the work package named:
1. **WP2, replayed kill vs `record_effective_judge` (`mutation.py:1920-1938`).** The function returns the effective judge only when `carry_over.from_judge_sha256 == payload.judge_sha256`. A replayed kill is executed by the CURRENT judge, so its `judge_sha256` is the current one while the schema-2 block names the prior judge: define the field (`from_judge` is provenance, never an effective-judge override) and relax or extend `_valid_carry_over` (`1904-1917`), which requires a non-empty `reason` that `--carry-forward` does not take (use a fixed machine reason such as `carry-forward`).
2. **WP4, isolated semantics in the judge identity.** `candidate_fail_fast` enters `judge_sha256` only when on (`mutation.py:1744`); `witness-isolated` does not, so a store holding isolated kills resumes under the same judge in a plain `--resume` run. Fold the replay mode into the judge or add a verifier rule that refuses `witness-isolated` unless the run declared it.
3. **WP1/WP3, collision rule.** "Two records with one locus key are both ignored" must first de-duplicate by `candidate_id` (the same candidate can sit in the flat record and in `judges/*/`, B527 archives), otherwise locus matching fails often.
4. **WP7 (Phase 2), config edits.** The context digest replaces `tree_sha256` with a names-only listing, so a content edit to `pytest.ini`/`pyproject.toml`/`setup.cfg` addopts is in neither the digest nor the footprint (pytest reads it before the recorder starts): hash those files into the context digest and add a P2 test for it.
5. **WP3, survivor loop.** A test added in the same release leaves the carried survivor carried again from the old generation, so the lane keeps failing until `--rejudge-carried` (or the R-a probe, D3) runs: state this in the run output and the docs.
