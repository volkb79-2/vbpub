# assay Wave A ("JS consumer wave", target release 3.2.0) — REPORT

**Branch:** `feature/assay-wave-a-js-consumer` · **Worktree:**
`.worktrees/assay-wave-a-js-consumer` · **Base:** `main` @ `52e033a3`.
**Commits:** see `assay-WAVE-A-js-consumer-LOG.md` (10 commits, one per
concern). **Read first:** the wave prompt names decisions.md rows A-334,
A-335, A-340..A-346, A-253, A-272, A-007 and backlog B041/B042/B044/B047
item 4/B048/B039 as this session's contracts; all read in full before any
edit, per the READ FIRST list.

---

## 1. B044 — `assay lanes --json`

**Contract:** `assay lanes --json [--file PATH]`, `inventory_schema: 1`,
every field with one producer, exit 2 + empty stdout on a bad lane file,
golden JSON over a javascript/sql/delegating lane, Wave-B keys null/[].

- [x] Every field has ONE producer — `src/assay/cli.py:947-1122`
  (`_render_lanes_json`/`_lane_inventory_entry`): `Lane`/`JudgeConfig` for
  everything declared, `_built_in_registry()` for `rigor_reachable`/
  `external_tools` only. `base_source` is the one place this function
  DERIVES (A-328's own absent-means-"declared" default) rather than passes
  through raw — documented in the function's own docstring and recorded as
  A-349, because the alternative forces every consumer to reimplement
  A-328's default rule.
- [x] Exit 2, empty stdout on a bad lane file —
  `test_a_lane_file_that_fails_to_load_exits_two_with_no_json_on_stdout`,
  `test_a_missing_lane_file_exits_two_with_no_json_on_stdout`
  (`tests/test_cli_lanes_json.py:262-283`). No new try/except needed: the
  existing `except AssayError` in `main()` (`cli.py:265-267`) already gives
  this subcommand its refusal, since `_resolve_lane_file` runs before
  `_render_lanes_json` is ever called.
- [x] Golden JSON, javascript/sql/delegating — `test_cli_lanes_json.py`:
  `test_an_r0_only_lane`, `test_a_python_r1_lane_with_a_declared_base`,
  `test_a_javascript_r1_lane_that_delegates_its_base`, `test_a_sql_r2_lane`,
  `test_a_lane_delegating_its_base_records_base_source_request` (11 tests
  total, all pass).
- [x] Never executes a lane, writes no verdict artifact —
  `test_lanes_json_does_not_execute_the_lane_argv`,
  `test_lanes_json_writes_no_verdict_artifact`.
- [x] CONSUMERS shows a gate consuming it —
  `docs/CONSUMERS.md:938-1001` ("Preflighting a gate environment with
  `assay lanes --json`"), under "CMRU / tester-unified integration". The
  "ciu handoff note" acceptance box item is CIU-72 itself, already filed by
  the design review in ciu's OWN backlog — out of scope for this wave
  (touches `assay/**` only); flagged below for the controller.

**Design call not in the backlog text, recorded as A-349:** `rigor_reachable`
and `external_tools` never RAISE for an unregistered `language` — `[]`, not a
refusal — because `assay lanes` has never executed or refused a lane (A-054),
and the whole point is letting a gate compare `rigor` against
`rigor_reachable` itself instead of discovering a mismatch only when a real
`assay run` refuses.

---

## 2. B042 — JavaScript consumer documentation corrections

Every item names its exact disposition (see the docs table in §7 below for
line numbers). Item 1 (the worked-lane replacement) is discharged by B041(a)
below rather than duplicated here.

- [x] Item 2 (Jest scope). Landed with the c8 measurement bonus (§3 below)
  rather than "untested" — the wave prompt's own permission ("If the
  implementer can reproduce... under `c8` cheaply... state the measured
  result").
- [x] Item 3 (support files). Landed, and CORRECTED past the backlog
  entry's own assumed mechanism — see §4.
- [x] Item 4 (README `defineConfig` snippet comment) — landed,
  `README.md:170-172`.
- [x] Item 5 (cross-links) — landed both directions.
- [x] Acceptance: no doc still calls the two Vitest providers
  interchangeable or Jest unconditionally unaffected — re-grepped
  2026-08-30 across README/CONSUMERS/DESIGN-GUIDE/the parser docstring
  (`src/assay/coverage_parsers/coverage_istanbul_json.py`, the one file
  outside README/CONSUMERS that had the overclaim — corrected in the
  B039/B047-4 commit since it's the same file that commit already touched).

---

## 3. B042 item 2 — the `c8` measurement

**What was asked:** state a measured result if `c8` reproduction is cheap;
otherwise leave "untested."

**What was measured.** `tests/fixtures/coverage/probe-js-provider-defect-c8/`
imports `probe-js-provider-defect/src/shapes.ts` (byte-identical copy,
diffed at production time) via Node's own native TypeScript support and
runs the five guarded functions under `c8@12.0.0`, no Vitest involved.

```
c8's own false-positive set:      {9, 10, 11, 16, 17, 18}
vitest3-v8's own false-positive set: {10, 11, 16, 17, 18}
vitest4-v8's own false-positive set: {10, 11, 17, 18}
```

`c8` shares the trigger (a conditional expression) and the three
non-triggering shapes (binary/call/object-literal) with `@vitest/coverage-v8`
— but its own set is a strict SUPERSET of `vitest3-v8`'s (line 9, the
ternary's own second arm, is additionally wrong here) and disagrees with
`vitest4-v8`'s narrower one. Reported as a related-but-not-identical defect
in both README and CONSUMERS, not conflated with Vitest's own — see A-346's
own process lesson ("measured both producers for SHAPE and never once for
ACCURACY") applied a third time rather than repeated.

Witness: `tests/fixtures/coverage/coverage-istanbul-json.provider-defect.c8.json`,
`tests/fixtures/coverage/PROVENANCE.md`'s new section (exact commands,
versions, reproducibility note), `tests/test_coverage_istanbul_provider_accuracy.py`'s
`C8`/`C8_FALSE_GREENS` and three new tests (22 total in that module, up
from 19 — all pass).

---

## 4. B042 item 3 — the support-files trap, corrected

The backlog entry's own text asserted the mechanism was "Vitest's default
`coverage.exclude` drops config files from the artifact." **Measured this to
be wrong** before writing it into CONSUMERS.md:

1. Inspected `vitest@4.1.11`'s own `chunks/defaults.*.js`:
   `coverageConfigDefaults.exclude = []` — empty. The format-agnostic
   default is not a glob list at all.
2. Inspected the actual hardcoded exclude computation
   (`chunks/coverage.*.js:343-352`, comment: "Add hard-coded default
   coverage exclusions. These cannot be overridden by user config."): it
   appends `resolved.setupFiles`, `resolved.include` (the TEST-name glob,
   `**/*.{test,spec}.*`), `resolved.config` (the ONE resolved vitest/vite
   config file actually in use — a literal path, not a glob), and a fixed
   `configFiles` list of exact candidate config FILENAMES
   (`vite.config.ts`, `vitest.config.js`, ...) — never an arbitrary
   `*.config.*`/`*.stories.*` pattern.
3. Built a real scratch Vitest project with `src/thing.config.ts` and
   `src/thing.stories.tsx` alongside real source. With NO `coverage.include`
   override (Vitest's own default), neither appears in the artifact at all
   — because they were never imported, so v8/istanbul never instrumented
   them; nothing to do with an exclude glob. With `coverage.include =
   ['src/**']` set (as every worked lane in this guide declares), BOTH
   appear, every statement at count `0` — Vitest's own "report on untested
   files" feature synthesises a zero-coverage record for every file the
   glob MATCHES, whether imported or not.

**What shipped:** `docs/CONSUMERS.md`'s "support files" paragraph states the
real mechanism (`coverage.include`'s zero-coverage synthesis, gated on
declaring `include` — which every worked lane in this guide does), corrects
the record on what Vitest's hardcoded excludes actually cover, and keeps the
practical advice identical (keep these files out of `source_roots`) since
the END EFFECT a consumer must avoid is the same regardless of mechanism.

---

## 5. B041(a)+(c) — dependency closure docs + real-Vitest qualification

### (a) CONSUMERS section

`docs/CONSUMERS.md:568-685`, "JavaScript lanes and the dependency closure":
mechanism (confirmed against `isolation.py` — snapshots are `git read-tree`,
tracked blobs only), pattern (a)'s worked MONOREPO lane
(`applications/webapp-ui-react/`, offline install + `--no-install` runner,
`base_source = "request"`), the image-side npm-cache recipe, the `npx` fetch
hazard, the `environment_command` caveat, the R3 cost (baseline + two canary
snapshots, each repeating the offline install), and a one-paragraph preview
of (b) `link_paths` explicitly marked "Wave B, schema v9" per the wave
prompt's own instruction (this item's own acceptance box asked for the FULL
purity trade-off write-up; the wave prompt supersedes that for Wave A — see
§8).

### (c) Qualification harness

`tests/qualification/test_javascript_real_vitest.py`. Skipped everywhere
except `ASSAY_NODE_QUALIFICATION=1` with node/npm on `PATH`
(`pytestmark = pytest.mark.skipif(...)`, reason named in `_ENV_REASON`).
Builds a private npm cache from `probe-js`'s own committed lockfile,
materialises a real two-commit git fixture via the `git_repo` fixture, and
drives `assay.cli.main` — the real CLI entry point — against a real `npx
--no-install vitest run --coverage` inside assay's own isolated snapshot.

**Both transcripts** (`ASSAY_NODE_QUALIFICATION=1 python3 -m pytest
tests/qualification/ -v -s`, full log at
`nyxloom-trove/reports/assay-WAVE-A-qualification-transcript.txt`, excerpted
here):

```
tests/qualification/test_javascript_real_vitest.py::test_a_real_javascript_lane_passes_end_to_end
$ assay run ui --file .../repo/assay.toml --verdict-json -
exit=0
...
{
  "outcome": "PASS",
  "claims": [
    {"rigor": "R0", "status": "PASS", ...},
    {"rigor": "R1", "status": "PASS", "coverage": {
        "pct": 100.0, "executable": 1, "covered": 1, "missing_lines": {}
    }}
  ],
  ...
}
PASSED

tests/qualification/test_javascript_real_vitest.py::test_a_real_javascript_lane_fails_and_names_the_uncovered_line
$ assay run ui --file .../repo/assay.toml --verdict-json -
exit=1
...
{
  "outcome": "FAIL",
  "reason_code": "UNCOVERED_LINES",
  "claims": [
    {"rigor": "R0", "status": "PASS", ...},
    {"rigor": "R1", "status": "FAIL", "reason_code": "UNCOVERED_LINES",
     "coverage": {
        "pct": 75.0, "executable": 4, "covered": 3,
        "missing_lines": {"src/app.ts": [7]}
    }}
  ],
  ...
}
PASSED

======================== 2 passed, 1 warning in 11.22s =========================
```

Re-run twice for stability (both green); the no-env-var path re-verified to
skip cleanly (`2 skipped`).

**This is the A-335 proof the 3.1.0 wave did not have** (every prior JS CLI
test drove a heredoc, A-334's own "test double is not evidence" applied to
assay's OWN prior work, not just a consumer's).

---

## 6. What running the qualification harness for real surfaced: B049 / A-347

Running (c) for real — the first time any real external coverage tool has
run inside an assay snapshot — surfaced a genuine, previously-unknown
defect, not anticipated by B041's own text.

**Measured, isolated by direct A/B** (nothing else changed):

| `vitest.config.ts` | `assay run` result |
|---|---|
| `coverage.clean` unset (Vitest's own default, `true`) | `NO_MEASUREMENT`/`EMPTY_COVERAGE`, exit 3 |
| `coverage.clean = false`, nothing else changed | `PASS`, `pct: 100.0` |

A `cp .assay/coverage-final.json /tmp/…` appended to the SAME lane command,
run immediately after `vitest` and BEFORE assay's own post-command read,
proved the artifact was genuinely complete and correctly keyed the whole
time — the reservation mechanism failed to find something that really was
there.

**Mechanism, cited exactly:** `runner.py:1692`
(`safeio.reserve_output(..., create_missing_parents=True)`) opens/creates
the coverage artifact's parent directory ONCE, before the lane's command
runs, and holds that directory's own file descriptor for the whole
execution. `runner.py:1771` (`reservation.consume()`) reads the artifact
AFTER the command exits by looking up the declared basename relative to
that SAME held descriptor (`safeio.py:319`,
`os.open(basename, dir_fd=parent_fd)`) — never re-opening the path fresh.
Vitest's own default `coverage.clean = true` deletes and recreates
`reportsDirectory` rather than writing into the one directory assay already
opened, orphaning the held descriptor: it now points at an empty, unlinked
directory, so the lookup raises `FileNotFoundError`, read downstream as "the
command never wrote anything."

**Not fixed in code this wave** — `safeio.py`/`runner.py` are core,
language-free machinery shared by every adapter (Python R1/R2/R3, SQL R2, JS
R1) and every future one; the wave prompt scopes Wave A to the JS adapter's
own consumer-facing gaps and explicitly excludes core-mechanism changes.
Filed as **B049** with three unranked fix options (re-open by name at
consume time and lose part of `arm()`'s TOCTOU protection; detect a
recreated directory and name a distinguishable reason instead of folding
into `EMPTY_COVERAGE`; leave it fully consumer-owned via documentation, the
Wave A default) and recorded as **A-347**. Every `vitest.config.ts` this
wave ships (README, CONSUMERS, the qualification harness) declares
`clean: false` as the documented, working mitigation.

**This is a decision ask, not a BLOCKED item** — it did not block anything
in this wave's own contract (the workaround exists and is documented), but
it needs a maintainer ruling among B049's three options before Wave B or a
later Go wave meets the same class of defect with a different tool.

---

## 7. B039 / B047 item 4 — the shared classified-line ceiling

**Contract:** ONE shared classified-line ceiling in
`coverage_parsers/model.py`, used by `go_cover` AND
`coverage_istanbul_json`; `go_cover` refuses `ERROR`/`UNREADABLE_ARTIFACT`
past it with a must-succeed control over a real profile.

- [x] `MAX_CLASSIFIED_LINES` + `ClassifiedLineBudget` moved to
  `coverage_parsers/model.py:34-125` (class body at `:89-125`).
  `go_cover.py:103-105` spends the budget before expanding each block's
  range; `coverage_istanbul_json.py:186-188` does the same at its own call
  site (previously its own local `_Budget` class, now deleted).
- [x] Must-succeed control over a real profile —
  `test_an_ordinary_real_shaped_profile_still_parses_under_the_shared_bound`
  (`tests/test_coverage_parsers_go_cover.py`), run at the SHIPPED
  2,000,000 ceiling (not monkeypatched), against the existing
  `DRIVE_LETTER_AND_OVERLAP_ARTIFACT` fixture.
- [x] Malicious-block refusal —
  `test_one_enormous_block_is_refused_rather_than_expanded`
  (`pkg/f.go:1.1,999999999.1 1 1`).
- [x] Boundary arithmetic (at/one-past/spent-across-the-whole-profile) —
  mirrors istanbul's own `tiny_bound` fixture pattern exactly, 3 tests.
- [x] Object identity, not merely equal literals —
  `test_the_shipped_bound_is_the_one_shared_documented_value` asserts
  `go_cover.MAX_CLASSIFIED_LINES is model.MAX_CLASSIFIED_LINES`.

**Design call not fully spelled out in the backlog, recorded as A-348:** each
module RE-EXPORTS `MAX_CLASSIFIED_LINES` into its own namespace
(`from .model import MAX_CLASSIFIED_LINES as MAX_CLASSIFIED_LINES`) and
passes it EXPLICITLY at each `parse()` call rather than defaulting it inside
`ClassifiedLineBudget.__init__` — a default there would be bound once at
class-definition (import) time, silently breaking each module's own
pre-existing `monkeypatch.setattr(<module>, "MAX_CLASSIFIED_LINES", ...)`
test idiom. Preserving both modules' existing test-patching idiom was a real
constraint, not incidental — rewriting `test_coverage_parsers_coverage_istanbul_json.py`'s
own fixture to target a different module would have been a needless,
unrelated test-suite change riding along on a bug fix.

**Side effect:** the refusal message changed from istanbul's own literal
"classified statement lines" to the shared "classified lines" (format name
in a prefix) — "statement lines" is meaningless for a Go BLOCK. Three
pre-existing istanbul tests' string assertions updated to match; no
behavioral assertion changed.

---

## 8. B048 — browser coverage of a UI as an R1 lane

- [x] CONSUMERS section "Browser coverage of a UI as an R1 lane"
  (`docs/CONSUMERS.md:841-905`) — recipe (offline install, `vite build
  --mode coverage` with `forceBuildInstrument: true` — REQUIRED, cited from
  the plugin's own README, not assumed — serve, drive a real Playwright
  suite, merge `window.__coverage__`, declare the lane like any other JS R1
  lane) and the limit paragraph (UI code bound to the snapshot; the API it
  talks to is an unverified declared fact until B004; do not build a
  detached `assay judge` verb before B004).
- [x] Small committed `vite-plugin-istanbul` artifact, produced OUTSIDE
  assay from a real build+execution — `tests/fixtures/coverage/
  probe-js-vite-plugin-istanbul/` (node `v26.5.1`, `vite` `8.2.2`,
  `vite-plugin-istanbul` `9.0.1`, `jsdom` `26.1.0`),
  `coverage-istanbul-json.vite-plugin-istanbul.json`,
  `PROVENANCE.md`'s new section (exact commands, what it proves, what it
  does NOT license). Keys measured to be the original `src/math.ts`/
  `src/main.ts` paths, never `dist/assets/*.js` — and the artifact is a
  genuinely PARTIAL-coverage one (a defensive branch never taken by the
  one real call), not a trivially-all-green fixture.
- [x] One parser test — `tests/test_coverage_parsers_vite_plugin_istanbul_artifact.py`,
  3 tests, all pass, driving the EXISTING unmodified `coverage-istanbul-json`
  parser with no code change.
- [ ] "the consumer-side `__coverage__` dump fixture is dstdns's package" —
  not this repo's work, unchanged, per the backlog entry's own framing.

---

## 9. Docs disposition table

| file | what changed | item(s) |
|---|---|---|
| `README.md` | Jest/c8 scope corrected; `defineConfig` comment; `clean: false` added + explained; cross-link to new CONSUMERS section | B042, B049, B041(a) |
| `docs/CONSUMERS.md` | new §"JavaScript lanes and the dependency closure" (replaces old worked lane); new §"Preflighting a gate environment with `assay lanes --json`"; new §"Browser coverage of a UI as an R1 lane"; Jest/c8 scope corrected with the c8 measurement; support-files mechanism corrected; `clean: false` added + explained; "Dependency closures come from the image" added to Practices | B041(a), B044, B048, B042, B049 |
| `src/assay/coverage_parsers/coverage_istanbul_json.py` (module docstring) | "nyc/istanbul or Jest... unaffected" scoped to Jest's default `babel` provider | B042 item 2 (grep target: "the parser docstring") |
| `tests/fixtures/coverage/PROVENANCE.md` | two new sections (c8, vite-plugin-istanbul) | B042 item 2, B048 |
| `CHANGES.md` | `[Unreleased]` gains 6 new bullets (1 Added, 1 Fixed, 4 Documentation) | all |

No `docs/DESIGN-GUIDE.md` edit was needed this wave (its own JS section was
untouched by the review's findings).

---

## 10. Decisions recorded

- **A-347** — the B049 finding (mechanism, measured A/B, Wave A's
  documentary mitigation, why not fixed in code).
- **A-348** — B039's shared-bound design (why `remaining` has no default on
  `ClassifiedLineBudget`, why the refusal message changed, the object-
  identity test).
- **A-349** — B044's inventory field set and its stability rule
  (`inventory_schema` bumps only on a meaning change), and why
  `base_source`/`rigor_reachable` are the two derived (not passed-through)
  fields.
- **A-350** — the qualification harness's place in the gate (never wired
  into `tools/tester-unified-gate.sh`; environment-gated exactly like P25's
  own real-Python qualification, for the identical reason `tester-unified`
  cannot run it).

All four in `nyxloom-trove/decisions.md`, appended after A-346 under a new
"## Decided — Wave A" heading, never rewriting an existing row.

---

## 11. The registered gate

Run per the wave prompt: `bash assay/tools/tester-unified-gate.sh
/workspaces/vbpub/.worktrees/assay-wave-a-js-consumer` from
`/workspaces/vbpub`, AFTER the last commit. Verdict read in a separate step
(exit code + `ASSAY_REGISTERED_GATE_COMPLETE=1`), never as a pipe tail.

**Attempt 1 (judged commit `4a4056b6`): `ERROR`, self-inflicted, not a
product defect — reported honestly rather than discarded.** While the gate
ran (it takes several minutes: wheel build, attestation, verdict-successor
suites, then the self-hosted lane's own full `pytest tests -q` inside the
container), I continued editing this REPORT and the LOG in the SAME live
worktree the gate had already started judging — the worktree is a live bind
mount, not a snapshot taken at invocation. The self-hosted lane's own
post-command dirty-tree check caught exactly that:

```
tester-unified: NO_MEASUREMENT/DIRTY_TREE (exit 3)
  commit: 4a4056b6f8839ca736b839d0d733695b6134dcc1
  argv: python -m pytest tests -q --ignore=tests/test_self_hosting.py --override-ini=pythonpath=
ASSAY_GATE_DIAGNOSTIC=worktree-status-after-the-lane
 M assay/nyxloom-trove/reports/assay-WAVE-A-js-consumer-LOG.md
?? assay/nyxloom-trove/reports/assay-WAVE-A-js-consumer-REPORT.md
```

**The underlying suite itself was green** — the gate's own diagnostic rerun
(`python -m pytest tests -q --ignore=tests/test_self_hosting.py`) reports
`3574 passed, 13 skipped in 365.52s (0:06:05)`, zero failures — but the
lane's own verdict is honestly `NO_MEASUREMENT`, not `PASS`, because the
tree it judged was not the tree I claimed to judge. This is assay's own
`NO_MEASUREMENT`/`DIRTY_TREE` discipline working exactly as designed, on
its own implementer, and it is reported here rather than quietly re-run and
forgotten — the earlier phases (`wheel-installed` through
`verdict-v8-successors-verified`) all passed cleanly on this same attempt.

**Attempt 2 (judged commit `e9424676`): also `ERROR`/environmental, not a
product defect.** Launched cleanly from an untouched worktree, but this
session's own background-task tracking killed the outer shell mid-run twice
in a row (unrelated to assay — an artifact of this harness's own background-
task lifecycle, observed independently of any file this wave touches); the
underlying `tester-unified:local` container kept running detached and
un-inspectable after `--rm` auto-removal raced my attempts to recover its
exit code. No product signal either way from these two attempts; not
counted as gate evidence.

**Attempt 3 (judged commit `e9424676`, same as attempt 2 — worktree never
touched between attempts): GREEN, full transcript captured.** Launched via
`nohup ... & disown` so the run survived independent of this session's own
background-task tracking, polled through a `Monitor` watch on its log file
rather than the process itself. Full transcript committed verbatim at
`nyxloom-trove/reports/assay-WAVE-A-gate-transcript.txt`; the load-bearing
lines, read in a separate step from the pipe (never a tail):

```
ASSAY_GATE_PHASE=wheel-installed
25 passed, 16 deselected in 1.48s
ASSAY_GATE_PHASE=attestation-hardened
13 passed, 31 deselected in 21.75s
ASSAY_GATE_PHASE=verdict-v5-accepted
17 passed in 0.81s
ASSAY_GATE_PHASE=lane-schema-v2-successors-verified
v6/v7 hard-cut guard passed for 12 frozen templates
ASSAY_GATE_PHASE=verdict-v6-v7-hard-cut-verified
41 passed in 0.97s
ASSAY_GATE_PHASE=verdict-v8-successors-verified
tester-unified: PASS (exit 0)
  commit: e94246767b1935e1233c42d9fdab4df5fed22eff
  argv: python -m pytest tests -q --ignore=tests/test_self_hosting.py --override-ini=pythonpath=
ASSAY_GATE_PHASE=judge-provenance-bound-to-the-installed-wheel
ASSAY_GATE_PHASE=self-hosted-lane-passed
ASSAY_GATE_PHASE=topos-qualified
--- B006(a) WI-5 qualification receipt ---
outcome=PASS exit_code=0
claim[R0]=status=PASS
claim[R1]=status=PASS
claim[R2]=status=PASS
claim[R3]=status=PASS
ASSAY_B006A_CMRU_QUALIFIED=1
ASSAY_GATE_PHASE=cmru-b006a-qualified
7 passed in 13.70s
ASSAY_GATE_PHASE=independent-self-hosting-passed
ASSAY_REGISTERED_GATE_COMPLETE=1
```

`ASSAY_REGISTERED_GATE_COMPLETE=1` is the literal last line of the captured
log (line 61 of 61) — verified by direct file inspection, not a pipe tail.
The judged worktree's own `git status --short -- assay` was confirmed empty
immediately before AND after this run; the judged commit (`e9424676`) is
this wave's actual final commit before this REPORT's own closing commits
(which land after, and are therefore not gate-verified — see the note at
the end of this section).

**Every phase, all real, nothing skipped:** the self-hosted lane runs this
project's ENTIRE `pytest tests -q` suite (the same 3574-pass/13-skip count
attempt 1's diagnostic rerun already showed) against the wheel-installed
build; P25's Topos qualification and CMRU's B006a qualification each run
real R0/R1/R2/R3 claims (a real killed mutant, a real canary) against real
disposable git snapshots; the independent witness re-verifies self-hosting
from a second angle. None of this wave's own new work (B044's inventory,
the shared bound, the JS/vite-plugin-istanbul fixtures, the qualification
harness) is part of what these OTHER packages' own qualification suites
exercise directly — their green result is evidence the WHOLE installed
wheel behaves correctly, including this wave's changes, not a claim that
they specifically targeted B036/B041/B042/B044/B048/B039.

**Note on commits after the judged one:** this REPORT's own remaining
edits (filling in this very section) and its commit land AFTER `e9424676`,
so the gate has not re-verified those specific bytes — they are pure
documentation of an already-green run, touching no source or test file.


## 12. What a reviewer should push on

1. **The B049 finding itself** — re-run the qualification harness, then
   independently confirm the `clean: true`→`EMPTY_COVERAGE`,
   `clean: false`→`PASS` flip on a fresh scratch project, not trusting this
   report's own transcript. Check whether the three options in B049 missed
   a cheaper fourth (e.g., re-`fstat`ing the held `parent_fd` after the
   command and comparing device/inode against a fresh `stat` of the path by
   name, refusing loudly on mismatch without needing a full re-open —
   sketched nowhere in B049, not evaluated here).
2. **`base_source`'s derivation in `assay lanes --json`** (A-349) — is
   resolving `None → "declared"` really the right call, or should the
   inventory pass `null` through raw and let CIU implement A-328 itself?
   Argued for resolution in A-349; a reviewer who disagrees should push
   here specifically, since it is the one field this command derives
   rather than reads.
3. **The c8/vite-plugin-istanbul measurements' external validity** — both
   were run once, on this exact devcontainer, with these exact package
   versions. Nothing here re-runs them on a second machine or a second
   dependency resolution to rule out an environment-specific artifact.
4. **The support-files mechanism correction (§4)** — re-derive it
   independently from Vitest's own source rather than trusting this
   report's citation of `chunks/coverage.*.js:343-352`; the exact chunk
   filename is content-hashed and will differ across Vitest patch
   releases, so a reviewer on a different `vitest@4.1.x` patch may need to
   re-locate the equivalent block.
5. **Whether B041(a)'s CONSUMERS section, on its own, is enough for a
   first-time JS consumer to succeed** — it was written and cross-checked
   against the wave prompt's own recipe, but never handed to a fresh reader
   with no other context to see if the offline-install pattern reads
   clearly on a first pass.

---

## 13. What I did NOT do, and why (Wave B boundaries hit)

- **`link_paths` (B041(b)), `cwd` (B043), `producer` (B045), ingested R2
  (B046)** — explicitly out of scope; the wave prompt's own "NOT IN SCOPE"
  list names all four. Not implemented, not partially stubbed beyond the
  `null`/`[]` shape B044's inventory already carries for forward
  compatibility.
- **No verdict field, schema const, `verify.py` registration, or the frozen
  drift-guard was touched** — checked at the end of the session: `git diff
  --stat` against `main`'s merge-base shows no change under
  `src/assay/verdict.py`, `src/assay/verify.py`, or
  `nyxloom-trove/carve-assets/W4/`.
- **`javascript` was not registered at R2 or R3** in `cli.py`'s
  `_built_in_registry()` — untouched; confirmed by grep, still exactly
  `frozenset({"R1"})` for the JavaScript entry.
- **No Go adapter change beyond the shared bound (B039)** — `adapters/go.py`
  itself untouched; only `coverage_parsers/go_cover.py`'s expansion loop
  changed, and only to spend the shared budget.
- **B049 was filed, not fixed** — see §6; a genuine, out-of-scope core-code
  finding, left for a maintainer ruling rather than an implementer's
  unilateral product call.
- **CHANGES.md's stale `[Unreleased]` entry from 3.1.0 was left in place**
  — its own housekeeping comment says clearing it is part of releasing,
  which is the controller's job (per the wave's own "Roles" section), not
  mine; flagged explicitly rather than silently worked around.
- **The full local `pytest tests/ -q` run I started early in this session
  never produced output before this REPORT was written** — targeted subsets
  covering every file this wave touched were run instead and are reported
  individually above (all green); the registered gate (§11) is the real
  ship signal per A-335 and is what this REPORT's own verdict rests on.

---

## 14. Decision asks

- **B049's three fix options need a ruling** (§6) — not blocking this
  wave's own contract, but blocking a clean answer the next time a
  different external tool (a future Go coverage/mutation producer, a
  consumer's own SQL dump step) shares Vitest's "recreate my output
  directory" convention.
