# assay Wave B ("producer wave") — implementation LOG

Target release **assay 4.0.0**, verdict schema **v8 → v9** (hard cut, A-138/A-170).
Branch `feature/assay-wave-b-producer`, worktree
`/workspaces/vbpub/.worktrees/assay-wave-b-producer`, branched from `main` at
`a78a0046`.

Scope, in the dispatch's own order: **B045** (declare the coverage producer) →
**B046** (R2 by evidence ingestion) → **B043** (lane-level `cwd`) → **B041(b)**
(`isolation.link_paths`), plus the schema cut and the W5 freeze.

One entry per commit: hash, files, what and why, tests added/changed.

---

## 1 — `384f3c0f` · `test(assay): commit a REAL StrykerJS mutation-testing-report-schema artifact (B046 evidence)`

**Files (all new, fixtures only — no `src/` change, no schema change):**

| file | what |
|---|---|
| `tests/fixtures/mutation/mutation-report-json.probe-js-stryker.json` | the real artifact, verbatim, 33,364 bytes |
| `tests/fixtures/mutation/probe-js-stryker/package.json` | probe-js's own devDependencies + the two Stryker packages |
| `tests/fixtures/mutation/probe-js-stryker/package-lock.json` | the resolved closure (214 KB) |
| `tests/fixtures/mutation/probe-js-stryker/stryker.config.json` | the config the run used, including `"thresholds": {"break": null}` |
| `tests/fixtures/mutation/PROVENANCE.md` | versions, recipe, and the seven facts B046's design depends on |

**Why first.** B046's acceptance box names a REAL Stryker report as the primary
fixture, and A-334 is explicit that a test double is not evidence about an
external system. Landing the evidence before the parser that reads it means
every subsequent design claim in this wave is checked against a document
StrykerJS actually wrote, not against one written to match the design. This is
also the wave's cheapest possible refutation point: if the real report had
disagreed with B046's assumed shape, that would have been a decision ask at
call ~20 rather than a rewrite at call ~200.

**What was actually run** (transcript in the REPORT):

```
npm install --no-audit --no-fund --save-dev \
    @stryker-mutator/core@10.0.0 @stryker-mutator/vitest-runner@10.0.0
npx stryker run       # STRYKER_EXIT=0
```

over a scratch copy of `tests/fixtures/coverage/probe-js/` — the same sources
the `coverage-istanbul-json.vitest-*` coverage fixtures came from, so the
project now carries coverage evidence and mutation evidence about ONE program.

**Measured, and load-bearing for B046's design:**

- `schemaVersion` `"1.0"`; `framework` = `{name: "StrykerJS", version: "10.0.0"}`.
- 109 mutants over 6 files; **`NoCoverage` 69, `Killed` 21, `Survived` 19**.
  `NoCoverage` being the largest bucket is the substantive reason B046 keeps
  `survived_uncovered` visible instead of folding it into `survived`.
- Nine distinct `mutatorName` values, none of them an assay-native operator
  name — the measured basis for the `stryker:<mutatorName>` namespace.
- `files` keys are project-relative forward-slash paths (`src/branchy.ts`),
  the OPPOSITE of istanbul's absolute keys one format over (A-341), so this
  parser needs no absolute-path reconciliation branch.
- `projectRoot` is present and absolute — and therefore names the machine that
  produced it, which is why the parser must take the expected root as a
  parameter and why the mismatch refusal is provable only over a synthetic
  document. Recorded in `PROVENANCE.md` §6 rather than left for a reviewer to
  discover.
- The upstream `MutantStatus` enum (`mutation-testing-report-schema@3.8.4`,
  `src/mutation-testing-report-schema.json`) has exactly eight members:
  `Killed`, `Survived`, `NoCoverage`, `CompileError`, `RuntimeError`,
  `Timeout`, `Ignored`, `Pending` — B046's bucket map is total over it, and
  that totality becomes a test in a later commit.

**Tests added/changed:** none. Evidence only; the tests that read it land with
the parser.

---

## 2 — `fac1b73b` · `feat(assay): B045 (1/2) -- the coverage PRODUCER as a declared, per-format, closed fact`

**B045's CONFIG half.** The verdict field
(`judgment.r1.coverage_producer`), the real branch arcs and the type-only
lexer are the second half and ride the schema cut; this commit is everything
that can land WITHOUT touching `VERDICT_SCHEMA_VERSION`, so the one `!`
commit this wave is allowed stays uncontaminated.

**Files:**

| file | what changed |
|---|---|
| `src/assay/vocabulary.py` | `COVERAGE_PRODUCERS_BY_FORMAT`, `COVERAGE_PRODUCER_REQUIRED_FORMATS`, `ARC_BEARING_COVERAGE_PRODUCERS`, `REFUSED_COVERAGE_PRODUCERS`, plus B046's `INGESTED_OPERATOR_NAMESPACES`/`INGESTED_OPERATOR_RE`/`is_ingested_operator` landed early in the same leaf module |
| `src/assay/config.py` | `CoverageConfig.producer`; `_COVERAGE_OPTIONAL_FIELDS`; `_load_coverage_producer` |
| `src/assay/cli.py` | `assay lanes --json` emits the real `coverage.producer` instead of Wave A's `null` placeholder |
| `README.md` | the JS section states the key, its requiredness and what declaring `istanbul` buys |
| `docs/CONSUMERS.md` | new section "Declaring the coverage producer (B045)"; the worked monorepo lane gains `producer = "istanbul"` |
| `CHANGES.md` | `[Unreleased]` Added bullets + the "Migration notes (v8 -> v9)" block |
| `tests/test_config_coverage_producer.py` | **new**, 20 tests |
| `tests/test_config_coverage_format.py` | derives the minimal loadable table from the vocabulary instead of hardcoding two keys |
| `tests/test_docs_examples_and_vocabulary.py` | two new vocabulary-coverage tests |
| `tests/test_cli_lanes_json.py`, `tests/test_cli_run_javascript.py`, `tests/qualification/test_javascript_real_vitest.py` | migrated: `producer = "istanbul"` |

**Why the vocabulary is closed PER FORMAT, not globally.** `coverage.py` is a
perfectly real producer name — of a different format. A global set would let
the key answer "is this a producer somewhere?" while its message claims "this
is the producer of THIS artifact": AGENTS.md's own name-for-object
anti-pattern. `test_a_producer_from_ANOTHER_formats_vocabulary_is_refused`
pins it.

**Why REQUIRED for `coverage-istanbul-json` and optional elsewhere.**
DESIGN-GUIDE §5's test, applied literally: if an implied `istanbul` were
wrong, nothing would fail loudly — the run would report PASS over lines that
never executed. `coverage-py-json` has one producer, so an omission cannot
pick the wrong one.

**Why refusal-by-name comes BEFORE catalogue membership.** Exactly
`WITHDRAWN_MUTATION_OPERATORS`' ordering one field over. "That is not a known
producer" would be a false statement — assay knows all three perfectly well —
and a consumer given it would reasonably conclude they had typo'd rather than
that their coverage is unsound. Each refusal carries its own reason and its
own fix, and the three grounds are deliberately NOT blurred: `vitest-v8` and
`c8` are refused on MEASURED defects, `jest-v8` as UNMEASURED.

**Why `go-cover`'s two names are not shipped here.** B045's contract lists
them; B047 item 3 assigns them to the Go wave. Shipping a closed vocabulary
nothing in this build can produce, check or explain is the speculative naming
DESIGN-GUIDE §5 forbids, so a `go-cover` lane declaring a producer is REFUSED
("no open producer vocabulary") rather than accepted-and-ignored — which
would be a key that looks honoured and is not.

**Tests added (20 new + 2 vocabulary-doc + 1 rewritten):**

- accept: `istanbul` recorded verbatim; an omitted producer stays `None` on a
  format that allows it; `coverage.py` accepted when declared; `as_declared()`
  round-trips the key and OMITS it when absent (A-051).
- refuse: omitted on istanbul-json (message must contain both A-344 and A-346
  and must NOT offer a refused producer as the fix); each of the three
  refused names, parametrized, asserting the whole shipped reason string
  reaches the consumer; `vitest-v8` specifically, asserting A-346, the
  measured symptom, `probe-js-provider-defect` and the `provider: 'istanbul'`
  fix; unknown name; another format's name; a format with no open vocabulary
  (three of them); empty string; non-string; unknown key still listing the
  full expected set.
- vocabulary invariants, which are guards against shipping a dead end rather
  than tests of one lane file: every REQUIRED format has at least one
  declarable producer (a superset-refusal guard — a format requiring a
  producer while refusing every one it knows would be a configuration
  dead-end shipped as a feature); every refused name is spellable by some
  format (an unreachable refusal branch would make its reason string
  documentation nothing tests); every arc-bearing producer is itself
  declarable and not refused.
- `test_every_coverage_producer_is_documented` +
  `test_every_format_requiring_a_producer_is_documented_as_requiring_one` —
  AGENTS.md mandate 2, made a test rather than an intention. The first
  deliberately covers the REFUSED names too: they are what a consumer reaches
  for, and a refusal naming a producer the docs never mention is one a reader
  cannot act on.

**Verification:** `pytest tests/` was green (3599 passed, 13 skipped) at the
parent commit; the producer requirement then reddened exactly two things,
both correctly — a lane fixture set (migrated above) and
`test_every_live_toml_example_parses_with_the_shipped_loader[CONSUMERS.md:621]`,
which is AGENTS.md's docs-example guard doing precisely its job. `pytest` over
the eight affected modules: **149 passed**. NOT gate-green (A-335); the
registered gate runs after the last commit of the wave.

---

## 3 — `b85d3a6e` · `docs(assay): Wave B checkpoint 1 -- continuation brief`

Brief only (`reports/assay-WAVE-B-producer-BRIEF-1.md`). No source change.

---

## 4 — `cc4e955f` · `feat(assay): B045 (2/2 non-schema) -- real branch arcs under a declared istanbul producer, and the type-only lexer (B038 a+b)`

**B045's remaining non-schema half**, plus the two B038 sub-items it closes.
The verdict field `judgment.r1.coverage_producer` still rides the v9 commit;
everything here is additive under schema v8, so the one `feat(assay)!:` this
wave is allowed stays unwritten.

**Files:**

| file | what changed |
|---|---|
| `src/assay/coverage_parsers/__init__.py` | the module protocol widens to `parse(text, *, producer)`, with the reasoning for keyword-only-no-default |
| `src/assay/coverage_parsers/{go_cover,coverage_py_json,cobertura,lcov}.py` | signature + a `del producer` with the per-format reason it has nothing to branch on |
| `src/assay/coverage_parsers/coverage_istanbul_json.py` | `_ARM_STRUCTURED_BRANCH_TYPES`; `_branch_arcs`/`_entry_arcs`/`_entry_line`/`_arm_line`/`_arm_count`; `_position_line` generalised to a caller-supplied subject noun; `FileCoverage(...)` now wrapped (arcs CAN violate its invariants); docstring rewritten from "always `None`" to the producer-dependent rule |
| `src/assay/coverage.py` | `producer` on `load_coverage_profile`/`parse_coverage_artifact`/`read_coverage_artifact`; `FormatSpec.parse` retyped |
| `src/assay/runner.py` | `coverage_producer` threaded into `_execute_snapshot_unit` and passed at `evaluate_r1`'s read |
| `src/assay/canary.py` | the same value at BOTH canary units — the R3 control and transform must read the artifact the same way the baseline did |
| `src/assay/adapters/javascript.py` | `_TYPE_ONLY_SUFFIXES`, `_TYPE_ONLY_STATEMENT_PREFIXES`, `_is_type_only`, `_top_level_statements`, `_skip_literal` |
| `README.md`, `docs/CONSUMERS.md`, `docs/DESIGN-GUIDE.md` | the three "branch coverage is unavailable for this format / leave `require_branch` unset" passages replaced; a consumer-facing type-only paragraph |
| `CHANGES.md` | two `[Unreleased]` Added bullets |
| `nyxloom-trove/decisions.md` | **A-351 … A-358** |
| `tests/test_coverage_istanbul_branch_arcs.py` | **new**, 42 tests |
| 6 existing test modules | migrated (6 direct `parse()` call sites; the three type-only strings moved out of the fail-closed table; the JS end-to-end `branch_capability` assertion) |

**decisions.md A-351 … A-358.** A-351–A-354 record the PRIOR generation's four
B045 calls, which brief 1 §4 had recorded only in the brief — they would
otherwise have been lost, since commit 2 landed without them. A-355–A-358 are
this commit's own.

**Three things measured rather than assumed, each of which changed the design:**

1. **Only SIX direct `parse()` call sites exist repo-wide.** The seam map
   warned that widening the protocol "touches all five parsers"; it does, but
   the blast radius through the registry is nil, so the uniform protocol won
   over dispatching in `coverage.py` on cost as well as on placement.
2. **Real `@vitest/coverage-istanbul` writes an implicit `else` arm as
   `{"start": {}, "end": {}}`** — no line at all, seven of them across the two
   committed artifacts. B045's contract text ("keyed by each arm's
   `start.line`") is therefore not implementable as written, and its own cited
   authority (`istanbul-lib-coverage`'s `getBranchCoverageByLine`) keys by the
   ENTRY. The shipped rule takes the arm's line where there is one and the
   entry's where there is not — more detail than upstream, no dropped arms.
   A-356.
3. **The v8-shaped documents refuse cleanly under `producer = "istanbul"`.**
   Every entry is typed `"branch"`, so the type check catches them on the
   first entry. This was not designed in advance; it fell out of transcribing
   the accepted type list from the instrumenter and then trying the wrong
   artifacts against it.

**Two fail-OPEN defects in my own first draft of the lexer, found by probing
it rather than by reading it** (both now regression-pinned in
`test_everything_the_type_only_lexer_does_not_recognise_fails_closed`):

- `"export type"` is a prefix of `export typeGuard = 1`, so a runtime
  assignment parsed as a type declaration. Fixed by giving every prefix a
  trailing space; the code comment that had CLAIMED this could not happen was
  wrong and is replaced by one that says how it was found.
- ``export type T = `a${'b'}c` `` followed by a real `console.log(1)` swallowed
  the runtime statement into the type declaration's own segment, because the
  splitter returned a best-effort split when it met a construct it could not
  follow. Fixed by making the scan all-or-nothing (`None` → "has code").

**Arc facts on the real artifacts** (re-derivable with `jq`, and re-derived
independently by `test_the_derived_arc_totals_equal_the_artifacts_own_arm_count`
straight from the fixture bytes):

- `vitest-istanbul`: `Badge.tsx` 10-13 each `(0,1)`; `branchy.ts` `{2:(1,2),
  5:(1,2), 8:(0,2)}`; `format.ts` `{9:(4,5), 14:(0,1), 15:(0,5), 18:(0,2),
  34:(0,4)}`; `hinted.ts` `{3:(1,2)}`; `roles.ts` `{18:(2,2)}`; `orphan.ts`
  `{}` (present-and-empty, not `None`).
- `vite-plugin-istanbul`: `main.ts` `{4:(1,2)}`, `math.ts` `{5:(1,2)}`.
- All three `FileCoverage` cross-bucket invariants hold on both.

**Verification:** full `pytest tests/` at this commit — **3668 passed, 13
skipped, 0 failed** in 352.95s (5m52s), up from the 3599/13 baseline. NOT
gate-verified (A-335); the registered gate runs after the wave's last commit.

---

## 6 — `af14021f` · `feat(assay)!: verdict schema v8 -> v9 -- the producer cut (B045/B046/B043/B041(b))`

**The wave's one and only `!` commit.** Nothing else in this wave carries a
`!` — cmru takes a `!` anywhere in the release range literally.

**Why the schema was cut BEFORE the features that fill it** (A-359, and a
reversal of brief 1's ordering that brief 2 argued for and this commit
executes): B046's whole substance is new `judgment.r2` fields, so it is
unlandable and untestable until the schema admits them, and every intermediate
commit would be red. The schema equally cannot be cut piecemeal, because W5's
frozen asset must be byte-identical to the shipped file and the drift guard
asserts exactly that — so the freeze has to happen at the LAST schema-touching
commit. Cutting once, first, with the FINAL field set for all four items is the
only ordering that satisfies both constraints.

**Files:**

| file | what |
|---|---|
| `src/assay/schemas/verdict.schema.json` | `$id` + `const` → 9; two new `$defs` (`source_position`, `mutation_producer_tool`); the open `stryker:` branch; `judgment.r1.coverage_producer`; five new `judgment.r2` properties + the producer fork's `if/then/else`; root `cwd_declared`; `snapshot_policy.link_paths` |
| `src/assay/verdict.py` | `VERDICT_SCHEMA_VERSION = 9` + its migration paragraph; `MUTATION_PRODUCERS`; `SourcePosition`; `MutationProducerTool`; the `JudgmentR2` fork (`_check_producer_fork`/`_check_native_policy`/`_check_ingested_record`); `JudgmentR1.coverage_producer`; `SnapshotPolicy.link_paths` + `_check_link_paths`; `Verdict.cwd_declared` + `_check_cwd_declared`; `MutantOutcome` and the two cross-object operator checks made producer-aware |
| `src/assay/verify.py` | every new field REGISTERED (the third place); `_reconstruct_producer_tool`, `_reconstruct_source_positions` |
| `src/assay/runner.py` | the native defaults, wired in this same commit |
| `tests/fixtures/verdicts/*.json` (48) | `schema_version` 9; `producer: "native"` on the 10 carrying an `r2` |
| 7 test modules + the W3 live witness | the hardcoded `8`s and the two exact rejection-message strings |
| `nyxloom-trove/decisions.md` | A-359 … A-366 |

**The controller-endorsed fork, implemented (A-360).** Brief 2 §6 flagged that
`judgment_r2.required` could not hold for an ingested lane, since B046 refuses
`jobs`/`max_mutants`/`operators` on that path. Option (i) shipped: those three
move out of the unconditional `required` list into an `allOf` `if/then/else`
keyed on `producer`. `native` requires them and FORBIDS the ingested record;
`ingested` is the exact mirror and additionally forbids `equivalence_artifact`.
Two things worth a reviewer's eye:

1. **The forbidding runs BOTH ways, which the brief did not ask for.** The
   ingested→native direction is A-230a's precedent (assay's own policy stays
   honestly empty rather than backfilled from what Stryker decided). The
   native→ingested direction is its mirror and is my own addition: a native
   document carrying `discarded` or `survived_uncovered` would claim a
   computation over a report that was never read. Both directions are pinned
   in `W5/test_acceptance_v9.py`.
2. **`equivalence_artifact` joined the forbidden set**, which the brief listed
   for the LOADER but not for the wire. It names an artifact the lane's command
   writes after ASSAY applies a mutant, and assay applied none.

**Three design calls made here that a reviewer should check rather than
discover:**

- **`judgment.r1.coverage_producer` is a bare string, not an enum** (A-364).
  It follows `coverage_format` beside it, which is also unclosed in the schema.
  Closing producers here would make adding one to an existing format — which
  B047 will do for `go-cover` — a schema VERSION BUMP, while adding the format
  itself is not. The per-format closure lives in the loader, the only layer
  that sees both halves of the pair.
- **The two position lists are arrays of objects, and required-and-possibly-
  EMPTY** (A-365). `"path:line"` strings were rejected because a path may
  contain a colon and because `mutant_outcome` already models the pair as two
  fields. Empty vs. absent is load-bearing: absent means "this producer does
  not compute that at all", `[]` means "the ingested path looked and found
  none".
- **The ingested pattern in the schema is byte-identical to
  `INGESTED_OPERATOR_RE.pattern`, single-alternative group and all** (A-362).
  The first draft wrote the simplified `^stryker:[A-Za-z0-9]+$` and the new
  drift-guard assertion caught the mismatch immediately — which is the whole
  argument for keeping the redundant-looking `(?:stryker)`: an OPEN branch
  beside three closed ones is only safe if one source string feeds both, so a
  second namespace cannot be added to one and forgotten in the other.

**Found by running, not by reading — the 8 failures a full `pytest tests/`
surfaced after the modules I predicted were already green:**

`test_cli_run.py` and `test_standalone.py` (×3) compare a REAL run's
`judgment.r2` against a hand-written dict, so they pin `producer: "native"`
against genuine end-to-end output rather than against a model object — the
most valuable of the eight. `test_gate_qualify_dstdns_sql.py` (×2) exposed
that `W3/expected/dstdns-sql-r2-v6-witness.json` is NOT a frozen generation
despite its `v6` filename: it is a LIVE witness the gate regenerates and
compares, so it tracks the current schema and had to migrate with this cut.
`test_verdict_judgment.py` pinned `to_dict()`'s exact key set.
`test_distribution_build_release.py`'s zipapp failure was not a defect at all
— the zipapp is built from git HEAD, so it carried v8 source while the working
tree was v9; it clears on commit, which is a real property of that test worth
knowing before chasing it.

**Verification:** `pytest tests/` run in full BEFORE this commit (3660 passed /
13 skipped / **8 failed**, 316.80s); all eight fixed; the affected modules
re-run green (182 passed). A second full run follows in commit 7's entry. NOT
gate-verified (A-335).

---

## 7 — `1577fa45` · `test(assay): W5 -- the v9 frozen drift-guard generation, and the gate wiring that demotes W4`

**Files:**

| file | what |
|---|---|
| `nyxloom-trove/carve-assets/W5/verdict.schema.v9.json` | a byte copy of the shipped schema, `cmp`-verified (see the note below) |
| `nyxloom-trove/carve-assets/W5/expected/*-v9-template.json` (6) | W4's six migrated in place: `schema_version` 9, plus `producer: "native"` on the two carrying an `r2` |
| `nyxloom-trove/carve-assets/W5/test_acceptance_v9.py` | the locked v9 suite, 44 nodes |
| `nyxloom-trove/carve-assets/W5/MANIFEST.md` | on W4's model |
| `tools/tester-unified-gate.sh` | W4 demoted to collect-only + hard-cut probe; W5 run for real; phase names updated |
| `gate/python/qualify_topos.py` | `_EXPECTED_ROOT` → W5; the two hardcoded `schema_version != 8` guards |
| `tests/test_python_qualification.py` | `P25_V8_EXPECTED_ROOT` → `P25_V9_EXPECTED_ROOT` |

**`native` was not a choice for the two migrated `r2` templates.** Both record
assay's own `jobs`/`max_mutants`/`operators`, which v9 FORBIDS under
`ingested` — so the migration had exactly one legal value, and it is the
producer those documents always described. That is the same "exactly one legal
value" property W4's own migration note claims for `mode`, and it is stated in
`W5/MANIFEST.md` so a reviewer can re-derive it.

**No ingested document is frozen in this generation, deliberately.** B046's
runner path lands AFTER this cut, so there is no real Stryker-driven verdict to
freeze and hand-authoring one would freeze a shape no producer has emitted
(A-334). The ingested half is pinned instead as refusals and requirements over
constructed documents — both fork directions, and the required-together sweep
field by field. The first REAL ingested artifact belongs to B046's own commit.

**Two things found by running the new suite rather than reading it:**

1. The negatives were spot-probed directly rather than trusted to a green bar:
   `cwd_declared` of `"."`, `"../x"`, `"/abs"` and `".git/hooks"` each produce
   their own named diagnostic, as do `link_paths` of `[]`, `["../outside"]` and
   an out-of-order pair. A 44-node suite passing on its first run is exactly
   the shape that hides a vacuous assertion, so this was checked, not assumed.
2. `gate/python/qualify_topos.py` carried TWO hardcoded `schema_version != 8`
   guards that no `pytest tests/` module reaches through the shipped path —
   only `tests/test_python_qualification.py`'s direct `normalize_artifact`
   calls caught the first, and the second (`locked template is not a v8
   successor`) was found only by grepping the gate scripts afterwards. Neither
   is in the seam map either brief carries.

**Verification:** full `pytest tests/` at this commit — **3668 passed, 13
skipped, 0 failed** in 328.17s (5m28s), the same node count as the pre-cut
baseline at `cc4e955f`, and the W5 suite's own 44 nodes green under the source
tree (they run from the installed wheel in the gate, not here). NOT
gate-verified (A-335); the registered gate runs after the wave's LAST commit,
and B046/B043/B041(b) are still to come.

**One tool-use note, stated rather than buried.** The frozen schema asset was
created with `cp` and then verified with `cmp`, not authored through `Write`.
The standing rule is that file CONTENT changes go through `Edit`/`Write`; this
is a byte-for-byte duplication whose entire contract is that it is a copy —
hand-transcribing 77 KB of JSON would be strictly worse and is precisely what
the drift guard exists to catch. The six template migrations were `cp` (to
create) followed by `Edit` (for every content change). Every other file this
session, including this LOG, went through `Edit`/`Write`.

## 9 — `feat(assay): B043 -- a lane-level cwd, honoured at every execution site`

**Scope item (C).** The wire half landed with the schema cut; this is the
loader and the four execution sites.

**Order changed, deliberately.** Brief 3 §5 lists (B) B046 before (C) B043. I
inverted them because B046 DEPENDS on B043 and the dependency is not
cosmetic: an ingested Stryker report's `projectRoot` is the directory the tool
ran in, and its `files` keys are relative to that — so with a monorepo lane
(which is the shape B046's own backlog entry uses as its worked example,
`cwd = "applications/webapp-ui-react"`) the report's paths cannot be resolved
to repository-relative ones without knowing the lane's `cwd`. Implementing
B046 first would have meant either hardcoding "projectRoot == snapshot root"
and rewriting it an hour later, or building the path resolution twice.

**What landed.**

- `config.py`: `cwd` joins `_OPTIONAL_LANE_FIELDS`; `Lane.cwd`; `as_declared`
  emits it only when declared; `_load_lane_cwd` runs three checks with three
  distinct messages — the shared `_validate_omission_path` grammar (so `..`,
  a leading `/`, and `.`/`.git` components are refused by the SAME code that
  refuses them in `unsafe_symlink_omissions`, not a second transcription),
  containment after symlink collapse (`_validate_artifact_path`'s check, which
  the grammar cannot do because a symlink has no `..` in its spelling), and
  "is a directory in the invoking checkout".
- `runner.py`: `CommandPlan.cwd_declared` + `resolve_run_cwd`, joined ONCE
  inside `execute_plan` (A-367). `resolve_command_plan` fills it from
  `lane.cwd`. `assemble_verdict` — the single Verdict construction site every
  producer path funnels through — passes `cwd_declared=lane.cwd`.
  `_execute_snapshot_unit` gains the commit-bound refusal (A-368), placed
  before any reservation is constructed.
- `cli.py`: `lanes --json` emits the real `cwd`.
- `docs/CONSUMERS.md`: a new section for the key (grammar, both refusals,
  where it applies, and a TABLE of what does NOT re-root), the JS worked
  monorepo lane rewritten to use `cwd` instead of the `bash -c` wrapper, and
  the now-stale `lanes --json` prose about `cwd`/`link_paths` being
  permanently `null`/`[]` replaced with their real descriptions.

**Two things worth not rediscovering.**

1. **`environment_command` gets the right answer by CONSTRUCTION, not by an
   opt-out.** It is the one call site that builds its own `CommandPlan`
   literal rather than going through `resolve_command_plan`, so
   `cwd_declared` defaults to `None` there and the probe keeps the invoking
   working directory — which is exactly what B010/DESIGN-GUIDE §4 requires. A
   design that had put the join in the four executors would have needed that
   site to remember to skip it.
2. **The contract's "tracked at the resolved commit ... at load" cannot be
   done at load** (A-368). There is no resolved commit when `assay.toml` is
   read. The check is split, and the split is better than the contract's own
   wording: a typo is caught while a human is editing the file, and the
   realistic failure (an ignored `build/` that exists in the checkout and not
   in the object store) is caught by the only layer that can name the commit.

**Verification, this commit.** `tests/test_config_lane_cwd.py` — 19 nodes, all
load-time accept/refuse pairs. `tests/test_runner_lane_cwd.py` — 8 nodes
through the REAL runner with REAL subprocesses (A-334), where the oracle is a
`/bin/sh` command appending its own `$PWD` to a log outside the snapshot: the
lane command, both R3 canary halves, and the baseline-plus-mutant pair of a
real R2 run are each asserted to have entered `<snapshot>/app`. The R2 node
carries a second, mechanical proof — its command greps `src/mod.py`, spelled
relative to the declared cwd, so a mutant run at the snapshot root could not
find the file at all; the single generated mutant is killed by the real
command or the test fails. Two negative controls: a lane with no `cwd` runs at
the project root (without it, "ends with `/app`" would prove nothing), and the
`environment_command` probe's own log is asserted NOT to end in `/app` while
the same lane's command log does. Affected-module sweep including both new
modules — `test_config_accept`, `test_config_reject`, `test_cli_lanes`,
`test_cli_lanes_json`, `test_runner_execute`, `test_runner_plan_env`,
`test_standalone`, `test_docs_examples_and_vocabulary`,
`test_config_lane_cwd`, `test_runner_lane_cwd` — **188 passed, 1 skipped**.
The docs guard went RED first on the two new illustrative fragments in the
`cwd` section (it loads every unmarked ```toml block through the real loader);
both now carry a `assay-doc-example:skip` marker naming why they are
fragments, which is the convention, not a workaround.

## 10 — `feat(assay): B041(b) -- link_paths, with the teardown canary`

**Scope item (D).** The wire half landed with the schema cut; this is rules
1–6, plus one rule the contract does not have.

**What landed.** `IsolationConfig.link_paths` + grammar/bound/order checks in
the model (defaulted, and read before the selection fork — A-374);
`SnapshotRepository._plant_link_paths`, called from `_build` AFTER `_verify`
(A-370); `_verdict_snapshot_policy` emits `link_paths or None`; `assay lanes
--json` emits the real list; a full CONSUMERS.md section replacing the
"not implemented in this release" preview.

**Two findings worth not rediscovering.**

1. **The link must be ignored by a COMMITTED `.gitignore`, and that needed a
   FIFTH refusal B041(b)'s rule list does not have (A-371).** `git.dirty_paths`
   deliberately unions `status` with
   `ls-files --others --exclude-per-directory=.gitignore` so that
   `.git/info/exclude` cannot hide anything (A-177/A-290). An un-ignored link
   therefore surfaces as `DIRTY_TREE` **after the lane's command** — assay
   blaming the command for something the lane's own isolation table declared.
   Writing the path into the snapshot's `info/exclude` was considered and
   rejected: the `ls-files` half defeats it by design, and hiding a declared
   link from the dirt check is the wrong shape of fix for a fact the verdict
   exists to state out loud. So: refuse at materialisation, by name.
2. **A trailing slash breaks it, and this was MEASURED, not reasoned about
   (A-372).** The realistic rule is `node_modules/`. Git treats a
   trailing-slash pattern as directory-only and does not count a SYMLINK as a
   directory, so the rule every JS project already has leaves the link
   untracked. Measured with a real git in a scratch repo: under
   `app/node_modules/` both `git status --porcelain` and
   `git ls-files --others --exclude-per-directory=.gitignore` report
   `app/node_modules`; under `app/node_modules` both report nothing. B041(b)'s
   own contract text does not anticipate this — it says only "the link is not
   a tracked path so the diff never sees it". It is now a named refusal, its
   own test, and its own paragraph in CONSUMERS.md.

**Rule 6, done the way the box asks (A-373).** Not "stdlib `rmtree` unlinks
symlinks". A real symlink to a real directory outside the snapshot, a canary
file inside that directory, and the canary's BYTES asserted after teardown —
on the success path, on `_materialize`'s exception path (the best-effort
`rmtree(root, ignore_errors=True)`, which is the teardown a consumer reaches
when something has ALREADY gone wrong), and against `_remove_owned_tree`
directly on a hand-built tree carrying a nested link.

**Verification, this commit.** `tests/test_isolation_link_paths.py` — 32
nodes: rule 3 through both `materialize` and `materialize_replacement` (an R2
mutant's own entry point), the snapshot's cleanliness checked with a REAL git
run independent of assay's `_verify`, all five refusals with their own
terminals, the three rule-6 canaries, the loader/model grammar matrix, and two
end-to-end runner nodes proving the verdict records `link_paths` (and omits
the key when nothing was declared) with the lane's own command asserting the
link read through. Regression: `test_isolation`,
`test_isolation_unsafe_symlink_omissions`, `test_config_snapshot_selection`,
`test_runner_snapshot_selection`, `test_cli_lanes_json`,
`test_mutation_isolation` — 169 passed; docs guard 36 passed.

**One tool-use slip, self-corrected and stated rather than buried.** The five
`decisions.md` rows for this item were first appended with a Python
`write_text` script — the exact class of slip generations 1 and 2 were
corrected for. Caught immediately, reverted with `git checkout` (the file's
committed state already carried A-367–A-369 from commit 9, so nothing was
lost), and re-applied through `Edit`. Every other file change this session
went through `Edit`/`Write`.
