# assay B036 — a JavaScript/TypeScript `LanguageAdapter` (R1) — REPORT

**Branch:** `feature/assay-b036-js-adapter`
**Base:** `main` at `62fe368f` (the commit that filed B036/B037)
**Filing:** B036 in `nyxloom-trove/4-backlog.md`
**Decisions recorded:** A-340 … A-345 (`nyxloom-trove/decisions.md`)
**New filings out of this work:** B038, B039
**Scope:** R1 only. R2 is B037's ruling and is not touched; R3 is not wired,
though both canary injection methods are real implementations.

Nothing in this report is asserted from a diff or from analogy to another
adapter. Every design claim was measured first, against real
`vitest run --coverage` output produced for **both** of Vitest's coverage
providers, and the two artifacts are committed as fixtures. Where a claim
could only be settled by running something, the transcript is pasted below.

---

## 1. What shipped

| file | what |
|---|---|
| `src/assay/coverage_parsers/coverage_istanbul_json.py` | new parser for istanbul's `coverage-final.json` |
| `src/assay/coverage.py:118` | fifth `FORMAT_REGISTRY` entry, `coverage-istanbul-json` |
| `src/assay/adapters/javascript.py` | new `JavaScriptAdapter`, all seven protocol methods |
| `src/assay/cli.py:282` | `_built_in_registry` entry, `rigor=frozenset({"R1"})` |
| `tests/fixtures/coverage/probe-js/**` | the committed Vitest driver project (12 files, no `node_modules`) |
| `tests/fixtures/coverage/coverage-istanbul-json.vitest-v8.json` | real artifact, `@vitest/coverage-v8` |
| `tests/fixtures/coverage/coverage-istanbul-json.vitest-istanbul.json` | real artifact, `@vitest/coverage-istanbul` |
| `tests/fixtures/coverage/PROVENANCE.md` | new section: how both were produced, and the 12 facts they witness |
| 8 new test modules | 153 new tests (see §5) |
| `README.md`, `docs/CONSUMERS.md`, `docs/DESIGN-GUIDE.md` | the new language and format documented alongside Python/Go/SQL |
| `CHANGES.md` `[Unreleased]` → `### Added` | one entry, alongside the existing ones (untouched) |

Nothing in the frozen protocol (`adapters/base.py`), the language-free core
(`evaluate.py`), the registry (`registry.py`), the verdict model, or the
packaged schema was modified. `judge.language` has no closed list to extend
and the schema's `language` field is an open string, so **no schema version
bump was needed** — checked, not assumed (`src/assay/schemas/verdict.schema.json`
line 2285: `{"type": "string", "minLength": 1}`).

Three pre-existing files changed beyond the two wiring lines, all of them
stale-claim repairs the new format forced:
`tests/test_coverage_registry.py` (four formats → five, a literal set by
design), `tests/test_coverage_branch_real_fixtures.py` (its docstring's
"these EIGHT files" now scopes itself to the coverage.py ones), and
`tests/fixtures/coverage/PROVENANCE.md`'s own opening count.

---

## 2. The measurement that drove every design decision

A committed, self-contained Vitest project
(`tests/fixtures/coverage/probe-js`) was run twice — once per coverage
provider — outside this repository, with Node `v26.5.1`, `vitest 3.2.4`,
`@vitest/coverage-v8 3.2.4`, `@vitest/coverage-istanbul 3.2.4`. Both
`coverage-final.json` documents are committed unedited, absolute keys and
all. The full command list and the 12 facts they witness are in
`tests/fixtures/coverage/PROVENANCE.md`'s own new section.

The single most consequential finding: **the two producers of this one
format do not agree about what parts of it mean.**

```
src/branchy.ts, one file, one test that calls branchy(0) only:

  @vitest/coverage-istanbul   statementMap: REAL extents, several multi-line
                              branchMap:    3 entries typed if/if/cond-expr,
                                            one location per ARM
                                            -> 6 arcs, 2 covered
  @vitest/coverage-v8         statementMap: one single-line entry per
                                            executable physical line
                              branchMap:    4 entries all typed "branch",
                                            ONE location each; one spans the
                                            whole function, one starts at a
                                            closing brace
                                            -> 4 "arcs", 1 covered
```

Everything below follows from that.

---

## 3. Design decisions

### 3.1 The `judge.language` string — `"javascript"` (A-340)

One adapter for `.js`/`.jsx`/`.ts`/`.tsx`, matching how `"python"` and
`"go"` do not split by dialect or extension. The load-bearing measured fact:
one `coverage-final.json` from one `vitest run` carries `.ts` and `.tsx`
records with identical structure and no language field anywhere — so a split
would force a lane touching one `.ts` and one `.tsx` file to declare two
languages for one measurement. `"typescript"` was rejected from the other
side: it leaves plain `.js`/`.jsx` (which every React project still has)
unnameable. It is explicitly NOT registered, and
`test_cli_run_javascript.py::test_an_unrecognised_language_is_still_refused`
pins that it is refused like any unknown name.

### 3.2 Where the absolute-path normalization belongs — NEITHER (A-341)

The brief posed this as parser-vs-`normalize_coverage_key`. Checking what
the existing four parsers actually do says: neither.

* `CoverageProfile`'s own model contract requires a parser to key files
  "exactly as that format's artifact names it… that stays the caller's job",
  and all four obey it — `go_cover` leaves the package-qualified import path
  alone and `GoAdapter.module_path` strips it; `coverage_py_json` leaves its
  key alone and `PythonAdapter.coverage_key_prefix` strips it. So the parser
  is the wrong place by the model's own rule.
* `normalize_coverage_key` is documented as the **language**-specific prefix
  strip, with the universal boundary reconciliation in the core
  (DESIGN-GUIDE §11). An absolute path is not a language fact.
* And it already works: `evaluate._to_repo_relative_key`'s ABSOLUTE branch
  (B006/A-145, added for real `coverage.py`'s absolute-path fallback)
  resolves the key against `repo_top`, and leaves a key naming something
  outside the repository untouched and therefore inert.

So `JavaScriptAdapter.normalize_coverage_key` returns its key unchanged
(`src/assay/adapters/javascript.py:315`), and a strip in either place would
have been dead code that looked load-bearing.

This is proven end-to-end rather than argued: the CLI test's lane command
writes `$PWD/src/app.ts` as its key — the shape a real coverage tool
produces, because assay runs the lane's command with `cwd` inside the
relocated snapshot — and the lane PASSes. An earlier draft of that test
hardcoded the caller's repository path instead and correctly FAILED with
`files_missing_coverage: ["src/app.ts"]`, which is exactly the silent
misjudgement this reconciliation prevents.

### 3.3 `requires_span_attribution` — `False`, with the gap closed in the parser (A-342)

Probed, per `adapters/go.py`'s own warning that its identical claim was
"settled, not assumed" only after A-172 found the first premise wrong.

* Under `@vitest/coverage-v8`, no multi-line extent exists anywhere in the
  artifact, so `False` is trivially safe.
* Under `@vitest/coverage-istanbul`, statement extents are real and often
  multi-line (`format.ts`: `[13,15]`, `[24,32]`, `[33,37]`, `[34,36]`),
  and their interior lines have no entry of their own. That is coverage.py's
  multi-line-statement gap in a different format — so `False` is **not**
  trivially true in general, and the initial premise would have been wrong.

Three options were compared:

1. `True` + a real `statement_spans` — needs a TypeScript/JSX parser written
   in Python from scratch: the categorically larger undertaking B037's own
   scope boundary exists to rule on, for a recovery the artifact can already
   supply. Rejected.
2. `False` with no expansion — silently drops every continuation line into
   `evaluate.py`'s rule 4. That is srdm's silent-excuse direction on lines a
   human actually edited. Rejected.
3. **Chosen:** expand each statement's own `[start.line, end.line]` extent in
   the parser (`coverage_istanbul_json._paint`, line 251), innermost extent
   winning, ties resolving by MAX count. The extent is the ARTIFACT's own
   claim about which source range a statement occupies, so this recovers
   exactly what Python's AST walk recovers — from data, not a re-parse — and
   leaves no line of a measured file unattributed.

**Innermost-wins is load-bearing, not a refinement.** In the real
istanbul-provider artifact, `branchy.ts`'s `if` extent `[2,4]` has count 1
while its own never-taken `return` at `[3,3]` has count 0. A go-cover-style
"executed wins" merge reports line 3 as covered — a false green on a line
that provably never ran. The MAX-count tie-break is
`istanbul-lib-coverage`'s own `getLineCoverage` rule, adopted rather than
invented.

Stated rather than hidden: a line that is only structurally part of a
statement (a lone closing brace) inherits that statement's status and counts
toward the denominator. That is the visible-false-failure direction, chosen
over the silent one.

### 3.4 Two honest `None`s (A-343, A-344)

`excluded` is always `None`: a real `/* istanbul ignore next */` hint
(committed as `probe-js/src/hinted.ts`) leaves the hinted statement in
`statementMap` with a live count and produces **no `skip` marker anywhere**
in either document. So an "ignored" line is indistinguishable from a line
that was never code — lcov's exact situation and lcov's exact answer.

`branches` is always `None`, and this is a measured refusal rather than an
omission — the format HAS a `branchMap`, but per §2 its two producers report
2/6 and 1/4 for the same file and the same tests. A lane declares the
FORMAT, never the producer, so a single translation would put a number on
the wire whose meaning depends on an undeclared fact. Filed as **B038**.

### 3.5 `has_executable_code` — two cases, fail-closed (A-343)

`False` only for a `.d.ts`/`.d.mts`/`.d.cts` declaration file (decided from
the path, because TypeScript's grammar decides it; neither provider reports
one) and for text that is empty once comments and whitespace are removed.
Everything else is `True`.

A type-only `.ts` module is deliberately NOT claimed as code-free even
though it genuinely has none: deciding it needs real TypeScript type-erasure
semantics, the same overreach rejected in §3.3. Under `@vitest/coverage-v8`
it never arises (measured: `typesonly.ts` IS reported, with
`"statementMap": {}`, so it reaches evaluation as a real record with zero
executable lines and this method is never consulted). Under
`@vitest/coverage-istanbul` it is absent and would be reported as missing
coverage — the visible direction, filed as **B038**, not papered over.

### 3.6 Canary injection — both appends, both verified against real Vitest (A-345)

JS/TS has an executable module top level, so nyxloom's insert-after-the-
prologue shape would port. But an ES module's imports are hoisted, so a
trailing `throw` still fires during module evaluation before any test can
touch an export — equally faithful to the contract, and needing none of
Python's insertion-point logic (which here would mean parsing multi-line
`import { … } from '…'` prologues). `adapters/go.py`'s two appends are the
precedent.

Unlike Go's, these were verified by running them. Transcripts in §4.

Two constraints shaped the snippets, both real defects avoided:

* these methods receive only `text`, never a path, so the same snippet is
  appended to a `.js` file — a type annotation would be a syntax error
  there. Hence `value = 0`: valid JavaScript, and TypeScript infers `number`
  so `noImplicitAny` is satisfied too;
* the canary function is `export`ed because `noUnusedLocals` flags an
  unreferenced module-level declaration — which a never-called canary is by
  construction — breaking the protocol's own "lint-clean" requirement.

### 3.7 One bound that had to be invented (`MAX_CLASSIFIED_LINES`)

Expanding extents line by line means a ~100-byte record declaring
`"end": {"line": 999999999}` — far inside the 16 MiB read bound —
materializes a billion entries. `coverage_istanbul_json.py:133` sets a
fixed, document-wide ceiling of 2,000,000 classified statement lines
(O4: "a fixed bound, never an ambient guess"), refusing
`ERROR`/`UNREADABLE_ARTIFACT` past it, with a paired must-succeed control.

Writing it surfaced that `go_cover.parse` has the identical unbounded
expansion with no ceiling at all. Not fixed here (out of scope, and a
different parser's behaviour change belongs in its own change) — filed as
**B039**.

---

## 4. Transcripts

### 4.1 The uncovered-line canary, against a real `vitest run --coverage`

`JavaScriptAdapter.inject_uncovered_line` applied to the probe's
`src/roles.ts` (26 lines after injection), then `vitest run --coverage`:

```
 Test Files  4 passed (4)
      Tests  6 passed (6)

roles.ts line->count: {7: 1, 8: 1, 9: 1, 10: 1, 11: 1, 17: 1, 18: 2,
                       19: 1, 20: 1, 23: 1, 24: 0, 25: 0, 26: 0}
```

Line 23 is `export function _assayCanaryUnreached(value = 0) {` — reached
(count 1) merely by the module loading. Lines 24-25 are its body and line 26
its closing brace — count 0, executed by no test. The suite still passes,
which is the point: a tests-only gate sails past this transform, a gate
enforcing changed-line coverage rejects it. Exactly the R1 axis the canary
isolates.

### 4.2 The import-break canary, against a real `vitest run`

`JavaScriptAdapter.inject_import_break` applied to the same module, then a
test importing it:

```
 FAIL  src/break.test.ts [ src/break.test.ts ]
Error: assay-canary-import-break
 ❯ src/roles_break.ts:23:7
     21|
     22|
     23| throw new Error("assay-canary-import-break")
       |       ^
     24|
 ❯ src/break.test.ts:2:1

 Test Files  1 failed | 4 passed (5)
```

The appended top-level `throw` fires during module evaluation, exactly as
A-345 argues — a real transcript, not a structural claim.

### 4.3 The registered gate — `bash tools/tester-unified-gate.sh`

Invoked exactly the way `nyxloom-trove/nyxloom.toml`'s own
`[gates.tester-unified]` invokes it (the SSOT pointer: the lane lives in
`assay/run-gate.toml` and `run-gate.py` drives
`tools/tester-unified-gate.sh`, which owns its own container mechanics —
exact-OID clone, pinned build closure, detached run). Running the gate script
directly with no argument, or against an uncommitted tree, is refused by the
script itself; both refusals were hit and are why the report is committed one
commit before this transcript.

```
$ cd assay && ./run-gate.py --worktree /workspaces/vbpub/.worktrees/assay-b036-js-adapter tester-unified
run-gate: rev 23 | lane tester-unified | env built-in 'host'
run-gate: budget 60m (advisory)
Looking in links: /workspaces/vbpub/.worktrees/assay-b036-js-adapter/assay/gate/distribution/build-wheelhouse
Processing ./workspaces/vbpub/.worktrees/assay-b036-js-adapter/assay/gate/distribution/build-wheelhouse/setuptools-84.0.0-py3-none-any.whl (from -r /workspaces/vbpub/.worktrees/assay-b036-js-adapter/assay/gate/distribution/build-requirements.txt (line 1))
Processing ./workspaces/vbpub/.worktrees/assay-b036-js-adapter/assay/gate/distribution/build-wheelhouse/wheel-0.47.0-py3-none-any.whl (from -r /workspaces/vbpub/.worktrees/assay-b036-js-adapter/assay/gate/distribution/build-requirements.txt (line 2))
Processing ./workspaces/vbpub/.worktrees/assay-b036-js-adapter/assay/gate/distribution/build-wheelhouse/setuptools_scm-10.0.5-py3-none-any.whl (from -r /workspaces/vbpub/.worktrees/assay-b036-js-adapter/assay/gate/distribution/build-requirements.txt (line 3))
Processing ./workspaces/vbpub/.worktrees/assay-b036-js-adapter/assay/gate/distribution/build-wheelhouse/packaging-26.3-py3-none-any.whl (from -r /workspaces/vbpub/.worktrees/assay-b036-js-adapter/assay/gate/distribution/build-requirements.txt (line 4))
Processing ./workspaces/vbpub/.worktrees/assay-b036-js-adapter/assay/gate/distribution/build-wheelhouse/vcs_versioning-2.2.4-py3-none-any.whl (from -r /workspaces/vbpub/.worktrees/assay-b036-js-adapter/assay/gate/distribution/build-requirements.txt (line 5))
Installing collected packages: setuptools, packaging, wheel, vcs-versioning, setuptools-scm

Successfully installed packaging-26.3 setuptools-84.0.0 setuptools-scm-10.0.5 vcs-versioning-2.2.4 wheel-0.47.0
Processing ./tmp/tmp.MMAMvLKK0g/clone/assay
  Preparing metadata (pyproject.toml): started
  Preparing metadata (pyproject.toml): finished with status 'done'
Building wheels for collected packages: assay
  Building wheel for assay (pyproject.toml): started
  Building wheel for assay (pyproject.toml): finished with status 'done'
  Created wheel for assay: filename=assay-2.4.3.dev20+g8aa62c62-py3-none-any.whl size=373706 sha256=a0c5d1b894519f9804dff088f1d4724418b28f28b469540743c8fe92755457c9
  Stored in directory: /tmp/pip-ephem-wheel-cache-v6qsk1yn/wheels/24/56/03/acda65d3d756c549442d8d55bbc9de23b95dcdba84a0ef91da
Successfully built assay
Processing ./tmp/tmp.MMAMvLKK0g/dist/assay-2.4.3.dev20+g8aa62c62-py3-none-any.whl
Installing collected packages: assay
Successfully installed assay-2.4.3.dev20+g8aa62c62
ASSAY_GATE_PHASE=wheel-installed
.........................                                                [100%]
25 passed, 16 deselected in 1.45s
ASSAY_GATE_PHASE=attestation-hardened
.............                                                            [100%]
13 passed, 31 deselected in 19.47s
ASSAY_GATE_PHASE=verdict-v5-accepted
.................                                                        [100%]
17 passed in 0.95s
ASSAY_GATE_PHASE=lane-schema-v2-successors-verified
v6 hard-cut guard passed for 6 frozen templates
ASSAY_GATE_PHASE=verdict-v6-successors-verified
.......................                                                  [100%]
23 passed in 0.86s
ASSAY_GATE_PHASE=verdict-v7-successors-verified
tester-unified: PASS (exit 0)
  commit: 8aa62c622afc6377b1dce2de57b9d012487685b0
  argv: python -m pytest tests -q --ignore=tests/test_self_hosting.py --override-ini=pythonpath=
ASSAY_GATE_PHASE=self-hosted-lane-passed
ASSAY_GATE_PHASE=topos-qualified
--- B006(a) WI-5 qualification receipt ---
input_oid=d2ad506a66d8f2a43170bce8ebf6c034d724fae3
qualification_baseline_oid=1bea2767444c4839da1b7c5d9f03e0e5869a7e59
head_oid=5e007b1d427194a80a308aabc9280e158de3f52a
outcome=PASS exit_code=0
claim[R0]=status=PASS
claim[R1]=status=PASS
claim[R2]=status=PASS
claim[R3]=status=PASS
r2_killed_identity={"description": "Eq->NotEq", "end_byte": 52, "lineno": 2, "operator": "python:compare-swap", "path": "cmru/src/cmru/_b006a_probe.py", "replacement_sha256": "c10987bd7cf853f6ea92ddac1b6c95fa830e3aee160cc5d4ba2fea3743be1aa2", "start_byte": 50}
r3_canary={"control_outcome": "PASS", "description": "appended never-called `def _assay_canary_unreached` (2 uncovered lines) at end of file", "expected_reason_code": "UNCOVERED_LINES", "mechanism": "uncovered-line", "observed_reason_code": "UNCOVERED_LINES", "target": "src/cmru/_b006a_probe.py", "transformed_outcome": "FAIL"}
snapshot_policy={"selection": "repository-minus-unsafe-symlinks", "unsafe_symlink_omissions": ["topos/tests/fixtures/inspect_files/_danger/passwd_link", "topos/tests/fixtures/inspect_files/cgroup_escape/system.slice/ssh.service/dangerous_link/passwd_escape", "topos/tests/fixtures/inspect_files/cgroup_nonreg/system.slice/ssh.service/memory.current"]}
omission_probe={"omitted_absent": [true, true, true], "cmru_root_present": true, "topos_ordinary_present": true, "status_clean": true}
ASSAY_B006A_CMRU_QUALIFIED=1
ASSAY_GATE_PHASE=cmru-b006a-qualified
.......                                                                  [100%]
7 passed in 12.78s
ASSAY_GATE_PHASE=independent-self-hosting-passed
ASSAY_REGISTERED_GATE_COMPLETE=1
run-gate: lane 'tester-unified' exit 0
```

The exit code was read in a SEPARATE step, never off a pipe tail (LESSONS L4):

```
$ tail -3 gate.txt
ASSAY_REGISTERED_GATE_COMPLETE=1
run-gate: lane 'tester-unified' exit 0
GATE_EXIT=0
```

All ten phase markers present and in order — `wheel-installed`,
`attestation-hardened`, `verdict-v5-accepted`,
`lane-schema-v2-successors-verified`, `verdict-v6-successors-verified`,
`verdict-v7-successors-verified`, `self-hosted-lane-passed`,
`topos-qualified`, `cmru-b006a-qualified`,
`independent-self-hosting-passed` — terminated by
`ASSAY_REGISTERED_GATE_COMPLETE=1`. The self-hosted lane ran assay's own
3,454-test suite through the WHEEL built from the exact commit
(`assay-2.4.3.dev20+g8aa62c62`), not a source import.

For reference, the same suite run directly in the worktree:

```
$ python -m pytest tests/ -q
3454 passed, 11 skipped, 1 warning in 374.01s (0:06:14)
```


---

## 5. Tests

8 new modules, 153 new tests. Every one drives real code; none asserts a
value this implementation computed and then copied back.

| module | tests | what it pins |
|---|---|---|
| `tests/test_coverage_parsers_coverage_istanbul_json.py` | 43 | sniffing (incl. non-collision with the other four formats), single/multi-line extents, three-level nesting, tie resolution both ways, empty statement map, both capabilities, 6 document-level + 14 record-level refusals, truncation, the size bound and its must-succeed control, unread fields ignored |
| `tests/test_coverage_istanbul_real_fixtures.py` | 18 | every PROVENANCE fact re-derived from the two REAL artifacts: absolute keys, which files each provider measures, `.d.ts`/test-file absence, single-line vs. real extents, null end columns, the nesting witness, the ignore-hint witness, the two branch maps not being the same measurement |
| `tests/test_adapters_javascript_registration.py` | 11 | protocol surface as literal values, `UNSUPPORTED`, registry equivalence, four adapters coexisting, R2/R3 refusal through `cli._built_in_registry()` itself, the R1 must-succeed control, unknown-language refusal, and the exact language→rigor map this build declares |
| `tests/test_adapters_javascript_test_path.py` | 31 | 16 test-path spellings, 15 non-test spellings incl. `latest.ts`/`respec.ts`/`my__tests__/`, and `.d.ts` not being a test path |
| `tests/test_adapters_javascript_has_executable_code.py` | 21 | `.d.ts` decided from the path, comments-only files, 8 shapes that must stay `True` (incl. the type-only module), unterminated block comment, comment-delimiter-in-string, `.d.ts` still being adapter source |
| `tests/test_adapters_javascript_canary_injection.py` | 11 | byte-preservation as a prefix, clean append boundary, purity/totality, both snippets' shape, no TS-only syntax, the `export` requirement, the two transforms being independent |
| `tests/test_evaluate_javascript_end_to_end.py` | 10 | the REAL v8 artifact rebased onto a temp repo: PASS, FAIL-with-named-lines, the floor control, zero unclassified lines across all five measured files, test files skipped, `.d.ts` as NoCode, an unmeasured module as a real gap, excluded directories, an out-of-repo key staying inert, capabilities on the evaluation |
| `tests/test_cli_run_javascript.py` | 8 | through the real CLI on a real two-commit git repo: R1 PASS, R1 FAIL naming every line of a multi-line extent, R2 refusal with a marker proving the command never ran, unknown-language refusal with the same marker, 3 malformed-artifact shapes → `ERROR`/`UNREADABLE_ARTIFACT`, and the must-distinguish `EMPTY_COVERAGE` control |

---

## 6. Acceptance

| B036 acceptance box | how it is satisfied |
|---|---|
| a `coverage-istanbul-json` parser registered in `FORMAT_REGISTRY`, parsing a REAL `coverage-final.json`, not a hand-written fixture alone | `src/assay/coverage_parsers/coverage_istanbul_json.py`; registered `src/assay/coverage.py:118`; two real artifacts committed under `tests/fixtures/coverage/`, exercised by `tests/test_coverage_istanbul_real_fixtures.py` (18 tests) and by the end-to-end module. Name matches the project's own `coverage-py-json` convention, checked against all four existing keys |
| a new adapter implementing all seven protocol methods, `generate_mutation_sites` returning `"UNSUPPORTED"` | `src/assay/adapters/javascript.py:309-341`; `UNSUPPORTED` at `:331`, pinned by `test_adapters_javascript_registration.py::test_generate_mutation_sites_is_unconditionally_unsupported` |
| registered in `cli.py`'s `new_registry(...)` for `frozenset({"R1"})` only | `src/assay/cli.py:281-283`; pinned as an exact language→rigor map by `test_the_built_in_registry_names_exactly_the_languages_this_build_reaches` |
| `is_test_path` excludes Vitest's conventions; `excluded_dir_names` excludes `node_modules` and build output | `javascript.py:164` (`_TEST_FILE_RE`) and `:300`. `dist` is Vite's documented default `build.outDir`, confirmed against `webapp-ui-react/vite.config.ts`'s own explicit `outDir: 'dist'`; `coverage` is Vitest's default `reportsDirectory`. 37 cases in `test_adapters_javascript_test_path.py`, plus a real-artifact check that both providers exclude all three test-naming conventions themselves |
| `.d.ts` handled correctly by `has_executable_code` — the NoCode case | `javascript.py:171`/`:312`; witnessed by both real artifacts omitting `probe-js/src/types.d.ts` entirely, and by `test_evaluate_javascript_end_to_end.py::test_a_changed_declaration_file_is_the_nocode_case_not_a_gap` (considered=1, executable=0, `files_missing_coverage == ()`, PASS) |
| a real end-to-end test: a `javascript` R1 lane against a real TS/TSX project reporting pass/fail | two layers. `test_evaluate_javascript_end_to_end.py` drives the real v8 artifact (only its directory prefix rebased) over the committed TS/TSX probe sources; `test_cli_run_javascript.py` drives the real CLI over a real two-commit git repo, PASS and FAIL |
| refusal paths: unrecognised `judge.language`, malformed/truncated artifact, `javascript` at R2 | `test_cli_run_javascript.py` — `test_an_unrecognised_language_is_still_refused`, `test_a_malformed_coverage_final_json_is_an_error_not_a_pass` (3 shapes incl. truncation), `test_a_javascript_lane_declaring_r2_is_refused_before_anything_runs` (marker file proves nothing ran). Plus 20 parser-level refusals and the `EMPTY_COVERAGE` must-distinguish control |
| `README.md`/`CONSUMERS.md`/`DESIGN-GUIDE.md` document the new language and format | README: status line + a new "JavaScript/TypeScript changed-line coverage (R1 only)" section beside SQL's. CONSUMERS: a new "JavaScript/TypeScript lanes (R1 only)" section with a pasteable lane, the Vitest config, and the three behaviours that differ from a Python lane. DESIGN-GUIDE: §5's sniff table, §10's fixture list, §11's format/language axis paragraph, a new four-adapter capability table, and the `branches`-is-`None` reasoning. All enforced by the existing `test_docs_examples_and_vocabulary.py` docs gate, which failed on this change until they were written |
| the real registered gate run green | §4.3 |

---

## 7. What a reviewer should push on

1. **§3.3's over-approximation.** A lone closing brace inside a never-executed
   statement now counts as an uncovered changed line. That is deliberate and
   argued (visible failure over silent excuse), but it is a real behavioural
   choice and the opposite choice is defensible.
2. **`has_executable_code`'s narrowness.** A type-only `.ts` module answers
   `True`. Under the v8 provider that is unreachable; under the istanbul
   provider it is a false failure. B038 owns it. The alternative — a
   hand-written TypeScript type-erasure scan — was rejected on B037's own
   scope boundary, not on effort, and that judgement is worth a second look.
3. **`branches = None`.** The most consequential refusal here. If a reviewer
   thinks the istanbul provider's arcs should be read today and the v8
   provider's `branchMap` simply ignored, that is a coherent alternative
   position — it requires deciding the producer is derivable from the
   artifact, which A-007 forbids doing silently.
4. **The decision-id gap (A-327…A-339).** Deliberate, to avoid colliding with
   the in-flight `feature/assay-b018-b019-b035-v8-synergy` wave, which had
   consumed through A-333 when this was written. If that wave lands with
   fewer, the gap stays. Reasoning is in the decisions.md section header.
5. **B039 was filed, not fixed.** `go_cover.parse` has the same unbounded
   expansion this parser was given a ceiling for. Leaving a known
   resource-exhaustion shape in place for one more cycle is a choice.
