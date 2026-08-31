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

## 2 — `14df397a` · `feat(assay): B045 (1/2) -- the coverage PRODUCER as a declared, per-format, closed fact`

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
