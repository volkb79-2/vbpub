# Real coverage artifacts — carver-owned evidence, never hand-authored

Ten artifacts. Eight are coverage.py's (below); the two
`coverage-istanbul-json.*` documents are real `vitest run --coverage` output
and have their own section at the bottom of this file (B036).

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
pins every version exactly, no ranges). Outside this repository — no
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
| the two providers' `branchMap` are **not the same measurement** | for `branchy.ts` the istanbul artifact has 3 entries typed `if`/`if`/`cond-expr` with per-arm counts `[0,1]`, `[1,0]`, `[0,0]` (6 arcs, 2 covered); the v8 artifact has 4 entries all typed `"branch"`, each with exactly ONE location and ONE count (4 "arcs", 1 covered), one of them spanning the whole function |
| an end position's `column` can be **`null`** in real output | every `end` in the istanbul artifact — a parser requiring an integer column rejects genuine output |
| a `.d.ts` declaration file is reported by **neither** provider | `src/types.d.ts` appears in neither document |
| a **type-only `.ts` module** is reported by v8 with an EMPTY `statementMap`, and not at all by istanbul | `src/typesonly.ts` has `"statementMap": {}` in the v8 artifact and no record in the istanbul one |
| a never-imported source file is still measured by v8 | `src/orphan.ts` carries `"all": true` and all-zero counts in the v8 artifact |
| test files are excluded by the tool itself, under all three naming conventions | neither document has a record for `__tests__/roles.test.ts`, `branchy.test.ts` or `Badge.spec.tsx` |
| an `/* istanbul ignore next */` hint leaves **no exclusion field** to read | `src/hinted.ts` carries the hint, and in both artifacts its hinted `if` is still an ordinary `statementMap` entry with a live count (istanbul: `[3,5]` count 1, its never-taken `return` `[4,4]` count 0) and no `skip` marker appears anywhere in either document — which is why `excluded` is `None` for this format |
