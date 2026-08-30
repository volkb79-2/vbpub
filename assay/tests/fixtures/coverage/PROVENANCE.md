# Real coverage artifacts — carver-owned evidence, never hand-authored

Sixteen artifacts. Eight are coverage.py's (below); the six
`coverage-istanbul-json.*` documents are real `vitest run --coverage` output
and have their own sections at the bottom of this file (B036/A-346), as do the
two canary artifacts under `tests/fixtures/canary/javascript/`.

Eight artifacts. Six are three formats × (branch tracking ON / OFF) over the
two-file program in `probe/`; two more (`*.exitarc.*`) come from `probe-exit/`
and exist only to witness the exit-arc spellings described at the bottom of this
file. All were produced by real `coverage.py` runs. They exist because the
branch-capability rules in the wave-1 specification are claims about what these
tools **actually emit**, and this project's own record says a fixture invented
to match a design is the shape of failure that survives a green suite
(A-124, A-131, MEASUREMENTS.md).

**The implementer must not edit any of these eight artifacts**, nor the four
`probe*/` driver files that produce them. A branch parser that needs one of
them changed is a parser that does not read the real format.

## How they were produced

Inside `probe/`, with `coverage 7.15.3` / `pytest 8.4.2` / CPython in this
devcontainer, on 2026-08-16, driven by `probe/.coveragerc`
(`source = .`, `relative_files = True`):

```sh
# branch tracking ON
coverage run  --rcfile=.coveragerc --branch -m pytest check_sample.py -q
coverage json --rcfile=.coveragerc -o ../coverage-py-json.branch.json --pretty-print
coverage lcov --rcfile=.coveragerc -o ../lcov.branch.info
coverage xml  --rcfile=.coveragerc -o ../cobertura.branch.xml

# branch tracking OFF — same program, same tests, same commands minus --branch
coverage run  --rcfile=.coveragerc -m pytest check_sample.py -q
coverage json --rcfile=.coveragerc -o ../coverage-py-json.nobranch.json --pretty-print
coverage lcov --rcfile=.coveragerc -o ../lcov.nobranch.info
coverage xml  --rcfile=.coveragerc -o ../cobertura.nobranch.xml
```

**No file was edited afterwards** — including the Cobertura documents.
`relative_files = True` is why: without it, `coverage xml` writes the producing
machine's absolute directory into `<sources><source>`, and a fixture carrying
this worktree's path would break for every other reader. With it, the two
emitted `<source>` elements are the empty string and `.`.

**The driver files are named `check_*.py`, not `test_*.py`, and must stay that
way.** `pyproject.toml` sets `testpaths = ["tests"]` with no `norecursedirs`
override, so a `test_*.py` anywhere under `tests/fixtures/` is collected into
the project's REAL suite — measured here: `pytest tests/fixtures/coverage
--collect-only -q` reported `4 tests collected` under the original names and
`no tests collected` under these. pytest still runs an explicitly named file
regardless of the pattern, which is why the generating commands work unchanged.
Renaming these back would quietly add fixture programs to the gate's own suite.

`probe/sample.py` is deliberately shaped so every branch state appears at once:
`classify` has one taken and one untaken arc, `first_two` has a loop with both
arcs of one branch taken, and `never_called` is never entered at all — which is
the case `lcov` spells `-` rather than `0`.

## What each artifact proves — the facts the specification depends on

| fact | witness |
|---|---|
| coverage.py JSON states branch capability **explicitly** | `meta.branch_coverage` is `true` in `coverage-py-json.branch.json` and `false` in `coverage-py-json.nobranch.json` |
| coverage.py JSON carries per-arc detail, not only totals | `files["sample.py"].executed_branches` = `[[5,6],[11,12],[12,11],[12,13]]`, `missing_branches` = `[[5,7],[11,14],[18,19],[18,20]]` |
| its own totals can be cross-checked against that detail | `summary.num_branches` 8 = `covered_branches` 4 + `missing_branches` 4, and 4 = `len(executed_branches)` |
| the combined line+branch percentage IS coverage.py's `percent_covered` | `(covered_lines 8 + covered_branches 4) / (num_statements 13 + num_branches 8)` = 57.14…, exactly `summary.percent_covered` — the metric `--cov-fail-under` compares against |
| lcov states branch capability **nowhere** | `lcov.branch.info` and `lcov.nobranch.info` differ only by the presence of `BRDA`/`BRF`/`BRH` records |
| an lcov record for a branch-free file omits those records **even when tracking is on** | in `lcov.branch.info`, `check_sample.py` carries no `BRF`/`BRH` at all while `sample.py` carries `BRF:8`/`BRH:4` — so a *per-file* capability rule would call this single real artifact "mixed" and refuse it |
| lcov distinguishes "arc not taken" from "block never entered" | `BRDA:18,0,jump to line 19,-` uses `-`, not `0`, because line 18 never ran |
| Cobertura states capability only as a **document-level count** | root `branches-valid="8"` with tracking on, `branches-valid="0"` with it off |
| Cobertura's per-line branch detail is a ratio, not an arc list | `<line number="5" hits="1" branch="true" condition-coverage="50% (1/2)" missing-branches="7"/>` |
| a never-entered branch line reports zero covered arcs in every format | line 18: coverage.py `[18,19]`/`[18,20]` both missing; lcov two `-`; Cobertura `condition-coverage="0% (0/2)"` |

The last row is the invariant `FileCoverage` enforces once, in the model, for
all formats: a line in `missing` can never carry a covered arc.

## The exit-arc fixtures — two shapes that reject a "reasonable" parser

`probe-exit/exits.py` has one branch whose untaken arc **leaves the function**
instead of reaching another line. Generated the same way, `--branch` only:

```sh
coverage run  --rcfile=.coveragerc --branch -m pytest check_exits.py -q
coverage json --rcfile=.coveragerc -o ../coverage-py-json.exitarc.json --pretty-print
coverage lcov --rcfile=.coveragerc -o ../lcov.exitarc.info
```

| fact | witness |
|---|---|
| a coverage.py branch **destination can be negative** | `missing_branches` = `[[5,7],[11,-10]]` — `-10` encodes "exit the function that starts at line 10" |
| so `src` is a line number and `dst` is an opaque identity | a parser validating both members as positive line numbers rejects this real artifact. assay attributes a branch to its SOURCE line and never needs `dst` |
| an lcov branch id is **free text**, not a number | `BRDA:11,0,return from function 'falls_off_the_end',0` — spaces and an apostrophe inside the third field, so a parser reading it as an integer rejects this real record |

That last row proves the field is non-numeric free text. It does **not** prove
the field can contain a comma — this record carries exactly the three delimiter
commas a four-field split expects, and no artifact here witnesses an id with a
comma in it. The specification still requires right-splitting `taken` off the
end, but as a *defensive* choice with its own stated reason, not as something
these bytes demonstrate. Saying otherwise would be the unfalsifiable-oracle
mistake this directory exists to prevent.

Both files carry the ordinary shapes too (`[5,6]`/`[5,7]`, `BRDA:5,0,jump to
line 6,1`), so a test that reads them is not exercising only the exotic case.

---

# The istanbul artifacts — real `vitest run --coverage` output (B036)

Two artifacts, `coverage-istanbul-json.vitest-v8.json` and
`coverage-istanbul-json.vitest-istanbul.json`. They are the SAME program
measured by Vitest's TWO coverage providers, and they exist because the whole
design of `coverage_parsers/coverage_istanbul_json.py` and of
`adapters/javascript.py`'s `requires_span_attribution = False` turns on how
these two documents actually differ. A hand-written istanbul fixture would
have reproduced neither difference.

**The implementer must not edit either artifact**, nor the driver project in
`probe-js/` that produces them. A parser that needs one of them changed is a
parser that does not read the real format.

## How they were produced

`probe-js/` is a complete, self-contained Vitest project (its `package.json`
pins every direct version exactly, no ranges, and its committed
`package-lock.json` pins every transitive one — round-1 review nitpick: the
recipe below was reproducible in practice but not exactly, since transitive
resolution was unpinned). Outside this repository — no
`node_modules` is committed — with Node `v26.5.1`, `vite 7.3.6`,
`vitest 3.2.4`, `@vitest/coverage-v8 3.2.4` and
`@vitest/coverage-istanbul 3.2.4`, on 2026-08-30:

```sh
npm install
npx vitest run --coverage --coverage.provider=v8
cp coverage/coverage-final.json ../coverage-istanbul-json.vitest-v8.json
npx vitest run --coverage --coverage.provider=istanbul
cp coverage/coverage-final.json ../coverage-istanbul-json.vitest-istanbul.json
```

**No file was edited afterwards.** In particular the record keys are the
producing machine's own ABSOLUTE paths, left exactly as istanbul writes them —
that is the format's actual key shape, and a fixture with them rewritten to
relative paths would silently retire the very case
`evaluate._to_repo_relative_key`'s absolute-key branch exists to handle
(A-341). A test that needs those keys under its own temp repository rebases
the directory prefix at read time and changes nothing else.

`probe-js/src/format.ts` and `probe-js/src/roles.ts` are copied verbatim from
dstdns's `applications/webapp-ui-react/src/lib/` (the first real consumer),
with one edit to `roles.ts`: its `@/auth/types` path alias is rewritten to a
relative `./types`, because the probe declares no alias. The rest of the
program is shaped so every case the parser and the adapter must decide appears
at once — a partially-covered `if`/`if`/ternary function, a TSX component with
a multi-line JSX return, a `.d.ts` declaration file, a type-only `.ts` module,
a never-imported module, a function carrying an
`/* istanbul ignore next */` hint, and test files under all three naming
conventions (`__tests__/roles.test.ts`, `branchy.test.ts`, `Badge.spec.tsx`).

## What each artifact proves — the facts B036's design depends on

| fact | witness |
|---|---|
| the format keys records by **absolute filesystem path** | every top-level key in both documents is an absolute path, and each record repeats it in its own `path` field |
| `@vitest/coverage-v8` emits **one single-line statement per executable line** | in the v8 artifact no `statementMap` entry anywhere has `end.line != start.line` |
| `@vitest/coverage-istanbul` emits **real multi-line statement extents** | in the istanbul artifact `format.ts` carries `[13,15]`, `[24,32]`, `[33,37]` and `[34,36]`; `roles.ts` carries `[7,11]` |
| so this format DOES have coverage.py's multi-line-statement gap, under one provider | those extents' interior lines have no statement entry of their own at all |
| statement extents **nest**, and "executed wins" would be a false green | `branchy.ts` (istanbul) reports `[2,4]` (the whole `if`) with count **1** and `[3,3]` (its own never-taken `return`) with count **0**; innermost-wins classifies line 3 missing, a go-cover-style merge would call it covered |
| an `if`'s else-arm location is `{"start": {}, "end": {}}` — empty objects, no line at all | every two-location `if` entry in the istanbul artifact; irrelevant to this parser, which reads no branch data, and load-bearing for whoever implements B038 |
| the two providers' `branchMap` are **not the same measurement** | for `branchy.ts` the istanbul artifact has 3 entries typed `if`/`if`/`cond-expr` with per-arm counts `[0,1]`, `[1,0]`, `[0,0]` (6 arcs, 2 covered); the v8 artifact has 4 entries all typed `"branch"`, each with exactly ONE location and ONE count (4 "arcs", 1 covered), one of them spanning the whole function |
| an end position's `column` can be **`null`** in real output | every `end` in the istanbul artifact — a parser requiring an integer column rejects genuine output |
| a `.d.ts` declaration file is reported by **neither** provider | `src/types.d.ts` appears in neither document |
| a **type-only `.ts` module** is reported by v8 with an EMPTY `statementMap`, and not at all by istanbul | `src/typesonly.ts` has `"statementMap": {}` in the v8 artifact and no record in the istanbul one |
| a never-imported source file is still measured by v8 | `src/orphan.ts` carries `"all": true` and all-zero counts in the v8 artifact |
| test files are excluded by the tool itself, under all three naming conventions | neither document has a record for `__tests__/roles.test.ts`, `branchy.test.ts` or `Badge.spec.tsx` |
| an `/* istanbul ignore next */` hint leaves **no exclusion field** to read | `src/hinted.ts` carries the hint, and in both artifacts its hinted `if` is still an ordinary `statementMap` entry with a live count (istanbul: `[3,5]` count 1, its never-taken `return` `[4,4]` count 0) and no `skip` marker appears anywhere in either document — which is why `excluded` is `None` for this format |

---

# The provider-defect artifacts — one program, two providers, two Vitest majors (A-346)

Four artifacts, `coverage-istanbul-json.provider-defect.{vitest3,vitest4}-{v8,istanbul}.json`,
produced from `probe-js-provider-defect/`. They exist because the round-1
adversarial review of B036 found that both providers had been measured
exhaustively for SHAPE and never once for ACCURACY — and that one of them is
wrong.

**The program is built so ground truth needs no coverage tool.** Five
functions, each guarded by `if (v === 0) return 0` on its second line; one
test calls each with `0`. Every line below a guard provably never executes.
That is a fact about the program; any artifact disagreeing with it is wrong.

**The implementer must not edit these four artifacts** nor `probe-js-provider-defect/`.

## How they were produced

Outside this repository, on 2026-08-30, with Node `v26.5.1`. Two dependency
sets are committed as `package.vitest3.json`/`package-lock.vitest3.json` and
`package.vitest4.json`/`package-lock.vitest4.json` (rename either pair to
`package.json`/`package-lock.json`); everything else in the project is shared:

```sh
cp package.vitest3.json package.json && npm install
npx vitest run --coverage --coverage.provider=v8
cp coverage/coverage-final.json ../coverage-istanbul-json.provider-defect.vitest3-v8.json
npx vitest run --coverage --coverage.provider=istanbul
cp coverage/coverage-final.json ../coverage-istanbul-json.provider-defect.vitest3-istanbul.json

rm -rf node_modules package-lock.json
cp package.vitest4.json package.json && npm install     # vitest 4.1.11
# ...the same two runs, into the vitest4-* names
```

**No file was edited afterwards.**

## What they prove

| fact | witness |
|---|---|
| **`@vitest/coverage-v8` reports never-executed lines as EXECUTED** | in both v8 artifacts, `shapes.ts` lines 10-11 (below `ternaryMultiLine`'s guard) carry a nonzero count, and the test only ever calls `ternaryMultiLine(0)` |
| it is triggered by a **one-line** ternary too, not only a multi-line one | `vitest3-v8` additionally has lines 16-18 (`ternaryOneLine`) executed; `vitest4-v8` has 17-18 |
| it is **not a version to upgrade past** | present in `vitest3-v8` AND `vitest4-v8` — the two currently-released majors |
| it is the PROVIDER, not the version | `vitest3-istanbul` and `vitest4-istanbul` have zero false greens across all five shapes |
| only conditional expressions trigger it | in the same v8 artifacts, `binaryMultiLine`, `callMultiLine` and `objectLiteralMultiLine` bodies are all correctly not-executed — so sound and unsound records are structurally indistinguishable |
| istanbul reports the never-run lines as **missing**, not merely absent | `{10, 11}` and `{17, 18}` are in `missing` in both istanbul artifacts, so they reach an R1 denominator and a floor can refuse them |
| the v8 provider's statement geometry **changed between majors** | `vitest3-v8` has no multi-line extent anywhere; `vitest4-v8` has several — which is why no shape-based producer discriminator could have been written and stayed correct |

`coverage.experimentalAstAwareRemapping = true` was also tested against
Vitest 3.2.4 and does not fix it; no artifact is committed for that run
because it is byte-identical in the respect that matters (lines 10-11 still
carry a nonzero count).

---

# The canary artifacts — `tests/fixtures/canary/javascript/` (A-345)

`roles.uncovered-line-injected.ts` is exactly what
`JavaScriptAdapter.inject_uncovered_line` produces for `probe-js/src/roles.ts`
— asserted byte-for-byte by
`test_the_committed_injected_file_is_byte_for_byte_what_this_adapter_produces`,
so the two halves cannot drift apart silently. The two
`coverage-istanbul-json.uncovered-line.*.json` documents are real
`vitest run --coverage` output from a `probe-js` copy with that injected file
in place, one per provider, produced the same way and the same day.

They prove A-345's R1 claim as committed evidence rather than as a report
transcript: the appended function's declaration line is reached merely by the
module loading, while its two body lines are reported **missing** under both
providers — so a gate enforcing a changed-line-coverage floor rejects the
transform while a tests-only gate sails past it. The suite still passed in
both runs, which is the other half of the contract: the injection is valid,
lint-clean and test-neutral.

There is deliberately no artifact for `inject_import_break`, and there cannot
be one: that injection makes the test run fail, and a failed run writes no
coverage document at all — the same reason `adapters/go.py` records for its
own import-break half.

