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
