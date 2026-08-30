# assay Wave A ("JS consumer wave", target release 3.2.0) — commit LOG

One entry per commit, in order. Branch `feature/assay-wave-a-js-consumer`,
worktree `.worktrees/assay-wave-a-js-consumer`, base `main` @ `52e033a3`.

---

## `04ad5688` — feat(assay): B044 — `assay lanes --json`, a machine-readable lane inventory

**Files:** `src/assay/cli.py`, `tests/test_cli_lanes_json.py` (new)

**What:** Adds the `--json` flag to `assay lanes`, plus
`_render_lanes_json`/`_lane_inventory_entry` producing
`{inventory_schema, assay_version, lanes: [...]}`. Every field traces to one
producer (`Lane`/`JudgeConfig`, or `_built_in_registry()`); `rigor_reachable`
and `external_tools` come from the registry (`[]` for an unregistered
language, never a refusal); `base_source` resolves A-328's own absent-means-
"declared" default rather than passing `None` through, `null` only where no
tier reads a comparison commit at all. Wave-B keys (`cwd`, `link_paths`,
`coverage.producer`) are `null`/`[]`. A lane file that fails to load exits 2
with empty stdout (the existing `except AssayError` path in `main()`, no new
try/except needed).

**Why:** CIU v8 (S16 stage 12, CIU-72) needs to preflight a lane's
environment fitness (does a `javascript` lane's environment have Node?) and
delegation policy (`base_source`) without re-parsing `assay.toml` or
restating a fact assay already resolves.

**Tests added:** 11 new tests in `test_cli_lanes_json.py` — golden JSON for
an R0-only, a Python R1 (declared base), a JavaScript R1 (delegating), a SQL
R2 lane; file-order preservation across multiple lanes; the refusal path
(bad/missing lane file, exit 2, empty stdout); "does not execute the argv";
"writes no verdict artifact". All existing `test_cli_lanes.py` (15) and
`test_docs_examples_and_vocabulary.py` (34) tests re-verified green.

---

## `1eeab9db` — fix(assay): B039/B047-4 — one shared classified-line ceiling for every expanding coverage parser

**Files:** `src/assay/coverage_parsers/model.py`,
`src/assay/coverage_parsers/go_cover.py`,
`src/assay/coverage_parsers/coverage_istanbul_json.py`,
`tests/test_coverage_parsers_go_cover.py`,
`tests/test_coverage_parsers_coverage_istanbul_json.py`

**What:** `MAX_CLASSIFIED_LINES` and a new `ClassifiedLineBudget` class move
into `coverage_parsers/model.py` (importing `assay.errors`, itself a leaf —
no cycle introduced). `go_cover.parse` now spends the budget before
expanding each block's `range(start, end + 1)`, refusing
`ERROR`/`UNREADABLE_ARTIFACT` past it exactly as `coverage_istanbul_json`
already does. Both parser modules re-export `MAX_CLASSIFIED_LINES` into
their own namespace (`from .model import MAX_CLASSIFIED_LINES as
MAX_CLASSIFIED_LINES`) and pass it explicitly at each `parse()` call — never
a default baked into `ClassifiedLineBudget.__init__` — so each module's
pre-existing `monkeypatch.setattr(<module>, "MAX_CLASSIFIED_LINES", ...)`
test idiom keeps working. The refusal message moved from istanbul's own
literal "classified statement lines" to the shared, format-agnostic
"classified lines" (with a `{format_name}: ` prefix per record), since
"statement lines" is meaningless for a Go block. Also corrects
`coverage_istanbul_json.py`'s own module docstring, which claimed "nyc/
istanbul or Jest... unaffected" with no scope to Jest's default `babel`
provider (the same overclaim class B042 fixes elsewhere; named explicitly as
a grep target for "the parser docstring" in the wave prompt).

**Why:** `go_cover.parse` had NO bound at all — a ~60-byte block line
declaring an end line of `999999999` would materialize close to a billion
dict entries, the identical shape `coverage_istanbul_json` was given a bound
for specifically because it is dangerous (B039, filed by B036's own
implementation).

**Tests added/changed:** `test_coverage_parsers_go_cover.py` gains 7 tests
(malicious-block refusal; must-succeed control over the real
`DRIVE_LETTER_AND_OVERLAP_ARTIFACT` fixture at the SHIPPED ceiling; a
`tiny_bound` fixture mirroring istanbul's own; at-bound/one-past-bound/
spent-across-the-whole-profile boundary tests; an object-identity pin that
`go_cover.MAX_CLASSIFIED_LINES is model.MAX_CLASSIFIED_LINES`).
`test_coverage_parsers_coverage_istanbul_json.py`: 3 pre-existing tests'
string assertions updated from `"classified statement lines"` to
`"classified lines"` to match the new shared message — no behavioral
assertion changed. Decision A-348.

---

## `5e347d04` — test(assay): B042 item 2 — measure `c8`'s v8-to-istanbul remapper against the same defect probe

**Files:** `tests/fixtures/coverage/probe-js-provider-defect-c8/` (new:
`package.json`, `package-lock.json`, `run.mjs`, `src/shapes.ts`),
`tests/fixtures/coverage/coverage-istanbul-json.provider-defect.c8.json`
(new), `tests/test_coverage_istanbul_provider_accuracy.py`

**What:** A self-contained harness reproducing `probe-js-provider-defect`'s
own ground truth (five guarded functions, one `f(0)`-only call each) under
`c8@12.0.0` instead of Vitest — Node's own native TypeScript support
imports `src/shapes.ts` (a byte-identical copy of the frozen fixture)
directly, no Vitest, no test framework. `C8`/`C8_FALSE_GREENS` constants
and three new tests re-derive the measured result from the committed
artifact on every run.

**Why:** README/CONSUMERS said `c8` was "not measured" for the same
false-executed-line defect ruled on for `@vitest/coverage-v8` (A-346); the
wave prompt invited reproducing it "cheaply" if possible, and node is on
PATH in this devcontainer.

**Measured result:** `c8`'s own `v8-to-istanbul` remapping ALSO reports
never-executed lines as executed, triggered by the same conditional-
expression shape, and correct on the same three non-triggering shapes
(binary/call/object-literal expressions) — but the exact false-positive set
(`{9, 10, 11, 16, 17, 18}`) is a strict superset of `vitest3-v8`'s own
(`{10, 11, 16, 17, 18}`; line 9, the ternary's own second arm, is
additionally wrong here) and disagrees with `vitest4-v8`'s narrower one.
Reported precisely as a related-but-not-identical defect, not conflated with
Vitest's own.

**Tests added:** `test_c8_also_falsely_reports_never_executed_lines_as_executed`,
`test_c8s_false_positive_set_is_not_identical_to_either_vitest_v8_reading`,
`test_only_the_ternary_shapes_trigger_c8s_mis_attribution_too` (22 total in
the module, up from 19).

---

## `0fbe1261` — test(assay): B048 — a real `vite-plugin-istanbul` artifact proves original src/*.ts(x) keys

**Files:** `tests/fixtures/coverage/probe-js-vite-plugin-istanbul/` (new:
`package.json`, `package-lock.json`, `vite.config.ts`, `index.html`,
`src/math.ts`, `src/main.ts`, `run-coverage.mjs`),
`tests/fixtures/coverage/coverage-istanbul-json.vite-plugin-istanbul.json`
(new), `tests/test_coverage_parsers_vite_plugin_istanbul_artifact.py` (new)

**What:** A minimal Vite project built for real
(`vite-plugin-istanbul@9.0.1`, `forceBuildInstrument: true` — the plugin
does NOT instrument a production build by default) and executed for real in
jsdom (`run-coverage.mjs`, committed as the capture harness), reading back
`window.__coverage__`. `subtract`'s defensive branch is genuinely never
taken by the one real call in `main.ts`, so this is a real partial-coverage
artifact, not a trivially-all-green one.

**Why:** B048 documents a browser-coverage lane pattern that needs no assay
code change, but its own load-bearing claim ("keys are the original
`src/*.tsx` paths, never `dist/`") had to be MEASURED, not assumed from the
plugin's README.

**Tests added:** `test_every_key_is_an_original_src_path_never_a_dist_bundle_path`,
`test_the_real_instrumented_bundle_reports_genuine_partial_coverage`,
`test_the_parser_needed_no_change_for_this_producer` — all 3 pass, driving
the committed artifact through the existing, UNMODIFIED
`coverage-istanbul-json` parser.

---

## `28b39344` — docs(assay): PROVENANCE entries for the c8 and vite-plugin-istanbul artifacts

**Files:** `tests/fixtures/coverage/PROVENANCE.md`

**What:** Two new sections (exact tool versions, exact commands, a
fact/witness table, what each artifact does and does not license claiming)
for the two artifacts committed in the two prior commits — A-334's
requirement that a committed artifact carries the same provenance record
every other fixture in this directory already does.

**Why:** separated from the artifact commits themselves so each of those
stays scoped to its own fixture files plus the test that reads them; this
commit is pure documentation of both.

---

## `0ea21a05` — test(assay): B041(c) — real Vitest qualification through the real assay CLI

**Files:** `tests/qualification/test_javascript_real_vitest.py` (new)

**What:** Skipped everywhere except `ASSAY_NODE_QUALIFICATION=1` with
node/npm on `PATH` (named reason in the `pytest.mark.skipif`). Builds a
private, offline-replayable npm cache from the committed `probe-js`
lockfile (B041(a)'s pattern), materialises a real two-commit git fixture
via the `git_repo` fixture, and drives `assay.cli.main` (the real CLI entry
point) against a real `npx --no-install vitest run --coverage` inside
assay's own isolated snapshot. Two scenarios: a fully-covered diff (PASS)
and a diff with one genuinely uncovered defensive-branch line (FAIL, naming
line 7 exactly).

**Why:** B041(c)'s own contract — every prior JS CLI test used a `/bin/sh
-c` heredoc as the lane's own command (A-334: a test double is not evidence
about an external system), and `tester-unified` genuinely has no Node
toolchain (DESIGN-GUIDE §10), so this can never be a registered-gate test
either.

**What running it for real surfaced:** B049/A-347 — Vitest's own default
`coverage.clean = true` silently orphans `safeio.reserve_output`'s held
parent-directory descriptor, reading a fully-covered real lane as
`NO_MEASUREMENT`/`EMPTY_COVERAGE`. Isolated by direct A/B measurement
(toggling `clean` alone, nothing else, flips the outcome). Filed as B049,
NOT fixed in code this wave (core, language-free machinery outside Wave
A's scope) — see the REPORT's decision-adjacent section and A-347 for the
full mechanism and the options left for a maintainer ruling. Every
`vitest.config.ts` this module writes declares `clean: false`.

**Transcripts:** both PASS and FAIL runs, pasted into the wave REPORT.

---

## `5bd20c71` — docs(assay): B041(a)/B042/B044/B048/B049 — README and CONSUMERS updates

**Files:** `README.md`, `docs/CONSUMERS.md`

**What:** Combined doc landing (both files touched by multiple items,
hence one commit — see the commit body for the per-item breakdown):
- B041(a): new CONSUMERS section "JavaScript lanes and the dependency
  closure" (mechanism, worked monorepo lane, `npx` fetch hazard,
  `environment_command` caveat, R3 cost, one-paragraph `link_paths`
  preview marked Wave B). Replaces the old root-level-only worked lane.
- B042: Jest/`c8` scope corrected in both README and CONSUMERS (measured,
  not "untested"); the support-files trap's mechanism corrected to what was
  actually measured (`coverage.include`'s zero-coverage synthesis, not an
  exclude glob); the README `defineConfig` import comment; cross-links.
- B044: new CONSUMERS section "Preflighting a gate environment with
  `assay lanes --json`".
- B048: new CONSUMERS section "Browser coverage of a UI as an R1 lane".
- B049: every `vitest.config.ts` snippet now declares `clean: false`, with
  the mechanism and the measured before/after in prose.

**Why:** see each item's own commit/backlog entry.

**Tests:** `test_docs_examples_and_vocabulary.py` re-verified green (34
passed) — no new `toml` fences were added by this commit's edits, so the
existing count is unchanged; anchors manually verified (`#javascript-lanes-
and-the-dependency-closure`, `#the-v8-provider-is-not-safe-to-gate-on`).

---

## `917c1e92` — backlog(assay): Wave A — file B049, record A-347..A-350, tick acceptance boxes

**Files:** `nyxloom-trove/4-backlog.md`, `nyxloom-trove/decisions.md`

**What:** Files B049 (the `coverage.clean`/`EMPTY_COVERAGE` reservation
defect, three unranked fix options left for a maintainer ruling). Records
decisions A-347 (the B049 finding and Wave A's documentary mitigation),
A-348 (B039's shared-bound design and why `remaining` has no default),
A-349 (B044's inventory field set and its stability rule), A-350 (the
qualification harness's place in the gate — never wired into
`tools/tester-unified-gate.sh`, mirroring P25's real-environment-gated
shape). Ticks B039, B041(a)+(c), B042, B044, B047 item 4, and B048's
acceptance boxes with file:line evidence. B041(b)/B043/B045/B046 (Wave B)
left untouched.

---

## `f7bf309e` — docs(assay): CHANGES.md [Unreleased] entries for Wave A

**Files:** `CHANGES.md`

**What:** One Added/Fixed/Documentation bullet per landed item (B044,
B039/B047-4, B041(a), B042, B048, B049), no `!` marker (minor bump).

**Note:** the pre-existing B036 entry above these is leftover from 3.1.0 —
CHANGES.md's own housekeeping comment says `[Unreleased]` should have been
cleared at that release and was not. Left untouched per the wave's division
of labor (the controller clears `[Unreleased]` as part of cutting the
release); flagged in the REPORT.

---

## `4a4056b6` — docs(assay): Wave A commit LOG

**Files:** `nyxloom-trove/reports/assay-WAVE-A-js-consumer-LOG.md` (this
file, its own first version).

---

## `4a70e09e` — docs(assay): fix a dangling "see the qualification harness below" reference

**Files:** `docs/CONSUMERS.md`

**What:** Self-review catch in the R3-cost paragraph of "JavaScript lanes
and the dependency closure" (B041(a)) — "see the qualification harness
below" pointed at nothing; the section it was written for was cut during
drafting. Replaced with a direct pointer to
`tests/qualification/test_javascript_real_vitest.py`.

---

## `8353ff25` — docs(assay): Wave A REPORT, and LOG entries for the housekeeping commits

**Files:** `nyxloom-trove/reports/assay-WAVE-A-js-consumer-LOG.md`,
`nyxloom-trove/reports/assay-WAVE-A-js-consumer-REPORT.md` (new)

**What:** The REPORT's first full version (per-item acceptance evidence,
both qualification transcripts, the c8 measurement, decisions recorded,
docs disposition table); the gate section left as a placeholder pending the
real run.

---

## `e9424676` — docs(assay): commit the qualification transcript (renamed past *.log gitignore)

**Files:** `nyxloom-trove/reports/assay-WAVE-A-qualification-transcript.txt`
(renamed from `.log`), `nyxloom-trove/reports/assay-WAVE-A-js-consumer-REPORT.md`

**What:** The committed qualification transcript was silently excluded by
the repo's blanket `*.log` gitignore rule. Renamed to `.txt` (a
deliberately committed artifact, not an ephemeral run log); the REPORT's
own reference updated to match. **This is the commit the registered gate
judged green** — see the REPORT's §11 for the full transcript.

---

## (post-gate) docs(assay): the gate transcript, and the REPORT's final §11

**Files:** `nyxloom-trove/reports/assay-WAVE-A-gate-transcript.txt` (new),
`nyxloom-trove/reports/assay-WAVE-A-js-consumer-REPORT.md`

**What:** The registered gate's own full, green transcript (judged commit
`e9424676`), committed verbatim; REPORT §11 filled in with the real result,
replacing the placeholder and honestly recording the two environmental
(non-product) failed attempts that preceded it. Lands after the judged
commit — pure documentation of an already-green run, no source or test
file touched.
